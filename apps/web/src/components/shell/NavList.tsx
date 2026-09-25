import Link from "next/link";
import { expedicaoHabilitado } from "@/lib/expedicao-contract";
import { shopeeFbsEnabled } from "@/lib/shopee-fbs-flag";
import { estoqueFullEnabled } from "@/lib/estoque-full-flag";

import { isNavItemActive, navSections } from "./nav-config";

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
}

/**
 * Renderizacao dos grupos/links de navegacao — compartilhada pela Sidebar
 * (desktop) e pelo MobileDrawer, para nao duplicar a marcacao de item ativo
 * e de itens desabilitados em dois lugares.
 *
 * UX-TORRE-1: tokens semanticos e piso tipografico de 12px. O item ativo e'
 * marcado por superficie + tinta de destaque, nunca so' por peso da fonte —
 * peso sozinho some na visao periferica.
 */
export default function NavList({ pathname, hrefFor, alvoAmplo = false }: NavListProps) {
  const alturaDoItem = alvoAmplo ? "min-h-11" : "min-h-9";
  return (
    <nav aria-label="Navegação principal" className="flex flex-col gap-4">
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
      }).map((section) => (
        <div key={section.label} className="flex flex-col gap-0.5">
          <span className="px-2 pb-1 text-xs font-semibold uppercase tracking-wider text-ink-faint">
            {section.label}
          </span>
          {section.pages.map((page) => {
            if (page.disabled) {
              return (
                <span
                  key={page.href}
                  className={`flex cursor-default select-none items-center gap-1.5 rounded-lg px-2 text-sm text-ink-faint/60 ${alturaDoItem}`}
                  title={page.badge}
                >
                  {page.label}
                  {page.badge && (
                    <span className="rounded border border-line px-1 text-xs uppercase leading-4 tracking-wide text-ink-faint/60">
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
                className={`flex items-center rounded-lg px-2 text-sm font-medium transition-colors ${alturaDoItem} ${
                  active
                    ? "bg-accent-soft font-semibold text-accent-ink"
                    : "text-ink-muted hover:bg-accent-soft/60 hover:text-accent-ink"
                }`}
              >
                {page.label}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
