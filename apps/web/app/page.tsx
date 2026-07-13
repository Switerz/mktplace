"use client";

import { Suspense, useEffect, useState } from "react";
import {
  fetchOverview, fetchBrands, fetchTrend,
  type OverviewData, type BrandRow, type TrendPoint,
} from "@/lib/api-client";
import { isMarketplaceSelected } from "@/lib/marketplace-filter";
import { useGlobalFilters } from "@/hooks/useGlobalFilters";
import KpiCard from "@/components/KpiCard";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import BrandFilter from "@/components/BrandFilter";
import DateRangeFilter from "@/components/DateRangeFilter";
import TrendChart from "@/components/TrendChart";
import BrandPerformanceTable from "@/components/BrandPerformanceTable";
import AppNav from "@/components/AppNav";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { fmtPeriodo, fmtRefreshedAt, mockLimitationNote } from "@/lib/filters/format";
import { detectPreset } from "@/lib/filters/presets";

function fmtSplit(tkGmv: number | null, mlGmv: number | null, shGmv: number | null): string | undefined {
  const parts: string[] = [];
  if (tkGmv) parts.push(`TK ${fmtBrl(tkGmv)}`);
  if (mlGmv) parts.push(`ML ${fmtBrl(mlGmv)}`);
  if (shGmv) parts.push(`SH ${fmtBrl(shGmv)}`);
  return parts.length ? parts.join(" · ") : undefined;
}

function DashboardInner() {
  const [filters, setFilters] = useGlobalFilters({ defaultPreset: "mes_anterior", defaultCompare: true });
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [brands, setBrands] = useState<BrandRow[]>([]);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [trendGranularity, setTrendGranularity] = useState<"day" | "month">("day");
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);

  useEffect(() => {
    // Ignora a resposta se os filtros mudarem antes dela chegar — evita que
    // uma resposta antiga (ex: intervalo grande, mais lenta) sobrescreva o
    // estado de um filtro mais recente aplicado em seguida.
    let ignore = false;
    setLoading(true);
    setError(null);
    const opts = { brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, compare: filters.compare };
    Promise.all([
      fetchOverview(filters.channels, undefined, opts),
      fetchBrands(filters.channels, undefined, opts),
      fetchTrend(filters.channels, opts),
    ]).then(([ov, br, tr]) => {
      if (ignore) return;
      setOverview(ov.data);
      setBrands(br.data);
      setTrend(tr.data);
      setTrendGranularity(tr.granularity);
      setIsLive(ov.live);
      setRefreshedAt(ov.meta.refreshedAt);
      setLoading(false);
    }).catch(() => {
      if (ignore) return;
      setError("Falha ao carregar dados. Verifique a conexão e tente novamente.");
      setLoading(false);
    });
    return () => { ignore = true; };
  }, [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey]);

  const periodLabel = fmtPeriodo(filters.dateFrom, filters.dateTo);
  const isEmpty = !loading && !error && overview != null && overview.gmv === 0 && brands.length === 0;

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
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div className="flex items-start gap-3 flex-wrap min-w-0">
            <MarketplaceFilter value={filters.channels} onChange={(channels) => setFilters({ channels })} />
            <BrandFilter value={filters.brands} onChange={(brands) => setFilters({ brands })} />
          </div>
          <div className="flex items-center gap-3 min-w-0 flex-wrap">
            {loading && <span className="text-xs text-violet-400 animate-pulse shrink-0">Atualizando...</span>}
            <DateRangeFilter
              dateFrom={filters.dateFrom}
              dateTo={filters.dateTo}
              compare={filters.compare}
              onChange={(v) => setFilters(v)}
              onCompareChange={(compare) => setFilters({ compare })}
            />
          </div>
        </div>

        <p className="text-xs text-slate-400 -mt-3">
          Período: {periodLabel}
          {refreshedAt && <> · Atualizado em {fmtRefreshedAt(refreshedAt)}</>}
        </p>

        {(() => {
          const isCustomPeriod = detectPreset(filters.dateFrom, filters.dateTo) !== "mes_anterior";
          const note = mockLimitationNote(isLive, filters.brands, isCustomPeriod);
          return note && (
            <div className="bg-amber-50 border border-amber-200 rounded-2xl p-3">
              <p className="text-xs text-amber-800">{note}</p>
            </div>
          );
        })()}

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
          {loading ? "Carregando dados..." : error ? "Falha ao carregar dados." : "Dados carregados."}
        </span>

        {isEmpty ? (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-12 text-center">
            <p className="text-slate-500 text-sm font-medium">Sem dados no período e filtros selecionados.</p>
            <p className="text-slate-400 text-xs mt-1">Tente ampliar o intervalo de datas ou revisar canal/marca.</p>
          </div>
        ) : (
          <>
            {/* KPI Cards */}
            <div
              className="grid grid-cols-2 md:grid-cols-4 gap-4"
              aria-busy={loading}
            >
              <KpiCard
                label="GMV Total"
                value={overview ? fmtBrl(overview.gmv) : "—"}
                subvalue={overview ? fmtSplit(overview.tiktok_gmv, overview.ml_gmv, overview.shopee_gmv) : undefined}
                mom={overview?.gmv_mom_pct ?? null}
                accent="bg-violet-600"
              />
              <KpiCard
                label="Pedidos"
                value={overview ? fmtNumber(overview.orders) : "—"}
                subvalue={overview && (overview.tiktok_customers != null || overview.ml_unique_buyers != null || overview.shopee_unique_buyers != null)
                  // Soma diaria, nao comprador unico do periodo — o mesmo
                  // comprador pode ser contado em mais de um dia (ver
                  // docs/kpi_dictionary.md, nota "Compradores — soma diaria").
                  ? `${fmtNumber((overview.tiktok_customers ?? 0) + (overview.ml_unique_buyers ?? 0) + (overview.shopee_unique_buyers ?? 0))} compradores (soma diária, não único no período)`
                  : undefined}
                accent="bg-cyan-500"
              />
              <KpiCard
                label="Ticket Médio"
                value={overview ? fmtBrl(overview.avg_ticket) : "—"}
                accent="bg-amber-500"
              />
              {(() => {
                const mlRoas = overview?.ml_roas;
                const shRoas = overview?.shopee_roas;
                const showMl = isMarketplaceSelected(filters.channels, "ml");
                const showSh = isMarketplaceSelected(filters.channels, "shopee");

                if (!showMl && !showSh) {
                  return <KpiCard label="ROAS" value="N/D" subvalue="Não disponível no TikTok Shop" accent="bg-slate-300" />;
                }
                if (showSh && !showMl) {
                  return shRoas != null
                    ? <KpiCard label="ROAS Shopee" value={`${shRoas.toFixed(1)}x`} subvalue={overview?.ad_spend != null ? `Ad Spend: ${fmtBrl(overview.ad_spend)}` : undefined} accent="bg-emerald-500" />
                    : <KpiCard label="ROAS Shopee" value="—" accent="bg-slate-300" />;
                }
                if (showMl && !showSh) {
                  return mlRoas != null
                    ? <KpiCard label="ROAS ML" value={`${mlRoas.toFixed(1)}x`} subvalue={overview?.ad_spend != null ? `Ad Spend: ${fmtBrl(overview.ad_spend)}` : undefined} accent="bg-emerald-500" />
                    : <KpiCard label="ROAS ML" value="—" accent="bg-slate-300" />;
                }
                // ML + Shopee selecionados — mostra ambos se disponíveis
                const parts: string[] = [];
                if (mlRoas != null) parts.push(`ML ${mlRoas.toFixed(1)}x`);
                if (shRoas != null) parts.push(`SH ${shRoas.toFixed(1)}x`);
                return <KpiCard
                  label="ROAS Ads"
                  value={parts.length ? parts[0].split(" ")[1] : "—"}
                  subvalue={parts.length > 1 ? parts.join(" · ") : (overview?.ad_spend != null ? `Ad Spend: ${fmtBrl(overview.ad_spend)}` : undefined)}
                  accent={parts.length ? "bg-emerald-500" : "bg-slate-300"}
                />;
              })()}
            </div>

            {/* Tabela por marca */}
            <BrandPerformanceTable
              brands={brands}
              filter={filters.channels}
              period={filters.dateTo.slice(0, 7)}
              loading={loading}
              periodLabel={periodLabel}
            />

            {/* Tendencia do periodo selecionado — respeita canal e marca; a
                soma da serie reconcilia com o GMV do KPI acima, pois ambos
                vem da mesma consulta filtrada no backend (ver get_trend). */}
            <TrendChart data={trend} granularity={trendGranularity} loading={loading} />

            {/* Alerta operacional — apenas dados reais acionaveis (especifico de ML) */}
            {!loading && isMarketplaceSelected(filters.channels, "ml") && (() => {
              const lescent = brands.find((b) => b.brand === "lescent");
              if (!lescent || (lescent.ml_gmv ?? 0) > 0) return null;
              return (
                <div className="bg-amber-50 border border-amber-200 rounded-2xl p-4">
                  <p className="text-xs font-semibold text-amber-700 uppercase tracking-wider mb-1">Alerta operacional</p>
                  <p className="text-sm text-amber-800">
                    Lescent ML — GMV = R$0 em {periodLabel}. Verificar pausa de conta ou falha de ingestao no Data Mart.
                  </p>
                </div>
              );
            })()}
          </>
        )}
      </main>
    </div>
  );
}

export default function Dashboard() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#f8f7ff]" />}>
      <DashboardInner />
    </Suspense>
  );
}
