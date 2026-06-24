"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { BrandRow } from "@/lib/api-client";
import type { Marketplace } from "@/lib/mock-data";
import { getGoals } from "@/lib/goals-data";
import { fmtBrl, fmtNumber } from "@/lib/formatters";

type Filter = Marketplace | "all";

interface Props {
  brands: BrandRow[];
  filter: Filter;
  period: string;
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

function attainmentStyle(pct: number) {
  if (pct >= 1) return { bar: "bg-emerald-500", text: "text-emerald-700", bg: "bg-emerald-50" };
  if (pct >= 0.8) return { bar: "bg-amber-400", text: "text-amber-700", bg: "bg-amber-50" };
  return { bar: "bg-rose-500", text: "text-rose-700", bg: "bg-rose-50" };
}

function cosPctStyle(v: number | null): string {
  if (v == null) return "text-slate-400";
  if (v < 25) return "text-emerald-700 bg-emerald-50";
  if (v < 30) return "text-amber-700 bg-amber-50";
  return "text-rose-700 bg-rose-50";
}

// Formato compacto sem prefixo R$ — para colunas de canal
function fmtM(v: number | null): string | null {
  if (v == null) return null;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${Math.round(v / 1_000)}K`;
  return `${Math.round(v)}`;
}

function MiniBar({ actual, goal, mounted }: { actual: number | null; goal: number | null; mounted: boolean }) {
  if (!goal) return <span className="text-slate-300 text-xs select-none">—</span>;
  const a = actual ?? 0;
  const pct = a / goal;
  const barPct = Math.min(pct, 1);
  const { bar, text, bg } = attainmentStyle(pct);

  return (
    <div className="flex items-center gap-1 justify-end">
      <div className="w-10 bg-gray-100 rounded-full h-1.5 overflow-hidden shrink-0">
        <div
          className={`h-1.5 rounded-full transition-[width] duration-700 ease-out motion-reduce:transition-none ${bar}`}
          style={{ width: mounted ? `${barPct * 100}%` : "0%" }}
        />
      </div>
      <span className={`text-xs font-bold tabular-nums w-8 text-right rounded px-1 py-0.5 ${text} ${bg}`}>
        {(pct * 100).toFixed(0)}%
      </span>
    </div>
  );
}

export default function BrandPerformanceTable({ brands, filter, period, loading = false, periodLabel }: Props) {
  const goals = getGoals(period);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const raf = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  const visibleTk = filter !== "ml" && filter !== "shopee";
  const visibleMl = filter !== "tiktok" && filter !== "shopee";
  const visibleSh = filter !== "tiktok" && filter !== "ml";
  const hasGoals = Object.keys(goals).length > 0;

  const showTkGoal = hasGoals && visibleTk && brands.some((b) => goals[b.brand]?.tiktok != null);
  const showMlGoal = hasGoals && visibleMl && brands.some((b) => goals[b.brand]?.ml != null);
  const showShGoal = hasGoals && visibleSh && brands.some((b) => goals[b.brand]?.shopee != null);
  const showMlRoas = visibleMl && brands.some((b) => b.ml_roas != null);
  const showGpm = visibleTk && brands.some((b) => b.gpm != null);

  const totalActual = brands.reduce((s, b) => {
    let v = 0;
    if (visibleTk) v += b.tiktok_gmv ?? 0;
    if (visibleMl) v += b.ml_gmv ?? 0;
    if (visibleSh) v += b.shopee_gmv ?? 0;
    return s + v;
  }, 0);
  const totalGoal = brands.reduce((s, b) => {
    let v = 0;
    if (visibleTk) v += goals[b.brand]?.tiktok ?? 0;
    if (visibleMl) v += goals[b.brand]?.ml ?? 0;
    if (visibleSh) v += goals[b.brand]?.shopee ?? 0;
    return s + v;
  }, 0);
  const consolidatedPct = totalGoal > 0 ? totalActual / totalGoal : 0;
  const cs = attainmentStyle(consolidatedPct);

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-violet-100 overflow-hidden">
      {/* Header */}
      <div className="px-5 py-3.5 border-b border-violet-100 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-slate-700">
            Performance por Marca{periodLabel ? ` — ${periodLabel}` : ""}
          </h2>
          {loading && <span className="text-xs text-violet-400 animate-pulse">Atualizando...</span>}
        </div>
        {totalGoal > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400 hidden sm:block tabular-nums">
              {fmtBrl(totalActual)} / {fmtBrl(totalGoal)}
            </span>
            <span className={`text-xs font-bold tabular-nums px-2 py-0.5 rounded ${cs.text} ${cs.bg}`}>
              {(consolidatedPct * 100).toFixed(1)}% da meta
            </span>
          </div>
        )}
      </div>

      {/* Table — sem overflow-x para eliminar scroll lateral */}
      <table className="w-full" aria-label={`Performance por marca${periodLabel ? ` — ${periodLabel}` : ""}`}>
        <caption className="sr-only">
          Tabela de performance e atingimento de metas por marca{periodLabel ? ` referente a ${periodLabel}` : ""}
        </caption>
        <thead>
          <tr className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider bg-slate-50">
            <th className="text-left px-4 py-2.5">Marca</th>
            {filter === "all" && (
              <>
                <th className="text-right px-2 py-2.5">TikTok</th>
                <th className="text-right px-2 py-2.5">ML</th>
                <th className="text-right px-2 py-2.5">Shopee</th>
              </>
            )}
            <th className="text-right px-2 py-2.5">GMV Total</th>
            <th className="text-right px-2 py-2.5">Pedidos</th>
            <th className="text-right px-2 py-2.5">Ticket</th>
            <th className="text-right px-2 py-2.5">MoM</th>
            {visibleTk && <th className="text-right px-2 py-2.5">COS%</th>}
            {showGpm && <th className="text-right px-2 py-2.5">R$/1k</th>}
            {showMlRoas && <th className="text-right px-2 py-2.5">ROAS</th>}
            {showTkGoal && <th className="text-right px-3 py-2.5">Meta TK</th>}
            {showMlGoal && <th className="text-right px-3 py-2.5">Meta ML</th>}
            {showShGoal && <th className="text-right px-3 py-2.5">Meta SH</th>}
          </tr>
        </thead>
        <tbody>
          {brands.length === 0 && (
            <tr>
              <td colSpan={14} className="text-center py-10 text-slate-400 text-sm">
                {loading ? "Carregando..." : "Sem dados para o período"}
              </td>
            </tr>
          )}
          {brands.map((b, i) => {
            const momColor =
              b.mom_pct == null ? "text-slate-300" : b.mom_pct >= 0 ? "text-emerald-600" : "text-rose-600";
            const momArrow = b.mom_pct == null ? "" : b.mom_pct >= 0 ? "▲" : "▼";
            const momText =
              b.mom_pct == null ? "—" : `${momArrow} ${Math.abs(b.mom_pct).toFixed(1)}%`;
            const cosStyle = cosPctStyle(b.cos_pct);

            return (
              <tr
                key={b.brand}
                className={`border-t border-violet-100 hover:bg-violet-50/40 transition-colors duration-100 ${i % 2 === 0 ? "" : "bg-gray-50/30"}`}
              >
                {/* Marca */}
                <td className="px-4 py-2.5">
                  <div className="flex items-center gap-2.5">
                    <span className={`w-7 h-7 rounded-lg ${BRAND_COLORS[b.brand] ?? "bg-gray-400"} flex items-center justify-center text-white text-[10px] font-bold shrink-0`}>
                      {BRAND_INITIALS[b.brand] ?? b.brand.substring(0, 2).toUpperCase()}
                    </span>
                    <Link
                      href={`/brand/${b.brand}`}
                      className="font-semibold text-gray-800 text-xs hover:text-violet-600 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-1 rounded"
                    >
                      {b.label}
                    </Link>
                  </div>
                </td>

                {/* Colunas de canal — formato compacto sem R$ */}
                {filter === "all" && (
                  <>
                    <td className="text-right px-2 py-2.5 text-xs text-slate-500 tabular-nums">
                      {fmtM(b.tiktok_gmv) ?? <span className="text-slate-300">—</span>}
                    </td>
                    <td className="text-right px-2 py-2.5 text-xs text-slate-500 tabular-nums">
                      {fmtM(b.ml_gmv) ?? <span className="text-slate-300">—</span>}
                    </td>
                    <td className="text-right px-2 py-2.5 text-xs text-slate-500 tabular-nums">
                      {fmtM(b.shopee_gmv) ?? <span className="text-slate-300">—</span>}
                    </td>
                  </>
                )}

                {/* GMV Total */}
                <td className="text-right px-2 py-2.5 font-bold text-gray-900 text-sm tabular-nums whitespace-nowrap">
                  {fmtBrl(b.total_gmv)}
                </td>

                {/* Pedidos */}
                <td className="text-right px-2 py-2.5 text-xs text-slate-600 tabular-nums">
                  {b.orders > 0 ? fmtNumber(b.orders) : <span className="text-slate-300">—</span>}
                </td>

                {/* Ticket */}
                <td className="text-right px-2 py-2.5 text-xs text-slate-600 tabular-nums whitespace-nowrap">
                  {b.avg_ticket != null ? fmtBrl(b.avg_ticket) : <span className="text-slate-300">—</span>}
                </td>

                {/* MoM */}
                <td className={`text-right px-2 py-2.5 text-xs font-semibold tabular-nums ${momColor}`}>
                  {momText}
                </td>

                {/* COS% */}
                {visibleTk && (
                  <td className="text-right px-2 py-2.5">
                    {b.cos_pct != null ? (
                      <span className={`text-[10px] font-semibold tabular-nums px-1.5 py-0.5 rounded ${cosStyle}`}>
                        {b.cos_pct.toFixed(1)}%
                      </span>
                    ) : (
                      <span className="text-slate-300 text-xs">—</span>
                    )}
                  </td>
                )}

                {/* R$/1k views (GPM) — só renderiza quando há dados */}
                {showGpm && (
                  <td className="text-right px-2 py-2.5 text-xs text-slate-500 tabular-nums">
                    {b.gpm != null ? `R$${b.gpm.toFixed(0)}` : <span className="text-slate-300">—</span>}
                  </td>
                )}

                {/* ROAS */}
                {showMlRoas && (
                  <td className="text-right px-2 py-2.5 text-xs tabular-nums">
                    {b.ml_roas != null ? (
                      <span className={`font-semibold ${b.ml_roas >= 10 ? "text-emerald-700" : b.ml_roas >= 5 ? "text-amber-700" : "text-rose-700"}`}>
                        {b.ml_roas.toFixed(1)}x
                      </span>
                    ) : (
                      <span className="text-slate-300">—</span>
                    )}
                  </td>
                )}

                {/* Meta TK */}
                {showTkGoal && (
                  <td className="text-right px-3 py-2.5">
                    <MiniBar actual={b.tiktok_gmv} goal={goals[b.brand]?.tiktok ?? null} mounted={mounted} />
                  </td>
                )}

                {/* Meta ML */}
                {showMlGoal && (
                  <td className="text-right px-3 py-2.5">
                    <MiniBar actual={b.ml_gmv} goal={goals[b.brand]?.ml ?? null} mounted={mounted} />
                  </td>
                )}

                {/* Meta SH */}
                {showShGoal && (
                  <td className="text-right px-3 py-2.5">
                    <MiniBar actual={b.shopee_gmv} goal={goals[b.brand]?.shopee ?? null} mounted={mounted} />
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* Rodapé legenda */}
      {visibleTk && (
        <div className="px-4 py-2.5 border-t border-slate-100 flex items-center gap-4 flex-wrap">
          <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">COS%:</span>
          <span className="flex items-center gap-1.5 text-[11px] text-emerald-700">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" /> &lt;25%
          </span>
          <span className="flex items-center gap-1.5 text-[11px] text-amber-700">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block" /> 25–30%
          </span>
          <span className="flex items-center gap-1.5 text-[11px] text-rose-700">
            <span className="w-1.5 h-1.5 rounded-full bg-rose-400 inline-block" /> &gt;30%
          </span>
          <span className="ml-auto text-[10px] text-slate-300 hidden sm:block">
            COS = taxa TK/GMV · R$/1k = GPM · ROAS = receita ads/custo ads
          </span>
        </div>
      )}
    </div>
  );
}
