"""
Mapeia shop-stats diários Shopee → schema canônico (patch de funil + GMV).

Campos preenchidos: visitors, conversion_rate, new_buyers,
repeat_buyers, repeat_buyer_rate_pct, unique_buyers, gmv.

Gate R2.1 (Projeto R): shop-stats passa a ser a fonte autoritativa do GMV
Shopee (gmv = Vendas (BRL) - Vendas Canceladas - Vendas Devolvidas ou
Reembolsadas, já calculado e arredondado em
pipelines/connectors/shopee/_parser_shop_stats.py). Este transform só
repassa o valor — não recalcula, não altera pedidos/unidades/cancelamentos
vindos do Order.all (esses continuam em shopee_orders_daily.py).

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
        "gmv":                   row.get("gmv"),
    }


def transform_batch(rows: list[dict]) -> list[dict]:
    return [r for row in rows if (r := transform(row)) is not None]
