"""Gate UE8-I1 — sync de marts.fact_tiktok_order_discounts_daily.

Publica os DOIS descontos do pedido TikTok, por (ref_date, brand), a partir de
`raw.tiktok_shop_orders` no Data Mart. Contrato completo na migration 013.

O QUE ESTE MODULO NAO E'
------------------------
Nao e' receita economica (depende de refunds/settlement), nao e' caixa (pertence
ao statement), nao e' margem (falta CMV e Shop Ads) e nao e' maturidade
financeira. A fonte e' snapshot mutavel sem historico de versoes: a tabela nunca
fica "madura", e por isso o incremental RECARREGA a janela em vez de so'
acrescentar dias novos.

SINAL: INVERTIDO EXATAMENTE UMA VEZ, AQUI
------------------------------------------
`seller_discount` na Raw e' magnitude POSITIVA. O `-SUM(...)` do SQL abaixo e' a
UNICA inversao de todo o caminho. Os CHECKs da 013 travam o resultado; API e UI
recebem o valor ja assinado e nunca invertem de novo.

AUSENCIA NAO E' ZERO, E MARCA AUSENTE NAO BLOQUEIA SOZINHA
-----------------------------------------------------------
A grade OBSERVADA -- (ref_date, brand) com ao menos um pedido de qualquer status
-- e' a unica prova de atividade disponivel. Nao existe prova independente de
completude da INGESTAO: `audit.source_sync_run` guarda janela (min/max), nao
grade. Por isso:

  - chave observada, zero comercial  -> publica linha com commercial_orders = 0
  - chave nao observada              -> NAO publica linha; registra ausencia
  - marca sem pedido na janela       -> WARN, nao bloqueia (indistinguivel de
                                        ausencia legitima de vendas)

`coverage_status = complete` significa "grade observada completa", nunca
"ingestao completa". O warning diz isso em texto.

BLOQUEIA (fail-fast) apenas quando ha quebra de contrato demonstravel:
janela inteiramente vazia, status nulo/desconhecido, campo monetario obrigatorio
invalido, fechamento de populacoes que nao fecha.

DRY-RUN E' READ-ONLY DE VERDADE
--------------------------------
Sem `--apply` nao existe conexao gravavel: nem para a fato, nem para
`audit.source_sync_run`, nem para `audit.data_quality_check`. O diagnostico sai
em stdout sanitizado. Registrar divergencia no banco durante dry-run seria
escrever para dizer que nao se escreveu.

NAO EXECUTADO NESTA TASK: zero `--apply`, zero banco escrito. UE8-I2 e' o gate
do piloto.
"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from pipelines.common.db import DataMartSession, LocalSession
from pipelines.common.logging import get_logger
from pipelines.common.operational_calendar import (
    OPERATIONAL_TZ,
    assert_closed_day,
    closed_window,
    last_closed_date,
)
from pipelines.connectors.tiktok.connector import (
    BRANDS_IN_SCOPE,
    COMMERCIAL_ORDER_STATUSES,
    KNOWN_ORDER_STATUSES,
    NON_COMMERCIAL_ORDER_STATUSES,
)

logger = get_logger(__name__)

#: `NON_COMMERCIAL_ORDER_STATUSES` inclui CANCELLED, que aqui tem populacao
#: PROPRIA. Derivar (nunca copiar) evita a dupla contagem que quebraria o
#: fechamento: comercial + cancelado + unpaid/on_hold + desconhecido somaria
#: CANCELLED duas vezes e nunca bateria com o total deduplicado.
UNPAID_ON_HOLD_STATUSES = tuple(
    status for status in NON_COMMERCIAL_ORDER_STATUSES if status != "CANCELLED"
)


class DiscountSyncError(RuntimeError):
    """Quebra de contrato que impede publicar. Aborta antes de qualquer escrita."""


class AuditoriaIncompleta(RuntimeError):
    """Falha ao FINALIZAR auditoria DEPOIS de um commit confirmado.

    Os dados estao publicados; as linhas de auditoria ficam em `running`. Marcar
    `failed` aqui afirmaria que nada foi publicado -- falso -- e faria a
    obrigacao durável do modo parecer nao atendida, provocando outra recarga.
    """


TARGET_TABLE = "marts.fact_tiktok_order_discounts_daily"
SOURCE_TABLE = "raw.tiktok_shop_orders"

#: Chave propria de advisory lock. Nao colide com 912120012 (afiliados/UE2-C).
ADVISORY_LOCK_KEY = 912130013

#: `marts.dim_marketplace` do TikTok, para as duas tabelas de auditoria.
AUDIT_MARKETPLACE_ID = 1

#: Tres nomes DURAVEIS. A obrigacao de cada modo e' lida da auditoria, nunca de
#: estado em memoria, arquivo temporario ou `today.day == 1`.
CANONICAL_AUDIT_SOURCE = "tiktok_order_discounts_daily"
BACKFILL_AUDIT_SOURCE = CANONICAL_AUDIT_SOURCE + "_backfill"
FULL_AUDIT_SOURCE = CANONICAL_AUDIT_SOURCE + "_full"

MODE_INCREMENTAL = "incremental"
MODE_BACKFILL = "backfill"
MODE_FULL = "full"
MODE_AUTO = "auto"
MODES = (MODE_INCREMENTAL, MODE_BACKFILL, MODE_FULL, MODE_AUTO)

#: Larguras das janelas, em dias ANTERIORES ao ultimo dia fechado. `closed_window`
#: devolve janela inclusiva de `days_back + 1` dias terminando em D-1.
INCREMENTAL_DAYS_BACK = 9   # 10 dias fechados, igual ao `daily_tiktok --days 10`
BACKFILL_DAYS_BACK = 89     # 90 dias fechados

#: Menor `created_at` COMPROVADO na fonte (preflight UE8-I1 sobre 2.692.671
#: pedidos deduplicados). O `full` nunca comeca antes disto.
FULL_MIN_DATE = date(2025, 6, 4)

#: Backfill e' devido quando nao houve `_backfill` com `success` nesta janela.
BACKFILL_OBLIGATION_DAYS = 7

SOURCE_STATEMENT_TIMEOUT_MS = 600_000
TARGET_STATEMENT_TIMEOUT_MS = 300_000

#: Limite de pares (data, marca) listados em `details`. Evita TEXT gigante e
#: mantem o registro legivel; o total exato vai em `missing_keys_total`.
MAX_KEYS_IN_DETAILS = 50

PUBLICACAO_NAO_TENTADA = "nao_tentada"
PUBLICACAO_ROLLBACK_CONFIRMADO = "rollback_confirmado"
PUBLICACAO_COMMIT_CONFIRMADO = "commit_confirmado"
PUBLICACAO_INDETERMINADA = "indeterminada"

COVERAGE_NOTE = (
    "Cobertura medida sobre a grade OBSERVADA na fonte (data x marca com ao "
    "menos um pedido de qualquer status). Isto NAO e prova independente de "
    "completude da ingestao: nao existe manifesto por data x marca."
)

#: Colunas do destino, explicitas. Nunca `SELECT *`, nunca `INSERT` posicional.
TARGET_COLUMNS = (
    "ref_date",
    "brand",
    "commercial_orders",
    "official_gmv",
    "full_product_value",
    "seller_discount_signed",
    "platform_subsidy_amount",
    "cancelled_orders",
    "cancelled_seller_discount_signed",
    "cancelled_platform_subsidy_amount",
    "source_max_updated_at",
    "raw_max_updated_at",
    "source_run_id",
)

_PADROES_SENSIVEIS = (
    re.compile(r"postgres(?:ql)?://[^\s]*", re.I),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    re.compile(r"password=\S+", re.I),
    re.compile(r"\bhost=\S+", re.I),
)


def sanitizar(exc: BaseException | str) -> str:
    """Mensagem sem DSN, IP, host, senha ou corpo de SQL."""
    texto = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    for padrao in _PADROES_SENSIVEIS:
        texto = padrao.sub("[REDACTED]", texto)
    texto = " ".join(texto.split())
    return texto[:500]


# ---------------------------------------------------------------------------
# Janelas e modos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    date_from: date
    date_to: date

    def __post_init__(self) -> None:
        if self.date_from > self.date_to:
            raise DiscountSyncError(
                f"janela invertida: {self.date_from.isoformat()} > "
                f"{self.date_to.isoformat()}."
            )

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1


def resolve_window(mode: str, agora: datetime | None = None,
                   date_from: date | None = None,
                   date_to: date | None = None) -> Window:
    """Janela do modo, sempre com teto em D-1 (America/Sao_Paulo).

    Funcao PURA: nao toca banco. `assert_closed_day` levanta antes de qualquer
    I/O quando o pedido inclui o dia corrente.
    """
    if date_from is not None or date_to is not None:
        if date_from is None or date_to is None:
            raise DiscountSyncError(
                "janela explicita exige --date-from E --date-to."
            )
        assert_closed_day(date_to, agora, rotulo="--date-to")
        return Window(date_from, date_to)

    if mode == MODE_INCREMENTAL:
        inicio, fim = closed_window(INCREMENTAL_DAYS_BACK, agora)
    elif mode == MODE_BACKFILL:
        inicio, fim = closed_window(BACKFILL_DAYS_BACK, agora)
    elif mode == MODE_FULL:
        inicio, fim = FULL_MIN_DATE, last_closed_date(agora)
    else:
        raise DiscountSyncError(f"modo sem janela definida: {mode!r}")
    return Window(inicio, fim)


def _inicio_mes_brt(agora: datetime | None = None) -> datetime:
    hoje = (agora or datetime.now(timezone.utc)).astimezone(OPERATIONAL_TZ).date()
    return datetime(hoje.year, hoje.month, 1, tzinfo=OPERATIONAL_TZ)


_SQL_ULTIMO_SUCESSO = text("""
    SELECT MAX(finished_at) AS ultimo
    FROM audit.source_sync_run
    WHERE source_name = :fonte
      AND status = 'success'
      AND finished_at IS NOT NULL
      AND finished_at >= :desde
""")


def decide_effective_mode(conn, agora: datetime | None = None) -> str:
    """Resolve `auto` -> full | backfill | incremental. SOB O LOCK, na conexao
    que o detem.

    Decidir ANTES do lock abriria corrida: duas execucoes leriam a mesma
    ausencia de `_full` no mes e ambas escolheriam full, a segunda reconstruindo
    o destino em cima da primeira.

    Ordem e obrigacoes DURAVEIS:
      1. full     -- devido enquanto nao houver `_full` + `success` no mes BRT
      2. backfill -- devido enquanto nao houver `_backfill` + `success` em 7 dias
      3. incremental

    `failed`, `running` e ausencia NAO consomem obrigacao: so' `success` consome.
    """
    inicio_mes = _inicio_mes_brt(agora)
    ultimo_full = conn.execute(
        _SQL_ULTIMO_SUCESSO, {"fonte": FULL_AUDIT_SOURCE, "desde": inicio_mes}
    ).scalar()
    if ultimo_full is None:
        return MODE_FULL

    limite_backfill = (agora or datetime.now(timezone.utc)) - timedelta(
        days=BACKFILL_OBLIGATION_DAYS
    )
    ultimo_backfill = conn.execute(
        _SQL_ULTIMO_SUCESSO,
        {"fonte": BACKFILL_AUDIT_SOURCE, "desde": limite_backfill},
    ).scalar()
    if ultimo_backfill is None:
        return MODE_BACKFILL

    return MODE_INCREMENTAL


def audit_sources_for_mode(mode: str) -> tuple[str, ...]:
    """Nomes de auditoria abertos por este modo.

    Incremental grava so' a canonica. Backfill e full gravam DUAS -- canonica e
    a especifica -- ambas criadas ANTES da leitura, para que a tentativa que
    falhar tambem apareca. Gravar a especifica so' no sucesso seria marcador, nao
    auditoria: esconderia exatamente as tentativas que precisam ser vistas.
    """
    if mode == MODE_FULL:
        return (CANONICAL_AUDIT_SOURCE, FULL_AUDIT_SOURCE)
    if mode == MODE_BACKFILL:
        return (CANONICAL_AUDIT_SOURCE, BACKFILL_AUDIT_SOURCE)
    return (CANONICAL_AUDIT_SOURCE,)


# ---------------------------------------------------------------------------
# Leitura da fonte -- formulas CONGELADAS
# ---------------------------------------------------------------------------

_COMERCIAL = "o.order_status IN :commercial_statuses"
_CANCELADO = "o.order_status = 'CANCELLED'"


def _zero_se_populacao_vazia(expressao: str, condicao: str) -> str:
    """`SUM(...) FILTER (...)` de populacao vazia devolve NULL, nao zero.

    Numa chave OBSERVADA (a marca teve pedido no dia) mas sem nenhum pedido
    daquela populacao, o valor correto e' ZERO MEDIDO -- a populacao existe e
    esta comprovadamente vazia. O destino exige NOT NULL, e sem isto a primeira
    carga com um dia so' de cancelados falharia.

    `COALESCE` esta PROIBIDO: ele nao distingue "populacao vazia" de "campo nulo
    dentro de linha existente", e o segundo caso TEM de bloquear. O `CASE` se
    apoia na CONTAGEM da populacao, que e' a prova de que ela esta vazia.
    """
    return (f"CASE WHEN COUNT(*) FILTER (WHERE {condicao}) = 0 "
            f"THEN 0::numeric ELSE {expressao} FILTER (WHERE {condicao}) END")

#: `raw_dedup` reproduz LITERALMENTE a CTE do conector (DQ-TK1): mesma ordem de
#: desempate, mesma chave. A fonte ja tem `uk_tiktok_orders UNIQUE (order_id)`,
#: entao isto e' camada DEFENSIVA -- e continua necessaria: se a constraint cair,
#: o sync nao pode passar a contar o mesmo pedido duas vezes em silencio.
SQL_AGREGADO = text(f"""
WITH raw_dedup AS (
    SELECT DISTINCT ON (order_id)
        order_id, brand, order_status, created_at,
        total_amount, sub_total, seller_discount, platform_discount,
        -- Os dois relogios de procedencia. `updated_at` tambem e' o criterio de
        -- desempate do DISTINCT ON, herdado do conector (DQ-TK1).
        updated_at, updated_at_tiktok
    FROM {SOURCE_TABLE}
    WHERE brand IN :brands
      AND created_at >= :date_from
      AND created_at <  :date_to_exclusive
    ORDER BY order_id, updated_at DESC NULLS LAST, id DESC
)
SELECT
    o.created_at::date                                            AS ref_date,
    o.brand                                                       AS brand,

    COUNT(*) FILTER (WHERE {_COMERCIAL})                          AS commercial_orders,
    {_zero_se_populacao_vazia("SUM(o.total_amount)", _COMERCIAL)} AS official_gmv,
    {_zero_se_populacao_vazia(
        "SUM(o.sub_total + o.seller_discount + o.platform_discount)", _COMERCIAL)}
                                                                  AS full_product_value,
    {_zero_se_populacao_vazia("-SUM(o.seller_discount)", _COMERCIAL)}
                                                                  AS seller_discount_signed,
    {_zero_se_populacao_vazia("SUM(o.platform_discount)", _COMERCIAL)}
                                                                  AS platform_subsidy_amount,

    COUNT(*) FILTER (WHERE {_CANCELADO})                          AS cancelled_orders,
    {_zero_se_populacao_vazia("-SUM(o.seller_discount)", _CANCELADO)}
                                                                  AS cancelled_seller_discount_signed,
    {_zero_se_populacao_vazia("SUM(o.platform_discount)", _CANCELADO)}
                                                                  AS cancelled_platform_subsidy_amount,

    -- Dois relogios distintos, ambos tecnicos e naive (ver migration 013).
    MAX(o.updated_at_tiktok)                                      AS source_max_updated_at,
    MAX(o.updated_at)                                             AS raw_max_updated_at,

    -- Fechamento de populacoes, por chave. Nao vai para a fato: e' evidencia de
    -- execucao, verificada aqui e registrada em audit.data_quality_check.
    COUNT(*)                                                      AS total_dedup,
    -- :unpaid_onhold_statuses e' NON_COMMERCIAL menos CANCELLED. Usar a tupla
    -- inteira contaria CANCELLED duas vezes e o fechamento nunca bateria.
    COUNT(*) FILTER (WHERE o.order_status IN :unpaid_onhold_statuses)
                                                                  AS unpaid_onhold_orders,
    COUNT(*) FILTER (
        WHERE o.order_status IS NULL OR o.order_status NOT IN :known_statuses
    )                                                             AS unknown_orders,

    -- Nulos em campo monetario obrigatorio, por populacao. Qualquer um bloqueia.
    COUNT(*) FILTER (WHERE {_COMERCIAL} AND (
        o.total_amount IS NULL OR o.sub_total IS NULL
        OR o.seller_discount IS NULL OR o.platform_discount IS NULL))
                                                                  AS commercial_null_money,
    COUNT(*) FILTER (WHERE {_CANCELADO} AND (
        o.seller_discount IS NULL OR o.platform_discount IS NULL))
                                                                  AS cancelled_null_money
FROM raw_dedup o
GROUP BY 1, 2
ORDER BY 1, 2
""").bindparams()


@dataclass
class SourceRow:
    ref_date: date
    brand: str
    commercial_orders: int
    official_gmv: object
    full_product_value: object
    seller_discount_signed: object
    platform_subsidy_amount: object
    cancelled_orders: int
    cancelled_seller_discount_signed: object
    cancelled_platform_subsidy_amount: object
    source_max_updated_at: object
    raw_max_updated_at: object
    total_dedup: int
    unpaid_onhold_orders: int
    unknown_orders: int
    commercial_null_money: int
    cancelled_null_money: int


@dataclass
class SourceSnapshot:
    window: Window
    rows: list[SourceRow]
    #: Chaves (data, marca) do produto cartesiano observado que NAO tem linha.
    missing_keys: list[tuple[date, str]] = field(default_factory=list)
    brands_absent: list[str] = field(default_factory=list)

    @property
    def total_dedup(self) -> int:
        return sum(r.total_dedup for r in self.rows)

    @property
    def commercial(self) -> int:
        return sum(r.commercial_orders for r in self.rows)

    @property
    def cancelled(self) -> int:
        return sum(r.cancelled_orders for r in self.rows)

    @property
    def unpaid_onhold(self) -> int:
        return sum(r.unpaid_onhold_orders for r in self.rows)

    @property
    def unknown(self) -> int:
        return sum(r.unknown_orders for r in self.rows)


def read_source(dm_conn, window: Window) -> SourceSnapshot:
    """Fotografia REPEATABLE READ + READ ONLY da fonte, agregada por chave.

    A janela e' fechada a esquerda e ABERTA a direita (`< date_to + 1 dia`),
    para nao depender de precisao de hora no limite superior.
    """
    dm_conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    dm_conn.execute(text("SET TRANSACTION READ ONLY"))
    dm_conn.execute(text(f"SET LOCAL statement_timeout = {SOURCE_STATEMENT_TIMEOUT_MS}"))

    resultado = dm_conn.execute(
        SQL_AGREGADO,
        {
            "brands": tuple(BRANDS_IN_SCOPE),
            "commercial_statuses": tuple(COMMERCIAL_ORDER_STATUSES),
            "unpaid_onhold_statuses": UNPAID_ON_HOLD_STATUSES,
            "known_statuses": tuple(KNOWN_ORDER_STATUSES),
            "date_from": datetime.combine(window.date_from, datetime.min.time()),
            "date_to_exclusive": datetime.combine(
                window.date_to + timedelta(days=1), datetime.min.time()
            ),
        },
    ).mappings()

    linhas = [SourceRow(**dict(m)) for m in resultado]
    snapshot = SourceSnapshot(window=window, rows=linhas)
    snapshot.missing_keys, snapshot.brands_absent = _coverage(linhas, window)
    return snapshot


def _coverage(rows: list[SourceRow], window: Window
              ) -> tuple[list[tuple[date, str]], list[str]]:
    """Chaves ausentes da grade OBSERVADA e marcas sem nenhum pedido na janela.

    A grade esperada e' o produto (dias com atividade) x (marcas com atividade)
    -- observada, nunca fabricada. Uma chave ausente e' ambigua por construcao:
    pode ser dia sem venda daquela marca ou buraco de ingestao. Vira WARN.
    """
    dias = sorted({r.ref_date for r in rows})
    marcas = sorted({r.brand for r in rows})
    presentes = {(r.ref_date, r.brand) for r in rows}
    ausentes = [
        (d, m) for d in dias for m in marcas if (d, m) not in presentes
    ]
    marcas_ausentes = sorted(set(BRANDS_IN_SCOPE) - set(marcas))
    return ausentes, marcas_ausentes


# ---------------------------------------------------------------------------
# Validacao de contrato -- o que BLOQUEIA
# ---------------------------------------------------------------------------


def validate_contract(snapshot: SourceSnapshot) -> list[str]:
    """Levanta em quebra de contrato; devolve a lista de warnings observacionais.

    BLOQUEIA: janela vazia, status desconhecido/nulo, monetario nulo em campo
    obrigatorio, fechamento de populacoes que nao fecha, sinal fora do contrato.

    NAO BLOQUEIA: marca ausente na janela, chave ausente da grade observada.
    """
    if not snapshot.rows:
        raise DiscountSyncError(
            f"janela {snapshot.window.date_from.isoformat()}.."
            f"{snapshot.window.date_to.isoformat()} sem nenhum pedido das marcas "
            "no escopo. Publicar apagaria o destino sem substituto; abortando "
            "com o destino intacto."
        )

    if snapshot.unknown:
        raise DiscountSyncError(
            f"{snapshot.unknown} pedido(s) com order_status nulo ou fora de "
            "KNOWN_ORDER_STATUSES. Status novo nao entra em populacao alguma "
            "por omissao: e' preciso decidir a que populacao pertence."
        )

    nulos_com = sum(r.commercial_null_money for r in snapshot.rows)
    nulos_can = sum(r.cancelled_null_money for r in snapshot.rows)
    if nulos_com or nulos_can:
        raise DiscountSyncError(
            f"campo monetario obrigatorio nulo: {nulos_com} na populacao "
            f"comercial, {nulos_can} na cancelada. COALESCE para zero e' "
            "proibido -- ausencia nao e' medicao de zero."
        )

    esperado = (snapshot.commercial + snapshot.cancelled
                + snapshot.unpaid_onhold + snapshot.unknown)
    if esperado != snapshot.total_dedup:
        raise DiscountSyncError(
            "fechamento de populacoes nao fecha: comercial + cancelado + "
            f"unpaid/on_hold + desconhecido = {esperado}, total deduplicado = "
            f"{snapshot.total_dedup}. As populacoes deveriam ser disjuntas e "
            "exaustivas."
        )

    for r in snapshot.rows:
        if r.seller_discount_signed is not None and r.seller_discount_signed > 0:
            raise DiscountSyncError(
                "seller_discount_signed positivo apos a inversao: a fonte "
                "mudou a convencao de sinal ou o SQL inverteu duas vezes."
            )
        if (r.platform_subsidy_amount is not None
                and r.platform_subsidy_amount < 0):
            raise DiscountSyncError(
                "platform_subsidy_amount negativo: a fonte mudou a convencao "
                "de sinal do subsidio."
            )

    warnings: list[str] = []
    if snapshot.missing_keys:
        warnings.append(
            f"{len(snapshot.missing_keys)} chave(s) (data, marca) da grade "
            f"observada sem linha. {COVERAGE_NOTE}"
        )
    if snapshot.brands_absent:
        warnings.append(
            f"{len(snapshot.brands_absent)} marca(s) do escopo sem nenhum "
            "pedido na janela. Nao bloqueia: ausencia de vendas e buraco de "
            "ingestao sao indistinguiveis com as fontes atuais."
        )
    return warnings


def coverage_status(snapshot: SourceSnapshot) -> str:
    """`complete` aqui significa GRADE OBSERVADA completa -- ver COVERAGE_NOTE."""
    if snapshot.missing_keys or snapshot.brands_absent:
        return "incomplete_brand_coverage"
    return "complete"


# ---------------------------------------------------------------------------
# Publicacao
# ---------------------------------------------------------------------------

_COLS = ", ".join(TARGET_COLUMNS)
_BINDS = ", ".join(f":{c}" for c in TARGET_COLUMNS)

SQL_STAGING_CREATE = text(f"""
    CREATE TEMP TABLE stg_ftodd (
        LIKE {TARGET_TABLE} INCLUDING DEFAULTS
    ) ON COMMIT DROP
""")

SQL_STAGING_INSERT = text(f"INSERT INTO stg_ftodd ({_COLS}) VALUES ({_BINDS})")

SQL_DELETE_JANELA = text(f"""
    DELETE FROM {TARGET_TABLE}
    WHERE ref_date >= :date_from AND ref_date <= :date_to
""")

SQL_INSERT_DO_STAGING = text(f"""
    INSERT INTO {TARGET_TABLE} ({_COLS})
    SELECT {_COLS} FROM stg_ftodd
""")

#: EXCEPT bidirecional staging x destino, restrito a janela. Zero linha dos dois
#: lados e' a unica prova de igualdade -- contagem e soma nao detectam troca.
SQL_EXCEPT_BIDIRECIONAL = text(f"""
    WITH destino AS (
        SELECT {_COLS} FROM {TARGET_TABLE}
        WHERE ref_date >= :date_from AND ref_date <= :date_to
    ),
    so_no_staging AS (SELECT {_COLS} FROM stg_ftodd EXCEPT SELECT {_COLS} FROM destino),
    so_no_destino AS (SELECT {_COLS} FROM destino EXCEPT SELECT {_COLS} FROM stg_ftodd)
    SELECT
        (SELECT COUNT(*) FROM so_no_staging) AS so_staging,
        (SELECT COUNT(*) FROM so_no_destino) AS so_destino
""")


def publish_in_transaction(conn, snapshot: SourceSnapshot, run_id: str) -> int:
    """Staging temporario -> DELETE da janela -> INSERT -> EXCEPT. Uma transacao.

    `DELETE` limitado a `ref_date BETWEEN date_from AND date_to`: nunca toca dia
    fora da janela recarregada. Recarga integral (nao UPSERT) porque um pedido
    que migrou para CANCELLED precisa SAIR de `commercial_*` -- um UPSERT
    deixaria o valor antigo na chave.
    """
    conn.execute(text(f"SET LOCAL statement_timeout = {TARGET_STATEMENT_TIMEOUT_MS}"))
    conn.execute(SQL_STAGING_CREATE)

    for r in snapshot.rows:
        conn.execute(SQL_STAGING_INSERT, {
            "ref_date": r.ref_date,
            "brand": r.brand,
            "commercial_orders": r.commercial_orders,
            "official_gmv": r.official_gmv,
            "full_product_value": r.full_product_value,
            "seller_discount_signed": r.seller_discount_signed,
            "platform_subsidy_amount": r.platform_subsidy_amount,
            "cancelled_orders": r.cancelled_orders,
            "cancelled_seller_discount_signed": r.cancelled_seller_discount_signed,
            "cancelled_platform_subsidy_amount": r.cancelled_platform_subsidy_amount,
            "source_max_updated_at": r.source_max_updated_at,
            "raw_max_updated_at": r.raw_max_updated_at,
            "source_run_id": run_id,
        })

    janela = {"date_from": snapshot.window.date_from,
              "date_to": snapshot.window.date_to}
    conn.execute(SQL_DELETE_JANELA, janela)
    conn.execute(SQL_INSERT_DO_STAGING)

    diff = conn.execute(SQL_EXCEPT_BIDIRECIONAL, janela).mappings().one()
    if diff["so_staging"] or diff["so_destino"]:
        raise DiscountSyncError(
            f"EXCEPT bidirecional falhou: {diff['so_staging']} linha(s) so' no "
            f"staging, {diff['so_destino']} so' no destino. Sem tolerancia "
            "monetaria: a transacao inteira e' desfeita."
        )
    return len(snapshot.rows)


# ---------------------------------------------------------------------------
# Auditoria -- conexao INDEPENDENTE da publicacao
# ---------------------------------------------------------------------------

SQL_AUDIT_START = text("""
    INSERT INTO audit.source_sync_run
        (source_name, marketplace_id, status, source_min_date, source_max_date)
    VALUES (:fonte, :mkt, 'running', :min_date, :max_date)
    RETURNING sync_run_id
""")

SQL_AUDIT_FINISH = text("""
    UPDATE audit.source_sync_run
       SET status = :status,
           finished_at = NOW(),
           rows_extracted = :extracted,
           rows_loaded = :loaded,
           error_message = :erro
     WHERE sync_run_id = :sync_run_id
""")

SQL_DQ_INSERT = text("""
    INSERT INTO audit.data_quality_check
        (check_name, table_name, marketplace_id, status, severity,
         failed_rows, details)
    VALUES (:nome, :tabela, :mkt, :status, :severidade, :failed, :detalhes)
""")


def audit_start(conn, sources: tuple[str, ...], window: Window) -> dict[str, int]:
    """Abre uma linha `running` por nome, ANTES da leitura e da publicacao."""
    ids: dict[str, int] = {}
    for fonte in sources:
        ids[fonte] = conn.execute(SQL_AUDIT_START, {
            "fonte": fonte,
            "mkt": AUDIT_MARKETPLACE_ID,
            "min_date": window.date_from,
            "max_date": window.date_to,
        }).scalar_one()
    return ids


def audit_finish(conn, ids: dict[str, int], status: str,
                 extracted: int, loaded: int, erro: str | None) -> None:
    """Fecha TODAS as linhas com o mesmo resultado factual.

    Cada UPDATE exige `rowcount == 1`: 0 (id inexistente) ou >1 derrubam a
    transacao inteira, para que as duas linhas de um full/backfill nunca
    terminem pela metade.

    `error_message` so' recebe texto em `failed`. Em `success` fica NULL --
    contagem, cobertura e divergencia vao para `audit.data_quality_check`, que
    e' o lugar de metadado de qualidade.
    """
    if status not in ("success", "failed"):
        raise DiscountSyncError(f"status de auditoria invalido: {status!r}")
    if status == "success" and erro is not None:
        raise DiscountSyncError(
            "execucao success nao pode gravar error_message: metadado de "
            "qualidade vai para audit.data_quality_check."
        )
    for sync_run_id in ids.values():
        resultado = conn.execute(SQL_AUDIT_FINISH, {
            "status": status,
            "extracted": extracted,
            "loaded": loaded,
            "erro": erro,
            "sync_run_id": sync_run_id,
        })
        if resultado.rowcount != 1:
            raise DiscountSyncError(
                f"UPDATE de auditoria afetou {resultado.rowcount} linha(s); "
                "esperado exatamente 1."
            )


def _detalhes(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)


def build_quality_checks(snapshot: SourceSnapshot, run_ids: dict[str, int],
                         mode: str, teto_d_menos_1: date | None = None
                         ) -> list[dict]:
    """Checks de qualidade, um por invariante. Funcao PURA -- testavel sem banco.

    `details` leva apenas agregados e, no maximo, MAX_KEYS_IN_DETAILS pares
    (data, marca). Nunca `order_id`, cliente ou qualquer identificador pessoal.
    """
    janela = {
        "sync_run_id": run_ids.get(CANONICAL_AUDIT_SOURCE),
        "mode": mode,
        "date_from": snapshot.window.date_from,
        "date_to": snapshot.window.date_to,
    }
    checks: list[dict] = []

    checks.append({
        "nome": "ftodd_fechamento_populacoes",
        "status": "pass",
        "severidade": "critical",
        "failed": 0,
        "detalhes": _detalhes({**janela,
                               "commercial": snapshot.commercial,
                               "cancelled": snapshot.cancelled,
                               "unpaid_onhold": snapshot.unpaid_onhold,
                               "unknown": snapshot.unknown,
                               "total_dedup": snapshot.total_dedup}),
    })

    checks.append({
        "nome": "ftodd_dominio_status",
        "status": "pass" if snapshot.unknown == 0 else "fail",
        "severidade": "critical",
        "failed": snapshot.unknown,
        "detalhes": _detalhes({**janela, "unknown_orders": snapshot.unknown}),
    })

    nulos = sum(r.commercial_null_money + r.cancelled_null_money
                for r in snapshot.rows)
    checks.append({
        "nome": "ftodd_monetario_obrigatorio",
        "status": "pass" if nulos == 0 else "fail",
        "severidade": "critical",
        "failed": nulos,
        "detalhes": _detalhes({**janela, "null_money_rows": nulos}),
    })

    # DOIS checks separados: uma chave faltando dentro da grade observada e uma
    # marca inteira ausente sao lacunas diferentes, e um `failed_rows` unico
    # mentiria -- com marca inteira fora, `missing_keys` fica em zero justamente
    # porque a marca nao entra na grade observada.
    ausentes = snapshot.missing_keys
    amostra = [[d.isoformat(), m] for d, m in ausentes[:MAX_KEYS_IN_DETAILS]]
    checks.append({
        "nome": "ftodd_cobertura_chaves_observadas",
        # WARN, nunca FAIL: lacuna observacional sem prova independente.
        "status": "pass" if not ausentes else "warn",
        "severidade": "medium",
        "failed": len(ausentes),
        "detalhes": _detalhes({
            **janela,
            "coverage_status": coverage_status(snapshot),
            "missing_keys_total": len(ausentes),
            "missing_keys_sample": amostra,
            "truncated": len(ausentes) > MAX_KEYS_IN_DETAILS,
            "note": COVERAGE_NOTE,
        }),
    })

    checks.append({
        "nome": "ftodd_cobertura_marcas_do_escopo",
        "status": "pass" if not snapshot.brands_absent else "warn",
        "severidade": "medium",
        # Contagem HONESTA: marcas sem nenhum pedido, nao um cartesiano
        # fabricado como se fossem linhas realmente perdidas.
        "failed": len(snapshot.brands_absent),
        "detalhes": _detalhes({
            **janela,
            "brands_expected": len(BRANDS_IN_SCOPE),
            "brands_observed": len(BRANDS_IN_SCOPE) - len(snapshot.brands_absent),
            "brands_absent": snapshot.brands_absent,
            "note": COVERAGE_NOTE,
        }),
    })

    fora = sum(
        1 for r in snapshot.rows
        if (r.seller_discount_signed or 0) > 0
        or (r.platform_subsidy_amount or 0) < 0
    )
    checks.append({
        "nome": "ftodd_sinais",
        "status": "pass" if fora == 0 else "fail",
        "severidade": "critical",
        "failed": fora,
        "detalhes": _detalhes({**janela, "rows_fora_do_contrato": fora}),
    })

    # O teto registrado tem de ser o MESMO que resolveu a janela. Chamar
    # `last_closed_date()` de novo consultaria outro instante, e na fronteira da
    # meia-noite BRT o check gravaria um dia diferente do que foi publicado.
    checks.append({
        "nome": "ftodd_teto_d_menos_1",
        "status": "pass",
        "severidade": "high",
        "failed": 0,
        "detalhes": _detalhes({
            **janela,
            "last_closed_date": teto_d_menos_1,
            "teto_conhecido": teto_d_menos_1 is not None,
        }),
    })

    return checks


def write_quality_checks(conn, checks: list[dict]) -> None:
    for c in checks:
        conn.execute(SQL_DQ_INSERT, {
            "nome": c["nome"],
            "tabela": TARGET_TABLE,
            "mkt": AUDIT_MARKETPLACE_ID,
            "status": c["status"],
            "severidade": c["severidade"],
            "failed": c["failed"],
            "detalhes": c["detalhes"],
        })


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------

#: Lock TRANSACIONAL, nao de sessao. A transacao do destino ja comeca antes da
#: decisao do modo e permanece aberta durante decisao, auditoria inicial, leitura
#: da fonte e publicacao -- exatamente o intervalo que precisa de exclusao mutua.
#: O PostgreSQL libera no commit OU no rollback, inclusive quando o commit fica
#: indeterminado. Nao existe unlock manual que possa ser pulado por um caminho de
#: excecao, e nenhum lock sobrevive a devolucao da conexao ao pool.
SQL_LOCK = text("SELECT pg_advisory_xact_lock(:chave)")


def run_dry(mode: str, agora: datetime | None = None,
            date_from: date | None = None, date_to: date | None = None,
            *, dm_factory=None) -> dict:
    """Diagnostico SEM NENHUMA conexao gravavel.

    `auto` nao pode ser resolvido aqui: a decisao durável le
    `audit.source_sync_run`, que exige conexao ao Neon. Em dry-run `auto` e'
    reportado como indeterminado, e o modo concreto tem de ser passado.
    """
    if mode == MODE_AUTO:
        raise DiscountSyncError(
            "--mode auto exige --apply: a decisao durável le a auditoria no "
            "destino, e dry-run nao abre conexao ao destino. Use um modo "
            "concreto para diagnosticar."
        )

    # Um unico instante para toda a execucao: a janela e o check de D-1 tem de
    # concordar mesmo se a chamada cruzar a meia-noite BRT.
    instante = agora or datetime.now(timezone.utc)
    teto = last_closed_date(instante)
    window = resolve_window(mode, instante, date_from, date_to)
    fabrica = dm_factory or DataMartSession
    if fabrica is None:
        raise DiscountSyncError("Data Mart nao configurado.")

    sessao = fabrica()
    try:
        snapshot = read_source(sessao.connection(), window)
    finally:
        sessao.rollback()
        sessao.close()

    warnings = validate_contract(snapshot)
    checks = build_quality_checks(snapshot, {}, mode, teto)

    return {
        "mode": mode,
        "applied": False,
        "date_from": window.date_from,
        "date_to": window.date_to,
        "rows": len(snapshot.rows),
        "commercial_orders": snapshot.commercial,
        "cancelled_orders": snapshot.cancelled,
        "unpaid_onhold_orders": snapshot.unpaid_onhold,
        "unknown_orders": snapshot.unknown,
        "coverage_status": coverage_status(snapshot),
        "missing_keys": len(snapshot.missing_keys),
        "brands_absent": snapshot.brands_absent,
        "warnings": warnings,
        "quality_checks": [
            {"nome": c["nome"], "status": c["status"]} for c in checks
        ],
    }


def run_apply(mode: str, agora: datetime | None = None,
              date_from: date | None = None, date_to: date | None = None,
              *, dm_factory=None, neon_factory=None) -> dict:
    """Execucao real. Ordem travada:

        lock -> decide modo -> auditoria running -> le fonte -> valida
             -> publica -> commit -> data_quality -> finaliza auditoria
    """
    # ANTES do lock, ANTES da auditoria, ANTES de qualquer conexao: janela
    # explicita nao pode consumir obrigacao durável. `--mode full` com dois dias
    # gravaria `_full` + `success` e faria o full mensal parecer cumprido sem
    # reconstruir historico algum.
    if date_from is not None or date_to is not None:
        raise DiscountSyncError(
            "janela explicita nao e' permitida com --apply: os modos "
            "incremental, backfill, full e auto tem obrigacao durável "
            "registrada em auditoria, e uma janela arbitraria a consumiria sem "
            "fazer o trabalho correspondente. Use --date-from/--date-to apenas "
            "em dry-run, ou espere um modo manual com auditoria propria."
        )

    fabrica_dm = dm_factory or DataMartSession
    fabrica_neon = neon_factory or LocalSession
    if fabrica_dm is None:
        raise DiscountSyncError("Data Mart nao configurado.")

    instante = agora or datetime.now(timezone.utc)
    teto = last_closed_date(instante)
    run_id = uuid.uuid4().hex[:32]
    sessao_pub = fabrica_neon()
    sessao_audit = fabrica_neon()   # INDEPENDENTE: sobrevive ao rollback acima
    publicacao = PUBLICACAO_NAO_TENTADA
    ids: dict[str, int] = {}
    extracted = loaded = 0
    modo_efetivo = mode

    try:
        conn_pub = sessao_pub.connection()
        conn_pub.execute(SQL_LOCK, {"chave": ADVISORY_LOCK_KEY})

        if mode == MODE_AUTO:
            modo_efetivo = decide_effective_mode(conn_pub, instante)

        window = resolve_window(modo_efetivo, instante)

        ids = audit_start(sessao_audit.connection(),
                          audit_sources_for_mode(modo_efetivo), window)
        sessao_audit.commit()

        sessao_dm = fabrica_dm()
        try:
            snapshot = read_source(sessao_dm.connection(), window)
        finally:
            sessao_dm.rollback()
            sessao_dm.close()

        warnings = validate_contract(snapshot)
        extracted = snapshot.total_dedup

        loaded = publish_in_transaction(conn_pub, snapshot, run_id)

        # A partir daqui NINGUEM sabe se o servidor efetivou. Marcar antes do
        # commit e' o que impede tratar um commit que levantou como se nada
        # tivesse sido publicado.
        publicacao = PUBLICACAO_INDETERMINADA
        sessao_pub.commit()
        publicacao = PUBLICACAO_COMMIT_CONFIRMADO

    except Exception as exc:
        if publicacao == PUBLICACAO_NAO_TENTADA:
            sessao_pub.rollback()
            publicacao = PUBLICACAO_ROLLBACK_CONFIRMADO
        # INDETERMINADA e COMMIT_CONFIRMADO nao recebem rollback e nao viram
        # `failed`: depois de um commit() que levantou, o rollback nao esclarece
        # nada, pode levantar por cima da excecao original, e afirmar
        # "nada publicado" seria mentira possivel. O lock transacional e'
        # liberado pelo proprio PostgreSQL quando a transacao terminar.
        if ids and publicacao in (PUBLICACAO_NAO_TENTADA,
                                  PUBLICACAO_ROLLBACK_CONFIRMADO):
            try:
                audit_finish(sessao_audit.connection(), ids, "failed",
                             extracted, 0, sanitizar(exc))
                sessao_audit.commit()
            except Exception:
                sessao_audit.rollback()
        if publicacao == PUBLICACAO_INDETERMINADA:
            logger.error(
                "resultado da publicacao INDETERMINADO: o commit levantou e "
                "nao se sabe se o servidor efetivou. Auditorias permanecem em "
                "'running'. Nenhuma nova tentativa foi feita. Detalhe: %s",
                sanitizar(exc),
            )
        sessao_audit.close()
        sessao_pub.close()
        raise

    # --- pos-commit: dado publicado. Nada aqui pode marcar `failed`. ---
    try:
        conn_audit = sessao_audit.connection()
        write_quality_checks(
            conn_audit,
            build_quality_checks(snapshot, ids, modo_efetivo, teto),
        )
        audit_finish(conn_audit, ids, "success", extracted, loaded, None)
        sessao_audit.commit()
    except Exception as exc:
        sessao_audit.rollback()
        raise AuditoriaIncompleta(
            "dados PUBLICADOS e auditoria incompleta: as linhas ficam em "
            f"'running'. Detalhe: {sanitizar(exc)}"
        ) from exc
    finally:
        # Sem unlock manual: o lock e' transacional e ja caiu no commit.
        sessao_audit.close()
        sessao_pub.close()

    return {
        "mode": modo_efetivo,
        "applied": True,
        "publicacao": publicacao,
        "run_id": run_id,
        "date_from": window.date_from,
        "date_to": window.date_to,
        "rows_extracted": extracted,
        "rows_loaded": loaded,
        "coverage_status": coverage_status(snapshot),
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Sync de marts.fact_tiktok_order_discounts_daily (UE8-I1)."
    )
    p.add_argument("--mode", choices=MODES, default=MODE_INCREMENTAL)
    p.add_argument("--date-from", type=date.fromisoformat)
    p.add_argument("--date-to", type=date.fromisoformat)
    p.add_argument("--apply", action="store_true",
                   help="Sem esta flag nada e' escrito, em lugar nenhum.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.apply:
            resultado = run_apply(args.mode, None, args.date_from, args.date_to)
        else:
            resultado = run_dry(args.mode, None, args.date_from, args.date_to)
    except DiscountSyncError as exc:
        logger.error("contrato violado: %s", sanitizar(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        logger.error("falha: %s", sanitizar(exc))
        return 1
    logger.info("resultado: %s", _detalhes(resultado))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
