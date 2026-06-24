"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import type { Marketplace } from "@/lib/mock-data";
import { generateDailyData, type DailyRow, AVAILABLE_MONTHS } from "@/lib/mock-daily";
import { fetchBrandDetail, type BrandDetail } from "@/lib/api-client";
import DailyChart from "@/components/DailyChart";
import ChannelMixChart from "@/components/ChannelMixChart";
import KpiCard from "@/components/KpiCard";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import PeriodSelector from "@/components/PeriodSelector";
import AppNav from "@/components/AppNav";
import { fmtBrl, fmtNumber, calcMoM } from "@/lib/formatters";

type Filter = Marketplace | "all";

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

function summarize(rows: DailyRow[], filter: Filter) {
  const gmv = rows.reduce((s, r) =>
    s + (filter === "tiktok" ? (r.tiktok_gmv ?? 0) : filter === "ml" ? (r.ml_gmv ?? 0) : r.total_gmv), 0);
  const orders = rows.reduce((s, r) => s + r.orders, 0);
  const adSpend = filter !== "tiktok" ? rows.reduce((s, r) => s + (r.ad_spend ?? 0), 0) : null;
  return { gmv, orders, adSpend, avgTicket: orders > 0 ? gmv / orders : 0 };
}

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

export default function BrandPage() {
  const { brand } = useParams<{ brand: string }>();
  const meta = BRAND_META[brand];

  const [filter, setFilter] = useState<Filter>("all");
  const [period, setPeriod] = useState<string>(AVAILABLE_MONTHS[0].value);
  const [daily, setDaily] = useState<DailyRow[]>([]);
  const [isLive, setIsLive] = useState(false);
  const [brandDetail, setBrandDetail] = useState<BrandDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const raf = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(
          `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080"}/api/v1/performance/daily?brand=${brand}&marketplace=${filter}&days_back=60`
        );
        if (res.ok) {
          const json = await res.json();
          setDaily(json.data);
          setIsLive(true);
          return;
        }
      } catch {/* api offline */}
      setDaily(generateDailyData(brand, 60));
      setIsLive(false);
    }
    load();
  }, [brand, filter]);

  useEffect(() => {
    setDetailLoading(true);
    fetchBrandDetail(brand, period).then((d) => {
      setBrandDetail(d);
      setDetailLoading(false);
    });
  }, [brand, period]);

  if (!meta) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-400">
        Brand &quot;{brand}&quot; nao encontrado.{" "}
        <Link href="/" className="text-violet-600 underline ml-1">Voltar</Link>
      </div>
    );
  }

  const last30 = daily.slice(-30);
  const prev30 = daily.slice(-60, -30);
  const cur = summarize(last30, filter);
  const prev = summarize(prev30, filter);
  const gmvMoM = prev.gmv > 0 ? calcMoM(cur.gmv, prev.gmv) : null;

  const hasTiktok = ["all", "tiktok"].includes(filter) && daily.some((r) => r.tiktok_gmv != null);
  const hasMl = ["all", "ml"].includes(filter) && daily.some((r) => r.ml_gmv != null);
  const showTikTokDetail = filter !== "ml";

  const chartData = filter === "tiktok"
    ? daily.map((r) => ({ ...r, ml_gmv: null, total_gmv: r.tiktok_gmv ?? 0 }))
    : filter === "ml"
    ? daily.map((r) => ({ ...r, tiktok_gmv: null, total_gmv: r.ml_gmv ?? 0 }))
    : daily;

  const last7 = [...daily].reverse().slice(0, 7);
  const d = brandDetail;

  return (
    <div className="min-h-screen bg-[#f8f7ff]">
      <header className="bg-white border-b border-violet-100 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href="/"
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
              href={`/brand/${b.slug}`}
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

        {/* Filtro de canal */}
        <MarketplaceFilter value={filter} onChange={setFilter} />

        {/* Tendencia — ultimos 30d */}
        <section aria-label="Tendencia dos ultimos 30 dias">
          <SectionTitle>Tendencia — Ultimos 30 dias</SectionTitle>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <KpiCard label="GMV (30d)" value={fmtBrl(cur.gmv)} mom={gmvMoM} accent={meta.color} />
            <KpiCard label="Pedidos (30d)" value={fmtNumber(cur.orders)} accent="bg-cyan-500" />
            <KpiCard label="Ticket Medio" value={fmtBrl(cur.avgTicket)} accent="bg-amber-500" />
            {cur.adSpend != null && cur.adSpend > 0 ? (
              <KpiCard
                label="Ad Spend (30d)"
                value={fmtBrl(cur.adSpend)}
                subvalue={`ROAS ~${(cur.gmv / cur.adSpend).toFixed(1)}x`}
                accent="bg-emerald-500"
              />
            ) : (
              <KpiCard label="Ad Spend" value="—" subvalue="N/D para TikTok Shop" accent="bg-slate-300" />
            )}
          </div>
          <DailyChart data={chartData} hasTiktok={hasTiktok} hasMl={hasMl} />
        </section>

        {/* Deep-dive mensal — TikTok Shop */}
        {showTikTokDetail && (
          <section aria-label="Analise mensal TikTok Shop">
            <div className="flex flex-wrap items-center justify-between gap-4 mb-4">
              <SectionTitle>TikTok Shop — Analise Mensal</SectionTitle>
              <PeriodSelector value={period} onChange={setPeriod} />
            </div>

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
                            <th className="text-left px-5 py-3">Canal</th>
                            <th className="text-right px-4 py-3">Impressoes</th>
                            <th className="text-right px-4 py-3">CTR%</th>
                            <th className="text-right px-4 py-3">Pag. Produto</th>
                            <th className="text-right px-4 py-3">CVR%</th>
                            <th className="text-right px-5 py-3">GMV</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-50">
                          {d.channel_funnel.map((ch) => (
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
