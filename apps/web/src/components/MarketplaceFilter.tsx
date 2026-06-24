"use client";

import type { Marketplace } from "@/lib/mock-data";

type Filter = Marketplace | "all";

const OPTIONS: { value: Filter; label: string }[] = [
  { value: "all", label: "Todos" },
  { value: "tiktok", label: "TikTok Shop" },
  { value: "ml", label: "Mercado Livre" },
  { value: "shopee", label: "Shopee" },
];

interface Props {
  value: Filter;
  onChange: (v: Filter) => void;
}

export default function MarketplaceFilter({ value, onChange }: Props) {
  return (
    <div className="flex gap-2 bg-white border border-violet-100 rounded-xl p-1 shadow-sm w-fit">
      {OPTIONS.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={`px-4 py-2 rounded-lg text-sm font-semibold transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-1 ${
            value === opt.value
              ? "bg-violet-600 text-white shadow"
              : "text-violet-700 hover:bg-violet-50"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

