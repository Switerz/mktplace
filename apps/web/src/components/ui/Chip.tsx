import type { ReactNode } from "react";

import { TOM_PONTO, TOM_SELO, type Tom } from "./tone";

/**
 * UX-TORRE-1 — selo compacto de metadado (frescor, modo de carga, estado).
 *
 * Existe para tirar da primeira dobra os paragrafos que so' carregavam um
 * dado: "atualizada ha 21h" cabe num selo, nao numa frase.
 */
export default function Chip({
  children,
  tom = "neutro",
  ponto = false,
  title,
}: {
  children: ReactNode;
  tom?: Tom;
  /** Marcador de estado a esquerda. So' quando ha regra de negocio. */
  ponto?: boolean;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset ${TOM_SELO[tom]}`}
    >
      {ponto && (
        <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${TOM_PONTO[tom]}`} />
      )}
      {children}
    </span>
  );
}
