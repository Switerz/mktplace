/**
 * Envelope canonico de resposta das tools.
 *
 * Regras (docs/ORACLE_MCP_PLAN.md secao 11, Fase F do handoff OM1):
 * - dinheiro permanece NUMERICO no structuredContent; formatacao monetaria
 *   existe apenas no fallback textual;
 * - `null` e' preservado; zero e' preservado como zero; ausencia NUNCA vira 0;
 * - array vazio (consulta valida sem linhas) e' diferente de fonte indisponivel
 *   — indisponibilidade vira erro de tool, nao envelope vazio;
 * - `total_count` so aparece quando o backend fornece total VERDADEIRO;
 * - `refreshed_at` nunca e' inventado: se o backend nao mandou, fica `null`.
 */

export const SOURCE_NAME = "Torre de Controle de Marketplaces — GoBeaute";
export const SOURCE_LAYER = "marts (Neon)";

export type EnvelopePeriod = {
  readonly start: string;
  readonly end: string;
  readonly inclusive: true;
};

export type EnvelopeMeta = {
  source: string;
  layer: string;
  period: EnvelopePeriod | null;
  timezone: string;
  currency: "BRL";
  /** Explicita a unidade: os valores sao REAIS, nao centavos. */
  monetary_unit: "reais";
  /** Eco do que o backend efetivamente aplicou (nunca o input cru do modelo). */
  filters_applied: Record<string, unknown>;
  metric_definition: string;
  /** ISO-8601 UTC vindo do mart, ou `null`. Nunca fabricado. */
  refreshed_at: string | null;
  coverage: string;
  limit: number | null;
  returned_count: number;
  /** Preenchido SOMENTE quando o backend devolve um total verdadeiro. */
  total_count: number | null;
  truncated: boolean;
  warnings: string[];
};

export type Envelope<T> = {
  meta: EnvelopeMeta;
  data: T;
};

export type BuildEnvelopeArgs<T> = {
  period: EnvelopePeriod | null;
  filtersApplied: Record<string, unknown>;
  metricDefinition: string;
  refreshedAt: string | null;
  coverage: string;
  limit?: number | null;
  returnedCount: number;
  totalCount?: number | null;
  warnings?: string[];
  data: T;
};

export function buildEnvelope<T>(args: BuildEnvelopeArgs<T>): Envelope<T> {
  const limit = args.limit ?? null;
  const totalCount = args.totalCount ?? null;
  return {
    meta: {
      source: SOURCE_NAME,
      layer: SOURCE_LAYER,
      period: args.period,
      timezone: "America/Sao_Paulo",
      currency: "BRL",
      monetary_unit: "reais",
      filters_applied: args.filtersApplied,
      metric_definition: args.metricDefinition,
      refreshed_at: args.refreshedAt,
      coverage: args.coverage,
      limit,
      returned_count: args.returnedCount,
      total_count: totalCount,
      // Truncado apenas quando ha total verdadeiro E ele supera o devolvido.
      truncated: totalCount !== null && totalCount > args.returnedCount,
      warnings: args.warnings ?? [],
    },
    data: args.data,
  };
}

/** Aviso obrigatorio quando o periodo alcanca o dia corrente. */
export const CURRENT_DAY_WARNING =
  "O periodo inclui o dia corrente, cuja carga ainda nao fechou: o valor esta subestimado e nao deve ser lido como queda real.";

// ---------------------------------------------------------------------------
// Fallback textual — para clientes que nao consomem structuredContent
// ---------------------------------------------------------------------------

const BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

/** Formata dinheiro APENAS no texto. `null` continua sendo "sem dado". */
export function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return "sem dado";
  return BRL.format(v);
}

export function fmtNumber(v: number | null | undefined): string {
  if (v === null || v === undefined) return "sem dado";
  return new Intl.NumberFormat("pt-BR").format(v);
}

/**
 * Resumo textual curto. Nao substitui o structuredContent — existe para
 * interoperabilidade, conforme a spec 2026-07-28 recomenda ("a tool that
 * returns structured content SHOULD also return the serialized JSON in a
 * TextContent block"). Aqui devolvemos um resumo legivel seguido do JSON.
 */
export function textFallback(headline: string, envelope: Envelope<unknown>): string {
  const lines: string[] = [headline];
  const p = envelope.meta.period;
  if (p) lines.push(`Periodo: ${p.start} a ${p.end} (inclusivo, ${envelope.meta.timezone}).`);
  lines.push(`Fonte: ${envelope.meta.source} — camada ${envelope.meta.layer}.`);
  lines.push(
    `Atualizado em: ${envelope.meta.refreshed_at ?? "nao informado pela fonte"}.`,
  );
  lines.push(`Cobertura: ${envelope.meta.coverage}.`);
  if (envelope.meta.total_count !== null) {
    lines.push(
      `Retornados ${envelope.meta.returned_count} de ${envelope.meta.total_count} registros${envelope.meta.truncated ? " (amostra truncada)" : ""}.`,
    );
  }
  for (const w of envelope.meta.warnings) lines.push(`Aviso: ${w}`);
  lines.push("", JSON.stringify(envelope));
  return lines.join("\n");
}
