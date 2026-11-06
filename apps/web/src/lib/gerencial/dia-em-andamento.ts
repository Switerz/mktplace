/**
 * GERENCIAL-D0 — o dia de hoje (D0) no grafico de evolucao.
 *
 * Os presets de janela terminam em D-1 (`presets.ts`): somar um dia em
 * andamento aos totais puxaria comparacao, ritmo e meta para baixo. O grafico,
 * porem, precisa mostrar hoje — com o MESMO numero do filtro "Hoje" (mesmo
 * `/trend`, mesmo escopo de canais e marcas, `date_from = date_to = hoje`).
 *
 * Por isso hoje entra so' como um bucket EXTRA desenhado depois da serie do
 * periodo: fica fora da soma, da reconciliacao, das tendencias dos cards e da
 * meta, sai listrado (parcial) e nao tem par no periodo anterior.
 */
import type { TrendMetric } from "./request-key.ts";
import { mergeChannelSeries, type ChannelSeries, type MergedBucket } from "./trend-series.ts";
import type { Marketplace } from "../mock-data.ts";

/** Dia anterior a `hoje` (YYYY-MM-DD), em datas locais. */
export function diaAnterior(hoje: string): string {
  const [a, m, d] = hoje.split("-").map(Number);
  const dt = new Date(a, m - 1, d - 1);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
}

/**
 * Hoje entra no grafico quando o periodo termina ONTEM (o ultimo dia fechado,
 * como em "Mes atual", "7 dias" e "30 dias"). Periodos que acabam antes nao
 * ganham um dia solto la' na frente; o proprio "Hoje" ja' e' o dia inteiro.
 */
export function mostrarHojeNoGrafico(dateFrom: string, dateTo: string, hoje: string): boolean {
  return dateTo === diaAnterior(hoje) && dateFrom <= dateTo;
}

/**
 * O bucket de hoje, montado pela MESMA regra de mescla do periodo: o total so'
 * existe com todos os canais selecionados respondendo. Qualquer canal fora do
 * ar (ou um grao diferente de diario) devolve `null` — sem ponto, nunca um
 * total parcial disfarcado.
 */
export function bucketDeHoje(
  seriesHoje: readonly ChannelSeries[],
  selecionados: readonly Marketplace[],
  metric: TrendMetric,
  hoje: string,
): MergedBucket | null {
  if (seriesHoje.length === 0) return null;
  const m = mergeChannelSeries(seriesHoje, selecionados, metric, "day");
  if (!m.allChannelsFresh || m.granularityIssue !== null) return null;
  const b = m.buckets.find((x) => x.date === hoje);
  if (!b) return null;
  return { ...b, label: "Hoje", comparison: null };
}
