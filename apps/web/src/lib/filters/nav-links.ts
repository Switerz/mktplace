/**
 * Paginas que compartilham o contrato de filtros globais (canal, marca,
 * periodo) — usado para decidir quando propagar a querystring atual ao
 * navegar entre telas (AppNav, pills de marca, tabela de marcas, link de
 * volta ao Gerencial). Produtos/Tempo Real/Inteligencia/Operacoes tem
 * semantica propria e nunca herdam esses parametros.
 */
export const FILTER_AWARE_PAGES = new Set(["/", "/canais", "/financeiro", "/qualidade", "/pedidos", "/regioes"]);

/** `/brand/[brand]` tambem e uma rota compativel com o contrato de filtros
 * (marca fixa pela rota, canal/periodo globais) — tratado por prefixo
 * generico, nunca por uma marca especifica hardcoded. */
export function isFilterAwarePath(pathname: string): boolean {
  return FILTER_AWARE_PAGES.has(pathname) || pathname.startsWith("/brand/");
}

/** Anexa uma querystring a um href, sem deixar um "?" pendurado quando a
 * query esta vazia. */
export function appendQuery(href: string, query: string): string {
  return query ? `${href}?${query}` : href;
}

/** Chaves de filtro global preservadas ao navegar entre paginas filter-aware
 * (canal, marca, periodo, comparacao) — mesma lista que o AppNav usava
 * inline, extraida para ser compartilhada pela Sidebar e pelo MobileDrawer. */
export const FILTER_QUERY_KEYS = ["channels", "brands", "date_from", "date_to", "compare"];

/** Monta a querystring de filtros a preservar ao navegar a partir de
 * `pathname`, usando um leitor de parametro generico (`searchParams.get` do
 * Next, ou um dublê simples em teste) — nao depende de nenhum tipo do
 * Next.js para permanecer testavel com node:test. */
export function buildPreservedQuery(pathname: string, getParam: (key: string) => string | null): string {
  if (!isFilterAwarePath(pathname)) return "";
  const qs = new URLSearchParams();
  for (const key of FILTER_QUERY_KEYS) {
    const v = getParam(key);
    if (v) qs.set(key, v);
  }
  return qs.toString();
}

/** Resolve o href final de um item de navegacao, anexando a querystring
 * preservada apenas quando a pagina de destino participa do contrato de
 * filtros globais. */
export function hrefForPage(pageHref: string, preservedQuery: string): string {
  if (!FILTER_AWARE_PAGES.has(pageHref)) return pageHref;
  return appendQuery(pageHref, preservedQuery);
}

/**
 * Combina os filtros globais atualmente aplicados com o href de um destino
 * (gerado pelo backend, ex: `ExecutiveInsight.href`, ou fixo na propria
 * tela, ex: painel de canais/drill-downs). Regra de precedencia:
 * - qualquer parametro de filtro que o proprio destino ja traga
 *   explicitamente (`?brands=kokeshi`) sempre vence sobre o filtro global
 *   atualmente aplicado, mesmo que sejam a mesma chave;
 * - os demais filtros globais (`FILTER_QUERY_KEYS`) nao sobrescritos pelo
 *   destino sao preservados;
 * - nenhum parametro fora do contrato de filtros globais e herdado do
 *   estado atual — so os que o proprio destino ja trouxer.
 */
export function mergeFilteredHref(destinationHref: string, currentSearch: URLSearchParams): string {
  const [path, destQuery = ""] = destinationHref.split("?");
  const destParams = new URLSearchParams(destQuery);

  const merged = new URLSearchParams();
  for (const key of FILTER_QUERY_KEYS) {
    const current = currentSearch.get(key);
    if (current) merged.set(key, current);
  }
  for (const [key, value] of destParams) {
    merged.set(key, value);
  }

  return appendQuery(path, merged.toString());
}

/** Mesma logica de `mergeFilteredHref`, mas tolera destino ausente (ex:
 * `ExecutiveDataWarning.href` pode ser `null`) — nunca fabrica um link para
 * um aviso que nao tem destino real. */
export function mergeOptionalFilteredHref(
  destinationHref: string | null,
  currentSearch: URLSearchParams,
): string | null {
  return destinationHref == null ? null : mergeFilteredHref(destinationHref, currentSearch);
}
