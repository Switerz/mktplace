"use client";

import { ALL_BRAND_OPTIONS } from "@/lib/filters/brands";
import { toggleMultiSelect } from "@/lib/filters/multi-select";

interface Props {
  /** Marcas selecionadas (brand_key). Vazio = todas. */
  value: string[];
  onChange: (v: string[]) => void;
}

/**
 * UX-TORRE-2: mesma aparencia e mesmo alvo do `MarketplaceFilter`. O botao
 * media ~36px (`py-2` + `line-height`) — agora o piso de 44px e' explicito.
 * "Todas" continua sendo a selecao vazia, nunca "nenhuma marca".
 */
const BASE_BTN =
  "inline-flex shrink-0 items-center justify-center min-h-11 min-w-11 px-3 rounded-md text-sm whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent";
const ACTIVE_BTN = "bg-surface font-semibold text-ink shadow-sm ring-1 ring-inset ring-line-strong";
const INACTIVE_BTN = "text-ink-muted hover:bg-surface/70 hover:text-ink";

export default function BrandFilter({ value, onChange }: Props) {
  const allActive = value.length === 0;

  return (
    <div
      role="group"
      aria-label="Filtro de marcas"
      className="flex gap-0.5 bg-raised border border-line rounded-lg p-0.5 w-fit overflow-x-auto min-w-0 max-w-full"
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
