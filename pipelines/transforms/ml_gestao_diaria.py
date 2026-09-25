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


def _fee_pct(fee, gmv) -> Optional[float]:
    """avg_fee_pct só existe quando há numerador E denominador válidos.

    Ausência permanece None — nunca zero. Um zero aqui seria lido como
    "o marketplace não cobrou nada", que é afirmação diferente de
    "não sabemos quanto o marketplace cobrou".
    """
    if fee is None or gmv is None:
        return None
    try:
        fee_f = float(fee)
        gmv_f = float(gmv)
    except (TypeError, ValueError):
        return None
    # NaN não é comparável: NaN > 0 é False, então o guard abaixo já o exclui.
    if not gmv_f > 0:
        return None
    if fee_f != fee_f:   # NaN
        return None
    return round(fee_f / gmv_f * 100, 2)


def transform(row: dict) -> Optional[dict]:
    brand = row.get("brand")
    loja_id = BRAND_TO_LOJA.get(brand)
    if loja_id is None:
        return None

    # MARGEM-REAL-2 — comissão do marketplace.
    #
    # Sinal: preservado como vem da fonte, POSITIVO. É a mesma convenção da
    # Shopee (`commission_net + service_fee_net`, também positivo) e a oposta
    # do TikTok, que grava negativo. A apresentação pode usar magnitude; o
    # armazenamento não troca o sinal em silêncio.
    #
    # Conteúdo: SOMENTE tarifa/comissão do marketplace. Ads, frete, imposto,
    # devolução e afiliado ficam fora — Ads e frete já têm colunas próprias
    # nesta mesma linha, e somá-los aqui seria dupla contagem.
    #
    # Um dia sem pedido pago não tem comissão conhecida: o LEFT JOIN do
    # conector devolve None, e None é o que se grava. Zero diria outra coisa.
    total_fees = row.get("marketplace_fee")

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
        "total_fees": total_fees,
        "avg_fee_pct": _fee_pct(total_fees, row.get("gmv")),
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
