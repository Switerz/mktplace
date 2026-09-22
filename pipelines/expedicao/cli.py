"""CLI do refresh de expedicao.

    --diagnose   le a fonte e imprime agregados. Read-only, sem tocar no Neon.
    --apply      publica a fotografia do canal no Neon.

`--diagnose` NAO abre conexao gravavel e NAO registra em `audit.source_sync_run`:
diagnostico que aparece no audit log seria lido pelo health check como fonte
publicada com sucesso. Mesma regra de `sync_serving_snapshots`.

`--apply` E' A PROPRIA CONFIRMACAO
-----------------------------------
Nao existe segunda flag, feature flag escondida nem variavel de ambiente de
desbloqueio. Quem digita `--apply` esta confirmando; exigir um segundo gesto so'
ensinaria o operador a digitar os dois sempre, sem ler nenhum dos dois.

O QUE ESTE ARQUIVO FAZ — E O QUE ELE NAO FAZ
---------------------------------------------
Ele encadeia pecas que ja existem, na ordem certa, e traduz desfecho em exit
code. Ele nao classifica estado (`transform`), nao le a fonte (`shopee_extract`),
nao escreve fila (`publisher`) e nao formata auditoria (`audit`). O unico SQL
proprio e' o PREFLIGHT do target, que nao existia em lugar nenhum.

ORDEM DO `--apply`, E POR QUE ELA E' ESSA
------------------------------------------
    1. configuracao canonica (sem fallback inseguro)
    2. abre o target
    3. preflight: identidade, primary gravavel, schema da 018 presente
    4. channel_lock FAIL-FAST
    5. registry do canal
    6. refresh_batch_id (UUIDv4)
    7. auditoria `running` (conexao independente)
    8. abre a fonte read-only
    9. extracao + baseline
   10. valida saude e contas
   11. fila + resumos
   12. publicacao atomica
   13. auditoria `success`
   14. libera lock e fecha conexoes no `finally`

O lock vem ANTES de ler os insumos (4 antes de 8) de proposito: ler primeiro e
travar depois abriria uma janela em que outro processo publica entre a leitura e
a escrita, e a fotografia publicada descreveria um estado que ja' mudou.

A auditoria comeca ANTES da fonte (7 antes de 8) para que falha de leitura deixe
rastro. Ela usa conexao INDEPENDENTE: se compartilhasse a transacao da
publicacao, o rollback apagaria a evidencia da propria falha.

DESFECHOS QUE NAO PODEM SER CONFUNDIDOS
----------------------------------------
* fonte NAO saudavel      -> nada publicado, fila anterior preservada (exit 3)
* fonte saudavel e vazia  -> fotografia vazia PUBLICADA, fila limpa e resumos
                             zerados — resultado legitimo (exit 0)
* commit indeterminado    -> estado desconhecido: sem rollback, sem retry, sem
                             marcar success nem failed (exit 4)
* auditoria apos o commit -> dado publicado e rastro incompleto; jamais alegar
                             que houve reversao (exit 5)

SEM RETRY. Uma execucao, um desfecho. Repetir automaticamente sobre um commit
indeterminado e' exatamente como se duplica ou se apaga uma fila.

SEM NO_OP POR WATERMARK (EXP-1A-R)
-----------------------------------
A versao anterior pulava a publicacao quando nenhuma conta avancava. Isso estava
errado: `deadline_status`, `operational_age_status`, `is_slow_vs_baseline`,
`is_source_zombie` e `is_stalled` dependem do RELOGIO, nao so da fonte. Com a
fonte parada, um pedido ainda atravessa a faixa de 24h, o prazo nativo, as 48h,
os 30 dias e o 2 x p50 — e a fila publicada ficaria descrevendo um estado que
deixou de existir.

Comportamento atual: **toda execucao agendada recomputa e publica**. Sao cerca
de mil linhas; nao ha otimizacao a fazer que valha a chance de exibir estado
velho como se fosse atual. O watermark continua medido e auditado, mas como
METADADO DE FRESCOR (`source_advanced`), nunca como condicao para pular.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pipelines.expedicao import audit as audit_mod
from pipelines.expedicao import ml_extract, shopee_extract, transform
from pipelines.expedicao.contract import (
    FILA_TABLE,
    MARKETPLACE_ID,
    RUN_TABLE,
    Channel,
    FreshnessStatus,
    ExtractionResult,
    RegistryError,
    SellerAccount,
    SourceUnhealthy,
)
from pipelines.expedicao.publisher import (
    IndeterminateCommit,
    LockNotAcquired,
    channel_lock,
    publish_channel,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Nomes OFICIAIS ja usados pelo repositorio. Nenhum secret novo e' criado.
ENV_TARGET = "DATABASE_URL"
ENV_SOURCE = "DATAMART_DATABASE_URL"

CONNECT_TIMEOUT_SECONDS = 20

# ---------------------------------------------------------------------------
# Exit codes: cada desfecho operacional tem o seu, para que o runbook diga o que
# fazer sem depender de ler a mensagem.
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_FALHA = 1
EXIT_LOCK_OCUPADO = 2
EXIT_FONTE_NAO_PUBLICAVEL = 3
EXIT_COMMIT_INDETERMINADO = 4
EXIT_AUDITORIA_INCOMPLETA = 5
EXIT_PRECONDICAO = 6


class PreflightFalhou(RuntimeError):
    """Target fora de condicao de receber a publicacao, ou config ausente.

    Preocupacao de ORQUESTRACAO, nao de dominio — por isso vive aqui e nao em
    `contract.py`.
    """


def source_advanced(
    atual: dict[str, datetime | None], anterior: dict[str, datetime | None]
) -> bool:
    """Alguma conta avancou desde a ultima observacao?

    METADADO DE FRESCOR. Nao decide publicacao — decidia, e era o defeito 1.
    Serve para a auditoria responder "a fonte se mexeu?" sem que a resposta
    interfira em "o estado foi recomputado?" (que agora e sempre sim).

    Conta nova com carimbo conta como avanco. Carimbo nulo nao conta.
    """
    for conta, carimbo in atual.items():
        if carimbo is None:
            continue
        previo = anterior.get(conta)
        if previo is None or carimbo > previo:
            return True
    return False


def _agora_utc() -> datetime:
    """Unico ponto do pacote que le relogio. Sempre com fuso explicito.

    O instante e capturado UMA vez por execucao e injetado como `effective_at`
    em todas as funcoes de classificacao; nenhuma delas chama relogio.
    """
    return datetime.now(timezone.utc)


def _log_stderr(msg: str) -> None:
    """Sink das mensagens do `--apply`: stderr.

    O resultado produtivo deste comando sao as linhas publicadas e as de
    auditoria, nao o texto do terminal. Mandar falha para stdout faria um
    redirecionamento `2>` do agendador perder justamente o que importa.
    """
    print(msg, file=sys.stderr)


def _uuid4() -> str:
    """UUIDv4 por TENTATIVA REAL de publicacao.

    Nao e' derivado de timestamp, canal nem `effective_at`: duas tentativas no
    mesmo segundo precisam de identidades distintas, senao a auditoria nao
    consegue separa-las.
    """
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Diagnostico (read-only) — inalterado
# ---------------------------------------------------------------------------
def diagnose(conn, effective_at: datetime) -> dict:
    """Agregados da fonte. Sem ID de pedido, sem PII, sem escrita.

    Nao resolve registry: `marts.dim_seller_account` vive no Neon, e o
    diagnostico nao abre conexao la. Por isso as contas aparecem pelo
    `external_seller_id`, nunca pelo texto `brand` da fonte.
    """
    backlog = shopee_extract.fetch_backlog(conn)
    watermarks = shopee_extract.fetch_watermarks(conn)
    baselines = shopee_extract.fetch_baselines(conn, effective_at)

    por_conta: dict[str, dict] = {}
    for linha in backlog:
        conta = str(linha["shop_id"])
        bucket = por_conta.setdefault(
            conta,
            {
                "aguardando": 0,
                "overdue": 0,
                "due_within_24h": 0,
                "on_time": 0,
                "unavailable": 0,
                "over_48h": 0,
                "source_zombie": 0,
            },
        )
        bucket["aguardando"] += 1
        bucket[
            transform.classify_deadline(linha.get("ship_by_date"), effective_at).value
        ] += 1
        marco = transform.marco_inicial(linha.get("pay_time"), linha.get("create_time"))
        horas = transform.hours_between(marco, effective_at)
        if transform.classify_age(marco, effective_at).value == "over_48h":
            bucket["over_48h"] += 1
        if transform.is_source_zombie(horas):
            bucket["source_zombie"] += 1

    return {
        "effective_at": effective_at,
        "por_conta": por_conta,
        "watermarks": {w.external_seller_id: w.max_ingested_at for w in watermarks},
        "baselines": baselines,
    }


def diagnose_ml(conn, effective_at: datetime) -> dict:
    """Agregados do Mercado Livre. Sem ID de shipment, sem PII, sem escrita.

    Devolve o FUNIL inteiro, nao so' a fila: quantos candidatos existiam,
    quantos sairam por Full e quantos sairam por registro congelado. Mostrar
    apenas o numero final esconderia que 2 de cada 3 candidatos foram
    descartados, e um numero pequeno pareceria operacao em dia.
    """
    candidatos = ml_extract.fetch_candidates(conn)
    fila, diagnostico = ml_extract.classify_candidates(candidatos, effective_at)
    watermarks = ml_extract.fetch_watermarks(conn)

    por_conta: dict[str, dict] = {}
    for linha in fila:
        conta = str(linha["seller_id"])
        bucket = por_conta.setdefault(
            conta,
            {
                "marca": str(linha.get("brand") or "?"),
                "aguardando": 0,
                "over_48h": 0,
                "source_zombie": 0,
                "sem_prazo": 0,
            },
        )
        bucket["aguardando"] += 1
        # TODO do contrato, nao omissao: o ML nao entrega prazo de despacho,
        # entao TODA linha e' `unavailable`. A coluna existe para deixar isso
        # visivel no diagnostico em vez de sumir da tela.
        bucket["sem_prazo"] += 1
        marco = transform.normalizar_negocio_ml(linha.get("date_ready_to_ship"))
        if marco is None:
            marco = transform.normalizar_negocio_ml(linha.get("order_created_at"))
        horas = transform.hours_between(marco, effective_at)
        if transform.classify_age(marco, effective_at).value == "over_48h":
            bucket["over_48h"] += 1
        if transform.is_source_zombie(horas):
            bucket["source_zombie"] += 1

    return {
        "effective_at": effective_at,
        "diagnostico": diagnostico,
        "por_conta": por_conta,
        # `fetch_watermarks` ja devolve em UTC: normalizar de novo levantaria,
        # porque a funcao recusa carimbo aware de proposito (EXP-3B2-H1).
        "watermarks": {
            w.external_seller_id: w.max_ingested_at for w in watermarks
        },
    }


def format_diagnose_ml(resultado: dict) -> str:
    """Saida legivel. Agregados por CONTA — nenhum `shipment_id` impresso."""
    d = resultado["diagnostico"]
    linhas = [
        "DIAGNOSTICO expedicao/mercadolivre (read-only, nada publicado)",
        f"  effective_at: {resultado['effective_at'].isoformat()}",
        "",
        "  FUNIL DE EXCLUSAO",
        f"    candidatos (ready_to_ship + pedido pago) : {d['candidate_count']}",
        f"    seller-managed (fora Full)               : {d['seller_managed_count']}",
        f"    excluidos por Full (armazem do ML)       : {d['fulfillment_excluded_count']}",
        f"    excluidos por registro congelado         : {d['stale_source_record_count']}",
        f"    modalidade nao mapeada                   : {d['unmapped_logistic_type_count']}",
        f"    FILA                                     : {d['queue_count']}",
        "",
        "  SEM PRAZO: o Mercado Livre nao entrega prazo de despacho no armazem.",
        "  Nenhuma linha e' classificada como vencida, a vencer ou no prazo.",
        "",
        "  conta            marca        aguard  >48h  zumbi  s/prazo",
    ]
    for conta, b in sorted(resultado["por_conta"].items()):
        linhas.append(
            f"  {conta:<16} {b['marca']:<12} {b['aguardando']:>6} "
            f"{b['over_48h']:>5} {b['source_zombie']:>6} {b['sem_prazo']:>8}"
        )
    linhas.append("")
    linhas.append("  watermark por conta (extracted_at, ja em UTC):")
    for conta, carimbo in sorted(resultado["watermarks"].items()):
        linhas.append(f"    {conta:<16} {carimbo.isoformat() if carimbo else 'ausente'}")
    return "\n".join(linhas)



def format_diagnose(resultado: dict) -> str:
    """Saida legivel. Agregados por CONTA — nenhum `order_sn` impresso."""
    linhas = [
        "DIAGNOSTICO expedicao/shopee (read-only, nada publicado)",
        f"  effective_at: {resultado['effective_at'].isoformat()}",
        "",
        "  conta            aguard  overdue  due24h  on_time  s/prazo  >48h  zumbi",
    ]
    total = {
        "aguardando": 0, "overdue": 0, "due_within_24h": 0,
        "on_time": 0, "unavailable": 0, "over_48h": 0, "source_zombie": 0,
    }
    for conta, b in sorted(resultado["por_conta"].items()):
        for k in total:
            total[k] += b[k]
        linhas.append(
            f"  {conta:<15} {b['aguardando']:>6} {b['overdue']:>8} "
            f"{b['due_within_24h']:>7} {b['on_time']:>8} {b['unavailable']:>8} "
            f"{b['over_48h']:>5} {b['source_zombie']:>6}"
        )
    linhas.append(
        f"  {'TOTAL':<15} {total['aguardando']:>6} {total['overdue']:>8} "
        f"{total['due_within_24h']:>7} {total['on_time']:>8} "
        f"{total['unavailable']:>8} {total['over_48h']:>5} "
        f"{total['source_zombie']:>6}"
    )
    soma_prazo = (
        total["overdue"] + total["due_within_24h"]
        + total["on_time"] + total["unavailable"]
    )
    linhas.append("")
    linhas.append(
        f"  conferencia: as 4 categorias de prazo somam {soma_prazo} "
        f"= aguardando ({total['aguardando']}) "
        f"{'OK' if soma_prazo == total['aguardando'] else 'DIVERGENTE'}"
    )
    linhas.append(
        f"  >48h ({total['over_48h']}) e transversal: nao entra nessa soma."
    )
    linhas.append("")
    linhas.append("  watermark por conta (metadado de frescor, nao decide publicacao):")
    for conta, carimbo in sorted(resultado["watermarks"].items()):
        idade = (
            f"{(resultado['effective_at'] - carimbo).total_seconds() / 3600:.2f}h"
            if carimbo is not None else "sem carimbo"
        )
        linhas.append(f"    {conta:<15} {carimbo} (idade {idade})")
    linhas.append("")
    linhas.append("  baseline p50 (horas ate pickup, 30d, CANCELLED excluido):")
    for conta, (n, p50) in sorted(resultado["baselines"].items()):
        p50_txt = f"{p50:.2f}h" if p50 is not None else "sem p50"
        suficiente = "usavel" if n >= 100 else "amostra insuficiente"
        linhas.append(f"    {conta:<15} n={n:<6} p50={p50_txt:<10} ({suficiente})")
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Preflight e leitura de estado anterior (target)
# ---------------------------------------------------------------------------
def preflight_target(conn) -> None:
    """Identidade, gravabilidade e presenca do schema da migration 018.

    Falha FECHADA e ANTES de abrir a fonte: sem as tabelas da 018 nao ha onde
    publicar, e descobrir isso depois de ler o Data Mart so' gastaria a leitura
    e deixaria uma auditoria iniciada sem motivo.

    Nada aqui e' impresso — nem banco, nem usuario, nem host.

    O `rollback()` no fim NAO e' cosmetico. O target abre com `autocommit=False`,
    entao este SELECT inicia uma transacao; `channel_lock`, logo em seguida,
    precisa passar a conexao para `autocommit=True`, e o psycopg2 recusa isso
    dentro de uma transacao aberta (`set_session cannot be used inside a
    transaction`). Sem encerrar a leitura aqui, TODA execucao de `--apply`
    morreria antes de adquirir o lock. Nao ha o que commitar: e leitura.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT current_database() AS db, current_user AS usr,"
            " pg_is_in_recovery() AS replica,"
            " current_setting('transaction_read_only') AS somente_leitura,"
            " to_regclass(%s) IS NOT NULL AS tem_fila,"
            " to_regclass(%s) IS NOT NULL AS tem_resumo",
            (FILA_TABLE, RUN_TABLE),
        )
        linha = cur.fetchone()
    conn.rollback()

    if linha is None:
        raise PreflightFalhou("preflight do target nao retornou linha")

    def campo(nome: str, posicao: int):
        return linha[nome] if isinstance(linha, dict) else linha[posicao]

    if not campo("db", 0) or not campo("usr", 1):
        raise PreflightFalhou("target sem identidade de banco/usuario")
    if campo("replica", 2):
        raise PreflightFalhou("target esta em recovery: nao e' primary gravavel")
    if str(campo("somente_leitura", 3)).lower() == "on":
        raise PreflightFalhou("target esta em transaction_read_only")
    if not campo("tem_fila", 4) or not campo("tem_resumo", 5):
        raise PreflightFalhou(
            "schema da migration 018 ausente no target: aplicar a 018 e' "
            "precondicao EXTERNA deste comando"
        )


def previous_watermarks(conn, channel: Channel) -> dict[str, datetime | None]:
    """Ultimo `source_watermark_at` por conta, do resumo ja publicado.

    Alimenta `source_advanced`, que e' METADADO e nao decide publicacao. Na
    primeira execucao volta vazio, e toda conta com carimbo conta como avanco —
    a leitura correta de "a fonte avancou em relacao ao que se sabia".
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT shop_account, MAX(source_watermark_at) AS wm "
            f"FROM {RUN_TABLE} WHERE channel = %s GROUP BY shop_account",
            (channel.value,),
        )
        linhas = cur.fetchall() or []
    saida: dict[str, datetime | None] = {}
    for linha in linhas:
        conta = linha["shop_account"] if isinstance(linha, dict) else linha[0]
        carimbo = linha["wm"] if isinstance(linha, dict) else linha[1]
        saida[str(conta)] = carimbo
    return saida


# ---------------------------------------------------------------------------
# Conexoes canonicas — nenhum nome de secret novo
# ---------------------------------------------------------------------------
def _url(env: str) -> str:
    import os  # noqa: PLC0415

    url = os.environ.get(env, "")
    if not url:
        # Sem fallback: um default silencioso apontaria a publicacao para um
        # banco que ninguem escolheu.
        raise PreflightFalhou(f"{env} nao configurado.")
    return url


def open_target_default():
    """Neon, gravavel, transacao explicita (autocommit desligado)."""
    import psycopg2  # noqa: PLC0415
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    conn = psycopg2.connect(
        _url(ENV_TARGET),
        cursor_factory=RealDictCursor,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    )
    conn.autocommit = False
    return conn


def open_source_default():
    """Data Mart, READ-ONLY na propria sessao: o servidor recusa escrita."""
    import psycopg2  # noqa: PLC0415
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    conn = psycopg2.connect(
        _url(ENV_SOURCE),
        cursor_factory=RealDictCursor,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    )
    conn.set_session(readonly=True, autocommit=True)
    return conn


def open_audit_default():
    """Auditoria em conexao INDEPENDENTE da transacao de publicacao."""
    import psycopg2  # noqa: PLC0415
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    return psycopg2.connect(
        _url(ENV_TARGET),
        cursor_factory=RealDictCursor,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    )


# ---------------------------------------------------------------------------
# Orquestracao do --apply
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Adaptadores por canal (EXP-3B2-H2)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AdaptadorDeCanal:
    """O que muda de um canal para o outro — e SO isso.

    Lock, auditoria, publicacao atomica, ausencia de retry e o tratamento de
    excecao sao os MESMOS para todos os canais, de proposito: um segundo
    caminho de publicacao seria uma segunda chance de errar em cada uma dessas
    garantias.

    `load_registry` e o mesmo objeto para os dois canais: ele le
    `marts.dim_seller_account` parametrizado por `marketplace_id` e nao conhece
    Shopee nenhuma. Vive em `shopee_extract` por ordem de nascimento; mover o
    modulo agora inflaria este diff sem mudar comportamento.
    """

    load_registry: Callable[..., tuple[dict[str, SellerAccount], list[str]]]
    extract: Callable[..., ExtractionResult]
    build_fila: Callable[..., list[dict]]


def _fila_shopee(source, extracao, registry, effective_at, batch_id) -> list[dict]:
    """Shopee precisa do p50 por conta para `is_slow_vs_baseline`."""
    baselines = shopee_extract.fetch_baselines(source, effective_at)
    return transform.build_fila_shopee(
        extracao.backlog_rows, registry, baselines, effective_at, batch_id
    )


def _fila_ml(source, extracao, registry, effective_at, batch_id) -> list[dict]:
    """O ML nao tem baseline: a coorte de 7 dias nao sustenta amostra de 100.

    `source` entra na assinatura para manter a forma do adaptador; o build do
    ML nao precisa reabrir a fonte.
    """
    del source
    return transform.build_fila_ml(
        extracao.backlog_rows, registry, effective_at, batch_id
    )


#: ALLOWLIST de canais publicaveis. Canal ausente daqui nao publica, e a
#: ausencia e' explicita — nao depende de ninguem lembrar de escrever um `if`.
ADAPTADORES: dict[Channel, AdaptadorDeCanal] = {
    Channel.SHOPEE: AdaptadorDeCanal(
        load_registry=shopee_extract.load_registry,
        extract=shopee_extract.extract,
        build_fila=_fila_shopee,
    ),
    Channel.MERCADOLIVRE: AdaptadorDeCanal(
        load_registry=shopee_extract.load_registry,
        extract=ml_extract.extract,
        build_fila=_fila_ml,
    ),
}


# ---------------------------------------------------------------------------
# Reconciliacao DETERMINISTICA (EXP-3B2-H2)
# ---------------------------------------------------------------------------
#: Campos classificados que, para a MESMA chave e o MESMO `effective_at`, tem
#: que bater exatamente. Divergencia aqui nao e' churn da fonte: e' o codigo
#: classificando a mesma linha de dois jeitos.
CAMPOS_RECONCILIADOS = (
    "brand",
    "deadline_status",
    "operational_age_status",
    "is_stalled",
    "logistic_type",
)

#: Colunas lidas do destino. Alem dos campos comparados, traz
#: `source_ingested_at`, que NAO e comparado: e o DISCRIMINADOR entre
#: mutacao da fonte e drift do codigo. Ver `reconcile_channel`.
COLUNAS_PUBLICADAS = (
    "channel",
    "shop_account",
    "marketplace_order_id",
    *CAMPOS_RECONCILIADOS,
    "source_ingested_at",
)


def fingerprint_fila(linhas: list[dict]) -> str:
    """Impressao INDEPENDENTE DE ORDEM da fila.

    Ordenar por chave antes de somar evita que a mesma fotografia produza dois
    hashes so' porque o SELECT devolveu em outra ordem.
    """
    itens = sorted(
        "|".join(
            [
                str(x["channel"]),
                str(x["shop_account"]),
                str(x["marketplace_order_id"]),
                *(str(x.get(c)) for c in CAMPOS_RECONCILIADOS),
            ]
        )
        for x in linhas
    )
    return hashlib.sha256("\n".join(itens).encode()).hexdigest()


def published_snapshot(target, channel: Channel) -> dict:
    """Le do destino a fotografia publicada do canal, em UM snapshot.

    As duas consultas (cabecalho e linhas) correm dentro de
    `REPEATABLE READ, READ ONLY`. Em READ COMMITTED elas poderiam cair de lados
    diferentes de uma republicacao e a reconciliacao compararia o cabecalho de
    um lote com as linhas de outro — divergencia inventada pela leitura.

    Um canal com mais de um batch na fila e inconsistencia, nao ambiguidade a
    resolver: levanta.
    """
    autocommit_anterior = getattr(target, "autocommit", None)
    if autocommit_anterior:
        target.autocommit = False
    try:
        with target.cursor() as cur:
            cur.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            cur.execute(
                "SELECT DISTINCT refresh_batch_id, effective_at "
                f"FROM {FILA_TABLE} WHERE channel = %s",
                (channel.value,),
            )
            lotes = [dict(r) for r in cur.fetchall()]
            if not lotes:
                return {"effective_at": None, "refresh_batch_id": None, "linhas": []}
            if len(lotes) > 1:
                raise SourceUnhealthy(
                    f"fila de {channel.value} tem {len(lotes)} lotes simultaneos; "
                    "a substituicao por canal deveria deixar exatamente um."
                )
            cur.execute(
                f"SELECT {', '.join(COLUNAS_PUBLICADAS)} FROM {FILA_TABLE} "
                "WHERE channel = %s",
                (channel.value,),
            )
            linhas = [dict(r) for r in cur.fetchall()]
    finally:
        with suppress(Exception):
            target.rollback()
        if autocommit_anterior:
            with suppress(Exception):
                target.autocommit = True
    return {
        "effective_at": lotes[0]["effective_at"],
        "refresh_batch_id": lotes[0]["refresh_batch_id"],
        "linhas": linhas,
    }


def reconcile_channel(
    target,
    source,
    channel: Channel,
    *,
    open_registry=None,
) -> dict:
    """Reconcilia o PUBLICADO contra uma recomputacao no MESMO instante.

    POR QUE NAO SE EXIGE FINGERPRINT IGUAL ENTRE DOIS INSTANTES
    -----------------------------------------------------------
    Medido no EXP-3B2-P2: a candidata do ML foi de 888 linhas as 21:19 para 885
    as 21:52, e `over_48h` saltou de 269 para 358 em 33 minutos. Duas causas
    independentes: a fonte muda (shipment entra e sai de `ready_to_ship`) e o
    RELOGIO muda (a idade reclassifica sozinha). Exigir hashes iguais entre o
    dry-run e o apply reprovaria toda execucao saudavel — e, pior, ensinaria
    quem opera a afrouxar o criterio no meio de um incidente.

    A ancora e o `effective_at` DO BATCH PUBLICADO. `build_fila_*` e funcao pura
    de (linhas, registry, effective_at): recomputando no mesmo instante, a
    classificacao e reproduzivel.

    AS TRES DIFERENCAS, QUE NAO SAO A MESMA COISA
    ----------------------------------------------
    1. CHURN de chave — o shipment entrou ou saiu de `ready_to_ship` entre a
       publicacao e esta leitura. Contado, reportado, nunca fatal.

    2. MUTACAO DA MESMA CHAVE NA FONTE — a linha foi RELIDA pelo job de
       ingestao depois da publicacao e o conteudo dela mudou de verdade
       (substatus avancou, modalidade corrigida, prontidao recarimbada). A
       entrada e OUTRA, entao a saida ser outra nao prova nada contra o
       transform. Contado, reportado, nunca fatal.

    3. DRIFT DETERMINISTICO — mesma chave, MESMA entrada, mesmo instante, e
       ainda assim classificacao diferente. Isso e o codigo se contradizendo.
       Falha FECHADA.

    O discriminador entre (2) e (3) esta no proprio dado: `source_ingested_at`
    e o carimbo de quando AQUELA linha foi lida da fonte, e o publicado guarda
    o valor que valia no apply. Se o publicado e o recomputado carregam o MESMO
    `source_ingested_at`, a linha nao foi relida no intervalo — a entrada e
    identica e a comparacao e legitima. Se diferem, a fonte releu, e comparar
    classificacao seria comparar coisas diferentes.

    A revisao EXP-3B2-H2-R/V encontrou exatamente esse defeito: sem o
    discriminador, mudar `logistic_type` na fonte entre o apply e o reconcile
    era reportado como "DRIFT MATERIAL". Alarme falso em operacao normal ensina
    a ignorar o alarme.

    O QUE ESTE RESULTADO PROVA — E O QUE NAO PROVA
    -----------------------------------------------
    Prova determinismo SOBRE O SUBCONJUNTO COMPARAVEL. `comparaveis == 0`
    significa INCONCLUSIVO, nao "equivalente": nao houve uma linha sequer com
    entrada identica para comparar. O campo `veredito` diz qual dos dois e.
    """
    publicado = published_snapshot(target, channel)
    if publicado["effective_at"] is None:
        raise SourceUnhealthy(
            f"nao ha fotografia publicada de {channel.value} para reconciliar."
        )

    efetivo = publicado["effective_at"]
    adaptador = ADAPTADORES.get(channel)
    if adaptador is None:
        raise PreflightFalhou(f"canal {channel.value} sem adaptador de reconciliacao.")

    carregar = open_registry or adaptador.load_registry
    registry, problemas = carregar(target, MARKETPLACE_ID[channel])
    if problemas:
        raise SourceUnhealthy(f"registry ambiguo: {problemas}")
    if not registry:
        raise PreflightFalhou(f"registry sem conta ativa para {channel.value}.")

    extracao = adaptador.extract(source, efetivo, frozenset(registry))
    if not extracao.source_health.can_publish:
        raise SourceUnhealthy(
            f"fonte em '{extracao.source_health.value}' na reconciliacao: "
            f"{extracao.detail}"
        )
    recomputada = adaptador.build_fila(
        source, extracao, registry, efetivo, publicado["refresh_batch_id"]
    )

    def chave(x):
        return (x["channel"], str(x["shop_account"]), str(x["marketplace_order_id"]))

    pub = {chave(x): x for x in publicado["linhas"]}
    rec = {chave(x): x for x in recomputada}
    comuns = set(pub) & set(rec)

    comparaveis, mutadas, divergentes = 0, 0, []
    for k in sorted(comuns):
        if pub[k].get("source_ingested_at") != rec[k].get("source_ingested_at"):
            # A fonte releu esta linha depois do apply: entrada diferente.
            mutadas += 1
            continue
        comparaveis += 1
        for campo in CAMPOS_RECONCILIADOS:
            a, b = pub[k].get(campo), rec[k].get(campo)
            if a != b:
                # A chave NAO entra na mensagem: e identificador operacional.
                divergentes.append(f"{campo}: publicado={a!r} recomputado={b!r}")

    if divergentes:
        raise SourceUnhealthy(
            f"DRIFT DETERMINISTICO na reconciliacao de {channel.value}: "
            f"{len(divergentes)} campo(s) classificados de forma diferente para a "
            f"MESMA chave, com a MESMA entrada (source_ingested_at identico) e o "
            f"MESMO effective_at. Exemplos: {sorted(set(divergentes))[:3]}"
        )

    duplicadas = len(publicado["linhas"]) - len(pub)
    if duplicadas:
        raise SourceUnhealthy(
            f"fila de {channel.value} tem {duplicadas} chave(s) duplicada(s) "
            "no destino."
        )

    return {
        "channel": channel.value,
        "effective_at": efetivo,
        "refresh_batch_id": publicado["refresh_batch_id"],
        "publicadas": len(pub),
        "recomputadas": len(rec),
        "chaves_comuns": len(comuns),
        "comparaveis": comparaveis,
        "mutadas_na_fonte": mutadas,
        "somente_publicadas": len(set(pub) - set(rec)),
        "somente_recomputadas": len(set(rec) - set(pub)),
        "campos_divergentes": 0,
        "duplicadas": duplicadas,
        "fingerprint_publicado": fingerprint_fila(publicado["linhas"]),
        "fingerprint_recomputado": fingerprint_fila(recomputada),
        "veredito": (
            "deterministico_no_subconjunto_comparavel" if comparaveis
            else "inconclusivo_sem_linha_comparavel"
        ),
    }

def run_apply(
    channel: Channel,
    effective_at: datetime,
    *,
    open_target=open_target_default,
    open_source=open_source_default,
    open_audit=open_audit_default,
    uuid_factory=_uuid4,
    execute_values=None,
    log=_log_stderr,
) -> int:
    """Executa UMA publicacao e devolve o exit code. Nunca faz retry.

    `effective_at` chega pronto: o relogio e' lido em `main`, uma unica vez.
    Toda dependencia externa entra por parametro para que o teste exercite o
    fluxo real sem abrir conexao.
    """

    def avisar(msg: str) -> None:
        """Escrever a mensagem nunca decide o desfecho.

        Um `BrokenPipeError` em stderr — comum em job agendado com a saida
        fechada — nao pode transformar uma publicacao commitada numa excecao sem
        exit code.
        """
        with suppress(Exception):
            log(msg)

    # GUARDA DE CANAL — depois de `avisar` existir, nao antes.
    #
    # A versao anterior chamava `avisar` acima da propria definicao. Nunca
    # disparou porque `--channel` so' aceitava shopee, entao o branch era
    # inalcancavel; ao abrir o argumento para o ML o defeito latente virou
    # `UnboundLocalError`. Fica aqui, e ainda assim ANTES de qualquer conexao.
    # A trava incondicional do Mercado Livre saiu no EXP-3B2-H2. O que impede
    # uma publicacao indevida agora sao as MESMAS barreiras do Shopee, todas
    # fail-closed e todas medidas: registry vazio ou ambiguo levanta antes de
    # abrir auditoria, `SOURCE_STALE` recusa antes do DELETE, modalidade
    # desconhecida bloqueia a fila inteira e o lock impede segunda execucao.
    #
    # O canal continua em ALLOWLIST: canal sem adaptador nao publica.
    adaptador = ADAPTADORES.get(channel)
    if adaptador is None:
        avisar(f"canal {channel.value} ainda nao suportado pelo --apply.")
        return EXIT_PRECONDICAO

    marketplace_id = MARKETPLACE_ID[channel]

    target = source = auditoria = None
    run_id: int | None = None
    publicado = False
    extraidas = 0

    try:
        target = open_target()
        preflight_target(target)

        # Lock ANTES de ler os insumos que serao publicados.
        with channel_lock(target, channel, blocking=False):
            registry, problemas = adaptador.load_registry(target, marketplace_id)
            if not registry and not problemas:
                # PRECONDICAO externa, nao defeito de dado: nada foi tentado
                # contra a fonte, e abrir uma execucao de auditoria aqui
                # registraria uma tentativa que nao houve.
                raise PreflightFalhou(
                    f"registry sem conta ativa para {channel.value}: cadastrar as "
                    "contas em marts.dim_seller_account e' precondicao EXTERNA "
                    "deste comando"
                )

            batch_id = uuid_factory()
            esperadas = frozenset(registry)
            anteriores = previous_watermarks(target, channel)

            auditoria = open_audit()
            run_id = audit_mod.audit_start(
                auditoria, audit_mod.source_name_for(channel), marketplace_id
            )

            source = open_source()
            # `problemas` (registry ambiguo) entra aqui de proposito: `extract` ja
            # classifica isso como REGISTRY_AMBIGUOUS, e reimplementar a decisao
            # criaria uma segunda definicao de "registry confiavel".
            extracao = adaptador.extract(
                source, effective_at, esperadas, registry_problems=problemas
            )
            extraidas = extracao.backlog_count

            if not extracao.source_health.can_publish:
                # Fonte doente: nada e' apagado nem inserido, e a fila anterior
                # segue no ar. Diferente de fonte saudavel com backlog vazio,
                # que SEGUE para a publicacao logo abaixo.
                raise SourceUnhealthy(
                    f"fonte em '{extracao.source_health.value}': {extracao.detail}"
                )

            nomes = extracao.account_shop_names or {}
            faltando = sorted(esperadas - set(nomes))
            if faltando:
                raise SourceUnhealthy(
                    f"{len(faltando)} conta(s) esperada(s) sem nome na fonte; "
                    "publicacao recusada para nao limpar a fila de uma loja que "
                    "apenas sumiu da leitura"
                )

            fila = adaptador.build_fila(
                source, extracao, registry, effective_at, batch_id
            )

            contas = {ext: (nomes[ext], registry[ext].brand_key) for ext in esperadas}
            atuais = {
                nomes[ext]: extracao.account_watermarks.get(ext) for ext in esperadas
            }
            avancou = source_advanced(atuais, anteriores)

            resumos = transform.build_account_summaries(
                fila,
                effective_at,
                channel=channel.value,
                refresh_batch_id=batch_id,
                accounts=contas,
                watermarks=extracao.account_watermarks,
                source_advanced=avancou,
            )

            linhas, contas_registradas = publish_channel(
                target,
                channel,
                fila,
                resumos,
                source_health=extracao.source_health,
                refresh_batch_id=batch_id,
                execute_values=execute_values,
            )
            publicado = True

            # A partir daqui o dado ESTA publicado. Qualquer falha abaixo e'
            # rastro incompleto, nunca reversao.
            try:
                audit_mod.finish_after_commit(auditoria, run_id, extraidas, linhas)
                audit_mod.record_observation(
                    auditoria,
                    channel,
                    marketplace_id,
                    effective_at,
                    source_health=extracao.source_health,
                    backlog_count=extraidas,
                    expected_accounts=extracao.expected_accounts,
                    observed_accounts=extracao.observed_accounts,
                    watermarks=extracao.account_watermarks,
                    source_advanced=avancou,
                    published=True,
                    accounts_recorded=contas_registradas,
                )
                audit_mod.record_freshness(
                    auditoria,
                    marketplace_id,
                    _freshness_por_marca(fila, resumos, effective_at),
                )
            except Exception as exc:  # noqa: BLE001 — pos-commit: nunca vira failed
                avisar(
                    "publicacao COMMITADA e auditoria incompleta (nenhuma reversao "
                    f"ocorreu): {audit_mod.sanitize_error_message(exc)}"
                )
                return EXIT_AUDITORIA_INCOMPLETA

        vazia = " (fotografia vazia)" if linhas == 0 else ""
        avisar(
            f"publicado{vazia}: {linhas} pedido(s), "
            f"{contas_registradas} conta(s), batch {batch_id}"
        )
        return EXIT_OK

    except LockNotAcquired as exc:
        # Lock ocupado: nao houve leitura da fonte nem auditoria iniciada.
        log(audit_mod.sanitize_error_message(exc))
        return EXIT_LOCK_OCUPADO

    except PreflightFalhou as exc:
        log(audit_mod.sanitize_error_message(exc))
        return EXIT_PRECONDICAO

    except IndeterminateCommit as exc:
        # Estado DESCONHECIDO. Sem rollback, sem retry e sem marcar a auditoria
        # de um jeito ou de outro: nenhum dos dois tem prova.
        log(audit_mod.sanitize_error_message(exc))
        return EXIT_COMMIT_INDETERMINADO

    except (SourceUnhealthy, RegistryError) as exc:
        _falhar_auditoria(auditoria, run_id, exc, publicado, extraidas, log)
        log(audit_mod.sanitize_error_message(exc))
        return EXIT_FONTE_NAO_PUBLICAVEL

    except Exception as exc:  # noqa: BLE001 — fronteira do orquestrador
        if publicado:
            # Falha DEPOIS do commit — por exemplo na liberacao do lock. Exit 1
            # diria "falha anterior ao commit, fila anterior intacta", que seria
            # mentira: a fila nova ja esta publicada.
            avisar(
                "publicacao COMMITADA e finalizacao incompleta (nenhuma reversao "
                f"ocorreu): {audit_mod.sanitize_error_message(exc)}"
            )
            return EXIT_AUDITORIA_INCOMPLETA
        _falhar_auditoria(auditoria, run_id, exc, publicado, extraidas, log)
        avisar(f"FALHA: {audit_mod.sanitize_error_message(exc)}")
        return EXIT_FALHA

    finally:
        # KeyboardInterrupt e SystemExit tambem passam por aqui: o cleanup
        # acontece e a interrupcao segue propagando, sem virar falha comum.
        for conn in (source, auditoria, target):
            if conn is not None:
                with suppress(Exception):
                    conn.close()


def _falhar_auditoria(auditoria, run_id, exc, publicado, extraidas, log) -> None:
    """Marca `failed` SO quando a falha e' comprovadamente anterior ao commit."""
    if auditoria is None or run_id is None or publicado:
        return
    try:
        audit_mod.audit_finish(
            auditoria,
            run_id,
            "failed",
            extraidas,
            0,
            error=audit_mod.sanitize_error_message(exc),
        )
    except Exception as falha:  # noqa: BLE001 — rastro, nao desfecho
        log(
            "auditoria de falha nao registrada: "
            f"{audit_mod.sanitize_error_message(falha)}"
        )


#: Pior estado vence na consolidacao por marca. `unknown` e' o pior porque
#: "nao sei" nunca pode se apresentar como "fresco".
_ORDEM_FRESCOR = {"fresh": 0, "stale": 1, "critical": 2, "unknown": 3}


def _frescor_da_fonte(watermark, effective_at) -> str:
    """Frescor do watermark, com carimbo do FUTURO tratado como desconhecido.

    `classify_freshness` compara `effective_at - watermark <= 8h`, e idade
    NEGATIVA satisfaz essa condicao: um watermark adiantado sairia `fresh`. Seria
    o MESMO defeito que este gate corrige — sinal quebrado se apresentando como
    saudavel. O relogio do Data Mart e o de quem roda a CLI sao maquinas
    diferentes, e `ingested_at` e escrito pelo carregador: divergencia acontece.

    `unknown` e a unica resposta honesta. A partir de um carimbo impossivel nao
    da para afirmar que a fonte esta fresca NEM que esta velha — `critical`
    seria tao inventado quanto `fresh`.

    A comparacao e estrita de proposito: qualquer tolerancia seria um threshold
    novo, e o contrato so define 8h e 24h. Se skew de segundos gerar `warn` na
    operacao, a tolerancia vira decisao de contrato, nao de implementacao.
    """
    if watermark is None or watermark > effective_at:
        return FreshnessStatus.UNKNOWN.value
    return transform.classify_freshness(watermark, effective_at).value


def _freshness_por_marca(fila, resumos, effective_at) -> dict[str, dict]:
    """Frescor da FONTE por marca, medido pelo WATERMARK DA CONTA.

    TRES IDADES DIFERENTES, QUE NAO PODEM SER A MESMA COISA
    --------------------------------------------------------
    1. `source_watermark_at` — quando a conta INTEIRA foi lida pela ultima vez.
       Responde "a fonte esta atualizada?". E o unico insumo do veredito deste
       alerta.
    2. `source_ingested_at` de cada pedido — quando AQUELA LINHA foi relida.
       Um pedido parado no backlog nao e' relido enquanto nada nele muda, entao
       essa idade cresce sozinha. Entra aqui so' como CONTEXTO.
    3. `hours_open` / `deadline_status` — ha quanto tempo o PEDIDO espera
       expedicao. E atraso operacional, vive no resumo e nao neste alerta.

    POR QUE A VERSAO ANTERIOR ESTAVA ERRADA (EXP-1E)
    ------------------------------------------------
    Ela agregava o pior `source_freshness_status` das LINHAS da fila, ou seja, a
    idade (2). Backlog legitimo sempre tem pedido cuja linha nao e' relida ha
    mais de 24h — medimos 240h na rituaria com a fonte a 0,55h de idade. As
    quatro marcas nasceram `fail`/`high` no primeiro piloto. Alerta permanente
    e' alerta ignorado: foi assim que a planilha antiga ficou 43 dias defasada
    sem ninguem perceber. `FreshnessStatus` ja dizia no contrato "idade do DADO,
    nao do pedido, medida por conta" — a implementacao e' que divergia.

    AGREGACAO POR MARCA
    -------------------
    Uma marca pode ter mais de uma conta. O veredito e' o PIOR estado entre as
    contas dela, nunca a media nem a melhor: uma conta parada nao pode se
    esconder atras de outra atualizada. Carimbo ausente vira `unknown`, que e' o
    pior — "nao sei" nunca se apresenta como "fresco".

    Os limites sao os do contrato (`classify_freshness`: 8h / 24h). Nenhum
    threshold novo e' criado aqui.
    """
    # Contexto (2): idade da linha mais velha do backlog, por marca. NAO decide.
    linha_mais_velha: dict[str, float] = {}
    for linha in fila:
        idade = transform.hours_between(linha["source_ingested_at"], effective_at)
        if idade is None:
            continue
        marca = linha["brand"]
        if idade > linha_mais_velha.get(marca, -1.0):
            linha_mais_velha[marca] = idade

    # Agrupa primeiro, decide depois: com as contas da marca em maos o "pior"
    # fica explicito, em vez de emergir da ordem em que os resumos chegaram.
    por_marca: dict[str, list[dict]] = {}
    for resumo in resumos:
        por_marca.setdefault(resumo["brand"], []).append(resumo)

    saida: dict[str, dict] = {}
    for marca, contas in por_marca.items():
        estados = [
            _frescor_da_fonte(c["source_watermark_at"], effective_at) for c in contas
        ]
        pior = max(estados, key=lambda e: _ORDEM_FRESCOR[e])

        # Carimbo reportado: o mais ANTIGO entre as contas da marca. `None`
        # vence, porque conta sem carimbo e' o caso mais grave.
        # Carimbo do futuro tambem invalida o reportado: exibir um instante
        # impossivel como "ultima leitura" sugeriria que houve leitura.
        carimbos = [c["source_watermark_at"] for c in contas]
        invalido = any(w is None or w > effective_at for w in carimbos)
        watermark = None if invalido else min(carimbos)

        saida[marca] = {
            # (1) VEREDITO: a fonte esta atualizada? So o watermark responde.
            "freshness": pior,
            "source_watermark": watermark,
            "source_age_hours": transform.hours_between(watermark, effective_at),
            "accounts": len(contas),
            # tamanho do backlog — contexto, nunca veredito
            "open_orders": sum(int(c["backlog_count"]) for c in contas),
            # (2) pedido antigo no backlog — informativo, NAO reprova a fonte
            "oldest_row_age_hours": linha_mais_velha.get(marca),
        }
    return saida


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="expedicao",
        description=(
            "Refresh de expedicao por canal. Sem --apply, apenas diagnostico "
            "read-only."
        ),
    )
    p.add_argument(
        "--channel", default=Channel.SHOPEE.value,
        choices=[Channel.SHOPEE.value, Channel.MERCADOLIVRE.value],
        help=(
            "canal a processar. `shopee` diagnostica e publica; "
            "`mercadolivre` so' diagnostica (nucleo do EXP-3B1, sem piloto)"
        ),
    )
    p.add_argument(
        "--diagnose", action="store_true",
        help="le a fonte e imprime agregados; nao abre conexao gravavel",
    )
    p.add_argument(
        "--apply", action="store_true",
        help=(
            "publica a fotografia no Neon. A flag E a confirmacao; requer a "
            "migration 018 aplicada e o registry cadastrado."
        ),
    )

    p.add_argument(
        "--reconcile", action="store_true",
        help=(
            "READ-ONLY: recomputa a candidata no MESMO effective_at do batch "
            "publicado e compara. Nao toma lock, nao escreve, nao republica."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    # `parse_args` ANTES de carregar `.env`: `--help` e argumento invalido saem
    # por SystemExit aqui, sem ler segredo, abrir conexao ou tocar auditoria.
    args = build_parser().parse_args(argv)

    escolhidos = [args.apply, args.diagnose, args.reconcile]
    if sum(bool(x) for x in escolhidos) > 1:
        print("--apply, --diagnose e --reconcile sao mutuamente exclusivos.",
              file=sys.stderr)
        return EXIT_FALHA
    if not any(escolhidos):
        print("informe --diagnose, --reconcile ou --apply.", file=sys.stderr)
        return EXIT_FALHA

    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    canal = Channel(args.channel)
    effective_at = _agora_utc()

    if args.diagnose:
        print("MODO DIAGNOSTICO: nenhuma escrita, nenhuma conexao gravavel.")
        try:
            return _run_diagnose(canal, effective_at)
        except Exception as exc:  # noqa: BLE001 — fronteira do CLI
            print(
                f"FALHA (diagnose/{canal.value}): "
                f"{audit_mod.sanitize_error_message(exc)}",
                file=sys.stderr,
            )
            return EXIT_FALHA

    if args.reconcile:
        print("MODO RECONCILIACAO: read-only, sem lock e sem escrita.")
        try:
            return _run_reconcile(canal)
        except Exception as exc:  # noqa: BLE001 — fronteira do CLI
            print(
                f"FALHA (reconcile/{canal.value}): "
                f"{audit_mod.sanitize_error_message(exc)}",
                file=sys.stderr,
            )
            return EXIT_FONTE_NAO_PUBLICAVEL

    print(
        f"MODO APPLY ({canal.value}): publicacao transacional, sem retry. "
        f"effective_at={effective_at.isoformat()}"
    )
    return run_apply(canal, effective_at)



def _run_reconcile(canal: Channel) -> int:
    """READ-ONLY: abre as duas fontes sem permissao de escrita e compara.

    O destino entra com `readonly=True` de proposito. A reconciliacao existe
    para CONFERIR a publicacao, e uma conexao gravavel aqui daria a ela o
    poder de consertar o que deveria apenas denunciar.
    """
    import os  # noqa: PLC0415

    import psycopg2  # noqa: PLC0415
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    alvo = os.environ.get(ENV_TARGET, "")
    fonte = os.environ.get(ENV_SOURCE, "")
    if not alvo or not fonte:
        print("DATABASE_URL e DATAMART_DATABASE_URL sao obrigatorias.",
              file=sys.stderr)
        return EXIT_FALHA

    target = psycopg2.connect(alvo, cursor_factory=RealDictCursor,
                              connect_timeout=CONNECT_TIMEOUT_SECONDS)
    target.set_session(readonly=True, autocommit=True)
    source = psycopg2.connect(fonte, cursor_factory=RealDictCursor,
                              connect_timeout=CONNECT_TIMEOUT_SECONDS)
    source.set_session(readonly=True, autocommit=True)
    try:
        r = reconcile_channel(target, source, canal)
    finally:
        target.close()
        source.close()

    print(format_reconcile(r))
    return EXIT_OK


def format_reconcile(r: dict) -> str:
    """Saida legivel. Nenhum `shipment_id` nem `order_sn` impresso."""
    return "\n".join([
        f"RECONCILIACAO {r['channel']} (read-only)",
        f"  effective_at do batch : {r['effective_at']}",
        f"  batch                 : {r['refresh_batch_id']}",
        "",
        f"  publicadas            : {r['publicadas']}",
        f"  recomputadas          : {r['recomputadas']}",
        f"  chaves em comum       : {r['chaves_comuns']}",
        "",
        "  AS TRES DIFERENCAS, que nao sao a mesma coisa:",
        f"    churn de chave      : {r['somente_publicadas']} sairam, "
        f"{r['somente_recomputadas']} entraram",
        f"    mutadas na fonte    : {r['mutadas_na_fonte']}  "
        "(relidas apos o apply: entrada diferente, nao comparaveis)",
        f"    comparaveis         : {r['comparaveis']}  "
        "(mesma entrada: e sobre estas que o veredito fala)",
        f"    divergencias        : {r['campos_divergentes']}",
        "",
        f"  fingerprint publicado  : {r['fingerprint_publicado']}",
        f"  fingerprint recomputado: {r['fingerprint_recomputado']}",
        f"  VEREDITO               : {r['veredito']}",
        "",
        "  Os dois fingerprints so coincidem se NADA mudou na fonte entre a",
        "  publicacao e esta leitura. Diferenca entre eles e churn ou mutacao,",
        "  nao defeito. O que reprova e divergencia sobre linha COMPARAVEL.",
    ])

def _run_diagnose(canal: Channel, effective_at: datetime) -> int:
    import os  # noqa: PLC0415

    import psycopg2  # noqa: PLC0415
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    url = os.environ.get("DATAMART_DATABASE_URL", "")
    if not url:
        print("DATAMART_DATABASE_URL nao configurada.", file=sys.stderr)
        return EXIT_FALHA

    conn = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=10)
    # READ-ONLY na propria sessao: mesmo que algum SQL futuro tente escrever, o
    # servidor recusa. Nao depende de disciplina de quem edita a query.
    conn.set_session(readonly=True, autocommit=True)
    try:
        if canal is Channel.MERCADOLIVRE:
            resultado = diagnose_ml(conn, effective_at)
            saida = format_diagnose_ml(resultado)
        else:
            resultado = diagnose(conn, effective_at)
            saida = format_diagnose(resultado)
    finally:
        conn.close()

    print(saida)
    print()
    print(f"  marketplace_id (audit): {MARKETPLACE_ID[canal]}")
    print("  nenhuma linha publicada; nenhum registro em audit.source_sync_run.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
