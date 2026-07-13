"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import {
  fetchRegioesSummary, fetchRegioesByUf, fetchRegioesByBrand, fetchRegioesTrend,
  type RegioesSummaryData, type RegiaoUfRow, type RegiaoBrandRow, type RegiaoTrendPoint,
} from "@/lib/api-client";
import { useGlobalFilters } from "@/hooks/useGlobalFilters";
import KpiCard from "@/components/KpiCard";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import BrandFilter from "@/components/BrandFilter";
import DateRangeFilter from "@/components/DateRangeFilter";
import AppNav from "@/components/AppNav";
import RegioesBrazilMap from "@/components/RegioesBrazilMap";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { fmtPeriodo, fmtRefreshedAt } from "@/lib/filters/format";
import {
  fmtPctOrNA, coverageLabel, coverageBadgeClass, semCoberturaAviso, fmtShareOfTotalPct,
} from "@/lib/regioes-format";
import { useSortableTable } from "@/lib/use-sortable-table";
import SortableHeader from "@/components/SortableHeader";
import TableScrollHint from "@/components/TableScrollHint";

const ALL_UFS = [
  "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
  "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO", "XX",
];

function CoverageBadge({ level }: { level: RegiaoUfRow["coverage_level"] }) {
  return (
    <span className={`inline-block text-[10px] font-semibold uppercase tracking-wide border rounded-full px-2 py-0.5 whitespace-nowrap ${coverageBadgeClass(level)}`}>
      {coverageLabel(level)}
    </span>
  );
}

function RegioesPageInner() {
  const [filters, setFilters] = useGlobalFilters({ defaultPreset: "mes_anterior", defaultCompare: false });
  // Filtro local de UF — nao faz parte do contrato de filtros globais/URL
  // (por design: e' especifico desta tela, ver docs/filtros_globais_contrato.md).
  const [ufFilter, setUfFilter] = useState<string>("");

  const [summary, setSummary] = useState<RegioesSummaryData | null>(null);
  const [byUf, setByUf] = useState<RegiaoUfRow[]>([]);
  const [byBrand, setByBrand] = useState<RegiaoBrandRow[]>([]);
  const [trend, setTrend] = useState<RegiaoTrendPoint[]>([]);
  const [trendGranularity, setTrendGranularity] = useState<"day" | "month">("day");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  const [semCobertura, setSemCobertura] = useState<string[]>([]);

  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setError(null);
    const opts = { brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo };
    const ufOpts = ufFilter ? { ...opts, uf: [ufFilter] } : opts;
    Promise.all([
      fetchRegioesSummary(filters.channels, ufOpts),
      fetchRegioesByUf(filters.channels, ufOpts),
      fetchRegioesByBrand(filters.channels, opts),
      fetchRegioesTrend(filters.channels, opts),
    ]).then(([sm, uf, br, tr]) => {
      if (ignore) return;
      if (sm == null) {
        setError("Falha ao carregar dados regionais. Verifique a conexão e tente novamente.");
        setLoading(false);
        return;
      }
      setSummary(sm);
      setByUf(uf?.data ?? []);
      setByBrand(br?.data ?? []);
      setTrend(tr?.data ?? []);
      setTrendGranularity(tr?.granularity ?? "day");
      setRefreshedAt(sm.refreshed_at);
      setSemCobertura(sm.channels_sem_cobertura_regional);
      setLoading(false);
    }).catch(() => {
      if (ignore) return;
      setError("Falha ao carregar dados regionais. Verifique a conexão e tente novamente.");
      setLoading(false);
    });
    return () => { ignore = true; };
  }, [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, ufFilter, retryKey]);

  const periodLabel = fmtPeriodo(filters.dateFrom, filters.dateTo);
  const isEmpty = !loading && !error && summary != null && summary.orders === 0 && byUf.length === 0;
  const aviso = semCoberturaAviso(semCobertura);

  const ufColumnTypes = useMemo(() => ({
    uf: "text" as const, gmv: "numeric" as const, orders: "numeric" as const,
    share: "numeric" as const, uf_fill_pct: "numeric" as const,
  }), []);
  const ufGetValue = (row: RegiaoUfRow, column: string): string | number | null => {
    switch (column) {
      case "uf": return row.uf;
      case "gmv": return row.gmv;
      case "orders": return row.orders;
      case "share": return summary ? fmtShareOfTotalPct(row.gmv, summary.gmv) : null;
      case "uf_fill_pct": return row.uf_fill_pct;
      default: return null;
    }
  };
  const ufSort = useSortableTable(byUf, ufGetValue, ufColumnTypes);

  const brandColumnTypes = useMemo(() => ({
    brand: "text" as const, marketplace: "text" as const, gmv: "numeric" as const,
    orders: "numeric" as const, uf_fill_pct: "numeric" as const, shipping_pct: "numeric" as const,
  }), []);
  const brandGetValue = (row: RegiaoBrandRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "marketplace": return row.marketplace;
      case "gmv": return row.gmv;
      case "orders": return row.orders;
      case "uf_fill_pct": return row.uf_fill_pct;
      case "shipping_pct": return row.shipping_cost_coverage_pct;
      default: return null;
    }
  };
  const brandSort = useSortableTable(byBrand, brandGetValue, brandColumnTypes);

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
              <p className="text-xs text-slate-400">Gobeauté · Marketplaces</p>
            </div>
          </div>
        </div>
      </header>

      <AppNav />

      <main className="max-w-7xl mx-auto px-6 py-8 flex flex-col gap-6">
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div className="flex items-start gap-3 flex-wrap min-w-0">
            <MarketplaceFilter value={filters.channels} onChange={(channels) => setFilters({ channels })} />
            <BrandFilter value={filters.brands} onChange={(brands) => setFilters({ brands })} />
            <div className="flex items-center gap-1.5 bg-white border border-violet-100 rounded-xl px-3 py-1.5 shadow-sm">
              <label htmlFor="uf-filter" className="text-xs text-slate-500 font-medium">UF</label>
              <select
                id="uf-filter"
                value={ufFilter}
                onChange={(e) => setUfFilter(e.target.value)}
                className="text-sm font-semibold text-violet-700 bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
              >
                <option value="">Todas</option>
                {ALL_UFS.map((uf) => <option key={uf} value={uf}>{uf}</option>)}
              </select>
            </div>
          </div>
          <div className="flex items-center gap-3 min-w-0 flex-wrap">
            {loading && <span className="text-xs text-violet-400 animate-pulse shrink-0">Atualizando...</span>}
            <DateRangeFilter
              dateFrom={filters.dateFrom}
              dateTo={filters.dateTo}
              compare={false}
              onChange={(v) => setFilters(v)}
              onCompareChange={() => {}}
              hideCompare
            />
          </div>
        </div>

        <p className="text-xs text-slate-400 -mt-3">
          Período: {periodLabel}
          {refreshedAt && <> · Atualizado em {fmtRefreshedAt(refreshedAt)}</>}
        </p>

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
          {loading ? "Carregando dados regionais..." : error ? "Falha ao carregar dados regionais." : "Dados regionais carregados."}
        </span>

        {aviso && (
          <div className="bg-amber-50 border border-amber-200 rounded-2xl p-4">
            <p className="text-xs font-semibold text-amber-700 uppercase tracking-wider mb-1">Canal sem cobertura regional</p>
            <p className="text-sm text-amber-800">{aviso}</p>
          </div>
        )}

        {!loading && !error && summary?.coverage_warning && (
          <div className={`rounded-2xl p-4 border ${summary.coverage_level === "low" ? "bg-rose-50 border-rose-200" : "bg-amber-50 border-amber-200"}`}>
            <p className={`text-xs font-semibold uppercase tracking-wider mb-1 ${summary.coverage_level === "low" ? "text-rose-700" : "text-amber-700"}`}>
              {summary.coverage_level === "low" ? "Cobertura de UF baixa" : "Cobertura de UF parcial"}
            </p>
            <p className={`text-sm ${summary.coverage_level === "low" ? "text-rose-800" : "text-amber-800"}`}>
              Apenas {fmtPctOrNA(summary.uf_fill_pct)} dos pedidos elegíveis ({fmtNumber(summary.uf_known_orders)} de {fmtNumber(summary.uf_eligible_orders)}) têm UF identificada no período/filtros selecionados. Os números por UF abaixo refletem só os pedidos com UF conhecida — não é erro, é limitação de dado na fonte.
            </p>
          </div>
        )}

        {isEmpty ? (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-12 text-center">
            <p className="text-slate-500 text-sm font-medium">Sem dados regionais no período e filtros selecionados.</p>
            <p className="text-slate-400 text-xs mt-1">Tente ampliar o intervalo de datas ou revisar canal/marca/UF.</p>
          </div>
        ) : (
          <>
            {/* KPI Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4" aria-busy={loading}>
              <KpiCard
                label="GMV Regional"
                value={summary ? fmtBrl(summary.gmv) : "—"}
                accent="bg-violet-600"
              />
              <KpiCard
                label="Pedidos"
                value={summary ? fmtNumber(summary.orders) : "—"}
                accent="bg-cyan-500"
              />
              <KpiCard
                label="UFs com venda"
                value={summary ? `${summary.ufs_com_venda}/27` : "—"}
                accent="bg-amber-500"
              />
              <KpiCard
                label="Cobertura UF"
                value={summary ? fmtPctOrNA(summary.uf_fill_pct) : "—"}
                subvalue={summary ? coverageLabel(summary.coverage_level) : undefined}
                accent={summary?.coverage_level === "ok" ? "bg-emerald-500" : summary?.coverage_level === "partial" ? "bg-amber-500" : summary?.coverage_level === "low" ? "bg-rose-500" : "bg-slate-300"}
              />
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 -mt-2" aria-busy={loading}>
              <KpiCard
                label="Cobertura Custo Frete"
                value={summary ? fmtPctOrNA(summary.shipping_cost_coverage_pct) : "—"}
                subvalue="Quando aplicável — Shopee não tem este dado na fonte"
                accent="bg-slate-400"
              />
              <KpiCard
                label="Custo Frete Seller"
                value={summary?.seller_shipping_cost != null ? fmtBrl(summary.seller_shipping_cost) : "N/A"}
                accent="bg-slate-400"
              />
            </div>

            {/* Mapa do Brasil por UF — geometria real (SVG), ver RegioesBrazilMap.tsx */}
            <RegioesBrazilMap rows={byUf} totalGmv={summary?.gmv ?? 0} loading={loading} />

            {/* Ranking por UF */}
            <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
              <div className="px-6 py-4 border-b border-violet-50">
                <h2 className="text-sm font-semibold text-slate-700">Ranking por UF</h2>
                <p className="text-xs text-slate-500 mt-0.5">GMV, pedidos e cobertura de identificação de UF, por estado</p>
              </div>
              <TableScrollHint>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-slate-50 text-left">
                      <SortableHeader label="UF" column="uf" sort={ufSort.sort} onSort={ufSort.toggleSort} align="left" />
                      <SortableHeader label="GMV" column="gmv" sort={ufSort.sort} onSort={ufSort.toggleSort} />
                      <SortableHeader label="Pedidos" column="orders" sort={ufSort.sort} onSort={ufSort.toggleSort} />
                      <SortableHeader label="Participação" column="share" sort={ufSort.sort} onSort={ufSort.toggleSort} />
                      <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Cobertura</th>
                    </tr>
                  </thead>
                  <tbody className={`divide-y divide-slate-50 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}>
                    {byUf.length === 0 && !loading && (
                      <tr>
                        <td colSpan={5} className="px-6 py-8 text-center text-slate-400 text-sm">
                          Sem dados por UF para o período e filtros selecionados.
                        </td>
                      </tr>
                    )}
                    {ufSort.sortedRows.map((row) => {
                      const share = summary ? fmtShareOfTotalPct(row.gmv, summary.gmv) : null;
                      return (
                        <tr key={row.uf} className="hover:bg-slate-50 transition-colors">
                          <td className="px-6 py-3 font-semibold text-slate-700 whitespace-nowrap">{row.uf}</td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-600">{fmtBrl(row.gmv)}</td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-600">{fmtNumber(row.orders)}</td>
                          <td className="px-4 py-3 text-right tabular-nums text-slate-600">{share != null ? `${share.toFixed(1)}%` : "N/A"}</td>
                          <td className="px-4 py-3 text-right"><CoverageBadge level={row.coverage_level} /></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </TableScrollHint>
            </div>

            {/* Tabela por marca x marketplace */}
            <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
              <div className="px-6 py-4 border-b border-violet-50">
                <h2 className="text-sm font-semibold text-slate-700">Cobertura por Marca × Canal</h2>
                <p className="text-xs text-slate-500 mt-0.5">GMV, pedidos e cobertura de UF/frete por marca e marketplace</p>
              </div>
              <TableScrollHint>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-slate-50 text-left">
                      <SortableHeader label="Marca" column="brand" sort={brandSort.sort} onSort={brandSort.toggleSort} align="left" />
                      <SortableHeader label="Canal" column="marketplace" sort={brandSort.sort} onSort={brandSort.toggleSort} align="left" />
                      <SortableHeader label="GMV" column="gmv" sort={brandSort.sort} onSort={brandSort.toggleSort} />
                      <SortableHeader label="Pedidos" column="orders" sort={brandSort.sort} onSort={brandSort.toggleSort} />
                      <SortableHeader label="Cobertura UF" column="uf_fill_pct" sort={brandSort.sort} onSort={brandSort.toggleSort} />
                      <SortableHeader label="Cobertura Frete" column="shipping_pct" sort={brandSort.sort} onSort={brandSort.toggleSort} />
                      <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Alerta</th>
                    </tr>
                  </thead>
                  <tbody className={`divide-y divide-slate-50 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}>
                    {byBrand.length === 0 && !loading && (
                      <tr>
                        <td colSpan={7} className="px-6 py-8 text-center text-slate-400 text-sm">
                          Sem dados por marca/canal para o período e filtros selecionados.
                        </td>
                      </tr>
                    )}
                    {brandSort.sortedRows.map((row) => (
                      <tr key={`${row.brand}-${row.marketplace_id}`} className="hover:bg-slate-50 transition-colors">
                        <td className="px-6 py-3 font-semibold text-slate-700 whitespace-nowrap">{row.label}</td>
                        <td className="px-4 py-3 text-slate-600 whitespace-nowrap capitalize">{row.marketplace}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-slate-600">{fmtBrl(row.gmv)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-slate-600">{fmtNumber(row.orders)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-slate-600">{fmtPctOrNA(row.uf_fill_pct)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-slate-600">{fmtPctOrNA(row.shipping_cost_coverage_pct)}</td>
                        <td className="px-4 py-3 text-right">
                          {row.coverage_warning
                            ? <CoverageBadge level={row.coverage_level} />
                            : <span className="text-slate-300 text-xs">—</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScrollHint>
              {semCobertura.length > 0 && (
                <div className="px-6 py-3 border-t border-slate-50">
                  <span className="text-[10px] text-slate-400">
                    {semCobertura.map((c) => (c === "tiktok" ? "TikTok Shop" : c)).join(", ")} não aparece nesta tabela — sem cobertura regional na fonte.
                  </span>
                </div>
              )}
            </div>

            {/* Tendencia — tabela simples (sem mapa/grafico nesta fase) */}
            <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
              <div className="px-6 py-4 border-b border-violet-50 flex items-center justify-between gap-4 flex-wrap">
                <div>
                  <h2 className="text-sm font-semibold text-slate-700">Tendência</h2>
                  <p className="text-xs text-slate-500 mt-0.5">GMV, pedidos e cobertura de UF por período — respeita canal e marca</p>
                </div>
                <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">
                  Granularidade {trendGranularity === "day" ? "diária" : "mensal"}
                </span>
              </div>
              <TableScrollHint className="max-h-80 overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-slate-50">
                    <tr className="text-left">
                      <th className="px-6 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider">Período</th>
                      <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">GMV</th>
                      <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Pedidos</th>
                      <th className="px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider text-right">Cobertura UF</th>
                    </tr>
                  </thead>
                  <tbody className={`divide-y divide-slate-50 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}>
                    {trend.length === 0 && !loading && (
                      <tr>
                        <td colSpan={4} className="px-6 py-8 text-center text-slate-400 text-sm">
                          Sem série de tendência para o período e filtros selecionados.
                        </td>
                      </tr>
                    )}
                    {trend.map((p) => (
                      <tr key={p.date} className="hover:bg-slate-50 transition-colors">
                        <td className="px-6 py-3 font-medium text-slate-700 whitespace-nowrap">{p.label}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-slate-600">{fmtBrl(p.gmv)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-slate-600">{fmtNumber(p.orders)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-slate-600">{fmtPctOrNA(p.uf_fill_pct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScrollHint>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

export default function RegioesPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#f8f7ff]" />}>
      <RegioesPageInner />
    </Suspense>
  );
}
