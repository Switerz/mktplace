import Chip from "@/components/ui/Chip";
import StateBlock from "@/components/ui/StateBlock";
import type { VisaoIndicador } from "@/lib/performance-contas";

/**
 * T-0014 — um indicador da tela "Performance das contas".
 *
 * Server component. O "?" usa `<details>/<summary>`: abre e fecha sem JS e o
 * leitor de tela anuncia o estado expandido nativamente. Enter/Espaco e foco
 * vem do proprio <summary>; o texto abre abaixo do cabecalho, nao ao lado.
 *
 * Recebe a visao ja' montada por `montarIndicador` (testada em node --test):
 * aqui so' se desenha. O valor so' aparece quando o estado efetivo e' `dado`.
 * Nos demais estados o numero NAO e' desenhado — nem "0", nem "—" no lugar do
 * numero: o bloco de estado diz o que aconteceu. O valor nunca recebe cor de
 * severidade (contrato §6.3).
 */
export default function IndicadorItem({ v }: { v: VisaoIndicador }) {
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-lg border border-line bg-surface p-3">
      {/* O "?" fica fixo no canto direito do cabecalho, aberto ou fechado
          (reteste T-0014): o <summary> e' posicionado em absoluto e o rotulo
          reserva a largura dele (pr). O <details> e' um bloco de largura total
          ABAIXO do rotulo: fechado tem altura zero; aberto, o texto de ajuda
          ocupa a largura do card sem espremer o titulo. A tabela de origem
          mora so' ali, fora do caminho do numero. */}
      <div className="relative">
        <div className="flex min-h-11 min-w-0 flex-wrap items-center gap-2 pr-[3.25rem]">
          <h4 className="min-w-0 break-words text-sm font-semibold text-ink">{v.rotulo}</h4>
          <Chip tom="neutro">{v.tipo}</Chip>
        </div>
        <details>
          <summary
            aria-label={`O que é: ${v.rotulo}`}
            aria-describedby={v.idAjuda}
            className="absolute right-0 top-0 inline-flex min-h-11 min-w-11 cursor-pointer list-none items-center justify-center rounded-md border border-line text-sm font-semibold text-ink-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent [&::-webkit-details-marker]:hidden"
          >
            ?
          </summary>
          <p
            id={v.idAjuda}
            className="mt-2 break-words rounded-lg border border-line bg-raised px-3 py-2.5 text-xs leading-relaxed text-ink-muted"
          >
            {v.ajuda}
            <span className="mt-1 block break-all">Fonte: {v.fonte}</span>
          </p>
        </details>
      </div>

      {v.valor !== null ? (
        <p className="text-2xl font-semibold tabular-nums text-ink">{v.valor}</p>
      ) : (
        v.bloco && (
          <StateBlock
            tipo={v.bloco.tipo}
            compacto
            titulo={v.bloco.titulo}
            descricao={v.bloco.descricao ?? undefined}
          />
        )
      )}

      <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs text-ink-muted sm:grid-cols-2">
        {v.janela && (
          <div>
            <dt className="inline font-medium">Janela: </dt>
            <dd className="inline">{v.janela}</dd>
          </div>
        )}
        {v.atualizado && (
          <div>
            <dt className="inline font-medium">Atualizado em: </dt>
            <dd className="inline">{v.atualizado}</dd>
          </div>
        )}
        {v.cobertura && (
          <div>
            <dt className="inline font-medium">Cobertura: </dt>
            <dd className="inline">{v.cobertura}</dd>
          </div>
        )}
        {/* Rotulo do limiar e texto da origem em linhas separadas: nao
            misturar "limite interno" com "referência interna" na mesma frase. */}
        {v.limiar && (
          <div>
            <dt className="inline font-medium">Limiar: </dt>
            <dd className="inline">{v.limiar.texto}</dd>
          </div>
        )}
        {v.limiar && (
          <div>
            <dt className="inline font-medium">Origem do limiar: </dt>
            <dd className="inline">{v.limiar.origem}</dd>
          </div>
        )}
      </dl>

      {v.avisoFrescor && (
        <p role="note" className="rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-xs text-warn-ink">
          {v.avisoFrescor}
        </p>
      )}
    </li>
  );
}
