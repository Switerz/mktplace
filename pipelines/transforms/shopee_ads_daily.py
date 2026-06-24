"""
Mapeia métricas de ads Shopee (médias diárias do período) → schema canônico.

Campos preenchidos: ad_spend, ad_revenue, ad_impressions, ad_clicks,
roas, acos_pct, ctr_pct, cpc.

Nota: valores são médias diárias distribuídas do total do período.
Não refletem variação diária real — não há breakdown diário no export.
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

MARKETPLACE_ID = 3
EMPRESA_ID = 1


def transform(row: dict) -> Optional[dict]:
    brand = row.get("brand")
    loja_id = BRAND_TO_LOJA.get(brand)
    if loja_id is None:
        return None

    return {
        "date":           row["date"],
        "loja_id":        loja_id,
        "marketplace_id": MARKETPLACE_ID,
        "empresa_id":     EMPRESA_ID,
        "ad_spend":       row.get("ad_spend"),
        "ad_revenue":     row.get("ad_revenue"),
        "ad_impressions": row.get("ad_impressions"),
        "ad_clicks":      row.get("ad_clicks"),
        "roas":           row.get("roas"),
        "acos_pct":       row.get("acos_pct"),
        "ctr_pct":        row.get("ctr_pct"),
        "cpc":            row.get("cpc"),
    }


def transform_batch(rows: list[dict]) -> list[dict]:
    return [r for row in rows if (r := transform(row)) is not None]
