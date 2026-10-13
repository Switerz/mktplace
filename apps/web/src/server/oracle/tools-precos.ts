/**
 * torre_monitoramento_precos — MCP-COVERAGE-2.
 *
 * Quantos anuncios estao abaixo da referencia B2B (PDV), quais sao as maiores
 * violacoes, e quanto do catalogo e' comparavel.
 *
 * REGRAS:
 * - `product_query` (texto livre da API) NAO e' exposto; o adapter o recusa.
 * - Ausencia de referencia nunca vira zero: `no_reference` sai com diferenca
 *   nula. Preco anunciado ausente sai nulo — nunca inventado, e o preco de
 *   checkout (frete/cupom/subsidio) nao existe nesta fonte.
 * - Marca e data sao validadas contra a PROPRIA fotografia (marcas observadas,
 *   datas disponiveis) antes do filtro: a API recusa marca nao observada com
 *   422, e "nao observada" nao e' "zero anuncio".
 * - Os KPIs descrevem o conjunto completo do escopo de marca/data; o filtro de
 *   situacao so' recorta a lista de anuncios (contrato da fonte).
 */
import { z } from "zod";

import { buildEnvelope } from "./envelope.ts";
import { PRICE_ADVERTISED_ONLY, PRICE_REFERENCE_PDV, type Limitation } from "./limitations.ts";
import { BRANDS, isoDate, noDuplicates } from "./schemas.ts";
import { envelopeSchema, limitationSchema, ok, safeText, safeTexts, type OracleTool } from "./tool-kit.ts";
import { numOrNull } from "./torre-client.ts";
import { parseUpstream } from "./upstream.ts";
import { monitoramentoPrecoContract, PRODUCT_TYPES } from "./upstream-operacional.ts";

export const PRECOS_MAX_ANUNCIOS = 50;

const SITUACOES = {
  abaixo_da_referencia: "below_reference",
  no_ou_acima: "at_or_above_reference",
  sem_referencia: "no_reference",
  referencia_ambigua: "non_comparable_reference_ambiguous",
  inativo: "inactive_listing",
} as const;
type Situacao = keyof typeof SITUACOES;
const SITUACAO_KEYS = Object.keys(SITUACOES) as [Situacao, ...Situacao[]];

export const precosInput = z
  .object({
    canal: z.enum(["ml", "shopee", "tiktok"]),
    marcas: noDuplicates(z.array(z.enum(BRANDS)).min(1).max(5), "marcas").optional(),
    situacoes: noDuplicates(z.array(z.enum(SITUACAO_KEYS)).min(1).max(5), "situacoes")
      .default(["abaixo_da_referencia"]),
    tipos_produto: noDuplicates(z.array(z.enum(PRODUCT_TYPES)).min(1).max(4), "tipos_produto").optional(),
    data_observada: isoDate.optional(),
    limite: z.number().int().min(1).max(PRECOS_MAX_ANUNCIOS).default(20),
  })
  .strict();

const anuncioSchema = z.object({
  posicao: z.number(),
  item_id: z.string(),
  brand: z.string(),
  loja: z.string().nullable(),
  produto: z.string().nullable(),
  titulo_anuncio: z.string().nullable(),
  sku: z.string().nullable(),
  tipo_produto: z.string().nullable(),
  situacao: z.string(),
  motivo_nao_comparavel: z.string().nullable(),
  status_anuncio: z.string().nullable(),
  /** null = nao observado. Nunca inventado. */
  preco_anunciado: z.number().nullable(),
  /** null = sem referencia B2B. Nunca 0. */
  referencia_pdv: z.number().nullable(),
  diferenca_valor: z.number().nullable(),
  diferenca_pct: z.number().nullable(),
  metodo_casamento: z.string().nullable(),
  qualidade_casamento: z.string(),
  kit: z
    .object({
      componentes: z.number().nullable(),
      base_componentes: z.number().nullable(),
      desconto_kit_pct: z.number().nullable(),
    })
    .nullable(),
  frescor: z.string(),
});

const precosOutput = envelopeSchema(
  z.object({
    canal: z.string(),
    disponibilidade: z.enum(["available", "unavailable"]),
    motivo_indisponivel: z.string().nullable(),
    modo: z.string().nullable(),
    data_observada: z.string().nullable(),
    data_pedida: z.string().nullable(),
    datas_disponiveis: z.array(z.string()),
    frescor: z.string().nullable(),
    defasagem_dias: z.number().nullable(),
    politica_de_data: z.string().nullable(),
    mutabilidade: z.string().nullable(),
    referencia: z.object({
      tipo: z.string().nullable(),
      capturada_em: z.string().nullable(),
      vigencia: z.string().nullable(),
    }),
    cobertura_de_preco: z.string().nullable(),
    marcas: z.object({
      monitoradas: z.array(z.string()),
      observadas: z.array(z.string()),
      monitoradas_nao_observadas: z.array(z.string()),
      comparaveis: z.array(z.string()),
      sem_referencia: z.array(z.string()),
      fora_de_escopo: z.record(z.string(), z.string()),
    }),
    /** Marcas pedidas que esta fotografia nao serve, com o motivo. */
    marcas_pedidas_fora: z.array(z.object({ brand: z.string(), motivo: z.string() })),
    kpis: z
      .object({
        monitorados: z.number(),
        comparaveis: z.number(),
        abaixo_da_referencia: z.number(),
        no_ou_acima: z.number(),
        sem_referencia: z.number(),
        referencia_ambigua: z.number(),
        inativos: z.number(),
        kits_confirmados: z.number(),
        kits_suspeitos: z.number(),
        kits_com_referencia_derivada: z.number().nullable(),
        taxa_cobertura: z.number().nullable(),
        motivos_sem_referencia: z.record(z.string(), z.number()).nullable(),
      })
      .nullable(),
    anuncios: z.array(anuncioSchema),
    notas_da_fonte: z.array(z.string()),
    limitations: z.array(limitationSchema),
  }),
);

type PrecosData = z.infer<typeof precosOutput>["data"];

export const precosTool: OracleTool = {
  name: "torre_monitoramento_precos",
  config: {
    title: "Monitoramento de precos contra a referencia B2B",
    description: [
      "Use para 'quantos anuncios estao abaixo da referencia', 'quais as maiores violacoes de preco', 'quanto do catalogo e' comparavel' em UM canal (ml, shopee ou tiktok).",
      "Compara o preco ANUNCIADO das lojas proprias com o preco sugerido de revenda (PDV) da tabela B2B, inclusive kits com referencia derivada quando houver.",
      "Os KPIs descrevem o escopo completo (monitorados, comparaveis, abaixo, sem referencia, inativos); 'situacoes' so' recorta a lista de anuncios, que vem ordenada pela maior diferenca negativa (no maximo 50).",
      "Sem referencia NUNCA e' 0%: diferenca nula. Preco de checkout, frete e cupom nao sao medidos e nunca sao inventados.",
      "Marca nao observada na fotografia aparece em marcas_pedidas_fora com o motivo — nao e' 'zero anuncios'. Sem filtro de texto livre.",
    ].join(" "),
    inputSchema: precosInput,
    outputSchema: precosOutput,
  },
  async run(args, deps) {
    const input = precosInput.parse(args);
    const marketplace = input.canal;

    // Preflight: a fotografia diz quais marcas e datas existem. Feito SO' quando
    // o filtro depende disso, e nunca com texto livre.
    let marcas: string[] | undefined;
    const fora: Array<{ brand: string; motivo: string }> = [];
    let datasDisponiveis: string[] = [];
    let dataPedida: string | null = input.data_observada ?? null;
    if (input.marcas || input.data_observada) {
      const pre = parseUpstream(
        monitoramentoPrecoContract,
        await deps.client.get("monitoramentoPreco", { marketplace, limit: 1 }),
      );
      datasDisponiveis = pre.meta.available_observed_dates;
      if (input.data_observada && !datasDisponiveis.includes(input.data_observada)) {
        return ok(
          `Monitoramento de precos (${marketplace}): nao ha observacao em ${input.data_observada}.`,
          vazio(input.canal, "data_sem_observacao", dataPedida, datasDisponiveis, fora,
            `Nao existe observacao de preco em ${input.data_observada} para este canal. Use uma das datas_disponiveis.`),
        );
      }
      if (input.marcas) {
        const observadas = pre.meta.observed_brands ?? [];
        for (const b of input.marcas) {
          if (observadas.includes(b)) continue;
          const oos = pre.meta.out_of_scope_brands[b];
          const naoObs = (pre.meta.monitored_unobserved_brands ?? []).includes(b);
          fora.push({
            brand: b,
            motivo: safeText(oos) ?? (naoObs ? "monitorada_nao_observada_nesta_fotografia" : "nao_monitorada_neste_canal"),
          });
        }
        marcas = input.marcas.filter((b) => observadas.includes(b));
        if (marcas.length === 0) {
          return ok(
            `Monitoramento de precos (${marketplace}): nenhuma das marcas pedidas e' observada nesta fotografia.`,
            vazio(input.canal, "marca_nao_observada", dataPedida, datasDisponiveis, fora,
              "Nenhuma das marcas pedidas tem anuncio observado nesta fotografia. Isso nao e' 'zero anuncios abaixo da referencia'."),
          );
        }
      }
    }

    const p = parseUpstream(
      monitoramentoPrecoContract,
      await deps.client.get("monitoramentoPreco", {
        marketplace,
        brand: marcas ? [...marcas].sort().join(",") : undefined,
        status: input.situacoes.map((s) => SITUACOES[s]).join(","),
        product_type: input.tipos_produto ? [...input.tipos_produto].join(",") : undefined,
        observed_date: input.data_observada,
        limit: input.limite,
      }),
    );
    const m = p.meta;
    if (datasDisponiveis.length === 0) datasDisponiveis = m.available_observed_dates;
    if (dataPedida === null) dataPedida = m.requested_observed_date ?? null;
    const disponivel = (m.availability ?? "available") === "available";

    const anuncios = disponivel
      ? p.rows.slice(0, input.limite).map((r, i) => {
          const kit = r.kit_component_count !== null && r.kit_component_count !== undefined;
          return {
            posicao: i + 1,
            item_id: r.item_id,
            brand: r.brand,
            loja: r.shop_account ?? null,
            produto: r.product_name ?? null,
            titulo_anuncio: r.listing_title ?? null,
            sku: r.seller_sku ?? null,
            tipo_produto: r.product_type ?? null,
            situacao: r.comparison_status,
            motivo_nao_comparavel: r.non_comparable_reason ?? null,
            status_anuncio: r.listing_status ?? null,
            preco_anunciado: numOrNull(r.advertised_price),
            referencia_pdv: numOrNull(r.suggested_retail_amount),
            diferenca_valor: numOrNull(r.difference_amount),
            diferenca_pct: numOrNull(r.difference_pct),
            metodo_casamento: r.match_method ?? null,
            qualidade_casamento: r.match_quality,
            kit: kit
              ? {
                  componentes: numOrNull(r.kit_component_count),
                  base_componentes: numOrNull(r.kit_components_base_amount),
                  desconto_kit_pct: numOrNull(r.kit_discount_pct),
                }
              : null,
            frescor: r.freshness_status,
          };
        })
      : [];

    const limitations: Limitation[] = [PRICE_ADVERTISED_ONLY, PRICE_REFERENCE_PDV];
    const warnings: string[] = [];
    if (!disponivel) {
      warnings.push(`Monitoramento indisponivel (${safeText(m.unavailable_reason) ?? "sem motivo"}) — nenhum numero, nao trate como zero anuncio.`);
    }
    if (m.freshness_status === "stale") {
      warnings.push(`A observacao esta atrasada (${numOrNull(m.lag_days) ?? "?"} dia(s) de defasagem): o sync nao avancou.`);
    }
    if (m.snapshot_mutability === "mutable_operational_snapshot") {
      warnings.push("Fotografia do dia corrente: ainda pode mudar hoje se a origem recarregar.");
    }
    if (fora.length > 0) {
      warnings.push(`Marcas pedidas fora desta fotografia: ${fora.map((f) => `${f.brand} (${f.motivo})`).join(", ")}.`);
    }

    const data: PrecosData = {
      canal: input.canal,
      disponibilidade: disponivel ? "available" : "unavailable",
      motivo_indisponivel: safeText(m.unavailable_reason),
      modo: m.mode,
      data_observada: m.observed_ref_date ?? null,
      data_pedida: dataPedida,
      datas_disponiveis: datasDisponiveis,
      frescor: m.freshness_status,
      defasagem_dias: numOrNull(m.lag_days),
      politica_de_data: m.date_policy ?? null,
      mutabilidade: m.snapshot_mutability ?? null,
      referencia: {
        tipo: m.reference_type,
        capturada_em: m.reference_captured_at ?? null,
        vigencia: m.validity_status,
      },
      cobertura_de_preco: m.coverage_status,
      marcas: {
        monitoradas: m.monitored_brands,
        observadas: m.observed_brands ?? [],
        monitoradas_nao_observadas: m.monitored_unobserved_brands ?? [],
        comparaveis: m.comparable_brands,
        sem_referencia: m.no_reference_brands,
        fora_de_escopo: Object.fromEntries(Object.entries(m.out_of_scope_brands).map(([k, v]) => [k, safeText(v) ?? ""])),
      },
      marcas_pedidas_fora: fora,
      kpis: disponivel
        ? {
            monitorados: p.kpis.monitored_count,
            comparaveis: p.kpis.comparable_count,
            abaixo_da_referencia: p.kpis.below_reference_count,
            no_ou_acima: p.kpis.at_or_above_reference_count,
            sem_referencia: p.kpis.no_reference_count,
            referencia_ambigua: p.kpis.ambiguous_reference_count,
            inativos: p.kpis.inactive_count,
            kits_confirmados: p.metrics.kit_confirmed,
            kits_suspeitos: p.metrics.kit_suspected,
            kits_com_referencia_derivada: numOrNull(p.metrics.kit_reference_derived),
            taxa_cobertura: numOrNull(p.metrics.coverage_rate),
            motivos_sem_referencia: p.metrics.no_reference_breakdown ?? null,
          }
        : null,
      anuncios,
      notas_da_fonte: safeTexts(m.warnings, 400),
      limitations,
    };

    const obs = m.observed_ref_date ?? null;
    const env = buildEnvelope({
      period: obs ? { start: obs, end: obs, inclusive: true } : null,
      filtersApplied: {
        canal: input.canal,
        marcas: marcas ?? null,
        situacoes: input.situacoes,
        tipos_produto: input.tipos_produto ?? null,
        data_observada: input.data_observada ?? null,
        product_query: null,
      },
      metricDefinition:
        "diferenca_pct = (preco anunciado - referencia PDV) / referencia PDV. Abaixo da referencia = diferenca negativa. Comparavel = anuncio ativo com exatamente uma referencia casada.",
      refreshedAt: m.refreshed_at ?? null,
      coverage: `ate ${input.limite} anuncios das situacoes pedidas, da maior diferenca negativa para a menor`,
      limit: input.limite,
      returnedCount: anuncios.length,
      totalCount: disponivel ? p.total_count : null,
      warnings,
      data,
    });

    const k = data.kpis;
    return ok(
      k
        ? `Precos ${input.canal} (${obs ?? "sem data"}): ${k.abaixo_da_referencia} abaixo da referencia de ${k.comparaveis} comparaveis; ${k.sem_referencia} sem referencia (nao e' 0%); ${k.inativos} inativos; ${k.monitorados} monitorados.`
        : `Precos ${input.canal}: indisponivel.`,
      env,
    );
  },
};

/** Resposta sem numero, com o motivo — nunca KPI zerado. */
function vazio(
  canal: string,
  motivo: string,
  dataPedida: string | null,
  datas: string[],
  fora: Array<{ brand: string; motivo: string }>,
  aviso: string,
) {
  const data: PrecosData = {
    canal, disponibilidade: "unavailable", motivo_indisponivel: motivo, modo: null,
    data_observada: null, data_pedida: dataPedida, datas_disponiveis: datas, frescor: null,
    defasagem_dias: null, politica_de_data: null, mutabilidade: null,
    referencia: { tipo: null, capturada_em: null, vigencia: null }, cobertura_de_preco: null,
    marcas: { monitoradas: [], observadas: [], monitoradas_nao_observadas: [], comparaveis: [], sem_referencia: [], fora_de_escopo: {} },
    marcas_pedidas_fora: fora, kpis: null, anuncios: [], notas_da_fonte: [],
    limitations: [PRICE_ADVERTISED_ONLY, PRICE_REFERENCE_PDV],
  };
  return buildEnvelope({
    period: null,
    filtersApplied: { canal, data_observada: dataPedida },
    metricDefinition: "Sem numero: o escopo pedido nao existe nesta fotografia.",
    refreshedAt: null,
    coverage: "nenhum anuncio — escopo nao observado",
    returnedCount: 0,
    warnings: [aviso],
    data,
  });
}
