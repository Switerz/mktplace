from datetime import date, timedelta

from pipelines.common.db import datamart_query
from pipelines.common.logging import get_logger
from pipelines.common.operational_calendar import closed_window

logger = get_logger(__name__)


class TikTokConnectorError(ValueError):
    """Erro de contrato do conector TikTok: bloqueia fetch() antes de
    qualquer carregamento quando os dados da Raw não permitem calcular um
    GMV completo e correto (Gate DQ-TK1)."""

# Brands no escopo — filtro obrigatório para excluir azbuy/gocase
BRANDS_IN_SCOPE = ("apice", "barbours", "kokeshi", "lescent", "rituaria")

# Gate DQ-TK1 (2026-08-25): allowlist COMERCIAL única. A MESMA população
# define GMV, pedidos e ticket — não existe mais uma população no numerador
# e outra no denominador (era o que o Gate R2.1 fazia, ver `content_orders`).
#
# AWAITING_COLLECTION e AWAITING_SHIPMENT entraram com prova dupla, exigida
# pelo gate ANTES de qualquer implementação:
#  - dado: em 01-24/08/2026 (dedup por order_id, 5 marcas), 3.876/3.876
#    AWAITING_COLLECTION e 4/4 AWAITING_SHIPMENT têm `paid_at` preenchido
#    (100%), contra 0/611 em UNPAID. O contraste é binário, não inferido.
#  - contrato: docs/data_contracts.md §2 descreve AWAITING_SHIPMENT como
#    "Pago, aguardando envio pelo seller", e mapeia AWAITING_COLLECTION para
#    o canônico `shipped` (já despachado, logo necessariamente pago).
COMMERCIAL_ORDER_STATUSES = (
    "COMPLETED",
    "DELIVERED",
    "IN_TRANSIT",
    "AWAITING_COLLECTION",
    "AWAITING_SHIPMENT",
)

# Status conhecidos e deliberadamente FORA da população comercial, com motivo:
#  - UNPAID: 0% com `paid_at` na medição de ago/2026 — ainda não é venda.
#  - CANCELLED: venda desfeita; nunca entra no GMV.
#  - ON_HOLD: retido (fraude/revisão). RESSALVA: a medição mostrou 4/4 com
#    `paid_at`, ou seja o DADO sugere pagamento — a exclusão é decisão de
#    negócio do gate, não do dado. Volume imaterial (R$ 384,42 em 24 dias).
#    Se o volume crescer, revisar com o dono do número.
NON_COMMERCIAL_ORDER_STATUSES = ("UNPAID", "CANCELLED", "ON_HOLD")

# TODOS os status conhecidos, explícitos (os 8 observados na fonte). Um status
# novo — ou nulo — não entra silenciosamente em lugar nenhum: BLOQUEIA a carga
# em fetch(), antes de qualquer upsert. Gate DQ-TK1 endureceu isto: no Gate
# R2.1 era apenas um warning, e foi exatamente assim que AWAITING_COLLECTION
# (3.876 pedidos, R$ 187.962,33 em agosto) ficou fora do GMV sem ninguém ver.
KNOWN_ORDER_STATUSES = COMMERCIAL_ORDER_STATUSES + NON_COMMERCIAL_ORDER_STATUSES

# Piso do lookback incremental do TikTok. O orquestrador já passa --days 10
# (pipelines/ops/orchestrate.py; medição de 18/08/2026: mediana de 5,1 dias
# até DELIVERED, p90 8,3, e um dia só estabiliza a partir de ~8 dias de
# idade). Este piso existe para que uma chamada SEM --days não reintroduza a
# janela default de 3 dias, que congelava dias ainda imaturos. Nunca reduzir:
# a janela recente é provisória e precisa ser reafirmada a cada rodada.
MIN_INCREMENTAL_LOOKBACK_DAYS = 10

# Gate DQ-TK1: GMV é SUM(total_amount) da população comercial — o total
# cobrado do comprador, INCLUINDO o frete que ele pagou. Decisão do dono do
# número: é valor faturado, será descontado no DRE, mas compõe o bruto.
# Substitui o SUM(sub_total) do Gate R2, que excluía o frete.
#
# Ressalva medida e ACEITA (não ratear, não explicar artificialmente): em
# 01-24/08/2026, `total_amount` fica R$ 76.297,53 acima de
# `sub_total + shipping_fee` (0,90% do GMV). `handling_fee` explica
# R$ 60.269,26; a identidade
# `total_amount = sub_total + shipping_fee + handling_fee` fecha em 146.861
# de 149.784 pedidos (98,05%). Sobram R$ 16.028,27 (0,19% do GMV) em 2.923
# pedidos sem coluna que os explique — 94,8% deles têm
# `shipping_fee_platform_discount` não-nulo, mas sem identidade linear.
# `total_amount` é o total da plataforma e é usado como tal.
#
# raw_dedup é uma camada DEFENSIVA: order_id já tem constraint
# UNIQUE em raw.tiktok_shop_orders (confirmado por inspeção nesta mesma
# correção), então isto não deveria mudar nenhuma linha hoje — existe para
# não depender silenciosamente dessa constraint se ela for relaxada no
# futuro. raw_daily é a tabela DIRIGENTE (LEFT JOIN de gold nela, nunca o
# contrário): um dia com pedidos na Raw sempre produz linha, mesmo sem
# correspondente em gold.tiktok_brand_daily, e mesmo quando todos os pedidos
# do dia são CANCELLED (gmv=0 explícito, dia não desaparece).
#
# Gate DQ-TK1 — o que mudou em relação ao Gate R2.1:
# - `orders`: agora é a contagem da população COMERCIAL, a mesma do GMV. O
#   Gate R2.1 usava `COALESCE(g.orders, r.orders_eligible)` — o relatório de
#   conteúdo da Gold — como denominador, sabendo que são populações
#   incompatíveis. Em ago/2026 isso dava 179.059 pedidos contra 149.784
#   comerciais, e um ticket de R$ 43,80 contra R$ 56,41. Em 24/08, com a
#   maturação, chegou a R$ 8,64 — GMV filtrado por status dividido por
#   pedidos não filtrados. `g.orders` continua exposto, com nome próprio
#   (`content_orders`), e NUNCA volta a ser denominador comercial.
# - `avg_ticket`: GMV comercial / pedidos comerciais. Mesma população.
# - `gmv_video`/`gmv_live`/`gmv_card`: seguem passthrough absoluto da Gold,
#   sem rateio nem escala. Sua soma é "GMV atribuído a conteúdo" e tem base
#   PRÓPRIA — não fecha com o headline e não deve ser apresentada como
#   quebra dele (ver nota em transforms/tiktok_brand_daily.py).
# - `orders_unknown_status`: status nulo OU fora dos 8 conhecidos. fetch()
#   BLOQUEIA (antes era só warning — foi assim que AWAITING_COLLECTION ficou
#   fora do GMV sem ninguém ver).
# - `orders_commercial_null_amount`: pedido comercial com `total_amount IS
#   NULL` — fetch() BLOQUEIA, em vez de somar 0 silenciosamente.
# - `canceled_orders`: MEDIDO na Raw deduplicada (`COUNT` de CANCELLED no
#   mesmo `raw_daily` do GMV) — cancelamento TEM fonte confiável: 37.598
#   pedidos em 01-24/08/2026. Nunca lido da Gold, que grava 0 literal
#   (120/120 linhas, zero nulos) e é comprovadamente falso. CANCELLED segue
#   fora de `gmv` e de `orders`.
# - `cancel_rate_pct`: derivado, `canceled / (comercial + canceled) * 100`.
# - `returned_orders`/`refunded_orders`/`problem_rate`: NULL. A Raw não tem
#   nenhum status de devolução ou reembolso em toda a sua história, e
#   `problem_rate` depende dos dois. Ausência não vira zero.
QUERY = """
WITH raw_dedup AS (
    SELECT DISTINCT ON (order_id)
        order_id, brand, order_status, total_amount, created_at
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
        SUM(CASE WHEN order_status IN :commercial_statuses THEN total_amount ELSE 0 END) AS gmv,
        COUNT(*) FILTER (WHERE order_status IN :commercial_statuses)                     AS orders_commercial,
        -- Cancelamentos TÊM fonte confiável: o próprio status CANCELLED na Raw
        -- já deduplicada por order_id (37.598 pedidos em 01-24/08/2026). Contado
        -- aqui, no MESMO raw_daily do GMV, e nunca lido da Gold (que grava 0).
        -- CANCELLED continua fora de `gmv` e de `orders_commercial`.
        COUNT(*) FILTER (WHERE order_status = 'CANCELLED')                               AS orders_canceled,
        COUNT(*) FILTER (
            WHERE order_status IS NULL OR order_status NOT IN :known_statuses
        )                                                                                AS orders_unknown_status,
        COUNT(*) FILTER (
            WHERE order_status IN :commercial_statuses AND total_amount IS NULL
        )                                                                                AS orders_commercial_null_amount
    FROM raw_dedup
    GROUP BY created_at::date, brand
)
SELECT
    r.date,
    r.brand,

    -- Comercial: GMV, pedidos e ticket saem TODOS da mesma população
    -- (:commercial_statuses). Nenhum deles usa a Gold como denominador.
    r.gmv,
    r.orders_unknown_status,
    r.orders_commercial_null_amount,
    r.orders_commercial AS orders,
    CASE
        WHEN r.orders_commercial > 0
        THEN ROUND(r.gmv / r.orders_commercial, 2)
        ELSE NULL
    END AS avg_ticket,

    -- Pedidos do relatório de CONTEÚDO da Gold, com nome próprio e distinto.
    -- Existe para rastreabilidade e para medir a divergência entre as duas
    -- populações; NUNCA é denominador do ticket comercial.
    g.orders AS content_orders,

    -- Demais campos: passthrough de gold.tiktok_brand_daily, sem alteração
    -- semântica (pedidos/unidades/compradores/taxas fora do escopo deste
    -- gate). Podem ser NULL quando gold não tem linha para (date, brand).
    g.items_sold                  AS units_sold,
    g.customers                   AS unique_buyers,
    NULLIF(g.visitors, 0)         AS visitors,
    NULLIF(g.conversion_rate, 0)  AS conversion_rate,

    -- Cancelamentos: MEDIDOS na Raw deduplicada, nunca lidos da Gold (que
    -- grava 0 literal em 120/120 linhas de ago/2026, com a Raw registrando
    -- 37.598 CANCELLED no mesmo período).
    r.orders_canceled             AS canceled_orders,

    -- Taxa de cancelamento sobre a população de pedidos COLOCADOS que se
    -- resolveram: comercial + cancelado. Mesmo contrato já adotado pela Torre
    -- (ver pipelines/reconciliation/monitor_bug8_invariants.py: "cancel_rate_pct
    -- consistente com canceled/(completed+canceled)"). Precisão de 2 casas,
    -- igual a `avg_ticket` aqui e às taxas do Shopee. Denominador zero => NULL:
    -- sem pedido resolvido no dia não existe taxa, e 0% seria uma afirmação
    -- falsa. `::numeric` evita divisão inteira entre os dois COUNT.
    CASE
        WHEN (r.orders_commercial + r.orders_canceled) > 0
        THEN ROUND(
            r.orders_canceled::numeric
            / (r.orders_commercial + r.orders_canceled) * 100, 2)
        ELSE NULL
    END AS cancel_rate_pct,

    -- SEM FONTE => NULL, nunca 0. A Raw não tem nenhum status de devolução ou
    -- reembolso em toda a sua história (os 8 status conhecidos são os de
    -- KNOWN_ORDER_STATUSES), e a Gold grava 0 literal nos dois. Um 0 aqui é
    -- indistinguível de "não houve devolução", o que não se pode afirmar.
    NULL::bigint                  AS returned_orders,
    NULL::bigint                  AS refunded_orders,

    -- problem_rate: NULL. Não é dívida futura — é ausência de fonte hoje. O
    -- indicador mistura cancelamento com devolução/reembolso, e devolução e
    -- reembolso não existem na Raw. Derivá-lo só do cancelamento mudaria a
    -- definição do indicador; deixá-lo vir da Gold reintroduziria o 0 falso
    -- (120/120 linhas em ago/2026). Sem fonte completa, não se afirma nada.
    NULL::numeric                 AS problem_rate,
    g.delivered_orders,
    g.avg_delivery_hours,

    -- Conteúdo TikTok — passthrough ABSOLUTO da Gold. Gate DQ-TK1: não
    -- ratear, não escalar, não ajustar para fechar com o headline. A soma
    -- destes três é "GMV atribuído a conteúdo" e tem base PRÓPRIA: em
    -- ago/2026 ela ficou 0,65% a 4,75% ACIMA do GMV comercial por marca,
    -- porque mede atribuição de conteúdo e não pedido/dia. Qualquer share de
    -- vídeo/live/card usa a soma dos três como denominador — nunca `gmv`, e
    -- nunca chamado de "share das vendas totais".
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
    logger.info(
        "TikTok: buscando %s → %s (GMV = total_amount da população comercial, Gate DQ-TK1)",
        date_from, date_to,
    )
    rows = datamart_query(
        QUERY,
        {
            "brands": BRANDS_IN_SCOPE,
            "date_from": date_from,
            "date_to_exclusive": date_to + timedelta(days=1),
            "commercial_statuses": COMMERCIAL_ORDER_STATUSES,
            "known_statuses": KNOWN_ORDER_STATUSES,
        },
    )

    # Bloqueio 1 — status desconhecido ou nulo. É o mais fundamental: um status
    # novo pode ser comercial, e tratá-lo como não-comercial produziria um GMV
    # silenciosamente incompleto. Foi exatamente o que aconteceu com
    # AWAITING_COLLECTION entre o Gate R2 e este. Bloqueia antes do upsert.
    unknown_total = sum(row.get("orders_unknown_status", 0) or 0 for row in rows)
    if unknown_total:
        raise TikTokConnectorError(
            f"{unknown_total} pedido(s) com order_status nulo ou fora dos status "
            f"conhecidos ({', '.join(KNOWN_ORDER_STATUSES)}) em "
            f"raw.tiktok_shop_orders no intervalo {date_from}..{date_to}. Um status "
            "novo não pode ser classificado por inferência: carregamento bloqueado "
            "antes de qualquer transformação/upsert. Classifique o status como "
            "comercial ou não-comercial e inclua-o explicitamente em "
            "COMMERCIAL_ORDER_STATUSES ou NON_COMMERCIAL_ORDER_STATUSES."
        )

    # Bloqueio 2 — total_amount nulo em pedido comercial. Um nulo contribuiria
    # 0 na soma, sem nenhum aviso.
    null_amount_total = sum(row.get("orders_commercial_null_amount", 0) or 0 for row in rows)
    if null_amount_total:
        raise TikTokConnectorError(
            f"{null_amount_total} pedido(s) comercial(is) com total_amount nulo em "
            f"raw.tiktok_shop_orders no intervalo {date_from}..{date_to} — GMV "
            "incompleto seria calculado silenciosamente; carregamento bloqueado "
            "antes de qualquer transformação/upsert."
        )

    for row in rows:
        row.pop("orders_unknown_status", None)
        row.pop("orders_commercial_null_amount", None)
    logger.info("TikTok: %d linhas retornadas", len(rows))
    return rows


def fetch_incremental(days_back: int = MIN_INCREMENTAL_LOOKBACK_DAYS) -> list[dict]:
    """Busca os últimos N dias para sync incremental diário.

    A janela é ELEVADA ao piso `MIN_INCREMENTAL_LOOKBACK_DAYS` quando vier
    menor. Motivo: o status de um pedido TikTok amadurece por dias, e uma
    janela curta congela dias ainda imaturos que nunca mais são revisitados.
    Nunca reduz uma janela maior pedida pelo chamador.
    """
    effective_days = max(days_back, MIN_INCREMENTAL_LOOKBACK_DAYS)
    if effective_days != days_back:
        logger.warning(
            "TikTok: lookback incremental de %d dia(s) elevado ao piso de %d — "
            "janelas curtas congelam dias ainda imaturos.",
            days_back, effective_days,
        )
    # Gate DQ-D1: teto em D-1 (America/Sao_Paulo), nunca D0. A LARGURA inclusiva
    # da janela e' a mesma de antes — apenas o teto desceu um dia.
    inicio, fim = closed_window(effective_days)
    return fetch(inicio, fim)


def fetch_backfill(days_back: int = 90) -> list[dict]:
    inicio, fim = closed_window(days_back)
    return fetch(inicio, fim)
