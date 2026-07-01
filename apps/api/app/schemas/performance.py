from __future__ import annotations

from datetime import date
from typing import Optional
from pydantic import BaseModel


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
    ref_month: str          # "2026-05"
    marketplace: str        # "all" | "tiktok" | "ml"
    current: KpiSummary
    previous: KpiSummary
    gmv_mom_pct: Optional[float] = None


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
    ref_month: str
    brands: list[BrandPerformance]


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
    ref_month: str
    marketplace: str
    kpis: QualityKpis
    brands: list[QualityBrandRow]


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
    ref_month: str
    marketplace: str
    kpis: FinanceiroKpis
    brands: list[FinanceiroBrandRow]


class CanaisKpis(BaseModel):
    tiktok_gmv: Optional[float] = None
    tiktok_gmv_video: Optional[float] = None
    tiktok_gmv_live: Optional[float] = None
    tiktok_gmv_card: Optional[float] = None
    tiktok_video_pct: Optional[float] = None
    tiktok_live_pct: Optional[float] = None
    tiktok_card_pct: Optional[float] = None
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
    tiktok_video_pct: Optional[float] = None
    tiktok_live_pct: Optional[float] = None
    tiktok_card_pct: Optional[float] = None
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


class CanaisResponse(BaseModel):
    ref_month: str
    marketplace: str
    kpis: CanaisKpis
    brands: list[CanaisBrandRow]


class ProdutoShopeeRow(BaseModel):
    brand: str
    sku_ref: Optional[str] = None
    product_name: str
    variation_name: Optional[str] = None
    gmv: float
    units_sold: int
    orders: int
    canceled_orders: int
    cancel_rate_pct: Optional[float] = None
    unique_buyers: Optional[int] = None
    avg_price: Optional[float] = None


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


class ParetoBucketSummary(BaseModel):
    bucket: str
    label: str
    description: str
    gmv: float
    count: int
    gmv_pct: float


class ProdutosMLSummaryResponse(BaseModel):
    total_gmv: float
    total_count: int
    brand: Optional[str] = None
    buckets: list[ParetoBucketSummary]


class ProdutoTikTokRow(BaseModel):
    brand: str
    product_id: str
    product_name: str
    gmv: float
    orders: int
    items_sold: int
    pct_gmv_video: Optional[float] = None
    pct_gmv_live: Optional[float] = None
    pct_gmv_card: Optional[float] = None
    problem_rate: Optional[float] = None
    rating_avg: Optional[float] = None
    total_ratings: Optional[int] = None


class ProdutosTikTokResponse(BaseModel):
    ref_month: str
    total: int
    limit: int
    offset: int
    items: list[ProdutoTikTokRow]


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


