"use client";

import type { ExecutiveHealth } from "@/lib/api-client";
import type { Pulse, PulseGroup } from "@/lib/executive-pulse";
import { formatMetricByType } from "@/lib/executive-pulse";
import { HEALTH_STATUS_LABEL, HEALTH_STATUS_TONE, SEVERITY_TONE, SEVERITY_LABEL } from "@/lib/executive-summary";

interface Props {
  pulse: Pulse;
  health: ExecutiveHealth | null;
  loading: boolean;
  unavailable: boolean;
  /** Abre o drill-down de um grupo específico (modo "insight"). */
  onOpenInsight: (key: string) => void;
  /** Abre o drill-down "Ver todos" (grupos por categoria, inclui dado). */
  onOpenAll: () => void;
}

function InsightButton({ group, onOpen }: { group: PulseGroup; onOpen: (key: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(group.key)}
      aria-haspopup="dialog"
      className="w-full text-left flex items-start gap-2 rounded-lg px-2 py-1.5 min-h-11 hover:bg-violet-50/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 transition-colors"
    >
      <span className={`text-[10px] font-semibold uppercase rounded px-1.5 py-0.5 shrink-0 mt-0.5 ${SEVERITY_TONE[group.severity]}`}>
        {SEVERITY_LABEL[group.severity]}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium text-slate-800 leading-snug">{group.title}</span>
        {group.count === 1 && group.representative.metric_value != null && (
          <span className="block text-xs text-slate-500 tabular-nums">{formatMetricByType(group.type, group.representative.metric_value)}</span>
        )}
      </span>
    </button>
  );
}

/**
 * "Pulso do período" (Gate G1) — síntese compacta que substitui o antigo
 * card full-width de Resumo Executivo. Mostra a saúde comercial, no máximo 3
 * insights priorizados/agrupados, e uma contagem SEPARADA de avisos de
 * confiança no dado (nunca misturada aos riscos comerciais). Clicar abre a
 * explicação primeiro (drill-down), nunca navega direto.
 */
export default function PulsoPeriodoPanel({ pulse, health, loading, unavailable, onOpenInsight, onOpenAll }: Props) {
  if (loading) {
    return (
      <section className="bg-white rounded-2xl shadow-sm border border-violet-100 p-4 animate-pulse" aria-busy="true">
        <div className="h-3 w-32 bg-violet-100 rounded mb-3" />
        <div className="h-4 w-full bg-violet-50 rounded mb-2" />
        <div className="h-4 w-2/3 bg-violet-50 rounded" />
      </section>
    );
  }

  if (unavailable) {
    return (
      <section className="bg-slate-50 border border-slate-200 rounded-2xl p-4">
        <h2 className="text-sm font-semibold text-slate-700 mb-1">Pulso do período</h2>
        <p className="text-xs text-slate-500">Indisponível no momento — os KPIs, a tendência e a tabela continuam com os dados do período.</p>
      </section>
    );
  }

  const totalCommercial = pulse.commercial.length;
  const dc = pulse.dataConfidence;

  return (
    <section className="bg-white rounded-2xl shadow-sm border border-violet-100 p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-700">Pulso do período</h2>
        {/* Finding 3: sem dados NAO e' diagnostico comercial "Crítico" —
            comunica "Dados indisponíveis" (tom neutro). Com dados, mostra a
            saude comercial (Saudável/Atenção/Crítico). */}
        {pulse.dataUnavailable ? (
          <span className="text-[11px] font-semibold rounded-lg px-2 py-1 shrink-0 text-slate-600 bg-slate-100 border border-slate-200">
            Dados indisponíveis
          </span>
        ) : health ? (
          <span className={`text-[11px] font-semibold rounded-lg px-2 py-1 shrink-0 ${HEALTH_STATUS_TONE[health.status]}`}>
            {HEALTH_STATUS_LABEL[health.status]}
          </span>
        ) : null}
      </div>

      {health?.summary && <p className="text-xs text-slate-600 leading-snug">{health.summary}</p>}

      {totalCommercial === 0 ? (
        <p className="text-xs text-slate-400">Sem atenções comerciais/operacionais no período.</p>
      ) : (
        <div className="flex flex-col gap-0.5">
          {pulse.top.map((g) => (
            <InsightButton key={g.key} group={g} onOpen={onOpenInsight} />
          ))}
        </div>
      )}

      {totalCommercial > pulse.top.length && (
        <button
          type="button"
          onClick={onOpenAll}
          aria-haspopup="dialog"
          className="inline-flex items-center min-h-11 text-xs font-semibold text-violet-700 hover:underline self-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
        >
          Ver todos ({totalCommercial})
        </button>
      )}

      {dc.count > 0 && (
        <button
          type="button"
          onClick={onOpenAll}
          aria-haspopup="dialog"
          className={`mt-1 inline-flex items-center min-h-11 text-xs font-medium rounded-lg px-2.5 py-1.5 self-start border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${dc.severity ? SEVERITY_TONE[dc.severity] : "text-slate-600 bg-slate-50 border-slate-200"}`}
        >
          {dc.count} aviso(s) de confiança no dado
        </button>
      )}
    </section>
  );
}
