"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fetchProdutosML, fetchProdutosTikTok, fetchProdutosMLSummary, fetchProdutosShopee,
  type ProdutoMLRow, type ProdutoTikTokRow, type ProdutosMLSummary, type ProdutoShopeeRow,
} from "@/lib/api-client";
import AppNav from "@/components/AppNav";
import PeriodSelector from "@/components/PeriodSelector";
import { SkeletonTableRows } from "@/components/Skeleton";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { AVAILABLE_MONTHS } from "@/lib/mock-daily";

type Tab = "ml" | "tiktok" | "shopee";
type BrandML = "" | "barbours" | "kokeshi" | "lescent" | "rituaria";
type BrandTK = "" | "apice" | "barbours" | "kokeshi" | "lescent" | "rituaria";
type BrandSH = "" | "apice" | "barbours" | "kokeshi" | "lescent" | "rituaria";
type ParetoBucket = "" | "A_top50" | "B_next30" | "C_next15" | "D_tail";
type ProductStatus = "" | "sells+advertised" | "sells_organic_only" | "ad_spend_no_sales" | "inactive";
type VelocityFilter = "" | "high" | "medium" | "low" | "zero";

const PAGE_SIZE = 25;

const PARETO_LABEL: Record<string, string> = {
  A_top50: "A", B_next30: "B", C_next15: "C", D_tail: "D",
};
const PARETO_COLOR: Record<string, string> = {
  A_top50: "bg-violet-100 text-violet-800",
  B_next30: "bg-cyan-100 text-cyan-800",
  C_next15: "bg-amber-100 text-amber-800",
  D_tail:   "bg-slate-100 text-slate-500",
};
const VELOCITY_COLOR: Record<string, string> = {
  high:   "text-emerald-700",
  medium: "text-amber-700",
  low:    "text-slate-500",
  zero:   "text-rose-600",
};
const EFFICIENCY_COLOR: Record<string, string> = {
  star:        "bg-amber-100 text-amber-800",
  efficient:   "bg-emerald-100 text-emerald-800",
  marginal:    "bg-slate-100 text-slate-600",
  inefficient: "bg-rose-100 text-rose-700",
  no_ads:      "bg-slate-100 text-slate-500",
  no_return:   "bg-rose-100 text-rose-800",
};
const EFFICIENCY_LABEL: Record<string, string> = {
  star: "estrela", efficient: "eficiente", marginal: "marginal",
  inefficient: "ineficiente", no_ads: "sem ads", no_return: "sem retorno",
};
const SIGNAL_COLOR: Record<string, string> = {
  "ACAO:": "bg-emerald-50 border-emerald-200 text-emerald-800",
  "ALERTA:": "bg-rose-50 border-rose-200 text-rose-800",
  "ATENCAO:": "bg-amber-50 border-amber-200 text-amber-800",
  "OPORTUNIDADE:": "bg-cyan-50 border-cyan-200 text-cyan-800",
  "REVIEW:": "bg-slate-50 border-slate-200 text-slate-600",
};

function signalStyle(signal: string | null): string {
  if (!signal) return "";
  const key = Object.keys(SIGNAL_COLOR).find((k) => signal.startsWith(k));
  return key ? SIGNAL_COLOR[key] : "bg-slate-50 border-slate-200 text-slate-600";
}

function AttributionBar({ v, l, c }: { v: number | null; l: number | null; c: number | null }) {
  const vp = v ?? 0; const lp = l ?? 0; const cp = c ?? 0;
  const other = Math.max(0, 100 - vp - lp - cp);
  return (
    <div className="flex h-1.5 rounded-full overflow-hidden w-20 bg-slate-100">
      <div className="bg-violet-500" style={{ width: `${vp}%` }} />
      <div className="bg-rose-500"   style={{ width: `${lp}%` }} />
      <div className="bg-sky-400"    style={{ width: `${cp}%` }} />
      {other > 0.5 && <div className="bg-slate-300" style={{ width: `${other}%` }} />}
    </div>
  );
}

const PARETO_STRIP_COLORS: Record<string, { bg: string; border: string; text: string; bar: string; sub: string }> = {
  A_top50:  { bg: "bg-emerald-50", border: "border-emerald-200", text: "text-emerald-800", bar: "bg-emerald-500", sub: "text-emerald-600" },
  B_next30: { bg: "bg-cyan-50",    border: "border-cyan-200",    text: "text-cyan-800",    bar: "bg-cyan-500",    sub: "text-cyan-600"    },
  C_next15: { bg: "bg-amber-50",   border: "border-amber-200",   text: "text-amber-800",   bar: "bg-amber-500",   sub: "text-amber-600"   },
  D_tail:   { bg: "bg-rose-50",    border: "border-rose-200",    text: "text-rose-800",    bar: "bg-rose-500",    sub: "text-rose-600"    },
};

function ParetoStrip({ summary }: { summary: ProdutosMLSummary }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 px-6 py-4 border-b border-violet-50">
      {summary.buckets.map((b) => {
        const c = PARETO_STRIP_COLORS[b.bucket] ?? PARETO_STRIP_COLORS["D_tail"];
        return (
          <div key={b.bucket} className={`rounded-xl border px-4 py-3 ${c.bg} ${c.border}`}>
            <div className="flex items-baseline justify-between mb-2">
              <span className={`text-lg font-bold tabular-nums ${c.text}`}>{b.label}</span>
              <span className={`text-xs font-semibold tabular-nums ${c.sub}`}>{b.gmv_pct.toFixed(1)}%</span>
            </div>
            <div className="h-1 rounded-full bg-white/60 overflow-hidden mb-2">
              <div className={`h-1 rounded-full ${c.bar}`} style={{ width: `${b.gmv_pct}%` }} />
            </div>
            <p className={`text-xs font-semibold tabular-nums ${c.text}`}>
              {b.gmv >= 1_000_000
                ? `R$ ${(b.gmv / 1_000_000).toFixed(1)}M`
                : `R$ ${(b.gmv / 1_000).toFixed(0)}K`}
            </p>
            <p className={`text-[11px] tabular-nums mt-0.5 ${c.sub}`}>{b.count} produtos · {b.description}</p>
          </div>
        );
      })}
    </div>
  );
}

function Pagination({ total, limit, offset, onChange }: {
  total: number; limit: number; offset: number; onChange: (offset: number) => void;
}) {
  const pages = Math.ceil(total / limit);
  const current = Math.floor(offset / limit);
  if (pages <= 1) return null;

  const visible: (number | "…")[] = [];
  for (let i = 0; i < pages; i++) {
    if (i === 0 || i === pages - 1 || Math.abs(i - current) <= 2) {
      visible.push(i);
    } else if (visible[visible.length - 1] !== "…") {
      visible.push("…");
    }
  }

  return (
    <div className="flex items-center justify-between px-6 py-3 border-t border-slate-50">
      <p className="text-xs text-slate-500 tabular-nums">
        {offset + 1}–{Math.min(offset + limit, total)} de {fmtNumber(total)} produtos
      </p>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onChange(Math.max(0, offset - limit))}
          disabled={offset === 0}
          className="px-2 py-1 text-xs rounded text-slate-500 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-default transition-colors"
        >
          ‹
        </button>
        {visible.map((v, i) =>
          v === "…" ? (
            <span key={`e${i}`} className="px-1 text-xs text-slate-400">…</span>
          ) : (
            <button
              key={v}
              onClick={() => onChange((v as number) * limit)}
              className={`w-7 h-7 text-xs rounded transition-colors ${
                v === current
                  ? "bg-violet-600 text-white font-semibold"
                  : "text-slate-500 hover:bg-slate-100"
              }`}
            >
              {(v as number) + 1}
            </button>
          )
        )}
        <button
          onClick={() => onChange(Math.min((pages - 1) * limit, offset + limit))}
          disabled={offset + limit >= total}
          className="px-2 py-1 text-xs rounded text-slate-500 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-default transition-colors"
        >
          ›
        </button>
      </div>
    </div>
  );
}

export default function ProdutosPage() {
  const [tab, setTab] = useState<Tab>("ml");
  const [period, setPeriod] = useState(AVAILABLE_MONTHS[0].value);

  // ML state
  const [mlBrand, setMlBrand] = useState<BrandML>("");
  const [mlPareto, setMlPareto] = useState<ParetoBucket>("");
  const [mlSignal, setMlSignal] = useState("");
  const [mlStatus, setMlStatus] = useState<ProductStatus>("");
  const [mlVelocity, setMlVelocity] = useState<VelocityFilter>("");
  const [mlOffset, setMlOffset] = useState(0);
  const [mlData, setMlData] = useState<{ total: number; items: ProdutoMLRow[] } | null>(null);
  const [mlLoading, setMlLoading] = useState(false);
  const [mlSummary, setMlSummary] = useState<ProdutosMLSummary | null>(null);

  // TikTok state
  const [tkBrand, setTkBrand] = useState<BrandTK>("");
  const [tkOffset, setTkOffset] = useState(0);
  const [tkData, setTkData] = useState<{ total: number; ref_month: string; items: ProdutoTikTokRow[] } | null>(null);
  const [tkLoading, setTkLoading] = useState(false);

  // Shopee state
  const [shBrand, setShBrand] = useState<BrandSH>("");
  const [shOffset, setShOffset] = useState(0);
  const [shData, setShData] = useState<{ total: number; ref_month: string; items: ProdutoShopeeRow[] } | null>(null);
  const [shLoading, setShLoading] = useState(false);

  const [isLive, setIsLive] = useState(false);

  const loadML = useCallback(() => {
    setMlLoading(true);
    fetchProdutosML({
      brand: mlBrand || undefined,
      pareto_bucket: mlPareto || undefined,
      action_signal: mlSignal || undefined,
      product_status: mlStatus || undefined,
      revenue_velocity: mlVelocity || undefined,
      limit: PAGE_SIZE,
      offset: mlOffset,
    }).then((r) => {
      if (r) { setMlData(r); setIsLive(true); }
      setMlLoading(false);
    }).catch(() => setMlLoading(false));
  }, [mlBrand, mlPareto, mlSignal, mlStatus, mlVelocity, mlOffset]);

  const loadTK = useCallback(() => {
    setTkLoading(true);
    fetchProdutosTikTok({
      brand: tkBrand || undefined,
      period,
      limit: PAGE_SIZE,
      offset: tkOffset,
    }).then((r) => {
      if (r) { setTkData(r); setIsLive(true); }
      setTkLoading(false);
    }).catch(() => setTkLoading(false));
  }, [tkBrand, period, tkOffset]);

  const loadSH = useCallback(() => {
    setShLoading(true);
    fetchProdutosShopee({
      brand: shBrand || undefined,
      period,
      limit: PAGE_SIZE,
      offset: shOffset,
    }).then((r) => {
      if (r) { setShData(r); setIsLive(true); }
      setShLoading(false);
    }).catch(() => setShLoading(false));
  }, [shBrand, period, shOffset]);

  useEffect(() => { if (tab === "ml") loadML(); }, [tab, loadML]);
  useEffect(() => { if (tab === "tiktok") loadTK(); }, [tab, loadTK]);
  useEffect(() => { if (tab === "shopee") loadSH(); }, [tab, loadSH]);

  useEffect(() => {
    if (tab !== "ml") return;
    fetchProdutosMLSummary(mlBrand || undefined).then((r) => { if (r) setMlSummary(r); });
  }, [tab, mlBrand]);

  // Reset offset when filters change
  useEffect(() => { setMlOffset(0); }, [mlBrand, mlPareto, mlSignal, mlStatus, mlVelocity]);
  useEffect(() => { setTkOffset(0); }, [tkBrand, period]);
  useEffect(() => { setShOffset(0); }, [shBrand, period]);

  const filterSelect = "text-sm border border-slate-200 rounded-lg px-3 py-1.5 text-slate-600 bg-white focus:outline-none focus:ring-2 focus:ring-violet-500 focus:border-transparent";

  return (
    <div className="min-h-screen bg-[#f8f7ff]">
      <header className="bg-white border-b border-violet-100 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-violet-600 flex items-center justify-center">
              <span className="text-white font-bold text-xs tracking-tight">TC</span>
            </div>
            <div>
              <h1 className="text-lg font-bold text-gray-900 leading-none">Torre de Controle</h1>
              <p className="text-xs text-slate-500">Gobeaute · Marketplaces</p>
            </div>
          </div>
          <span className={`text-xs rounded-lg px-3 py-1.5 font-medium ${isLive ? "text-emerald-700 bg-emerald-50 border border-emerald-200" : "text-amber-700 bg-amber-50 border border-amber-200"}`}>
            {isLive ? "Dados ao vivo · API conectada" : "Demonstração · API offline"}
          </span>
        </div>
      </header>

      <AppNav />

      <main className="max-w-7xl mx-auto px-6 py-8 flex flex-col gap-6">
        {/* Tabs */}
        <div className="flex items-center gap-1 bg-white border border-violet-100 rounded-xl p-1 w-fit shadow-sm">
          {(["ml", "tiktok", "shopee"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                tab === t
                  ? "bg-violet-600 text-white shadow-sm"
                  : "text-slate-500 hover:text-slate-700 hover:bg-slate-50"
              }`}
            >
              {t === "ml" ? "Mercado Livre" : t === "tiktok" ? "TikTok Shop" : "Shopee"}
            </button>
          ))}
        </div>

        {/* ML view */}
        {tab === "ml" && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            {/* Filter bar */}
            <div className="px-6 py-4 border-b border-violet-50 flex items-center gap-3 flex-wrap">
              <select className={filterSelect} value={mlBrand} onChange={(e) => setMlBrand(e.target.value as BrandML)}>
                <option value="">Todas as marcas</option>
                <option value="barbours">BARBOURS</option>
                <option value="kokeshi">KOKESHI</option>
                <option value="lescent">LESCENT</option>
                <option value="rituaria">RITUARIA</option>
              </select>
              <select className={filterSelect} value={mlPareto} onChange={(e) => setMlPareto(e.target.value as ParetoBucket)}>
                <option value="">Todos os buckets</option>
                <option value="A_top50">A — Top 50%</option>
                <option value="B_next30">B — Next 30%</option>
                <option value="C_next15">C — Next 15%</option>
                <option value="D_tail">D — Cauda</option>
              </select>
              <select className={filterSelect} value={mlStatus} onChange={(e) => setMlStatus(e.target.value as ProductStatus)}>
                <option value="">Todos os status</option>
                <option value="sells+advertised">Vende + anunciado</option>
                <option value="sells_organic_only">Vende organico</option>
                <option value="ad_spend_no_sales">Gasta ads, sem venda</option>
                <option value="inactive">Inativo</option>
              </select>
              <select className={filterSelect} value={mlVelocity} onChange={(e) => setMlVelocity(e.target.value as VelocityFilter)}>
                <option value="">Toda velocidade</option>
                <option value="high">Alta velocidade</option>
                <option value="medium">Media velocidade</option>
                <option value="low">Baixa velocidade</option>
                <option value="zero">Sem vendas</option>
              </select>
              <select className={filterSelect} value={mlSignal} onChange={(e) => setMlSignal(e.target.value)}>
                <option value="">Todos os sinais</option>
                <option value="ACAO: aumentar investimento (ROAS > 15x)">Aumentar investimento</option>
                <option value="ACAO: considerar pausar ads (ROAS &lt; 3x)">Considerar pausar ads</option>
                <option value="ALERTA: taxa cancelamento alta (> 10%)">Cancelamento alto</option>
                <option value="OPORTUNIDADE: produto vende organico, considerar ads">Oportunidade organica</option>
                <option value="REVIEW: spend sem vendas no período de orders">Review spend</option>
                <option value="ATENCAO: grande variacao de preco">Variacao de preco</option>
              </select>
              {mlData && (
                <span className="text-xs text-slate-500 tabular-nums ml-auto">{fmtNumber(mlData.total)} produtos</span>
              )}
            </div>

            {mlSummary && <ParetoStrip summary={mlSummary} />}

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 text-left">
                    <th className="px-6 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Produto</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Marca</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Receita</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Unid.</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-center">Pareto</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Cancel.</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">ROAS</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-center">Efic. Ads</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Sinal</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {mlLoading && !mlData && <SkeletonTableRows rows={8} cols={9} />}
                  {!mlData && !mlLoading && (
                    <tr><td colSpan={9} className="px-6 py-12 text-center text-slate-500">API offline — dados de produtos indisponíveis sem conexão.</td></tr>
                  )}
                  {mlData && (
                  <>{mlData.items.map((p) => (
                    <tr key={p.item_id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-6 py-3 max-w-xs">
                        <p className="text-slate-700 font-medium truncate leading-tight">{p.title}</p>
                        {p.seller_sku && <p className="text-[11px] text-slate-500 tabular-nums mt-0.5">{p.seller_sku}</p>}
                      </td>
                      <td className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase whitespace-nowrap">{p.brand}</td>
                      <td className="px-4 py-3 text-right whitespace-nowrap">
                        <p className="tabular-nums text-slate-700 font-medium">{fmtBrl(p.gross_revenue)}</p>
                        {p.revenue_share_pct != null && (
                          <p className="text-[11px] text-slate-500 tabular-nums mt-0.5">{p.revenue_share_pct.toFixed(1)}% do total</p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right whitespace-nowrap">
                        <p className="tabular-nums text-slate-600">{fmtNumber(p.units_sold)}</p>
                        {p.unique_buyers != null && (
                          <p className="text-[11px] text-slate-500 tabular-nums mt-0.5">{fmtNumber(p.unique_buyers)} compr.</p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <div className="flex flex-col items-center gap-1">
                          {p.pareto_bucket && (
                            <span className={`inline-block text-[10px] font-bold px-1.5 py-0.5 rounded ${PARETO_COLOR[p.pareto_bucket] ?? "bg-slate-100 text-slate-500"}`}>
                              {PARETO_LABEL[p.pareto_bucket] ?? p.pareto_bucket}
                            </span>
                          )}
                          {p.revenue_velocity && (
                            <span className={`text-[10px] font-medium ${VELOCITY_COLOR[p.revenue_velocity] ?? "text-slate-500"}`}>
                              {p.revenue_velocity}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {p.cancel_rate_pct != null ? (
                          <span className={p.cancel_rate_pct < 2 ? "text-emerald-700" : p.cancel_rate_pct < 5 ? "text-amber-700 font-semibold" : "text-rose-700 font-semibold"}>
                            {p.cancel_rate_pct.toFixed(1)}%
                          </span>
                        ) : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {p.ad_roas != null ? (
                          <span className={p.ad_roas >= 4 ? "text-emerald-700 font-semibold" : p.ad_roas >= 2.5 ? "text-amber-700" : "text-rose-700"}>
                            {p.ad_roas.toFixed(1)}x
                          </span>
                        ) : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {p.ad_efficiency && (
                          <span className={`inline-block text-[10px] font-semibold px-1.5 py-0.5 rounded ${EFFICIENCY_COLOR[p.ad_efficiency] ?? "bg-slate-100 text-slate-500"}`}>
                            {EFFICIENCY_LABEL[p.ad_efficiency] ?? p.ad_efficiency}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 max-w-[200px]">
                        {p.action_signal && (
                          <span className={`inline-block text-[10px] font-medium px-2 py-0.5 rounded border leading-tight ${signalStyle(p.action_signal)}`}>
                            {p.action_signal}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}</>
                  )}
                  {mlData && mlLoading && (
                    <tr><td colSpan={9} className="px-6 py-3 text-center">
                      <span className="text-xs text-violet-400 animate-pulse">Atualizando...</span>
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>

            {mlData && (
              <Pagination
                total={mlData.total}
                limit={PAGE_SIZE}
                offset={mlOffset}
                onChange={setMlOffset}
              />
            )}
          </div>
        )}

        {/* TikTok view */}
        {tab === "tiktok" && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            {/* Filter bar */}
            <div className="px-6 py-4 border-b border-violet-50 flex items-center gap-3 flex-wrap">
              <select className={filterSelect} value={tkBrand} onChange={(e) => setTkBrand(e.target.value as BrandTK)}>
                <option value="">Todas as marcas</option>
                <option value="apice">APICE</option>
                <option value="barbours">BARBOURS</option>
                <option value="kokeshi">KOKESHI</option>
                <option value="lescent">LESCENT</option>
                <option value="rituaria">RITUARIA</option>
              </select>
              <PeriodSelector value={period} onChange={setPeriod} />
              {tkData && (
                <span className="text-xs text-slate-400 tabular-nums ml-auto">{fmtNumber(tkData.total)} produtos</span>
              )}
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 text-left">
                    <th className="px-6 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Produto</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Marca</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">GMV</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Pedidos</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Canal</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Prob.%</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Rating</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {tkLoading && !tkData && <SkeletonTableRows rows={8} cols={7} />}
                  {!tkData && !tkLoading && (
                    <tr><td colSpan={7} className="px-6 py-12 text-center text-slate-500">API offline — dados de produtos indisponíveis sem conexão.</td></tr>
                  )}
                  {tkData && <>{tkData.items.map((p) => (
                    <tr key={p.product_id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-6 py-3 max-w-xs">
                        <p className="text-slate-700 font-medium truncate leading-tight">{p.product_name}</p>
                        <p className="text-[11px] text-slate-500 tabular-nums mt-0.5">{p.product_id}</p>
                      </td>
                      <td className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase whitespace-nowrap">{p.brand}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-700 font-medium whitespace-nowrap">{fmtBrl(p.gmv)}</td>
                      <td className="px-4 py-3 text-right whitespace-nowrap">
                        <p className="tabular-nums text-slate-600">{fmtNumber(p.orders)} ped.</p>
                        {p.items_sold != null && p.items_sold !== p.orders && (
                          <p className="text-[11px] text-slate-500 tabular-nums mt-0.5">{fmtNumber(p.items_sold)} unid.</p>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-col gap-1">
                          <AttributionBar v={p.pct_gmv_video} l={p.pct_gmv_live} c={p.pct_gmv_card} />
                          <p className="text-[10px] text-slate-500 tabular-nums">
                            {[
                              p.pct_gmv_video != null && `V ${p.pct_gmv_video.toFixed(0)}%`,
                              p.pct_gmv_live  != null && `L ${p.pct_gmv_live.toFixed(0)}%`,
                              p.pct_gmv_card  != null && `C ${p.pct_gmv_card.toFixed(0)}%`,
                            ].filter(Boolean).join(" · ")}
                          </p>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {p.problem_rate != null ? (
                          <span className={p.problem_rate < 2 ? "text-emerald-700" : p.problem_rate < 5 ? "text-amber-700" : "text-rose-700 font-semibold"}>
                            {p.problem_rate.toFixed(1)}%
                          </span>
                        ) : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-4 py-3 text-right whitespace-nowrap">
                        {p.rating_avg != null ? (
                          <>
                            <p className="tabular-nums text-slate-600">{p.rating_avg.toFixed(1)}</p>
                            {p.total_ratings != null && (
                              <p className="text-[11px] text-slate-500 tabular-nums mt-0.5">{fmtNumber(p.total_ratings)} aval.</p>
                            )}
                          </>
                        ) : <span className="text-slate-300">—</span>}
                      </td>
                    </tr>
                  ))}</>}
                  {tkData && tkLoading && (
                    <tr><td colSpan={7} className="px-6 py-3 text-center">
                      <span className="text-xs text-violet-400 animate-pulse">Atualizando...</span>
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>

            {tkData && (
              <Pagination
                total={tkData.total}
                limit={PAGE_SIZE}
                offset={tkOffset}
                onChange={setTkOffset}
              />
            )}
          </div>
        )}

        {/* Shopee view */}
        {tab === "shopee" && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            {/* Filter bar */}
            <div className="px-6 py-4 border-b border-violet-50 flex items-center gap-3 flex-wrap">
              <select className={filterSelect} value={shBrand} onChange={(e) => setShBrand(e.target.value as BrandSH)}>
                <option value="">Todas as marcas</option>
                <option value="apice">APICE</option>
                <option value="barbours">BARBOURS</option>
                <option value="kokeshi">KOKESHI</option>
                <option value="lescent">LESCENT</option>
                <option value="rituaria">RITUARIA</option>
              </select>
              <PeriodSelector value={period} onChange={setPeriod} />
              {shData && (
                <span className="text-xs text-slate-400 tabular-nums ml-auto">{fmtNumber(shData.total)} produtos</span>
              )}
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 text-left">
                    <th className="px-6 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Produto</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Variação</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">SKU</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Marca</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">GMV</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Unid.</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Pedidos</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Cancel.%</th>
                    <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Ticket Médio</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {shLoading && !shData && <SkeletonTableRows rows={8} cols={9} />}
                  {!shData && !shLoading && (
                    <tr><td colSpan={9} className="px-6 py-12 text-center text-slate-500">API offline — dados de produtos Shopee indisponíveis sem conexão.</td></tr>
                  )}
                  {shData && shData.total === 0 && (
                    <tr><td colSpan={9} className="px-6 py-12 text-center text-slate-500">Sem dados de produtos Shopee para este período.</td></tr>
                  )}
                  {shData && shData.total > 0 && <>{shData.items.map((p, i) => (
                    <tr key={`${p.brand}-${p.product_name}-${p.variation_name ?? ""}-${i}`} className="hover:bg-slate-50 transition-colors">
                      <td className="px-6 py-3 max-w-xs">
                        <p className="text-slate-700 font-medium truncate leading-tight">{p.product_name}</p>
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-500 max-w-[120px] truncate">
                        {p.variation_name ?? <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-4 py-3 text-[11px] text-slate-500 tabular-nums whitespace-nowrap">
                        {p.sku_ref ?? <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase whitespace-nowrap">{p.brand}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-700 font-medium whitespace-nowrap">{fmtBrl(p.gmv)}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-600 whitespace-nowrap">{fmtNumber(p.units_sold)}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-600 whitespace-nowrap">{fmtNumber(p.orders)}</td>
                      <td className="px-4 py-3 text-right tabular-nums whitespace-nowrap">
                        {p.cancel_rate_pct != null ? (
                          <span className={p.cancel_rate_pct < 2 ? "text-emerald-700" : p.cancel_rate_pct < 5 ? "text-amber-700 font-semibold" : "text-rose-700 font-semibold"}>
                            {p.cancel_rate_pct.toFixed(1)}%
                          </span>
                        ) : <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-600 whitespace-nowrap">
                        {p.avg_price != null ? fmtBrl(p.avg_price) : <span className="text-slate-300">—</span>}
                      </td>
                    </tr>
                  ))}</>}
                  {shData && shLoading && (
                    <tr><td colSpan={9} className="px-6 py-3 text-center">
                      <span className="text-xs text-violet-400 animate-pulse">Atualizando...</span>
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>

            {shData && shData.total > 0 && (
              <Pagination
                total={shData.total}
                limit={PAGE_SIZE}
                offset={shOffset}
                onChange={setShOffset}
              />
            )}
          </div>
        )}
      </main>
    </div>
  );
}
