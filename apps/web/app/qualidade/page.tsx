"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import {
  fetchQuality,
  type QualityKpis,
  type QualityBrandRow,
} from "@/lib/api-client";
import { isMarketplaceSelected } from "@/lib/marketplace-filter";
import { useGlobalFilters } from "@/hooks/useGlobalFilters";
import KpiCard from "@/components/KpiCard";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import BrandFilter from "@/components/BrandFilter";
import DateRangeFilter from "@/components/DateRangeFilter";
import LiveStatusBadge from "@/components/LiveStatusBadge";
import { fmtPeriodo, fmtRefreshedAt, mockLimitationNote } from "@/lib/filters/format";
import { detectPreset } from "@/lib/filters/presets";
import { useSortableTable } from "@/lib/use-sortable-table";
import SortableHeader from "@/components/SortableHeader";
import TableScrollHint from "@/components/TableScrollHint";
import { buildQualityRequestKey } from "@/lib/quality-request-key";
import { computeRequestStatus } from "@/lib/request-freshness";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import {
  TIKTOK_UNAVAILABLE_QUALITY_METRICS,
  TIKTOK_QUALITY_UNAVAILABLE_NOTE,
  qualityLoadedAnnouncement,
} from "@/lib/quality-availability";

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

function QualityPageInner() {
  const [filters, setFilters] = useGlobalFilters({ defaultPreset: "mes_anterior", defaultCompare: true });
  const filter = filters.channels; // alias — preserva as referencias existentes abaixo
  const [kpis, setKpis] = useState<QualityKpis | null>(null);
  const [brands, setBrands] = useState<QualityBrandRow[]>([]);
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  // Chave da ultima requisicao resolvida com sucesso ou falha — comparada
  // com a chave atual para decidir se o estado em memoria reflete de fato
  // os filtros exibidos agora (mesmo padrao Financeiro/Regioes, Gate U4).
  const [resolvedKey, setResolvedKey] = useState<string | null>(null);

  const requestKey = useMemo(
    () => buildQualityRequestKey({ channels: filters.channels, brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, compare: filters.compare, retryKey }),
    [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey],
  );

  useEffect(() => {
    // Ignora a resposta se os filtros mudarem antes dela chegar.
    let ignore = false;
    setLoading(true);
    setError(null);
    const key = buildQualityRequestKey({ channels: filters.channels, brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, compare: filters.compare, retryKey });
    const opts = { brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, compare: filters.compare };
    fetchQuality(filters.channels, undefined, opts)
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
        setError("Falha ao carregar dados de qualidade. Verifique a conexão.");
        // A chave precisa ser marcada como resolvida MESMO na falha — senao
        // `computeRequestStatus` nunca sai de "loading" (resolvedKey nunca
        // bate com requestKey) e a falha da requisicao atual nunca vira
        // "error" de fato (fica presa em loading para sempre).
        setResolvedKey(key);
        setLoading(false);
      });
    return () => { ignore = true; };
  }, [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey]);

  // FINDING 2 (Gate U4) — loading/error/fresh SEPARADOS: depois de um erro
  // definitivo, `dataIsFresh` fica `false` para sempre, mas `isLoadingState`
  // tambem precisa ficar `false` (senao skeleton/opacidade/aria-busy
  // continuariam ligados como se ainda estivesse buscando).
  const requestStatus = computeRequestStatus({ loading, error: error != null, resolvedKey, requestKey });
  const dataIsFresh = requestStatus.fresh;
  const isLoadingState = requestStatus.loading;
  const isErrorState = requestStatus.error;

  // Versoes protegidas do estado bruto — nenhum calculo/card/tabela abaixo
  // deve ler kpis/brands/isLive/refreshedAt diretamente.
  const displayKpis = dataIsFresh ? kpis : null;
  const displayBrands = dataIsFresh ? brands : [];
  const displayIsLive = dataIsFresh ? isLive : false;
  const displayRefreshedAt = dataIsFresh ? refreshedAt : null;

  const periodLabel = fmtPeriodo(filters.dateFrom, filters.dateTo);

  const showTiktok = isMarketplaceSelected(filter, "tiktok");
  const showMl = isMarketplaceSelected(filter, "ml");
  const showShopee = isMarketplaceSelected(filter, "shopee");
  const qualityColSpan = 1 + (showTiktok ? 1 : 0) + (showMl ? 3 : 0) + (showShopee ? 2 : 0);

  const qualityColumnTypes = useMemo(() => ({
    brand: "text" as const, tk_delivery: "numeric" as const, ml_cancel: "numeric" as const,
    ml_not_delivered: "numeric" as const, ml_delivery: "numeric" as const,
    sh_cancel: "numeric" as const, sh_return: "numeric" as const,
  }), []);
  const qualityGetValue = (row: QualityBrandRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "tk_delivery": return row.tiktok_avg_delivery_days;
      case "ml_cancel": return row.ml_cancel_rate_pct;
      case "ml_not_delivered": return row.ml_not_delivered_rate_pct;
      case "ml_delivery": return row.ml_avg_delivery_days;
      case "sh_cancel": return row.shopee_cancel_rate_pct ?? null;
      case "sh_return": return row.shopee_return_rate_pct ?? null;
      default: return null;
    }
  };
  const qualitySort = useSortableTable(displayBrands, qualityGetValue, qualityColumnTypes);
  const qualityVisibleColumns = useMemo(() => {
    const cols = ["brand"];
    if (showTiktok) cols.push("tk_delivery");
    if (showMl) cols.push("ml_cancel", "ml_not_delivered", "ml_delivery");
    if (showShopee) cols.push("sh_cancel", "sh_return");
    return cols;
  }, [showTiktok, showMl, showShopee]);
  useEffect(() => {
    qualitySort.resetSortIfColumnMissing(qualityVisibleColumns);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qualityVisibleColumns.join(",")]);

  const mlLoyaltyRows = displayBrands.filter((b) => b.ml_unique_buyers != null || b.ml_repeat_buyer_rate_pct != null);
  const mlLoyaltyColumnTypes = useMemo(() => ({
    brand: "text" as const, buyers: "numeric" as const, new: "numeric" as const,
    repeat_pct: "numeric" as const, gmv_per_buyer: "numeric" as const,
    mom: "numeric" as const, shipping_pct: "numeric" as const,
  }), []);
  const mlLoyaltyGetValue = (row: QualityBrandRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "buyers": return row.ml_unique_buyers;
      case "new": return row.ml_new_buyers;
      case "repeat_pct": return row.ml_repeat_buyer_rate_pct;
      case "gmv_per_buyer": return row.ml_gmv_per_buyer;
      case "mom": return row.ml_gmv_mom_pct;
      case "shipping_pct": return row.ml_shipping_pct_of_gmv;
      default: return null;
    }
  };
  const mlLoyaltySort = useSortableTable(mlLoyaltyRows, mlLoyaltyGetValue, mlLoyaltyColumnTypes);

  const shQualityRows = displayBrands.filter((b) => b.shopee_orders != null || b.shopee_cancel_rate_pct != null);
  const shQualityColumnTypes = useMemo(() => ({
    brand: "text" as const, orders: "numeric" as const, canceled: "numeric" as const,
    cancel_pct: "numeric" as const, returned: "numeric" as const, return_pct: "numeric" as const,
  }), []);
  const shQualityGetValue = (row: QualityBrandRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "orders": return row.shopee_orders ?? null;
      case "canceled": return row.shopee_canceled_orders ?? null;
      case "cancel_pct": return row.shopee_cancel_rate_pct ?? null;
      case "returned": return row.shopee_returned_orders ?? null;
      case "return_pct": return row.shopee_return_rate_pct ?? null;
      default: return null;
    }
  };
  const shQualitySort = useSortableTable(shQualityRows, shQualityGetValue, shQualityColumnTypes);

  const hasMlLoyalty = showMl && displayBrands.some((b) => b.ml_repeat_buyer_rate_pct != null || b.ml_unique_buyers != null);
  const hasShQuality = showShopee && displayBrands.some((b) => b.shopee_cancel_rate_pct != null || b.shopee_orders != null);

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 flex flex-col gap-6">
      {/* Cabecalho */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div className="min-w-0">
          <h2 className="text-xl font-bold text-gray-900">Qualidade</h2>
          <p className="text-sm text-slate-500">Cancelamentos, devoluções/problemas, entrega e fidelização por canal e marca.</p>
        </div>
        {/* Badge live/mock só aparece quando os dados sao frescos — nunca o
            estado da requisicao ANTERIOR durante a transicao de filtro. */}
        {dataIsFresh ? (
          <LiveStatusBadge live={displayIsLive} />
        ) : isLoadingState ? (
          <span className="text-xs text-slate-500 bg-slate-100 border border-slate-200 rounded-lg px-3 py-1.5 font-medium">
            Atualizando dados...
          </span>
        ) : null}
      </div>

      <div className="flex items-start justify-between flex-wrap gap-3">
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
          />
        </div>

        <p className="text-xs text-slate-400 -mt-3">
          Período: {periodLabel}
          {dataIsFresh && displayRefreshedAt && <> · Atualizado em {fmtRefreshedAt(displayRefreshedAt)}</>}
        </p>

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
          {isLoadingState
            ? "Carregando dados de qualidade..."
            : isErrorState
              ? "Falha ao carregar."
              : qualityLoadedAnnouncement(showTiktok)}
        </span>

      {isErrorState ? (
        // FINDING 2: erro definitivo mostra so cabecalho/filtros/banner de
        // erro (ja renderizado acima) — nunca skeleton, nunca dado antigo.
        <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-10 text-center">
          <p className="text-slate-500 text-sm font-medium">Não foi possível carregar os dados de qualidade.</p>
          <p className="text-slate-400 text-xs mt-1">Use "Tentar novamente" no banner de erro acima.</p>
        </div>
      ) : (
      <>
        {/* Navegacao interna compacta (Task 1) */}
        <nav aria-label="Navegação interna da página" className="flex flex-wrap gap-1 -mx-2.5">
          <a href="#resumo" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">Resumo</a>
          <a href="#qualidade-marca" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">Qualidade por Marca</a>
          {hasMlLoyalty && <a href="#fidelizacao-ml" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">Fidelização ML</a>}
          {hasShQuality && <a href="#qualidade-shopee" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">Qualidade Shopee</a>}
        </nav>

        {/* KPI Cards */}
        <div
          id="resumo"
          className={`scroll-mt-24 grid grid-cols-2 md:grid-cols-4 gap-4 transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}
          aria-busy={isLoadingState}
        >
          {showTiktok && (
            <KpiCard
              label="Entrega TK"
              value={fmtDays(displayKpis?.tiktok_avg_delivery_days ?? null)}
              subvalue="Tempo medio · dados a partir de abr/26"
              accent="bg-violet-500"
            />
          )}
          {/* Gate DQ2: cancelamento e devolucao do TikTok NAO existem na fonte
              servida (API devolve null). Antes, a tela simplesmente omitia as
              duas metricas para o canal e a ausencia nunca era declarada.
              Agora sao cards explicitos de indisponibilidade — valor "N/D"
              (nunca "0%"/"—") e accent neutro (nunca verde de "saudavel"). */}
          {showTiktok && TIKTOK_UNAVAILABLE_QUALITY_METRICS.map((m) => (
            <KpiCard
              key={m.label}
              label={m.label}
              value={m.value}
              subvalue={m.subvalue}
              accent="bg-slate-300"
            />
          ))}
          {showMl && (
            <KpiCard
              label="Cancelamento ML"
              value={fmtRate(displayKpis?.ml_cancel_rate_pct ?? null)}
              subvalue="Taxa de cancelamento"
              accent="bg-cyan-500"
            />
          )}
          {showMl && (
            <KpiCard
              label="Nao Entregue ML"
              value={fmtRate(displayKpis?.ml_not_delivered_rate_pct ?? null)}
              subvalue="Proxy: pagos - entregues"
              accent="bg-amber-500"
            />
          )}
          {showMl && (
            <KpiCard
              label="Entrega ML"
              value={fmtDays(displayKpis?.ml_avg_delivery_days ?? null)}
              subvalue="Tempo medio em dias"
              accent="bg-emerald-500"
            />
          )}
          {showShopee && (
            <KpiCard
              label="Cancelamento Shopee"
              value={fmtRate(displayKpis?.shopee_cancel_rate_pct ?? null)}
              subvalue="Taxa de cancelamento"
              accent="bg-orange-500"
            />
          )}
          {showShopee && (
            <KpiCard
              label="Devolucao Shopee"
              value={fmtRate(displayKpis?.shopee_return_rate_pct ?? null)}
              subvalue="Taxa de devolucao"
              accent="bg-rose-400"
            />
          )}
        </div>

        {/* Tabela por marca */}
        {/* Gate DQ2: limitacao declarada uma vez, junto da tabela que promete
            "cancelamentos e devolucoes" — sem ela, a ausencia das colunas do
            TikTok seria lida como "canal sem cancelamento". */}
        {showTiktok && <DataQualityNote note={TIKTOK_QUALITY_UNAVAILABLE_NOTE} />}

        <div id="qualidade-marca" className="scroll-mt-24 bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
          <div className="px-6 py-4 border-b border-violet-50">
            <h2 className="text-sm font-semibold text-slate-700">Qualidade por Marca</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Cancelamentos, devolucoes e tempos logisticos
              {showTiktok && " · TikTok Shop entra apenas com tempo de entrega (cancelamento/devolução indisponíveis na fonte)"}
            </p>
          </div>

          <TableScrollHint>
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50 text-left">
                  <SortableHeader label="Marca" column="brand" sort={qualitySort.sort} onSort={qualitySort.toggleSort} align="left" />
                  {showTiktok && (
                    <SortableHeader
                      label={<>TK Entrega <span className="ml-1 text-[9px] font-normal text-slate-400 normal-case tracking-normal">abr/26+</span></>}
                      column="tk_delivery" sort={qualitySort.sort} onSort={qualitySort.toggleSort}
                    />
                  )}
                  {showMl && (
                    <>
                      <SortableHeader label="ML Cancel." column="ml_cancel" sort={qualitySort.sort} onSort={qualitySort.toggleSort} />
                      <SortableHeader label="ML N. Entregue" column="ml_not_delivered" sort={qualitySort.sort} onSort={qualitySort.toggleSort} />
                      <SortableHeader label="ML Entrega" column="ml_delivery" sort={qualitySort.sort} onSort={qualitySort.toggleSort} />
                    </>
                  )}
                  {showShopee && (
                    <>
                      <SortableHeader label="SH Cancel." column="sh_cancel" sort={qualitySort.sort} onSort={qualitySort.toggleSort} />
                      <SortableHeader label="SH Devol." column="sh_return" sort={qualitySort.sort} onSort={qualitySort.toggleSort} />
                    </>
                  )}
                </tr>
              </thead>
              <tbody className={`divide-y divide-slate-50 transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                {displayBrands.length === 0 && dataIsFresh && (
                  <tr>
                    <td colSpan={qualityColSpan} className="px-6 py-8 text-center text-slate-400 text-sm">
                      Sem dados para o periodo selecionado.
                    </td>
                  </tr>
                )}
                {qualitySort.sortedRows.map((b) => {
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
          </TableScrollHint>

          <div className="px-6 py-3 border-t border-slate-50 flex items-center gap-6 flex-wrap">
            <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">Taxas:</span>
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
              TK: cancelamento/problema indisponiveis; ML N. Entregue = proxy pagos - entregues
            </span>
          </div>
        </div>

        {/* Fidelizacao ML */}
        {hasMlLoyalty && (
          <div id="fidelizacao-ml" className="scroll-mt-24 bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-violet-50 flex items-start justify-between gap-4 flex-wrap">
              <div>
                <h2 className="text-sm font-semibold text-slate-700">Fidelizacao — Mercado Livre</h2>
                <p className="text-xs text-slate-500 mt-0.5">ML e canal de aquisicao (~90% novos por mes). Recompra &lt;10% e esperado.</p>
              </div>
            </div>
            <TableScrollHint>
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 text-left">
                    <SortableHeader label="Marca" column="brand" sort={mlLoyaltySort.sort} onSort={mlLoyaltySort.toggleSort} align="left" />
                    <SortableHeader label="Compradores" column="buyers" sort={mlLoyaltySort.sort} onSort={mlLoyaltySort.toggleSort} />
                    <SortableHeader label="Novos" column="new" sort={mlLoyaltySort.sort} onSort={mlLoyaltySort.toggleSort} />
                    <SortableHeader label="Recompra%" column="repeat_pct" sort={mlLoyaltySort.sort} onSort={mlLoyaltySort.toggleSort} />
                    <SortableHeader label="GMV/Buyer" column="gmv_per_buyer" sort={mlLoyaltySort.sort} onSort={mlLoyaltySort.toggleSort} />
                    <SortableHeader label="MoM GMV" column="mom" sort={mlLoyaltySort.sort} onSort={mlLoyaltySort.toggleSort} />
                    <SortableHeader label="Frete/GMV" column="shipping_pct" sort={mlLoyaltySort.sort} onSort={mlLoyaltySort.toggleSort} />
                  </tr>
                </thead>
                <tbody className={`divide-y divide-slate-50 transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                  {mlLoyaltySort.sortedRows.map((b) => {
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
            </TableScrollHint>
            <div className="px-6 py-3 border-t border-slate-50 flex items-center gap-6 flex-wrap">
              <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">Recompra:</span>
              <span className="flex items-center gap-1.5 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> &gt;20%</span>
              <span className="flex items-center gap-1.5 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-500 inline-block" /> 10–20%</span>
              <span className="flex items-center gap-1.5 text-xs text-rose-600"><span className="w-2 h-2 rounded-full bg-rose-500 inline-block" /> &lt;10% (normal para aquisicao)</span>
              <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest ml-4">Frete:</span>
              <span className="flex items-center gap-1.5 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> &lt;11%</span>
              <span className="flex items-center gap-1.5 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-500 inline-block" /> 11–14%</span>
              <span className="flex items-center gap-1.5 text-xs text-rose-600"><span className="w-2 h-2 rounded-full bg-rose-500 inline-block" /> &gt;14%</span>
              <span className="ml-auto text-[10px] text-slate-400">Compradores ML: soma diária por dia — não é comprador único do período (mesma pessoa pode ser contada em mais de um dia)</span>
            </div>
          </div>
        )}

        {/* Qualidade Shopee por marca */}
        {hasShQuality && (
          <div id="qualidade-shopee" className="scroll-mt-24 bg-white border border-orange-100 rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-orange-50">
              <h2 className="text-sm font-semibold text-slate-700">Qualidade — Shopee</h2>
              <p className="text-xs text-slate-500 mt-0.5">Cancelamentos e devoluções por marca</p>
            </div>
            <TableScrollHint>
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 text-left">
                    <SortableHeader label="Marca" column="brand" sort={shQualitySort.sort} onSort={shQualitySort.toggleSort} align="left" />
                    <SortableHeader label="Pedidos" column="orders" sort={shQualitySort.sort} onSort={shQualitySort.toggleSort} />
                    <SortableHeader label="Cancelados" column="canceled" sort={shQualitySort.sort} onSort={shQualitySort.toggleSort} />
                    <SortableHeader label="Cancel.%" column="cancel_pct" sort={shQualitySort.sort} onSort={shQualitySort.toggleSort} />
                    <SortableHeader label="Devolvidos" column="returned" sort={shQualitySort.sort} onSort={shQualitySort.toggleSort} />
                    <SortableHeader label="Devol.%" column="return_pct" sort={shQualitySort.sort} onSort={shQualitySort.toggleSort} />
                  </tr>
                </thead>
                <tbody className={`divide-y divide-slate-50 transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                  {shQualitySort.sortedRows.map((b) => (
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
            </TableScrollHint>
            <div className="px-6 py-3 border-t border-slate-50 flex items-center gap-6 flex-wrap">
              <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">Cancel./Devol.:</span>
              <span className="flex items-center gap-1.5 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> &lt;2%</span>
              <span className="flex items-center gap-1.5 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-500 inline-block" /> 2–5%</span>
              <span className="flex items-center gap-1.5 text-xs text-rose-700"><span className="w-2 h-2 rounded-full bg-rose-500 inline-block" /> &gt;5%</span>
            </div>
          </div>
        )}
      </>
      )}
      </div>
  );
}

export default function QualityPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#f8f7ff]" />}>
      <QualityPageInner />
    </Suspense>
  );
}
