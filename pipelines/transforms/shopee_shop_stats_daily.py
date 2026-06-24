"""
Mapeia shop-stats diários Shopee → schema canônico (patch de funil).

Campos preenchidos: visitors, conversion_rate, new_buyers,
repeat_buyers, repeat_buyer_rate_pct, unique_buyers.

Todos os outros campos ficam ausentes do dict — o PATCH SQL
no daily_performance.py atualiza apenas esses campos no ON CONFLICT.
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
        "date":                  row["date"],
        "loja_id":               loja_id,
        "marketplace_id":        MARKETPLACE_ID,
        "empresa_id":            EMPRESA_ID,
        "visitors":              row.get("visitors"),
        "conversion_rate":       row.get("conversion_rate"),
        "new_buyers":            row.get("new_buyers"),
        "repeat_buyers":         row.get("repeat_buyers"),
        "repeat_buyer_rate_pct": row.get("repeat_buyer_rate_pct"),
        "unique_buyers":         row.get("unique_buyers"),
    }


def transform_batch(rows: list[dict]) -> list[dict]:
    return [r for row in rows if (r := transform(row)) is not None]
