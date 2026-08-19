// Marcas ML da Inteligencia, derivadas do PAYLOAD (Gate V3-1A).
//
// Defeito que isto corrige (V3-0, finding B4): a pagina tinha
// `ML_BRANDS = ["barbours","kokeshi","lescent"]` hardcoded, sem `rituaria`.
// A API tem 4 marcas ML (rituaria incluida em 01/07/2026) e a fonte tem 4 —
// resultado: o filtro nao oferecia rituaria e a secao Pareto iterava 3
// marcas, deixando o dado real de uma marca INVISIVEL.
//
// A lista passa a ser a uniao das marcas presentes nos blocos ML do payload.
// `tk_products` fica de fora de proposito: e' TikTok (inclui apice) e tem
// regime temporal proprio.
//
// Modulo puro, sem React.

import type { InteligenciaData, LtvRow, ParetoRow, ProductSignalRow } from "../api-client.ts";

/** Valor da selecao "todas as marcas". Nunca vai para a URL como marca. */
export const BRAND_ALL = "all";

export type BrandSelection = string;

/** Rotulos de exibicao. Marca que aparecer no payload sem rotulo conhecido
 * cai no proprio slug em maiuscula — nunca some da lista por falta de label. */
export const BRAND_LABELS: Record<string, string> = {
  apice: "ÁPICE",
  barbours: "BARBOURS",
  kokeshi: "KOKESHI",
  lescent: "LESCENT",
  rituaria: "RITUÁRIA",
};

export function brandLabel(slug: string): string {
  return BRAND_LABELS[slug] ?? slug.toUpperCase();
}

function collect(target: Set<string>, rows: readonly { brand?: string | null }[] | undefined) {
  for (const r of rows ?? []) {
    const b = r?.brand;
    if (typeof b === "string" && b.trim() !== "") target.add(b);
  }
}

/**
 * Marcas ML presentes no payload, ordenadas. Uniao dos cinco blocos ML —
 * `ltv` e `pareto` cobrem o portfolio inteiro, as tres listas cobrem so' o
 * que caiu na amostra, e a uniao e' o conjunto seguro.
 */
export function mlBrandsFromPayload(data: InteligenciaData | null | undefined): string[] {
  const out = new Set<string>();
  if (!data) return [];
  collect(out, data.urgent as readonly ProductSignalRow[] | undefined);
  collect(out, data.scale as readonly ProductSignalRow[] | undefined);
  collect(out, data.organic as readonly ProductSignalRow[] | undefined);
  collect(out, data.pareto as readonly ParetoRow[] | undefined);
  collect(out, data.ltv as readonly LtvRow[] | undefined);
  return [...out].sort();
}

/**
 * Resolve a selecao local de marca.
 *
 * Ausente, repetida, vazia ou fora das marcas realmente disponiveis cai com
 * seguranca em `all`, sem erro — mesma disciplina do contrato `ctx_*`.
 */
export function parseBrandSelection(
  raw: string | null | undefined,
  available: readonly string[],
): BrandSelection {
  if (raw == null) return BRAND_ALL;
  const v = raw.trim();
  if (v === "" || v === BRAND_ALL) return BRAND_ALL;
  return available.includes(v) ? v : BRAND_ALL;
}

/** Filtra qualquer coleccao com coluna `brand` pela selecao local. */
export function filterByBrand<T extends { brand: string }>(
  rows: readonly T[] | undefined,
  selection: BrandSelection,
): T[] {
  const list = rows ?? [];
  if (selection === BRAND_ALL) return [...list];
  return list.filter((r) => r.brand === selection);
}

/** Rotulo do escopo de marca, para cabecalhos e dialogos. */
export function brandScopeLabel(selection: BrandSelection): string {
  return selection === BRAND_ALL ? "Todas as marcas ML" : brandLabel(selection);
}
