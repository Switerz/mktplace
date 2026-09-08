/**
 * As cinco tools do MVP do Oraculo (docs/ORACLE_MCP_PLAN.md secao 8).
 *
 * Nenhuma tool escreve. Nenhuma expoe PII. Nenhuma espelha 1:1 um endpoint —
 * cada uma responde uma pergunta completa de negocio.
 *
 * Todo payload upstream passa por um contrato minimo (`upstream.ts`) ANTES de
 * ser projetado: contrato quebrado vira erro, nunca "sucesso vazio".
 */
import { z } from "zod";

import {
  buildEnvelope, CURRENT_DAY_WARNING, textFallback, type Envelope, type EnvelopePeriod,
} from "./envelope.ts";
import { toolError } from "./errors.ts";
import {
  ALWAYS_APPLICABLE, ML_COMMISSION_MISSING, ML_PRODUCTS_CUMULATIVE,
  scopeWarningToLimitation,
  REGIONAL_COVERAGE, TIKTOK_CANCEL_NOT_MEASURED, TIKTOK_SETTLEMENT_DIRECTIONAL,
  type Limitation,
} from "./limitations.ts";
import {
  canaisInput, CHANNEL_LABELS, desempenhoInput, produtosInput, qualidadeInput,
  regioesInput, resolvePeriod, type Channel, type ResolvedPeriod,
} from "./schemas.ts";
import { numOrNull, type TorreClient } from "./torre-client.ts";
import {
  brandsContract, canaisContract, healthContract, monthBoundsOf, overviewContract,
  overviewFreshnessContract, parseUpstream, productKey, produtosMlContract,
  produtosShopeeContract, produtosTiktokContract, qualityContract,
  regioesByUfContract, regioesSummaryContract, trendContract,
} from "./upstream.ts";

export type ToolDeps = {
  client: TorreClient;
  /** Relogio injetavel: a suite nao depende da hora real da maquina. */
  now: () => Date;
};

export type ToolResult = {
  content: Array<{ type: "text"; text: string }>;
  structuredContent: Envelope<unknown>;
};

export type OracleTool = {
  name: string;
  config: {
    title: string;
    description: string;
    inputSchema?: z.ZodType;
    outputSchema: z.ZodType;
  };
  run: (args: unknown, deps: ToolDeps) => Promise<ToolResult>;
};

const GMV_DEFINITION =
  "GMV = valor bruto dos pedidos, sem frete, sem cancelados e sem devolucoes.";

// ---------------------------------------------------------------------------
// Helpers compartilhados
// ---------------------------------------------------------------------------

/** Envelope como schema Zod, para servir de `outputSchema` das tools. */
function envelopeSchema<T extends z.ZodType>(data: T) {
  return z.object({
    meta: z.object({
      source: z.string(),
      layer: z.string(),
      period: z
        .object({ start: z.string(), end: z.string(), inclusive: z.literal(true) })
        .nullable(),
      timezone: z.string(),
      currency: z.literal("BRL"),
      monetary_unit: z.literal("reais"),
      filters_applied: z.record(z.string(), z.unknown()),
      metric_definition: z.string(),
      refreshed_at: z.string().nullable(),
      coverage: z.string(),
      limit: z.number().nullable(),
      returned_count: z.number(),
      total_count: z.number().nullable(),
      truncated: z.boolean(),
      warnings: z.array(z.string()),
    }),
    data,
  });
}

// ---------------------------------------------------------------------------
// Gate SH-API-2D — selo de qualidade do escopo, exposto ao modelo.
//
// Por que o selo tem de sair no `data` e nao so em `limitations`: o modelo usa
// `limitations` como ressalva textual, mas decide com os CAMPOS. Sem
// `definitive`/`maturity_status` estruturados, um mes em maturacao seria
// resumido como "vendeu R$ X" com uma nota de rodape que o modelo pode nao
// repassar. Com o campo, "posso tratar como definitivo?" tem resposta binaria.
// ---------------------------------------------------------------------------

const scopeQualityOutSchema = z.object({
  ref_month: z.string(),
  brand: z.string().nullable(),
  source_status: z.string(),
  load_status: z.string(),
  eligibility_status: z.string(),
  maturity_status: z.string(),
  coverage_status: z.string(),
  /** Ultima publicacao NO MART. Nao e a atualizacao do dado na Shopee. */
  loaded_at: z.string().nullable(),
  load_age_days: z.number().nullable(),
  /** false = NAO apresentar como numero fechado. */
  definitive: z.boolean(),
  /**
   * Indice OPERACIONAL de maturacao. NAO e percentual de conclusao: valores
   * > 1 sao normais. Sempre citar `maturation_index_note` ao mencionar o
   * numero. null = nao medido, nunca 0.
   */
  maturation_index: z.number().nullable(),
  /** Limiar heuristico configuravel, nao meta. */
  maturation_threshold: z.number().nullable(),
  maturation_index_note: z.string().nullable(),
  rows_present: z.number().nullable(),
  eligible_rows: z.number().nullable(),
});

type ScopeQualityOut = z.infer<typeof scopeQualityOutSchema>;

/**
 * Forma do selo como ele chega do upstream (ja validado por
 * `scopeQualityContract`). Declarado com nome proprio porque DOIS pontos o
 * consomem — a tool de produtos e a de qualidade — e ambos precisam de
 * `warnings`, que e a fonte das limitacoes.
 */
type UpstreamScopeQuality = {
  ref_month: string;
  brand?: string | null;
  source_status: string;
  load_status: string;
  eligibility_status: string;
  maturity_status: string;
  coverage_status: string;
  loaded_at?: string | null;
  load_age_days?: number | null;
  definitive: boolean;
  measured: {
    maturation_index?: number | null;
    maturation_threshold?: number | null;
    maturation_index_note?: string | null;
    rows_present?: number | null;
    eligible_rows?: number | null;
  };
  warnings?: Array<{ code: string; severity: string; message: string }> | null;
};

/** Achata o selo do upstream no formato de saida. */
function toScopeQualityOut(q: UpstreamScopeQuality): ScopeQualityOut {
  return {
    ref_month: q.ref_month,
    brand: q.brand ?? null,
    source_status: q.source_status,
    load_status: q.load_status,
    eligibility_status: q.eligibility_status,
    maturity_status: q.maturity_status,
    coverage_status: q.coverage_status,
    loaded_at: q.loaded_at ?? null,
    load_age_days: numOrNull(q.load_age_days),
    definitive: q.definitive,
    maturation_index: numOrNull(q.measured.maturation_index),
    maturation_threshold: numOrNull(q.measured.maturation_threshold),
    maturation_index_note: q.measured.maturation_index_note ?? null,
    rows_present: numOrNull(q.measured.rows_present),
    eligible_rows: numOrNull(q.measured.eligible_rows),
  };
}

const limitationSchema = z.object({
  topic: z.string(),
  scope: z.string(),
  description: z.string(),
});

const windowSchema = z.object({
  start: z.string(),
  end: z.string(),
  inclusive: z.literal(true),
});

/** Converte o escopo validado nos parametros aceitos pelo backend. */
function scopeParams(canais?: Channel[], marcas?: string[]) {
  return {
    channels: canais && canais.length > 0 ? [...canais].sort().join(",") : "all",
    brands: marcas && marcas.length > 0 ? [...marcas].sort().join(",") : undefined,
  };
}

/** Resolve o periodo convertendo erro de faixa em erro de tool determinista. */
function safeResolvePeriod(input: Parameters<typeof resolvePeriod>[0], now: Date): ResolvedPeriod {
  try {
    return resolvePeriod(input, now);
  } catch (err) {
    throw toolError("INVALID_INPUT", err instanceof Error ? err.message : undefined);
  }
}

function asWindow(p: { start: string; end: string }): EnvelopePeriod {
  return { start: p.start, end: p.end, inclusive: true };
}

/** Eco dos filtros REALMENTE aplicados, lido da resposta do backend. */
function echoFilters(
  filters: Record<string, unknown> | null | undefined,
  fallback: Record<string, unknown>,
): Record<string, unknown> {
  // Array tambem e' `typeof "object"`: um eco em forma de lista nao descreve
  // filtros e nao pode virar `filters_applied`.
  if (!filters || typeof filters !== "object" || Array.isArray(filters)) return fallback;
  return filters;
}

function periodWarnings(period: ResolvedPeriod, extra: string[] = []): string[] {
  return period.includesCurrentDay ? [CURRENT_DAY_WARNING, ...extra] : extra;
}

function share(part: number | null, total: number | null): number | null {
  if (part === null || total === null || total <= 0) return null;
  return Math.round((part / total) * 10000) / 100;
}

function ok(headline: string, env: Envelope<unknown>): ToolResult {
  return {
    content: [{ type: "text", text: textFallback(headline, env) }],
    structuredContent: env,
  };
}

// ---------------------------------------------------------------------------
// 1. torre_desempenho_marketplaces
// ---------------------------------------------------------------------------

const desempenhoOutput = envelopeSchema(
  z.object({
    total: z.object({
      gmv: z.number().nullable(),
      orders: z.number().nullable(),
      avg_ticket: z.number().nullable(),
      gmv_previous: z.number().nullable(),
      change_pct: z.number().nullable(),
    }),
    by_channel: z.array(
      z.object({
        channel: z.string(),
        channel_label: z.string(),
        gmv: z.number().nullable(),
        share_pct: z.number().nullable(),
      }),
    ),
    by_brand: z.array(
      z.object({
        brand: z.string(),
        label: z.string(),
        gmv: z.number().nullable(),
        orders: z.number().nullable(),
        change_pct: z.number().nullable(),
      }),
    ),
    series: z
      .array(
        z.object({
          date: z.string(),
          label: z.string(),
          gmv: z.number().nullable(),
          orders: z.number().nullable(),
        }),
      )
      .nullable(),
  }),
);

export const desempenhoTool: OracleTool = {
  name: "torre_desempenho_marketplaces",
  config: {
    title: "Desempenho consolidado dos marketplaces",
    description: [
      "Use para responder 'quanto vendemos', 'como foi o mes', GMV, pedidos, ticket medio e crescimento por canal (TikTok Shop, Mercado Livre, Shopee) e por marca.",
      "Granularidade: agregado do periodo, com serie diaria/semanal/mensal opcional.",
      `Metrica principal: ${GMV_DEFINITION}`,
      "Retorna o universo completo dos canais e marcas no escopo — nao e' top-N.",
      "NAO use para produto individual, regiao/UF, qualidade de entrega, dados intradiarios ou margem/lucro (nao existe CMV nesta base).",
      "Limitacao relevante: o dia corrente e' sempre parcial e vem sinalizado em warnings.",
    ].join(" "),
    inputSchema: desempenhoInput,
    outputSchema: desempenhoOutput,
  },
  async run(args, deps) {
    const input = desempenhoInput.parse(args);
    const period = safeResolvePeriod(input, deps.now());
    const scope = scopeParams(input.canais, input.marcas);

    const base = {
      channels: scope.channels,
      brands: scope.brands,
      date_from: period.start,
      date_to: period.end,
      compare: input.comparar ? "true" : undefined,
    };

    const [overviewRaw, brandsRaw] = await Promise.all([
      deps.client.get("overview", base),
      deps.client.get("brands", base),
    ]);

    const overview = parseUpstream(overviewContract, overviewRaw);
    const brandsPayload = parseUpstream(brandsContract, brandsRaw);

    const current = overview.current;
    const totalGmv = numOrNull(current.gmv);

    const requested: Channel[] = input.canais ?? ["tiktok", "ml", "shopee"];
    const byChannel = requested.map((c) => {
      const gmv = numOrNull(
        c === "tiktok" ? current.tiktok_gmv : c === "ml" ? current.ml_gmv : current.shopee_gmv,
      );
      return {
        channel: c,
        channel_label: CHANNEL_LABELS[c],
        gmv,
        share_pct: share(gmv, totalGmv),
      };
    });

    const byBrand = brandsPayload.brands.map((row) => ({
      brand: row.brand,
      label: row.label,
      gmv: numOrNull(row.total_gmv),
      orders: numOrNull(row.orders),
      change_pct: numOrNull(row.mom_pct),
    }));

    let series:
      | Array<{ date: string; label: string; gmv: number | null; orders: number | null }>
      | null = null;
    if (input.granularidade !== "none") {
      const trend = parseUpstream(
        trendContract,
        await deps.client.get("trend", { ...base, granularity: input.granularidade }),
      );
      series = trend.data.map((point) => ({
        date: point.date,
        label: point.label,
        gmv: numOrNull(point.gmv),
        orders: numOrNull(point.orders),
      }));
    }

    const env = buildEnvelope({
      period: asWindow(period),
      filtersApplied: echoFilters(overview.filters, {
        channels: scope.channels,
        brands: input.marcas ?? null,
      }),
      metricDefinition: GMV_DEFINITION,
      refreshedAt: overview.refreshed_at ?? null,
      coverage: "universo completo dos canais e marcas no escopo",
      returnedCount: byChannel.length + byBrand.length,
      warnings: periodWarnings(period),
      data: {
        total: {
          gmv: totalGmv,
          orders: numOrNull(current.orders),
          avg_ticket: numOrNull(current.avg_ticket),
          gmv_previous: input.comparar ? numOrNull(overview.previous?.gmv) : null,
          change_pct: input.comparar ? numOrNull(overview.gmv_mom_pct) : null,
        },
        by_channel: byChannel,
        by_brand: byBrand,
        series,
      },
    });

    return ok(
      `Desempenho consolidado dos marketplaces (${period.start} a ${period.end}).`,
      env,
    );
  },
};

// ---------------------------------------------------------------------------
// 2. torre_comparar_canais_marcas
// ---------------------------------------------------------------------------

const canaisOutput = envelopeSchema(
  z.object({
    rows: z.array(
      z.object({
        brand: z.string(),
        label: z.string(),
        channel: z.string(),
        channel_label: z.string(),
        gmv: z.number().nullable(),
        orders: z.number().nullable(),
        ad_spend: z.number().nullable(),
        ads_over_gmv_pct: z.number().nullable(),
        roas: z.number().nullable(),
        acos_pct: z.number().nullable(),
        marketplace_cost_pct: z.number().nullable(),
        seller_shipping_pct: z.number().nullable(),
        ads_applicable: z.boolean(),
        ads_available: z.boolean(),
        marketplace_cost_applicable: z.boolean(),
        marketplace_cost_available: z.boolean(),
        seller_shipping_applicable: z.boolean(),
        seller_shipping_available: z.boolean(),
        data_warning: z.string().nullable(),
      }),
    ),
    channel_medians: z.array(
      z.object({
        channel: z.string(),
        channel_label: z.string(),
        gmv_median: z.number().nullable(),
        roas_median: z.number().nullable(),
        marketplace_cost_pct_median: z.number().nullable(),
        seller_shipping_pct_median: z.number().nullable(),
        brands_with_data: z.number().nullable(),
      }),
    ),
    limitations: z.array(limitationSchema),
  }),
);

export const canaisTool: OracleTool = {
  name: "torre_comparar_canais_marcas",
  config: {
    title: "Comparacao de eficiencia entre canais e marcas",
    description: [
      "Use para comparar EFICIENCIA entre canais e marcas: ROAS, ACOS, investimento de ads sobre GMV, custo de marketplace e frete do vendedor, com medianas por canal.",
      "Granularidade: uma linha por marca x canal. Retorna o universo completo, nao e' top-N.",
      "Cada metrica traz 'applicable' e 'available': nao aplicavel (o canal nao opera esse custo) e' DIFERENTE de sem dado. Nenhum dos dois vira zero.",
      "Limitacoes que devem ser ditas ao usuario: a comissao do Mercado Livre NAO existe no mart, e o custo do TikTok e' direcional (base de repasse difere do GMV em ~5,5%).",
      "NAO use para totais de GMV (use torre_desempenho_marketplaces), para produtos, nem para margem.",
    ].join(" "),
    inputSchema: canaisInput,
    outputSchema: canaisOutput,
  },
  async run(args, deps) {
    const input = canaisInput.parse(args);
    const period = safeResolvePeriod(input, deps.now());
    const scope = scopeParams(input.canais, input.marcas);

    const payload = parseUpstream(
      canaisContract,
      await deps.client.get("canais", {
        channels: scope.channels,
        brands: scope.brands,
        date_from: period.start,
        date_to: period.end,
      }),
    );

    const rows = payload.channel_rows.map((row) => ({
      brand: row.brand,
      label: row.label,
      channel: row.channel,
      channel_label: row.channel_label,
      gmv: numOrNull(row.gmv),
      orders: numOrNull(row.orders),
      ad_spend: numOrNull(row.ad_spend),
      ads_over_gmv_pct: numOrNull(row.ads_gmv_pct),
      roas: numOrNull(row.roas),
      acos_pct: numOrNull(row.acos_pct),
      marketplace_cost_pct: numOrNull(row.marketplace_cost_pct),
      seller_shipping_pct: numOrNull(row.seller_shipping_pct),
      ads_applicable: row.ads_applicable,
      ads_available: row.ads_available,
      marketplace_cost_applicable: row.marketplace_cost_applicable,
      marketplace_cost_available: row.marketplace_cost_available,
      seller_shipping_applicable: row.seller_shipping_applicable,
      seller_shipping_available: row.seller_shipping_available,
      // `signals` do backend NAO e' exposto cru: e' texto livre nao
      // normalizado, e um sinal desconhecido viraria afirmacao sem contrato.
      data_warning: row.data_warning ?? null,
    }));

    // Mediana/p75 so existem quando ha >= 2 marcas com dado; o backend ja
    // devolve `null` nesse caso e nos preservamos, sem fabricar comparacao.
    const medians = payload.channel_medians.map((m) => ({
      channel: m.channel,
      channel_label: m.channel_label,
      gmv_median: numOrNull(m.gmv_median),
      roas_median: numOrNull(m.roas_median),
      marketplace_cost_pct_median: numOrNull(m.marketplace_cost_pct_median),
      seller_shipping_pct_median: numOrNull(m.seller_shipping_pct_median),
      brands_with_data: numOrNull(m.brands_with_data),
    }));

    // Limitacoes DERIVADAS da resposta, nao presumidas.
    const limitations: Limitation[] = [];
    if (
      rows.some(
        (r) => r.channel === "ml" && r.marketplace_cost_applicable && !r.marketplace_cost_available,
      )
    ) {
      limitations.push(ML_COMMISSION_MISSING);
    }
    if (rows.some((r) => r.channel === "tiktok" && r.marketplace_cost_available)) {
      limitations.push(TIKTOK_SETTLEMENT_DIRECTIONAL);
    }

    const env = buildEnvelope({
      period: asWindow(period),
      filtersApplied: echoFilters(payload.filters, {
        channels: scope.channels,
        brands: input.marcas ?? null,
      }),
      metricDefinition:
        "ROAS = receita atribuida / investimento; ACOS = investimento / receita atribuida; custo de marketplace = taxas / GMV. Denominador zero devolve nulo, nunca zero.",
      refreshedAt: payload.refreshed_at ?? null,
      coverage: "universo completo das combinacoes marca x canal com dado no periodo",
      returnedCount: rows.length,
      warnings: periodWarnings(period),
      data: { rows, channel_medians: medians, limitations },
    });

    return ok(`Eficiencia por canal e marca (${period.start} a ${period.end}).`, env);
  },
};

// ---------------------------------------------------------------------------
// 3. torre_produtos_prioritarios
// ---------------------------------------------------------------------------

const produtosOutput = envelopeSchema(
  z.object({
    channel: z.string(),
    temporal_scope: z.enum(["cumulativo", "mensal"]),
    /** Competencia REAL aplicada pela fonte; `null` apenas para o ML. */
    ref_month: z.string().nullable(),
    items: z.array(
      z.object({
        position: z.number(),
        identifier: z.string(),
        title: z.string().nullable(),
        brand: z.string(),
        revenue: z.number().nullable(),
        units: z.number().nullable(),
        revenue_share_pct: z.number().nullable(),
        pareto_bucket: z.string().nullable(),
        roas: z.number().nullable(),
        acos_pct: z.number().nullable(),
        action_signal: z.string().nullable(),
      }),
    ),
    /**
     * `null` = este canal ainda nao publica selo de escopo (ML, TikTok).
     * Ausencia de selo NAO significa escopo confiavel.
     */
    scope_quality: scopeQualityOutSchema.nullable(),
    limitations: z.array(limitationSchema),
  }),
);

export const produtosTool: OracleTool = {
  name: "torre_produtos_prioritarios",
  config: {
    title: "Produtos prioritarios por canal",
    description: [
      "Use para 'quais produtos vendem mais', 'onde investir', 'curva ABC/Pareto' em UM canal especifico (ml, tiktok ou shopee).",
      "Granularidade: produto. Retorna TOP-N ordenado por receita (padrao 20, maximo 50) — NAO e' o universo completo; o total real vem em meta.total_count e meta.truncated.",
      "Metrica principal: receita bruta do produto no escopo. Somente o Mercado Livre tem ROAS/ACOS por produto.",
      "TikTok e Shopee tem competencia MENSAL: o mes efetivamente aplicado vem em data.ref_month e em meta.period, mesmo quando nao informado na chamada.",
      "O Mercado Livre e' CUMULATIVO, sem competencia mensal: nao aceita filtro de mes e meta.period vem nulo.",
      "NAO existe margem real (sem CMV) e nenhum campo de margem estimada e' devolvido. NAO use para totais por canal nem para comparar marcas entre si.",
    ].join(" "),
    inputSchema: produtosInput,
    outputSchema: produtosOutput,
  },
  async run(args, deps) {
    const input = produtosInput.parse(args);

    const query = {
      brand: input.marca,
      ref_month: input.canal === "ml" ? undefined : input.mes,
      pareto_bucket: input.pareto_bucket,
      limit: input.limite,
    };

    let refMonth: string | null = null;
    let period: EnvelopePeriod | null = null;
    let items: Array<{
      position: number;
      identifier: string;
      title: string | null;
      brand: string;
      revenue: number | null;
      units: number | null;
      revenue_share_pct: number | null;
      pareto_bucket: string | null;
      roas: number | null;
      acos_pct: number | null;
      action_signal: string | null;
    }>;
    let totalCount: number;
    let refreshedAt: string | null;
    let scopeQuality: ScopeQualityOut | null = null;
    const limitations: Limitation[] = [];

    if (input.canal === "ml") {
      const payload = parseUpstream(
        produtosMlContract,
        await deps.client.get("produtosMl", query),
      );
      // ML e' cumulativo: nao inventamos ref_month nem periodo.
      items = payload.items.map((row, i) => ({
        position: i + 1,
        identifier: productKey(row),
        title: row.title ?? null,
        brand: row.brand,
        revenue: numOrNull(row.gross_revenue),
        units: numOrNull(row.units_sold),
        revenue_share_pct: numOrNull(row.revenue_share_pct),
        pareto_bucket: row.pareto_bucket ?? null,
        roas: numOrNull(row.ad_roas),
        acos_pct: numOrNull(row.ad_acos_pct),
        action_signal: row.action_signal ?? null,
      }));
      totalCount = payload.total;
      refreshedAt = payload.refreshed_at ?? null;
      limitations.push(ML_PRODUCTS_CUMULATIVE);
    } else {
      const contract =
        input.canal === "tiktok" ? produtosTiktokContract : produtosShopeeContract;
      const endpoint = input.canal === "tiktok" ? "produtosTiktok" : "produtosShopee";
      const payload = parseUpstream(contract, await deps.client.get(endpoint, query));

      // Competencia EFETIVA: vem da fonte, nao do input. Se o usuario pediu um
      // mes e a fonte respondeu outro, a resposta nao descreve o que foi
      // pedido — apresentar isso seria enganoso.
      refMonth = payload.ref_month;
      if (input.mes !== undefined && input.mes !== refMonth) {
        throw toolError("INVALID_UPSTREAM_RESPONSE");
      }
      period = asWindow(monthBoundsOf(refMonth));

      items = payload.items.map((row, i) => {
        const anyRow = row as {
          product_name?: string | null;
          gmv?: number | null;
          items_sold?: number | null;
          units_sold?: number | null;
        };
        return {
          position: i + 1,
          identifier: productKey(row),
          title: anyRow.product_name ?? null,
          brand: row.brand,
          revenue: numOrNull(anyRow.gmv),
          units: numOrNull(anyRow.items_sold ?? anyRow.units_sold),
          revenue_share_pct: null, // nao fornecido por estes canais
          pareto_bucket: row.pareto_bucket ?? null,
          roas: null, // ROAS por produto so existe no ML
          acos_pct: null,
          action_signal: null,
        };
      });
      totalCount = payload.total;
      refreshedAt = payload.refreshed_at ?? null;

      // Gate SH-API-2D. As limitacoes vem do BACKEND (que mede), nunca de uma
      // lista local: `limitations.ts` proibe explicitamente duplicar aqui algo
      // que a resposta ja informa, justamente para o conector nao contradizer
      // o mart quando a medicao mudar.
      const q = (payload as { quality?: unknown }).quality;
      if (q) {
        const selo = q as UpstreamScopeQuality;
        scopeQuality = toScopeQualityOut(selo);
        for (const w of selo.warnings ?? []) {
          limitations.push(scopeWarningToLimitation(w, input.canal));
        }
      }
    }

    const env = buildEnvelope({
      period,
      filtersApplied: {
        channel: input.canal,
        brand: input.marca ?? null,
        // Competencia REAL aplicada, nunca o eco do input.
        ref_month: refMonth,
        pareto_bucket: input.pareto_bucket ?? null,
      },
      metricDefinition:
        "Receita bruta do produto no escopo do mart. Sem CMV, portanto sem margem real.",
      refreshedAt,
      coverage: `top-${input.limite} por receita`,
      limit: input.limite,
      returnedCount: items.length,
      totalCount,
      data: {
        channel: input.canal,
        temporal_scope: input.canal === "ml" ? ("cumulativo" as const) : ("mensal" as const),
        ref_month: refMonth,
        items,
        scope_quality: scopeQuality,
        limitations,
      },
    });

    const scopeLabel = refMonth ? ` — competencia ${refMonth}` : " — base cumulativa";
    // O resumo textual TEM de carregar o veredito: e a unica parte da resposta
    // que sempre chega ao usuario final, mesmo quando o modelo nao le o `data`.
    const seloLabel = scopeQuality && !scopeQuality.definitive
      ? " ATENCAO: competencia NAO definitiva — ver data.scope_quality antes de usar."
      : "";
    return ok(
      `Produtos prioritarios: ${input.canal}${scopeLabel} (top ${input.limite}).${seloLabel}`,
      env,
    );
  },
};

// ---------------------------------------------------------------------------
// 4. torre_qualidade_dados
// ---------------------------------------------------------------------------

const qualidadeOutput = envelopeSchema(
  z.object({
    technical_health: z.object({
      active_source: z.string().nullable(),
      database_connected: z.boolean().nullable(),
    }),
    freshness: z.object({
      refreshed_at: z.string().nullable(),
      checked_period: windowSchema,
      current_day_partial: z.boolean(),
    }),
    /** Janela DISTINTA da de frescor — os indicadores olham o mes fechado. */
    quality_indicators_checked_period: windowSchema,
    quality_indicators: z.array(
      z.object({
        metric: z.string(),
        channel: z.string(),
        value_pct: z.number().nullable(),
        measured: z.boolean(),
        note: z.string().nullable(),
      }),
    ),
    /**
     * Gate SH-API-2D — confiabilidade do MART DE PRODUTOS Shopee na
     * competencia dos indicadores. Fonte DISTINTA da diaria que alimenta
     * `quality_indicators`, com ciclo de maturacao proprio: uma competencia
     * pode ter cancelamento otimo na diaria e produtos ainda em maturacao.
     * `null` = nao medido (nunca "esta tudo bem").
     */
    produtos_shopee_scope: scopeQualityOutSchema.nullable(),
    limitations: z.array(limitationSchema),
  }),
);

export const qualidadeTool: OracleTool = {
  name: "torre_qualidade_dados",
  config: {
    title: "Frescor e confiabilidade dos dados da Torre",
    description: [
      "Use ANTES de confiar em qualquer numero, e para responder 'os dados estao atualizados?', 'ate quando temos dado?', 'posso confiar nisso?', ou quando o usuario questionar uma divergencia.",
      "ATENCAO: esta e' uma resposta COMPOSTA de duas janelas distintas — o frescor olha o mes corrente (freshness.checked_period) e os indicadores de qualidade olham o mes fechado anterior (quality_indicators_checked_period). Por isso meta.period e' nulo: nao existe uma janela unica.",
      "Para o TikTok Shop o cancelamento NAO e' mensurado — vem como 0 e e' marcado com measured=false. Nunca leia isso como 0% de cancelamento.",
      "Inclui `data.produtos_shopee_scope`: a confiabilidade do mart de PRODUTOS da Shopee na competencia dos indicadores — fonte distinta da diaria. Se `definitive` vier false, os numeros por produto daquele mes ainda vao mudar.",
      "NAO devolve GMV, ranking nem receita: e' uma tool de metadados.",
    ].join(" "),
    inputSchema: qualidadeInput,
    outputSchema: qualidadeOutput,
  },
  async run(args, deps) {
    qualidadeInput.parse(args);
    const now = deps.now();
    // Duas janelas DIFERENTES, de proposito:
    // - frescor: mes corrente (queremos saber ate quando ha carga);
    // - indicadores: mes anterior fechado (sem ruido do dia parcial).
    const indicatorsPeriod = safeResolvePeriod({ periodo: "mes_anterior" }, now);
    const freshnessPeriod = safeResolvePeriod({ periodo: "mes_atual" }, now);

    const [healthRaw, overviewRaw, qualityRaw] = await Promise.all([
      deps.client.get("healthDatasource"),
      deps.client.get("overview", {
        channels: "all",
        date_from: freshnessPeriod.start,
        date_to: freshnessPeriod.end,
      }),
      deps.client.get("quality", {
        channels: "all",
        date_from: indicatorsPeriod.start,
        date_to: indicatorsPeriod.end,
      }),
    ]);

    const health = parseUpstream(healthContract, healthRaw);
    const overview = parseUpstream(overviewFreshnessContract, overviewRaw);
    const quality = parseUpstream(qualityContract, qualityRaw);
    const kpis = quality.kpis;

    const tiktokCancel = numOrNull(kpis.tiktok_cancel_rate);
    // Regra derivada, nao presumida: o TikTok so conta como MEDIDO se houver
    // taxa estritamente positiva. 0/null significam ausencia de medicao.
    const tiktokMeasured = tiktokCancel !== null && tiktokCancel > 0;

    const mlCancel = numOrNull(kpis.ml_cancel_rate_pct);
    const shopeeCancel = numOrNull(kpis.shopee_cancel_rate_pct);
    const shopeeReturn = numOrNull(kpis.shopee_return_rate_pct);

    const indicators = [
      {
        metric: "taxa_cancelamento",
        channel: "tiktok",
        value_pct: tiktokMeasured ? tiktokCancel : null,
        measured: tiktokMeasured,
        note: tiktokMeasured ? null : TIKTOK_CANCEL_NOT_MEASURED.description,
      },
      {
        metric: "taxa_cancelamento",
        channel: "ml",
        value_pct: mlCancel,
        measured: mlCancel !== null,
        note: null,
      },
      {
        metric: "taxa_cancelamento",
        channel: "shopee",
        value_pct: shopeeCancel,
        measured: shopeeCancel !== null,
        note: null,
      },
      {
        metric: "taxa_devolucao",
        channel: "shopee",
        value_pct: shopeeReturn,
        measured: shopeeReturn !== null,
        note: null,
      },
    ];

    const limitations: Limitation[] = [...ALWAYS_APPLICABLE];
    if (!tiktokMeasured) limitations.push(TIKTOK_CANCEL_NOT_MEASURED);

    // Gate SH-API-2D. Esta tool existe para ser chamada ANTES de confiar em
    // qualquer numero — entao o regime do mart de Produtos precisa estar aqui,
    // e nao so na tool de produtos. Sem isso era possivel perguntar "posso
    // confiar nos dados?", receber 0,3% de cancelamento, e concluir que sim
    // sobre uma competencia cujo mart de Produtos cobria ~73% do GMV.
    const seloProdutosSh = quality.produtos_shopee_quality ?? null;
    let produtosShopeeScope: ScopeQualityOut | null = null;
    if (seloProdutosSh) {
      produtosShopeeScope = toScopeQualityOut(seloProdutosSh);
      for (const w of seloProdutosSh.warnings ?? []) {
        limitations.push(scopeWarningToLimitation(w, "shopee"));
      }
    }

    const env = buildEnvelope({
      // Resposta COMPOSTA: nao existe janela unica, entao nao afirmamos uma.
      period: null,
      filtersApplied: {
        channels: "all",
        // Nomes distintos: cada janela declarada separadamente.
        freshness_period: { start: freshnessPeriod.start, end: freshnessPeriod.end, inclusive: true },
        quality_indicators_period: {
          start: indicatorsPeriod.start,
          end: indicatorsPeriod.end,
          inclusive: true,
        },
      },
      metricDefinition:
        "Metadados de disponibilidade e frescor. 'measured=false' indica ausencia de medicao, que e' diferente de valor zero. As duas janelas sao distintas e estao declaradas separadamente.",
      // `refreshed_at` e' o carimbo da carga, NAO a competencia dos indicadores.
      refreshedAt: overview.refreshed_at ?? null,
      coverage:
        "resposta composta: frescor do mes corrente e indicadores do mes fechado anterior",
      returnedCount: indicators.length,
      warnings: [],
      data: {
        technical_health: {
          active_source: health.active_source,
          database_connected: health.db_connected,
        },
        freshness: {
          refreshed_at: overview.refreshed_at ?? null,
          checked_period: asWindow(freshnessPeriod),
          current_day_partial: freshnessPeriod.includesCurrentDay,
        },
        quality_indicators_checked_period: asWindow(indicatorsPeriod),
        quality_indicators: indicators,
        produtos_shopee_scope: produtosShopeeScope,
        limitations,
      },
    });

    // O resumo textual precisa dizer que ha ressalva: e a parte da resposta
    // que sempre chega ao usuario, mesmo quando o modelo nao le o `data`.
    const seloLabel = produtosShopeeScope && !produtosShopeeScope.definitive
      ? ` ATENCAO: o mart de Produtos da Shopee NAO e definitivo em ${produtosShopeeScope.ref_month} — ver data.produtos_shopee_scope.`
      : "";
    return ok(
      `Frescor (${freshnessPeriod.start} a ${freshnessPeriod.end}) e indicadores de qualidade (${indicatorsPeriod.start} a ${indicatorsPeriod.end}).${seloLabel}`,
      env,
    );
  },
};

// ---------------------------------------------------------------------------
// 5. torre_regioes_vendas
// ---------------------------------------------------------------------------

const regioesOutput = envelopeSchema(
  z.object({
    summary: z.object({
      gmv: z.number().nullable(),
      orders: z.number().nullable(),
      units_sold: z.number().nullable(),
      ufs_with_sales: z.number().nullable(),
      uf_fill_pct: z.number().nullable(),
      coverage_level: z.string().nullable(),
    }),
    channels_without_regional_coverage: z.array(z.string()),
    by_uf: z.array(
      z.object({
        uf: z.string(),
        gmv: z.number().nullable(),
        orders: z.number().nullable(),
        units_sold: z.number().nullable(),
        uf_fill_pct: z.number().nullable(),
        coverage_level: z.string().nullable(),
      }),
    ),
    limitations: z.array(limitationSchema),
  }),
);

export const regioesTool: OracleTool = {
  name: "torre_regioes_vendas",
  config: {
    title: "Vendas por regiao (UF)",
    description: [
      "Use para 'onde vendemos', 'quais estados vendem mais', 'concentracao geografica'.",
      "Granularidade: UF (27 estados + XX para desconhecida), agregada no periodo, ordenada por GMV.",
      "AVISO OBRIGATORIO: a cobertura regional e' menor que a de canais — nem todo pedido tem UF. Os totais aqui NAO reconciliam com torre_desempenho_marketplaces, e a diferenca e' de cobertura, nao erro de calculo. Nenhum ajuste artificial e' feito para forcar igualdade.",
      "Os canais sem cobertura regional vem listados em channels_without_regional_coverage, lidos da propria fonte.",
      "NAO use para totais oficiais da empresa, nem para qualquer coisa em grao de pedido, cliente, cidade ou CEP.",
    ].join(" "),
    inputSchema: regioesInput,
    outputSchema: regioesOutput,
  },
  async run(args, deps) {
    const input = regioesInput.parse(args);
    const period = safeResolvePeriod(input, deps.now());
    const scope = scopeParams(input.canais, input.marcas);

    const base = {
      channels: scope.channels,
      brands: scope.brands,
      date_from: period.start,
      date_to: period.end,
      uf: input.ufs && input.ufs.length > 0 ? [...input.ufs].sort().join(",") : undefined,
    };

    const [summaryRaw, byUfRaw] = await Promise.all([
      deps.client.get("regioesSummary", base),
      deps.client.get("regioesByUf", base),
    ]);

    const summary = parseUpstream(regioesSummaryContract, summaryRaw);
    const byUfPayload = parseUpstream(regioesByUfContract, byUfRaw);

    const rows = byUfPayload.data
      .map((row) => ({
        uf: row.uf,
        gmv: numOrNull(row.gmv),
        orders: numOrNull(row.orders),
        units_sold: numOrNull(row.units_sold),
        uf_fill_pct: numOrNull(row.uf_fill_pct),
        coverage_level: row.coverage_level ?? null,
      }))
      .sort((a, b) => (b.gmv ?? -Infinity) - (a.gmv ?? -Infinity))
      .slice(0, input.limite);

    // Lido da fonte — nunca uma lista fixa de canais no nosso codigo.
    const semCobertura = summary.channels_sem_cobertura_regional ?? [];

    const env = buildEnvelope({
      period: asWindow(period),
      filtersApplied: echoFilters(summary.filters, {
        channels: scope.channels,
        brands: input.marcas ?? null,
        uf: input.ufs ?? null,
      }),
      metricDefinition: `${GMV_DEFINITION} Atribuido a UF apenas quando o pedido tem UF conhecida.`,
      refreshedAt: summary.refreshed_at ?? null,
      coverage:
        "UFs com venda no periodo, ordenadas por GMV — cobertura regional PARCIAL, nao reconciliavel com a Gerencial",
      limit: input.limite,
      returnedCount: rows.length,
      // Sem total verdadeiro do backend aqui: nao inventamos um.
      warnings: periodWarnings(period, [REGIONAL_COVERAGE.description]),
      data: {
        summary: {
          gmv: numOrNull(summary.gmv),
          orders: numOrNull(summary.orders),
          units_sold: numOrNull(summary.units_sold),
          ufs_with_sales: numOrNull(summary.ufs_com_venda),
          uf_fill_pct: numOrNull(summary.uf_fill_pct),
          coverage_level: summary.coverage_level ?? null,
        },
        channels_without_regional_coverage: semCobertura,
        by_uf: rows,
        limitations: [REGIONAL_COVERAGE],
      },
    });

    return ok(`Vendas por UF (${period.start} a ${period.end}).`, env);
  },
};

/** As cinco tools do MVP — nenhuma a mais. */
export const ORACLE_TOOLS: readonly OracleTool[] = [
  desempenhoTool,
  canaisTool,
  produtosTool,
  qualidadeTool,
  regioesTool,
];
