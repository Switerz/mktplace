"use client";

import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from "recharts";
import {
  fetchPedidos,
  type PedidosData,
  type PedidosBrandRow,
} from "@/lib/api-client";
import KpiCard from "@/components/KpiCard";
import AppNav from "@/components/AppNav";
import { fmtBrl } from "@/lib/formatters";
import { useSortableTable } from "@/lib/use-sortable-table";
import SortableHeader from "@/components/SortableHeader";

const DAYS_OPTIONS = [
  { value: 7, label: "7 dias" },
  { value: 14, label: "14 dias" },
  { value: 30, label: "30 dias" },
];

function fmtNum(v: number | null): string {
  if (v == null) return "—";
  return v.toLocaleString("pt-BR");
}

function fmtRate(v: number | null): string {
  if (v == null) return "—";
  return v.toFixed(1) + "%";
}

function cancelColor(v: number | null): string {
  if (v == null) return "text-slate-400";
  if (v < 2) return "text-emerald-700";
  if (v < 5) return "text-amber-700";
  return "text-rose-700";
}

function cancelBg(v: number | null): string {
  if (v == null) return "";
  if (v < 2) return "bg-emerald-50";
  if (v < 5) return "bg-amber-50";
  return "bg-rose-50";
}

function shortDate(dateStr: string): string {
  const [, m, d] = dateStr.split("-");
  return `${parseInt(d)}/${parseInt(m)}`;
}

interface CanalCardProps {
  title: string;
  accentColor: string;
  orders: number;
  canceled: number;
  gmv: number;
  cancelRate: number | null;
  delivered: number | null;
}

function CanalCard({ title, accentColor, orders, canceled, gmv, cancelRate, delivered }: CanalCardProps) {
  return (
    <div className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5 flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full ${accentColor} shrink-0`} />
        <span className="text-xs font-semibold text-slate-600 uppercase tracking-wide">{title}</span>
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-3">
        <div>
          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide mb-0.5">Pedidos</p>
          <p className="text-xl font-bold text-slate-800 tabular-nums">{fmtNum(orders)}</p>
        </div>
        <div>
          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide mb-0.5">GMV</p>
          <p className="text-xl font-bold text-slate-800 tabular-nums">{fmtBrl(gmv)}</p>
        </div>
        <div>
          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide mb-0.5">Cancelados</p>
          <p className={`text-base font-bold tabular-nums ${cancelBg(cancelRate)} rounded-md px-1.5 py-0.5 inline-block ${cancelColor(cancelRate)}`}>
            {fmtNum(canceled)} <span className="text-xs font-semibold">({fmtRate(cancelRate)})</span>
          </p>
        </div>
        {delivered != null && (
          <div>
            <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide mb-0.5">Entregues</p>
            <p className="text-base font-bold text-emerald-700 tabular-nums">{fmtNum(delivered)}</p>
          </div>
        )}
      </div>
    </div>
  );
}

function getBrandSortValue(row: PedidosBrandRow, column: string): string | number | null {
  switch (column) {
    case "brand":
      return row.label;
    case "tk_orders":
      return row.tiktok_orders;
    case "tk_cancel":
      return row.tiktok_cancel_rate_pct;
    case "ml_orders":
      return row.ml_orders;
    case "ml_cancel":
      return row.ml_cancel_rate_pct;
    case "total_orders":
      return row.total_orders;
    case "total_gmv":
      return row.total_gmv;
    default:
      return null;
  }
}

const BRAND_COLUMN_TYPES: Record<string, "numeric" | "text"> = {
  brand: "text",
  tk_orders: "numeric",
  tk_cancel: "numeric",
  ml_orders: "numeric",
  ml_cancel: "numeric",
  total_orders: "numeric",
  total_gmv: "numeric",
};

function BrandTable({ rows }: { rows: PedidosBrandRow[] }) {
  const { sort, toggleSort, sortedRows } = useSortableTable(rows, getBrandSortValue, BRAND_COLUMN_TYPES);
  return (
    <div className="bg-white rounded-2xl shadow-sm border border-violet-100 overflow-hidden">
      <div className="px-5 py-4 border-b border-violet-100">
        <h2 className="text-sm font-semibold text-slate-700">Por marca</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm" role="table">
          <thead>
            <tr className="bg-slate-50 text-left">
              <SortableHeader label="Marca" column="brand" sort={sort} onSort={toggleSort} align="left" className="!px-5 !py-3 !text-[10px]" />
              <SortableHeader label="TK Pedidos" column="tk_orders" sort={sort} onSort={toggleSort} className="!px-4 !py-3 !text-[10px]" />
              <SortableHeader label="TK Cancel." column="tk_cancel" sort={sort} onSort={toggleSort} className="!px-4 !py-3 !text-[10px]" />
              <SortableHeader label="ML Pedidos" column="ml_orders" sort={sort} onSort={toggleSort} className="!px-4 !py-3 !text-[10px]" />
              <SortableHeader label="ML Cancel." column="ml_cancel" sort={sort} onSort={toggleSort} className="!px-4 !py-3 !text-[10px]" />
              <SortableHeader label="Total" column="total_orders" sort={sort} onSort={toggleSort} className="!px-4 !py-3 !text-[10px]" />
              <SortableHeader label="GMV" column="total_gmv" sort={sort} onSort={toggleSort} className="!px-5 !py-3 !text-[10px]" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {sortedRows.map((row) => (
              <tr key={row.brand} className="hover:bg-slate-50/60 transition-colors">
                <td className="px-5 py-3 font-semibold text-slate-700">{row.label}</td>
                <td className="px-4 py-3 text-right tabular-nums text-slate-600">{fmtNum(row.tiktok_orders)}</td>
                <td className="px-4 py-3 text-right tabular-nums">
                  <span className={`text-xs font-semibold ${cancelColor(row.tiktok_cancel_rate_pct)}`}>
                    {fmtRate(row.tiktok_cancel_rate_pct)}
                  </span>
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-slate-600">{fmtNum(row.ml_orders)}</td>
                <td className="px-4 py-3 text-right tabular-nums">
                  <span className={`text-xs font-semibold ${cancelColor(row.ml_cancel_rate_pct)}`}>
                    {fmtRate(row.ml_cancel_rate_pct)}
                  </span>
                </td>
                <td className="px-4 py-3 text-right tabular-nums font-semibold text-slate-800">
                  {fmtNum(row.total_orders)}
                </td>
                <td className="px-5 py-3 text-right tabular-nums text-slate-700">{fmtBrl(row.total_gmv)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function PedidosPage() {
  const [daysBack, setDaysBack] = useState(30);
  const [data, setData] = useState<PedidosData | null>(null);
  const [loading, setLoading] = useState(true);
  const [isLive, setIsLive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchPedidos(daysBack)
      .then((result) => {
        if (result) {
          setData(result);
          setIsLive(true);
        } else {
          setIsLive(false);
          setData(null);
        }
        setLoading(false);
      })
      .catch(() => {
        setError("Falha ao carregar dados de pedidos. Verifique a conexão.");
        setLoading(false);
      });
  }, [daysBack, retryKey]);

  const kpis = data?.kpis;
  const tk = data?.tiktok;
  const ml = data?.ml;
  const daily = data?.daily ?? [];
  const brands = data?.by_brand ?? [];

  const chartData = daily.map((r) => ({
    date: shortDate(r.date),
    tiktok: r.tiktok_orders,
    ml: r.ml_orders,
  }));

  return (
    <div className="min-h-screen bg-[#f8f7ff]">
      <header className="bg-white border-b border-violet-100 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-violet-600 flex items-center justify-center">
              <span className="text-white font-bold text-xs tracking-tight">TC</span>
            </div>
            <div>
              <h1 className="text-lg font-bold text-gray-900 leading-none">Torre de Controle</h1>
              <p className="text-xs text-slate-500">Gobeaute · Marketplaces</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {isLive ? (
              <span className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-1.5 font-medium">
                Dados ao vivo · API conectada
              </span>
            ) : (
              <span className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5 font-medium">
                Sem dados · API offline
              </span>
            )}
          </div>
        </div>
      </header>

      <AppNav />

      <main className="max-w-7xl mx-auto px-6 py-8 flex flex-col gap-6">
        {/* Controls */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h2 className="text-base font-semibold text-slate-800 leading-none">Pedidos</h2>
            <p className="text-xs text-slate-500 mt-0.5">TikTok Shop + Mercado Livre</p>
          </div>
          <div className="flex items-center gap-2">
            {loading && <span className="text-xs text-violet-400 animate-pulse">Atualizando...</span>}
            <span className="text-xs text-slate-500 font-medium shrink-0">Janela</span>
            <div className="flex gap-1 bg-white border border-violet-100 rounded-xl p-1 shadow-sm">
              {DAYS_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setDaysBack(opt.value)}
                  className={`px-3 py-1.5 rounded-lg text-sm font-semibold whitespace-nowrap transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-1 ${
                    daysBack === opt.value
                      ? "bg-violet-600 text-white shadow"
                      : "text-violet-700 hover:bg-violet-50"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {error && (
          <div className="bg-rose-50 border border-rose-200 rounded-2xl p-4 flex items-center justify-between gap-4">
            <div>
              <p className="text-xs font-semibold text-rose-700 uppercase tracking-wider mb-1">Erro de carregamento</p>
              <p className="text-sm text-rose-800">{error}</p>
            </div>
            <button
              onClick={() => { setError(null); setRetryKey((k) => k + 1); }}
              className="text-xs font-semibold text-rose-700 border border-rose-300 rounded-lg px-3 py-1.5 hover:bg-rose-100 transition-colors shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
            >
              Tentar novamente
            </button>
          </div>
        )}

        <span className="sr-only" aria-live="polite" aria-atomic="true">
          {loading ? "Carregando dados de pedidos..." : error ? "Falha ao carregar." : "Dados de pedidos carregados."}
        </span>

        {/* KPI cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <KpiCard
            label="Total de pedidos"
            value={kpis ? fmtNum(kpis.total_orders) : "—"}
            subvalue={`últimos ${daysBack} dias`}
            accent="bg-violet-600"
          />
          <KpiCard
            label="GMV total"
            value={kpis ? fmtBrl(kpis.total_gmv) : "—"}
            subvalue={`TikTok + ML`}
            accent="bg-violet-600"
          />
          <KpiCard
            label="Ticket médio"
            value={kpis ? fmtBrl(kpis.avg_ticket) : "—"}
            subvalue="por pedido"
            accent="bg-violet-400"
          />
          <KpiCard
            label="Taxa cancelamento"
            value={kpis ? fmtRate(kpis.cancel_rate_pct) : "—"}
            subvalue={kpis ? `${fmtNum((data?.tiktok.canceled ?? 0) + (data?.ml.canceled ?? 0))} cancelados` : undefined}
            accent={kpis?.cancel_rate_pct == null ? "bg-slate-300" : kpis.cancel_rate_pct < 2 ? "bg-emerald-500" : kpis.cancel_rate_pct < 5 ? "bg-amber-500" : "bg-rose-500"}
          />
        </div>

        {/* Canal breakdown */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {tk && (
            <CanalCard
              title="TikTok Shop"
              accentColor="bg-violet-600"
              orders={tk.orders}
              canceled={tk.canceled}
              gmv={tk.gmv}
              cancelRate={tk.cancel_rate_pct}
              delivered={tk.delivered}
            />
          )}
          {ml && (
            <CanalCard
              title="Mercado Livre"
              accentColor="bg-amber-500"
              orders={ml.orders}
              canceled={ml.canceled}
              gmv={ml.gmv}
              cancelRate={ml.cancel_rate_pct}
              delivered={ml.delivered}
            />
          )}
          {!tk && !ml && !loading && (
            <div className="col-span-2 bg-white rounded-2xl border border-violet-100 p-8 text-center">
              <p className="text-sm text-slate-400">Sem dados de canais disponíveis.</p>
            </div>
          )}
        </div>

        {/* Daily chart */}
        {chartData.length > 0 && (
          <div className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5">
            <h2 className="text-sm font-semibold text-slate-700 mb-4">
              Volume diário de pedidos — últimos {daysBack} dias
            </h2>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={chartData} margin={{ top: 4, right: 0, left: 0, bottom: 0 }} barSize={daysBack > 14 ? 8 : 14}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11, fill: "#64748b" }}
                  tickLine={false}
                  axisLine={false}
                  interval={daysBack > 14 ? 4 : 1}
                />
                <YAxis
                  tick={{ fontSize: 11, fill: "#64748b" }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) => v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v)}
                  width={36}
                />
                <Tooltip
                  contentStyle={{ borderRadius: "12px", border: "1px solid #ede9fe", fontSize: 12 }}
                  formatter={(value: number, name: string) => [
                    fmtNum(value),
                    name === "tiktok" ? "TikTok Shop" : "Mercado Livre",
                  ]}
                  labelStyle={{ fontWeight: 600, color: "#1e293b" }}
                />
                <Legend
                  formatter={(v) => v === "tiktok" ? "TikTok Shop" : "Mercado Livre"}
                  iconType="circle"
                  iconSize={8}
                  wrapperStyle={{ fontSize: 12, paddingTop: 12 }}
                />
                <Bar dataKey="tiktok" stackId="orders" fill="#7c3aed" radius={[0, 0, 0, 0]} />
                <Bar dataKey="ml" stackId="orders" fill="#f59e0b" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Brand table */}
        {brands.length > 0 && <BrandTable rows={brands} />}

        {!loading && !error && !data && (
          <div className="bg-white rounded-2xl border border-violet-100 p-12 text-center">
            <p className="text-sm text-slate-500">API offline — conecte o banco de dados para visualizar pedidos.</p>
          </div>
        )}
      </main>
    </div>
  );
}
