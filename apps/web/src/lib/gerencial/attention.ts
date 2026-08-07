/**
 * Fila de atencao e faixa de confianca da Gerencial V2 (Gate V2-1, Tasks C e J).
 *
 * Fonte unica: `/executive-summary`, que ja entrega todos os campos
 * necessarios (`severity`, `category`, `metric_value`, `reference_value`,
 * `reference_kind`, `delta_abs`, `delta_pct`, `confidence_note`, `source`,
 * `last_date`, `staleness_days`, `href`). Nenhum endpoint novo.
 *
 * Este modulo substitui o alerta hard-coded por marca que existia no JSX da
 * Gerencial (`if (brand === "lescent")`) — regra de negocio embutida em
 * markup, invisivel para quem mantem a metrica.
 *
 * Regra estrutural do G1 preservada: **risco comercial e aviso de confianca no
 * dado nunca compartilham lista**. Ausencia de dado nao e' diagnostico
 * comercial, e por isso um aviso de dado nunca recebe severidade comercial
 * "Critico" — ele e' rebaixado para um tom informativo proprio.
 */
import type {
  ExecutiveDataWarning,
  ExecutiveInsight,
  ExecutiveSummaryData,
  InsightReferenceKind,
} from "../api-client.ts";
import { isMarketplaceSelected } from "../marketplace-filter.ts";
import type { Marketplace } from "../mock-data.ts";
import { CHANNEL_LABEL } from "./kpi-band.ts";

export type CommercialSeverity = "critical" | "warning" | "info";
/** Avisos de dado tem escala PROPRIA — nunca "Critico" comercial. */
export type DataSeverity = "attention" | "note";

export const REFERENCE_KIND_LABEL: Record<InsightReferenceKind, string> = {
  median: "mediana do canal",
  p75: "p75 do canal",
  threshold: "limiar",
  previous_period: "período anterior",
};

export interface CommercialAttentionItem {
  key: string;
  severity: CommercialSeverity;
  title: string;
  description: string;
  metricValue: number | null;
  referenceValue: number | null;
  referenceKind: InsightReferenceKind | null;
  deltaAbs: number | null;
  deltaPct: number | null;
  confidenceNote: string | null;
  source: string | null;
  lastDate: string | null;
  brand: string | null;
  marketplace: string | null;
  href: string;
  type: string;
}

export interface DataConfidenceItem {
  key: string;
  severity: DataSeverity;
  message: string;
  source: string | null;
  lastDate: string | null;
  stalenessDays: number | null;
  thresholdDays: number | null;
  href: string | null;
  type: string;
}

export interface AttentionQueue {
  commercial: CommercialAttentionItem[];
  dataConfidence: DataConfidenceItem[];
}

function isDataCategory(insight: ExecutiveInsight): boolean {
  return insight.category === "data_confidence";
}

/** Um insight de dado nunca carrega severidade comercial. */
function toDataSeverity(severity: ExecutiveInsight["severity"]): DataSeverity {
  return severity === "info" ? "note" : "attention";
}

export function buildAttentionQueue(summary: ExecutiveSummaryData | null): AttentionQueue {
  if (!summary) return { commercial: [], dataConfidence: [] };

  const commercial: CommercialAttentionItem[] = [];
  const dataConfidence: DataConfidenceItem[] = [];

  const insights = [...summary.risks, ...summary.changes];
  insights.forEach((insight, index) => {
    if (isDataCategory(insight)) {
      dataConfidence.push({
        key: `insight:${insight.type}:${index}`,
        severity: toDataSeverity(insight.severity),
        message: insight.description || insight.title,
        source: insight.source ?? null,
        lastDate: insight.last_date ?? null,
        stalenessDays: insight.staleness_days ?? null,
        thresholdDays: insight.threshold_days ?? null,
        href: insight.href || null,
        type: insight.type,
      });
      return;
    }
    commercial.push({
      key: `insight:${insight.type}:${index}`,
      severity: insight.severity,
      title: insight.title,
      description: insight.description,
      metricValue: insight.metric_value ?? null,
      referenceValue: insight.reference_value ?? null,
      referenceKind: insight.reference_kind ?? null,
      deltaAbs: insight.delta_abs ?? null,
      deltaPct: insight.delta_pct ?? null,
      confidenceNote: insight.confidence_note ?? null,
      source: insight.source ?? null,
      lastDate: insight.last_date ?? null,
      brand: insight.brand ?? null,
      marketplace: insight.marketplace ?? null,
      href: insight.href,
      type: insight.type,
    });
  });

  summary.data_warnings.forEach((warning: ExecutiveDataWarning, index) => {
    dataConfidence.push({
      key: `warning:${warning.type}:${index}`,
      severity: toDataSeverity(warning.severity),
      message: warning.message,
      source: null,
      lastDate: null,
      stalenessDays: null,
      thresholdDays: null,
      href: warning.href,
      type: warning.type,
    });
  });

  const severityRank: Record<CommercialSeverity, number> = { critical: 0, warning: 1, info: 2 };
  commercial.sort(
    (a, b) => severityRank[a.severity] - severityRank[b.severity] || a.title.localeCompare(b.title),
  );

  return { commercial, dataConfidence };
}

// ---------------------------------------------------------------------------
// Faixa de confianca no dado (nao e' KPI)
// ---------------------------------------------------------------------------

/**
 * Estado de DISPONIBILIDADE da serie de um canal. Deliberadamente nao se chama
 * "cobertura": disponibilidade de serie nao comprova completude do dado.
 */
export type SeriesAvailability = "checking" | "available" | "no_records" | "unavailable";

export const AVAILABILITY_LABEL: Record<SeriesAvailability, string> = {
  checking: "verificando",
  available: "série disponível",
  no_records: "sem registros no período",
  unavailable: "série indisponível",
};

export interface ChannelAvailability {
  channel: Marketplace;
  label: string;
  availability: SeriesAvailability;
}

export interface ConfidenceStripData {
  channels: ChannelAvailability[];
  selectedCount: number;
  availableCount: number;
  noRecordsCount: number;
  unavailableCount: number;
  checkingCount: number;
  /** `false` quando o executive-summary nao respondeu: defasagem e avisos
   * simplesmente NAO foram verificados — o que e' diferente de "sem avisos". */
  warningsChecked: boolean;
  /** Maior defasagem observada entre os avisos, em dias. */
  maxStalenessDays: number | null;
  warningCount: number;
  /** Fontes nomeadas nos avisos, sem repeticao. */
  sources: string[];
}

/** Entrada minima por canal — o chamador traduz o estado da serie. */
export interface ChannelSeriesState {
  channel: Marketplace;
  status: "loading" | "fresh" | "error";
  pointCount: number;
}

/**
 * Deriva a disponibilidade da serie de cada canal.
 *
 * Um ponto com valor ZERO continua sendo serie disponivel — o que decide e' a
 * existencia de registros, nunca o valor. Era exatamente a confusao da versao
 * anterior, que usava `gmv != null` do agregado para afirmar "cobertura": um
 * zero real ou a ausencia de linha caiam na mesma gaveta.
 */
export function seriesAvailability(state: ChannelSeriesState): SeriesAvailability {
  if (state.status === "loading") return "checking";
  if (state.status === "error") return "unavailable";
  return state.pointCount > 0 ? "available" : "no_records";
}

export function buildConfidenceStrip(
  seriesStates: readonly ChannelSeriesState[],
  queue: AttentionQueue,
  channels: readonly Marketplace[],
  /** O executive-summary concluiu com sucesso? Se nao, avisos/defasagem nao
   * foram verificados, mas a disponibilidade das series continua valendo. */
  warningsChecked: boolean,
): ConfidenceStripData {
  const selected = seriesStates.filter((s) => isMarketplaceSelected(channels, s.channel));
  const channelsOut: ChannelAvailability[] = selected.map((s) => ({
    channel: s.channel,
    label: CHANNEL_LABEL[s.channel],
    availability: seriesAvailability(s),
  }));

  let maxStalenessDays: number | null = null;
  const sources = new Set<string>();
  if (warningsChecked) {
    for (const item of queue.dataConfidence) {
      if (item.stalenessDays != null) {
        maxStalenessDays =
          maxStalenessDays == null ? item.stalenessDays : Math.max(maxStalenessDays, item.stalenessDays);
      }
      if (item.source) sources.add(item.source);
    }
  }

  const count = (a: SeriesAvailability) => channelsOut.filter((c) => c.availability === a).length;

  return {
    channels: channelsOut,
    selectedCount: channelsOut.length,
    availableCount: count("available"),
    noRecordsCount: count("no_records"),
    unavailableCount: count("unavailable"),
    checkingCount: count("checking"),
    warningsChecked,
    maxStalenessDays,
    warningCount: warningsChecked ? queue.dataConfidence.length : 0,
    sources: [...sources],
  };
}
