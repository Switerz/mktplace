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

/** Teto de texto livre vindo da fonte (motivos, notas, alertas). */
export const SOURCE_TEXT_MAX = 200;

/**
 * Texto livre DA FONTE que chega ao modelo: sem caracteres de controle e com
 * teto de tamanho. A fonte e' a nossa API (Pydantic), mas nada garante o
 * tamanho nem o charset desses campos — e eles vao para `content`.
 * `null`/ausente continua `null`.
 */
export function safeText(v: unknown, max = SOURCE_TEXT_MAX): string | null {
  if (typeof v !== "string") return null;
  const limpo = v.replace(/[\p{Cc}\p{Zl}\p{Zp}]/gu, " ").trim();
  if (limpo.length === 0) return null;
  return limpo.length > max ? `${limpo.slice(0, max - 1)}…` : limpo;
}

/** Lista de textos da fonte, com o mesmo saneamento e no maximo 20 itens. */
export function safeTexts(v: readonly unknown[] | null | undefined, max = SOURCE_TEXT_MAX): string[] {
  return (v ?? []).slice(0, 20).map((x) => safeText(x, max)).filter((x): x is string => x !== null);
}
