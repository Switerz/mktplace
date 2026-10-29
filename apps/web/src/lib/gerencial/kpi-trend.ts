/**
 * Tendencia dos cards de KPI (KPIS-COMPARACAO-1) — logica PURA.
 *
 * A comparacao do card responde "melhorou ou piorou contra o periodo
 * anterior?". A tendencia responde outra coisa: "para onde esta indo AGORA,
 * dentro do periodo?". Por isso ela compara o fim do periodo com o trecho
 * imediatamente antes: os ultimos 7 pontos contra os 7 anteriores (ou metade
 * contra metade, em periodos curtos).
 *
 * So' existe para o que tem serie: GMV e Pedidos vem de `/trend`; Ticket e'
 * GMV / Pedidos ponto a ponto. Ads e ROAS nao tem serie diaria na fonte e nao
 * ganham tendencia (nunca inventada).
 *
 * A cauda sem venda e' cortada antes do calculo: o Data Mart chega com D-1, e o
 * "hoje" zerado do preset "Mes atual" puxaria toda tendencia para baixo.
 */
import type { MergedBucket } from "./trend-series.ts";
import { deltaPct, direcao } from "./kpi-band.ts";

export interface TendenciaKpi {
  /** Pontos do periodo, na ordem, sem a cauda vazia. */
  pontos: number[];
  /** Variacao do trecho recente contra o anterior, em %. */
  variacaoPct: number | null;
  direcao: "up" | "down" | "flat" | null;
  /** "últimos 7 dias vs 7 anteriores". */
  rotulo: string;
}

const UNIDADE: Record<string, [string, string]> = {
  day: ["dia", "dias"],
  week: ["semana", "semanas"],
  month: ["mês", "meses"],
};

/** Totais por ponto, cortando a cauda sem dado (null ou zero). */
export function totaisDaSerie(buckets: Pick<MergedBucket, "total">[]): (number | null)[] {
  const totais = buckets.map((b) => b.total);
  let fim = totais.length;
  while (fim > 0 && !(totais[fim - 1]! > 0)) fim--;
  return totais.slice(0, fim);
}

export function tendencia(pontos: (number | null)[], granularidade: string): TendenciaKpi | null {
  const vals = pontos.filter((v): v is number => v != null && Number.isFinite(v));
  if (vals.length < 4) return null;
  const janela = vals.length >= 14 ? 7 : Math.floor(vals.length / 2);
  const media = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
  const recente = media(vals.slice(-janela));
  const anterior = media(vals.slice(-2 * janela, -janela));
  const pct = deltaPct(recente, anterior);
  const [um, varios] = UNIDADE[granularidade] ?? UNIDADE.day;
  const nome = janela === 1 ? um : varios;
  return {
    pontos: vals,
    variacaoPct: pct,
    direcao: direcao(pct),
    rotulo: `últimos ${janela} ${nome} vs ${janela} anteriores`,
  };
}

/** Ticket ponto a ponto: GMV / Pedidos onde os dois existem e ha' pedido. */
export function ticketPorPonto(gmv: (number | null)[], pedidos: (number | null)[]): (number | null)[] {
  const n = Math.min(gmv.length, pedidos.length);
  const saida: (number | null)[] = [];
  for (let i = 0; i < n; i++) {
    const g = gmv[i];
    const p = pedidos[i];
    saida.push(g != null && p != null && p > 0 ? g / p : null);
  }
  return saida;
}

export function tendenciasDosKpis(
  gmv: { buckets: Pick<MergedBucket, "total">[]; granularity: string },
  pedidos: { buckets: Pick<MergedBucket, "total">[]; granularity: string },
): { orders: TendenciaKpi | null; avg_ticket: TendenciaKpi | null } {
  // O GMV da Gerencial e' o numero do bloco principal, ao lado do grafico de
  // evolucao: a faixa de cards nao o mostra, entao nao se calcula tendencia
  // dele aqui. O GMV entra so' para o ticket (GMV / pedidos).
  const g = totaisDaSerie(gmv.buckets);
  const p = totaisDaSerie(pedidos.buckets);
  return {
    orders: tendencia(p, pedidos.granularity),
    avg_ticket: tendencia(ticketPorPonto(g, p), pedidos.granularity),
  };
}

/**
 * Linha do mini grafico: media movel de `janela` pontos. A serie diaria oscila
 * dia a dia (fim de semana, campanha) e a linha crua vira serrilha; suavizada,
 * ela mostra so' o rumo, que e' o que o indicativo quer dizer. Com poucos
 * pontos (menos de 2 janelas), devolve a serie como esta'.
 */
export function suavizar(pontos: number[], janela = 7): number[] {
  if (pontos.length < janela * 2) return pontos;
  const saida: number[] = [];
  for (let i = janela - 1; i < pontos.length; i++) {
    let soma = 0;
    for (let j = i - janela + 1; j <= i; j++) soma += pontos[j];
    saida.push(soma / janela);
  }
  return saida;
}
