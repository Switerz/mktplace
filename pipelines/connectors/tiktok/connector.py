from datetime import date, timedelta

from pipelines.common.db import datamart_query
from pipelines.common.logging import get_logger

logger = get_logger(__name__)


class TikTokConnectorError(ValueError):
    """Erro de contrato do conector TikTok: bloqueia fetch() antes de
    qualquer carregamento quando os dados da Raw não permitem calcular um
    GMV completo e correto (Gate R2.1, Projeto R)."""

# Brands no escopo — filtro obrigatório para excluir azbuy/gocase
BRANDS_IN_SCOPE = ("apice", "barbours", "kokeshi", "lescent", "rituaria")

# Gate R2 (Projeto R): allowlist de status elegíveis para GMV, comprovada por
# inspeção agregada de raw.tiktok_shop_orders em jan-mai/2026 (únicos status
# observados no período: COMPLETED, DELIVERED, IN_TRANSIT, CANCELLED — nenhum
# status adicional encontrado). CANCELLED nunca entra no GMV. Qualquer status
# fora desta allowlist conhecida — OU nulo (Gate R2.1) — é contado em
# `orders_unexpected_status` e gera um warning no log; não é incluído
# silenciosamente no GMV.
ELIGIBLE_ORDER_STATUSES = ("COMPLETED", "DELIVERED", "IN_TRANSIT")
KNOWN_ORDER_STATUSES = ELIGIBLE_ORDER_STATUSES + ("CANCELLED",)

# Gate R2: GMV agora é SUM(sub_total) — produtos elegíveis sem o frete pago
# pelo comprador — calculado direto de raw.tiktok_shop_orders, e não mais um
# passthrough de gold.tiktok_brand_daily.gmv (que aproxima total_amount, ~
# 4-7% acima do XLSX; ver docs/analise_reconciliacao_xlsx_torre_jan_maio_2026.md
# seção 3). raw_dedup é uma camada DEFENSIVA: order_id já tem constraint
# UNIQUE em raw.tiktok_shop_orders (confirmado por inspeção nesta mesma
# correção), então isto não deveria mudar nenhuma linha hoje — existe para
# não depender silenciosamente dessa constraint se ela for relaxada no
# futuro. raw_daily é a tabela DIRIGENTE (LEFT JOIN de gold nela, nunca o
# contrário): um dia com pedidos na Raw sempre produz linha, mesmo sem
# correspondente em gold.tiktok_brand_daily, e mesmo quando todos os pedidos
# do dia são CANCELLED (gmv=0 explícito, dia não desaparece).
#
# Gate R2.1 — hardening:
# - `orders`: comparação read-only agregada em jan-mai/2026 mostrou que
#   `gold.orders` e a contagem elegível calculada da Raw (`orders_eligible`)
#   divergem em 741 de 755 células (soma de diferenças absolutas: 78.292
#   pedidos; maior divergência única: 1.421 pedidos numa única célula) —
#   são populações incompatíveis, não um simples arredondamento. Preferência
#   mínima adotada: preservar `g.orders` quando a Gold tiver linha; usar
#   `r.orders_eligible` só como fallback quando a Gold não tiver linha
#   (nesta consulta de jan-mai/2026, 0 de 755 células ficaram sem linha na
#   Gold — o fallback é defensivo, não usado no período testado).
#   `avg_ticket` usa exatamente esse mesmo valor escolhido como denominador.
# - `gmv_video`/`gmv_live`/`gmv_card`: RESTAURADOS como passthrough absoluto
#   da Gold (não removidos) — ver nota em transform.py sobre não
#   reconciliarem necessariamente com o GMV corrigido.
# - `orders_unexpected_status` agora conta status nulo OU fora da allowlist.
# - `orders_eligible_null_subtotal`: pedidos elegíveis com `sub_total IS
#   NULL` — fetch() BLOQUEIA (levanta TikTokConnectorError) se houver
#   algum, em vez de computar um GMV incompleto silenciosamente (um
#   `sub_total` nulo contribuiria 0 na soma sem nenhum aviso).
QUERY = """
WITH raw_dedup AS (
    SELECT DISTINCT ON (order_id)
        order_id, brand, order_status, sub_total, created_at
    FROM raw.tiktok_shop_orders
    WHERE brand IN :brands
      AND created_at >= :date_from
      AND created_at < :date_to_exclusive
    ORDER BY order_id, updated_at DESC NULLS LAST, id DESC
),
raw_daily AS (
    SELECT
        created_at::date AS date,
        brand,
        SUM(CASE WHEN order_status IN :eligible_statuses THEN sub_total ELSE 0 END) AS gmv,
        COUNT(*) FILTER (WHERE order_status IN :eligible_statuses)                  AS orders_eligible,
        COUNT(*) FILTER (
            WHERE order_status IS NULL OR order_status NOT IN :known_statuses
        )                                                                           AS orders_unexpected_status,
        COUNT(*) FILTER (
            WHERE order_status IN :eligible_statuses AND sub_total IS NULL
        )                                                                           AS orders_eligible_null_subtotal
    FROM raw_dedup
    GROUP BY created_at::date, brand
)
SELECT
    r.date,
    r.brand,

    -- Comercial: GMV corrigido (Gate R2) + orders = Gold quando disponível,
    -- fallback para a contagem elegível da Raw só quando a Gold não tem
    -- linha (Gate R2.1) + avg_ticket recomputado com esse MESMO valor.
    r.gmv,
    r.orders_unexpected_status,
    r.orders_eligible_null_subtotal,
    COALESCE(g.orders, r.orders_eligible) AS orders,
    CASE
        WHEN COALESCE(g.orders, r.orders_eligible) > 0
        THEN ROUND(r.gmv / COALESCE(g.orders, r.orders_eligible), 2)
        ELSE NULL
    END AS avg_ticket,

    -- Demais campos: passthrough de gold.tiktok_brand_daily, sem alteração
    -- semântica (pedidos/unidades/compradores/taxas fora do escopo deste
    -- gate). Podem ser NULL quando gold não tem linha para (date, brand).
    g.items_sold                  AS units_sold,
    g.customers                   AS unique_buyers,
    NULLIF(g.visitors, 0)         AS visitors,
    NULLIF(g.conversion_rate, 0)  AS conversion_rate,

    g.canceled                    AS canceled_orders,
    g.returned                    AS returned_orders,
    g.refunded                    AS refunded_orders,
    g.problem_rate,
    g.delivered_orders,
    g.avg_delivery_hours,

    -- Conteúdo TikTok — passthrough absoluto da Gold (Gate R2.1: restaurado
    -- após remoção no Gate R2; ver nota em transform.py — não necessariamente
    -- reconciliam com o novo GMV, pois a Gold os calcula com base no GMV
    -- externo antigo).
    g.gmv_video,
    g.gmv_live,
    g.gmv_card,

    -- Financeiro: valores absolutos preservados (não são o foco deste
    -- gate). avg_fee_pct/avg_settlement_pct (percentuais sobre o GMV antigo)
    -- continuam deliberadamente FORA desta consulta — ver seção "Gate R2"
    -- do documento-base para a justificativa (universo de statement
    -- incompatível com o GMV corrigido de pedido/dia). Como não são
    -- selecionados aqui, chegam como None em transform.py sem exigir
    -- nenhuma mudança lá.
    g.total_settlement,
    g.total_fees

FROM raw_daily r
LEFT JOIN gold.tiktok_brand_daily g
    ON g.date = r.date AND g.brand = r.brand
ORDER BY r.date, r.brand
"""


def fetch(date_from: date, date_to: date) -> list[dict]:
    logger.info("TikTok: buscando %s → %s (GMV = sub_total, Gate R2.1)", date_from, date_to)
    rows = datamart_query(
        QUERY,
        {
            "brands": BRANDS_IN_SCOPE,
            "date_from": date_from,
            "date_to_exclusive": date_to + timedelta(days=1),
            "eligible_statuses": ELIGIBLE_ORDER_STATUSES,
            "known_statuses": KNOWN_ORDER_STATUSES,
        },
    )

    null_subtotal_total = sum(row.get("orders_eligible_null_subtotal", 0) or 0 for row in rows)
    if null_subtotal_total:
        raise TikTokConnectorError(
            f"{null_subtotal_total} pedido(s) elegível(is) com sub_total nulo em "
            f"raw.tiktok_shop_orders no intervalo {date_from}..{date_to} — GMV "
            "incompleto seria calculado silenciosamente; carregamento bloqueado "
            "antes de qualquer transformação/upsert."
        )

    unexpected_total = sum(row.pop("orders_unexpected_status", 0) or 0 for row in rows)
    if unexpected_total:
        logger.warning(
            "TikTok: %d pedidos com order_status nulo ou fora da allowlist conhecida "
            "(%s) — NAO incluidos no GMV; classificar antes de decidir inclusao.",
            unexpected_total,
            KNOWN_ORDER_STATUSES,
        )
    for row in rows:
        row.pop("orders_eligible_null_subtotal", None)
    logger.info("TikTok: %d linhas retornadas", len(rows))
    return rows


def fetch_incremental(days_back: int = 3) -> list[dict]:
    """Busca os últimos N dias para sync incremental diário."""
    today = date.today()
    return fetch(today - timedelta(days=days_back), today)


def fetch_backfill(days_back: int = 90) -> list[dict]:
    today = date.today()
    return fetch(today - timedelta(days=days_back), today)
