/**
 * Faixa de cinco KPIs da Gerencial V2 (Gate V2-1, Task D).
 *
 * Contrato fechado no Gate V2-0: GMV, Pedidos, Ticket Medio, Investimento em
 * Ads e ROAS por canal. **Confianca no dado NAO e' KPI** — vive numa faixa
 * transversal propria (ver `confidence-strip.ts`).
 *
 * A regra que este modulo existe para impedir: **delta inventado**. Somente o
 * GMV tem comparacao garantida no contrato (`prev_gmv` / `gmv_mom_pct`).
 * Pedidos e Ticket declaram "Comparacao indisponivel"; Investimento em Ads e
 * ROAS nao tem delta nenhum. E ROAS **nunca** e' consolidado: sem soma, sem
 * media, sem "ROAS total".
 */
import type { OverviewData } from "../api-client.ts";
import { isMarketplaceSelected } from "../marketplace-filter.ts";
import type { Marketplace } from "../mock-data.ts";
import { fmtBrlFull, fmtNumber } from "../formatters.ts";

export type KpiKey = "gmv" | "orders" | "avg_ticket" | "ad_spend" | "roas";

/** Canais que possuem investimento em midia no contrato atual. */
export const ADS_CHANNELS: readonly Marketplace[] = ["ml", "shopee"];
export const TIKTOK_ADS_UNAVAILABLE = "TikTok Shop: não disponível nesta fonte";
export const COMPARISON_UNAVAILABLE = "Comparação indisponível";

/**
 * Nota de cobertura de midia derivada ESTRITAMENTE da selecao atual.
 *
 * A versao anterior citava sempre "Mercado Livre e Shopee", mesmo com apenas um
 * deles selecionado — afirmava cobertura de um canal que o usuario nem estava
 * olhando. Aqui a nota lista somente os canais de midia efetivamente
 * selecionados, e o TikTok so' e' declarado `N/D` quando esta na selecao.
 */
export function adsCoverageNote(channels: readonly Marketplace[]): string {
  const covered = ADS_CHANNELS.filter((c) => isMarketplaceSelected(channels, c));
  const tiktokSelected = isMarketplaceSelected(channels, "tiktok");

  const parts: string[] = [];
  if (covered.length > 0) {
    parts.push(`Cobertura: ${covered.map((c) => CHANNEL_LABEL[c]).join(" e ")}`);
  } else {
    // Selecao sem nenhum canal de midia: diz que nao ha cobertura, e nao
    // menciona ML/Shopee — eles nem estao na selecao.
    parts.push("Sem cobertura de mídia na seleção");
  }
  if (tiktokSelected) parts.push(TIKTOK_ADS_UNAVAILABLE);
  return parts.join(" · ");
}

export const CHANNEL_SHORT: Record<Marketplace, string> = {
  tiktok: "TK",
  ml: "ML",
  shopee: "SH",
};

export const CHANNEL_LABEL: Record<Marketplace, string> = {
  tiktok: "TikTok Shop",
  ml: "Mercado Livre",
  shopee: "Shopee",
};

export interface ChannelValue {
  channel: Marketplace;
  label: string;
  /** Texto ja formatado, ou null quando indisponivel nesta fonte. */
  value: string | null;
  unavailableReason?: string;
}

export interface KpiDescriptor {
  key: KpiKey;
  label: string;
  /** Valor principal formatado. `null` => renderizar como N/D com motivo. */
  value: string | null;
  unavailableReason: string | null;
  subvalue: string | null;
  /** Somente GMV: variacao percentual do contrato. */
  deltaPct: number | null;
  /** Somente GMV: "vs. período anterior: R$ X". */
  reference: string | null;
  /** Texto explicito quando o contrato nao traz comparacao. */
  comparisonNote: string | null;
  /** Nota de cobertura (Ads/ROAS). */
  coverageNote: string | null;
  /** ROAS: um valor por canal, jamais agregado. */
  channelValues: ChannelValue[] | null;
  /** ROAS: canal unico compativel selecionado, para destaque. */
  highlightChannel: Marketplace | null;
  accent: string;
}

function splitByChannel(overview: OverviewData, channels: readonly Marketplace[]): string | null {
  const parts: string[] = [];
  const pairs: [Marketplace, number | null][] = [
    ["tiktok", overview.tiktok_gmv],
    ["ml", overview.ml_gmv],
    ["shopee", overview.shopee_gmv],
  ];
  for (const [channel, value] of pairs) {
    if (!isMarketplaceSelected(channels, channel)) continue;
    if (value == null) continue;
    parts.push(`${CHANNEL_SHORT[channel]} ${fmtBrlFull(value)}`);
  }
  return parts.length ? parts.join(" · ") : null;
}

function buyersSubvalue(overview: OverviewData): string | null {
  const total =
    (overview.tiktok_customers ?? 0) +
    (overview.ml_unique_buyers ?? 0) +
    (overview.shopee_unique_buyers ?? 0);
  if (total === 0) return null;
  // Soma diaria, nao comprador unico do periodo — a ressalva do dicionario de
  // KPIs viaja junto com o numero, nunca e' omitida.
  return `${fmtNumber(total)} compradores (soma diária, não único no período)`;
}

/**
 * Ticket medio: usa o valor do contrato, mas protege a exibicao. Zero pedidos
 * ou valor nao finito nunca podem vazar como `NaN`/`Infinity` na interface.
 */
export function avgTicketDisplay(overview: OverviewData): { value: string | null; reason: string | null } {
  if (overview.orders === 0) {
    return { value: null, reason: "Sem pedidos no período — ticket médio não definido." };
  }
  if (!Number.isFinite(overview.avg_ticket)) {
    return { value: null, reason: "Valor não disponível para o período e filtros selecionados." };
  }
  return { value: fmtBrlFull(overview.avg_ticket), reason: null };
}

export function buildKpiBand(
  overview: OverviewData | null,
  channels: readonly Marketplace[],
): KpiDescriptor[] {
  const mlSelected = isMarketplaceSelected(channels, "ml");
  const shopeeSelected = isMarketplaceSelected(channels, "shopee");
  const anyAdsChannel = mlSelected || shopeeSelected;

  const gmv: KpiDescriptor = {
    key: "gmv",
    label: "GMV",
    value: overview ? fmtBrlFull(overview.gmv) : null,
    unavailableReason: null,
    subvalue: overview ? splitByChannel(overview, channels) : null,
    deltaPct: overview?.gmv_mom_pct ?? null,
    reference:
      overview && overview.prev_gmv > 0
        ? `vs. período anterior: ${fmtBrlFull(overview.prev_gmv)}`
        : null,
    comparisonNote:
      overview && !(overview.prev_gmv > 0) ? COMPARISON_UNAVAILABLE : null,
    coverageNote: null,
    channelValues: null,
    highlightChannel: null,
    accent: "bg-violet-600",
  };

  const orders: KpiDescriptor = {
    key: "orders",
    label: "Pedidos",
    value: overview ? fmtNumber(overview.orders) : null,
    unavailableReason: null,
    subvalue: overview ? buyersSubvalue(overview) : null,
    // Sem delta: o contrato nao traz pedidos do periodo anterior.
    deltaPct: null,
    reference: null,
    comparisonNote: COMPARISON_UNAVAILABLE,
    coverageNote: null,
    channelValues: null,
    highlightChannel: null,
    accent: "bg-cyan-500",
  };

  const ticket = overview ? avgTicketDisplay(overview) : { value: null, reason: null };
  const avgTicket: KpiDescriptor = {
    key: "avg_ticket",
    label: "Ticket Médio",
    value: ticket.value,
    unavailableReason: ticket.reason,
    subvalue: null,
    deltaPct: null,
    reference: null,
    comparisonNote: ticket.value ? COMPARISON_UNAVAILABLE : null,
    coverageNote: null,
    channelValues: null,
    highlightChannel: null,
    accent: "bg-amber-500",
  };

  let adsValue: string | null = null;
  let adsReason: string | null = null;
  if (!anyAdsChannel) {
    adsReason = "Nenhum canal com investimento em mídia selecionado.";
  } else if (!overview || overview.ad_spend == null) {
    adsReason = "Investimento não disponível para o período e filtros selecionados.";
  } else {
    adsValue = fmtBrlFull(overview.ad_spend);
  }
  const adSpend: KpiDescriptor = {
    key: "ad_spend",
    label: "Investimento em Ads",
    value: adsValue,
    unavailableReason: adsReason,
    subvalue: null,
    // Sem delta por decisao de contrato.
    deltaPct: null,
    reference: null,
    comparisonNote: null,
    coverageNote: adsCoverageNote(channels),
    channelValues: null,
    highlightChannel: null,
    accent: "bg-emerald-500",
  };

  const roasChannels: ChannelValue[] = [];
  for (const channel of ["ml", "shopee"] as const) {
    if (!isMarketplaceSelected(channels, channel)) continue;
    const raw = channel === "ml" ? overview?.ml_roas : overview?.shopee_roas;
    roasChannels.push({
      channel,
      label: CHANNEL_LABEL[channel],
      value: raw != null ? `${raw.toFixed(1)}x` : null,
      unavailableReason: raw != null ? undefined : "Sem ROAS para o período e filtros selecionados.",
    });
  }
  if (isMarketplaceSelected(channels, "tiktok")) {
    roasChannels.push({
      channel: "tiktok",
      label: CHANNEL_LABEL.tiktok,
      value: null,
      unavailableReason: "Não disponível nesta fonte",
    });
  }
  const compatibleWithValue = roasChannels.filter((c) => c.channel !== "tiktok" && c.value != null);
  const roas: KpiDescriptor = {
    key: "roas",
    label: "ROAS por canal",
    // NUNCA um valor unico agregado: o card renderiza a lista por canal.
    value: null,
    unavailableReason: anyAdsChannel
      ? compatibleWithValue.length === 0
        ? "Sem ROAS disponível nos canais selecionados."
        : null
      : "Nenhum canal com ROAS selecionado.",
    subvalue: null,
    deltaPct: null,
    reference: null,
    comparisonNote: null,
    coverageNote: null,
    channelValues: roasChannels.length ? roasChannels : null,
    highlightChannel: compatibleWithValue.length === 1 ? compatibleWithValue[0].channel : null,
    accent: compatibleWithValue.length ? "bg-violet-500" : "bg-slate-300",
  };

  return [gmv, orders, avgTicket, adSpend, roas];
}
