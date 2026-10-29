export function fmtBrl(value: number): string {
  if (value >= 1_000_000) {
    return `R$ ${(value / 1_000_000).toFixed(1)}M`;
  }
  if (value >= 1_000) {
    return `R$ ${(value / 1_000).toFixed(0)}K`;
  }
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    minimumFractionDigits: 0,
  }).format(value);
}

export function fmtBrlFull(value: number): string {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    minimumFractionDigits: 0,
  }).format(value);
}

export function fmtNumber(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString("pt-BR");
}

export function fmtPct(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

export function calcMoM(current: number, prev: number): number {
  if (prev === 0) return 0;
  return ((current - prev) / prev) * 100;
}

// ---------------------------------------------------------------------------
// UI-DENSIDADE-1 — numero CURTO em pt-BR (escolha do proprietario: "so' o
// numero curto"). Virgula decimal e "mil / mi / bi", nunca "K/M" com ponto.
// O valor exato continua no detalhe de cada card.
// ---------------------------------------------------------------------------

const fmtDec = (min: number, max: number) =>
  new Intl.NumberFormat("pt-BR", { minimumFractionDigits: min, maximumFractionDigits: max });

// A unidade e' escolhida pelo valor JA' ARREDONDADO na unidade de baixo: sem
// isso, 999.960 virava "1.000,0 mil" em vez de "1,0 mi". Cada limiar e' o
// ponto em que o arredondamento da unidade menor chegaria a 1.000.

/** Contagem curta: "308,4 mil", "1,24 mi", "850". */
export function fmtNumCurto(valor: number): string {
  const abs = Math.abs(valor);
  if (abs >= 999_995_000) return `${fmtDec(1, 2).format(valor / 1e9)} bi`;
  if (abs >= 999_950) return `${fmtDec(1, 2).format(valor / 1e6)} mi`;
  if (abs >= 999.5) return `${fmtDec(1, 1).format(valor / 1e3)} mil`;
  return fmtDec(0, 0).format(valor);
}

/** Reais curtos: "R$ 345,3 mil", "R$ 19,61 mi"; abaixo de mil, com centavos ("R$ 63,59"). */
export function fmtBrlCurto(valor: number): string {
  const abs = Math.abs(valor);
  if (abs >= 999_995_000) return `R$ ${fmtDec(2, 2).format(valor / 1e9)} bi`;
  if (abs >= 999_950) return `R$ ${fmtDec(2, 2).format(valor / 1e6)} mi`;
  if (abs >= 999.995) return `R$ ${fmtDec(1, 1).format(valor / 1e3)} mil`;
  return `R$ ${fmtDec(2, 2).format(valor)}`;
}

/** Variacao em pt-BR com sinal tipografico: "+3,6%", "−11,1%", "0,0%". */
export function fmtPctBr(valor: number): string {
  const sinal = valor > 0 ? "+" : valor < 0 ? "−" : "";
  return `${sinal}${fmtDec(1, 1).format(Math.abs(valor))}%`;
}

/** Multiplicador (ROAS): "12,0x". */
export function fmtVezes(valor: number): string {
  return `${fmtDec(1, 1).format(valor)}x`;
}
