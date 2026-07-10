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
