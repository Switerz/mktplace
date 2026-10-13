// Formatacao e rotulos da matriz comparativa marca x canal da aba Canais
// (Gate 2, docs/sections/canais_audit.md secao 14). Modulo puro (sem React)
// para ser testavel isoladamente — mesmo padrao de async-channel-state.ts.

import type { CanaisChannelMedian, CanaisChannelRow } from "./api-client";

export type MetricTone = "value" | "muted" | "warning";

export interface FormattedMetric {
  text: string;
  tone: MetricTone;
}

/**
 * Formata uma metrica derivada por canal (Ads/GMV, ROAS, ACOS, Custo
 * marketplace/GMV, Frete seller/GMV) respeitando os 3 estados do contrato:
 * - nao aplicavel ao canal -> "N/A" (ex: Ads no TikTok);
 * - aplicavel mas sem dado no mart -> "Sem dado" (ex: custo ML);
 * - aplicavel e disponivel mas matematicamente indefinido (denominador
 *   zero/nulo, ex: ROAS sem ad_spend) -> "—", nunca "0%".
 * Um valor real igual a zero (ex: 0% de Ads/GMV porque a marca nao gastou
 * nada, mas o canal tem dado) É exibido normalmente — zero real nao e
 * ausencia de dado.
 */
export function formatChannelMetric(
  value: number | null,
  applicable: boolean,
  available: boolean,
  format: (v: number) => string,
): FormattedMetric {
  if (!applicable) return { text: "N/A", tone: "muted" };
  if (!available) return { text: "Sem dado", tone: "warning" };
  if (value == null) return { text: "—", tone: "muted" };
  return { text: format(value), tone: "value" };
}

export const CHANNEL_SIGNAL_LABEL: Record<string, string> = {
  roas_forte: "ROAS forte",
  ads_subutilizado: "Ads subutilizado",
  custo_alto: "Custo alto",
  frete_alto: "Frete alto",
  sem_dado: "Sem dado",
};

export const CHANNEL_SIGNAL_TONE: Record<string, string> = {
  roas_forte: "text-ok-ink bg-ok-soft border border-ok/35",
  ads_subutilizado: "text-accent-ink bg-accent-soft border border-accent/30",
  custo_alto: "text-crit-ink bg-crit-soft border border-crit/35",
  frete_alto: "text-warn-ink bg-warn-soft border border-warn/35",
  sem_dado: "text-ink-faint bg-raised border border-line",
};

export const CHANNEL_LABEL: Record<string, string> = {
  tiktok: "TikTok Shop",
  ml: "Mercado Livre",
  shopee: "Shopee",
};

export const CHANNEL_BADGE_TONE: Record<string, string> = {
  tiktok: "text-ink-muted bg-raised ring-1 ring-inset ring-canal-tiktok/60",
  ml: "text-ink-muted bg-raised ring-1 ring-inset ring-canal-ml/60",
  shopee: "text-ink-muted bg-raised ring-1 ring-inset ring-canal-shopee/60",
};

/** Rotulo de fallback para um sinal desconhecido — nunca quebra a UI. */
/** Rotulo exibido quando o backend manda um sinal que a UI nao conhece. O
 * identificador cru (snake_case) NUNCA e' renderizado nem interpolado em
 * `aria-label` — ele fica so' no contrato/diagnostico. */
export const UNKNOWN_SIGNAL_LABEL = "Sinal não mapeado";

export function signalLabel(signal: string): string {
  return CHANNEL_SIGNAL_LABEL[signal] ?? UNKNOWN_SIGNAL_LABEL;
}

/** Verdadeiro quando o sinal nao tem rotulo humano — util para diagnostico e
 * teste, sem que o identificador cru chegue a interface. */
export function isUnmappedSignal(signal: string): boolean {
  return CHANNEL_SIGNAL_LABEL[signal] === undefined;
}

export function signalTone(signal: string): string {
  return CHANNEL_SIGNAL_TONE[signal] ?? "text-ink-faint bg-raised border border-line";
}

/**
 * Localiza a mediana/p75 do MESMO canal de uma linha da matriz comparativa
 * (Gate U3, drill-down marca x canal). Nunca mistura mediana de outro canal
 * — usa igualdade estrita de `channel`. Retorna `null` quando o canal nao
 * tem mediana calculada (ex: `channelMedians` vazio no modo demonstracao) em
 * vez de inventar uma referencia de comparacao.
 */
export function findChannelMedian(
  medians: CanaisChannelMedian[],
  channel: CanaisChannelRow["channel"],
): CanaisChannelMedian | null {
  return medians.find((m) => m.channel === channel) ?? null;
}
