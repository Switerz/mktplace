// Nucleo puro (sem React) do "Pulso do periodo" da Gerencial (Gate G1).
// Categoriza, agrupa e prioriza os insights do resumo executivo de forma
// deterministica e testavel — separando desempenho comercial, eficiencia/
// operacao e confianca no dado. Mesmo padrao de modulo puro de
// executive-summary.ts / canais-channel-metrics.ts.

import type { ExecutiveInsight, ExecutiveDataWarning, ExecutiveSummaryData } from "@/lib/api-client";
import type { ExecutiveSeverity } from "@/lib/executive-summary";
// Import relativo COM extensao `.ts` (mesma convencao de
// produtos-tab-transition.ts) porque este e' um import de VALOR: sob
// `node --test` o alias `@/` nao e' resolvido e a extensao e' exigida;
// `allowImportingTsExtensions` no tsconfig cobre o build.
import { fmtBrl } from "./formatters.ts";

export type PulseCategory = "performance" | "efficiency_ops" | "data_confidence";

const CHANNEL_LABEL: Record<string, string> = {
  tiktok: "TikTok Shop",
  ml: "Mercado Livre",
  shopee: "Shopee",
};

const CATEGORY_BY_TYPE: Record<string, PulseCategory> = {
  growth: "performance",
  drop: "performance",
  high_cost: "efficiency_ops",
  high_cancel_rate: "efficiency_ops",
  stale_data: "data_confidence",
  low_regional_coverage: "data_confidence",
  missing_data: "data_confidence",
  not_applicable: "data_confidence",
};

const SEVERITY_RANK: Record<ExecutiveSeverity, number> = { critical: 0, warning: 1, info: 2 };

// Prioridade fixa por tipo entre insights COMERCIAIS (Gate G1). Tipos fora
// desta lista nunca disputam as vagas do topo comercial.
const TYPE_PRIORITY: Record<string, number> = {
  drop: 0,
  high_cancel_rate: 1,
  high_cost: 2,
  growth: 3,
};

export interface PulseInsight {
  type: string;
  severity: ExecutiveSeverity;
  category: PulseCategory;
  title: string;
  description: string;
  brand: string | null;
  marketplace: string | null;
  metric_value: number | null;
  href: string | null;
  reference_value: number | null;
  reference_kind: string | null;
  delta_abs: number | null;
  delta_pct: number | null;
  confidence_note: string | null;
  // Evidencia de frescor (stale_data) — propagada ate o drill-down (Finding 4).
  source: string | null;
  last_date: string | null;
  threshold_days: number | null;
  staleness_days: number | null;
}

export interface PulseGroup {
  key: string;
  category: PulseCategory;
  type: string;
  marketplace: string | null;
  severity: ExecutiveSeverity;
  title: string;
  members: PulseInsight[];
  representative: PulseInsight;
  count: number;
}

export interface Pulse {
  commercial: PulseGroup[];
  top: PulseGroup[];
  rest: PulseGroup[];
  dataConfidence: {
    groups: PulseGroup[];
    count: number;
    severity: ExecutiveSeverity | null;
  };
  /** Ha `missing_data` no payload — a apresentacao deve dizer "Dados
   * indisponiveis", nunca "Crítico" como diagnostico comercial (Finding 3). */
  dataUnavailable: boolean;
}

const TOP_LIMIT = 3;

function categoryOf(type: string, declared: PulseCategory | null | undefined): PulseCategory {
  // Usa a categoria do backend quando presente; senao deriva do tipo
  // (fallback seguro para payload legado). Desconhecido -> confianca no dado.
  return declared ?? CATEGORY_BY_TYPE[type] ?? "data_confidence";
}

function normalizeInsight(i: ExecutiveInsight): PulseInsight {
  return {
    type: i.type,
    severity: i.severity,
    category: categoryOf(i.type, i.category),
    title: i.title,
    description: i.description,
    brand: i.brand ?? null,
    marketplace: i.marketplace ?? null,
    metric_value: i.metric_value ?? null,
    href: i.href ?? null,
    reference_value: i.reference_value ?? null,
    reference_kind: i.reference_kind ?? null,
    delta_abs: i.delta_abs ?? null,
    delta_pct: i.delta_pct ?? null,
    confidence_note: i.confidence_note ?? null,
    source: i.source ?? null,
    last_date: i.last_date ?? null,
    threshold_days: i.threshold_days ?? null,
    staleness_days: i.staleness_days ?? null,
  };
}

function normalizeWarning(w: ExecutiveDataWarning): PulseInsight {
  return {
    type: w.type,
    severity: w.severity,
    category: categoryOf(w.type, w.category),
    title: w.message,
    description: "",
    brand: null,
    marketplace: null,
    metric_value: null,
    href: w.href ?? null,
    reference_value: null,
    reference_kind: null,
    delta_abs: null,
    delta_pct: null,
    confidence_note: null,
    source: null,
    last_date: null,
    threshold_days: null,
    staleness_days: null,
  };
}

/**
 * Chave de agrupamento por REGRA (Gate G1) — nunca universal:
 * - growth/drop: por marca (nunca colapsam marcas distintas);
 * - high_cancel_rate/high_cost: por regra e canal;
 * - stale_data: por origem (source) e canal (diario x regional nao colapsam);
 * - demais confianca-no-dado: por tipo/origem/mensagem, sem misturar avisos
 *   semanticamente diferentes.
 */
export function groupKeyOf(i: PulseInsight): string {
  const c = i.category;
  if (i.type === "growth" || i.type === "drop") {
    return `${c}|${i.type}|${i.brand ?? ""}|${i.marketplace ?? ""}`;
  }
  if (i.type === "high_cancel_rate" || i.type === "high_cost") {
    return `${c}|${i.type}|${i.marketplace ?? ""}`;
  }
  if (i.type === "stale_data") {
    return `${c}|stale_data|${i.source ?? ""}|${i.marketplace ?? ""}`;
  }
  return `${c}|${i.type}|${i.marketplace ?? ""}|${i.href ?? ""}`;
}

/** Magnitude comparavel SOMENTE entre itens do mesmo tipo (nunca cruza
 * unidades — pp de custo nunca e' comparado com % de variacao de GMV). */
function magnitudeOf(i: PulseInsight): number {
  if (i.type === "growth" || i.type === "drop") {
    return Math.abs(i.delta_pct ?? i.metric_value ?? 0);
  }
  if (i.type === "high_cost" || i.type === "high_cancel_rate") {
    return Math.abs(i.delta_abs ?? i.metric_value ?? 0);
  }
  return Math.abs(i.metric_value ?? 0);
}

/** Pior severidade de uma lista (menor rank = pior). Retorna null se vazia. */
function worstSeverity(severities: ExecutiveSeverity[]): ExecutiveSeverity | null {
  if (severities.length === 0) return null;
  return severities.reduce((worst, s) => (SEVERITY_RANK[s] < SEVERITY_RANK[worst] ? s : worst));
}

function maxSeverity(items: PulseInsight[]): ExecutiveSeverity {
  return worstSeverity(items.map((i) => i.severity)) ?? "info";
}

function groupTitle(type: string, marketplace: string | null, members: PulseInsight[], representative: PulseInsight): string {
  if (members.length <= 1) return representative.title;
  const channel = marketplace ? CHANNEL_LABEL[marketplace] ?? marketplace : "";
  if (type === "high_cost") return `${members.length} marcas com custo no p75 ou acima no ${channel}`;
  if (type === "high_cancel_rate") return `${members.length} marcas com cancelamento alto no ${channel}`;
  if (type === "stale_data") return `${members.length} fontes com dado desatualizado`;
  return `${members.length} itens · ${representative.title}`;
}

function buildGroups(items: PulseInsight[]): PulseGroup[] {
  const byKey = new Map<string, PulseInsight[]>();
  const order: string[] = [];
  for (const i of items) {
    const k = groupKeyOf(i);
    if (!byKey.has(k)) { byKey.set(k, []); order.push(k); }
    byKey.get(k)!.push(i);
  }
  return order.map((key) => {
    const members = byKey.get(key)!;
    const representative = [...members].sort((a, b) => {
      const dm = magnitudeOf(b) - magnitudeOf(a);
      if (dm !== 0) return dm;
      return (a.brand ?? "").localeCompare(b.brand ?? "");
    })[0];
    return {
      key,
      category: representative.category,
      type: representative.type,
      marketplace: representative.marketplace,
      severity: maxSeverity(members),
      title: groupTitle(representative.type, representative.marketplace, members, representative),
      members,
      representative,
      count: members.length,
    };
  });
}

/** Ordenacao deterministica dos grupos COMERCIAIS: severidade -> prioridade
 * fixa por tipo -> magnitude (so' compara dentro do mesmo tipo) -> desempate
 * lexicografico estavel pela chave. */
function compareCommercial(a: PulseGroup, b: PulseGroup): number {
  const s = SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity];
  if (s !== 0) return s;
  const tp = (TYPE_PRIORITY[a.type] ?? 99) - (TYPE_PRIORITY[b.type] ?? 99);
  if (tp !== 0) return tp;
  // Mesmo tipo aqui — comparar magnitude e' seguro (mesma unidade).
  const m = magnitudeOf(b.representative) - magnitudeOf(a.representative);
  if (m !== 0) return m;
  return a.key.localeCompare(b.key);
}

/** Constroi o Pulso a partir do payload do resumo executivo. Robusto a
 * payload legado sem os campos aditivos (categoria derivada do tipo). */
export function buildPulse(data: ExecutiveSummaryData | null): Pulse {
  const empty: Pulse = { commercial: [], top: [], rest: [], dataConfidence: { groups: [], count: 0, severity: null }, dataUnavailable: false };
  if (!data) return empty;

  const all: PulseInsight[] = [
    ...(data.changes ?? []).map(normalizeInsight),
    ...(data.risks ?? []).map(normalizeInsight),
    ...(data.data_warnings ?? []).map(normalizeWarning),
  ];

  const commercialItems = all.filter((i) => i.category === "performance" || i.category === "efficiency_ops");
  const dataItems = all.filter((i) => i.category === "data_confidence");

  const commercial = buildGroups(commercialItems).sort(compareCommercial);
  const dataGroups = buildGroups(dataItems);
  const dataCount = dataGroups.reduce((n, g) => n + g.count, 0);

  return {
    commercial,
    top: commercial.slice(0, TOP_LIMIT),
    rest: commercial.slice(TOP_LIMIT),
    dataConfidence: {
      groups: dataGroups,
      count: dataCount,
      // Severidade consolidada = pior de TODOS os grupos, e cada grupo ja e' o
      // pior de TODOS os seus membros (nunca só o representante escolhido por
      // magnitude — Finding 5).
      severity: worstSeverity(dataGroups.map((g) => g.severity)),
    },
    dataUnavailable: all.some((i) => i.type === "missing_data"),
  };
}

// Formatacao ORIENTADA AO CAMPO (Finding 1) — o mesmo `type` tem unidades
// diferentes por campo: em growth/drop o valor principal e' %, mas a
// referencia (GMV anterior) e o delta_abs sao em BRL. Formatar tudo pelo tipo
// (como antes) rotulava reference/delta de growth como "%", o que era falso.

/** Valor principal (metric_value) — sempre percentual/dias por tipo. */
export function formatMetricByType(type: string, value: number | null | undefined): string {
  if (value == null) return "—";
  switch (type) {
    case "growth":
    case "drop":
      return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
    case "high_cost":
    case "high_cancel_rate":
    case "low_regional_coverage":
      return `${value.toFixed(1)}%`;
    case "stale_data":
      return `${Math.round(value)} dia(s)`;
    default:
      return "—";
  }
}

/** Referencia (reference_value). growth/drop -> GMV anterior em BRL;
 * custo/cancelamento/cobertura -> %; demais -> "—". */
export function formatReferenceValue(type: string, value: number | null | undefined): string {
  if (value == null) return "—";
  if (type === "growth" || type === "drop") return fmtBrl(value);
  if (type === "high_cost" || type === "high_cancel_rate" || type === "low_regional_coverage") {
    return `${value.toFixed(1)}%`;
  }
  return "—";
}

/** Diferenca absoluta (delta_abs). growth/drop -> BRL (com sinal);
 * custo/cancelamento -> pontos percentuais (com sinal); demais -> "—". */
export function formatDeltaAbs(type: string, value: number | null | undefined): string {
  if (value == null) return "—";
  if (type === "growth" || type === "drop") return `${value >= 0 ? "+" : ""}${fmtBrl(value)}`;
  if (type === "high_cost" || type === "high_cancel_rate") return `${value >= 0 ? "+" : ""}${value.toFixed(1)} p.p.`;
  return "—";
}

/** Diferenca percentual (delta_pct) — sempre %. */
export function formatDeltaPct(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

/** Rótulo do valor principal por tipo, para o cabeçalho do drill-down. */
export function metricLabel(type: string): string {
  if (type === "growth" || type === "drop") return "Variação";
  if (type === "stale_data") return "Defasagem";
  return "Valor atual";
}

const REFERENCE_KIND_LABEL: Record<string, string> = {
  median: "mediana",
  p75: "p75",
  threshold: "limite",
  previous_period: "período anterior",
};

export function referenceKindLabel(kind: string | null | undefined): string {
  return kind ? REFERENCE_KIND_LABEL[kind] ?? kind : "referência";
}
