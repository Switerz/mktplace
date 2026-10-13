"use client";

import { useId } from "react";
import { FileSpreadsheet } from "lucide-react";

import Disclosure from "@/components/ui/Disclosure";
import Panel from "@/components/ui/Panel";
import { ClassificacaoChip, StatusChip } from "@/components/dre/DreChips";
import {
  ROTULO_MARGEM,
  custosAusentes,
  fmtCusto,
  fmtInstanteBrt,
  fmtPctDre,
  fmtReais,
  linhasDre,
  type CelulaDRE,
  type LinhaDre,
} from "@/lib/dre";

/**
 * DRE-UX-1 — DRE vertical de UM recorte (total, canal ou marca).
 *
 * Tres regras que esta tabela existe para nao quebrar:
 *   1. valor `null` mostra status + motivo, nunca "R$ 0";
 *   2. "dos quais: afiliados" e' recuada, sem sinal de subtracao e fora de
 *      qualquer soma — ja' esta dentro de Taxas;
 *   3. um custo com valor mas FORA da margem (nao confirmado, nao
 *      reconciliado, provisorio) aparece apagado e com "fora da margem", para
 *      ninguem refazer a conta somando-o.
 */
export interface OpcaoRecorte {
  valor: string;
  rotulo: string;
  celula: CelulaDRE;
}

function Origem({ l }: { l: LinhaDre }) {
  const c = l.componente;
  const partes: string[] = [];
  if (c.data_as_of) partes.push(`dado até ${fmtInstanteBrt(c.data_as_of)}`);
  if (c.publicado_em) partes.push(`publicado ${fmtInstanteBrt(c.publicado_em)}`);
  if (!c.fonte && !partes.length) return null;
  return (
    <span className="block break-all text-xs text-ink-faint">
      {c.fonte && <>Fonte: {c.fonte}</>}
      {c.fonte && partes.length ? " · " : ""}
      {partes.join(" · ")}
    </span>
  );
}

function Valor({ l }: { l: LinhaDre }) {
  const v = l.componente.valor;
  if (v === null) {
    // Ausencia: nenhum numero. O status ocupa a coluna do valor.
    return <StatusChip status={l.componente.status} motivo={l.componente.motivo} />;
  }
  if (l.recuo) {
    // Afiliado: magnitude informativa, sem "−". Ja' esta em Taxas.
    return <span className="italic text-ink-muted">{fmtReais(v)}</span>;
  }
  const texto = l.custo ? fmtCusto(v) : fmtReais(v);
  return <span className={l.naMargem || !l.custo ? "text-ink" : "text-ink-faint"}>{texto}</span>;
}

export default function DreVertical({
  opcoes,
  selecionado,
  aoEscolher,
}: {
  opcoes: OpcaoRecorte[];
  selecionado: string;
  aoEscolher: (valor: string) => void;
}) {
  const idSel = useId();
  const atual = opcoes.find((o) => o.valor === selecionado) ?? opcoes[0];
  if (!atual) return null;
  const c = atual.celula;
  const m = c.margem_contribuicao_conhecida;
  const naoCalc = c.classificacao === "NAO_CALCULAVEL" || m.valor === null;
  const faltam = custosAusentes(c);

  return (
    <Panel
      titulo="DRE vertical"
      descricao="Receita, cada custo e a margem de contribuição conhecida do recorte escolhido."
      icone={FileSpreadsheet}
      semPadding
      acao={
        <div className="flex items-center gap-2">
          <label htmlFor={idSel} className="text-xs font-semibold text-ink-muted">
            Recorte
          </label>
          <select
            id={idSel}
            value={atual.valor}
            onChange={(e) => aoEscolher(e.target.value)}
            className="min-h-11 max-w-[14rem] rounded-lg border border-line bg-surface px-2 text-sm font-semibold text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            {opcoes.map((o) => (
              <option key={o.valor} value={o.valor}>
                {o.rotulo}
              </option>
            ))}
          </select>
        </div>
      }
      rodape={
        c.avisos_qualidade.length ? (
          // Num agregado os avisos das celulas se acumulam (medido: 11 no
          // total de jul/2026). Dois ficam visiveis; o resto abre sob demanda.
          <div className="flex flex-col gap-0.5">
            <ul className="flex flex-col gap-0.5">
              {c.avisos_qualidade.slice(0, 2).map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
            {c.avisos_qualidade.length > 2 && (
              <Disclosure rotulo={`Mais ${c.avisos_qualidade.length - 2} avisos de qualidade`} className="-ml-2">
                <ul className="flex flex-col gap-0.5">
                  {c.avisos_qualidade.slice(2).map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              </Disclosure>
            )}
          </div>
        ) : undefined
      }
    >
      <table className="w-full text-data" aria-label={`DRE vertical — ${atual.rotulo}`}>
        <thead>
          <tr className="bg-raised text-left text-xs font-semibold uppercase tracking-wide text-ink-faint">
            <th scope="col" className="px-4 py-2">Linha</th>
            <th scope="col" className="px-4 py-2 text-right">Valor</th>
            <th scope="col" className="w-16 px-4 py-2 text-right sm:w-24">% rec.</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {linhasDre(c).map((l) => {
            const pct = fmtPctDre(l.componente.pct_receita);
            // So' faz sentido dizer "fora da margem" quando HA margem: num
            // recorte nao calculavel todo custo estaria fora, e o selo vira ruido.
            const foraDaMargem = !naoCalc && l.custo && !l.recuo && l.componente.valor !== null && !l.naMargem;
            return (
              <tr key={l.chave} className="align-top">
                <th scope="row" className={`px-4 py-2.5 text-left font-normal ${l.recuo ? "pl-9" : ""}`}>
                  <span className={`block ${l.recuo ? "italic text-ink-muted" : l.chave === "receita" ? "font-semibold text-ink" : "text-ink"}`}>
                    {l.rotulo}
                  </span>
                  {l.recuo && l.componente.valor !== null && (
                    <span className="block text-xs text-ink-muted">Já incluído em Taxas — não somado de novo.</span>
                  )}
                  {l.componente.valor !== null && l.chip && (
                    <span className="mt-1 flex flex-wrap items-center gap-1.5">
                      <StatusChip status={l.componente.status} motivo={l.componente.motivo} />
                      {foraDaMargem && <span className="text-xs text-ink-muted">fora da margem</span>}
                    </span>
                  )}
                  {l.componente.motivo && l.componente.status !== "disponivel" && (
                    <span className="mt-0.5 block text-xs text-ink-muted">{l.componente.motivo}</span>
                  )}
                  <Origem l={l} />
                </th>
                <td className="whitespace-nowrap px-4 py-2.5 text-right tabular-nums">
                  <Valor l={l} />
                </td>
                <td className="whitespace-nowrap px-4 py-2.5 text-right tabular-nums text-ink-muted">
                  {l.componente.valor !== null && pct !== null ? pct : ""}
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-line-strong bg-raised align-top">
            <th scope="row" className="px-4 py-3 text-left">
              <span className="block font-semibold text-ink">{ROTULO_MARGEM}</span>
              <span className="mt-1 flex flex-wrap items-center gap-1.5">
                <ClassificacaoChip classificacao={c.classificacao} completude={c.completude.texto} />
              </span>
              {naoCalc ? (
                c.motivos_classificacao.length > 0 && (
                  <ul className="mt-1 flex flex-col gap-0.5 text-xs font-normal text-ink-muted">
                    {c.motivos_classificacao.slice(0, 4).map((mot) => (
                      <li key={mot}>{mot}</li>
                    ))}
                    {c.motivos_classificacao.length > 4 && (
                      <li>e mais {c.motivos_classificacao.length - 4} motivo(s)</li>
                    )}
                  </ul>
                )
              ) : (
                m.formula && <span className="mt-1 block text-xs font-normal text-ink-muted">Fórmula: {m.formula}</span>
              )}
              {faltam.length > 0 && (
                <span className="mt-0.5 block text-xs font-normal text-ink-muted">Faltam: {faltam.join(", ")}</span>
              )}
            </th>
            <td className="whitespace-nowrap px-4 py-3 text-right text-sm font-semibold tabular-nums text-ink">
              {naoCalc || m.valor === null ? <span className="text-ink-faint">Não calculável</span> : fmtReais(m.valor)}
            </td>
            <td className="whitespace-nowrap px-4 py-3 text-right font-semibold tabular-nums text-ink">
              {!naoCalc ? fmtPctDre(m.pct_receita) ?? "" : ""}
            </td>
          </tr>
        </tfoot>
      </table>
    </Panel>
  );
}
