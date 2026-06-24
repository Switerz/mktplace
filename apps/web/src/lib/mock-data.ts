// Dados reais extraídos do profiling do Data Mart (2026-06-16)
// Fonte: gold.tiktok_brand_daily e gold.ml_gestao_diaria

export type Marketplace = "tiktok" | "ml" | "shopee";

export interface BrandMonthly {
  brand: string;
  label: string;
  tiktok: number | null;
  ml: number | null;
  tiktokPrev: number | null;
  mlPrev: number | null;
  tiktokOrders: number | null;
  mlOrders: number | null;
}

export interface MonthlyGmv {
  mes: string;
  mesLabel: string;
  barbours: number;
  kokeshi: number;
  apice: number;
  lescent: number;
  rituaria: number;
}

// Mai/26 (mês de referência) e Abr/26 (mês anterior)
export const BRANDS: BrandMonthly[] = [
  {
    brand: "barbours",
    label: "BARBOURS",
    tiktok: 9_709_786,
    ml: 2_578_760,
    tiktokPrev: 9_166_934,
    mlPrev: 1_958_271,
    tiktokOrders: 185_000,
    mlOrders: 21_000,
  },
  {
    brand: "kokeshi",
    label: "KOKESHI",
    tiktok: 2_316_329,
    ml: 789_678,
    tiktokPrev: 2_294_455,
    mlPrev: 166_125,
    tiktokOrders: 44_000,
    mlOrders: 11_000,
  },
  {
    brand: "apice",
    label: "ÁPICE",
    tiktok: 876_174,
    ml: null,
    tiktokPrev: 637_021,
    mlPrev: null,
    tiktokOrders: 14_200,
    mlOrders: null,
  },
  {
    brand: "lescent",
    label: "LESCENT",
    tiktok: 253_922,
    ml: 510_206,
    tiktokPrev: 283_203,
    mlPrev: 100_412,
    tiktokOrders: 5_900,
    mlOrders: 7_400,
  },
  {
    brand: "rituaria",
    label: "RITUÁRIA",
    tiktok: 239_773,
    ml: null,
    tiktokPrev: 154_687,
    mlPrev: null,
    tiktokOrders: 3_800,
    mlOrders: null,
  },
];

// Evolução GMV TikTok + ML consolidado por mês (últimos 6 meses)
export const GMV_MONTHLY: MonthlyGmv[] = [
  { mes: "2025-12", mesLabel: "Dez/25", barbours: 3_090_000, kokeshi: 987_000, apice: 323_000, lescent: 498_000, rituaria: 71_000 },
  { mes: "2026-01", mesLabel: "Jan/26", barbours: 6_840_000, kokeshi: 1_612_000, apice: 296_000, lescent: 408_000, rituaria: 92_000 },
  { mes: "2026-02", mesLabel: "Fev/26", barbours: 11_786_000, kokeshi: 1_491_000, apice: 235_000, lescent: 298_000, rituaria: 87_000 },
  { mes: "2026-03", mesLabel: "Mar/26", barbours: 13_686_000, kokeshi: 2_348_000, apice: 436_000, lescent: 468_000, rituaria: 92_000 },
  { mes: "2026-04", mesLabel: "Abr/26", barbours: 11_125_000, kokeshi: 2_460_000, apice: 637_000, lescent: 383_000, rituaria: 155_000 },
  { mes: "2026-05", mesLabel: "Mai/26", barbours: 12_289_000, kokeshi: 3_106_000, apice: 876_000, lescent: 764_000, rituaria: 240_000 },
];

export const REF_MONTH = "Mai/26";
export const PREV_MONTH = "Abr/26";

// Totais do mês de referência (mai/26)
export function totalGmv(filter: Marketplace | "all"): number {
  return BRANDS.reduce((sum, b) => {
    const tiktok = b.tiktok ?? 0;
    const ml = b.ml ?? 0;
    if (filter === "tiktok") return sum + tiktok;
    if (filter === "ml") return sum + ml;
    return sum + tiktok + ml;
  }, 0);
}

export function totalGmvPrev(filter: Marketplace | "all"): number {
  return BRANDS.reduce((sum, b) => {
    const tiktok = b.tiktokPrev ?? 0;
    const ml = b.mlPrev ?? 0;
    if (filter === "tiktok") return sum + tiktok;
    if (filter === "ml") return sum + ml;
    return sum + tiktok + ml;
  }, 0);
}

export function totalOrders(filter: Marketplace | "all"): number {
  return BRANDS.reduce((sum, b) => {
    const tiktok = b.tiktokOrders ?? 0;
    const ml = b.mlOrders ?? 0;
    if (filter === "tiktok") return sum + tiktok;
    if (filter === "ml") return sum + ml;
    return sum + tiktok + ml;
  }, 0);
}

export function avgTicket(filter: Marketplace | "all"): number {
  const g = totalGmv(filter);
  const o = totalOrders(filter);
  return o > 0 ? g / o : 0;
}

