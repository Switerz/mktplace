"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  fetchOverview, fetchBrands, fetchTrend, fetchExecutiveSummary,
  type OverviewData, type BrandRow, type TrendPoint, type ExecutiveSummaryData,
} from "@/lib/api-client";
import { isMarketplaceSelected } from "@/lib/marketplace-filter";
import { useGlobalFilters } from "@/hooks/useGlobalFilters";
import { mergeFilteredHref } from "@/lib/filters/nav-links";
import { KPI_META, type KpiKind } from "@/lib/kpi-drilldown";
import KpiCard from "@/components/KpiCard";
import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import KpiDrilldownContent from "@/components/KpiDrilldownContent";
import ChannelPerformancePanel from "@/components/ChannelPerformancePanel";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import BrandFilter from "@/components/BrandFilter";
import DateRangeFilter from "@/components/DateRangeFilter";
import TrendChart from "@/components/TrendChart";
import BrandPerformanceTable from "@/components/BrandPerformanceTable";
import ExecutiveSummaryCard from "@/components/ExecutiveSummaryCard";
import LiveStatusBadge from "@/components/LiveStatusBadge";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { fmtPeriodo, fmtRefreshedAt, mockLimitationNote } from "@/lib/filters/format";
import { detectPreset } from "@/lib/filters/presets";

/** Identidade estavel da requisicao atual (Finding 2, rodada de correcao
 * U2) — deriva de tudo que muda o resultado do Promise.all (filtros +
 * retryKey). So e' registrada como "resolvida" quando esse Promise.all
 * termina com sucesso; enquanto a chave resolvida nao bate com a chave
 * atual, os dados em estado (overview/brands/trend) sao considerados
 * potencialmente antigos e nao devem ser exibidos como se fossem do
 * periodo/filtro atual — inclusive durante o unico frame de render em que
 * os filtros ja mudaram mas `loading` ainda nao foi setado (o `useEffect`
 * so roda depois do commit). */
function buildRequestKey(
  channels: readonly string[], brands: readonly string[], dateFrom: string, dateTo: string, compare: boolean, retryKey: number,
): string {
  return `${channels.join(",")}|${brands.join(",")}|${dateFrom}|${dateTo}|${compare}|${retryKey}`;
}

function fmtSplit(tkGmv: number | null, mlGmv: number | null, shGmv: number | null): string | undefined {
  const parts: string[] = [];
  if (tkGmv) parts.push(`TK ${fmtBrl(tkGmv)}`);
  if (mlGmv) parts.push(`ML ${fmtBrl(mlGmv)}`);
  if (shGmv) parts.push(`SH ${fmtBrl(shGmv)}`);
  return parts.length ? parts.join(" · ") : undefined;
}

/** Valor formatado exibido no topo do KpiCard/dialogo — mesma logica que ja
 * decide o rotulo/valor de ROAS na grade de KPIs (canal unico, ambos, ou
 * nenhum disponivel). */
function roasCardValue(overview: OverviewData | null, showMl: boolean, showSh: boolean): { label: string; value: string; subvalue?: string } {
  if (!overview || (!showMl && !showSh)) return { label: "ROAS", value: "N/D", subvalue: "Não disponível no TikTok Shop" };
  const mlRoas = overview.ml_roas;
  const shRoas = overview.shopee_roas;
  if (showSh && !showMl) return { label: "ROAS Shopee", value: shRoas != null ? `${shRoas.toFixed(1)}x` : "—" };
  if (showMl && !showSh) return { label: "ROAS ML", value: mlRoas != null ? `${mlRoas.toFixed(1)}x` : "—" };
  const parts: string[] = [];
  if (mlRoas != null) parts.push(`ML ${mlRoas.toFixed(1)}x`);
  if (shRoas != null) parts.push(`SH ${shRoas.toFixed(1)}x`);
  return { label: "ROAS Ads", value: parts.length ? parts[0].split(" ")[1] : "—", subvalue: parts.length > 1 ? parts.join(" · ") : undefined };
}

function DashboardInner() {
  const [filters, setFilters] = useGlobalFilters({ defaultPreset: "mes_anterior", defaultCompare: true });
  const searchParams = useSearchParams();
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [brands, setBrands] = useState<BrandRow[]>([]);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [trendGranularity, setTrendGranularity] = useState<"day" | "month">("day");
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  const [execSummary, setExecSummary] = useState<ExecutiveSummaryData | null>(null);
  const [execLoading, setExecLoading] = useState(true);
  const [openKpi, setOpenKpi] = useState<KpiKind | null>(null);
  // Chave da ultima requisicao que terminou com SUCESSO (Finding 2) — null
  // ate o primeiro fetch resolver. Comparada com `requestKey` (derivado dos
  // filtros/retryKey atuais) para decidir se overview/brands/trend em
  // estado sao realmente do periodo/filtro exibido agora.
  const [resolvedKey, setResolvedKey] = useState<string | null>(null);

  const requestKey = useMemo(
    () => buildRequestKey(filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey),
    [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey],
  );

  useEffect(() => {
    // Ignora a resposta se os filtros mudarem antes dela chegar — evita que
    // uma resposta antiga (ex: intervalo grande, mais lenta) sobrescreva o
    // estado de um filtro mais recente aplicado em seguida.
    let ignore = false;
    setLoading(true);
    setError(null);
    // Uma nova requisicao de filtros comeca -> fecha qualquer drill-down
    // aberto, que so faz sentido para o dado que estava fresco ate agora
    // (Finding 2).
    setOpenKpi(null);
    const key = buildRequestKey(filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey);
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
      setResolvedKey(key);
      setLoading(false);
    }).catch(() => {
      if (ignore) return;
      setError("Falha ao carregar dados. Verifique a conexão e tente novamente.");
      setLoading(false);
      // Nao atualiza resolvedKey — os dados anteriores (se houver) continuam
      // com uma chave diferente da atual e ficam marcados como obsoletos.
    });
    return () => { ignore = true; };
  }, [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey]);

  useEffect(() => {
    // Efeito independente do Promise.all acima: uma falha aqui nunca deve
    // acionar o banner de erro nem bloquear cards/tabela/trend — o resumo
    // executivo e um bloco de sintese, nao um dado essencial da Gerencial.
    let ignore = false;
    setExecLoading(true);
    const opts = { brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, compare: filters.compare };
    fetchExecutiveSummary(filters.channels, opts)
      .then((res) => {
        if (ignore) return;
        setExecSummary(res.data);
        setExecLoading(false);
      })
      .catch(() => {
        if (ignore) return;
        setExecSummary(null);
        setExecLoading(false);
      });
    return () => { ignore = true; };
  }, [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey]);

  const periodLabel = fmtPeriodo(filters.dateFrom, filters.dateTo);

  // Dados so sao considerados "frescos" (utilizaveis para exibir/abrir
  // drill-down) quando: nao esta carregando, nao ha erro, a chave resolvida
  // bate com a chave da requisicao atual, e overview existe (Finding 2).
  // Isso fecha inclusive o frame de render em que os filtros ja mudaram mas
  // o efeito ainda nao rodou (loading ainda nao virou true).
  const dataIsFresh = !loading && !error && resolvedKey === requestKey && overview != null;
  const displayOverview = dataIsFresh ? overview : null;
  const displayBrands = dataIsFresh ? brands : [];
  const displayTrend = dataIsFresh ? trend : [];

  const isEmpty = displayOverview != null && displayOverview.gmv === 0 && displayBrands.length === 0;

  // Combina os filtros globais atuais com o href de destino (backend ou
  // fixo da tela), preservando o contrato de precedencia do Gate U2 Task 7.
  const buildHref = useMemo(() => (href: string) => mergeFilteredHref(href, searchParams), [searchParams]);

  const showMlRoasChannel = isMarketplaceSelected(filters.channels, "ml");
  const showShRoasChannel = isMarketplaceSelected(filters.channels, "shopee");
  const roasCard = roasCardValue(displayOverview, showMlRoasChannel, showShRoasChannel);

  const kpiValues: Record<KpiKind, string> = {
    gmv: displayOverview ? fmtBrl(displayOverview.gmv) : "—",
    orders: displayOverview ? fmtNumber(displayOverview.orders) : "—",
    avg_ticket: displayOverview ? fmtBrl(displayOverview.avg_ticket) : "—",
    roas: roasCard.value,
  };

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 flex flex-col gap-6">
      {/* Cabecalho da Gerencial */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-bold text-gray-900">Visão Gerencial</h1>
          <p className="text-sm text-slate-500">Como estamos, onde investigar e para onde ir a seguir.</p>
        </div>
        {/* isLive/refreshedAt sao metadados da ULTIMA requisicao resolvida
            com sucesso — sem o guard de dataIsFresh, uma troca de filtro ou
            um erro continuariam afirmando "API conectada"/mostrando o
            timestamp anterior ao lado do periodo NOVO ja exibido acima
            (ajuste de coerencia, rodada de correcao U2). */}
        {dataIsFresh ? (
          <LiveStatusBadge live={isLive} />
        ) : loading ? (
          <span className="text-xs text-slate-500 bg-slate-100 border border-slate-200 rounded-lg px-3 py-1.5 font-medium">
            Atualizando dados...
          </span>
        ) : null}
      </div>
      <p className="text-xs text-slate-400 -mt-3">
        Período: {periodLabel}
        {dataIsFresh && refreshedAt && <> · Atualizado em {fmtRefreshedAt(refreshedAt)}</>}
      </p>

      {/* Barra de filtros */}
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

      {/* mockLimitationNote depende de isLive (mesma ressalva do badge
          acima) — so calcula/renderiza com dado fresco, nunca durante
          loading/erro com o isLive da requisicao anterior. */}
      {dataIsFresh && (() => {
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

      {/* Resumo executivo — sintese de Saude/O que mudou/Atencoes, ponto de
          entrada do fluxo resumo->desvio->clique->explicacao. Fetch e falha
          independentes dos cards/tabela/trend abaixo (ver useEffect proprio). */}
      <ExecutiveSummaryCard data={execSummary} loading={execLoading} buildHref={buildHref} />

      {isEmpty ? (
        <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-12 text-center">
          <p className="text-slate-500 text-sm font-medium">Sem dados no período e filtros selecionados.</p>
          <p className="text-slate-400 text-xs mt-1">Tente ampliar o intervalo de datas ou revisar canal/marca.</p>
        </div>
      ) : (
        <>
          {/* Grade de KPIs — cada card abre o mesmo dialogo de drill-down
              agregado (Gate U2, Task 3/4), com explicacao e decomposicao. */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4" aria-busy={loading}>
            <KpiCard
              label="GMV Total"
              value={kpiValues.gmv}
              subvalue={displayOverview ? fmtSplit(displayOverview.tiktok_gmv, displayOverview.ml_gmv, displayOverview.shopee_gmv) : undefined}
              mom={displayOverview?.gmv_mom_pct ?? null}
              accent="bg-violet-600"
              onOpenDetail={displayOverview ? () => setOpenKpi("gmv") : undefined}
            />
            <KpiCard
              label="Pedidos"
              value={kpiValues.orders}
              subvalue={displayOverview && (displayOverview.tiktok_customers != null || displayOverview.ml_unique_buyers != null || displayOverview.shopee_unique_buyers != null)
                // Soma diaria, nao comprador unico do periodo — o mesmo
                // comprador pode ser contado em mais de um dia (ver
                // docs/kpi_dictionary.md, nota "Compradores — soma diaria").
                ? `${fmtNumber((displayOverview.tiktok_customers ?? 0) + (displayOverview.ml_unique_buyers ?? 0) + (displayOverview.shopee_unique_buyers ?? 0))} compradores (soma diária, não único no período)`
                : undefined}
              accent="bg-cyan-500"
              onOpenDetail={displayOverview ? () => setOpenKpi("orders") : undefined}
            />
            <KpiCard
              label="Ticket Médio"
              value={kpiValues.avg_ticket}
              accent="bg-amber-500"
              onOpenDetail={displayOverview ? () => setOpenKpi("avg_ticket") : undefined}
            />
            <KpiCard
              label={roasCard.label}
              value={roasCard.value}
              subvalue={roasCard.subvalue ?? (displayOverview?.ad_spend != null ? `Ad Spend: ${fmtBrl(displayOverview.ad_spend)}` : undefined)}
              accent={roasCard.value !== "—" && roasCard.value !== "N/D" ? "bg-emerald-500" : "bg-slate-300"}
              onOpenDetail={displayOverview ? () => setOpenKpi("roas") : undefined}
            />
          </div>

          {/* Area analitica principal — tendencia em destaque + composicao
              por canal, lado a lado no desktop. */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
            <div className="lg:col-span-2">
              <TrendChart data={displayTrend} granularity={trendGranularity} loading={loading} />
            </div>
            <ChannelPerformancePanel overview={displayOverview} channels={filters.channels} loading={loading} buildHref={buildHref} />
          </div>

          {/* Tabela por marca */}
          <BrandPerformanceTable
            brands={displayBrands}
            filter={filters.channels}
            period={filters.dateTo.slice(0, 7)}
            loading={loading}
            periodLabel={periodLabel}
          />

          {/* Alerta operacional — apenas dados reais acionaveis (especifico de ML) */}
          {dataIsFresh && isMarketplaceSelected(filters.channels, "ml") && (() => {
            const lescent = displayBrands.find((b) => b.brand === "lescent");
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

      <KpiDrilldownDialog
        open={openKpi != null}
        onClose={() => setOpenKpi(null)}
        title={openKpi ? KPI_META[openKpi].label : ""}
      >
        {openKpi && displayOverview && (
          <KpiDrilldownContent
            kind={openKpi}
            value={kpiValues[openKpi]}
            periodLabel={periodLabel}
            refreshedAt={refreshedAt}
            overview={displayOverview}
            brands={displayBrands}
            channels={filters.channels}
            buildHref={buildHref}
          />
        )}
      </KpiDrilldownDialog>
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
