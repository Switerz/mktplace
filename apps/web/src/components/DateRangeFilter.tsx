"use client";

import { useEffect, useState } from "react";
import { DATE_PRESET_OPTIONS, detectPreset, presetRange, toISODate } from "@/lib/filters/presets";
import { validateDateRange } from "@/lib/filters/url-state";
import type { DatePreset } from "@/lib/filters/types";

interface Props {
  dateFrom: string;
  dateTo: string;
  compare: boolean;
  onChange: (v: { dateFrom: string; dateTo: string }) => void;
  onCompareChange: (v: boolean) => void;
  /** Esconde o toggle de comparação em telas onde a fonte não calcula deltas
   * por período (ver docs/filtros_globais_contrato.md). */
  hideCompare?: boolean;
}

export default function DateRangeFilter({
  dateFrom, dateTo, compare, onChange, onCompareChange, hideCompare,
}: Props) {
  const activePreset = detectPreset(dateFrom, dateTo);
  const [showCustom, setShowCustom] = useState(activePreset === "personalizado");
  const [error, setError] = useState<string | null>(null);
  const todayIso = toISODate(new Date());

  // Sincroniza showCustom quando dateFrom/dateTo mudam por causa externa a
  // este componente (navegação back/forward, troca de marca/tela que
  // preserva a querystring, link direto) — sem isto, voltar para uma URL
  // com intervalo personalizado continuava mostrando os botões de preset
  // sem revelar os campos de data.
  useEffect(() => {
    setShowCustom(detectPreset(dateFrom, dateTo) === "personalizado");
    setError(null);
  }, [dateFrom, dateTo]);

  function selectPreset(preset: DatePreset) {
    if (preset === "personalizado") {
      setShowCustom(true);
      return;
    }
    setShowCustom(false);
    setError(null);
    onChange(presetRange(preset));
  }

  function applyCustomChange(next: { dateFrom: string; dateTo: string }) {
    const validation = validateDateRange(next.dateFrom, next.dateTo);
    if (!validation.valid) {
      setError(validation.error ?? "Intervalo inválido.");
      return;
    }
    setError(null);
    onChange(next);
  }

  return (
    <div className="flex flex-col gap-2 min-w-0 max-w-full">
      <div className="flex items-center gap-2 min-w-0 flex-wrap">
        <span className="text-xs text-slate-500 font-medium shrink-0">Período</span>
        <div
          role="group"
          aria-label="Presets de período"
          className="flex gap-1 bg-white border border-violet-100 rounded-xl p-1 shadow-sm overflow-x-auto min-w-0 max-w-full"
        >
          {DATE_PRESET_OPTIONS.map((opt) => {
            const active = showCustom ? opt.value === "personalizado" : activePreset === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                aria-pressed={active}
                onClick={() => selectPreset(opt.value)}
                className={`px-3 py-1.5 rounded-lg text-sm font-semibold whitespace-nowrap transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-1 ${
                  active ? "bg-violet-600 text-white shadow" : "text-violet-700 hover:bg-violet-50"
                }`}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
        {!hideCompare && (
          <label className="flex items-center gap-1.5 text-xs text-slate-600 font-medium ml-1 select-none cursor-pointer">
            <input
              type="checkbox"
              checked={compare}
              onChange={(e) => onCompareChange(e.target.checked)}
              className="rounded border-violet-300 text-violet-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
            />
            Comparar com período anterior
          </label>
        )}
      </div>
      {showCustom && (
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap min-w-0">
            <label className="flex items-center gap-1.5 text-xs text-slate-500 min-w-0">
              De
              <input
                type="date"
                value={dateFrom}
                max={dateTo < todayIso ? dateTo : todayIso}
                onChange={(e) => e.target.value && applyCustomChange({ dateFrom: e.target.value, dateTo })}
                className="border border-violet-200 rounded-lg px-2 py-1 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 min-w-0 max-w-full"
              />
            </label>
            <label className="flex items-center gap-1.5 text-xs text-slate-500 min-w-0">
              Até
              <input
                type="date"
                value={dateTo}
                min={dateFrom}
                max={todayIso}
                onChange={(e) => e.target.value && applyCustomChange({ dateFrom, dateTo: e.target.value })}
                className="border border-violet-200 rounded-lg px-2 py-1 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 min-w-0 max-w-full"
              />
            </label>
          </div>
          {error && (
            <p role="alert" className="text-xs text-rose-600 font-medium">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
