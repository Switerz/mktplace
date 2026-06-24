"use client";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import type { BrandDetailDayRow } from "@/lib/api-client";

interface Props {
  data: BrandDetailDayRow[];
}

function fmtK(v: number): string {
  if (v >= 1_000_000) return `R$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `R$${(v / 1_000).toFixed(0)}K`;
  return `R$${v.toFixed(0)}`;
}

function fmtDate(d: string): string {
  const [, , day] = d.split("-");
  return `${day}`;
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
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const total = payload.reduce((s, p) => s + (p.value ?? 0), 0);
  return (
    <div className="bg-white border border-violet-100 rounded-xl shadow-lg px-4 py-3 text-sm min-w-[160px]">
      <p className="font-semibold text-slate-700 mb-2">{label}</p>
      {[...payload].reverse().map((p) => (
        <div key={p.name} className="flex justify-between gap-4 text-xs tabular-nums">
          <span style={{ color: p.color }}>{p.name}</span>
          <span className="font-medium text-slate-800">{fmtK(p.value)}</span>
        </div>
      ))}
      <div className="border-t border-slate-100 mt-2 pt-2 flex justify-between text-xs tabular-nums">
        <span className="text-slate-500">Total</span>
        <span className="font-semibold text-slate-800">{fmtK(total)}</span>
      </div>
    </div>
  );
}

export default function ChannelMixChart({ data }: Props) {
  const chartData = data.map((r) => ({
    date: fmtDate(r.date),
    Video: r.gmv_video ?? 0,
    Live: r.gmv_live ?? 0,
    Card: r.gmv_card ?? 0,
  }));

  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="gVideo" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#7c3aed" stopOpacity={0.7} />
            <stop offset="95%" stopColor="#7c3aed" stopOpacity={0.3} />
          </linearGradient>
          <linearGradient id="gLive" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.7} />
            <stop offset="95%" stopColor="#06b6d4" stopOpacity={0.3} />
          </linearGradient>
          <linearGradient id="gCard" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.7} />
            <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.3} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#f1f0ff" vertical={false} />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 11, fill: "#64748b" }}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fontSize: 11, fill: "#64748b" }}
          tickLine={false}
          axisLine={false}
          tickFormatter={fmtK}
          width={52}
        />
        <Tooltip content={<CustomTooltip />} />
        <Legend
          iconType="square"
          wrapperStyle={{ fontSize: 11, paddingTop: 8, color: "#64748b" }}
        />
        <Area
          type="monotone"
          dataKey="Video"
          stackId="a"
          stroke="#7c3aed"
          fill="url(#gVideo)"
          strokeWidth={1.5}
        />
        <Area
          type="monotone"
          dataKey="Live"
          stackId="a"
          stroke="#06b6d4"
          fill="url(#gLive)"
          strokeWidth={1.5}
        />
        <Area
          type="monotone"
          dataKey="Card"
          stackId="a"
          stroke="#f59e0b"
          fill="url(#gCard)"
          strokeWidth={1.5}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
