// Conteúdo dos dois drill-downs mensais da Marca 360 (Gate V3-2; matriz em
// docs/INTELIGENCIA_BRAND_V3_PLAN.md §9, linhas "Marca B6" e "Marca B8").
//
// O módulo devolve dados SEMÂNTICOS, não texto formatado: cada métrica sai
// como `{ kind: "value", n }` ou `{ kind: "missing" }`. Quem formata é o
// componente. Duas razões:
//
// 1. `null ≠ zero` fica testável no nível certo. Zero é medida ("vendeu zero"),
//    ausência é falha de cobertura ("não sabemos") — e um formatador que
//    recebesse `number` já teria apagado a diferença antes do teste;
// 2. nenhuma métrica comercial é recalculada aqui. Todas as taxas exibidas
//    (`ctr_pct`, `cvr_pct`, `gpm`) vêm PRONTAS do backend. Este módulo escolhe
//    o que mostrar e em que ordem; ele não deriva indicador.
//
// Vocabulário, que aqui não é detalhe: `channel_funnel` entrega VIDEO, LIVE e
// PRODUCT_CARD, que são **superfícies do TikTok Shop**, não marketplaces. A copy
// apresentada diz "superfície"; os nomes internos de campo continuam os do
// contrato (`channel`, `channel_funnel`), porque renomeá-los ampliaria o diff
// sem mudar nada para quem lê a tela.
//
// O que este módulo deliberadamente NÃO faz:
// - nenhuma mediana, p75, média ou benchmark entre superfícies. Vídeo, live e
//   product card são superfícies heterogêneas, e sem regra de negócio
//   documentada que demonstre comparabilidade, a mediana das três não é
//   referência de performance — seria número inventado com cara de meta;
// - nenhum share do canal no GMV da marca calculado a partir de outra
//   resposta: o detalhe fala só do próprio canal e da própria marca;
// - nenhum diagnóstico, threshold, recomendação ou previsão.
//
// Módulo puro (sem React).

import type { BrandDetailChannelRow, BrandDetailProduto } from "../api-client.ts";
import { fmtCompetencia } from "./ref-month.ts";

/** Medida presente (inclusive zero) × ausência de cobertura. */
export type MetricValue = { kind: "value"; n: number } | { kind: "missing" };

/** `null`/`undefined`/`NaN` ⇒ ausência. **Zero é valor**, nunca ausência. */
export function metricValue(v: number | null | undefined): MetricValue {
  if (v == null || Number.isNaN(v)) return { kind: "missing" };
  return { kind: "value", n: v };
}

/** Unidade da métrica — orienta a formatação, não a semântica. */
export type MetricUnit = "brl" | "int" | "pct" | "brl2";

export interface DrilldownMetric {
  key: string;
  label: string;
  value: MetricValue;
  unit: MetricUnit;
}

export interface ChannelDrilldown {
  channel: string;
  label: string;
  refMonthLabel: string;
  metrics: DrilldownMetric[];
  /** Limitação obrigatória do escopo. Nunca `null`. */
  note: string;
  ctaLabel: string;
}

/**
 * Detalhe do funil de UM canal TikTok, na competência mensal.
 *
 * Ordem = ordem do funil (impressão → visita → item → GMV), com as duas taxas
 * que o backend já entrega intercaladas onde explicam a passagem.
 */
export function readChannelDetail(row: BrandDetailChannelRow, refMonth: string | null): ChannelDrilldown {
  return {
    channel: row.channel,
    label: row.label,
    refMonthLabel: fmtCompetencia(refMonth),
    metrics: [
      { key: "impressions", label: "Impressões", value: metricValue(row.impressions), unit: "int" },
      { key: "ctr_pct", label: "CTR", value: metricValue(row.ctr_pct), unit: "pct" },
      { key: "page_views", label: "Visitas à página do produto", value: metricValue(row.page_views), unit: "int" },
      { key: "cvr_pct", label: "CVR", value: metricValue(row.cvr_pct), unit: "pct" },
      { key: "items_sold", label: "Itens vendidos", value: metricValue(row.items_sold), unit: "int" },
      { key: "gmv", label: "GMV", value: metricValue(row.gmv), unit: "brl" },
    ],
    note:
      "Só esta superfície e esta marca, na competência mensal. Vídeo, live e product card são " +
      "superfícies heterogêneas: sem regra de negócio documentada que demonstre comparabilidade, " +
      "não há mediana, p75 nem benchmark entre superfícies aqui. As taxas vêm prontas do contrato.",
    ctaLabel: "Abrir TikTok Shop em Canais",
  };
}

/**
 * Ressalva obrigatória do CTA de superfície.
 *
 * `/canais` compara **marketplaces** (TikTok × ML × Shopee). Não existe
 * parâmetro de superfície no contrato de filtros, e este gate não cria um.
 * Rotular o CTA como "comparar canais" sugeria que vídeo × live × product card
 * seguiriam para lá, o que é falso — daí dizer o que NÃO viaja.
 */
export const CHANNEL_CTA_SCOPE_NOTE =
  "A superfície específica não viaja: Canais abre a visão do marketplace TikTok Shop, " +
  "não uma comparação vídeo × live × product card.";

export interface ProductDrilldown {
  productName: string;
  refMonthLabel: string;
  metrics: DrilldownMetric[];
  note: string;
  ctaLabel: string;
}

/**
 * Detalhe de UM produto TikTok, na competência mensal.
 *
 * `gpm` é GMV por mil visualizações e **já vem calculado** pelo backend
 * (`SUM(gmv)/SUM(video_views)*1000`); quando é `null`, a linha diz ausência —
 * jamais reconstruímos a divisão no frontend com views de outra fonte.
 */
export function readProductDetail(p: BrandDetailProduto, refMonth: string | null): ProductDrilldown {
  return {
    productName: p.product_name,
    refMonthLabel: fmtCompetencia(refMonth),
    metrics: [
      { key: "gmv", label: "GMV", value: metricValue(p.gmv), unit: "brl" },
      { key: "orders", label: "Pedidos", value: metricValue(p.orders), unit: "int" },
      { key: "videos", label: "Vídeos ativos", value: metricValue(p.videos), unit: "int" },
      { key: "gpm", label: "GMV por mil visualizações", value: metricValue(p.gpm), unit: "brl2" },
    ],
    note:
      "Competência mensal, agrupado como o contrato entrega. Sem Ads, ROAS, margem ou CMV: " +
      "o contrato do TikTok não traz investimento de mídia nem custo do produto.",
    ctaLabel: "Ver este catálogo em Produtos",
  };
}

/** Destino do detalhe de canal: Canais com marca e canal TikTok fixados. */
export function channelCtaHref(brand: string): string {
  return `/canais?brands=${brand}&channels=tiktok`;
}

/** Destino do detalhe de produto: Produtos na aba TikTok, marca preservada. */
export function productCtaHref(brand: string): string {
  return `/produtos?channels=tiktok&brands=${brand}`;
}

/**
 * Ressalva obrigatória do CTA de produto.
 *
 * `/produtos` **não lê `ref_month`** (o contrato de URL daquela página cobre
 * `channels`, `brands` e `pareto_bucket`), então afirmar que a competência
 * viaja seria falso. Dizer o que NÃO é transportado custa uma linha e evita
 * que o analista leia o intervalo global de Produtos como se fosse o mês.
 */
export const PRODUCT_CTA_SCOPE_NOTE =
  "A competência mensal não viaja neste destino: Produtos usa o intervalo global dos filtros, não o mês.";
