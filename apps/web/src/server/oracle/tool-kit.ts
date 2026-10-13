/**
 * Pecas compartilhadas por TODAS as tools do Oraculo.
 *
 * Existe para que as tools operacionais (MCP-COVERAGE-2) vivam em arquivos
 * proprios sem ciclo de import com `tools.ts`. Nada aqui tem regra de negocio:
 * so' o envelope, os tipos e a resolucao de periodo — movidos de `tools.ts`
 * sem alteracao de comportamento.
 */
import { z } from "zod";

import { textFallback, type Envelope, type EnvelopePeriod, CURRENT_DAY_WARNING } from "./envelope.ts";
import { toolError } from "./errors.ts";
import { resolvePeriod, type ResolvedPeriod } from "./schemas.ts";
import type { TorreClient } from "./torre-client.ts";

export type ToolDeps = {
  client: TorreClient;
  /** Relogio injetavel: a suite nao depende da hora real da maquina. */
  now: () => Date;
};

export type ToolResult = {
  content: Array<{ type: "text"; text: string }>;
  structuredContent: Envelope<unknown>;
};

export type OracleTool = {
  name: string;
  config: {
    title: string;
    description: string;
    inputSchema?: z.ZodType;
    outputSchema: z.ZodType;
  };
  run: (args: unknown, deps: ToolDeps) => Promise<ToolResult>;
};

/** Envelope como schema Zod, para servir de `outputSchema` das tools. */
export function envelopeSchema<T extends z.ZodType>(data: T) {
  return z.object({
    meta: z.object({
      source: z.string(),
      layer: z.string(),
      period: z
        .object({ start: z.string(), end: z.string(), inclusive: z.literal(true) })
        .nullable(),
      timezone: z.string(),
      currency: z.literal("BRL"),
      monetary_unit: z.literal("reais"),
      filters_applied: z.record(z.string(), z.unknown()),
      metric_definition: z.string(),
      refreshed_at: z.string().nullable(),
      coverage: z.string(),
      limit: z.number().nullable(),
      returned_count: z.number(),
      total_count: z.number().nullable(),
      truncated: z.boolean(),
      warnings: z.array(z.string()),
    }),
    data,
  });
}

export const limitationSchema = z.object({
  topic: z.string(),
  scope: z.string(),
  description: z.string(),
});

export const windowSchema = z.object({
  start: z.string(),
  end: z.string(),
  inclusive: z.literal(true),
});

/** Resolve o periodo convertendo erro de faixa em erro de tool determinista. */
export function safeResolvePeriod(input: Parameters<typeof resolvePeriod>[0], now: Date): ResolvedPeriod {
  try {
    return resolvePeriod(input, now);
  } catch (err) {
    throw toolError("INVALID_INPUT", err instanceof Error ? err.message : undefined);
  }
}

export function asWindow(p: { start: string; end: string }): EnvelopePeriod {
  return { start: p.start, end: p.end, inclusive: true };
}

export function periodWarnings(period: ResolvedPeriod, extra: string[] = []): string[] {
  return period.includesCurrentDay ? [CURRENT_DAY_WARNING, ...extra] : extra;
}

export function ok(headline: string, env: Envelope<unknown>): ToolResult {
  return {
    content: [{ type: "text", text: textFallback(headline, env) }],
    structuredContent: env,
  };
}

/** Horas entre um carimbo ISO do mart e `now`. Carimbo invalido => null. */
export function ageHours(stamp: string | null | undefined, now: Date): number | null {
  if (!stamp) return null;
  const t = Date.parse(stamp);
  if (!Number.isFinite(t)) return null;
  return Math.round(((now.getTime() - t) / 3_600_000) * 100) / 100;
}
