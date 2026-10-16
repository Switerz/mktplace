/**
 * torre_estoque_full — MCP-COVERAGE-2.
 *
 * Cobertura, ruptura, estoque baixo/alto e itens prioritarios do Full.
 *
 * TRES FONTES, TRES NATUREZAS — e a tool nao mistura:
 * - Shopee ESTOQUE (`/shopee-fbs-estoque`): fotografia fisica por produto no CD
 *   da Shopee, com classificacao da Torre. E' a unica fonte de ESTOQUE.
 * - Shopee FBS (`/shopee-fbs`): participacao FBS nas vendas do periodo.
 * - Mercado Livre Full (`/ml-fulfillment`): MODALIDADE do envio. A fonte nao
 *   mede estoque. Estoque Full do ML e' APLICAVEL, mas sem fonte integrada:
 *   `estoque: "sem_dado"` com `estoque_motivo: "fonte_nao_integrada"`, nunca
 *   zero (DISCOVERY-2, 16/10/2026 — antes era `nao_aplicavel`).
 *
 * QUATRO ESTADOS que o modelo precisa distinguir, por item e por bloco:
 *   zero_real       mediu e deu zero (ex.: estoque vendavel 0 com venda)
 *   sem_dado        nao ha medicao (fonte indisponivel ou nao integrada, sem
 *                   demanda medida, periodo sem carga, marca sem pedido)
 *   nao_conciliado  existe numero, mas a semantica nao foi conciliada (kits)
 *   nao_aplicavel   a pergunta nao se aplica (mantido no vocabulario por
 *                   compatibilidade; o estoque ML NAO usa mais este estado)
 *
 * Filtros SO' por enum. O parametro de texto livre `busca` da API nao e'
 * exposto e o adapter o recusa antes do fetch.
 */
import { z } from "zod";

import { buildEnvelope } from "./envelope.ts";
import {
  STOCK_ML_NOT_STOCK, STOCK_PARTIAL_SCOPE, STOCK_TOWER_COVERAGE, type Limitation,
} from "./limitations.ts";
import { BRANDS, isoDate, noDuplicates, todayInAppTimezone, inclusiveDays } from "./schemas.ts";
import { envelopeSchema, limitationSchema, ok, safeText, safeTexts, type OracleTool, type ToolDeps } from "./tool-kit.ts";
import { decOrNull, numOrNull } from "./torre-client.ts";
import { toolError } from "./errors.ts";
import { parseUpstream } from "./upstream.ts";
import {
  CLASSIFICACOES_ESTOQUE, mlFulfillmentContract, shopeeEstoqueContract, shopeeFbsContract,
} from "./upstream-operacional.ts";

export const ESTOQUE_MAX_ITENS = 50;
/** Janela das superficies de desempenho Full/FBS. */
export const ESTOQUE_MAX_DIAS = 92;

export const estoqueInput = z
  .object({
    canais: noDuplicates(z.array(z.enum(["ml", "shopee"])).min(1).max(2), "canais").optional(),
    marcas: noDuplicates(z.array(z.enum(BRANDS)).min(1).max(5), "marcas").optional(),
    classificacoes: noDuplicates(
      z.array(z.enum(CLASSIFICACOES_ESTOQUE)).min(1).max(CLASSIFICACOES_ESTOQUE.length),
      "classificacoes",
    ).optional(),
    severidade: z.enum(["exige_acao", "todas"]).default("todas"),
    periodo: z.enum(["ultimos_30_dias_fechados", "mes_anterior", "personalizado"])
      .default("ultimos_30_dias_fechados"),
    data_inicio: isoDate.optional(),
    data_fim: isoDate.optional(),
    limite: z.number().int().min(1).max(ESTOQUE_MAX_ITENS).default(20),
  })
  .strict();

type EstoqueInput = z.infer<typeof estoqueInput>;

function shiftDays(iso: string, n: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + n));
  return dt.toISOString().slice(0, 10);
}

/** Periodo de dias FECHADOS (teto D-1), limitado a 92 dias. */
export function resolveClosedPeriod(input: EstoqueInput, now: Date): { start: string; end: string } {
  const today = todayInAppTimezone(now);
  const ontem = shiftDays(today, -1);
  if (input.periodo !== "personalizado" && (input.data_inicio || input.data_fim)) {
    throw toolError("INVALID_INPUT", "data_inicio/data_fim so com periodo='personalizado'");
  }
  if (input.periodo === "ultimos_30_dias_fechados") return { start: shiftDays(today, -30), end: ontem };
  if (input.periodo === "mes_anterior") {
    const [y, m] = today.split("-").map(Number);
    const py = m === 1 ? y - 1 : y;
    const pm = m === 1 ? 12 : m - 1;
    const last = new Date(Date.UTC(py, pm, 0)).getUTCDate();
    const mm = String(pm).padStart(2, "0");
    return { start: `${py}-${mm}-01`, end: `${py}-${mm}-${String(last).padStart(2, "0")}` };
  }
  if (!input.data_inicio || !input.data_fim) {
    throw toolError("INVALID_INPUT", "periodo 'personalizado' exige data_inicio e data_fim");
  }
  if (input.data_inicio > input.data_fim) throw toolError("INVALID_INPUT", "data_inicio posterior a data_fim");
  if (input.data_fim > ontem) throw toolError("INVALID_INPUT", "data_fim deve ser no maximo ontem (dias fechados)");
  if (inclusiveDays(input.data_inicio, input.data_fim) > ESTOQUE_MAX_DIAS) {
    throw toolError("INVALID_INPUT", `intervalo maximo de ${ESTOQUE_MAX_DIAS} dias`);
  }
  return { start: input.data_inicio, end: input.data_fim };
}

// ---------------------------------------------------------------------------
// Saida
// ---------------------------------------------------------------------------

const ESTADOS = ["zero_real", "sem_dado", "nao_conciliado", "nao_aplicavel", "medido"] as const;

const itemSchema = z.object({
  posicao: z.number(),
  brand: z.string(),
  item_id: z.string(),
  model_id: z.string(),
  sku: z.string().nullable(),
  produto: z.string().nullable(),
  is_kit: z.boolean(),
  classificacao: z.string(),
  estoque_vendavel: z.number(),
  estoque_total: z.number(),
  /** zero_real | medido | nao_conciliado. */
  estado_estoque: z.enum(ESTADOS),
  unidades_vendidas_28d: z.number(),
  media_diaria_28d: z.number().nullable(),
  /** null = sem demanda medida ou nao conciliado. Nunca 0 fabricado. */
  cobertura_dias: z.number().nullable(),
  estado_cobertura: z.enum(ESTADOS),
});

const shopeeEstoqueBlock = z.object({
  disponibilidade: z.enum(["available", "unavailable"]),
  estado: z.enum(ESTADOS),
  motivo_indisponivel: z.string().nullable(),
  escopo: z.string(),
  fotografia: z
    .object({
      ref_date: z.string(),
      capturada_em: z.string().nullable(),
      publicada_em: z.string().nullable(),
      dias_desde_a_fotografia: z.number().nullable(),
      load_mode: z.string(),
    })
    .nullable(),
  /** Descrevem o escopo de MARCA; nao seguem o filtro de classificacao. */
  indicadores: z
    .object({
      unidades_vendaveis_operacional: z.number(),
      unidades_vendaveis_kits_contexto: z.number(),
      produtos_ruptura: z.number(),
      produtos_baixo: z.number(),
      produtos_excesso: z.number(),
      produtos_sem_giro: z.number(),
      produtos_suficientes: z.number(),
      produtos_sem_demanda_medida: z.number(),
      produtos_kit_nao_conciliado: z.number(),
      produtos_exigem_acao: z.number(),
      produtos_total: z.number(),
    })
    .nullable(),
  limiares_provisorios: z
    .object({ baixo_abaixo_de_dias: z.number(), excesso_a_partir_de_dias: z.number() })
    .nullable(),
  itens: z.array(itemSchema),
  itens_no_filtro: z.number().nullable(),
  marcas_nao_cobertas: z.array(z.string()),
  notas_da_fonte: z.array(z.string()),
});

const shopeeFbsBlock = z.object({
  estado: z.enum(ESTADOS),
  escopo: z.string(),
  periodo_efetivo: z.object({ start: z.string().nullable(), end: z.string().nullable() }),
  gmv_bruto: z.number().nullable(),
  pedidos_criados: z.number().nullable(),
  taxa_cancelamento: z.number().nullable(),
  share_fbs_gmv: z.number().nullable(),
  share_fbs_pedidos: z.number().nullable(),
  share_fbs_unidades: z.number().nullable(),
  por_marca: z.array(
    z.object({
      brand: z.string(),
      gmv_bruto: z.number().nullable(),
      share_fbs_gmv: z.number().nullable(),
      share_fbs_pedidos: z.number().nullable(),
    }),
  ),
  marcas_nao_cobertas: z.array(z.string()),
  contas_faltando: z.array(z.string()),
  frescor: z.object({
    status: z.string(),
    publicado_em: z.string().nullable(),
    ultima_data_da_fonte: z.string().nullable(),
    dias_fechados_atrasados: z.number().nullable(),
  }),
  definicao_gmv: z.string(),
});

const mlFullBlock = z.object({
  estado: z.enum(ESTADOS),
  /** Estoque Full do ML: aplicavel, sem fonte integrada. `sem_dado`, nunca
   *  zero; os shares abaixo sao modalidade de venda, nao estoque. */
  estoque: z.literal("sem_dado"),
  estoque_aplicavel: z.literal(true),
  estoque_motivo: z.literal("fonte_nao_integrada"),
  periodo_efetivo: z.object({ start: z.string(), end: z.string() }),
  marcas: z.array(z.string()),
  gmv_pago_total: z.number().nullable(),
  pedidos_pagos: z.number().nullable(),
  share_full_gmv: z.number().nullable(),
  share_full_pedidos: z.number().nullable(),
  share_full_unidades: z.number().nullable(),
  por_modalidade: z.array(
    z.object({
      modalidade: z.string(),
      gmv_pago: z.number().nullable(),
      pedidos_pagos: z.number(),
      taxa_cancelamento: z.number().nullable(),
      dias_ate_despacho: z.number().nullable(),
      dias_ate_entrega: z.number().nullable(),
    }),
  ),
  anuncios: z.object({ so_full: z.number(), misto: z.number(), so_nao_full: z.number(), total: z.number() }),
  oportunidades_migracao: z.array(
    z.object({
      item_id: z.string(),
      brand: z.string(),
      gmv_nao_full: z.number().nullable(),
      unidades_nao_full: z.number(),
      gmv_full: z.number().nullable(),
      classe_anuncio: z.string(),
    }),
  ),
  qualidade: z.object({ completa: z.boolean(), pedidos_sem_envio: z.number() }),
  frescor: z.object({
    status: z.string(),
    publicado_em: z.string().nullable(),
    ultima_data_da_fonte: z.string().nullable(),
    load_mode: z.string(),
  }),
  notas_da_fonte: z.array(z.string()),
});

const estoqueOutput = envelopeSchema(
  z.object({
    shopee_estoque: shopeeEstoqueBlock.nullable(),
    shopee_fbs: shopeeFbsBlock.nullable(),
    ml_full: mlFullBlock.nullable(),
    limitations: z.array(limitationSchema),
  }),
);

type ShopeeEstoqueBlock = z.infer<typeof shopeeEstoqueBlock>;
type ShopeeFbsBlock = z.infer<typeof shopeeFbsBlock>;
type MlFullBlock = z.infer<typeof mlFullBlock>;

// ---------------------------------------------------------------------------
// Blocos
// ---------------------------------------------------------------------------

function bloqueioEstoque(motivo: string, escopo: string, naoCobertas: string[]): ShopeeEstoqueBlock {
  return {
    disponibilidade: "unavailable", estado: "sem_dado", motivo_indisponivel: motivo, escopo,
    fotografia: null, indicadores: null, limiares_provisorios: null, itens: [],
    itens_no_filtro: null, marcas_nao_cobertas: naoCobertas, notas_da_fonte: [],
  };
}

async function shopeeEstoque(input: EstoqueInput, deps: ToolDeps): Promise<ShopeeEstoqueBlock> {
  let marcas: string[] | undefined;
  let naoCobertasPedidas: string[] = [];
  if (input.marcas) {
    // A fonte RECUSA marca fora da esteira (422). A cobertura e' lida da
    // propria fonte antes do filtro — nunca de uma lista fixa nossa.
    const pre = parseUpstream(shopeeEstoqueContract, await deps.client.get("shopeeFbsEstoque", { limite: 1 }));
    if (pre.status === "unavailable") {
      return bloqueioEstoque(safeText(pre.unavailable_reason) ?? "indisponivel", pre.scope_label, []);
    }
    naoCobertasPedidas = input.marcas.filter((b) => pre.marcas_nao_cobertas.includes(b));
    marcas = input.marcas.filter((b) => !pre.marcas_nao_cobertas.includes(b));
    if (marcas.length === 0) {
      return bloqueioEstoque(
        "Nenhuma das marcas pedidas e' coberta pela esteira de estoque da Shopee.",
        pre.scope_label, naoCobertasPedidas,
      );
    }
  }

  const p = parseUpstream(
    shopeeEstoqueContract,
    await deps.client.get("shopeeFbsEstoque", {
      brands: marcas ? [...marcas].sort() : undefined,
      classificacoes: input.classificacoes ? [...input.classificacoes] : undefined,
      somente_acao: input.severidade === "exige_acao" ? "true" : undefined,
      limite: input.limite,
    }),
  );
  if (p.status === "unavailable") {
    return { ...bloqueioEstoque(safeText(p.unavailable_reason) ?? "indisponivel", p.scope_label, []), notas_da_fonte: safeTexts(p.limitacoes) };
  }

  // A fonte ja ordena por risco (ruptura, baixo, excesso, sem giro; depois
  // menor cobertura). A ordem e' preservada, com teto de 50.
  const itens = p.produtos.slice(0, input.limite).map((r, i) => {
    const kit = r.classificacao_torre === "KIT_NAO_CONCILIADO";
    const cobertura = decOrNull(r.cobertura_torre_dias);
    const semDemanda = r.classificacao_torre === "SEM_DEMANDA_MEDIDA" || (cobertura === null && !kit);
    return {
      posicao: i + 1,
      brand: r.brand,
      item_id: r.item_id,
      model_id: r.model_id,
      sku: r.item_sku ?? null,
      produto: r.item_name ?? null,
      is_kit: r.is_kit,
      classificacao: r.classificacao_torre,
      estoque_vendavel: r.full_stock_saleable,
      estoque_total: r.full_stock_total,
      estado_estoque: kit ? ("nao_conciliado" as const) : r.full_stock_saleable === 0 ? ("zero_real" as const) : ("medido" as const),
      unidades_vendidas_28d: r.units_sold_28d,
      media_diaria_28d: decOrNull(r.avg_daily_units_28d),
      cobertura_dias: kit || semDemanda ? null : cobertura,
      estado_cobertura: kit
        ? ("nao_conciliado" as const)
        : semDemanda
          ? ("sem_dado" as const)
          : cobertura === 0 ? ("zero_real" as const) : ("medido" as const),
    };
  });

  return {
    disponibilidade: "available",
    estado: "medido",
    motivo_indisponivel: null,
    escopo: p.scope_label,
    fotografia: {
      ref_date: p.frescor.ref_date,
      capturada_em: p.frescor.source_captured_at ?? null,
      publicada_em: p.frescor.ingested_at ?? null,
      dias_desde_a_fotografia: numOrNull(p.frescor.dias_desde_a_fotografia),
      load_mode: p.frescor.load_mode,
    },
    indicadores: {
      unidades_vendaveis_operacional: p.indicadores.unidades_vendaveis_operacional,
      unidades_vendaveis_kits_contexto: p.indicadores.unidades_vendaveis_kits_contexto,
      produtos_ruptura: p.indicadores.produtos_ruptura,
      produtos_baixo: p.indicadores.produtos_baixo,
      produtos_excesso: p.indicadores.produtos_excesso,
      produtos_sem_giro: p.indicadores.produtos_sem_giro,
      produtos_suficientes: p.indicadores.produtos_suficientes,
      produtos_sem_demanda_medida: p.indicadores.produtos_sem_demanda_medida,
      produtos_kit_nao_conciliado: p.indicadores.produtos_kit_nao_conciliado,
      produtos_exigem_acao: p.indicadores.produtos_exigem_acao,
      produtos_total: p.indicadores.produtos_total,
    },
    limiares_provisorios: {
      baixo_abaixo_de_dias: p.limites.cobertura_baixa_dias,
      excesso_a_partir_de_dias: p.limites.cobertura_excesso_dias,
    },
    itens,
    itens_no_filtro: p.total_no_filtro,
    marcas_nao_cobertas: [...new Set([...p.marcas_nao_cobertas, ...naoCobertasPedidas])],
    notas_da_fonte: safeTexts(p.limitacoes, 400),
  };
}

async function shopeeFbs(
  input: EstoqueInput, periodo: { start: string; end: string }, deps: ToolDeps,
): Promise<ShopeeFbsBlock> {
  const base = { date_from: periodo.start, date_to: periodo.end };
  let p = parseUpstream(shopeeFbsContract, await deps.client.get("shopeeFbs", base));
  let naoCobertasPedidas: string[] = [];
  if (input.marcas) {
    naoCobertasPedidas = input.marcas.filter((b) => !p.meta.covered_brands.includes(b));
    const cobertas = input.marcas.filter((b) => p.meta.covered_brands.includes(b));
    if (cobertas.length > 0) {
      p = parseUpstream(
        shopeeFbsContract,
        await deps.client.get("shopeeFbs", { ...base, brands: [...cobertas].sort() }),
      );
    }
    if (cobertas.length === 0) {
      return {
        estado: "sem_dado", escopo: p.meta.scope_label,
        periodo_efetivo: { start: null, end: null },
        gmv_bruto: null, pedidos_criados: null, taxa_cancelamento: null,
        share_fbs_gmv: null, share_fbs_pedidos: null, share_fbs_unidades: null,
        por_marca: [], marcas_nao_cobertas: naoCobertasPedidas, contas_faltando: [],
        frescor: {
          status: p.meta.freshness.freshness_status,
          publicado_em: p.meta.freshness.refreshed_at ?? null,
          ultima_data_da_fonte: p.meta.freshness.source_max_date ?? null,
          dias_fechados_atrasados: numOrNull(p.meta.freshness.closed_days_behind),
        },
        definicao_gmv: safeText(p.meta.gmv_definition, 400) ?? "",
      };
    }
  }

  // Janela sem nenhum dia na serie (ex.: antes do inicio da esteira): a fonte
  // devolve "0" com periodo efetivo nulo. Isso e' ausencia, nao venda zero.
  const semSerie = !p.meta.effective_date_from || p.totals.created_orders === 0;
  const n = (v: number | null) => (semSerie ? null : v);
  return {
    estado: semSerie ? "sem_dado" : "medido",
    escopo: p.meta.scope_label,
    periodo_efetivo: { start: p.meta.effective_date_from ?? null, end: p.meta.effective_date_to ?? null },
    gmv_bruto: n(decOrNull(p.totals.gross_gmv)),
    pedidos_criados: n(p.totals.created_orders),
    taxa_cancelamento: n(numOrNull(p.rates.cancellation_rate)),
    share_fbs_gmv: n(numOrNull(p.shares.share_fbs_gmv)),
    share_fbs_pedidos: n(numOrNull(p.shares.share_fbs_orders)),
    share_fbs_unidades: n(numOrNull(p.shares.share_fbs_units)),
    por_marca: semSerie
      ? []
      : p.by_brand.map((b) => ({
          brand: b.brand,
          gmv_bruto: decOrNull(b.gross_gmv),
          share_fbs_gmv: numOrNull(b.share_fbs_gmv),
          share_fbs_pedidos: numOrNull(b.share_fbs_orders),
        })),
    marcas_nao_cobertas: [...new Set([...p.meta.brands_not_covered, ...naoCobertasPedidas])],
    contas_faltando: p.meta.missing_accounts,
    frescor: {
      status: p.meta.freshness.freshness_status,
      publicado_em: p.meta.freshness.refreshed_at ?? null,
      ultima_data_da_fonte: p.meta.freshness.source_max_date ?? null,
      dias_fechados_atrasados: numOrNull(p.meta.freshness.closed_days_behind),
    },
    definicao_gmv: safeText(p.meta.gmv_definition, 400) ?? "",
  };
}

async function mlFull(
  input: EstoqueInput, periodo: { start: string; end: string }, deps: ToolDeps,
): Promise<MlFullBlock> {
  const p = parseUpstream(
    mlFulfillmentContract,
    await deps.client.get("mlFulfillment", {
      date_from: periodo.start,
      date_to: periodo.end,
      brands: input.marcas ? [...input.marcas].sort().join(",") : undefined,
    }),
  );
  // Nenhum pedido pago no escopo (ex.: marca sem loja no ML): a fonte devolve
  // GMV "0". Isso e' ausencia de registro, nao venda zero.
  const semRegistro = p.paid_orders_total === 0;
  const n = (v: number | null) => (semRegistro ? null : v);
  return {
    estado: semRegistro ? "sem_dado" : "medido",
    estoque: "sem_dado",
    estoque_aplicavel: true,
    estoque_motivo: "fonte_nao_integrada",
    periodo_efetivo: { start: p.date_from, end: p.date_to },
    marcas: p.brands,
    gmv_pago_total: n(decOrNull(p.paid_gmv_total)),
    pedidos_pagos: n(p.paid_orders_total),
    share_full_gmv: n(numOrNull(p.share_full_gmv)),
    share_full_pedidos: n(numOrNull(p.share_full_orders)),
    share_full_unidades: n(numOrNull(p.share_full_units)),
    por_modalidade: semRegistro
      ? []
      : p.by_class.map((c) => ({
          modalidade: c.fulfillment_class,
          gmv_pago: decOrNull(c.paid_gmv),
          pedidos_pagos: c.paid_orders,
          taxa_cancelamento: numOrNull(c.cancellation_rate),
          dias_ate_despacho: c.handling.sample_count > 0 ? numOrNull(c.handling.days_avg) : null,
          dias_ate_entrega: c.delivery.sample_count > 0 ? numOrNull(c.delivery.days_avg) : null,
        })),
    anuncios: {
      so_full: p.listings.full_only,
      misto: p.listings.mixed,
      so_nao_full: p.listings.non_full_only,
      total: p.listings.total,
    },
    oportunidades_migracao: p.migration_opportunities.slice(0, input.limite).map((o) => ({
      item_id: o.item_id,
      brand: o.brand,
      gmv_nao_full: decOrNull(o.non_full_gmv),
      unidades_nao_full: o.non_full_units,
      gmv_full: decOrNull(o.full_gmv),
      classe_anuncio: o.listing_class,
    })),
    qualidade: { completa: p.quality.is_complete, pedidos_sem_envio: p.quality.unmatched_orders },
    frescor: {
      status: p.freshness.freshness_status,
      publicado_em: p.freshness.refreshed_at ?? null,
      ultima_data_da_fonte: p.freshness.source_max_date ?? null,
      load_mode: p.freshness.load_mode,
    },
    notas_da_fonte: safeTexts(p.quality.limitations, 400),
  };
}

export const estoqueTool: OracleTool = {
  name: "torre_estoque_full",
  config: {
    title: "Full e estoque: cobertura, ruptura e prioridades",
    description: [
      "Use para 'o que esta em ruptura ou com estoque baixo no Full', 'cobertura em dias', 'excesso de estoque', 'quanto das vendas sai pelo Full/FBS' e 'quais anuncios do ML migrar para o Full'.",
      "Shopee: shopee_estoque e' a fotografia FISICA no CD (unica fonte de estoque), com itens ordenados por risco (no maximo 50); shopee_fbs e' a participacao FBS nas vendas do periodo.",
      "Mercado Livre: ml_full mede a MODALIDADE do envio (Full x nao-Full), nao estoque. Estoque Full do ML e' aplicavel mas sem fonte integrada (estoque='sem_dado', estoque_motivo='fonte_nao_integrada') — nunca diga que o estoque ML e' zero nem use o share Full como estoque.",
      "Cada item e bloco declara estado: zero_real (mediu zero), sem_dado (sem medicao), nao_conciliado (kits) ou nao_aplicavel. Nenhum dos tres ultimos vira zero.",
      "Cobertura e classificacao sao calculo da Torre com limiares PROVISORIOS, nao numero oficial da Shopee. Marcas fora da esteira vem em marcas_nao_cobertas e NAO estao nos totais.",
      "Escopo sem nenhum pedido no periodo (FBS ou Full ML) sai como sem_dado, de proposito: a fonte devolve 0 tanto para marca sem loja quanto para janela sem serie, e o conector nao afirma venda zero que nao consegue distinguir de ausencia.",
      "Filtros apenas por enums (canal, marca, classificacao, severidade). Periodo de dias fechados, ate 92 dias.",
    ].join(" "),
    inputSchema: estoqueInput,
    outputSchema: estoqueOutput,
  },
  async run(args, deps) {
    const input = estoqueInput.parse(args);
    const periodo = resolveClosedPeriod(input, deps.now());
    const canais = input.canais ?? ["ml", "shopee"];

    const [estoque, fbs, ml] = await Promise.all([
      canais.includes("shopee") ? shopeeEstoque(input, deps) : null,
      canais.includes("shopee") ? shopeeFbs(input, periodo, deps) : null,
      canais.includes("ml") ? mlFull(input, periodo, deps) : null,
    ]);

    const limitations: Limitation[] = [];
    if (ml) limitations.push(STOCK_ML_NOT_STOCK);
    if (estoque?.disponibilidade === "available") limitations.push(STOCK_TOWER_COVERAGE);
    if ((estoque?.marcas_nao_cobertas.length ?? 0) > 0 || (fbs?.marcas_nao_cobertas.length ?? 0) > 0) {
      limitations.push(STOCK_PARTIAL_SCOPE);
    }

    const warnings: string[] = [];
    const pedidasFora = (input.marcas ?? []).filter(
      (b) => estoque?.marcas_nao_cobertas.includes(b) || fbs?.marcas_nao_cobertas.includes(b),
    );
    if (pedidasFora.length > 0) {
      warnings.push(`Shopee: ${pedidasFora.join(", ")} fora da esteira API — nao estao nos totais (ausencia, nao estoque ou venda zero).`);
    }
    if (estoque?.disponibilidade === "unavailable") {
      warnings.push(`Estoque Shopee indisponivel: ${estoque.motivo_indisponivel ?? "sem motivo"} — nao trate como estoque zero.`);
    }
    const dias = estoque?.fotografia?.dias_desde_a_fotografia ?? null;
    if (dias !== null && dias >= 1) {
      warnings.push(`A fotografia de estoque da Shopee tem ${dias} dia(s): o estoque atual pode ser outro.`);
    }
    if (fbs && fbs.frescor.status !== "fresh") warnings.push(`Shopee FBS: frescor '${fbs.frescor.status}'.`);
    if (fbs && (fbs.frescor.dias_fechados_atrasados ?? 0) > 0) {
      warnings.push(`Shopee FBS: a fonte esta ${fbs.frescor.dias_fechados_atrasados} dia(s) fechado(s) atras.`);
    }
    if (fbs?.estado === "sem_dado") warnings.push("Shopee FBS: sem serie no periodo/escopo — ausencia, nao venda zero.");
    if (ml && ml.frescor.status !== "fresh") warnings.push(`Mercado Livre Full: frescor '${ml.frescor.status}'.`);
    if (ml?.estado === "sem_dado") warnings.push("Mercado Livre Full: nenhum pedido pago no escopo — ausencia, nao venda zero.");

    const stamps = [estoque?.fotografia?.publicada_em, fbs?.frescor.publicado_em, ml?.frescor.publicado_em]
      .filter((s): s is string => typeof s === "string").sort();
    const itens = estoque?.itens.length ?? 0;

    const env = buildEnvelope({
      period: { start: periodo.start, end: periodo.end, inclusive: true },
      filtersApplied: {
        canais,
        marcas: input.marcas ?? null,
        classificacoes: input.classificacoes ?? null,
        severidade: input.severidade,
        busca: null,
      },
      metricDefinition:
        "Estoque Full Shopee = soma do estoque VENDAVEL no CD; cobertura = vendavel / media diaria de 28 dias (calculo da Torre). Shares FBS/Full = participacao da modalidade no GMV, pedidos ou unidades do periodo. O periodo vale para FBS e Full ML; o estoque e' a ultima fotografia.",
      refreshedAt: stamps[0] ?? null,
      coverage: `ate ${input.limite} itens de maior risco do estoque Shopee; Shopee na cobertura da esteira API`,
      limit: input.limite,
      returnedCount: itens,
      totalCount: estoque?.itens_no_filtro ?? null,
      warnings,
      data: { shopee_estoque: estoque, shopee_fbs: fbs, ml_full: ml, limitations },
    });

    const partes: string[] = [];
    if (estoque?.indicadores) {
      partes.push(`Estoque Shopee: ${estoque.indicadores.produtos_ruptura} em ruptura, ${estoque.indicadores.produtos_baixo} baixo, ${estoque.indicadores.produtos_excesso} em excesso (limiares provisorios)`);
    } else if (estoque) {
      partes.push("Estoque Shopee indisponivel");
    }
    if (ml) partes.push("estoque Full do Mercado Livre sem fonte integrada (ausente, nao zero; o share Full mede modalidade, nao estoque)");
    return ok(`Full e estoque. ${partes.join("; ")}.`, env);
  },
};
