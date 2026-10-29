import Chip from "@/components/ui/Chip";
import StateBlock from "@/components/ui/StateBlock";
import {
  estadoEfetivo,
  estadoParaStateBlock,
  formatarInstante,
  formatarValor,
  limiarExibivel,
  rotuloTipo,
  tituloEstado,
  type Indicador,
} from "@/lib/performance-contas";

/**
 * T-0014 — um indicador da tela "Performance das contas".
 *
 * Server component. O "?" usa `<details>/<summary>`: abre e fecha sem JS e o
 * leitor de tela anuncia o estado expandido nativamente.
 *
 * O valor so' aparece quando o estado efetivo e' `dado`. Nos demais estados o
 * numero NAO e' desenhado — nem "0", nem "—" no lugar do numero: o bloco de
 * estado diz o que aconteceu. O valor nunca recebe cor de severidade
 * (contrato §6.3).
 */
export default function IndicadorItem({ ind, canal }: { ind: Indicador; canal: string }) {
  const efetivo = estadoEfetivo(ind);
  const valor = efetivo === "dado" ? formatarValor(ind) : null;
  const limiar = limiarExibivel(ind);
  const atualizado = formatarInstante(ind.atualizado_em);
  const tipoBloco = efetivo === "invalido" ? "erro" : estadoParaStateBlock(efetivo);
  // Id unico por canal + codigo, so' com caracteres seguros para atributo.
  const idAjuda = `ajuda-${canal}-${ind.codigo}`.replace(/[^A-Za-z0-9_-]/g, "_");

  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-lg border border-line bg-surface p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h4 className="text-sm font-semibold text-ink">{ind.rotulo}</h4>
          <Chip tom="neutro">{rotuloTipo(ind.tipo)}</Chip>
        </div>
        <details className="group shrink-0">
          <summary
            aria-label={`O que é: ${ind.rotulo}`}
            aria-describedby={idAjuda}
            className="inline-flex min-h-11 min-w-11 cursor-pointer list-none items-center justify-center rounded-md border border-line text-sm font-semibold text-ink-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent [&::-webkit-details-marker]:hidden"
          >
            ?
          </summary>
          <p
            id={idAjuda}
            className="mt-1.5 max-w-sm rounded-lg border border-line bg-raised px-3 py-2.5 text-xs leading-relaxed text-ink-muted"
          >
            {ind.ajuda}
          </p>
        </details>
      </div>

      {valor !== null ? (
        <p className="text-2xl font-semibold tabular-nums text-ink">{valor}</p>
      ) : (
        tipoBloco && (
          <StateBlock
            tipo={tipoBloco}
            compacto
            titulo={tituloEstado(efetivo, ind.janela)}
            descricao={ind.motivo_indisponivel ?? undefined}
          />
        )
      )}

      <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs text-ink-muted sm:grid-cols-2">
        {ind.janela && (
          <div>
            <dt className="inline font-medium">Janela: </dt>
            <dd className="inline">{ind.janela}</dd>
          </div>
        )}
        {atualizado && (
          <div>
            <dt className="inline font-medium">Atualizado em: </dt>
            <dd className="inline">{atualizado}</dd>
          </div>
        )}
        {ind.cobertura && (
          <div>
            <dt className="inline font-medium">Cobertura: </dt>
            <dd className="inline">{ind.cobertura}</dd>
          </div>
        )}
        {limiar && (
          <div>
            <dt className="inline font-medium">Limiar: </dt>
            <dd className="inline">
              {limiar.texto} ({limiar.origem})
            </dd>
          </div>
        )}
        <div className="sm:col-span-2">
          <dt className="inline font-medium">Fonte: </dt>
          <dd className="inline">{ind.fonte}</dd>
        </div>
      </dl>

      {ind.aviso_frescor && (
        <p role="note" className="rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-xs text-warn-ink">
          {ind.aviso_frescor}
        </p>
      )}
    </li>
  );
}
