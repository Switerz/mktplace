"""
Mapeia uma linha de gold.ml_gestao_diaria para o schema canônico
fact_marketplace_daily_performance.
"""
from __future__ import annotations

from typing import Optional

BRAND_TO_LOJA: dict[str, int] = {
    "apice": 1,
    "barbours": 2,
    "kokeshi": 3,
    "lescent": 4,
    "rituaria": 5,
}

MARKETPLACE_ID = 2   # Mercado Livre (db/seeds/01_marketplaces.sql)
EMPRESA_ID = 1       # GoBeauté


def transform(row: dict) -> Optional[dict]:
    brand = row.get("brand")
    loja_id = BRAND_TO_LOJA.get(brand)
    if loja_id is None:
        return None

    return {
        # Chaves
        "date": row["date"],   # conector já renomeou ref_date → date
        "loja_id": loja_id,
        "marketplace_id": MARKETPLACE_ID,
        "empresa_id": EMPRESA_ID,

        # Comercial
        "gmv": row.get("gmv"),
        "orders": row.get("orders"),            # conector: paid_orders → orders
        "units_sold": row.get("units_sold"),    # conector: total_units → units_sold
        "avg_ticket": row.get("avg_ticket"),
        "unique_buyers": row.get("unique_buyers"),
        "new_buyers": row.get("new_buyers"),
        "repeat_buyers": row.get("repeat_buyers"),
        "repeat_buyer_rate_pct": row.get("repeat_buyer_rate_pct"),

        # Funil — não disponível no ML gold
        "visitors": None,
        "conversion_rate": None,

        # Operacional
        "canceled_orders": row.get("canceled_orders"),
        "returned_orders": None,      # não disponível no gold ML
        "refunded_orders": None,
        "problem_rate": None,
        "cancel_rate_pct": row.get("cancel_rate_pct"),
        "delivered_orders": row.get("delivered_orders"),
        "avg_delivery_hours": None,   # ML usa dias
        "avg_delivery_days": row.get("avg_delivery_days"),

        # Mídia
        "ad_spend": row.get("ad_spend"),
        "ad_revenue": row.get("ad_revenue"),
        "ad_impressions": row.get("ad_impressions"),
        "ad_clicks": row.get("ad_clicks"),
        "roas": row.get("roas"),
        "acos_pct": row.get("acos_pct"),
        "ctr_pct": row.get("ctr_pct"),
        "cpc": row.get("cpc"),

        # TikTok-específico — não aplicável
        "gmv_video": None,
        "gmv_live": None,
        "gmv_card": None,

        # Financeiro
        "total_settlement": None,
        "total_fees": None,
        "avg_fee_pct": None,
        "avg_settlement_pct": None,
        "seller_shipping_cost": row.get("seller_shipping_cost"),
        "shipping_pct_of_gmv": row.get("shipping_pct_of_gmv"),

        # Metas — calculadas em outro processo
        "target_revenue": None,
        "target_attainment_pct": None,
        "projected_month_revenue": None,

        # Rastreabilidade
        "data_quality_score": None,
        "source_updated_at": None,
    }


def transform_batch(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        canonical = transform(row)
        if canonical is not None:
            result.append(canonical)
    return result
