"use client";

import { useId, useMemo, useState } from "react";
import { Grid3x3, Tags } from "lucide-react";

import Panel from "@/components/ui/Panel";
import InfoDica from "@/components/ui/InfoDica";
import Segmented from "@/components/ui/Segmented";
import Chip from "@/components/ui/Chip";
import TableScrollHint from "@/components/TableScrollHint";
import BotaoRecorteCalculavel from "@/components/dre/BotaoRecorteCalculavel";
import { ClassificacaoChip } from "@/components/dre/DreChips";
import {
  ROTULO_CANAL,
  completudeExibida,
  custosAusentes,
  fmtPctDre,
  fmtReais,
  juntarNomes,
  rankingDeMargem,
  semMargem,
  textoMargem,
  textoMargemPct,
  textoShare,
  type CelulaDRE,
  type ItemRanking,
} from "@/lib/dre";
import type { Marketplace } from "@/lib/mock-data";

/**
 * DRE-UX-1/2 — comparacao entre marcas.
 *
 * Share = receita da marca ÷ receita total da selecao, com os valores crus
 * da API (`shareDaReceita`). Ranking de margem SO' entre marcas comparaveis
 * (`rankingDeMargem`): marca nao calculavel, sem custo essencial ou com um
 * conjunto de custos diferente sai do ranking com o motivo, e nunca aparece
 * como "melhor margem".
 *
 * DRE-UX-2: a coluna de faltantes diz "Ausente em ao menos um canal" — pelo
 * contrato, `faltam` de uma marca e' o que faltou em AO MENOS UMA celula dela.
 * Ranking vazio diz por que e oferece o mesmo recorte calculavel do resumo.
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

/** Por que o ranking esta vazio (ou com uma marca so'), em linguagem de gestor. */
const REGRA_RANKING = "Posição = ordem de margem % entre marcas com os mesmos custos na conta.";

export function explicacaoRanking(comparaveis: number, bloqueadores: string[]): string {
  if (comparaveis >= 2) return REGRA_RANKING;
  const quantas = comparaveis === 0 ? "nenhuma marca é comparável" : "só uma marca é comparável";
  if (bloqueadores.length) {
    const verbo = bloqueadores.length > 1 ? "não são calculáveis" : "não é calculável";
    return `Ranking vazio: ${quantas} neste recorte porque ${juntarNomes(bloqueadores)} ${verbo} e ${bloqueadores.length > 1 ? "entram" : "entra"} no agregado de cada marca. Receita e share continuam válidos.`;
  }
  return `Ranking vazio: ${quantas} neste recorte. O motivo de cada marca está na coluna Posição.`;
}

export function DreMarcasTabela({
  marcas,
  total,
  bloqueadores = [],
  calculaveis = [],
  aoFiltrar,
}: {
  marcas: CelulaDRE[];
  total: CelulaDRE;
  /** Canais nao calculaveis que impedem o agregado (rotulos). */
  bloqueadores?: string[];
  /** Canais calculaveis escondidos pelo agregado — alvo do botao. */
  calculaveis?: Marketplace[];
  aoFiltrar?: (canais: Marketplace[]) => void;
}) {
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
      titulo={
        <>
          Comparação entre marcas
          <InfoDica texto={REGRA_RANKING} />
        </>
      }
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
        // Com ranking preenchido a regra fica no ⓘ; o rodape so' explica o
        // ranking vazio, que muda a leitura da coluna Posicao.
        comparaveis < 2 ? (
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <span>{explicacaoRanking(comparaveis, bloqueadores)}</span>
            {aoFiltrar && <BotaoRecorteCalculavel canais={calculaveis} aoAplicar={aoFiltrar} />}
          </div>
        ) : undefined
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
              <th scope="col" className={TH}>Ausente em ao menos um canal</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {linhas.map((item) => {
              const c = item.celula;
              const sem = semMargem(c);
              const receita = c.componentes.receita.valor;
              const faltam = custosAusentes(c);
              return (
                <tr key={c.marca ?? nomeMarca(c)} className="transition-colors hover:bg-raised/70">
                  <th scope="row" className="whitespace-nowrap px-3 py-2.5 text-left align-top font-semibold text-ink">
                    {nomeMarca(c)}
                  </th>
                  <td className={`${TD} text-right text-ink`}>{receita !== null ? fmtReais(receita) : "Sem valor"}</td>
                  <td className={`${TD} text-right text-ink-muted`}>{textoShare(c, total)}</td>
                  <td className={`${TD} text-right ${sem ? "text-ink-faint" : "text-ink"}`}>{textoMargem(c)}</td>
                  <td className={`${TD} text-right font-semibold ${sem ? "text-ink-faint" : "text-ink"}`}>
                    {textoMargemPct(c)}
                  </td>
                  <td className={TD}>
                    <Posicao item={item} />
                  </td>
                  <td className={TD}>
                    <ClassificacaoChip classificacao={c.classificacao} completude={completudeExibida(c)} />
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

/**
 * Celula nao calculavel da matriz: o selo e' um BOTAO que abre os motivos ali
 * mesmo — clique, toque e teclado. Antes o motivo so' existia em `title` e
 * `sr-only`, inalcancavel para quem enxerga e nao usa mouse.
 */
export function CelulaNaoCalculavel({ c, abertoInicial = false }: { c: CelulaDRE; abertoInicial?: boolean }) {
  const [aberto, setAberto] = useState(abertoInicial);
  const id = useId();
  return (
    <span className="flex flex-col items-end gap-1">
      <button
        type="button"
        aria-expanded={aberto}
        aria-controls={id}
        onClick={() => setAberto((v) => !v)}
        className="inline-flex min-h-11 items-center rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <ClassificacaoChip classificacao={c.classificacao} />
        <span className="ml-1 text-xs font-semibold text-accent">{aberto ? "ocultar" : "por quê?"}</span>
      </button>
      {aberto && (
        <ul id={id} className="max-w-[16rem] whitespace-normal text-left text-xs text-ink-muted">
          {c.motivos_classificacao.length ? (
            c.motivos_classificacao.map((m) => <li key={m}>{m}</li>)
          ) : (
            <li>sem motivo informado pela API</li>
          )}
        </ul>
      )}
    </span>
  );
}

/** Celula da matriz. `undefined` = a marca nao vendeu no canal (nao e' zero). */
function Celula({ c }: { c: CelulaDRE | undefined }) {
  if (!c) return <span className="text-xs text-ink-faint">Sem venda</span>;
  const v = c.margem_contribuicao_conhecida.valor;
  if (semMargem(c) || v === null) return <CelulaNaoCalculavel c={c} />;
  return (
    <span className="flex flex-col items-end gap-0.5">
      <span className="font-semibold text-ink">{fmtPctDre(c.margem_contribuicao_conhecida.pct_receita) ?? "Sem %"}</span>
      <span className="text-xs text-ink-muted">{fmtReais(v)}</span>
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
              <th scope="col" className={`${TH} text-right`}>Marca (todos os canais)</th>
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
