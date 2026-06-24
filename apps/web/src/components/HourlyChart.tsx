"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import type { TempoRealHour } from "@/lib/api-client";

interface Props {
  hours: TempoRealHour[];
  mode: "acumulado" | "hora";
  currentHour?: number;
}

function fmtK(v: number): string {
  if (v >= 1_000_000) return `R$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `R$${(v / 1_000).toFixed(0)}K`;
  return `R$${v.toFixed(0)}`;
}

function fmtBrl(v: number): string {
  return v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

interface TooltipPayload {
  name: string;
  value: number;
  color: string;
}

function CustomTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipPayload[];
  label?: number;
}) {
  if (!active || !payload?.length) return null;
  const validPayload = payload.filter((p) => p.value != null && !isNaN(p.value));
  if (!validPayload.length) return null;
  return (
    <div className="bg-white border border-violet-100 rounded-xl shadow-lg px-4 py-3 text-sm min-w-[160px]">
      <p className="font-semibold text-slate-700 mb-2">{label}h</p>
      {validPayload.map((p) => (
        <div key={p.name} className="flex justify-between gap-4 text-xs tabular-nums">
          <span style={{ color: p.color }}>{p.name}</span>
          <span className="font-medium text-slate-800">{fmtBrl(p.value)}</span>
        </div>
      ))}
    </div>
  );
}

export default function HourlyChart({ hours, mode, currentHour }: Props) {
  const field = mode === "acumulado" ? "gmv_acumulado" : "gmv_hour";
  const prevField = mode === "acumulado" ? "gmv_acumulado_prior" : "gmv_hour_prior";

  const data = hours.map((h) => ({
    hour: h.hour,
    Hoje: h[field as keyof TempoRealHour] as number,
    Ontem: (h[prevField as keyof TempoRealHour] as number | null) ?? undefined,
    "Media 7d": h.gmv_avg7d ?? undefined,
  }));

  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="lineHoje" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#7c3aed" />
            <stop offset="100%" stopColor="#a855f7" />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#f1f0ff" vertical={false} />
        <XAxis
          dataKey="hour"
          tick={{ fontSize: 11, fill: "#64748b" }}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `${v}h`}
          ticks={[0, 4, 8, 12, 16, 20, 23]}
        />
        <YAxis
          tick={{ fontSize: 11, fill: "#64748b" }}
          tickLine={false}
          axisLine={false}
          tickFormatter={fmtK}
          width={56}
        />
        <Tooltip content={<CustomTooltip />} />
        {currentHour != null && (
          <ReferenceLine
            x={currentHour}
            stroke="#7c3aed"
            strokeWidth={1}
            strokeDasharray="3 3"
            strokeOpacity={0.5}
            label={{ value: "agora", position: "top", fontSize: 10, fill: "#7c3aed", opacity: 0.7 }}
          />
        )}
        <Line
          type="monotone"
          dataKey="Hoje"
          stroke="url(#lineHoje)"
          strokeWidth={2.5}
          dot={false}
          activeDot={{ r: 4, fill: "#7c3aed" }}
          connectNulls
        />
        <Line
          type="monotone"
          dataKey="Ontem"
          stroke="#cbd5e1"
          strokeWidth={1.5}
          strokeDasharray="4 3"
          dot={false}
          activeDot={{ r: 3, fill: "#64748b" }}
          connectNulls
        />
        <Line
          type="monotone"
          dataKey="Media 7d"
          stroke="#c4b5fd"
          strokeWidth={1.5}
          strokeDasharray="2 4"
          dot={false}
          activeDot={{ r: 3, fill: "#a78bfa" }}
          connectNulls
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
