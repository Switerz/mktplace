import {
  parseMarketplaceParam,
  serializeMarketplaceSelection,
} from "../marketplace-filter.ts";
import { ALL_BRAND_KEYS } from "./brands.ts";
import { presetRange, toISODate } from "./presets.ts";
import type { DatePreset, GlobalFilters } from "./types.ts";

export const MAX_RANGE_DAYS = 366;

/**
 * Valida uma data de calendario REAL — `new Date("2026-02-31")` nao lanca
 * nem retorna NaN em JS, ele "rola" silenciosamente para 03/03/2026. Por
 * isso a validacao reconstroi a data a partir dos componentes numericos e
 * confere que ela nao mudou (round-trip), em vez de confiar no parser do
 * Date nativo.
 */
function isValidISODate(s: string): boolean {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return false;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return false;
  const dt = new Date(year, month - 1, day);
  return dt.getFullYear() === year && dt.getMonth() === month - 1 && dt.getDate() === day;
}

function daysBetween(from: string, to: string): number {
  const a = new Date(`${from}T00:00:00`).getTime();
  const b = new Date(`${to}T00:00:00`).getTime();
  return Math.round((b - a) / 86_400_000) + 1;
}

export interface DateRangeValidation {
  valid: boolean;
  error?: string;
}

/**
 * Validacao completa de um par (dateFrom, dateTo) — calendario real, ordem,
 * intervalo maximo e data futura. Usada tanto ao ler a URL quanto antes de
 * disparar qualquer request (DateRangeFilter chama isso antes de propagar
 * onChange).
 */
export function validateDateRange(dateFrom: string, dateTo: string, today: Date = new Date()): DateRangeValidation {
  if (!isValidISODate(dateFrom) || !isValidISODate(dateTo)) {
    return { valid: false, error: "Data invalida — confira o calendario." };
  }
  if (dateFrom > dateTo) {
    return { valid: false, error: "A data inicial não pode ser posterior à data final." };
  }
  if (daysBetween(dateFrom, dateTo) > MAX_RANGE_DAYS) {
    return { valid: false, error: `Intervalo máximo permitido é de ${MAX_RANGE_DAYS} dias.` };
  }
  if (dateTo > toISODate(today)) {
    return { valid: false, error: "A data final não pode ser uma data futura." };
  }
  return { valid: true };
}

/**
 * Le os filtros globais da URL. Em qualquer entrada invalida (datas
 * malformadas/inexistentes no calendario, invertidas, intervalo > 366 dias,
 * data futura, marca desconhecida), cai para o preset default (ultimos 30
 * dias / marca desconhecida ignorada) em vez de quebrar a tela — nunca
 * lanca excecao.
 */
export function parseFiltersFromSearchParams(params: URLSearchParams): GlobalFilters {
  const channels = parseMarketplaceParam(params.get("channels") ?? params.get("marketplace") ?? "all");

  const brandsRaw = params.get("brands");
  const brands = brandsRaw
    ? [...new Set(brandsRaw.split(",").map((b) => b.trim()).filter((b) => ALL_BRAND_KEYS.includes(b)))]
    : [];

  const dateFromRaw = params.get("date_from") ?? "";
  const dateToRaw = params.get("date_to") ?? "";
  const validation = validateDateRange(dateFromRaw, dateToRaw);

  const { dateFrom, dateTo } = validation.valid ? { dateFrom: dateFromRaw, dateTo: dateToRaw } : presetRange("30d");

  const compareRaw = params.get("compare");
  const compare = compareRaw === "true" || compareRaw === "1";

  return { channels, brands, dateFrom, dateTo, compare };
}

/** Nomes de query param reconhecidos como "intencao explicita de filtro".
 * Presenca de qualquer um deles significa que a URL nao esta "vazia" — o
 * default por tela (`defaultPreset`/`defaultCompare`) nao se aplica mais,
 * mesmo que o parametro individual esteja ausente (nesse caso cai no
 * fallback neutro do parser: canal "all", sem marca, compare=false). Isso e
 * o que garante que "querystring explicita sempre vence o default da tela"
 * e que compare=false sobrevive a um reload (ver useGlobalFilters). */
const FILTER_PARAM_NAMES = ["channels", "marketplace", "brands", "date_from", "date_to", "compare"] as const;

export function hasExplicitFilterParams(params: URLSearchParams): boolean {
  return FILTER_PARAM_NAMES.some((name) => params.has(name));
}

export interface DefaultFiltersOptions {
  /** Preset aplicado quando a URL chega sem nenhum parametro de filtro
   * (entrada direta na tela). Nao afeta navegacao que ja traz filtros. */
  defaultPreset?: DatePreset;
  /** Comparacao (MoM) ativa por padrao quando a URL chega vazia. */
  defaultCompare?: boolean;
  /** Relogio injetavel — usado nos testes para nao depender da data real. */
  now?: () => Date;
}

/** Resolve o default de uma tela (usado so quando a URL nao tem NENHUM
 * parametro de filtro reconhecido) — canal "all", sem marca, o intervalo do
 * `defaultPreset` e `defaultCompare`. */
export function computeDefaultFilters(options: DefaultFiltersOptions = {}): GlobalFilters {
  const { defaultPreset = "30d", defaultCompare = false, now = () => new Date() } = options;
  const range = presetRange(defaultPreset, now());
  return {
    channels: parseMarketplaceParam("all"),
    brands: [],
    dateFrom: range.dateFrom,
    dateTo: range.dateTo,
    compare: defaultCompare,
  };
}

/** Fonte unica de resolucao de filtros a partir da URL: explicita (qualquer
 * parametro de filtro presente) sempre vence; URL vazia usa o default da
 * tela. Funcao pura — o hook so agrega estado/efeitos em cima dela. */
export function resolveFilters(params: URLSearchParams, options: DefaultFiltersOptions = {}): GlobalFilters {
  return hasExplicitFilterParams(params) ? parseFiltersFromSearchParams(params) : computeDefaultFilters(options);
}

/** Serializa os filtros para query params, preservando quaisquer outros
 * parametros ja presentes em `base` (ex: paginacao especifica da tela). */
export function filtersToSearchParams(filters: GlobalFilters, base?: URLSearchParams): URLSearchParams {
  const params = new URLSearchParams(base);
  params.set("channels", serializeMarketplaceSelection(filters.channels));
  if (filters.brands.length > 0) {
    params.set("brands", [...filters.brands].sort().join(","));
  } else {
    params.delete("brands");
  }
  params.set("date_from", filters.dateFrom);
  params.set("date_to", filters.dateTo);
  if (filters.compare) {
    params.set("compare", "true");
  } else {
    params.delete("compare");
  }
  return params;
}
