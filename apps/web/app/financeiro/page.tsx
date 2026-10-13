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
import GlobalFilterControls from "@/components/layout/GlobalFilterControls";
import LiveStatusBadge from "@/components/LiveStatusBadge";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import { fmtBrl } from "@/lib/formatters";
import { fmtPeriodo, fmtRefreshedAt, mockLimitationNote } from "@/lib/filters/format";
import { detectPreset } from "@/lib/filters/presets";
import { useSortableTable } from "@/lib/use-sortable-table";
import SortHeader from "@/components/ui/SortHeader";
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
  if (v == null) return "text-ink-faint";
  if (v >= 10) return "text-ok-ink";
  if (v >= 5) return "text-warn-ink";
  return "text-crit-ink";
}

function acosColor(v: number | null): string {
  if (v == null) return "text-ink-faint";
  if (v <= 15) return "text-ok-ink";
  if (v <= 25) return "text-warn-ink";
  return "text-crit-ink";
}

function totalCostColor(v: number | null): string {
  if (v == null) return "text-ink-faint";
  if (v <= 18) return "text-ok-ink";
  if (v <= 25) return "text-warn-ink";
  return "text-crit-ink";
}

function feePctColor(v: number | null): string {
  if (v == null) return "text-ink-faint";
  if (v < 20) return "text-ink-muted";
  if (v < 30) return "text-warn-ink";
  return "text-crit-ink";
}

function CostBar({ adPct, freteP }: { adPct: number | null; freteP: number | null }) {
  if (adPct == null && freteP == null) return null;
  const ad = Math.max(0, adPct ?? 0);
  const frete = Math.max(0, freteP ?? 0);
  const total = Math.min(100, ad + frete);
  const rest = Math.max(0, 100 - total);
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full overflow-hidden bg-raised flex">
        {/* Composicao, nao estado: cor de serie, nunca ok/warn/crit. */}
        <div className="h-full bg-chart-2" style={{ width: `${ad}%` }} />
        <div className="h-full bg-chart-4" style={{ width: `${frete}%` }} />
        <div className="h-full bg-raised" style={{ width: `${rest}%` }} />
      </div>
      <span className="text-xs tabular-nums text-ink-faint w-11 text-right shrink-0">
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
            <span className="text-xs text-ink-faint bg-raised border border-line rounded-lg px-3 py-1.5 font-medium">
              Atualizando dados...
            </span>
          ) : null
        }
        filters={<GlobalFilterControls filters={filters} onChange={setFilters} hideCompare loading={isLoadingState} />}
      />

        {dataIsFresh && (() => {
          const isCustomPeriod = detectPreset(filters.dateFrom, filters.dateTo) !== "mes_anterior";
          const note = mockLimitationNote(displayIsLive, filters.brands, isCustomPeriod);
          return note && (
            <div className="bg-warn-soft border border-warn/35 rounded-2xl p-3">
              <p className="text-xs text-warn-ink">{note}</p>
            </div>
          );
        })()}

        {error && (
          <div className="bg-crit-soft border border-crit/35 rounded-2xl p-4 flex items-center justify-between gap-4">
            <p className="text-sm text-crit-ink">{error}</p>
            <button
              onClick={() => { setError(null); setRetryKey((k) => k + 1); }}
              className="text-xs font-semibold text-crit-ink border border-crit/35 rounded-lg px-3 py-1.5 hover:bg-crit-soft transition-colors shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-crit/30"
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
        <div className="bg-surface border border-line rounded-2xl shadow-sm px-6 py-10 text-center">
          <p className="text-ink-faint text-sm font-medium">Não foi possível carregar os dados financeiros.</p>
          <p className="text-ink-faint text-xs mt-1">Use "Tentar novamente" no banner de erro acima.</p>
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
              accent="bg-canal-tiktok"
            />
          )}
          {showTiktok && (
            <KpiCard
              label="Taxas e encargos / GMV"
              value={displayKpis?.tiktok_avg_fee_pct != null ? fmtPct(displayKpis.tiktok_avg_fee_pct) : "—"}
              subvalue={displayKpis?.tiktok_fees != null ? fmtBrl(displayKpis.tiktok_fees) + " total" : undefined}
              accent="bg-canal-tiktok"
            />
          )}
          {showMl && (
            <KpiCard
              label="ROAS ML"
              value={fmtRoas(displayKpis?.ml_roas ?? null)}
              subvalue={displayKpis?.ml_acos_pct != null ? `ACOS ${fmtPct(displayKpis.ml_acos_pct)}` : undefined}
              accent="bg-canal-ml"
            />
          )}
          {showMl && (
            <KpiCard
              label="Ads + Frete / GMV"
              value={displayKpis?.ml_total_cost_pct != null ? fmtPct(displayKpis.ml_total_cost_pct) : "—"}
              subvalue="Nao inclui comissao do Mercado Livre"
              accent="bg-canal-ml"
            />
          )}
          {showShopee && (
            <KpiCard
              label="Taxas e encargos Shopee"
              value={displayKpis?.shopee_avg_fee_pct != null ? fmtPct(displayKpis.shopee_avg_fee_pct) : "—"}
              subvalue={displayKpis?.shopee_fees != null ? fmtBrl(displayKpis.shopee_fees) + " total" : undefined}
              accent="bg-canal-shopee"
            />
          )}
          {showShopee && (
            <KpiCard
              label="ROAS Shopee"
              value={displayKpis?.shopee_roas != null ? fmtRoas(displayKpis.shopee_roas) : "—"}
              subvalue={displayKpis?.shopee_ad_spend != null ? `Ad spend ${fmtBrl(displayKpis.shopee_ad_spend)}` : undefined}
              accent="bg-canal-shopee"
            />
          )}
        </div>

        {/* Navegacao interna compacta (Task 4) */}
        <nav aria-label="Navegação interna da página" className="flex flex-wrap gap-1 -mx-2.5">
          {showTiktok && <a href="#tiktok" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-accent hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">TikTok Shop</a>}
          {showMl && <a href="#mercado-livre" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-accent hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">Mercado Livre</a>}
          {showShopee && <a href="#shopee" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-accent hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">Shopee</a>}
        </nav>

        {/* Tabela: Repasses TikTok */}
        {showTiktok && (
          <div id="tiktok" className="scroll-mt-24 bg-surface border border-line rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-line">
              <h2 className="text-sm font-semibold text-ink-muted">Repasses TikTok</h2>
              <p className="text-xs text-ink-faint mt-0.5">GMV bruto, taxas e encargos e repasse recebido por marca</p>
            </div>
            <TableScrollHint>
              <table className="w-full text-sm" aria-label="Repasses TikTok por marca">
                <thead>
                  <tr className="bg-raised text-left">
                    <SortHeader rotulo="Marca" coluna="brand" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} alinhamento="left" />
                    <SortHeader rotulo="GMV" coluna="gmv" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                    <SortHeader rotulo="Taxas (R$)" coluna="fees" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                    <SortHeader rotulo="Taxas e encargos %" coluna="fee_pct" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                    <SortHeader rotulo="Repasse %" coluna="settlement_pct" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                    <SortHeader rotulo="Repasse Recebido" coluna="settlement" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                  </tr>
                </thead>
                <tbody className={`divide-y divide-line transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                  {tkBrands.length === 0 && dataIsFresh && (
                    <tr>
                      <td colSpan={6} className="px-6 py-8 text-center text-ink-faint text-sm">
                        Sem dados para o periodo selecionado.
                      </td>
                    </tr>
                  )}
                  {tkSort.sortedRows.map((b) => (
                    <tr key={b.brand} className="hover:bg-raised/70 transition-colors">
                      <td className="px-6 py-4 font-semibold text-ink whitespace-nowrap">
                        <Link
                          href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=tiktok`)}
                          className="hover:text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                        >
                          {b.label}
                        </Link>
                      </td>
                      <td className="px-4 py-4 text-right tabular-nums text-ink-muted font-medium">{fmtBrl(b.tiktok_gmv!)}</td>
                      <td className="px-4 py-4 text-right tabular-nums text-ink-muted">{fmtBrl(b.tiktok_fees!)}</td>
                      <td className={`px-4 py-4 text-right tabular-nums font-semibold ${feePctColor(b.tiktok_avg_fee_pct)}`}>
                        {fmtPct(b.tiktok_avg_fee_pct)}
                      </td>
                      <td className="px-4 py-4 text-right tabular-nums text-ink-muted">
                        {fmtPct(b.tiktok_avg_settlement_pct)}
                      </td>
                      <td className="px-4 py-4 text-right tabular-nums text-ink font-bold">{fmtBrl(b.tiktok_settlement!)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScrollHint>
            <div className="px-6 py-3 border-t border-line flex items-center gap-5 flex-wrap">
              <span className="text-xs font-semibold text-ink-faint uppercase tracking-wide">Taxas e encargos %:</span>
              <span className="flex items-center gap-1.5 text-xs text-ink-muted">
                <span className="w-2 h-2 rounded-full bg-ink-faint inline-block" /> abaixo de 20%
              </span>
              <span className="flex items-center gap-1.5 text-xs text-warn-ink">
                <span className="w-2 h-2 rounded-full bg-warn inline-block" /> 20–30%
              </span>
              <span className="flex items-center gap-1.5 text-xs text-crit-ink">
                <span className="w-2 h-2 rounded-full bg-crit inline-block" /> acima de 30%
              </span>
              <span className="ml-auto text-xs text-ink-faint">
                Taxas e encargos / GMV · Repasse % = Repasse Recebido / GMV · repasses e GMV podem ter competencias diferentes (o repasse pode incluir pedidos de outros meses)
              </span>
            </div>
          </div>
        )}

        {/* Tabela: Publicidade e Custos ML */}
        {showMl && (
          <div id="mercado-livre" className="scroll-mt-24 bg-surface border border-line rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-line">
              <h2 className="text-sm font-semibold text-ink-muted">Publicidade e Custos ML</h2>
              <p className="text-xs text-ink-faint mt-0.5">Ad Spend, receita atribuida, frete e custo total como % do GMV</p>
            </div>
            <TableScrollHint>
              <table className="w-full text-sm" aria-label="Publicidade e custos ML por marca">
                <thead>
                  <tr className="bg-raised text-left">
                    <SortHeader rotulo="Marca" coluna="brand" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} alinhamento="left" />
                    <SortHeader rotulo="GMV ML" coluna="gmv" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="Ad Spend" coluna="ad_spend" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="Receita Ads" coluna="ad_revenue" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="ROAS" coluna="roas" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="ACOS" coluna="acos" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="Frete" coluna="frete" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="Ads + Frete / GMV" coluna="cost_pct" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} alinhamento="left" />
                  </tr>
                </thead>
                <tbody className={`divide-y divide-line transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                  {mlBrands.length === 0 && dataIsFresh && (
                    <tr>
                      <td colSpan={8} className="px-6 py-8 text-center text-ink-faint text-sm">
                        Sem dados de anuncios ML para o periodo selecionado.
                      </td>
                    </tr>
                  )}
                  {mlSort.sortedRows.map((b) => {
                    const adPct = b.ml_ad_spend != null && b.ml_gmv != null && b.ml_gmv > 0
                      ? b.ml_ad_spend / b.ml_gmv * 100
                      : null;
                    return (
                      <tr key={b.brand} className="hover:bg-raised/70 transition-colors">
                        <td className="px-6 py-4 font-semibold text-ink whitespace-nowrap">
                          <Link
                            href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=ml`)}
                            className="hover:text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                          >
                            {b.label}
                          </Link>
                        </td>
                        <td className="px-4 py-4 text-right tabular-nums text-ink-muted font-medium">{fmtBrl(b.ml_gmv!)}</td>
                        <td className="px-4 py-4 text-right tabular-nums text-ink-muted">{fmtBrl(b.ml_ad_spend!)}</td>
                        <td className="px-4 py-4 text-right tabular-nums text-ink-muted font-medium">
                          {b.ml_ad_revenue != null ? fmtBrl(b.ml_ad_revenue) : "—"}
                        </td>
                        <td className={`px-4 py-4 text-right tabular-nums font-semibold ${roasColor(b.ml_roas)}`}>
                          {fmtRoas(b.ml_roas)}
                        </td>
                        <td className={`px-4 py-4 text-right tabular-nums font-semibold ${acosColor(b.ml_acos_pct)}`}>
                          {fmtPct(b.ml_acos_pct)}
                        </td>
                        <td className="px-4 py-4 text-right tabular-nums text-ink-muted">
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

            <div className="px-6 py-3 border-t border-line flex items-center gap-5 flex-wrap">
              <span className="text-xs font-semibold text-ink-faint uppercase tracking-wide">ROAS:</span>
              <span className="flex items-center gap-1.5 text-xs text-ok-ink">
                <span className="w-2 h-2 rounded-full bg-ok inline-block" /> acima de 10x
              </span>
              <span className="flex items-center gap-1.5 text-xs text-warn-ink">
                <span className="w-2 h-2 rounded-full bg-warn inline-block" /> 5x–10x
              </span>
              <span className="flex items-center gap-1.5 text-xs text-crit-ink">
                <span className="w-2 h-2 rounded-full bg-crit inline-block" /> abaixo de 5x
              </span>
              <span className="text-ink-faint select-none mx-1">|</span>
              <span className="text-xs font-semibold text-ink-faint uppercase tracking-wide">Ads + Frete / GMV:</span>
              <span className="flex items-center gap-1.5 text-xs text-ink-muted">
                <span className="inline-block w-2 h-2 rounded-sm bg-chart-2" /> Ads
              </span>
              <span className="flex items-center gap-1.5 text-xs text-ink-muted">
                <span className="inline-block w-2 h-2 rounded-sm bg-chart-4" /> Frete
              </span>
              <span className="ml-auto text-xs text-ink-faint">Ads + Frete / GMV · nao inclui comissao do Mercado Livre (sem granularidade mensal/diaria na fonte atual) · Receita Ads = GMV atribuido a anuncios</span>
            </div>
          </div>
        )}

        {/* Tabela: Taxas e Custos Shopee */}
        {showShopee && (
          <div id="shopee" className="scroll-mt-24 bg-surface border border-line rounded-2xl shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-line">
              <h2 className="text-sm font-semibold text-ink-muted">Taxas e Custos Shopee</h2>
              <p className="text-xs text-ink-faint mt-0.5">GMV, taxas e encargos, total global dos pedidos, anuncios e frete por marca</p>
            </div>
            <TableScrollHint>
              <table className="w-full text-sm" aria-label="Taxas e custos Shopee por marca">
                <thead>
                  <tr className="bg-raised text-left">
                    <SortHeader rotulo="Marca" coluna="brand" sort={shSort.sort} aoOrdenar={shSort.toggleSort} alinhamento="left" />
                    <SortHeader rotulo="GMV" coluna="gmv" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                    <SortHeader rotulo="Taxas (R$)" coluna="fees" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                    <SortHeader rotulo="Taxas e encargos %" coluna="fee_pct" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                    <SortHeader rotulo="Total Global (pedidos)" coluna="settlement" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                    <SortHeader rotulo="Ad Spend" coluna="ad_spend" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                    <SortHeader rotulo="ROAS" coluna="roas" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                    <SortHeader rotulo="Frete" coluna="frete" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                  </tr>
                </thead>
                <tbody className={`divide-y divide-line transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                  {shBrands.length === 0 && dataIsFresh && (
                    <tr>
                      <td colSpan={8} className="px-6 py-8 text-center text-ink-faint text-sm">
                        Sem dados Shopee para o periodo selecionado.
                      </td>
                    </tr>
                  )}
                  {shSort.sortedRows.map((b) => (
                    <tr key={b.brand} className="hover:bg-raised/70 transition-colors">
                      <td className="px-6 py-4 font-semibold text-ink whitespace-nowrap">
                        <Link
                          href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=shopee`)}
                          className="hover:text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                        >
                          {b.label}
                        </Link>
                      </td>
                      <td className="px-4 py-4 text-right tabular-nums text-ink-muted font-medium">{fmtBrl(b.shopee_gmv!)}</td>
                      <td className="px-4 py-4 text-right tabular-nums text-ink-muted">
                        {b.shopee_fees != null ? fmtBrl(b.shopee_fees) : "—"}
                      </td>
                      <td className={`px-4 py-4 text-right tabular-nums font-semibold ${feePctColor(b.shopee_avg_fee_pct ?? null)}`}>
                        {fmtPct(b.shopee_avg_fee_pct ?? null)}
                      </td>
                      <td className="px-4 py-4 text-right tabular-nums text-ink font-bold">
                        {b.shopee_settlement != null ? fmtBrl(b.shopee_settlement) : "—"}
                      </td>
                      <td className="px-4 py-4 text-right tabular-nums text-ink-muted">
                        {b.shopee_ad_spend != null ? fmtBrl(b.shopee_ad_spend) : "—"}
                      </td>
                      <td className={`px-4 py-4 text-right tabular-nums font-semibold ${roasColor(b.shopee_roas ?? null)}`}>
                        {fmtRoas(b.shopee_roas ?? null)}
                      </td>
                      <td className="px-4 py-4 text-right tabular-nums text-ink-muted">
                        {b.shopee_shipping_cost != null ? fmtBrl(b.shopee_shipping_cost) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScrollHint>
            <div className="px-6 py-3 border-t border-line flex items-center gap-5 flex-wrap">
              <span className="text-xs font-semibold text-ink-faint uppercase tracking-wide">Taxas e encargos %:</span>
              <span className="flex items-center gap-1.5 text-xs text-ink-muted">
                <span className="w-2 h-2 rounded-full bg-ink-faint inline-block" /> abaixo de 20%
              </span>
              <span className="flex items-center gap-1.5 text-xs text-warn-ink">
                <span className="w-2 h-2 rounded-full bg-warn inline-block" /> 20–30%
              </span>
              <span className="flex items-center gap-1.5 text-xs text-crit-ink">
                <span className="w-2 h-2 rounded-full bg-crit inline-block" /> acima de 30%
              </span>
              <span className="ml-auto text-xs text-ink-faint">
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
    <Suspense fallback={<div className="min-h-screen bg-canvas" />}>
      <FinanceiroPageInner />
    </Suspense>
  );
}
