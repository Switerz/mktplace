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
  type TikTokOrderDiscountsBlock,
} from "@/lib/api-client";
import { isMarketplaceSelected } from "@/lib/marketplace-filter";
import { useGlobalFilters } from "@/hooks/useGlobalFilters";
import { mergeFilteredHref } from "@/lib/filters/nav-links";
import { Store } from "lucide-react";
import StatCard from "@/components/ui/StatCard";
import Freshness, { PeriodChip } from "@/components/ui/Freshness";
import Note from "@/components/ui/Note";
import Segmented from "@/components/ui/Segmented";
import StateBlock, { RetryButton } from "@/components/ui/StateBlock";
import ChannelOverview, { type CanalId, type ColunaCanal } from "@/components/canais/ChannelOverview";
import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import ChannelComparisonDialogContent from "@/components/ChannelComparisonDialogContent";
import AffiliateCostsPanel from "@/components/AffiliateCostsPanel";
import TiktokOrderDiscountsPanel from "@/components/TiktokOrderDiscountsPanel";
import { resolveBlockPhase } from "@/lib/canais-affiliate-costs";
import { SkeletonTableRows } from "@/components/Skeleton";
import GlobalFilterControls from "@/components/layout/GlobalFilterControls";
import InfoDica from "@/components/ui/InfoDica";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { fmtPeriodo, mockLimitationNote } from "@/lib/filters/format";
import { detectPreset } from "@/lib/filters/presets";
import { useSortableTable } from "@/lib/use-sortable-table";
import SortHeader from "@/components/ui/SortHeader";
import TableScrollHint from "@/components/TableScrollHint";
import {
  formatChannelMetric,
  signalLabel,
  signalTone,
  findChannelMedian,
  CHANNEL_BADGE_TONE,
  CHANNEL_LABEL,
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
  if (tone === "value") return "text-ink-muted font-medium";
  if (tone === "warning") return "text-warn-ink bg-warn-soft rounded px-1.5 py-0.5 text-xs font-semibold";
  return "text-ink-faint text-xs italic";
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
    return <span className="text-xs text-ink-faint">{MIX_UNAVAILABLE_LABEL}</span>;
  }
  const w = contentMixWeights(gmvVideo, gmvLive, gmvCard);
  const label =
    `Mix do GMV de conteúdo sobre base de ${fmtBrl(mix.base)}: ` +
    `vídeo ${formatContentPctBr(mix.videoPct)}, live ${formatContentPctBr(mix.livePct)}, card/vitrine ${formatContentPctBr(mix.cardPct)}.`;
  return (
    <div
      className="flex h-2 rounded-full overflow-hidden w-28 bg-raised"
      role="img"
      aria-label={label}
      title={label}
    >
      {/* Composicao: cor de SERIE por origem, nunca ok/warn/crit. */}
      <div className={`${MIX_SERIE.video} transition-all`} style={{ flexGrow: w.video }} />
      <div className={`${MIX_SERIE.live} transition-all`} style={{ flexGrow: w.live }} />
      <div className={`${MIX_SERIE.card} transition-all`} style={{ flexGrow: w.card }} />
    </div>
  );
}

/** UX-TORRE-2: serie por origem do conteudo. Card era ambar — a mesma cor de
 * "atencao" — sem nenhuma regra de negocio por tras. */
const MIX_SERIE = {
  video: "bg-chart-1",
  live: "bg-chart-5",
  card: "bg-chart-6",
};

/** Origem dominante: destaque NEUTRO (relevo + peso), igual para as tres —
 * ser dominante nao e' bom nem ruim. */
const DOMINANT_STYLE = "text-ink bg-raised ring-1 ring-inset ring-line-strong font-bold";
const CHANNEL_STYLE = {
  video: DOMINANT_STYLE,
  live: DOMINANT_STYLE,
  card: DOMINANT_STYLE,
};
const DIM_STYLE = "text-ink-faint";

function convRateStyle(v: number | null): string {
  if (v == null) return DIM_STYLE;
  if (v >= 4) return "text-ok-ink bg-ok-soft font-semibold";
  if (v >= 2) return "text-warn-ink bg-warn-soft font-semibold";
  return "text-crit-ink bg-crit-soft font-semibold";
}

function repeatRateStyle(v: number | null): string {
  if (v == null) return DIM_STYLE;
  if (v >= 15) return "text-ok-ink bg-ok-soft font-semibold";
  if (v >= 8) return "text-warn-ink bg-warn-soft font-semibold";
  return "text-crit-ink bg-crit-soft font-semibold";
}

/** Faixas de cor das colunas e a ressalva de "Compradores" (soma diaria), no ⓘ
 * das tabelas de perfil de compradores ML e Shopee. */
const LEGENDA_COMPRADORES =
  "Novos %: verde ≥85, amarelo 70–85, vermelho <70 · Recompra %: verde ≥15, amarelo 8–15, vermelho <8. " +
  "Compradores = soma diária, não comprador único do período.";

function newBuyerPctStyle(v: number | null): string {
  if (v == null) return DIM_STYLE;
  if (v >= 85) return "text-ok-ink bg-ok-soft font-semibold";
  if (v >= 70) return "text-warn-ink bg-warn-soft font-semibold";
  return "text-crit-ink bg-crit-soft font-semibold";
}

/** Anchors internos compactos (Task 3) — sempre reais (`href="#id"`), nunca
 * um estado paralelo de navegacao. So aparecem para canais atualmente
 * selecionados/visiveis. */
function InternalNav({
  showTiktok, showMl, showShopee, onEscolherAba,
}: {
  showTiktok: boolean; showMl: boolean; showShopee: boolean;
  /** O link de canal escolhe a aba direto: se a aba foi trocada a mao, a
   * ancora pode ja' ser a atual e o `hashchange` nao dispararia. */
  onEscolherAba: (aba: CanalId) => void;
}) {
  // UX-TORRE-B2B-1: alvo de 44px explicito e a ordem nova da pagina. As
  // ancoras de canal continuam: elas escolhem a aba do "Detalhe por canal".
  const linkClass = "inline-flex min-h-11 items-center px-2.5 rounded-lg text-xs font-semibold text-accent hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";
  return (
    <nav aria-label="Navegação interna da página" className="flex flex-wrap items-center gap-1 -mx-2.5">
      <a href="#comparativo-canais" className={linkClass}>Lado a lado</a>
      <a href="#comparativo" className={linkClass}>Marca × canal</a>
      <a href="#afiliados" className={linkClass}>Afiliados</a>
      <a href="#descontos-tiktok" className={linkClass}>Descontos TikTok</a>
      <span aria-hidden="true" className="mx-1 h-4 w-px bg-line-strong" />
      {showTiktok && <a href="#tiktok-shop" onClick={() => onEscolherAba("tiktok")} className={linkClass}>TikTok Shop</a>}
      {showMl && <a href="#mercado-livre" onClick={() => onEscolherAba("ml")} className={linkClass}>Mercado Livre</a>}
      {showShopee && <a href="#shopee" onClick={() => onEscolherAba("shopee")} className={linkClass}>Shopee</a>}
    </nav>
  );
}

/**
 * Espelho do contrato de aplicabilidade por canal do backend
 * (`_ADS_APPLICABLE`, `_COST_APPLICABLE`, `_SHIPPING_APPLICABLE` em
 * performance_service.py, Gate 1 do canais_audit §14.2). "Aplicavel" e'
 * modelo de negocio do canal — nao depende de haver linha no periodo.
 */
const APLICAVEL_POR_CANAL: Record<CanalId, { ads: boolean; custo: boolean; frete: boolean }> = {
  tiktok: { ads: false, custo: true, frete: false },
  ml: { ads: true, custo: true, frete: true },
  shopee: { ads: true, custo: true, frete: true },
};

/** Ancora de canal -> aba do detalhe. */
const ABA_DA_ANCORA: Record<string, CanalId> = {
  "#tiktok-shop": "tiktok",
  "#mercado-livre": "ml",
  "#shopee": "shopee",
};

function CanaisPageInner() {
  const [filters, setFilters] = useGlobalFilters({ defaultPreset: "mes_anterior" });
  const searchParams = useSearchParams();
  const filter = filters.channels; // alias — preserva as referencias existentes abaixo
  const [kpis, setKpis] = useState<CanaisKpis | null>(null);
  const [brands, setBrands] = useState<CanaisBrandRow[]>([]);
  const [channelRows, setChannelRows] = useState<CanaisChannelRow[]>([]);
  const [channelMedians, setChannelMedians] = useState<CanaisChannelMedian[]>([]);
  const [affiliateCosts, setAffiliateCosts] = useState<AffiliateCostsBlock | null>(null);
  const [tiktokOrderDiscounts, setTiktokOrderDiscounts] =
    useState<TikTokOrderDiscountsBlock | null>(null);
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
        setTiktokOrderDiscounts(r.tiktokOrderDiscounts);
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
  // Mesma guarda de frescor: um desconto do filtro anterior exibido sob o
  // filtro novo seria pior que ausencia.
  const displayTiktokOrderDiscounts = dataIsFresh ? tiktokOrderDiscounts : null;
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

  // ── UX-TORRE-B2B-1: colunas do "Canais lado a lado" ──
  // GMV e pedidos = soma das linhas marca x canal (a MESMA base da matriz);
  // custo/Ads/frete = medianas que a API ja' entrega; compradores = KPIs de
  // canal que a pagina ja' exibia, no MESMO formato de antes.
  const canaisSelecionados: CanalId[] = [
    ...(showTiktok ? (["tiktok"] as const) : []),
    ...(showMl ? (["ml"] as const) : []),
    ...(showShopee ? (["shopee"] as const) : []),
  ];
  const colunasCanal: ColunaCanal[] = canaisSelecionados.map((canal) => {
    const linhas = displayChannelRows.filter((r) => r.channel === canal);
    const k = displayKpis;
    const compradores =
      canal === "tiktok"
        ? { novos: "N/D", recompra: "N/D", porComprador: "N/D", conversao: formatContentPctBr(k?.tiktok_conversion_rate) }
        : canal === "ml"
          ? {
              novos: fmtPct(k?.ml_new_buyer_pct ?? null),
              recompra: fmtPct(k?.ml_repeat_buyer_rate_pct ?? null),
              porComprador: k?.ml_gmv_per_buyer != null ? fmtBrl(k.ml_gmv_per_buyer) : "—",
              conversao: "N/D",
            }
          : {
              novos: fmtPct(k?.shopee_new_buyer_pct ?? null),
              recompra: fmtPct(k?.shopee_repeat_buyer_rate_pct ?? null),
              porComprador: k?.shopee_gmv_per_buyer != null ? fmtBrl(k.shopee_gmv_per_buyer) : "—",
              conversao: k?.shopee_conversion_rate != null ? fmtPct(k.shopee_conversion_rate) : "—",
            };
    // Canal sem NENHUMA linha no recorte: ausencia de medicao (null -> "Sem
    // dado"), nunca R$ 0. A aplicabilidade vem do contrato fixo por canal, e
    // nao das linhas — sem linhas, `some()` devolveria "N/A" onde o certo e'
    // "Sem dado".
    const temLinhas = linhas.length > 0;
    return {
      canal,
      rotulo: CHANNEL_LABEL[canal],
      gmv: temLinhas ? linhas.reduce((s, r) => s + (r.gmv ?? 0), 0) : null,
      pedidos: temLinhas ? linhas.reduce((s, r) => s + (r.orders ?? 0), 0) : null,
      mediana: findChannelMedian(displayChannelMedians, canal),
      aplicavel: APLICAVEL_POR_CANAL[canal],
      disponivel: {
        ads: linhas.some((r) => r.ads_available),
        custo: linhas.some((r) => r.marketplace_cost_available),
        frete: linhas.some((r) => r.seller_shipping_available),
      },
      novosPct: compradores.novos,
      recompraPct: compradores.recompra,
      gmvPorComprador: compradores.porComprador,
      conversao: compradores.conversao,
    };
  });

  // ── Detalhe por canal: uma aba por canal selecionado ──
  const abasDetalhe: CanalId[] = canaisSelecionados;
  const [abaEscolhida, setAba] = useState<CanalId>("tiktok");
  // A ancora (#tiktok-shop, #mercado-livre, #shopee) escolhe a aba — ao
  // carregar e ao clicar na navegacao interna.
  useEffect(() => {
    const aplicar = () => {
      const ancora = window.location.hash;
      const aba = ABA_DA_ANCORA[ancora];
      if (!aba) return;
      setAba(aba);
      // A secao da aba so' existe depois do render: o navegador ja' tentou
      // rolar e nao achou o alvo. Rola de novo quando ela montar.
      window.setTimeout(() => document.getElementById(ancora.slice(1))?.scrollIntoView({ block: "start" }), 0);
    };
    aplicar();
    window.addEventListener("hashchange", aplicar);
    return () => window.removeEventListener("hashchange", aplicar);
  }, []);
  // Link interno de canal: escolhe a aba e rola ate ela depois que monta.
  const escolherAbaPorLink = (aba: CanalId) => {
    setAba(aba);
    const ancora = Object.keys(ABA_DA_ANCORA).find((k) => ABA_DA_ANCORA[k] === aba);
    if (ancora) window.setTimeout(() => document.getElementById(ancora.slice(1))?.scrollIntoView({ block: "start" }), 0);
  };
  // Aba escolhida fora do filtro atual cai na primeira disponivel.
  const abaAtiva: CanalId = abasDetalhe.includes(abaEscolhida) ? abaEscolhida : abasDetalhe[0] ?? "tiktok";

  return (
    <PageContainer>
      <PageHeader
        title="Canais"
        icon={Store}
        meta={
          <>
            <PeriodChip label={periodLabel} />
            <Freshness live={dataIsFresh ? isLive : null} refreshedAt={dataIsFresh ? refreshedAt : null} loading={loading} />
          </>
        }
        filters={<GlobalFilterControls filters={filters} onChange={setFilters} hideCompare loading={loading} />}
      />

      {/* Navegacao interna compacta */}
      <InternalNav showTiktok={showTiktok} showMl={showMl} showShopee={showShopee} onEscolherAba={escolherAbaPorLink} />

      {dataIsFresh && (() => {
        const isCustomPeriod = detectPreset(filters.dateFrom, filters.dateTo) !== "mes_anterior";
        const note = mockLimitationNote(isLive, filters.brands, isCustomPeriod);
        return note && <Note tom="atencao">{note}</Note>;
      })()}

      {error && (
        <StateBlock
          tipo="erro"
          titulo="Erro de carregamento"
          descricao={error}
          acao={<RetryButton onClick={() => { setError(null); setRetryKey((k) => k + 1); }} />}
        />
      )}

      <span className="sr-only" aria-live="polite" aria-atomic="true">
        {loading ? "Carregando dados de canais..." : error ? "Falha ao carregar." : "Dados de canais carregados."}
      </span>

      {isEmpty && (
        <StateBlock
          tipo="vazio"
          titulo="Sem dados no período e filtros selecionados."
          descricao="Tente ampliar o intervalo de datas ou revisar canal/marca."
        />
      )}

      {/* ── Canais lado a lado (UX-TORRE-B2B-1) ── */}
      {/* Em modo demonstracao (API offline) o mock nao modela linhas marca x
          canal — a matriz abaixo ja' declara isso; aqui o painel nem aparece,
          em vez de mostrar colunas vazias. */}
      {!error && !isEmpty && colunasCanal.length > 0 && (!dataIsFresh || isLive) && (
        <ChannelOverview colunas={colunasCanal} fmtBrl={fmtBrl} fmtNumber={fmtNumber} carregando={!dataIsFresh} />
      )}

      {/* ── Comparativo entre Canais: Ads, Custo e Frete (Gate 2) ── */}
      <div id="comparativo" className="scroll-mt-24 bg-surface border border-line rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-line">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold text-ink-muted">
            Comparativo entre Canais — Ads, Custo e Frete
            <InfoDica texto="N/A = não se aplica ao canal · Sem dado = deveria existir, mas falta hoje · — = denominador zero ou métrica não calculável. Sem desconto nem afiliados." />
          </h2>
        </div>
        <TableScrollHint>
          <table className="w-full text-data" aria-label="Comparativo entre canais">
            <thead>
              <tr className="bg-raised">
                <SortHeader rotulo="Marca" coluna="brand" sort={channelMatrixSort.sort} aoOrdenar={channelMatrixSort.toggleSort} alinhamento="left" />
                <SortHeader rotulo="Canal" coluna="channel" sort={channelMatrixSort.sort} aoOrdenar={channelMatrixSort.toggleSort} alinhamento="left" />
                <SortHeader rotulo="GMV" coluna="gmv" sort={channelMatrixSort.sort} aoOrdenar={channelMatrixSort.toggleSort} />
                <SortHeader rotulo="Pedidos" coluna="orders" sort={channelMatrixSort.sort} aoOrdenar={channelMatrixSort.toggleSort} />
                <SortHeader rotulo="Ads/GMV" coluna="ads_gmv_pct" sort={channelMatrixSort.sort} aoOrdenar={channelMatrixSort.toggleSort} />
                <SortHeader rotulo="ROAS" coluna="roas" sort={channelMatrixSort.sort} aoOrdenar={channelMatrixSort.toggleSort} />
                <SortHeader rotulo="ACOS" coluna="acos_pct" sort={channelMatrixSort.sort} aoOrdenar={channelMatrixSort.toggleSort} />
                <SortHeader rotulo="Custo marketplace/GMV" coluna="marketplace_cost_pct" sort={channelMatrixSort.sort} aoOrdenar={channelMatrixSort.toggleSort} />
                <SortHeader rotulo="Frete seller/GMV" coluna="seller_shipping_pct" sort={channelMatrixSort.sort} aoOrdenar={channelMatrixSort.toggleSort} />
                <th className="px-4 py-3 text-left text-xs font-semibold text-ink-muted uppercase tracking-wider">Sinal</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-ink-muted uppercase tracking-wider">Detalhe</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {!dataIsFresh ? (
                <SkeletonTableRows rows={5} cols={11} />
              ) : channelMatrixSort.sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={11} className="px-6 py-8 text-center text-sm text-ink-faint">
                    {isLive
                      ? "Sem dados de canal no período e filtros selecionados."
                      : "Disponível só com a API conectada."}
                  </td>
                </tr>
              ) : (
                channelMatrixSort.sortedRows.map((row, i) => (
                  <tr key={`${row.brand}-${row.channel}`} className={`hover:bg-accent-soft/50 transition-colors ${i % 2 === 0 ? "" : "bg-raised/30"}`}>
                    <td className="px-4 py-2.5 font-semibold text-ink-muted whitespace-nowrap">{row.label}</td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <span className={`text-xs font-semibold px-1.5 py-0.5 rounded ${CHANNEL_BADGE_TONE[row.channel] ?? ""}`}>
                        {row.channel_label}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-ink-muted font-medium whitespace-nowrap">{fmtBrl(row.gmv)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-ink-faint">{fmtNumber(row.orders)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      <ChannelMetricCell value={row.ads_gmv_pct} applicable={row.ads_applicable} available={row.ads_available} format={fmtPct1} />
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      <ChannelMetricCell value={row.roas} applicable={row.ads_applicable} available={row.ads_available} format={fmtRoas} />
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      <ChannelMetricCell value={row.acos_pct} applicable={row.ads_applicable} available={row.ads_available} format={fmtPct1} />
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      <ChannelMetricCell
                        value={row.marketplace_cost_pct}
                        applicable={row.marketplace_cost_applicable}
                        available={row.marketplace_cost_available}
                        format={fmtPct1}
                        warning={row.data_warning}
                      />
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      <ChannelMetricCell value={row.seller_shipping_pct} applicable={row.seller_shipping_applicable} available={row.seller_shipping_available} format={fmtPct1} />
                    </td>
                    <td className="px-4 py-2.5 min-w-[15rem]">
                      <div className="flex flex-wrap gap-1">
                        {row.signals.length === 0 ? (
                          <span className="text-ink-faint text-xs">—</span>
                        ) : (
                          row.signals.map((s) => (
                            <span key={s} className={`text-xs px-1.5 py-0.5 rounded-full font-semibold whitespace-nowrap ${signalTone(s)}`}>
                              {signalLabel(s)}
                            </span>
                          ))
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-2.5">
                      <button
                        type="button"
                        onClick={() => setDetailRow(row)}
                        aria-haspopup="dialog"
                        aria-label={`Ver detalhes de ${row.label} no ${row.channel_label}`}
                        className="inline-flex items-center justify-center min-h-11 min-w-11 text-xs font-semibold text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded whitespace-nowrap"
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

      {/* ── Descontos e subsidios do pedido — TikTok Shop (UE8-I3, §28) ──
          Bloco SEPARADO do de afiliados: outra fonte, outro grao (dia x marca)
          e outro frescor. Os dois componentes tem financiadores diferentes e
          nao se somam, entao nao cabem como colunas de nenhuma tabela acima.
          `key={requestKey}`: na troca de filtro o painel REMONTA e o dialogo
          volta a fechado — sem isso, um dialogo aberto durante a troca
          reapareceria com o payload novo, ou pior, com o antigo em tela.
          Reusa `affiliatePhase`: a fase e' da REQUISICAO de /canais, uma so'
          para os dois blocos, e duplica-la abriria espaco para divergirem. */}
      <div id="descontos-tiktok" className="scroll-mt-24">
        <TiktokOrderDiscountsPanel
          key={requestKey}
          block={displayTiktokOrderDiscounts}
          phase={affiliatePhase}
        />
      </div>

      {/* ── Detalhe por canal (UX-TORRE-B2B-1) ──
          Os tres blocos por canal eram empilhados (~2.600px no desktop). Agora
          uma aba por canal SELECIONADO; o conteudo de cada aba e' o mesmo de
          antes, com as mesmas tabelas e totais. A ancora
          #tiktok-shop / #mercado-livre / #shopee continua funcionando: ela
          escolhe a aba ao carregar. */}
      {!isEmpty && abasDetalhe.length > 0 && (
        <section id="detalhe-canal" aria-labelledby="detalhe-canal-heading" className="scroll-mt-24 flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-2">
            <h2 id="detalhe-canal-heading" className="text-base font-semibold text-ink">Detalhe por canal</h2>
            {abasDetalhe.length > 1 && (
              <Segmented
                rotulo="Canal do detalhe"
                valor={abaAtiva}
                aoEscolher={setAba}
                opcoes={abasDetalhe.map((a) => ({ valor: a, texto: CHANNEL_LABEL[a] }))}
              />
            )}
          </div>

          {abaAtiva === "tiktok" && (
            <>
      {/* ── Resumo TikTok ── */}
      {showTiktok && (
        <section id="tiktok-shop" aria-labelledby="tiktok-shop-heading" className="scroll-mt-24 flex flex-col gap-3" aria-busy={!dataIsFresh}>
          <h2 id="tiktok-shop-heading" className="flex items-center gap-1.5 text-sm font-semibold text-ink-muted">
            Mix do GMV de conteúdo do TikTok
            <InfoDica texto="% da base de conteúdo (vídeo + live + card/vitrine). Não representa participação nas vendas totais." />
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {!dataIsFresh ? (
              <><StatCard rotulo="" valor="" carregando /><StatCard rotulo="" valor="" carregando /><StatCard rotulo="" valor="" carregando /><StatCard rotulo="" valor="" carregando /></>
            ) : (
              <>
                <StatCard
                  rotulo="Vídeo — % da base de conteúdo"
                  valor={formatContentPctBr(displayKpis?.tiktok_video_pct)}
                  contexto={displayKpis?.tiktok_gmv_video != null ? fmtBrl(displayKpis.tiktok_gmv_video) : undefined}
                  canal={MIX_SERIE.video}
                />
                <StatCard
                  rotulo="Live — % da base de conteúdo"
                  valor={formatContentPctBr(displayKpis?.tiktok_live_pct)}
                  contexto={displayKpis?.tiktok_gmv_live != null ? fmtBrl(displayKpis.tiktok_gmv_live) : undefined}
                  canal={MIX_SERIE.live}
                />
                <StatCard
                  rotulo="Card/vitrine — % da base de conteúdo"
                  valor={formatContentPctBr(displayKpis?.tiktok_card_pct)}
                  contexto={displayKpis?.tiktok_gmv_card != null ? fmtBrl(displayKpis.tiktok_gmv_card) : undefined}
                  canal={MIX_SERIE.card}
                />
                <StatCard
                  rotulo="Conversao TikTok"
                  valor={formatContentPctBr(displayKpis?.tiktok_conversion_rate)}
                  contexto={displayKpis?.tiktok_customers != null ? `${fmtNumber(displayKpis.tiktok_customers)} compradores (soma diária)` : undefined}
                  canal="bg-canal-tiktok"
                />
              </>
            )}
          </div>
          {/* Faixa de reconciliacao — diagnostico NEUTRO entre duas linhagens.
            * Fica FORA da barra de composicao de proposito: a divergencia nao e'
            * um quarto segmento do mix. Sem cor de julgamento (positivo nao e'
            * "bom", negativo nao e' "ruim") e sem a palavra cobertura/share. */}
          {dataIsFresh && (
            <div className="bg-raised border border-line rounded-xl px-4 py-3 flex flex-wrap items-baseline gap-x-6 gap-y-2">
              <div className="flex items-baseline gap-2">
                <span className="text-xs text-ink-faint">Base de conteúdo:</span>
                <span className="text-xs font-semibold tabular-nums text-ink-muted">
                  {tkMixTotal.base != null ? fmtBrl(tkMixTotal.base) : "N/D"}
                </span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-xs text-ink-faint">GMV comercial TikTok:</span>
                <span className="text-xs font-semibold tabular-nums text-ink-muted">
                  {displayKpis?.tiktok_gmv != null ? fmtBrl(displayKpis.tiktok_gmv) : "N/D"}
                </span>
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-xs text-ink-faint">Diferença entre base de conteúdo e GMV comercial:</span>
                <span className="text-xs font-semibold tabular-nums text-ink-muted">
                  {formatDivergenceBr(tkMixTotal.divergencePct)}
                </span>
                {/* A ressalva de linhagem muda a leitura da diferenca: fica no ⓘ. */}
                <InfoDica
                  className="self-center"
                  texto="As medidas têm linhagens diferentes. A diferença é diagnóstico de reconciliação — não é cobertura, participação, margem nem severidade."
                />
              </div>
            </div>
          )}
        </section>
      )}

      {/* ── Tabela: Mix do GMV de conteudo do TikTok por marca ── */}
      {showTiktok && (
        <div className="bg-surface border border-line rounded-xl overflow-hidden">
          <div className="px-6 py-4 border-b border-line flex items-start justify-between">
            <h2 className="flex items-center gap-1.5 text-sm font-semibold text-ink-muted">
              Mix do GMV de conteúdo por marca
              <InfoDica texto="Conversão só nos dias com dado de visitantes (limite da API TikTok)." />
            </h2>
            <div className="flex items-center gap-3 text-xs font-semibold text-ink-faint uppercase tracking-widest shrink-0">
              <span className="flex items-center gap-1"><span className={`w-2 h-2 rounded-full ${MIX_SERIE.video} inline-block`} /> Video</span>
              <span className="flex items-center gap-1"><span className={`w-2 h-2 rounded-full ${MIX_SERIE.live} inline-block`} /> Live</span>
              <span className="flex items-center gap-1"><span className={`w-2 h-2 rounded-full ${MIX_SERIE.card} inline-block`} /> Card</span>
            </div>
          </div>
          <TableScrollHint>
            <table
              className="w-full text-data"
              aria-label="Mix do GMV de conteúdo por marca — vídeo, live e card/vitrine como composição interna da base de conteúdo"
            >
              <thead>
                <tr className="bg-raised">
                  <SortHeader rotulo="Marca" coluna="brand" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} alinhamento="left" />
                  <SortHeader rotulo="GMV comercial" coluna="gmv" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                  <th className="px-4 py-3 text-right text-xs font-semibold text-ink-muted uppercase tracking-wider">
                    Share da marca no GMV comercial
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-ink-muted uppercase tracking-wider">
                    Mix de conteúdo
                  </th>
                  <SortHeader rotulo="Vídeo % da base" coluna="video_pct" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                  <SortHeader rotulo="Live % da base" coluna="live_pct" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                  <SortHeader rotulo="Card % da base" coluna="card_pct" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                  <SortHeader rotulo="Visitantes" coluna="visitors" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                  <SortHeader rotulo="Conversao" coluna="conversion" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
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
                        <tr key={b.brand} className={`hover:bg-accent-soft/50 transition-colors ${i % 2 === 0 ? "" : "bg-raised/30"}`}>
                          <td className="px-4 py-2.5 font-semibold whitespace-nowrap">
                            <Link
                              href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=tiktok`)}
                              className="text-ink-muted hover:text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                            >
                              {b.label}
                            </Link>
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-ink-muted font-medium">{fmtBrl(b.tiktok_gmv!)}</td>
                          <td className="px-4 py-2.5 text-right tabular-nums">
                            <span className="text-ink-faint text-xs">{formatContentPctBr(partPct)}</span>
                          </td>
                          <td className="px-4 py-2.5">
                            <div className="flex flex-col gap-1">
                              <ContentMixBar
                                mix={mix}
                                gmvVideo={b.tiktok_gmv_video}
                                gmvLive={b.tiktok_gmv_live}
                                gmvCard={b.tiktok_gmv_card}
                              />
                              {mix.base != null && (
                                <span className="text-xs text-ink-faint tabular-nums whitespace-nowrap">
                                  Base {fmtBrl(mix.base)}
                                </span>
                              )}
                              {/* Sublinha separada: diagnostico de reconciliacao,
                                * fora da barra de composicao de proposito. */}
                              <span className="text-xs text-ink-faint tabular-nums whitespace-nowrap">
                                Dif. base × GMV comercial: {formatDivergenceBr(mix.divergencePct)}
                              </span>
                            </div>
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${dom === "video" ? CHANNEL_STYLE.video : DIM_STYLE}`}>
                              {formatContentPctBr(b.tiktok_video_pct)}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${dom === "live" ? CHANNEL_STYLE.live : DIM_STYLE}`}>
                              {formatContentPctBr(b.tiktok_live_pct)}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${dom === "card" ? CHANNEL_STYLE.card : DIM_STYLE}`}>
                              {formatContentPctBr(b.tiktok_card_pct)}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-ink-faint">
                            {b.tiktok_visitors != null ? fmtNumber(b.tiktok_visitors) : "—"}
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${convRateStyle(b.tiktok_conversion_rate)}`}>
                              {formatContentPctBr(b.tiktok_conversion_rate)}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                    {tkBrands.length > 0 && (
                      <tr className="bg-raised border-t border-line">
                        <td className="px-6 py-3 text-xs font-bold text-ink-muted uppercase tracking-wider">Total</td>
                        <td className="px-4 py-3 text-right tabular-nums font-bold text-ink text-sm">{fmtBrl(tkGmvTotal)}</td>
                        <td className="px-4 py-3 text-right tabular-nums"><span className="text-ink-faint text-xs">100%</span></td>
                        <td className="px-4 py-3">
                          <div className="flex flex-col gap-1">
                            <ContentMixBar
                              mix={tkMixTotal}
                              gmvVideo={tkVidTotal}
                              gmvLive={tkLiveTotal}
                              gmvCard={tkCardTotal}
                            />
                            {tkMixTotal.base != null && (
                              <span className="text-xs text-ink-faint tabular-nums whitespace-nowrap">
                                Base {fmtBrl(tkMixTotal.base)}
                              </span>
                            )}
                            <span className="text-xs text-ink-faint tabular-nums whitespace-nowrap">
                              Dif. base × GMV comercial: {formatDivergenceBr(tkMixTotal.divergencePct)}
                            </span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink-muted text-xs font-bold">{formatContentPctBr(tkMixTotal.videoPct)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink-muted text-xs font-bold">{formatContentPctBr(tkMixTotal.livePct)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink-muted text-xs font-bold">{formatContentPctBr(tkMixTotal.cardPct)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink-faint text-xs">{fmtNumber(tkVisitorsTotal)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-xs font-semibold text-ink-muted">
                          {formatContentPctBr(displayKpis?.tiktok_conversion_rate)}
                        </td>
                      </tr>
                    )}
                  </>
                )}
              </tbody>
            </table>
          </TableScrollHint>
        </div>
      )}
            </>
          )}

          {abaAtiva === "ml" && (
            <>
      {/* ── Resumo Mercado Livre ── */}
      {showMl && (
        <section id="mercado-livre" aria-labelledby="mercado-livre-heading" className="scroll-mt-24 flex flex-col gap-3" aria-busy={!dataIsFresh}>
          <h2 id="mercado-livre-heading" className="text-sm font-semibold text-ink-muted">Mercado Livre</h2>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            {!dataIsFresh ? (
              <><StatCard rotulo="" valor="" carregando /><StatCard rotulo="" valor="" carregando /><StatCard rotulo="" valor="" carregando /></>
            ) : (
              <>
                <StatCard
                  rotulo="Novos Compradores ML"
                  valor={fmtPct(displayKpis?.ml_new_buyer_pct ?? null)}
                  contexto={displayKpis?.ml_new_buyers != null ? `${fmtNumber(displayKpis.ml_new_buyers)} novos` : undefined}
                  canal="bg-canal-ml"
                />
                <StatCard
                  rotulo="Recompra ML"
                  valor={fmtPct(displayKpis?.ml_repeat_buyer_rate_pct ?? null)}
                  contexto={displayKpis?.ml_repeat_buyers != null ? `${fmtNumber(displayKpis.ml_repeat_buyers)} recorrentes` : undefined}
                  canal="bg-canal-ml"
                />
                <StatCard
                  rotulo="GMV por Comprador ML"
                  valor={displayKpis?.ml_gmv_per_buyer != null ? fmtBrl(displayKpis.ml_gmv_per_buyer) : "—"}
                  contexto={displayKpis?.ml_unique_buyers != null ? `${fmtNumber(displayKpis.ml_unique_buyers)} compradores (soma diária)` : undefined}
                  canal="bg-canal-ml"
                />
              </>
            )}
          </div>
        </section>
      )}

      {/* ── Tabela: Perfil de compradores ML ── */}
      {showMl && (
        <div className="bg-surface border border-line rounded-xl overflow-hidden">
          <div className="px-6 py-4 border-b border-line">
            <h2 className="flex items-center gap-1.5 text-sm font-semibold text-ink-muted">
              Perfil de Compradores ML por Marca
              <InfoDica texto={LEGENDA_COMPRADORES} />
            </h2>
          </div>
          <TableScrollHint>
            <table className="w-full text-data" aria-label="Perfil de compradores ML por marca">
              <thead>
                <tr className="bg-raised">
                  <SortHeader rotulo="Marca" coluna="brand" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} alinhamento="left" />
                  <SortHeader rotulo="GMV" coluna="gmv" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                  <th className="px-4 py-3 text-right text-xs font-semibold text-ink-muted uppercase tracking-wider">Part.%</th>
                  <SortHeader rotulo="Compradores" coluna="buyers" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                  <SortHeader rotulo="Novos %" coluna="new_pct" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                  <SortHeader rotulo="Novos" coluna="new" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                  <SortHeader rotulo="Recorrentes" coluna="repeat" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                  <SortHeader rotulo="Recompra %" coluna="repeat_pct" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                  <SortHeader rotulo="GMV / Comprador" coluna="gmv_per_buyer" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {!dataIsFresh ? (
                  <SkeletonTableRows rows={3} cols={9} />
                ) : (
                  <>
                    {mlSort.sortedRows.map((b, i) => {
                      const partPct = mlGmvTotal > 0 ? ((b.ml_gmv ?? 0) / mlGmvTotal) * 100 : 0;
                      const newPct = b.ml_new_buyer_pct ??
                        (b.ml_unique_buyers && b.ml_new_buyers ? (b.ml_new_buyers / b.ml_unique_buyers) * 100 : null);
                      return (
                        <tr key={b.brand} className={`hover:bg-accent-soft/50 transition-colors ${i % 2 === 0 ? "" : "bg-raised/30"}`}>
                          <td className="px-4 py-2.5 font-semibold whitespace-nowrap">
                            <Link
                              href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=ml`)}
                              className="text-ink-muted hover:text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                            >
                              {b.label}
                            </Link>
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-ink-muted font-medium">{fmtBrl(b.ml_gmv!)}</td>
                          <td className="px-4 py-2.5 text-right tabular-nums">
                            <span className="text-ink-faint text-xs">{partPct.toFixed(1)}%</span>
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-ink-muted">
                            {b.ml_unique_buyers != null ? fmtNumber(b.ml_unique_buyers) : "—"}
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${newBuyerPctStyle(newPct)}`}>{fmtPct(newPct)}</span>
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-ink-muted">
                            {b.ml_new_buyers != null ? fmtNumber(b.ml_new_buyers) : "—"}
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-ink-muted">
                            {b.ml_repeat_buyers != null ? fmtNumber(b.ml_repeat_buyers) : "—"}
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${repeatRateStyle(b.ml_repeat_buyer_rate_pct)}`}>
                              {fmtPct(b.ml_repeat_buyer_rate_pct)}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-ink-muted font-medium">
                            {b.ml_gmv_per_buyer != null ? fmtBrl(b.ml_gmv_per_buyer) : "—"}
                          </td>
                        </tr>
                      );
                    })}
                    {mlBrands.length > 0 && (
                      <tr className="bg-raised border-t border-line">
                        <td className="px-6 py-3 text-xs font-bold text-ink-muted uppercase tracking-wider">Total</td>
                        <td className="px-4 py-3 text-right tabular-nums font-bold text-ink text-sm">{fmtBrl(mlGmvTotal)}</td>
                        <td className="px-4 py-3 text-right tabular-nums"><span className="text-ink-faint text-xs">100%</span></td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink-muted text-xs font-bold">{fmtNumber(mlBuyersTotal)}</td>
                        <td className="px-4 py-3 text-right tabular-nums">
                          <span className={`text-xs px-1.5 py-0.5 rounded ${newBuyerPctStyle(mlNewPctTotal)}`}>{mlNewPctTotal.toFixed(1)}%</span>
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink-muted text-xs font-bold">{fmtNumber(mlNewTotal)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink-muted text-xs font-bold">{fmtNumber(mlRepeatTotal)}</td>
                        <td className="px-4 py-3 text-right tabular-nums">
                          <span className={`text-xs px-1.5 py-0.5 rounded ${repeatRateStyle(mlRepeatPctTotal)}`}>{mlRepeatPctTotal.toFixed(1)}%</span>
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink-muted text-xs font-bold">
                          {mlGmvPerBuyerTotal != null ? fmtBrl(mlGmvPerBuyerTotal) : "—"}
                        </td>
                      </tr>
                    )}
                  </>
                )}
              </tbody>
            </table>
          </TableScrollHint>
        </div>
      )}
            </>
          )}

          {abaAtiva === "shopee" && (
            <>
      {/* ── Resumo Shopee — só quando há dados ── */}
      {showShopee && hasShopeeData && (
        <section id="shopee" aria-labelledby="shopee-heading" className="scroll-mt-24 flex flex-col gap-3" aria-busy={!dataIsFresh}>
          <h2 id="shopee-heading" className="text-sm font-semibold text-ink-muted">Shopee</h2>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
            {!dataIsFresh ? (
              <><StatCard rotulo="" valor="" carregando /><StatCard rotulo="" valor="" carregando /><StatCard rotulo="" valor="" carregando /><StatCard rotulo="" valor="" carregando /><StatCard rotulo="" valor="" carregando /></>
            ) : (
              <>
                <StatCard
                  rotulo="Novos Compradores Shopee"
                  valor={fmtPct(displayKpis?.shopee_new_buyer_pct ?? null)}
                  contexto={displayKpis?.shopee_new_buyers != null ? `${fmtNumber(displayKpis.shopee_new_buyers)} novos` : undefined}
                  canal="bg-canal-shopee"
                />
                <StatCard
                  rotulo="Recompra Shopee"
                  valor={fmtPct(displayKpis?.shopee_repeat_buyer_rate_pct ?? null)}
                  contexto={displayKpis?.shopee_repeat_buyers != null ? `${fmtNumber(displayKpis.shopee_repeat_buyers)} recorrentes` : undefined}
                  canal="bg-canal-shopee"
                />
                <StatCard
                  rotulo="GMV / Comprador Shopee"
                  valor={displayKpis?.shopee_gmv_per_buyer != null ? fmtBrl(displayKpis.shopee_gmv_per_buyer) : "—"}
                  contexto={displayKpis?.shopee_unique_buyers != null ? `${fmtNumber(displayKpis.shopee_unique_buyers)} compradores (soma diária)` : undefined}
                  canal="bg-canal-shopee"
                />
                <StatCard
                  rotulo="Visitantes Shopee"
                  valor={displayKpis?.shopee_visitors != null ? fmtNumber(displayKpis.shopee_visitors) : "—"}
                  contexto="Visitas ao perfil no mês"
                  canal="bg-canal-shopee"
                />
                <StatCard
                  rotulo="Conversão Shopee"
                  valor={displayKpis?.shopee_conversion_rate != null ? fmtPct(displayKpis.shopee_conversion_rate) : "—"}
                  contexto="Compradores / Visitantes"
                  canal="bg-canal-shopee"
                />
              </>
            )}
          </div>
        </section>
      )}

      {/* ── Placeholder Shopee quando filtro=shopee e sem dados ── */}
      {showShopee && dataIsFresh && !hasShopeeData && (
        <div id="shopee" className="scroll-mt-24 bg-surface border border-dashed border-line-strong rounded-xl p-6 flex flex-col items-center gap-2 text-center">
          <p className="text-sm font-semibold text-ink-muted">Shopee — Dados de canal em integração</p>
          <p className="text-xs text-ink-faint">GMV e pedidos já estão na Gerencial.</p>
        </div>
      )}

      {/* ── Tabela: Perfil de compradores Shopee — só quando há dados ── */}
      {showShopee && hasShopeeData && (
        <div className="bg-surface border border-line rounded-xl overflow-hidden">
          <div className="px-6 py-4 border-b border-line">
            <h2 className="flex items-center gap-1.5 text-sm font-semibold text-ink-muted">
              Perfil de Compradores Shopee por Marca
              <InfoDica texto={LEGENDA_COMPRADORES} />
            </h2>
          </div>
          <TableScrollHint>
            <table className="w-full text-data" aria-label="Perfil de compradores Shopee por marca">
              <thead>
                <tr className="bg-raised">
                  <SortHeader rotulo="Marca" coluna="brand" sort={shSort.sort} aoOrdenar={shSort.toggleSort} alinhamento="left" />
                  <SortHeader rotulo="GMV" coluna="gmv" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                  <th className="px-4 py-3 text-right text-xs font-semibold text-ink-muted uppercase tracking-wider">Part.%</th>
                  <SortHeader rotulo="Compradores" coluna="buyers" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                  <SortHeader rotulo="Novos %" coluna="new_pct" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                  <SortHeader rotulo="Novos" coluna="new" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                  <SortHeader rotulo="Recorrentes" coluna="repeat" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                  <SortHeader rotulo="Recompra %" coluna="repeat_pct" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                  <SortHeader rotulo="GMV / Comprador" coluna="gmv_per_buyer" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                  <SortHeader rotulo="Visitantes" coluna="visitors" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                  <SortHeader rotulo="Conversão" coluna="conversion" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {!dataIsFresh ? (
                  <SkeletonTableRows rows={3} cols={11} />
                ) : (
                  <>
                    {shSort.sortedRows.map((b, i) => {
                      const partPct = shGmvTotal > 0 ? ((b.shopee_gmv ?? 0) / shGmvTotal) * 100 : 0;
                      const newPct = b.shopee_new_buyer_pct ??
                        (b.shopee_unique_buyers && b.shopee_new_buyers ? (b.shopee_new_buyers / b.shopee_unique_buyers) * 100 : null);
                      return (
                        <tr key={b.brand} className={`hover:bg-accent-soft/50 transition-colors ${i % 2 === 0 ? "" : "bg-raised/30"}`}>
                          <td className="px-4 py-2.5 font-semibold whitespace-nowrap">
                            <Link
                              href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=shopee`)}
                              className="text-ink-muted hover:text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                            >
                              {b.label}
                            </Link>
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-ink-muted font-medium">{fmtBrl(b.shopee_gmv!)}</td>
                          <td className="px-4 py-2.5 text-right tabular-nums">
                            <span className="text-ink-faint text-xs">{partPct.toFixed(1)}%</span>
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-ink-muted">
                            {b.shopee_unique_buyers != null ? fmtNumber(b.shopee_unique_buyers) : "—"}
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${newBuyerPctStyle(newPct)}`}>{fmtPct(newPct)}</span>
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-ink-muted">
                            {b.shopee_new_buyers != null ? fmtNumber(b.shopee_new_buyers) : "—"}
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-ink-muted">
                            {b.shopee_repeat_buyers != null ? fmtNumber(b.shopee_repeat_buyers) : "—"}
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums">
                            <span className={`text-xs px-1.5 py-0.5 rounded ${repeatRateStyle(b.shopee_repeat_buyer_rate_pct)}`}>
                              {fmtPct(b.shopee_repeat_buyer_rate_pct)}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-ink-muted font-medium">
                            {b.shopee_gmv_per_buyer != null ? fmtBrl(b.shopee_gmv_per_buyer) : "—"}
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums text-ink-muted">
                            {b.shopee_visitors != null ? fmtNumber(b.shopee_visitors) : "—"}
                          </td>
                          <td className="px-4 py-2.5 text-right tabular-nums">
                            {b.shopee_conversion_rate != null
                              ? <span className="text-xs px-1.5 py-0.5 rounded text-ink-muted">{fmtPct(b.shopee_conversion_rate)}</span>
                              : "—"}
                          </td>
                        </tr>
                      );
                    })}
                    {shBrands.length > 0 && (
                      <tr className="bg-raised border-t border-line">
                        <td className="px-6 py-3 text-xs font-bold text-ink-muted uppercase tracking-wider">Total</td>
                        <td className="px-4 py-3 text-right tabular-nums font-bold text-ink text-sm">{fmtBrl(shGmvTotal)}</td>
                        <td className="px-4 py-3 text-right tabular-nums"><span className="text-ink-faint text-xs">100%</span></td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink-muted text-xs font-bold">{fmtNumber(shBuyersTotal)}</td>
                        <td className="px-4 py-3 text-right tabular-nums">
                          <span className={`text-xs px-1.5 py-0.5 rounded ${newBuyerPctStyle(shNewPctTotal)}`}>{shNewPctTotal.toFixed(1)}%</span>
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink-muted text-xs font-bold">{fmtNumber(shNewTotal)}</td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink-muted text-xs font-bold">{fmtNumber(shRepeatTotal)}</td>
                        <td className="px-4 py-3 text-right tabular-nums">
                          <span className={`text-xs px-1.5 py-0.5 rounded ${repeatRateStyle(shRepeatPctTotal)}`}>{shRepeatPctTotal.toFixed(1)}%</span>
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink-muted text-xs font-bold">
                          {shGmvPerBuyerTotal != null ? fmtBrl(shGmvPerBuyerTotal) : "—"}
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink-muted text-xs font-bold">
                          {shVisitorsTotal > 0 ? fmtNumber(shVisitorsTotal) : "—"}
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums">
                          {shConvRateTotal != null
                            ? <span className="text-xs px-1.5 py-0.5 rounded text-ink-muted font-bold">{shConvRateTotal.toFixed(1)}%</span>
                            : "—"}
                        </td>
                      </tr>
                    )}
                  </>
                )}
              </tbody>
            </table>
          </TableScrollHint>
        </div>
      )}
            </>
          )}
        </section>
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
            /* POPUPS-1 — as mesmas linhas da matriz, para as quebras por
               canal e por marca do detalhe; nada novo e' buscado. */
            rows={displayChannelRows}
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
    <Suspense fallback={<div className="min-h-screen bg-canvas" />}>
      <CanaisPageInner />
    </Suspense>
  );
}
