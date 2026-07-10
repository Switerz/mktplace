import type { CoverageLevel } from "./api-client";

/**
 * Utilidades de apresentação do mapa regional por UF — normalização de
 * intensidade de GMV, escala de cor e ranking. A geometria real do mapa
 * (paths SVG por UF, com projeção geográfica de fato) vive em
 * ./brazil-uf-paths; este módulo não depende de nenhuma forma de
 * apresentação específica (grade, SVG, etc.) e continua valendo tal como
 * antes da troca do cartograma em grade pelo mapa real.
 *
 * `XX` (UF desconhecida) é deliberadamente excluída de toda normalização
 * aqui — é sempre exibida separadamente, fora do mapa (ver
 * RegioesBrazilMap.tsx).
 */

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

/**
 * Cor de contorno por nível de cobertura — sinal ADICIONAL à cor de
 * preenchimento (que continua representando GMV), nunca a substitui.
 * "ok"/"not_applicable" usam o contorno neutro padrão do mapa.
 */
export function coverageStrokeColor(level: CoverageLevel): string | null {
  switch (level) {
    case "low": return "#e11d48"; // rose-600
    case "partial": return "#d97706"; // amber-600
    default: return null;
  }
}

/**
 * Top N UFs por GMV, maior primeiro — usado no painel de detalhe quando
 * nenhuma UF está em foco/selecionada. `XX` é sempre excluída (não é uma UF
 * do mapa). Empates mantêm a ordem original (sort estável).
 */
export function topUfsByGmv<T extends { uf: string; gmv: number }>(rows: readonly T[], limit: number): T[] {
  return rows
    .filter((r) => r.uf !== "XX")
    .slice()
    .sort((a, b) => b.gmv - a.gmv)
    .slice(0, Math.max(0, limit));
}
