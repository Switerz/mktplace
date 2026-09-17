/**
 * Estrutura de navegacao compartilhada pela Sidebar (desktop) e pelo
 * MobileDrawer — substitui o array SECTIONS que antes vivia inline no
 * AppNav. Mesmos 4 grupos, mesmas rotas, sem adicionar nem remover nada
 * (Gate U1 e apenas fundacao visual, nao redesenho de informacao).
 */

export interface NavPage {
  href: string;
  label: string;
  badge?: string;
  disabled?: boolean;
}

export interface NavSection {
  label: string;
  pages: NavPage[];
}

export const NAV_SECTIONS: NavSection[] = [
  {
    label: "Cockpits",
    pages: [
      { href: "/", label: "Gerencial" },
      { href: "/canais", label: "Canais" },
      { href: "/produtos", label: "Produtos" },
      { href: "/qualidade", label: "Qualidade" },
      { href: "/financeiro", label: "Financeiro" },
      { href: "/regioes", label: "Regiões" },
      { href: "/tempo-real", label: "Tempo Real" },
    ],
  },
  {
    label: "Pedidos",
    pages: [
      { href: "/pedidos", label: "Geral" },
      { href: "/pedidos/tiktok", label: "TikTok Shop", badge: "Em breve", disabled: true },
      { href: "/pedidos/ml", label: "Mercado Livre", badge: "Em breve", disabled: true },
    ],
  },
  {
    label: "Inteligência",
    pages: [
      { href: "/inteligencia", label: "Ações ML + TikTok" },
      // Gate PMA-3. Observacional: compara o preco anunciado das lojas
      // proprias no ML com o preco sugerido de revenda das tabelas B2B.
      { href: "/monitoramento-preco", label: "Monitoramento de preços" },
    ],
  },
  {
    label: "Operações",
    pages: [
      { href: "/operacoes", label: "Criadores + Alertas" },
      // Gate FULL-1D. Rota PROPRIA, e nao /operacoes/full-ml, porque
      // `isNavItemActive` casa por prefixo: um filho de /operacoes deixaria os
      // dois itens ativos ao mesmo tempo.
      //
      // Mede MODALIDADE LOGISTICA do envio, nao posicao de estoque nem
      // expedicao -- por isso nao entra no grupo de Pedidos nem se mistura com
      // a frente de Expedicao.
      { href: "/full-ml", label: "Full Mercado Livre" },
    ],
  },
  // Gate AVH-4B-S — grupo PROPRIO, e nao um item dentro de Cockpits ou
  // Inteligência, de proposito: o que vive aqui e' dado de terceiro, com carga
  // manual, que nao e' KPI da Torre. A separacao na navegacao e' a primeira
  // barreira contra alguem ler estes numeros como oficiais.
  {
    label: "Referências externas",
    pages: [{ href: "/referencias-externas/avoe", label: "Avoe Hub" }],
  },
];

/**
 * Gate EXP-2B — item da Expedicao, atras de `NEXT_PUBLIC_EXPEDICAO_ENABLED`.
 *
 * Entra em "Operações" e NAO em "Pedidos": mede o que ainda nao saiu, e nao o
 * pedido em si. Nao vira filho de `/operacoes` porque `isNavItemActive` casa
 * por prefixo — dois itens ficariam ativos ao mesmo tempo, o mesmo motivo que
 * levou o Full ML para rota propria.
 */
export const EXPEDICAO_NAV: NavPage = { href: "/expedicao", label: "Expedição Shopee" };

/**
 * FAIL-CLOSED: so' a string exata "true" liga. Esconder o item e' a SEGUNDA
 * barreira — a primeira e' a propria rota, que devolve 404 com a flag
 * desligada. Esconder so' o menu deixaria a URL direta acessivel, e a API nao
 * tem autenticacao.
 */
export function navSections(expedicaoEnabled: boolean): NavSection[] {
  if (!expedicaoEnabled) return NAV_SECTIONS;
  return NAV_SECTIONS.map((s) =>
    s.label === "Operações" ? { ...s, pages: [...s.pages, EXPEDICAO_NAV] } : s,
  );
}

/**
 * Mesma regra que o AppNav ja usava inline: a Gerencial tambem fica ativa em
 * qualquer /brand/[brand], porque o drill-down de marca parte dela — e' a
 * "associacao visual" que o Gate U1 pede para preservar.
 */
export function isNavItemActive(pageHref: string, pathname: string): boolean {
  if (pageHref === "/") return pathname === "/" || pathname.startsWith("/brand");
  return pathname.startsWith(pageHref);
}

/**
 * Titulo de rota exibido na topbar do shell (identificacao da rota atual).
 * Itens desabilitados nunca sao considerados (nao sao rotas navegaveis).
 * Cai em "Torre de Controle" para qualquer pathname fora do mapa atual.
 */
export function getRouteTitle(pathname: string): string {
  if (pathname.startsWith("/brand/")) return "Gerencial";
  // `navSections(true)` e nao `NAV_SECTIONS`: quem esta NA rota da Expedicao ja
  // passou pelo guard, entao a flag e necessariamente `true`. Resolver o titulo
  // pela lista sem o item deixaria a topbar dizendo "Torre de Controle" numa
  // pagina que tem nome proprio.
  for (const section of navSections(true)) {
    for (const page of section.pages) {
      if (page.disabled) continue;
      const matches = page.href === "/" ? pathname === "/" : pathname.startsWith(page.href);
      if (matches) return page.label;
    }
  }
  return "Torre de Controle";
}
