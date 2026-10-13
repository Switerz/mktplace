// Rotulos/tons e ordenacao do Resumo Executivo da Gerencial (Gate 2 Fase 1,
// docs/sections/gerencial_audit.md secao 11). Modulo puro (sem React) para
// ser testavel isoladamente — mesmo padrao de canais-channel-metrics.ts.

export type ExecutiveSeverity = "info" | "warning" | "critical";
export type ExecutiveHealthStatus = "ok" | "attention" | "critical";

export const SEVERITY_LABEL: Record<ExecutiveSeverity, string> = {
  info: "Info",
  warning: "Atenção",
  critical: "Crítico",
};

export const SEVERITY_TONE: Record<ExecutiveSeverity, string> = {
  info: "text-accent-ink bg-accent-soft border border-accent/30",
  warning: "text-warn-ink bg-warn-soft border border-warn/35",
  critical: "text-crit-ink bg-crit-soft border border-crit/35",
};

export const HEALTH_STATUS_LABEL: Record<ExecutiveHealthStatus, string> = {
  ok: "Saudável",
  attention: "Atenção",
  critical: "Crítico",
};

export const HEALTH_STATUS_TONE: Record<ExecutiveHealthStatus, string> = {
  ok: "text-ok-ink bg-ok-soft border border-ok/35",
  attention: "text-warn-ink bg-warn-soft border border-warn/35",
  critical: "text-crit-ink bg-crit-soft border border-crit/35",
};

const SEVERITY_RANK: Record<ExecutiveSeverity, number> = { critical: 0, warning: 1, info: 2 };

/**
 * Ordena itens por severidade (critico primeiro), preservando a ordem
 * relativa dentro da mesma severidade (sort estavel) — usado na coluna
 * "Atenções" para que o risco mais grave sempre apareça no topo,
 * independente da ordem em que o backend os construiu.
 */
export function sortBySeverity<T extends { severity: ExecutiveSeverity }>(items: T[]): T[] {
  return [...items].sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
}

/** Rotulo de fallback para severidade desconhecida — nunca quebra a UI. */
export function severityLabel(severity: string): string {
  return SEVERITY_LABEL[severity as ExecutiveSeverity] ?? severity;
}

export function severityTone(severity: string): string {
  return SEVERITY_TONE[severity as ExecutiveSeverity] ?? "text-ink-faint bg-raised border border-line";
}
