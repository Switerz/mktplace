"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { DailyRow } from "@/lib/mock-daily";
import { fmtBrl } from "@/lib/formatters";

interface Props {
  data: DailyRow[];
  hasTiktok: boolean;
  hasMl: boolean;
  hasShopee: boolean;
}

const SERIES_LABEL: Record<string, string> = {
  tiktok: "TikTok",
  ml: "Mercado Livre",
  shopee: "Shopee",
  total: "Total",
};

function shortDate(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  return `${d.getDate()}/${d.getMonth() + 1}`;
}

export default function DailyChart({ data, hasTiktok, hasMl, hasShopee }: Props) {
  const chartData = data.map((r) => ({
    date: shortDate(r.date),
    tiktok: r.tiktok_gmv ?? undefined,
    ml: r.ml_gmv ?? undefined,
    shopee: r.shopee_gmv ?? undefined,
    total: r.total_gmv,
  }));

  // Renderiza uma serie por canal ativo (nunca uma linha "Total" ambigua que
  // misture canais sem identifica-los); se nenhum canal individual tiver
  // dado, cai para "total" como ultimo recurso.
  const activeSeries: { key: "tiktok" | "ml" | "shopee"; color: string; gradientId: string }[] = [];
  if (hasTiktok) activeSeries.push({ key: "tiktok", color: "#7c3aed", gradientId: "gradTk" });
  if (hasMl) activeSeries.push({ key: "ml", color: "#f59e0b", gradientId: "gradMl" });
  if (hasShopee) activeSeries.push({ key: "shopee", color: "#f97316", gradientId: "gradSh" });

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5">
      <h2 className="text-sm font-semibold text-slate-700 mb-4">
        GMV Diário — Últimos 60 dias
      </h2>
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={chartData} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="gradTk" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#7c3aed" stopOpacity={0.15} />
              <stop offset="95%" stopColor="#7c3aed" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="gradMl" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.15} />
              <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="gradSh" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#f97316" stopOpacity={0.15} />
              <stop offset="95%" stopColor="#f97316" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#f3f0ff" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: "#64748b" }}
            axisLine={false}
            tickLine={false}
            interval={6}
          />
          <YAxis
            tickFormatter={(v) => fmtBrl(v)}
            tick={{ fontSize: 10, fill: "#64748b" }}
            axisLine={false}
            tickLine={false}
            width={68}
          />
          <Tooltip
            formatter={(value: number, name: string) => [fmtBrl(value), SERIES_LABEL[name] ?? name]}
            contentStyle={{ borderRadius: 12, border: "1px solid #ede9fe", fontSize: 12 }}
          />
          <Legend formatter={(v) => SERIES_LABEL[v] ?? v} />
          {activeSeries.length > 0 ? (
            activeSeries.map((s) => (
              <Area
                key={s.key}
                type="monotone"
                dataKey={s.key}
                stroke={s.color}
                strokeWidth={2}
                fill={`url(#${s.gradientId})`}
                dot={false}
              />
            ))
          ) : (
            <Area type="monotone" dataKey="total" stroke="#7c3aed" strokeWidth={2} fill="url(#gradTk)" dot={false} />
          )}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
