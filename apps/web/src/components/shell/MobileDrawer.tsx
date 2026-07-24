"use client";

import { Suspense, useEffect, useRef, type RefObject } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { buildPreservedQuery, hrefForPage } from "@/lib/filters/nav-links";
import NavList from "./NavList";

interface MobileDrawerProps {
  open: boolean;
  onClose: () => void;
  openerRef: RefObject<HTMLButtonElement | null>;
}

/** Seletor dos elementos que participam do ciclo de foco dentro do drawer —
 * so os itens de navegacao reais e o botao de fechar (spans desabilitados
 * nunca tem href/tabindex, entao ja ficam fora por construcao). */
const FOCUSABLE_SELECTOR = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Drawer de navegacao mobile (Gate U1, com focus trap adicionado na correcao
 * do Finding 2). Fica sempre no DOM (visibilidade via classe `hidden`) para
 * que `aria-controls="mobile-drawer"` do botao no Topbar sempre aponte para
 * um elemento real, aberto ou fechado.
 *
 * Foco preso enquanto aberto: Tab no ultimo elemento focalizavel volta ao
 * primeiro; Shift+Tab no primeiro vai ao ultimo. O fundo (topbar + main) fica
 * `inert` nesse meio tempo (ver AppShell), entao Tab nunca alcancaria o
 * hamburger de qualquer forma — o ciclo aqui e a garantia explicita pedida
 * pelo finding, independente do suporte a `inert` do navegador.
 */
export default function MobileDrawer(props: MobileDrawerProps) {
  return (
    <Suspense fallback={null}>
      <MobileDrawerInner {...props} />
    </Suspense>
  );
}

function MobileDrawerInner({ open, onClose, openerRef }: MobileDrawerProps) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const mountedRef = useRef(false);

  // Move o foco para o botao de fechar ao abrir; devolve ao botao que abriu
  // ao fechar. Ignora a primeira renderizacao (open=false no mount) para nao
  // roubar o foco da pagina assim que ela carrega.
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      return;
    }
    if (open) {
      closeButtonRef.current?.focus();
    } else {
      openerRef.current?.focus();
    }
  }, [open, openerRef]);

  // Fecha com Escape e prende o Tab dentro do painel enquanto aberto.
  useEffect(() => {
    if (!open) return;

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab") return;

      const panel = panelRef.current;
      if (!panel) return;
      const focusables = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      if (focusables.length === 0) return;

      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;

      if (e.shiftKey) {
        if (active === first || !panel.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (active === last || !panel.contains(active)) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const preservedQuery = buildPreservedQuery(pathname, (key) => searchParams.get(key));
  const hrefFor = (pageHref: string) => hrefForPage(pageHref, preservedQuery);

  return (
    <div className={open ? "fixed inset-0 z-50 md:hidden" : "hidden"}>
      <div className="absolute inset-0 bg-slate-900/40" aria-hidden="true" onClick={onClose} />
      <div
        ref={panelRef}
        id="mobile-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Navegação principal"
        aria-hidden={!open}
        className="absolute inset-y-0 left-0 w-[min(80vw,300px)] bg-white border-r border-violet-100 shadow-lg flex flex-col overflow-y-auto"
      >
        <div className="flex items-center justify-between px-4 py-4 border-b border-violet-100">
          <span className="text-sm font-bold text-gray-900">Navegação</span>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            aria-label="Fechar menu de navegação"
            className="w-8 h-8 flex items-center justify-center rounded-lg text-slate-500 hover:bg-violet-50 hover:text-violet-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
          >
            <CloseIcon />
          </button>
        </div>
        <div className="px-4 py-4">
          <NavList pathname={pathname} hrefFor={hrefFor} />
        </div>
      </div>
    </div>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" className="w-4 h-4" aria-hidden="true">
      <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
    </svg>
  );
}
