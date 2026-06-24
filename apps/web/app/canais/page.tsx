"use client";

import { useEffect, useState } from "react";
import type { Marketplace } from "@/lib/mock-data";
import {
  fetchCanais,
  type CanaisKpis,
  type CanaisBrandRow,
} from "@/lib/api-client";
import KpiCard from "@/components/KpiCard";
import { SkeletonKpiCard, SkeletonTableRows } from "@/components/Skeleton";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import PeriodSelector from "@/components/PeriodSelector";
import AppNav from "@/components/AppNav";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { AVAILABLE_MONTHS } from "@/lib/mock-daily";

type Filter = Marketplace | "all";

function fmtPct(v: number | null, dec = 1): string {
  if (v == null) return "—";
  return v.toFixed(dec) + "%";
}

function AttributionBar({
  video, live, card,
}: { video: number | null; live: number | null; card: number | null }) {
  const v = video ?? 0;
  const l = live ?? 0;
  const c = card ?? 0;
  const others = Math.max(0, 100 - v - l - c);
  return (
    <div
      className="flex h-2 rounded-full overflow-hidden w-28 bg-slate-100"
      title={`Video ${v.toFixed(0)}% · Live ${l.toFixed(0)}% · Card ${c.toFixed(0)}%`}
    >
      <div className="bg-violet-500 transition-all" style={{ width: `${v}%` }} />
      <div className="bg-cyan-500 transition-all" style={{ width: `${l}%` }} />
      <div className="bg-amber-400 transition-all" style={{ width: `${c}%` }} />
      {others > 0.5 && <div className="bg-slate-300 transition-all" style={{ width: `${others}%` }} />}
    </div>
  );
}

function dominantChannel(video: number | null, live: number | null, card: number | null): "video" | "live" | "card" {
  const v = video ?? 0; const l = live ?? 0; const c = card ?? 0;
  if (v >= l && v >= c) return "video";
  if (l >= v && l >= c) return "live";
  return "card";
}

const CHANNEL_STYLE = {
  video: "text-violet-800 bg-violet-100 font-bold",
  live: "text-cyan-800 bg-cyan-100 font-bold",
  card: "text-amber-800 bg-amber-100 font-bold",
};
const DIM_STYLE = "text-slate-500";

function convRateStyle(v: number | null): string {
  if (v == null) return DIM_STYLE;
  if (v >= 4) return "text-emerald-700 bg-emerald-50 font-semibold";
  if (v >= 2) return "text-amber-700 bg-amber-50 font-semibold";
  return "text-rose-700 bg-rose-50 font-semibold";
}

function repeatRateStyle(v: number | null): string {
  if (v == null) return DIM_STYLE;
  if (v >= 15) return "text-emerald-700 bg-emerald-50 font-semibold";
  if (v >= 8) return "text-amber-700 bg-amber-50 font-semibold";
  return "text-rose-700 bg-rose-50 font-semibold";
}

function newBuyerPctStyle(v: number | null): string {
  if (v == null) return DIM_STYLE;
  if (v >= 85) return "text-emerald-700 bg-emerald-50 font-semibold";
  if (v >= 70) return "text-amber-700 bg-amber-50 font-semibold";
  return "text-rose-700 bg-rose-50 font-semibold";
}

export default function CanaisPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const [period, setPeriod] = useState(AVAILABLE_MONTHS[0].value);
  const [kpis, setKpis] = useState<CanaisKpis | null>(null);
  const [brands, setBrands] = useState<CanaisBrandRow[]>([]);
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchCanais(filter, period)
      .then((r) => {
        setKpis(r.kpis);
        setBrands(r.brands);
        setIsLive(r.live);
        setLoading(false);
      })
      .catch(() => {
        setError("Falha ao carregar dados de canais.");
        setLoading(false);
      });
  }, [filter, period, retryKey]);

  const showTiktok = filter !== "ml" && filter !== "shopee";
  const showMl = filter !== "tiktok" && filter !== "shopee";
  const showShopee = filter !== "tiktok" && filter !== "ml";

  const tkBrands = brands.filter((b) => b.tiktok_gmv != null);
  const mlBrands = brands.filter((b) => b.ml_gmv != null);
  const shBrands = brands.filter((b) => b.shopee_gmv != null);

  // totais TikTok
  const tkGmvTotal = tkBrands.reduce((s, b) => s + (b.tiktok_gmv ?? 0), 0);
  const tkVidTotal = tkBrands.reduce((s, b) => s + (b.tiktok_gmv_video ?? 0), 0);
  const tkLiveTotal = tkBrands.reduce((s, b) => s + (b.tiktok_gmv_live ?? 0), 0);
  const tkCardTotal = tkBrands.reduce((s, b) => s + (b.tiktok_gmv_card ?? 0), 0);
  const tkVisitorsTotal = tkBrands.reduce((s, b) => s + (b.tiktok_visitors ?? 0), 0);

  // totais ML
  const mlGmvTotal = mlBrands.reduce((s, b) => s + (b.ml_gmv ?? 0), 0);
  const mlBuyersTotal = mlBrands.reduce((s, b) => s + (b.ml_unique_buyers ?? 0), 0);
  const mlNewTotal = mlBrands.reduce((s, b) => s + (b.ml_new_buyers ?? 0), 0);
  const mlRepeatTotal = mlBrands.reduce((s, b) => s + (b.ml_repeat_buyers ?? 0), 0);

  // totais Shopee
  const shGmvTotal = shBrands.reduce((s, b) => s + (b.shopee_gmv ?? 0), 0);
  const shBuyersTotal = shBrands.reduce((s, b) => s + (b.shopee_unique_buyers ?? 0), 0);
  const shNewTotal = shBrands.reduce((s, b) => s + (b.shopee_new_buyers ?? 0), 0);
  const shRepeatTotal = shBrands.reduce((s, b) => s + (b.shopee_repeat_buyers ?? 0), 0);

  const tkVidPctTotal = tkGmvTotal > 0 ? (tkVidTotal / tkGmvTotal) * 100 : 0;
  const tkLivePctTotal = tkGmvTotal > 0 ? (tkLiveTotal / tkGmvTotal) * 100 : 0;
  const tkCardPctTotal = tkGmvTotal > 0 ? (tkCardTotal / tkGmvTotal) * 100 : 0;

  const mlNewPctTotal = mlBuyersTotal > 0 ? (mlNewTotal / mlBuyersTotal) * 100 : 0;
  const mlRepeatPctTotal = mlBuyersTotal > 0 ? (mlRepeatTotal / mlBuyersTotal) * 100 : 0;
  const mlGmvPerBuyerTotal = mlBuyersTotal > 0 ? mlGmvTotal / mlBuyersTotal : null;

  const shNewPctTotal = shBuyersTotal > 0 ? (shNewTotal / shBuyersTotal) * 100 : 0;
  const shRepeatPctTotal = shBuyersTotal > 0 ? (shRepeatTotal / shBuyersTotal) * 100 : 0;
  const shGmvPerBuyerTotal = shBuyersTotal > 0 ? shGmvTotal / shBuyersTotal : null;
  const shVisitorsTotal = shBrands.reduce((s, b) => s + (b.shopee_visitors ?? 0), 0);
  const shConvRateTotal = shVisitorsTotal > 0 ? (shBuyersTotal / shVisitorsTotal) * 100 : null;

  // Shopee: só renderiza seção completa quando há dados reais
  const hasShopeeData = loading || shBrands.length > 0;

  const periodLabel = AVAILABLE_MONTHS.find((m) => m.value === period)?.label ?? period;

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
          <div>
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
          {loading ? "Carregando dados de canais..." : error ? "Falha ao carregar." : "Dados de canais carregados."}
        </span>

        {/* ── KPI TikTok ── */}
        {showTiktok && (
          <div className="flex flex-col gap-3" aria-busy={loading}>
            {filter === "all" && (
              <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">
                TikTok Shop — Atribuicao
              </p>
            )}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {loading ? (
                <><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /></>
              ) : (
                <>
                  <KpiCard
                    label="Video TikTok"
                    value={fmtPct(kpis?.tiktok_video_pct ?? null)}
                    subvalue={kpis?.tiktok_gmv_video != null ? fmtBrl(kpis.tiktok_gmv_video) : undefined}
                    accent="bg-violet-600"
                  />
                  <KpiCard
                    label="Live TikTok"
                    value={fmtPct(kpis?.tiktok_live_pct ?? null)}
                    subvalue={kpis?.tiktok_gmv_live != null ? fmtBrl(kpis.tiktok_gmv_live) : undefined}
                    accent="bg-cyan-500"
                  />
                  <KpiCard
                    label="Card TikTok"
                    value={fmtPct(kpis?.tiktok_card_pct ?? null)}
                    subvalue={kpis?.tiktok_gmv_card != null ? fmtBrl(kpis.tiktok_gmv_card) : undefined}
                    accent="bg-amber-400"
                  />
                  <KpiCard
                    label="Conversao TikTok"
                    value={fmtPct(kpis?.tiktok_conversion_rate ?? null)}
                    subvalue={kpis?.tiktok_customers != null ? `${fmtNumber(kpis.tiktok_customers)} compradores` : undefined}
                    accent="bg-violet-300"
                  />
                </>
              )}
            </div>
          </div>
        )}

        {/* ── KPI Mercado Livre ── */}
        {showMl && (
          <div className="flex flex-col gap-3" aria-busy={loading}>
            {filter === "all" && (
              <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">
                Mercado Livre — Perfil de compradores
              </p>
            )}
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              {loading ? (
                <><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /></>
              ) : (
                <>
                  <KpiCard
                    label="Novos Compradores ML"
                    value={fmtPct(kpis?.ml_new_buyer_pct ?? null)}
                    subvalue={kpis?.ml_new_buyers != null ? `${fmtNumber(kpis.ml_new_buyers)} novos` : undefined}
                    accent="bg-cyan-500"
                  />
                  <KpiCard
                    label="Recompra ML"
                    value={fmtPct(kpis?.ml_repeat_buyer_rate_pct ?? null)}
                    subvalue={kpis?.ml_repeat_buyers != null ? `${fmtNumber(kpis.ml_repeat_buyers)} recorrentes` : undefined}
                    accent="bg-emerald-500"
                  />
                  <KpiCard
                    label="GMV por Comprador ML"
                    value={kpis?.ml_gmv_per_buyer != null ? fmtBrl(kpis.ml_gmv_per_buyer) : "—"}
                    subvalue={kpis?.ml_unique_buyers != null ? `${fmtNumber(kpis.ml_unique_buyers)} compradores` : undefined}
                    accent="bg-amber-500"
                  />
                </>
              )}
            </div>
          </div>
        )}

        {/* ── KPI Shopee — só quando há dados ── */}
        {showShopee && hasShopeeData && (
          <div className="flex flex-col gap-3" aria-busy={loading}>
            {filter === "all" && (
              <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">
                Shopee — Perfil de compradores
              </p>
            )}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
              {loading ? (
                <><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /></>
              ) : (
                <>
                  <KpiCard
                    label="Novos Compradores Shopee"
                    value={fmtPct(kpis?.shopee_new_buyer_pct ?? null)}
                    subvalue={kpis?.shopee_new_buyers != null ? `${fmtNumber(kpis.shopee_new_buyers)} novos` : undefined}
                    accent="bg-orange-500"
                  />
                  <KpiCard
                    label="Recompra Shopee"
                    value={fmtPct(kpis?.shopee_repeat_buyer_rate_pct ?? null)}
                    subvalue={kpis?.shopee_repeat_buyers != null ? `${fmtNumber(kpis.shopee_repeat_buyers)} recorrentes` : undefined}
                    accent="bg-emerald-500"
                  />
                  <KpiCard
                    label="GMV / Comprador Shopee"
                    value={kpis?.shopee_gmv_per_buyer != null ? fmtBrl(kpis.shopee_gmv_per_buyer) : "—"}
                    subvalue={kpis?.shopee_unique_buyers != null ? `${fmtNumber(kpis.shopee_unique_buyers)} compradores` : undefined}
                    accent="bg-amber-500"
                  />
                  <KpiCard
                    label="Visitantes Shopee"
                    value={kpis?.shopee_visitors != null ? fmtNumber(kpis.shopee_visitors) : "—"}
                    subvalue="Visitas ao perfil no mês"
                    accent="bg-sky-500"
                  />
                  <KpiCard
                    label="Conversão Shopee"
                    value={kpis?.shopee_conversion_rate != null ? fmtPct(kpis.shopee_conversion_rate) : "—"}
                    subvalue="Compradores / Visitantes"
                    accent="bg-violet-400"
                  />
                </>
              )}
            </div>
          </div>
        )}

        {/* ── Placeholder Shopee quando filtro=shopee e sem dados ── */}
        {filter === "shopee" && !loading && !hasShopeeData && (
          <div className="bg-orange-50 border border-orange-100 rounded-2xl p-6 flex flex-col items-center gap-2 text-center">
            <p className="text-sm font-semibold text-orange-700">Shopee — Dados de canal em integração</p>
            <p className="text-xs text-orange-600 max-w-md">
              O perfil de compradores e métricas de canal da Shopee serão disponibilizados assim que o endpoint da API for integrado.
              Os dados de GMV e pedidos já estão disponíveis na visão Gerencial.
            </p>
          </div>
        )}

        {/* ── Tabela: Atribuicao TikTok por canal ── */}
        {showTiktok && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-violet-50 flex items-start justify-between">
              <div>
                <h2 className="text-sm font-semibold text-slate-700">Atribuicao TikTok por Marca</h2>
                <p className="text-xs text-slate-400 mt-0.5">Origem do GMV — video, live e card/vitrine</p>
              </div>
              <div className="flex items-center gap-3 text-[10px] font-semibold text-slate-400 uppercase tracking-widest shrink-0">
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-violet-500 inline-block" /> Video</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-cyan-500 inline-block" /> Live</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> Card</span>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Atribuicao TikTok por marca">
                <thead>
                  <tr className="bg-slate-50">
                    <th className="px-6 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Marca</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">GMV</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Part.%</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Composicao</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Video %</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Live %</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Card %</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Visitantes</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Conversao</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {loading ? (
                    <SkeletonTableRows rows={4} cols={9} />
                  ) : (
                    <>
                      {tkBrands.map((b, i) => {
                        const dom = dominantChannel(b.tiktok_video_pct, b.tiktok_live_pct, b.tiktok_card_pct);
                        const partPct = tkGmvTotal > 0 ? ((b.tiktok_gmv ?? 0) / tkGmvTotal) * 100 : 0;
                        return (
                          <tr key={b.brand} className={`hover:bg-violet-50/50 transition-colors ${i % 2 === 0 ? "" : "bg-gray-50/30"}`}>
                            <td className="px-6 py-3.5 font-semibold text-slate-700 whitespace-nowrap">{b.label}</td>
                            <td className="px-4 py-3.5 text-right tabular-nums text-slate-700 font-medium">{fmtBrl(b.tiktok_gmv!)}</td>
                            <td className="px-4 py-3.5 text-right tabular-nums">
                              <span className="text-slate-500 text-xs">{partPct.toFixed(1)}%</span>
                            </td>
                            <td className="px-4 py-3.5">
                              <AttributionBar video={b.tiktok_video_pct} live={b.tiktok_live_pct} card={b.tiktok_card_pct} />
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums">
                              <span className={`text-xs px-1.5 py-0.5 rounded ${dom === "video" ? CHANNEL_STYLE.video : DIM_STYLE}`}>
                                {fmtPct(b.tiktok_video_pct)}
                              </span>
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums">
                              <span className={`text-xs px-1.5 py-0.5 rounded ${dom === "live" ? CHANNEL_STYLE.live : DIM_STYLE}`}>
                                {fmtPct(b.tiktok_live_pct)}
                              </span>
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums">
                              <span className={`text-xs px-1.5 py-0.5 rounded ${dom === "card" ? CHANNEL_STYLE.card : DIM_STYLE}`}>
                                {fmtPct(b.tiktok_card_pct)}
                              </span>
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums text-slate-500">
                              {b.tiktok_visitors != null ? fmtNumber(b.tiktok_visitors) : "—"}
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums">
                              <span className={`text-xs px-1.5 py-0.5 rounded ${convRateStyle(b.tiktok_conversion_rate)}`}>
                                {fmtPct(b.tiktok_conversion_rate)}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                      {tkBrands.length > 0 && (
                        <tr className="bg-slate-50 border-t border-slate-200">
                          <td className="px-6 py-3 text-xs font-bold text-slate-600 uppercase tracking-wider">Total</td>
                          <td className="px-4 py-3 text-right tabular-nums font-bold text-slate-800 text-sm">{fmtBrl(tkGmvTotal)}</td>
                          <td className="px-4 py-3 text-right tabular-nums"><span className="text-slate-400 text-xs">100%</span></td>
                          <td className="px-4 py-3">
                            <AttributionBar video={tkVidPctTotal} live={tkLivePctTotal} card={tkCardPctTotal} />
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums text-violet-700 text-xs font-bold">{tkVidPctTotal.toFixed(1)}%</td>
                          <td className="px-4 py-3 text-right tabular-nums text-cyan-700 text-xs font-bold">{tkLivePctTotal.toFixed(1)}%</td>
                          <td className="px-4 py-3 text-right tabular-nums text-amber-700 text-xs font-bold">{tkCardPctTotal.toFixed(1)}%</td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-500 text-xs">{fmtNumber(tkVisitorsTotal)}</td>
                          <td className="px-4 py-3 text-right tabular-nums text-xs font-semibold text-slate-600">
                            {kpis?.tiktok_conversion_rate != null ? `${kpis.tiktok_conversion_rate.toFixed(1)}%` : "—"}
                          </td>
                        </tr>
                      )}
                    </>
                  )}
                </tbody>
              </table>
            </div>
            <div className="px-6 py-3 border-t border-slate-100 flex items-center gap-5 flex-wrap">
              <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Dominante:</span>
              <span className="flex items-center gap-1.5 text-xs text-violet-700"><span className="w-2 h-2 rounded-full bg-violet-500 inline-block" /> Video</span>
              <span className="flex items-center gap-1.5 text-xs text-cyan-700"><span className="w-2 h-2 rounded-full bg-cyan-500 inline-block" /> Live</span>
              <span className="flex items-center gap-1.5 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> Card</span>
              <span className="ml-auto text-[10px] text-slate-400">
                Conversao calculada apenas nos dias com dado de visitantes — cobertura estruturalmente limitada pela API TikTok
              </span>
            </div>
          </div>
        )}

        {/* ── Tabela: Perfil de compradores ML ── */}
        {showMl && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-violet-50">
              <h2 className="text-sm font-semibold text-slate-700">Perfil de Compradores ML por Marca</h2>
              <p className="text-xs text-slate-400 mt-0.5">Aquisicao vs. retencao — novos e recorrentes no mes</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Perfil de compradores ML por marca">
                <thead>
                  <tr className="bg-slate-50">
                    <th className="px-6 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Marca</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">GMV</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Part.%</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Compradores</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Novos %</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Novos</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Recorrentes</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Recompra %</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">GMV / Comprador</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {loading ? (
                    <SkeletonTableRows rows={3} cols={9} />
                  ) : (
                    <>
                      {mlBrands.map((b, i) => {
                        const partPct = mlGmvTotal > 0 ? ((b.ml_gmv ?? 0) / mlGmvTotal) * 100 : 0;
                        const newPct = b.ml_new_buyer_pct ??
                          (b.ml_unique_buyers && b.ml_new_buyers ? (b.ml_new_buyers / b.ml_unique_buyers) * 100 : null);
                        return (
                          <tr key={b.brand} className={`hover:bg-violet-50/50 transition-colors ${i % 2 === 0 ? "" : "bg-gray-50/30"}`}>
                            <td className="px-6 py-3.5 font-semibold text-slate-700 whitespace-nowrap">{b.label}</td>
                            <td className="px-4 py-3.5 text-right tabular-nums text-slate-700 font-medium">{fmtBrl(b.ml_gmv!)}</td>
                            <td className="px-4 py-3.5 text-right tabular-nums">
                              <span className="text-slate-500 text-xs">{partPct.toFixed(1)}%</span>
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums text-slate-600">
                              {b.ml_unique_buyers != null ? fmtNumber(b.ml_unique_buyers) : "—"}
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums">
                              <span className={`text-xs px-1.5 py-0.5 rounded ${newBuyerPctStyle(newPct)}`}>{fmtPct(newPct)}</span>
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums text-slate-600">
                              {b.ml_new_buyers != null ? fmtNumber(b.ml_new_buyers) : "—"}
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums text-slate-600">
                              {b.ml_repeat_buyers != null ? fmtNumber(b.ml_repeat_buyers) : "—"}
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums">
                              <span className={`text-xs px-1.5 py-0.5 rounded ${repeatRateStyle(b.ml_repeat_buyer_rate_pct)}`}>
                                {fmtPct(b.ml_repeat_buyer_rate_pct)}
                              </span>
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums text-slate-700 font-medium">
                              {b.ml_gmv_per_buyer != null ? fmtBrl(b.ml_gmv_per_buyer) : "—"}
                            </td>
                          </tr>
                        );
                      })}
                      {mlBrands.length > 0 && (
                        <tr className="bg-slate-50 border-t border-slate-200">
                          <td className="px-6 py-3 text-xs font-bold text-slate-600 uppercase tracking-wider">Total</td>
                          <td className="px-4 py-3 text-right tabular-nums font-bold text-slate-800 text-sm">{fmtBrl(mlGmvTotal)}</td>
                          <td className="px-4 py-3 text-right tabular-nums"><span className="text-slate-400 text-xs">100%</span></td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-xs font-bold">{fmtNumber(mlBuyersTotal)}</td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${newBuyerPctStyle(mlNewPctTotal)}`}>{mlNewPctTotal.toFixed(1)}%</span>
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-xs font-bold">{fmtNumber(mlNewTotal)}</td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-xs font-bold">{fmtNumber(mlRepeatTotal)}</td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${repeatRateStyle(mlRepeatPctTotal)}`}>{mlRepeatPctTotal.toFixed(1)}%</span>
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-700 text-xs font-bold">
                            {mlGmvPerBuyerTotal != null ? fmtBrl(mlGmvPerBuyerTotal) : "—"}
                          </td>
                        </tr>
                      )}
                    </>
                  )}
                </tbody>
              </table>
            </div>
            <div className="px-6 py-3 border-t border-slate-100 flex items-start gap-5 flex-wrap">
              <div className="flex flex-col gap-1.5">
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Novos %:</span>
                  <span className="flex items-center gap-1 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> acima de 85%</span>
                  <span className="flex items-center gap-1 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> 70–85%</span>
                  <span className="flex items-center gap-1 text-xs text-rose-700"><span className="w-2 h-2 rounded-full bg-rose-400 inline-block" /> abaixo de 70%</span>
                </div>
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Recompra %:</span>
                  <span className="flex items-center gap-1 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> acima de 15%</span>
                  <span className="flex items-center gap-1 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> 8–15%</span>
                  <span className="flex items-center gap-1 text-xs text-rose-700"><span className="w-2 h-2 rounded-full bg-rose-400 inline-block" /> abaixo de 8%</span>
                </div>
              </div>
              <p className="ml-auto text-[11px] text-slate-400 self-end">
                Recompra = compradores com historico previo na marca no ML · compradores mensais em validacao
              </p>
            </div>
          </div>
        )}

        {/* ── Tabela: Perfil de compradores Shopee — só quando há dados ── */}
        {showShopee && hasShopeeData && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-violet-50">
              <h2 className="text-sm font-semibold text-slate-700">Perfil de Compradores Shopee por Marca</h2>
              <p className="text-xs text-slate-400 mt-0.5">Aquisicao vs. retencao — novos e recorrentes no mes</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Perfil de compradores Shopee por marca">
                <thead>
                  <tr className="bg-slate-50">
                    <th className="px-6 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Marca</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">GMV</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Part.%</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Compradores</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Novos %</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Novos</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Recorrentes</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Recompra %</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">GMV / Comprador</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Visitantes</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Conversão</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {loading ? (
                    <SkeletonTableRows rows={3} cols={11} />
                  ) : (
                    <>
                      {shBrands.map((b, i) => {
                        const partPct = shGmvTotal > 0 ? ((b.shopee_gmv ?? 0) / shGmvTotal) * 100 : 0;
                        const newPct = b.shopee_new_buyer_pct ??
                          (b.shopee_unique_buyers && b.shopee_new_buyers ? (b.shopee_new_buyers / b.shopee_unique_buyers) * 100 : null);
                        return (
                          <tr key={b.brand} className={`hover:bg-orange-50/40 transition-colors ${i % 2 === 0 ? "" : "bg-gray-50/30"}`}>
                            <td className="px-6 py-3.5 font-semibold text-slate-700 whitespace-nowrap">{b.label}</td>
                            <td className="px-4 py-3.5 text-right tabular-nums text-slate-700 font-medium">{fmtBrl(b.shopee_gmv!)}</td>
                            <td className="px-4 py-3.5 text-right tabular-nums">
                              <span className="text-slate-500 text-xs">{partPct.toFixed(1)}%</span>
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums text-slate-600">
                              {b.shopee_unique_buyers != null ? fmtNumber(b.shopee_unique_buyers) : "—"}
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums">
                              <span className={`text-xs px-1.5 py-0.5 rounded ${newBuyerPctStyle(newPct)}`}>{fmtPct(newPct)}</span>
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums text-slate-600">
                              {b.shopee_new_buyers != null ? fmtNumber(b.shopee_new_buyers) : "—"}
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums text-slate-600">
                              {b.shopee_repeat_buyers != null ? fmtNumber(b.shopee_repeat_buyers) : "—"}
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums">
                              <span className={`text-xs px-1.5 py-0.5 rounded ${repeatRateStyle(b.shopee_repeat_buyer_rate_pct)}`}>
                                {fmtPct(b.shopee_repeat_buyer_rate_pct)}
                              </span>
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums text-slate-700 font-medium">
                              {b.shopee_gmv_per_buyer != null ? fmtBrl(b.shopee_gmv_per_buyer) : "—"}
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums text-slate-600">
                              {b.shopee_visitors != null ? fmtNumber(b.shopee_visitors) : "—"}
                            </td>
                            <td className="px-4 py-3.5 text-right tabular-nums">
                              {b.shopee_conversion_rate != null
                                ? <span className="text-xs px-1.5 py-0.5 rounded bg-sky-50 text-sky-700">{fmtPct(b.shopee_conversion_rate)}</span>
                                : "—"}
                            </td>
                          </tr>
                        );
                      })}
                      {shBrands.length > 0 && (
                        <tr className="bg-slate-50 border-t border-slate-200">
                          <td className="px-6 py-3 text-xs font-bold text-slate-600 uppercase tracking-wider">Total</td>
                          <td className="px-4 py-3 text-right tabular-nums font-bold text-slate-800 text-sm">{fmtBrl(shGmvTotal)}</td>
                          <td className="px-4 py-3 text-right tabular-nums"><span className="text-slate-400 text-xs">100%</span></td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-xs font-bold">{fmtNumber(shBuyersTotal)}</td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${newBuyerPctStyle(shNewPctTotal)}`}>{shNewPctTotal.toFixed(1)}%</span>
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-xs font-bold">{fmtNumber(shNewTotal)}</td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-xs font-bold">{fmtNumber(shRepeatTotal)}</td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${repeatRateStyle(shRepeatPctTotal)}`}>{shRepeatPctTotal.toFixed(1)}%</span>
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-700 text-xs font-bold">
                            {shGmvPerBuyerTotal != null ? fmtBrl(shGmvPerBuyerTotal) : "—"}
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-xs font-bold">
                            {shVisitorsTotal > 0 ? fmtNumber(shVisitorsTotal) : "—"}
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            {shConvRateTotal != null
                              ? <span className="text-xs px-1.5 py-0.5 rounded bg-sky-50 text-sky-700">{shConvRateTotal.toFixed(1)}%</span>
                              : "—"}
                          </td>
                        </tr>
                      )}
                    </>
                  )}
                </tbody>
              </table>
            </div>
            <div className="px-6 py-3 border-t border-slate-100 flex items-start gap-5 flex-wrap">
              <div className="flex flex-col gap-1.5">
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Novos %:</span>
                  <span className="flex items-center gap-1 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> acima de 85%</span>
                  <span className="flex items-center gap-1 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> 70–85%</span>
                  <span className="flex items-center gap-1 text-xs text-rose-700"><span className="w-2 h-2 rounded-full bg-rose-400 inline-block" /> abaixo de 70%</span>
                </div>
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Recompra %:</span>
                  <span className="flex items-center gap-1 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> acima de 15%</span>
                  <span className="flex items-center gap-1 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> 8–15%</span>
                  <span className="flex items-center gap-1 text-xs text-rose-700"><span className="w-2 h-2 rounded-full bg-rose-400 inline-block" /> abaixo de 8%</span>
                </div>
              </div>
              <p className="ml-auto text-[11px] text-slate-400 self-end">
                Recompra = compradores com historico previo na marca na Shopee · compradores mensais em validacao
              </p>
            </div>
          </div>
        )}

        {/* ── Insight: ML como canal de aquisicao ── */}
        {showMl && !loading && mlBrands.length > 0 && mlNewPctTotal >= 80 && (
          <div className="bg-cyan-50 border border-cyan-200 rounded-2xl p-4">
            <p className="text-xs font-semibold text-cyan-700 uppercase tracking-wider mb-1">
              Insight — ML como canal de aquisicao
            </p>
            <p className="text-sm text-cyan-800">
              {mlNewPctTotal.toFixed(0)}% dos compradores ML em {periodLabel} sao novos — sem historico previo com a marca.
              O ML opera como canal de aquisicao primaria, nao de retencao. Recompra media de{" "}
              {mlRepeatPctTotal.toFixed(1)}% indica oportunidade de programas de fidelidade pos-compra.
            </p>
          </div>
        )}

        {/* ── Insight: Shopee como canal de aquisicao ── */}
        {showShopee && hasShopeeData && !loading && shBrands.length > 0 && shNewPctTotal >= 80 && (
          <div className="bg-orange-50 border border-orange-200 rounded-2xl p-4">
            <p className="text-xs font-semibold text-orange-700 uppercase tracking-wider mb-1">
              Insight — Shopee como canal de aquisicao
            </p>
            <p className="text-sm text-orange-800">
              {shNewPctTotal.toFixed(0)}% dos compradores Shopee em {periodLabel} sao novos.
              Recompra media de {shRepeatPctTotal.toFixed(1)}% — perfil de aquisicao similar ao ML.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
