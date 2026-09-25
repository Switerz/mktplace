"use client";

import { usePathname } from "next/navigation";
import type { RefObject } from "react";
import ThemeToggle from "@/components/theme/ThemeToggle";
import { getRouteTitle } from "./nav-config";

interface TopbarProps {
  open: boolean;
  onToggleMenu: () => void;
  menuButtonRef: RefObject<HTMLButtonElement | null>;
}

/**
 * Cabecalho compartilhado do shell — identidade "Torre de Controle" +
 * identificacao da rota atual + hamburger do drawer mobile (Gate U1) +
 * seletor de tema (UX-TORRE-1).
 *
 * Faixa COMPACTA: titulo e rota na MESMA linha, com o seletor de tema a
 * direita. Antes eram duas linhas de texto empilhadas num bloco de 69px de
 * altura. O alvo de 44px do seletor e do hamburger e piso de acessibilidade
 * e nao e negociado por densidade.
 */
export default function Topbar({ open, onToggleMenu, menuButtonRef }: TopbarProps) {
  const pathname = usePathname();
  const routeTitle = getRouteTitle(pathname);

  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex max-w-[1400px] items-center gap-2.5 px-3 py-1.5 sm:px-4">
        <button
          ref={menuButtonRef}
          type="button"
          onClick={onToggleMenu}
          aria-label="Abrir menu de navegação"
          aria-expanded={open}
          aria-controls="mobile-drawer"
          className="md:hidden inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-ink-muted hover:bg-accent-soft hover:text-accent"
        >
          <MenuIcon />
        </button>

        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent">
          <span className="text-xs font-bold tracking-tight text-accent-on">TC</span>
        </div>

        <div className="flex min-w-0 items-baseline gap-2">
          <h1 className="truncate text-sm font-bold leading-none text-ink">
            Torre de Controle
          </h1>
          <span aria-hidden="true" className="text-ink-faint">
            ·
          </span>
          <p className="truncate text-xs text-ink-muted">{routeTitle}</p>
        </div>

        <div className="ml-auto flex shrink-0 items-center">
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}

function MenuIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" className="w-5 h-5" aria-hidden="true">
      <path d="M3 5h14M3 10h14M3 15h14" strokeLinecap="round" />
    </svg>
  );
}
