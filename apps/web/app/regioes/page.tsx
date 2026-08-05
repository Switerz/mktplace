"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import {
  fetchRegioesSummary, fetchRegioesByUf, fetchRegioesByBrand, fetchRegioesTrend,
  type RegioesSummaryData, type RegiaoUfRow, type RegiaoBrandRow, type RegiaoTrendPoint,
} from "@/lib/api-client";
import { useGlobalFilters } from "@/hooks/useGlobalFilters";
import KpiCard from "@/components/KpiCard";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import BrandFilter from "@/components/BrandFilter";
import DateRangeFilter from "@/components/DateRangeFilter";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { fmtPeriodo, fmtRefreshedAt } from "@/lib/filters/format";
import {
  fmtPctOrNA, coverageLabel, coverageBadgeClass, semCoberturaAviso, fmtShareOfTotalPct,
} from "@/lib/regioes-format";
import { buildRegionalScope, REGIONAL_GMV_LABEL, UF_FILL_LABEL } from "@/lib/regioes-scope";
import { useSortableTable } from "@/lib/use-sortable-table";
import SortableHeader from "@/components/SortableHeader";
import TableScrollHint from "@/components/TableScrollHint";
import {
  buildRegioesRequestKey, buildRegioesFetchScopes, describeRegioesPartialSections, formatRegioesPartialWarning,
} from "@/lib/regioes-request-key";
import { computeRequestStatus } from "@/lib/request-freshness";

const ALL_UFS = [
  "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
  "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO", "XX",
];

// Mapa SVG do Brasil — pesado (27 UFs de path + logica de hover/selecao) e
// so usado nesta pagina; carregado sob demanda (Task 5) para nao entrar no
// bundle inicial de outras rotas. Sem SSR: e puramente interativo (hover/
// clique/teclado), nao ha conteudo indexavel perdido no fallback.
const RegioesBrazilMap = dynamic(() => import("@/components/RegioesBrazilMap"), {
  ssr: false,
  loading: () => (
    <div className="bg-white border border-violet-100 rounded-2xl shadow-sm h-96 animate-pulse" aria-hidden="true" />
  ),
});

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
  // `null` = secao indisponivel (endpoint individual falhou); `[]` = secao
  // resolvida com sucesso e realmente vazia (FINDING 3 — nunca confundir os
  // dois). `summary == null` continua sendo o unico gatilho de erro TOTAL.
  const [byUf, setByUf] = useState<RegiaoUfRow[] | null>(null);
  const [byBrand, setByBrand] = useState<RegiaoBrandRow[] | null>(null);
  const [trend, setTrend] = useState<RegiaoTrendPoint[] | null>(null);
  const [trendGranularity, setTrendGranularity] = useState<"day" | "month">("day");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  const [semCobertura, setSemCobertura] = useState<string[]>([]);
  // Chave da ultima requisicao resolvida com sucesso — comparada com a
  // chave atual para decidir se o estado em memoria reflete de fato os
  // filtros exibidos agora.
  const [resolvedKey, setResolvedKey] = useState<string | null>(null);

  const requestKey = useMemo(
    () => buildRegioesRequestKey({ channels: filters.channels, brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, uf: ufFilter, retryKey }),
    [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, ufFilter, retryKey],
  );

  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setError(null);
    const key = buildRegioesRequestKey({ channels: filters.channels, brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, uf: ufFilter, retryKey });
    const { ufScoped, national } = buildRegioesFetchScopes(
      { brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo }, ufFilter,
    );
    Promise.all([
      fetchRegioesSummary(filters.channels, ufScoped),
      fetchRegioesByUf(filters.channels, ufScoped),
      fetchRegioesByBrand(filters.channels, national),
      fetchRegioesTrend(filters.channels, national),
    ]).then(([sm, uf, br, tr]) => {
      if (ignore) return;
      if (sm == null) {
        setError("Falha ao carregar dados regionais. Verifique a conexão e tente novamente.");
        // A chave precisa ser marcada como resolvida MESMO na falha — senao
        // `computeRequestStatus` nunca sai de "loading" (resolvedKey nunca
        // bate com requestKey) e a falha da requisicao atual nunca vira
        // "error" de fato (fica presa em loading para sempre).
        setResolvedKey(key);
        setLoading(false);
        return;
      }
      setSummary(sm);
      setByUf(uf?.data ?? null);
      setByBrand(br?.data ?? null);
      setTrend(tr?.data ?? null);
      setTrendGranularity(tr?.granularity ?? "day");
      setRefreshedAt(sm.refreshed_at);
      setSemCobertura(sm.channels_sem_cobertura_regional);
      setResolvedKey(key);
      setLoading(false);
    }).catch(() => {
      if (ignore) return;
      setError("Falha ao carregar dados regionais. Verifique a conexão e tente novamente.");
      setResolvedKey(key);
      setLoading(false);
    });
    return () => { ignore = true; };
  }, [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, ufFilter, retryKey]);

  // FINDING 2 (rodada de correcao) — loading/error/fresh SEPARADOS: depois
  // de um erro definitivo, `dataIsFresh` fica `false` para sempre, mas
  // `isLoadingState` tambem precisa ficar `false` (senao skeleton/opacidade/
  // aria-busy continuariam ligados como se ainda estivesse buscando).
  const requestStatus = computeRequestStatus({ loading, error: error != null, resolvedKey, requestKey });
  const dataIsFresh = requestStatus.fresh;
  const isLoadingState = requestStatus.loading;
  const isErrorState = requestStatus.error;

  // Versoes protegidas do estado bruto — nenhum calculo/card/tabela/mapa
  // abaixo deve ler summary/byUf/byBrand/trend/refreshedAt/semCobertura
  // diretamente; sempre via estas constantes. byUf/byBrand/trend viram `[]`
  // tanto quando indisponiveis (FINDING 3) quanto quando nao frescos — a
  // distincao "indisponivel" x "vazio real" e feita separadamente abaixo
  // (unavailableSections), nunca no valor exibido nas tabelas/mapa.
  const displaySummary = dataIsFresh ? summary : null;
  const displayByUf = dataIsFresh ? (byUf ?? []) : [];
  const displayByBrand = dataIsFresh ? (byBrand ?? []) : [];
  const displayTrend = dataIsFresh ? (trend ?? []) : [];
  const displayRefreshedAt = dataIsFresh ? refreshedAt : null;
  const displaySemCobertura = dataIsFresh ? semCobertura : [];

  // Dados parciais (FINDING 3): `summary == null` ja e' erro TOTAL (acima).
  // Aqui, com `summary` resolvido, cada secao individual que veio `null`
  // (nao `[]`) e' listada — nunca durante loading/erro total.
  const unavailableSections = dataIsFresh ? describeRegioesPartialSections({ byUf, byBrand, trend }) : [];
  const partialWarning = formatRegioesPartialWarning(unavailableSections);

  const periodLabel = fmtPeriodo(filters.dateFrom, filters.dateTo);
  const isEmpty = dataIsFresh && displaySummary != null && displaySummary.orders === 0 && byUf != null && byUf.length === 0;
  const aviso = semCoberturaAviso(displaySemCobertura);
  // Gate DQ2: escopo regional declarado (canal x elegibilidade x preenchimento
  // de UF sao dimensoes distintas) — o total desta tela nunca deve ser lido
  // como o GMV do periodo. So' com dado fresco; nunca durante loading/erro.
  const regionalScope = dataIsFresh
    ? buildRegionalScope(filters.channels, displaySemCobertura, displaySummary?.uf_fill_pct ?? null)
    : null;

  const ufColumnTypes = useMemo(() => ({
    uf: "text" as const, gmv: "numeric" as const, orders: "numeric" as const,
    share: "numeric" as const, uf_fill_pct: "numeric" as const,
  }), []);
  const ufGetValue = (row: RegiaoUfRow, column: string): string | number | null => {
    switch (column) {
      case "uf": return row.uf;
      case "gmv": return row.gmv;
      case "orders": return row.orders;
      case "share": return displaySummary ? fmtShareOfTotalPct(row.gmv, displaySummary.gmv) : null;
      case "uf_fill_pct": return row.uf_fill_pct;
      default: return null;
    }
  };
  const ufSort = useSortableTable(displayByUf, ufGetValue, ufColumnTypes);

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
  const brandSort = useSortableTable(displayByBrand, brandGetValue, brandColumnTypes);

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 flex flex-col gap-6">
      {/* Cabecalho */}
      <div className="min-w-0">
        <h2 className="text-xl font-bold text-gray-900">Regiões</h2>
        <p className="text-sm text-slate-500">Distribuição geográfica de GMV e pedidos por UF — cobertura de identificação regional por canal.</p>
      </div>

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
            {isLoadingState && <span className="text-xs text-violet-400 animate-pulse shrink-0">Atualizando...</span>}
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
          {dataIsFresh && displayRefreshedAt && <> · Atualizado em {fmtRefreshedAt(displayRefreshedAt)}</>}
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
          {isLoadingState ? "Carregando dados regionais..." : isErrorState ? "Falha ao carregar dados regionais." : "Dados regionais carregados."}
        </span>

        {/* Gate DQ2: escopo do que esta sendo medido, SEMPRE visivel com dado
            fresco — separa cobertura de canal (dimensao 1) do preenchimento de
            UF (dimensao 3) e impede a leitura de "cobertura integral". */}
        {regionalScope && (
          <div className={`rounded-2xl p-4 border ${regionalScope.status === "not_applicable" ? "bg-slate-50 border-slate-200" : regionalScope.status === "partial_channels" ? "bg-amber-50 border-amber-200" : "bg-violet-50/60 border-violet-100"}`}>
            <p className={`text-xs font-semibold uppercase tracking-wider mb-1 ${regionalScope.status === "not_applicable" ? "text-slate-600" : regionalScope.status === "partial_channels" ? "text-amber-700" : "text-violet-700"}`}>
              {regionalScope.status === "not_applicable" ? "Sem cobertura regional na seleção" : "Escopo regional"}
            </p>
            <p className={`text-sm ${regionalScope.status === "not_applicable" ? "text-slate-700" : regionalScope.status === "partial_channels" ? "text-amber-800" : "text-slate-700"}`}>
              {regionalScope.scopeNote}
            </p>
            {regionalScope.ufFillCaveat && (
              <p className="text-xs text-amber-800 mt-1.5">{regionalScope.ufFillCaveat}</p>
            )}
          </div>
        )}

        {dataIsFresh && aviso && (
          <div className="bg-amber-50 border border-amber-200 rounded-2xl p-4">
            <p className="text-xs font-semibold text-amber-700 uppercase tracking-wider mb-1">Canal sem cobertura regional</p>
            <p className="text-sm text-amber-800">{aviso}</p>
          </div>
        )}

        {dataIsFresh && displaySummary?.coverage_warning && (
          <div className={`rounded-2xl p-4 border ${displaySummary.coverage_level === "low" ? "bg-rose-50 border-rose-200" : "bg-amber-50 border-amber-200"}`}>
            <p className={`text-xs font-semibold uppercase tracking-wider mb-1 ${displaySummary.coverage_level === "low" ? "text-rose-700" : "text-amber-700"}`}>
              {displaySummary.coverage_level === "low" ? "Cobertura de UF baixa" : "Cobertura de UF parcial"}
            </p>
            <p className={`text-sm ${displaySummary.coverage_level === "low" ? "text-rose-800" : "text-amber-800"}`}>
              Apenas {fmtPctOrNA(displaySummary.uf_fill_pct)} dos pedidos elegíveis ({fmtNumber(displaySummary.uf_known_orders)} de {fmtNumber(displaySummary.uf_eligible_orders)}) têm UF identificada no período/filtros selecionados. Os números por UF abaixo refletem só os pedidos com UF conhecida — não é erro, é limitação de dado na fonte.
            </p>
          </div>
        )}

        {isErrorState ? (
          // FINDING 2: erro definitivo mostra so cabecalho/filtros/banner de
          // erro (ja renderizado acima) — nunca skeleton, nunca dado antigo,
          // nunca `aria-busy=true`. Estado de erro dedicado, sem tentar
          // renderizar KPIs/mapa/tabelas.
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-10 text-center">
            <p className="text-slate-500 text-sm font-medium">Não foi possível carregar os dados regionais.</p>
            <p className="text-slate-400 text-xs mt-1">Use "Tentar novamente" no banner de erro acima.</p>
          </div>
        ) : isEmpty ? (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-12 text-center">
            <p className="text-slate-500 text-sm font-medium">Sem dados regionais no período e filtros selecionados.</p>
            <p className="text-slate-400 text-xs mt-1">Tente ampliar o intervalo de datas ou revisar canal/marca/UF.</p>
          </div>
        ) : (
          <>
            {partialWarning && (
              <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-2.5">
                <p className="text-xs text-amber-800">{partialWarning}</p>
              </div>
            )}

            {/* KPI Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4" aria-busy={isLoadingState}>
              <KpiCard
                label={REGIONAL_GMV_LABEL}
                value={displaySummary ? fmtBrl(displaySummary.gmv) : "—"}
                subvalue={regionalScope ? `Escopo: ${regionalScope.channelsInScope.join(" + ") || "nenhum canal com regional"}` : undefined}
                accent="bg-violet-600"
              />
              <KpiCard
                label="Pedidos"
                value={displaySummary ? fmtNumber(displaySummary.orders) : "—"}
                accent="bg-cyan-500"
              />
              <KpiCard
                label="UFs com venda"
                value={displaySummary ? `${displaySummary.ufs_com_venda}/27` : "—"}
                accent="bg-amber-500"
              />
              <KpiCard
                label={UF_FILL_LABEL}
                value={displaySummary ? fmtPctOrNA(displaySummary.uf_fill_pct) : "—"}
                subvalue={displaySummary ? `${coverageLabel(displaySummary.coverage_level)} · dentro dos pedidos elegíveis` : undefined}
                accent={displaySummary?.coverage_level === "ok" ? "bg-emerald-500" : displaySummary?.coverage_level === "partial" ? "bg-amber-500" : displaySummary?.coverage_level === "low" ? "bg-rose-500" : "bg-slate-300"}
              />
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 -mt-2" aria-busy={isLoadingState}>
              <KpiCard
                label="Cobertura Custo Frete"
                value={displaySummary ? fmtPctOrNA(displaySummary.shipping_cost_coverage_pct) : "—"}
                subvalue="Quando aplicável — Shopee não tem este dado na fonte"
                accent="bg-slate-400"
              />
              <KpiCard
                label="Custo Frete Seller"
                value={displaySummary?.seller_shipping_cost != null ? fmtBrl(displaySummary.seller_shipping_cost) : "N/A"}
                accent="bg-slate-400"
              />
            </div>

            {/* Mapa do Brasil por UF — geometria real (SVG), ver RegioesBrazilMap.tsx.
                Carregado via next/dynamic (Task 5) — usa o filtro local de UF. */}
            <RegioesBrazilMap rows={displayByUf} totalGmv={displaySummary?.gmv ?? 0} loading={isLoadingState} />

            {/* Ranking por UF — usa o filtro local de UF */}
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
                  <tbody className={`divide-y divide-slate-50 transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                    {displayByUf.length === 0 && dataIsFresh && (
                      <tr>
                        <td colSpan={5} className="px-6 py-8 text-center text-slate-400 text-sm">
                          {unavailableSections.includes("byUf")
                            ? "Dados indisponíveis para o Ranking por UF neste momento."
                            : "Sem dados por UF para o período e filtros selecionados."}
                        </td>
                      </tr>
                    )}
                    {ufSort.sortedRows.map((row) => {
                      const share = displaySummary ? fmtShareOfTotalPct(row.gmv, displaySummary.gmv) : null;
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

            {/* Tabela por marca x marketplace — escopo NACIONAL dos filtros
                globais: o endpoint by-brand nao aceita filtro de UF (contrato
                de backend nao alterado neste gate). */}
            <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
              <div className="px-6 py-4 border-b border-violet-50">
                <h2 className="text-sm font-semibold text-slate-700">Cobertura por Marca × Canal</h2>
                <p className="text-xs text-slate-500 mt-0.5">GMV, pedidos e cobertura de UF/frete por marca e marketplace</p>
                {ufFilter && (
                  <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-1 mt-2 inline-block">
                    O filtro de UF ({ufFilter}) não se aplica aqui — esta tabela sempre mostra o escopo nacional dos filtros globais (canal/marca/período).
                  </p>
                )}
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
                  <tbody className={`divide-y divide-slate-50 transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                    {displayByBrand.length === 0 && dataIsFresh && (
                      <tr>
                        <td colSpan={7} className="px-6 py-8 text-center text-slate-400 text-sm">
                          {unavailableSections.includes("byBrand")
                            ? "Dados indisponíveis para Cobertura por Marca × Canal neste momento."
                            : "Sem dados por marca/canal para o período e filtros selecionados."}
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
              {displaySemCobertura.length > 0 && (
                <div className="px-6 py-3 border-t border-slate-50">
                  <span className="text-[10px] text-slate-400">
                    {displaySemCobertura.map((c) => (c === "tiktok" ? "TikTok Shop" : c)).join(", ")} não aparece nesta tabela — sem cobertura regional na fonte.
                  </span>
                </div>
              )}
            </div>

            {/* Tendencia — tabela simples (sem mapa/grafico nesta fase).
                Escopo NACIONAL dos filtros globais: o endpoint trend nao
                aceita filtro de UF. */}
            <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
              <div className="px-6 py-4 border-b border-violet-50 flex items-center justify-between gap-4 flex-wrap">
                <div>
                  <h2 className="text-sm font-semibold text-slate-700">Tendência</h2>
                  <p className="text-xs text-slate-500 mt-0.5">GMV, pedidos e cobertura de UF por período — respeita canal e marca</p>
                  {ufFilter && (
                    <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-1 mt-2 inline-block">
                      O filtro de UF ({ufFilter}) não se aplica aqui — esta série sempre mostra o escopo nacional dos filtros globais.
                    </p>
                  )}
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
                  <tbody className={`divide-y divide-slate-50 transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                    {displayTrend.length === 0 && dataIsFresh && (
                      <tr>
                        <td colSpan={4} className="px-6 py-8 text-center text-slate-400 text-sm">
                          {unavailableSections.includes("trend")
                            ? "Dados indisponíveis para a Tendência neste momento."
                            : "Sem série de tendência para o período e filtros selecionados."}
                        </td>
                      </tr>
                    )}
                    {displayTrend.map((p) => (
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
