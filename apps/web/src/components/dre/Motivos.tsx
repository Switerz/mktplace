"use client";

import { useId, useState } from "react";

/**
 * DRE-UX-2 — lista de motivos que se expande.
 *
 * Substitui o "e mais N motivo(s)" que nao abria nada: os motivos escondidos
 * ficavam inalcancaveis (medido: 5 no total de 07/2026). Botao de verdade, com
 * `aria-expanded` amarrado a lista por `aria-controls` — funciona por clique,
 * toque e teclado. Nada aqui depende de `title`.
 */
export default function Motivos({
  motivos,
  visiveis = 2,
  abertoInicial = false,
  className = "",
  unidade = ["motivo", "motivos"],
}: {
  motivos: string[];
  /** Quantos aparecem antes de expandir. */
  visiveis?: number;
  abertoInicial?: boolean;
  className?: string;
  /** Singular e plural do item no botao ("Ver mais 3 avisos"). */
  unidade?: [string, string];
}) {
  const [aberto, setAberto] = useState(abertoInicial);
  const id = useId();
  if (!motivos.length) return null;
  const resto = motivos.length - visiveis;
  const mostrados = aberto || resto <= 0 ? motivos : motivos.slice(0, visiveis);
  return (
    <div className={className}>
      <ul id={id} className="flex flex-col gap-0.5 text-xs font-normal leading-snug text-ink-muted">
        {mostrados.map((m) => (
          <li key={m}>{m}</li>
        ))}
      </ul>
      {resto > 0 && (
        <button
          type="button"
          aria-expanded={aberto}
          aria-controls={id}
          onClick={() => setAberto((v) => !v)}
          className="inline-flex min-h-11 items-center rounded-md text-xs font-semibold text-accent underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {aberto ? "Mostrar menos" : `Ver mais ${resto} ${resto === 1 ? unidade[0] : unidade[1]}`}
        </button>
      )}
    </div>
  );
}
