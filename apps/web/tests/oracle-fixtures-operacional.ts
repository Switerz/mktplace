// Fixtures das tools operacionais (MCP-COVERAGE-2). Numeros e nomes FICTICIOS
// — a forma espelha o payload real medido em 2026-10-13, os valores nao.

export const EXP_SHOPEE = {
  availability: "available",
  unavailable_reason: null,
  channel: "shopee",
  snapshot: {
    channel: "shopee", refresh_batch_id: "b-sh", effective_at: "2026-08-18T10:00:00Z",
    snapshot_hour: "2026-08-18T10:00:00Z", load_mode: "manual_snapshot", source_health: "healthy",
    source_advanced: true, run_status: "success", rows_extracted: 30, rows_loaded: 30, audit_run_id: 1,
  },
  totals: {
    backlog_count: 30, overdue_count: 0, due_within_24h_count: 5, on_time_count: 25,
    deadline_unavailable_count: 0, over_48h_count: 4, slow_count: 6, zombie_count: 1, stalled_count: 7,
  },
  accounts: [
    {
      shop_account: "apice", brand: "apice", backlog_count: 10, overdue_count: 0,
      due_within_24h_count: 2, on_time_count: 8, deadline_unavailable_count: 0, over_48h_count: 1,
      slow_count: 2, zombie_count: 0, stalled_count: 2, run_status: "success",
      source_watermark_at: "2026-08-18T09:50:00Z", source_advanced: true, snapshot_hour: "2026-08-18T10:00:00Z",
    },
    {
      shop_account: "barbours", brand: "barbours", backlog_count: 20, overdue_count: 0,
      due_within_24h_count: 3, on_time_count: 17, deadline_unavailable_count: 0, over_48h_count: 3,
      slow_count: 4, zombie_count: 1, stalled_count: 5, run_status: "success",
      source_watermark_at: "2026-08-18T09:50:00Z", source_advanced: true, snapshot_hour: "2026-08-18T10:00:00Z",
    },
  ],
  freshness: [
    { brand: "apice", freshness: "fresh", source_age_hours: 0.5, derived_from_batch: false },
    { brand: "barbours", freshness: "fresh", source_age_hours: 0.5, derived_from_batch: false },
  ],
  coverage: {
    expected_accounts: ["apice", "barbours"], observed_accounts: ["apice", "barbours"],
    missing_accounts: [], unexpected_accounts: [], accounts: [], brands_not_covered: ["kokeshi"],
  },
  // A fonte NUNCA deveria mandar fila com include_queue=false; a fixture manda
  // assim mesmo, para provar que a tool nao a repassa.
  queue: [
    {
      order_ref: "REF-SECRETO-123", shop_account: "apice", brand: "apice",
      created_at: "2026-08-17T10:00:00Z", paid_at: "2026-08-17T10:05:00Z",
      dispatch_deadline: "2026-08-19T10:00:00Z", deadline_source: "marketplace_native",
      deadline_status: "on_time", operational_age_status: "within_48h", is_slow_vs_baseline: false,
      is_source_zombie: false, is_stalled: false, hours_open: 24, hours_overdue: null,
      logistic_type: "x", carrier: "TRANSPORTADORA-X", source_ingested_at: null,
      source_freshness_status: "fresh", timestamp_quality: "verified",
    },
  ],
  pagination: null,
  limitations: {
    no_automation: true, load_mode: "manual_snapshot", snapshot_age_hours: 2,
    brands_not_covered: ["kokeshi"], order_identifier_withheld: true,
    notes: ["Nenhum agendamento existe."],
  },
};

/** ML: a fonte nao publica prazo — todo o backlog em deadline_unavailable. */
export const EXP_ML = {
  ...EXP_SHOPEE,
  channel: "mercadolivre",
  snapshot: { ...EXP_SHOPEE.snapshot, channel: "mercadolivre", refresh_batch_id: "b-ml" },
  totals: {
    backlog_count: 12, overdue_count: 0, due_within_24h_count: 0, on_time_count: 0,
    deadline_unavailable_count: 12, over_48h_count: 2, slow_count: 0, zombie_count: 3, stalled_count: 3,
  },
  accounts: [
    {
      shop_account: "1000000001", brand: "kokeshi", backlog_count: 12, overdue_count: 0,
      due_within_24h_count: 0, on_time_count: 0, deadline_unavailable_count: 12, over_48h_count: 2,
      slow_count: 0, zombie_count: 3, stalled_count: 3, run_status: "success",
      source_watermark_at: "2026-08-18T09:50:00Z", source_advanced: true, snapshot_hour: "2026-08-18T10:00:00Z",
    },
  ],
  freshness: [{ brand: "kokeshi", freshness: "fresh", source_age_hours: 0.2, derived_from_batch: false }],
  coverage: {
    expected_accounts: ["1000000001"], observed_accounts: ["1000000001"],
    missing_accounts: [], unexpected_accounts: [], accounts: [], brands_not_covered: [],
  },
  queue: [],
};

export const EXP_TREND_SHOPEE = {
  availability: "available", unavailable_reason: null, channel: "shopee", window_hours: 72,
  from_hour: "2026-08-18T09:00:00Z", to_hour: "2026-08-18T10:00:00Z", truncated: false,
  points: [
    // 09h: so' a apice apareceu -> hora INCOMPLETA.
    { ...EXP_SHOPEE.accounts[0], snapshot_hour: "2026-08-18T09:00:00Z", refresh_batch_id: "b0", observed_at: "2026-08-18T09:20:00Z" },
    { ...EXP_SHOPEE.accounts[0], snapshot_hour: "2026-08-18T10:00:00Z", refresh_batch_id: "b-sh", observed_at: "2026-08-18T10:20:00Z" },
    { ...EXP_SHOPEE.accounts[1], snapshot_hour: "2026-08-18T10:00:00Z", refresh_batch_id: "b-sh", observed_at: "2026-08-18T10:20:00Z" },
  ],
  limitations: EXP_SHOPEE.limitations,
};

export const EXP_TREND_ML = {
  ...EXP_TREND_SHOPEE,
  channel: "mercadolivre",
  points: [{ ...EXP_ML.accounts[0], refresh_batch_id: "b-ml", observed_at: "2026-08-18T10:20:00Z" }],
};

export const EXP_TIKTOK = {
  availability: "available", unavailable_reason: null, channel: "tiktokshop", event: "coleta",
  deadline_is_reconstructed: true, sla_business_days: { despacho: 1, coleta: 2 },
  targets: { tiktok_ldr: 0.04, internal_critical: 0.1 },
  snapshot: {
    refresh_batch_id: "b-tk", effective_at: "2026-08-18T10:00:00Z",
    source_watermark_at: "2026-08-18T10:05:00Z", today_brt: "2026-08-18",
  },
  ldr: {
    from: "2026-08-11", to: "2026-08-18", days: 7, rate: 0.05, base: 200, late: 10,
    pending_overdue: 2, pending_on_time: 30, pending_at_risk: 20, mature_due_days: 4,
    partial_due_days: 1, above_target: true, internal_critical: false,
    incident_start: null, incident_end: null,
    daily: [
      { due_date: "2026-08-12", base: 200, late: 10, pending_overdue: 2, pending_on_time: 0, rate: 0.05, is_mature: true, above_target: true, is_critical: false },
      // Dia sem base: taxa NULA, nunca 0%.
      { due_date: "2026-08-13", base: 0, late: 0, pending_overdue: 0, pending_on_time: 0, rate: null, is_mature: false, above_target: false, is_critical: false },
    ],
  },
  payment_flow: [],
  brands: [
    { brand: "apice", shop_name: "Loja Apice", base: 150, late: 9, rate: 0.06, above_target: true },
    { brand: "rituaria", shop_name: "Loja Rituaria", base: 50, late: 0, rate: 0, above_target: false },
  ],
  alerts: [{ severity: "critical", code: "fora_da_meta_tiktok", message: "Acima da meta — medicao interna." }],
};

// ---------------------------------------------------------------------------
// Full / estoque
// ---------------------------------------------------------------------------

export const ML_FULL = {
  date_from: "2026-07-18", date_to: "2026-08-17", brands: [],
  share_full_gmv: 0.8, share_full_orders: 0.75, share_full_units: 0.7,
  paid_gmv_total: "1000.50", paid_orders_total: 40, paid_units_total: 44,
  by_class: [
    {
      fulfillment_class: "full", paid_gmv: "800.40", paid_orders: 30, paid_units: 31, eligible_orders: 31,
      cancelled_orders: 1, other_orders: 0, cancellation_rate: 0.03,
      handling: { seconds_avg: 1, hours_avg: 1, days_avg: 1.1, sample_count: 30 },
      delivery: { seconds_avg: 1, hours_avg: 1, days_avg: 2.5, sample_count: 29 },
    },
    {
      // Classe sem amostra: tempos NULOS, nunca 0 dia.
      fulfillment_class: "unknown", paid_gmv: "0", paid_orders: 0, paid_units: 0, eligible_orders: 1,
      cancelled_orders: 1, other_orders: 0, cancellation_rate: 1,
      handling: { seconds_avg: null, hours_avg: null, days_avg: null, sample_count: 0 },
      delivery: { seconds_avg: null, hours_avg: null, days_avg: null, sample_count: 0 },
    },
  ],
  by_logistic_type: [],
  daily: [],
  listings: { full_only: 5, mixed: 3, non_full_only: 2, total: 10 },
  migration_opportunities: [
    { item_id: "MLB0001", brand: "kokeshi", non_full_gmv: "120.00", non_full_units: 4, full_gmv: "0", listing_class: "non_full_only" },
  ],
  quality: { unmatched_orders: 1, missing_shipping_items: 0, unknown_logistic_type_orders: 1, is_complete: true, limitations: ["Mede modalidade, nao estoque."] },
  freshness: { freshness_status: "fresh", refreshed_at: "2026-08-18T09:00:00Z", source_max_date: "2026-08-17", age_hours: 3, load_mode: "manual_snapshot" },
};

/** Marca sem loja no ML: a fonte devolve GMV "0" com zero pedidos. */
export const ML_FULL_SEM_PEDIDO = {
  ...ML_FULL,
  brands: ["apice"],
  share_full_gmv: null, share_full_orders: null, share_full_units: null,
  paid_gmv_total: "0", paid_orders_total: 0, paid_units_total: 0,
  by_class: [], migration_opportunities: [],
  listings: { full_only: 0, mixed: 0, non_full_only: 0, total: 0 },
};

const FBS_META = {
  marketplace: "shopee", scope_label: "Shopee — cobertura API", date_policy: "closed_day",
  d0_materialized: false, last_closed_date: "2026-08-17",
  requested_date_from: "2026-07-18", requested_date_to: "2026-08-17",
  effective_date_from: "2026-07-18", effective_date_to: "2026-08-17",
  expected_accounts: ["apice", "barbours"], observed_accounts: ["apice", "barbours"],
  missing_accounts: [], unexpected_accounts: [],
  covered_brands: ["apice", "barbours"], brands_not_covered: ["kokeshi"],
  gmv_definition: "GMV bruto de pedidos nao cancelados.",
  freshness: {
    freshness_status: "fresh", source_watermark_at: null, refreshed_at: "2026-08-18T09:30:00Z",
    source_max_date: "2026-08-17", source_min_date: "2026-01-01", source_age_hours: 1,
    snapshot_age_hours: 1, closed_days_behind: 0, load_mode: "manual_snapshot", no_automation: true,
  },
  warnings: [], limitations: ["Cobertura parcial."],
};

export const SHOPEE_FBS = {
  meta: FBS_META,
  totals: { gross_gmv: "500.00", gross_units: 20, created_orders: 22, eligible_orders: 20, cancelled_orders: 2 },
  rates: { cancellation_rate: 0.09 },
  shares: { share_fbs_gmv: 0, share_fbs_orders: 0, share_fbs_units: 0 },
  by_class: [],
  by_brand: [
    // Zero REAL: vendeu e nao teve FBS. Precisa continuar 0, nao null.
    { brand: "apice", gross_gmv: "500.00", eligible_orders: 20, created_orders: 22, cancelled_orders: 2, share_fbs_gmv: 0, share_fbs_orders: 0 },
  ],
  by_account: [],
  daily: [],
};

/** Janela antes do inicio da serie: "0" com periodo efetivo nulo. */
export const SHOPEE_FBS_SEM_SERIE = {
  ...SHOPEE_FBS,
  meta: { ...FBS_META, effective_date_from: null, effective_date_to: null },
  totals: { gross_gmv: "0", gross_units: 0, created_orders: 0, eligible_orders: 0, cancelled_orders: 0 },
  rates: { cancellation_rate: null },
  shares: { share_fbs_gmv: null, share_fbs_orders: null, share_fbs_units: null },
  by_brand: [],
};

const produto = (o: Record<string, unknown>) => ({
  shop_account: "apice", brand: "apice", item_id: "1001", model_id: "0", item_sku: "SKU-1",
  item_name: "Produto Ficticio", item_status: "normal", is_kit: false,
  full_stock_saleable: 10, full_stock_total: 10, location_count: 1, por_cd: [],
  units_sold_28d: 28, days_with_sales_28d: 20, avg_daily_units_28d: "1.0",
  cobertura_torre_dias: "10", classificacao_torre: "SUFICIENTE", vinculo_vendas: "COM_VENDA",
  reserved_stock: 0, seller_stock_total: 5, summary_available_stock: 5,
  units_sold_28d_legado_com_unpaid: 28, ref_date: "2026-08-18",
  ...o,
});

export const SHOPEE_ESTOQUE = {
  status: "ok",
  marketplace: "shopee",
  scope_label: "Estoque Full Shopee — cobertura API",
  indicadores: {
    unidades_vendaveis_operacional: 100, unidades_vendaveis_kits_contexto: 5, unidades_vendaveis_total: 105,
    produtos_ruptura: 1, produtos_baixo: 0, produtos_excesso: 0, produtos_sem_giro: 0,
    produtos_suficientes: 1, produtos_sem_demanda_medida: 1, produtos_kit_nao_conciliado: 1,
    produtos_total: 4, produtos_exigem_acao: 1,
  },
  produtos: [
    // Ruptura: estoque ZERO medido, com venda.
    produto({ item_id: "1", full_stock_saleable: 0, full_stock_total: 0, cobertura_torre_dias: "0", classificacao_torre: "RUPTURA_CANDIDATA" }),
    produto({ item_id: "2" }),
    // Sem demanda medida: cobertura NULA, nunca 0 dia.
    produto({ item_id: "3", units_sold_28d: 0, avg_daily_units_28d: "0", cobertura_torre_dias: null, classificacao_torre: "SEM_DEMANDA_MEDIDA", vinculo_vendas: "SEM_VENDA_NA_JANELA" }),
    // Kit: numero existe mas nao foi conciliado.
    produto({ item_id: "4", is_kit: true, full_stock_saleable: 0, cobertura_torre_dias: "0", classificacao_torre: "KIT_NAO_CONCILIADO" }),
  ],
  total_no_filtro: 4,
  truncado: false,
  frescor: {
    ref_date: "2026-08-18", source_captured_at: "2026-08-18T06:00:00Z", ingested_at: "2026-08-18T09:00:00Z",
    dias_desde_a_fotografia: 0, load_mode: "manual_snapshot", no_automation: true,
  },
  limites: { cobertura_baixa_dias: 7, cobertura_excesso_dias: 90, provisorio: true, observacao: "Provisorio." },
  contas_cobertas: ["apice", "barbours"],
  marcas_nao_cobertas: ["kokeshi"],
  limitacoes: ["Cobertura parcial."],
};

export const SHOPEE_ESTOQUE_INDISPONIVEL = {
  status: "unavailable",
  marketplace: "shopee",
  unavailable_reason: "Tela ainda nao habilitada.",
  motivo_tecnico: "feature_flag_desligada",
  scope_label: "Estoque Full Shopee — cobertura API",
  limitacoes: [],
};

// ---------------------------------------------------------------------------
// Monitoramento de precos
// ---------------------------------------------------------------------------

const PMA_META = {
  timezone: "America/Sao_Paulo", currency: "BRL", marketplace: "ml", mode: "latest",
  refreshed_at: "2026-08-18T09:00:00+00:00", requested_observed_date: null,
  observed_ref_date: "2026-08-17", eligible_ref_date: "2026-08-17",
  available_observed_dates: ["2026-08-17", "2026-08-16"], available_observed_dates_limit: 90,
  lag_days: 0, freshness_status: "fresh", reference_snapshot_id: "x",
  reference_captured_at: "2026-08-01T00:00:00+00:00", reference_basis: "latest_available_snapshot",
  comparison_basis_text: null, reference_type: "suggested_retail_pdv",
  policy_status: "not_applicable_to_own_store_monitoring", validity_status: "missing",
  coverage_status: "advertised_only",
  monitored_brands: ["barbours", "kokeshi", "lescent"], observed_brands: ["barbours", "kokeshi", "lescent"],
  monitored_unobserved_brands: [], comparable_brands: ["barbours", "kokeshi"], no_reference_brands: ["lescent"],
  out_of_scope_brands: { apice: "out_of_scope_no_ml_catalog" }, order_by: "difference_pct",
  warnings: ["MVP observacional."], metric_version: "v1_all_active", observation_mode: "daily_series",
  promo_context: "available", availability: "available", unavailable_reason: null,
  date_policy: "closed_day", snapshot_mutability: "settled_snapshot",
};

const anuncio = (o: Record<string, unknown>) => ({
  product_name: "Produto Ficticio", brand: "kokeshi", marketplace: "ml", item_id: "MLB1",
  seller_sku: "S1", gtin: "000", listing_title: "Anuncio Ficticio", permalink: "https://exemplo.invalid/x",
  listing_status: "active", currency: "BRL", ref_date: "2026-08-17", observed_at: null,
  listing_metadata_updated_at: null, advertised_price: 50, original_price: 60, observed_effective_amount: 50,
  shipping_amount: null, seller_coupon_amount: null, platform_subsidy_amount: null, checkout_price: null,
  coverage_status: "advertised_only", suggested_retail_amount: 100, reference_type: "suggested_retail_pdv",
  validity_status: "missing", policy_status: "not_applicable_to_own_store_monitoring",
  reference_captured_at: null, reference_row_id: null, difference_amount: -50, difference_pct: -50,
  match_method: "brand_gtin_exact", kit_protheus_sku: null, kit_bridge_method: null,
  kit_component_count: null, kit_total_units: null, kit_components_base_amount: null, kit_discount_pct: null,
  match_quality: "primary_gtin_exact", reference_candidate_count: 1, comparison_status: "below_reference",
  non_comparable_reason: null, freshness_status: "fresh", limitations: [],
  ...o,
});

export const PMA = {
  meta: PMA_META,
  kpis: {
    monitored_count: 10, comparable_count: 4, below_reference_count: 2, at_or_above_reference_count: 2,
    no_reference_count: 3, ambiguous_reference_count: 0, inactive_count: 3,
    fresh_count: 10, stale_count: 0, historical_count: 0,
  },
  metrics: {
    monitored_offers: 10, active_offers: 7, inactive_offers: 3, kit_confirmed: 1, kit_suspected: 0,
    no_kit_signal: 6, product_type_unknown: 0, kit_reference_derived: 1, eligible_offers: 7,
    comparable_offers: 4, below_reference: 2, at_or_above_reference: 2,
    non_comparable_reasons: {}, no_reference_breakdown: { reference_missing_for_product: 3 }, coverage_rate: 0.57,
  },
  rows: [
    anuncio({ item_id: "MLB1", difference_pct: -50 }),
    // Kit com referencia derivada.
    anuncio({
      item_id: "MLB2", difference_amount: -10, difference_pct: -10, product_type: "kit_confirmed",
      kit_component_count: 2, kit_components_base_amount: 100, kit_discount_pct: 5,
    }),
    // Sem referencia: diferenca NULA, nunca 0%.
    anuncio({
      item_id: "MLB3", brand: "lescent", suggested_retail_amount: null, difference_amount: null,
      difference_pct: null, comparison_status: "no_reference", match_method: null,
      match_quality: "none", reference_candidate_count: 0, non_comparable_reason: "brand_without_b2b_reference",
    }),
    // Preco anunciado ausente: continua NULO, nunca inventado.
    anuncio({ item_id: "MLB4", advertised_price: null, difference_amount: null, difference_pct: null, comparison_status: "inactive_listing", listing_status: "paused" }),
  ],
  returned_count: 4,
  total_count: 4,
  truncated: false,
};

// ---------------------------------------------------------------------------
// Canais — blocos
// ---------------------------------------------------------------------------

export const AFFILIATE_COSTS = {
  availability_status: "available", period_status: "complete_month", coverage_status: "complete",
  freshness_status: "manual_snapshot",
  rows: [
    { channel: "tiktok", brand: "barbours", ref_month: "2026-07", creator_commission_signed: -100,
      partner_commission_signed: 0, affiliate_ads_commission_signed: null, coverage_status: "complete", brands_present_in_month: 1 },
  ],
  channels: [
    { channel: "tiktok", availability_status: "available", reason_note: "Dados disponiveis." },
    { channel: "mercadolivre", availability_status: "unavailable_no_source", reason_note: "Sem fonte." },
  ],
  months_included: ["2026-07"],
  affiliate_refreshed_at: "2026-08-10T00:00:00+00:00",
  source_watermark: "2026-08-10T00:00:00",
  return_availability: "unavailable_no_attributed_revenue",
  return_note: "Retorno indisponivel: sem receita atribuida.",
  source_note: "Competencia mensal.",
  limitation_note: "Lancamentos nao somados.",
};

export const TIKTOK_DISCOUNTS = {
  availability_status: "available", period_status: "complete_month", coverage_status: "complete",
  coverage_basis: "observed_grid", coverage_expected_keys: 31, coverage_present_keys: 31, coverage_missing_keys: 0,
  freshness_status: "recent_load", date_from: "2026-07-01", date_to: "2026-07-31", date_count: 31,
  discounts_refreshed_at: "2026-08-18T08:00:00+00:00", source_max_date: "2026-07-31",
  source_max_updated_at: "2026-08-18T07:00:00",
  rows: [
    // Desconto ZERO medido: continua 0.
    { brand: "barbours", commercial_orders: 24, official_gmv: 600, full_product_value: 700,
      seller_discount_signed: 0, platform_subsidy_amount: 15, cancelled_orders: 1,
      cancelled_seller_discount_signed: 0, cancelled_platform_subsidy_amount: 0,
      seller_discount_rate: 0, platform_subsidy_rate: 2.5 },
  ],
  seller_note: "Desconto financiado pela marca.",
  subsidy_note: "Subsidio financiado pelo TikTok.",
  limitation_note: "Nunca somados.",
  warnings: [],
};

// ---------------------------------------------------------------------------
// DRE (contrato futuro)
// ---------------------------------------------------------------------------

const comp = (status: string, valor: number | null, extra: Record<string, unknown> = {}) => ({
  valor, pct_receita: null, status, motivo: status === "disponivel" ? null : `motivo ${status}`,
  fonte: null, somavel: true, partes: null, data_as_of: null, publicado_em: null, ...extra,
});

export function dreCelula(over: Record<string, unknown> = {}, publicadoEm = "2026-08-18T00:00:00+00:00") {
  return {
    escopo: "canal_marca", mes: "2026-07", canal: "tiktok", canal_label: "TikTok Shop",
    marca: "barbours", marca_label: "BARBOURS", celulas: 1, classificacao: "COMPLETA",
    motivos_classificacao: [], avisos_qualidade: [],
    componentes: {
      receita: comp("disponivel", 1000, { somavel: false }),
      devolucoes: comp("disponivel", 0),
      taxas: comp("disponivel", 100, { publicado_em: publicadoEm }),
      afiliados: comp("disponivel", 30, { somavel: false }),
      ads: comp("nao_aplicavel", null),
      frete: comp("disponivel", 50, { publicado_em: publicadoEm }),
      cmv: comp("disponivel", 400, { publicado_em: publicadoEm }),
      impostos: comp("indisponivel", 0),
      ...over,
    },
    componentes_disponiveis: [], componentes_ausentes: [],
    completude: { usados: 5, aplicaveis: 6, texto: "5 de 6" },
    margem_contribuicao_conhecida: {
      valor: 450, pct_receita: 45, rotulo: "Margem de contribuicao conhecida", formula: null,
      componentes_usados: ["receita", "devolucoes", "taxas", "frete", "cmv"], faltam: ["impostos"],
    },
    data_maxima: { insumo_publicado_em: publicadoEm },
  };
}

export function dreResponse(celulas: unknown[], estado = "ok", publicadoEm = "2026-08-18T00:00:00+00:00") {
  return {
    periodo: { date_from: "2026-07-01", date_to: "2026-07-31", meses: ["2026-07"], origem: "informado" },
    filtros: {},
    fontes: { neon: "ok", insumos_dre: { estado, publicado_em: publicadoEm, motivo: null } },
    regras: {},
    total: null, por_canal: [], por_marca: [],
    por_canal_marca: celulas,
    celulas_base: [],
  };
}
