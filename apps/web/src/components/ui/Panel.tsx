import type { ReactNode } from "react";

/**
 * UX-TORRE-1 — a unidade de superficie da Torre.
 *
 * Sem cartao dentro de cartao: um `Panel` contem dados, nunca outro `Panel`.
 * Aviso interno usa fundo tingido (`ok-soft`, `warn-soft`, `crit-soft`), nao
 * uma segunda moldura.
 */
export default function Panel({
  titulo,
  descricao,
  acao,
  rodape,
  children,
  className = "",
  semPadding = false,
  "aria-label": ariaLabel,
}: {
  titulo?: ReactNode;
  /** Uma linha, no maximo. Metodologia longa vai para um recolhivel. */
  descricao?: ReactNode;
  acao?: ReactNode;
  rodape?: ReactNode;
  children: ReactNode;
  className?: string;
  semPadding?: boolean;
  "aria-label"?: string;
}) {
  return (
    <section
      aria-label={ariaLabel}
      className={`flex min-w-0 flex-col overflow-hidden rounded-xl border border-line bg-surface ${className}`}
    >
      {(titulo || acao) && (
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-line px-4 py-2.5">
          <div className="min-w-0">
            {titulo && <h3 className="text-sm font-semibold text-ink">{titulo}</h3>}
            {descricao && (
              <p className="mt-0.5 text-xs leading-snug text-ink-muted">{descricao}</p>
            )}
          </div>
          {acao && <div className="flex flex-wrap items-center gap-2">{acao}</div>}
        </div>
      )}
      <div className={semPadding ? "min-w-0" : "min-w-0 p-4"}>{children}</div>
      {rodape && (
        <div className="border-t border-line px-4 py-2 text-xs text-ink-muted">{rodape}</div>
      )}
    </section>
  );
}
