// Metas mensais extraídas do arquivo [GoBeaute Marketplaces] Análise de métricas e resultados.xlsx
// Fonte: aba "Resultados & Metas 2026" — canais TikTok, ML e Shopee por marca
// Colunas: Jan-2026 → Dez-2026

export interface GoalEntry {
  tiktok: number | null;
  ml: number | null;
  shopee: number | null;
}

export type GoalMap = Record<string, GoalEntry>;

const MONTHS_2026 = [
  "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
  "2026-07", "2026-08", "2026-09", "2026-10", "2026-11", "2026-12",
];

const GOALS_RAW: Record<string, { tiktok: number[]; ml: (number | null)[]; shopee: (number | null)[] }> = {
  apice: {
    tiktok:  [275000, 150000, 300000, 440000, 484000, 532400, 585640, 644204, 708624, 850349, 1020419, 850349],
    ml:      Array(12).fill(null),
    shopee:  [60000, 90000, 120000, 234000, 257400, 283140, 311454, 342599, 376859, 452231, 542677, 452231],
  },
  barbours: {
    tiktok:  [3600000, 9000000, 12600000, 13860000, 15246000, 16770600, 18447660, 20292426, 22321669, 26786002, 32143203, 26786002],
    ml:      [800000,  900000,  2000000,  2200000,  2420000,  2662000,  2928200,  3221020,  3543122,  4251746,  5102096,  4251746],
    shopee:  [200000, 500000, 2200000, 2420000, 2662000, 2928200, 3221020, 3543122, 3897434, 4676921, 5612305, 4676921],
  },
  kokeshi: {
    tiktok:  [1100000, 1600000, 1760000, 2100000, 2400000, 2640000, 2904000, 3194400, 3513840, 4216608, 5059930, 4216608],
    ml:      [300000,  260000,  430000,  473000,  610000,  671000,  738100,  811910,  893101,  1071721, 1286065, 1071721],
    shopee:  [1100000, 1300000, 1400000, 1540000, 2300000, 2530000, 2783000, 3061300, 3367430, 4040916, 4849099, 4040916],
  },
  lescent: {
    tiktok:  [60000,  45000,  200000, 250000, 275000, 302500, 332750, 366025, 402628, 483153, 579784, 483153],
    ml:      [500000, 250000, 370000, 500000, 550000, 605000, 665500, 732050, 805255, 966306, 1159567, 966306],
    shopee:  [70000, 50000, 140000, 154000, 169400, 186340, 204974, 225471, 248018, 297622, 357146, 297622],
  },
  rituaria: {
    tiktok:  [100000, 90000,  100000, 110000, 121000, 210000, 231000, 254100, 279510, 335412, 402494, 335412],
    ml:      Array(12).fill(null),
    shopee:  [200000, 240000, 280000, 308000, 338800, 372680, 409948, 450942, 496037, 595244, 714293, 595244],
  },
};

export function getGoals(period: string): GoalMap {
  const idx = MONTHS_2026.indexOf(period);
  if (idx < 0) return {};
  return Object.fromEntries(
    Object.entries(GOALS_RAW).map(([brand, data]) => [
      brand,
      {
        tiktok: data.tiktok[idx] ?? null,
        ml: data.ml[idx] ?? null,
        shopee: data.shopee[idx] ?? null,
      } satisfies GoalEntry,
    ])
  );
}

export function PERIOD_LABEL(period: string): string {
  const labels: Record<string, string> = {
    "2026-01": "Jan/26", "2026-02": "Fev/26", "2026-03": "Mar/26",
    "2026-04": "Abr/26", "2026-05": "Mai/26", "2026-06": "Jun/26",
    "2026-07": "Jul/26", "2026-08": "Ago/26", "2026-09": "Set/26",
    "2026-10": "Out/26", "2026-11": "Nov/26", "2026-12": "Dez/26",
  };
  return labels[period] ?? period;
}
