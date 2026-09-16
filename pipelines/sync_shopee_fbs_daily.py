"""Gate FULL-SH-1A-R — publica marts.fact_shopee_fbs_daily.

Le a esteira API da Shopee no Data Mart (`silver.stg_shopee_orders` +
`silver.stg_shopee_order_items`), agrega por dia/marca/conta/classe FBS e
publica no Neon.

O QUE ESTE PIPELINE NAO FAZ
----------------------------
- NAO le `silver.stg_shopee_products`. A classe do pedido vem de
  `fulfillment_flag`, nunca da flag de catalogo (ver migration 019).
- NAO usa `total_amount` em GMV, share ou qualquer KPI financeiro.
- NAO usa `is_sale`: aquele predicado exclui `to_return` e `unpaid`, que por
  decisao deste gate PERMANECEM no GMV bruto.
- NAO le nenhuma coluna de comprador, documento, telefone ou endereco.
- NAO mistura Kokeshi: ela nao existe na esteira API e o XLSX nao entra aqui.

GMV = SUM(item_total) DOS PEDIDOS NAO CANCELADOS
-------------------------------------------------
Unico criterio de exclusao: `order_status = 'cancelled'`. Reconciliado contra
`fact_marketplace_daily_performance` e contra o `product_subtotal` do XLSX --
no universo comum a diferenca e' 0,00 em tres marcas e R$ 77,91 em Barbours.

ESCOPO DA PUBLICACAO
---------------------
DELETE + INSERT limitados a JANELA DE DATAS pedida. Nenhum outro canal,
periodo ou conta e' tocado: a tabela e' exclusiva da Shopee e o DELETE carrega
`ref_date BETWEEN :date_from AND :date_to`. Recarga integral da janela, e nao
UPSERT, porque um pedido que virou `cancelled` precisa SAIR do GMV -- um UPSERT
deixaria a linha antiga sobrevivendo.
"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from psycopg2.extras import execute_values
from sqlalchemy import text

from pipelines.common.db import DataMartSession, LocalSession
from pipelines.common.logging import get_logger

logger = get_logger(__name__)

FACT_TABLE = "marts.fact_shopee_fbs_daily"

#: Fuso operacional da Torre. `created_date_brt` ja' vem convertido na silver;
#: a constante existe para a decisao de janela, que e' nossa.
OPERATIONAL_TZ = timezone(timedelta(hours=-3))

#: Primeiro dia com esteira API confiavel. Antes disso a fonte tem apenas 3
#: pedidos de 2025-12-31, residuo de fronteira de fuso -- nao serie.
SOURCE_COMPLETE_FROM_DATE = date(2026, 1, 1)

#: As quatro contas da esteira API. Kokeshi NAO esta aqui e nao deve ser
#: adicionada sem um gate proprio: ela vive so' no XLSX, com outro contrato.
CONTAS_ESPERADAS = ("apice", "barbours", "lescent", "rituaria")

#: Marca conhecida por estar FORA da esteira API. Declarada para que a
#: cobertura diga "ausente" em vez de omitir.
MARCAS_FORA_DA_API = ("kokeshi",)

#: Dominio FECHADO de `fulfillment_flag`. Valor novo ou NULL falha a carga.
FLAG_FBS = "fulfilled_by_shopee"
FLAG_SELLER = "fulfilled_by_local_seller"
FLAGS_AUTORIZADAS = (FLAG_FBS, FLAG_SELLER)
CLASSES = ("fbs", "seller")

MODE_INCREMENTAL = "incremental"
MODE_FULL = "full"
MODES = (MODE_INCREMENTAL, MODE_FULL)

#: Cobre maturacao de status: um pedido criado ha' dias ainda pode virar
#: `cancelled` ou ganhar `pickup_done_time`, e a janela precisa reprocessa-lo.
INCREMENTAL_DAYS_BACK = 15

STAGING_PAGE_SIZE = 500
STAGING_TABLE = "stg_fsfd"
_STAGINGS_PERMITIDAS = (STAGING_TABLE,)
TARGET_STATEMENT_TIMEOUT_MS = 180_000

#: Chave do advisory lock. Deriva de 019 + shopee, e nao colide com a do
#: ml_fulfillment (916140016).
ADVISORY_LOCK_KEY = 916140019

AUDIT_MARKETPLACE_ID = 3   # Shopee (db/seeds/01_marketplaces.sql)
AUDIT_SOURCE = "shopee_fbs_daily"
AUDIT_SOURCE_FULL = "shopee_fbs_daily_full"

PUBLICACAO_NAO_TENTADA = "nao_tentada"
PUBLICACAO_INDETERMINADA = "indeterminada"
PUBLICACAO_COMMIT_CONFIRMADO = "commit_confirmado"


class ShopeeFbsSyncError(RuntimeError):
    """Falha propria deste pipeline."""


class ConcurrentRunError(ShopeeFbsSyncError):
    """Outra execucao detem o advisory lock. Sem espera, sem retry."""


class SourceUnavailableError(ShopeeFbsSyncError):
    """A fonte nao atende ao contrato minimo."""


# ---------------------------------------------------------------------------
# Sanitizacao — nenhuma mensagem pode carregar dado de pessoa
# ---------------------------------------------------------------------------

#: A fonte tem `recipient_address` em 100% dos pedidos e `buyer_username` em
#: 99,9%. Este pipeline nao os le, mas uma mensagem de erro do driver pode
#: ecoar trecho de linha. O filtro e' cinto e suspensorio.
_PADROES_PII = (
    re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),          # CPF
    re.compile(r"\b[\w.+-]+@[\w-]+\.\w+\b"),                   # e-mail
    re.compile(r"\(?\d{2}\)?\s?9?\d{4}-?\d{4}\b"),             # telefone
)


def sanitizar(exc: BaseException | str) -> str:
    """Texto de erro sem PII e sem DSN."""
    msg = str(exc)
    msg = re.sub(r"postgres(?:ql)?://[^\s\"']+", "postgresql://***", msg)
    for p in _PADROES_PII:
        msg = p.sub("[REMOVIDO]", msg)
    return msg[:2000]


# ---------------------------------------------------------------------------
# Janela
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Window:
    date_from: date
    date_to: date

    def __post_init__(self) -> None:
        if self.date_from > self.date_to:
            raise ShopeeFbsSyncError(
                f"janela invertida: {self.date_from} > {self.date_to}")
        if self.date_from < SOURCE_COMPLETE_FROM_DATE:
            raise ShopeeFbsSyncError(
                f"janela comeca em {self.date_from}, antes do piso historico "
                f"{SOURCE_COMPLETE_FROM_DATE}. A esteira API nao cobre o "
                "periodo anterior."
            )


def _hoje_brt(agora: datetime | None = None) -> date:
    return (agora or datetime.now(timezone.utc)).astimezone(OPERATIONAL_TZ).date()


def resolve_window(mode: str, agora: datetime | None = None) -> Window:
    """Janela fechada em D-1. D0 nunca e' publicado: o dia corrente ainda
    recebe pedidos, e publicar parcial produziria uma queda falsa na serie."""
    hoje = _hoje_brt(agora)
    ate = hoje - timedelta(days=1)
    if ate < SOURCE_COMPLETE_FROM_DATE:
        raise ShopeeFbsSyncError(
            f"nada a publicar: D-1 ({ate}) e' anterior ao piso "
            f"{SOURCE_COMPLETE_FROM_DATE}.")
    if mode == MODE_FULL:
        return Window(SOURCE_COMPLETE_FROM_DATE, ate)
    if mode == MODE_INCREMENTAL:
        desde = max(ate - timedelta(days=INCREMENTAL_DAYS_BACK),
                    SOURCE_COMPLETE_FROM_DATE)
        return Window(desde, ate)
    raise ShopeeFbsSyncError(f"modo desconhecido: {mode!r}")


def audit_sources_for_mode(mode: str) -> tuple[str, ...]:
    return (AUDIT_SOURCE, AUDIT_SOURCE_FULL) if mode == MODE_FULL else (AUDIT_SOURCE,)


# ---------------------------------------------------------------------------
# Leitura da fonte
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FactRow:
    ref_date: date
    brand: str
    shop_account: str
    fbs_class: str
    created_orders: int
    eligible_orders: int
    cancelled_orders: int
    to_return_orders: int
    unpaid_orders: int
    gross_gmv: Decimal
    gross_units: int
    to_return_gmv: Decimal
    unpaid_gmv: Decimal
    handling_seconds_sum: int
    handling_sample_count: int
    source_max_ingested_at: datetime | None


@dataclass(frozen=True)
class SourceSnapshot:
    window: Window
    rows: list[FactRow]
    preflight: dict = field(default_factory=dict)


#: Preflight: mede o contrato ANTES de agregar. Roda na janela pedida.
#:
#: `item_total` e `quantity` sao checados aqui porque um NULL neles viraria
#: silenciosamente um GMV menor depois do SUM.
SQL_PREFLIGHT = text("""
    WITH ped AS (
        SELECT o.shop_account, o.brand, o.order_sn, o.order_status,
               o.fulfillment_flag
          FROM silver.stg_shopee_orders o
         WHERE o.created_date_brt BETWEEN :date_from AND :date_to
    )
    SELECT
        (SELECT count(*) FROM ped)                                   AS pedidos,
        (SELECT count(DISTINCT shop_account) FROM ped)               AS contas,
        (SELECT count(*) FROM ped WHERE fulfillment_flag IS NULL)    AS flag_nula,
        (SELECT count(*) FROM ped
          WHERE fulfillment_flag IS NOT NULL
            AND fulfillment_flag NOT IN :flags)                      AS flag_fora_dominio,
        (SELECT count(*) FROM (
            SELECT shop_account, order_sn FROM ped
             GROUP BY 1, 2 HAVING count(*) > 1) d)                   AS pedidos_duplicados,
        (SELECT count(*) FROM silver.stg_shopee_order_items i
          WHERE i.item_total IS NULL)                                AS item_total_nulo,
        (SELECT count(*) FROM silver.stg_shopee_order_items i
          WHERE i.quantity IS NULL OR i.quantity <= 0)               AS quantidade_invalida,
        (SELECT max(ingested_at) FROM silver.stg_shopee_orders)      AS fonte_max_ingestao
""")

#: Agregacao. Grao final: (ref_date, brand, shop_account, fbs_class).
#:
#: O join pedido->itens e' LEFT porque a ausencia de item precisa aparecer como
#: GMV zero naquele pedido, nunca eliminar o pedido da contagem. As contagens
#: de pedido saem de `ped` (grao do pedido); os valores saem de `itn`, ja'
#: pre-agregado por pedido -- sem isso o join multiplicaria as contagens pelo
#: numero de itens.
SQL_AGREGA = text("""
    WITH ped AS (
        SELECT o.created_date_brt              AS ref_date,
               o.brand,
               o.shop_account,
               o.order_sn,
               o.order_status,
               CASE o.fulfillment_flag
                   WHEN :flag_fbs    THEN 'fbs'
                   WHEN :flag_seller THEN 'seller'
               END                              AS fbs_class,
               o.pay_time,
               o.pickup_done_time,
               o.ingested_at
          FROM silver.stg_shopee_orders o
         WHERE o.created_date_brt BETWEEN :date_from AND :date_to
    ),
    itn AS (
        SELECT i.shop_account, i.order_sn,
               sum(i.item_total) AS gmv,
               sum(i.quantity)   AS un
          FROM silver.stg_shopee_order_items i
         GROUP BY 1, 2
    ),
    j AS (
        SELECT p.*,
               COALESCE(t.gmv, 0)::numeric AS gmv,
               COALESCE(t.un, 0)::bigint   AS un
          FROM ped p
          LEFT JOIN itn t
                 ON t.shop_account = p.shop_account
                AND t.order_sn     = p.order_sn
    )
    SELECT
        ref_date, brand, shop_account, fbs_class,
        count(*)                                                      AS created_orders,
        count(*) FILTER (WHERE order_status <> 'cancelled')            AS eligible_orders,
        count(*) FILTER (WHERE order_status =  'cancelled')            AS cancelled_orders,
        count(*) FILTER (WHERE order_status =  'to_return')            AS to_return_orders,
        count(*) FILTER (WHERE order_status =  'unpaid')               AS unpaid_orders,
        -- COALESCE porque SUM de conjunto vazio e' NULL, e a coluna e' NOT
        -- NULL. Zero aqui e' o valor correto: a classe EXISTE na linha e nao
        -- teve valor, o que e' diferente de nao existir.
        COALESCE(sum(gmv) FILTER (WHERE order_status <> 'cancelled'), 0) AS gross_gmv,
        COALESCE(sum(un)  FILTER (WHERE order_status <> 'cancelled'), 0) AS gross_units,
        COALESCE(sum(gmv) FILTER (WHERE order_status =  'to_return'), 0) AS to_return_gmv,
        COALESCE(sum(gmv) FILTER (WHERE order_status =  'unpaid'), 0)    AS unpaid_gmv,
        -- Handling = pagamento -> COLETA. Pedido sem `pickup_done_time` ou sem
        -- `pay_time` fica FORA da amostra; nunca entra como tempo zero.
        -- `GREATEST(...,0)` protege de relogio invertido na fonte, que produziria
        -- soma negativa e quebraria o CHECK.
        COALESCE(sum(GREATEST(
            EXTRACT(EPOCH FROM (pickup_done_time - pay_time)), 0
        )::bigint) FILTER (
            WHERE pickup_done_time IS NOT NULL AND pay_time IS NOT NULL
              AND order_status <> 'cancelled'
        ), 0)                                                          AS handling_seconds_sum,
        count(*) FILTER (
            WHERE pickup_done_time IS NOT NULL AND pay_time IS NOT NULL
              AND order_status <> 'cancelled'
        )                                                              AS handling_sample_count,
        max(ingested_at)                                               AS source_max_ingested_at
      FROM j
     GROUP BY 1, 2, 3, 4
     ORDER BY 1, 2, 3, 4
""")

SQL_COBERTURA = text("""
    SELECT shop_account,
           count(*)          AS pedidos,
           max(ingested_at)  AS ult_ingestao,
           min(created_date_brt) AS primeiro,
           max(created_date_brt) AS ultimo
      FROM silver.stg_shopee_orders
     WHERE created_date_brt BETWEEN :date_from AND :date_to
     GROUP BY 1
""")


def read_source(conn, window: Window) -> SourceSnapshot:
    """Le e agrega. Falha FECHADA se o contrato da fonte estiver quebrado."""
    pre = conn.execute(SQL_PREFLIGHT, {
        "date_from": window.date_from, "date_to": window.date_to,
        "flags": FLAGS_AUTORIZADAS,
    }).mappings().one()

    # Decisao 8 do gate: valor fora do dominio ou NULL FALHA, nunca vira classe.
    if pre["flag_nula"]:
        raise SourceUnavailableError(
            f"{pre['flag_nula']} pedido(s) com fulfillment_flag NULL na janela. "
            "A classe FBS nao pode ser inferida e nao ha classe 'unknown' neste "
            "contrato. Carga recusada."
        )
    if pre["flag_fora_dominio"]:
        raise SourceUnavailableError(
            f"{pre['flag_fora_dominio']} pedido(s) com fulfillment_flag fora de "
            f"{FLAGS_AUTORIZADAS}. Valor novo na fonte exige decisao humana "
            "antes de virar classe. Carga recusada."
        )
    if pre["pedidos_duplicados"]:
        raise SourceUnavailableError(
            f"{pre['pedidos_duplicados']} par(es) (shop_account, order_sn) "
            "duplicado(s) na fonte. A agregacao contaria o mesmo pedido duas "
            "vezes. Carga recusada."
        )
    if pre["item_total_nulo"]:
        raise SourceUnavailableError(
            f"{pre['item_total_nulo']} item(ns) com item_total NULL. O SUM "
            "silenciaria a falta e publicaria GMV menor. Carga recusada."
        )
    if pre["quantidade_invalida"]:
        raise SourceUnavailableError(
            f"{pre['quantidade_invalida']} item(ns) com quantity nula ou <= 0. "
            "Carga recusada."
        )

    linhas = conn.execute(SQL_AGREGA, {
        "date_from": window.date_from, "date_to": window.date_to,
        "flag_fbs": FLAG_FBS, "flag_seller": FLAG_SELLER,
    }).mappings().all()

    rows = [FactRow(
        ref_date=r["ref_date"], brand=r["brand"], shop_account=r["shop_account"],
        fbs_class=r["fbs_class"],
        created_orders=int(r["created_orders"]),
        eligible_orders=int(r["eligible_orders"]),
        cancelled_orders=int(r["cancelled_orders"]),
        to_return_orders=int(r["to_return_orders"]),
        unpaid_orders=int(r["unpaid_orders"]),
        gross_gmv=Decimal(r["gross_gmv"]),
        gross_units=int(r["gross_units"]),
        to_return_gmv=Decimal(r["to_return_gmv"]),
        unpaid_gmv=Decimal(r["unpaid_gmv"]),
        handling_seconds_sum=int(r["handling_seconds_sum"]),
        handling_sample_count=int(r["handling_sample_count"]),
        source_max_ingested_at=r["source_max_ingested_at"],
    ) for r in linhas]

    cobertura = conn.execute(SQL_COBERTURA, {
        "date_from": window.date_from, "date_to": window.date_to,
    }).mappings().all()

    observadas = tuple(sorted(c["shop_account"] for c in cobertura))
    ausentes = tuple(c for c in CONTAS_ESPERADAS if c not in observadas)

    preflight = dict(pre)
    preflight.update({
        "contas_esperadas": list(CONTAS_ESPERADAS),
        "contas_observadas": list(observadas),
        "contas_ausentes": list(ausentes),
        "marcas_fora_da_api": list(MARCAS_FORA_DA_API),
        "cobertura_por_conta": [dict(c) for c in cobertura],
    })
    return SourceSnapshot(window=window, rows=rows, preflight=preflight)


def validate_contract(snapshot: SourceSnapshot) -> list[str]:
    """Avisos que NAO impedem a carga, mas precisam viajar com o resultado."""
    avisos: list[str] = []
    pf = snapshot.preflight

    if pf.get("contas_ausentes"):
        avisos.append(
            "contas esperadas sem pedido na janela: "
            f"{', '.join(pf['contas_ausentes'])}. A cobertura da janela e' "
            "parcial e o total nao representa a Shopee inteira."
        )
    avisos.append(
        f"cobertura API: {len(pf.get('contas_observadas', []))} conta(s). "
        f"{', '.join(MARCAS_FORA_DA_API)} NAO esta(o) na esteira API e nao "
        "e(sao) somada(s) aqui -- este total e' 'Shopee (cobertura API)', "
        "nunca 'Shopee total'."
    )
    avisos.append(
        "handling = pay_time -> pickup_done_time (COLETA). A API da Shopee nao "
        "expoe data real de entrega; nao ha metrica de entrega nesta fato."
    )
    avisos.append(
        "GMV BRUTO de pedidos nao cancelados. Inclui to_return e unpaid, "
        "publicados tambem em coluna propria. Nao e' receita liquida nem "
        "realizada."
    )

    # Invariantes aritmeticas: se quebrarem, o CHECK do banco barraria depois,
    # mas o erro aqui aponta a linha exata.
    for r in snapshot.rows:
        if r.eligible_orders + r.cancelled_orders != r.created_orders:
            raise ShopeeFbsSyncError(
                f"populacoes nao fecham em {r.ref_date}/{r.brand}/"
                f"{r.shop_account}/{r.fbs_class}: "
                f"{r.eligible_orders}+{r.cancelled_orders} != {r.created_orders}"
            )
        if r.fbs_class not in CLASSES:
            raise ShopeeFbsSyncError(
                f"classe invalida {r.fbs_class!r} em {r.ref_date}/{r.brand}")
        if r.handling_sample_count > r.eligible_orders:
            raise ShopeeFbsSyncError(
                f"amostra de handling ({r.handling_sample_count}) maior que a "
                f"coorte ({r.eligible_orders}) em {r.ref_date}/{r.brand}")
    return avisos


def share(numerador: Decimal | int | None,
          denominador: Decimal | int | None) -> Decimal | None:
    """Regra CANONICA de share. Ponto unico -- nao reimplemente em outro lugar.

    Semantica de zero x ausencia (decisao 9 do gate FULL-SH-1A-R):

        denominador > 0, numerador 0  -> Decimal('0')  -- e' 0%, nao ausencia
        denominador = 0 ou None       -> None          -- NULL, indefinido
        numerador None, denominador>0 -> Decimal('0')  -- classe sem linha e'
                                                          zero medido, nao furo

    A divisao e' feita em Decimal e SEM arredondamento: quem formata decide a
    precisao de exibicao. Arredondar aqui perderia digito antes da hora.
    """
    if denominador is None:
        return None
    den = Decimal(denominador)
    if den == 0:
        return None
    num = Decimal(0) if numerador is None else Decimal(numerador)
    return num / den


def cobertura_por_conta(snapshot: SourceSnapshot) -> dict[str, dict]:
    """Share de FBS por conta, distinguindo 0% de FORA DA COBERTURA.

    Conta esperada que nao aparece na janela recebe `coberta=False` e
    `share_fbs_gmv=None` -- nunca 0%. Dizer 0% de uma conta que nao carregou
    seria afirmar que ela vendeu sem FBS, o que ninguem mediu.
    """
    out: dict[str, dict] = {}
    presentes = {r.shop_account for r in snapshot.rows}

    for conta in CONTAS_ESPERADAS:
        if conta not in presentes:
            out[conta] = {
                "coberta": False, "gross_gmv": None, "gmv_fbs": None,
                "share_fbs_gmv": None,
                "motivo": "sem pedido na janela; fora da cobertura medida",
            }
            continue
        sel = [r for r in snapshot.rows if r.shop_account == conta]
        total = sum((r.gross_gmv for r in sel), Decimal(0))
        fbs = sum((r.gross_gmv for r in sel if r.fbs_class == "fbs"), Decimal(0))
        out[conta] = {
            "coberta": True, "gross_gmv": total, "gmv_fbs": fbs,
            "share_fbs_gmv": share(fbs, total), "motivo": None,
        }

    for marca in MARCAS_FORA_DA_API:
        out[marca] = {
            "coberta": False, "gross_gmv": None, "gmv_fbs": None,
            "share_fbs_gmv": None,
            "motivo": "marca fora da esteira API; nao suprida por XLSX aqui",
        }
    return out


def summarize(snapshot: SourceSnapshot) -> dict:
    tot = {
        "rows": len(snapshot.rows),
        "created_orders": sum(r.created_orders for r in snapshot.rows),
        "eligible_orders": sum(r.eligible_orders for r in snapshot.rows),
        "cancelled_orders": sum(r.cancelled_orders for r in snapshot.rows),
        "gross_gmv": str(sum((r.gross_gmv for r in snapshot.rows), Decimal(0))),
        "gross_units": sum(r.gross_units for r in snapshot.rows),
        "handling_sample_count": sum(r.handling_sample_count for r in snapshot.rows),
    }
    por_classe = {}
    for c in CLASSES:
        sel = [r for r in snapshot.rows if r.fbs_class == c]
        por_classe[c] = {
            "eligible_orders": sum(r.eligible_orders for r in sel),
            "gross_gmv": str(sum((r.gross_gmv for r in sel), Decimal(0))),
            "gross_units": sum(r.gross_units for r in sel),
        }
    tot["by_class"] = por_classe

    # Shares agregados pela regra canonica, em Decimal e sem arredondamento.
    den = sum((r.gross_gmv for r in snapshot.rows), Decimal(0))
    fbs = sum((r.gross_gmv for r in snapshot.rows if r.fbs_class == "fbs"),
              Decimal(0))
    den_ped = sum(r.eligible_orders for r in snapshot.rows)
    fbs_ped = sum(r.eligible_orders for r in snapshot.rows if r.fbs_class == "fbs")
    den_un = sum(r.gross_units for r in snapshot.rows)
    fbs_un = sum(r.gross_units for r in snapshot.rows if r.fbs_class == "fbs")
    tot["share_fbs_gmv"] = share(fbs, den)
    tot["share_fbs_orders"] = share(fbs_ped, den_ped)
    tot["share_fbs_units"] = share(fbs_un, den_un)

    # Denominador PROPRIO: todos os pedidos criados, cancelados inclusive.
    criados = sum(r.created_orders for r in snapshot.rows)
    cancelados = sum(r.cancelled_orders for r in snapshot.rows)
    tot["cancellation_rate"] = share(cancelados, criados)

    tot["coverage"] = cobertura_por_conta(snapshot)
    return tot


# ---------------------------------------------------------------------------
# Publicacao
# ---------------------------------------------------------------------------

_COLS = (
    "ref_date", "brand", "shop_account", "fbs_class",
    "created_orders", "eligible_orders", "cancelled_orders",
    "to_return_orders", "unpaid_orders",
    "gross_gmv", "gross_units", "to_return_gmv", "unpaid_gmv",
    "handling_seconds_sum", "handling_sample_count",
    "source_max_ingested_at", "source_run_id",
)

SQL_STAGING_CREATE = text(f"""
    CREATE TEMP TABLE {STAGING_TABLE} (
        ref_date DATE, brand TEXT, shop_account TEXT, fbs_class TEXT,
        created_orders BIGINT, eligible_orders BIGINT, cancelled_orders BIGINT,
        to_return_orders BIGINT, unpaid_orders BIGINT,
        gross_gmv NUMERIC, gross_units BIGINT,
        to_return_gmv NUMERIC, unpaid_gmv NUMERIC,
        handling_seconds_sum BIGINT, handling_sample_count BIGINT,
        source_max_ingested_at TIMESTAMPTZ, source_run_id VARCHAR(64)
    ) ON COMMIT DROP
""")

#: ESCOPADO POR DATA. Nao ha clausula de marca ou conta: a janela inteira e'
#: recarregada, e a tabela e' exclusiva da Shopee -- nenhum outro canal existe
#: aqui para ser apagado por engano.
SQL_DELETE_JANELA = text(f"""
    DELETE FROM {FACT_TABLE}
     WHERE ref_date BETWEEN :date_from AND :date_to
""")

SQL_INSERT_DO_STAGING = text(f"""
    INSERT INTO {FACT_TABLE} ({', '.join(_COLS)})
    SELECT {', '.join(_COLS)} FROM {STAGING_TABLE}
""")

#: EXCEPT bidirecional ANTES do commit: e' o unico ponto em que divergencia
#: entre o que quisemos publicar e o que o destino tem ainda pode ser desfeita.
SQL_EXCEPT = text(f"""
    SELECT
      (SELECT count(*) FROM (
          SELECT {', '.join(_COLS[:15])} FROM {STAGING_TABLE}
          EXCEPT
          SELECT {', '.join(_COLS[:15])} FROM {FACT_TABLE}
           WHERE ref_date BETWEEN :date_from AND :date_to) a) AS so_staging,
      (SELECT count(*) FROM (
          SELECT {', '.join(_COLS[:15])} FROM {FACT_TABLE}
           WHERE ref_date BETWEEN :date_from AND :date_to
          EXCEPT
          SELECT {', '.join(_COLS[:15])} FROM {STAGING_TABLE}) b) AS so_destino
""")

SQL_RECONCILIA_DESTINO = text(f"""
    SELECT count(*) AS linhas,
           COALESCE(sum(gross_gmv), 0) AS gmv,
           COALESCE(sum(eligible_orders), 0) AS pedidos
      FROM {FACT_TABLE}
     WHERE ref_date BETWEEN :date_from AND :date_to
""")


def _valor_finito(v) -> bool:
    if isinstance(v, Decimal):
        return v.is_finite()
    if isinstance(v, float):
        return v == v and v not in (float("inf"), float("-inf"))
    return True


def _inserir_em_lote(conn, staging: str, colunas: tuple[str, ...],
                     linhas: list[tuple]) -> int:
    """Carrega em paginas de STAGING_PAGE_SIZE usando o cursor DBAPI da PROPRIA
    conexao, que compartilha a transacao da Connection SQLAlchemy.

    `executemany` do psycopg2 e' um laco em C: uma ida ao servidor por linha.
    `execute_values` empacota a pagina inteira num INSERT so'.

    Nunca chama commit nem rollback: quem controla a transacao e'
    `publish_in_transaction`.
    """
    if staging not in _STAGINGS_PERMITIDAS:
        raise ShopeeFbsSyncError(
            f"staging nao permitida: {staging!r}. Apenas {_STAGINGS_PERMITIDAS}.")
    if not linhas:
        return 0

    esperado = len(colunas)
    for i, linha in enumerate(linhas):
        if len(linha) != esperado:
            raise ShopeeFbsSyncError(
                f"linha {i} tem {len(linha)} valor(es); esperado {esperado}.")
        for j, v in enumerate(linha):
            if not _valor_finito(v):
                raise ShopeeFbsSyncError(
                    f"valor nao finito em {staging}.{colunas[j]}, linha {i}. "
                    "NaN e infinito nao podem chegar ao banco."
                )

    sql = f"INSERT INTO {staging} ({', '.join(colunas)}) VALUES %s"
    cur = conn.connection.cursor()
    execute_values(cur, sql, linhas, page_size=STAGING_PAGE_SIZE)
    return len(linhas)


def publish_in_transaction(conn, snapshot: SourceSnapshot, run_id: str) -> int:
    """staging -> DELETE da janela -> INSERT -> EXCEPT. UMA transacao."""
    conn.execute(text(f"SET LOCAL statement_timeout = {TARGET_STATEMENT_TIMEOUT_MS}"))
    conn.execute(SQL_STAGING_CREATE)

    janela = {"date_from": snapshot.window.date_from,
              "date_to": snapshot.window.date_to}

    if not snapshot.rows:
        # Leitura vazia NAO apaga historico publicado.
        atual = conn.execute(SQL_RECONCILIA_DESTINO, janela).mappings().one()
        if atual["linhas"]:
            raise ShopeeFbsSyncError(
                f"fonte devolveu zero linhas, mas o destino tem "
                f"{atual['linhas']} linha(s) na janela. Recusado: leitura vazia "
                "nao apaga historico."
            )
        return 0

    _inserir_em_lote(conn, STAGING_TABLE, _COLS, [(
        r.ref_date, r.brand, r.shop_account, r.fbs_class,
        r.created_orders, r.eligible_orders, r.cancelled_orders,
        r.to_return_orders, r.unpaid_orders,
        r.gross_gmv, r.gross_units, r.to_return_gmv, r.unpaid_gmv,
        r.handling_seconds_sum, r.handling_sample_count,
        r.source_max_ingested_at, run_id,
    ) for r in snapshot.rows])

    conn.execute(SQL_DELETE_JANELA, janela)
    conn.execute(SQL_INSERT_DO_STAGING)

    diff = conn.execute(SQL_EXCEPT, janela).mappings().one()
    if diff["so_staging"] or diff["so_destino"]:
        raise ShopeeFbsSyncError(
            f"EXCEPT bidirecional falhou: {diff['so_staging']} linha(s) so' no "
            f"staging, {diff['so_destino']} so' no destino. Transacao desfeita."
        )
    return len(snapshot.rows)


# ---------------------------------------------------------------------------
# Auditoria — conexao INDEPENDENTE da publicacao
# ---------------------------------------------------------------------------

SQL_AUDIT_START = text("""
    INSERT INTO audit.source_sync_run
        (source_name, marketplace_id, status, source_min_date, source_max_date)
    VALUES (:fonte, :mkt, 'running', :min_date, :max_date)
    RETURNING sync_run_id
""")
SQL_AUDIT_FINISH = text("""
    UPDATE audit.source_sync_run
       SET status = :status, finished_at = NOW(),
           rows_extracted = :extracted, rows_loaded = :loaded,
           error_message = :erro
     WHERE sync_run_id = :sync_run_id
""")

SQL_TRY_LOCK = text("SELECT pg_try_advisory_lock(:chave) AS obtido")
SQL_UNLOCK = text("SELECT pg_advisory_unlock(:chave)")


def audit_start(conn, sources: tuple[str, ...], window: Window) -> dict[str, int]:
    return {f: conn.execute(SQL_AUDIT_START, {
        "fonte": f, "mkt": AUDIT_MARKETPLACE_ID,
        "min_date": window.date_from, "max_date": window.date_to,
    }).scalar_one() for f in sources}


def audit_finish(conn, ids: dict[str, int], status: str,
                 extracted: int, loaded: int, erro: str | None) -> None:
    if status not in ("success", "failed"):
        raise ShopeeFbsSyncError(f"status de auditoria invalido: {status!r}")
    if status == "success" and erro is not None:
        raise ShopeeFbsSyncError("execucao success nao pode gravar error_message.")
    for sync_run_id in ids.values():
        res = conn.execute(SQL_AUDIT_FINISH, {
            "status": status, "extracted": extracted, "loaded": loaded,
            "erro": erro, "sync_run_id": sync_run_id,
        })
        if res.rowcount != 1:
            raise ShopeeFbsSyncError(
                f"UPDATE de auditoria afetou {res.rowcount} linha(s); esperado 1.")


def _conn_lock():
    engine = LocalSession.kw.get("bind")
    if engine is None:
        raise ShopeeFbsSyncError("destino nao configurado (sem engine).")
    return engine.connect().execution_options(isolation_level="AUTOCOMMIT")


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------

def run(mode: str = MODE_INCREMENTAL, apply: bool = False,
        agora: datetime | None = None, neon_factory=None, dm_factory=None) -> dict:
    """Dry-run por padrao. `apply=True` publica."""
    if mode not in MODES:
        raise ShopeeFbsSyncError(f"modo desconhecido: {mode!r}")

    fabrica_neon = neon_factory or LocalSession
    fabrica_dm = dm_factory or DataMartSession
    run_id = uuid.uuid4().hex
    window = resolve_window(mode, agora)

    if not apply:
        # Dry-run: le a fonte, valida, NAO abre transacao gravavel e NAO audita.
        sessao_dm = fabrica_dm()
        try:
            snapshot = read_source(sessao_dm.connection(), window)
        finally:
            sessao_dm.rollback()
            sessao_dm.close()
        avisos = validate_contract(snapshot)
        resultado = dict(summarize(snapshot))
        resultado.update({
            "mode": mode, "applied": False,
            "date_from": window.date_from, "date_to": window.date_to,
            "run_id": run_id, "warnings": avisos,
            "preflight": dict(snapshot.preflight),
        })
        return resultado

    conn_lock = _conn_lock()
    lock_obtido = False
    sessao_audit = None
    sessao_pub = None
    ids: dict[str, int] = {}
    publicacao = PUBLICACAO_NAO_TENTADA

    try:
        obtido = conn_lock.execute(SQL_TRY_LOCK, {"chave": ADVISORY_LOCK_KEY}).scalar()
        # `is not True` de proposito: None, 0 ou resultado vazio NAO sao posse.
        if obtido is not True:
            raise ConcurrentRunError(
                f"outra execucao de {AUDIT_SOURCE} ja' detem o lock "
                f"{ADVISORY_LOCK_KEY}. Nada foi lido, auditado ou publicado. "
                "Sem espera e sem retry."
            )
        lock_obtido = True

        sessao_audit = fabrica_neon()
        ids = audit_start(sessao_audit.connection(),
                          audit_sources_for_mode(mode), window)
        sessao_audit.commit()

        sessao_dm = fabrica_dm()
        try:
            snapshot = read_source(sessao_dm.connection(), window)
        finally:
            sessao_dm.rollback()
            sessao_dm.close()

        avisos = validate_contract(snapshot)
        resumo = summarize(snapshot)
        extracted = resumo["created_orders"]

        sessao_pub = fabrica_neon()
        conn_pub = sessao_pub.connection()
        janela = {"date_from": window.date_from, "date_to": window.date_to}

        antes = dict(conn_pub.execute(SQL_RECONCILIA_DESTINO, janela).mappings().one())
        loaded = publish_in_transaction(conn_pub, snapshot, run_id)
        depois = dict(conn_pub.execute(SQL_RECONCILIA_DESTINO, janela).mappings().one())

        # A partir daqui ninguem sabe se o servidor efetivou.
        publicacao = PUBLICACAO_INDETERMINADA
        sessao_pub.commit()
        publicacao = PUBLICACAO_COMMIT_CONFIRMADO

        resultado = dict(resumo)
        resultado.update({
            "mode": mode, "applied": True,
            "date_from": window.date_from, "date_to": window.date_to,
            "run_id": run_id, "warnings": avisos,
            "preflight": dict(snapshot.preflight),
            "reconciliation": {"before": antes, "after": depois},
            "rows_loaded": loaded, "rows_extracted": extracted,
        })

        try:
            audit_finish(sessao_audit.connection(), ids, "success",
                         extracted, loaded, None)
            sessao_audit.commit()
        except Exception as aud:
            # Publicacao COMMITADA. Marcar `failed` diria ao proximo run que
            # nada foi publicado -- falso, e faria refazer trabalho sobre dado
            # bom. Perda de rastro, nao perda de dado.
            logger.error("publicacao COMMITADA mas auditoria nao registrou: %s",
                         sanitizar(aud))
            try:
                sessao_audit.rollback()
            except Exception:   # pragma: no cover
                pass
            resultado["audit_status"] = "nao_registrada_apos_commit"
        return resultado

    except Exception as exc:
        msg = sanitizar(exc)
        if publicacao == PUBLICACAO_INDETERMINADA:
            # Commit levantou: o servidor PODE ter efetivado. Sem rollback cego
            # e sem retry automatico.
            msg = (f"ESTADO INDETERMINADO apos commit: {msg}. Verifique "
                   f"{FACT_TABLE} na janela antes de qualquer reexecucao; nao "
                   "reexecute automaticamente.")
        elif publicacao == PUBLICACAO_NAO_TENTADA and sessao_pub is not None:
            try:
                sessao_pub.rollback()
            except Exception:   # pragma: no cover
                pass

        if ids and sessao_audit is not None and publicacao != PUBLICACAO_INDETERMINADA:
            try:
                audit_finish(sessao_audit.connection(), ids, "failed", 0, 0, msg)
                sessao_audit.commit()
            except Exception:   # pragma: no cover
                try:
                    sessao_audit.rollback()
                except Exception:
                    pass

        classe = type(exc) if isinstance(exc, ShopeeFbsSyncError) else ShopeeFbsSyncError
        raise classe(msg) from exc

    finally:
        if sessao_pub is not None:
            try:
                sessao_pub.close()
            except Exception:   # pragma: no cover
                pass
        if sessao_audit is not None:
            try:
                sessao_audit.close()
            except Exception:   # pragma: no cover
                pass
        if lock_obtido:
            try:
                conn_lock.execute(SQL_UNLOCK, {"chave": ADVISORY_LOCK_KEY})
            except Exception:   # pragma: no cover
                logger.error("falha ao liberar o advisory lock %s", ADVISORY_LOCK_KEY)
        try:
            conn_lock.close()
        except Exception:   # pragma: no cover
            pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Publica marts.fact_shopee_fbs_daily")
    p.add_argument("--mode", choices=MODES, default=MODE_INCREMENTAL)
    p.add_argument("--apply", action="store_true",
                   help="publica. Sem esta flag o run e' dry-run.")
    args = p.parse_args(argv)
    try:
        res = run(mode=args.mode, apply=args.apply)
    except ShopeeFbsSyncError as exc:
        logger.error("%s", sanitizar(exc))
        return 1
    print(json.dumps(res, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
