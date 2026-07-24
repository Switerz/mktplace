"use client";

import Link from "next/link";
import type { OverviewData } from "@/lib/api-client";
import type { Marketplace } from "@/lib/mock-data";
import { gmvChannelBreakdown } from "@/lib/kpi-drilldown";
import { fmtBrl } from "@/lib/formatters";

const CHANNEL_ACCENT: Record<string, string> = {
  tiktok: "bg-violet-500",
  ml: "bg-cyan-500",
  shopee: "bg-orange-500",
};

interface Props {
  overview: OverviewData | null;
  /** Selecao atual de marketplace (Finding 3) — decide quais canais aparecem
   * na lista; um canal selecionado sem valor no contrato continua visivel
   * como "Sem dado", nunca omitido nem convertido em R$ 0. */
  channels: Marketplace[];
  loading?: boolean;
  /** Resolve o href de destino combinando os filtros globais atuais (Gate
   * U2, Task 7) — injetado pela pagina para manter este componente sem
   * dependencia direta de `useSearchParams`. */
  buildHref: (href: string) => string;
}

/**
 * Painel simples de desempenho por canal (Gate U2, Task 6) — usa somente
 * OverviewData ja carregado pela Gerencial, sem fetch adicional. Barras
 * proporcionais em CSS puro (sem biblioteca de grafico) e continua util com
 * um unico canal filtrado (nesse caso a participacao e sempre 100%).
 */
export default function ChannelPerformancePanel({ overview, channels, loading, buildHref }: Props) {
  if (loading) {
    return (
      <div className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5 animate-pulse" aria-busy="true">
        <div className="h-3 w-40 bg-violet-100 rounded mb-4" />
        <div className="h-8 w-full bg-violet-50 rounded mb-2" />
        <div className="h-8 w-full bg-violet-50 rounded mb-2" />
        <div className="h-8 w-full bg-violet-50 rounded" />
      </div>
    );
  }

  if (!overview) {
    // Nao-carregando mas sem dado disponivel (ex: requisicao anterior falhou
    // ou ainda nao concluiu de forma fresca) — mensagem estatica, nunca um
    // skeleton animado indefinidamente (Finding 2, rodada de correcao U2).
    return (
      <div className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5">
        <h2 className="text-sm font-semibold text-slate-700 mb-2">Desempenho por canal</h2>
        <p className="text-sm text-slate-400">Dados indisponíveis no momento para o período e filtros selecionados.</p>
      </div>
    );
  }

  const shares = gmvChannelBreakdown(overview, channels);

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5 flex flex-col gap-4">
      <h2 className="text-sm font-semibold text-slate-700">Desempenho por canal</h2>
      {shares.length === 0 ? (
        <p className="text-sm text-slate-400">Sem dados de canal para o período e filtros selecionados.</p>
      ) : (
        <div className="flex flex-col gap-3">
          {shares.map((s) => (
            <Link
              key={s.channel}
              href={buildHref(`/canais?channels=${s.channel}`)}
              className="group flex flex-col gap-1.5 rounded-lg p-1.5 -m-1.5 hover:bg-violet-50/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 transition-colors"
            >
              <div className="flex items-center justify-between text-xs gap-2">
                <span className="font-semibold text-slate-700 group-hover:text-violet-700">{s.label}</span>
                <span className="tabular-nums text-slate-500 shrink-0">
                  {s.value != null ? fmtBrl(s.value) : <span className="text-slate-400">Sem dado</span>}
                  {s.pct != null && ` · ${s.pct.toFixed(1)}%`}
                </span>
              </div>
              <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
                <div
                  className={`h-2 rounded-full ${s.value != null ? (CHANNEL_ACCENT[s.channel] ?? "bg-slate-400") : "bg-slate-200"}`}
                  style={{ width: `${s.pct ?? 0}%` }}
                />
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
