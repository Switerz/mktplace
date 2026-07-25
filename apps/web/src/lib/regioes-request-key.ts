// Identidade da requisicao e escopo de fetch da pagina Regioes (Gate U4).
// Extraida do componente para ser testavel sem React/JSDOM.

export interface RegioesRequestKeyInput {
  channels: readonly string[];
  brands: readonly string[];
  dateFrom: string;
  dateTo: string;
  /** Filtro local de UF — nao faz parte do contrato de filtros globais/URL,
   * mas ainda assim precisa invalidar dados antigos ao mudar. */
  uf: string;
  retryKey: number;
}

/** Identidade estavel da requisicao atual — inclui a UF local, ao contrario
 * do contrato de filtros globais/URL (que nao a inclui). */
export function buildRegioesRequestKey(input: RegioesRequestKeyInput): string {
  return `${input.channels.join(",")}|${input.brands.join(",")}|${input.dateFrom}|${input.dateTo}|${input.uf}|${input.retryKey}`;
}

export interface RegioesBaseFetchOpts {
  // Mutavel (nao `readonly`) para casar exatamente com o tipo esperado pelas
  // funcoes de fetch (`RegioesFilterParams`/`GlobalFilterParams` em
  // api-client.ts) — `filters.brands` (GlobalFilters) ja e' `string[]`.
  brands: string[];
  dateFrom: string;
  dateTo: string;
}

export interface RegioesUfScopedOpts extends RegioesBaseFetchOpts {
  uf?: string[];
}

/**
 * Escopo de fetch para os 4 endpoints regionais — diferenca de contrato
 * documentada no Gate U4: `summary`/`by-uf` aceitam o filtro local de UF;
 * `by-brand`/`trend` nao aceitam (contrato de backend nao alterado neste
 * gate) e por isso usam sempre o escopo nacional dos filtros globais.
 */
export function buildRegioesFetchScopes(base: RegioesBaseFetchOpts, uf: string): {
  ufScoped: RegioesUfScopedOpts;
  national: RegioesBaseFetchOpts;
} {
  return {
    ufScoped: uf ? { ...base, uf: [uf] } : { ...base },
    national: { ...base },
  };
}

// FINDING 3 (rodada de correcao consolidada) — `summary == null` continua
// sendo erro total da requisicao. Mas se `summary` funcionar e apenas
// `byUf`/`byBrand`/`trend` vier `null` (endpoint individual falhou), o
// resultado e "fresh" porem PARCIAL: as secoes bem-sucedidas continuam
// exibidas, e um unico aviso compacto identifica quais ficaram
// indisponiveis — nunca confundido com um array vazio de sucesso real.
export type RegioesSectionKey = "byUf" | "byBrand" | "trend";

const REGIOES_SECTION_LABEL: Record<RegioesSectionKey, string> = {
  byUf: "Ranking por UF",
  byBrand: "Cobertura por Marca × Canal",
  trend: "Tendência",
};

export interface RegioesSectionResults {
  byUf: unknown[] | null;
  byBrand: unknown[] | null;
  trend: unknown[] | null;
}

/** Lista as secoes que vieram `null` (indisponiveis) — nunca inclui uma
 * secao que resolveu com sucesso para um array vazio (`[]`), que e' um
 * empty state real, nao uma indisponibilidade. */
export function describeRegioesPartialSections(input: RegioesSectionResults): RegioesSectionKey[] {
  const unavailable: RegioesSectionKey[] = [];
  if (input.byUf == null) unavailable.push("byUf");
  if (input.byBrand == null) unavailable.push("byBrand");
  if (input.trend == null) unavailable.push("trend");
  return unavailable;
}

/** Mensagem unica e compacta do aviso de dados parciais — `null` quando
 * nenhuma secao esta indisponivel (nada a avisar). */
export function formatRegioesPartialWarning(unavailable: RegioesSectionKey[]): string | null {
  if (unavailable.length === 0) return null;
  const labels = unavailable.map((k) => REGIOES_SECTION_LABEL[k]);
  return `Dados indisponíveis para: ${labels.join(", ")}. As demais seções continuam refletindo o período e os filtros selecionados.`;
}
