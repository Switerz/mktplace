// Metadados e decomposicao dos 4 KPIs com drill-down agregado na Gerencial
// (Gate U2 — ver docs/UI_REVAMP_PLAN.md Task 4/8). Modulo puro (sem React),
// mesmo padrao de canais-channel-metrics.ts/executive-summary.ts — usa
// apenas os contratos ja existentes (OverviewData/BrandRow), nunca inventa
// metrica ou grao de dado novo.

import type { BrandRow, OverviewData } from "./api-client";
import type { Marketplace } from "./mock-data";
import { isMarketplaceSelected } from "./marketplace-filter.ts";

/** `ad_spend` entrou no Gate V2-1: a faixa da Gerencial passou de 4 para 5
 * KPIs (GMV, Pedidos, Ticket Medio, Investimento em Ads e ROAS por canal). A
 * extensao e' aditiva — nenhum consumidor existente precisou mudar. */
export type KpiKind = "gmv" | "orders" | "avg_ticket" | "ad_spend" | "roas";

export interface KpiMeta {
  label: string;
  definition: string;
  formula: string;
  caveat?: string;
  nextLabel: string;
  nextHref: string;
}

/** Explicacoes refletem o codigo vigente de calculo de GMV por canal (ver
 * pipelines/connectors/{tiktok,shopee}, gold.ml_gestao_diaria) — nao a
 * definicao generica de docs/kpi_dictionary.md, que nao distingue a regra
 * por canal. */
export const KPI_META: Record<KpiKind, KpiMeta> = {
  gmv: {
    label: "GMV Total",
    definition:
      "Soma do faturamento bruto dos canais selecionados. TikTok Shop soma o subtotal dos pedidos elegíveis (concluídos, entregues ou em trânsito). Mercado Livre soma o valor total dos pedidos cujo status atual é \"pago\". Shopee soma as vendas do período menos vendas canceladas e menos vendas devolvidas ou reembolsadas.",
    formula: "GMV = GMV TikTok Shop + GMV Mercado Livre + GMV Shopee",
    nextLabel: "Ver detalhamento por canal em Canais",
    nextHref: "/canais",
  },
  orders: {
    label: "Pedidos",
    definition: "Contagem de pedidos no período, somada entre os canais selecionados.",
    formula: "Pedidos = pedidos TikTok Shop + pedidos Mercado Livre + pedidos Shopee",
    nextLabel: "Ver detalhamento por canal em Canais",
    nextHref: "/canais",
  },
  avg_ticket: {
    label: "Ticket Médio",
    definition: "Valor médio por pedido no período, calculado sobre o GMV e os pedidos consolidados dos canais selecionados.",
    formula: "Ticket Médio = GMV / Pedidos",
    caveat: "Quando não há pedidos no período, o ticket médio fica indisponível — nunca é exibido como zero ou como erro de cálculo.",
    nextLabel: "Ver detalhamento por canal em Canais",
    nextHref: "/canais",
  },
  ad_spend: {
    label: "Investimento em Ads",
    definition:
      "Investimento em mídia no período, somado nos canais que reportam anúncios. A cobertura é de Mercado Livre e Shopee.",
    formula: "Investimento = investimento Mercado Livre + investimento Shopee",
    caveat:
      "TikTok Shop não reporta investimento em mídia nesta fonte, então não entra na soma e não é exibido como zero. O contrato não traz o investimento do período anterior, portanto este indicador não tem variação.",
    nextLabel: "Ver detalhamento de mídia em Financeiro",
    nextHref: "/financeiro",
  },
  roas: {
    label: "ROAS por canal",
    definition:
      "Receita atribuída a anúncios dividida pelo investimento em mídia, apurada por canal — disponível hoje para Mercado Livre e Shopee.",
    formula: "ROAS = receita atribuída a anúncios / investimento em anúncios",
    caveat:
      "Cada canal tem seu próprio ROAS e nenhum valor consolidado é exibido: somar ou tirar média de ROAS entre canais produziria um número sem significado. TikTok Shop não tem ROAS no contrato atual.",
    nextLabel: "Ver detalhamento de mídia em Financeiro",
    nextHref: "/financeiro",
  },
};

const CHANNEL_LABEL: Record<Marketplace, string> = {
  tiktok: "TikTok Shop",
  ml: "Mercado Livre",
  shopee: "Shopee",
};

export interface ChannelShare {
  channel: Marketplace;
  label: string;
  /** null quando o canal esta selecionado mas o contrato nao reporta valor
   * (indisponivel) — nunca convertido silenciosamente em zero. Um zero
   * numerico real (canal selecionado, contrato reporta 0) fica distinto,
   * com `value: 0`. */
  value: number | null;
  /** null quando o proprio valor e indisponivel, ou quando o total dos
   * canais numericos selecionados e zero — nunca NaN/Infinity. */
  pct: number | null;
}

/**
 * Decompoe o GMV total por canal, usando somente os campos ja carregados em
 * OverviewData e a selecao de marketplace atual (Finding 3, rodada de
 * correcao U2). Duas ambiguidades distintas de `null` sao resolvidas
 * separadamente:
 * - canal NAO selecionado -> omitido da lista (nunca aparece);
 * - canal SELECIONADO com valor `null` -> permanece na lista com
 *   `value: null` ("Sem dado" na UI) — nunca vira R$ 0 fabricado.
 * O percentual so existe para valores numericos com denominador positivo
 * (soma dos canais selecionados que tem valor numerico). A soma dos
 * `value` numericos retornados sempre reconcilia com a soma dos canais
 * selecionados e numericos de `overview`.
 */
export function gmvChannelBreakdown(overview: OverviewData, selection: readonly Marketplace[]): ChannelShare[] {
  const entries: { channel: Marketplace; value: number | null }[] = [
    { channel: "tiktok", value: overview.tiktok_gmv },
    { channel: "ml", value: overview.ml_gmv },
    { channel: "shopee", value: overview.shopee_gmv },
  ];
  const selected = entries.filter((e) => isMarketplaceSelected(selection, e.channel));
  const numericTotal = selected.reduce((s, e) => s + (e.value ?? 0), 0);
  return selected.map((e) => ({
    channel: e.channel,
    label: CHANNEL_LABEL[e.channel],
    value: e.value,
    pct: e.value != null && numericTotal > 0 ? (e.value / numericTotal) * 100 : null,
  }));
}

export interface BrandShare {
  brand: string;
  label: string;
  value: number;
  pct: number | null;
}

/** Decompoe o GMV total por marca a partir de BrandRow.total_gmv — mesma
 * regra de reconciliacao e ausencia de NaN/Infinity da decomposicao por
 * canal. */
export function gmvBrandBreakdown(brands: BrandRow[]): BrandShare[] {
  const total = brands.reduce((s, b) => s + b.total_gmv, 0);
  return brands.map((b) => ({
    brand: b.brand,
    label: b.label,
    value: b.total_gmv,
    pct: total > 0 ? (b.total_gmv / total) * 100 : null,
  }));
}

/** Decompoe Pedidos por marca — nao inventa decomposicao por canal, pois
 * BrandRow nao tem contagem de pedidos por canal (ver Task 4, "Pedidos"). */
export function ordersBrandBreakdown(brands: BrandRow[]): BrandShare[] {
  const total = brands.reduce((s, b) => s + b.orders, 0);
  return brands.map((b) => ({
    brand: b.brand,
    label: b.label,
    value: b.orders,
    pct: total > 0 ? (b.orders / total) * 100 : null,
  }));
}

export interface AvgTicketRow {
  brand: string;
  label: string;
  avgTicket: number | null;
}

/** Ticket medio por marca — trata pedidos=0 como indisponivel
 * independentemente do que a API tenha calculado, para nunca exibir
 * NaN/Infinity nem um ticket "fantasma" sem pedido nenhum. */
export function avgTicketBrandBreakdown(brands: BrandRow[]): AvgTicketRow[] {
  return brands.map((b) => ({
    brand: b.brand,
    label: b.label,
    avgTicket: b.orders > 0 ? b.avg_ticket : null,
  }));
}

export interface RoasBreakdown {
  ml: number | null;
  shopee: number | null;
  tiktokAvailable: false;
}

/** ROAS por canal — TikTok e sempre marcado como indisponivel (contrato
 * atual nao tem essa metrica para o canal), nunca um valor inventado. */
export function roasBreakdown(overview: OverviewData): RoasBreakdown {
  return { ml: overview.ml_roas, shopee: overview.shopee_roas, tiktokAvailable: false };
}
