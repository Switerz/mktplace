/**
 * Tendencia por canal da Gerencial V2 (Gate V2-1, Task E).
 *
 * `/trend` devolve UMA serie agregada por requisicao, sem dimensao de canal no
 * payload. Para desenhar uma linha por canal o frontend faz **uma chamada por
 * canal selecionado (no maximo tres)** com selecao unitaria, reutilizando o
 * endpoint existente — nenhuma quarta chamada agregada, nenhum endpoint novo.
 *
 * Este modulo e' 100% puro: recebe as respostas ja resolvidas e monta a uniao
 * dos buckets. As tres regras que ele existe para garantir:
 *
 * 1. **gap != zero.** Um bucket que a resposta do canal simplesmente nao traz
 *    e' `null` (lacuna), nunca `0`. Zero so' aparece quando a resposta trouxe
 *    zero explicito.
 * 2. **Total so' quando completo.** O total de um bucket existe apenas se TODOS
 *    os canais selecionados tem valor numerico naquele bucket. Um canal em
 *    falha, ou um bucket ausente em qualquer canal, elimina o total daquele
 *    bucket — nao vira soma parcial disfarcada de total.
 * 3. **Reconciliacao explicita.** Com todas as series completas, a soma dos
 *    buckets tem de bater com o `/overview` do mesmo escopo. Divergencia vira
 *    estado de erro do bloco; nunca e' arredondada para desaparecer.
 */
import type { TrendPoint } from "../api-client.ts";
import type { Marketplace } from "../mock-data.ts";
import type { TrendMetric } from "./request-key.ts";

export type ChannelSeriesStatus = "loading" | "fresh" | "error";

export interface ChannelSeries {
  channel: Marketplace;
  status: ChannelSeriesStatus;
  granularity: "day" | "month";
  points: TrendPoint[];
}

export interface MergedBucket {
  date: string;
  label: string;
  /** Valor por canal: `number` (inclusive 0 explicito) ou `null` = lacuna. */
  values: Record<string, number | null>;
  /** Soma dos canais selecionados — apenas quando todos tem numero aqui. */
  total: number | null;
}

export interface MergedSeries {
  buckets: MergedBucket[];
  granularity: "day" | "month";
  /** Canais com resposta fresca (linha desenhavel). */
  availableChannels: Marketplace[];
  /** Canais selecionados cuja fonte falhou — nomeados na UI. */
  failedChannels: Marketplace[];
  /** Canais selecionados ainda carregando. */
  loadingChannels: Marketplace[];
  /** Canais frescos mas sem nenhuma linha no periodo (empty, nao zero). */
  emptyChannels: Marketplace[];
  /** Todos os canais selecionados responderam com sucesso. */
  allChannelsFresh: boolean;
  /** Todo bucket tem valor numerico em todos os canais selecionados. */
  everyBucketComplete: boolean;
  /** Soma de todos os buckets — `null` se algum bucket nao tem total. */
  seriesTotal: number | null;
}

function metricValue(point: TrendPoint, metric: TrendMetric): number {
  return metric === "gmv" ? point.gmv : point.orders;
}

/**
 * Monta a uniao dos buckets das series por canal.
 *
 * `selectedChannels` e' a fonte de verdade de "quais canais deveriam estar
 * aqui" — um canal selecionado que falhou continua contando para impedir o
 * total, mesmo sem contribuir com nenhum bucket.
 */
export function mergeChannelSeries(
  series: readonly ChannelSeries[],
  selectedChannels: readonly Marketplace[],
  metric: TrendMetric,
): MergedSeries {
  // `selected` e' sempre a selecao completa do filtro. Um canal selecionado
  // SEM entrada de serie (requisicao ainda nao despachada) conta como
  // carregando — nunca desaparece da conta, senao um total incompleto passaria
  // por completo.
  const selected = [...selectedChannels];
  const byChannel = new Map<Marketplace, ChannelSeries>();
  for (const s of series) {
    if (selectedChannels.includes(s.channel)) byChannel.set(s.channel, s);
  }

  const availableChannels: Marketplace[] = [];
  const failedChannels: Marketplace[] = [];
  const loadingChannels: Marketplace[] = [];
  const emptyChannels: Marketplace[] = [];

  for (const channel of selected) {
    const s = byChannel.get(channel);
    if (!s || s.status === "loading") {
      loadingChannels.push(channel);
      continue;
    }
    if (s.status === "error") {
      failedChannels.push(channel);
      continue;
    }
    availableChannels.push(channel);
    if (s.points.length === 0) emptyChannels.push(channel);
  }

  // Uniao ordenada dos buckets, com o rotulo vindo da primeira serie que o
  // conhece (todas as respostas usam o mesmo formatador do backend).
  const labelByDate = new Map<string, string>();
  for (const channel of availableChannels) {
    for (const p of byChannel.get(channel)!.points) {
      if (!labelByDate.has(p.date)) labelByDate.set(p.date, p.label);
    }
  }
  const dates = [...labelByDate.keys()].sort();

  // Granularidade: as respostas do mesmo intervalo concordam; se por algum
  // motivo divergirem, a mais grossa vence (nunca finge granularidade menor).
  const granularity: "day" | "month" = availableChannels.some(
    (c) => byChannel.get(c)!.granularity === "month",
  )
    ? "month"
    : "day";

  const allChannelsFresh = selected.length > 0 && availableChannels.length === selected.length;

  const buckets: MergedBucket[] = dates.map((date) => {
    const values: Record<string, number | null> = {};
    for (const channel of selected) {
      const s = byChannel.get(channel);
      if (!s || s.status !== "fresh") {
        values[channel] = null;
        continue;
      }
      const point = s.points.find((p) => p.date === date);
      // Ausencia de ponto e' LACUNA. Zero so' quando a resposta trouxe zero.
      values[channel] = point ? metricValue(point, metric) : null;
    }
    const complete =
      allChannelsFresh && selected.every((c) => typeof values[c] === "number");
    const total = complete
      ? selected.reduce((sum, c) => sum + (values[c] as number), 0)
      : null;
    return { date, label: labelByDate.get(date)!, values, total };
  });

  const everyBucketComplete = buckets.length > 0 && buckets.every((b) => b.total !== null);
  const seriesTotal = everyBucketComplete
    ? buckets.reduce((sum, b) => sum + (b.total as number), 0)
    : null;

  return {
    buckets,
    granularity,
    availableChannels,
    failedChannels,
    loadingChannels,
    emptyChannels,
    allChannelsFresh,
    everyBucketComplete,
    seriesTotal,
  };
}

// ---------------------------------------------------------------------------
// Intervalo de um bucket, por grao
// ---------------------------------------------------------------------------

/** Dias de cada mes; fevereiro resolvido pela regra bissexta gregoriana. */
function daysInMonth(year: number, month: number): number {
  if (month === 2) {
    const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
    return leap ? 29 : 28;
  }
  return [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
}

export interface BucketRange {
  dateFrom: string;
  dateTo: string;
  /** Rotulo da acao — "este dia" x "este mes". */
  label: string;
}

/**
 * Converte a data de um bucket no intervalo que ele representa.
 *
 * No grao MENSAL, `bucket.date` e' o primeiro dia da competencia, e fixar
 * `date_from = date_to = bucket.date` reduziria o mes inteiro a um unico dia —
 * o CTA prometia "fixar este dia" e entregava um recorte errado.
 *
 * Tudo por manipulacao de string: `new Date("2026-02-01")` e' interpretado como
 * UTC e, em fuso negativo, `getDate()` devolve o dia anterior. Aqui nao existe
 * `Date` no caminho, logo nao existe erro de fuso.
 */
export function bucketRange(bucketDate: string, granularity: "day" | "month"): BucketRange {
  if (granularity === "day") {
    return { dateFrom: bucketDate, dateTo: bucketDate, label: "este dia" };
  }
  const [yearText, monthText] = bucketDate.split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const last = daysInMonth(year, month);
  const mm = monthText.padStart(2, "0");
  return {
    dateFrom: `${yearText}-${mm}-01`,
    dateTo: `${yearText}-${mm}-${String(last).padStart(2, "0")}`,
    label: "este mês",
  };
}

/**
 * Tolerancia de centavos para GMV. Cada bucket ja chega arredondado em 2
 * casas pelo backend, entao a soma de N buckets pode divergir do agregado por
 * ate N meios-centavos. A tolerancia escala com o numero de buckets e nunca
 * passa disso — nao existe folga percentual que esconda divergencia real.
 */
export function gmvTolerance(bucketCount: number): number {
  return Math.max(0.05, bucketCount * 0.01);
}

export type Reconciliation =
  | { status: "ok" }
  | { status: "skipped"; reason: string }
  | { status: "mismatch"; seriesTotal: number; referenceValue: number; diff: number; tolerance: number };

/**
 * Reconcilia a soma das series com o agregado do `/overview` no mesmo escopo.
 * GMV admite tolerancia de centavos (arredondamento por bucket); Pedidos exige
 * igualdade inteira. Divergencia acima disso e' reportada, nunca suavizada.
 */
export function reconcileSeriesTotal(
  merged: MergedSeries,
  metric: TrendMetric,
  referenceValue: number | null,
): Reconciliation {
  if (!merged.allChannelsFresh) {
    return { status: "skipped", reason: "Uma ou mais séries de canal não estão disponíveis." };
  }
  if (merged.seriesTotal === null) {
    return { status: "skipped", reason: "Há buckets sem valor em todos os canais selecionados." };
  }
  if (referenceValue == null) {
    return { status: "skipped", reason: "Agregado do período indisponível para comparação." };
  }
  const diff = merged.seriesTotal - referenceValue;
  const tolerance = metric === "gmv" ? gmvTolerance(merged.buckets.length) : 0;
  if (Math.abs(diff) <= tolerance) return { status: "ok" };
  return {
    status: "mismatch",
    seriesTotal: merged.seriesTotal,
    referenceValue,
    diff,
    tolerance,
  };
}
