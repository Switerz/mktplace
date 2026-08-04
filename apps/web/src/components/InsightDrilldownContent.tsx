"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import type { Pulse, PulseGroup, PulseInsight } from "@/lib/executive-pulse";
import {
  formatMetricByType, formatReferenceValue, formatDeltaAbs, formatDeltaPct,
  referenceKindLabel, metricLabel,
} from "@/lib/executive-pulse";
import { SEVERITY_LABEL, SEVERITY_TONE, type ExecutiveSeverity } from "@/lib/executive-summary";

export type PulseView = { mode: "insight" | "all"; key: string | null };

interface Props {
  pulse: Pulse;
  view: PulseView;
  periodLabel: string;
  /** No modo "all", selecionar um grupo mostra o detalhe no MESMO diálogo. */
  onSelectGroup: (key: string) => void;
  onBackToAll: () => void;
  buildHref: (href: string) => string;
}

const CATEGORY_LABEL: Record<string, string> = {
  performance: "Desempenho",
  efficiency_ops: "Eficiência e operação",
  data_confidence: "Confiança no dado",
};

function findGroup(pulse: Pulse, key: string | null): PulseGroup | null {
  if (!key) return null;
  return (
    pulse.commercial.find((g) => g.key === key) ??
    pulse.dataConfidence.groups.find((g) => g.key === key) ??
    null
  );
}

function severityReason(type: string, severity: ExecutiveSeverity): string {
  switch (type) {
    case "drop":
      return severity === "critical" ? "Queda acentuada no período (≥ 30%)." : "Queda relevante frente ao período anterior.";
    case "growth":
      return "Crescimento relevante frente ao período anterior.";
    case "high_cost":
      return "Custo de marketplace no topo do canal (≥ p75) e acima da mediana.";
    case "high_cancel_rate":
      return "Cancelamento acima do limite do canal (mediana × 1,5).";
    case "stale_data":
      return "Dado além do limite de frescor definido.";
    case "low_regional_coverage":
      return severity === "warning" ? "Cobertura regional baixa no período." : "Cobertura regional parcial no período.";
    case "missing_data":
      return "Sem dados para os filtros selecionados.";
    case "not_applicable":
      return "Nota estrutural — não é um risco comercial.";
    default:
      return SEVERITY_LABEL[severity] ?? severity;
  }
}

function InsightDetail({ insight }: { insight: PulseInsight }) {
  const hasReference = insight.reference_value != null;
  const hasDelta = insight.delta_abs != null || insight.delta_pct != null;
  const staleEvidence =
    insight.type === "stale_data" &&
    (insight.source || insight.last_date || insight.staleness_days != null || insight.threshold_days != null);
  return (
    <div className="flex flex-col gap-3 text-sm">
      <div>
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">O que aconteceu</p>
        <p className="text-slate-700">{insight.description || insight.title}</p>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <p className="text-xs text-slate-400">{metricLabel(insight.type)}</p>
          <p className="text-base font-bold text-gray-900 tabular-nums">{formatMetricByType(insight.type, insight.metric_value)}</p>
        </div>
        {hasReference && (
          <div>
            <p className="text-xs text-slate-400">Referência ({referenceKindLabel(insight.reference_kind)})</p>
            <p className="text-base font-semibold text-slate-700 tabular-nums">{formatReferenceValue(insight.type, insight.reference_value)}</p>
          </div>
        )}
      </div>
      {hasDelta && (
        <div>
          <p className="text-xs text-slate-400">Diferença</p>
          <p className="text-sm font-semibold text-slate-700 tabular-nums">
            {insight.delta_pct != null && formatDeltaPct(insight.delta_pct)}
            {insight.delta_pct != null && insight.delta_abs != null && " · "}
            {insight.delta_abs != null && formatDeltaAbs(insight.type, insight.delta_abs)}
          </p>
        </div>
      )}
      {staleEvidence && (
        <div className="text-xs text-slate-500 flex flex-col gap-0.5 border border-slate-100 rounded-lg px-3 py-2">
          {insight.source && <span>Origem: <span className="font-medium text-slate-700">{insight.source}</span></span>}
          {insight.last_date && <span>Última data disponível: <span className="font-medium text-slate-700">{insight.last_date}</span></span>}
          {insight.staleness_days != null && <span>Defasagem: <span className="font-medium text-slate-700">{insight.staleness_days} dia(s)</span></span>}
          {insight.threshold_days != null && <span>Limite de frescor: <span className="font-medium text-slate-700">{insight.threshold_days} dia(s)</span></span>}
        </div>
      )}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
        {insight.brand && <span>Marca: <span className="font-medium text-slate-700">{insight.brand}</span></span>}
        {insight.marketplace && <span>Canal: <span className="font-medium text-slate-700">{insight.marketplace}</span></span>}
      </div>
      <div>
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Por que essa severidade</p>
        <p className="text-slate-600 text-xs">{severityReason(insight.type, insight.severity)}</p>
      </div>
      {insight.confidence_note && (
        <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5">
          {insight.confidence_note}
        </p>
      )}
    </div>
  );
}

function GroupDetail({ group, periodLabel, buildHref }: { group: PulseGroup; periodLabel: string; buildHref: (h: string) => string }) {
  const many = group.count > 1;
  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-slate-400">{CATEGORY_LABEL[group.category] ?? group.category} · {periodLabel}</p>

      {many ? (
        <div className="flex flex-col gap-2">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">{group.count} itens agrupados</p>
          <ul className="flex flex-col divide-y divide-slate-100 border border-slate-100 rounded-lg">
            {group.members.map((m, i) => (
              <li key={i} className="flex items-center justify-between gap-3 px-3 py-1.5 text-sm">
                <span className="text-slate-700 truncate">{m.brand ?? m.title}</span>
                <div className="flex items-center gap-3 shrink-0">
                  <span className="tabular-nums font-semibold text-slate-800">{formatMetricByType(m.type, m.metric_value)}</span>
                  {/* CTA próprio por membro (Finding 6) — nome acessível
                      identifica a marca/item; filtros preservados por buildHref. */}
                  {m.href && (
                    <Link
                      href={buildHref(m.href)}
                      aria-label={`Abrir ${m.brand ?? m.title} na tela de origem`}
                      className="inline-flex items-center justify-center min-h-11 min-w-11 text-xs font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
                    >
                      Abrir
                    </Link>
                  )}
                </div>
              </li>
            ))}
          </ul>
          <div className="pt-1">
            <InsightDetail insight={group.representative} />
          </div>
        </div>
      ) : (
        <InsightDetail insight={group.representative} />
      )}

      {/* Grupo unitário mantém o CTA único (Finding 6). */}
      {!many && group.representative.href && (
        <Link
          href={buildHref(group.representative.href)}
          className="inline-flex items-center min-h-11 text-sm font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded self-start"
        >
          Abrir na tela de origem →
        </Link>
      )}
    </div>
  );
}

function GroupButton({ group, onSelect, innerRef }: { group: PulseGroup; onSelect: (key: string) => void; innerRef?: React.Ref<HTMLButtonElement> }) {
  return (
    <button
      ref={innerRef}
      type="button"
      onClick={() => onSelect(group.key)}
      className="w-full text-left flex items-start gap-2 rounded-lg border border-violet-100 px-3 py-2 min-h-11 hover:bg-violet-50/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
    >
      <span className={`text-[10px] font-semibold uppercase rounded px-1.5 py-0.5 shrink-0 mt-0.5 ${SEVERITY_TONE[group.severity]}`}>
        {SEVERITY_LABEL[group.severity]}
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium text-slate-800">{group.title}</span>
        {group.count === 1 && group.representative.metric_value != null && (
          <span className="block text-xs text-slate-500 tabular-nums">{formatMetricByType(group.type, group.representative.metric_value)}</span>
        )}
      </span>
    </button>
  );
}

/** Conteudo do KpiDrilldownDialog reutilizado pelo Pulso (Gate G1). Dois modos
 * no MESMO diálogo (sem modal empilhado): detalhe de um grupo, ou "Ver todos"
 * listando grupos por categoria — selecionar um grupo troca para o detalhe.
 * Gerencia o foco na troca lista <-> detalhe para nunca deixá-lo no body. */
export default function InsightDrilldownContent(props: Props) {
  const { pulse, view, periodLabel, onSelectGroup, onBackToAll, buildHref } = props;
  const selected = findGroup(pulse, view.key);
  const backRef = useRef<HTMLButtonElement>(null);
  const firstGroupRef = useRef<HTMLButtonElement>(null);

  // Foco coerente ao alternar dentro do modo "all": ao abrir um grupo, foca o
  // botão "Voltar"; ao voltar à lista, foca o primeiro grupo (nunca o body).
  // No mount do diálogo, o KpiDrilldownDialog já foca o botão de fechar.
  useEffect(() => {
    if (view.mode !== "all") return;
    if (view.key) backRef.current?.focus();
    else firstGroupRef.current?.focus();
  }, [view.mode, view.key]);

  if (selected) {
    return (
      <div className="flex flex-col gap-3">
        {view.mode === "all" && (
          <button
            ref={backRef}
            type="button"
            onClick={onBackToAll}
            className="inline-flex items-center min-h-11 text-xs font-semibold text-violet-700 hover:underline self-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
          >
            ← Voltar para todos os sinais
          </button>
        )}
        <GroupDetail group={selected} periodLabel={periodLabel} buildHref={buildHref} />
      </div>
    );
  }

  // Modo "all" sem grupo selecionado: lista por categoria.
  const sections: { key: PulseGroup["category"]; groups: PulseGroup[] }[] = [
    { key: "performance", groups: pulse.commercial.filter((g) => g.category === "performance") },
    { key: "efficiency_ops", groups: pulse.commercial.filter((g) => g.category === "efficiency_ops") },
    { key: "data_confidence", groups: pulse.dataConfidence.groups },
  ];
  let firstAssigned = false;
  const assignFirst = () => (firstAssigned ? undefined : ((firstAssigned = true), firstGroupRef));

  return (
    <div className="flex flex-col gap-4">
      {sections.every((s) => s.groups.length === 0) && (
        <p className="text-sm text-slate-500">Nenhum sinal no período.</p>
      )}
      {sections.map((s) =>
        s.groups.length === 0 ? null : (
          <div key={s.key} className="flex flex-col gap-2">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">{CATEGORY_LABEL[s.key]}</p>
            {s.groups.map((g) => (
              <GroupButton key={g.key} group={g} onSelect={onSelectGroup} innerRef={assignFirst()} />
            ))}
          </div>
        ),
      )}
    </div>
  );
}
