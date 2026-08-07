"use client";

/**
 * Gerencial V2 (Gate V2-1) — pagina flagship da Torre.
 *
 * Responsabilidade DESTE arquivo, e so' dela: filtros, identidade das
 * requisicoes (delegada a `useGerencialSources`), derivacao via helpers puros,
 * composicao dos oito blocos e o estado do unico dialogo aberto. Nenhuma regra
 * de negocio vive aqui — ela mora em `src/lib/gerencial/*`, testavel sem React.
 *
 * Narrativa vertical, uma pergunta por dobra:
 *   1. O que aconteceu?      -> cabecalho + faixa de confianca + 5 KPIs
 *   2. Como evoluiu?         -> Evolucao (dominante) | Pulso+Canais
 *   3. Onde aconteceu?       -> Saude do volume por canal + Matriz Marca x Canal
 *   4. Por que?              -> Movimentos + Concentracao por marca
 *   5. Para onde ir?         -> Fila de atencao
 *
 * INDEPENDENCIA DAS FONTES (Task A da rodada consolidada): a estrutura da pagina
 * fica SEMPRE montada. Nao existe gate global de erro nem de vazio — a versao
 * anterior envolvia quase todos os blocos num ramo de `overviewStatus.error ||
 * isEmpty`, e assim uma falha so' do `/overview` apagava evolucao, Pulso, matriz,
 * movimentos e fila mesmo com as fontes deles frescas. Agora cada bloco responde
 * exclusivamente as fontes de que depende.
 *
 * A lacuna diagnosticada no V2-0 morre por construcao: a grade da segunda dobra
 * nao usa `items-start`, cada card E' o item da grade, e nao existe `row-span`.
 */
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useGlobalFilters } from "@/hooks/useGlobalFilters";
import { useGerencialSources } from "@/hooks/useGerencialSources";
import { mergeFilteredHref } from "@/lib/filters/nav-links";
import { fmtPeriodo } from "@/lib/filters/format";
import type { Marketplace } from "@/lib/mock-data";
import { buildPulse } from "@/lib/executive-pulse";
import { findChannelMedian } from "@/lib/canais-channel-metrics";
import { CHANNEL_LABEL, buildKpiBand } from "@/lib/gerencial/kpi-band";
import { buildVolumeHealth } from "@/lib/gerencial/volume-health";
import {
  buildAttentionQueue,
  buildConfidenceStrip,
  type ChannelSeriesState,
  type CommercialAttentionItem,
  type DataConfidenceItem,
} from "@/lib/gerencial/attention";
import {
  buildBrandChannelMatrix,
  buildConcentration,
  buildMovements,
  type ConcentrationEntry,
  type Movement,
} from "@/lib/gerencial/brand-matrix";
import { mergeChannelSeries, reconcileSeriesTotal, type MergedBucket } from "@/lib/gerencial/trend-series";
import type { TrendMetric } from "@/lib/gerencial/request-key";
import { KPI_META, type KpiKind } from "@/lib/kpi-drilldown";

import MarketplaceFilter from "@/components/MarketplaceFilter";
import BrandFilter from "@/components/BrandFilter";
import DateRangeFilter from "@/components/DateRangeFilter";
import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import KpiDrilldownContent from "@/components/KpiDrilldownContent";
import InsightDrilldownContent, { type PulseView } from "@/components/InsightDrilldownContent";
import ChannelComparisonDialogContent from "@/components/ChannelComparisonDialogContent";
import GerencialHeader from "@/components/gerencial/GerencialHeader";
import ConfidenceStrip from "@/components/gerencial/ConfidenceStrip";
import KpiBand from "@/components/gerencial/KpiBand";
import EvolutionCard from "@/components/gerencial/EvolutionCard";
import PulseChannelsColumn from "@/components/gerencial/PulseChannelsColumn";
import VolumeHealthCard from "@/components/gerencial/VolumeHealthCard";
import BrandChannelMatrix, { cellKey, type SignalsState } from "@/components/gerencial/BrandChannelMatrix";
import MovementsPanels from "@/components/gerencial/MovementsPanels";
import AttentionQueue from "@/components/gerencial/AttentionQueue";
import {
  ChannelSeriesDrilldownContent,
  CommercialAttentionDrilldownContent,
  ConcentrationDrilldownContent,
  ConfidenceDrilldownContent,
  DataWarningDrilldownContent,
  MatrixCellUnavailableContent,
  MatrixChannelDrilldownContent,
  MovementDrilldownContent,
  TrendBucketDrilldownContent,
  VolumeHealthDrilldownContent,
} from "@/components/gerencial/GerencialDrilldowns";

/** Todo drill-down da pagina passa por UM shell. `view` decide o conteudo. */
type DialogView =
  | { kind: "kpi"; key: KpiKind }
  | { kind: "confidence" }
  | { kind: "pulse"; view: PulseView }
  | { kind: "bucket"; date: string }
  | { kind: "channelSeries"; channel: Marketplace }
  | { kind: "volume"; channel: Marketplace }
  | { kind: "matrix"; brand: string; channel: Marketplace }
  | { kind: "matrixChannel"; channel: Marketplace }
  | { kind: "movement"; movement: Movement }
  | { kind: "concentration"; entry: ConcentrationEntry }
  | { kind: "commercial"; item: CommercialAttentionItem }
  | { kind: "dataWarning"; item: DataConfidenceItem };

function GerencialInner() {
  const [filters, setFilters] = useGlobalFilters({ defaultPreset: "mes_anterior", defaultCompare: true });
  const searchParams = useSearchParams();

  // Metrica do grafico: estado LOCAL. `/trend` entrega GMV e Pedidos na mesma
  // resposta, entao alternar nao dispara requisicao alguma nem fecha o dialogo.
  const [metric, setMetric] = useState<TrendMetric>("gmv");
  const [dialog, setDialog] = useState<DialogView | null>(null);

  const sources = useGerencialSources({
    channels: filters.channels,
    brands: filters.brands,
    dateFrom: filters.dateFrom,
    dateTo: filters.dateTo,
    compare: filters.compare,
  });

  /**
   * Task G — identidade dos filtros EFETIVOS. O dialogo fecha por efeito quando
   * ela muda, seja a mudanca vinda dos controles da pagina, do back/forward do
   * navegador ou de uma URL colada. `applyFilters` sozinho nao cobria esses dois
   * ultimos casos, e um detalhe do escopo anterior podia sobreviver.
   */
  const filterIdentity = [
    filters.channels.join(","),
    filters.brands.join(","),
    filters.dateFrom,
    filters.dateTo,
    String(filters.compare),
  ].join("|");

  useEffect(() => {
    setDialog(null);
  }, [filterIdentity]);

  // Retry inicia uma nova resolucao de todas as fontes: o detalhe aberto era do
  // ciclo anterior.
  useEffect(() => {
    setDialog(null);
  }, [sources.retryKey]);

  const periodLabel = fmtPeriodo(filters.dateFrom, filters.dateTo);
  const buildHref = useMemo(() => (href: string) => mergeFilteredHref(href, searchParams), [searchParams]);
  const brandHref = useCallback((brand: string) => buildHref(`/brand/${brand}`), [buildHref]);

  // ---- derivacoes puras -------------------------------------------------
  const overview = sources.overview.data;
  const kpis = useMemo(() => buildKpiBand(overview, filters.channels), [overview, filters.channels]);

  const merged = useMemo(
    () => mergeChannelSeries(sources.series, filters.channels, metric),
    [sources.series, filters.channels, metric],
  );
  const reconciliation = useMemo(
    () =>
      reconcileSeriesTotal(
        merged,
        metric,
        overview ? (metric === "gmv" ? overview.gmv : overview.orders) : null,
      ),
    [merged, metric, overview],
  );

  const pulse = useMemo(() => buildPulse(sources.executiveSummary.data), [sources.executiveSummary.data]);
  const queue = useMemo(() => buildAttentionQueue(sources.executiveSummary.data), [sources.executiveSummary.data]);

  // Task F — a faixa afirma DISPONIBILIDADE DE SERIE, derivada das series por
  // canal, nunca "cobertura" inferida de `gmv != null`.
  const seriesStates = useMemo<ChannelSeriesState[]>(
    () => sources.series.map((s) => ({ channel: s.channel, status: s.status, pointCount: s.points.length })),
    [sources.series],
  );
  const confidence = useMemo(
    () => buildConfidenceStrip(seriesStates, queue, filters.channels, sources.executiveSummary.status.fresh),
    [seriesStates, queue, filters.channels, sources.executiveSummary.status.fresh],
  );

  const volumeRows = useMemo(
    () =>
      buildVolumeHealth({
        kpis: sources.quality.data?.kpis ?? null,
        brands: sources.quality.data?.brands ?? [],
        channels: filters.channels,
        gmvByChannel: {
          tiktok: overview?.tiktok_gmv ?? null,
          ml: overview?.ml_gmv ?? null,
          shopee: overview?.shopee_gmv ?? null,
        },
      }),
    [sources.quality.data, filters.channels, overview],
  );

  const brands = sources.brands.data ?? [];
  const matrix = useMemo(() => buildBrandChannelMatrix(brands, filters.channels), [brands, filters.channels]);
  const movements = useMemo(() => buildMovements(brands, filters.channels), [brands, filters.channels]);
  const concentration = useMemo(() => buildConcentration(brands), [brands]);

  // Task B.4 / C — sinais vem de `/canais`, com estado PROPRIO: a matriz de GMV
  // (que vem de `/brands`) nao depende deles para existir.
  const signalsByCell = useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const row of sources.canais.data?.channelRows ?? []) {
      if (row.signals.length > 0) map[cellKey(row.brand, row.channel)] = row.signals;
    }
    return map;
  }, [sources.canais.data]);

  const signalsState: SignalsState = sources.canais.status.loading
    ? "loading"
    : sources.canais.status.error
      ? "error"
      : "fresh";

  // ---- estados por bloco --------------------------------------------------
  const overviewStatus = sources.overview.status;
  // `live` so' existe quando o overview esta fresco; enquanto nao estiver, o
  // cabecalho nao afirma nem live nem demonstracao.
  const headerLive = overviewStatus.fresh ? sources.overview.live : null;

  // Task A, item 7 — o anuncio nao pode dizer "Dados carregados" havendo fonte
  // em erro. Nomear quais falharam e' mais util que um estado binario.
  const failedSources = [
    overviewStatus.error && "indicadores do período",
    sources.brands.status.error && "desempenho por marca",
    sources.executiveSummary.status.error && "resumo executivo",
    sources.quality.status.error && "qualidade",
    sources.canais.status.error && "sinais de canal",
    merged.failedChannels.length > 0 &&
      `série de ${merged.failedChannels.map((c) => CHANNEL_LABEL[c]).join(", ")}`,
  ].filter(Boolean) as string[];

  const liveAnnouncement = sources.anyLoading
    ? "Carregando dados da Visão Gerencial."
    : failedSources.length > 0
      ? `Dados carregados parcialmente. Indisponível: ${failedSources.join("; ")}.`
      : "Dados carregados.";

  const openBucket = useCallback((date: string) => setDialog({ kind: "bucket", date }), []);
  const pinRange = useCallback(
    (dateFrom: string, dateTo: string) => {
      setDialog(null);
      // Somente o intervalo muda; canais, marcas e comparacao seguem preservados.
      setFilters({ dateFrom, dateTo });
    },
    [setFilters],
  );

  // ---- titulo e conteudo do dialogo unico --------------------------------
  const dialogTitle = (() => {
    if (!dialog) return "";
    switch (dialog.kind) {
      case "kpi":
        return KPI_META[dialog.key].label;
      case "confidence":
        return "Confiança no dado";
      case "bucket": {
        const b = merged.buckets.find((x) => x.date === dialog.date);
        return b ? `Detalhe de ${b.label}` : "Detalhe do período";
      }
      case "channelSeries":
        return `Série — ${CHANNEL_LABEL[dialog.channel]}`;
      case "volume":
        return `Saúde do volume — ${CHANNEL_LABEL[dialog.channel]}`;
      case "matrix":
        return `${dialog.brand.toUpperCase()} em ${CHANNEL_LABEL[dialog.channel]}`;
      case "matrixChannel":
        return `${CHANNEL_LABEL[dialog.channel]} — distribuição por marca`;
      case "movement":
        return `${dialog.movement.brandLabel} — ${CHANNEL_LABEL[dialog.movement.channel]}`;
      case "concentration":
        return `${dialog.entry.label} — concentração`;
      case "commercial":
        return dialog.item.title;
      case "dataWarning":
        return "Aviso de confiança no dado";
      case "pulse": {
        if (dialog.view.key) {
          const g =
            pulse.commercial.find((x) => x.key === dialog.view.key) ??
            pulse.dataConfidence.groups.find((x) => x.key === dialog.view.key);
          if (g) return g.title;
        }
        return "Todos os sinais do período";
      }
    }
  })();

  const matrixRow =
    dialog?.kind === "matrix"
      ? sources.canais.data?.channelRows.find(
          (r) => r.brand === dialog.brand && r.channel === dialog.channel,
        ) ?? null
      : null;

  const bucket: MergedBucket | null =
    dialog?.kind === "bucket" ? merged.buckets.find((b) => b.date === dialog.date) ?? null : null;

  // Canal realcado no grafico enquanto o detalhe da serie esta aberto.
  const highlightedChannel = dialog?.kind === "channelSeries" ? dialog.channel : null;

  return (
    <div className="max-w-[1440px] mx-auto px-6 py-6 flex flex-col gap-4">
      <GerencialHeader
        periodLabel={periodLabel}
        refreshedAt={sources.overview.refreshedAt}
        live={headerLive}
        loading={sources.anyLoading}
      >
        <div className="flex items-start gap-2 flex-wrap min-w-0">
          <MarketplaceFilter value={filters.channels} onChange={(channels) => setFilters({ channels })} />
          <BrandFilter value={filters.brands} onChange={(brandKeys) => setFilters({ brands: brandKeys })} />
        </div>
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          {sources.anyLoading && (
            <span className="text-xs text-violet-400 animate-pulse shrink-0">Atualizando…</span>
          )}
          <DateRangeFilter
            dateFrom={filters.dateFrom}
            dateTo={filters.dateTo}
            compare={filters.compare}
            onChange={(v) => setFilters(v)}
            onCompareChange={(compare) => setFilters({ compare })}
          />
        </div>
      </GerencialHeader>

      <span className="sr-only" aria-live="polite" aria-atomic="true">
        {liveAnnouncement}
      </span>

      {/* ---- Dobra 1: o que aconteceu ----
          Nenhum gate global daqui para baixo: cada bloco decide sozinho. */}
      <ConfidenceStrip
        data={confidence}
        loading={confidence.checkingCount === confidence.selectedCount && confidence.selectedCount > 0}
        onOpen={() => setDialog({ kind: "confidence" })}
      />

      <KpiBand
        kpis={kpis}
        loading={overviewStatus.loading}
        error={overviewStatus.error}
        onOpen={(key) => setDialog({ kind: "kpi", key })}
        onRetry={sources.retry}
      />

      {/* ---- Dobra 2: como evoluiu / o que atender ----
          12 colunas: 8/4 no tablet e 7/5 no desktop. SEM `items-start` e SEM
          `row-span`: os dois itens esticam juntos. */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
        <div className="order-2 lg:order-none lg:col-span-8 xl:col-span-7">
          <EvolutionCard
            merged={merged}
            metric={metric}
            onMetricChange={setMetric}
            reconciliation={reconciliation}
            compareActive={filters.compare}
            loading={merged.loadingChannels.length > 0 && merged.availableChannels.length === 0}
            onSelectBucket={openBucket}
            onOpenChannelSeries={(channel) => setDialog({ kind: "channelSeries", channel })}
            highlightedChannel={highlightedChannel}
          />
        </div>
        <div className="order-1 lg:order-none lg:col-span-4 xl:col-span-5">
          <PulseChannelsColumn
            pulse={pulse}
            health={sources.executiveSummary.data?.health ?? null}
            pulseLoading={sources.executiveSummary.status.loading}
            pulseUnavailable={sources.executiveSummary.status.error}
            onOpenInsight={(key) => setDialog({ kind: "pulse", view: { mode: "insight", key } })}
            onOpenAllInsights={() => setDialog({ kind: "pulse", view: { mode: "all", key: null } })}
            overview={overview}
            channels={filters.channels}
            channelsLoading={overviewStatus.loading}
            channelsUnavailable={overviewStatus.error}
            onOpenChannel={(channel) => setDialog({ kind: "volume", channel })}
            channelsHref={buildHref("/canais")}
          />
        </div>
      </div>

      {/* ---- Dobra 3: onde aconteceu ---- */}
      <VolumeHealthCard
        rows={volumeRows}
        loading={sources.quality.status.loading}
        error={sources.quality.status.error}
        onOpenChannel={(channel) => setDialog({ kind: "volume", channel })}
        onRetry={sources.retry}
      />

      <BrandChannelMatrix
        matrix={matrix}
        loading={sources.brands.status.loading}
        error={sources.brands.status.error}
        signalsByCell={signalsByCell}
        signalsState={signalsState}
        onOpenCell={(brand, channel) => setDialog({ kind: "matrix", brand, channel })}
        onOpenChannelHeader={(channel) => setDialog({ kind: "matrixChannel", channel })}
        brandHref={brandHref}
        onRetry={sources.retry}
      />

      {/* ---- Dobra 4: por que ---- */}
      <MovementsPanels
        movements={movements}
        concentration={concentration}
        loading={sources.brands.status.loading}
        error={sources.brands.status.error}
        onOpenMovement={(movement) => setDialog({ kind: "movement", movement })}
        onOpenConcentration={(entry) => setDialog({ kind: "concentration", entry })}
        produtosHref={buildHref("/produtos")}
        onRetry={sources.retry}
      />

      {/* ---- Dobra 5: para onde ir ---- */}
      <AttentionQueue
        commercial={queue.commercial}
        dataConfidence={queue.dataConfidence}
        commercialLoading={sources.executiveSummary.status.loading}
        commercialError={sources.executiveSummary.status.error}
        onOpenCommercial={(item) => setDialog({ kind: "commercial", item })}
        onOpenDataWarning={(item) => setDialog({ kind: "dataWarning", item })}
        onRetry={sources.retry}
      />

      {/* ---- Shell UNICO de dialogo, para todos os drill-downs ---- */}
      <KpiDrilldownDialog open={dialog != null} onClose={() => setDialog(null)} title={dialogTitle}>
        {dialog?.kind === "kpi" && overview && (
          <KpiDrilldownContent
            kind={dialog.key}
            value={kpis.find((k) => k.key === dialog.key)?.value ?? "—"}
            periodLabel={periodLabel}
            refreshedAt={sources.overview.refreshedAt}
            overview={overview}
            brands={brands}
            channels={filters.channels}
            buildHref={buildHref}
          />
        )}

        {dialog?.kind === "confidence" && (
          <ConfidenceDrilldownContent
            data={confidence}
            items={queue.dataConfidence}
            periodLabel={periodLabel}
            refreshedAt={sources.executiveSummary.refreshedAt}
            regioesHref={buildHref("/regioes")}
          />
        )}

        {dialog?.kind === "pulse" && (
          <InsightDrilldownContent
            pulse={pulse}
            view={dialog.view}
            periodLabel={periodLabel}
            refreshedAt={sources.executiveSummary.refreshedAt}
            onSelectGroup={(key) => setDialog({ kind: "pulse", view: { mode: "all", key } })}
            onBackToAll={() => setDialog({ kind: "pulse", view: { mode: "all", key: null } })}
            buildHref={buildHref}
          />
        )}

        {dialog?.kind === "bucket" && bucket && (
          <TrendBucketDrilldownContent
            bucket={bucket}
            merged={merged}
            metric={metric}
            channels={filters.channels}
            granularityLabel={merged.granularity === "day" ? "grão diário" : "grão mensal"}
            periodLabel={periodLabel}
            brandsFilter={filters.brands}
            demoMode={sources.demoMode}
            onPinRange={pinRange}
          />
        )}

        {dialog?.kind === "channelSeries" && (
          <ChannelSeriesDrilldownContent
            channel={dialog.channel}
            merged={merged}
            metric={metric}
            periodLabel={periodLabel}
            canaisHref={buildHref(`/canais?channels=${dialog.channel}`)}
          />
        )}

        {dialog?.kind === "volume" && (() => {
          const row = volumeRows.find((r) => r.channel === dialog.channel);
          if (!row) {
            return (
              <p className="text-sm text-slate-500">
                Sem dados de qualidade para {CHANNEL_LABEL[dialog.channel]} no período e filtros selecionados.
              </p>
            );
          }
          return (
            <VolumeHealthDrilldownContent
              row={row}
              periodLabel={periodLabel}
              refreshedAt={sources.quality.refreshedAt}
              qualidadeHref={buildHref("/qualidade")}
            />
          );
        })()}

        {dialog?.kind === "matrix" &&
          (matrixRow ? (
            <ChannelComparisonDialogContent
              row={matrixRow}
              median={findChannelMedian(sources.canais.data?.channelMedians ?? [], matrixRow.channel)}
              periodLabel={periodLabel}
              refreshedAt={sources.canais.refreshedAt}
              buildHref={buildHref}
            />
          ) : (
            // Quatro causas distintas para a linha comparativa faltar — a
            // mensagem diz qual, em vez de sempre culpar a carga.
            <MatrixCellUnavailableContent
              status={signalsState}
              demoMode={sources.demoMode}
              brandLabel={matrix.rows.find((r) => r.brand === dialog.brand)?.label ?? dialog.brand.toUpperCase()}
              channelLabel={CHANNEL_LABEL[dialog.channel]}
            />
          ))}

        {dialog?.kind === "matrixChannel" && (
          <MatrixChannelDrilldownContent
            channel={dialog.channel}
            matrix={matrix}
            periodLabel={periodLabel}
            refreshedAt={sources.brands.refreshedAt}
            canaisHref={buildHref(`/canais?channels=${dialog.channel}`)}
          />
        )}

        {dialog?.kind === "movement" && (
          <MovementDrilldownContent
            movement={dialog.movement}
            periodLabel={periodLabel}
            refreshedAt={sources.brands.refreshedAt}
            brandHref={brandHref(dialog.movement.brand)}
          />
        )}

        {dialog?.kind === "concentration" && (
          <ConcentrationDrilldownContent
            entry={dialog.entry}
            position={concentration.entries.findIndex((e) => e.brand === dialog.entry.brand) + 1}
            top1Pct={concentration.top1Pct}
            top3Pct={concentration.top3Pct}
            positiveBrands={concentration.positiveBrands}
            periodLabel={periodLabel}
            refreshedAt={sources.brands.refreshedAt}
            brandHref={brandHref(dialog.entry.brand)}
            produtosHref={buildHref("/produtos")}
          />
        )}

        {dialog?.kind === "commercial" && (
          <CommercialAttentionDrilldownContent
            item={dialog.item}
            periodLabel={periodLabel}
            refreshedAt={sources.executiveSummary.refreshedAt}
            href={buildHref(dialog.item.href)}
          />
        )}

        {dialog?.kind === "dataWarning" && (
          <DataWarningDrilldownContent
            item={dialog.item}
            periodLabel={periodLabel}
            refreshedAt={sources.executiveSummary.refreshedAt}
            href={dialog.item.href ? buildHref(dialog.item.href) : null}
          />
        )}
      </KpiDrilldownDialog>
    </div>
  );
}

export default function Gerencial() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#f8f7ff]" />}>
      <GerencialInner />
    </Suspense>
  );
}
