"use client";

import { useEffect, useState } from "react";
import type { Marketplace } from "@/lib/mock-data";
import {
  fetchFinanceiro,
  type FinanceiroKpis,
  type FinanceiroBrandRow,
} from "@/lib/api-client";
import KpiCard from "@/components/KpiCard";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import PeriodSelector from "@/components/PeriodSelector";
import AppNav from "@/components/AppNav";
import { fmtBrl } from "@/lib/formatters";
import { AVAILABLE_MONTHS } from "@/lib/mock-daily";

type Filter = Marketplace | "all";

function fmtPct(v: number | null, decimals = 1): string {
  if (v == null) return "—";
  return v.toFixed(decimals) + "%";
}

function fmtRoas(v: number | null): string {
  if (v == null) return "—";
  return v.toFixed(1) + "x";
}

function roasColor(v: number | null): string {
  if (v == null) return "text-slate-400";
  if (v >= 10) return "text-emerald-700";
  if (v >= 5) return "text-amber-700";
  return "text-rose-700";
}

function acosColor(v: number | null): string {
  if (v == null) return "text-slate-400";
  if (v <= 15) return "text-emerald-700";
  if (v <= 25) return "text-amber-700";
  return "text-rose-700";
}

function totalCostColor(v: number | null): string {
  if (v == null) return "text-slate-400";
  if (v <= 18) return "text-emerald-700";
  if (v <= 25) return "text-amber-700";
  return "text-rose-700";
}

function feePctColor(v: number | null): string {
  if (v == null) return "text-slate-500";
  if (v < 20) return "text-slate-600";
  if (v < 30) return "text-amber-700";
  return "text-rose-700";
}

function CostBar({ adPct, freteP }: { adPct: number | null; freteP: number | null }) {
  if (adPct == null && freteP == null) return null;
  const ad = Math.max(0, adPct ?? 0);
  const frete = Math.max(0, freteP ?? 0);
  const total = Math.min(100, ad + frete);
  const rest = Math.max(0, 100 - total);
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full overflow-hidden bg-slate-100 flex">
        <div className="h-full bg-cyan-400" style={{ width: `${ad}%` }} />
        <div className="h-full bg-amber-300" style={{ width: `${frete}%` }} />
        <div className="h-full bg-slate-100" style={{ width: `${rest}%` }} />
      </div>
      <span className="text-xs tabular-nums text-slate-500 w-11 text-right shrink-0">
        {(ad + frete).toFixed(1)}%
      </span>
    </div>
  );
}

export default function FinanceiroPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const [period, setPeriod] = useState(AVAILABLE_MONTHS[0].value);
  const [kpis, setKpis] = useState<FinanceiroKpis | null>(null);
  const [brands, setBrands] = useState<FinanceiroBrandRow[]>([]);
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchFinanceiro(filter, period)
      .then((result) => {
        setKpis(result.kpis);
        setBrands(result.brands);
        setIsLive(result.live);
        setLoading(false);
      })
      .catch(() => {
        setError("Falha ao carregar dados financeiros.");
        setLoading(false);
      });
  }, [filter, period, retryKey]);

  const showTiktok = filter !== "ml" && filter !== "shopee";
  const showMl = filter !== "tiktok" && filter !== "shopee";
  const showShopee = filter !== "tiktok" && filter !== "ml";

  const tkBrands = brands.filter((b) => b.tiktok_gmv != null);
  const mlBrands = brands.filter((b) => b.ml_ad_spend != null);
  const shBrands = brands.filter((b) => b.shopee_gmv != null);

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
              <p className="text-xs text-slate-400">Gobeaute · Marketplaces</p>
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
            <p className="text-sm text-rose-800">{error}</p>
            <button
              onClick={() => { setError(null); setRetryKey((k) => k + 1); }}
              className="text-xs font-semibold text-rose-700 border border-rose-300 rounded-lg px-3 py-1.5 hover:bg-rose-100 transition-colors shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
            >
              Tentar novamente
            </button>
          </div>
        )}

        <span className="sr-only" aria-live="polite" aria-atomic="true">
          {loading ? "Carregando dados financeiros..." : error ? "Falha ao carregar." : "Dados financeiros carregados."}
        </span>

        {/* KPI Cards */}
        <div
          className={`grid grid-cols-2 md:grid-cols-4 gap-4 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}
          aria-busy={loading}
        >
          {showTiktok && (
            <KpiCard
              label="Repasse recebido TikTok"
              value={kpis?.tiktok_settlement != null ? fmtBrl(kpis.tiktok_settlement) : "—"}
              subvalue={kpis?.tiktok_avg_settlement_pct != null ? `${fmtPct(kpis.tiktok_avg_settlement_pct)} do GMV · competencias podem divergir` : undefined}
              accent="bg-violet-600"
            />
          )}
          {showTiktok && (
            <KpiCard
              label="Taxas e encargos / GMV"
              value={kpis?.tiktok_avg_fee_pct != null ? fmtPct(kpis.tiktok_avg_fee_pct) : "—"}
              subvalue={kpis?.tiktok_fees != null ? fmtBrl(kpis.tiktok_fees) + " total" : undefined}
              accent="bg-violet-400"
            />
          )}
          {showMl && (
            <KpiCard
              label="ROAS ML"
              value={fmtRoas(kpis?.ml_roas ?? null)}
              subvalue={kpis?.ml_acos_pct != null ? `ACOS ${fmtPct(kpis.ml_acos_pct)}` : undefined}
              accent="bg-cyan-500"
            />
          )}
          {showMl && (
            <KpiCard
              label="Ads + Frete / GMV"
              value={kpis?.ml_total_cost_pct != null ? fmtPct(kpis.ml_total_cost_pct) : "—"}
              subvalue="Nao inclui comissao do Mercado Livre"
              accent="bg-amber-500"
            />
          )}
          {showShopee && (
            <KpiCard
              label="Taxas e encargos Shopee"
              value={kpis?.shopee_avg_fee_pct != null ? fmtPct(kpis.shopee_avg_fee_pct) : "—"}
              subvalue={kpis?.shopee_fees != null ? fmtBrl(kpis.shopee_fees) + " total" : undefined}
              accent="bg-orange-400"
            />
          )}
          {showShopee && (
            <KpiCard
              label="ROAS Shopee"
              value={kpis?.shopee_roas != null ? fmtRoas(kpis.shopee_roas) : "—"}
              subvalue={kpis?.shopee_ad_spend != null ? `Ad spend ${fmtBrl(kpis.shopee_ad_spend)}` : undefined}
              accent="bg-amber-400"
            />
          )}
        </div>

        {/* Tabela: Repasses TikTok */}
        {showTiktok && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-violet-50">
              <h2 className="text-sm font-semibold text-slate-700">Repasses TikTok</h2>
              <p className="text-xs text-slate-500 mt-0.5">GMV bruto, taxas e encargos e repasse recebido por marca</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Repasses TikTok por marca">
                <thead>
                  <tr className="bg-slate-50 text-left">
                    <th className="px-6 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider">Marca</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">GMV</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Taxas (R$)</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Taxas e encargos %</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Repasse %</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Repasse Recebido</th>
                  </tr>
                </thead>
                <tbody className={`divide-y divide-slate-100 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}>
                  {tkBrands.length === 0 && !loading && (
                    <tr>
                      <td colSpan={6} className="px-6 py-8 text-center text-slate-400 text-sm">
                        Sem dados para o periodo selecionado.
                      </td>
                    </tr>
                  )}
                  {tkBrands.map((b) => (
                    <tr key={b.brand} className="hover:bg-slate-50/70 transition-colors">
                      <td className="px-6 py-4 font-semibold text-slate-800 whitespace-nowrap">{b.label}</td>
                      <td className="px-4 py-4 text-right tabular-nums text-slate-700 font-medium">{fmtBrl(b.tiktok_gmv!)}</td>
                      <td className="px-4 py-4 text-right tabular-nums text-slate-600">{fmtBrl(b.tiktok_fees!)}</td>
                      <td className={`px-4 py-4 text-right tabular-nums font-semibold ${feePctColor(b.tiktok_avg_fee_pct)}`}>
                        {fmtPct(b.tiktok_avg_fee_pct)}
                      </td>
                      <td className="px-4 py-4 text-right tabular-nums text-slate-600">
                        {fmtPct(b.tiktok_avg_settlement_pct)}
                      </td>
                      <td className="px-4 py-4 text-right tabular-nums text-gray-900 font-bold">{fmtBrl(b.tiktok_settlement!)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="px-6 py-3 border-t border-slate-100 flex items-center gap-5 flex-wrap">
              <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Taxas e encargos %:</span>
              <span className="flex items-center gap-1.5 text-xs text-slate-600">
                <span className="w-2 h-2 rounded-full bg-slate-400 inline-block" /> abaixo de 20%
              </span>
              <span className="flex items-center gap-1.5 text-xs text-amber-700">
                <span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> 20–30%
              </span>
              <span className="flex items-center gap-1.5 text-xs text-rose-700">
                <span className="w-2 h-2 rounded-full bg-rose-400 inline-block" /> acima de 30%
              </span>
              <span className="ml-auto text-[10px] text-slate-400">
                Taxas e encargos / GMV · Repasse % = Repasse Recebido / GMV · repasses e GMV podem ter competencias diferentes (o repasse pode incluir pedidos de outros meses)
              </span>
            </div>
          </div>
        )}

        {/* Tabela: Publicidade e Custos ML */}
        {showMl && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-violet-50">
              <h2 className="text-sm font-semibold text-slate-700">Publicidade e Custos ML</h2>
              <p className="text-xs text-slate-500 mt-0.5">Ad Spend, receita atribuida, frete e custo total como % do GMV</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Publicidade e custos ML por marca">
                <thead>
                  <tr className="bg-slate-50 text-left">
                    <th className="px-6 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider">Marca</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">GMV ML</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Ad Spend</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Receita Ads</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">ROAS</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">ACOS</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Frete</th>
                    <th className="px-6 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider">Ads + Frete / GMV</th>
                  </tr>
                </thead>
                <tbody className={`divide-y divide-slate-100 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}>
                  {mlBrands.length === 0 && !loading && (
                    <tr>
                      <td colSpan={8} className="px-6 py-8 text-center text-slate-400 text-sm">
                        Sem dados de anuncios ML para o periodo selecionado.
                      </td>
                    </tr>
                  )}
                  {mlBrands.map((b) => {
                    const adPct = b.ml_ad_spend != null && b.ml_gmv != null && b.ml_gmv > 0
                      ? b.ml_ad_spend / b.ml_gmv * 100
                      : null;
                    return (
                      <tr key={b.brand} className="hover:bg-slate-50/70 transition-colors">
                        <td className="px-6 py-4 font-semibold text-slate-800 whitespace-nowrap">{b.label}</td>
                        <td className="px-4 py-4 text-right tabular-nums text-slate-700 font-medium">{fmtBrl(b.ml_gmv!)}</td>
                        <td className="px-4 py-4 text-right tabular-nums text-slate-700">{fmtBrl(b.ml_ad_spend!)}</td>
                        <td className="px-4 py-4 text-right tabular-nums text-emerald-700 font-medium">
                          {b.ml_ad_revenue != null ? fmtBrl(b.ml_ad_revenue) : "—"}
                        </td>
                        <td className={`px-4 py-4 text-right tabular-nums font-semibold ${roasColor(b.ml_roas)}`}>
                          {fmtRoas(b.ml_roas)}
                        </td>
                        <td className={`px-4 py-4 text-right tabular-nums font-semibold ${acosColor(b.ml_acos_pct)}`}>
                          {fmtPct(b.ml_acos_pct)}
                        </td>
                        <td className="px-4 py-4 text-right tabular-nums text-slate-700">
                          {b.ml_seller_shipping_cost != null ? fmtBrl(b.ml_seller_shipping_cost) : "—"}
                        </td>
                        <td className="px-6 py-4 min-w-[160px]">
                          <CostBar adPct={adPct} freteP={b.ml_shipping_pct_of_gmv} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="px-6 py-3 border-t border-slate-100 flex items-center gap-5 flex-wrap">
              <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">ROAS:</span>
              <span className="flex items-center gap-1.5 text-xs text-emerald-700">
                <span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> acima de 10x
              </span>
              <span className="flex items-center gap-1.5 text-xs text-amber-700">
                <span className="w-2 h-2 rounded-full bg-amber-500 inline-block" /> 5x–10x
              </span>
              <span className="flex items-center gap-1.5 text-xs text-rose-700">
                <span className="w-2 h-2 rounded-full bg-rose-500 inline-block" /> abaixo de 5x
              </span>
              <span className="text-slate-200 select-none mx-1">|</span>
              <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Ads + Frete / GMV:</span>
              <span className="flex items-center gap-1.5 text-xs text-slate-600">
                <span className="inline-block w-2 h-2 rounded-sm bg-cyan-400" /> Ads
              </span>
              <span className="flex items-center gap-1.5 text-xs text-slate-600">
                <span className="inline-block w-2 h-2 rounded-sm bg-amber-300" /> Frete
              </span>
              <span className="ml-auto text-[10px] text-slate-400">Ads + Frete / GMV · nao inclui comissao do Mercado Livre (sem granularidade mensal/diaria na fonte atual) · Receita Ads = GMV atribuido a anuncios</span>
            </div>
          </div>
        )}

        {/* Tabela: Taxas e Custos Shopee */}
        {showShopee && (
          <div className="bg-white border border-orange-100 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-orange-50">
              <h2 className="text-sm font-semibold text-slate-700">Taxas e Custos Shopee</h2>
              <p className="text-xs text-slate-500 mt-0.5">GMV, taxas e encargos, total global dos pedidos, anuncios e frete por marca</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Taxas e custos Shopee por marca">
                <thead>
                  <tr className="bg-slate-50 text-left">
                    <th className="px-6 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider">Marca</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">GMV</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Taxas (R$)</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Taxas e encargos %</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Total Global (pedidos)</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Ad Spend</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">ROAS</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Frete</th>
                  </tr>
                </thead>
                <tbody className={`divide-y divide-slate-100 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}>
                  {shBrands.length === 0 && !loading && (
                    <tr>
                      <td colSpan={8} className="px-6 py-8 text-center text-slate-400 text-sm">
                        Sem dados Shopee para o periodo selecionado.
                      </td>
                    </tr>
                  )}
                  {shBrands.map((b) => (
                    <tr key={b.brand} className="hover:bg-orange-50/40 transition-colors">
                      <td className="px-6 py-4 font-semibold text-slate-800 whitespace-nowrap">{b.label}</td>
                      <td className="px-4 py-4 text-right tabular-nums text-slate-700 font-medium">{fmtBrl(b.shopee_gmv!)}</td>
                      <td className="px-4 py-4 text-right tabular-nums text-slate-600">
                        {b.shopee_fees != null ? fmtBrl(b.shopee_fees) : "—"}
                      </td>
                      <td className={`px-4 py-4 text-right tabular-nums font-semibold ${feePctColor(b.shopee_avg_fee_pct ?? null)}`}>
                        {fmtPct(b.shopee_avg_fee_pct ?? null)}
                      </td>
                      <td className="px-4 py-4 text-right tabular-nums text-gray-900 font-bold">
                        {b.shopee_settlement != null ? fmtBrl(b.shopee_settlement) : "—"}
                      </td>
                      <td className="px-4 py-4 text-right tabular-nums text-slate-700">
                        {b.shopee_ad_spend != null ? fmtBrl(b.shopee_ad_spend) : "—"}
                      </td>
                      <td className={`px-4 py-4 text-right tabular-nums font-semibold ${roasColor(b.shopee_roas ?? null)}`}>
                        {fmtRoas(b.shopee_roas ?? null)}
                      </td>
                      <td className="px-4 py-4 text-right tabular-nums text-slate-600">
                        {b.shopee_shipping_cost != null ? fmtBrl(b.shopee_shipping_cost) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="px-6 py-3 border-t border-slate-100 flex items-center gap-5 flex-wrap">
              <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Taxas e encargos %:</span>
              <span className="flex items-center gap-1.5 text-xs text-slate-600">
                <span className="w-2 h-2 rounded-full bg-slate-400 inline-block" /> abaixo de 20%
              </span>
              <span className="flex items-center gap-1.5 text-xs text-amber-700">
                <span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> 20–30%
              </span>
              <span className="flex items-center gap-1.5 text-xs text-rose-700">
                <span className="w-2 h-2 rounded-full bg-rose-400 inline-block" /> acima de 30%
              </span>
              <span className="ml-auto text-[10px] text-slate-400">
                Taxas e encargos = comissao liquida + taxa de servico liquida, sobre o GMV · o indicador de liquidacao foi removido: o campo antigo (Total Global do pedido) nao representa repasse liquido
              </span>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
