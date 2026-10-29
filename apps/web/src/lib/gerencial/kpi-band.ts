/**
 * Faixa de cinco KPIs da Gerencial V2 (Gate V2-1, Task D).
 *
 * Contrato fechado no Gate V2-0: GMV, Pedidos, Ticket Medio, Investimento em
 * Ads e ROAS por canal. **Confianca no dado NAO e' KPI** — vive numa faixa
 * transversal propria (ver `confidence-strip.ts`).
 *
 * A regra que este modulo existe para impedir: **delta inventado**. O delta so'
 * existe quando o periodo anterior veio no contrato (`previous: KpiSummary`) e
 * e' positivo; sem ele, o card declara "Comparacao indisponivel".
 * KPIS-COMPARACAO-1: Pedidos, Ticket, Ads e ROAS por canal passaram a usar o
 * `previous` que a API ja' devolvia (antes so' o GMV lia). Investimento em Ads
 * tem tom NEUTRO: gastar mais nao e' bom nem ruim por si. E ROAS **nunca** e' consolidado: sem soma, sem
 * media, sem "ROAS total".
 */
import type { OverviewData } from "../api-client.ts";
import { isMarketplaceSelected } from "../marketplace-filter.ts";
import type { Marketplace } from "../mock-data.ts";
import { fmtBrlFull, fmtNumber, fmtBrlCurto, fmtNumCurto, fmtVezes } from "../formatters.ts";

export type KpiKey = "gmv" | "orders" | "avg_ticket" | "ad_spend" | "roas";

/** Canais com investimento em midia COBERTO pelo mart hoje. */
export const ADS_CHANNELS: readonly Marketplace[] = ["ml", "shopee"];
/** Ads do TikTok e' aplicavel, mas a fonte ainda nao foi integrada: ausente,
 *  nunca zero — e a comissao de afiliado nao preenche este lugar. */
export const TIKTOK_ADS_UNAVAILABLE = "TikTok Shop: Ads sem fonte integrada (ausente, não zero)";
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
  /** Valor do periodo anterior, formatado ("12.6x"); null sem comparacao. */
  previous?: string | null;
  /** Sentido da variacao contra o anterior; null sem comparacao. */
  direction?: "up" | "down" | "flat" | null;
}

/** Como colorir a variacao: subir e' bom, ou nao ha' juizo (neutro). */
export type DeltaTone = "higher_is_better" | "neutral";

/** Variacao % quando ha' base: anterior positivo e finito; senao null. */
export function deltaPct(current: number | null | undefined, previous: number | null | undefined): number | null {
  if (current == null || previous == null || !Number.isFinite(current) || !Number.isFinite(previous)) return null;
  if (previous <= 0) return null;
  return ((current - previous) / previous) * 100;
}

/** "flat" abaixo de 0,5% de variacao: ruido de arredondamento, nao movimento. */
export function direcao(pct: number | null): "up" | "down" | "flat" | null {
  if (pct == null) return null;
  if (Math.abs(pct) < 0.5) return "flat";
  return pct > 0 ? "up" : "down";
}

export interface KpiDescriptor {
  key: KpiKey;
  label: string;
  /** Valor principal formatado (curto). `null` => renderizar como N/D com motivo. */
  value: string | null;
  /** UI-DENSIDADE-1: o valor EXATO, mostrado no detalhe do card. */
  exactValue: string | null;
  unavailableReason: string | null;
  subvalue: string | null;
  /** Variacao % contra o periodo anterior; null sem base. */
  deltaPct: number | null;
  /** Como colorir `deltaPct`. */
  deltaTone: DeltaTone;
  /** "vs. período anterior: X" / "antes X". */
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
  /** "Comparar com periodo anterior" desligado: nenhum delta alem do GMV. */
  compare = true,
): KpiDescriptor[] {
  const mlSelected = isMarketplaceSelected(channels, "ml");
  const shopeeSelected = isMarketplaceSelected(channels, "shopee");
  const anyAdsChannel = mlSelected || shopeeSelected;

  const gmv: KpiDescriptor = {
    key: "gmv",
    label: "GMV",
    value: overview ? fmtBrlCurto(overview.gmv) : null,
    exactValue: overview ? fmtBrlFull(overview.gmv) : null,
    unavailableReason: null,
    subvalue: overview ? splitByChannel(overview, channels) : null,
    deltaPct: overview?.gmv_mom_pct ?? null,
    deltaTone: "higher_is_better",
    reference:
      overview && overview.prev_gmv > 0
        ? `vs. período anterior: ${fmtBrlFull(overview.prev_gmv)}`
        : null,
    comparisonNote:
      overview && !(overview.prev_gmv > 0) ? COMPARISON_UNAVAILABLE : null,
    coverageNote: null,
    channelValues: null,
    highlightChannel: null,
    accent: "bg-chart-1",
  };

  const ordersDelta = compare ? deltaPct(overview?.orders, overview?.prev_orders) : null;
  const orders: KpiDescriptor = {
    key: "orders",
    label: "Pedidos",
    value: overview ? fmtNumCurto(overview.orders) : null,
    exactValue: overview ? overview.orders.toLocaleString("pt-BR") : null,
    unavailableReason: null,
    subvalue: overview ? buyersSubvalue(overview) : null,
    deltaPct: ordersDelta,
    deltaTone: "higher_is_better",
    reference: ordersDelta != null ? `antes ${fmtNumCurto(overview!.prev_orders!)}` : null,
    comparisonNote: compare && ordersDelta == null ? COMPARISON_UNAVAILABLE : null,
    coverageNote: null,
    channelValues: null,
    highlightChannel: null,
    accent: "bg-chart-2",
  };

  const ticket = overview ? avgTicketDisplay(overview) : { value: null, reason: null };
  const ticketDelta = compare && ticket.value ? deltaPct(overview?.avg_ticket, overview?.prev_avg_ticket) : null;
  const avgTicket: KpiDescriptor = {
    key: "avg_ticket",
    label: "Ticket Médio",
    value: ticket.value,
    exactValue: ticket.value,
    unavailableReason: ticket.reason,
    subvalue: null,
    deltaPct: ticketDelta,
    deltaTone: "higher_is_better",
    reference: ticketDelta != null ? `antes ${fmtBrlFull(overview!.prev_avg_ticket!)}` : null,
    comparisonNote: compare && ticket.value && ticketDelta == null ? COMPARISON_UNAVAILABLE : null,
    coverageNote: null,
    channelValues: null,
    highlightChannel: null,
    accent: "bg-chart-4",
  };

  let adsValue: string | null = null;
  let adsReason: string | null = null;
  if (!anyAdsChannel) {
    adsReason = "Nenhum canal com investimento em mídia selecionado.";
  } else if (!overview || overview.ad_spend == null) {
    adsReason = "Investimento não disponível para o período e filtros selecionados.";
  } else {
    adsValue = fmtBrlCurto(overview.ad_spend);
  }
  const adsDelta = compare && adsValue ? deltaPct(overview?.ad_spend, overview?.prev_ad_spend) : null;
  const adSpend: KpiDescriptor = {
    key: "ad_spend",
    label: "Investimento em Ads",
    value: adsValue,
    exactValue: adsValue && overview?.ad_spend != null ? fmtBrlFull(overview.ad_spend) : null,
    unavailableReason: adsReason,
    subvalue: null,
    deltaPct: adsDelta,
    deltaTone: "neutral",
    reference: adsDelta != null ? `antes ${fmtBrlCurto(overview!.prev_ad_spend!)}` : null,
    comparisonNote: null,
    coverageNote: adsCoverageNote(channels),
    channelValues: null,
    highlightChannel: null,
    accent: "bg-chart-5",
  };

  const roasChannels: ChannelValue[] = [];
  for (const channel of ["ml", "shopee"] as const) {
    if (!isMarketplaceSelected(channels, channel)) continue;
    const raw = channel === "ml" ? overview?.ml_roas : overview?.shopee_roas;
    const anterior = channel === "ml" ? overview?.prev_ml_roas : overview?.prev_shopee_roas;
    const pct = compare && raw != null ? deltaPct(raw, anterior) : null;
    roasChannels.push({
      channel,
      label: CHANNEL_LABEL[channel],
      value: raw != null ? fmtVezes(raw) : null,
      unavailableReason: raw != null ? undefined : "Sem ROAS para o período e filtros selecionados.",
      previous: pct != null ? fmtVezes(anterior!) : null,
      direction: direcao(pct),
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
    exactValue: null,
    unavailableReason: anyAdsChannel
      ? compatibleWithValue.length === 0
        ? "Sem ROAS disponível nos canais selecionados."
        : null
      : "Nenhum canal com ROAS selecionado.",
    subvalue: null,
    deltaPct: null,
    deltaTone: "higher_is_better",
    reference: null,
    comparisonNote: null,
    coverageNote: null,
    channelValues: roasChannels.length ? roasChannels : null,
    highlightChannel: compatibleWithValue.length === 1 ? compatibleWithValue[0].channel : null,
    accent: compatibleWithValue.length ? "bg-chart-3" : "bg-line",
  };

  return [gmv, orders, avgTicket, adSpend, roas];
}
