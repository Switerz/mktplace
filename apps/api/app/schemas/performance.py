from __future__ import annotations

from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field


class FiltersEcho(BaseModel):
    """Ecoa os filtros efetivamente aplicados — canal(is) e marca(s) — para
    o cliente confirmar o que o backend realmente usou (ver
    docs/filtros_globais_contrato.md)."""
    channels: str
    brands: Optional[list[str]] = None


class KpiSummary(BaseModel):
    gmv: float
    tiktok_gmv: Optional[float] = None
    ml_gmv: Optional[float] = None
    shopee_gmv: Optional[float] = None
    orders: int
    avg_ticket: float
    ad_spend: Optional[float] = None
    ml_roas: Optional[float] = None
    ml_cancel_rate_pct: Optional[float] = None
    tiktok_customers: Optional[int] = None
    ml_unique_buyers: Optional[int] = None
    shopee_unique_buyers: Optional[int] = None
    shopee_roas: Optional[float] = None


class OverviewResponse(BaseModel):
    ref_month: Optional[str] = None   # "2026-05"; None quando date_from/date_to e um intervalo personalizado
    marketplace: str                  # "all" | "tiktok" | "ml"
    current: KpiSummary
    previous: KpiSummary
    gmv_mom_pct: Optional[float] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    compare_date_from: Optional[date] = None
    compare_date_to: Optional[date] = None
    filters: Optional[FiltersEcho] = None
    refreshed_at: Optional[str] = None


class BrandPerformance(BaseModel):
    brand: str              # "barbours"
    label: str              # "BARBOURS"
    tiktok_gmv: Optional[float] = None
    ml_gmv: Optional[float] = None
    shopee_gmv: Optional[float] = None
    total_gmv: float
    orders: int
    avg_ticket: Optional[float] = None
    tiktok_gmv_prev: Optional[float] = None
    ml_gmv_prev: Optional[float] = None
    shopee_gmv_prev: Optional[float] = None
    total_gmv_prev: float
    mom_pct: Optional[float] = None
    cos_pct: Optional[float] = None
    gpm: Optional[float] = None
    ml_roas: Optional[float] = None
    ml_cancel_rate_pct: Optional[float] = None


class BrandsResponse(BaseModel):
    ref_month: Optional[str] = None
    brands: list[BrandPerformance]
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    compare_date_from: Optional[date] = None
    compare_date_to: Optional[date] = None
    filters: Optional[FiltersEcho] = None
    refreshed_at: Optional[str] = None


class MonthlyBrandGmv(BaseModel):
    mes: str                # "2026-01"
    mes_label: str          # "Jan/26"
    barbours: float = 0
    kokeshi: float = 0
    apice: float = 0
    lescent: float = 0
    rituaria: float = 0


class MonthlyResponse(BaseModel):
    data: list[MonthlyBrandGmv]


class TrendPoint(BaseModel):
    date: str
    label: str
    gmv: float
    orders: int


class TrendComparison(BaseModel):
    """Serie do periodo ANTERIOR (Gate V2-2), com as datas reais da janela.

    Nao existe shift de datas: `data` traz os buckets do proprio periodo
    anterior, e o alinhamento com o atual e' feito no cliente por posicao
    ordinal do bucket.
    """
    date_from: date
    date_to: date
    data: list[TrendPoint]


class TrendResponse(BaseModel):
    # Granularidade EFETIVAMENTE usada: "day" | "week" | "month".
    granularity: str
    data: list[TrendPoint]
    # Gate V2-2, campo ADITIVO e opcional:
    # - `None`  => comparacao NAO solicitada (`compare=false`);
    # - objeto  => solicitada; `data: []` significa "sem registros na janela
    #   anterior", que e' diferente de nao ter sido pedida.
    comparison: Optional[TrendComparison] = None
    date_from: date
    date_to: date
    filters: Optional[FiltersEcho] = None
    refreshed_at: Optional[str] = None


class DailyRow(BaseModel):
    date: date
    tiktok_gmv: Optional[float] = None
    ml_gmv: Optional[float] = None
    shopee_gmv: Optional[float] = None
    total_gmv: float
    orders: int
    avg_ticket: Optional[float] = None
    ad_spend: Optional[float] = None


class DailyResponse(BaseModel):
    brand: str
    marketplace: str
    data: list[DailyRow]


class QualityKpis(BaseModel):
    tiktok_problem_rate: Optional[float] = None
    tiktok_cancel_rate: Optional[float] = None
    tiktok_avg_delivery_days: Optional[float] = None
    ml_cancel_rate_pct: Optional[float] = None
    ml_not_delivered_rate_pct: Optional[float] = None
    ml_avg_delivery_days: Optional[float] = None
    shopee_cancel_rate_pct: Optional[float] = None
    shopee_return_rate_pct: Optional[float] = None


class QualityBrandRow(BaseModel):
    brand: str
    label: str
    tiktok_orders: Optional[int] = None
    tiktok_canceled: Optional[int] = None
    tiktok_refunded: Optional[int] = None
    tiktok_returned: Optional[int] = None
    tiktok_problem_rate: Optional[float] = None
    tiktok_cancel_rate: Optional[float] = None
    tiktok_avg_delivery_days: Optional[float] = None
    ml_cancel_rate_pct: Optional[float] = None
    ml_not_delivered_rate_pct: Optional[float] = None
    ml_cancelled_orders: Optional[int] = None
    ml_total_orders: Optional[int] = None
    ml_not_delivered_shipments: Optional[int] = None
    ml_avg_delivery_days: Optional[float] = None
    ml_repeat_buyer_rate_pct: Optional[float] = None
    ml_gmv_per_buyer: Optional[float] = None
    ml_gmv_mom_pct: Optional[float] = None
    ml_new_buyers: Optional[int] = None
    ml_unique_buyers: Optional[int] = None
    ml_shipping_pct_of_gmv: Optional[float] = None
    shopee_orders: Optional[int] = None
    shopee_canceled_orders: Optional[int] = None
    shopee_returned_orders: Optional[int] = None
    shopee_cancel_rate_pct: Optional[float] = None
    shopee_return_rate_pct: Optional[float] = None


class QualityResponse(BaseModel):
    ref_month: Optional[str] = None
    marketplace: str
    kpis: QualityKpis
    brands: list[QualityBrandRow]
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    compare_date_from: Optional[date] = None
    compare_date_to: Optional[date] = None
    filters: Optional[FiltersEcho] = None
    refreshed_at: Optional[str] = None


class FinanceiroKpis(BaseModel):
    tiktok_gmv: Optional[float] = None
    tiktok_settlement: Optional[float] = None
    tiktok_fees: Optional[float] = None
    tiktok_avg_fee_pct: Optional[float] = None
    tiktok_avg_settlement_pct: Optional[float] = None
    ml_ad_spend: Optional[float] = None
    ml_ad_revenue: Optional[float] = None
    ml_gmv: Optional[float] = None
    ml_roas: Optional[float] = None
    ml_acos_pct: Optional[float] = None
    ml_cpc: Optional[float] = None
    ml_total_cost_pct: Optional[float] = None
    shopee_gmv: Optional[float] = None
    shopee_settlement: Optional[float] = None
    shopee_fees: Optional[float] = None
    shopee_avg_fee_pct: Optional[float] = None
    shopee_avg_settlement_pct: Optional[float] = None
    shopee_ad_spend: Optional[float] = None
    shopee_roas: Optional[float] = None


class FinanceiroBrandRow(BaseModel):
    brand: str
    label: str
    tiktok_gmv: Optional[float] = None
    tiktok_settlement: Optional[float] = None
    tiktok_fees: Optional[float] = None
    tiktok_avg_fee_pct: Optional[float] = None
    tiktok_avg_settlement_pct: Optional[float] = None
    ml_gmv: Optional[float] = None
    ml_ad_spend: Optional[float] = None
    ml_ad_revenue: Optional[float] = None
    ml_roas: Optional[float] = None
    ml_acos_pct: Optional[float] = None
    ml_cpc: Optional[float] = None
    ml_ctr_pct: Optional[float] = None
    ml_ad_clicks: Optional[int] = None
    ml_ad_impressions: Optional[int] = None
    ml_seller_shipping_cost: Optional[float] = None
    ml_shipping_pct_of_gmv: Optional[float] = None
    ml_total_cost_pct: Optional[float] = None
    shopee_gmv: Optional[float] = None
    shopee_settlement: Optional[float] = None
    shopee_fees: Optional[float] = None
    shopee_avg_fee_pct: Optional[float] = None
    shopee_avg_settlement_pct: Optional[float] = None
    shopee_ad_spend: Optional[float] = None
    shopee_ad_revenue: Optional[float] = None
    shopee_roas: Optional[float] = None
    shopee_shipping_cost: Optional[float] = None
    shopee_shipping_pct_of_gmv: Optional[float] = None


class FinanceiroResponse(BaseModel):
    ref_month: Optional[str] = None
    marketplace: str
    kpis: FinanceiroKpis
    brands: list[FinanceiroBrandRow]
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    compare_date_from: Optional[date] = None
    compare_date_to: Optional[date] = None
    filters: Optional[FiltersEcho] = None
    refreshed_at: Optional[str] = None


class CanaisKpis(BaseModel):
    tiktok_gmv: Optional[float] = None
    tiktok_gmv_video: Optional[float] = None
    tiktok_gmv_live: Optional[float] = None
    tiktok_gmv_card: Optional[float] = None
    # UE-F1A: os tres percentuais abaixo se fecham sobre
    # `tiktok_content_gmv_base` (= video+live+card), NAO sobre `tiktok_gmv`.
    # Sao mix da linhagem de conteudo, nao share das vendas totais.
    tiktok_video_pct: Optional[float] = None
    tiktok_live_pct: Optional[float] = None
    tiktok_card_pct: Optional[float] = None
    # Denominador explicito do mix, exposto para a UI poder mostrar o valor
    # monetario da base em vez de so percentuais.
    tiktok_content_gmv_base: Optional[float] = None
    # Diagnostico de reconciliacao entre linhagens (base de conteudo x GMV
    # comercial). Nunca share, nunca cobertura. Ver
    # docs/UNIT_ECONOMICS_ATTRIBUTION_AUDIT.md secao 11.
    tiktok_content_gmv_divergence_pct: Optional[float] = None
    tiktok_visitors: Optional[int] = None
    tiktok_customers: Optional[int] = None
    tiktok_conversion_rate: Optional[float] = None
    ml_unique_buyers: Optional[int] = None
    ml_new_buyers: Optional[int] = None
    ml_repeat_buyers: Optional[int] = None
    ml_new_buyer_pct: Optional[float] = None
    ml_repeat_buyer_rate_pct: Optional[float] = None
    ml_gmv_per_buyer: Optional[float] = None
    shopee_gmv: Optional[float] = None
    shopee_unique_buyers: Optional[int] = None
    shopee_new_buyers: Optional[int] = None
    shopee_repeat_buyers: Optional[int] = None
    shopee_new_buyer_pct: Optional[float] = None
    shopee_repeat_buyer_rate_pct: Optional[float] = None
    shopee_gmv_per_buyer: Optional[float] = None
    shopee_visitors: Optional[int] = None
    shopee_conversion_rate: Optional[float] = None


class CanaisBrandRow(BaseModel):
    brand: str
    label: str
    tiktok_gmv: Optional[float] = None
    tiktok_gmv_video: Optional[float] = None
    tiktok_gmv_live: Optional[float] = None
    tiktok_gmv_card: Optional[float] = None
    # UE-F1A: mesmo contrato da CanaisKpis — os tres percentuais se fecham
    # sobre `tiktok_content_gmv_base`, nunca sobre `tiktok_gmv`.
    tiktok_video_pct: Optional[float] = None
    tiktok_live_pct: Optional[float] = None
    tiktok_card_pct: Optional[float] = None
    tiktok_content_gmv_base: Optional[float] = None
    tiktok_content_gmv_divergence_pct: Optional[float] = None
    tiktok_visitors: Optional[int] = None
    tiktok_customers: Optional[int] = None
    tiktok_conversion_rate: Optional[float] = None
    ml_gmv: Optional[float] = None
    ml_unique_buyers: Optional[int] = None
    ml_new_buyers: Optional[int] = None
    ml_repeat_buyers: Optional[int] = None
    ml_repeat_buyer_rate_pct: Optional[float] = None
    ml_gmv_per_buyer: Optional[float] = None
    shopee_gmv: Optional[float] = None
    shopee_unique_buyers: Optional[int] = None
    shopee_new_buyers: Optional[int] = None
    shopee_repeat_buyers: Optional[int] = None
    shopee_new_buyer_pct: Optional[float] = None
    shopee_repeat_buyer_rate_pct: Optional[float] = None
    shopee_gmv_per_buyer: Optional[float] = None
    shopee_cancel_rate_pct: Optional[float] = None
    shopee_visitors: Optional[int] = None
    shopee_conversion_rate: Optional[float] = None


class CanaisChannelRow(BaseModel):
    """Uma linha da matriz comparativa marca x canal (Gate 2, docs/sections/
    canais_audit.md secao 14). `ads_available`/`marketplace_cost_available`/
    `seller_shipping_available` distinguem "nao aplicavel" (N/A — o canal nao
    opera esse tipo de custo) de "aplicavel mas sem dado" (Sem dado — o mart
    nao tem o campo populado). Nunca inclui desconto ou afiliados (bloqueados
    no Gate 1 por falta de fonte/semantica confiavel)."""
    brand: str
    label: str
    channel: str
    channel_label: str
    gmv: float
    orders: int
    ad_spend: Optional[float] = None
    ad_revenue: Optional[float] = None
    ads_gmv_pct: Optional[float] = None
    roas: Optional[float] = None
    acos_pct: Optional[float] = None
    marketplace_cost_pct: Optional[float] = None
    seller_shipping_pct: Optional[float] = None
    ads_available: bool
    marketplace_cost_available: bool
    seller_shipping_available: bool
    ads_applicable: bool
    marketplace_cost_applicable: bool
    seller_shipping_applicable: bool
    data_warning: Optional[str] = None
    signals: list[str] = Field(default_factory=list)


class CanaisChannelMedian(BaseModel):
    """Mediana/percentil 75 por canal, usados como limiar dos sinais de
    oportunidade (`custo_alto`/`frete_alto`/`roas_forte`/`ads_subutilizado`).
    So calculado com >=2 marcas com dado valido no canal — None quando a
    comparacao seria contra si mesma."""
    channel: str
    channel_label: str
    gmv_median: Optional[float] = None
    ads_gmv_pct_median: Optional[float] = None
    roas_median: Optional[float] = None
    marketplace_cost_pct_median: Optional[float] = None
    marketplace_cost_pct_p75: Optional[float] = None
    seller_shipping_pct_median: Optional[float] = None
    seller_shipping_pct_p75: Optional[float] = None
    brands_with_data: int


# ---------------------------------------------------------------------------
# Impacto de afiliados no resultado — contrato §23 de
# docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md
#
# QUATRO DIMENSOES ORTOGONAIS, nao um enum unico. Disponibilidade, alinhamento
# de periodo, cobertura de marca e frescor sao INDEPENDENTES e coexistem: um
# bloco pode ser `available` + `complete_month` + `incomplete_brand_coverage` +
# `manual_snapshot` ao mesmo tempo. Com um enum mutuamente exclusivo seria
# preciso escolher qual verdade contar, escondendo as outras tres.
# ---------------------------------------------------------------------------

#: A fonte tem dado para este canal/escopo?
AvailabilityStatus = Literal[
    "available",
    "unavailable_no_source",   # ML/Shopee: fonte equivalente NAO confirmada
    "no_eligible_brand",       # filtro de marca nao intersecta as marcas com dado
    "error",                   # falha esperada ao ler a fact
]

#: O periodo pedido fecha competencia mensal?
PeriodStatus = Literal[
    "complete_month",          # exatamente um mes calendario, fechado
    "complete_months",         # varios meses calendario, todos fechados
    "partial_month",           # mes corrente, ou intervalo dentro de um mes
    "not_month_aligned",       # nao fecha mes(es) calendario
]

#: A competencia tem as cinco marcas na fact?
CoverageStatus = Literal["complete", "incomplete_brand_coverage", "unknown"]

#: Quao recente e' a fotografia. `fresh`/`stale` SO passam a ser atribuiveis
#: depois da frente UE2-C (§23.12) definir rotina e SLA: hoje a carga e' manual
#: e qualquer limiar temporal seria inventado.
FreshnessStatus = Literal["manual_snapshot", "fresh", "stale", "unknown"]

#: Indisponibilidade TIPADA do retorno. Enum de UM valor, deliberado: cria o
#: lugar para DECLARAR ausencia sem criar o lugar para GUARDAR numero. Nao
#: existe `return_amount`, `roi`, `roas` nem receita atribuida.
ReturnAvailability = Literal["unavailable_no_attributed_revenue"]


class AffiliateCostRow(BaseModel):
    """Um lancamento por (canal, marca, competencia).

    Os tres campos terminam em `_signed` de proposito: o nome carrega a
    semantica de LANCAMENTO CONTABIL ASSINADO, nao de magnitude de custo ja
    normalizada. `abs()` e' proibido em todo o caminho (§18.5.1, §23.9).
    """
    channel: str
    brand: str
    ref_month: str                                     # "YYYY-MM", sempre explicito
    creator_commission_signed: Optional[float] = None
    partner_commission_signed: Optional[float] = None
    affiliate_ads_commission_signed: Optional[float] = None
    coverage_status: CoverageStatus = "unknown"
    brands_present_in_month: Optional[int] = None


class AffiliateChannelStatus(BaseModel):
    """Status POR CANAL. Autoritativo: o agregado do bloco nunca o sobrescreve,
    e disponibilidade do TikTok jamais torna ML/Shopee disponiveis (§23.6.1)."""
    channel: str
    availability_status: AvailabilityStatus
    reason_note: str


class AffiliateCostsBlock(BaseModel):
    availability_status: AvailabilityStatus
    period_status: PeriodStatus
    coverage_status: CoverageStatus
    freshness_status: FreshnessStatus

    rows: list[AffiliateCostRow] = Field(default_factory=list)
    channels: list[AffiliateChannelStatus] = Field(default_factory=list)
    #: METADADO de auditoria — diz quais competencias entraram no escopo. NAO
    #: acompanha agregado multimensal, porque nenhum e' devolvido (§23.7).
    months_included: list[str] = Field(default_factory=list)

    #: MAX(synced_at) da fact no escopo retornado. NAO reutiliza o
    #: `refreshed_at` geral de /canais: reutiliza-lo classificaria como recente
    #: um dado congelado so' porque a rota respondeu agora (§23.5.2).
    affiliate_refreshed_at: Optional[str] = None
    #: `last_successful_upper_bound` do sync_state — ate onde a fonte foi lida.
    source_watermark: Optional[str] = None

    return_availability: ReturnAvailability = "unavailable_no_attributed_revenue"
    return_note: str
    source_note: str
    limitation_note: str


class CanaisResponse(BaseModel):
    ref_month: Optional[str] = None
    marketplace: str
    kpis: CanaisKpis
    brands: list[CanaisBrandRow]
    channel_rows: list[CanaisChannelRow] = Field(default_factory=list)
    channel_medians: list[CanaisChannelMedian] = Field(default_factory=list)
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    compare_date_from: Optional[date] = None
    compare_date_to: Optional[date] = None
    filters: Optional[FiltersEcho] = None
    #: Frescor do pipeline diario de /canais. Permanece INDEPENDENTE do frescor
    #: do bloco de afiliados (`affiliate_costs.affiliate_refreshed_at`).
    refreshed_at: Optional[str] = None
    #: ADITIVO e opcional (§23). `None` quando o bloco nao foi montado.
    affiliate_costs: Optional[AffiliateCostsBlock] = None


class ProdutoShopeeRow(BaseModel):
    brand: str
    sku_ref: Optional[str] = None
    product_name: str
    # Atributo descritivo, nao parte da chave de identidade — a UNIQUE
    # constraint real do mart e (ref_month, brand, sku_ref_key, product_name).
    # Pode ja ter sido consolidado/sobrescrito rio acima pelo ETL antes de
    # chegar aqui (ver docs/sections/produtos_audit.md, Bug 5 e Bug 8).
    variation_name: Optional[str] = None
    gmv: float
    units_sold: int
    orders: int
    canceled_orders: int
    cancel_rate_pct: Optional[float] = None
    # Valor calculado pelo proprio ETL (nunique de comprador na agregacao
    # mensal); a API nunca soma/consolida entre linhas do mart — cada linha
    # de saida e exatamente 1 linha do mart (ver docs/sections/produtos_audit.md,
    # Bug 9 — chave estrita, sem consolidacao automatica por sku_ref_key).
    unique_buyers: Optional[int] = None
    avg_price: Optional[float] = None
    pareto_bucket: Optional[str] = None


class ProdutosShopeeResponse(BaseModel):
    ref_month: str
    total: int
    limit: int
    offset: int
    items: list[ProdutoShopeeRow]


class ProdutoMLRow(BaseModel):
    brand: str
    item_id: str
    seller_sku: Optional[str] = None
    title: Optional[str] = None
    gross_revenue: float
    units_sold: int
    unique_buyers: Optional[int] = None
    avg_price: Optional[float] = None
    cancel_rate_pct: Optional[float] = None
    pareto_bucket: Optional[str] = None
    revenue_velocity: Optional[str] = None
    ad_roas: Optional[float] = None
    ad_acos_pct: Optional[float] = None
    ad_spend: Optional[float] = None
    ad_efficiency: Optional[str] = None
    action_signal: Optional[str] = None
    estimated_margin: Optional[float] = None
    revenue_share_pct: Optional[float] = None
    product_status: Optional[str] = None


class ProdutosMLResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ProdutoMLRow]
    scope: str = "ranking_acumulado_atual"
    refreshed_at: Optional[str] = None


class ParetoBucketSummary(BaseModel):
    bucket: str
    label: str
    description: str
    gmv: float
    count: int
    gmv_pct: float


class ProdutosMLSummaryResponse(BaseModel):
    total_gmv: float
    # total_count: TODOS os produtos no escopo filtrado (inclui GMV=0).
    # eligible_count: apenas os com GMV>0 — os unicos que entram nos buckets
    # A/B/C/D (soma dos buckets == eligible_count, nunca == total_count).
    # excluded_zero_gmv_count = total_count - eligible_count.
    total_count: int
    eligible_count: int
    excluded_zero_gmv_count: int
    brand: Optional[str] = None
    buckets: list[ParetoBucketSummary]
    scope: str = "ranking_acumulado_atual"
    refreshed_at: Optional[str] = None
    # total_gmv / total_units do escopo elegivel (GMV>0) — media ponderada,
    # NUNCA media simples de avg_price por linha (ver produtos_audit.md 10.5).
    avg_price_weighted: Optional[float] = None


class ProdutoTikTokRow(BaseModel):
    brand: str
    product_id: str
    product_name: str
    gmv: float
    orders: int
    items_sold: int
    # gmv / items_sold, calculado no service (NULL quando items_sold = 0) —
    # ver docs/sections/produtos_audit.md secao 10.2.
    avg_price: Optional[float] = None
    pct_gmv_video: Optional[float] = None
    pct_gmv_live: Optional[float] = None
    pct_gmv_card: Optional[float] = None
    problem_rate: Optional[float] = None
    rating_avg: Optional[float] = None
    total_ratings: Optional[int] = None
    pareto_bucket: Optional[str] = None


class ProdutosTikTokResponse(BaseModel):
    ref_month: str
    total: int
    limit: int
    offset: int
    items: list[ProdutoTikTokRow]


class ProdutosTikTokSummaryResponse(BaseModel):
    ref_month: str
    total_gmv: float
    total_count: int
    eligible_count: int
    excluded_zero_gmv_count: int
    brand: Optional[str] = None
    buckets: list[ParetoBucketSummary]
    avg_price_weighted: Optional[float] = None


class ProdutosShopeeSummaryResponse(BaseModel):
    ref_month: str
    total_gmv: float
    total_count: int
    eligible_count: int
    excluded_zero_gmv_count: int
    brand: Optional[str] = None
    buckets: list[ParetoBucketSummary]
    avg_price_weighted: Optional[float] = None


# ---------------------------------------------------------------------------
# Pedidos
# ---------------------------------------------------------------------------

class PedidosKpis(BaseModel):
    total_orders: int
    total_gmv: float
    avg_ticket: float
    cancel_rate_pct: Optional[float] = None


class PedidosCanalKpis(BaseModel):
    orders: int
    canceled: int
    gmv: float
    cancel_rate_pct: Optional[float] = None
    delivered: Optional[int] = None


class PedidosDailyRow(BaseModel):
    date: str
    tiktok_orders: int = 0
    tiktok_canceled: int = 0
    ml_orders: int = 0
    ml_canceled: int = 0
    total_orders: int = 0
    total_gmv: float = 0.0


class PedidosBrandRow(BaseModel):
    brand: str
    label: str
    tiktok_orders: Optional[int] = None
    tiktok_canceled: Optional[int] = None
    tiktok_cancel_rate_pct: Optional[float] = None
    tiktok_gmv: Optional[float] = None
    ml_orders: Optional[int] = None
    ml_canceled: Optional[int] = None
    ml_cancel_rate_pct: Optional[float] = None
    ml_gmv: Optional[float] = None
    total_orders: int = 0
    total_gmv: float = 0.0


class PedidosResponse(BaseModel):
    days_back: int
    kpis: PedidosKpis
    tiktok: PedidosCanalKpis
    ml: PedidosCanalKpis
    daily: list[PedidosDailyRow]
    by_brand: list[PedidosBrandRow]
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    filters: Optional[FiltersEcho] = None
    refreshed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Tempo Real
# ---------------------------------------------------------------------------

class TempoRealHour(BaseModel):
    hour: int
    gmv_hour: float
    gmv_acumulado: float
    gmv_hour_prior: Optional[float] = None
    gmv_acumulado_prior: Optional[float] = None
    gmv_avg7d: Optional[float] = None
    customers_hour: int
    customers_acumulado: int
    conversion_hour: Optional[float] = None
    ticket_medio: Optional[float] = None


class TempoRealBrand(BaseModel):
    brand: str
    label: str
    gmv_hoje: float
    gmv_ontem: Optional[float] = None
    delta_pct: Optional[float] = None
    ritmo_projetado: Optional[float] = None
    clientes_hoje: int
    ultima_hora: int
    conversion_hora: Optional[float] = None
    ticket_medio: Optional[float] = None
    hours: list[TempoRealHour]


class TempoRealResponse(BaseModel):
    total_gmv_hoje: float
    total_gmv_ontem: Optional[float] = None
    total_delta_pct: Optional[float] = None
    total_ritmo_projetado: Optional[float] = None
    brands: list[TempoRealBrand]


# ---------------------------------------------------------------------------
# Brand Detail
# ---------------------------------------------------------------------------

class BrandDetailDayRow(BaseModel):
    date: str
    gmv: Optional[float] = None
    gmv_video: Optional[float] = None
    gmv_live: Optional[float] = None
    gmv_card: Optional[float] = None
    new_videos_posted: Optional[int] = None


class BrandDetailCreator(BaseModel):
    creator: str
    gmv: float
    videos: int
    lives: int


class BrandDetailProduto(BaseModel):
    product_id: str
    product_name: str
    gmv: float
    orders: int
    videos: int
    gpm: Optional[float] = None


class BrandDetailResponse(BaseModel):
    brand: str
    label: str
    ref_month: str
    #: BE5 — competencias que realmente possuem linha para esta marca,
    #: `YYYY-MM` decrescente. Lista vazia = marca sem historico. `ref_month`
    #: acima continua ecoando o mes PEDIDO, mesmo que ele nao esteja aqui.
    available_months: list[str] = []
    gmv: float
    orders: int
    customers: int
    cvr_pct: Optional[float] = None
    cos_pct: Optional[float] = None
    pct_video: Optional[float] = None
    pct_live: Optional[float] = None
    pct_card: Optional[float] = None
    active_videos: int
    new_videos_posted: int
    active_video_creators: int
    total_views: int
    total_lives: int
    live_creators: int
    gpm: Optional[float] = None
    gmv_per_video: Optional[float] = None
    gmv_per_creator: Optional[float] = None
    gmv_per_live: Optional[float] = None
    videos_per_creator: Optional[float] = None
    fresh_videos: int
    evergreen_videos: int
    gmv_fresh: float
    gmv_evergreen: float
    pct_gmv_fresh: Optional[float] = None
    viewers_pct_female: Optional[float] = None
    viewers_pct_male: Optional[float] = None
    viewers_pct_18_24: Optional[float] = None
    viewers_pct_25_34: Optional[float] = None
    viewers_pct_35_44: Optional[float] = None
    viewers_pct_45_54: Optional[float] = None
    viewers_pct_55_plus: Optional[float] = None
    daily: list[BrandDetailDayRow]
    top_creators: list[BrandDetailCreator]
    top_produtos: list[BrandDetailProduto]


