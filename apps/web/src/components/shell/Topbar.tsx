"use client";

import { usePathname } from "next/navigation";
import type { RefObject } from "react";
import { getRouteTitle } from "./nav-config";

interface TopbarProps {
  open: boolean;
  onToggleMenu: () => void;
  menuButtonRef: RefObject<HTMLButtonElement | null>;
}

/**
 * Cabecalho compartilhado do shell — identidade "Torre de Controle" +
 * identificacao da rota atual + hamburger do drawer mobile (Gate U1).
 * Substitui o <header> que cada pagina montava por conta propria.
 */
export default function Topbar({ open, onToggleMenu, menuButtonRef }: TopbarProps) {
  const pathname = usePathname();
  const routeTitle = getRouteTitle(pathname);

  return (
    <header className="bg-white border-b border-violet-100 shadow-sm">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-4 flex items-center gap-3">
        <button
          ref={menuButtonRef}
          type="button"
          onClick={onToggleMenu}
          aria-label="Abrir menu de navegação"
          aria-expanded={open}
          aria-controls="mobile-drawer"
          className="md:hidden w-9 h-9 flex items-center justify-center rounded-lg text-slate-500 hover:bg-violet-50 hover:text-violet-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 shrink-0"
        >
          <MenuIcon />
        </button>

        <div className="w-9 h-9 rounded-xl bg-violet-600 flex items-center justify-center shrink-0">
          <span className="text-white font-bold text-xs tracking-tight">TC</span>
        </div>

        <div className="min-w-0">
          <h1 className="text-lg font-bold text-gray-900 leading-none truncate">Torre de Controle</h1>
          <p className="text-xs text-slate-400 truncate">GoBeauté · Marketplaces · {routeTitle}</p>
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
