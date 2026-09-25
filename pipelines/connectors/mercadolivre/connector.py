from datetime import date, timedelta

from pipelines.common.db import datamart_query
from pipelines.common.logging import get_logger
from pipelines.common.operational_calendar import closed_window

logger = get_logger(__name__)

BRANDS_IN_SCOPE = ("barbours", "kokeshi", "lescent", "rituaria")
# rituaria incluida em 2026-07-01: gold.ml_gestao_diaria tem dados reais desde
# 2025-12-28 (~R$8M GMV histórico); a exclusão anterior estava desatualizada
# (ver docs/architecture.md, decisão de 2026-06-16, e docs/backlog.md).
# apice permanece fora — confirmado sem nenhuma linha em gold.ml_gestao_diaria.

# MARGEM-REAL-2: comissao do marketplace.
#
# `gold.ml_gestao_diaria` nao tem coluna de tarifa. A comissao vem da fonte
# transacional (`api.ml_order_line_items.sale_fee`), agregada aqui no mesmo
# SELECT para nao criar um pipeline paralelo: o fluxo canonico continua sendo
# connector -> transform -> daily_performance.
#
# Tres propriedades medidas da fonte (ver docs/margem_real_1_diagnostico.md
# §10.5 e o contrato de fonte do PR do MARGEM-REAL-2):
#
#  1. `sale_fee` e' POR UNIDADE, nao por item. A comissao do item e'
#     `sale_fee * quantity`. Medido: fee/unit_price fica estavel em ~0,17-0,18
#     para qualquer quantity, enquanto fee/(unit_price*quantity) cai com a
#     quantidade. Somar `sale_fee` cru subestima a comissao em ~2,5%.
#  2. O join e' por `order_id`. `api.ml_orders.id` e' surrogate sequencial e
#     devolve ZERO correspondencia em silencio.
#  3. A populacao da Torre e' `status = 'paid'` por `date_created::date`.
#     Medido em jul+ago/2026: identico a `gold.ml_gestao_diaria` em 248 de 248
#     celulas dia x marca, GMV e pedidos, diferenca maxima 0,00.
#
# `cancelled` (5.934 pedidos, R$ 443.208 na janela) e `partially_refunded`
# (72 pedidos, R$ 10.032) ficam DE FORA, porque tambem estao fora do GMV da
# gold. Excluir e' o que mantem numerador e denominador na mesma populacao.
#
# Sinal: a fonte e' POSITIVA e e' assim que o valor e' armazenado, igual a
# Shopee. O TikTok grava negativo. Ver o transform para a convencao.
ML_FEES_CTE = """
WITH ml_fees AS (
    SELECT
        o.date_created::date            AS ref_date,
        o.brand                         AS brand,
        SUM(li.sale_fee * li.quantity)  AS marketplace_fee,
        COUNT(DISTINCT o.order_id)      AS fee_orders
    FROM api.ml_orders o
    JOIN api.ml_order_line_items li
      ON li.order_id = o.order_id
    WHERE o.status = 'paid'
      AND o.brand IN :brands
      AND o.date_created >= :date_from
      AND o.date_created < (CAST(:date_to AS date) + 1)
    GROUP BY 1, 2
)
"""

QUERY = ML_FEES_CTE + """
SELECT
    g.ref_date                  AS date,
    g.brand,

    -- Comercial
    g.gmv,
    g.paid_orders               AS orders,
    g.total_units               AS units_sold,
    g.avg_ticket,
    g.unique_buyers,
    g.new_buyers,
    g.repeat_buyers,
    g.repeat_buyer_rate_pct,

    -- Operacional
    g.cancelled_orders          AS canceled_orders,
    g.cancel_rate_pct,
    g.delivered_shipments       AS delivered_orders,
    g.avg_delivery_days,
    g.seller_shipping_cost,
    g.shipping_pct_of_gmv,

    -- Mídia
    g.ad_spend,
    g.ad_revenue,
    g.ad_impressions,
    g.ad_clicks,
    g.roas,
    g.acos_pct,
    g.ctr_pct,
    g.cpc,

    -- Financeiro (MARGEM-REAL-2): tarifa/comissao do marketplace, e SO ela.
    -- Nao inclui Ads, frete, imposto, devolucao nem afiliado.
    f.marketplace_fee,
    f.fee_orders

FROM gold.ml_gestao_diaria g
LEFT JOIN ml_fees f
       ON f.ref_date = g.ref_date
      AND f.brand    = g.brand
WHERE g.brand IN :brands
  AND g.ref_date >= :date_from
  AND g.ref_date <= :date_to
ORDER BY g.ref_date, g.brand
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
    # Gate DQ-D1: teto em D-1 (America/Sao_Paulo), nunca D0. A LARGURA
    # inclusiva da janela e' a mesma de antes — apenas o teto desceu um dia.
    inicio, fim = closed_window(days_back)
    return fetch(inicio, fim)


def fetch_backfill(days_back: int = 90) -> list[dict]:
    # Gate DQ-D1: teto em D-1 (America/Sao_Paulo), nunca D0. A LARGURA
    # inclusiva da janela e' a mesma de antes — apenas o teto desceu um dia.
    inicio, fim = closed_window(days_back)
    return fetch(inicio, fim)
