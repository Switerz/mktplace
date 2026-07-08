"use client";

import { Suspense, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { generateDailyData, type DailyRow, AVAILABLE_MONTHS } from "@/lib/mock-daily";
import { fetchBrandDetail, type BrandDetail } from "@/lib/api-client";
import { isMarketplaceSelected, serializeMarketplaceSelection } from "@/lib/marketplace-filter";
import { useGlobalFilters } from "@/hooks/useGlobalFilters";
import { previousEquivalentRange } from "@/lib/filters/presets";
import { appendQuery } from "@/lib/filters/nav-links";
import { fmtPeriodo } from "@/lib/filters/format";
import { summarize } from "@/lib/brand-daily-summary";
import DailyChart from "@/components/DailyChart";
import ChannelMixChart from "@/components/ChannelMixChart";
import KpiCard from "@/components/KpiCard";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import DateRangeFilter from "@/components/DateRangeFilter";
import PeriodSelector from "@/components/PeriodSelector";
import AppNav from "@/components/AppNav";
import { fmtBrl, fmtNumber, calcMoM } from "@/lib/formatters";
import { useSortableTable } from "@/lib/use-sortable-table";
import SortableHeader from "@/components/SortableHeader";
import type { BrandDetailChannelRow } from "@/lib/api-client";

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

function fmtBig(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return String(v);
}

function StatBox({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-slate-50 rounded-xl px-4 py-3">
      <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-wide mb-1">{label}</p>
      <p className="text-base font-bold text-slate-800 tabular-nums leading-tight">{value}</p>
      {sub && <p className="text-[10px] text-slate-500 mt-0.5">{sub}</p>}
    </div>
  );
}

function DemoBar({ label, pct, color, mounted }: { label: string; pct: number | null; color: string; mounted: boolean }) {
  if (pct == null) return null;
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-slate-600 w-16 shrink-0">{label}</span>
      <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
        <div
          className={`h-2 rounded-full transition-[width] duration-700 ease-out motion-reduce:transition-none ${color}`}
          style={{ width: mounted ? `${Math.min(pct, 100)}%` : "0%" }}
        />
      </div>
      <span className="text-xs font-semibold text-slate-700 tabular-nums w-10 text-right">{pct.toFixed(1)}%</span>
    </div>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <h3 className="text-sm font-semibold text-slate-600">{children}</h3>
      <div className="flex-1 h-px bg-violet-100" />
    </div>
  );
}

function BrandPageInner() {
  const { brand } = useParams<{ brand: string }>();
  const meta = BRAND_META[brand];

  const [filters, setFilters] = useGlobalFilters({ defaultPreset: "mes_anterior", defaultCompare: true });
  const filter = filters.channels; // alias — preserva as referencias existentes abaixo
  const searchParams = useSearchParams();
  // Preserva canal/marca/periodo ao voltar ao Gerencial ou trocar de marca
  // pelos pills — "/brand/[brand]" e uma rota compativel com o contrato de
  // filtros globais, tratada aqui pela querystring atual (nunca hardcoded).
  const currentQuery = searchParams.toString();
  const withQuery = (href: string) => appendQuery(href, currentQuery);
  const [period, setPeriod] = useState<string>(AVAILABLE_MONTHS[0].value);
  const [daily, setDaily] = useState<DailyRow[]>([]);
  const [prevDaily, setPrevDaily] = useState<DailyRow[]>([]);
  const [isLive, setIsLive] = useState(false);
  const [brandDetail, setBrandDetail] = useState<BrandDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const raf = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    // Ignora a resposta se marca/canal/periodo mudarem antes dela chegar.
    let ignore = false;
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
        setDaily(generateDailyData(brand, days));
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
    }
    load();
    return () => { ignore = true; };
  }, [brand, filter, filters.dateFrom, filters.dateTo, filters.compare]);

  useEffect(() => {
    // Deep-dive mensal TikTok tem competencia PROPRIA (mes calendario via
    // PeriodSelector), independente do intervalo global acima — ver nota na
    // secao "TikTok Shop — Analise Mensal" mais abaixo. Protegido contra
    // resposta fora de ordem do mesmo jeito que o efeito diario.
    let ignore = false;
    setDetailLoading(true);
    fetchBrandDetail(brand, period).then((d) => {
      if (ignore) return;
      setBrandDetail(d);
      setDetailLoading(false);
    });
    return () => { ignore = true; };
  }, [brand, period]);

  const funnelColumnTypes = {
    channel: "text" as const, impressions: "numeric" as const, ctr_pct: "numeric" as const,
    page_views: "numeric" as const, cvr_pct: "numeric" as const, gmv: "numeric" as const,
  };
  const funnelGetValue = (row: BrandDetailChannelRow, column: string): string | number | null => {
    switch (column) {
      case "channel": return row.label;
      case "impressions": return row.impressions;
      case "ctr_pct": return row.ctr_pct;
      case "page_views": return row.page_views;
      case "cvr_pct": return row.cvr_pct;
      case "gmv": return row.gmv;
      default: return null;
    }
  };
  const funnelSort = useSortableTable(brandDetail?.channel_funnel ?? [], funnelGetValue, funnelColumnTypes);

  if (!meta) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-400">
        Brand &quot;{brand}&quot; nao encontrado.{" "}
        <Link href="/" className="text-violet-600 underline ml-1">Voltar</Link>
      </div>
    );
  }

  const periodLabel = fmtPeriodo(filters.dateFrom, filters.dateTo);
  const cur = summarize(daily, filter);
  const prev = summarize(prevDaily, filter);
  const gmvMoM = filters.compare && prev.gmv > 0 ? calcMoM(cur.gmv, prev.gmv) : null;

  const showTk = isMarketplaceSelected(filter, "tiktok");
  const showMl = isMarketplaceSelected(filter, "ml");
  const showSh = isMarketplaceSelected(filter, "shopee");
  const singleChannel = filter.length === 1;
  const hasTiktok = showTk && daily.some((r) => r.tiktok_gmv != null);
  const hasMl = showMl && daily.some((r) => r.ml_gmv != null);
  const hasShopee = showSh && daily.some((r) => r.shopee_gmv != null);
  const showTikTokDetail = showTk;

  // Zera explicitamente os canais nao selecionados quando apenas um canal
  // esta ativo — necessario para o fallback mock (que sempre gera os 3
  // canais juntos, independente do filtro atual).
  const chartData = singleChannel && showTk
    ? daily.map((r) => ({ ...r, ml_gmv: null, shopee_gmv: null, total_gmv: r.tiktok_gmv ?? 0 }))
    : singleChannel && showMl
    ? daily.map((r) => ({ ...r, tiktok_gmv: null, shopee_gmv: null, total_gmv: r.ml_gmv ?? 0 }))
    : singleChannel && showSh
    ? daily.map((r) => ({ ...r, tiktok_gmv: null, ml_gmv: null, total_gmv: r.shopee_gmv ?? 0 }))
    : daily;

  const last7 = [...daily].reverse().slice(0, 7);
  const d = brandDetail;

  return (
    <div className="min-h-screen bg-[#f8f7ff]">
      <header className="bg-white border-b border-violet-100 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href={withQuery("/")}
              className="text-slate-400 hover:text-violet-600 transition-colors text-sm font-medium flex items-center gap-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
            >
              &larr; Dashboard
            </Link>
            <span className="text-slate-200 select-none">|</span>
            <span className={`w-9 h-9 rounded-xl ${meta.color} flex items-center justify-center text-white text-xs font-bold shrink-0`}>
              {meta.initials}
            </span>
            <div>
              <h1 className="text-lg font-bold text-gray-900 leading-none">{meta.label}</h1>
              <p className="text-xs text-slate-400">Drill-down por marca</p>
            </div>
          </div>
          {!isLive && (
            <span className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5 font-medium">
              Demonstracao
            </span>
          )}
        </div>
      </header>

      <AppNav />

      <main className="max-w-7xl mx-auto px-6 py-8 flex flex-col gap-8">

        {/* Navegacao entre marcas */}
        <nav aria-label="Selecionar marca" className="flex flex-wrap gap-2">
          {BRAND_PILLS.map((b) => (
            <Link
              key={b.slug}
              href={withQuery(`/brand/${b.slug}`)}
              className={`px-4 py-1.5 rounded-full text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                b.slug === brand
                  ? "bg-violet-600 text-white shadow-sm"
                  : "bg-white text-slate-500 border border-violet-200 hover:border-violet-400 hover:text-violet-700"
              }`}
            >
              {b.label}
            </Link>
          ))}
        </nav>

        {/* Filtro de canal e periodo */}
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

        {/* Tendencia — periodo selecionado */}
        <section aria-label={`Tendencia — ${periodLabel}`}>
          <SectionTitle>Tendencia — {periodLabel}</SectionTitle>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <KpiCard label="GMV" value={fmtBrl(cur.gmv)} mom={gmvMoM} accent={meta.color} />
            <KpiCard label="Pedidos" value={fmtNumber(cur.orders)} accent="bg-cyan-500" />
            <KpiCard label="Ticket Medio" value={fmtBrl(cur.avgTicket)} accent="bg-amber-500" />
            {cur.adSpend != null && cur.adSpend > 0 ? (
              <KpiCard
                label="Ad Spend"
                value={fmtBrl(cur.adSpend)}
                subvalue={`ROAS ~${(cur.gmv / cur.adSpend).toFixed(1)}x`}
                accent="bg-emerald-500"
              />
            ) : (
              <KpiCard label="Ad Spend" value="—" subvalue="N/D para TikTok Shop" accent="bg-slate-300" />
            )}
          </div>
          <DailyChart data={chartData} hasTiktok={hasTiktok} hasMl={hasMl} hasShopee={hasShopee} />
        </section>

        {/* Deep-dive mensal — TikTok Shop. Competencia PROPRIA (mes
            calendario via PeriodSelector), independente do periodo global
            selecionado acima — a fonte (gold.tiktok_brand_daily) so suporta
            mes fechado, nao intervalos arbitrarios. Nao misturar com o
            periodo da secao "Tendencia" acima. */}
        {showTikTokDetail && (
          <section aria-label="Analise mensal TikTok Shop — competencia mensal independente">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-1">
              <SectionTitle>TikTok Shop — Análise Mensal</SectionTitle>
              <PeriodSelector value={period} onChange={setPeriod} />
            </div>
            <p className="text-[11px] text-slate-400 mb-3">
              Competência mensal independente — não usa o período selecionado acima (canal/marca são compartilhados, mas a fonte só suporta mês calendário fechado).
            </p>

            {detailLoading && (
              <div className="h-32 flex items-center justify-center text-slate-400 text-sm">
                Carregando dados mensais...
              </div>
            )}

            {!detailLoading && !d && (
              <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-10 text-center">
                <p className="text-slate-500 text-sm font-medium">Dados mensais indisponiveis — API offline</p>
              </div>
            )}

            {!detailLoading && d && (
              <div className="flex flex-col gap-6">

                {/* KPIs mensais */}
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                  <StatBox label="GMV" value={fmtBrl(d.gmv)} />
                  <StatBox label="Pedidos" value={fmtNumber(d.orders)} />
                  <StatBox
                    label="Ticket Medio"
                    value={d.orders > 0 ? fmtBrl(d.gmv / d.orders) : "—"}
                  />
                  <StatBox label="Clientes" value={fmtNumber(d.customers)} sub="dias com visitantes" />
                  <StatBox
                    label="Conversao"
                    value={d.cvr_pct != null ? `${d.cvr_pct.toFixed(1)}%` : "—"}
                    sub="visitantes com dados"
                  />
                  <StatBox
                    label="COS"
                    value={d.cos_pct != null ? `${d.cos_pct.toFixed(1)}%` : "—"}
                    sub="custo sobre GMV"
                  />
                </div>

                {/* Canal mix chart */}
                <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
                  <div className="px-5 py-4 border-b border-violet-100 flex items-center justify-between">
                    <h2 className="text-sm font-semibold text-slate-700">Mix de Canal — GMV Diario</h2>
                    <div className="flex gap-4 text-xs text-slate-500">
                      {d.pct_video != null && <span>Video <span className="font-semibold text-violet-700">{d.pct_video.toFixed(1)}%</span></span>}
                      {d.pct_live != null && <span>Live <span className="font-semibold text-cyan-600">{d.pct_live.toFixed(1)}%</span></span>}
                      {d.pct_card != null && <span>Card <span className="font-semibold text-amber-600">{d.pct_card.toFixed(1)}%</span></span>}
                    </div>
                  </div>
                  <div className="px-4 pt-4 pb-2">
                    {d.daily.length > 0 ? (
                      <ChannelMixChart data={d.daily} />
                    ) : (
                      <div className="h-48 flex items-center justify-center text-slate-400 text-sm">
                        Sem dados diarios para o periodo
                      </div>
                    )}
                  </div>
                </div>

                {/* Ecossistema + Atratividade */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

                  {/* Ecossistema */}
                  <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-5 py-5">
                    <h2 className="text-sm font-semibold text-slate-700 mb-4">Ecossistema de Conteudo</h2>
                    <div className="grid grid-cols-2 gap-3">
                      <StatBox label="Videos Ativos" value={fmtBig(d.active_videos)} />
                      <StatBox label="Videos Novos" value={fmtBig(d.new_videos_posted)} />
                      <StatBox label="Creators Video" value={fmtBig(d.active_video_creators)} />
                      <StatBox label="Views" value={fmtBig(d.total_views)} />
                      <StatBox label="Lives" value={fmtBig(d.total_lives)} />
                      <StatBox label="Creators Live" value={fmtBig(d.live_creators)} />
                    </div>
                  </div>

                  {/* Atratividade */}
                  <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-5 py-5">
                    <h2 className="text-sm font-semibold text-slate-700 mb-4">Atratividade</h2>
                    <div className="grid grid-cols-2 gap-3">
                      <StatBox
                        label="GPM"
                        value={d.gpm != null ? `R$${d.gpm.toFixed(2)}` : "—"}
                        sub="GMV / 1000 views"
                      />
                      <StatBox
                        label="R$/Video"
                        value={d.gmv_per_video != null ? fmtBrl(d.gmv_per_video) : "—"}
                      />
                      <StatBox
                        label="R$/Creator"
                        value={d.gmv_per_creator != null ? fmtBrl(d.gmv_per_creator) : "—"}
                      />
                      <StatBox
                        label="R$/Live"
                        value={d.gmv_per_live != null ? fmtBrl(d.gmv_per_live) : "—"}
                      />
                      <StatBox
                        label="Videos/Creator"
                        value={d.videos_per_creator != null ? d.videos_per_creator.toFixed(1) : "—"}
                      />
                    </div>
                  </div>
                </div>

                {/* Freshness + Demographics */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

                  {/* Freshness */}
                  <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-5 py-5">
                    <h2 className="text-sm font-semibold text-slate-700 mb-1">Freshness de Conteudo</h2>
                    {d.pct_gmv_fresh != null && (
                      <p className="text-3xl font-bold text-gray-900 tabular-nums mb-4">
                        {d.pct_gmv_fresh.toFixed(1)}%
                        <span className="text-sm font-normal text-slate-400 ml-2">receita de videos novos</span>
                      </p>
                    )}
                    <div className="space-y-3">
                      <div>
                        <div className="flex justify-between text-xs text-slate-500 mb-1">
                          <span>Videos Fresh</span>
                          <span className="tabular-nums font-semibold">{fmtNumber(d.fresh_videos)}</span>
                        </div>
                        <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                          {(d.fresh_videos + d.evergreen_videos) > 0 && (
                            <div
                              className="h-2 bg-violet-500 rounded-full transition-[width] duration-700 ease-out motion-reduce:transition-none"
                              style={{ width: mounted ? `${d.fresh_videos / (d.fresh_videos + d.evergreen_videos) * 100}%` : "0%" }}
                            />
                          )}
                        </div>
                      </div>
                      <div>
                        <div className="flex justify-between text-xs text-slate-500 mb-1">
                          <span>Videos Evergreen</span>
                          <span className="tabular-nums font-semibold">{fmtNumber(d.evergreen_videos)}</span>
                        </div>
                        <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                          {(d.fresh_videos + d.evergreen_videos) > 0 && (
                            <div
                              className="h-2 bg-slate-400 rounded-full transition-[width] duration-700 ease-out motion-reduce:transition-none"
                              style={{ width: mounted ? `${d.evergreen_videos / (d.fresh_videos + d.evergreen_videos) * 100}%` : "0%" }}
                            />
                          )}
                        </div>
                      </div>
                      <div className="flex gap-4 pt-1">
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wide">GMV Fresh</p>
                          <p className="text-sm font-semibold text-slate-700 tabular-nums">{fmtBrl(d.gmv_fresh)}</p>
                        </div>
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wide">GMV Evergreen</p>
                          <p className="text-sm font-semibold text-slate-700 tabular-nums">{fmtBrl(d.gmv_evergreen)}</p>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Demographics */}
                  <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-5 py-5">
                    <h2 className="text-sm font-semibold text-slate-700 mb-4">Demographics</h2>
                    <div className="grid grid-cols-2 gap-6">
                      {/* Viewers */}
                      <div className="space-y-4">
                        <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Viewers</p>
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-2">Genero</p>
                          <div className="space-y-2">
                            <DemoBar label="Feminino" pct={d.viewers_pct_female} color="bg-violet-400" mounted={mounted} />
                            <DemoBar label="Masculino" pct={d.viewers_pct_male} color="bg-cyan-400" mounted={mounted} />
                          </div>
                        </div>
                        {(d.viewers_pct_18_24 != null || d.viewers_pct_25_34 != null) && (
                          <div>
                            <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-2">Faixa etaria</p>
                            <div className="space-y-2">
                              <DemoBar label="18–24" pct={d.viewers_pct_18_24} color="bg-slate-400" mounted={mounted} />
                              <DemoBar label="25–34" pct={d.viewers_pct_25_34} color="bg-slate-400" mounted={mounted} />
                              <DemoBar label="35–44" pct={d.viewers_pct_35_44} color="bg-slate-400" mounted={mounted} />
                              <DemoBar label="45–54" pct={d.viewers_pct_45_54} color="bg-slate-400" mounted={mounted} />
                              <DemoBar label="55+" pct={d.viewers_pct_55_plus} color="bg-slate-400" mounted={mounted} />
                            </div>
                          </div>
                        )}
                        {d.viewers_pct_female == null && d.viewers_pct_18_24 == null && (
                          <p className="text-xs text-slate-400">Sem dados de viewers.</p>
                        )}
                      </div>
                      {/* Followers */}
                      <div className="space-y-4">
                        <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Followers</p>
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-2">Genero</p>
                          <div className="space-y-2">
                            <DemoBar label="Feminino" pct={d.followers_pct_female} color="bg-violet-400" mounted={mounted} />
                            <DemoBar label="Masculino" pct={d.followers_pct_male} color="bg-cyan-400" mounted={mounted} />
                          </div>
                        </div>
                        {(d.followers_pct_18_24 != null || d.followers_pct_25_34 != null) && (
                          <div>
                            <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-2">Faixa etaria</p>
                            <div className="space-y-2">
                              <DemoBar label="18–24" pct={d.followers_pct_18_24} color="bg-slate-400" mounted={mounted} />
                              <DemoBar label="25–34" pct={d.followers_pct_25_34} color="bg-slate-400" mounted={mounted} />
                              <DemoBar label="35–44" pct={d.followers_pct_35_44} color="bg-slate-400" mounted={mounted} />
                              <DemoBar label="45–54" pct={d.followers_pct_45_54} color="bg-slate-400" mounted={mounted} />
                              <DemoBar label="55+" pct={d.followers_pct_55_plus} color="bg-slate-400" mounted={mounted} />
                            </div>
                          </div>
                        )}
                        {d.followers_pct_female == null && d.followers_pct_18_24 == null && (
                          <p className="text-xs text-slate-400">Sem dados de followers.</p>
                        )}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Funil por Canal */}
                {d.channel_funnel.length > 0 && (
                  <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
                    <div className="px-5 py-4 border-b border-violet-100">
                      <h2 className="text-sm font-semibold text-slate-700">Funil por Canal</h2>
                      <p className="text-xs text-slate-400 mt-0.5">Impressoes → pagina do produto → vendas</p>
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm" aria-label="Funil de conversao por canal">
                        <thead>
                          <tr className="bg-slate-50 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                            <SortableHeader label="Canal" column="channel" sort={funnelSort.sort} onSort={funnelSort.toggleSort} align="left" />
                            <SortableHeader label="Impressoes" column="impressions" sort={funnelSort.sort} onSort={funnelSort.toggleSort} />
                            <SortableHeader label="CTR%" column="ctr_pct" sort={funnelSort.sort} onSort={funnelSort.toggleSort} />
                            <SortableHeader label="Pag. Produto" column="page_views" sort={funnelSort.sort} onSort={funnelSort.toggleSort} />
                            <SortableHeader label="CVR%" column="cvr_pct" sort={funnelSort.sort} onSort={funnelSort.toggleSort} />
                            <SortableHeader label="GMV" column="gmv" sort={funnelSort.sort} onSort={funnelSort.toggleSort} />
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-50">
                          {funnelSort.sortedRows.map((ch) => (
                            <tr key={ch.channel} className="hover:bg-violet-50/50 transition-colors">
                              <td className="px-5 py-3.5 font-semibold text-slate-700">
                                <span className={`inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full font-semibold ${
                                  ch.channel === "VIDEO" ? "bg-violet-100 text-violet-700"
                                  : ch.channel === "LIVE" ? "bg-cyan-100 text-cyan-700"
                                  : "bg-amber-100 text-amber-700"
                                }`}>
                                  {ch.label}
                                </span>
                              </td>
                              <td className="px-4 py-3.5 text-right tabular-nums text-slate-600">
                                {fmtNumber(ch.impressions)}
                              </td>
                              <td className="px-4 py-3.5 text-right tabular-nums">
                                <span className="text-xs font-semibold text-slate-700">
                                  {ch.ctr_pct != null ? `${ch.ctr_pct.toFixed(2)}%` : "—"}
                                </span>
                              </td>
                              <td className="px-4 py-3.5 text-right tabular-nums text-slate-600">
                                {fmtNumber(ch.page_views)}
                              </td>
                              <td className="px-4 py-3.5 text-right tabular-nums">
                                <span className="text-xs font-semibold text-slate-700">
                                  {ch.cvr_pct != null ? `${ch.cvr_pct.toFixed(2)}%` : "—"}
                                </span>
                              </td>
                              <td className="px-5 py-3.5 text-right tabular-nums font-semibold text-slate-800">
                                {fmtBrl(ch.gmv)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="px-5 py-2.5 border-t border-slate-100">
                      <p className="text-[10px] text-slate-400">CTR = impressoes que geraram visita a pagina do produto · CVR = visitas que converteram em pedido</p>
                    </div>
                  </div>
                )}

                {/* Top Creators */}
                {d.top_creators.length > 0 && (
                  <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
                    <div className="px-5 py-4 border-b border-violet-100">
                      <h2 className="text-sm font-semibold text-slate-700">Top 5 Creators</h2>
                    </div>
                    <table className="w-full" aria-label="Top 5 creators por GMV">
                      <thead>
                        <tr className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                          <th className="text-left px-5 py-3">Creator</th>
                          <th className="text-right px-4 py-3">GMV</th>
                          <th className="text-right px-4 py-3">Videos</th>
                          <th className="text-right px-5 py-3">Lives</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.top_creators.map((c, i) => (
                          <tr
                            key={c.creator}
                            className={`border-t border-violet-100 hover:bg-violet-50/50 transition-colors ${i % 2 === 0 ? "" : "bg-gray-50/30"}`}
                          >
                            <td className="px-5 py-3 text-sm text-slate-700 font-medium">
                              <span className="text-slate-400 tabular-nums mr-2">{i + 1}.</span>
                              {c.creator}
                            </td>
                            <td className="text-right px-4 py-3 text-sm font-bold text-slate-900 tabular-nums">{fmtBrl(c.gmv)}</td>
                            <td className="text-right px-4 py-3 text-sm text-slate-600 tabular-nums">{fmtNumber(c.videos)}</td>
                            <td className="text-right px-5 py-3 text-sm text-slate-600 tabular-nums">{fmtNumber(c.lives)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* Top Produtos */}
                {d.top_produtos.length > 0 && (
                  <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
                    <div className="px-5 py-4 border-b border-violet-100">
                      <h2 className="text-sm font-semibold text-slate-700">Top 5 Produtos — TikTok Shop</h2>
                    </div>
                    <table className="w-full" aria-label="Top 5 produtos por GMV">
                      <thead>
                        <tr className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                          <th className="text-left px-5 py-3">Produto</th>
                          <th className="text-right px-4 py-3">GMV</th>
                          <th className="text-right px-4 py-3">Pedidos</th>
                          <th className="text-right px-4 py-3">Videos</th>
                          <th className="text-right px-5 py-3">GPM</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.top_produtos.map((p, i) => (
                          <tr
                            key={p.product_id}
                            className={`border-t border-violet-100 hover:bg-violet-50/50 transition-colors ${i % 2 === 0 ? "" : "bg-gray-50/30"}`}
                          >
                            <td className="px-5 py-3 text-sm text-slate-700 max-w-[200px]">
                              <span className="text-slate-400 tabular-nums mr-2">{i + 1}.</span>
                              <span className="font-medium truncate">{p.product_name}</span>
                            </td>
                            <td className="text-right px-4 py-3 text-sm font-bold text-slate-900 tabular-nums">{fmtBrl(p.gmv)}</td>
                            <td className="text-right px-4 py-3 text-sm text-slate-600 tabular-nums">{fmtNumber(p.orders)}</td>
                            <td className="text-right px-4 py-3 text-sm text-slate-600 tabular-nums">{fmtNumber(p.videos)}</td>
                            <td className="text-right px-5 py-3 text-sm text-slate-600 tabular-nums">
                              {p.gpm != null ? `R$${p.gpm.toFixed(2)}` : <span className="text-slate-300">—</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

              </div>
            )}
          </section>
        )}

        {/* Ultimos 7 dias */}
        <section aria-label="Ultimos 7 dias">
          <SectionTitle>Ultimos 7 Dias</SectionTitle>
          <div className="bg-white rounded-2xl shadow-sm border border-violet-100 overflow-hidden">
            <table className="w-full" aria-label="Ultimos 7 dias de performance">
              <caption className="sr-only">Dados diarios de GMV, pedidos e ticket medio dos ultimos 7 dias</caption>
              <thead>
                <tr className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                  <th className="text-left px-5 py-3">Data</th>
                  {hasTiktok && <th className="text-right px-4 py-3">TikTok GMV</th>}
                  {hasMl && <th className="text-right px-4 py-3">ML GMV</th>}
                  {hasShopee && <th className="text-right px-4 py-3">Shopee GMV</th>}
                  <th className="text-right px-4 py-3">GMV Total</th>
                  <th className="text-right px-4 py-3">Pedidos</th>
                  <th className="text-right px-5 py-3">Ticket Medio</th>
                </tr>
              </thead>
              <tbody>
                {last7.map((r, i) => (
                  <tr
                    key={r.date}
                    className={`border-t border-violet-100 hover:bg-violet-50/50 hover:shadow-[0_4px_12px_0_rgba(124,58,237,0.08),0_1px_3px_0_rgba(0,0,0,0.06)] transition-all duration-150 ${i % 2 === 0 ? "" : "bg-gray-50/30"}`}
                  >
                    <td className="px-5 py-3 text-sm text-gray-700 font-medium">
                      {new Date(r.date + "T00:00:00").toLocaleDateString("pt-BR", { day: "2-digit", month: "short" })}
                    </td>
                    {hasTiktok && (
                      <td className="text-right px-4 py-3 text-sm text-gray-600 tabular-nums">
                        {r.tiktok_gmv != null ? fmtBrl(r.tiktok_gmv) : <span className="text-slate-300">—</span>}
                      </td>
                    )}
                    {hasMl && (
                      <td className="text-right px-4 py-3 text-sm text-gray-600 tabular-nums">
                        {r.ml_gmv != null ? fmtBrl(r.ml_gmv) : <span className="text-slate-300">—</span>}
                      </td>
                    )}
                    {hasShopee && (
                      <td className="text-right px-4 py-3 text-sm text-gray-600 tabular-nums">
                        {r.shopee_gmv != null ? fmtBrl(r.shopee_gmv) : <span className="text-slate-300">—</span>}
                      </td>
                    )}
                    <td className="text-right px-4 py-3 font-bold text-gray-900 text-sm tabular-nums">
                      {fmtBrl(r.total_gmv)}
                    </td>
                    <td className="text-right px-4 py-3 text-sm text-gray-600 tabular-nums">
                      {fmtNumber(r.orders)}
                    </td>
                    <td className="text-right px-5 py-3 text-sm text-gray-600 tabular-nums">
                      {r.avg_ticket != null ? fmtBrl(r.avg_ticket) : <span className="text-slate-300">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

      </main>
    </div>
  );
}

export default function BrandPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#f8f7ff]" />}>
      <BrandPageInner />
    </Suspense>
  );
}
