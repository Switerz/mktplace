"use client";

import { ALL_BRAND_OPTIONS } from "@/lib/filters/brands";
import { toggleMultiSelect } from "@/lib/filters/multi-select";

interface Props {
  /** Marcas selecionadas (brand_key). Vazio = todas. */
  value: string[];
  onChange: (v: string[]) => void;
}

const BASE_BTN =
  "px-3 py-2 rounded-lg text-sm font-semibold transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-1 whitespace-nowrap";
const ACTIVE_BTN = "bg-violet-600 text-white shadow";
const INACTIVE_BTN = "text-violet-700 hover:bg-violet-50";

export default function BrandFilter({ value, onChange }: Props) {
  const allActive = value.length === 0;

  return (
    <div
      role="group"
      aria-label="Filtro de marcas"
      className="flex gap-2 bg-white border border-violet-100 rounded-xl p-1 shadow-sm w-fit overflow-x-auto min-w-0 max-w-full"
    >
      <button
        type="button"
        aria-pressed={allActive}
        onClick={() => onChange([])}
        className={`${BASE_BTN} ${allActive ? ACTIVE_BTN : INACTIVE_BTN}`}
      >
        Todas
      </button>
      {ALL_BRAND_OPTIONS.map((opt) => {
        const active = value.includes(opt.value);
        return (
          <button
            key={opt.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(toggleMultiSelect(value, opt.value))}
            className={`${BASE_BTN} ${active ? ACTIVE_BTN : INACTIVE_BTN}`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
