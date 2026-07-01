import type { Marketplace } from "./mock-data";

/** Ordem canônica exibida em toda a UI e usada na serialização para a API. */
export const ALL_MARKETPLACES: readonly Marketplace[] = ["tiktok", "ml", "shopee"];

export type MarketplaceSelection = Marketplace[];

export const DEFAULT_MARKETPLACE_SELECTION: MarketplaceSelection = [...ALL_MARKETPLACES];

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

/** Compat: aceita o parâmetro escalar legado ("all"|"tiktok"|"ml"|"shopee") e devolve a seleção equivalente. */
export function parseMarketplaceParam(value: string): MarketplaceSelection {
  if (value === "all" || value === "") return [...DEFAULT_MARKETPLACE_SELECTION];
  const parts = value.split(",").map((p) => p.trim()).filter(Boolean) as Marketplace[];
  const valid = parts.filter((p) => (ALL_MARKETPLACES as string[]).includes(p));
  return canonicalizeSelection(valid.length ? valid : DEFAULT_MARKETPLACE_SELECTION);
}
