/**
 * Schemas de entrada das tools (Zod v4) e resolucao de periodo.
 *
 * Regras (docs/ORACLE_MCP_PLAN.md secao 9):
 * - objetos ESTRITOS: parametro desconhecido e' erro, nunca ignorado;
 * - enums fechados para canal, marca, granularidade e bucket;
 * - datas ISO estritas, intervalo INCLUSIVO, maximo de 366 dias;
 * - nenhum texto livre chega a querystring, path ou SQL.
 */
import { z } from "zod";

export const MAX_RANGE_DAYS = 366;
export const APP_TIMEZONE = "America/Sao_Paulo";

export const BRANDS = ["apice", "barbours", "kokeshi", "lescent", "rituaria"] as const;
export const CHANNELS = ["tiktok", "ml", "shopee"] as const;
export const GRANULARITIES = ["none", "day", "week", "month"] as const;
export const PARETO_BUCKETS = ["A_top50", "B_next30", "C_next15", "D_tail"] as const;

export type Brand = (typeof BRANDS)[number];
export type Channel = (typeof CHANNELS)[number];

/** Rotulos oficiais dos canais — nunca inventados no meio do codigo. */
export const CHANNEL_LABELS: Record<Channel, string> = {
  tiktok: "TikTok Shop",
  ml: "Mercado Livre",
  shopee: "Shopee",
};

/** 27 UFs oficiais + XX (desconhecida), conforme o contrato do backend. */
export const UFS = [
  "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
  "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE",
  "TO", "XX",
] as const;

const isoDate = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, "data deve estar no formato YYYY-MM-DD")
  // Rejeita data sintaticamente valida mas inexistente (ex: 2026-02-31).
  .refine((s) => {
    const [y, m, d] = s.split("-").map(Number);
    const dt = new Date(Date.UTC(y, m - 1, d));
    return (
      dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d
    );
  }, "data inexistente no calendario");

const isoMonth = z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/, "mes deve estar no formato YYYY-MM");

export const PERIOD_PRESETS = [
  "mes_anterior",
  "mes_atual",
  "ultimos_7_dias",
  "ultimos_30_dias",
  "personalizado",
] as const;
export type PeriodPreset = (typeof PERIOD_PRESETS)[number];

// ---------------------------------------------------------------------------
// Resolucao de periodo — relogio injetado, sem leitura implicita do sistema
// ---------------------------------------------------------------------------

/** Data corrente em America/Sao_Paulo, como "YYYY-MM-DD". */
export function todayInAppTimezone(now: Date): string {
  // `en-CA` produz YYYY-MM-DD, que e' exatamente o formato do contrato.
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: APP_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

function parseISO(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

function formatISO(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function addDays(iso: string, days: number): string {
  const d = parseISO(iso);
  d.setUTCDate(d.getUTCDate() + days);
  return formatISO(d);
}

/** Numero de dias do intervalo INCLUSIVO [start, end]. */
export function inclusiveDays(start: string, end: string): number {
  return Math.round((parseISO(end).getTime() - parseISO(start).getTime()) / 86_400_000) + 1;
}

export type ResolvedPeriod = {
  readonly start: string;
  readonly end: string;
  readonly inclusive: true;
  /** Preenchido apenas quando o intervalo e' exatamente um mes calendario. */
  readonly refMonth: string | null;
  /** `true` quando o intervalo alcanca o dia corrente (carga parcial). */
  readonly includesCurrentDay: boolean;
};

function monthBounds(year: number, month: number): { start: string; end: string } {
  const start = `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-01`;
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return { start, end: `${start.slice(0, 8)}${String(lastDay).padStart(2, "0")}` };
}

export type PeriodInput = {
  periodo: PeriodPreset;
  data_inicio?: string | undefined;
  data_fim?: string | undefined;
};

/**
 * Converte o preset (ou o intervalo explicito) em datas concretas.
 * Lanca `RangeError` com mensagem determinista quando o input e' incoerente —
 * quem chama converte para erro de tool.
 */
export function resolvePeriod(input: PeriodInput, now: Date): ResolvedPeriod {
  const today = todayInAppTimezone(now);
  const [ty, tm] = today.split("-").map(Number);

  let start: string;
  let end: string;

  if (input.periodo === "personalizado") {
    if (!input.data_inicio || !input.data_fim) {
      throw new RangeError("periodo 'personalizado' exige data_inicio e data_fim.");
    }
    start = input.data_inicio;
    end = input.data_fim;
    if (start > end) throw new RangeError("data_inicio nao pode ser posterior a data_fim.");
    if (end > today) throw new RangeError("data_fim nao pode ser uma data futura.");
    if (inclusiveDays(start, end) > MAX_RANGE_DAYS) {
      throw new RangeError(`intervalo maximo permitido e de ${MAX_RANGE_DAYS} dias.`);
    }
  } else {
    if (input.data_inicio || input.data_fim) {
      throw new RangeError(
        "data_inicio/data_fim so podem ser usados com periodo='personalizado'.",
      );
    }
    switch (input.periodo) {
      case "mes_anterior": {
        const py = tm === 1 ? ty - 1 : ty;
        const pm = tm === 1 ? 12 : tm - 1;
        ({ start, end } = monthBounds(py, pm));
        break;
      }
      case "mes_atual": {
        const b = monthBounds(ty, tm);
        start = b.start;
        // Mes corrente termina HOJE, nunca no fim do mes — nao inventamos futuro.
        end = today;
        break;
      }
      case "ultimos_7_dias":
        end = today;
        start = addDays(today, -6);
        break;
      case "ultimos_30_dias":
        end = today;
        start = addDays(today, -29);
        break;
    }
  }

  const mb = monthBounds(Number(start.slice(0, 4)), Number(start.slice(5, 7)));
  const refMonth = start === mb.start && end === mb.end ? start.slice(0, 7) : null;

  return { start, end, inclusive: true, refMonth, includesCurrentDay: end >= today };
}

// ---------------------------------------------------------------------------
// Schemas das cinco tools
// ---------------------------------------------------------------------------

const periodShape = {
  periodo: z.enum(PERIOD_PRESETS).default("mes_anterior"),
  data_inicio: isoDate.optional(),
  data_fim: isoDate.optional(),
};

/**
 * Duplicata em filtro e' REJEITADA, nao normalizada em silencio.
 * `["ml","ml"]` quase sempre indica chamada malformada; deduplicar sem avisar
 * esconderia o defeito de quem chamou.
 */
function noDuplicates<T extends z.ZodType>(schema: T, label: string) {
  return schema.refine(
    (v) => !Array.isArray(v) || new Set(v).size === v.length,
    `${label} nao pode conter valores duplicados.`,
  );
}

const scopeShape = {
  canais: noDuplicates(z.array(z.enum(CHANNELS)).min(1).max(3), "canais").optional(),
  marcas: noDuplicates(z.array(z.enum(BRANDS)).min(1).max(5), "marcas").optional(),
};

export const desempenhoInput = z
  .object({
    ...periodShape,
    ...scopeShape,
    granularidade: z.enum(GRANULARITIES).default("none"),
    comparar: z.boolean().default(false),
  })
  .strict();

export const canaisInput = z
  .object({
    periodo: z.enum(["mes_anterior", "mes_atual", "personalizado"]).default("mes_anterior"),
    data_inicio: isoDate.optional(),
    data_fim: isoDate.optional(),
    ...scopeShape,
  })
  .strict();

export const produtosInput = z
  .object({
    canal: z.enum(CHANNELS),
    marca: z.enum(BRANDS).optional(),
    mes: isoMonth.optional(),
    pareto_bucket: z.enum(PARETO_BUCKETS).optional(),
    limite: z.number().int().min(1).max(50).default(20),
  })
  .strict()
  .superRefine((v, ctx) => {
    // A base de produtos do ML e' CUMULATIVA (sem competencia mensal). Aceitar
    // `mes` e ignorar seria mentir sobre o recorte — entao e' erro explicito.
    if (v.canal === "ml" && v.mes !== undefined) {
      ctx.addIssue({
        code: "custom",
        path: ["mes"],
        message:
          "produtos do Mercado Livre vem de uma base cumulativa, sem competencia mensal: 'mes' nao se aplica a este canal.",
      });
    }
  });

export const qualidadeInput = z.object({}).strict();

export const regioesInput = z
  .object({
    periodo: z
      .enum(["mes_anterior", "mes_atual", "ultimos_30_dias", "personalizado"])
      .default("mes_anterior"),
    data_inicio: isoDate.optional(),
    data_fim: isoDate.optional(),
    ...scopeShape,
    ufs: noDuplicates(z.array(z.enum(UFS)).min(1).max(28), "ufs").optional(),
    limite: z.number().int().min(1).max(28).default(27),
  })
  .strict();

export type DesempenhoInput = z.infer<typeof desempenhoInput>;
export type CanaisInput = z.infer<typeof canaisInput>;
export type ProdutosInput = z.infer<typeof produtosInput>;
export type RegioesInput = z.infer<typeof regioesInput>;
