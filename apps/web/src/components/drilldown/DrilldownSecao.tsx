import type { ReactNode } from "react";

/**
 * POPUPS-1 — secao de um detalhe: rotulo curto em caixa alta e o conteudo.
 *
 * Substitui os subtitulos soltos ("Por canal", "Decomposição") por um mesmo
 * cabecalho em todos os popups. `dica` vira um "ⓘ" ao lado do rotulo — e' ali
 * que mora a ressalva que muda a leitura, nunca um paragrafo.
 */
export default function DrilldownSecao({
  titulo,
  acao,
  children,
}: {
  titulo: string;
  /** Algo curto a direita do rotulo (contagem, selo, ⓘ). */
  acao?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-1.5 border-t border-line pt-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-faint">{titulo}</h3>
        {acao && <div className="flex items-center gap-1.5 text-xs text-ink-faint">{acao}</div>}
      </div>
      {children}
    </section>
  );
}
