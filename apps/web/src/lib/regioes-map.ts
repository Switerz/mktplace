import type { CoverageLevel } from "./api-client";

/**
 * Cartograma em grade (grid map) do Brasil por UF — NÃO é uma projeção
 * geográfica precisa. Posições em (col, row) aproximam a posição relativa
 * real de cada estado por região (Norte no topo, Nordeste no arco superior
 * direito, Centro-Oeste no meio, Sudeste/Sul na parte inferior), o
 * suficiente para reconhecimento visual num dashboard sem depender de
 * geometria SVG real (que exigiria um dataset topográfico externo — fora do
 * escopo "sem dependência pesada" / "sem baixar assets externos em
 * runtime" deste Gate). Pode ser substituído por um SVG geográfico real no
 * futuro sem mudar o contrato deste módulo (computeGmvIntensity/
 * intensityToColor continuam válidos para qualquer forma de apresentação).
 *
 * `XX` (UF desconhecida) deliberadamente NÃO entra nesta grade — é sempre
 * exibida separadamente (ver RegioesBrazilMap.tsx).
 */
export interface UfPosition {
  uf: string;
  col: number;
  row: number;
}

export const BRAZIL_UF_GRID: readonly UfPosition[] = [
  { uf: "RR", col: 2, row: 0 },
  { uf: "AM", col: 1, row: 1 },
  { uf: "PA", col: 3, row: 1 },
  { uf: "AP", col: 4, row: 1 },
  { uf: "RO", col: 1, row: 2 },
  { uf: "MA", col: 4, row: 2 },
  { uf: "PI", col: 5, row: 2 },
  { uf: "CE", col: 6, row: 2 },
  { uf: "RN", col: 7, row: 2 },
  { uf: "AC", col: 0, row: 3 },
  { uf: "MT", col: 2, row: 3 },
  { uf: "TO", col: 3, row: 3 },
  { uf: "BA", col: 5, row: 3 },
  { uf: "PE", col: 6, row: 3 },
  { uf: "PB", col: 7, row: 3 },
  { uf: "MS", col: 2, row: 4 },
  { uf: "GO", col: 3, row: 4 },
  { uf: "DF", col: 4, row: 4 },
  { uf: "SE", col: 5, row: 4 },
  { uf: "AL", col: 6, row: 4 },
  { uf: "MG", col: 4, row: 5 },
  { uf: "ES", col: 5, row: 5 },
  { uf: "SP", col: 3, row: 6 },
  { uf: "RJ", col: 4, row: 6 },
  { uf: "PR", col: 3, row: 7 },
  { uf: "SC", col: 3, row: 8 },
  { uf: "RS", col: 3, row: 9 },
] as const;

/**
 * Intensidade de GMV (0 a 1) relativa ao MAIOR gmv presente no conjunto
 * filtrado — mesma normalização de qualquer choropleth padrão (maior valor
 * = intensidade maxima). `XX` e' sempre excluida do resultado (nunca entra
 * no mapa). UFs com gmv <= 0 (ou ausentes do conjunto) recebem intensidade
 * 0 — estado neutro, nunca uma cor "quase zero" enganosa.
 */
export function computeGmvIntensity(rows: readonly { uf: string; gmv: number }[]): Map<string, number> {
  const known = rows.filter((r) => r.uf !== "XX");
  const max = known.reduce((m, r) => Math.max(m, r.gmv > 0 ? r.gmv : 0), 0);
  const result = new Map<string, number>();
  for (const r of known) {
    result.set(r.uf, max > 0 && r.gmv > 0 ? r.gmv / max : 0);
  }
  return result;
}

const NEUTRAL_COLOR = "rgb(238, 242, 247)"; // slate-100-ish — estado neutro (zero/ausente)
const LOW_COLOR = { r: 0xed, g: 0xe9, b: 0xfe }; // violet-100
const HIGH_COLOR = { r: 0x5b, g: 0x21, b: 0xb6 }; // violet-800

/** Interpola entre um lilás claro (baixa intensidade) e violeta forte (alta
 * intensidade). Intensidade <= 0 vira a cor neutra explícita (nunca "quase
 * a cor 0 do gradiente", que ficaria visualmente indistinguível de dado
 * real muito baixo). Clampa intensidade fora de [0,1]. */
export function intensityToColor(intensity: number): string {
  if (!(intensity > 0)) return NEUTRAL_COLOR;
  const t = Math.max(0, Math.min(1, intensity));
  const r = Math.round(LOW_COLOR.r + (HIGH_COLOR.r - LOW_COLOR.r) * t);
  const g = Math.round(LOW_COLOR.g + (HIGH_COLOR.g - LOW_COLOR.g) * t);
  const b = Math.round(LOW_COLOR.b + (HIGH_COLOR.b - LOW_COLOR.b) * t);
  return `rgb(${r}, ${g}, ${b})`;
}

/** Cor de texto com contraste razoável sobre intensityToColor — texto claro
 * quando o fundo fica escuro o suficiente, texto escuro caso contrário. */
export function textColorForIntensity(intensity: number): string {
  return intensity > 0.55 ? "#ffffff" : "#312e81";
}

/**
 * Marcador textual de cobertura — NUNCA depende só da cor de fundo (ver
 * requisito de acessibilidade). "ok" não tem marcador (estado normal,
 * silencioso); os demais níveis têm símbolos distintos entre si.
 */
export function coverageGlyph(level: CoverageLevel): string {
  switch (level) {
    case "ok": return "";
    case "partial": return "!";
    case "low": return "!!";
    case "not_applicable": return "–";
  }
}
