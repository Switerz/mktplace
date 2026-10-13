/**
 * UX-TORRE-1 — paleta dos graficos nos dois temas.
 *
 * O recharts pinta SVG: `stroke` e `fill` precisam de uma cor resolvida, e uma
 * `var(--tc-chart-1)` aplicada num atributo SVG nao e' herdada por todos os
 * navegadores. Entao os valores vivem aqui, espelhando os tokens de
 * `globals.css` — a fonte da verdade continua sendo um par (papel, tema), e
 * nenhum componente escolhe cor por conta propria.
 *
 * As series sao coloridas por POSICAO. Verde, ambar e vermelho ficam de fora:
 * sao estado, e uma conta nao e' um estado.
 *
 * UX-TORRE-2 acrescentou `canais` e `total`: as linhas por canal da Gerencial
 * sao IDENTIDADE (TikTok e' violeta em toda a Torre), nao posicao. No claro os
 * valores sao exatamente os hex que `CHANNEL_STROKE` usava.
 */
import type { TemaResolvido } from "./theme";

export type CanalDoGrafico = "tiktok" | "ml" | "shopee";

export interface TemaGrafico {
  series: readonly string[];
  /** Espelha `--tc-canal-*`. */
  canais: Readonly<Record<CanalDoGrafico, string>>;
  /** Linha de total/comparacao: mais forte que qualquer canal. */
  total: string;
  /** Serie de referencia apagada (periodo anterior). */
  referencia: string;
  grade: string;
  eixo: string;
  tooltipFundo: string;
  tooltipBorda: string;
  tooltipTinta: string;
}

const CLARO: TemaGrafico = {
  series: ["#6d28d9", "#0284c7", "#64748b", "#a855f7", "#0891b2", "#475569"],
  canais: { tiktok: "#8b5cf6", ml: "#06b6d4", shopee: "#f97316" },
  total: "#4c1d95",
  referencia: "#94a3b8",
  grade: "#e7e3f5",
  eixo: "#6c6582",
  tooltipFundo: "#ffffff",
  tooltipBorda: "#d6d0ec",
  tooltipTinta: "#171226",
};

const ESCURO: TemaGrafico = {
  series: ["#a78bfa", "#38bdf8", "#94a3b8", "#d9b4ff", "#22d3ee", "#cbd5e1"],
  canais: { tiktok: "#a78bfa", ml: "#22d3ee", shopee: "#fb923c" },
  total: "#ddd2fe",
  referencia: "#7d7697",
  grade: "#2c2544",
  eixo: "#968db2",
  tooltipFundo: "#1f1a33",
  tooltipBorda: "#3d3459",
  tooltipTinta: "#f2effa",
};

export function temaDoGrafico(resolvido: TemaResolvido): TemaGrafico {
  return resolvido === "dark" ? ESCURO : CLARO;
}

/** Cor da serie na posicao `i`, com retorno circular. */
export function corDaSerie(tema: TemaGrafico, i: number): string {
  return tema.series[i % tema.series.length];
}
