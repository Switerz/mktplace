import type { Marketplace } from "./mock-data";

/** Ordem canônica exibida em toda a UI e usada na serialização para a API. */
export const ALL_MARKETPLACES: readonly Marketplace[] = ["tiktok", "ml", "shopee"];

export type MarketplaceSelection = Marketplace[];

/** Os tres canais — o botao "Todos" e o `channels=all` da URL. */
export const ALL_MARKETPLACE_SELECTION: MarketplaceSelection = [...ALL_MARKETPLACES];

/**
 * Consolidado: Mercado Livre + Shopee, sem TikTok Shop. E' o escopo padrao
 * das telas — o TikTok continua a um clique ("Todos" ou o proprio botao).
 */
export const CONSOLIDADO_SELECTION: MarketplaceSelection = ["ml", "shopee"];

/** Selecao aplicada quando a URL nao diz o canal (entrada direta na tela). */
export const DEFAULT_MARKETPLACE_SELECTION: MarketplaceSelection = [...CONSOLIDADO_SELECTION];

/** Remove duplicados e ordena na ordem canônica TikTok, ML, Shopee. */
export function canonicalizeSelection(selection: readonly Marketplace[]): MarketplaceSelection {
  const set = new Set(selection);
  return ALL_MARKETPLACES.filter((mp) => set.has(mp));
}

export function isMarketplaceSelected(selection: readonly Marketplace[], mp: Marketplace): boolean {
  return selection.includes(mp);
}

export function isAllSelected(selection: readonly Marketplace[]): boolean {
  return ALL_MARKETPLACES.every((mp) => selection.includes(mp));
}

/** Exatamente ML + Shopee (a ordem nao importa). */
export function isConsolidadoSelected(selection: readonly Marketplace[]): boolean {
  const canonical = canonicalizeSelection(selection);
  return (
    canonical.length === CONSOLIDADO_SELECTION.length &&
    CONSOLIDADO_SELECTION.every((mp) => canonical.includes(mp))
  );
}

/**
 * Alterna um canal na seleção. Nunca retorna seleção vazia — se o canal
 * alternado for o único selecionado, a seleção é preservada.
 */
export function toggleMarketplace(
  selection: readonly Marketplace[],
  mp: Marketplace,
): MarketplaceSelection {
  const has = selection.includes(mp);
  if (has && selection.length === 1) return canonicalizeSelection(selection);
  const next = has ? selection.filter((m) => m !== mp) : [...selection, mp];
  return canonicalizeSelection(next);
}

/**
 * Serializa a seleção para o formato aceito pela API: "all" quando os três
 * canais estão ativos (compatibilidade com chamadas antigas), o nome do
 * canal isolado ("tiktok"|"ml"|"shopee"), ou lista canônica separada por
 * vírgula ("tiktok,ml") para combinações parciais.
 */
export function serializeMarketplaceSelection(selection: readonly Marketplace[]): string {
  const canonical = canonicalizeSelection(selection);
  if (canonical.length === 0 || canonical.length === ALL_MARKETPLACES.length) return "all";
  return canonical.join(",");
}

/**
 * Compat: aceita o parâmetro escalar legado ("all"|"tiktok"|"ml"|"shopee") e devolve a seleção equivalente.
 * "all" explícito (e valor inválido) continua sendo os três canais — links antigos e o botão "Todos".
 * Parâmetro AUSENTE é decidido por quem chama: as telas de filtro global caem no Consolidado
 * (ver `parseFiltersFromSearchParams`).
 */
export function parseMarketplaceParam(value: string): MarketplaceSelection {
  if (value === "all" || value === "") return [...ALL_MARKETPLACE_SELECTION];
  const parts = value.split(",").map((p) => p.trim()).filter(Boolean) as Marketplace[];
  const valid = parts.filter((p) => (ALL_MARKETPLACES as string[]).includes(p));
  return canonicalizeSelection(valid.length ? valid : ALL_MARKETPLACE_SELECTION);
}
