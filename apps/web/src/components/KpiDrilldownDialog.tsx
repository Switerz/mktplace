"use client";

import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";

interface KpiDrilldownDialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}

/** Mesmo seletor/padrao de focus trap do MobileDrawer (Gate U1) — reaproveita
 * a convencao ja validada em vez de inventar uma nova. */
const FOCUSABLE_SELECTOR = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Dialogo generico de drill-down agregado, reutilizado pelos 4 KPIs da
 * Gerencial (Gate U2, Task 4/5) — nao existe um modal por KPI, so o
 * conteudo interno muda. Portalizado para `document.body` e nao para dentro
 * de `#app-shell-root`, para que o `inert` aplicado ao shell enquanto aberto
 * nunca alcance o proprio dialogo (ver comentario em AppShell.tsx).
 */
export default function KpiDrilldownDialog({ open, onClose, title, children }: KpiDrilldownDialogProps) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  // Foco inicial no botao de fechar; devolve ao elemento que abriu o
  // dialogo (o KpiCard clicado) ao fechar. Torna o shell inteiro inert e
  // bloqueia o scroll de fundo enquanto aberto.
  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();

    const root = document.getElementById("app-shell-root");
    root?.setAttribute("inert", "");
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      root?.removeAttribute("inert");
      document.body.style.overflow = originalOverflow;
      previousFocusRef.current?.focus();
    };
  }, [open]);

  // Escape fecha; Tab/Shift+Tab prendem o foco dentro do painel.
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

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div className="fixed inset-0 z-50">
      {/* Camada escurecida puramente visual — o container seguinte cobre
          toda a viewport por cima dela e e' quem realmente recebe o clique
          (Finding 1, rodada de correcao U2); por isso o fechamento fica
          nele, nao aqui. */}
      <div className="absolute inset-0 bg-slate-900/40" aria-hidden="true" />
      <div
        className="absolute inset-0 flex items-end sm:items-center justify-center sm:p-4"
        onClick={(e) => {
          // So fecha quando o clique atinge o proprio container (fora do
          // painel) — um clique dentro do painel tem `target` num
          // descendente e nunca fecha o dialogo.
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          className="relative bg-white w-full sm:max-w-lg sm:rounded-2xl shadow-xl h-[92vh] sm:h-auto sm:max-h-[85vh] flex flex-col overflow-hidden"
        >
          <div className="flex items-center justify-between gap-4 px-5 py-4 border-b border-violet-100 shrink-0">
            <h2 id={titleId} className="text-sm font-bold text-gray-900">{title}</h2>
            <button
              ref={closeButtonRef}
              type="button"
              onClick={onClose}
              aria-label="Fechar detalhes"
              className="w-8 h-8 flex items-center justify-center rounded-lg text-slate-500 hover:bg-violet-50 hover:text-violet-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 shrink-0"
            >
              <CloseIcon />
            </button>
          </div>
          <div className="px-5 py-4 flex flex-col gap-4 overflow-y-auto">{children}</div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" className="w-4 h-4" aria-hidden="true">
      <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
    </svg>
  );
}
