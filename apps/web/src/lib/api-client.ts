import {
  totalGmv, totalGmvPrev, totalOrders,
  BRANDS, GMV_MONTHLY,
} from "./mock-data";
import { calcMoM } from "./formatters";
import {
  DEFAULT_MARKETPLACE_SELECTION,
  isMarketplaceSelected,
  serializeMarketplaceSelection,
  type MarketplaceSelection,
} from "./marketplace-filter";
import { buildRegioesQueryParams } from "./regioes-query";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export type Filter = MarketplaceSelection;

// ---------- tipos espelhando a API ----------

export interface OverviewData {
  gmv: number;
  tiktok_gmv: number | null;
  ml_gmv: number | null;
  shopee_gmv: number | null;
  orders: number;
  avg_ticket: number;
  ad_spend: number | null;
  ml_roas: number | null;
  ml_cancel_rate_pct: number | null;
  shopee_roas: number | null;
  tiktok_customers: number | null;
  ml_unique_buyers: number | null;
  shopee_unique_buyers: number | null;
  gmv_mom_pct: number | null;
  prev_gmv: number;
}

export interface BrandRow {
  brand: string;
  label: string;
  tiktok_gmv: number | null;
  ml_gmv: number | null;
  shopee_gmv: number | null;
  total_gmv: number;
  orders: number;
  avg_ticket: number | null;
  tiktok_avg_ticket: number | null;
  ml_avg_ticket: number | null;
  tiktok_gmv_prev: number | null;
  ml_gmv_prev: number | null;
  shopee_gmv_prev: number | null;
  total_gmv_prev: number;
  mom_pct: number | null;
  cos_pct: number | null;
  gpm: number | null;
  ml_roas: number | null;
  ml_cancel_rate_pct: number | null;
}

export interface MonthPoint {
  mes: string;
  mes_label: string;
  barbours: number;
  kokeshi: number;
  apice: number;
  lescent: number;
  rituaria: number;
}

// ---------- cache em memória ----------

const _cache = new Map<string, { data: unknown; at: number }>();
const CACHE_TTL = 5 * 60 * 1000; // 5 min

function cacheGet<T>(key: string): T | undefined {
  const e = _cache.get(key);
  if (!e) return undefined;
  if (Date.now() - e.at > CACHE_TTL) { _cache.delete(key); return undefined; }
  return e.data as T;
}

function cacheSet<T>(key: string, data: T): T {
  _cache.set(key, { data, at: Date.now() });
  return data;
}

async function withCache<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const hit = cacheGet<T>(key);
  if (hit !== undefined) return hit;
  const result = await fn();
  return cacheSet(key, result);
}

// ---------- fetch helpers ----------

async function apiFetch<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`);
    if (!res.ok) return null;
    // `await` explicito: sem ele, uma rejeicao de res.json() (corpo invalido)
    // escaparia deste catch, pois a promise seria devolvida sem ser
    // "adotada" dentro do try/catch local.
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

// ---------- filtros globais (canal, marca, periodo, comparacao) ----------

export interface GlobalFilterParams {
  brands?: string[];
  dateFrom?: string;
  dateTo?: string;
  compare?: boolean;
}

export interface ResponseMeta {
  dateFrom: string | null;
  dateTo: string | null;
  compareDateFrom: string | null;
  compareDateTo: string | null;
  refreshedAt: string | null;
}

const EMPTY_META: ResponseMeta = {
  dateFrom: null, dateTo: null, compareDateFrom: null, compareDateTo: null, refreshedAt: null,
};

function metaFromResponse(raw: {
  date_from?: string | null; date_to?: string | null;
  compare_date_from?: string | null; compare_date_to?: string | null;
  refreshed_at?: string | null;
}): ResponseMeta {
  return {
    dateFrom: raw.date_from ?? null,
    dateTo: raw.date_to ?? null,
    compareDateFrom: raw.compare_date_from ?? null,
    compareDateTo: raw.compare_date_to ?? null,
    refreshedAt: raw.refreshed_at ?? null,
  };
}

/** Monta a querystring do contrato novo (channels/brands/date_from/date_to/
 * compare). Usa `ref_month` como fallback legado apenas quando nenhuma data
 * personalizada foi passada — nunca mistura os dois. */
function buildFilterQuery(marketplace: string, period: string | undefined, filters?: GlobalFilterParams): URLSearchParams {
  const qs = new URLSearchParams();
  qs.set("channels", marketplace);
  if (filters?.brands && filters.brands.length > 0) {
    qs.set("brands", [...filters.brands].sort().join(","));
  }
  if (filters?.dateFrom && filters?.dateTo) {
    qs.set("date_from", filters.dateFrom);
    qs.set("date_to", filters.dateTo);
  } else {
    qs.set("ref_month", period ?? refMonth());
  }
  if (filters?.compare) qs.set("compare", "true");
  return qs;
}

function refMonth(): string {
  const d = new Date();
  // mês anterior como referência padrão (igual ao service)
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

// ---------- fallbacks com mock data ----------

function overviewFromMock(selection: MarketplaceSelection): OverviewData {
  const showTk = isMarketplaceSelected(selection, "tiktok");
  const showMl = isMarketplaceSelected(selection, "ml");
  const showSh = isMarketplaceSelected(selection, "shopee");

  // Shopee ainda nao tem dataset mock proprio nesta camada de fallback.
  const tkGmv = showTk ? totalGmv("tiktok") : 0;
  const mlGmv = showMl ? totalGmv("ml") : 0;
  const tkGmvPrev = showTk ? totalGmvPrev("tiktok") : 0;
  const mlGmvPrev = showMl ? totalGmvPrev("ml") : 0;
  const tkOrders = showTk ? totalOrders("tiktok") : 0;
  const mlOrders = showMl ? totalOrders("ml") : 0;

  const gmv = tkGmv + mlGmv;
  const prev = tkGmvPrev + mlGmvPrev;
  const orders = tkOrders + mlOrders;
  const ticket = orders > 0 ? gmv / orders : 0;

  return {
    gmv,
    tiktok_gmv: showTk ? tkGmv : null,
    ml_gmv: showMl ? mlGmv : null,
    shopee_gmv: showSh ? 0 : null,
    orders,
    avg_ticket: ticket,
    ad_spend: (showMl || showSh) ? 148_600 : null,
    ml_roas: null,
    ml_cancel_rate_pct: null,
    shopee_roas: null,
    tiktok_customers: null,
    ml_unique_buyers: null,
    shopee_unique_buyers: null,
    gmv_mom_pct: calcMoM(gmv, prev),
    prev_gmv: prev,
  };
}

function brandsFromMock(selection: MarketplaceSelection): BrandRow[] {
  const showTk = isMarketplaceSelected(selection, "tiktok");
  const showMl = isMarketplaceSelected(selection, "ml");
  return BRANDS.map((b) => {
    const currentTk = showTk ? (b.tiktok ?? 0) : 0;
    const currentMl = showMl ? (b.ml ?? 0) : 0;
    const prevTk = showTk ? (b.tiktokPrev ?? 0) : 0;
    const prevMl = showMl ? (b.mlPrev ?? 0) : 0;
    const total = currentTk + currentMl;
    const totalPrev = prevTk + prevMl;
    const orders = (showTk ? (b.tiktokOrders ?? 0) : 0) + (showMl ? (b.mlOrders ?? 0) : 0);
    return {
      brand: b.brand,
      label: b.label,
      tiktok_gmv: showTk ? b.tiktok : null,
      ml_gmv: showMl ? b.ml : null,
      shopee_gmv: null,
      total_gmv: total,
      orders,
      avg_ticket: orders > 0 ? total / orders : null,
      tiktok_avg_ticket: showTk && b.tiktokOrders && b.tiktok ? b.tiktok / b.tiktokOrders : null,
      ml_avg_ticket: showMl && b.mlOrders && b.ml ? b.ml / b.mlOrders : null,
      tiktok_gmv_prev: showTk ? b.tiktokPrev : null,
      ml_gmv_prev: showMl ? b.mlPrev : null,
      shopee_gmv_prev: null,
      total_gmv_prev: totalPrev,
      mom_pct: totalPrev > 0 ? calcMoM(total, totalPrev) : null,
      cos_pct: null,
      gpm: null,
      ml_roas: null,
      ml_cancel_rate_pct: null,
    };
  }).filter((b) => b.total_gmv > 0);
}

function monthlyFromMock(): MonthPoint[] {
  return GMV_MONTHLY.map((m) => ({
    mes: m.mes,
    mes_label: m.mesLabel,
    barbours: m.barbours,
    kokeshi: m.kokeshi,
    apice: m.apice,
    lescent: m.lescent,
    rituaria: m.rituaria,
  }));
}

// ---------- funções públicas (API com fallback) ----------

export function fetchOverview(
  selection: MarketplaceSelection,
  period?: string,
  filters?: GlobalFilterParams,
): Promise<{ data: OverviewData; live: boolean; meta: ResponseMeta }> {
  interface ApiResp {
    current: {
      gmv: number;
      tiktok_gmv?: number | null;
      ml_gmv?: number | null;
      shopee_gmv?: number | null;
      orders: number;
      avg_ticket: number;
      ad_spend: number | null;
      ml_roas?: number | null;
      ml_cancel_rate_pct?: number | null;
      shopee_roas?: number | null;
      tiktok_customers?: number | null;
      ml_unique_buyers?: number | null;
      shopee_unique_buyers?: number | null;
    };
    previous: { gmv: number };
    gmv_mom_pct: number | null;
    date_from?: string | null;
    date_to?: string | null;
    compare_date_from?: string | null;
    compare_date_to?: string | null;
    refreshed_at?: string | null;
  }
  const marketplace = serializeMarketplaceSelection(selection);
  const qs = buildFilterQuery(marketplace, period, filters);
  return withCache(`overview:${qs.toString()}`, async () => {
    const raw = await apiFetch<ApiResp>(`/api/v1/performance/overview?${qs.toString()}`);
    if (raw) {
      return {
        live: true,
        meta: metaFromResponse(raw),
        data: {
          gmv: raw.current.gmv,
          tiktok_gmv: raw.current.tiktok_gmv ?? null,
          ml_gmv: raw.current.ml_gmv ?? null,
          shopee_gmv: raw.current.shopee_gmv ?? null,
          orders: raw.current.orders,
          avg_ticket: raw.current.avg_ticket,
          ad_spend: raw.current.ad_spend,
          ml_roas: raw.current.ml_roas ?? null,
          ml_cancel_rate_pct: raw.current.ml_cancel_rate_pct ?? null,
          shopee_roas: raw.current.shopee_roas ?? null,
          tiktok_customers: raw.current.tiktok_customers ?? null,
          ml_unique_buyers: raw.current.ml_unique_buyers ?? null,
          shopee_unique_buyers: raw.current.shopee_unique_buyers ?? null,
          gmv_mom_pct: raw.gmv_mom_pct,
          prev_gmv: raw.previous.gmv,
        },
      };
    }
    return { live: false, meta: EMPTY_META, data: overviewFromMock(selection) };
  });
}

export function fetchBrands(
  selection: MarketplaceSelection,
  period?: string,
  filters?: GlobalFilterParams,
): Promise<{ data: BrandRow[]; live: boolean; meta: ResponseMeta }> {
  interface ApiResp {
    brands: BrandRow[];
    date_from?: string | null; date_to?: string | null;
    compare_date_from?: string | null; compare_date_to?: string | null;
    refreshed_at?: string | null;
  }
  const marketplace = serializeMarketplaceSelection(selection);
  const qs = buildFilterQuery(marketplace, period, filters);
  return withCache(`brands:${qs.toString()}`, async () => {
    const raw = await apiFetch<ApiResp>(
      `/api/v1/performance/brands?${qs.toString()}`
    );
    if (raw) return { live: true, meta: metaFromResponse(raw), data: raw.brands };
    return { live: false, meta: EMPTY_META, data: brandsFromMock(selection) };
  });
}

export function fetchMonthly(
  selection: MarketplaceSelection = DEFAULT_MARKETPLACE_SELECTION,
): Promise<{ data: MonthPoint[]; live: boolean }> {
  interface ApiResp { data: MonthPoint[] }
  const marketplace = serializeMarketplaceSelection(selection);
  return withCache(`monthly:${marketplace}`, async () => {
    const raw = await apiFetch<ApiResp>(`/api/v1/performance/monthly?months_back=6&marketplace=${marketplace}`);
    if (raw) return { live: true, data: raw.data };
    return { live: false, data: monthlyFromMock() };
  });
}

// ---------- Tendencia (respeita canal, marca e periodo — usado no Gerencial) ----------

export interface TrendPoint {
  date: string;
  label: string;
  gmv: number;
  orders: number;
}

export function fetchTrend(
  selection: MarketplaceSelection,
  filters?: GlobalFilterParams,
): Promise<{ granularity: "day" | "month"; data: TrendPoint[]; live: boolean; meta: ResponseMeta }> {
  interface ApiResp {
    granularity: "day" | "month"; data: TrendPoint[];
    date_from?: string | null; date_to?: string | null;
    refreshed_at?: string | null;
  }
  const marketplace = serializeMarketplaceSelection(selection);
  const qs = buildFilterQuery(marketplace, undefined, filters);
  return withCache(`trend:${qs.toString()}`, async () => {
    const raw = await apiFetch<ApiResp>(`/api/v1/performance/trend?${qs.toString()}`);
    if (raw) return { live: true, meta: metaFromResponse(raw), granularity: raw.granularity, data: raw.data };
    return { live: false, meta: EMPTY_META, granularity: "day", data: [] };
  });
}

export interface ProdutoMLRow {
  brand: string;
  item_id: string;
  seller_sku: string | null;
  title: string;
  gross_revenue: number;
  units_sold: number;
  unique_buyers: number | null;
  avg_price: number | null;
  cancel_rate_pct: number | null;
  pareto_bucket: string | null;
  revenue_velocity: string | null;
  ad_roas: number | null;
  ad_acos_pct: number | null;
  ad_spend: number | null;
  ad_efficiency: string | null;
  action_signal: string | null;
  estimated_margin: number | null;
  revenue_share_pct: number | null;
  product_status: string | null;
}

export interface ProdutosMLResponse {
  total: number;
  limit: number;
  offset: number;
  items: ProdutoMLRow[];
  scope: string;
  refreshed_at: string | null;
}

export interface ProdutoTikTokRow {
  brand: string;
  product_id: string;
  product_name: string;
  gmv: number;
  orders: number;
  items_sold: number;
  pct_gmv_video: number | null;
  pct_gmv_live: number | null;
  pct_gmv_card: number | null;
  problem_rate: number | null;
  rating_avg: number | null;
  total_ratings: number | null;
  pareto_bucket: string | null;
}

export interface ProdutosTikTokResponse {
  ref_month: string;
  total: number;
  limit: number;
  offset: number;
  items: ProdutoTikTokRow[];
}

export interface ProdutoMLListParams {
  brand?: string;
  pareto_bucket?: string;
  action_signal?: string;
  product_status?: string;
  revenue_velocity?: string;
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

export function fetchProdutosML(params: ProdutoMLListParams): Promise<ProdutosMLResponse | null> {
  const qs = new URLSearchParams();
  if (params.brand) qs.set("brand", params.brand);
  if (params.pareto_bucket) qs.set("pareto_bucket", params.pareto_bucket);
  if (params.action_signal) qs.set("action_signal", params.action_signal);
  if (params.product_status) qs.set("product_status", params.product_status);
  if (params.revenue_velocity) qs.set("revenue_velocity", params.revenue_velocity);
  qs.set("limit", String(params.limit ?? 25));
  qs.set("offset", String(params.offset ?? 0));
  if (params.sort_by) qs.set("sort_by", params.sort_by);
  if (params.sort_dir) qs.set("sort_dir", params.sort_dir);
  return withCache(`produtos-ml:${qs}`, () =>
    apiFetch<ProdutosMLResponse>(`/api/v1/performance/produtos/ml?${qs}`)
  );
}

export interface ProdutoShopeeRow {
  brand: string;
  sku_ref: string | null;
  product_name: string;
  variation_name: string | null;
  gmv: number;
  units_sold: number;
  orders: number;
  canceled_orders: number;
  cancel_rate_pct: number | null;
  // Calculado pelo proprio ETL — a API nunca soma/consolida entre linhas
  // (cada produto Shopee e exatamente 1 linha do mart, chave estrita).
  unique_buyers: number | null;
  avg_price: number | null;
  pareto_bucket: string | null;
}

export interface ProdutosShopeeListResponse {
  ref_month: string;
  total: number;
  limit: number;
  offset: number;
  items: ProdutoShopeeRow[];
}

export interface ProdutoChannelListParams {
  brand?: string;
  period?: string;
  pareto_bucket?: string;
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

export function fetchProdutosShopee(params: ProdutoChannelListParams): Promise<ProdutosShopeeListResponse | null> {
  const qs = new URLSearchParams();
  if (params.brand) qs.set("brand", params.brand);
  qs.set("ref_month", params.period ?? refMonth());
  if (params.pareto_bucket) qs.set("pareto_bucket", params.pareto_bucket);
  qs.set("limit", String(params.limit ?? 25));
  qs.set("offset", String(params.offset ?? 0));
  if (params.sort_by) qs.set("sort_by", params.sort_by);
  if (params.sort_dir) qs.set("sort_dir", params.sort_dir);
  return withCache(`produtos-shopee:${qs}`, () =>
    apiFetch<ProdutosShopeeListResponse>(`/api/v1/performance/produtos/shopee?${qs}`)
  );
}

export function fetchProdutosTikTok(params: ProdutoChannelListParams): Promise<ProdutosTikTokResponse | null> {
  const qs = new URLSearchParams();
  if (params.brand) qs.set("brand", params.brand);
  qs.set("ref_month", params.period ?? refMonth());
  if (params.pareto_bucket) qs.set("pareto_bucket", params.pareto_bucket);
  qs.set("limit", String(params.limit ?? 25));
  qs.set("offset", String(params.offset ?? 0));
  if (params.sort_by) qs.set("sort_by", params.sort_by);
  if (params.sort_dir) qs.set("sort_dir", params.sort_dir);
  return withCache(`produtos-tk:${qs}`, () =>
    apiFetch<ProdutosTikTokResponse>(`/api/v1/performance/produtos/tiktok?${qs}`)
  );
}

export interface ParetoSummaryBucket {
  bucket: string;
  label: string;
  description: string;
  gmv: number;
  count: number;
  gmv_pct: number;
}

export interface ProdutosMLSummary {
  total_gmv: number;
  // total_count inclui produtos com GMV=0 (inativos, ads sem venda etc.);
  // eligible_count e o subconjunto com GMV>0 que entra nos buckets A/B/C/D
  // (soma dos buckets == eligible_count, nunca == total_count).
  total_count: number;
  eligible_count: number;
  excluded_zero_gmv_count: number;
  brand: string | null;
  buckets: ParetoSummaryBucket[];
  scope: string;
  refreshed_at: string | null;
}

export function fetchProdutosMLSummary(params: {
  brand?: string;
  action_signal?: string;
  product_status?: string;
  revenue_velocity?: string;
} = {}): Promise<ProdutosMLSummary | null> {
  const qs = new URLSearchParams();
  if (params.brand) qs.set("brand", params.brand);
  if (params.action_signal) qs.set("action_signal", params.action_signal);
  if (params.product_status) qs.set("product_status", params.product_status);
  if (params.revenue_velocity) qs.set("revenue_velocity", params.revenue_velocity);
  return withCache(`produtos-ml-summary:${qs}`, () =>
    apiFetch<ProdutosMLSummary>(`/api/v1/performance/produtos/ml/summary?${qs}`)
  );
}

export interface ProdutosChannelSummary {
  ref_month: string;
  total_gmv: number;
  total_count: number;
  eligible_count: number;
  excluded_zero_gmv_count: number;
  brand: string | null;
  buckets: ParetoSummaryBucket[];
}

export function fetchProdutosTikTokSummary(params: { brand?: string; period?: string } = {}): Promise<ProdutosChannelSummary | null> {
  const qs = new URLSearchParams();
  if (params.brand) qs.set("brand", params.brand);
  qs.set("ref_month", params.period ?? refMonth());
  return withCache(`produtos-tk-summary:${qs}`, () =>
    apiFetch<ProdutosChannelSummary>(`/api/v1/performance/produtos/tiktok/summary?${qs}`)
  );
}

export function fetchProdutosShopeeSummary(params: { brand?: string; period?: string } = {}): Promise<ProdutosChannelSummary | null> {
  const qs = new URLSearchParams();
  if (params.brand) qs.set("brand", params.brand);
  qs.set("ref_month", params.period ?? refMonth());
  return withCache(`produtos-sh-summary:${qs}`, () =>
    apiFetch<ProdutosChannelSummary>(`/api/v1/performance/produtos/shopee/summary?${qs}`)
  );
}

export interface CanaisKpis {
  tiktok_gmv: number | null;
  tiktok_gmv_video: number | null;
  tiktok_gmv_live: number | null;
  tiktok_gmv_card: number | null;
  tiktok_video_pct: number | null;
  tiktok_live_pct: number | null;
  tiktok_card_pct: number | null;
  tiktok_visitors: number | null;
  tiktok_customers: number | null;
  tiktok_conversion_rate: number | null;
  tiktok_impressions: number | null;
  tiktok_page_views: number | null;
  tiktok_ctr_pct: number | null;
  ml_unique_buyers: number | null;
  ml_new_buyers: number | null;
  ml_repeat_buyers: number | null;
  ml_new_buyer_pct: number | null;
  ml_repeat_buyer_rate_pct: number | null;
  ml_gmv_per_buyer: number | null;
  shopee_gmv: number | null;
  shopee_unique_buyers: number | null;
  shopee_new_buyers: number | null;
  shopee_repeat_buyers: number | null;
  shopee_new_buyer_pct: number | null;
  shopee_repeat_buyer_rate_pct: number | null;
  shopee_gmv_per_buyer: number | null;
  shopee_visitors?: number | null;
  shopee_conversion_rate?: number | null;
}

export interface CanaisBrandRow {
  brand: string;
  label: string;
  tiktok_gmv: number | null;
  tiktok_gmv_video: number | null;
  tiktok_gmv_live: number | null;
  tiktok_gmv_card: number | null;
  tiktok_video_pct: number | null;
  tiktok_live_pct: number | null;
  tiktok_card_pct: number | null;
  tiktok_visitors: number | null;
  tiktok_customers: number | null;
  tiktok_conversion_rate: number | null;
  tiktok_impressions: number | null;
  tiktok_page_views: number | null;
  tiktok_ctr_pct: number | null;
  ml_gmv: number | null;
  ml_unique_buyers: number | null;
  ml_new_buyers: number | null;
  ml_repeat_buyers: number | null;
  ml_repeat_buyer_rate_pct: number | null;
  ml_gmv_per_buyer: number | null;
  ml_new_buyer_pct: number | null;
  shopee_gmv: number | null;
  shopee_unique_buyers: number | null;
  shopee_new_buyers: number | null;
  shopee_repeat_buyers: number | null;
  shopee_repeat_buyer_rate_pct: number | null;
  shopee_gmv_per_buyer: number | null;
  shopee_new_buyer_pct: number | null;
  shopee_cancel_rate_pct?: number | null;
  shopee_visitors?: number | null;
  shopee_conversion_rate?: number | null;
}

// Matriz comparativa marca x canal (Ads/Custo/Frete + sinais) — Gate 2,
// docs/sections/canais_audit.md secao 14. Nunca inclui desconto/afiliados
// (bloqueados no Gate 1 por falta de fonte/semantica confiavel).
export interface CanaisChannelRow {
  brand: string;
  label: string;
  channel: "tiktok" | "ml" | "shopee";
  channel_label: string;
  gmv: number;
  orders: number;
  ad_spend: number | null;
  ad_revenue: number | null;
  ads_gmv_pct: number | null;
  roas: number | null;
  acos_pct: number | null;
  marketplace_cost_pct: number | null;
  seller_shipping_pct: number | null;
  ads_available: boolean;
  marketplace_cost_available: boolean;
  seller_shipping_available: boolean;
  ads_applicable: boolean;
  marketplace_cost_applicable: boolean;
  seller_shipping_applicable: boolean;
  data_warning: string | null;
  signals: string[];
}

export interface CanaisChannelMedian {
  channel: "tiktok" | "ml" | "shopee";
  channel_label: string;
  gmv_median: number | null;
  ads_gmv_pct_median: number | null;
  roas_median: number | null;
  marketplace_cost_pct_median: number | null;
  marketplace_cost_pct_p75: number | null;
  seller_shipping_pct_median: number | null;
  seller_shipping_pct_p75: number | null;
  brands_with_data: number;
}

const CANAIS_MOCK_BRANDS: CanaisBrandRow[] = [
  {
    brand: "barbours", label: "BARBOURS",
    tiktok_gmv: 9_709_787, tiktok_gmv_video: 4_996_333, tiktok_gmv_live: 2_201_169, tiktok_gmv_card: 2_512_285,
    tiktok_video_pct: 51.5, tiktok_live_pct: 22.7, tiktok_card_pct: 25.9,
    tiktok_visitors: 133_606, tiktok_customers: 2_808, tiktok_conversion_rate: 2.1,
    tiktok_impressions: null, tiktok_page_views: null, tiktok_ctr_pct: null,
    ml_gmv: 2_578_072, ml_unique_buyers: 25_541, ml_new_buyers: 23_619, ml_repeat_buyers: 1_011,
    ml_repeat_buyer_rate_pct: 4.0, ml_gmv_per_buyer: 100.94, ml_new_buyer_pct: 92.5,
    shopee_gmv: 612_400, shopee_unique_buyers: 5_820, shopee_new_buyers: 5_238, shopee_repeat_buyers: 408,
    shopee_repeat_buyer_rate_pct: 7.0, shopee_gmv_per_buyer: 105.22, shopee_new_buyer_pct: 90.0,
  },
  {
    brand: "kokeshi", label: "KOKESHI",
    tiktok_gmv: 2_316_329, tiktok_gmv_video: 1_333_654, tiktok_gmv_live: 452_889, tiktok_gmv_card: 529_786,
    tiktok_video_pct: 57.6, tiktok_live_pct: 19.6, tiktok_card_pct: 22.9,
    tiktok_visitors: 61_982, tiktok_customers: 1_663, tiktok_conversion_rate: 2.68,
    tiktok_impressions: null, tiktok_page_views: null, tiktok_ctr_pct: null,
    ml_gmv: 789_601, ml_unique_buyers: 9_987, ml_new_buyers: 9_332, ml_repeat_buyers: 401,
    ml_repeat_buyer_rate_pct: 4.0, ml_gmv_per_buyer: 79.06, ml_new_buyer_pct: 93.4,
    shopee_gmv: 198_700, shopee_unique_buyers: 2_310, shopee_new_buyers: 2_100, shopee_repeat_buyers: 185,
    shopee_repeat_buyer_rate_pct: 8.0, shopee_gmv_per_buyer: 86.02, shopee_new_buyer_pct: 90.9,
  },
  {
    brand: "apice", label: "ÁPICE",
    tiktok_gmv: 876_174, tiktok_gmv_video: 275_685, tiktok_gmv_live: 154_350, tiktok_gmv_card: 446_139,
    tiktok_video_pct: 31.5, tiktok_live_pct: 17.6, tiktok_card_pct: 50.9,
    tiktok_visitors: 14_059, tiktok_customers: 288, tiktok_conversion_rate: 2.05,
    tiktok_impressions: null, tiktok_page_views: null, tiktok_ctr_pct: null,
    ml_gmv: null, ml_unique_buyers: null, ml_new_buyers: null, ml_repeat_buyers: null,
    ml_repeat_buyer_rate_pct: null, ml_gmv_per_buyer: null, ml_new_buyer_pct: null,
    shopee_gmv: null, shopee_unique_buyers: null, shopee_new_buyers: null, shopee_repeat_buyers: null,
    shopee_repeat_buyer_rate_pct: null, shopee_gmv_per_buyer: null, shopee_new_buyer_pct: null,
  },
  {
    brand: "lescent", label: "LESCENT",
    tiktok_gmv: 253_922, tiktok_gmv_video: 115_633, tiktok_gmv_live: 64_436, tiktok_gmv_card: 73_854,
    tiktok_video_pct: 45.5, tiktok_live_pct: 25.4, tiktok_card_pct: 29.1,
    tiktok_visitors: 3_903, tiktok_customers: 127, tiktok_conversion_rate: 3.25,
    tiktok_impressions: null, tiktok_page_views: null, tiktok_ctr_pct: null,
    ml_gmv: 552_643, ml_unique_buyers: 6_999, ml_new_buyers: 6_214, ml_repeat_buyers: 564,
    ml_repeat_buyer_rate_pct: 8.1, ml_gmv_per_buyer: 78.96, ml_new_buyer_pct: 88.8,
    shopee_gmv: 154_200, shopee_unique_buyers: 1_870, shopee_new_buyers: 1_664, shopee_repeat_buyers: 168,
    shopee_repeat_buyer_rate_pct: 9.0, shopee_gmv_per_buyer: 82.46, shopee_new_buyer_pct: 89.0,
  },
  {
    brand: "rituaria", label: "RITUÁRIA",
    tiktok_gmv: 239_773, tiktok_gmv_video: 63_824, tiktok_gmv_live: 20_980, tiktok_gmv_card: 154_969,
    tiktok_video_pct: 26.6, tiktok_live_pct: 8.8, tiktok_card_pct: 64.6,
    tiktok_visitors: 1_628, tiktok_customers: 88, tiktok_conversion_rate: 5.41,
    tiktok_impressions: null, tiktok_page_views: null, tiktok_ctr_pct: null,
    ml_gmv: null, ml_unique_buyers: null, ml_new_buyers: null, ml_repeat_buyers: null,
    ml_repeat_buyer_rate_pct: null, ml_gmv_per_buyer: null, ml_new_buyer_pct: null,
    shopee_gmv: null, shopee_unique_buyers: null, shopee_new_buyers: null, shopee_repeat_buyers: null,
    shopee_repeat_buyer_rate_pct: null, shopee_gmv_per_buyer: null, shopee_new_buyer_pct: null,
  },
];

export function fetchCanais(
  selection: MarketplaceSelection,
  period?: string,
  filters?: GlobalFilterParams,
): Promise<{
  kpis: CanaisKpis; brands: CanaisBrandRow[];
  channelRows: CanaisChannelRow[]; channelMedians: CanaisChannelMedian[];
  live: boolean; meta: ResponseMeta;
}> {
  const marketplace = serializeMarketplaceSelection(selection);
  const qs = buildFilterQuery(marketplace, period, filters);
  return withCache(`canais:${qs.toString()}`, async () => {
    interface ApiResp {
      kpis: CanaisKpis; brands: CanaisBrandRow[];
      channel_rows?: CanaisChannelRow[]; channel_medians?: CanaisChannelMedian[];
      date_from?: string | null; date_to?: string | null;
      compare_date_from?: string | null; compare_date_to?: string | null;
      refreshed_at?: string | null;
    }
    const raw = await apiFetch<ApiResp>(
      `/api/v1/performance/canais?${qs.toString()}`
    );
  if (raw) {
    const brands: CanaisBrandRow[] = raw.brands.map((b) => ({
      ...b,
      ml_new_buyer_pct:
        b.ml_unique_buyers && b.ml_new_buyers
          ? parseFloat(((b.ml_new_buyers / b.ml_unique_buyers) * 100).toFixed(1))
          : null,
    }));
    return {
      live: true, meta: metaFromResponse(raw), kpis: raw.kpis, brands,
      channelRows: raw.channel_rows ?? [],
      channelMedians: raw.channel_medians ?? [],
    };
  }

  const brands = CANAIS_MOCK_BRANDS;

  const tkBrands = brands.filter((b) => b.tiktok_gmv !== null);
  const mlBrands = brands.filter((b) => b.ml_gmv !== null);
  const shBrands = CANAIS_MOCK_BRANDS.filter((b) => b.shopee_gmv !== null);
  const tkGmv = tkBrands.reduce((s, b) => s + (b.tiktok_gmv ?? 0), 0);
  const tkVid = tkBrands.reduce((s, b) => s + (b.tiktok_gmv_video ?? 0), 0);
  const tkLive = tkBrands.reduce((s, b) => s + (b.tiktok_gmv_live ?? 0), 0);
  const tkCard = tkBrands.reduce((s, b) => s + (b.tiktok_gmv_card ?? 0), 0);
  const tkVisitors = tkBrands.reduce((s, b) => s + (b.tiktok_visitors ?? 0), 0);
  const tkCustomers = tkBrands.reduce((s, b) => s + (b.tiktok_customers ?? 0), 0);
  const mlBuyers = mlBrands.reduce((s, b) => s + (b.ml_unique_buyers ?? 0), 0);
  const mlNew = mlBrands.reduce((s, b) => s + (b.ml_new_buyers ?? 0), 0);
  const mlRepeat = mlBrands.reduce((s, b) => s + (b.ml_repeat_buyers ?? 0), 0);
  const mlGmv = mlBrands.reduce((s, b) => s + (b.ml_gmv ?? 0), 0);
  const shBuyers = shBrands.reduce((s, b) => s + (b.shopee_unique_buyers ?? 0), 0);
  const shNew = shBrands.reduce((s, b) => s + (b.shopee_new_buyers ?? 0), 0);
  const shRepeat = shBrands.reduce((s, b) => s + (b.shopee_repeat_buyers ?? 0), 0);
  const shGmv = shBrands.reduce((s, b) => s + (b.shopee_gmv ?? 0), 0);

  const showTk = isMarketplaceSelected(selection, "tiktok");
  const showMl = isMarketplaceSelected(selection, "ml");
  const showSh = isMarketplaceSelected(selection, "shopee");

  const kpis: CanaisKpis = {
    tiktok_gmv: showTk ? tkGmv : null,
    tiktok_gmv_video: showTk ? tkVid : null,
    tiktok_gmv_live: showTk ? tkLive : null,
    tiktok_gmv_card: showTk ? tkCard : null,
    tiktok_video_pct: showTk ? parseFloat((tkVid / tkGmv * 100).toFixed(1)) : null,
    tiktok_live_pct: showTk ? parseFloat((tkLive / tkGmv * 100).toFixed(1)) : null,
    tiktok_card_pct: showTk ? parseFloat((tkCard / tkGmv * 100).toFixed(1)) : null,
    tiktok_visitors: showTk ? tkVisitors : null,
    tiktok_customers: showTk ? tkCustomers : null,
    tiktok_conversion_rate: (showTk && tkVisitors > 0) ? parseFloat((tkCustomers / tkVisitors * 100).toFixed(1)) : null,
    tiktok_impressions: null,
    tiktok_page_views: null,
    tiktok_ctr_pct: null,
    ml_unique_buyers: showMl ? mlBuyers : null,
    ml_new_buyers: showMl ? mlNew : null,
    ml_repeat_buyers: showMl ? mlRepeat : null,
    ml_new_buyer_pct: (showMl && mlBuyers > 0) ? parseFloat((mlNew / mlBuyers * 100).toFixed(1)) : null,
    ml_repeat_buyer_rate_pct: (showMl && mlBuyers > 0) ? parseFloat((mlRepeat / mlBuyers * 100).toFixed(1)) : null,
    ml_gmv_per_buyer: (showMl && mlBuyers > 0) ? parseFloat((mlGmv / mlBuyers).toFixed(2)) : null,
    shopee_gmv: showSh ? shGmv : null,
    shopee_unique_buyers: showSh ? shBuyers : null,
    shopee_new_buyers: showSh ? shNew : null,
    shopee_repeat_buyers: showSh ? shRepeat : null,
    shopee_new_buyer_pct: (showSh && shBuyers > 0) ? parseFloat((shNew / shBuyers * 100).toFixed(1)) : null,
    shopee_repeat_buyer_rate_pct: (showSh && shBuyers > 0) ? parseFloat((shRepeat / shBuyers * 100).toFixed(1)) : null,
    shopee_gmv_per_buyer: (showSh && shBuyers > 0) ? parseFloat((shGmv / shBuyers).toFixed(2)) : null,
  };

    // Modo demonstracao (API offline): dados mock nao modelam Ads/Custo/Frete
    // por canal — a matriz comparativa fica vazia em vez de inventar valores.
    return { live: false, meta: EMPTY_META, kpis, brands, channelRows: [], channelMedians: [] };
  });
}

export interface FinanceiroKpis {
  tiktok_gmv: number | null;
  tiktok_settlement: number | null;
  tiktok_fees: number | null;
  tiktok_avg_fee_pct: number | null;
  tiktok_avg_settlement_pct: number | null;
  ml_gmv: number | null;
  ml_ad_spend: number | null;
  ml_ad_revenue: number | null;
  ml_roas: number | null;
  ml_acos_pct: number | null;
  ml_cpc: number | null;
  ml_total_cost_pct: number | null;
  shopee_gmv?: number | null;
  shopee_settlement?: number | null;
  shopee_fees?: number | null;
  shopee_avg_fee_pct?: number | null;
  shopee_avg_settlement_pct?: number | null;
  shopee_ad_spend?: number | null;
  shopee_roas?: number | null;
}

export interface FinanceiroBrandRow {
  brand: string;
  label: string;
  tiktok_gmv: number | null;
  tiktok_settlement: number | null;
  tiktok_fees: number | null;
  tiktok_avg_fee_pct: number | null;
  tiktok_avg_settlement_pct: number | null;
  ml_gmv: number | null;
  ml_ad_spend: number | null;
  ml_ad_revenue: number | null;
  ml_roas: number | null;
  ml_acos_pct: number | null;
  ml_cpc: number | null;
  ml_ctr_pct: number | null;
  ml_ad_clicks: number | null;
  ml_ad_impressions: number | null;
  ml_seller_shipping_cost: number | null;
  ml_shipping_pct_of_gmv: number | null;
  ml_total_cost_pct: number | null;
  shopee_gmv?: number | null;
  shopee_settlement?: number | null;
  shopee_fees?: number | null;
  shopee_avg_fee_pct?: number | null;
  shopee_avg_settlement_pct?: number | null;
  shopee_ad_spend?: number | null;
  shopee_ad_revenue?: number | null;
  shopee_roas?: number | null;
  shopee_shipping_cost?: number | null;
  shopee_shipping_pct_of_gmv?: number | null;
}

// Mock calibrado com proporções reais de mai/2026: taxa TikTok ~25-31%, ROAS ML 12-15x, frete ML 11-14%
const FINANCEIRO_MOCK_BRANDS: FinanceiroBrandRow[] = [
  { brand: "barbours", label: "BARBOURS", tiktok_gmv: 9_710_000, tiktok_fees: 2_944_000, tiktok_settlement: 7_151_000, tiktok_avg_fee_pct: 30.3, tiktok_avg_settlement_pct: 73.6, ml_gmv: 2_578_000, ml_ad_spend: 120_000, ml_ad_revenue: 1_451_000, ml_roas: 12.1, ml_acos_pct: 8.3, ml_cpc: 0.70, ml_ctr_pct: 0.28, ml_ad_clicks: 171_000, ml_ad_impressions: 61_000_000, ml_seller_shipping_cost: 307_000, ml_shipping_pct_of_gmv: 11.9, ml_total_cost_pct: 16.5 },
  { brand: "kokeshi", label: "KOKESHI", tiktok_gmv: 2_316_000, tiktok_fees: 708_000, tiktok_settlement: 1_588_000, tiktok_avg_fee_pct: 30.6, tiktok_avg_settlement_pct: 68.6, ml_gmv: 789_000, ml_ad_spend: 34_000, ml_ad_revenue: 469_000, ml_roas: 13.7, ml_acos_pct: 7.3, ml_cpc: 0.47, ml_ctr_pct: 0.32, ml_ad_clicks: 72_000, ml_ad_impressions: 22_500_000, ml_seller_shipping_cost: 85_000, ml_shipping_pct_of_gmv: 10.7, ml_total_cost_pct: 15.1 },
  { brand: "apice", label: "ÁPICE", tiktok_gmv: 876_000, tiktok_fees: 223_000, tiktok_settlement: 629_000, tiktok_avg_fee_pct: 25.5, tiktok_avg_settlement_pct: 71.8, ml_gmv: null, ml_ad_spend: null, ml_ad_revenue: null, ml_roas: null, ml_acos_pct: null, ml_cpc: null, ml_ctr_pct: null, ml_ad_clicks: null, ml_ad_impressions: null, ml_seller_shipping_cost: null, ml_shipping_pct_of_gmv: null, ml_total_cost_pct: null },
  { brand: "lescent", label: "LESCENT", tiktok_gmv: 254_000, tiktok_fees: 74_000, tiktok_settlement: 168_000, tiktok_avg_fee_pct: 29.1, tiktok_avg_settlement_pct: 66.2, ml_gmv: 553_000, ml_ad_spend: 23_000, ml_ad_revenue: 338_000, ml_roas: 14.6, ml_acos_pct: 6.9, ml_cpc: 0.40, ml_ctr_pct: 0.25, ml_ad_clicks: 57_500, ml_ad_impressions: 23_000_000, ml_seller_shipping_cost: 76_000, ml_shipping_pct_of_gmv: 13.7, ml_total_cost_pct: 17.9 },
  { brand: "rituaria", label: "RITUÁRIA", tiktok_gmv: 240_000, tiktok_fees: 61_000, tiktok_settlement: 185_000, tiktok_avg_fee_pct: 25.6, tiktok_avg_settlement_pct: 77.0, ml_gmv: null, ml_ad_spend: null, ml_ad_revenue: null, ml_roas: null, ml_acos_pct: null, ml_cpc: null, ml_ctr_pct: null, ml_ad_clicks: null, ml_ad_impressions: null, ml_seller_shipping_cost: null, ml_shipping_pct_of_gmv: null, ml_total_cost_pct: null },
];

export function fetchFinanceiro(
  selection: MarketplaceSelection,
  period?: string,
  filters?: GlobalFilterParams,
): Promise<{ kpis: FinanceiroKpis; brands: FinanceiroBrandRow[]; live: boolean; meta: ResponseMeta }> {
  interface ApiResp {
    kpis: FinanceiroKpis; brands: FinanceiroBrandRow[];
    date_from?: string | null; date_to?: string | null;
    compare_date_from?: string | null; compare_date_to?: string | null;
    refreshed_at?: string | null;
  }
  const marketplace = serializeMarketplaceSelection(selection);
  const qs = buildFilterQuery(marketplace, period, filters);
  return withCache(`financeiro:${qs.toString()}`, async () => {
  const raw = await apiFetch<ApiResp>(
    `/api/v1/performance/financeiro?${qs.toString()}`
  );
  if (raw) return { live: true, meta: metaFromResponse(raw), kpis: raw.kpis, brands: raw.brands };

  const showTk = isMarketplaceSelected(selection, "tiktok");
  const showMl = isMarketplaceSelected(selection, "ml");
  const brands = FINANCEIRO_MOCK_BRANDS;

  const allTkGmv = FINANCEIRO_MOCK_BRANDS.reduce((s, b) => s + (b.tiktok_gmv ?? 0), 0);
  const allTkFees = FINANCEIRO_MOCK_BRANDS.reduce((s, b) => s + (b.tiktok_fees ?? 0), 0);
  const allTkSettlement = FINANCEIRO_MOCK_BRANDS.reduce((s, b) => s + (b.tiktok_settlement ?? 0), 0);
  const mlBrands = FINANCEIRO_MOCK_BRANDS.filter((b) => b.ml_ad_spend !== null);
  const allMlGmv = mlBrands.reduce((s, b) => s + (b.ml_gmv ?? 0), 0);
  const allMlSpend = mlBrands.reduce((s, b) => s + (b.ml_ad_spend ?? 0), 0);
  const allMlRevenue = mlBrands.reduce((s, b) => s + (b.ml_ad_revenue ?? 0), 0);
  const allMlClicks = mlBrands.reduce((s, b) => s + (b.ml_ad_clicks ?? 0), 0);
  const allMlShipping = mlBrands.reduce((s, b) => s + (b.ml_seller_shipping_cost ?? 0), 0);

  const kpis: FinanceiroKpis = {
    tiktok_gmv: showTk ? allTkGmv : null,
    tiktok_settlement: showTk ? allTkSettlement : null,
    tiktok_fees: showTk ? allTkFees : null,
    tiktok_avg_fee_pct: showTk ? parseFloat((allTkFees / allTkGmv * 100).toFixed(2)) : null,
    tiktok_avg_settlement_pct: showTk ? parseFloat((allTkSettlement / allTkGmv * 100).toFixed(2)) : null,
    ml_gmv: showMl ? allMlGmv : null,
    ml_ad_spend: showMl ? allMlSpend : null,
    ml_ad_revenue: showMl ? allMlRevenue : null,
    ml_roas: showMl ? parseFloat((allMlRevenue / allMlSpend).toFixed(2)) : null,
    ml_acos_pct: showMl ? parseFloat((allMlSpend / allMlRevenue * 100).toFixed(2)) : null,
    ml_cpc: showMl ? parseFloat((allMlSpend / allMlClicks).toFixed(4)) : null,
    ml_total_cost_pct: showMl ? parseFloat(((allMlSpend + allMlShipping) / allMlGmv * 100).toFixed(2)) : null,
  };

  return { live: false, meta: EMPTY_META, kpis, brands };
  });
}

export interface QualityKpis {
  tiktok_problem_rate: number | null;
  tiktok_cancel_rate: number | null;
  tiktok_avg_delivery_days: number | null;
  ml_cancel_rate_pct: number | null;
  ml_not_delivered_rate_pct: number | null;
  ml_avg_delivery_days: number | null;
  shopee_cancel_rate_pct?: number | null;
  shopee_return_rate_pct?: number | null;
}

export interface QualityBrandRow {
  brand: string;
  label: string;
  tiktok_orders: number | null;
  tiktok_canceled: number | null;
  tiktok_refunded: number | null;
  tiktok_returned: number | null;
  tiktok_problem_rate: number | null;
  tiktok_cancel_rate: number | null;
  tiktok_avg_delivery_days: number | null;
  ml_cancel_rate_pct: number | null;
  ml_not_delivered_rate_pct: number | null;
  ml_cancelled_orders: number | null;
  ml_total_orders: number | null;
  ml_not_delivered_shipments: number | null;
  ml_avg_delivery_days: number | null;
  ml_repeat_buyer_rate_pct: number | null;
  ml_gmv_per_buyer: number | null;
  ml_gmv_mom_pct: number | null;
  ml_new_buyers: number | null;
  ml_unique_buyers: number | null;
  ml_shipping_pct_of_gmv: number | null;
  shopee_orders?: number | null;
  shopee_canceled_orders?: number | null;
  shopee_returned_orders?: number | null;
  shopee_cancel_rate_pct?: number | null;
  shopee_return_rate_pct?: number | null;
}

const QUALITY_MOCK_BRANDS: QualityBrandRow[] = [
  { brand: "barbours", label: "BARBOURS", tiktok_orders: 3_380, tiktok_canceled: 207, tiktok_refunded: 78, tiktok_returned: 29, tiktok_problem_rate: 8.6, tiktok_cancel_rate: 5.8, tiktok_avg_delivery_days: 5.2, ml_cancel_rate_pct: 3.1, ml_not_delivered_rate_pct: 0.8, ml_cancelled_orders: 68, ml_total_orders: 2_194, ml_not_delivered_shipments: 17, ml_avg_delivery_days: 3.9, ml_repeat_buyer_rate_pct: 8.2, ml_gmv_per_buyer: 180.50, ml_gmv_mom_pct: 12.3, ml_new_buyers: 1_984, ml_unique_buyers: 2_162, ml_shipping_pct_of_gmv: 10.5 },
  { brand: "kokeshi", label: "KOKESHI", tiktok_orders: 3_626, tiktok_canceled: 279, tiktok_refunded: 94, tiktok_returned: 34, tiktok_problem_rate: 10.6, tiktok_cancel_rate: 7.1, tiktok_avg_delivery_days: 5.6, ml_cancel_rate_pct: 2.5, ml_not_delivered_rate_pct: 1.1, ml_cancelled_orders: 44, ml_total_orders: 1_760, ml_not_delivered_shipments: 19, ml_avg_delivery_days: 3.5, ml_repeat_buyer_rate_pct: 7.5, ml_gmv_per_buyer: 155.20, ml_gmv_mom_pct: -3.2, ml_new_buyers: 1_620, ml_unique_buyers: 1_752, ml_shipping_pct_of_gmv: 11.8 },
  { brand: "apice", label: "ÁPICE", tiktok_orders: 3_120, tiktok_canceled: 171, tiktok_refunded: 52, tiktok_returned: 19, tiktok_problem_rate: 7.3, tiktok_cancel_rate: 5.2, tiktok_avg_delivery_days: 5.8, ml_cancel_rate_pct: null, ml_not_delivered_rate_pct: null, ml_cancelled_orders: null, ml_total_orders: null, ml_not_delivered_shipments: null, ml_avg_delivery_days: null, ml_repeat_buyer_rate_pct: null, ml_gmv_per_buyer: null, ml_gmv_mom_pct: null, ml_new_buyers: null, ml_unique_buyers: null, ml_shipping_pct_of_gmv: null },
  { brand: "lescent", label: "LESCENT", tiktok_orders: 3_360, tiktok_canceled: 228, tiktok_refunded: 83, tiktok_returned: 31, tiktok_problem_rate: 9.8, tiktok_cancel_rate: 6.4, tiktok_avg_delivery_days: 6.2, ml_cancel_rate_pct: null, ml_not_delivered_rate_pct: null, ml_cancelled_orders: null, ml_total_orders: null, ml_not_delivered_shipments: null, ml_avg_delivery_days: null, ml_repeat_buyer_rate_pct: null, ml_gmv_per_buyer: null, ml_gmv_mom_pct: null, ml_new_buyers: null, ml_unique_buyers: null, ml_shipping_pct_of_gmv: null },
  { brand: "rituaria", label: "RITUÁRIA", tiktok_orders: 2_490, tiktok_canceled: 131, tiktok_refunded: 47, tiktok_returned: 16, tiktok_problem_rate: 7.5, tiktok_cancel_rate: 5.0, tiktok_avg_delivery_days: 5.4, ml_cancel_rate_pct: null, ml_not_delivered_rate_pct: null, ml_cancelled_orders: null, ml_total_orders: null, ml_not_delivered_shipments: null, ml_avg_delivery_days: null, ml_repeat_buyer_rate_pct: null, ml_gmv_per_buyer: null, ml_gmv_mom_pct: null, ml_new_buyers: null, ml_unique_buyers: null, ml_shipping_pct_of_gmv: null },
];

export function fetchQuality(
  selection: MarketplaceSelection,
  period?: string,
  filters?: GlobalFilterParams,
): Promise<{ kpis: QualityKpis; brands: QualityBrandRow[]; live: boolean; meta: ResponseMeta }> {
  interface ApiResp {
    kpis: QualityKpis; brands: QualityBrandRow[];
    date_from?: string | null; date_to?: string | null;
    compare_date_from?: string | null; compare_date_to?: string | null;
    refreshed_at?: string | null;
  }
  const marketplace = serializeMarketplaceSelection(selection);
  const qs = buildFilterQuery(marketplace, period, filters);
  return withCache(`quality:${qs.toString()}`, async () => {
    const raw = await apiFetch<ApiResp>(
      `/api/v1/performance/quality?${qs.toString()}`
    );
    if (raw) return { live: true, meta: metaFromResponse(raw), kpis: raw.kpis, brands: raw.brands };

    const showTk = isMarketplaceSelected(selection, "tiktok");
    const showMl = isMarketplaceSelected(selection, "ml");
    const brands = QUALITY_MOCK_BRANDS;

    const kpis: QualityKpis = {
      tiktok_problem_rate: showTk ? 9.0 : null,
      tiktok_cancel_rate: showTk ? 5.9 : null,
      tiktok_avg_delivery_days: showTk ? 5.6 : null,
      ml_cancel_rate_pct: showMl ? 2.8 : null,
      ml_not_delivered_rate_pct: showMl ? 0.9 : null,
      ml_avg_delivery_days: showMl ? 3.7 : null,
    };

    return { live: false, meta: EMPTY_META, kpis, brands };
  });
}

// ---------- Tempo Real ----------

export interface TempoRealHour {
  hour: number;
  gmv_hour: number;
  gmv_acumulado: number;
  gmv_hour_prior: number | null;
  gmv_acumulado_prior: number | null;
  gmv_avg7d: number | null;
  customers_hour: number;
  customers_acumulado: number;
  conversion_hour: number | null;
  ticket_medio: number | null;
}

export interface TempoRealBrand {
  brand: string;
  label: string;
  gmv_hoje: number;
  gmv_ontem: number | null;
  delta_pct: number | null;
  ritmo_projetado: number | null;
  clientes_hoje: number;
  ultima_hora: number;
  conversion_hora: number | null;
  ticket_medio: number | null;
  hours: TempoRealHour[];
}

export interface TempoRealData {
  total_gmv_hoje: number;
  total_gmv_ontem: number | null;
  total_delta_pct: number | null;
  total_ritmo_projetado: number | null;
  brands: TempoRealBrand[];
}

export async function fetchTempoReal(): Promise<{ data: TempoRealData; live: boolean } | null> {
  const raw = await apiFetch<TempoRealData>("/api/v1/performance/tempo-real");
  if (raw) return { live: true, data: raw };
  return null;
}

// ---------- Brand Detail ----------

export interface BrandDetailDayRow {
  date: string;
  gmv: number | null;
  gmv_video: number | null;
  gmv_live: number | null;
  gmv_card: number | null;
  new_videos_posted: number | null;
}

export interface BrandDetailCreator {
  creator: string;
  gmv: number;
  videos: number;
  lives: number;
}

export interface BrandDetailProduto {
  product_id: string;
  product_name: string;
  gmv: number;
  orders: number;
  videos: number;
  gpm: number | null;
}

export interface BrandDetailChannelRow {
  channel: string;
  label: string;
  impressions: number;
  page_views: number;
  items_sold: number;
  gmv: number;
  ctr_pct: number | null;
  cvr_pct: number | null;
}

export interface BrandDetail {
  brand: string;
  label: string;
  ref_month: string;
  gmv: number;
  orders: number;
  customers: number;
  cvr_pct: number | null;
  cos_pct: number | null;
  pct_video: number | null;
  pct_live: number | null;
  pct_card: number | null;
  active_videos: number;
  new_videos_posted: number;
  active_video_creators: number;
  total_views: number;
  total_lives: number;
  live_creators: number;
  gpm: number | null;
  gmv_per_video: number | null;
  gmv_per_creator: number | null;
  gmv_per_live: number | null;
  videos_per_creator: number | null;
  fresh_videos: number;
  evergreen_videos: number;
  gmv_fresh: number;
  gmv_evergreen: number;
  pct_gmv_fresh: number | null;
  viewers_pct_female: number | null;
  viewers_pct_male: number | null;
  viewers_pct_18_24: number | null;
  viewers_pct_25_34: number | null;
  viewers_pct_35_44: number | null;
  viewers_pct_45_54: number | null;
  viewers_pct_55_plus: number | null;
  followers_pct_female: number | null;
  followers_pct_male: number | null;
  followers_pct_18_24: number | null;
  followers_pct_25_34: number | null;
  followers_pct_35_44: number | null;
  followers_pct_45_54: number | null;
  followers_pct_55_plus: number | null;
  channel_funnel: BrandDetailChannelRow[];
  daily: BrandDetailDayRow[];
  top_creators: BrandDetailCreator[];
  top_produtos: BrandDetailProduto[];
}

export function fetchBrandDetail(
  brand: string,
  period?: string,
): Promise<BrandDetail | null> {
  const month = period ?? refMonth();
  return withCache(`brand-detail:${brand}:${month}`, () =>
    apiFetch<BrandDetail>(
      `/api/v1/performance/brand-detail?brand=${brand}&ref_month=${month}`
    )
  );
}

// ---------- Pedidos ----------

export interface PedidosKpis {
  total_orders: number;
  total_gmv: number;
  avg_ticket: number;
  cancel_rate_pct: number | null;
}

export interface PedidosCanalKpis {
  orders: number;
  canceled: number;
  gmv: number;
  cancel_rate_pct: number | null;
  delivered: number | null;
}

export interface PedidosDailyRow {
  date: string;
  tiktok_orders: number;
  tiktok_canceled: number;
  ml_orders: number;
  ml_canceled: number;
  total_orders: number;
  total_gmv: number;
}

export interface PedidosBrandRow {
  brand: string;
  label: string;
  tiktok_orders: number | null;
  tiktok_canceled: number | null;
  tiktok_cancel_rate_pct: number | null;
  tiktok_gmv: number | null;
  ml_orders: number | null;
  ml_canceled: number | null;
  ml_cancel_rate_pct: number | null;
  ml_gmv: number | null;
  total_orders: number;
  total_gmv: number;
}

export interface PedidosData {
  days_back: number;
  kpis: PedidosKpis;
  tiktok: PedidosCanalKpis;
  ml: PedidosCanalKpis;
  daily: PedidosDailyRow[];
  by_brand: PedidosBrandRow[];
  date_from?: string | null;
  date_to?: string | null;
  refreshed_at?: string | null;
}

export function fetchPedidos(
  selection: MarketplaceSelection = DEFAULT_MARKETPLACE_SELECTION,
  filters?: GlobalFilterParams,
  daysBack = 30,
): Promise<PedidosData | null> {
  const marketplace = serializeMarketplaceSelection(selection);
  const qs = new URLSearchParams();
  qs.set("channels", marketplace);
  if (filters?.brands && filters.brands.length > 0) qs.set("brands", [...filters.brands].sort().join(","));
  if (filters?.dateFrom && filters?.dateTo) {
    qs.set("date_from", filters.dateFrom);
    qs.set("date_to", filters.dateTo);
  } else {
    qs.set("days_back", String(daysBack));
  }
  return withCache(`pedidos:${qs.toString()}`, () =>
    apiFetch<PedidosData>(`/api/v1/performance/pedidos?${qs.toString()}`)
  );
}

// ---------- Inteligencia ----------

export interface SignalRow {
  product_status: string;
  n_products: number;
  gmv: number;
  ad_spend: number;
  avg_roas: number | null;
}

export interface ProductSignalRow {
  brand: string;
  title: string;
  pareto_bucket: string | null;
  revenue_velocity: string | null;
  gmv: number;
  ad_spend: number;
  ad_roas: number | null;
  ad_acos_pct: number | null;
  cancel_rate_pct: number | null;
  revenue_share_pct: number | null;
  units_sold: number | null;
  days_advertised: number | null;
  ad_efficiency: string | null;
}

export interface ParetoRow {
  brand: string;
  pareto_bucket: string;
  n_products: number;
  gmv: number;
  ad_spend: number;
}

export interface LtvRow {
  brand: string;
  total_buyers: number;
  repeat_buyers: number;
  repeat_rate_pct: number | null;
  avg_customer_ltv: number | null;
  vip_buyers: number | null;
  one_and_done_buyers: number | null;
  at_risk_or_churned: number | null;
  overall_roas: number | null;
}

export interface TkProductRow {
  brand: string;
  product_name: string;
  gmv: number;
  orders: number;
  avg_pct_video: number | null;
  avg_pct_live: number | null;
  avg_pct_card: number | null;
  avg_rating: number | null;
}

export interface InteligenciaData {
  signals: SignalRow[];
  urgent: ProductSignalRow[];
  scale: ProductSignalRow[];
  organic: ProductSignalRow[];
  pareto: ParetoRow[];
  ltv: LtvRow[];
  tk_products: TkProductRow[];
}

export function fetchInteligencia(): Promise<{ data: InteligenciaData | null; live: boolean }> {
  return withCache("inteligencia", async () => {
    const raw = await apiFetch<InteligenciaData>("/api/v1/performance/inteligencia");
    return { data: raw, live: raw != null };
  });
}

// ---------- Operacoes ----------

export interface AlertaRow {
  tipo: string;
  severidade: string;
  brand: string;
  mensagem: string;
  ad_spend?: number;
  gmv?: number;
  roas?: number;
}

export interface MlVelocityRow {
  brand: string;
  ad_spend_7d: number;
  gmv_7d: number;
  orders_7d: number;
  roas_7d: number | null;
}

export interface CreatorRow {
  brand: string;
  creator: string;
  gmv: number;
  views: number;
  videos: number;
  lives: number;
  gmv_video: number;
  gmv_live: number;
  gpm_video: number | null;
}

export interface LiveRow {
  brand: string;
  days_with_lives: number;
  total_lives: number;
  total_minutes: number;
  live_gmv: number;
  total_gmv: number;
  pct_live: number | null;
  gmv_per_live: number | null;
  gmv_per_minute: number | null;
}

export interface TkDailyRow {
  brand: string;
  ref_date: string;
  gmv: number;
  orders: number;
}

export interface OperacoesData {
  alertas: AlertaRow[];
  ml_velocity: MlVelocityRow[];
  creators: CreatorRow[];
  lives: LiveRow[];
  tk_daily: TkDailyRow[];
}

export function fetchOperacoes(): Promise<{ data: OperacoesData | null; live: boolean }> {
  return withCache("operacoes", async () => {
    const raw = await apiFetch<OperacoesData>("/api/v1/performance/operacoes");
    return { data: raw, live: raw != null };
  });
}

// ---------- Regioes (Gate 6D.1) ----------
// Sem dataset mock — ao contrario das demais telas, nao existe fallback de
// demonstracao aqui: inventar numeros de cobertura/GMV por UF plausiveis
// seria mais arriscado que simplesmente mostrar "API indisponivel" (mesmo
// padrao ja usado por fetchPedidos/fetchBrandDetail/fetchTempoReal, que
// tambem retornam T | null direto, sem wrapper live/mock).

export type CoverageLevel = "ok" | "partial" | "low" | "not_applicable";

export interface RegioesSummaryData {
  gmv: number;
  orders: number;
  units_sold: number;
  ufs_com_venda: number;
  uf_known_orders: number;
  uf_eligible_orders: number;
  uf_fill_pct: number | null;
  shipping_cost_covered_orders: number;
  shipping_cost_eligible_orders: number;
  shipping_cost_coverage_pct: number | null;
  seller_shipping_cost: number | null;
  coverage_level: CoverageLevel;
  coverage_warning: boolean;
  date_from: string;
  date_to: string;
  refreshed_at: string | null;
  channels_sem_cobertura_regional: string[];
}

export interface RegiaoUfRow {
  uf: string;
  gmv: number;
  orders: number;
  units_sold: number;
  canceled_orders: number;
  returned_orders: number;
  seller_shipping_cost: number | null;
  uf_known_orders: number;
  uf_eligible_orders: number;
  shipping_cost_covered_orders: number;
  shipping_cost_eligible_orders: number;
  uf_fill_pct: number | null;
  shipping_cost_coverage_pct: number | null;
  coverage_level: CoverageLevel;
  coverage_warning: boolean;
}

export interface RegioesByUfData {
  data: RegiaoUfRow[];
  date_from: string;
  date_to: string;
  refreshed_at: string | null;
  channels_sem_cobertura_regional: string[];
}

export interface RegiaoBrandRow {
  brand: string;
  label: string;
  marketplace_id: number;
  marketplace: string;
  gmv: number;
  orders: number;
  units_sold: number;
  uf_known_orders: number;
  uf_eligible_orders: number;
  uf_fill_pct: number | null;
  shipping_cost_covered_orders: number;
  shipping_cost_eligible_orders: number;
  shipping_cost_coverage_pct: number | null;
  coverage_level: CoverageLevel;
  coverage_warning: boolean;
}

export interface RegioesByBrandData {
  data: RegiaoBrandRow[];
  date_from: string;
  date_to: string;
  refreshed_at: string | null;
  channels_sem_cobertura_regional: string[];
}

export interface RegiaoTrendPoint {
  date: string;
  label: string;
  gmv: number;
  orders: number;
  uf_fill_pct: number | null;
}

export interface RegioesTrendData {
  granularity: "day" | "month";
  data: RegiaoTrendPoint[];
  date_from: string;
  date_to: string;
  refreshed_at: string | null;
  channels_sem_cobertura_regional: string[];
}

export interface RegioesFilterParams extends GlobalFilterParams {
  /** UF(s) — filtro LOCAL da tela (nao faz parte do contrato de filtros
   * globais/URL), suportado apenas por summary e by-uf (mesmo contrato do
   * backend — by-brand/trend nao aceitam uf). */
  uf?: string[];
}

export function fetchRegioesSummary(
  selection: MarketplaceSelection,
  filters?: RegioesFilterParams,
): Promise<RegioesSummaryData | null> {
  const marketplace = serializeMarketplaceSelection(selection);
  const qs = buildRegioesQueryParams(marketplace, filters);
  return withCache(`regioes-summary:${qs.toString()}`, () =>
    apiFetch<RegioesSummaryData>(`/api/v1/regioes/summary?${qs.toString()}`)
  );
}

export function fetchRegioesByUf(
  selection: MarketplaceSelection,
  filters?: RegioesFilterParams,
): Promise<RegioesByUfData | null> {
  const marketplace = serializeMarketplaceSelection(selection);
  const qs = buildRegioesQueryParams(marketplace, filters);
  return withCache(`regioes-by-uf:${qs.toString()}`, () =>
    apiFetch<RegioesByUfData>(`/api/v1/regioes/by-uf?${qs.toString()}`)
  );
}

export function fetchRegioesByBrand(
  selection: MarketplaceSelection,
  filters?: GlobalFilterParams,
): Promise<RegioesByBrandData | null> {
  const marketplace = serializeMarketplaceSelection(selection);
  const qs = buildFilterQuery(marketplace, undefined, filters);
  return withCache(`regioes-by-brand:${qs.toString()}`, () =>
    apiFetch<RegioesByBrandData>(`/api/v1/regioes/by-brand?${qs.toString()}`)
  );
}

export function fetchRegioesTrend(
  selection: MarketplaceSelection,
  filters?: GlobalFilterParams,
): Promise<RegioesTrendData | null> {
  const marketplace = serializeMarketplaceSelection(selection);
  const qs = buildFilterQuery(marketplace, undefined, filters);
  return withCache(`regioes-trend:${qs.toString()}`, () =>
    apiFetch<RegioesTrendData>(`/api/v1/regioes/trend?${qs.toString()}`)
  );
}


