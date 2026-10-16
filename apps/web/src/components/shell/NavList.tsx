import Link from "next/link";
import {
  Activity,
  BookMarked,
  Boxes,
  Calculator,
  CircleDot,
  LayoutDashboard,
  Lightbulb,
  MapPinned,
  Package,
  PackageSearch,
  Radar,
  ShieldCheck,
  ShoppingBag,
  Store,
  Truck,
  Users,
  Wallet,
  Warehouse,
  type LucideIcon,
} from "lucide-react";
import { expedicaoHabilitado } from "@/lib/expedicao-contract";
import { shopeeFbsEnabled } from "@/lib/shopee-fbs-flag";
import { estoqueFullEnabled } from "@/lib/estoque-full-flag";

import { isNavItemActive, navSections } from "./nav-config";

/**
 * UX-TORRE-B2B-1 — um icone por ROTA, e nao por item de menu: o mesmo icone
 * volta no cabecalho da pagina, e o operador aprende o vocabulario uma vez.
 *
 * Vive aqui e nao em `nav-config.ts` de proposito: a configuracao continua
 * sendo dado puro, testavel com node:test sem React. Rota sem icone mapeado
 * cai num marcador neutro em vez de quebrar o menu.
 */
export const ICONE_DA_ROTA: Record<string, LucideIcon> = {
  "/": LayoutDashboard,
  "/canais": Store,
  "/produtos": Package,
  "/qualidade": ShieldCheck,
  "/financeiro": Wallet,
  "/dre": Calculator,
  "/regioes": MapPinned,
  "/tempo-real": Activity,
  "/pedidos": ShoppingBag,
  "/inteligencia": Lightbulb,
  "/monitoramento-preco": Radar,
  "/operacoes": Users,
  "/full-ml": Warehouse,
  "/expedicao": Truck,
  "/full-shopee": Boxes,
  "/estoque-full": PackageSearch,
  "/referencias-externas/avoe": BookMarked,
};

interface NavListProps {
  pathname: string;
  hrefFor: (pageHref: string) => string;
  /**
   * Alvo de toque de 44px. Ligado no drawer mobile, onde o dedo e' o
   * apontador; desligado na sidebar do desktop, onde o mouse mira em 36px sem
   * dificuldade e dezesseis itens a 44px empurrariam metade da navegacao para
   * fora da primeira tela.
   *
   * Nos DOIS casos a altura e' EXPLICITA: `py-*` mais `line-height` chega a um
   * alvo por acidente aritmetico, e qualquer troca de fonte o derruba sem
   * nenhum sinal.
   */
  alvoAmplo?: boolean;
  /**
   * Trilho de icones (sidebar recolhida). O rotulo NAO some do DOM: vira
   * `sr-only`, entao o link continua com nome acessivel, e o `title` da a
   * dica ao mouse.
   */
  recolhido?: boolean;
}

/**
 * Renderizacao dos grupos/links de navegacao — compartilhada pela Sidebar
 * (desktop) e pelo MobileDrawer, para nao duplicar a marcacao de item ativo
 * e de itens desabilitados em dois lugares.
 *
 * UX-TORRE-B2B-1: pinta sobre a moldura navy do shell (tokens `shell-*`). O
 * item ativo e' marcado por TRES sinais juntos — superficie, tinta clara e
 * barra de destaque a esquerda — nunca so' por peso da fonte, que some na
 * visao periferica.
 */
export default function NavList({ pathname, hrefFor, alvoAmplo = false, recolhido = false }: NavListProps) {
  const alturaDoItem = alvoAmplo ? "min-h-11" : "min-h-9";
  return (
    <nav aria-label="Navegação principal" className={`flex flex-col ${recolhido ? "gap-2" : "gap-4"}`}>
      {/* Cada item responde SO' a propria flag: ligar uma nao revela a outra
          tela. Esconder o menu e' a SEGUNDA barreira -- cada rota ja' devolve
          404 por conta propria, porque a URL direta continuaria acessivel.

          Acesso LITERAL a `process.env.NEXT_PUBLIC_*`: o Next substitui por
          analise estatica do texto, e ler de uma variavel deixaria o valor
          `undefined` no browser. */}
      {navSections({
        expedicao: expedicaoHabilitado(process.env.NEXT_PUBLIC_EXPEDICAO_ENABLED),
        shopeeFbs: shopeeFbsEnabled(),
        estoqueFull: estoqueFullEnabled(),
      }).map((section, idx) => (
        <div key={section.label} className="flex flex-col gap-0.5">
          {recolhido ? (
            // No trilho o rotulo do grupo vira um divisor: a separacao entre
            // grupos continua visivel sem texto cortado.
            idx > 0 && <span aria-hidden="true" className="mx-auto mb-1.5 h-px w-6 bg-shell-line" />
          ) : (
            <span className="px-2.5 pb-1 text-xs font-semibold uppercase tracking-wider text-shell-faint">
              {section.label}
            </span>
          )}
          {section.pages.map((page) => {
            const Icone = ICONE_DA_ROTA[page.href] ?? CircleDot;
            if (page.disabled) {
              return (
                <span
                  key={page.href}
                  className={`flex cursor-default select-none items-center gap-2.5 rounded-md px-2.5 text-data text-shell-faint/70 ${alturaDoItem}`}
                  title={page.badge}
                >
                  <Icone aria-hidden="true" className="h-4 w-4 shrink-0" strokeWidth={1.75} />
                  <span className={recolhido ? "sr-only" : undefined}>{page.label}</span>
                  {page.badge && !recolhido && (
                    <span className="rounded border border-shell-line px-1 text-xs uppercase leading-4 tracking-wide">
                      {page.badge}
                    </span>
                  )}
                </span>
              );
            }
            const active = isNavItemActive(page.href, pathname);
            return (
              <Link
                key={page.href}
                href={hrefFor(page.href)}
                aria-current={active ? "page" : undefined}
                title={recolhido ? page.label : undefined}
                className={`group relative flex items-center rounded-md text-data font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-shell-accent ${alturaDoItem} ${
                  recolhido ? "justify-center px-0" : "gap-2.5 px-2.5"
                } ${
                  active
                    ? "bg-shell-active text-shell-ink"
                    : "text-shell-muted hover:bg-shell-raised hover:text-shell-ink"
                }`}
              >
                {active && (
                  <span
                    aria-hidden="true"
                    className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-shell-accent"
                  />
                )}
                <Icone
                  aria-hidden="true"
                  className={`h-4 w-4 shrink-0 ${active ? "text-shell-accent" : "text-shell-faint group-hover:text-shell-muted"}`}
                  strokeWidth={active ? 2.25 : 1.75}
                />
                <span className={recolhido ? "sr-only" : "truncate"}>{page.label}</span>
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
