/**
 * UX-TORRE-1 — vocabulario de severidade.
 *
 * `neutro` nao e' ausencia de estilo: e' a afirmacao de que NAO HA regra de
 * negocio dizendo que aquele numero e' bom ou ruim. Cor de severidade sem
 * regra por tras e' decoracao, e decoracao em tela operacional vira alarme
 * falso.
 *
 * `ausente` e' o quarto tom, e existe porque "nao medido" nao e' "zero": ele
 * apaga o valor em vez de pinta-lo de verde.
 */
export type Tom = "neutro" | "bom" | "atencao" | "critico" | "ausente";

export const TOM_SUPERFICIE: Record<Tom, string> = {
  neutro: "border-line bg-surface",
  bom: "border-ok/35 bg-ok-soft",
  atencao: "border-warn/40 bg-warn-soft",
  critico: "border-crit/40 bg-crit-soft",
  ausente: "border-line border-dashed bg-surface",
};

export const TOM_TINTA: Record<Tom, string> = {
  neutro: "text-ink",
  bom: "text-ok-ink",
  atencao: "text-warn-ink",
  critico: "text-crit-ink",
  ausente: "text-ink-faint",
};

export const TOM_PONTO: Record<Tom, string> = {
  neutro: "bg-ink-faint",
  bom: "bg-ok",
  atencao: "bg-warn",
  critico: "bg-crit",
  ausente: "bg-ink-faint/50",
};

export const TOM_SELO: Record<Tom, string> = {
  neutro: "bg-raised text-ink-muted ring-line-strong",
  bom: "bg-ok-soft text-ok-ink ring-ok/30",
  atencao: "bg-warn-soft text-warn-ink ring-warn/30",
  critico: "bg-crit-soft text-crit-ink ring-crit/30",
  ausente: "bg-raised text-ink-faint ring-line",
};
