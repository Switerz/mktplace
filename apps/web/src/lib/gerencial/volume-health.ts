/**
 * "Saude do volume por canal" da Gerencial V2 (Gate V2-1, Task G).
 *
 * Substitui o funil monetario da referencia, que **nao tem lastro** no nosso
 * contrato: nao existe valor monetario cancelado, valor monetario devolvido,
 * GMV anterior as exclusoes, nem etapas monetarias mutuamente exclusivas.
 *
 * O que existe (`/quality`, `apps/api/app/schemas/performance.py:124-161`) sao
 * CONTAGENS e TAXAS por canal. Este modulo produz uma linha descritiva por
 * canal, cada metrica na sua unidade, e nunca:
 *
 * - converte cancelamento/devolucao em reais;
 * - recalcula a taxa com outro denominador (a taxa exibida e' a servida);
 * - trata devolvidos como particao exclusiva do total;
 * - transforma o campo zerado do TikTok em "0%".
 *
 * Sobre a formula: ML e Shopee usam a MESMA definicao,
 * `cancelados / (nao cancelados + cancelados)`. Ainda assim nao existe ranking
 * competitivo entre canais — a razao nao e' aritmetica, e' que fonte, processo
 * de captura e semantica operacional dos status diferem entre marketplaces, e
 * o TikTok nao tem cobertura confiavel.
 */
import type { QualityBrandRow, QualityKpis } from "../api-client.ts";
import { isMarketplaceSelected } from "../marketplace-filter.ts";
import type { Marketplace } from "../mock-data.ts";
import { CHANNEL_LABEL } from "./kpi-band.ts";

export const CANCEL_RATE_FORMULA = "Taxa = cancelados / (não cancelados + cancelados)";
export const NO_CROSS_CHANNEL_RANKING =
  "Sem comparação entre canais: fonte, captura e semântica de status diferem por marketplace.";
export const UNAVAILABLE_IN_SOURCE = "Não disponível nesta fonte";
export const TIKTOK_QUALITY_REASON =
  "O TikTok Shop não fornece cancelamento nem devolução confiáveis nesta fonte.";
export const ML_RETURN_REASON = "Devolução não disponível na fonte do Mercado Livre.";

/** Rotulos de pedidos — deliberadamente diferentes por canal. */
export const ORDERS_CONSIDERED_LABEL = "Pedidos considerados";
export const ORDERS_REGISTERED_LABEL = "Pedidos registrados";

export interface VolumeHealthRow {
  channel: Marketplace;
  channelLabel: string;
  gmv: number | null;
  /** "Pedidos considerados" (ML/Shopee) ou "Pedidos registrados" (TikTok). */
  ordersLabel: string;
  orders: number | null;
  /** Explicacao do que o denominador inclui, exibida no drill-down. */
  ordersDefinition: string;
  canceled: number | null;
  cancelRatePct: number | null;
  cancelAvailable: boolean;
  cancelUnavailableReason: string | null;
  returned: number | null;
  returnRatePct: number | null;
  returnAvailable: boolean;
  returnUnavailableReason: string | null;
}

/**
 * Soma um campo opcional das linhas por marca. Se NENHUMA linha tem o campo,
 * devolve `null` — ausencia total nunca vira zero fabricado.
 */
function sumOptional(rows: readonly QualityBrandRow[], pick: (r: QualityBrandRow) => number | null | undefined): number | null {
  let total = 0;
  let seen = false;
  for (const row of rows) {
    const v = pick(row);
    if (v == null) continue;
    total += v;
    seen = true;
  }
  return seen ? total : null;
}

function addNullable(a: number | null, b: number | null): number | null {
  if (a == null && b == null) return null;
  return (a ?? 0) + (b ?? 0);
}

export interface VolumeHealthInput {
  kpis: QualityKpis | null;
  brands: readonly QualityBrandRow[];
  channels: readonly Marketplace[];
  /** GMV por canal vem do `/overview` (fonte autoritativa do GMV). */
  gmvByChannel: Partial<Record<Marketplace, number | null>>;
}

export function buildVolumeHealth(input: VolumeHealthInput): VolumeHealthRow[] {
  const { kpis, brands, channels, gmvByChannel } = input;
  const rows: VolumeHealthRow[] = [];

  if (isMarketplaceSelected(channels, "ml")) {
    // ml_total_orders JA E' nao cancelados + cancelados — nao e' "elegiveis".
    const considered = sumOptional(brands, (r) => r.ml_total_orders);
    rows.push({
      channel: "ml",
      channelLabel: CHANNEL_LABEL.ml,
      gmv: gmvByChannel.ml ?? null,
      ordersLabel: ORDERS_CONSIDERED_LABEL,
      orders: considered,
      ordersDefinition: "Total considerado = pedidos não cancelados + cancelados.",
      canceled: sumOptional(brands, (r) => r.ml_cancelled_orders),
      cancelRatePct: kpis?.ml_cancel_rate_pct ?? null,
      cancelAvailable: (kpis?.ml_cancel_rate_pct ?? null) != null,
      cancelUnavailableReason:
        (kpis?.ml_cancel_rate_pct ?? null) == null
          ? "Taxa não disponível para o período e filtros selecionados."
          : null,
      returned: null,
      returnRatePct: null,
      returnAvailable: false,
      returnUnavailableReason: ML_RETURN_REASON,
    });
  }

  if (isMarketplaceSelected(channels, "shopee")) {
    // shopee_orders e' a populacao de NAO cancelados; o total considerado soma
    // os cancelados por cima — mesma definicao do denominador servido.
    const notCanceled = sumOptional(brands, (r) => r.shopee_orders);
    const canceled = sumOptional(brands, (r) => r.shopee_canceled_orders);
    rows.push({
      channel: "shopee",
      channelLabel: CHANNEL_LABEL.shopee,
      gmv: gmvByChannel.shopee ?? null,
      ordersLabel: ORDERS_CONSIDERED_LABEL,
      orders: addNullable(notCanceled, canceled),
      ordersDefinition: "Total considerado = pedidos não cancelados + cancelados.",
      canceled,
      cancelRatePct: kpis?.shopee_cancel_rate_pct ?? null,
      cancelAvailable: (kpis?.shopee_cancel_rate_pct ?? null) != null,
      cancelUnavailableReason:
        (kpis?.shopee_cancel_rate_pct ?? null) == null
          ? "Taxa não disponível para o período e filtros selecionados."
          : null,
      returned: sumOptional(brands, (r) => r.shopee_returned_orders),
      returnRatePct: kpis?.shopee_return_rate_pct ?? null,
      returnAvailable: (kpis?.shopee_return_rate_pct ?? null) != null,
      returnUnavailableReason: null,
    });
  }

  if (isMarketplaceSelected(channels, "tiktok")) {
    // O campo `tiktok_canceled` EXISTE no schema, mas o valor e'
    // estruturalmente zero (DQ1) — logo vai para N/D com motivo, nunca 0%.
    rows.push({
      channel: "tiktok",
      channelLabel: CHANNEL_LABEL.tiktok,
      gmv: gmvByChannel.tiktok ?? null,
      ordersLabel: ORDERS_REGISTERED_LABEL,
      orders: sumOptional(brands, (r) => r.tiktok_orders),
      ordersDefinition:
        "Pedidos registrados na fonte. Não se infere total considerado incluindo cancelados.",
      canceled: null,
      cancelRatePct: null,
      cancelAvailable: false,
      cancelUnavailableReason: TIKTOK_QUALITY_REASON,
      returned: null,
      returnRatePct: null,
      returnAvailable: false,
      returnUnavailableReason: TIKTOK_QUALITY_REASON,
    });
  }

  return rows;
}

/** Verdadeiro quando nenhum canal selecionado tem taxa confiavel — nesse caso
 * o bloco nao renderiza, em vez de renderizar vazio. */
export function volumeHealthHasAnyRate(rows: readonly VolumeHealthRow[]): boolean {
  return rows.some((r) => r.cancelAvailable);
}
