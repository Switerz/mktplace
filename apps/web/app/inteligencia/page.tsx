"use client";

import { useEffect, useState } from "react";
import AppNav from "@/components/AppNav";
import KpiCard from "@/components/KpiCard";
import {
  fetchInteligencia,
  type InteligenciaData,
  type ProductSignalRow,
  type ParetoRow,
  type LtvRow,
  type TkProductRow,
} from "@/lib/api-client";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { useSortableTable, type SortColumnType } from "@/lib/use-sortable-table";
import SortableHeader from "@/components/SortableHeader";

const BRAND_LABELS: Record<string, string> = {
  apice: "ÁPICE",
  barbours: "BARBOURS",
  kokeshi: "KOKESHI",
  lescent: "LESCENT",
  rituaria: "RITUÁRIA",
};

const ML_BRANDS = ["barbours", "kokeshi", "lescent"];

const PARETO_COLORS: Record<string, string> = {
  A_top50: "bg-violet-600",
  B_next30: "bg-violet-400",
  C_next15: "bg-violet-200",
  D_tail: "bg-slate-200",
};

const PARETO_TEXT: Record<string, string> = {
  A_top50: "text-white",
  B_next30: "text-white",
  C_next15: "text-violet-800",
  D_tail: "text-slate-600",
};

const VELOCITY_STYLES: Record<string, string> = {
  high: "bg-emerald-100 text-emerald-800",
  medium: "bg-amber-100 text-amber-800",
  low: "bg-rose-100 text-rose-700",
  zero: "bg-slate-100 text-slate-500",
};

const VELOCITY_LABELS: Record<string, string> = {
  high: "Alta",
  medium: "Média",
  low: "Baixa",
  zero: "Zero",
};

function truncate(s: string | null | undefined, n: number) {
  if (!s) return "—";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function fmt(v: number | null | undefined, decimals = 1): string {
  if (v == null) return "—";
  return v.toFixed(decimals);
}

function VelocityBadge({ v }: { v: string | null }) {
  if (!v) return <span className="text-slate-300">—</span>;
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${VELOCITY_STYLES[v] ?? "bg-slate-100 text-slate-500"}`}>
      {VELOCITY_LABELS[v] ?? v}
    </span>
  );
}

function ParetoBadge({ v }: { v: string | null }) {
  if (!v) return <span className="text-slate-300">—</span>;
  const label = v.replace("_top50", "").replace("_next30", "").replace("_next15", "").replace("_tail", "");
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-bold ${PARETO_COLORS[v] ?? "bg-slate-100"} ${PARETO_TEXT[v] ?? "text-slate-700"}`}>
      {label}
    </span>
  );
}

function SectionHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="px-6 py-4 border-b border-violet-50">
      <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
      {subtitle && <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>}
    </div>
  );
}

function TableWrap({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">{children}</table>
    </div>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={`px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider ${right ? "text-right" : "text-left"}`}>
      {children}
    </th>
  );
}

export default function InteligenciaPage() {
  const [data, setData] = useState<InteligenciaData | null>(null);
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [brandFilter, setBrandFilter] = useState<string>("all");

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchInteligencia()
      .then((res) => {
        setData(res.data);
        setIsLive(res.live);
        setLoading(false);
      })
      .catch(() => {
        setError("Falha ao carregar dados de inteligência. Verifique a conexão.");
        setLoading(false);
      });
  }, [retryKey]);

  // Signals por status
  const statusMeta: Record<string, { label: string; accent: string; buildSub: (gmv: number, spend: number, n: number, roas: number | null) => string }> = {
    "sells+advertised": {
      label: "Vende + Ads",
      accent: "bg-violet-600",
      buildSub: (_g, _s, _n, roas) => `ROAS médio: ${roas != null ? roas.toFixed(1) + "x" : "—"}`,
    },
    sells_organic_only: {
      label: "Vende Orgânico",
      accent: "bg-amber-500",
      buildSub: (gmv) => `${fmtBrl(gmv)} sem ads`,
    },
    ad_spend_no_sales: {
      label: "Ads sem Venda",
      accent: "bg-rose-600",
      buildSub: (_g, spend) => `${fmtBrl(spend)} gastos desperdicados`,
    },
    inactive: {
      label: "Inativo",
      accent: "bg-slate-400",
      buildSub: (_g, _s, n) => `${n} produtos parados`,
    },
  };

  const signalMap = Object.fromEntries((data?.signals ?? []).map((s) => [s.product_status, s]));

  const filteredUrgent = (data?.urgent ?? []).filter(
    (r) => brandFilter === "all" || r.brand === brandFilter
  );
  const filteredScale = (data?.scale ?? []).filter(
    (r) => brandFilter === "all" || r.brand === brandFilter
  );
  const filteredOrganic = (data?.organic ?? [])
    .filter((r) => brandFilter === "all" || r.brand === brandFilter)
    .slice(0, 10);

  // Ordenação — Urgente: Parar Agora
  const urgentColumnTypes: Record<string, SortColumnType> = {
    brand: "text",
    gmv: "numeric",
    ad_spend: "numeric",
    days_advertised: "numeric",
  };
  function getUrgentValue(row: ProductSignalRow, column: string) {
    switch (column) {
      case "brand":
        return BRAND_LABELS[row.brand] ?? row.brand;
      case "gmv":
        return row.gmv;
      case "ad_spend":
        return row.ad_spend;
      case "days_advertised":
        return row.days_advertised;
      default:
        return null;
    }
  }
  const urgentSort = useSortableTable(filteredUrgent, getUrgentValue, urgentColumnTypes);

  // Ordenação — Escalar Agora
  const scaleColumnTypes: Record<string, SortColumnType> = {
    brand: "text",
    gmv: "numeric",
    ad_roas: "numeric",
    ad_acos_pct: "numeric",
    revenue_share_pct: "numeric",
  };
  function getScaleValue(row: ProductSignalRow, column: string) {
    switch (column) {
      case "brand":
        return BRAND_LABELS[row.brand] ?? row.brand;
      case "gmv":
        return row.gmv;
      case "ad_roas":
        return row.ad_roas;
      case "ad_acos_pct":
        return row.ad_acos_pct;
      case "revenue_share_pct":
        return row.revenue_share_pct;
      default:
        return null;
    }
  }
  const scaleSort = useSortableTable(filteredScale, getScaleValue, scaleColumnTypes);

  // Ordenação — Testar Ads
  const organicColumnTypes: Record<string, SortColumnType> = {
    brand: "text",
    gmv: "numeric",
    units_sold: "numeric",
    cancel_rate_pct: "numeric",
  };
  function getOrganicValue(row: ProductSignalRow, column: string) {
    switch (column) {
      case "brand":
        return BRAND_LABELS[row.brand] ?? row.brand;
      case "gmv":
        return row.gmv;
      case "units_sold":
        return row.units_sold;
      case "cancel_rate_pct":
        return row.cancel_rate_pct;
      default:
        return null;
    }
  }
  const organicSort = useSortableTable(filteredOrganic, getOrganicValue, organicColumnTypes);

  // Pareto por brand: agrupa e calcula % GMV
  const paretoByBrand: Record<string, ParetoRow[]> = {};
  for (const row of data?.pareto ?? []) {
    if (!paretoByBrand[row.brand]) paretoByBrand[row.brand] = [];
    paretoByBrand[row.brand].push(row);
  }

  // TikTok canal dominante
  function dominantChannel(row: { avg_pct_video: number | null; avg_pct_live: number | null; avg_pct_card: number | null }) {
    const channels = [
      { key: "video", pct: row.avg_pct_video ?? 0 },
      { key: "live", pct: row.avg_pct_live ?? 0 },
      { key: "card", pct: row.avg_pct_card ?? 0 },
    ];
    return channels.reduce((a, b) => (b.pct > a.pct ? b : a));
  }

  // Ordenação — LTV & Fidelização
  const ltvColumnTypes: Record<string, SortColumnType> = {
    brand: "text",
    total_buyers: "numeric",
    repeat_buyers: "numeric",
    repeat_rate_pct: "numeric",
    avg_customer_ltv: "numeric",
    vip_buyers: "numeric",
    one_and_done_buyers: "numeric",
    at_risk_or_churned: "numeric",
    overall_roas: "numeric",
  };
  function getLtvValue(row: LtvRow, column: string) {
    switch (column) {
      case "brand":
        return BRAND_LABELS[row.brand] ?? row.brand;
      case "total_buyers":
        return row.total_buyers;
      case "repeat_buyers":
        return row.repeat_buyers;
      case "repeat_rate_pct":
        return row.repeat_rate_pct;
      case "avg_customer_ltv":
        return row.avg_customer_ltv;
      case "vip_buyers":
        return row.vip_buyers;
      case "one_and_done_buyers":
        return row.one_and_done_buyers;
      case "at_risk_or_churned":
        return row.at_risk_or_churned;
      case "overall_roas":
        return row.overall_roas;
      default:
        return null;
    }
  }
  const ltvSort = useSortableTable(data?.ltv ?? [], getLtvValue, ltvColumnTypes);

  // Ordenação — Top Produtos TikTok
  const tkProductsColumnTypes: Record<string, SortColumnType> = {
    brand: "text",
    gmv: "numeric",
    orders: "numeric",
    avg_rating: "numeric",
  };
  function getTkProductValue(row: TkProductRow, column: string) {
    switch (column) {
      case "brand":
        return BRAND_LABELS[row.brand] ?? row.brand;
      case "gmv":
        return row.gmv;
      case "orders":
        return row.orders;
      case "avg_rating":
        return row.avg_rating;
      default:
        return null;
    }
  }
  const tkProductsSort = useSortableTable(data?.tk_products ?? [], getTkProductValue, tkProductsColumnTypes);

  const CHANNEL_STYLES: Record<string, string> = {
    video: "bg-violet-100 text-violet-800",
    live: "bg-rose-100 text-rose-800",
    card: "bg-sky-100 text-sky-800",
  };
  const CHANNEL_LABELS: Record<string, string> = {
    video: "Vídeo",
    live: "Live",
    card: "Card",
  };

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
          <div className="flex items-center gap-3">
            {isLive ? (
              <span className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-1.5 font-medium">
                Dados ao vivo · API conectada
              </span>
            ) : (
              <span className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5 font-medium">
                Demonstracao · API offline
              </span>
            )}
          </div>
        </div>
      </header>

      <AppNav />

      <main className="max-w-7xl mx-auto px-6 py-8 flex flex-col gap-6">
        {/* Filtro por marca ML */}
        <div className="flex items-center gap-2 flex-wrap">
          {["all", ...ML_BRANDS].map((b) => (
            <button
              key={b}
              onClick={() => setBrandFilter(b)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                brandFilter === b
                  ? "bg-violet-600 text-white border-violet-600"
                  : "bg-white text-slate-600 border-violet-100 hover:border-violet-300"
              }`}
            >
              {b === "all" ? "Todas as marcas" : BRAND_LABELS[b]}
            </button>
          ))}
          <span className="text-[10px] text-slate-400 ml-2">Filtro aplica-se a seções ML</span>
        </div>

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
          {loading ? "Carregando inteligência..." : error ? "Falha ao carregar." : "Dados carregados."}
        </span>

        {/* Seção 1 — Status de Portfólio ML */}
        <div>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-3">
            Status de Portfólio ML
          </h2>
          <div
            className={`grid grid-cols-2 md:grid-cols-4 gap-4 transition-opacity duration-200 ${loading ? "opacity-50 pointer-events-none" : ""}`}
            aria-busy={loading}
          >
            {Object.entries(statusMeta).map(([key, meta]) => {
              const row = signalMap[key];
              const n = row?.n_products ?? 0;
              const gmv = row?.gmv ?? 0;
              const spend = row?.ad_spend ?? 0;
              const roas = row?.avg_roas ?? null;
              return (
                <KpiCard
                  key={key}
                  label={meta.label}
                  value={String(n)}
                  subvalue={meta.buildSub(gmv, spend, n, roas)}
                  accent={meta.accent}
                />
              );
            })}
          </div>
        </div>

        {/* Seção 2 — Urgente: Parar Agora */}
        {(filteredUrgent.length > 0 || loading) && (
          <div
            className={`bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden transition-opacity duration-200 ${loading ? "opacity-50 pointer-events-none" : ""}`}
            aria-busy={loading}
          >
            <SectionHeader
              title="Urgente: Parar Agora!"
              subtitle={`${filteredUrgent.length} produto(s) com ad spend sem nenhuma venda`}
            />
            <TableWrap>
              <thead>
                <tr className="bg-slate-50 text-left">
                  <SortableHeader label="Marca" column="brand" sort={urgentSort.sort} onSort={urgentSort.toggleSort} align="left" />
                  <Th>Produto</Th>
                  <SortableHeader label="Ad Spend" column="ad_spend" sort={urgentSort.sort} onSort={urgentSort.toggleSort} align="right" />
                  <SortableHeader label="Dias c/ Ads" column="days_advertised" sort={urgentSort.sort} onSort={urgentSort.toggleSort} align="right" />
                  <Th>Pareto</Th>
                  <Th>Velocity</Th>
                  <Th>Ação</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {urgentSort.sortedRows.map((r, i) => (
                  <tr key={i} className="hover:bg-slate-50 transition-colors">
                    <td className="px-4 py-3 text-xs font-semibold text-slate-700 whitespace-nowrap">
                      {BRAND_LABELS[r.brand] ?? r.brand}
                    </td>
                    <td className="px-4 py-3 text-slate-700 max-w-xs" title={r.title}>
                      {truncate(r.title, 40)}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      <span className="inline-block bg-rose-50 text-rose-700 font-semibold rounded px-2 py-0.5 text-xs">
                        {fmtBrl(r.ad_spend)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                      {r.days_advertised ?? "—"}
                    </td>
                    <td className="px-4 py-3"><ParetoBadge v={r.pareto_bucket} /></td>
                    <td className="px-4 py-3"><VelocityBadge v={r.revenue_velocity} /></td>
                    <td className="px-4 py-3">
                      <span className="inline-block bg-rose-600 text-white text-[10px] font-semibold px-2 py-1 rounded">
                        Pausar Ads
                      </span>
                    </td>
                  </tr>
                ))}
                {filteredUrgent.length === 0 && !loading && (
                  <tr>
                    <td colSpan={7} className="px-6 py-8 text-center text-slate-400 text-sm">
                      Nenhum produto nesta categoria.
                    </td>
                  </tr>
                )}
              </tbody>
            </TableWrap>
          </div>
        )}

        {/* Seção 3 — Escalar Agora */}
        <div
          className={`bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden transition-opacity duration-200 ${loading ? "opacity-50 pointer-events-none" : ""}`}
          aria-busy={loading}
        >
          <SectionHeader
            title="Escalar Agora"
            subtitle="Produtos com ads ativos e ROAS >= 8x — candidatos a aumentar budget"
          />
          <TableWrap>
            <thead>
              <tr className="bg-slate-50 text-left">
                <SortableHeader label="Marca" column="brand" sort={scaleSort.sort} onSort={scaleSort.toggleSort} align="left" />
                <Th>Produto</Th>
                <SortableHeader label="GMV" column="gmv" sort={scaleSort.sort} onSort={scaleSort.toggleSort} align="right" />
                <SortableHeader label="ROAS" column="ad_roas" sort={scaleSort.sort} onSort={scaleSort.toggleSort} align="right" />
                <SortableHeader label="ACOS%" column="ad_acos_pct" sort={scaleSort.sort} onSort={scaleSort.toggleSort} align="right" />
                <SortableHeader label="Part. GMV" column="revenue_share_pct" sort={scaleSort.sort} onSort={scaleSort.toggleSort} align="right" />
                <Th>Velocity</Th>
                <Th>Ação</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {scaleSort.sortedRows.map((r, i) => {
                const roasColor =
                  (r.ad_roas ?? 0) >= 12
                    ? "text-emerald-700 font-bold"
                    : "text-amber-700 font-semibold";
                return (
                  <tr key={i} className="hover:bg-slate-50 transition-colors">
                    <td className="px-4 py-3 text-xs font-semibold text-slate-700 whitespace-nowrap">
                      {BRAND_LABELS[r.brand] ?? r.brand}
                    </td>
                    <td className="px-4 py-3 text-slate-700 max-w-xs" title={r.title}>
                      {truncate(r.title, 40)}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-slate-900 font-semibold text-sm">
                      {fmtBrl(r.gmv)}
                    </td>
                    <td className={`px-4 py-3 text-right tabular-nums text-sm ${roasColor}`}>
                      {r.ad_roas != null ? r.ad_roas.toFixed(1) + "x" : "—"}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                      {fmt(r.ad_acos_pct)}%
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                      {r.revenue_share_pct != null
                        ? (r.revenue_share_pct * 100).toFixed(1) + "%"
                        : "—"}
                    </td>
                    <td className="px-4 py-3"><VelocityBadge v={r.revenue_velocity} /></td>
                    <td className="px-4 py-3">
                      <span className="inline-block bg-emerald-600 text-white text-[10px] font-semibold px-2 py-1 rounded">
                        Aumentar Budget
                      </span>
                    </td>
                  </tr>
                );
              })}
              {filteredScale.length === 0 && !loading && (
                <tr>
                  <td colSpan={8} className="px-6 py-8 text-center text-slate-400 text-sm">
                    Nenhum produto elegível para escalar no momento.
                  </td>
                </tr>
              )}
            </tbody>
          </TableWrap>
        </div>

        {/* Seção 4 — Testar Ads */}
        <div
          className={`bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden transition-opacity duration-200 ${loading ? "opacity-50 pointer-events-none" : ""}`}
          aria-busy={loading}
        >
          <SectionHeader
            title="Testar Ads"
            subtitle="Produtos orgânicos com bom GMV — ainda sem investimento em publicidade"
          />
          <TableWrap>
            <thead>
              <tr className="bg-slate-50 text-left">
                <SortableHeader label="Marca" column="brand" sort={organicSort.sort} onSort={organicSort.toggleSort} align="left" />
                <Th>Produto</Th>
                <SortableHeader label="GMV" column="gmv" sort={organicSort.sort} onSort={organicSort.toggleSort} align="right" />
                <Th>Pareto</Th>
                <SortableHeader label="Compradores" column="units_sold" sort={organicSort.sort} onSort={organicSort.toggleSort} align="right" />
                <SortableHeader label="Cancel%" column="cancel_rate_pct" sort={organicSort.sort} onSort={organicSort.toggleSort} align="right" />
                <Th>Ação</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {organicSort.sortedRows.map((r, i) => (
                <tr key={i} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3 text-xs font-semibold text-slate-700 whitespace-nowrap">
                    {BRAND_LABELS[r.brand] ?? r.brand}
                  </td>
                  <td className="px-4 py-3 text-slate-700 max-w-xs" title={r.title}>
                    {truncate(r.title, 40)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-900 font-semibold text-sm">
                    {fmtBrl(r.gmv)}
                  </td>
                  <td className="px-4 py-3"><ParetoBadge v={r.pareto_bucket} /></td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                    {r.units_sold != null ? fmtNumber(r.units_sold) : "—"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-sm">
                    <span className={
                      r.cancel_rate_pct == null ? "text-slate-400"
                        : r.cancel_rate_pct < 2 ? "text-emerald-700"
                        : r.cancel_rate_pct < 5 ? "text-amber-700"
                        : "text-rose-700 font-semibold"
                    }>
                      {fmt(r.cancel_rate_pct)}%
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="inline-block bg-amber-500 text-white text-[10px] font-semibold px-2 py-1 rounded">
                      Testar Ads
                    </span>
                  </td>
                </tr>
              ))}
              {filteredOrganic.length === 0 && !loading && (
                <tr>
                  <td colSpan={7} className="px-6 py-8 text-center text-slate-400 text-sm">
                    Nenhum produto orgânico elegível.
                  </td>
                </tr>
              )}
            </tbody>
          </TableWrap>
        </div>

        {/* Seção 5 — Concentração Pareto por Marca */}
        <div
          className={`bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden transition-opacity duration-200 ${loading ? "opacity-50 pointer-events-none" : ""}`}
          aria-busy={loading}
        >
          <SectionHeader
            title="Concentração Pareto por Marca"
            subtitle="Distribuição do GMV por bucket A/B/C/D — Mercado Livre"
          />
          <div className="px-6 py-5 flex flex-col gap-5">
            {ML_BRANDS.map((brand) => {
              const rows = paretoByBrand[brand] ?? [];
              const totalGmv = rows.reduce((s, r) => s + r.gmv, 0);
              const totalN = rows.reduce((s, r) => s + r.n_products, 0);
              const bucketOrder = ["A_top50", "B_next30", "C_next15", "D_tail"];
              const byBucket = Object.fromEntries(rows.map((r) => [r.pareto_bucket, r]));
              return (
                <div key={brand}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-semibold text-slate-700">{BRAND_LABELS[brand]}</span>
                    <span className="text-[10px] text-slate-400">{totalN} produtos · {fmtBrl(totalGmv)} GMV total</span>
                  </div>
                  {/* Barra empilhada */}
                  <div className="flex h-5 rounded overflow-hidden w-full">
                    {bucketOrder.map((bk) => {
                      const row = byBucket[bk];
                      if (!row || totalGmv === 0) return null;
                      const pct = (row.gmv / totalGmv) * 100;
                      return (
                        <div
                          key={bk}
                          className={`${PARETO_COLORS[bk]} h-full`}
                          style={{ width: `${pct}%` }}
                          title={`${bk}: ${pct.toFixed(1)}% GMV (${row.n_products} produtos)`}
                        />
                      );
                    })}
                  </div>
                  {/* Legenda */}
                  <div className="flex gap-4 mt-1.5 flex-wrap">
                    {bucketOrder.map((bk) => {
                      const row = byBucket[bk];
                      if (!row) return null;
                      const pct = totalGmv > 0 ? (row.gmv / totalGmv) * 100 : 0;
                      const label = bk.replace("_top50", "").replace("_next30", "").replace("_next15", "").replace("_tail", "");
                      return (
                        <span key={bk} className="flex items-center gap-1.5 text-[10px] text-slate-500">
                          <span className={`w-2.5 h-2.5 rounded-sm inline-block ${PARETO_COLORS[bk]}`} />
                          <span className="font-semibold text-slate-700">{label}</span>
                          <span>{pct.toFixed(0)}% · {row.n_products}p</span>
                        </span>
                      );
                    })}
                  </div>
                </div>
              );
            })}
            {Object.keys(paretoByBrand).length === 0 && !loading && (
              <p className="text-sm text-slate-400 text-center py-4">Sem dados de pareto disponíveis.</p>
            )}
          </div>
        </div>

        {/* Seção 6 — LTV & Fidelização */}
        {(data?.ltv ?? []).length > 0 && (
          <div
            className={`bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden transition-opacity duration-200 ${loading ? "opacity-50 pointer-events-none" : ""}`}
            aria-busy={loading}
          >
            <SectionHeader
              title="LTV & Fidelização — Mercado Livre"
              subtitle="Dados cross-temporais de comportamento de compradores por marca"
            />
            <TableWrap>
              <thead>
                <tr className="bg-slate-50 text-left">
                  <SortableHeader label="Marca" column="brand" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="left" />
                  <SortableHeader label="Compradores" column="total_buyers" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="right" />
                  <SortableHeader label="Recorrentes" column="repeat_buyers" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="right" />
                  <SortableHeader label="Taxa Recorrência" column="repeat_rate_pct" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="right" />
                  <SortableHeader label="LTV Médio" column="avg_customer_ltv" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="right" />
                  <SortableHeader label="VIPs" column="vip_buyers" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="right" />
                  <SortableHeader label="Compraram 1x" column="one_and_done_buyers" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="right" />
                  <SortableHeader label="Em Risco" column="at_risk_or_churned" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="right" />
                  <SortableHeader label="ROAS Geral" column="overall_roas" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="right" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {ltvSort.sortedRows.map((r) => {
                  const recColor =
                    r.repeat_rate_pct == null ? "text-slate-400"
                      : r.repeat_rate_pct > 20 ? "text-emerald-700 font-semibold"
                      : r.repeat_rate_pct > 10 ? "text-amber-700 font-semibold"
                      : "text-rose-600 font-semibold";
                  return (
                    <tr key={r.brand} className="hover:bg-slate-50 transition-colors">
                      <td className="px-4 py-3 text-xs font-semibold text-slate-700 whitespace-nowrap">
                        {BRAND_LABELS[r.brand] ?? r.brand}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                        {fmtNumber(r.total_buyers)}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                        {fmtNumber(r.repeat_buyers)}
                      </td>
                      <td className={`px-4 py-3 text-right tabular-nums text-sm ${recColor}`}>
                        {r.repeat_rate_pct != null ? r.repeat_rate_pct.toFixed(1) + "%" : "—"}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-900 font-semibold text-sm">
                        {r.avg_customer_ltv != null ? fmtBrl(r.avg_customer_ltv) : "—"}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-violet-700 font-semibold text-sm">
                        {r.vip_buyers != null ? fmtNumber(r.vip_buyers) : "—"}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                        {r.one_and_done_buyers != null ? fmtNumber(r.one_and_done_buyers) : "—"}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-rose-600 text-sm">
                        {r.at_risk_or_churned != null ? fmtNumber(r.at_risk_or_churned) : "—"}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-900 font-semibold text-sm">
                        {r.overall_roas != null ? r.overall_roas.toFixed(1) + "x" : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </TableWrap>
          </div>
        )}

        {/* Seção 7 — Top Produtos TikTok */}
        <div
          className={`bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden transition-opacity duration-200 ${loading ? "opacity-50 pointer-events-none" : ""}`}
          aria-busy={loading}
        >
          <SectionHeader
            title="Top Produtos TikTok — Últimos 30 dias"
            subtitle="Produtos com maior GMV no período, agrupados por marca e nome"
          />
          <TableWrap>
            <thead>
              <tr className="bg-slate-50 text-left">
                <SortableHeader label="Marca" column="brand" sort={tkProductsSort.sort} onSort={tkProductsSort.toggleSort} align="left" />
                <Th>Produto</Th>
                <SortableHeader label="GMV" column="gmv" sort={tkProductsSort.sort} onSort={tkProductsSort.toggleSort} align="right" />
                <SortableHeader label="Pedidos" column="orders" sort={tkProductsSort.sort} onSort={tkProductsSort.toggleSort} align="right" />
                <Th>Canal Dominante</Th>
                <Th right>% Vídeo</Th>
                <Th right>% Live</Th>
                <Th right>% Card</Th>
                <SortableHeader label="Rating" column="avg_rating" sort={tkProductsSort.sort} onSort={tkProductsSort.toggleSort} align="right" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {tkProductsSort.sortedRows.map((r, i) => {
                const dom = dominantChannel(r);
                return (
                  <tr key={i} className="hover:bg-slate-50 transition-colors">
                    <td className="px-4 py-3 text-xs font-semibold text-slate-700 whitespace-nowrap">
                      {BRAND_LABELS[r.brand] ?? r.brand}
                    </td>
                    <td className="px-4 py-3 text-slate-700 max-w-xs" title={r.product_name}>
                      {truncate(r.product_name, 40)}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-slate-900 font-semibold text-sm">
                      {fmtBrl(r.gmv)}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                      {fmtNumber(r.orders)}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${CHANNEL_STYLES[dom.key]}`}>
                        {CHANNEL_LABELS[dom.key]}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                      {r.avg_pct_video != null ? r.avg_pct_video.toFixed(0) + "%" : "—"}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                      {r.avg_pct_live != null ? r.avg_pct_live.toFixed(0) + "%" : "—"}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                      {r.avg_pct_card != null ? r.avg_pct_card.toFixed(0) + "%" : "—"}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                      {r.avg_rating != null ? r.avg_rating.toFixed(1) : "—"}
                    </td>
                  </tr>
                );
              })}
              {(data?.tk_products ?? []).length === 0 && !loading && (
                <tr>
                  <td colSpan={9} className="px-6 py-8 text-center text-slate-400 text-sm">
                    Sem dados TikTok nos últimos 30 dias.
                  </td>
                </tr>
              )}
            </tbody>
          </TableWrap>
        </div>
      </main>
    </div>
  );
}
