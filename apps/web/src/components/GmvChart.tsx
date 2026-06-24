"use client";

import {
  Bar,
  ComposedChart,
  CartesianGrid,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { MonthPoint } from "@/lib/api-client";
import { fmtBrl } from "@/lib/formatters";

const BRAND_COLORS: Record<string, string> = {
  barbours: "#7c3aed",
  kokeshi: "#06b6d4",
  apice: "#f59e0b",
  lescent: "#ec4899",
  rituaria: "#10b981",
};

const BRAND_LABELS: Record<string, string> = {
  barbours: "Barbours",
  kokeshi: "Kokeshi",
  apice: "Ápice",
  lescent: "Lescent",
  rituaria: "Rituária",
};

const BRANDS = ["barbours", "kokeshi", "apice", "lescent", "rituaria"] as const;

interface Props {
  data: MonthPoint[];
}

interface TooltipPayload {
  name: string;
  value: number;
  color: string;
  dataKey: string;
}

function CustomTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipPayload[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const brandEntries = payload.filter((p) => p.dataKey !== "total");
  const total = brandEntries.reduce((s, p) => s + (p.value ?? 0), 0);
  return (
    <div className="bg-white border border-violet-100 rounded-xl shadow-lg px-4 py-3 text-sm min-w-[180px]">
      <p className="font-semibold text-slate-700 mb-2">{label}</p>
      {[...brandEntries].reverse().map((p) => (
        <div key={p.name} className="flex justify-between gap-6 text-xs tabular-nums py-0.5">
          <span style={{ color: p.color }}>{BRAND_LABELS[p.dataKey] ?? p.name}</span>
          <span className="font-medium text-slate-800">{fmtBrl(p.value)}</span>
        </div>
      ))}
      <div className="border-t border-slate-100 mt-2 pt-2 flex justify-between text-xs tabular-nums">
        <span className="font-semibold text-slate-600">Total</span>
        <span className="font-bold text-gray-900">{fmtBrl(total)}</span>
      </div>
    </div>
  );
}

function currentMonthKey(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export default function GmvChart({ data }: Props) {
  const partial = currentMonthKey();
  const chartData = data.map((d) => ({
    ...d,
    mes_label: d.mes === partial ? `${d.mes_label}*` : d.mes_label,
    total: BRANDS.reduce((s, b) => s + (d[b] ?? 0), 0),
  }));

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-slate-700">Evolucao GMV — Todos os canais</h2>
        <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">
          Linha = total consolidado · * mês parcial
        </span>
      </div>
      {data.length === 0 ? (
        <div className="h-60 flex items-center justify-center text-slate-400 text-sm">
          Carregando...
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f3f0ff" vertical={false} />
            <XAxis
              dataKey="mes_label"
              tick={{ fontSize: 11, fill: "#64748b" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tickFormatter={(v) => {
                if (v >= 1_000_000) return `R$${(v / 1_000_000).toFixed(0)}M`;
                if (v >= 1_000) return `R$${(v / 1_000).toFixed(0)}K`;
                return `R$${v}`;
              }}
              tick={{ fontSize: 11, fill: "#64748b" }}
              axisLine={false}
              tickLine={false}
              width={52}
            />
            <Tooltip content={<CustomTooltip />} />
            <Legend
              formatter={(value) =>
                value === "total" ? (
                  <span style={{ fontSize: 11, color: "#1e293b", fontWeight: 600 }}>Total</span>
                ) : (
                  <span style={{ fontSize: 11, color: "#475569" }}>{BRAND_LABELS[value] ?? value}</span>
                )
              }
            />
            {BRANDS.map((brand) => (
              <Bar
                key={brand}
                dataKey={brand}
                stackId="a"
                fill={BRAND_COLORS[brand]}
                radius={brand === "rituaria" ? [3, 3, 0, 0] : [0, 0, 0, 0]}
              />
            ))}
            <Line
              type="monotone"
              dataKey="total"
              stroke="#1e293b"
              strokeWidth={2}
              dot={{ r: 3, fill: "#1e293b", strokeWidth: 0 }}
              activeDot={{ r: 5, fill: "#7c3aed" }}
              legendType="plainline"
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
