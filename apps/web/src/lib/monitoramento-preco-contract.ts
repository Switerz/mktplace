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

/**
 * PARTICAO COMERCIAL — cinco valores que somam `monitored_count`.
 *
 * Gate PMA-H1: `stale_observation` SAIU daqui. Era um status comercial e
 * substituia a classificacao, de modo que um sync atrasado zerava os cinco
 * cartoes comerciais e a tela aparentava "nenhum desvio de preco". Frescor
 * virou dimensao propria, em `FreshnessStatus`.
 */
export type ComparisonStatus =
  | "below_reference"
  | "at_or_above_reference"
  | "no_reference"
  | "non_comparable_reference_ambiguous"
  | "inactive_listing";

/**
 * QUALIDADE TRANSVERSAL — sobreposta a `ComparisonStatus`, nunca no lugar dele.
 *   fresh       observacao de D-1;
 *   stale       o modo `latest` caiu num dia anterior porque o sync atrasou;
 *   historical  o usuario ESCOLHEU um dia anterior (nao e' atraso);
 *   unavailable nao ha observacao materializada para responder.
 */
export type FreshnessStatus = "fresh" | "stale" | "historical" | "unavailable";

export type QueryMode = "latest" | "selected_date";

/** Alias DEPRECIADO aceito pelo backend como filtro de frescor. */
export const DEPRECATED_STALE_STATUS = "stale_observation";

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
  /** `latest` (maior observacao <= D-1) ou `selected_date` (dia pedido). */
  mode: QueryMode;
  refreshed_at: string | null;
  /** O que foi PEDIDO em `observed_date`. Nulo no modo `latest`. */
  requested_observed_date: string | null;
  /**
   * A data efetivamente USADA. Nula quando a data pedida nao tem observacao —
   * nunca substituida pela mais proxima, porque aproximar responderia outra
   * pergunta.
   */
  observed_ref_date: string | null;
  /** D-1 do dia operacional: o teto do que e' consultavel. */
  eligible_ref_date: string | null;
  /** Datas MATERIALIZADAS, <= D-1, mais recentes primeiro. Sem calendario. */
  available_observed_dates: string[];
  available_observed_dates_limit: number;
  /** Dias entre a observacao e D-1. Zero = em dia. Nulo = sem observacao. */
  lag_days: number | null;
  freshness_status: FreshnessStatus;
  reference_snapshot_id: string | null;
  reference_captured_at: string | null;
  /** Sempre o snapshot mais recente — inclusive numa data historica. */
  reference_basis: "latest_available_snapshot";
  /** Frase que declara as DUAS datas e a ausencia de vigencia historica. */
  comparison_basis_text: string | null;
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
  inactive_count: number;
  /** Frescor: SOBREPOSTO a particao comercial, nao competindo com ela. */
  fresh_count: number;
  stale_count: number;
  historical_count: number;
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
  /** Frescor da linha, ao LADO do status comercial. */
  freshness_status: FreshnessStatus;
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
  /** Data OBSERVADA, YYYY-MM-DD. Omitida = modo `latest`. */
  observedDate?: string;
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
  // Gate PMA-H1: a data OBSERVADA e' o parametro publico de data.
  if (params.observedDate) qs.set("observed_date", params.observedDate);
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  // `ref_date` NUNCA e' enviado: o nome e' ambiguo entre a data observada e a
  // data de captura da referencia, e o backend responde 422 fixo a ele.
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
