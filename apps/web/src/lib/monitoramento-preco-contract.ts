/**
 * Gate PMA-3 — contrato do monitoramento de precos, SEM dependencias.
 *
 * Separado de `api-client.ts` de proposito: aquele modulo importa
 * `./mock-data` e outros helpers, e o type-stripping nativo do Node (usado
 * por `node --test`) nao resolve import sem extensao. Com o contrato isolado,
 * os tipos e a montagem de query ficam testaveis sem carregar a arvore
 * inteira do cliente. `api-client.ts` reexporta tudo daqui.
 */
// ---------------------------------------------------------------------------
// Monitoramento de precos proprios (Gate PMA-3)
// ---------------------------------------------------------------------------
// Tipos FIEIS ao schema Python de `apps/api/app/schemas/monitoramento_preco.py`.
// Nenhum campo e' derivado aqui: diferenca, match e classificacao vem do
// backend. O frontend so apresenta.

export type ComparisonStatus =
  | "below_reference"
  | "at_or_above_reference"
  | "no_reference"
  | "non_comparable_reference_ambiguous"
  | "inactive_listing"
  | "stale_observation";

export type MatchMethod = "brand_gtin_exact" | "brand_sku_exact_unique";

export type MatchQuality =
  | "primary_gtin_exact"
  | "secondary_sku_unique_in_brand"
  | "ambiguous_multiple_candidates"
  | "unmatched";

export interface MonitoramentoPrecoMeta {
  timezone: string;
  currency: string;
  marketplace: "ml";
  /** Sempre `latest`. Nao existe modo historico. */
  mode: "latest";
  refreshed_at: string | null;
  /** Maior `ref_date` publicada. NAO e' "hoje": o sync publica no maximo D-1. */
  observed_ref_date: string | null;
  /** D-1 do dia operacional — a unica data que sustenta comparacao. */
  eligible_ref_date: string | null;
  reference_snapshot_id: string | null;
  reference_captured_at: string | null;
  reference_type: "suggested_retail_pdv";
  policy_status: "not_applicable_to_own_store_monitoring";
  /** So `missing`: a referencia B2B nao tem vigencia declarada. */
  validity_status: "missing";
  /** So `advertised_only`: sem frete, cupom, subsidio ou checkout. */
  coverage_status: "advertised_only";
  monitored_brands: string[];
  comparable_brands: string[];
  no_reference_brands: string[];
  out_of_scope_brands: Record<string, string>;
  order_by: string;
  warnings: string[];
}

export interface MonitoramentoPrecoKpis {
  monitored_count: number;
  comparable_count: number;
  below_reference_count: number;
  at_or_above_reference_count: number;
  no_reference_count: number;
  ambiguous_reference_count: number;
  stale_count: number;
  inactive_count: number;
}

export interface MonitoramentoPrecoRow {
  product_name: string | null;
  brand: string;
  marketplace: string;
  item_id: string;
  seller_sku: string | null;
  gtin: string | null;
  listing_title: string | null;
  permalink: string | null;
  listing_status: string | null;
  currency: string | null;

  ref_date: string;
  /** Captura do PRECO. Sem fuso declarado pela origem — nao rotular como BRT. */
  observed_at: string | null;
  /** Alteracao do CADASTRO. NUNCA e' horario de preco. */
  listing_metadata_updated_at: string | null;
  advertised_price: number;
  original_price: number | null;

  observed_effective_amount: number;
  /** Sempre null neste MVP: a fonte do ML nao fornece. NULL != 0. */
  shipping_amount: number | null;
  seller_coupon_amount: number | null;
  platform_subsidy_amount: number | null;
  checkout_price: number | null;
  coverage_status: "advertised_only";

  suggested_retail_amount: number | null;
  reference_type: "suggested_retail_pdv";
  validity_status: "missing";
  policy_status: "not_applicable_to_own_store_monitoring";
  reference_captured_at: string | null;
  reference_row_id: string | null;

  difference_amount: number | null;
  difference_pct: number | null;
  match_method: MatchMethod | null;
  match_quality: MatchQuality;
  reference_candidate_count: number;
  comparison_status: ComparisonStatus;
  limitations: string[];
}

export interface MonitoramentoPrecoResponse {
  meta: MonitoramentoPrecoMeta;
  kpis: MonitoramentoPrecoKpis;
  rows: MonitoramentoPrecoRow[];
  returned_count: number;
  total_count: number;
  truncated: boolean;
}

export interface MonitoramentoPrecoParams {
  /** Neste MVP so `ml` — o unico canal com fonte de preco anunciado. */
  marketplace?: "ml";
  brand?: string;
  status?: string;
  productQuery?: string;
  limit?: number;
  offset?: number;
}

/** Teto do backend por pagina. As 855 linhas nao cabem numa resposta. */
export const MONITORAMENTO_PRECO_MAX_LIMIT = 500;

export function buildMonitoramentoPrecoQuery(
  params: MonitoramentoPrecoParams,
): URLSearchParams {
  const qs = new URLSearchParams();
  qs.set("marketplace", params.marketplace ?? "ml");
  if (params.brand) qs.set("brand", params.brand);
  if (params.status) qs.set("status", params.status);
  if (params.productQuery) qs.set("product_query", params.productQuery);
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  // `ref_date` NUNCA e' enviado: o endpoint opera exclusivamente em modo
  // `latest` e responde 422 fixo a qualquer data. Nao existe filtro de data.
  return qs;
}

export class MonitoramentoPrecoError extends Error {
  readonly status: number | null;
  constructor(message: string, status: number | null) {
    super(message);
    this.name = "MonitoramentoPrecoError";
    this.status = status;
  }
}
