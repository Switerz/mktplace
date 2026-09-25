import type { ReactNode } from "react";

import type { Tom } from "./tone";

const CAIXA: Record<Tom, string> = {
  neutro: "border-line bg-raised text-ink-muted",
  bom: "border-ok/35 bg-ok-soft text-ok-ink",
  atencao: "border-warn/40 bg-warn-soft text-warn-ink",
  critico: "border-crit/40 bg-crit-soft text-crit-ink",
  ausente: "border-line border-dashed bg-surface text-ink-faint",
};

/**
 * UX-TORRE-1 — ressalva compacta.
 *
 * Fundo tingido com moldura INTEIRA, nunca uma tarja lateral colorida: a
 * tarja e' decoracao que finge ser semantica, e num alerta de verdade a cor
 * precisa estar no fundo para ser vista de longe.
 *
 * Uma linha por padrao. Metodologia longa pertence a um `Disclosure`.
 */
export default function Note({
  children,
  tom = "neutro",
  papel = "note",
  acao,
  className = "",
}: {
  children: ReactNode;
  tom?: Tom;
  /** `alert` interrompe o leitor de tela; use so' para falha real. */
  papel?: "note" | "status" | "alert";
  acao?: ReactNode;
  className?: string;
}) {
  return (
    <div
      role={papel}
      className={`flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 rounded-lg border px-3 py-2 text-xs leading-snug ${CAIXA[tom]} ${className}`}
    >
      <span className="min-w-0">{children}</span>
      {acao}
    </div>
  );
}
