"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import type { Pulse, PulseGroup, PulseInsight } from "@/lib/executive-pulse";
import {
  formatMetricByType, formatReferenceValue, formatDeltaAbs, formatDeltaPct,
  referenceKindLabel, metricLabel,
} from "@/lib/executive-pulse";
import { SEVERITY_LABEL, SEVERITY_TONE, type ExecutiveSeverity } from "@/lib/executive-summary";
import DrilldownContextLine from "@/components/drilldown/DrilldownContextLine";
import DrilldownSecao from "@/components/drilldown/DrilldownSecao";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import DrilldownCta from "@/components/drilldown/DrilldownCta";
import InfoDica from "@/components/ui/InfoDica";
import NumeroAnimado from "@/components/ui/NumeroAnimado";

export type PulseView = { mode: "insight" | "all"; key: string | null };

interface Props {
  pulse: Pulse;
  view: PulseView;
  periodLabel: string;
  /** refreshed_at da MESMA resposta fresca do executive-summary que gerou o
   * insight (Gate G2) — null quando a resposta não traz o timestamp; nunca o
   * timestamp de outra requisição. */
  refreshedAt: string | null;
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

/** POPUPS-1 — pele da pilula pela severidade; crescimento e' bom sinal. */
function peleDe(insight: PulseInsight): string {
  if (insight.type === "growth") return "bg-ok-soft text-ok-ink";
  if (insight.severity === "critical") return "bg-crit-soft text-crit-ink";
  if (insight.severity === "warning") return "bg-warn-soft text-warn-ink";
  return "bg-raised text-ink-muted";
}

/** Cor da barra de um caso agrupado, pela severidade do proprio caso. */
const BARRA_SEVERIDADE: Record<ExecutiveSeverity, string> = {
  critical: "bg-crit",
  warning: "bg-warn",
  info: "bg-accent",
};

/**
 * POPUPS-1 — topo do insight no padrao do detalhe de KPI: numero grande, a
 * diferenca em pilula (cor da severidade) com a referencia nomeada, e os selos
 * de contexto. O criterio da severidade fica no "ⓘ".
 */
function InsightTopo({
  insight,
  category,
  periodLabel,
  refreshedAt,
}: {
  insight: PulseInsight;
  category: string;
  periodLabel: string;
  refreshedAt: string | null;
}) {
  const valor = formatMetricByType(insight.type, insight.metric_value);
  // growth/drop: o valor principal JA' e' a variacao %; a pilula leva o R$.
  const variacaoJaNoValor = insight.type === "growth" || insight.type === "drop";
  const diferenca = [
    !variacaoJaNoValor && insight.delta_pct != null ? formatDeltaPct(insight.delta_pct) : null,
    insight.delta_abs != null ? formatDeltaAbs(insight.type, insight.delta_abs) : null,
  ]
    .filter((t): t is string => t != null && t !== "—")
    .join(" · ");
  const referencia =
    insight.reference_value != null
      ? `vs ${formatReferenceValue(insight.type, insight.reference_value)} (${referenceKindLabel(insight.reference_kind)})`
      : null;
  return (
    <div className="flex flex-col gap-2">
      {valor !== "—" && (
        <div className="flex flex-wrap items-end gap-x-3 gap-y-1.5">
          <NumeroAnimado texto={valor} className="text-[32px] font-bold leading-none tracking-tight text-ink tabular-nums" />
          {(diferenca || referencia) && (
            <span className="flex flex-wrap items-center gap-2 pb-0.5">
              {diferenca && (
                <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-sm font-semibold tabular-nums ${peleDe(insight)}`}>
                  {diferenca}
                </span>
              )}
              {referencia && <span className="text-xs text-ink-faint tabular-nums">{referencia}</span>}
            </span>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${SEVERITY_TONE[insight.severity]}`}>
          {SEVERITY_LABEL[insight.severity] ?? insight.severity}
        </span>
        <DrilldownContextLine leading={category} periodLabel={periodLabel} refreshedAt={refreshedAt} />
        {insight.brand && (
          <span className="rounded-full border border-line px-2 py-0.5 text-xs text-ink-muted">{insight.brand}</span>
        )}
        {insight.marketplace && (
          <span className="rounded-full border border-line px-2 py-0.5 text-xs text-ink-muted">{insight.marketplace}</span>
        )}
        <InfoDica texto={`${metricLabel(insight.type)}. ${severityReason(insight.type, insight.severity)}`} />
      </div>
    </div>
  );
}

function InsightDetail({ insight }: { insight: PulseInsight }) {
  const staleEvidence =
    insight.type === "stale_data" &&
    (insight.source || insight.last_date || insight.staleness_days != null || insight.threshold_days != null);
  return (
    <>
      {(insight.description || insight.title) && (
        <p className="text-sm leading-snug text-ink-muted">{insight.description || insight.title}</p>
      )}
      {staleEvidence && (
        <DrilldownSecao titulo="Evidência">
          <ul className="flex flex-col">
            {insight.source && <EvidenceRow label="Origem" value={insight.source} />}
            {insight.last_date && <EvidenceRow label="Última data disponível" value={insight.last_date} />}
            {/* A defasagem ja' e' o numero do topo quando vem em metric_value. */}
            {insight.staleness_days != null && insight.metric_value == null && (
              <EvidenceRow label="Defasagem" value={`${insight.staleness_days} dia(s)`} />
            )}
            {insight.threshold_days != null && (
              <EvidenceRow
                label="Limite de frescor"
                value={`${insight.threshold_days} dia(s)`}
                tone={
                  insight.staleness_days != null && insight.staleness_days > insight.threshold_days ? "warning" : "value"
                }
              />
            )}
          </ul>
        </DrilldownSecao>
      )}
      <DataQualityNote note={insight.confidence_note} />
    </>
  );
}

function GroupDetail({ group, periodLabel, refreshedAt, buildHref }: { group: PulseGroup; periodLabel: string; refreshedAt: string | null; buildHref: (h: string) => string }) {
  const many = group.count > 1;
  // POPUPS-1: casos do maior ao menor (pela magnitude da metrica), com barra
  // relativa ao maior caso e a cor da severidade de cada um.
  const casos = [...group.members].sort(
    (a, b) => Math.abs(b.metric_value ?? 0) - Math.abs(a.metric_value ?? 0),
  );
  const maior = Math.max(0, ...casos.map((m) => Math.abs(m.metric_value ?? 0)));
  return (
    <div className="flex flex-col gap-4 text-sm">
      <InsightTopo
        insight={group.representative}
        category={CATEGORY_LABEL[group.category] ?? group.category}
        periodLabel={periodLabel}
        refreshedAt={refreshedAt}
      />
      <InsightDetail insight={group.representative} />

      {many && (
        <DrilldownSecao titulo="Casos" acao={<span className="tabular-nums">{group.count}</span>}>
          <ul className="flex flex-col">
            {casos.map((m, i) => (
              <li key={i} className="flex flex-col">
                <div className="flex items-center justify-between gap-3">
                  <span className="min-w-0 truncate text-ink-muted">{m.brand ?? m.title}</span>
                  <span className="flex shrink-0 items-center gap-1">
                    <span className="font-semibold text-ink tabular-nums">{formatMetricByType(m.type, m.metric_value)}</span>
                    {/* CTA próprio por membro (Finding 6) — nome acessível
                        identifica a marca/item; filtros preservados por buildHref. */}
                    {m.href && (
                      <Link
                        href={buildHref(m.href)}
                        aria-label={`Abrir ${m.brand ?? m.title} na tela de origem`}
                        className="inline-flex items-center justify-center min-h-11 min-w-11 text-xs font-semibold text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                      >
                        Abrir
                      </Link>
                    )}
                  </span>
                </div>
                {m.metric_value != null && maior > 0 && (
                  <span aria-hidden="true" className="mb-1 block h-1.5 w-full overflow-hidden rounded-full bg-raised">
                    <span
                      className={`tc-barra block h-full rounded-full ${BARRA_SEVERIDADE[m.severity] ?? "bg-accent"}`}
                      style={{ width: `${(Math.abs(m.metric_value) / maior) * 100}%` }}
                    />
                  </span>
                )}
              </li>
            ))}
          </ul>
        </DrilldownSecao>
      )}

      {/* Grupo unitário mantém o CTA único (Finding 6). */}
      {!many && group.representative.href && (
        <DrilldownCta href={buildHref(group.representative.href)}>
          Abrir na tela de origem →
        </DrilldownCta>
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
      className="w-full text-left flex items-center gap-2 rounded-lg border border-line px-3 py-2 min-h-11 hover:bg-accent-soft/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <span className={`text-xs font-semibold rounded px-1.5 py-0.5 shrink-0 ${SEVERITY_TONE[group.severity]}`}>
        {SEVERITY_LABEL[group.severity]}
      </span>
      <span className="min-w-0 flex-1 text-sm font-medium text-ink">{group.title}</span>
      {/* POPUPS-1: o numero do caso unico a direita; grupo mostra a contagem. */}
      <span className="shrink-0 text-xs text-ink-faint tabular-nums">
        {group.count > 1
          ? `${group.count} casos`
          : group.representative.metric_value != null
            ? formatMetricByType(group.type, group.representative.metric_value)
            : null}
      </span>
    </button>
  );
}

/** Conteudo do KpiDrilldownDialog reutilizado pelo Pulso (Gate G1). Dois modos
 * no MESMO diálogo (sem modal empilhado): detalhe de um grupo, ou "Ver todos"
 * listando grupos por categoria — selecionar um grupo troca para o detalhe.
 * Gerencia o foco na troca lista <-> detalhe para nunca deixá-lo no body. */
export default function InsightDrilldownContent(props: Props) {
  const { pulse, view, periodLabel, refreshedAt, onSelectGroup, onBackToAll, buildHref } = props;
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
            className="inline-flex items-center min-h-11 text-xs font-semibold text-accent hover:underline self-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
          >
            ← Voltar para todos os sinais
          </button>
        )}
        <GroupDetail group={selected} periodLabel={periodLabel} refreshedAt={refreshedAt} buildHref={buildHref} />
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
      <DrilldownContextLine periodLabel={periodLabel} refreshedAt={refreshedAt} />
      {sections.every((s) => s.groups.length === 0) && (
        <p className="text-sm text-ink-faint">Nenhum sinal no período.</p>
      )}
      {sections.map((s) =>
        s.groups.length === 0 ? null : (
          <DrilldownSecao key={s.key} titulo={CATEGORY_LABEL[s.key]} acao={<span className="tabular-nums">{s.groups.length}</span>}>
            <div className="flex flex-col gap-1.5">
              {s.groups.map((g) => (
                <GroupButton key={g.key} group={g} onSelect={onSelectGroup} innerRef={assignFirst()} />
              ))}
            </div>
          </DrilldownSecao>
        ),
      )}
    </div>
  );
}
