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
import sys
import uuid
from contextlib import suppress
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
    RegistryError,
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
        "watermarks": {
            w.external_seller_id: transform.normalizar_ingestao_ml(w.max_ingested_at)
            for w in watermarks
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
    if channel is Channel.MERCADOLIVRE:
        # EXP-3B1 entrega o NUCLEO do Mercado Livre: extrator, transformacao,
        # allowlist de modalidade e coorte de confiabilidade, tudo coberto por
        # teste. Publicar e' gate proprio (EXP-3B2), e depende de duas
        # precondicoes EXTERNAS que ainda nao existem: as contas do ML em
        # `marts.dim_seller_account` (hoje so' ha' marketplace_id = 3) e a
        # decisao do responsavel sobre publicar uma fila SEM prazo.
        avisar(
            "canal mercadolivre: --apply bloqueado no EXP-3B1. O nucleo esta "
            "implementado e testado; publicar e' o EXP-3B2, que exige cadastrar "
            "as contas do ML no registry e decidir sobre fila sem prazo. "
            "Use --diagnose."
        )
        return EXIT_PRECONDICAO

    if channel is not Channel.SHOPEE:
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
            registry, problemas = shopee_extract.load_registry(target, marketplace_id)
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
            extracao = shopee_extract.extract(
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

            baselines = shopee_extract.fetch_baselines(source, effective_at)
            fila = transform.build_fila_shopee(
                extracao.backlog_rows, registry, baselines, effective_at, batch_id
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
    return p


def main(argv: list[str] | None = None) -> int:
    # `parse_args` ANTES de carregar `.env`: `--help` e argumento invalido saem
    # por SystemExit aqui, sem ler segredo, abrir conexao ou tocar auditoria.
    args = build_parser().parse_args(argv)

    if args.apply and args.diagnose:
        print("--apply e --diagnose sao mutuamente exclusivos.", file=sys.stderr)
        return EXIT_FALHA
    if not args.apply and not args.diagnose:
        print("informe --diagnose ou --apply.", file=sys.stderr)
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

    print(
        f"MODO APPLY ({canal.value}): publicacao transacional, sem retry. "
        f"effective_at={effective_at.isoformat()}"
    )
    return run_apply(canal, effective_at)


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
