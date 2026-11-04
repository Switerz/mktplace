import Panel from "@/components/ui/Panel";
import StateBlock from "@/components/ui/StateBlock";
import type { VisaoCanalAtendimento, VisaoMotivos } from "@/lib/performance-contas-atendimento";

import IndicadorItem from "../performance-contas/IndicadorItem";

/**
 * CX-MKT-1 — um canal da aba "Atendimento".
 *
 * Server component: so' desenha a visao ja' montada por
 * `montarCanaisAtendimento` (testada em node --test). Ordem de leitura:
 * indicadores, motivos ao lado da tabela por marca, e no fim o que ainda nao
 * e' publicado (com o motivo), para a ausencia nunca parecer zero.
 */

/** Classe LITERAL por canal: o Tailwind so' gera o que encontra escrito. */
const COR_BARRA: Record<string, string> = {
  mercado_livre: "bg-canal-ml",
  shopee: "bg-canal-shopee",
  tiktok: "bg-canal-tiktok",
};

export default function AtendimentoCanal({ canal }: { canal: VisaoCanalAtendimento }) {
  return (
    <Panel titulo={canal.titulo} descricao={canal.descricao} aria-label={`Atendimento: ${canal.titulo}`}>
      <div className="flex flex-col gap-4">
        {canal.fora.length > 0 && (
          <div className="rounded-lg border border-dashed border-line px-3 py-2 text-xs text-ink-muted">
            <p className="font-medium">Fora da cobertura</p>
            <ul className="mt-1 list-disc pl-4">
              {canal.fora.map((f) => (
                <li key={f.chave}>{f.texto}</li>
              ))}
            </ul>
          </div>
        )}

        {canal.indicadores.length > 0 && (
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {canal.indicadores.map((v) => (
              <IndicadorItem key={v.chave} v={v} />
            ))}
          </ul>
        )}

        {(canal.motivos || canal.linhas.length > 0) && (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {canal.motivos && <Motivos m={canal.motivos} cor={COR_BARRA[canal.canal] ?? "bg-accent"} />}
            {canal.linhas.length > 0 && <TabelaMarcas canal={canal} />}
          </div>
        )}

        {canal.pendencias.length > 0 && (
          <section aria-label="Ainda não publicado" className="rounded-lg border border-dashed border-line px-3 py-2.5">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Ainda não publicado</h4>
            <ul className="mt-2 flex flex-col gap-1.5 text-xs text-ink-muted">
              {canal.pendencias.map((p) => (
                <li key={p.codigo}>
                  <span className="font-semibold text-ink">{p.titulo}.</span> {p.motivo}
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </Panel>
  );
}

function Motivos({ m, cor }: { m: VisaoMotivos; cor: string }) {
  return (
    <section aria-label={m.titulo} className="flex min-w-0 flex-col gap-3 rounded-lg border border-line bg-surface p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-sm font-semibold text-ink">{m.titulo}</h4>
        {m.total && <span className="text-xs tabular-nums text-ink-muted">{m.total} no total</span>}
      </div>
      <p className="-mt-2 text-xs text-ink-faint">{m.ajuda}</p>

      {m.bloco ? (
        <StateBlock tipo={m.bloco.tipo} compacto titulo={m.bloco.titulo} descricao={m.bloco.descricao ?? undefined} />
      ) : (
        <ol className="flex flex-col gap-2.5">
          {m.itens.map((i) => (
            <li key={i.chave} className="flex flex-col gap-1">
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="min-w-0 break-words text-ink">
                  {i.rotulo}
                  {!i.mapeado && <span className="ml-1.5 text-xs text-warn-ink">(sem tradução)</span>}
                  <span className="ml-1.5 text-xs text-ink-faint">{i.natureza}</span>
                </span>
                <span className="shrink-0 tabular-nums text-ink-muted">
                  {i.quantidade} <span className="text-xs">· {i.pct}</span>
                </span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-raised" aria-hidden="true">
                <div className={`h-full rounded-full ${cor}`} style={{ width: `${i.largura}%` }} />
              </div>
            </li>
          ))}
        </ol>
      )}

      {m.excluidos && <p className="text-xs text-ink-faint">{m.excluidos}.</p>}
      {m.naoMapeados > 0 && (
        <p className="text-xs text-warn-ink">
          {m.naoMapeados} motivo(s) novo(s) da plataforma ainda sem tradução: aparecem com o texto original.
        </p>
      )}
    </section>
  );
}

function TabelaMarcas({ canal }: { canal: VisaoCanalAtendimento }) {
  return (
    <section aria-label={`Por marca: ${canal.titulo}`} className="flex min-w-0 flex-col gap-2 rounded-lg border border-line bg-surface p-3">
      <h4 className="text-sm font-semibold text-ink">Por marca</h4>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs font-semibold text-ink-faint">
              <th scope="col" className="py-2 pr-3">Marca</th>
              {canal.colunas.map((c) => (
                <th key={c.campo} scope="col" className="whitespace-nowrap px-2 py-2 text-right">{c.titulo}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {canal.linhas.map((l) => (
              <tr key={l.chave} className="border-b border-line last:border-b-0">
                <th scope="row" className="py-2 pr-3 text-left font-medium text-ink">{l.marca}</th>
                {l.celulas.map((v, i) => (
                  <td key={canal.colunas[i].campo} className="whitespace-nowrap px-2 py-2 text-right tabular-nums text-ink-muted">
                    {v}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canal.temAusente && (
        <p className="text-xs text-ink-faint">“—” = a fonte não trouxe pedidos da marca no recorte (não é zero).</p>
      )}
    </section>
  );
}
