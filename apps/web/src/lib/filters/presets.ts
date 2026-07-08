import type { DatePreset } from "./types";

/** Formata em YYYY-MM-DD usando componentes LOCAIS (nunca toISOString/UTC —
 * em fuso negativo como America/Sao_Paulo, toISOString() pode voltar um dia). */
export function toISODate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function endOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0);
}

function lastNDays(today: Date, n: number): { dateFrom: string; dateTo: string } {
  const from = new Date(today.getFullYear(), today.getMonth(), today.getDate() - (n - 1));
  return { dateFrom: toISODate(from), dateTo: toISODate(today) };
}

export function presetRange(preset: DatePreset, today: Date = new Date()): { dateFrom: string; dateTo: string } {
  switch (preset) {
    case "hoje": {
      const iso = toISODate(today);
      return { dateFrom: iso, dateTo: iso };
    }
    case "7d":
      return lastNDays(today, 7);
    case "90d":
      return lastNDays(today, 90);
    case "mes_atual":
      return { dateFrom: toISODate(startOfMonth(today)), dateTo: toISODate(today) };
    case "mes_anterior": {
      const anchor = new Date(today.getFullYear(), today.getMonth() - 1, 1);
      return { dateFrom: toISODate(startOfMonth(anchor)), dateTo: toISODate(endOfMonth(anchor)) };
    }
    case "30d":
    case "personalizado":
    default:
      return lastNDays(today, 30);
  }
}

export const DATE_PRESET_OPTIONS: { value: DatePreset; label: string }[] = [
  { value: "hoje", label: "Hoje" },
  { value: "7d", label: "7 dias" },
  { value: "30d", label: "30 dias" },
  { value: "90d", label: "90 dias" },
  { value: "mes_atual", label: "Mês atual" },
  { value: "mes_anterior", label: "Mês anterior" },
  { value: "personalizado", label: "Personalizado" },
];

/** Periodo imediatamente anterior de mesma duracao — espelha
 * resolve_previous_period no backend (app/deps/period.py). */
export function previousEquivalentRange(dateFrom: string, dateTo: string): { dateFrom: string; dateTo: string } {
  const from = new Date(`${dateFrom}T00:00:00`);
  const to = new Date(`${dateTo}T00:00:00`);
  const days = Math.round((to.getTime() - from.getTime()) / 86_400_000) + 1;
  const prevTo = new Date(from);
  prevTo.setDate(prevTo.getDate() - 1);
  const prevFrom = new Date(prevTo);
  prevFrom.setDate(prevFrom.getDate() - (days - 1));
  return { dateFrom: toISODate(prevFrom), dateTo: toISODate(prevTo) };
}

/** Detecta se um par (dateFrom, dateTo) corresponde a algum preset conhecido
 * (para destacar o botao ativo) — "personalizado" quando nao corresponde a
 * nenhum preset fixo. */
export function detectPreset(dateFrom: string, dateTo: string, today: Date = new Date()): DatePreset {
  for (const opt of DATE_PRESET_OPTIONS) {
    if (opt.value === "personalizado") continue;
    const range = presetRange(opt.value, today);
    if (range.dateFrom === dateFrom && range.dateTo === dateTo) return opt.value;
  }
  return "personalizado";
}
