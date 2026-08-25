"use client";

import { Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { buildDemoSeries, DEMO_SERIES_LABEL, DEMO_SERIES_WARNING, type DailyRow } from "@/lib/brand/demo-series";
import { fetchBrandDetail, type BrandDetail, type BrandDetailChannelRow, type BrandDetailProduto } from "@/lib/api-client";
import { isMarketplaceSelected, serializeMarketplaceSelection } from "@/lib/marketplace-filter";
import { useGlobalFilters } from "@/hooks/useGlobalFilters";
import { previousEquivalentRange } from "@/lib/filters/presets";
import { mergeFilteredHref } from "@/lib/filters/nav-links";
import { fmtPeriodo } from "@/lib/filters/format";
import { summarize, isOrdersReliable, projectDailyRowsBySelection } from "@/lib/brand-daily-summary";
import DailyChart from "@/components/DailyChart";
import KpiCard from "@/components/KpiCard";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import DateRangeFilter from "@/components/DateRangeFilter";
import { SkeletonKpiCard } from "@/components/Skeleton";
import { fmtBrl, fmtNumber, calcMoM } from "@/lib/formatters";
// Apresentacao decimal em pt-BR. O modulo nasceu no V3-1A, quando o QA visual
// achou PONTO decimal em toda percentagem da Inteligencia, e e generico: cuida
// so de formatacao, sem calculo, metrica nem arredondamento de negocio.
// Reaproveitar e melhor que duplicar; o nome da pasta e artefato historico.
import { decBr, pctBr, roasBr } from "@/lib/inteligencia/format";
import TableScrollHint from "@/components/TableScrollHint";
import BrandArrivalBanner from "@/components/BrandArrivalBanner";
import PageContainer from "@/components/layout/PageContainer";
import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import DrilldownContextLine from "@/components/drilldown/DrilldownContextLine";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import DrilldownCta from "@/components/drilldown/DrilldownCta";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import TikTokMonthlyPanel from "@/components/brand/TikTokMonthlyPanel";
import { parseBrandArrivalContext, SECTION_PERIOD } from "@/lib/brand-arrival-context";
import { computeRequestStatus } from "@/lib/request-freshness";
import {
  availabilityForBrand, brandDetailRequestKey, fmtCompetencia, monthlyViewState,
  monthOptions, parseRefMonth, periodRegimeRelation, REF_MONTH_QUERY_KEY,
  resolveRefMonth, type MonthAvailability,
} from "@/lib/brand/ref-month";
import {
  changesState, CHANGES_NOTE, channelTotals, decomposeByChannel, sortByImpact,
} from "@/lib/brand/period-changes";
import {
  channelCtaHref, CHANNEL_CTA_SCOPE_NOTE, productCtaHref, PRODUCT_CTA_SCOPE_NOTE,
  readChannelDetail, readProductDetail,
  type DrilldownMetric, type MetricUnit, type MetricValue,
} from "@/lib/brand/monthly-drilldown";

const BRAND_META: Record<string, { label: string; color: string; initials: string }> = {
  barbours: { label: "BARBOURS", color: "bg-violet-600", initials: "BA" },
  kokeshi:  { label: "KOKESHI",  color: "bg-cyan-500",   initials: "KO" },
  apice:    { label: "APICE",    color: "bg-amber-500",  initials: "AP" },
  lescent:  { label: "LESCENT",  color: "bg-pink-500",   initials: "LE" },
  rituaria: { label: "RITUARIA", color: "bg-emerald-500",initials: "RI" },
};

const BRAND_PILLS = [
  { slug: "barbours", label: "BARBOURS" },
  { slug: "kokeshi",  label: "KOKESHI"  },
  { slug: "apice",    label: "APICE"    },
  { slug: "lescent",  label: "LESCENT"  },
  { slug: "rituaria", label: "RITUARIA" },
];

/** Identidade estavel da requisicao diaria/GLOBAL (tendencia + ultimos 7
 * dias) — mesmo padrao "Finding 2" da Gerencial/Canais (Gate U2/U3):
 * enquanto a chave resolvida nao bate com a atual (inclusive ao trocar de
 * marca pelos pills), os dados diarios em estado sao tratados como
 * potencialmente da marca/filtro anterior e nao sao exibidos.
 *
 * O Gate V3-2 NAO altera esta chave. A competencia mensal tem identidade
 * PROPRIA (`brandDetailRequestKey`), porque os dois fetches tem gatilhos
 * diferentes: trocar o intervalo global nao invalida dado mensal, e trocar a
 * competencia nao invalida a serie diaria. */
function buildDailyRequestKey(
  brand: string, channels: readonly string[], dateFrom: string, dateTo: string, compare: boolean,
): string {
  return `${brand}|${channels.join(",")}|${dateFrom}|${dateTo}|${compare}`;
}

/** `null` = ausencia de cobertura; zero = medida. Nunca se troca um pelo outro. */
function fmtMetric(m: MetricValue, unit: MetricUnit): string {
  if (m.kind === "missing") return "Sem dado";
  if (unit === "brl") return fmtBrl(m.n);
  if (unit === "brl2") return `R$ ${decBr(m.n, 2)}`;
  if (unit === "pct") return pctBr(m.n, 2);
  return fmtNumber(m.n);
}

function MetricList({ metrics }: { metrics: readonly DrilldownMetric[] }) {
  return (
    <ul className="flex flex-col gap-1 list-none p-0 m-0">
      {metrics.map((m) => (
        <EvidenceRow
          key={m.key}
          label={m.label}
          value={fmtMetric(m.value, m.unit)}
          tone={m.value.kind === "missing" ? "muted" : "value"}
        />
      ))}
    </ul>
  );
}

function SectionTitle({ children, badge }: { children: ReactNode; badge?: string }) {
  return (
    <div className="flex items-center gap-3 mb-4 flex-wrap">
      <h3 className="text-sm font-semibold text-slate-600">{children}</h3>
      {badge && (
        <span className="text-xs font-semibold text-slate-600 bg-slate-100 rounded-full px-2.5 py-0.5">
          {badge}
        </span>
      )}
      <div className="flex-1 h-px bg-violet-100" />
    </div>
  );
}

/** Cor da barra de cada marketplace no mix global — mesma paleta do DailyChart. */
const MIX_TOM: Record<string, string> = {
  tiktok: "bg-violet-500",
  ml: "bg-amber-500",
  shopee: "bg-orange-500",
};

/** Detalhe mensal aberto — um por vez, sempre no shell unico. */
type BrandDialog =
  | { kind: "channel"; row: BrandDetailChannelRow }
  | { kind: "product"; produto: BrandDetailProduto };

function BrandPageInner() {
  const { brand } = useParams<{ brand: string }>();
  const meta = BRAND_META[brand];

  const [filters, setFilters] = useGlobalFilters({ defaultPreset: "mes_anterior", defaultCompare: true });
  const filter = filters.channels; // alias — preserva as referencias existentes abaixo
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  // Combina os filtros globais atuais com o href de destino (mesmo padrao de
  // mergeFilteredHref usado em Canais/Gerencial) — a marca do destino sempre
  // sobrescreve `brands=` atual, para nunca navegar para uma marca com a
  // querystring apontando para a marca anterior (Gate U3, Task 6).
  const buildHref = (href: string) => mergeFilteredHref(href, searchParams);
  const backToCanais = mergeFilteredHref("/canais", searchParams);

  // Contexto de chegada "quente" (Gate G3, estendido no V3-2 para a origem
  // `inteligencia`): validado contra a marca da rota e o canal filtrado.
  // Marca/canal incompativeis, enum invalido, parametro repetido ou ausencia
  // ⇒ `null` e a pagina fica identica a de sempre. `ctx_*` NUNCA entra em
  // FILTER_QUERY_KEYS, entao nem a sidebar nem os links desta pagina
  // repropagam o contexto, e trocar de marca pelos pills o descarta.
  const arrivalCtx = useMemo(
    () => parseBrandArrivalContext(searchParams, brand, filters.channels),
    [searchParams, brand, filters.channels],
  );

  // ── regime 1: intervalo global ─────────────────────────────────────────
  const [daily, setDaily] = useState<DailyRow[]>([]);
  const [prevDaily, setPrevDaily] = useState<DailyRow[]>([]);
  const [isLive, setIsLive] = useState(false);
  const [dailyLoading, setDailyLoading] = useState(true);
  const [resolvedDailyKey, setResolvedDailyKey] = useState<string | null>(null);

  // ── regime 2: competencia mensal do TikTok ─────────────────────────────
  // A URL e a fonte de verdade da competencia ESCOLHIDA; a lista de
  // competencias reais vem sempre de `available_months` (BE5), nunca de mock.
  const refMonthUrl = useMemo(() => parseRefMonth(searchParams), [searchParams]);
  // FINDING 3: a disponibilidade carrega a MARCA que a produziu. Um `string[]`
  // solto fazia o seletor da marca anterior sobreviver a troca de rota durante
  // o loading, e permanecer se a leitura da marca nova falhasse.
  const [availability, setAvailability] = useState<MonthAvailability | null>(null);
  const [brandDetail, setBrandDetail] = useState<BrandDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [detailError, setDetailError] = useState(false);
  const [resolvedDetailKey, setResolvedDetailKey] = useState<string | null>(null);
  const [detailRetry, setDetailRetry] = useState(0);
  const [dialog, setDialog] = useState<BrandDialog | null>(null);

  // Portao de identidade: nada da marca anterior atravessa a troca de rota.
  const disponibilidade = availabilityForBrand(availability, brand);
  const mesesDaMarca = disponibilidade?.months ?? [];
  // O portao e reaplicado DENTRO do memo em vez de depender de `mesesDaMarca`:
  // aquele array e recriado a cada render e, como dependencia, recomputaria a
  // resolucao sempre. As dependencias reais sao a URL, a disponibilidade
  // guardada e a marca da rota.
  const resolucao = useMemo(
    () => resolveRefMonth(refMonthUrl, availabilityForBrand(availability, brand)?.months ?? []),
    [refMonthUrl, availability, brand],
  );
  const refMonth = resolucao.month;

  const dailyRequestKey = useMemo(
    () => buildDailyRequestKey(brand, filter, filters.dateFrom, filters.dateTo, filters.compare),
    [brand, filter, filters.dateFrom, filters.dateTo, filters.compare],
  );

  useEffect(() => {
    // Ignora a resposta se marca/canal/periodo mudarem antes dela chegar —
    // inclusive troca de marca pelos pills, que reusa este mesmo componente
    // (o React Nao remonta so porque o parametro de rota mudou).
    let ignore = false;
    setDailyLoading(true);
    const key = buildDailyRequestKey(brand, filter, filters.dateFrom, filters.dateTo, filters.compare);
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";
    const marketplace = serializeMarketplaceSelection(filter);

    async function fetchDailyRange(dateFrom: string, dateTo: string): Promise<DailyRow[] | null> {
      try {
        const res = await fetch(
          `${apiUrl}/api/v1/performance/daily?brand=${brand}&marketplace=${marketplace}&date_from=${dateFrom}&date_to=${dateTo}`
        );
        if (res.ok) return (await res.json()).data;
      } catch {/* api offline */}
      return null;
    }

    async function load() {
      const cur = await fetchDailyRange(filters.dateFrom, filters.dateTo);
      if (ignore) return;
      if (cur) {
        setDaily(cur);
        setIsLive(true);
      } else {
        const days = Math.max(1, Math.round(
          (new Date(`${filters.dateTo}T00:00:00`).getTime() - new Date(`${filters.dateFrom}T00:00:00`).getTime()) / 86_400_000
        ) + 1);
        // Fallback de DEMONSTRACAO, explicitamente rotulado na tela.
        setDaily(buildDemoSeries(brand, days));
        setIsLive(false);
      }

      if (filters.compare) {
        const prevRange = previousEquivalentRange(filters.dateFrom, filters.dateTo);
        const prev = await fetchDailyRange(prevRange.dateFrom, prevRange.dateTo);
        if (ignore) return;
        setPrevDaily(prev ?? []);
      } else {
        setPrevDaily([]);
      }
      if (ignore) return;
      setResolvedDailyKey(key);
      setDailyLoading(false);
    }
    load();
    return () => { ignore = true; };
  }, [brand, filter, filters.dateFrom, filters.dateTo, filters.compare]);

  // Dados diarios so sao considerados frescos quando a chave resolvida bate
  // com a chave atual — fecha o frame de render em que marca/filtro ja
  // mudaram mas o efeito ainda nao rodou (ex: troca de marca pelos pills).
  const dailyIsFresh = !dailyLoading && resolvedDailyKey === dailyRequestKey;

  const detailRequestKey = useMemo(() => brandDetailRequestKey(brand, refMonth), [brand, refMonth]);

  useEffect(() => {
    // Fetch MENSAL, com identidade propria (marca + competencia). Mesma guarda
    // `ignore` do efeito diario, mais o registro da chave resolvida nos TRES
    // desfechos possiveis — sucesso, resposta nula e rejeicao —, porque so
    // isso encerra o loading da chave atual. Sem o terceiro, uma rejeicao
    // deixaria o painel em skeleton para sempre.
    let ignore = false;
    setDetailLoading(true);
    setDetailError(false);
    const key = brandDetailRequestKey(brand, refMonth);
    fetchBrandDetail(brand, refMonth ?? undefined)
      .then((d) => {
        if (ignore) return;
        setBrandDetail(d);
        if (d) {
          // A disponibilidade e gravada COM a marca. Uma falha nao a sobrescreve:
          // um retry da mesma marca continua enxergando a propria lista, e uma
          // marca nova sem resposta simplesmente nao tem disponibilidade.
          setAvailability({
            brand,
            months: d.available_months ?? [],
            servedMonth: d.ref_month ?? null,
          });
        } else {
          // FINDING 2: `apiFetch` devolve null para HTTP nao-2xx, falha de rede,
          // JSON invalido e qualquer excecao capturada. `null` e FALHA DE
          // LEITURA, nao "concluiu sem payload" — logo, erro, e com retry.
          setDetailError(true);
        }
        setResolvedDetailKey(key);
        setDetailLoading(false);
      })
      .catch(() => {
        // Rejeicao nao acontece pelo contrato atual de `apiFetch`; a guarda
        // existe para que uma mudanca futura falhe como erro, nunca como
        // skeleton eterno.
        if (ignore) return;
        setBrandDetail(null);
        setDetailError(true);
        setResolvedDetailKey(key);
        setDetailLoading(false);
      });
    return () => { ignore = true; };
  }, [brand, refMonth, detailRetry]);

  const detailStatus = computeRequestStatus({
    loading: detailLoading,
    error: detailError,
    resolvedKey: resolvedDetailKey,
    requestKey: detailRequestKey,
  });
  // A protecao inteira esta aqui: fora do estado `fresh`, o payload mensal e
  // tratado como potencialmente da marca/competencia anterior e NAO e lido.
  const displayDetail = detailStatus.fresh ? brandDetail : null;

  // Trocar marca ou competencia fecha o detalhe aberto: um dialogo que
  // sobrevivesse a troca mostraria evidencia de outra identidade.
  useEffect(() => { setDialog(null); }, [detailRequestKey]);

  const selecionarCompetencia = useCallback((month: string) => {
    // A competencia escolhida vira URL — e isso que torna a selecao
    // compartilhavel. Os outros parametros presentes sao preservados.
    //
    // `push`, nao `replace`: escolher a competencia e' uma NAVEGACAO do
    // analista, e o botao "voltar" precisa desfazer a escolha. Com `replace` a
    // entrada anterior era sobrescrita, e o `back` pulava a competencia
    // inteira — saltava de mai/2026 direto para a URL sem `ref_month`, o que o
    // QA visual do V3-3 mediu (`history.length` crescia, mas a entrada de
    // jun/2026 desaparecia). E' tambem o que a lente da Inteligencia ja faz:
    // `buildLensHref` alimenta um `<Link>`, que empilha. O `replace` continua
    // certo para OUTRA coisa — materializar o filtro padrao em
    // `useGlobalFilters` —, porque ali ninguem escolheu nada.
    const qs = new URLSearchParams(searchParams.toString());
    qs.set(REF_MONTH_QUERY_KEY, month);
    router.push(`${pathname}?${qs.toString()}`, { scroll: false });
  }, [pathname, router, searchParams]);

  if (!meta) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-400">
        Brand &quot;{brand}&quot; nao encontrado.{" "}
        <Link href="/" className="text-violet-600 underline ml-1">Voltar</Link>
      </div>
    );
  }

  const periodLabel = fmtPeriodo(filters.dateFrom, filters.dateTo);
  const competenciaLabel = fmtCompetencia(refMonth);
  // Pedidos/Ticket Medio do fallback de demonstracao nao sao separados por
  // canal (ver isOrdersReliable) — em modo demonstracao com selecao parcial de
  // canal, ficam N/D em vez de reaproveitar o total combinado dos 3 canais.
  const ordersReliable = isOrdersReliable(isLive, filter);
  const cur = summarize(daily, filter, ordersReliable);
  const prev = summarize(prevDaily, filter, ordersReliable);
  const gmvMoM = filters.compare && prev.gmv > 0 ? calcMoM(cur.gmv, prev.gmv) : null;

  const showTk = isMarketplaceSelected(filter, "tiktok");
  const showMl = isMarketplaceSelected(filter, "ml");
  const showSh = isMarketplaceSelected(filter, "shopee");
  const hasTiktok = showTk && daily.some((r) => r.tiktok_gmv != null);
  const hasMl = showMl && daily.some((r) => r.ml_gmv != null);
  const hasShopee = showSh && daily.some((r) => r.shopee_gmv != null);

  // Projeta os canais nao selecionados para null e recalcula total_gmv pela
  // soma dos canais selecionados (Gate U3, Finding 2).
  const projectedDaily = projectDailyRowsBySelection(daily, filter);
  const last7 = [...projectedDaily].reverse().slice(0, 7);

  const mixCanais = channelTotals(projectedDaily, filter);
  const mudancas = changesState(filters.compare, isLive, prevDaily.length);
  const linhasMudanca = mudancas === "ready"
    ? sortByImpact(decomposeByChannel(daily, prevDaily, filter))
    : [];

  const relacaoRegimes = periodRegimeRelation(refMonth, filters.dateFrom, filters.dateTo);
  const opcoesMes = monthOptions(mesesDaMarca);
  // FINDING 1: `available_months = []` numa resposta FRESCA significa que a
  // marca nao tem competencia com dado — e o mesmo payload traz agregados
  // ZERADOS. Tratar isso como `ready` exibia zero como se fosse venda medida.
  // Agora a unica pergunta e se a competencia resolvida consta na lista; o caso
  // "ainda nao se sabe" fica coberto por `loading`, que tem precedencia.
  const semPayloadFresco = detailStatus.fresh && brandDetail == null;
  const estadoMensal = monthlyViewState({
    loading: detailStatus.loading,
    error: detailStatus.error || semPayloadFresco,
    monthAvailable: resolucao.available,
  });

  return (
    <PageContainer>
      {/* ══ 1. cabecalho e contexto de chegada ═══════════════════════════ */}
      <div className="flex flex-col gap-2">
        <Link
          href={backToCanais}
          className="inline-flex items-center min-h-11 text-xs font-semibold text-violet-600 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded w-fit"
        >
          ← Voltar para Canais
        </Link>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <span className={`w-9 h-9 rounded-xl ${meta.color} flex items-center justify-center text-white text-xs font-bold shrink-0`}>
              {meta.initials}
            </span>
            <div>
              <h2 className="text-xl font-bold text-gray-900 leading-none">{meta.label}</h2>
              <p className="text-xs text-slate-500">
                Marca 360 · intervalo global {periodLabel} · competência TikTok {competenciaLabel}
              </p>
            </div>
          </div>
          {dailyIsFresh ? (
            !isLive && (
              <span className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5 font-semibold">
                {DEMO_SERIES_LABEL}
              </span>
            )
          ) : (
            <span className="text-xs text-slate-600 bg-slate-100 border border-slate-200 rounded-lg px-3 py-1.5 font-medium">
              Atualizando dados...
            </span>
          )}
        </div>
      </div>

      {/* Navegacao entre marcas — path e brands= sempre apontam para a mesma
          marca de destino, e `ctx_*` nao viaja (nao esta em FILTER_QUERY_KEYS).
          A competencia mensal, sim, e preservada: e escolha do analista, nao
          contexto de chegada. */}
      <nav aria-label="Selecionar marca" className="flex flex-wrap gap-2">
        {BRAND_PILLS.map((b) => (
          <Link
            key={b.slug}
            href={buildHref(
              `/brand/${b.slug}?brands=${b.slug}${refMonthUrl ? `&${REF_MONTH_QUERY_KEY}=${refMonthUrl}` : ""}`,
            )}
            aria-current={b.slug === brand ? "page" : undefined}
            className={`inline-flex items-center min-h-11 px-4 rounded-full text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
              b.slug === brand
                ? "bg-violet-600 text-white shadow-sm"
                : "bg-white text-slate-600 border border-violet-200 hover:border-violet-400 hover:text-violet-700"
            }`}
          >
            {b.label}
          </Link>
        ))}
      </nav>

      <div className="flex items-start justify-between flex-wrap gap-3">
        <MarketplaceFilter value={filter} onChange={(channels) => setFilters({ channels })} />
        <DateRangeFilter
          dateFrom={filters.dateFrom}
          dateTo={filters.dateTo}
          compare={filters.compare}
          onChange={(v) => setFilters(v)}
          onCompareChange={(compare) => setFilters({ compare })}
        />
      </div>

      <BrandArrivalBanner ctx={arrivalCtx} periodLabel={periodLabel} buildHref={buildHref} />

      {/* ══ 2+3. situacao da marca no INTERVALO GLOBAL ═══════════════════ */}
      <section
        id={SECTION_PERIOD}
        className="scroll-mt-24"
        aria-label={`Situação da marca no intervalo global ${periodLabel}`}
        aria-busy={!dailyIsFresh}
      >
        <SectionTitle badge={`intervalo global ${periodLabel}`}>Situação da marca</SectionTitle>
        {!dailyIsFresh ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard />
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {!isLive && <DataQualityNote note={DEMO_SERIES_WARNING} />}
            {!ordersReliable && (
              <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5">
                Seleção parcial de canal em modo demonstração — os dados de exemplo não separam
                pedidos por canal, então Pedidos e Ticket Médio ficam indisponíveis para esta
                seleção. GMV continua filtrável normalmente.
              </p>
            )}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <KpiCard label="GMV" value={fmtBrl(cur.gmv)} mom={gmvMoM} accent={meta.color} />
              <KpiCard label="Pedidos" value={cur.orders != null ? fmtNumber(cur.orders) : "N/D"} accent="bg-cyan-500" />
              <KpiCard label="Ticket médio" value={cur.avgTicket != null ? fmtBrl(cur.avgTicket) : "N/D"} accent="bg-amber-500" />
              {cur.adSpend != null && cur.adSpend > 0 ? (
                <KpiCard
                  label="Ad Spend"
                  value={fmtBrl(cur.adSpend)}
                  subvalue={`ROAS ~${roasBr(cur.gmv / cur.adSpend)}`}
                  accent="bg-emerald-500"
                />
              ) : (
                <KpiCard label="Ad Spend" value="—" subvalue="N/D para TikTok Shop" accent="bg-slate-300" />
              )}
            </div>
            <p className="text-xs text-slate-500">
              Estes quatro indicadores são do <strong>intervalo global</strong>. A classificação de
              sinais por canal (custo, frete, Ads) é da matriz marca × canal, em Canais — esta
              página não a recalcula.
            </p>
          </div>
        )}
      </section>

      {/* ══ 4. evolucao e mix de canais — INTERVALO GLOBAL ═══════════════ */}
      <section aria-label={`Evolução e mix de canais no intervalo global ${periodLabel}`} aria-busy={!dailyIsFresh}>
        <SectionTitle badge={`intervalo global ${periodLabel}`}>Evolução e mix de canais</SectionTitle>
        {!dailyIsFresh ? (
          <div className="h-64 rounded-2xl bg-slate-100 animate-pulse" aria-hidden="true" />
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-stretch">
            {/* Sem titulo proprio aqui: `DailyChart` ja emite o seu (um `h2`, nivel
                herdado do componente compartilhado). Um `h4` meu por cima criava
                titulo duplicado E um salto de nivel 2→4 na leitura por leitor de
                tela. O aviso de demonstracao continua, sem virar heading. */}
            <div className={`lg:col-span-2 bg-white border rounded-2xl shadow-sm px-4 pt-4 pb-2 ${isLive ? "border-violet-100" : "border-amber-200"}`}>
              {!isLive && (
                <p className="text-xs font-semibold text-amber-800 mb-1">série de exemplo</p>
              )}
              <DailyChart data={projectedDaily} hasTiktok={hasTiktok} hasMl={hasMl} hasShopee={hasShopee} />
            </div>
            <div className={`bg-white border rounded-2xl shadow-sm px-4 py-4 ${isLive ? "border-violet-100" : "border-amber-200"}`}>
              <h3 className="text-sm font-semibold text-slate-700 mb-3">Mix por marketplace</h3>
              {mixCanais.length === 0 ? (
                <p className="text-sm text-slate-500 py-8 text-center">Nenhum canal selecionado.</p>
              ) : (
                <ul className="flex flex-col gap-3 list-none p-0 m-0">
                  {mixCanais.map((c) => (
                    <li key={c.key}>
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="text-xs font-semibold text-slate-600">{c.label}</span>
                        <span className="text-xs text-slate-600 tabular-nums">
                          {c.gmv == null ? "Sem dado" : fmtBrl(c.gmv)}
                          {c.sharePct != null && ` · ${pctBr(c.sharePct, 1)}`}
                        </span>
                      </div>
                      <div className="h-2 bg-slate-100 rounded-full overflow-hidden mt-1">
                        {c.sharePct != null && (
                          <div
                            // decorativa: o valor e o percentual ja estao em texto
                            // ao lado, entao a cor nao carrega significado sozinha
                            aria-hidden="true"
                            className={`h-2 rounded-full ${MIX_TOM[c.key]}`}
                            style={{ width: `${Math.max(2, Math.min(100, c.sharePct))}%` }}
                          />
                        )}
                      </div>
                    </li>
                  ))}
                  <li className="text-xs text-slate-500">
                    Participação sobre a soma dos canais com dado no intervalo. Canal sem cobertura
                    fica &quot;Sem dado&quot;, nunca 0%.
                  </li>
                </ul>
              )}
            </div>
          </div>
        )}
      </section>

      {/* ══ 5. o que mudou — so quando o dado existente sustenta ═════════ */}
      <section aria-label="O que mudou no intervalo global">
        <SectionTitle badge={`intervalo global ${periodLabel} vs anterior`}>O que mudou</SectionTitle>
        {mudancas !== "ready" ? (
          <p className="text-xs text-slate-500">{CHANGES_NOTE[mudancas]}</p>
        ) : (
          <ul className="flex flex-col gap-2 list-none p-0 m-0">
            {linhasMudanca.map((l) => (
              <li key={l.key} className="bg-white border border-violet-100 rounded-xl px-4 py-3 flex items-center justify-between gap-4 flex-wrap">
                <span className="text-sm font-semibold text-slate-700">{l.label}</span>
                <span className="text-xs text-slate-500 tabular-nums">
                  {l.previous == null ? "Sem dado antes" : fmtBrl(l.previous)}
                  {" → "}
                  {l.current == null ? "Sem dado agora" : fmtBrl(l.current)}
                </span>
                <span className={`text-sm font-bold tabular-nums ${
                  l.delta == null ? "text-slate-500" : l.delta >= 0 ? "text-emerald-700" : "text-rose-700"
                }`}>
                  {l.delta == null
                    ? "Sem dado"
                    : `${l.delta >= 0 ? "+" : "−"}${fmtBrl(Math.abs(l.delta))}${
                        l.deltaPct == null ? "" : ` (${l.delta >= 0 ? "+" : "−"}${pctBr(Math.abs(l.deltaPct), 1)})`
                      }`}
                </span>
              </li>
            ))}
            <li className="text-xs text-slate-500">
              Decomposição do mesmo GMV pela coluna de canal do payload diário, contra o intervalo
              equivalente anterior. Sem variação percentual quando a base anterior não é positiva —
              dividir por zero não produz leitura.
            </li>
          </ul>
        )}
      </section>

      {/* ══ 6+7. conteiner do REGIME MENSAL ═════════════════════════════ */}
      {showTk ? (
        <TikTokMonthlyPanel
          state={estadoMensal}
          detail={displayDetail}
          refMonth={refMonth}
          months={opcoesMes}
          onSelectMonth={selecionarCompetencia}
          onRetry={() => setDetailRetry((n) => n + 1)}
          onOpenChannel={(row) => setDialog({ kind: "channel", row })}
          onOpenProduct={(produto) => setDialog({ kind: "product", produto })}
          regimeNote={relacaoRegimes.note}
          hasHistory={resolucao.hasAvailable}
          servedMonth={disponibilidade?.servedMonth ?? null}
          disabled={!detailStatus.fresh}
        />
      ) : (
        <section
          aria-label="Análise mensal do TikTok Shop indisponível para esta seleção"
          className="rounded-2xl border border-slate-200 bg-white px-4 sm:px-6 py-5"
        >
          <h2 className="text-sm font-bold text-slate-700">TikTok Shop · análise mensal</h2>
          <p className="text-xs text-slate-600 mt-1">
            A seleção de canais atual não inclui TikTok Shop, e todo o conteúdo deste contêiner vem
            do contrato mensal do TikTok. Nada foi estimado para preencher o bloco.
          </p>
        </section>
      )}

      {/* ══ ultimos 7 dias — INTERVALO GLOBAL ═══════════════════════════ */}
      <section aria-label={`Últimos 7 dias do intervalo global ${periodLabel}`} aria-busy={!dailyIsFresh}>
        <SectionTitle badge={`intervalo global ${periodLabel}`}>Últimos 7 dias</SectionTitle>
        {!ordersReliable && dailyIsFresh && (
          <p className="text-xs text-slate-500 -mt-2 mb-3">
            Pedidos e Ticket Médio indisponíveis nesta seleção (modo demonstração, canal parcial).
          </p>
        )}
        <div className="bg-white rounded-2xl shadow-sm border border-violet-100 overflow-hidden">
          <TableScrollHint>
            <table className="w-full" aria-label="Últimos 7 dias de performance">
              <caption className="sr-only">Dados diarios de GMV, pedidos e ticket medio dos ultimos 7 dias</caption>
              <thead>
                <tr className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                  <th scope="col" className="text-left px-5 py-3">Data</th>
                  {hasTiktok && <th scope="col" className="text-right px-4 py-3">TikTok GMV</th>}
                  {hasMl && <th scope="col" className="text-right px-4 py-3">ML GMV</th>}
                  {hasShopee && <th scope="col" className="text-right px-4 py-3">Shopee GMV</th>}
                  <th scope="col" className="text-right px-4 py-3">GMV Total</th>
                  <th scope="col" className="text-right px-4 py-3">Pedidos</th>
                  <th scope="col" className="text-right px-5 py-3">Ticket médio</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-violet-100">
                {!dailyIsFresh ? (
                  <tr>
                    <td colSpan={7} className="px-5 py-8 text-center text-sm text-slate-500">Carregando...</td>
                  </tr>
                ) : (
                  last7.map((r) => (
                    <tr key={r.date}>
                      <td className="px-5 py-3 text-sm text-gray-700 font-medium">
                        {new Date(r.date + "T00:00:00").toLocaleDateString("pt-BR", { day: "2-digit", month: "short" })}
                      </td>
                      {hasTiktok && (
                        <td className="text-right px-4 py-3 text-sm text-gray-600 tabular-nums">
                          {r.tiktok_gmv != null ? fmtBrl(r.tiktok_gmv) : <span className="text-slate-400" title="sem dado">Sem dado</span>}
                        </td>
                      )}
                      {hasMl && (
                        <td className="text-right px-4 py-3 text-sm text-gray-600 tabular-nums">
                          {r.ml_gmv != null ? fmtBrl(r.ml_gmv) : <span className="text-slate-400" title="sem dado">Sem dado</span>}
                        </td>
                      )}
                      {hasShopee && (
                        <td className="text-right px-4 py-3 text-sm text-gray-600 tabular-nums">
                          {r.shopee_gmv != null ? fmtBrl(r.shopee_gmv) : <span className="text-slate-400" title="sem dado">Sem dado</span>}
                        </td>
                      )}
                      <td className="text-right px-4 py-3 font-bold text-gray-900 text-sm tabular-nums">
                        {fmtBrl(r.total_gmv)}
                      </td>
                      <td className="text-right px-4 py-3 text-sm text-gray-600 tabular-nums">
                        {ordersReliable ? fmtNumber(r.orders) : <span className="text-slate-500 text-xs">N/D</span>}
                      </td>
                      <td className="text-right px-5 py-3 text-sm text-gray-600 tabular-nums">
                        {!ordersReliable
                          ? <span className="text-slate-500 text-xs">N/D</span>
                          : r.avg_ticket != null ? fmtBrl(r.avg_ticket) : <span className="text-slate-400" title="sem dado">Sem dado</span>}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </TableScrollHint>
        </div>
      </section>

      {/* ══ 8. proximos passos ══════════════════════════════════════════ */}
      <section aria-label="Próximos passos">
        <SectionTitle>Próximos passos</SectionTitle>
        <ul className="flex flex-col gap-1 list-none p-0 m-0">
          <li>
            <DrilldownCta
              href={buildHref(`/canais?brands=${brand}`)}
              ariaLabel={`Comparar canais da marca ${meta.label} em Canais`}
            >
              Comparar canais desta marca →
            </DrilldownCta>
          </li>
          <li>
            <DrilldownCta
              href={buildHref(`/produtos?brands=${brand}`)}
              ariaLabel={`Abrir o catálogo da marca ${meta.label} em Produtos`}
            >
              Abrir o catálogo desta marca →
            </DrilldownCta>
          </li>
          <li>
            {/* `/inteligencia` NAO e filter-aware: o link vai direto, sem
                mergeFilteredHref, e sem nenhum `ctx_*` — navegar daqui para la
                e navegacao fria. */}
            <DrilldownCta
              href={`/inteligencia?brands=${brand}`}
              ariaLabel={`Abrir a Inteligência com a marca ${meta.label} selecionada`}
            >
              Ver oportunidades desta marca na Inteligência →
            </DrilldownCta>
          </li>
        </ul>
        <p className="text-xs text-slate-500 mt-1">
          Navegação limpa: os destinos preservam apenas filtros compatíveis, e nenhum contexto de
          chegada é repropagado.
        </p>
      </section>

      {/* ══ shell UNICO de detalhe — um dialogo por vez ═════════════════ */}
      <KpiDrilldownDialog
        open={dialog != null}
        onClose={() => setDialog(null)}
        title={
          dialog == null
            ? ""
            : dialog.kind === "channel"
              ? `${dialog.row.label} · ${competenciaLabel}`
              : `${dialog.produto.product_name} · ${competenciaLabel}`
        }
      >
        {dialog?.kind === "channel" && (() => {
          const d = readChannelDetail(dialog.row, refMonth);
          return (
            <div className="flex flex-col gap-4">
              <DrilldownContextLine
                leading={`${meta.label} · TikTok Shop`}
                periodLabel={`competência ${d.refMonthLabel}`}
                refreshedAt={null}
              />
              <MetricList metrics={d.metrics} />
              <DataQualityNote note={d.note} />
              <p className="text-xs text-slate-500">{CHANNEL_CTA_SCOPE_NOTE}</p>
              <DrilldownCta
                href={buildHref(channelCtaHref(brand))}
                ariaLabel={`${d.ctaLabel} para a marca ${meta.label}`}
              >
                {d.ctaLabel} →
              </DrilldownCta>
            </div>
          );
        })()}

        {dialog?.kind === "product" && (() => {
          const d = readProductDetail(dialog.produto, refMonth);
          return (
            <div className="flex flex-col gap-4">
              <DrilldownContextLine
                leading={`${meta.label} · TikTok Shop`}
                periodLabel={`competência ${d.refMonthLabel}`}
                refreshedAt={null}
              />
              <MetricList metrics={d.metrics} />
              <DataQualityNote note={d.note} />
              <p className="text-xs text-slate-500">{PRODUCT_CTA_SCOPE_NOTE}</p>
              <DrilldownCta
                href={buildHref(productCtaHref(brand))}
                ariaLabel={`${d.ctaLabel} da marca ${meta.label}`}
              >
                {d.ctaLabel} →
              </DrilldownCta>
            </div>
          );
        })()}
      </KpiDrilldownDialog>
    </PageContainer>
  );
}

export default function BrandPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#f8f7ff]" />}>
      <BrandPageInner />
    </Suspense>
  );
}
