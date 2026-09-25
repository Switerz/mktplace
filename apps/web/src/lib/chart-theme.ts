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
 */
import type { TemaResolvido } from "./theme";

export interface TemaGrafico {
  series: readonly string[];
  grade: string;
  eixo: string;
  tooltipFundo: string;
  tooltipBorda: string;
  tooltipTinta: string;
}

const CLARO: TemaGrafico = {
  series: ["#6d28d9", "#0284c7", "#64748b", "#a855f7", "#0891b2", "#475569"],
  grade: "#e7e3f5",
  eixo: "#6c6582",
  tooltipFundo: "#ffffff",
  tooltipBorda: "#d6d0ec",
  tooltipTinta: "#171226",
};

const ESCURO: TemaGrafico = {
  series: ["#a78bfa", "#38bdf8", "#94a3b8", "#d9b4ff", "#22d3ee", "#cbd5e1"],
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
