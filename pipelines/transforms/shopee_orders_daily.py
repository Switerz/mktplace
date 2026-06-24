"""
Mapeia o output do conector Shopee (orders agregados por dia + brand)
para o schema canônico de fact_marketplace_daily_performance.

Campos não disponíveis nos orders (visitors, ads, new_buyers, etc.)
ficam como None e serão preenchidos em fases futuras pelos módulos
de shop_stats e ads.
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

MARKETPLACE_ID = 3  # Shopee (db/seeds/01_marketplaces.sql)
EMPRESA_ID = 1      # GoBeauté


def transform(row: dict) -> Optional[dict]:
    """Retorna None se brand fora do escopo."""
    brand = row.get("brand")
    loja_id = BRAND_TO_LOJA.get(brand)
    if loja_id is None:
        return None

    return {
        # Chaves
        "date":           row["date"],
        "loja_id":        loja_id,
        "marketplace_id": MARKETPLACE_ID,
        "empresa_id":     EMPRESA_ID,

        # Comercial
        "gmv":                   row.get("gmv"),
        "orders":                row.get("orders"),
        "units_sold":            row.get("units_sold"),
        "avg_ticket":            row.get("avg_ticket"),
        "unique_buyers":         row.get("unique_buyers"),
        "new_buyers":            None,  # shop_stats — fase futura
        "repeat_buyers":         None,
        "repeat_buyer_rate_pct": None,

        # Funil — shop_stats — fase futura
        "visitors":        None,
        "conversion_rate": None,

        # Operacional
        "canceled_orders":    row.get("canceled_orders"),
        "returned_orders":    row.get("returned_orders"),
        "refunded_orders":    None,  # não discriminado nos orders
        "problem_rate":       None,
        "cancel_rate_pct":    row.get("cancel_rate_pct"),
        "delivered_orders":   row.get("delivered_orders"),
        "avg_delivery_hours": None,
        "avg_delivery_days":  None,

        # Mídia — ads CSV — fase futura
        "ad_spend":       None,
        "ad_revenue":     None,
        "ad_impressions": None,
        "ad_clicks":      None,
        "roas":           None,
        "acos_pct":       None,
        "ctr_pct":        None,
        "cpc":            None,

        # Conteúdo TikTok-específico — N/A Shopee
        "gmv_video": None,
        "gmv_live":  None,
        "gmv_card":  None,

        # Financeiro
        "total_settlement":   row.get("total_settlement"),
        "total_fees":         row.get("total_fees"),
        "avg_fee_pct":        row.get("avg_fee_pct"),
        "avg_settlement_pct": row.get("avg_settlement_pct"),
        "seller_shipping_cost": row.get("seller_shipping_cost"),
        "shipping_pct_of_gmv":  row.get("shipping_pct_of_gmv"),

        # Metas — calculadas em outro processo
        "target_revenue":          None,
        "target_attainment_pct":   None,
        "projected_month_revenue": None,

        # Rastreabilidade
        "data_quality_score": None,
        "source_updated_at":  None,
    }


def transform_batch(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        canonical = transform(row)
        if canonical is not None:
            result.append(canonical)
    return result
