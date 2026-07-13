"use client";

import { AVAILABLE_MONTHS } from "@/lib/mock-daily";
import type { MonthOption } from "@/lib/produtos-tab-transition";

interface Props {
  value: string;
  onChange: (v: string) => void;
  // Default preserva o comportamento existente (lista fixa do mock-daily).
  // Paginas com meses gerados dinamicamente (ex.: Produtos) passam a sua
  // propria lista aqui em vez de depender da constante hardcoded.
  months?: MonthOption[];
}

export default function PeriodSelector({ value, onChange, months = AVAILABLE_MONTHS }: Props) {
  return (
    <div className="flex items-center gap-2 min-w-0">
      <span className="text-xs text-slate-500 font-medium shrink-0">Período</span>
      <div className="flex gap-1 bg-white border border-violet-100 rounded-xl p-1 shadow-sm overflow-x-auto">
        {months.map((m) => (
          <button
            key={m.value}
            onClick={() => onChange(m.value)}
            className={`px-3 py-1.5 rounded-lg text-sm font-semibold whitespace-nowrap transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-1 ${
              value === m.value
                ? "bg-violet-600 text-white shadow"
                : "text-violet-700 hover:bg-violet-50"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>
    </div>
  );
}
