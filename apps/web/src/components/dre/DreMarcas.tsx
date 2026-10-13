"use client";

import { useMemo, useState } from "react";
import { Grid3x3, Tags } from "lucide-react";

import Panel from "@/components/ui/Panel";
import Segmented from "@/components/ui/Segmented";
import Chip from "@/components/ui/Chip";
import TableScrollHint from "@/components/TableScrollHint";
import { ClassificacaoChip } from "@/components/dre/DreChips";
import {
  ROTULO_CANAL,
  custosAusentes,
  fmtPctDre,
  fmtReais,
  rankingDeMargem,
  shareDaReceita,
  type CelulaDRE,
  type ItemRanking,
} from "@/lib/dre";

/**
 * DRE-UX-1 — comparacao entre marcas.
 *
 * Share = receita da marca ÷ receita total da selecao, com os valores crus
 * da API (`shareDaReceita`). Ranking de margem SO' entre marcas comparaveis
 * (`rankingDeMargem`): marca nao calculavel, sem custo essencial ou com um
 * conjunto de custos diferente sai do ranking com o motivo, e nunca aparece
 * como "melhor margem".
 *
 * A matriz marca x canal existe porque a classificacao da marca e' a PIOR
 * entre os canais dela: com a Shopee no filtro, toda marca que vende na
 * Shopee sai nao calculavel — e a margem do ML e do TikTok dela continua
 * visivel ali, celula a celula.
 */
type Ordem = "receita" | "margem";

const TH = "px-3 py-2 text-xs font-semibold uppercase tracking-wide text-ink-faint whitespace-nowrap";
const TD = "px-3 py-2.5 align-top tabular-nums";

function nomeMarca(c: CelulaDRE): string {
  return c.marca_label ?? c.marca ?? "—";
}

function Posicao({ item }: { item: ItemRanking }) {
  if (item.posicao !== null) {
    return <span className="font-semibold text-ink">{`${item.posicao}º`}</span>;
  }
  return (
    <span className="flex flex-col items-start gap-1">
      <Chip tom="ausente">Não comparável</Chip>
      <span className="max-w-[16rem] whitespace-normal text-xs text-ink-muted">{item.comparabilidade.motivo}</span>
    </span>
  );
}

export function DreMarcasTabela({ marcas, total }: { marcas: CelulaDRE[]; total: CelulaDRE }) {
  const [ordem, setOrdem] = useState<Ordem>("receita");
  const ranking = useMemo(() => rankingDeMargem(marcas), [marcas]);
  const linhas = useMemo(() => {
    if (ordem === "margem") return ranking;
    return [...ranking].sort(
      (a, b) => (b.celula.componentes.receita.valor ?? -1) - (a.celula.componentes.receita.valor ?? -1),
    );
  }, [ranking, ordem]);
  const comparaveis = ranking.filter((i) => i.posicao !== null).length;

  return (
    <Panel
      titulo="Comparação entre marcas"
      descricao="Receita, participação e margem conhecida por marca. Ranking só entre marcas comparáveis."
      icone={Tags}
      semPadding
      acao={
        <Segmented<Ordem>
          rotulo="Ordenar marcas por"
          opcoes={[
            { valor: "receita", texto: "Receita" },
            { valor: "margem", texto: "Margem %" },
          ]}
          valor={ordem}
          aoEscolher={setOrdem}
        />
      }
      rodape={
        comparaveis < 2
          ? "Menos de duas marcas comparáveis nesta seleção: não há ranking de margem."
          : "Posição = ordem de margem % entre marcas com os mesmos custos na conta."
      }
    >
      <TableScrollHint>
        <table className="w-full min-w-[60rem] text-data" aria-label="Comparação de receita e margem conhecida por marca">
          <thead>
            <tr className="bg-raised text-left">
              <th scope="col" className={TH}>Marca</th>
              <th scope="col" className={`${TH} text-right`}>Receita</th>
              <th scope="col" className={`${TH} text-right`}>Share</th>
              <th scope="col" className={`${TH} text-right`}>Margem conhecida</th>
              <th scope="col" className={`${TH} text-right`}>Margem %</th>
              <th scope="col" className={TH}>Posição</th>
              <th scope="col" className={TH}>Classificação</th>
              <th scope="col" className={TH}>Custos ausentes</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {linhas.map((item) => {
              const c = item.celula;
              const m = c.margem_contribuicao_conhecida;
              const naoCalc = c.classificacao === "NAO_CALCULAVEL" || m.valor === null;
              const receita = c.componentes.receita.valor;
              const faltam = custosAusentes(c);
              return (
                <tr key={c.marca ?? nomeMarca(c)} className="transition-colors hover:bg-raised/70">
                  <th scope="row" className="whitespace-nowrap px-3 py-2.5 text-left align-top font-semibold text-ink">
                    {nomeMarca(c)}
                  </th>
                  <td className={`${TD} text-right text-ink`}>{receita !== null ? fmtReais(receita) : "Sem valor"}</td>
                  <td className={`${TD} text-right text-ink-muted`}>{fmtPctDre(shareDaReceita(c, total)) ?? "—"}</td>
                  <td className={`${TD} text-right ${naoCalc ? "text-ink-faint" : "text-ink"}`}>
                    {naoCalc || m.valor === null ? "Não calculável" : fmtReais(m.valor)}
                  </td>
                  <td className={`${TD} text-right font-semibold ${naoCalc ? "text-ink-faint" : "text-ink"}`}>
                    {naoCalc ? "—" : fmtPctDre(m.pct_receita) ?? "—"}
                  </td>
                  <td className={TD}>
                    <Posicao item={item} />
                  </td>
                  <td className={TD}>
                    <ClassificacaoChip
                      classificacao={c.classificacao}
                      completude={c.completude.texto}
                      motivo={c.motivos_classificacao[0]}
                    />
                  </td>
                  <td className={`${TD} whitespace-normal text-xs text-ink-muted`}>
                    {faltam.length ? faltam.join(", ") : "nenhum"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScrollHint>
    </Panel>
  );
}

/** Celula da matriz. `undefined` = a marca nao vendeu no canal (nao e' zero). */
function Celula({ c }: { c: CelulaDRE | undefined }) {
  if (!c) return <span className="text-xs text-ink-faint">Sem venda</span>;
  const m = c.margem_contribuicao_conhecida;
  if (c.classificacao === "NAO_CALCULAVEL" || m.valor === null) {
    return (
      <span className="flex flex-col items-end gap-1">
        <ClassificacaoChip classificacao={c.classificacao} motivo={c.motivos_classificacao[0]} />
        <span className="sr-only">{c.motivos_classificacao[0]}</span>
      </span>
    );
  }
  return (
    <span className="flex flex-col items-end gap-0.5">
      <span className="font-semibold text-ink">{fmtPctDre(m.pct_receita)}</span>
      <span className="text-xs text-ink-muted">{fmtReais(m.valor)}</span>
    </span>
  );
}

export function DreMarcaCanalMatriz({
  celulas,
  marcas,
  canais,
}: {
  celulas: CelulaDRE[];
  marcas: CelulaDRE[];
  canais: string[];
}) {
  const porChave = new Map(celulas.map((c) => [`${c.marca}|${c.canal}`, c]));
  const ordenadas = [...marcas].sort(
    (a, b) => (b.componentes.receita.valor ?? -1) - (a.componentes.receita.valor ?? -1),
  );

  return (
    <Panel
      titulo="Margem conhecida por marca e canal"
      descricao="Cada célula com a própria classificação: um canal não calculável não esconde os outros."
      icone={Grid3x3}
      semPadding
    >
      <TableScrollHint>
        <table className="w-full min-w-[36rem] text-data" aria-label="Margem de contribuição conhecida por marca e canal">
          <thead>
            <tr className="bg-raised text-left">
              <th scope="col" className={TH}>Marca</th>
              {canais.map((canal) => (
                <th key={canal} scope="col" className={`${TH} text-right`}>
                  {ROTULO_CANAL[canal] ?? canal}
                </th>
              ))}
              <th scope="col" className={`${TH} text-right`}>Marca (todos)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {ordenadas.map((mc) => (
              <tr key={mc.marca ?? nomeMarca(mc)} className="transition-colors hover:bg-raised/70">
                <th scope="row" className="whitespace-nowrap px-3 py-2.5 text-left align-top font-semibold text-ink">
                  {nomeMarca(mc)}
                </th>
                {canais.map((canal) => (
                  <td key={canal} className={`${TD} text-right`}>
                    <Celula c={porChave.get(`${mc.marca}|${canal}`)} />
                  </td>
                ))}
                <td className={`${TD} text-right`}>
                  <Celula c={mc} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScrollHint>
    </Panel>
  );
}
