import { Info } from "lucide-react";

/**
 * VISUAL-PALETA-1 — a ressalva sai do corpo do card e vira um "ⓘ".
 *
 * A Torre do Atacado mostra o numero e quase nada em volta; a explicacao
 * (metodologia, cobertura, "nao e' comprador unico") continua existindo, mas
 * so' para quem procura. Nunca apaga a ressalva — so' tira do caminho do olho.
 *
 * Dois modos:
 *  - padrao: o "ⓘ" e' FOCAVEL (Tab ou toque no celular) e mostra o balao no
 *    hover e no foco; o leitor de tela le' o texto pelo `aria-label`.
 *  - `emControle`: dentro de um botao (card de KPI), onde elemento focavel
 *    aninhado e' invalido. Fica so' o `title` decorativo, fora do nome
 *    acessivel do botao; a ressalva completa esta' no detalhe que o botao abre.
 */
export default function InfoDica({
  texto,
  emControle = false,
  className = "",
}: {
  texto: string;
  emControle?: boolean;
  className?: string;
}) {
  if (emControle) {
    return (
      <span aria-hidden="true" title={texto} className={`inline-flex shrink-0 cursor-help text-ink-faint ${className}`}>
        <Info className="h-3.5 w-3.5" strokeWidth={2} />
      </span>
    );
  }
  return (
    <span
      tabIndex={0}
      role="img"
      aria-label={texto}
      className={`group/dica relative inline-flex shrink-0 cursor-help rounded-full text-ink-faint outline-none hover:text-ink-muted focus-visible:ring-2 focus-visible:ring-accent ${className}`}
    >
      <Info aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={2} />
      <span
        aria-hidden="true"
        className="pointer-events-none invisible absolute left-1/2 top-full z-40 mt-2 w-64 -translate-x-1/2 rounded-lg border border-line bg-surface px-3 py-2 text-xs font-normal normal-case leading-snug tracking-normal text-ink-muted opacity-0 shadow-lg transition-opacity duration-150 group-hover/dica:visible group-hover/dica:opacity-100 group-focus/dica:visible group-focus/dica:opacity-100"
      >
        {texto}
      </span>
    </span>
  );
}
