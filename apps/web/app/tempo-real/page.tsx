"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchTempoReal } from "@/lib/api-client";
import type { TempoRealData, TempoRealBrand } from "@/lib/api-client";
import HourlyChart from "@/components/HourlyChart";
import AppNav from "@/components/AppNav";

function fmtBrl(v: number) {
  return v.toLocaleString("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
}

function fmtBrlFull(v: number) {
  return v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function fmtNumber(v: number) {
  return v.toLocaleString("pt-BR");
}

function DeltaBadge({ pct }: { pct: number | null }) {
  if (pct == null) return <span className="text-slate-300 text-xs">—</span>;
  const up = pct >= 0;
  return (
    <span className={`text-xs font-semibold tabular-nums px-2 py-0.5 rounded-full ${up ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}>
      {up ? "+" : ""}{pct.toFixed(1)}%
    </span>
  );
}

const BRAND_COLORS_ACTIVE: Record<string, string> = {
  barbours: "bg-violet-600 text-white border-transparent",
  kokeshi:  "bg-cyan-500 text-white border-transparent",
  apice:    "bg-amber-500 text-white border-transparent",
  lescent:  "bg-pink-500 text-white border-transparent",
  rituaria: "bg-emerald-500 text-white border-transparent",
  todos:    "bg-slate-700 text-white border-transparent",
};

const BRAND_COLORS_IDLE: Record<string, string> = {
  barbours: "text-violet-700 border-violet-200 hover:bg-violet-50",
  kokeshi:  "text-cyan-700 border-cyan-200 hover:bg-cyan-50",
  apice:    "text-amber-700 border-amber-200 hover:bg-amber-50",
  lescent:  "text-pink-700 border-pink-200 hover:bg-pink-50",
  rituaria: "text-emerald-700 border-emerald-200 hover:bg-emerald-50",
  todos:    "text-slate-600 border-slate-200 hover:bg-slate-50",
};

const BRAND_DOT: Record<string, string> = {
  barbours: "bg-violet-500",
  kokeshi:  "bg-cyan-500",
  apice:    "bg-amber-500",
  lescent:  "bg-pink-500",
  rituaria: "bg-emerald-500",
};

type ChartMode = "acumulado" | "hora";

function GmvProgressBar({ hoje, ontem }: { hoje: number; ontem: number | null }) {
  if (!ontem || ontem === 0) return null;
  const pct = Math.min((hoje / ontem) * 100, 130);
  const color = pct >= 100 ? "bg-emerald-500" : pct >= 75 ? "bg-amber-400" : "bg-rose-400";
  return (
    <div className="mt-1.5 w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
      <div
        className={`h-full rounded-full transition-all duration-500 ${color}`}
        style={{ width: `${Math.min(pct, 100)}%` }}
      />
    </div>
  );
}

function buildTodosHours(brands: TempoRealBrand[]) {
  const byHour: Record<number, { gmv_hour: number; gmv_hour_prior: number; gmv_acumulado: number; gmv_acumulado_prior: number; gmv_avg7d: number; customers_hour: number; customers_acumulado: number }> = {};
  for (const b of brands) {
    for (const h of b.hours) {
      if (!byHour[h.hour]) {
        byHour[h.hour] = { gmv_hour: 0, gmv_hour_prior: 0, gmv_acumulado: 0, gmv_acumulado_prior: 0, gmv_avg7d: 0, customers_hour: 0, customers_acumulado: 0 };
      }
      byHour[h.hour].gmv_hour += h.gmv_hour;
      byHour[h.hour].gmv_hour_prior += h.gmv_hour_prior ?? 0;
      byHour[h.hour].gmv_acumulado += h.gmv_acumulado;
      byHour[h.hour].gmv_acumulado_prior += h.gmv_acumulado_prior ?? 0;
      byHour[h.hour].gmv_avg7d += h.gmv_avg7d ?? 0;
      byHour[h.hour].customers_hour += h.customers_hour;
      byHour[h.hour].customers_acumulado += h.customers_acumulado;
    }
  }
  return Object.entries(byHour)
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([hour, v]) => ({
      hour: Number(hour),
      gmv_hour: v.gmv_hour,
      gmv_acumulado: v.gmv_acumulado,
      gmv_hour_prior: v.gmv_hour_prior || null,
      gmv_acumulado_prior: v.gmv_acumulado_prior || null,
      gmv_avg7d: v.gmv_avg7d || null,
      customers_hour: v.customers_hour,
      customers_acumulado: v.customers_acumulado,
      conversion_hour: null,
      ticket_medio: null,
    }));
}

const REFRESH_INTERVAL_S = 300; // 5 minutos

export default function TempoRealPage() {
  const [data, setData] = useState<TempoRealData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [countdown, setCountdown] = useState(REFRESH_INTERVAL_S);
  const [selectedBrand, setSelectedBrand] = useState<string>("todos");
  const [mode, setMode] = useState<ChartMode>("acumulado");

  const doFetch = (silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    fetchTempoReal().then((res) => {
      if (res?.data) setData(res.data);
      setLastUpdated(new Date());
      setLoading(false);
      setRefreshing(false);
      setCountdown(REFRESH_INTERVAL_S);
    });
  };

  // carga inicial
  useEffect(() => { doFetch(false); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // auto-refresh a cada 5 min
  useEffect(() => {
    const iv = setInterval(() => doFetch(true), REFRESH_INTERVAL_S * 1000);
    return () => clearInterval(iv);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // countdown regressivo
  useEffect(() => {
    const tick = setInterval(() => setCountdown((c) => (c > 0 ? c - 1 : 0)), 1000);
    return () => clearInterval(tick);
  }, []);

  const now = new Date();
  const dateLabel = now.toLocaleDateString("pt-BR", { day: "2-digit", month: "long", year: "numeric" });
  const hourLabel = `${now.getHours().toString().padStart(2, "0")}:${now.getMinutes().toString().padStart(2, "0")}`;

  const selectedData: TempoRealBrand | undefined =
    selectedBrand === "todos" ? undefined : data?.brands.find((b) => b.brand === selectedBrand);

  const chartHours =
    selectedBrand === "todos" && data
      ? buildTodosHours(data.brands)
      : selectedData?.hours ?? [];

  const displayGmv     = selectedBrand === "todos" ? data?.total_gmv_hoje : selectedData?.gmv_hoje;
  const displayOntem   = selectedBrand === "todos" ? data?.total_gmv_ontem : selectedData?.gmv_ontem;
  const displayDelta   = selectedBrand === "todos" ? data?.total_delta_pct : selectedData?.delta_pct;
  const displayRitmo   = selectedBrand === "todos" ? data?.total_ritmo_projetado : selectedData?.ritmo_projetado;
  const displayClients = selectedBrand === "todos"
    ? data?.brands.reduce((s, b) => s + b.clientes_hoje, 0)
    : selectedData?.clientes_hoje;
  const displayUltimaHora = selectedBrand === "todos"
    ? (data?.brands.length ? Math.max(...data.brands.map((b) => b.ultima_hora)) : null)
    : selectedData?.ultima_hora;

  return (
    <div className="min-h-screen bg-[#f8f7ff]">
      <header className="bg-white border-b border-violet-100 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="text-slate-400 hover:text-violet-600 transition-colors text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
            >
              &larr; Dashboard
            </Link>
            <span className="text-slate-200 select-none">|</span>
            <div>
              <h1 className="text-base font-bold text-gray-900 leading-none">Tempo Real — TikTok Shop</h1>
              <p className="text-xs text-slate-400 mt-0.5">{dateLabel} · {hourLabel}</p>
            </div>
          </div>
          {!loading && data && (
            <div className="flex items-center gap-2">
              <button
                onClick={() => doFetch(true)}
                disabled={refreshing}
                className="text-xs font-medium text-slate-500 border border-slate-200 rounded-full px-3 py-1 hover:bg-slate-50 transition-colors disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
                title="Atualizar agora"
              >
                {refreshing ? "Atualizando..." : `↻ ${Math.floor(countdown / 60)}:${String(countdown % 60).padStart(2, "0")}`}
              </button>
              <span className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-full px-3 py-1">
                <span className={`w-1.5 h-1.5 rounded-full bg-emerald-500 ${refreshing ? "" : "animate-pulse"}`} />
                {lastUpdated
                  ? `Atualizado ${lastUpdated.getHours().toString().padStart(2,"0")}:${lastUpdated.getMinutes().toString().padStart(2,"0")}`
                  : "Ao vivo"}
              </span>
            </div>
          )}
        </div>
      </header>

      <AppNav />

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-5">

        {loading && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-12 text-center">
            <p className="text-slate-400 text-sm">Carregando dados em tempo real...</p>
          </div>
        )}

        {!loading && !data && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-12 text-center">
            <p className="text-slate-500 font-medium text-sm">API offline — dados em tempo real indisponiveis</p>
            <p className="text-slate-400 text-xs mt-1">Configure METABASE_API_KEY e reinicie a API para ativar este cockpit.</p>
          </div>
        )}

        {!loading && data && (
          <>
            {/* KPIs — 4 cards */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-5 py-4">
                <p className="text-xs text-slate-400 mb-1">GMV Acumulado Hoje</p>
                <p className="text-2xl font-bold text-gray-900 tabular-nums">{fmtBrl(data.total_gmv_hoje)}</p>
                {data.total_gmv_ontem != null && (
                  <p className="text-xs text-slate-400 mt-1 tabular-nums">Ontem: {fmtBrl(data.total_gmv_ontem)}</p>
                )}
              </div>

              <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-5 py-4">
                <p className="text-xs text-slate-400 mb-2">vs Ontem</p>
                <DeltaBadge pct={data.total_delta_pct} />
                {data.total_delta_pct != null && data.total_gmv_ontem != null && (
                  <p className="text-xs text-slate-400 mt-2 tabular-nums">
                    {data.total_gmv_hoje >= data.total_gmv_ontem ? "+" : ""}
                    {fmtBrl(data.total_gmv_hoje - data.total_gmv_ontem)}
                  </p>
                )}
              </div>

              <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-5 py-4">
                <p className="text-xs text-slate-400 mb-1">Ritmo Projetado</p>
                <p className="text-2xl font-bold text-slate-700 tabular-nums">
                  {data.total_ritmo_projetado ? fmtBrl(data.total_ritmo_projetado) : "—"}
                </p>
                <p className="text-xs text-slate-400 mt-1">se ritmo atual persistir</p>
              </div>

              <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-5 py-4">
                <p className="text-xs text-slate-400 mb-1">Ultima hora ativa</p>
                <p className="text-2xl font-bold text-gray-900">
                  {data.brands.length > 0
                    ? `${Math.max(...data.brands.map((b) => b.ultima_hora))}h`
                    : "—"}
                </p>
                <p className="text-xs text-slate-400 mt-1">{data.brands.length} marcas ativas</p>
              </div>
            </div>

            {/* Grafico principal */}
            <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
              <div className="px-5 py-4 border-b border-violet-100 flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => setSelectedBrand("todos")}
                    className={`text-xs font-semibold px-3 py-1.5 rounded-full border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                      selectedBrand === "todos"
                        ? BRAND_COLORS_ACTIVE["todos"]
                        : `bg-white ${BRAND_COLORS_IDLE["todos"]}`
                    }`}
                  >
                    TODOS
                  </button>
                  {data.brands.map((b) => (
                    <button
                      key={b.brand}
                      onClick={() => setSelectedBrand(b.brand)}
                      className={`text-xs font-semibold px-3 py-1.5 rounded-full border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                        selectedBrand === b.brand
                          ? (BRAND_COLORS_ACTIVE[b.brand] ?? "bg-slate-700 text-white border-transparent")
                          : `bg-white ${BRAND_COLORS_IDLE[b.brand] ?? "text-slate-500 border-slate-200"}`
                      }`}
                    >
                      {b.label}
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-1 bg-slate-50 rounded-lg p-1">
                  {(["acumulado", "hora"] as ChartMode[]).map((m) => (
                    <button
                      key={m}
                      onClick={() => setMode(m)}
                      className={`text-xs px-3 py-1 rounded-md font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                        mode === m ? "bg-white shadow-sm text-violet-700" : "text-slate-500"
                      }`}
                    >
                      {m === "acumulado" ? "Acumulado" : "Por hora"}
                    </button>
                  ))}
                </div>
              </div>

              <div className="px-4 pt-4 pb-2">
                {chartHours.length > 0 ? (
                  <HourlyChart hours={chartHours} mode={mode} currentHour={now.getHours()} />
                ) : (
                  <div className="h-64 flex items-center justify-center text-slate-400 text-sm">
                    Sem dados horarios
                  </div>
                )}
              </div>

              {/* Mini stats abaixo do grafico */}
              <div className="px-5 py-3 border-t border-slate-50 flex flex-wrap gap-6">
                <div>
                  <p className="text-[10px] text-slate-400 uppercase tracking-wide">GMV hoje</p>
                  <p className="text-sm font-semibold text-slate-800 tabular-nums">
                    {displayGmv != null ? fmtBrlFull(displayGmv) : "—"}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] text-slate-400 uppercase tracking-wide">vs Ontem</p>
                  <DeltaBadge pct={displayDelta ?? null} />
                </div>
                <div>
                  <p className="text-[10px] text-slate-400 uppercase tracking-wide">Clientes</p>
                  <p className="text-sm font-semibold text-slate-800 tabular-nums">
                    {displayClients != null ? fmtNumber(displayClients) : "—"}
                  </p>
                </div>
                {selectedData?.conversion_hora != null && (
                  <div>
                    <p className="text-[10px] text-slate-400 uppercase tracking-wide">Conv. ult. hora ativa</p>
                    <p className="text-sm font-semibold text-slate-800 tabular-nums">
                      {selectedData.conversion_hora.toFixed(1)}%
                    </p>
                  </div>
                )}
                {selectedData?.ticket_medio != null && (
                  <div>
                    <p className="text-[10px] text-slate-400 uppercase tracking-wide">Ticket ult. hora ativa</p>
                    <p className="text-sm font-semibold text-slate-800 tabular-nums">
                      {fmtBrlFull(selectedData.ticket_medio)}
                    </p>
                  </div>
                )}
                {displayRitmo != null && (
                  <div>
                    <p className="text-[10px] text-slate-400 uppercase tracking-wide">Ritmo projetado</p>
                    <p className="text-sm font-semibold text-slate-800 tabular-nums">{fmtBrl(displayRitmo)}</p>
                  </div>
                )}
                {displayUltimaHora != null && (
                  <div>
                    <p className="text-[10px] text-slate-400 uppercase tracking-wide">Ultima hora ativa</p>
                    <p className="text-sm font-semibold text-slate-800">{displayUltimaHora}h</p>
                  </div>
                )}
              </div>
            </div>

            {/* Tabela cross-brand */}
            <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
              <div className="px-5 py-4 border-b border-violet-100">
                <h2 className="text-sm font-semibold text-slate-700">Resumo por Marca — Hoje</h2>
                <p className="text-xs text-slate-400 mt-0.5">Barra de progresso = GMV hoje vs ontem · clique para ver grafico</p>
              </div>
              <table className="w-full" aria-label="Resumo em tempo real por marca">
                <thead>
                  <tr className="text-xs font-semibold text-slate-500 uppercase tracking-wider bg-slate-50">
                    <th className="text-left px-5 py-3">Marca</th>
                    <th className="text-right px-4 py-3">GMV Hoje</th>
                    <th className="text-right px-4 py-3">vs Ontem</th>
                    <th className="text-right px-4 py-3">Projecao</th>
                    <th className="text-right px-4 py-3">Clientes</th>
                    <th className="text-right px-4 py-3">Conv.</th>
                    <th className="text-right px-4 py-3">Ticket</th>
                    <th className="text-right px-5 py-3">Ult. hora</th>
                  </tr>
                </thead>
                <tbody>
                  {data.brands.map((b) => (
                    <tr
                      key={b.brand}
                      className={`border-t border-violet-50 hover:bg-violet-50/40 transition-colors cursor-pointer ${
                        selectedBrand === b.brand ? "bg-violet-50/60" : ""
                      }`}
                      onClick={() => setSelectedBrand(b.brand)}
                    >
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-2.5">
                          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${BRAND_DOT[b.brand] ?? "bg-slate-400"}`} />
                          <div className="min-w-[90px]">
                            <p className="text-sm font-semibold text-slate-700 leading-none">{b.label}</p>
                            <GmvProgressBar hoje={b.gmv_hoje} ontem={b.gmv_ontem} />
                          </div>
                        </div>
                      </td>
                      <td className="text-right px-4 py-3.5 text-sm font-bold text-gray-900 tabular-nums">
                        {fmtBrl(b.gmv_hoje)}
                      </td>
                      <td className="text-right px-4 py-3.5">
                        <DeltaBadge pct={b.delta_pct} />
                      </td>
                      <td className="text-right px-4 py-3.5 text-sm text-slate-500 tabular-nums">
                        {b.ritmo_projetado != null
                          ? fmtBrl(b.ritmo_projetado)
                          : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="text-right px-4 py-3.5 text-sm text-slate-600 tabular-nums">
                        {fmtNumber(b.clientes_hoje)}
                      </td>
                      <td className="text-right px-4 py-3.5 text-sm text-slate-600 tabular-nums">
                        {b.conversion_hora != null
                          ? `${b.conversion_hora.toFixed(1)}%`
                          : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="text-right px-4 py-3.5 text-sm text-slate-600 tabular-nums">
                        {b.ticket_medio != null
                          ? fmtBrlFull(b.ticket_medio)
                          : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="text-right px-5 py-3.5 text-sm text-slate-400 tabular-nums">
                        {b.ultima_hora}h
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <p className="text-[11px] text-slate-400 text-center">
              Dados de gold.tiktok_shop_hourly · Media 7d = mesmas horas dos 7 dias anteriores · Ritmo projetado = GMV/hora ativa × 24
            </p>
          </>
        )}
      </main>
    </div>
  );
}
