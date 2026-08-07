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
 *
 * O Gate V2-2 acrescentou duas regras da mesma natureza:
 *
 * 4. **Grao divergente nao se mescla.** Series com granularidades diferentes na
 *    mesma requisicao nao formam uma serie — nao se converte, nao se reagrega e
 *    nao se desenha; expoe-se `granularityIssue` e nomeiam-se os canais.
 * 5. **Janela comparativa vem do contrato.** `comparison.date_from`/`date_to`
 *    sao transportados da resposta HTTP; nunca reconstruidos do primeiro/ultimo
 *    bucket. Janela desconhecida ou divergente entre canais bloqueia o total
 *    anterior e e' nomeada — a serie ATUAL permanece intacta.
 */
import type { TrendGranularity, TrendGranularityRequest, TrendPoint } from "../api-client.ts";
import type { Marketplace } from "../mock-data.ts";
import type { TrendMetric } from "./request-key.ts";

export type ChannelSeriesStatus = "loading" | "fresh" | "error";

/**
 * Estado da COMPARACAO de um canal (Gate V2-2), independente do estado da serie
 * atual. Cinco conclusoes distintas, mais o caso em que nada foi pedido:
 *
 * - `not_requested`: `compare=false`. Nao gera aviso nem estado de falha.
 * - `loading` / `error`: como na serie atual, mas isolados dela.
 * - `unsupported`: o usuario pediu comparacao e a API nao devolveu o campo
 *   (backend anterior ao contrato). E' indisponibilidade, nao "nao pedido".
 * - `empty`: janela conhecida, sem registros. Nao e' zero.
 * - `fresh`: janela conhecida, com registros.
 */
export type ComparisonStatus =
  | "not_requested"
  | "loading"
  | "error"
  | "unsupported"
  | "empty"
  | "fresh";

export interface ChannelSeries {
  channel: Marketplace;
  status: ChannelSeriesStatus;
  granularity: TrendGranularity;
  points: TrendPoint[];
  /** Gate V2-2 — omitido pelos chamadores que nao pedem comparacao. */
  comparisonStatus?: ComparisonStatus;
  comparisonPoints?: TrendPoint[];
  /** Janela REAL da comparacao, vinda do contrato HTTP (`comparison.date_from`
   * / `date_to`). NUNCA derivada dos buckets: no grao semanal o primeiro bucket
   * comeca antes da janela, e com `data: []` a janela continua conhecida. */
  comparisonDateFrom?: string | null;
  comparisonDateTo?: string | null;
}

/** Lado comparativo de um bucket, alinhado por POSICAO ORDINAL. */
export interface BucketComparison {
  /** Data REAL do bucket anterior — nunca a data do atual deslocada. */
  date: string;
  /** Rotulo REAL do bucket anterior. */
  label: string;
  values: Record<string, number | null>;
  /** Soma — apenas quando todos os canais comparativos tem numero aqui. */
  total: number | null;
}

export interface MergedBucket {
  date: string;
  label: string;
  /** Valor por canal: `number` (inclusive 0 explicito) ou `null` = lacuna. */
  values: Record<string, number | null>;
  /** Soma dos canais selecionados — apenas quando todos tem numero aqui. */
  total: number | null;
  /** `null` quando a comparacao nao foi pedida OU quando o periodo anterior nao
   * tem bucket nesta posicao ordinal (ex.: 31 dias atuais x 28 anteriores). */
  comparison: BucketComparison | null;
}

/** Janela comparativa declarada por um canal, para diagnostico de divergencia. */
export interface ChannelComparisonWindow {
  channel: Marketplace;
  dateFrom: string | null;
  dateTo: string | null;
}

/**
 * Problema da JANELA comparativa (nao dos valores):
 *
 * - `inconsistent`: canais concluidos declararam janelas diferentes — comparar
 *   somas de janelas distintas produziria um numero sem significado.
 * - `unknown`: um canal concluiu a comparacao sem declarar a janela (backend
 *   anterior ao contrato). Janela desconhecida e' indisponibilidade; jamais se
 *   fabrica uma a partir dos buckets.
 */
export type ComparisonWindowIssue = "inconsistent" | "unknown";

/** Estado agregado da comparacao, derivado dos estados por canal. */
export interface ComparisonSummary {
  /** `false` => `compare=false`: nenhuma linha, legenda, skeleton ou aviso. */
  requested: boolean;
  loadingChannels: Marketplace[];
  failedChannels: Marketplace[];
  /** Canais cuja janela anterior respondeu SEM registros (nao e' zero). */
  emptyChannels: Marketplace[];
  /** Canais em que a API nao suporta o contrato comparativo. */
  unsupportedChannels: Marketplace[];
  availableChannels: Marketplace[];
  /** Todos os canais selecionados trouxeram comparacao (fresca ou vazia). */
  allChannelsFresh: boolean;
  /** Todo bucket comparativo tem valor em todos os canais selecionados. */
  everyBucketComplete: boolean;
  /** Total anterior — `null` a menos que a comparacao esteja COMPLETA. */
  seriesTotal: number | null;
  /** Janela real da comparacao, **exclusivamente** do contrato HTTP. `null`
   * quando desconhecida ou inconsistente entre canais. */
  dateFrom: string | null;
  dateTo: string | null;
  windowIssue: ComparisonWindowIssue | null;
  /** Janelas declaradas por canal — usadas para nomear a inconsistencia. */
  windowsByChannel: ChannelComparisonWindow[];
}

/**
 * Incompatibilidade de granularidade (Gate V2-2, correcao consolidada).
 *
 * - `channel_mismatch`: os canais da MESMA requisicao devolveram graos
 *   diferentes. Datas diarias e semanais representam buckets distintos: somar,
 *   alinhar por posicao ou rotular no tooltip seria semanticamente errado.
 * - `unsupported_request`: o grao pedido foi explicito e a API devolveu outro —
 *   contrato incompativel (ex.: `week` num backend anterior ao V2-2). O seletor
 *   nao pode parecer funcional enquanto a escolha e' ignorada.
 *
 * Em ambos os casos NAO se converte nem se reagrega nada no frontend.
 */
export interface GranularityIssue {
  kind: "channel_mismatch" | "unsupported_request";
  requested: TrendGranularityRequest;
  grains: { channel: Marketplace; granularity: TrendGranularity }[];
}

export interface MergedSeries {
  buckets: MergedBucket[];
  /** Grao comum das series frescas. Com `granularityIssue != null` nao ha grao
   * comum: este campo fica com o mais grosso observado apenas para satisfazer o
   * tipo, e nao deve ser usado para rotular nada (nao existem buckets). */
  granularity: TrendGranularity;
  /** `null` = todas as series frescas concordam com o grao e com o pedido. */
  granularityIssue: GranularityIssue | null;
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
  /** Gate V2-2 — estado do periodo anterior, independente da serie atual. */
  comparison: ComparisonSummary;
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
  /** Grao PEDIDO. Com um grao explicito, a resposta tem de devolver exatamente
   * ele; `auto` aceita o que o backend resolver, exigindo apenas que todos os
   * canais da mesma requisicao concordem entre si. */
  requestedGranularity: TrendGranularityRequest = "auto",
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

  // ---- granularidade: exige CONCORDANCIA, nunca reconciliacao -------------
  // Antes desta rodada "a mais grossa vencia". Isso nao tornava as series
  // compativeis: uma data diaria e uma semanal representam buckets diferentes,
  // e soma, alinhamento ordinal e tooltip ficariam semanticamente errados.
  // Pode acontecer com cache antigo, deploy parcial ou resposta incompativel —
  // e nesses casos a unica resposta correta e' nao mesclar.
  const grains = availableChannels.map((c) => ({
    channel: c,
    granularity: byChannel.get(c)!.granularity,
  }));
  const distinctGrains = [...new Set(grains.map((g) => g.granularity))];
  const ignoredExplicitRequest =
    requestedGranularity !== "auto" && distinctGrains.some((g) => g !== requestedGranularity);
  // `unsupported_request` tem precedencia: quando o grao pedido foi explicito e
  // ignorado, a causa acionavel e' a API, nao a divergencia entre canais.
  const granularityIssue: GranularityIssue | null = ignoredExplicitRequest
    ? { kind: "unsupported_request", requested: requestedGranularity, grains }
    : distinctGrains.length > 1
      ? { kind: "channel_mismatch", requested: requestedGranularity, grains }
      : null;

  // Placeholder de tipo apenas: com `granularityIssue` nao existe grao comum, e
  // tambem nao existem buckets para rotular.
  const granularity: TrendGranularity = distinctGrains.includes("month")
    ? "month"
    : distinctGrains.includes("week")
      ? "week"
      : "day";

  // Uniao ordenada dos buckets, com o rotulo vindo da primeira serie que o
  // conhece (todas as respostas usam o mesmo formatador do backend).
  const labelByDate = new Map<string, string>();
  if (granularityIssue === null) {
    for (const channel of availableChannels) {
      for (const p of byChannel.get(channel)!.points) {
        if (!labelByDate.has(p.date)) labelByDate.set(p.date, p.label);
      }
    }
  }
  const dates = [...labelByDate.keys()].sort();

  const allChannelsFresh = selected.length > 0 && availableChannels.length === selected.length;

  // ---- comparacao (Gate V2-2) -------------------------------------------
  // Pedida quando ALGUM canal selecionado declara um estado comparativo que nao
  // seja "not_requested". Estados por canal sao independentes do estado da serie
  // atual: a comparacao pode falhar sozinha.
  const comparisonRequested = selected.some((c) => {
    const st = byChannel.get(c)?.comparisonStatus;
    return st != null && st !== "not_requested";
  });

  const cmpLoading: Marketplace[] = [];
  const cmpFailed: Marketplace[] = [];
  const cmpEmpty: Marketplace[] = [];
  const cmpAvailable: Marketplace[] = [];
  const cmpUnsupported: Marketplace[] = [];
  if (comparisonRequested) {
    for (const channel of selected) {
      const st = byChannel.get(channel)?.comparisonStatus ?? "loading";
      if (st === "loading" || st === "not_requested") cmpLoading.push(channel);
      else if (st === "error") cmpFailed.push(channel);
      else if (st === "unsupported") cmpUnsupported.push(channel);
      else if (st === "empty") cmpEmpty.push(channel);
      else cmpAvailable.push(channel);
    }
  }
  // "Fresca" inclui as janelas que responderam SEM registros: elas concluiram.
  // `unsupported` NAO conclui — e' indisponibilidade do contrato.
  const cmpSettled = [...cmpAvailable, ...cmpEmpty];
  const cmpAllFresh = comparisonRequested && selected.length > 0 && cmpSettled.length === selected.length;

  // ---- janela comparativa: SO' do contrato HTTP ---------------------------
  // Reconstruir a janela a partir do primeiro/ultimo bucket estava errado: no
  // grao semanal o primeiro bucket comeca ANTES da janela real, no mensal os
  // limites nao sao os do bucket, e com `data: []` a janela desapareceria
  // embora a API a tenha declarado.
  const windowsByChannel: ChannelComparisonWindow[] = cmpSettled.map((channel) => {
    const s = byChannel.get(channel)!;
    return {
      channel,
      dateFrom: s.comparisonDateFrom ?? null,
      dateTo: s.comparisonDateTo ?? null,
    };
  });
  const windowUnknown = windowsByChannel.some((w) => w.dateFrom == null || w.dateTo == null);
  const distinctWindows = [...new Set(windowsByChannel.map((w) => `${w.dateFrom}|${w.dateTo}`))];
  const windowIssue: ComparisonWindowIssue | null = !comparisonRequested || windowsByChannel.length === 0
    ? null
    : windowUnknown
      ? "unknown"
      : distinctWindows.length > 1
        ? "inconsistent"
        : null;
  const cmpDateFrom = windowIssue === null ? (windowsByChannel[0]?.dateFrom ?? null) : null;
  const cmpDateTo = windowIssue === null ? (windowsByChannel[0]?.dateTo ?? null) : null;

  // Uniao ordenada dos buckets ANTERIORES, com os rotulos e datas reais deles.
  // Com janela desconhecida/inconsistente ou grao divergente nao se pareia nada:
  // o alinhamento ordinal entre janelas diferentes nao tem significado.
  const comparisonUsable = comparisonRequested && windowIssue === null && granularityIssue === null;
  const cmpLabelByDate = new Map<string, string>();
  if (comparisonUsable) {
    for (const channel of cmpAvailable) {
      for (const p of byChannel.get(channel)!.comparisonPoints ?? []) {
        if (!cmpLabelByDate.has(p.date)) cmpLabelByDate.set(p.date, p.label);
      }
    }
  }
  const cmpDates = [...cmpLabelByDate.keys()].sort();

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
    return { date, label: labelByDate.get(date)!, values, total, comparison: null };
  });

  // Alinhamento por POSICAO ORDINAL: o i-esimo bucket atual corresponde ao
  // i-esimo bucket anterior. Com 31 dias atuais e 28 anteriores, os tres
  // ultimos atuais ficam SEM comparacao (`null`) — nunca com zero.
  if (comparisonUsable) {
    buckets.forEach((bucket, index) => {
      const cmpDate = cmpDates[index];
      if (cmpDate == null) return; // periodo anterior mais curto: sem par
      const values: Record<string, number | null> = {};
      for (const channel of selected) {
        if (!cmpAvailable.includes(channel)) {
          values[channel] = null;
          continue;
        }
        const point = (byChannel.get(channel)!.comparisonPoints ?? []).find((p) => p.date === cmpDate);
        values[channel] = point ? metricValue(point, metric) : null;
      }
      const complete = cmpAllFresh && selected.every((c) => typeof values[c] === "number");
      bucket.comparison = {
        date: cmpDate,
        label: cmpLabelByDate.get(cmpDate)!,
        values,
        // Total anterior SO' com todos os canais comparativos completos: um
        // total parcial nunca pode se passar por total completo.
        total: complete ? selected.reduce((sum, c) => sum + (values[c] as number), 0) : null,
      };
    });
  }

  const cmpPaired = buckets.filter((b) => b.comparison != null);
  const cmpEveryBucketComplete =
    comparisonUsable && cmpPaired.length > 0 && cmpPaired.every((b) => b.comparison!.total !== null);
  const cmpSeriesTotal = cmpEveryBucketComplete
    ? cmpPaired.reduce((sum, b) => sum + (b.comparison!.total as number), 0)
    : null;

  const comparison: ComparisonSummary = {
    requested: comparisonRequested,
    loadingChannels: cmpLoading,
    failedChannels: cmpFailed,
    emptyChannels: cmpEmpty,
    unsupportedChannels: cmpUnsupported,
    availableChannels: cmpAvailable,
    allChannelsFresh: cmpAllFresh,
    everyBucketComplete: cmpEveryBucketComplete,
    seriesTotal: cmpSeriesTotal,
    // Do contrato, nunca dos buckets — e `null` quando a janela nao pode ser
    // afirmada com seguranca.
    dateFrom: cmpDateFrom,
    dateTo: cmpDateTo,
    windowIssue,
    windowsByChannel,
  };

  const everyBucketComplete = buckets.length > 0 && buckets.every((b) => b.total !== null);
  const seriesTotal = everyBucketComplete
    ? buckets.reduce((sum, b) => sum + (b.total as number), 0)
    : null;

  return {
    buckets,
    granularity,
    granularityIssue,
    availableChannels,
    failedChannels,
    loadingChannels,
    emptyChannels,
    allChannelsFresh,
    everyBucketComplete,
    seriesTotal,
    comparison,
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
  /** Rotulo da acao — "este dia" / "esta semana" / "este mes". */
  label: string;
  /** `true` quando o intervalo do bucket foi CORTADO pelo periodo global (a
   * primeira ou a ultima semana costuma ser parcial). */
  clamped: boolean;
}

/** Dias desde 1970-01-01, por aritmetica de calendario. Sem `Date`, logo sem
 * risco de fuso: `new Date("2026-02-01")` e' UTC e, em fuso negativo,
 * `getDate()` devolve o dia anterior. */
function toEpochDay(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  // Algoritmo civil-from-days invertido (Howard Hinnant), inteiro e exato.
  const yy = m <= 2 ? y - 1 : y;
  const era = Math.floor(yy / 400);
  const yoe = yy - era * 400;
  const doy = Math.floor((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5) + d - 1;
  const doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy;
  return era * 146097 + doe - 719468;
}

function fromEpochDay(z: number): string {
  const zz = z + 719468;
  const era = Math.floor(zz / 146097);
  const doe = zz - era * 146097;
  const yoe = Math.floor((doe - Math.floor(doe / 1460) + Math.floor(doe / 36524) - Math.floor(doe / 146096)) / 365);
  const y = yoe + era * 400;
  const doy = doe - (365 * yoe + Math.floor(yoe / 4) - Math.floor(yoe / 100));
  const mp = Math.floor((5 * doy + 2) / 153);
  const d = doy - Math.floor((153 * mp + 2) / 5) + 1;
  const m = mp + (mp < 10 ? 3 : -9);
  const year = m <= 2 ? y + 1 : y;
  return `${String(year).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** Limita `iso` ao intervalo [min, max] quando eles existirem. */
function clampIso(iso: string, min?: string, max?: string): string {
  if (min && iso < min) return min;
  if (max && iso > max) return max;
  return iso;
}

/**
 * Converte a data de um bucket no intervalo que ele representa.
 *
 * No grao MENSAL, `bucket.date` e' o primeiro dia da competencia, e fixar
 * `date_from = date_to = bucket.date` reduziria o mes inteiro a um unico dia —
 * o CTA prometia "fixar este dia" e entregava um recorte errado.
 *
 * No grao SEMANAL (Gate V2-2), `bucket.date` e' a SEGUNDA-FEIRA da semana ISO, e
 * o intervalo vai dela ao domingo. A primeira e a ultima semana de um periodo
 * quase sempre sao parciais, entao o intervalo e' CORTADO pelo periodo global
 * efetivamente selecionado — o CTA nunca aplica datas fora do que o usuario
 * estava vendo.
 *
 * Tudo por aritmetica de calendario, sem `Date`: nao existe erro de fuso.
 */
export function bucketRange(
  bucketDate: string,
  granularity: "day" | "week" | "month",
  /** Periodo global atual, usado para cortar buckets parciais. */
  bounds?: { dateFrom?: string; dateTo?: string },
): BucketRange {
  if (granularity === "day") {
    return { dateFrom: bucketDate, dateTo: bucketDate, label: "este dia", clamped: false };
  }

  let rawFrom: string;
  let rawTo: string;
  let label: string;

  if (granularity === "week") {
    const start = toEpochDay(bucketDate);
    rawFrom = bucketDate;
    rawTo = fromEpochDay(start + 6); // segunda + 6 = domingo
    label = "esta semana";
  } else {
    const [yearText, monthText] = bucketDate.split("-");
    const mm = monthText.padStart(2, "0");
    rawFrom = `${yearText}-${mm}-01`;
    rawTo = `${yearText}-${mm}-${String(daysInMonth(Number(yearText), Number(monthText))).padStart(2, "0")}`;
    label = "este mês";
  }

  const dateFrom = clampIso(rawFrom, bounds?.dateFrom, bounds?.dateTo);
  const dateTo = clampIso(rawTo, bounds?.dateFrom, bounds?.dateTo);
  return { dateFrom, dateTo, label, clamped: dateFrom !== rawFrom || dateTo !== rawTo };
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
  if (merged.granularityIssue !== null) {
    return { status: "skipped", reason: "As séries voltaram em granularidades diferentes; não há série combinada para reconciliar." };
  }
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
