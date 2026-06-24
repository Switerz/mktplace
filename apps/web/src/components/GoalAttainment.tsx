"use client";

import { useEffect, useState } from "react";
import type { BrandRow } from "@/lib/api-client";
import { getGoals, PERIOD_LABEL } from "@/lib/goals-data";
import { fmtBrl } from "@/lib/formatters";
import type { Marketplace } from "@/lib/mock-data";

type Filter = Marketplace | "all";

interface Props {
  brands: BrandRow[];
  period: string;
  filter: Filter;
}

interface BrandGoalRow {
  brand: string;
  label: string;
  tiktok_gmv: number | null;
  ml_gmv: number | null;
  tiktok_goal: number | null;
  ml_goal: number | null;
}

function barColor(pct: number): string {
  if (pct >= 1) return "bg-emerald-500";
  if (pct >= 0.8) return "bg-amber-400";
  return "bg-rose-500";
}

function badgeClass(pct: number): string {
  if (pct >= 1) return "text-emerald-700 bg-emerald-50";
  if (pct >= 0.8) return "text-amber-700 bg-amber-50";
  return "text-rose-700 bg-rose-50";
}

interface BarProps {
  actual: number | null;
  goal: number | null;
  label: string;
  color: string;
  mounted: boolean;
}

function MarketplaceBar({ actual, goal, label, color, mounted }: BarProps) {
  if (!goal) return null;
  const a = actual ?? 0;
  const pct = a / goal;
  const barPct = Math.min(pct, 1);

  return (
    <div className="flex items-center gap-3">
      <span className={`text-[11px] font-semibold uppercase tracking-wide w-24 shrink-0 ${color}`}>
        {label}
      </span>
      <div className="flex-1 bg-gray-100 rounded-full h-2 overflow-hidden">
        <div
          className={`h-2 rounded-full transition-[width] duration-700 ease-out motion-reduce:transition-none ${barColor(pct)}`}
          style={{ width: mounted ? `${barPct * 100}%` : "0%" }}
        />
      </div>
      <span className={`text-xs font-bold tabular-nums min-w-[46px] text-right rounded px-1.5 py-0.5 ${badgeClass(pct)}`}>
        {(pct * 100).toFixed(0)}%
      </span>
      <span className="text-xs text-slate-400 tabular-nums min-w-[140px] text-right hidden sm:block">
        {fmtBrl(a)} / {fmtBrl(goal)}
      </span>
    </div>
  );
}

export default function GoalAttainment({ brands, period, filter }: Props) {
  const goals = getGoals(period);
  const label = PERIOD_LABEL(period);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const raf = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  if (Object.keys(goals).length === 0) {
    return (
      <div className="bg-white rounded-2xl border border-violet-100 shadow-sm p-6">
        <p className="text-sm text-slate-400">Metas não configuradas para {label}.</p>
      </div>
    );
  }

  const rows: BrandGoalRow[] = brands
    .map((b) => ({
      brand: b.brand,
      label: b.label,
      tiktok_gmv: b.tiktok_gmv,
      ml_gmv: b.ml_gmv,
      tiktok_goal: goals[b.brand]?.tiktok ?? null,
      ml_goal: goals[b.brand]?.ml ?? null,
    }))
    .filter((r) => r.tiktok_goal !== null || r.ml_goal !== null);

  const visibleTk = filter !== "ml";
  const visibleMl = filter !== "tiktok";

  const filteredRows = rows.filter((r) =>
    (visibleTk && r.tiktok_goal !== null) || (visibleMl && r.ml_goal !== null)
  );

  const totalActual = filteredRows.reduce((s, r) => {
    let v = 0;
    if (visibleTk) v += r.tiktok_gmv ?? 0;
    if (visibleMl) v += r.ml_gmv ?? 0;
    return s + v;
  }, 0);
  const totalGoal = filteredRows.reduce((s, r) => {
    let v = 0;
    if (visibleTk) v += r.tiktok_goal ?? 0;
    if (visibleMl) v += r.ml_goal ?? 0;
    return s + v;
  }, 0);
  const totalPct = totalGoal > 0 ? totalActual / totalGoal : 0;

  return (
    <div className="bg-white rounded-2xl border border-violet-100 shadow-sm overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-violet-100 flex items-center justify-between gap-4">
        <div>
          <h2 className="font-semibold text-gray-900">Atingimento de Metas</h2>
          <p className="text-xs text-slate-400 mt-0.5">{label} · TikTok Shop{visibleMl ? " + Mercado Livre" : ""}</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500 hidden sm:block">
            {fmtBrl(totalActual)} / {fmtBrl(totalGoal)}
          </span>
          <span className={`px-3 py-1.5 rounded-xl text-sm font-bold ${badgeClass(totalPct)}`}>
            {(totalPct * 100).toFixed(1)}%
          </span>
        </div>
      </div>

      {/* Brand rows */}
      <div className="divide-y divide-violet-50">
        {filteredRows.map((row) => {
          const brandPct = (() => {
            const ga = (visibleTk ? (row.tiktok_goal ?? 0) : 0) + (visibleMl ? (row.ml_goal ?? 0) : 0);
            const aa = (visibleTk ? (row.tiktok_gmv ?? 0) : 0) + (visibleMl ? (row.ml_gmv ?? 0) : 0);
            return ga > 0 ? aa / ga : 0;
          })();

          return (
            <div key={row.brand} className="px-6 py-4 hover:bg-violet-50/30 transition-colors">
              <div className="flex items-center justify-between mb-2.5">
                <span className="text-sm font-semibold text-gray-800">{row.label}</span>
                <span className={`text-xs font-bold px-2 py-0.5 rounded ${badgeClass(brandPct)}`}>
                  {(brandPct * 100).toFixed(0)}%
                </span>
              </div>
              <div className="flex flex-col gap-2">
                {visibleTk && row.tiktok_goal && (
                  <MarketplaceBar
                    actual={row.tiktok_gmv}
                    goal={row.tiktok_goal}
                    label="TikTok Shop"
                    color="text-[#ff0050]"
                    mounted={mounted}
                  />
                )}
                {visibleMl && row.ml_goal && (
                  <MarketplaceBar
                    actual={row.ml_gmv}
                    goal={row.ml_goal}
                    label="Mercado Livre"
                    color="text-amber-600"
                    mounted={mounted}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Footer summary bar */}
      <div className="px-6 py-3 bg-violet-50/40 border-t border-violet-100">
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-500 font-medium w-24 shrink-0">Consolidado</span>
          <div className="flex-1 bg-gray-100 rounded-full h-2 overflow-hidden">
            <div
              className={`h-2 rounded-full transition-[width] duration-700 ease-out motion-reduce:transition-none ${barColor(totalPct)}`}
              style={{ width: mounted ? `${Math.min(totalPct, 1) * 100}%` : "0%" }}
            />
          </div>
          <span className={`text-xs font-bold tabular-nums px-1.5 py-0.5 rounded ${badgeClass(totalPct)}`}>
            {(totalPct * 100).toFixed(1)}%
          </span>
        </div>
      </div>
    </div>
  );
}
