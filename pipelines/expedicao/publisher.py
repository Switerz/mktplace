"""Publicacao no Neon — lock de sessao, substituicao POR CANAL, resumo por conta.

POR QUE LOCK DE SESSAO E NAO `pg_advisory_xact_lock`
----------------------------------------------------
A leitura acontece no Data Mart (RDS) e a escrita no Neon: duas conexoes, dois
bancos. Um lock transacional no Neon so existe enquanto a transacao existe —
para cobrir tambem a leitura, seria preciso abrir a transacao do Neon ANTES de
ler o Data Mart e deixa-la ociosa durante toda a extracao. Transacao ociosa
segura snapshot do MVCC, atrasa autovacuum e, em conexao pooled, derruba o pool.

Entao: `pg_advisory_lock` (sessao) adquirido em autocommit ANTES da leitura,
liberado no `finally`. A transacao do Neon so nasce na publicacao.

POR QUE A SUBSTITUICAO E POR CANAL
-----------------------------------
Shopee, ML e TikTok terao cadencias diferentes. Um `DELETE` sem filtro apagaria
os outros canais a cada refresh de um deles.

FOTOGRAFIA VAZIA
----------------
Backlog zero com fonte SAUDAVEL e uma fotografia legitima: todos os pedidos
foram expedidos. A publicacao apaga as linhas do canal, insere zero pedidos,
grava um resumo por conta esperada com `backlog_count = 0` e da commit. Tratar
isso como "nao faz nada" preservaria pedidos antigos na tela como se ainda
aguardassem expedicao.

Backlog zero com fonte DOENTE nao prova nada, e apagar a fila nesse caso seria
destruir estado com base em silencio. `publish_channel` exige `source_health` e
recusa antes de qualquer DELETE.

ATOMICIDADE FILA + RESUMO
-------------------------
Os dois vao na MESMA transacao, com o mesmo `refresh_batch_id`. Se fossem
transacoes separadas, uma falha no meio deixaria fila nova com resumo antigo (ou
o contrario) e a tela mostraria um backlog que a tendencia nao explica.

ETAPA DA FILA (T-0015, migration 033)
-------------------------------------
`etapa`/`etapa_fonte` sao colunas CONDICIONAIS: a cada execucao o publicador
pergunta ao `information_schema` se a 033 esta aplicada no destino. Com a
coluna, grava as duas; sem ela, o INSERT e' exatamente o de antes. Por isso a
imagem nova pode ir ao ar antes da migration.

A lista de colunas do INSERT continua EXPLICITA (`FILA_COLUMNS` + o recorte de
`ETAPA_COLUMNS` que existe no destino) e nunca sai de `records[0].keys()`: uma
chave nova na linha nao entra no banco sem alguem declara-la aqui.

Linha com etapa `outro` (valor novo da fonte) NAO bloqueia: e' contada em
`relatorio` e vira aviso no log do run.

ATRASO DE 7 DIAS NO RESUMO (migration 035)
------------------------------------------
Mesmo padrao da etapa, no RESUMO: `due_7d_count`, `late_7d_count`,
`late_7d_window_start`, `late_7d_window_end`, `over_48h_7d_count` e
`stalled_7d_count` (`ATRASO_7D_COLUMNS`) so' entram no INSERT e no `ON
CONFLICT ... DO UPDATE` se as SEIS existirem na tabela de resumo do destino.
Sem elas, o INSERT e o upsert sao byte a byte os de antes. Tudo ou nada, e nao
"o subconjunto presente", de proposito: as seis existem no Neon nas duas
tabelas de resumo (03/11/2026) e a 035 cria as seis em banco novo; gravar so'
as quatro antigas num destino incompleto esconderia a falta das duas novas
atras de uma tela que parece funcionar.
"""
from __future__ import annotations

import logging
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass

from pipelines.expedicao.contract import (
    ADVISORY_LOCK_KEYS,
    ATRASO_7D_COLUMNS,
    FILA_TABLE,
    RUN_TABLE,
    Channel,
    Etapa,
    LoteIncoerente,
    SourceHealth,
    SourceUnhealthy,
)
from pipelines.expedicao.coerencia import problemas_do_lote

#: Ordem das colunas do INSERT da fila. `brand` aparece DEPOIS de
#: `marketplace_order_id` para deixar visivel que e atributo, nao identidade.
FILA_COLUMNS = (
    "effective_at",
    "refresh_batch_id",
    "channel",
    "shop_account",
    "marketplace_order_id",
    "brand",
    "created_at",
    "paid_at",
    "dispatch_deadline",
    "deadline_source",
    "deadline_status",
    "operational_age_status",
    "is_slow_vs_baseline",
    "is_source_zombie",
    "is_stalled",
    "hours_open",
    "hours_overdue",
    "logistic_type",
    "carrier",
    "source_ingested_at",
    "source_freshness_status",
    "timestamp_quality",
)

#: Colunas da migration 033, em ordem. CONDICIONAIS: entram no INSERT so' as
#: que existirem no destino, e so' se `etapa` existir. Ficam FORA de
#: `FILA_COLUMNS` porque aquela lista e' o schema da 018, sempre presente.
ETAPA_COLUMNS = ("etapa", "etapa_fonte")

#: Deteccao por execucao. Roda DENTRO da transacao da publicacao, logo antes
#: do INSERT: se a coluna sumir entre a leitura e a escrita, o INSERT falha e o
#: rollback preserva a fila anterior. Serve as duas deteccoes (etapa na fila,
#: atraso de 7 dias no resumo).
COLUNAS_ETAPA_SQL = """
SELECT column_name
FROM information_schema.columns
WHERE table_schema = %s
  AND table_name = %s
  AND column_name = ANY(%s)
"""

#: Teto de valores brutos `outro` citados no relatorio/aviso.
_MAX_FONTES_OUTRO_NO_RELATORIO = 20

_log = logging.getLogger(__name__)

#: PK FISICA FUTURA da fila. Espelha `pk_shopee_orders (shop_account, order_sn)`.
#: `brand` NAO entra: corrigir a marca de uma conta nao pode criar outro pedido.
FILA_PRIMARY_KEY = ("channel", "shop_account", "marketplace_order_id")

SUMMARY_COLUMNS = (
    "refresh_batch_id",
    "channel",
    "shop_account",
    "brand",
    "snapshot_hour",
    "observed_at",
    "source_watermark_at",
    "source_advanced",
    "backlog_count",
    "overdue_count",
    "due_within_24h_count",
    "on_time_count",
    "deadline_unavailable_count",
    "over_48h_count",
    "slow_count",
    "zombie_count",
    "stalled_count",
    "run_status",
    "ingested_at",
)

#: PK FISICA FUTURA do resumo.
SUMMARY_PRIMARY_KEY = ("channel", "shop_account", "snapshot_hour")

#: Substituicao SEMPRE restrita ao canal. O teste garante que este e o unico
#: DELETE do modulo e que ele carrega `WHERE channel`.
_DELETE_SQL = "DELETE FROM {tabela} WHERE channel = %s"
DELETE_CANAL_SQL = _DELETE_SQL.format(tabela=FILA_TABLE)

#: Carga de CONTA (export do Seller Centre, EXPEDICAO-KOKESHI-EXPORT): so' as
#: contas do lote saem da fila. O canal continua no WHERE.
_SO_DAS_CONTAS_SQL = " AND shop_account = ANY(%s)"


@dataclass(frozen=True)
class Destino:
    """Onde a publicacao grava. O padrao e' o do publicador agendado.

    `contas` vazio substitui a fila do CANAL inteiro (o comportamento de
    sempre). A carga do export grava em tabelas proprias e substitui SO' as
    contas que trouxe: as tabelas do export podem ter mais de uma conta, cada
    uma com o seu lote.
    """

    fila: str = FILA_TABLE
    resumo: str = RUN_TABLE
    contas: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.fila != FILA_TABLE and not self.contas:
            raise ValueError(
                f"publicacao em {self.fila} exige as contas substituidas: sem "
                "elas o DELETE levaria a fila das outras contas do export"
            )


DESTINO_PADRAO = Destino()

#: Reexecucao na mesma hora: a observacao mais NOVA vence; a mais antiga nao
#: sobrescreve. `DO NOTHING` seria insuficiente — a fonte pode avancar depois da
#: primeira execucao da hora, e a primeira leitura ficaria congelada como se
#: fosse o estado da hora inteira.
#:
#: A clausula `WHERE` torna o resultado independente da ordem de reexecucao.
_RUN = RUN_TABLE.split(".")[-1]
_SUMMARY_CONFLICT_TEMPLATE = """
ON CONFLICT (channel, shop_account, snapshot_hour) DO UPDATE SET
    refresh_batch_id = EXCLUDED.refresh_batch_id,
    brand = EXCLUDED.brand,
    observed_at = EXCLUDED.observed_at,
    source_watermark_at = EXCLUDED.source_watermark_at,
    source_advanced = EXCLUDED.source_advanced,
    backlog_count = EXCLUDED.backlog_count,
    overdue_count = EXCLUDED.overdue_count,
    due_within_24h_count = EXCLUDED.due_within_24h_count,
    on_time_count = EXCLUDED.on_time_count,
    deadline_unavailable_count = EXCLUDED.deadline_unavailable_count,
    over_48h_count = EXCLUDED.over_48h_count,
    slow_count = EXCLUDED.slow_count,
    zombie_count = EXCLUDED.zombie_count,
    stalled_count = EXCLUDED.stalled_count,
    run_status = EXCLUDED.run_status,
    ingested_at = EXCLUDED.ingested_at
WHERE EXCLUDED.observed_at > {tabela}.observed_at
"""
SUMMARY_CONFLICT_SQL = _SUMMARY_CONFLICT_TEMPLATE.format(tabela=_RUN)

#: Ancora das colunas condicionais no `DO UPDATE SET`: entram logo antes dela.
_ANCORA_CONFLITO = "    run_status = EXCLUDED.run_status,\n"


def summary_conflict_sql(tabela: str, extras: tuple[str, ...] = ()) -> str:
    """`ON CONFLICT` do resumo. Sem `extras`, identico ao de sempre.

    Com as colunas da 035, a reexecucao da hora tambem as atualiza: senao a
    observacao mais nova levaria as contagens da fila e deixaria o atraso de
    7 dias da observacao anterior.
    """
    sql = _SUMMARY_CONFLICT_TEMPLATE.format(tabela=tabela)
    if not extras:
        return sql
    novas = "".join(f"    {c} = EXCLUDED.{c},\n" for c in extras)
    return sql.replace(_ANCORA_CONFLITO, novas + _ANCORA_CONFLITO, 1)


def colunas_de_etapa_no_destino(cur, tabela: str = FILA_TABLE) -> tuple[str, ...]:
    """Recorte de `ETAPA_COLUMNS` presente no destino. Vazio = 033 ausente.

    Aceita cursor de tupla e `RealDictCursor`, como `channel_lock`.
    """
    esquema, nome = tabela.split(".")
    cur.execute(COLUNAS_ETAPA_SQL, (esquema, nome, list(ETAPA_COLUMNS)))
    existentes = {
        linha["column_name"] if isinstance(linha, dict) else linha[0]
        for linha in cur.fetchall()
    }
    if "etapa" not in existentes:
        return ()
    return tuple(c for c in ETAPA_COLUMNS if c in existentes)


def colunas_de_atraso_7d_no_destino(cur, tabela: str = RUN_TABLE) -> tuple[str, ...]:
    """`ATRASO_7D_COLUMNS` se as SEIS existem no resumo do destino; senao vazio.

    Tudo ou nada: a API le as colunas juntas, e gravar parte delas publicaria
    contagem sem janela (ou janela sem contagem). Ver o cabecalho do modulo.
    """
    esquema, nome = tabela.split(".")
    cur.execute(COLUNAS_ETAPA_SQL, (esquema, nome, list(ATRASO_7D_COLUMNS)))
    existentes = {
        linha["column_name"] if isinstance(linha, dict) else linha[0]
        for linha in cur.fetchall()
    }
    if not set(ATRASO_7D_COLUMNS) <= existentes:
        return ()
    return ATRASO_7D_COLUMNS


def problemas_de_atraso_7d(summary_rows: list[dict]) -> list[str]:
    """Linhas de resumo com o atraso de 7 dias incoerente. Vazio = ok.

    Todas NULL (ou chaves ausentes) = nao calculado. Preenchido: base,
    atrasados e janela juntos, base > 0, 0 <= atrasados <= base e janela em
    ordem. Acima de 48 h e travados podem ser NULL sozinhos (nao calculados:
    ML sem baseline, conta sem amostra); preenchidos, ficam em [0, base].
    """
    problemas = []
    for r in summary_rows:
        v = [r.get(c) for c in ATRASO_7D_COLUMNS]
        if all(x is None for x in v):
            continue
        due, late, inicio, fim, acima_48h, travados = v
        opcionais = [x for x in (acima_48h, travados) if x is not None]
        conta = r.get("shop_account")
        if any(x is None for x in (due, late, inicio, fim)):
            problemas.append(f"conta {conta!r}: atraso de 7 dias parcial")
        elif any(type(x) is not int for x in (due, late, *opcionais)):
            problemas.append(f"conta {conta!r}: contagem de 7 dias nao inteira")
        elif due <= 0 or not 0 <= late <= due:
            problemas.append(
                f"conta {conta!r}: atraso de 7 dias fora do dominio ({late}/{due})"
            )
        elif any(not 0 <= x <= due for x in opcionais):
            problemas.append(
                f"conta {conta!r}: 48 h/travados de 7 dias fora do dominio "
                f"({acima_48h}, {travados} de {due})"
            )
        elif inicio > fim:
            problemas.append(f"conta {conta!r}: janela de 7 dias invertida")
    return problemas


def contagem_por_etapa(fila_rows: list[dict]) -> dict[str, int]:
    """Linhas por etapa. A soma e' sempre `len(fila_rows)`.

    Linha sem a chave `etapa` (montada fora de `build_fila_*`) conta como
    `nao_publicada`, que e' exatamente o que o banco gravaria para ela.
    """
    return dict(
        Counter(r.get("etapa", Etapa.NAO_PUBLICADA.value) for r in fila_rows)
    )


class LockNotAcquired(RuntimeError):
    """Outro refresh do mesmo canal esta em andamento."""


class IndeterminateCommit(RuntimeError):
    """`COMMIT` falhou sem resposta conclusiva do servidor.

    O estado da publicacao e DESCONHECIDO: pode ter sido aplicada. Retry cego
    duplicaria ou reverteria trabalho as cegas. A operacao para aqui e exige
    leitura de reconciliacao antes de qualquer nova tentativa.
    """


@contextmanager
def channel_lock(neon_conn, channel: Channel, *, blocking: bool = False):
    """Lock de SESSAO no Neon, cobrindo leitura + publicacao.

    Entra e sai em autocommit para nao deixar transacao ociosa. A liberacao vai
    no `finally`: falha na leitura do Data Mart nao pode deixar o canal travado.
    """
    chave = ADVISORY_LOCK_KEYS[channel]
    autocommit_anterior = neon_conn.autocommit
    neon_conn.autocommit = True
    adquirido = False
    try:
        with neon_conn.cursor() as cur:
            if blocking:
                cur.execute("SELECT pg_advisory_lock(%s)", (chave,))
                adquirido = True
            else:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (chave,))
                linha = cur.fetchone()
                # RealDictCursor devolve mapping; cursor padrao devolve tupla.
                adquirido = bool(
                    linha["pg_try_advisory_lock"] if isinstance(linha, dict)
                    else linha[0]
                )
            if not adquirido:
                raise LockNotAcquired(
                    f"refresh de expedicao ja em andamento para {channel.value} "
                    f"(advisory key {chave})"
                )
        yield
    finally:
        # A LIBERACAO E CLEANUP e nunca pode SUBSTITUIR a excecao que trouxe o
        # fluxo ate aqui. Se a interrupcao chegou no meio da publicacao, a
        # transacao esta aberta e `autocommit = True` levanta
        # `set_session cannot be used inside a transaction` — medido contra o
        # psycopg2: um KeyboardInterrupt saia daqui como ProgrammingError, e um
        # `IndeterminateCommit` saia como falha generica, que o runbook le como
        # "fila anterior intacta". Os dois eram afirmacoes falsas.
        #
        # O lock e de SESSAO: quando a liberacao explicita nao acontece, ele
        # morre junto com a conexao, que o orquestrador fecha no proprio
        # `finally`. Perder a liberacao explicita custa uma conexao; mascarar a
        # excecao custa o diagnostico.
        if adquirido:
            try:
                neon_conn.autocommit = True
                with neon_conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (chave,))
            except Exception:  # noqa: BLE001, S110 — ver comentario acima
                pass
        try:
            neon_conn.autocommit = autocommit_anterior
        except Exception:  # noqa: BLE001, S110 — idem
            pass


def publish_channel(
    neon_conn,
    channel: Channel,
    fila_rows: list[dict],
    summary_rows: list[dict],
    *,
    source_health: SourceHealth,
    refresh_batch_id: str,
    expected_accounts,
    execute_values=None,
    relatorio: dict | None = None,
    destino: Destino = DESTINO_PADRAO,
) -> tuple[int, int]:
    """Substitui a fila DO CANAL e grava o resumo por conta, numa transacao.

    `destino` so' muda para a carga do export (EXPEDICAO-KOKESHI-EXPORT): as
    tabelas proprias e o DELETE restrito as contas do lote. Todas as barreiras
    abaixo valem igual para as duas publicacoes.

    Devolve `(linhas_fila, contas_registradas)`. `fila_rows` vazio com fonte
    saudavel e uma FOTOGRAFIA VAZIA legitima: o DELETE roda, nenhum INSERT de
    pedido roda, o resumo grava `backlog_count = 0` por conta e o commit
    acontece.

    Fonte nao saudavel levanta `SourceUnhealthy` ANTES de qualquer DELETE.

    `expected_accounts` e' OBRIGATORIO e vem do registry ativo. Sem uma fonte
    externa de verdade, "o resumo cobre todas as contas" nao e' verificavel: o
    proprio resumo seria juiz da propria cobertura. Com ele, `problemas_do_lote`
    recusa o lote ANTES do DELETE - ver `LoteIncoerente`.

    `relatorio`, quando passado, recebe depois do COMMIT: as colunas de etapa
    efetivamente escritas, a contagem por etapa (soma = linhas publicadas) e
    quantas linhas foram `outro`, com os valores brutos. `outro` nunca bloqueia.
    """
    if not source_health.can_publish:
        raise SourceUnhealthy(
            f"publicacao recusada para {channel.value}: fonte em "
            f"'{source_health.value}'. A fila anterior foi preservada."
        )

    if not summary_rows:
        # Fonte saudavel SEMPRE produz uma linha por conta esperada. Resumo
        # vazio significa lote montado errado — publicar a fila sem resumo
        # deixaria a tendencia sem a hora correspondente.
        raise ValueError(
            f"publicacao de {channel.value} sem resumo por conta: uma execucao "
            "saudavel grava uma linha por conta esperada, inclusive com zero."
        )

    if execute_values is None:
        from psycopg2.extras import execute_values as _ev  # noqa: PLC0415

        execute_values = _ev

    estranhos = {r["channel"] for r in fila_rows} - {channel.value}
    if estranhos:
        raise ValueError(
            f"publicacao de {channel.value} recebeu linhas de outro canal: "
            f"{sorted(estranhos)}"
        )
    estranhos_resumo = {r["channel"] for r in summary_rows} - {channel.value}
    if estranhos_resumo:
        raise ValueError(
            f"resumo de {channel.value} contem outro canal: {sorted(estranhos_resumo)}"
        )

    # Um unico lote: fila e resumo precisam ser rastreaveis como a MESMA
    # observacao. Lotes diferentes indicam montagem incoerente.
    lotes = {r["refresh_batch_id"] for r in fila_rows} | {
        r["refresh_batch_id"] for r in summary_rows
    }
    if lotes - {refresh_batch_id}:
        raise ValueError(
            f"refresh_batch_id inconsistente no lote: {sorted(lotes)} "
            f"(esperado {refresh_batch_id!r})"
        )

    # Uma conta nao pode publicar pedido com a marca de outra: a marca vem do
    # registry, por conta, e misturar indicaria resolucao errada.
    marca_por_conta = {r["shop_account"]: r["brand"] for r in summary_rows}
    for linha in fila_rows:
        esperada = marca_por_conta.get(linha["shop_account"])
        if esperada is not None and linha["brand"] != esperada:
            raise ValueError(
                f"conta {linha['shop_account']!r} publicou pedido com marca "
                f"{linha['brand']!r}, mas o registry resolve {esperada!r}"
            )

    # INVARIANTE ESTRUTURAL (EXP-3B2-H3). Ultima conferencia antes do DELETE:
    # daqui em diante a fila anterior deixa de existir, entao um lote que nao
    # fecha consigo mesmo nao pode passar. Fica DENTRO de `publish_channel`, e
    # nao no chamador, porque este e' o unico ponto por onde toda publicacao
    # passa - um caminho novo herda a protecao sem precisar lembrar dela.
    incoerencias = problemas_do_lote(
        channel.value, fila_rows, summary_rows, expected_accounts=expected_accounts
    )
    if incoerencias:
        raise LoteIncoerente(
            f"publicacao de {channel.value} recusada, fila anterior preservada: "
            + "; ".join(incoerencias)
        )

    # Etapa fora do dominio fechado e' lote mal montado: recusa ANTES do
    # DELETE, em vez de deixar o CHECK da 033 derrubar a transacao.
    dominio_etapa = {e.value for e in Etapa}
    fora_do_dominio = sorted(
        {str(r["etapa"]) for r in fila_rows if "etapa" in r} - dominio_etapa
    )
    if fora_do_dominio:
        raise ValueError(
            f"publicacao de {channel.value} com etapa fora do dominio: "
            f"{fora_do_dominio}"
        )
    # Atraso de 7 dias incoerente e' lote mal montado: recusa ANTES do DELETE,
    # como a etapa. `atraso_7d` ja' entrega so' valores validados; isto e' a
    # barreira do ponto unico por onde toda publicacao passa.
    incoerencias_7d = problemas_de_atraso_7d(summary_rows)
    if incoerencias_7d:
        raise ValueError(
            f"publicacao de {channel.value} com atraso de 7 dias incoerente: "
            + "; ".join(incoerencias_7d)
        )

    por_etapa = contagem_por_etapa(fila_rows)
    fontes_outro = sorted(
        {
            str(r.get("etapa_fonte"))
            for r in fila_rows
            if r.get("etapa") == Etapa.OUTRO.value
        }
    )[:_MAX_FONTES_OUTRO_NO_RELATORIO]

    if destino.contas:
        # Carga de conta: nada do lote pode cair fora das contas substituidas,
        # ou o DELETE restrito deixaria linha duplicada de outra conta.
        fora = ({r["shop_account"] for r in fila_rows}
                | {r["shop_account"] for r in summary_rows}) - set(destino.contas)
        if fora:
            raise ValueError(
                f"lote de {channel.value} com conta fora do destino: {sorted(fora)}"
            )
        delete_sql = _DELETE_SQL.format(tabela=destino.fila) + _SO_DAS_CONTAS_SQL
        delete_params: tuple = (channel.value, list(destino.contas))
    else:
        delete_sql = DELETE_CANAL_SQL
        delete_params = (channel.value,)

    colunas_etapa: tuple[str, ...] = ()
    colunas_7d: tuple[str, ...] = ()
    neon_conn.autocommit = False
    try:
        with neon_conn.cursor() as cur:
            cur.execute(delete_sql, delete_params)

            if fila_rows:
                colunas_etapa = colunas_de_etapa_no_destino(cur, destino.fila)
                colunas = FILA_COLUMNS + colunas_etapa
                execute_values(
                    cur,
                    f"INSERT INTO {destino.fila} ({', '.join(colunas)}) VALUES %s",
                    [tuple(r[c] for c in colunas) for r in fila_rows],
                )
            # SEMPRE gravado, inclusive com backlog zero: e a prova de que a
            # fotografia foi tirada naquela hora, por conta.
            resumo = destino.resumo.split(".")[-1]
            colunas_7d = colunas_de_atraso_7d_no_destino(cur, destino.resumo)
            colunas_resumo = SUMMARY_COLUMNS + colunas_7d
            execute_values(
                cur,
                f"INSERT INTO {destino.resumo} ({', '.join(colunas_resumo)}) VALUES %s "
                + summary_conflict_sql(resumo, colunas_7d),
                [
                    tuple(r[c] for c in SUMMARY_COLUMNS)
                    + tuple(r.get(c) for c in colunas_7d)
                    for r in summary_rows
                ],
            )
    except Exception:
        neon_conn.rollback()
        raise

    try:
        neon_conn.commit()
    except Exception as exc:  # noqa: BLE001 — convertido, sem retry
        raise IndeterminateCommit(
            f"COMMIT do canal {channel.value} sem resposta conclusiva; "
            "reconcilie antes de reexecutar."
        ) from exc

    outro = por_etapa.get(Etapa.OUTRO.value, 0)
    if outro:
        # Aviso, nunca bloqueio: valor novo da fonte e' contado e citado para
        # alguem classificar no contrato. Sao codigos de status, nao PII.
        _log.warning(
            "expedicao %s: %d linha(s) com etapa 'outro' (valor da fonte fora "
            "do mapeamento): %s",
            channel.value, outro, fontes_outro,
        )
    if relatorio is not None:
        relatorio["etapa_colunas_escritas"] = colunas_etapa
        relatorio["etapa_por_etapa"] = por_etapa
        relatorio["etapa_outro_count"] = outro
        relatorio["etapa_outro_fontes"] = fontes_outro
        relatorio["atraso_7d_colunas_escritas"] = colunas_7d

    return len(fila_rows), len(summary_rows)
