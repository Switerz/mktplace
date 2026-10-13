/**
 * Contratos MINIMOS das superficies operacionais (MCP-COVERAGE-2).
 *
 * Mesma regra de `upstream.ts`: valida os campos consumidos pelas tools e os
 * estruturais; contrato quebrado vira `INVALID_UPSTREAM_RESPONSE`, nunca
 * "sucesso vazio". Enums que mudam a leitura do numero (disponibilidade,
 * frescor, classificacao) sao FECHADOS: um estado novo no backend quebra aqui
 * em vez de chegar ao modelo como string opaca.
 *
 * Campos de fila por pedido (`queue`, `order_ref`) NAO sao lidos: a tool pede
 * `include_queue=false` e o adapter recusa qualquer outra coisa.
 */
import { z } from "zod";

const id = z.string().min(1);
const count = z.number().int().nonnegative();
const optNum = z.number().finite().nullish();
const optStamp = z.string().nullish();
/** `Decimal` do FastAPI: chega como string decimal (ou numero). */
const decimal = z.union([z.number().finite(), z.string().regex(/^-?\d+(\.\d+)?$/)]);
const optDecimal = decimal.nullish();

// ---------------------------------------------------------------------------
// Expedicao — Shopee e Mercado Livre (fotografia por conta)
// ---------------------------------------------------------------------------

const medidasFila = {
  backlog_count: count,
  overdue_count: count,
  due_within_24h_count: count,
  on_time_count: count,
  deadline_unavailable_count: count,
  over_48h_count: count,
  slow_count: count,
  zombie_count: count,
  stalled_count: count,
};

export const expedicaoContract = z.object({
  availability: z.enum(["available", "unavailable"]),
  unavailable_reason: z.string().nullish(),
  channel: id,
  snapshot: z
    .object({
      effective_at: id,
      snapshot_hour: id,
      load_mode: z.enum(["manual_snapshot"]),
      source_health: z.enum([
        "healthy", "account_missing", "unexpected_account",
        "registry_ambiguous", "watermark_missing", "source_unavailable",
      ]),
      run_status: z.string().nullish(),
    })
    .nullish(),
  totals: z.object(medidasFila).nullish(),
  accounts: z.array(
    z.object({
      ...medidasFila,
      shop_account: id,
      brand: id,
      run_status: z.string(),
      source_watermark_at: optStamp,
    }),
  ),
  freshness: z.array(
    z.object({
      brand: id,
      freshness: z.enum(["fresh", "stale", "critical", "unknown"]),
      source_age_hours: optNum,
      derived_from_batch: z.boolean().nullish(),
    }),
  ),
  coverage: z
    .object({
      expected_accounts: z.array(z.string()),
      observed_accounts: z.array(z.string()),
      missing_accounts: z.array(z.string()),
      unexpected_accounts: z.array(z.string()),
      brands_not_covered: z.array(z.string()),
    })
    .nullish(),
  limitations: z
    .object({
      load_mode: z.string().nullish(),
      snapshot_age_hours: optNum,
      notes: z.array(z.string()).nullish(),
    })
    .nullish(),
});

export const expedicaoTrendContract = z.object({
  availability: z.enum(["available", "unavailable"]),
  unavailable_reason: z.string().nullish(),
  window_hours: count,
  from_hour: optStamp,
  to_hour: optStamp,
  truncated: z.boolean().nullish(),
  points: z.array(
    z.object({
      ...medidasFila,
      snapshot_hour: id,
      shop_account: id,
      brand: id,
    }),
  ),
});

// ---------------------------------------------------------------------------
// Expedicao — TikTok Shop (LDR por vencimento)
// ---------------------------------------------------------------------------

export const expedicaoTiktokContract = z.object({
  availability: z.enum(["available", "unavailable"]),
  unavailable_reason: z.string().nullish(),
  event: z.enum(["despacho", "coleta"]).nullish(),
  deadline_is_reconstructed: z.boolean(),
  sla_business_days: z.record(z.string(), z.number()),
  targets: z.record(z.string(), z.number()),
  snapshot: z
    .object({
      effective_at: id,
      source_watermark_at: optStamp,
      today_brt: z.string(),
    })
    .nullish(),
  ldr: z
    .object({
      from: id,
      to: id,
      days: count,
      rate: optNum,
      base: count,
      late: count,
      pending_overdue: count,
      pending_on_time: count,
      pending_at_risk: count,
      mature_due_days: count,
      partial_due_days: count,
      above_target: z.boolean(),
      internal_critical: z.boolean(),
      incident_start: z.string().nullish(),
      incident_end: z.string().nullish(),
      daily: z.array(
        z.object({
          due_date: id,
          base: count,
          late: count,
          pending_overdue: count,
          pending_on_time: count,
          rate: optNum,
          is_mature: z.boolean(),
          above_target: z.boolean(),
          is_critical: z.boolean(),
        }),
      ),
    })
    .nullish(),
  brands: z.array(
    z.object({
      brand: id,
      shop_name: z.string().nullish(),
      base: count,
      late: count,
      rate: optNum,
      above_target: z.boolean(),
    }),
  ),
  alerts: z.array(
    z.object({
      severity: z.enum(["critical", "warning", "info"]),
      code: id,
      message: z.string(),
    }),
  ),
});

// ---------------------------------------------------------------------------
// Full Mercado Livre — modalidade logistica (NAO e' estoque)
// ---------------------------------------------------------------------------

const timeStat = z.object({ days_avg: optNum, sample_count: count });

export const mlFulfillmentContract = z.object({
  date_from: id,
  date_to: id,
  brands: z.array(z.string()),
  share_full_gmv: optNum,
  share_full_orders: optNum,
  share_full_units: optNum,
  paid_gmv_total: decimal,
  paid_orders_total: count,
  paid_units_total: count,
  by_class: z.array(
    z.object({
      fulfillment_class: z.enum(["full", "non_full", "unknown"]),
      paid_gmv: decimal,
      paid_orders: count,
      cancellation_rate: optNum,
      handling: timeStat,
      delivery: timeStat,
    }),
  ),
  listings: z.object({ full_only: count, mixed: count, non_full_only: count, total: count }),
  migration_opportunities: z.array(
    z.object({
      item_id: id,
      brand: id,
      non_full_gmv: decimal,
      non_full_units: count,
      full_gmv: decimal,
      listing_class: z.enum(["mixed", "non_full_only"]),
    }),
  ),
  quality: z.object({
    unmatched_orders: count,
    is_complete: z.boolean(),
    limitations: z.array(z.string()),
  }),
  freshness: z.object({
    freshness_status: z.enum(["fresh", "stale", "never_loaded", "unknown"]),
    refreshed_at: optStamp,
    source_max_date: z.string().nullish(),
    age_hours: optNum,
    load_mode: z.enum(["manual_snapshot", "scheduled"]),
  }),
});

// ---------------------------------------------------------------------------
// Shopee FBS — desempenho por modalidade
// ---------------------------------------------------------------------------

export const shopeeFbsContract = z.object({
  meta: z.object({
    scope_label: z.string(),
    last_closed_date: z.string().nullish(),
    effective_date_from: z.string().nullish(),
    effective_date_to: z.string().nullish(),
    missing_accounts: z.array(z.string()),
    covered_brands: z.array(z.string()),
    brands_not_covered: z.array(z.string()),
    gmv_definition: z.string(),
    freshness: z.object({
      freshness_status: z.enum(["fresh", "stale", "never_loaded", "unknown"]),
      refreshed_at: optStamp,
      source_max_date: z.string().nullish(),
      closed_days_behind: optNum,
      load_mode: z.string(),
    }),
    warnings: z.array(z.string()),
    limitations: z.array(z.string()),
  }),
  totals: z.object({
    gross_gmv: optDecimal,
    created_orders: count,
    eligible_orders: count,
    cancelled_orders: count,
  }),
  rates: z.object({ cancellation_rate: optNum }),
  shares: z.object({
    share_fbs_gmv: optNum,
    share_fbs_orders: optNum,
    share_fbs_units: optNum,
  }),
  by_brand: z.array(
    z.object({
      brand: id,
      gross_gmv: optDecimal,
      eligible_orders: count,
      share_fbs_gmv: optNum,
      share_fbs_orders: optNum,
    }),
  ),
});

// ---------------------------------------------------------------------------
// Estoque Full (FBS) da Shopee — fotografia por produto
// ---------------------------------------------------------------------------

export const CLASSIFICACOES_ESTOQUE = [
  "RUPTURA_CANDIDATA",
  "BAIXO_CANDIDATO",
  "EXCESSO_CANDIDATO",
  "SEM_GIRO_CANDIDATO",
  "SUFICIENTE",
  "SEM_DEMANDA_MEDIDA",
  "KIT_NAO_CONCILIADO",
] as const;

const estoqueOk = z.object({
  status: z.literal("ok"),
  scope_label: z.string(),
  indicadores: z.object({
    unidades_vendaveis_operacional: count,
    unidades_vendaveis_kits_contexto: count,
    unidades_vendaveis_total: count,
    produtos_ruptura: count,
    produtos_baixo: count,
    produtos_excesso: count,
    produtos_sem_giro: count,
    produtos_suficientes: count,
    produtos_sem_demanda_medida: count,
    produtos_kit_nao_conciliado: count,
    produtos_total: count,
    produtos_exigem_acao: count,
  }),
  produtos: z.array(
    z.object({
      shop_account: id,
      brand: id,
      item_id: id,
      model_id: z.string(),
      item_sku: z.string().nullish(),
      item_name: z.string().nullish(),
      is_kit: z.boolean(),
      full_stock_saleable: count,
      full_stock_total: count,
      units_sold_28d: count,
      avg_daily_units_28d: decimal,
      cobertura_torre_dias: optDecimal,
      classificacao_torre: z.enum(CLASSIFICACOES_ESTOQUE),
      vinculo_vendas: z.string(),
    }),
  ),
  total_no_filtro: count,
  truncado: z.boolean(),
  frescor: z.object({
    ref_date: id,
    source_captured_at: optStamp,
    ingested_at: optStamp,
    dias_desde_a_fotografia: optNum,
    load_mode: z.string(),
  }),
  limites: z.object({
    cobertura_baixa_dias: count,
    cobertura_excesso_dias: count,
    provisorio: z.boolean(),
  }),
  contas_cobertas: z.array(z.string()),
  marcas_nao_cobertas: z.array(z.string()),
  limitacoes: z.array(z.string()),
});

const estoqueIndisponivel = z.object({
  status: z.literal("unavailable"),
  unavailable_reason: z.string(),
  motivo_tecnico: z.enum(["feature_flag_desligada", "fato_inexistente", "sem_fotografia_publicada"]),
  scope_label: z.string(),
  limitacoes: z.array(z.string()).nullish(),
});

export const shopeeEstoqueContract = z.discriminatedUnion("status", [estoqueOk, estoqueIndisponivel]);

// ---------------------------------------------------------------------------
// Monitoramento de precos (PMA)
// ---------------------------------------------------------------------------

export const COMPARISON_STATUSES = [
  "below_reference",
  "at_or_above_reference",
  "no_reference",
  "non_comparable_reference_ambiguous",
  "inactive_listing",
] as const;

export const PRODUCT_TYPES = [
  "kit_confirmed", "kit_suspected", "no_kit_signal", "product_type_unknown",
] as const;

export const monitoramentoPrecoContract = z.object({
  meta: z.object({
    marketplace: z.enum(["ml", "shopee", "tiktok"]),
    mode: z.enum(["latest", "selected_date"]),
    refreshed_at: optStamp,
    requested_observed_date: z.string().nullish(),
    observed_ref_date: z.string().nullish(),
    available_observed_dates: z.array(z.string()),
    lag_days: optNum,
    freshness_status: z.enum(["fresh", "stale", "historical", "unavailable"]),
    reference_captured_at: optStamp,
    reference_type: z.string(),
    validity_status: z.string(),
    coverage_status: z.string(),
    monitored_brands: z.array(z.string()),
    observed_brands: z.array(z.string()).nullish(),
    monitored_unobserved_brands: z.array(z.string()).nullish(),
    comparable_brands: z.array(z.string()),
    no_reference_brands: z.array(z.string()),
    out_of_scope_brands: z.record(z.string(), z.string()),
    warnings: z.array(z.string()),
    metric_version: z.string().nullish(),
    promo_context: z.enum(["available", "unavailable"]).nullish(),
    availability: z.enum(["available", "unavailable"]).nullish(),
    unavailable_reason: z.string().nullish(),
    date_policy: z.enum(["closed_day", "snapshot_current"]).nullish(),
    snapshot_mutability: z.string().nullish(),
  }),
  kpis: z.object({
    monitored_count: count,
    comparable_count: count,
    below_reference_count: count,
    at_or_above_reference_count: count,
    no_reference_count: count,
    ambiguous_reference_count: count,
    inactive_count: count,
  }),
  metrics: z.object({
    kit_confirmed: count,
    kit_suspected: count,
    kit_reference_derived: count.nullish(),
    coverage_rate: optNum,
    no_reference_breakdown: z.record(z.string(), z.number()).nullish(),
  }),
  rows: z.array(
    z.object({
      item_id: id,
      brand: id,
      marketplace: z.string(),
      product_name: z.string().nullish(),
      listing_title: z.string().nullish(),
      seller_sku: z.string().nullish(),
      shop_account: z.string().nullish(),
      listing_status: z.string().nullish(),
      advertised_price: optNum,
      suggested_retail_amount: optNum,
      difference_amount: optNum,
      difference_pct: optNum,
      match_method: z.string().nullish(),
      match_quality: z.string(),
      comparison_status: z.enum(COMPARISON_STATUSES),
      non_comparable_reason: z.string().nullish(),
      product_type: z.enum(PRODUCT_TYPES).nullish(),
      kit_component_count: optNum,
      kit_components_base_amount: optNum,
      kit_discount_pct: optNum,
      freshness_status: z.enum(["fresh", "stale", "historical", "unavailable"]),
    }),
  ),
  returned_count: count,
  total_count: count,
  truncated: z.boolean(),
});

// ---------------------------------------------------------------------------
// Canais — blocos de afiliados e de descontos TikTok
// ---------------------------------------------------------------------------

export const affiliateCostsContract = z.object({
  availability_status: z.enum(["available", "unavailable_no_source", "no_eligible_brand", "error"]),
  period_status: z.enum(["complete_month", "complete_months", "partial_month", "not_month_aligned"]),
  coverage_status: z.enum(["complete", "incomplete_brand_coverage", "unknown"]),
  freshness_status: z.enum(["manual_snapshot", "fresh", "stale", "unknown"]),
  rows: z.array(
    z.object({
      channel: id,
      brand: id,
      ref_month: id,
      creator_commission_signed: optNum,
      partner_commission_signed: optNum,
      affiliate_ads_commission_signed: optNum,
    }),
  ),
  channels: z.array(
    z.object({ channel: id, availability_status: z.string(), reason_note: z.string() }),
  ),
  months_included: z.array(z.string()),
  affiliate_refreshed_at: optStamp,
  source_watermark: optStamp,
  return_availability: z.enum(["unavailable_no_attributed_revenue"]),
  return_note: z.string(),
  limitation_note: z.string(),
});

export const tiktokDiscountsContract = z.object({
  availability_status: z.enum(["available", "unavailable_no_source", "no_eligible_brand", "error"]),
  period_status: z.enum(["complete_month", "complete_months", "partial_month", "not_month_aligned"]),
  coverage_status: z.enum(["complete", "incomplete_brand_coverage", "unknown"]),
  freshness_status: z.enum(["recent_load", "stale_load", "unknown"]),
  coverage_missing_keys: count,
  rows: z.array(
    z.object({
      brand: id,
      commercial_orders: count,
      official_gmv: optNum,
      full_product_value: optNum,
      seller_discount_signed: optNum,
      platform_subsidy_amount: optNum,
      seller_discount_rate: optNum,
      platform_subsidy_rate: optNum,
      cancelled_orders: count,
    }),
  ),
  date_from: z.string().nullish(),
  date_to: z.string().nullish(),
  discounts_refreshed_at: optStamp,
  source_max_date: z.string().nullish(),
  seller_note: z.string(),
  subsidy_note: z.string(),
  limitation_note: z.string(),
  warnings: z.array(z.string()),
});
