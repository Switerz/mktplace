"use client";

import {
  Bar, ComposedChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { TrendPoint } from "@/lib/api-client";
import { fmtBrl } from "@/lib/formatters";

interface Props {
  data: TrendPoint[];
  granularity: "day" | "month";
  loading?: boolean;
}

function CustomTooltip({ active, payload, label }: {
  active?: boolean; payload?: { value: number }[]; label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-white border border-violet-100 rounded-xl shadow-lg px-4 py-3 text-sm min-w-[140px]">
      <p className="font-semibold text-slate-700 mb-1">{label}</p>
      <p className="text-xs tabular-nums text-slate-800 font-medium">{fmtBrl(payload[0].value)}</p>
    </div>
  );
}

export default function TrendChart({ data, granularity, loading }: Props) {
  return (
    <div className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-slate-700">
          Tendência de GMV — período selecionado
        </h2>
        <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">
          Granularidade {granularity === "day" ? "diária" : "mensal"} · respeita canal e marca
        </span>
      </div>
      {loading || data.length === 0 ? (
        <div className="h-60 flex items-center justify-center text-slate-400 text-sm">
          {loading ? "Carregando..." : "Sem dados para o período e filtros selecionados."}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f3f0ff" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11, fill: "#64748b" }}
              axisLine={false}
              tickLine={false}
              interval={data.length > 20 ? Math.ceil(data.length / 20) : 0}
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
            <Bar dataKey="gmv" fill="#c4b5fd" radius={[3, 3, 0, 0]} />
            <Line
              type="monotone"
              dataKey="gmv"
              stroke="#7c3aed"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 5, fill: "#7c3aed" }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
