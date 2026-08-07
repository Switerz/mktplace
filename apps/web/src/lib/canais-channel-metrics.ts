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
  roas_forte: "text-emerald-700 bg-emerald-50 border border-emerald-200",
  ads_subutilizado: "text-sky-700 bg-sky-50 border border-sky-200",
  custo_alto: "text-rose-700 bg-rose-50 border border-rose-200",
  frete_alto: "text-orange-700 bg-orange-50 border border-orange-200",
  sem_dado: "text-slate-500 bg-slate-100 border border-slate-200",
};

export const CHANNEL_LABEL: Record<string, string> = {
  tiktok: "TikTok Shop",
  ml: "Mercado Livre",
  shopee: "Shopee",
};

export const CHANNEL_BADGE_TONE: Record<string, string> = {
  tiktok: "text-violet-700 bg-violet-50",
  ml: "text-cyan-700 bg-cyan-50",
  shopee: "text-orange-700 bg-orange-50",
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
  return CHANNEL_SIGNAL_TONE[signal] ?? "text-slate-500 bg-slate-100 border border-slate-200";
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
