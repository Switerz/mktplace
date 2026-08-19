/**
 * Contratos MINIMOS de resposta do FastAPI.
 *
 * Por que existe: sem validacao estrutural, um payload quebrado (array
 * ausente, tipo trocado, linha sem identificador) virava "sucesso vazio" — o
 * pior resultado possivel, porque e' indistinguivel de "nao houve venda". Aqui
 * um contrato quebrado vira `INVALID_UPSTREAM_RESPONSE`.
 *
 * Escopo: valida os campos EFETIVAMENTE consumidos pelas tools, mais os
 * estruturais. Nao replica os schemas Pydantic inteiros — campos que nao
 * usamos podem mudar sem quebrar o conector.
 *
 * Regra central: **chave de identidade nunca pode ser vazia**; metrica
 * opcional pode continuar `null`.
 */
import { z } from "zod";

import { toolError } from "./errors.ts";

/** Identificador estrutural: string nao vazia. Nunca aceita "" nem null. */
const id = z.string().min(1);

/** Metrica obrigatoria: numero finito. */
const num = z.number().finite();

/** Metrica opcional: numero, `null` ou ausente — os tres sao aceitos. */
const optNum = z.number().finite().nullish();

/** Timestamp do mart: string ou ausente. Nunca fabricado. */
const optStamp = z.string().nullish();

const optFilters = z.record(z.string(), z.unknown()).nullish();

const REF_MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

/** Competencia mensal obrigatoria, no formato YYYY-MM. */
const refMonth = z.string().regex(REF_MONTH);

// ---------------------------------------------------------------------------
// Desempenho
// ---------------------------------------------------------------------------

export const overviewContract = z.object({
  // `current` e' obrigatorio: sem ele nao existe resposta de desempenho.
  current: z.object({
    gmv: num,
    orders: num,
    avg_ticket: optNum,
    tiktok_gmv: optNum,
    ml_gmv: optNum,
    shopee_gmv: optNum,
  }),
  previous: z.object({ gmv: optNum }).nullish(),
  gmv_mom_pct: optNum,
  filters: optFilters,
  refreshed_at: optStamp,
});

export const brandsContract = z.object({
  brands: z.array(
    z.object({
      brand: id,
      label: z.string(),
      total_gmv: optNum,
      orders: optNum,
      mom_pct: optNum,
    }),
  ),
  refreshed_at: optStamp,
});

export const trendContract = z.object({
  data: z.array(
    z.object({
      date: id,
      label: z.string(),
      gmv: optNum,
      orders: optNum,
    }),
  ),
  refreshed_at: optStamp,
});

// ---------------------------------------------------------------------------
// Canais
// ---------------------------------------------------------------------------

export const canaisContract = z.object({
  channel_rows: z.array(
    z.object({
      brand: id,
      label: z.string(),
      channel: id,
      channel_label: z.string(),
      gmv: optNum,
      orders: optNum,
      ad_spend: optNum,
      ads_gmv_pct: optNum,
      roas: optNum,
      acos_pct: optNum,
      marketplace_cost_pct: optNum,
      seller_shipping_pct: optNum,
      // Os seis booleanos sao o que distingue "N/A" de "sem dado": se vierem
      // ausentes, a distincao se perde e a resposta deixa de ser confiavel.
      ads_available: z.boolean(),
      marketplace_cost_available: z.boolean(),
      seller_shipping_available: z.boolean(),
      ads_applicable: z.boolean(),
      marketplace_cost_applicable: z.boolean(),
      seller_shipping_applicable: z.boolean(),
      data_warning: z.string().nullish(),
    }),
  ),
  channel_medians: z.array(
    z.object({
      channel: id,
      channel_label: z.string(),
      gmv_median: optNum,
      roas_median: optNum,
      marketplace_cost_pct_median: optNum,
      seller_shipping_pct_median: optNum,
      brands_with_data: optNum,
    }),
  ),
  filters: optFilters,
  refreshed_at: optStamp,
});

// ---------------------------------------------------------------------------
// Produtos
// ---------------------------------------------------------------------------

const productCommon = {
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive().nullish(),
  offset: z.number().int().nonnegative().nullish(),
  refreshed_at: optStamp,
};

export const produtosMlContract = z.object({
  ...productCommon,
  // ML e' cumulativo: `scope` descreve isso e NAO ha ref_month.
  scope: z.string().nullish(),
  items: z.array(
    z.object({
      brand: id,
      item_id: id,
      title: z.string().nullish(),
      gross_revenue: optNum,
      units_sold: optNum,
      revenue_share_pct: optNum,
      pareto_bucket: z.string().nullish(),
      ad_roas: optNum,
      ad_acos_pct: optNum,
      action_signal: z.string().nullish(),
    }),
  ),
});

export const produtosTiktokContract = z.object({
  ...productCommon,
  // Competencia obrigatoria: e' o que permite declarar o periodo real.
  ref_month: refMonth,
  items: z.array(
    z.object({
      brand: id,
      product_id: id,
      product_name: z.string().nullish(),
      gmv: optNum,
      items_sold: optNum,
      pareto_bucket: z.string().nullish(),
    }),
  ),
});

export const produtosShopeeContract = z.object({
  ...productCommon,
  ref_month: refMonth,
  items: z.array(
    z.object({
      brand: id,
      // No mart da Shopee `sku_ref` e' opcional, mas `product_name` nao e':
      // a identidade da linha e' resolvida por um dos dois (ver `productKey`).
      sku_ref: z.string().nullish(),
      product_name: id,
      gmv: optNum,
      units_sold: optNum,
      pareto_bucket: z.string().nullish(),
    }),
  ),
});

// ---------------------------------------------------------------------------
// Qualidade
// ---------------------------------------------------------------------------

export const healthContract = z.object({
  active_source: z.string(),
  db_connected: z.boolean(),
});

export const qualityContract = z.object({
  // `kpis` obrigatorio: e' a unica fonte dos indicadores.
  kpis: z.object({
    tiktok_cancel_rate: optNum,
    ml_cancel_rate_pct: optNum,
    shopee_cancel_rate_pct: optNum,
    shopee_return_rate_pct: optNum,
  }),
  filters: optFilters,
  refreshed_at: optStamp,
});

/** Overview usado apenas para extrair frescor na tool de qualidade. */
export const overviewFreshnessContract = z.object({
  current: z.object({ gmv: optNum }),
  refreshed_at: optStamp,
});

// ---------------------------------------------------------------------------
// Regioes
// ---------------------------------------------------------------------------

export const regioesSummaryContract = z.object({
  gmv: optNum,
  orders: optNum,
  units_sold: optNum,
  ufs_com_venda: optNum,
  uf_fill_pct: optNum,
  coverage_level: z.string().nullish(),
  channels_sem_cobertura_regional: z.array(z.string()).nullish(),
  filters: optFilters,
  refreshed_at: optStamp,
});

export const regioesByUfContract = z.object({
  data: z.array(
    z.object({
      uf: id,
      gmv: optNum,
      orders: optNum,
      units_sold: optNum,
      uf_fill_pct: optNum,
      coverage_level: z.string().nullish(),
    }),
  ),
  filters: optFilters,
  refreshed_at: optStamp,
});

// ---------------------------------------------------------------------------
// Aplicacao
// ---------------------------------------------------------------------------

/**
 * Valida o payload contra o contrato. Falha vira `INVALID_UPSTREAM_RESPONSE`
 * SEM repassar o detalhe do Zod nem qualquer fragmento do corpo upstream — o
 * modelo recebe apenas a categoria.
 */
export function parseUpstream<T extends z.ZodType>(contract: T, raw: unknown): z.output<T> {
  const result = contract.safeParse(raw);
  if (!result.success) {
    throw toolError("INVALID_UPSTREAM_RESPONSE");
  }
  return result.data;
}

/**
 * Identidade da linha de produto por canal. Retorna string nao vazia ou
 * lanca — uma linha sem identificador nunca deve aparecer como item valido.
 */
export function productKey(row: {
  item_id?: string | null;
  product_id?: string | null;
  sku_ref?: string | null;
  product_name?: string | null;
}): string {
  const key = row.item_id ?? row.product_id ?? row.sku_ref ?? row.product_name ?? "";
  if (key.trim().length === 0) throw toolError("INVALID_UPSTREAM_RESPONSE");
  return key;
}

/**
 * Primeiro e ultimo dia de uma competencia `YYYY-MM`.
 * `Date.UTC(y, m, 0)` resolve fevereiro e ano bissexto sem tabela propria.
 */
export function monthBoundsOf(ref: string): { start: string; end: string } {
  if (!REF_MONTH.test(ref)) throw toolError("INVALID_UPSTREAM_RESPONSE");
  const year = Number(ref.slice(0, 4));
  const month = Number(ref.slice(5, 7));
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return { start: `${ref}-01`, end: `${ref}-${String(lastDay).padStart(2, "0")}` };
}
