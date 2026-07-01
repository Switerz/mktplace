"use client";

import type { Marketplace } from "@/lib/mock-data";
import {
  DEFAULT_MARKETPLACE_SELECTION,
  isAllSelected,
  isMarketplaceSelected,
  toggleMarketplace,
  type MarketplaceSelection,
} from "@/lib/marketplace-filter";

const CHANNEL_OPTIONS: { value: Marketplace; label: string }[] = [
  { value: "tiktok", label: "TikTok Shop" },
  { value: "ml", label: "Mercado Livre" },
  { value: "shopee", label: "Shopee" },
];

interface Props {
  value: MarketplaceSelection;
  onChange: (v: MarketplaceSelection) => void;
}

const BASE_BTN =
  "px-4 py-2 rounded-lg text-sm font-semibold transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-1";
const ACTIVE_BTN = "bg-violet-600 text-white shadow";
const INACTIVE_BTN = "text-violet-700 hover:bg-violet-50";

export default function MarketplaceFilter({ value, onChange }: Props) {
  const allActive = isAllSelected(value);

  return (
    <div
      role="group"
      aria-label="Filtro de marketplaces"
      className="flex gap-2 bg-white border border-violet-100 rounded-xl p-1 shadow-sm w-fit"
    >
      <button
        type="button"
        aria-pressed={allActive}
        onClick={() => onChange([...DEFAULT_MARKETPLACE_SELECTION])}
        className={`${BASE_BTN} ${allActive ? ACTIVE_BTN : INACTIVE_BTN}`}
      >
        Todos
      </button>
      {CHANNEL_OPTIONS.map((opt) => {
        const active = isMarketplaceSelected(value, opt.value);
        return (
          <button
            key={opt.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(toggleMarketplace(value, opt.value))}
            className={`${BASE_BTN} ${active ? ACTIVE_BTN : INACTIVE_BTN}`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
