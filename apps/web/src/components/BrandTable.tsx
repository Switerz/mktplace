"use client";

import Link from "next/link";
import type { BrandRow } from "@/lib/api-client";
import type { Marketplace } from "@/lib/mock-data";
import { fmtBrl, fmtNumber } from "@/lib/formatters";

type Filter = Marketplace | "all";

interface Props {
  brands: BrandRow[];
  filter: Filter;
  loading?: boolean;
  periodLabel?: string;
}

const BRAND_INITIALS: Record<string, string> = {
  barbours: "BA",
  kokeshi: "KO",
  apice: "ÁP",
  lescent: "LE",
  rituaria: "RI",
};

const BRAND_COLORS: Record<string, string> = {
  barbours: "bg-violet-600",
  kokeshi: "bg-cyan-500",
  apice: "bg-amber-500",
  lescent: "bg-pink-500",
  rituaria: "bg-emerald-500",
};

export default function BrandTable({ brands, filter, loading = false, periodLabel }: Props) {
  return (
    <div className={`bg-white rounded-2xl shadow-sm border border-violet-100 overflow-hidden transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}>
      <div className="px-5 py-4 border-b border-violet-100">
        <h2 className="text-sm font-semibold text-slate-700">
          Performance por Brand{periodLabel ? ` — ${periodLabel}` : ""}
        </h2>
      </div>
      <table className="w-full">
        <thead>
          <tr className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
            <th className="text-left px-5 py-3">Brand</th>
            {filter === "all" && (
              <>
                <th className="text-right px-4 py-3">TikTok GMV</th>
                <th className="text-right px-4 py-3">ML GMV</th>
              </>
            )}
            <th className="text-right px-4 py-3">GMV Total</th>
            <th className="text-right px-4 py-3">Pedidos</th>
            <th className="text-right px-4 py-3">Ticket Médio</th>
            <th className="text-right px-5 py-3">vs Mês Ant.</th>
          </tr>
        </thead>
        <tbody>
          {brands.length === 0 && (
            <tr>
              <td colSpan={7} className="text-center py-10 text-slate-400 text-sm">
                {loading ? "Carregando..." : "Sem dados"}
              </td>
            </tr>
          )}
          {brands.map((b, i) => {
            const momColor =
              b.mom_pct == null ? "text-slate-300" : b.mom_pct >= 0 ? "text-emerald-600" : "text-red-500";
            const momArrow = b.mom_pct == null ? "" : b.mom_pct >= 0 ? "▲" : "▼";
            const momText =
              b.mom_pct == null ? "—" : `${momArrow} ${Math.abs(b.mom_pct).toFixed(1)}%`;

            return (
              <tr
                key={b.brand}
                className={`border-t border-violet-100 hover:bg-violet-50/50 hover:shadow-[0_4px_12px_0_rgba(124,58,237,0.08),0_1px_3px_0_rgba(0,0,0,0.06)] transition-all duration-150 ${i % 2 === 0 ? "" : "bg-gray-50/30"}`}
              >
                <td className="px-5 py-4">
                  <div className="flex items-center gap-3">
                    <span
                      className={`w-9 h-9 rounded-xl ${BRAND_COLORS[b.brand] ?? "bg-gray-400"} flex items-center justify-center text-white text-xs font-bold flex-shrink-0`}
                    >
                      {BRAND_INITIALS[b.brand] ?? b.brand.substring(0, 2).toUpperCase()}
                    </span>
                    <Link
                      href={`/brand/${b.brand}`}
                      className="font-semibold text-gray-800 text-sm hover:text-violet-600 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-1 rounded"
                    >
                      {b.label}
                    </Link>
                  </div>
                </td>
                {filter === "all" && (
                  <>
                    <td className="text-right px-4 py-4 text-sm text-gray-600 tabular-nums">
                      {b.tiktok_gmv != null ? fmtBrl(b.tiktok_gmv) : <span className="text-slate-300">—</span>}
                    </td>
                    <td className="text-right px-4 py-4 text-sm text-gray-600 tabular-nums">
                      {b.ml_gmv != null ? fmtBrl(b.ml_gmv) : <span className="text-slate-300">—</span>}
                    </td>
                  </>
                )}
                <td className="text-right px-4 py-4 font-bold text-gray-900 text-sm tabular-nums">
                  {fmtBrl(b.total_gmv)}
                </td>
                <td className="text-right px-4 py-4 text-sm text-gray-600 tabular-nums">
                  {b.orders > 0 ? fmtNumber(b.orders) : <span className="text-slate-300">—</span>}
                </td>
                <td className="text-right px-4 py-4 text-sm text-gray-600 tabular-nums">
                  {b.avg_ticket != null ? fmtBrl(b.avg_ticket) : <span className="text-slate-300">—</span>}
                </td>
                <td className={`text-right px-5 py-4 text-sm font-semibold tabular-nums ${momColor}`}>
                  {momText}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
