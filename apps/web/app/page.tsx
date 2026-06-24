"use client";

import { useEffect, useState } from "react";
import type { Marketplace } from "@/lib/mock-data";
import {
  fetchOverview, fetchBrands, fetchMonthly,
  type OverviewData, type BrandRow, type MonthPoint,
} from "@/lib/api-client";
import KpiCard from "@/components/KpiCard";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import PeriodSelector from "@/components/PeriodSelector";
import GmvChart from "@/components/GmvChart";
import BrandPerformanceTable from "@/components/BrandPerformanceTable";
import AppNav from "@/components/AppNav";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { AVAILABLE_MONTHS } from "@/lib/mock-daily";

type Filter = Marketplace | "all";

function fmtSplit(tkGmv: number | null, mlGmv: number | null, shGmv: number | null): string | undefined {
  const parts: string[] = [];
  if (tkGmv) parts.push(`TK ${fmtBrl(tkGmv)}`);
  if (mlGmv) parts.push(`ML ${fmtBrl(mlGmv)}`);
  if (shGmv) parts.push(`SH ${fmtBrl(shGmv)}`);
  return parts.length ? parts.join(" · ") : undefined;
}

export default function Dashboard() {
  const [filter, setFilter] = useState<Filter>("all");
  const [period, setPeriod] = useState(AVAILABLE_MONTHS[0].value);
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [brands, setBrands] = useState<BrandRow[]>([]);
  const [monthly, setMonthly] = useState<MonthPoint[]>([]);
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      fetchOverview(filter, period),
      fetchBrands(filter, period),
      fetchMonthly(filter),
    ]).then(([ov, br, mo]) => {
      setOverview(ov.data);
      setBrands(br.data);
      setMonthly(mo.data as MonthPoint[]);
      setIsLive(ov.live);
      setLoading(false);
    }).catch(() => {
      setError("Falha ao carregar dados. Verifique a conexão e tente novamente.");
      setLoading(false);
    });
  }, [filter, period, retryKey]);

  const periodLabel = AVAILABLE_MONTHS.find((m) => m.value === period)?.label;

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
          <div className="flex items-center gap-3">
            {loading && <span className="text-xs text-violet-400 animate-pulse">Atualizando...</span>}
            <PeriodSelector value={period} onChange={setPeriod} />
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
          {loading ? "Carregando dados..." : error ? "Falha ao carregar dados." : "Dados carregados."}
        </span>

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
              ? `${fmtNumber((overview.tiktok_customers ?? 0) + (overview.ml_unique_buyers ?? 0) + (overview.shopee_unique_buyers ?? 0))} compradores`
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
            if (filter === "tiktok") {
              return <KpiCard label="ROAS" value="N/D" subvalue="Não disponível no TikTok Shop" accent="bg-slate-300" />;
            }
            if (filter === "shopee") {
              return shRoas != null
                ? <KpiCard label="ROAS Shopee" value={`${shRoas.toFixed(1)}x`} subvalue={overview?.ad_spend != null ? `Ad Spend: ${fmtBrl(overview.ad_spend)}` : undefined} accent="bg-emerald-500" />
                : <KpiCard label="ROAS Shopee" value="—" accent="bg-slate-300" />;
            }
            if (filter === "ml") {
              return mlRoas != null
                ? <KpiCard label="ROAS ML" value={`${mlRoas.toFixed(1)}x`} subvalue={overview?.ad_spend != null ? `Ad Spend: ${fmtBrl(overview.ad_spend)}` : undefined} accent="bg-emerald-500" />
                : <KpiCard label="ROAS ML" value="—" accent="bg-slate-300" />;
            }
            // all — mostra ML e Shopee se disponíveis
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
          filter={filter}
          period={period}
          loading={loading}
          periodLabel={periodLabel}
        />

        {/* Evolucao historica */}
        <GmvChart data={monthly} />

        {/* Alerta operacional — apenas dados reais acionaveis */}
        {!loading && filter !== "tiktok" && (() => {
          const lescent = brands.find((b) => b.brand === "lescent");
          if (!lescent || (lescent.ml_gmv ?? 0) > 0) return null;
          return (
            <div className="bg-amber-50 border border-amber-200 rounded-2xl p-4">
              <p className="text-xs font-semibold text-amber-700 uppercase tracking-wider mb-1">Alerta operacional</p>
              <p className="text-sm text-amber-800">
                Lescent ML — GMV = R$0 em {periodLabel ?? period}. Verificar pausa de conta ou falha de ingestao no Data Mart.
              </p>
            </div>
          );
        })()}
      </main>
    </div>
  );
}
