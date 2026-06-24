from datetime import date, timedelta

from pipelines.common.db import datamart_query
from pipelines.common.logging import get_logger

logger = get_logger(__name__)

# Brands no escopo — filtro obrigatório para excluir azbuy/gocase
BRANDS_IN_SCOPE = ("apice", "barbours", "kokeshi", "lescent", "rituaria")

QUERY = """
SELECT
    date,
    brand,

    -- Comercial
    gmv,
    orders,
    items_sold                  AS units_sold,
    customers                   AS unique_buyers,
    avg_ticket,

    -- Funil (visitors tem ~83% de ausência — tratar como null quando zero)
    NULLIF(visitors, 0)         AS visitors,
    NULLIF(conversion_rate, 0)  AS conversion_rate,

    -- Operacional
    canceled                    AS canceled_orders,
    returned                    AS returned_orders,
    refunded                    AS refunded_orders,
    problem_rate,
    delivered_orders,
    avg_delivery_hours,

    -- Financeiro
    total_settlement,
    total_fees,
    avg_fee_pct,
    avg_settlement_pct,

    -- Conteúdo TikTok
    gmv_video,
    gmv_live,
    gmv_card

FROM gold.tiktok_brand_daily
WHERE brand IN :brands
  AND date >= :date_from
  AND date <= :date_to
ORDER BY date, brand
"""


def fetch(date_from: date, date_to: date) -> list[dict]:
    logger.info("TikTok: buscando %s → %s", date_from, date_to)
    rows = datamart_query(
        QUERY,
        {
            "brands": BRANDS_IN_SCOPE,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
    logger.info("TikTok: %d linhas retornadas", len(rows))
    return rows


def fetch_incremental(days_back: int = 3) -> list[dict]:
    """Busca os últimos N dias para sync incremental diário."""
    today = date.today()
    return fetch(today - timedelta(days=days_back), today)


def fetch_backfill(days_back: int = 90) -> list[dict]:
    today = date.today()
    return fetch(today - timedelta(days=days_back), today)
