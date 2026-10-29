"use client";

import { useId } from "react";
import { FileSpreadsheet } from "lucide-react";

import Panel from "@/components/ui/Panel";
import Motivos from "@/components/dre/Motivos";
import { ClassificacaoChip, StatusChip } from "@/components/dre/DreChips";
import {
  ROTULO_MARGEM,
  completudeExibida,
  custosAusentes,
  fmtCusto,
  fmtInstanteBrt,
  fmtPctDre,
  fmtReais,
  linhasDre,
  rotuloAusentes,
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
    return <StatusChip status={l.componente.status} />;
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
        // Num agregado os avisos das celulas se acumulam (medido: 11 no total
        // de jul/2026). Dois ficam visiveis; o resto abre sob demanda.
        c.avisos_qualidade.length ? (
          <Motivos motivos={c.avisos_qualidade} visiveis={2} unidade={["aviso", "avisos"]} />
        ) : undefined
      }
    >
      <table className="w-full text-data" aria-label={`DRE vertical — ${atual.rotulo}`}>
        <thead>
          <tr className="bg-raised text-left text-xs font-semibold uppercase tracking-wide text-ink-faint">
            <th scope="col" className="px-3 py-2 sm:px-4">Linha</th>
            <th scope="col" className="px-3 py-2 text-right sm:px-4">Valor</th>
            <th scope="col" className="hidden w-24 px-4 py-2 text-right sm:table-cell">% rec.</th>
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
                <th scope="row" className={`px-3 py-2.5 text-left font-normal [overflow-wrap:anywhere] sm:px-4 ${l.recuo ? "pl-7 sm:pl-9" : ""}`}>
                  <span className={`block ${l.recuo ? "italic text-ink-muted" : l.chave === "receita" ? "font-semibold text-ink" : "text-ink"}`}>
                    {l.rotulo}
                  </span>
                  {l.recuo && l.componente.valor !== null && (
                    <span className="block text-xs text-ink-muted">Já incluído em Taxas — não somado de novo.</span>
                  )}
                  {l.componente.valor !== null && l.chip && (
                    <span className="mt-1 flex flex-wrap items-center gap-1.5">
                      <StatusChip status={l.componente.status} />
                      {foraDaMargem && <span className="text-xs text-ink-muted">fora da margem</span>}
                    </span>
                  )}
                  {l.componente.motivo && l.componente.status !== "disponivel" && (
                    <span className="mt-0.5 block text-xs text-ink-muted">{l.componente.motivo}</span>
                  )}
                  <Origem l={l} />
                </th>
                <td className="whitespace-nowrap px-3 py-2.5 sm:px-4 text-right tabular-nums">
                  <Valor l={l} />
                  {/* Mobile: o % mora sob o valor (a coluna some abaixo de sm). */}
                  {l.componente.valor !== null && pct !== null && (
                    <span className="block text-xs text-ink-muted sm:hidden">{pct}</span>
                  )}
                </td>
                <td className="hidden whitespace-nowrap px-4 py-2.5 text-right tabular-nums text-ink-muted sm:table-cell">
                  {l.componente.valor !== null && pct !== null ? pct : ""}
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-line-strong bg-raised align-top">
            <th scope="row" className="px-3 py-3 text-left [overflow-wrap:anywhere] sm:px-4">
              <span className="block font-semibold text-ink">{ROTULO_MARGEM}</span>
              <span className="mt-1 flex flex-wrap items-center gap-1.5">
                <ClassificacaoChip classificacao={c.classificacao} completude={completudeExibida(c)} />
                {completudeExibida(c) === null && (
                  <span className="text-xs font-semibold text-ink-muted">— veja os canais</span>
                )}
              </span>
              {naoCalc ? (
                // DRE-UX-2: o "e mais N" agora abre — antes os motivos alem do
                // quarto ficavam inalcancaveis.
                <Motivos motivos={c.motivos_classificacao} visiveis={4} className="mt-1" />
              ) : (
                m.formula && <span className="mt-1 block text-xs font-normal text-ink-muted">Fórmula: {m.formula}</span>
              )}
              {faltam.length > 0 && (
                <span className="mt-0.5 block text-xs font-normal text-ink-muted">
                  {rotuloAusentes(c)}: {faltam.join(", ")}
                </span>
              )}
            </th>
            <td className="px-3 py-3 text-right text-sm font-semibold tabular-nums text-ink sm:whitespace-nowrap sm:px-4">
              {naoCalc || m.valor === null ? <span className="text-ink-faint">Não calculável</span> : fmtReais(m.valor)}
              {!naoCalc && fmtPctDre(m.pct_receita) && (
                <span className="block text-xs text-ink-muted sm:hidden">{fmtPctDre(m.pct_receita)}</span>
              )}
            </td>
            <td className="hidden whitespace-nowrap px-4 py-3 text-right font-semibold tabular-nums text-ink sm:table-cell">
              {!naoCalc ? fmtPctDre(m.pct_receita) ?? "" : ""}
            </td>
          </tr>
        </tfoot>
      </table>
    </Panel>
  );
}
