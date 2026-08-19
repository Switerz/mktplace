// Contrato da lente da fila de evidencias da Inteligencia (Gate V3-1A,
// desenho em docs/INTELIGENCIA_BRAND_V3_PLAN.md §9.2).
//
// `lens` substitui as tres tabelas ML separadas por UMA superficie com
// lentes. Regras duras do contrato:
// - allowlist fechada; parametro ausente, repetido, vazio ou fora do enum
//   resolve para `todos`, sem erro;
// - e' estado local REPRODUZIVEL da rota (a URL restaura a lente aberta),
//   e NAO e' contexto quente `ctx_*`: nao expira, nao depende de origem;
// - NAO entra em FILTER_QUERY_KEYS, e `/inteligencia` nao e' filter-aware —
//   os dois fatos juntos garantem que a sidebar nunca propaga a lente para
//   outra pagina;
// - nenhuma metrica viaja na URL: so' o identificador da lente.
//
// Modulo puro (sem React, sem tipos do Next) para ser testavel com node:test.

export const LENSES = ["parar", "escalar", "testar", "todos"] as const;
export type Lens = (typeof LENSES)[number];

/** Lente padrao — tambem o destino de qualquer entrada invalida. */
export const DEFAULT_LENS: Lens = "todos";

/** Chave da lente na querystring. Deliberadamente fora de FILTER_QUERY_KEYS. */
export const LENS_QUERY_KEY = "lens";

/** Rotulos humanos das lentes (a UI nunca exibe o slug cru). */
export const LENS_LABELS: Record<Lens, string> = {
  parar: "Parar",
  escalar: "Escalar",
  testar: "Testar",
  todos: "Todos",
};

/** Leitor generico de parametro — `URLSearchParams` do Next satisfaz, e um
 * dublê simples tambem, para o teste nao depender do Next. */
export interface ParamReader {
  get(key: string): string | null;
  getAll?(key: string): string[];
}

export function isLens(v: unknown): v is Lens {
  return typeof v === "string" && (LENSES as readonly string[]).includes(v);
}

/**
 * Le exatamente UM valor. Parametro repetido e' tratado como invalido —
 * mesma regra do contrato `ctx_*` (brand-arrival-context): repeticao e'
 * ambiguidade, e ambiguidade nunca vira escolha silenciosa.
 */
export function readSingleParam(params: ParamReader, key: string): string | null {
  if (typeof params.getAll === "function") {
    const all = params.getAll(key);
    if (all.length !== 1) return null;
    return all[0];
  }
  return params.get(key);
}

/** `parseLens` aceita o valor cru; qualquer coisa fora da allowlist cai em `todos`. */
export function parseLensValue(raw: string | null | undefined): Lens {
  if (raw == null) return DEFAULT_LENS;
  const trimmed = raw.trim();
  return isLens(trimmed) ? trimmed : DEFAULT_LENS;
}

/** `parseLens` a partir de um leitor de querystring (rejeita repetido). */
export function parseLens(params: ParamReader): Lens {
  return parseLensValue(readSingleParam(params, LENS_QUERY_KEY));
}

/**
 * Href da propria pagina com a lente aplicada.
 *
 * Preserva os parametros compativeis JA presentes (a marca local, por
 * exemplo) e remove qualquer `ctx_*` — voltar ou trocar de lente nunca
 * repropaga contexto quente. A lente `todos` e' omitida da URL: e' o padrao,
 * e uma URL limpa e' preferivel a uma URL redundante.
 */
export function buildLensHref(
  pathname: string,
  lens: Lens,
  params: ParamReader,
  options: { anchor?: string; preserveKeys?: readonly string[] } = {},
): string {
  const { anchor, preserveKeys = ["brands"] } = options;
  const qs = new URLSearchParams();
  for (const key of preserveKeys) {
    if (key === LENS_QUERY_KEY) continue;
    const v = readSingleParam(params, key);
    if (v) qs.set(key, v);
  }
  if (lens !== DEFAULT_LENS) qs.set(LENS_QUERY_KEY, lens);
  const q = qs.toString();
  const base = q ? `${pathname}?${q}` : pathname;
  return anchor ? `${base}#${anchor}` : base;
}

/** Ancoras internas dos blocos, usadas pelos destinos e pela navegacao interna. */
export const INTELIGENCIA_ANCHORS = {
  prioridades: "prioridades",
  oportunidades: "oportunidades",
  concentracao: "concentracao",
  produtos: "produtos-midia",
  fila: "fila-evidencias",
  ltv: "ltv",
} as const;
