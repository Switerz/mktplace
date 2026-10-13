"use client";

import { usePathname } from "next/navigation";
import type { RefObject } from "react";
import { Menu } from "lucide-react";
import ThemeToggle from "@/components/theme/ThemeToggle";
import { getRouteTitle } from "./nav-config";

interface TopbarProps {
  open: boolean;
  onToggleMenu: () => void;
  menuButtonRef: RefObject<HTMLButtonElement | null>;
}

/**
 * Cabecalho do shell NO MOBILE — identidade "Torre de Controle" + rota atual +
 * hamburger do drawer (Gate U1) + seletor de tema (UX-TORRE-1).
 *
 * UX-TORRE-B2B-1: some a partir de `md`. No desktop a sidebar navy ja' carrega
 * identidade, navegacao e tema, e o cabecalho de cada pagina carrega o titulo;
 * uma terceira faixa so' empurrava o primeiro numero para baixo. No mobile ela
 * continua sendo a unica moldura do shell, na mesma cor navy.
 *
 * O `<h1>` daqui so' existe na arvore de acessibilidade no mobile (o elemento
 * inteiro fica `display: none` acima de `md`); o AppShell declara o equivalente
 * para o desktop. Nunca ha dois ao mesmo tempo.
 *
 * O alvo de 44px do seletor e do hamburger e piso de acessibilidade e nao e
 * negociado por densidade.
 */
export default function Topbar({ open, onToggleMenu, menuButtonRef }: TopbarProps) {
  const pathname = usePathname();
  const routeTitle = getRouteTitle(pathname);

  return (
    <header className="border-b border-shell-line bg-shell md:hidden">
      <div className="flex items-center gap-2.5 px-2 py-1">
        <button
          ref={menuButtonRef}
          type="button"
          onClick={onToggleMenu}
          aria-label="Abrir menu de navegação"
          aria-expanded={open}
          aria-controls="mobile-drawer"
          className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-md text-shell-muted hover:bg-shell-raised hover:text-shell-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-shell-accent"
        >
          <Menu aria-hidden="true" className="h-5 w-5" strokeWidth={1.75} />
        </button>

        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-accent">
          <span className="text-xs font-bold tracking-tight text-accent-on">TC</span>
        </div>

        <div className="flex min-w-0 items-baseline gap-2">
          <h1 className="truncate text-sm font-semibold leading-none text-shell-ink">
            Torre de Controle
          </h1>
          <span aria-hidden="true" className="text-shell-faint">
            ·
          </span>
          <p className="truncate text-xs text-shell-muted">{routeTitle}</p>
        </div>

        <div className="ml-auto flex shrink-0 items-center">
          <ThemeToggle variante="shell" ciclico />
        </div>
      </div>
    </header>
  );
}
