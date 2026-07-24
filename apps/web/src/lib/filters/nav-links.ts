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
