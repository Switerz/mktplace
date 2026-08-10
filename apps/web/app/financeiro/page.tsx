"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  fetchFinanceiro,
  type FinanceiroKpis,
  type FinanceiroBrandRow,
} from "@/lib/api-client";
import { isMarketplaceSelected } from "@/lib/marketplace-filter";
import { useGlobalFilters } from "@/hooks/useGlobalFilters";
import { mergeFilteredHref } from "@/lib/filters/nav-links";
import KpiCard from "@/components/KpiCard";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import BrandFilter from "@/components/BrandFilter";
import DateRangeFilter from "@/components/DateRangeFilter";
import LiveStatusBadge from "@/components/LiveStatusBadge";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import { fmtBrl } from "@/lib/formatters";
import { fmtPeriodo, fmtRefreshedAt, mockLimitationNote } from "@/lib/filters/format";
import { detectPreset } from "@/lib/filters/presets";
import { useSortableTable } from "@/lib/use-sortable-table";
import SortableHeader from "@/components/SortableHeader";
import TableScrollHint from "@/components/TableScrollHint";
import { buildFinanceiroRequestKey } from "@/lib/financeiro-request-key";
import { computeRequestStatus } from "@/lib/request-freshness";

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

function FinanceiroPageInner() {
  const [filters, setFilters] = useGlobalFilters({ defaultPreset: "mes_anterior" });
  const searchParams = useSearchParams();
  const filter = filters.channels; // alias — preserva as referencias existentes abaixo
  const [kpis, setKpis] = useState<FinanceiroKpis | null>(null);
  const [brands, setBrands] = useState<FinanceiroBrandRow[]>([]);
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  // Chave da ultima requisicao resolvida com sucesso — comparada com a
  // chave atual para decidir se o estado em memoria reflete de fato os
  // filtros exibidos agora.
  const [resolvedKey, setResolvedKey] = useState<string | null>(null);

  const requestKey = useMemo(
    () => buildFinanceiroRequestKey({ channels: filters.channels, brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, compare: filters.compare, retryKey }),
    [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey],
  );

  useEffect(() => {
    // Ignora a resposta se os filtros mudarem antes dela chegar.
    let ignore = false;
    setLoading(true);
    setError(null);
    const key = buildFinanceiroRequestKey({ channels: filters.channels, brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, compare: filters.compare, retryKey });
    const opts = { brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, compare: filters.compare };
    fetchFinanceiro(filters.channels, undefined, opts)
      .then((result) => {
        if (ignore) return;
        setKpis(result.kpis);
        setBrands(result.brands);
        setIsLive(result.live);
        setRefreshedAt(result.meta.refreshedAt);
        setResolvedKey(key);
        setLoading(false);
      })
      .catch(() => {
        if (ignore) return;
        setError("Falha ao carregar dados financeiros.");
        // A chave precisa ser marcada como resolvida MESMO na falha — senao
        // `computeRequestStatus` nunca sai de "loading" (resolvedKey nunca
        // bate com requestKey) e a falha da requisicao atual nunca vira
        // "error" de fato (fica presa em loading para sempre).
        setResolvedKey(key);
        setLoading(false);
      });
    return () => { ignore = true; };
  }, [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey]);

  // FINDING 2 (rodada de correcao) — loading/error/fresh SEPARADOS: depois
  // de um erro definitivo, `dataIsFresh` fica `false` para sempre, mas
  // `isLoadingState` tambem precisa ficar `false` (senao skeleton/opacidade/
  // aria-busy continuariam ligados como se ainda estivesse buscando).
  const requestStatus = computeRequestStatus({ loading, error: error != null, resolvedKey, requestKey });
  const dataIsFresh = requestStatus.fresh;
  const isLoadingState = requestStatus.loading;
  const isErrorState = requestStatus.error;

  // Versoes protegidas do estado bruto — nenhum calculo/card/tabela/link
  // abaixo deve ler kpis/brands/isLive/refreshedAt diretamente.
  const displayKpis = dataIsFresh ? kpis : null;
  const displayBrands = dataIsFresh ? brands : [];
  const displayIsLive = dataIsFresh ? isLive : false;
  const displayRefreshedAt = dataIsFresh ? refreshedAt : null;

  const buildHref = useMemo(() => (href: string) => mergeFilteredHref(href, searchParams), [searchParams]);

  const periodLabel = fmtPeriodo(filters.dateFrom, filters.dateTo);

  const showTiktok = isMarketplaceSelected(filter, "tiktok");
  const showMl = isMarketplaceSelected(filter, "ml");
  const showShopee = isMarketplaceSelected(filter, "shopee");

  const tkBrands = displayBrands.filter((b) => b.tiktok_gmv != null);
  const mlBrands = displayBrands.filter((b) => b.ml_ad_spend != null);
  const shBrands = displayBrands.filter((b) => b.shopee_gmv != null);

  const tkColumnTypes = useMemo(() => ({
    brand: "text" as const, gmv: "numeric" as const, fees: "numeric" as const,
    fee_pct: "numeric" as const, settlement_pct: "numeric" as const, settlement: "numeric" as const,
  }), []);
  const tkGetValue = (row: FinanceiroBrandRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "gmv": return row.tiktok_gmv;
      case "fees": return row.tiktok_fees;
      case "fee_pct": return row.tiktok_avg_fee_pct;
      case "settlement_pct": return row.tiktok_avg_settlement_pct;
      case "settlement": return row.tiktok_settlement;
      default: return null;
    }
  };
  const tkSort = useSortableTable(tkBrands, tkGetValue, tkColumnTypes);

  const mlColumnTypes = useMemo(() => ({
    brand: "text" as const, gmv: "numeric" as const, ad_spend: "numeric" as const,
    ad_revenue: "numeric" as const, roas: "numeric" as const, acos: "numeric" as const,
    frete: "numeric" as const, cost_pct: "numeric" as const,
  }), []);
  const mlGetValue = (row: FinanceiroBrandRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "gmv": return row.ml_gmv;
      case "ad_spend": return row.ml_ad_spend;
      case "ad_revenue": return row.ml_ad_revenue;
      case "roas": return row.ml_roas;
      case "acos": return row.ml_acos_pct;
      case "frete": return row.ml_seller_shipping_cost;
      case "cost_pct": return (row.ml_ad_spend != null && row.ml_gmv != null && row.ml_gmv > 0)
        ? (row.ml_ad_spend / row.ml_gmv) * 100 + (row.ml_shipping_pct_of_gmv ?? 0)
        : null;
      default: return null;
    }
  };
  const mlSort = useSortableTable(mlBrands, mlGetValue, mlColumnTypes);

  const shColumnTypes = useMemo(() => ({
    brand: "text" as const, gmv: "numeric" as const, fees: "numeric" as const,
    fee_pct: "numeric" as const, settlement: "numeric" as const, ad_spend: "numeric" as const,
    roas: "numeric" as const, frete: "numeric" as const,
  }), []);
  const shGetValue = (row: FinanceiroBrandRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "gmv": return row.shopee_gmv ?? null;
      case "fees": return row.shopee_fees ?? null;
      case "fee_pct": return row.shopee_avg_fee_pct ?? null;
      case "settlement": return row.shopee_settlement ?? null;
      case "ad_spend": return row.shopee_ad_spend ?? null;
      case "roas": return row.shopee_roas ?? null;
      case "frete": return row.shopee_shipping_cost ?? null;
      default: return null;
    }
  };
  const shSort = useSortableTable(shBrands, shGetValue, shColumnTypes);

  return (
    <PageContainer>
      <PageHeader
        title="Financeiro"
        subtitle="Comparar repasse, taxas, mídia e frete conforme a disponibilidade de dado de cada canal."
        scopeLine={
          <>
            Período: {periodLabel}
            {dataIsFresh && displayRefreshedAt && <> · Atualizado em {fmtRefreshedAt(displayRefreshedAt)}</>}
          </>
        }
        // Badge live/mock só aparece quando os dados sao frescos — nunca o
        // estado da requisicao ANTERIOR durante a transicao de filtro.
        status={
          dataIsFresh ? (
            <LiveStatusBadge live={displayIsLive} />
          ) : isLoadingState ? (
            <span className="text-xs text-slate-500 bg-slate-100 border border-slate-200 rounded-lg px-3 py-1.5 font-medium">
              Atualizando dados...
            </span>
          ) : null
        }
        filters={
          <>
            <div className="flex items-start gap-3 flex-wrap min-w-0">
              <MarketplaceFilter value={filters.channels} onChange={(channels) => setFilters({ channels })} />
              <BrandFilter value={filters.brands} onChange={(brands) => setFilters({ brands })} />
            </div>
            <DateRangeFilter
              dateFrom={filters.dateFrom}
              dateTo={filters.dateTo}
              compare={filters.compare}
              onChange={(v) => setFilters(v)}
              onCompareChange={(compare) => setFilters({ compare })}
              hideCompare
            />
          </>
        }
      />

        {dataIsFresh && (() => {
          const isCustomPeriod = detectPreset(filters.dateFrom, filters.dateTo) !== "mes_anterior";
          const note = mockLimitationNote(displayIsLive, filters.brands, isCustomPeriod);
          return note && (
            <div className="bg-amber-50 border border-amber-200 rounded-2xl p-3">
              <p className="text-xs text-amber-800">{note}</p>
            </div>
          );
        })()}

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
          {isLoadingState ? "Carregando dados financeiros..." : isErrorState ? "Falha ao carregar." : "Dados financeiros carregados."}
        </span>

      {isErrorState ? (
        // FINDING 2: erro definitivo mostra so cabecalho/filtros/banner de
        // erro (ja renderizado acima) — nunca skeleton, nunca dado antigo,
        // nunca `aria-busy=true`. Estado de erro dedicado, sem tentar
        // renderizar KPIs/tabelas.
        <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-10 text-center">
          <p className="text-slate-500 text-sm font-medium">Não foi possível carregar os dados financeiros.</p>
          <p className="text-slate-400 text-xs mt-1">Use "Tentar novamente" no banner de erro acima.</p>
        </div>
      ) : (
      <>
        {/* KPI Cards */}
        <div
          className={`grid grid-cols-2 md:grid-cols-4 gap-4 transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}
          aria-busy={isLoadingState}
        >
          {showTiktok && (
            <KpiCard
              label="Repasse recebido TikTok"
              value={displayKpis?.tiktok_settlement != null ? fmtBrl(displayKpis.tiktok_settlement) : "—"}
              subvalue={displayKpis?.tiktok_avg_settlement_pct != null ? `${fmtPct(displayKpis.tiktok_avg_settlement_pct)} do GMV · competencias podem divergir` : undefined}
              accent="bg-violet-600"
            />
          )}
          {showTiktok && (
            <KpiCard
              label="Taxas e encargos / GMV"
              value={displayKpis?.tiktok_avg_fee_pct != null ? fmtPct(displayKpis.tiktok_avg_fee_pct) : "—"}
              subvalue={displayKpis?.tiktok_fees != null ? fmtBrl(displayKpis.tiktok_fees) + " total" : undefined}
              accent="bg-violet-400"
            />
          )}
          {showMl && (
            <KpiCard
              label="ROAS ML"
              value={fmtRoas(displayKpis?.ml_roas ?? null)}
              subvalue={displayKpis?.ml_acos_pct != null ? `ACOS ${fmtPct(displayKpis.ml_acos_pct)}` : undefined}
              accent="bg-cyan-500"
            />
          )}
          {showMl && (
            <KpiCard
              label="Ads + Frete / GMV"
              value={displayKpis?.ml_total_cost_pct != null ? fmtPct(displayKpis.ml_total_cost_pct) : "—"}
              subvalue="Nao inclui comissao do Mercado Livre"
              accent="bg-amber-500"
            />
          )}
          {showShopee && (
            <KpiCard
              label="Taxas e encargos Shopee"
              value={displayKpis?.shopee_avg_fee_pct != null ? fmtPct(displayKpis.shopee_avg_fee_pct) : "—"}
              subvalue={displayKpis?.shopee_fees != null ? fmtBrl(displayKpis.shopee_fees) + " total" : undefined}
              accent="bg-orange-400"
            />
          )}
          {showShopee && (
            <KpiCard
              label="ROAS Shopee"
              value={displayKpis?.shopee_roas != null ? fmtRoas(displayKpis.shopee_roas) : "—"}
              subvalue={displayKpis?.shopee_ad_spend != null ? `Ad spend ${fmtBrl(displayKpis.shopee_ad_spend)}` : undefined}
              accent="bg-amber-400"
            />
          )}
        </div>

        {/* Navegacao interna compacta (Task 4) */}
        <nav aria-label="Navegação interna da página" className="flex flex-wrap gap-1 -mx-2.5">
          {showTiktok && <a href="#tiktok" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">TikTok Shop</a>}
          {showMl && <a href="#mercado-livre" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">Mercado Livre</a>}
          {showShopee && <a href="#shopee" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">Shopee</a>}
        </nav>

        {/* Tabela: Repasses TikTok */}
        {showTiktok && (
          <div id="tiktok" className="scroll-mt-24 bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-violet-50">
              <h2 className="text-sm font-semibold text-slate-700">Repasses TikTok</h2>
              <p className="text-xs text-slate-500 mt-0.5">GMV bruto, taxas e encargos e repasse recebido por marca</p>
            </div>
            <TableScrollHint>
              <table className="w-full text-sm" aria-label="Repasses TikTok por marca">
                <thead>
                  <tr className="bg-slate-50 text-left">
                    <SortableHeader label="Marca" column="brand" sort={tkSort.sort} onSort={tkSort.toggleSort} align="left" />
                    <SortableHeader label="GMV" column="gmv" sort={tkSort.sort} onSort={tkSort.toggleSort} />
                    <SortableHeader label="Taxas (R$)" column="fees" sort={tkSort.sort} onSort={tkSort.toggleSort} />
                    <SortableHeader label="Taxas e encargos %" column="fee_pct" sort={tkSort.sort} onSort={tkSort.toggleSort} />
                    <SortableHeader label="Repasse %" column="settlement_pct" sort={tkSort.sort} onSort={tkSort.toggleSort} />
                    <SortableHeader label="Repasse Recebido" column="settlement" sort={tkSort.sort} onSort={tkSort.toggleSort} />
                  </tr>
                </thead>
                <tbody className={`divide-y divide-slate-100 transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                  {tkBrands.length === 0 && dataIsFresh && (
                    <tr>
                      <td colSpan={6} className="px-6 py-8 text-center text-slate-400 text-sm">
                        Sem dados para o periodo selecionado.
                      </td>
                    </tr>
                  )}
                  {tkSort.sortedRows.map((b) => (
                    <tr key={b.brand} className="hover:bg-slate-50/70 transition-colors">
                      <td className="px-6 py-4 font-semibold text-slate-800 whitespace-nowrap">
                        <Link
                          href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=tiktok`)}
                          className="hover:text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
                        >
                          {b.label}
                        </Link>
                      </td>
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
            </TableScrollHint>
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
          <div id="mercado-livre" className="scroll-mt-24 bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-violet-50">
              <h2 className="text-sm font-semibold text-slate-700">Publicidade e Custos ML</h2>
              <p className="text-xs text-slate-500 mt-0.5">Ad Spend, receita atribuida, frete e custo total como % do GMV</p>
            </div>
            <TableScrollHint>
              <table className="w-full text-sm" aria-label="Publicidade e custos ML por marca">
                <thead>
                  <tr className="bg-slate-50 text-left">
                    <SortableHeader label="Marca" column="brand" sort={mlSort.sort} onSort={mlSort.toggleSort} align="left" />
                    <SortableHeader label="GMV ML" column="gmv" sort={mlSort.sort} onSort={mlSort.toggleSort} />
                    <SortableHeader label="Ad Spend" column="ad_spend" sort={mlSort.sort} onSort={mlSort.toggleSort} />
                    <SortableHeader label="Receita Ads" column="ad_revenue" sort={mlSort.sort} onSort={mlSort.toggleSort} />
                    <SortableHeader label="ROAS" column="roas" sort={mlSort.sort} onSort={mlSort.toggleSort} />
                    <SortableHeader label="ACOS" column="acos" sort={mlSort.sort} onSort={mlSort.toggleSort} />
                    <SortableHeader label="Frete" column="frete" sort={mlSort.sort} onSort={mlSort.toggleSort} />
                    <SortableHeader label="Ads + Frete / GMV" column="cost_pct" sort={mlSort.sort} onSort={mlSort.toggleSort} align="left" />
                  </tr>
                </thead>
                <tbody className={`divide-y divide-slate-100 transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                  {mlBrands.length === 0 && dataIsFresh && (
                    <tr>
                      <td colSpan={8} className="px-6 py-8 text-center text-slate-400 text-sm">
                        Sem dados de anuncios ML para o periodo selecionado.
                      </td>
                    </tr>
                  )}
                  {mlSort.sortedRows.map((b) => {
                    const adPct = b.ml_ad_spend != null && b.ml_gmv != null && b.ml_gmv > 0
                      ? b.ml_ad_spend / b.ml_gmv * 100
                      : null;
                    return (
                      <tr key={b.brand} className="hover:bg-slate-50/70 transition-colors">
                        <td className="px-6 py-4 font-semibold text-slate-800 whitespace-nowrap">
                          <Link
                            href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=ml`)}
                            className="hover:text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
                          >
                            {b.label}
                          </Link>
                        </td>
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
            </TableScrollHint>

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
          <div id="shopee" className="scroll-mt-24 bg-white border border-orange-100 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-orange-50">
              <h2 className="text-sm font-semibold text-slate-700">Taxas e Custos Shopee</h2>
              <p className="text-xs text-slate-500 mt-0.5">GMV, taxas e encargos, total global dos pedidos, anuncios e frete por marca</p>
            </div>
            <TableScrollHint>
              <table className="w-full text-sm" aria-label="Taxas e custos Shopee por marca">
                <thead>
                  <tr className="bg-slate-50 text-left">
                    <SortableHeader label="Marca" column="brand" sort={shSort.sort} onSort={shSort.toggleSort} align="left" />
                    <SortableHeader label="GMV" column="gmv" sort={shSort.sort} onSort={shSort.toggleSort} />
                    <SortableHeader label="Taxas (R$)" column="fees" sort={shSort.sort} onSort={shSort.toggleSort} />
                    <SortableHeader label="Taxas e encargos %" column="fee_pct" sort={shSort.sort} onSort={shSort.toggleSort} />
                    <SortableHeader label="Total Global (pedidos)" column="settlement" sort={shSort.sort} onSort={shSort.toggleSort} />
                    <SortableHeader label="Ad Spend" column="ad_spend" sort={shSort.sort} onSort={shSort.toggleSort} />
                    <SortableHeader label="ROAS" column="roas" sort={shSort.sort} onSort={shSort.toggleSort} />
                    <SortableHeader label="Frete" column="frete" sort={shSort.sort} onSort={shSort.toggleSort} />
                  </tr>
                </thead>
                <tbody className={`divide-y divide-slate-100 transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                  {shBrands.length === 0 && dataIsFresh && (
                    <tr>
                      <td colSpan={8} className="px-6 py-8 text-center text-slate-400 text-sm">
                        Sem dados Shopee para o periodo selecionado.
                      </td>
                    </tr>
                  )}
                  {shSort.sortedRows.map((b) => (
                    <tr key={b.brand} className="hover:bg-orange-50/40 transition-colors">
                      <td className="px-6 py-4 font-semibold text-slate-800 whitespace-nowrap">
                        <Link
                          href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=shopee`)}
                          className="hover:text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
                        >
                          {b.label}
                        </Link>
                      </td>
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
            </TableScrollHint>
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
      </>
      )}
      </PageContainer>
  );
}

export default function FinanceiroPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#f8f7ff]" />}>
      <FinanceiroPageInner />
    </Suspense>
  );
}
