"""
Mapeia uma linha de gold.tiktok_brand_daily para o schema canônico
fact_marketplace_daily_performance.

Inputs esperados: dict retornado pelo conector (já com NULLIF aplicado).
Output: dict pronto para upsert, ou None se brand não está no escopo.
"""
from __future__ import annotations

from typing import Optional

# Mapeamento brand_key → loja_id (espelha db/seeds/02_empresas_lojas.sql)
BRAND_TO_LOJA: dict[str, int] = {
    "apice": 1,
    "barbours": 2,
    "kokeshi": 3,
    "lescent": 4,
    "rituaria": 5,
}

MARKETPLACE_ID = 1   # TikTok Shop (db/seeds/01_marketplaces.sql)
EMPRESA_ID = 1       # GoBeauté


def transform(row: dict) -> Optional[dict]:
    """
    Retorna None se a brand não está no escopo.
    Retorna dict canônico pronto para inserção em fact_marketplace_daily_performance.
    """
    brand = row.get("brand")
    loja_id = BRAND_TO_LOJA.get(brand)
    if loja_id is None:
        return None

    return {
        # Chaves
        "date": row["date"],
        "loja_id": loja_id,
        "marketplace_id": MARKETPLACE_ID,
        "empresa_id": EMPRESA_ID,

        # Comercial
        "gmv": row.get("gmv"),
        "orders": row.get("orders"),
        "units_sold": row.get("units_sold"),
        "avg_ticket": row.get("avg_ticket"),
        "unique_buyers": row.get("unique_buyers"),
        "new_buyers": None,           # não disponível no gold TikTok
        "repeat_buyers": None,
        "repeat_buyer_rate_pct": None,

        # Funil — visitors já vem como NULLIF(visitors, 0) do conector
        "visitors": row.get("visitors"),
        "conversion_rate": row.get("conversion_rate"),

        # Operacional
        "canceled_orders": row.get("canceled_orders"),
        "returned_orders": row.get("returned_orders"),
        "refunded_orders": row.get("refunded_orders"),
        "problem_rate": row.get("problem_rate"),
        "cancel_rate_pct": None,      # não disponível diretamente
        "delivered_orders": row.get("delivered_orders"),
        "avg_delivery_hours": row.get("avg_delivery_hours"),
        "avg_delivery_days": None,    # TikTok usa horas, não dias

        # Mídia — não disponível no gold TikTok
        "ad_spend": None,
        "ad_revenue": None,
        "ad_impressions": None,
        "ad_clicks": None,
        "roas": None,
        "acos_pct": None,
        "ctr_pct": None,
        "cpc": None,

        # TikTok-específico: conteúdo
        "gmv_video": row.get("gmv_video"),
        "gmv_live": row.get("gmv_live"),
        "gmv_card": row.get("gmv_card"),

        # Financeiro
        "total_settlement": row.get("total_settlement"),
        "total_fees": row.get("total_fees"),
        "avg_fee_pct": row.get("avg_fee_pct"),
        "avg_settlement_pct": row.get("avg_settlement_pct"),
        "seller_shipping_cost": None,
        "shipping_pct_of_gmv": None,

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
