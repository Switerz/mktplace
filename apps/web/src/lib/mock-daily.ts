// Gera dados diários de demonstração para a página de brand
// baseados nos totais mensais reais do profiling

import type { MonthPoint } from "./api-client";

export interface DailyRow {
  date: string;       // "2026-05-01"
  tiktok_gmv: number | null;
  ml_gmv: number | null;
  total_gmv: number;
  orders: number;
  avg_ticket: number | null;
  ad_spend: number | null;
}

// Totais mensais por brand (TikTok) — últimos 60 dias abrange mai e abr/26
const TIKTOK_MONTHLY: Record<string, Record<string, number>> = {
  "2026-04": { barbours: 9_166_934, kokeshi: 2_294_455, apice: 637_021, lescent: 283_203, rituaria: 154_687 },
  "2026-05": { barbours: 9_709_786, kokeshi: 2_316_329, apice: 876_174, lescent: 253_922, rituaria: 239_773 },
};

const ML_MONTHLY: Record<string, Record<string, number>> = {
  "2026-04": { barbours: 1_958_271, kokeshi: 166_125, lescent: 100_412 },
  "2026-05": { barbours: 2_578_760, kokeshi: 789_678, lescent: 510_206 },
};

// Pesos por dia da semana (dom=0..sab=6) — sexta e sábado tendem a ser maiores no TikTok
const DAY_WEIGHTS = [0.80, 0.90, 1.00, 1.05, 1.15, 1.30, 1.10];

function deterministicVariance(date: string, seed: number): number {
  // Pseudorandom determinístico para evitar SSR mismatch
  const d = parseInt(date.replace(/-/g, ""), 10);
  const x = Math.sin(d * seed * 9301 + 49297) * 233280;
  return 0.75 + ((x - Math.floor(x)) * 0.5); // 0.75 a 1.25
}

export function generateDailyData(brand: string, daysBack: number = 60): DailyRow[] {
  const rows: DailyRow[] = [];
  const today = new Date();

  for (let i = daysBack - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const dateStr = d.toISOString().slice(0, 10);
    const monthKey = dateStr.slice(0, 7);
    const dayOfWeek = d.getDay();
    const daysInMonth = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();

    const tkMonthly = TIKTOK_MONTHLY[monthKey]?.[brand] ?? 0;
    const mlMonthly = ML_MONTHLY[monthKey]?.[brand] ?? 0;

    const weight = DAY_WEIGHTS[dayOfWeek];
    const tkBase = tkMonthly > 0 ? (tkMonthly / daysInMonth) * weight : 0;
    const mlBase = mlMonthly > 0 ? (mlMonthly / daysInMonth) * weight : 0;

    const tkGmv = tkBase > 0 ? Math.round(tkBase * deterministicVariance(dateStr, 1)) : null;
    const mlGmv = mlBase > 0 ? Math.round(mlBase * deterministicVariance(dateStr, 2)) : null;
    const total = (tkGmv ?? 0) + (mlGmv ?? 0);

    // Ticket médio estimado por brand
    const tickets: Record<string, number> = {
      barbours: 52, kokeshi: 41, apice: 62, lescent: 44, rituaria: 73,
    };
    const ticket = tickets[brand] ?? 50;
    const orders = total > 0 ? Math.round(total / ticket) : 0;
    const adSpend = mlGmv != null ? Math.round(mlGmv * 0.06) : null;

    rows.push({
      date: dateStr,
      tiktok_gmv: tkGmv,
      ml_gmv: mlGmv,
      total_gmv: total,
      orders,
      avg_ticket: orders > 0 ? Math.round(total / orders) : null,
      ad_spend: adSpend,
    });
  }
  return rows;
}

// Retorna os últimos N meses disponíveis para o seletor de período
export const AVAILABLE_MONTHS = [
  { value: "2026-05", label: "Mai/26" },
  { value: "2026-06", label: "Jun/26 (atual)" },
  { value: "2026-04", label: "Abr/26" },
  { value: "2026-03", label: "Mar/26" },
  { value: "2026-02", label: "Fev/26" },
  { value: "2026-01", label: "Jan/26" },
  { value: "2025-12", label: "Dez/25" },
];
