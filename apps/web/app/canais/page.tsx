"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  fetchCanais,
  type CanaisKpis,
  type CanaisBrandRow,
  type CanaisChannelRow,
  type CanaisChannelMedian,
  type AffiliateCostsBlock,
} from "@/lib/api-client";
import { isMarketplaceSelected } from "@/lib/marketplace-filter";
import { useGlobalFilters } from "@/hooks/useGlobalFilters";
import { mergeFilteredHref } from "@/lib/filters/nav-links";
import KpiCard from "@/components/KpiCard";
import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import ChannelComparisonDialogContent from "@/components/ChannelComparisonDialogContent";
import AffiliateCostsPanel from "@/components/AffiliateCostsPanel";
import { resolveBlockPhase } from "@/lib/canais-affiliate-costs";
import { SkeletonKpiCard, SkeletonTableRows } from "@/components/Skeleton";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import BrandFilter from "@/components/BrandFilter";
import DateRangeFilter from "@/components/DateRangeFilter";
import LiveStatusBadge from "@/components/LiveStatusBadge";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { fmtPeriodo, fmtRefreshedAt, mockLimitationNote } from "@/lib/filters/format";
import { detectPreset } from "@/lib/filters/presets";
import { useSortableTable } from "@/lib/use-sortable-table";
import SortableHeader from "@/components/SortableHeader";
import TableScrollHint from "@/components/TableScrollHint";
import {
  formatChannelMetric,
  signalLabel,
  signalTone,
  findChannelMedian,
  CHANNEL_BADGE_TONE,
  type FormattedMetric,
} from "@/lib/canais-channel-metrics";
import {
  contentMixFromApi,
  contentMixWeights,
  dominantContentOrigin,
  formatContentPctBr,
  formatDivergenceBr,
  MIX_UNAVAILABLE_LABEL,
  type ContentMix,
} from "@/lib/tiktok-content-mix";

function fmtPct(v: number | null, dec = 1): string {
  if (v == null) return "—";
  return v.toFixed(dec) + "%";
}

const fmtPct1 = (v: number) => `${v.toFixed(1)}%`;
const fmtRoas = (v: number) => `${v.toFixed(2)}x`;

/** Identidade estavel da requisicao atual (mesmo padrao "Finding 2" adotado
 * na Gerencial no Gate U2) — enquanto a chave resolvida nao bate com a
 * atual, kpis/brands/channelRows/channelMedians em estado sao tratados como
 * potencialmente antigos e nao sao exibidos como se fossem do filtro atual. */
function buildRequestKey(
  channels: readonly string[], brands: readonly string[], dateFrom: string, dateTo: string, compare: boolean, retryKey: number,
): string {
  return `${channels.join(",")}|${brands.join(",")}|${dateFrom}|${dateTo}|${compare}|${retryKey}`;
}

function channelMetricToneClass(tone: FormattedMetric["tone"]): string {
  if (tone === "value") return "text-slate-700 font-medium";
  if (tone === "warning") return "text-amber-700 bg-amber-50 rounded px-1.5 py-0.5 text-xs font-semibold";
  return "text-slate-400 text-xs italic";
}

function ChannelMetricCell({
  value, applicable, available, format, warning,
}: {
  value: number | null; applicable: boolean; available: boolean;
  format: (v: number) => string; warning?: string | null;
}) {
  const { text, tone } = formatChannelMetric(value, applicable, available, format);
  return (
    <span className={channelMetricToneClass(tone)} title={warning ?? undefined}>
      {text}
    </span>
  );
}

/**
 * Barra de COMPOSICAO do mix de conteudo — video, live e card/vitrine.
 *
 * Duas mudancas de contrato em relacao a antiga `AttributionBar`:
 *
 * 1. **Sem segmento residual.** A antiga calculava
 *    `others = Math.max(0, 100 - v - l - c)` e pintava o vao de cinza. Esse
 *    residuo nao existe na fonte: as tres categorias particionam a base de
 *    conteudo por construcao, e o vao era so arredondamento (ate 0,3pp).
 *    Agora os pesos sao os valores MONETARIOS via `flexGrow`, entao a barra
 *    preenche 100% sem inventar categoria e sem mexer nos percentuais exibidos.
 * 2. **Sem barra falsa de 0%.** Sem base valida a antiga renderizava tres
 *    divs de largura 0 sobre fundo cinza, visualmente identico a "tudo zero".
 *    Agora declara indisponibilidade em texto.
 */
function ContentMixBar({ mix, gmvVideo, gmvLive, gmvCard }: {
  mix: ContentMix;
  gmvVideo: number | null;
  gmvLive: number | null;
  gmvCard: number | null;
}) {
  if (mix.base == null) {
    return <span className="text-xs text-slate-400">{MIX_UNAVAILABLE_LABEL}</span>;
  }
  const w = contentMixWeights(gmvVideo, gmvLive, gmvCard);
  const label =
    `Mix do GMV de conteúdo sobre base de ${fmtBrl(mix.base)}: ` +
    `vídeo ${formatContentPctBr(mix.videoPct)}, live ${formatContentPctBr(mix.livePct)}, card/vitrine ${formatContentPctBr(mix.cardPct)}.`;
  return (
    <div
      className="flex h-2 rounded-full overflow-hidden w-28 bg-slate-100"
      role="img"
      aria-label={label}
      title={label}
    >
      <div className="bg-violet-500 transition-all" style={{ flexGrow: w.video }} />
      <div className="bg-cyan-500 transition-all" style={{ flexGrow: w.live }} />
      <div className="bg-amber-400 transition-all" style={{ flexGrow: w.card }} />
    </div>
  );
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

/** Anchors internos compactos (Task 3) — sempre reais (`href="#id"`), nunca
 * um estado paralelo de navegacao. So aparecem para canais atualmente
 * selecionados/visiveis. */
function InternalNav({ showTiktok, showMl, showShopee }: { showTiktok: boolean; showMl: boolean; showShopee: boolean }) {
  const linkClass = "px-2.5 py-1 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500";
  return (
    <nav aria-label="Navegação interna da página" className="flex flex-wrap gap-1 -mx-2.5">
      <a href="#comparativo" className={linkClass}>Comparativo</a>
      {showTiktok && <a href="#tiktok-shop" className={linkClass}>TikTok Shop</a>}
      {showMl && <a href="#mercado-livre" className={linkClass}>Mercado Livre</a>}
      {showShopee && <a href="#shopee" className={linkClass}>Shopee</a>}
    </nav>
  );
}

function CanaisPageInner() {
  const [filters, setFilters] = useGlobalFilters({ defaultPreset: "mes_anterior" });
  const searchParams = useSearchParams();
  const filter = filters.channels; // alias — preserva as referencias existentes abaixo
  const [kpis, setKpis] = useState<CanaisKpis | null>(null);
  const [brands, setBrands] = useState<CanaisBrandRow[]>([]);
  const [channelRows, setChannelRows] = useState<CanaisChannelRow[]>([]);
  const [channelMedians, setChannelMedians] = useState<CanaisChannelMedian[]>([]);
  const [affiliateCosts, setAffiliateCosts] = useState<AffiliateCostsBlock | null>(null);
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  const [detailRow, setDetailRow] = useState<CanaisChannelRow | null>(null);
  // Chave da ultima requisicao resolvida com sucesso — comparada com a chave
  // atual para decidir se o estado em memoria reflete de fato os filtros
  // exibidos agora (mesmo padrao da Gerencial, Gate U2 Finding 2).
  const [resolvedKey, setResolvedKey] = useState<string | null>(null);

  const requestKey = useMemo(
    () => buildRequestKey(filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey),
    [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey],
  );

  useEffect(() => {
    // Ignora a resposta se os filtros mudarem antes dela chegar.
    let ignore = false;
    setLoading(true);
    setError(null);
    // Fecha qualquer detalhe marca x canal aberto — o dado subjacente
    // (channelRows) so faz sentido para o filtro que estava fresco ate agora.
    setDetailRow(null);
    const key = buildRequestKey(filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey);
    const opts = { brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, compare: filters.compare };
    fetchCanais(filters.channels, undefined, opts)
      .then((r) => {
        if (ignore) return;
        setKpis(r.kpis);
        setBrands(r.brands);
        setChannelRows(r.channelRows);
        setChannelMedians(r.channelMedians);
        setAffiliateCosts(r.affiliateCosts);
        setIsLive(r.live);
        setRefreshedAt(r.meta.refreshedAt);
        setResolvedKey(key);
        setLoading(false);
      })
      .catch(() => {
        if (ignore) return;
        setError("Falha ao carregar dados de canais.");
        setLoading(false);
      });
    return () => { ignore = true; };
  }, [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey]);

  // Dados so sao considerados "frescos" quando: nao esta carregando, nao ha
  // erro, e a chave resolvida bate com a chave da requisicao atual — fecha
  // inclusive o frame de render em que os filtros ja mudaram mas o efeito
  // ainda nao rodou (nesse frame `loading`/`error` locais ainda podem estar
  // desatualizados, mas `resolvedKey !== requestKey` ja detecta o descompasso).
  const dataIsFresh = !loading && !error && resolvedKey === requestKey;

  // Versoes "protegidas" do estado bruto (Finding 1, rodada de correcao) —
  // NENHUM calculo/total/tabela/insight/dialogo abaixo deve ler
  // kpis/brands/channelRows/channelMedians diretamente; sempre via estas
  // constantes, que ficam vazias/nulas quando os dados nao sao frescos (em
  // vez de continuar exibindo o resultado da requisicao anterior sob um
  // erro novo ou um filtro novo).
  const displayKpis = dataIsFresh ? kpis : null;
  const displayBrands = dataIsFresh ? brands : [];
  const displayChannelRows = dataIsFresh ? channelRows : [];
  const displayChannelMedians = dataIsFresh ? channelMedians : [];
  // O bloco de afiliados obedece a MESMA guarda de frescor: um custo contabil
  // do filtro anterior exibido sob o filtro novo seria pior que ausencia.
  const displayAffiliateCosts = dataIsFresh ? affiliateCosts : null;
  // Quatro fases EXPLICITAS em vez de `loading = !dataIsFresh`, que colapsava
  // "carregando" e "terminou em erro" no mesmo skeleton — e o painel pulsava
  // para sempre depois de uma falha.
  const affiliatePhase = resolveBlockPhase({
    loading, error: error !== null, requestKey, resolvedKey,
  });

  const buildHref = useMemo(() => (href: string) => mergeFilteredHref(href, searchParams), [searchParams]);

  const showTiktok = isMarketplaceSelected(filter, "tiktok");
  const showMl = isMarketplaceSelected(filter, "ml");
  const showShopee = isMarketplaceSelected(filter, "shopee");

  const tkBrands = displayBrands.filter((b) => b.tiktok_gmv != null);
  const mlBrands = displayBrands.filter((b) => b.ml_gmv != null);
  const shBrands = displayBrands.filter((b) => b.shopee_gmv != null);

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

  // Mix do TOTAL: consumido do backend, NAO recalculado aqui.
  //
  // Antes esta pagina fazia `tkVidTotal / tkGmvTotal * 100` — o mesmo defeito
  // de denominador que o UE-F1A corrigiu no backend, duplicado no cliente, e
  // ainda com fallback `: 0` que fabricava 0% quando nao havia base. O
  // contrato (base = video+live+card) mora no backend; aqui so se le.
  const tkMixTotal = contentMixFromApi(displayKpis);

  const mlNewPctTotal = mlBuyersTotal > 0 ? (mlNewTotal / mlBuyersTotal) * 100 : 0;
  const mlRepeatPctTotal = mlBuyersTotal > 0 ? (mlRepeatTotal / mlBuyersTotal) * 100 : 0;
  const mlGmvPerBuyerTotal = mlBuyersTotal > 0 ? mlGmvTotal / mlBuyersTotal : null;

  const shNewPctTotal = shBuyersTotal > 0 ? (shNewTotal / shBuyersTotal) * 100 : 0;
  const shRepeatPctTotal = shBuyersTotal > 0 ? (shRepeatTotal / shBuyersTotal) * 100 : 0;
  const shGmvPerBuyerTotal = shBuyersTotal > 0 ? shGmvTotal / shBuyersTotal : null;
  const shVisitorsTotal = shBrands.reduce((s, b) => s + (b.shopee_visitors ?? 0), 0);
  const shConvRateTotal = shVisitorsTotal > 0 ? (shBuyersTotal / shVisitorsTotal) * 100 : null;

  // Shopee: só renderiza seção completa quando há dados reais. Enquanto os
  // dados nao estao frescos (loading OU o frame de transicao de filtro),
  // mantem a secao "reservada" com skeleton — nunca com base em `loading`
  // sozinho, que pode estar desatualizado nesse mesmo frame.
  const hasShopeeData = !dataIsFresh || shBrands.length > 0;

  const periodLabel = fmtPeriodo(filters.dateFrom, filters.dateTo);
  const isEmpty = dataIsFresh && displayBrands.length === 0;

  const tkColumnTypes = useMemo(() => ({
    brand: "text" as const, gmv: "numeric" as const, video_pct: "numeric" as const,
    live_pct: "numeric" as const, card_pct: "numeric" as const, visitors: "numeric" as const,
    conversion: "numeric" as const,
  }), []);
  const tkGetValue = (row: CanaisBrandRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "gmv": return row.tiktok_gmv;
      case "video_pct": return row.tiktok_video_pct;
      case "live_pct": return row.tiktok_live_pct;
      case "card_pct": return row.tiktok_card_pct;
      case "visitors": return row.tiktok_visitors;
      case "conversion": return row.tiktok_conversion_rate;
      default: return null;
    }
  };
  const tkSort = useSortableTable(tkBrands, tkGetValue, tkColumnTypes);

  const mlColumnTypes = useMemo(() => ({
    brand: "text" as const, gmv: "numeric" as const, buyers: "numeric" as const,
    new_pct: "numeric" as const, new: "numeric" as const, repeat: "numeric" as const,
    repeat_pct: "numeric" as const, gmv_per_buyer: "numeric" as const,
  }), []);
  const mlGetValue = (row: CanaisBrandRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "gmv": return row.ml_gmv;
      case "buyers": return row.ml_unique_buyers;
      case "new_pct": return row.ml_new_buyer_pct ??
        (row.ml_unique_buyers && row.ml_new_buyers ? (row.ml_new_buyers / row.ml_unique_buyers) * 100 : null);
      case "new": return row.ml_new_buyers;
      case "repeat": return row.ml_repeat_buyers;
      case "repeat_pct": return row.ml_repeat_buyer_rate_pct;
      case "gmv_per_buyer": return row.ml_gmv_per_buyer;
      default: return null;
    }
  };
  const mlSort = useSortableTable(mlBrands, mlGetValue, mlColumnTypes);

  const shColumnTypes = useMemo(() => ({
    brand: "text" as const, gmv: "numeric" as const, buyers: "numeric" as const,
    new_pct: "numeric" as const, new: "numeric" as const, repeat: "numeric" as const,
    repeat_pct: "numeric" as const, gmv_per_buyer: "numeric" as const, visitors: "numeric" as const,
    conversion: "numeric" as const,
  }), []);
  const shGetValue = (row: CanaisBrandRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "gmv": return row.shopee_gmv;
      case "buyers": return row.shopee_unique_buyers;
      case "new_pct": return row.shopee_new_buyer_pct ??
        (row.shopee_unique_buyers && row.shopee_new_buyers ? (row.shopee_new_buyers / row.shopee_unique_buyers) * 100 : null);
      case "new": return row.shopee_new_buyers;
      case "repeat": return row.shopee_repeat_buyers;
      case "repeat_pct": return row.shopee_repeat_buyer_rate_pct;
      case "gmv_per_buyer": return row.shopee_gmv_per_buyer;
      case "visitors": return row.shopee_visitors ?? null;
      case "conversion": return row.shopee_conversion_rate ?? null;
      default: return null;
    }
  };
  const shSort = useSortableTable(shBrands, shGetValue, shColumnTypes);

  // ── Matriz comparativa marca x canal (Ads/Custo/Frete + sinais) — Gate 2 ──
  const channelMatrixColumnTypes = useMemo(() => ({
    brand: "text" as const, channel: "text" as const, gmv: "numeric" as const,
    orders: "numeric" as const, ads_gmv_pct: "numeric" as const, roas: "numeric" as const,
    acos_pct: "numeric" as const, marketplace_cost_pct: "numeric" as const,
    seller_shipping_pct: "numeric" as const,
  }), []);
  const channelMatrixGetValue = (row: CanaisChannelRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "channel": return row.channel_label;
      case "gmv": return row.gmv;
      case "orders": return row.orders;
      case "ads_gmv_pct": return row.ads_available ? row.ads_gmv_pct : null;
      case "roas": return row.ads_available ? row.roas : null;
      case "acos_pct": return row.ads_available ? row.acos_pct : null;
      case "marketplace_cost_pct": return row.marketplace_cost_available ? row.marketplace_cost_pct : null;
      case "seller_shipping_pct": return row.seller_shipping_available ? row.seller_shipping_pct : null;
      default: return null;
    }
  };
  const channelMatrixSort = useSortableTable(displayChannelRows, channelMatrixGetValue, channelMatrixColumnTypes);

  return (
    <PageContainer>
      <PageHeader
        title="Canais"
        subtitle="Comparar TikTok Shop, Mercado Livre e Shopee — e investigar marca × canal."
        scopeLine={
          <>
            Período: {periodLabel}
            {dataIsFresh && refreshedAt && <> · Atualizado em {fmtRefreshedAt(refreshedAt)}</>}
          </>
        }
        status={
          dataIsFresh ? (
            <LiveStatusBadge live={isLive} />
          ) : loading ? (
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

      {/* Navegacao interna compacta */}
      <InternalNav showTiktok={showTiktok} showMl={showMl} showShopee={showShopee} />

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
        {loading ? "Carregando dados de canais..." : error ? "Falha ao carregar." : "Dados de canais carregados."}
      </span>

      {isEmpty && (
        <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-12 text-center">
          <p className="text-slate-500 text-sm font-medium">Sem dados no período e filtros selecionados.</p>
          <p className="text-slate-400 text-xs mt-1">Tente ampliar o intervalo de datas ou revisar canal/marca.</p>
        </div>
      )}

      {/* ── Resumo TikTok ── */}
      {showTiktok && (
        <section id="tiktok-shop" aria-labelledby="tiktok-shop-heading" className="scroll-mt-24 flex flex-col gap-3" aria-busy={!dataIsFresh}>
          <div>
            <h2 id="tiktok-shop-heading" className="text-sm font-semibold text-slate-700">Mix do GMV de conteúdo do TikTok</h2>
            <p className="text-xs text-slate-500">
              Distribuição entre vídeo, live e card/vitrine <strong className="font-semibold">dentro da base de conteúdo</strong> do
              TikTok. Não representa participação nas vendas totais.
            </p>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {!dataIsFresh ? (
              <><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /></>
            ) : (
              <>
                <KpiCard
                  label="Vídeo — % da base de conteúdo"
                  value={formatContentPctBr(displayKpis?.tiktok_video_pct)}
                  subvalue={displayKpis?.tiktok_gmv_video != null ? fmtBrl(displayKpis.tiktok_gmv_video) : undefined}
                  accent="bg-violet-600"
                />
                <KpiCard
                  label="Live — % da base de conteúdo"
                  value={formatContentPctBr(displayKpis?.tiktok_live_pct)}
                  subvalue={displayKpis?.tiktok_gmv_live != null ? fmtBrl(displayKpis.tiktok_gmv_live) : undefined}
                  accent="bg-cyan-500"
                />
                <KpiCard
                  label="Card/vitrine — % da base de conteúdo"
                  value={formatContentPctBr(displayKpis?.tiktok_card_pct)}
                  subvalue={displayKpis?.tiktok_gmv_card != null ? fmtBrl(displayKpis.tiktok_gmv_card) : undefined}
                  accent="bg-amber-400"
                />
                <KpiCard
                  label="Conversao TikTok"
                  value={formatContentPctBr(displayKpis?.tiktok_conversion_rate)}
                  subvalue={displayKpis?.tiktok_customers != null ? `${fmtNumber(displayKpis.tiktok_customers)} compradores (soma diária)` : undefined}
                  accent="bg-violet-300"
                />
              </>
            )}
          </div>
          {/* Faixa de reconciliacao — diagnostico NEUTRO entre duas linhagens.
            * Fica FORA da barra de composicao de proposito: a divergencia nao e'
            * um quarto segmento do mix. Sem cor de julgamento (positivo nao e'
            * "bom", negativo nao e' "ruim") e sem a palavra cobertura/share. */}
          {dataIsFresh && (
            <div className="bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 flex flex-wrap items-baseline gap-x-6 gap-y-2">
              <div className="flex items-baseline gap-2">
                <span className="text-xs text-slate-500">Base de conteúdo:</span>
                <span className="text-xs font-semibold tabular-nums text-slate-700">
                  {tkMixTotal.base != null ? fmtBrl(tkMixTotal.base) : "N/D"}
                </span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-xs text-slate-500">GMV comercial TikTok:</span>
                <span className="text-xs font-semibold tabular-nums text-slate-700">
                  {displayKpis?.tiktok_gmv != null ? fmtBrl(displayKpis.tiktok_gmv) : "N/D"}
                </span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-xs text-slate-500">Diferença entre base de conteúdo e GMV comercial:</span>
                <span className="text-xs font-semibold tabular-nums text-slate-600">
                  {formatDivergenceBr(tkMixTotal.divergencePct)}
                </span>
              </div>
              <p className="text-xs text-slate-400 basis-full">
                As medidas têm linhagens diferentes: o GMV comercial vem do GMV canônico de pedidos, e os componentes vêm da
                quebra de conteúdo do TikTok. A diferença é um diagnóstico de reconciliação — não é cobertura, participação,
                margem nem severidade.
              </p>
            </div>
          )}
        </section>
      )}

      {/* ── Resumo Mercado Livre ── */}
      {showMl && (
        <section id="mercado-livre" aria-labelledby="mercado-livre-heading" className="scroll-mt-24 flex flex-col gap-3" aria-busy={!dataIsFresh}>
          <div>
            <h2 id="mercado-livre-heading" className="text-sm font-semibold text-slate-700">Mercado Livre</h2>
            <p className="text-xs text-slate-400">Perfil de compradores — aquisição vs. retenção no mês.</p>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            {!dataIsFresh ? (
              <><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /></>
            ) : (
              <>
                <KpiCard
                  label="Novos Compradores ML"
                  value={fmtPct(displayKpis?.ml_new_buyer_pct ?? null)}
                  subvalue={displayKpis?.ml_new_buyers != null ? `${fmtNumber(displayKpis.ml_new_buyers)} novos` : undefined}
                  accent="bg-cyan-500"
                />
                <KpiCard
                  label="Recompra ML"
                  value={fmtPct(displayKpis?.ml_repeat_buyer_rate_pct ?? null)}
                  subvalue={displayKpis?.ml_repeat_buyers != null ? `${fmtNumber(displayKpis.ml_repeat_buyers)} recorrentes` : undefined}
                  accent="bg-emerald-500"
                />
                <KpiCard
                  label="GMV por Comprador ML"
                  value={displayKpis?.ml_gmv_per_buyer != null ? fmtBrl(displayKpis.ml_gmv_per_buyer) : "—"}
                  subvalue={displayKpis?.ml_unique_buyers != null ? `${fmtNumber(displayKpis.ml_unique_buyers)} compradores (soma diária)` : undefined}
                  accent="bg-amber-500"
                />
              </>
            )}
          </div>
        </section>
      )}

      {/* ── Resumo Shopee — só quando há dados ── */}
      {showShopee && hasShopeeData && (
        <section id="shopee" aria-labelledby="shopee-heading" className="scroll-mt-24 flex flex-col gap-3" aria-busy={!dataIsFresh}>
          <div>
            <h2 id="shopee-heading" className="text-sm font-semibold text-slate-700">Shopee</h2>
            <p className="text-xs text-slate-400">Perfil de compradores — aquisição vs. retenção no mês.</p>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
            {!dataIsFresh ? (
              <><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /></>
            ) : (
              <>
                <KpiCard
                  label="Novos Compradores Shopee"
                  value={fmtPct(displayKpis?.shopee_new_buyer_pct ?? null)}
                  subvalue={displayKpis?.shopee_new_buyers != null ? `${fmtNumber(displayKpis.shopee_new_buyers)} novos` : undefined}
                  accent="bg-orange-500"
                />
                <KpiCard
                  label="Recompra Shopee"
                  value={fmtPct(displayKpis?.shopee_repeat_buyer_rate_pct ?? null)}
                  subvalue={displayKpis?.shopee_repeat_buyers != null ? `${fmtNumber(displayKpis.shopee_repeat_buyers)} recorrentes` : undefined}
                  accent="bg-emerald-500"
                />
                <KpiCard
                  label="GMV / Comprador Shopee"
                  value={displayKpis?.shopee_gmv_per_buyer != null ? fmtBrl(displayKpis.shopee_gmv_per_buyer) : "—"}
                  subvalue={displayKpis?.shopee_unique_buyers != null ? `${fmtNumber(displayKpis.shopee_unique_buyers)} compradores (soma diária)` : undefined}
                  accent="bg-amber-500"
                />
                <KpiCard
                  label="Visitantes Shopee"
                  value={displayKpis?.shopee_visitors != null ? fmtNumber(displayKpis.shopee_visitors) : "—"}
                  subvalue="Visitas ao perfil no mês"
                  accent="bg-sky-500"
                />
                <KpiCard
                  label="Conversão Shopee"
                  value={displayKpis?.shopee_conversion_rate != null ? fmtPct(displayKpis.shopee_conversion_rate) : "—"}
                  subvalue="Compradores / Visitantes"
                  accent="bg-violet-400"
                />
              </>
            )}
          </div>
        </section>
      )}

      {/* ── Placeholder Shopee quando filtro=shopee e sem dados ── */}
      {filter.length === 1 && showShopee && dataIsFresh && !hasShopeeData && (
        <div id="shopee" className="scroll-mt-24 bg-orange-50 border border-orange-100 rounded-2xl p-6 flex flex-col items-center gap-2 text-center">
          <p className="text-sm font-semibold text-orange-700">Shopee — Dados de canal em integração</p>
          <p className="text-xs text-orange-600 max-w-md">
            O perfil de compradores e métricas de canal da Shopee serão disponibilizados assim que o endpoint da API for integrado.
            Os dados de GMV e pedidos já estão disponíveis na visão Gerencial.
          </p>
        </div>
      )}

      {/* ── Comparativo entre Canais: Ads, Custo e Frete (Gate 2) ── */}
      <div id="comparativo" className="scroll-mt-24 bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-violet-50">
          <h2 className="text-sm font-semibold text-slate-700">Comparativo entre Canais — Ads, Custo e Frete</h2>
          <p className="text-xs text-slate-400 mt-0.5">
            Marca × marketplace — mesmas métricas já validadas na aba Financeiro, lado a lado para comparar oportunidades.
            Não inclui desconto nem comissão de afiliados — afiliados aparecem em bloco próprio, por competência mensal (ver docs/sections/canais_audit.md, seção 14).
          </p>
        </div>
        <TableScrollHint>
          <table className="w-full text-sm" aria-label="Comparativo entre canais">
            <thead>
              <tr className="bg-slate-50">
                <SortableHeader label="Marca" column="brand" sort={channelMatrixSort.sort} onSort={channelMatrixSort.toggleSort} align="left" />
                <SortableHeader label="Canal" column="channel" sort={channelMatrixSort.sort} onSort={channelMatrixSort.toggleSort} align="left" />
                <SortableHeader label="GMV" column="gmv" sort={channelMatrixSort.sort} onSort={channelMatrixSort.toggleSort} />
                <SortableHeader label="Pedidos" column="orders" sort={channelMatrixSort.sort} onSort={channelMatrixSort.toggleSort} />
                <SortableHeader label="Ads/GMV" column="ads_gmv_pct" sort={channelMatrixSort.sort} onSort={channelMatrixSort.toggleSort} />
                <SortableHeader label="ROAS" column="roas" sort={channelMatrixSort.sort} onSort={channelMatrixSort.toggleSort} />
                <SortableHeader label="ACOS" column="acos_pct" sort={channelMatrixSort.sort} onSort={channelMatrixSort.toggleSort} />
                <SortableHeader label="Custo marketplace/GMV" column="marketplace_cost_pct" sort={channelMatrixSort.sort} onSort={channelMatrixSort.toggleSort} />
                <SortableHeader label="Frete seller/GMV" column="seller_shipping_pct" sort={channelMatrixSort.sort} onSort={channelMatrixSort.toggleSort} />
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Sinal</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Detalhe</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {!dataIsFresh ? (
                <SkeletonTableRows rows={5} cols={11} />
              ) : channelMatrixSort.sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={11} className="px-6 py-8 text-center text-sm text-slate-400">
                    {isLive
                      ? "Sem dados de canal no período e filtros selecionados."
                      : "Comparativo disponível apenas com a API conectada — o modo demonstração não modela Ads/Custo/Frete por canal."}
                  </td>
                </tr>
              ) : (
                channelMatrixSort.sortedRows.map((row, i) => (
                  <tr key={`${row.brand}-${row.channel}`} className={`hover:bg-violet-50/50 transition-colors ${i % 2 === 0 ? "" : "bg-gray-50/30"}`}>
                    <td className="px-6 py-3.5 font-semibold text-slate-700 whitespace-nowrap">{row.label}</td>
                    <td className="px-4 py-3.5 whitespace-nowrap">
                      <span className={`text-xs font-semibold px-1.5 py-0.5 rounded ${CHANNEL_BADGE_TONE[row.channel] ?? ""}`}>
                        {row.channel_label}
                      </span>
                    </td>
                    <td className="px-4 py-3.5 text-right tabular-nums text-slate-700 font-medium">{fmtBrl(row.gmv)}</td>
                    <td className="px-4 py-3.5 text-right tabular-nums text-slate-500">{fmtNumber(row.orders)}</td>
                    <td className="px-4 py-3.5 text-right tabular-nums">
                      <ChannelMetricCell value={row.ads_gmv_pct} applicable={row.ads_applicable} available={row.ads_available} format={fmtPct1} />
                    </td>
                    <td className="px-4 py-3.5 text-right tabular-nums">
                      <ChannelMetricCell value={row.roas} applicable={row.ads_applicable} available={row.ads_available} format={fmtRoas} />
                    </td>
                    <td className="px-4 py-3.5 text-right tabular-nums">
                      <ChannelMetricCell value={row.acos_pct} applicable={row.ads_applicable} available={row.ads_available} format={fmtPct1} />
                    </td>
                    <td className="px-4 py-3.5 text-right tabular-nums">
                      <ChannelMetricCell
                        value={row.marketplace_cost_pct}
                        applicable={row.marketplace_cost_applicable}
                        available={row.marketplace_cost_available}
                        format={fmtPct1}
                        warning={row.data_warning}
                      />
                    </td>
                    <td className="px-4 py-3.5 text-right tabular-nums">
                      <ChannelMetricCell value={row.seller_shipping_pct} applicable={row.seller_shipping_applicable} available={row.seller_shipping_available} format={fmtPct1} />
                    </td>
                    <td className="px-4 py-3.5">
                      <div className="flex flex-wrap gap-1">
                        {row.signals.length === 0 ? (
                          <span className="text-slate-300 text-xs">—</span>
                        ) : (
                          row.signals.map((s) => (
                            <span key={s} className={`text-xs px-1.5 py-0.5 rounded-full font-semibold whitespace-nowrap ${signalTone(s)}`}>
                              {signalLabel(s)}
                            </span>
                          ))
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3.5">
                      <button
                        type="button"
                        onClick={() => setDetailRow(row)}
                        aria-haspopup="dialog"
                        aria-label={`Ver detalhes de ${row.label} no ${row.channel_label}`}
                        className="inline-flex items-center justify-center min-h-11 min-w-11 text-xs font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded whitespace-nowrap"
                      >
                        Detalhe
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </TableScrollHint>
        <div className="px-6 py-3 border-t border-slate-100 flex flex-wrap items-center gap-x-5 gap-y-1.5">
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-widest">Legenda:</span>
          <span className="text-xs text-slate-500">N/A = não se aplica a esse canal</span>
          <span className="text-xs text-amber-700 font-medium">Sem dado = deveria existir, mas está ausente hoje</span>
          <span className="text-xs text-slate-400">— = denominador zero ou métrica não calculável no período (ex.: Ads/GMV quando GMV = 0)</span>
          <span className="ml-auto text-xs text-slate-400 max-w-md text-right">
            Sinais comparam cada marca contra a mediana/percentil 75 das marcas do mesmo canal no período — nunca incluem desconto ou afiliados.
          </span>
        </div>
      </div>

      {/* ── Impacto de afiliados no resultado (UE3, contrato §23) ──
          Bloco SEPARADO da matriz acima de proposito: o grao e' competencia
          mensal x marca, nao dia, e os tres lancamentos nao se somam — nao
          cabem como colunas da comparacao diaria por canal. */}
      {/* `key={requestKey}`: na troca de filtro o painel REMONTA, e o estado
          local do diálogo volta a `false`. Sem isso, um diálogo aberto durante
          a troca continuaria aberto e reapareceria com o payload novo — ou,
          pior, com o antigo ainda em tela. Nao altera o shell
          `KpiDrilldownDialog`, que segue generico. */}
      <div id="afiliados" className="scroll-mt-24">
        <AffiliateCostsPanel
          key={requestKey}
          block={displayAffiliateCosts}
          phase={affiliatePhase}
        />
      </div>

      {/* ── Tabela: Mix do GMV de conteudo do TikTok por marca ── */}
      {showTiktok && (
        <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
          <div className="px-6 py-4 border-b border-violet-50 flex items-start justify-between">
            <div>
              <h2 className="text-sm font-semibold text-slate-700">Mix do GMV de conteúdo por marca</h2>
              <p className="text-xs text-slate-500 mt-0.5">
                Vídeo, live e card/vitrine são composição interna da base de conteúdo de cada marca — não participação nas
                vendas totais.
              </p>
            </div>
            <div className="flex items-center gap-3 text-xs font-semibold text-slate-400 uppercase tracking-widest shrink-0">
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-violet-500 inline-block" /> Video</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-cyan-500 inline-block" /> Live</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> Card</span>
            </div>
          </div>
          <TableScrollHint>
            <table
              className="w-full text-sm"
              aria-label="Mix do GMV de conteúdo por marca — vídeo, live e card/vitrine como composição interna da base de conteúdo"
            >
              <thead>
                <tr className="bg-slate-50">
                  <SortableHeader label="Marca" column="brand" sort={tkSort.sort} onSort={tkSort.toggleSort} align="left" />
                  <SortableHeader label="GMV comercial" column="gmv" sort={tkSort.sort} onSort={tkSort.toggleSort} />
                  <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">
                    Share da marca no GMV comercial
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">
                    Mix de conteúdo
                  </th>
                  <SortableHeader label="Vídeo % da base" column="video_pct" sort={tkSort.sort} onSort={tkSort.toggleSort} />
                  <SortableHeader label="Live % da base" column="live_pct" sort={tkSort.sort} onSort={tkSort.toggleSort} />
                  <SortableHeader label="Card % da base" column="card_pct" sort={tkSort.sort} onSort={tkSort.toggleSort} />
                  <SortableHeader label="Visitantes" column="visitors" sort={tkSort.sort} onSort={tkSort.toggleSort} />
                  <SortableHeader label="Conversao" column="conversion" sort={tkSort.sort} onSort={tkSort.toggleSort} />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {!dataIsFresh ? (
                  <SkeletonTableRows rows={4} cols={9} />
                ) : (
                  <>
                    {tkSort.sortedRows.map((b, i) => {
                      // Mix vem do backend; `dom` e' null quando nao ha base
                      // valida — nunca rotula "Video" sobre ausencia de dado.
                      const mix = contentMixFromApi(b);
                      const dom = dominantContentOrigin(mix);
                      const partPct = tkGmvTotal > 0 ? ((b.tiktok_gmv ?? 0) / tkGmvTotal) * 100 : null;
                      return (
                        <tr key={b.brand} className={`hover:bg-violet-50/50 transition-colors ${i % 2 === 0 ? "" : "bg-gray-50/30"}`}>
                          <td className="px-6 py-3.5 font-semibold whitespace-nowrap">
                            <Link
                              href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=tiktok`)}
                              className="text-slate-700 hover:text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
                            >
                              {b.label}
                            </Link>
                          </td>
                          <td className="px-4 py-3.5 text-right tabular-nums text-slate-700 font-medium">{fmtBrl(b.tiktok_gmv!)}</td>
                          <td className="px-4 py-3.5 text-right tabular-nums">
                            <span className="text-slate-500 text-xs">{formatContentPctBr(partPct)}</span>
                          </td>
                          <td className="px-4 py-3.5">
                            <div className="flex flex-col gap-1">
                              <ContentMixBar
                                mix={mix}
                                gmvVideo={b.tiktok_gmv_video}
                                gmvLive={b.tiktok_gmv_live}
                                gmvCard={b.tiktok_gmv_card}
                              />
                              {mix.base != null && (
                                <span className="text-xs text-slate-500 tabular-nums whitespace-nowrap">
                                  Base {fmtBrl(mix.base)}
                                </span>
                              )}
                              {/* Sublinha separada: diagnostico de reconciliacao,
                                * fora da barra de composicao de proposito. */}
                              <span className="text-xs text-slate-400 tabular-nums whitespace-nowrap">
                                Dif. base × GMV comercial: {formatDivergenceBr(mix.divergencePct)}
                              </span>
                            </div>
                          </td>
                          <td className="px-4 py-3.5 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${dom === "video" ? CHANNEL_STYLE.video : DIM_STYLE}`}>
                              {formatContentPctBr(b.tiktok_video_pct)}
                            </span>
                          </td>
                          <td className="px-4 py-3.5 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${dom === "live" ? CHANNEL_STYLE.live : DIM_STYLE}`}>
                              {formatContentPctBr(b.tiktok_live_pct)}
                            </span>
                          </td>
                          <td className="px-4 py-3.5 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${dom === "card" ? CHANNEL_STYLE.card : DIM_STYLE}`}>
                              {formatContentPctBr(b.tiktok_card_pct)}
                            </span>
                          </td>
                          <td className="px-4 py-3.5 text-right tabular-nums text-slate-500">
                            {b.tiktok_visitors != null ? fmtNumber(b.tiktok_visitors) : "—"}
                          </td>
                          <td className="px-4 py-3.5 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${convRateStyle(b.tiktok_conversion_rate)}`}>
                              {formatContentPctBr(b.tiktok_conversion_rate)}
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
                          <div className="flex flex-col gap-1">
                            <ContentMixBar
                              mix={tkMixTotal}
                              gmvVideo={tkVidTotal}
                              gmvLive={tkLiveTotal}
                              gmvCard={tkCardTotal}
                            />
                            {tkMixTotal.base != null && (
                              <span className="text-xs text-slate-500 tabular-nums whitespace-nowrap">
                                Base {fmtBrl(tkMixTotal.base)}
                              </span>
                            )}
                            <span className="text-xs text-slate-400 tabular-nums whitespace-nowrap">
                              Dif. base × GMV comercial: {formatDivergenceBr(tkMixTotal.divergencePct)}
                            </span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums text-violet-700 text-xs font-bold">{formatContentPctBr(tkMixTotal.videoPct)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-cyan-700 text-xs font-bold">{formatContentPctBr(tkMixTotal.livePct)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-amber-700 text-xs font-bold">{formatContentPctBr(tkMixTotal.cardPct)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-slate-500 text-xs">{fmtNumber(tkVisitorsTotal)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-xs font-semibold text-slate-600">
                          {formatContentPctBr(displayKpis?.tiktok_conversion_rate)}
                        </td>
                      </tr>
                    )}
                  </>
                )}
              </tbody>
            </table>
          </TableScrollHint>
          <div className="px-6 py-3 border-t border-slate-100 flex items-center gap-5 flex-wrap">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-widest">Dominante:</span>
            <span className="flex items-center gap-1.5 text-xs text-violet-700"><span className="w-2 h-2 rounded-full bg-violet-500 inline-block" /> Video</span>
            <span className="flex items-center gap-1.5 text-xs text-cyan-700"><span className="w-2 h-2 rounded-full bg-cyan-500 inline-block" /> Live</span>
            <span className="flex items-center gap-1.5 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> Card</span>
            <span className="ml-auto text-xs text-slate-400">
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
          <TableScrollHint>
            <table className="w-full text-sm" aria-label="Perfil de compradores ML por marca">
              <thead>
                <tr className="bg-slate-50">
                  <SortableHeader label="Marca" column="brand" sort={mlSort.sort} onSort={mlSort.toggleSort} align="left" />
                  <SortableHeader label="GMV" column="gmv" sort={mlSort.sort} onSort={mlSort.toggleSort} />
                  <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Part.%</th>
                  <SortableHeader label="Compradores" column="buyers" sort={mlSort.sort} onSort={mlSort.toggleSort} />
                  <SortableHeader label="Novos %" column="new_pct" sort={mlSort.sort} onSort={mlSort.toggleSort} />
                  <SortableHeader label="Novos" column="new" sort={mlSort.sort} onSort={mlSort.toggleSort} />
                  <SortableHeader label="Recorrentes" column="repeat" sort={mlSort.sort} onSort={mlSort.toggleSort} />
                  <SortableHeader label="Recompra %" column="repeat_pct" sort={mlSort.sort} onSort={mlSort.toggleSort} />
                  <SortableHeader label="GMV / Comprador" column="gmv_per_buyer" sort={mlSort.sort} onSort={mlSort.toggleSort} />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {!dataIsFresh ? (
                  <SkeletonTableRows rows={3} cols={9} />
                ) : (
                  <>
                    {mlSort.sortedRows.map((b, i) => {
                      const partPct = mlGmvTotal > 0 ? ((b.ml_gmv ?? 0) / mlGmvTotal) * 100 : 0;
                      const newPct = b.ml_new_buyer_pct ??
                        (b.ml_unique_buyers && b.ml_new_buyers ? (b.ml_new_buyers / b.ml_unique_buyers) * 100 : null);
                      return (
                        <tr key={b.brand} className={`hover:bg-violet-50/50 transition-colors ${i % 2 === 0 ? "" : "bg-gray-50/30"}`}>
                          <td className="px-6 py-3.5 font-semibold whitespace-nowrap">
                            <Link
                              href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=ml`)}
                              className="text-slate-700 hover:text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
                            >
                              {b.label}
                            </Link>
                          </td>
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
          </TableScrollHint>
          <div className="px-6 py-3 border-t border-slate-100 flex items-start gap-5 flex-wrap">
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-xs font-semibold text-slate-500 uppercase tracking-widest">Novos %:</span>
                <span className="flex items-center gap-1 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> acima de 85%</span>
                <span className="flex items-center gap-1 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> 70–85%</span>
                <span className="flex items-center gap-1 text-xs text-rose-700"><span className="w-2 h-2 rounded-full bg-rose-400 inline-block" /> abaixo de 70%</span>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-xs font-semibold text-slate-500 uppercase tracking-widest">Recompra %:</span>
                <span className="flex items-center gap-1 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> acima de 15%</span>
                <span className="flex items-center gap-1 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> 8–15%</span>
                <span className="flex items-center gap-1 text-xs text-rose-700"><span className="w-2 h-2 rounded-full bg-rose-400 inline-block" /> abaixo de 8%</span>
              </div>
            </div>
            <p className="ml-auto text-xs text-slate-400 self-end">
              Recompra = compradores com historico previo na marca no ML · "Compradores" é soma diária de compradores únicos por dia — pode contar a mesma pessoa mais de uma vez em dias diferentes; não é comprador único do período selecionado
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
          <TableScrollHint>
            <table className="w-full text-sm" aria-label="Perfil de compradores Shopee por marca">
              <thead>
                <tr className="bg-slate-50">
                  <SortableHeader label="Marca" column="brand" sort={shSort.sort} onSort={shSort.toggleSort} align="left" />
                  <SortableHeader label="GMV" column="gmv" sort={shSort.sort} onSort={shSort.toggleSort} />
                  <th className="px-4 py-3 text-right text-xs font-semibold text-slate-600 uppercase tracking-wider">Part.%</th>
                  <SortableHeader label="Compradores" column="buyers" sort={shSort.sort} onSort={shSort.toggleSort} />
                  <SortableHeader label="Novos %" column="new_pct" sort={shSort.sort} onSort={shSort.toggleSort} />
                  <SortableHeader label="Novos" column="new" sort={shSort.sort} onSort={shSort.toggleSort} />
                  <SortableHeader label="Recorrentes" column="repeat" sort={shSort.sort} onSort={shSort.toggleSort} />
                  <SortableHeader label="Recompra %" column="repeat_pct" sort={shSort.sort} onSort={shSort.toggleSort} />
                  <SortableHeader label="GMV / Comprador" column="gmv_per_buyer" sort={shSort.sort} onSort={shSort.toggleSort} />
                  <SortableHeader label="Visitantes" column="visitors" sort={shSort.sort} onSort={shSort.toggleSort} />
                  <SortableHeader label="Conversão" column="conversion" sort={shSort.sort} onSort={shSort.toggleSort} />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {!dataIsFresh ? (
                  <SkeletonTableRows rows={3} cols={11} />
                ) : (
                  <>
                    {shSort.sortedRows.map((b, i) => {
                      const partPct = shGmvTotal > 0 ? ((b.shopee_gmv ?? 0) / shGmvTotal) * 100 : 0;
                      const newPct = b.shopee_new_buyer_pct ??
                        (b.shopee_unique_buyers && b.shopee_new_buyers ? (b.shopee_new_buyers / b.shopee_unique_buyers) * 100 : null);
                      return (
                        <tr key={b.brand} className={`hover:bg-orange-50/40 transition-colors ${i % 2 === 0 ? "" : "bg-gray-50/30"}`}>
                          <td className="px-6 py-3.5 font-semibold whitespace-nowrap">
                            <Link
                              href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=shopee`)}
                              className="text-slate-700 hover:text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
                            >
                              {b.label}
                            </Link>
                          </td>
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
          </TableScrollHint>
          <div className="px-6 py-3 border-t border-slate-100 flex items-start gap-5 flex-wrap">
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-xs font-semibold text-slate-500 uppercase tracking-widest">Novos %:</span>
                <span className="flex items-center gap-1 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> acima de 85%</span>
                <span className="flex items-center gap-1 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> 70–85%</span>
                <span className="flex items-center gap-1 text-xs text-rose-700"><span className="w-2 h-2 rounded-full bg-rose-400 inline-block" /> abaixo de 70%</span>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-xs font-semibold text-slate-500 uppercase tracking-widest">Recompra %:</span>
                <span className="flex items-center gap-1 text-xs text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> acima de 15%</span>
                <span className="flex items-center gap-1 text-xs text-amber-700"><span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> 8–15%</span>
                <span className="flex items-center gap-1 text-xs text-rose-700"><span className="w-2 h-2 rounded-full bg-rose-400 inline-block" /> abaixo de 8%</span>
              </div>
            </div>
            <p className="ml-auto text-xs text-slate-400 self-end">
              Recompra = compradores com historico previo na marca na Shopee · "Compradores" é soma diária de compradores únicos por dia — pode contar a mesma pessoa mais de uma vez em dias diferentes; não é comprador único do período selecionado
            </p>
          </div>
        </div>
      )}

      {/* ── Insight: ML como canal de aquisicao ── */}
      {showMl && dataIsFresh && mlBrands.length > 0 && mlNewPctTotal >= 80 && (
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
      {showShopee && hasShopeeData && dataIsFresh && shBrands.length > 0 && shNewPctTotal >= 80 && (
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

      <KpiDrilldownDialog
        open={dataIsFresh && detailRow != null}
        onClose={() => setDetailRow(null)}
        title={detailRow ? `${detailRow.label} · ${detailRow.channel_label}` : ""}
      >
        {dataIsFresh && detailRow && (
          <ChannelComparisonDialogContent
            row={detailRow}
            median={findChannelMedian(displayChannelMedians, detailRow.channel)}
            periodLabel={periodLabel}
            refreshedAt={refreshedAt}
            buildHref={buildHref}
          />
        )}
      </KpiDrilldownDialog>
    </PageContainer>
  );
}

export default function CanaisPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#f8f7ff]" />}>
      <CanaisPageInner />
    </Suspense>
  );
}
