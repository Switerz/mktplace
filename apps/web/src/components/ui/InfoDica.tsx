import { Info } from "lucide-react";

/**
 * VISUAL-PALETA-1 — a ressalva sai do corpo do card e vira um "ⓘ".
 *
 * A Torre do Atacado mostra o numero e quase nada em volta; a explicacao
 * (metodologia, cobertura, "nao e' comprador unico") continua existindo, mas
 * so' para quem procura: no `title` (mouse) e em texto `sr-only` (leitor de
 * tela). Nunca apaga a ressalva — so' tira do caminho do olho.
 */
export default function InfoDica({ texto, className = "" }: { texto: string; className?: string }) {
  return (
    <span title={texto} className={`inline-flex shrink-0 cursor-help align-middle text-ink-faint hover:text-ink-muted ${className}`}>
      <Info aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={2} />
      <span className="sr-only">{texto}</span>
    </span>
  );
}
