"use client";

import { useId, useState, type ReactNode } from "react";

/**
 * UX-TORRE-1 — "Como ler esta medição".
 *
 * O destino dos paragrafos de metodologia que ocupavam a primeira dobra. O
 * texto NAO e' apagado nem reescrito: ele passa a abrir sob demanda, com
 * `aria-expanded` e `aria-controls` amarrados — um `aria-expanded` sem alvo
 * declarado e' uma referencia ARIA quebrada.
 */
export default function Disclosure({
  rotulo = "Como ler esta medição",
  children,
  className = "",
}: {
  rotulo?: string;
  children: ReactNode;
  className?: string;
}) {
  const [aberto, setAberto] = useState(false);
  const id = useId();
  return (
    <div className={className}>
      <button
        type="button"
        aria-expanded={aberto}
        aria-controls={id}
        onClick={() => setAberto((v) => !v)}
        className="inline-flex min-h-11 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-ink-muted underline decoration-line-strong underline-offset-4 hover:text-ink"
      >
        <svg
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          aria-hidden="true"
          className={`h-3.5 w-3.5 transition-transform ${aberto ? "rotate-90" : ""}`}
        >
          <path d="M6 3.5 10.5 8 6 12.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {rotulo}
      </button>
      {aberto && (
        <div
          id={id}
          className="mt-1.5 rounded-lg border border-line bg-raised px-3 py-2.5 text-xs leading-relaxed text-ink-muted"
        >
          {children}
        </div>
      )}
    </div>
  );
}
