/**
 * DRE-UX-1 — fixtures SINTETICAS de `GET /api/v1/dre`.
 *
 * Nenhum numero aqui e' real: valores redondos, escolhidos para a conta
 * fechar de cabeca. O formato segue `apps/api/app/schemas/dre.py`.
 */
import type {
  CelulaDRE,
  Classificacao,
  Componente,
  DreResponse,
  EstadoInsumos,
  NomeComponente,
  StatusComponente,
} from "../src/lib/dre.ts";

export function comp(
  valor: number | null,
  status: StatusComponente,
  extra: Partial<Componente> = {},
): Componente {
  return {
    valor,
    pct_receita: null,
    status,
    motivo: null,
    fonte: null,
    somavel: true,
    partes: null,
    data_as_of: null,
    publicado_em: null,
    ...extra,
  };
}

const INDISP_DEVOL = comp(null, "indisponivel", { motivo: "sem fonte de devolucao/reembolso em valor (perguntas C6, F5)" });
const INDISP_IMPOSTO = comp(null, "indisponivel", { motivo: "fonte candidata nao ratificada (pergunta F8)" });

interface Opts {
  canal?: string | null;
  marca?: string | null;
  classificacao: Classificacao;
  motivos?: string[];
  comps: Partial<Record<NomeComponente, Componente>>;
  margem: number | null;
  usados: string[];
  faltam: string[];
  aplicaveis?: number;
}

/** Monta uma celula e preenche `pct_receita` como o backend faz. */
export function celula(o: Opts): CelulaDRE {
  const comps = {
    devolucoes: INDISP_DEVOL,
    impostos: INDISP_IMPOSTO,
    afiliados: comp(null, "nao_aplicavel", { somavel: false }),
    ...o.comps,
  } as Record<NomeComponente, Componente>;
  const receita = comps.receita.valor;
  for (const k of Object.keys(comps) as NomeComponente[]) {
    const v = comps[k].valor;
    comps[k] = { ...comps[k], pct_receita: v !== null && receita ? Math.round((v / receita) * 10000) / 100 : null };
  }
  const aplicaveis = o.aplicaveis ?? 6;
  return {
    canal: o.canal ?? null,
    canal_label: o.canal ? { tiktok: "TikTok Shop", ml: "Mercado Livre", shopee: "Shopee" }[o.canal] ?? null : null,
    marca: o.marca ?? null,
    marca_label: o.marca ? o.marca.toUpperCase() : null,
    mes: "2026-07",
    classificacao: o.classificacao,
    motivos_classificacao: o.motivos ?? [],
    avisos_qualidade: [],
    componentes: comps,
    componentes_disponiveis: [],
    componentes_ausentes: [],
    completude: { usados: o.usados.length, aplicaveis, texto: `${o.usados.length} de ${aplicaveis} custos` },
    margem_contribuicao_conhecida: {
      valor: o.margem,
      pct_receita: o.margem !== null && receita ? Math.round((o.margem / receita) * 10000) / 100 : null,
      rotulo: "Margem de contribuição conhecida",
      formula: o.margem !== null ? ["receita", ...o.usados].join(" − ") : null,
      componentes_usados: o.margem !== null ? o.usados : [],
      faltam: o.faltam,
    },
    data_maxima: {
      receita_ultimo_dia: "2026-07-31",
      mart_carregado_em: "2026-10-13T09:00:00+00:00",
      cmv_ultima_emissao: "2026-07-31",
      insumo_publicado_em: "2026-10-13T06:00:00+00:00",
    },
  };
}

/** 1. ML calculavel: receita 100k, custos 58k, margem 42k (42%). */
export const ML = celula({
  canal: "ml",
  classificacao: "PARCIAL",
  comps: {
    receita: comp(100_000, "disponivel", { somavel: false, fonte: "marts.fact_marketplace_daily_performance.gmv" }),
    taxas: comp(15_000, "disponivel", { fonte: "marts.fact_marketplace_daily_performance.total_fees" }),
    ads: comp(5_000, "disponivel"),
    frete: comp(8_000, "disponivel"),
    cmv: comp(30_000, "disponivel", { publicado_em: "2026-10-13T06:00:00+00:00", data_as_of: "2026-07-31" }),
  },
  margem: 42_000,
  usados: ["taxas", "ads", "frete", "cmv"],
  faltam: ["devolucoes", "impostos"],
});

/**
 * 2. TikTok calculavel com afiliado NAO somavel: afiliado 3k esta DENTRO das
 * taxas de 12k. Margem = 60k − 12k − 4k − 20k = 24k. Somar o afiliado de novo
 * daria 21k.
 */
export const TIKTOK = celula({
  canal: "tiktok",
  classificacao: "PARCIAL",
  comps: {
    receita: comp(60_000, "disponivel", { somavel: false }),
    taxas: comp(12_000, "disponivel"),
    afiliados: comp(3_000, "disponivel", {
      somavel: false,
      partes: { creator: 2_000, partner: 500, ads: 500 },
      motivo: "ja incluido em taxas (contrato §6); decomposicao, nao custo adicional",
    }),
    ads: comp(null, "indisponivel", { motivo: "sem fonte de Ads do TikTok no mart" }),
    frete: comp(4_000, "disponivel"),
    cmv: comp(20_000, "disponivel"),
  },
  margem: 24_000,
  usados: ["taxas", "frete", "cmv"],
  faltam: ["devolucoes", "ads", "impostos"],
});

/** 3. Shopee nao calculavel: taxa nao confirmada, CMV nao reconcilia (com valor). */
export const SHOPEE = celula({
  canal: "shopee",
  classificacao: "NAO_CALCULAVEL",
  motivos: ["taxas nao confirmadas (pergunta C4)", "CMV nao reconcilia (razao 0,62)"],
  comps: {
    receita: comp(40_000, "disponivel", { somavel: false }),
    taxas: comp(5_000, "nao_confirmado", { motivo: "sem contraprova independente do repasse (pergunta C4)" }),
    afiliados: comp(null, "indisponivel", { somavel: false, motivo: "nao verificado (pergunta C3)" }),
    ads: comp(1_000, "disponivel"),
    frete: comp(null, "indisponivel", { motivo: "frete da Shopee ausente no mart" }),
    cmv: comp(9_000, "nao_reconciliado", { motivo: "receita fiscal / GMV fora de [0,95; 1,05]" }),
  },
  margem: null,
  usados: [],
  faltam: ["devolucoes", "taxas", "frete", "cmv", "impostos"],
});

/** 5. Custo igual a zero MEDIDO (Ads do ML = 0 disponivel). */
export const ML_ADS_ZERO = celula({
  canal: "ml",
  classificacao: "PARCIAL",
  comps: {
    receita: comp(50_000, "disponivel", { somavel: false }),
    taxas: comp(7_500, "disponivel"),
    ads: comp(0, "disponivel"),
    frete: comp(4_000, "disponivel"),
    cmv: comp(15_000, "disponivel"),
  },
  margem: 23_500,
  usados: ["taxas", "ads", "frete", "cmv"],
  faltam: ["devolucoes", "impostos"],
});

/** 7. Total agregado: pior classificacao (Shopee) manda. */
export const TOTAL_MISTO = celula({
  classificacao: "NAO_CALCULAVEL",
  motivos: ["2026-07 shopee kokeshi: taxas nao confirmadas (pergunta C4)"],
  comps: {
    receita: comp(200_000, "disponivel", { somavel: false }),
    taxas: comp(32_000, "parcial", { motivo: "disponivel em celulas que somam 80% da receita" }),
    ads: comp(6_000, "parcial"),
    frete: comp(12_000, "parcial"),
    cmv: comp(59_000, "parcial"),
  },
  margem: null,
  usados: [],
  faltam: ["devolucoes", "taxas", "ads", "frete", "cmv", "impostos"],
});

// --- marcas (por_marca) -----------------------------------------------------

/** Mesmo conjunto de custos (com Ads) — comparaveis entre si. */
export const MARCA_A = celula({
  marca: "barbours",
  classificacao: "PARCIAL",
  comps: {
    receita: comp(80_000, "disponivel", { somavel: false }),
    taxas: comp(12_000, "disponivel"),
    ads: comp(4_000, "parcial"),
    frete: comp(6_000, "disponivel"),
    cmv: comp(34_000, "disponivel"),
  },
  margem: 24_000, // 30%
  usados: ["taxas", "ads", "frete", "cmv"],
  faltam: ["devolucoes", "ads", "impostos"],
});

export const MARCA_B = celula({
  marca: "kokeshi",
  classificacao: "PARCIAL",
  comps: {
    receita: comp(60_000, "disponivel", { somavel: false }),
    taxas: comp(9_000, "disponivel"),
    ads: comp(3_000, "parcial"),
    frete: comp(5_000, "disponivel"),
    cmv: comp(22_000, "disponivel"),
  },
  margem: 21_000, // 35%
  usados: ["taxas", "ads", "frete", "cmv"],
  faltam: ["devolucoes", "ads", "impostos"],
});

/** NAO_CALCULAVEL — nunca entra em ranking. */
export const MARCA_C = celula({
  marca: "apice",
  classificacao: "NAO_CALCULAVEL",
  motivos: ["CMV nao reconcilia (razao 0,62)"],
  comps: {
    receita: comp(40_000, "disponivel", { somavel: false }),
    taxas: comp(6_000, "disponivel"),
    ads: comp(null, "indisponivel"),
    frete: comp(3_000, "disponivel"),
    cmv: comp(null, "nao_reconciliado"),
  },
  margem: null,
  usados: [],
  faltam: ["devolucoes", "ads", "cmv", "impostos"],
});

/**
 * Margem % MAIOR (50%), mas sem Ads na conta (so' TikTok): nao pode
 * aparecer como "melhor margem" contra marcas que descontaram Ads.
 */
export const MARCA_D = celula({
  marca: "lescent",
  classificacao: "PARCIAL",
  comps: {
    receita: comp(20_000, "disponivel", { somavel: false }),
    taxas: comp(3_000, "disponivel"),
    ads: comp(null, "indisponivel"),
    frete: comp(1_000, "disponivel"),
    cmv: comp(6_000, "disponivel"),
  },
  margem: 10_000,
  usados: ["taxas", "frete", "cmv"],
  faltam: ["devolucoes", "ads", "impostos"],
});

export function resposta(over: Partial<DreResponse> = {}, estado: EstadoInsumos = "ok"): DreResponse {
  return {
    periodo: { date_from: "2026-07-01", date_to: "2026-07-31", meses: ["2026-07"], origem: "padrao_competencia_madura" },
    filtros: { canais: ["tiktok", "ml", "shopee"], marcas: ["apice", "barbours", "kokeshi", "lescent"] },
    fontes: {
      neon: "ok",
      insumos_dre: {
        estado,
        publicado_em: estado === "tabela_ausente" || estado === "nao_publicado" ? null : "2026-10-10T06:00:00+00:00",
        publicado_em_mais_antigo: null,
        publicado_em_mais_recente: null,
        motivo: estado === "ok" ? null : estado === "vencido" ? "12 de 12 linhas publicadas ha mais de 54 h" : "motivo do backend",
      },
    },
    regras: { maturacao_meses: 2 },
    total: TOTAL_MISTO,
    por_canal: [TIKTOK, ML, SHOPEE],
    por_marca: [MARCA_A, MARCA_B, MARCA_C, MARCA_D],
    por_canal_marca: [],
    celulas_base: [TIKTOK, ML, SHOPEE],
    ...over,
  };
}
