"use client";

import { useEffect, useState, useMemo } from "react";
import {
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import LiveStatusBadge from "@/components/LiveStatusBadge";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import {
  fetchOperacoes,
  type OperacoesData,
  type TkDailyRow,
  type CreatorRow,
  type LiveRow,
} from "@/lib/api-client";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { useSortableTable, type SortColumnType } from "@/lib/use-sortable-table";
import SortableHeader from "@/components/SortableHeader";
import TableScrollHint from "@/components/TableScrollHint";
import { computeRequestStatus } from "@/lib/request-freshness";

const BRAND_LABELS: Record<string, string> = {
  apice: "ÁPICE",
  barbours: "BARBOURS",
  kokeshi: "KOKESHI",
  lescent: "LESCENT",
  rituaria: "RITUÁRIA",
};

const ML_BRANDS = ["barbours", "kokeshi", "lescent"];
const ALL_BRANDS = ["apice", "barbours", "kokeshi", "lescent", "rituaria"];

// Brand colors for the trend chart
const BRAND_COLORS: Record<string, string> = {
  barbours: "#7c3aed",
  kokeshi: "#06b6d4",
  apice: "#f59e0b",
  lescent: "#ec4899",
  rituaria: "#10b981",
};

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
    <TableScrollHint>
      <table className="w-full text-sm">{children}</table>
    </TableScrollHint>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th
      className={`px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider ${
        right ? "text-right" : "text-left"
      }`}
    >
      {children}
    </th>
  );
}

function GpmBadge({ v }: { v: number | null }) {
  if (v == null) return <span className="text-slate-300">—</span>;
  const cls =
    v >= 20
      ? "bg-emerald-100 text-emerald-800"
      : v >= 10
      ? "bg-amber-100 text-amber-800"
      : "bg-rose-100 text-rose-700";
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${cls}`}>
      {fmtBrl(v)}
    </span>
  );
}

function PctLiveColor({ v }: { v: number | null }) {
  if (v == null) return <span className="text-slate-400">—</span>;
  const cls =
    v >= 15
      ? "text-emerald-700 font-semibold"
      : v >= 5
      ? "text-amber-700 font-semibold"
      : "text-rose-600 font-semibold";
  return <span className={`tabular-nums ${cls}`}>{v.toFixed(1)}%</span>;
}

// Build a pivot table for the trend chart: date → { brand: gmv }
function buildChartData(rows: TkDailyRow[]): Record<string, number | string>[] {
  const byDate: Record<string, Record<string, number>> = {};
  for (const r of rows) {
    const label = formatDateLabel(r.ref_date);
    if (!byDate[label]) byDate[label] = {};
    byDate[label][r.brand] = (byDate[label][r.brand] ?? 0) + r.gmv;
  }
  return Object.entries(byDate)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, brands]) => ({ date, ...brands }));
}

function formatDateLabel(iso: string): string {
  // iso is YYYY-MM-DD
  const parts = iso.split("-");
  if (parts.length < 3) return iso;
  return `${parts[2]}/${parts[1]}`;
}

export default function OperacoesPage() {
  const [data, setData] = useState<OperacoesData | null>(null);
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [creatorBrand, setCreatorBrand] = useState<string>("all");
  // Chave da ultima requisicao resolvida (sucesso ou falha) — comparada com
  // a chave atual para nunca deixar uma resposta obsoleta (de um retry
  // anterior) sobrescrever o retry ATUAL em andamento (mesmo padrao
  // Financeiro/Regioes/Qualidade/Pedidos/Inteligencia, Gate U4/U5). Esta tela
  // nao tem filtros globais — a unica variavel de identidade e o retryKey.
  const [resolvedKey, setResolvedKey] = useState<string | null>(null);
  const requestKey = useMemo(() => String(retryKey), [retryKey]);

  useEffect(() => {
    // Ignora a resposta se um novo retry disparar antes dela chegar.
    let ignore = false;
    setLoading(true);
    setError(null);
    const key = String(retryKey);
    fetchOperacoes()
      .then((res) => {
        if (ignore) return;
        setData(res.data);
        setIsLive(res.live);
        setResolvedKey(key);
        setLoading(false);
      })
      .catch(() => {
        if (ignore) return;
        setError("Falha ao carregar dados de operações. Verifique a conexão.");
        // A chave precisa ser marcada como resolvida MESMO na falha — senao
        // `computeRequestStatus` nunca sai de "loading".
        setResolvedKey(key);
        setLoading(false);
      });
    return () => { ignore = true; };
  }, [retryKey]);

  // FINDING 2 (Gate U4) — loading/error/fresh SEPARADOS.
  const requestStatus = computeRequestStatus({ loading, error: error != null, resolvedKey, requestKey });
  const dataIsFresh = requestStatus.fresh;
  const isLoadingState = requestStatus.loading;
  const isErrorState = requestStatus.error;

  // Versao protegida do estado bruto — `data == null` com sucesso e'
  // indisponibilidade real; nenhum calculo/card/tabela abaixo deve ler
  // `data`/`isLive` brutos diretamente.
  const displayData = dataIsFresh ? data : null;
  const displayIsLive = dataIsFresh ? isLive : false;

  const filteredCreators = useMemo(
    () =>
      (displayData?.creators ?? []).filter(
        (r) => creatorBrand === "all" || r.brand === creatorBrand
      ),
    [displayData, creatorBrand]
  );

  const getCreatorValue = (
    row: CreatorRow,
    column: string
  ): string | number | null | undefined => {
    switch (column) {
      case "creator":
        return row.creator;
      case "gmv":
        return row.gmv;
      case "gmv_video":
        return row.videos > 0 ? row.gmv_video : null;
      case "gmv_live":
        return row.lives > 0 ? row.gmv_live : null;
      case "views":
        return row.views > 0 ? row.views : null;
      case "videos":
        return row.videos > 0 ? row.videos : null;
      case "lives":
        return row.lives > 0 ? row.lives : null;
      case "gpm_video":
        return row.videos > 0 ? row.gpm_video : null;
      default:
        return null;
    }
  };
  const creatorColumnTypes: Record<string, SortColumnType> = {
    creator: "text",
    gmv: "numeric",
    gmv_video: "numeric",
    gmv_live: "numeric",
    views: "numeric",
    videos: "numeric",
    lives: "numeric",
    gpm_video: "numeric",
  };
  const creatorsSort = useSortableTable(
    filteredCreators,
    getCreatorValue,
    creatorColumnTypes
  );

  const getLiveValue = (
    row: LiveRow,
    column: string
  ): string | number | null | undefined => {
    switch (column) {
      case "brand":
        return BRAND_LABELS[row.brand] ?? row.brand;
      case "total_lives":
        return row.total_lives;
      case "total_minutes":
        return row.total_minutes;
      case "live_gmv":
        return row.live_gmv;
      case "pct_live":
        return row.pct_live;
      case "gmv_per_live":
        return row.gmv_per_live;
      case "gmv_per_minute":
        return row.gmv_per_minute;
      default:
        return null;
    }
  };
  const liveColumnTypes: Record<string, SortColumnType> = {
    brand: "text",
    total_lives: "numeric",
    total_minutes: "numeric",
    live_gmv: "numeric",
    pct_live: "numeric",
    gmv_per_live: "numeric",
    gmv_per_minute: "numeric",
  };
  const livesSort = useSortableTable(
    displayData?.lives ?? [],
    getLiveValue,
    liveColumnTypes
  );

  const chartData = useMemo(
    () => buildChartData(displayData?.tk_daily ?? []),
    [displayData]
  );

  // Only show brands that actually appear in tk_daily
  const brandsInChart = useMemo(() => {
    const seen = new Set<string>();
    for (const r of displayData?.tk_daily ?? []) seen.add(r.brand);
    return ALL_BRANDS.filter((b) => seen.has(b));
  }, [displayData]);

  const alertas = displayData?.alertas ?? [];
  const criticos = alertas.filter((a) => a.severidade === "critico");
  const atencoes = alertas.filter((a) => a.severidade === "atencao");

  const hasAnyData = dataIsFresh && displayData != null;

  return (
    <PageContainer>
      {/* Contrato independente dos filtros globais (ver nota de escopo abaixo):
          sem barra sticky, para nao sugerir um escopo que a tela nao aplica. */}
      <PageHeader
        title="Operações"
        subtitle="Acompanhamento e priorização operacional — alertas, criadores, lives e velocidade de mídia."
        status={
          dataIsFresh ? (
            <LiveStatusBadge live={displayIsLive} />
          ) : isLoadingState ? (
            <span className="text-xs text-slate-500 bg-slate-100 border border-slate-200 rounded-lg px-3 py-1.5 font-medium">
              Atualizando dados...
            </span>
          ) : null
        }
      />

      {/* Nota de escopo (Task 6) — esta tela nao herda os filtros globais. */}
      <div className="bg-slate-50 border border-slate-200 rounded-2xl p-3">
        <p className="text-xs text-slate-600">
          Cada seção usa o período fornecido pelo próprio endpoint (<code className="font-mono text-[11px]">fetchOperacoes</code>) — filtros globais de canal, marca e período não se aplicam aqui. O filtro de marca abaixo é local e afeta somente a tabela de Top Criadores.
        </p>
      </div>

      {error && (
          <div className="bg-rose-50 border border-rose-200 rounded-2xl p-4 flex items-center justify-between gap-4">
            <div>
              <p className="text-xs font-semibold text-rose-700 uppercase tracking-wider mb-1">
                Erro de carregamento
              </p>
              <p className="text-sm text-rose-800">{error}</p>
            </div>
            <button
              onClick={() => {
                setError(null);
                setRetryKey((k) => k + 1);
              }}
              className="text-xs font-semibold text-rose-700 border border-rose-300 rounded-lg px-3 py-1.5 hover:bg-rose-100 transition-colors shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
            >
              Tentar novamente
            </button>
          </div>
        )}

        <span className="sr-only" aria-live="polite" aria-atomic="true">
          {isLoadingState
            ? "Carregando dados de operações..."
            : isErrorState
            ? "Falha ao carregar."
            : "Dados carregados."}
        </span>

      {isErrorState ? (
        <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-10 text-center">
          <p className="text-slate-500 text-sm font-medium">Não foi possível carregar os dados de operações.</p>
          <p className="text-slate-400 text-xs mt-1">Use "Tentar novamente" no banner de erro acima.</p>
        </div>
      ) : dataIsFresh && !hasAnyData ? (
        <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-12 text-center">
          <p className="text-slate-500 text-sm font-medium">Dados de operações indisponíveis no momento.</p>
          <p className="text-slate-400 text-xs mt-1">Não é modo demonstração — a fonte não retornou dados. Tente novamente em instantes.</p>
          <button
            onClick={() => setRetryKey((k) => k + 1)}
            className="mt-3 text-xs font-semibold text-violet-700 border border-violet-200 rounded-lg px-3 py-1.5 hover:bg-violet-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
          >
            Tentar novamente
          </button>
        </div>
      ) : (
      <>
        {/* Navegacao interna compacta */}
        <nav aria-label="Navegação interna da página" className="flex flex-wrap gap-1 -mx-2.5">
          <a href="#alertas" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">Alertas</a>
          <a href="#top-criadores" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">Top Criadores</a>
          <a href="#lives" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">Performance de Lives</a>
          <a href="#velocidade-ml" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">Velocidade ML</a>
          <a href="#trend-tiktok" className="px-2.5 py-1 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">Trend TikTok</a>
        </nav>

        {/* Seção 1 — Alertas Ativos */}
        <div
          id="alertas"
          className={`scroll-mt-24 transition-opacity duration-200 ${isLoadingState ? "opacity-50 pointer-events-none" : ""}`}
          aria-busy={isLoadingState}
        >
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-3">
            Alertas Ativos — Últimos 7 dias
          </h2>

          {alertas.length === 0 && dataIsFresh ? (
            <div className="bg-emerald-50 border border-emerald-200 rounded-2xl p-5">
              <p className="text-sm font-semibold text-emerald-800">
                Nenhum alerta ativo esta semana.
              </p>
              <p className="text-xs text-emerald-600 mt-1">
                Todos os ad spends estão com GMV e ROAS dentro dos limites.
              </p>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {criticos.map((a, i) => (
                <div
                  key={i}
                  className="bg-rose-50 border border-rose-200 rounded-2xl px-5 py-4 flex items-start justify-between gap-4"
                >
                  <div className="flex items-start gap-3">
                    <span className="mt-0.5 inline-block w-2 h-2 rounded-full bg-rose-500 shrink-0" />
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-[10px] font-bold text-rose-700 uppercase tracking-wider">
                          Crítico
                        </span>
                        <span className="text-[10px] font-semibold text-rose-500 bg-rose-100 border border-rose-200 rounded px-1.5 py-0.5">
                          {BRAND_LABELS[a.brand] ?? a.brand}
                        </span>
                      </div>
                      <p className="text-sm text-rose-900">{a.mensagem}</p>
                    </div>
                  </div>
                  {a.ad_spend != null && (
                    <div className="text-right shrink-0">
                      <p className="text-xs text-rose-500 font-medium">Ad Spend</p>
                      <p className="text-sm font-bold text-rose-800 tabular-nums">
                        {fmtBrl(a.ad_spend)}
                      </p>
                    </div>
                  )}
                </div>
              ))}
              {atencoes.map((a, i) => (
                <div
                  key={i}
                  className="bg-amber-50 border border-amber-200 rounded-2xl px-5 py-4 flex items-start justify-between gap-4"
                >
                  <div className="flex items-start gap-3">
                    <span className="mt-0.5 inline-block w-2 h-2 rounded-full bg-amber-400 shrink-0" />
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-[10px] font-bold text-amber-700 uppercase tracking-wider">
                          Atenção
                        </span>
                        <span className="text-[10px] font-semibold text-amber-600 bg-amber-100 border border-amber-200 rounded px-1.5 py-0.5">
                          {BRAND_LABELS[a.brand] ?? a.brand}
                        </span>
                      </div>
                      <p className="text-sm text-amber-900">{a.mensagem}</p>
                    </div>
                  </div>
                  {a.roas != null && (
                    <div className="text-right shrink-0">
                      <p className="text-xs text-amber-500 font-medium">ROAS</p>
                      <p className="text-sm font-bold text-amber-800 tabular-nums">
                        {a.roas.toFixed(1)}x
                      </p>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Seção 2 — Top Criadores */}
        <div
          id="top-criadores"
          className={`scroll-mt-24 bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden transition-opacity duration-200 ${
            isLoadingState ? "opacity-50 pointer-events-none" : ""
          }`}
          aria-busy={isLoadingState}
        >
          <SectionHeader
            title="Top Criadores — Últimos 7 dias"
            subtitle="Criadores TikTok com maior GMV gerado no período"
          />

          {/* Filtros por marca */}
          <div className="px-6 pt-4 pb-2 flex items-center gap-2 flex-wrap">
            {["all", ...ALL_BRANDS].map((b) => (
              <button
                key={b}
                onClick={() => setCreatorBrand(b)}
                className={`px-3 py-1 rounded-lg text-xs font-semibold border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                  creatorBrand === b
                    ? "bg-violet-600 text-white border-violet-600"
                    : "bg-white text-slate-600 border-violet-100 hover:border-violet-300"
                }`}
              >
                {b === "all" ? "Todos" : BRAND_LABELS[b]}
              </button>
            ))}
          </div>

          <TableWrap>
            <thead>
              <tr className="bg-slate-50 text-left">
                <Th>#</Th>
                <Th>Marca</Th>
                <SortableHeader
                  label="Criador"
                  column="creator"
                  sort={creatorsSort.sort}
                  onSort={creatorsSort.toggleSort}
                  align="left"
                />
                <SortableHeader
                  label="GMV Total"
                  column="gmv"
                  sort={creatorsSort.sort}
                  onSort={creatorsSort.toggleSort}
                />
                <SortableHeader
                  label="GMV Vídeo"
                  column="gmv_video"
                  sort={creatorsSort.sort}
                  onSort={creatorsSort.toggleSort}
                />
                <SortableHeader
                  label="GMV Live"
                  column="gmv_live"
                  sort={creatorsSort.sort}
                  onSort={creatorsSort.toggleSort}
                />
                <SortableHeader
                  label="Views"
                  column="views"
                  sort={creatorsSort.sort}
                  onSort={creatorsSort.toggleSort}
                />
                <SortableHeader
                  label="Vídeos"
                  column="videos"
                  sort={creatorsSort.sort}
                  onSort={creatorsSort.toggleSort}
                />
                <SortableHeader
                  label="Lives"
                  column="lives"
                  sort={creatorsSort.sort}
                  onSort={creatorsSort.toggleSort}
                />
                <SortableHeader
                  label="GPM"
                  column="gpm_video"
                  sort={creatorsSort.sort}
                  onSort={creatorsSort.toggleSort}
                />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {creatorsSort.sortedRows.map((r, i) => (
                <tr key={i} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3 text-xs font-bold text-slate-400 tabular-nums w-8">
                    {i + 1}
                  </td>
                  <td className="px-4 py-3 text-xs font-semibold text-slate-700 whitespace-nowrap">
                    {BRAND_LABELS[r.brand] ?? r.brand}
                  </td>
                  <td className="px-4 py-3 text-slate-700 max-w-[180px] truncate" title={r.creator}>
                    {r.creator}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-900 font-semibold text-sm">
                    {fmtBrl(r.gmv)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                    {r.videos > 0 ? fmtBrl(r.gmv_video) : <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                    {r.lives > 0 ? fmtBrl(r.gmv_live) : <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                    {r.views > 0 ? fmtNumber(r.views) : <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                    {r.videos > 0 ? r.videos : <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                    {r.lives > 0 ? r.lives : <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {r.videos > 0 ? (
                      <GpmBadge v={r.gpm_video} />
                    ) : (
                      <span className="text-slate-300 text-sm">—</span>
                    )}
                  </td>
                </tr>
              ))}
              {creatorsSort.sortedRows.length === 0 && dataIsFresh && (
                <tr>
                  <td
                    colSpan={10}
                    className="px-6 py-8 text-center text-slate-400 text-sm"
                  >
                    Nenhum criador encontrado para o filtro selecionado.
                  </td>
                </tr>
              )}
            </tbody>
          </TableWrap>
          <div className="px-6 py-3 border-t border-slate-50 flex items-center gap-6 flex-wrap">
            <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">GPM:</span>
            <span className="flex items-center gap-1.5 text-xs text-emerald-700">
              <span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> &gt;= R$20
            </span>
            <span className="flex items-center gap-1.5 text-xs text-amber-700">
              <span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> R$10–20
            </span>
            <span className="flex items-center gap-1.5 text-xs text-rose-600">
              <span className="w-2 h-2 rounded-full bg-rose-400 inline-block" /> &lt; R$10
            </span>
          </div>
        </div>

        {/* Seção 3 — Performance de Lives */}
        <div
          id="lives"
          className={`scroll-mt-24 bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden transition-opacity duration-200 ${
            isLoadingState ? "opacity-50 pointer-events-none" : ""
          }`}
          aria-busy={isLoadingState}
        >
          <SectionHeader
            title="Performance de Lives — Últimos 30 dias"
            subtitle="Apenas marcas com lives no período. GMV/min = eficiência de cada minuto ao vivo."
          />
          <TableWrap>
            <thead>
              <tr className="bg-slate-50 text-left">
                <SortableHeader
                  label="Marca"
                  column="brand"
                  sort={livesSort.sort}
                  onSort={livesSort.toggleSort}
                  align="left"
                />
                <SortableHeader
                  label="Lives"
                  column="total_lives"
                  sort={livesSort.sort}
                  onSort={livesSort.toggleSort}
                />
                <SortableHeader
                  label="Horas ao Vivo"
                  column="total_minutes"
                  sort={livesSort.sort}
                  onSort={livesSort.toggleSort}
                />
                <SortableHeader
                  label="GMV Lives"
                  column="live_gmv"
                  sort={livesSort.sort}
                  onSort={livesSort.toggleSort}
                />
                <SortableHeader
                  label="% GMV via Live"
                  column="pct_live"
                  sort={livesSort.sort}
                  onSort={livesSort.toggleSort}
                />
                <SortableHeader
                  label="GMV/Live"
                  column="gmv_per_live"
                  sort={livesSort.sort}
                  onSort={livesSort.toggleSort}
                />
                <SortableHeader
                  label="GMV/min"
                  column="gmv_per_minute"
                  sort={livesSort.sort}
                  onSort={livesSort.toggleSort}
                />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {livesSort.sortedRows.map((r) => (
                <tr key={r.brand} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3 text-xs font-semibold text-slate-700 whitespace-nowrap">
                    {BRAND_LABELS[r.brand] ?? r.brand}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                    {r.total_lives}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                    {(r.total_minutes / 60).toFixed(1)}h
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-900 font-semibold text-sm">
                    {fmtBrl(r.live_gmv)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <PctLiveColor v={r.pct_live} />
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                    {r.gmv_per_live != null ? fmtBrl(r.gmv_per_live) : "—"}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600 text-sm">
                    {r.gmv_per_minute != null
                      ? `R$ ${r.gmv_per_minute.toFixed(2)}`
                      : "—"}
                  </td>
                </tr>
              ))}
              {livesSort.sortedRows.length === 0 && dataIsFresh && (
                <tr>
                  <td
                    colSpan={7}
                    className="px-6 py-8 text-center text-slate-400 text-sm"
                  >
                    Nenhuma marca realizou lives nos últimos 30 dias.
                  </td>
                </tr>
              )}
            </tbody>
          </TableWrap>
          <div className="px-6 py-3 border-t border-slate-50">
            <p className="text-[10px] text-slate-400">
              Marcas sem lives no período não aparecem aqui.
            </p>
          </div>
          <div className="px-6 py-3 border-t border-slate-50 flex items-center gap-6 flex-wrap">
            <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-widest">% GMV via Live:</span>
            <span className="flex items-center gap-1.5 text-xs text-emerald-700">
              <span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" /> &gt;= 15%
            </span>
            <span className="flex items-center gap-1.5 text-xs text-amber-700">
              <span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> 5–15%
            </span>
            <span className="flex items-center gap-1.5 text-xs text-rose-600">
              <span className="w-2 h-2 rounded-full bg-rose-400 inline-block" /> &lt; 5%
            </span>
          </div>
        </div>

        {/* Seção 4 — Velocidade ML */}
        <div
          id="velocidade-ml"
          className={`scroll-mt-24 transition-opacity duration-200 ${isLoadingState ? "opacity-50 pointer-events-none" : ""}`}
          aria-busy={isLoadingState}
        >
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-3">
            Velocidade ML — Últimos 7 dias
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {(displayData?.ml_velocity ?? []).map((r) => {
              const lowRoas = r.roas_7d != null && r.roas_7d < 3 && r.ad_spend_7d > 0;
              return (
                <div
                  key={r.brand}
                  className={`bg-white rounded-2xl shadow-sm border p-5 flex flex-col gap-3 ${
                    lowRoas ? "border-rose-300" : "border-violet-100"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
                      {BRAND_LABELS[r.brand] ?? r.brand}
                    </span>
                    {lowRoas && (
                      <span className="text-[10px] font-bold text-rose-600 bg-rose-50 border border-rose-200 rounded px-2 py-0.5">
                        ROAS baixo
                      </span>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-0.5">GMV 7d</p>
                      <p className="text-xl font-bold text-slate-900 tabular-nums leading-none">
                        {fmtBrl(r.gmv_7d)}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-0.5">Ad Spend</p>
                      <p className="text-xl font-bold text-slate-700 tabular-nums leading-none">
                        {fmtBrl(r.ad_spend_7d)}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-0.5">ROAS</p>
                      <p
                        className={`text-xl font-bold tabular-nums leading-none ${
                          lowRoas
                            ? "text-rose-600"
                            : r.roas_7d != null && r.roas_7d >= 8
                            ? "text-emerald-700"
                            : "text-amber-700"
                        }`}
                      >
                        {r.roas_7d != null ? r.roas_7d.toFixed(1) + "x" : "—"}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-0.5">Pedidos</p>
                      <p className="text-xl font-bold text-slate-900 tabular-nums leading-none">
                        {fmtNumber(r.orders_7d)}
                      </p>
                    </div>
                  </div>
                </div>
              );
            })}
            {ML_BRANDS.filter(
              (b) => !(displayData?.ml_velocity ?? []).some((r) => r.brand === b)
            ).map((b) => (
              <div
                key={b}
                className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5 flex flex-col gap-3"
              >
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
                  {BRAND_LABELS[b]}
                </span>
                <p className="text-sm text-slate-300">Sem dados no período.</p>
              </div>
            ))}
          </div>
        </div>

        {/* Seção 5 — Trend TikTok */}
        <div
          id="trend-tiktok"
          className={`scroll-mt-24 bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden transition-opacity duration-200 ${
            isLoadingState ? "opacity-50 pointer-events-none" : ""
          }`}
          aria-busy={isLoadingState}
        >
          <SectionHeader
            title="Trend TikTok — Últimos 14 dias"
            subtitle="GMV diário por marca — todos os canais combinados"
          />
          <div className="px-4 py-5">
            {chartData.length > 0 ? (
              <ResponsiveContainer width="100%" height={300}>
                <ComposedChart data={chartData} margin={{ top: 4, right: 24, left: 8, bottom: 4 }}>
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 11, fill: "#94a3b8" }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    tickFormatter={(v) => fmtBrl(v as number)}
                    tick={{ fontSize: 11, fill: "#94a3b8" }}
                    axisLine={false}
                    tickLine={false}
                    width={72}
                  />
                  <Tooltip
                    formatter={(value: number, name: string) => [
                      fmtBrl(value),
                      BRAND_LABELS[name] ?? name,
                    ]}
                    contentStyle={{
                      border: "1px solid #ede9fe",
                      borderRadius: "12px",
                      fontSize: "12px",
                      boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
                    }}
                    labelStyle={{ fontWeight: 600, color: "#334155", marginBottom: 4 }}
                  />
                  <Legend
                    formatter={(value) => BRAND_LABELS[value] ?? value}
                    wrapperStyle={{ fontSize: "11px", paddingTop: "8px" }}
                  />
                  {brandsInChart.map((brand) => (
                    <Line
                      key={brand}
                      type="monotone"
                      dataKey={brand}
                      stroke={BRAND_COLORS[brand]}
                      strokeWidth={2}
                      dot={false}
                      activeDot={{ r: 4 }}
                    />
                  ))}
                </ComposedChart>
              </ResponsiveContainer>
            ) : (
              dataIsFresh && (
                <p className="text-sm text-slate-400 text-center py-10">
                  Sem dados TikTok nos últimos 14 dias.
                </p>
              )
            )}
          </div>
        </div>
      </>
      )}
    </PageContainer>
  );
}
