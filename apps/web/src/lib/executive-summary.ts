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
  info: "text-sky-700 bg-sky-50 border border-sky-200",
  warning: "text-amber-700 bg-amber-50 border border-amber-200",
  critical: "text-rose-700 bg-rose-50 border border-rose-200",
};

export const HEALTH_STATUS_LABEL: Record<ExecutiveHealthStatus, string> = {
  ok: "Saudável",
  attention: "Atenção",
  critical: "Crítico",
};

export const HEALTH_STATUS_TONE: Record<ExecutiveHealthStatus, string> = {
  ok: "text-emerald-700 bg-emerald-50 border border-emerald-200",
  attention: "text-amber-700 bg-amber-50 border border-amber-200",
  critical: "text-rose-700 bg-rose-50 border border-rose-200",
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
  return SEVERITY_TONE[severity as ExecutiveSeverity] ?? "text-slate-500 bg-slate-100 border border-slate-200";
}
