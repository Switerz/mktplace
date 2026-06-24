from datetime import date, timedelta

from pipelines.common.db import datamart_query
from pipelines.common.logging import get_logger

logger = get_logger(__name__)

BRANDS_IN_SCOPE = ("barbours", "kokeshi", "lescent")

QUERY = """
SELECT
    ref_date                    AS date,
    brand,

    -- Comercial
    gmv,
    paid_orders                 AS orders,
    total_units                 AS units_sold,
    avg_ticket,
    unique_buyers,
    new_buyers,
    repeat_buyers,
    repeat_buyer_rate_pct,

    -- Operacional
    cancelled_orders            AS canceled_orders,
    cancel_rate_pct,
    delivered_shipments         AS delivered_orders,
    avg_delivery_days,
    seller_shipping_cost,
    shipping_pct_of_gmv,

    -- Mídia
    ad_spend,
    ad_revenue,
    ad_impressions,
    ad_clicks,
    roas,
    acos_pct,
    ctr_pct,
    cpc

FROM gold.ml_gestao_diaria
WHERE brand IN :brands
  AND ref_date >= :date_from
  AND ref_date <= :date_to
ORDER BY ref_date, brand
"""


def fetch(date_from: date, date_to: date) -> list[dict]:
    logger.info("ML: buscando %s → %s", date_from, date_to)
    rows = datamart_query(
        QUERY,
        {
            "brands": BRANDS_IN_SCOPE,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
    logger.info("ML: %d linhas retornadas", len(rows))
    return rows


def fetch_incremental(days_back: int = 3) -> list[dict]:
    today = date.today()
    return fetch(today - timedelta(days=days_back), today)


def fetch_backfill(days_back: int = 90) -> list[dict]:
    today = date.today()
    return fetch(today - timedelta(days=days_back), today)
