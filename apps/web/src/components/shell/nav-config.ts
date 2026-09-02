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
    pages: [{ href: "/operacoes", label: "Criadores + Alertas" }],
  },
];

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
  for (const section of NAV_SECTIONS) {
    for (const page of section.pages) {
      if (page.disabled) continue;
      const matches = page.href === "/" ? pathname === "/" : pathname.startsWith(page.href);
      if (matches) return page.label;
    }
  }
  return "Torre de Controle";
}
