"use client";

import { useEffect, useState } from "react";
import type { Marketplace } from "@/lib/mock-data";
import {
  fetchQuality,
  type QualityKpis,
  type QualityBrandRow,
} from "@/lib/api-client";
import KpiCard from "@/components/KpiCard";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import PeriodSelector from "@/components/PeriodSelector";
import AppNav from "@/components/AppNav";
import { AVAILABLE_MONTHS } from "@/lib/mock-daily";

type Filter = Marketplace | "all";

function fmtRate(v: number | null): string {
  if (v == null) return "—";
  return v.toFixed(1) + "%";
}

function fmtDays(v: number | null): string {
  if (v == null) return "—";
  return v.toFixed(1) + "d";
}

function fmtCount(v: number | null): string {
  if (v == null) return "—";
  return v.toLocaleString("pt-BR");
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

export default function QualityPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const [period, setPeriod] = useState(AVAILABLE_MONTHS[0].value);
  const [kpis, setKpis] = useState<QualityKpis | null>(null);
  const [brands, setBrands] = useState<QualityBrandRow[]>([]);
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchQuality(filter, period)
      .then((result) => {
        setKpis(result.kpis);
        setBrands(result.brands);
        setIsLive(result.live);
        setLoading(false);
      })
      .catch(() => {
        setError("Falha ao carregar dados de qualidade. Verifique a conexão.");
        setLoading(false);
      });
  }, [filter, period, retryKey]);

  const showTiktok = filter !== "ml" && filter !== "shopee";
  const showMl = filter !== "tiktok" && filter !== "shopee";
  const showShopee = filter !== "tiktok" && filter !== "ml";

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
                Demonstracao · API offline
              </span>
            )}
          </div>
        </div>
      </header>

      <AppNav />

      <main className="max-w-7xl mx-auto px-6 py-8 flex flex-col gap-6">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <MarketplaceFilter value={filter} onChange={setFilter} />
          <PeriodSelector value={period} onChange={setPeriod} />
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
          {loading ? "Carregando dados de qualidade..." : error ? "Falha ao carregar." : "Dados de qualidade carregados."}
        </span>

        {/* KPI Cards */}
        <div
          className={`grid grid-cols-2 md:grid-cols-4 gap-4 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}
          aria-busy={loading}
        >
          {showTiktok && (
            <KpiCard
              label="Entrega TK"
              value={fmtDays(kpis?.tiktok_avg_delivery_days ?? null)}
              subvalue="Tempo medio · dados a partir de abr/26"
              accent="bg-violet-500"
            />
          )}
          {showMl && (
            <KpiCard
              label="Cancelamento ML"
              value={fmtRate(kpis?.ml_cancel_rate_pct ?? null)}
              subvalue="Taxa de cancelamento"
              accent="bg-cyan-500"
            />
          )}
          {showMl && (
            <KpiCard
              label="Nao Entregue ML"
              value={fmtRate(kpis?.ml_not_delivered_rate_pct ?? null)}
              subvalue="Taxa de nao-entrega"
              accent="bg-amber-500"
            />
          )}
          {showMl && (
            <KpiCard
              label="Entrega ML"
              value={fmtDays(kpis?.ml_avg_delivery_days ?? null)}
              subvalue="Tempo medio em dias"
              accent="bg-emerald-500"
            />
          )}
          {showShopee && (
            <KpiCard
              label="Cancelamento Shopee"
              value={fmtRate(kpis?.shopee_cancel_rate_pct ?? null)}
              subvalue="Taxa de cancelamento"
              accent="bg-orange-500"
            />
          )}
          {showShopee && (
            <KpiCard
              label="Devolucao Shopee"
              value={fmtRate(kpis?.shopee_return_rate_pct ?? null)}
              subvalue="Taxa de devolucao"
              accent="bg-rose-400"
            />
          )}
        </div>

        {/* Tabela por marca */}
        <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
          <div className="px-6 py-4 border-b border-violet-50">
            <h2 className="text-sm font-semibold text-slate-700">Qualidade por Marca</h2>
            <p className="text-xs text-slate-500 mt-0.5">Pedidos com problema, cancelamentos e logistica</p>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50 text-left">
                  <th className="px-6 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Marca</th>
                  {showTiktok && (
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">
                      <span className="text-violet-500">TK</span> Entrega
                      <span className="ml-1 text-[9px] font-normal text-slate-400 normal-case tracking-normal">abr/26+</span>
                    </th>
                  )}
                  {showMl && (
                    <>
                      <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">
                        <span className="text-cyan-600">ML</span> Cancel.
                      </th>
                      <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">
                        <span className="text-cyan-600">ML</span> N. Entregue
                      </th>
                      <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">
                        <span className="text-cyan-600">ML</span> Entrega
                      </th>
                    </>
                  )}
                  {showShopee && (
                    <>
                      <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">
                        <span className="text-orange-500">SH</span> Cancel.
                      </th>
                      <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">
                        <span className="text-orange-500">SH</span> Devol.
                      </th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody className={`divide-y divide-slate-50 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}>
                {brands.length === 0 && !loading && (
                  <tr>
                    <td colSpan={8} className="px-6 py-8 text-center text-slate-400 text-sm">
                      Sem dados para o periodo selecionado.
                    </td>
                  </tr>
                )}
                {brands.map((b) => {
                  return (
                    <tr key={b.brand} className="hover:bg-slate-50 transition-colors">
                      <td className="px-6 py-4 font-semibold text-slate-700 whitespace-nowrap">{b.label}</td>
                      {showTiktok && (
                        <td className="px-4 py-4 text-right tabular-nums text-slate-600">
                          {fmtDays(b.tiktok_avg_delivery_days)}
                        </td>
                      )}
                      {showMl && (
                        <>
                          <td className={`px-4 py-4 text-right tabular-nums font-semibold ${cancelColor(b.ml_cancel_rate_pct)} ${b.ml_cancel_rate_pct != null ? cancelBg(b.ml_cancel_rate_pct) : ""}`}>
                            {fmtRate(b.ml_cancel_rate_pct)}
                          </td>
                          <td className={`px-4 py-4 text-right tabular-nums font-semibold ${cancelColor(b.ml_not_delivered_rate_pct)} ${b.ml_not_delivered_rate_pct != null ? cancelBg(b.ml_not_delivered_rate_pct) : ""}`}>
                            {fmtRate(b.ml_not_delivered_rate_pct)}
                          </td>
                          <td className="px-4 py-4 text-right tabular-nums text-slate-600">
                            {fmtDays(b.ml_avg_delivery_days)}
                          </td>
                        </>
                      )}
                      {showShopee && (
                        <>
                          <td className={`px-4 py-4 text-right tabular-nums font-semibold ${cancelColor(b.shopee_cancel_rate_pct ?? null)} ${b.shopee_cancel_rate_pct != null ? cancelBg(b.shopee_cancel_rate_pct) : ""}`}>
                            {fmtRate(b.shopee_cancel_rate_pct ?? null)}
                          </td>
                          <td className={`px-4 py-4 text-right tabular-nums font-semibold ${cancelColor(b.shopee_return_rate_pct ?? null)} ${b.shopee_return_rate_pct != null ? cancelBg(b.shopee_return_rate_pct) : ""}`}>
                            {fmtRate(b.shopee_return_rate_pct ?? null)}
                          </td>
                        </>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="px-6 py-3 border-t border-slate-50 flex items-center gap-6 flex-wrap">
            <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">Cancel. ML:</span>
            <span className="flex items-center gap-1.5 text-xs text-emerald-700">
              <span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> &lt;2%
            </span>
            <span className="flex items-center gap-1.5 text-xs text-amber-700">
              <span className="w-2 h-2 rounded-full bg-amber-500 inline-block" /> 2–5%
            </span>
            <span className="flex items-center gap-1.5 text-xs text-rose-700">
              <span className="w-2 h-2 rounded-full bg-rose-500 inline-block" /> &gt;5%
            </span>
            <span className="ml-auto text-[10px] text-slate-400">
              TK Entrega: dados confiaveis a partir de abr/26
            </span>
          </div>
        </div>

        {/* Fidelizacao ML */}
        {showMl && brands.some((b) => b.ml_repeat_buyer_rate_pct != null || b.ml_unique_buyers != null) && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-violet-50 flex items-start justify-between gap-4 flex-wrap">
              <div>
                <h2 className="text-sm font-semibold text-slate-700">Fidelizacao — Mercado Livre</h2>
                <p className="text-xs text-slate-500 mt-0.5">ML e canal de aquisicao (~90% novos por mes). Recompra &lt;10% e esperado.</p>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 text-left">
                    <th className="px-6 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Marca</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Compradores</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Novos</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Recompra%</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">GMV/Buyer</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">MoM GMV</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Frete/GMV</th>
                  </tr>
                </thead>
                <tbody className={`divide-y divide-slate-50 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}>
                  {brands
                    .filter((b) => b.ml_unique_buyers != null || b.ml_repeat_buyer_rate_pct != null)
                    .map((b) => {
                      const recompra = b.ml_repeat_buyer_rate_pct;
                      const recompraColor = recompra == null ? "text-slate-400"
                        : recompra > 20 ? "text-emerald-700 font-semibold"
                        : recompra > 10 ? "text-amber-700 font-semibold"
                        : "text-rose-600 font-semibold";
                      const freteColor = b.ml_shipping_pct_of_gmv == null ? "text-slate-400"
                        : b.ml_shipping_pct_of_gmv < 11 ? "text-emerald-700"
                        : b.ml_shipping_pct_of_gmv < 14 ? "text-amber-700"
                        : "text-rose-700 font-semibold";
                      const momColor = b.ml_gmv_mom_pct == null ? "text-slate-400"
                        : b.ml_gmv_mom_pct >= 0 ? "text-emerald-700" : "text-rose-600";
                      return (
                        <tr key={b.brand} className="hover:bg-slate-50 transition-colors">
                          <td className="px-6 py-4 font-semibold text-slate-700 whitespace-nowrap">{b.label}</td>
                          <td className="px-4 py-4 text-right tabular-nums text-slate-600">
                            {b.ml_unique_buyers != null ? fmtCount(b.ml_unique_buyers) : <span className="text-slate-300">—</span>}
                          </td>
                          <td className="px-4 py-4 text-right tabular-nums text-slate-600">
                            {b.ml_new_buyers != null ? fmtCount(b.ml_new_buyers) : <span className="text-slate-300">—</span>}
                          </td>
                          <td className={`px-4 py-4 text-right tabular-nums ${recompraColor}`}>
                            {recompra != null ? `${recompra.toFixed(1)}%` : <span className="text-slate-300">—</span>}
                          </td>
                          <td className="px-4 py-4 text-right tabular-nums text-slate-600">
                            {b.ml_gmv_per_buyer != null
                              ? `R$ ${b.ml_gmv_per_buyer.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                              : <span className="text-slate-300">—</span>}
                          </td>
                          <td className={`px-4 py-4 text-right tabular-nums ${momColor}`}>
                            {b.ml_gmv_mom_pct != null
                              ? `${b.ml_gmv_mom_pct >= 0 ? "+" : ""}${b.ml_gmv_mom_pct.toFixed(1)}%`
                              : <span className="text-slate-300">—</span>}
                          </td>
                          <td className={`px-4 py-4 text-right tabular-nums ${freteColor}`}>
                            {b.ml_shipping_pct_of_gmv != null ? `${b.ml_shipping_pct_of_gmv.toFixed(1)}%` : <span className="text-slate-300">—</span>}
                          </td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
            <div className="px-6 py-3 border-t border-slate-50 flex items-center gap-6 flex-wrap">
              <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">Recompra:</span>
              <span className="flex items-center gap-1.5 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> &gt;20%</span>
              <span className="flex items-center gap-1.5 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-500 inline-block" /> 10–20%</span>
              <span className="flex items-center gap-1.5 text-xs text-rose-600"><span className="w-2 h-2 rounded-full bg-rose-500 inline-block" /> &lt;10% (normal para aquisicao)</span>
              <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest ml-4">Frete:</span>
              <span className="flex items-center gap-1.5 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> &lt;11%</span>
              <span className="flex items-center gap-1.5 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-500 inline-block" /> 11–14%</span>
              <span className="flex items-center gap-1.5 text-xs text-rose-600"><span className="w-2 h-2 rounded-full bg-rose-500 inline-block" /> &gt;14%</span>
            </div>
          </div>
        )}

        {/* Qualidade Shopee por marca */}
        {showShopee && brands.some((b) => b.shopee_cancel_rate_pct != null || b.shopee_orders != null) && (
          <div className="bg-white border border-orange-100 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-orange-50">
              <h2 className="text-sm font-semibold text-slate-700">Qualidade — Shopee</h2>
              <p className="text-xs text-slate-500 mt-0.5">Cancelamentos e devoluções por marca</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 text-left">
                    <th className="px-6 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Marca</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Pedidos</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Cancelados</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Cancel.%</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Devolvidos</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Devol.%</th>
                  </tr>
                </thead>
                <tbody className={`divide-y divide-slate-50 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}>
                  {brands
                    .filter((b) => b.shopee_orders != null || b.shopee_cancel_rate_pct != null)
                    .map((b) => (
                      <tr key={b.brand} className="hover:bg-orange-50/40 transition-colors">
                        <td className="px-6 py-4 font-semibold text-slate-700 whitespace-nowrap">{b.label}</td>
                        <td className="px-4 py-4 text-right tabular-nums text-slate-600">
                          {b.shopee_orders != null ? fmtCount(b.shopee_orders) : <span className="text-slate-300">—</span>}
                        </td>
                        <td className="px-4 py-4 text-right tabular-nums text-slate-600">
                          {b.shopee_canceled_orders != null ? fmtCount(b.shopee_canceled_orders) : <span className="text-slate-300">—</span>}
                        </td>
                        <td className={`px-4 py-4 text-right tabular-nums font-semibold ${cancelColor(b.shopee_cancel_rate_pct ?? null)} ${b.shopee_cancel_rate_pct != null ? cancelBg(b.shopee_cancel_rate_pct) : ""}`}>
                          {fmtRate(b.shopee_cancel_rate_pct ?? null)}
                        </td>
                        <td className="px-4 py-4 text-right tabular-nums text-slate-600">
                          {b.shopee_returned_orders != null ? fmtCount(b.shopee_returned_orders) : <span className="text-slate-300">—</span>}
                        </td>
                        <td className={`px-4 py-4 text-right tabular-nums font-semibold ${cancelColor(b.shopee_return_rate_pct ?? null)} ${b.shopee_return_rate_pct != null ? cancelBg(b.shopee_return_rate_pct) : ""}`}>
                          {fmtRate(b.shopee_return_rate_pct ?? null)}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
            <div className="px-6 py-3 border-t border-slate-50 flex items-center gap-6 flex-wrap">
              <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">Cancel./Devol.:</span>
              <span className="flex items-center gap-1.5 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> &lt;2%</span>
              <span className="flex items-center gap-1.5 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-500 inline-block" /> 2–5%</span>
              <span className="flex items-center gap-1.5 text-xs text-rose-700"><span className="w-2 h-2 rounded-full bg-rose-500 inline-block" /> &gt;5%</span>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
