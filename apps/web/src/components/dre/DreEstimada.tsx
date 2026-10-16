"use client";

/**
 * DRE-UX-3 — visao "Estimada com premissas".
 *
 * Tudo aqui le `cenario_premissas` (PR #79) e NADA calcula: linhas, subtotais,
 * margem e estado chegam prontos da API. A margem conhecida so' aparece no
 * painel de comparacao, ROTULADA e em coluna propria — nunca somada,
 * subtraida ou misturada com a estimada no mesmo numero.
 */
import { useId } from "react";
import { BookOpenCheck, FileSpreadsheet, Scale, Table2 } from "lucide-react";

import Chip from "@/components/ui/Chip";
import Disclosure from "@/components/ui/Disclosure";
import KpiTile from "@/components/ui/KpiTile";
import Panel from "@/components/ui/Panel";
import StateBlock from "@/components/ui/StateBlock";
import TableScrollHint from "@/components/TableScrollHint";
import { ClassificacaoChip } from "@/components/dre/DreChips";
import Motivos from "@/components/dre/Motivos";
import {
  ROTULO_MARGEM,
  fmtInstanteBrt,
  fmtPctDre,
  fmtReais,
  semMargem,
  textoMargem,
  textoMargemPct,
  type CelulaDRE,
  type DreResponse,
} from "@/lib/dre";
import {
  ESTADO_CENARIO_CHIP,
  ORIGEM_ROTULO,
  ROTULO_MARGEM_ESTIMADA,
  fmtTaxaPremissa,
  hashCurto,
  linhasEstimadas,
  premissasPorMarca,
  textoMargemEstimada,
  textoMargemEstimadaPct,
  textoValorLinha,
  type CenarioPremissas,
  type Disponibilidade,
  type LinhaEstimadaExibida,
} from "@/lib/dre-estimada";

export function EstadoCenarioChip({ cenario }: { cenario: CenarioPremissas }) {
  const e = ESTADO_CENARIO_CHIP[cenario.estado];
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <Chip tom={e.tom} ponto>
        {e.rotulo}
      </Chip>
      {cenario.previa && <Chip tom="atencao">Prévia</Chip>}
    </span>
  );
}

/** Quando nao ha cenario para mostrar: API antiga ou competencia sem premissa. */
export function SemCenario({ disp }: { disp: Exclude<Disponibilidade, { tipo: "ok" }> }) {
  if (disp.tipo === "api_antiga") {
    return (
      <StateBlock
        tipo="indisponivel"
        titulo="A API publicada ainda não fornece o cenário estimado"
        descricao="Esta versão da API não traz premissas. A visão Conhecida continua disponível."
      />
    );
  }
  return (
    <StateBlock
      tipo="indisponivel"
      titulo="Sem premissas para esta competência"
      descricao={`${disp.motivo}. A premissa vale só para a competência publicada; nunca é estendida a outro mês.`}
    />
  );
}

// ---------------------------------------------------------------------------
// Resumo estimado
// ---------------------------------------------------------------------------

export function ResumoEstimado({ cenario }: { cenario: CenarioPremissas }) {
  const nc = cenario.estado === "nao_calculavel" || cenario.margem_contribuicao_estimada === null;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <KpiTile
        rotulo="Receita base"
        valor={cenario.receita_base !== null ? fmtReais(cenario.receita_base) : "Sem valor"}
        tom={cenario.receita_base === null ? "ausente" : "neutro"}
        comparacao="Receita realizada; a mesma da visão Conhecida"
      />
      <KpiTile
        rotulo={ROTULO_MARGEM_ESTIMADA}
        valor={textoMargemEstimada(cenario)}
        tom={nc ? "ausente" : "neutro"}
        destaque={!nc}
        comparacao={
          <span className="flex flex-wrap items-center gap-1.5">
            <EstadoCenarioChip cenario={cenario} />
            {nc && cenario.motivos[0] && <span>{cenario.motivos[0]}</span>}
          </span>
        }
      />
      <KpiTile
        rotulo="% da receita (estimada)"
        valor={textoMargemEstimadaPct(cenario)}
        tom={nc ? "ausente" : "neutro"}
        comparacao={nc ? "Depende da margem estimada" : "Margem estimada ÷ receita base"}
      />
      <KpiTile
        rotulo="Premissas"
        valor={<span className="text-base">{cenario.fonte_premissas.rotulo}</span>}
        comparacao={
          cenario.fonte_premissas.data_fonte
            ? `Fonte enviada em ${fmtInstanteBrt(cenario.fonte_premissas.data_fonte)}`
            : "Data da fonte não informada"
        }
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Conhecida x Estimada — dois numeros, dois rotulos, mesma receita base
// ---------------------------------------------------------------------------

export function ComparacaoCenarios({ celula, cenario }: { celula: CelulaDRE; cenario: CenarioPremissas }) {
  const m = celula.margem_contribuicao_conhecida;
  return (
    <Panel
      titulo="Conhecida × Estimada"
      descricao="Mesma receita base; margens de naturezas diferentes, nunca somadas nem misturadas."
      icone={Scale}
    >
      <dl className="grid grid-cols-1 divide-y divide-line text-data sm:grid-cols-2 sm:divide-x sm:divide-y-0">
        <div className="flex flex-col gap-1.5 pb-3 sm:pb-0 sm:pr-4">
          <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">{ROTULO_MARGEM}</dt>
          <dd className={`text-lg font-semibold tabular-nums ${semMargem(celula) ? "text-ink-faint" : "text-ink"}`}>
            {textoMargem(celula)}
          </dd>
          <dd className="text-xs text-ink-muted">% da receita: {textoMargemPct(celula)}</dd>
          <dd>
            <ClassificacaoChip classificacao={celula.classificacao} />
          </dd>
          <dd className="text-xs text-ink-muted">Só componentes realizados (oficiais). {m.faltam.length ? "Faltam custos sem fonte." : ""}</dd>
        </div>
        <div className="flex flex-col gap-1.5 pt-3 sm:pl-4 sm:pt-0">
          <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">{ROTULO_MARGEM_ESTIMADA}</dt>
          <dd
            className={`text-lg font-semibold tabular-nums ${
              cenario.estado === "nao_calculavel" || cenario.margem_contribuicao_estimada === null ? "text-ink-faint" : "text-ink"
            }`}
          >
            {textoMargemEstimada(cenario)}
          </dd>
          <dd className="text-xs text-ink-muted">% da receita: {textoMargemEstimadaPct(cenario)}</dd>
          <dd>
            <EstadoCenarioChip cenario={cenario} />
          </dd>
          <dd className="text-xs text-ink-muted">Realizado + {cenario.fonte_premissas.rotulo.toLowerCase()}.</dd>
        </div>
      </dl>
      <p className="mt-3 text-xs text-ink-muted">
        Receita base das duas: {cenario.receita_base !== null ? fmtReais(cenario.receita_base) : "sem valor"}.
      </p>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// DRE vertical estimada
// ---------------------------------------------------------------------------

function CelulaValor({ x }: { x: LinhaEstimadaExibida }) {
  const texto = textoValorLinha(x);
  if (texto === null) {
    // Mobile: o motivo ja' esta escrito sob o rotulo; aqui so' "Sem valor" (nunca
    // travessao nem R$ 0), para o selo nao alargar a coluna alem de 390 px.
    return (
      <>
        <span className="text-xs text-ink-faint sm:hidden">Sem valor</span>
        <span className="hidden sm:inline">
          {x.chip ? <Chip tom={x.chip.tom}>{x.chip.rotulo}</Chip> : <span className="text-ink-faint">Sem valor</span>}
        </span>
      </>
    );
  }
  return <span className={x.foraDaSoma ? "text-ink-faint" : x.subtotal ? "font-semibold text-ink" : "text-ink"}>{texto}</span>;
}

function LinhaTabela({ x }: { x: LinhaEstimadaExibida }) {
  const l = x.l;
  const pct = fmtPctDre(l.pct_receita ?? null);
  const taxa = fmtTaxaPremissa(l.percentual);
  return (
    <tr className={`align-top ${x.subtotal ? "bg-raised/60" : ""}`}>
      <th
        scope="row"
        className={`px-3 py-2.5 text-left font-normal [overflow-wrap:anywhere] sm:px-4 ${x.recuo ? "pl-7 sm:pl-9" : ""}`}
      >
        <span className={`block ${x.subtotal ? "font-semibold text-ink" : x.recuo ? "text-ink-muted" : "text-ink"}`}>{l.rotulo}</span>
        {/* Mobile: origem e taxa moram aqui (as colunas somem abaixo de sm). */}
        <span className="mt-0.5 block text-xs text-ink-faint sm:hidden">
          {ORIGEM_ROTULO[l.origem] ?? l.origem}
          {taxa && <> · premissa {taxa}</>}
        </span>
        {(x.foraDaSoma || (x.chip && l.valor !== null)) && (
          <span className="mt-1 flex flex-wrap items-center gap-1.5">
            {x.chip && l.valor !== null && <Chip tom={x.chip.tom}>{x.chip.rotulo}</Chip>}
            {x.foraDaSoma && <span className="text-xs font-semibold text-ink-muted">fora da soma</span>}
          </span>
        )}
        {x.ausencia ? (
          <span className="mt-0.5 block text-xs text-ink-muted">{x.ausencia}</span>
        ) : (
          l.motivo && l.status !== "ok" && <span className="mt-0.5 block text-xs text-ink-muted">{l.motivo}</span>
        )}
      </th>
      <td className="hidden whitespace-nowrap px-3 py-2.5 text-xs text-ink-muted sm:table-cell sm:px-4">
        {ORIGEM_ROTULO[l.origem] ?? l.origem}
      </td>
      <td className="hidden whitespace-nowrap px-3 py-2.5 text-right tabular-nums text-ink-muted sm:table-cell sm:px-4">
        {taxa ?? ""}
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums sm:px-4">
        <CelulaValor x={x} />
        {l.valor !== null && pct !== null && <span className="block text-xs text-ink-muted sm:hidden">{pct}</span>}
      </td>
      <td className="hidden whitespace-nowrap px-3 py-2.5 text-right tabular-nums text-ink-muted sm:table-cell sm:px-4">
        {l.valor !== null && pct !== null ? pct : ""}
      </td>
    </tr>
  );
}

export interface OpcaoRecorteEstimado {
  valor: string;
  rotulo: string;
  celula: CelulaDRE;
}

export function DreVerticalEstimada({
  opcoes,
  selecionado,
  aoEscolher,
  dispDe,
}: {
  opcoes: OpcaoRecorteEstimado[];
  selecionado: string;
  aoEscolher: (v: string) => void;
  dispDe: (c: CelulaDRE) => Disponibilidade;
}) {
  const idSel = useId();
  const atual = opcoes.find((o) => o.valor === selecionado) ?? opcoes[0];
  if (!atual) return null;
  const disp = dispDe(atual.celula);
  const seletor = (
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
  );
  if (disp.tipo !== "ok") {
    return (
      <Panel titulo="DRE estimada" icone={FileSpreadsheet} acao={seletor}>
        <SemCenario disp={disp} />
      </Panel>
    );
  }
  const c = disp.cenario;
  const { principais, informativas, margem } = linhasEstimadas(c);
  const nc = c.estado === "nao_calculavel" || c.margem_contribuicao_estimada === null;
  return (
    <Panel
      titulo="DRE estimada"
      descricao={
        c.linhas.some((l) => l.percentual !== null && l.percentual !== undefined)
          ? `${c.fonte_premissas.rotulo} sobre a receita realizada. Origem de cada linha ao lado.`
          : `${c.fonte_premissas.rotulo}. Recorte agregado: soma das células, sem taxa própria — as taxas estão em Premissas por marca.`
      }
      icone={FileSpreadsheet}
      semPadding
      acao={seletor}
      rodape={
        c.avisos.length ? (
          <div className="flex flex-col gap-0.5">
            <ul className="flex flex-col gap-0.5">
              {c.avisos.slice(0, 2).map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
            {c.avisos.length > 2 && (
              <Disclosure rotulo={`Mais ${c.avisos.length - 2} avisos`} className="-ml-2">
                <ul className="flex flex-col gap-0.5">
                  {c.avisos.slice(2).map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              </Disclosure>
            )}
          </div>
        ) : undefined
      }
    >
      <table className="w-full text-data" aria-label={`DRE estimada — ${atual.rotulo}`}>
        <thead>
          <tr className="bg-raised text-left text-xs font-semibold uppercase tracking-wide text-ink-faint">
            <th scope="col" className="px-3 py-2 sm:px-4">Linha</th>
            <th scope="col" className="hidden px-3 py-2 sm:table-cell sm:px-4">Origem</th>
            <th scope="col" className="hidden px-3 py-2 text-right sm:table-cell sm:px-4">Premissa</th>
            <th scope="col" className="px-3 py-2 text-right sm:px-4">Valor</th>
            <th scope="col" className="hidden w-24 px-4 py-2 text-right sm:table-cell">% rec.</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {principais.map((x) => (
            <LinhaTabela key={x.l.linha} x={x} />
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-line-strong bg-raised align-top">
            <th scope="row" className="px-3 py-3 text-left [overflow-wrap:anywhere] sm:px-4">
              <span className="block font-semibold text-ink">{ROTULO_MARGEM_ESTIMADA}</span>
              <span className="mt-1 block">
                <EstadoCenarioChip cenario={c} />
              </span>
              <Motivos motivos={c.motivos} visiveis={3} className="mt-1" />
              {c.componentes_ausentes.length > 0 && (
                <span className="mt-0.5 block text-xs font-normal text-ink-muted">
                  Sem valor:{" "}
                  {c.componentes_ausentes
                    .map((a) => (c.linhas.find((l) => l.linha === a.linha)?.rotulo ?? a.linha).replace(/^\([-=]\)\s*/, ""))
                    .join(", ")}
                </span>
              )}
            </th>
            <td className="hidden sm:table-cell" />
            <td className="hidden sm:table-cell" />
            <td className="px-3 py-3 text-right text-sm font-semibold tabular-nums text-ink sm:whitespace-nowrap sm:px-4">
              {nc ? <span className="text-ink-faint">Não calculável</span> : textoMargemEstimada(c)}
              {!nc && margem && fmtPctDre(c.margem_contribuicao_estimada_pct) && (
                <span className="block text-xs text-ink-muted sm:hidden">{fmtPctDre(c.margem_contribuicao_estimada_pct)}</span>
              )}
            </td>
            <td className="hidden whitespace-nowrap px-4 py-3 text-right font-semibold tabular-nums text-ink sm:table-cell">
              {!nc ? fmtPctDre(c.margem_contribuicao_estimada_pct) ?? "" : ""}
            </td>
          </tr>
        </tfoot>
      </table>
      {informativas.length > 0 && (
        <div className="border-t border-line px-3 py-3 sm:px-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Fora da soma (informativo)</p>
          <table className="mt-1 w-full text-data" aria-label="Linhas informativas, fora da soma">
            <tbody className="divide-y divide-line">
              {informativas.map((x) => (
                <LinhaTabela key={x.l.linha} x={x} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Estado do cenario por recorte (total, canais, marcas)
// ---------------------------------------------------------------------------

export function EstimadaPorRecorte({
  linhas,
  dispDe,
}: {
  linhas: { rotulo: string; celula: CelulaDRE }[];
  dispDe: (c: CelulaDRE) => Disponibilidade;
}) {
  return (
    <Panel
      titulo="Margem estimada por recorte"
      descricao="Cada recorte com o próprio estado. Agregado é soma das células, sem reaplicar taxa."
      icone={Table2}
      semPadding
    >
      <TableScrollHint>
        <table className="w-full min-w-[44rem] text-data" aria-label="Margem estimada por recorte">
          <thead>
            <tr className="bg-raised text-left text-xs font-semibold uppercase tracking-wide text-ink-faint">
              <th scope="col" className="px-3 py-2">Recorte</th>
              <th scope="col" className="px-3 py-2">Estado</th>
              <th scope="col" className="px-3 py-2 text-right">Margem estimada</th>
              <th scope="col" className="px-3 py-2 text-right">% receita</th>
              <th scope="col" className="px-3 py-2">Motivos</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {linhas.map(({ rotulo, celula }) => {
              const d = dispDe(celula);
              return (
                <tr key={rotulo} className="align-top">
                  <th scope="row" className="whitespace-nowrap px-3 py-2.5 text-left font-semibold text-ink">
                    {rotulo}
                  </th>
                  {d.tipo === "ok" ? (
                    <>
                      <td className="px-3 py-2.5">
                        <EstadoCenarioChip cenario={d.cenario} />
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums">{textoMargemEstimada(d.cenario)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums">{textoMargemEstimadaPct(d.cenario)}</td>
                      <td className="px-3 py-2.5">
                        <Motivos motivos={d.cenario.motivos} visiveis={1} />
                      </td>
                    </>
                  ) : (
                    <td colSpan={4} className="px-3 py-2.5 text-xs text-ink-muted">
                      {d.tipo === "api_antiga" ? "API sem cenário estimado" : `Sem premissa: ${d.motivo}`}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScrollHint>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Premissas por marca
// ---------------------------------------------------------------------------

export function PremissasPorMarca({ resp }: { resp: DreResponse }) {
  const { marcas, componentes, fonte } = premissasPorMarca(resp);
  if (!marcas.length) return null;
  return (
    <Panel
      titulo="Premissas por marca"
      descricao={`${fonte?.rotulo ?? "Premissas"} — percentual da receita bruta aplicado em cada célula.`}
      icone={BookOpenCheck}
      semPadding
      rodape={
        <div className="flex flex-col gap-1">
          <span>
            Fonte: {fonte?.rotulo ?? "—"}
            {fonte?.data_fonte ? ` · enviada em ${fmtInstanteBrt(fonte.data_fonte)}` : ""}
            {fonte?.carregado_em ? ` · carregada em ${fmtInstanteBrt(fonte.carregado_em)}` : ""}
          </span>
          {fonte?.source_sha256 && (
            <Disclosure rotulo="Detalhes da fonte" className="-ml-2">
              <span className="break-all">SHA-256 {fonte.source_sha256}</span>
            </Disclosure>
          )}
          <span>Comissões e Outras Receitas Operacionais aparecem, mas ficam fora da soma até a ratificação.</span>
        </div>
      }
    >
      <TableScrollHint>
        <table className="w-full min-w-[44rem] text-data" aria-label="Premissas por marca (percentual da receita)">
          <thead>
            <tr className="bg-raised text-left text-xs font-semibold uppercase tracking-wide text-ink-faint">
              <th scope="col" className="px-3 py-2">Premissa</th>
              {marcas.map((m) => (
                <th key={m.marca} scope="col" className="px-3 py-2 text-right">
                  {m.marca_label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {componentes.map((comp) => {
              const amostra = marcas.map((m) => m.premissas[comp.linha]).find(Boolean);
              const fora = amostra ? !amostra.somavel : false;
              return (
                <tr key={comp.linha} className="align-top">
                  <th scope="row" className="px-3 py-2 text-left font-normal text-ink">
                    {comp.rotulo.replace(/^\(-\)\s*/, "")}
                    {fora && <span className="ml-1.5 text-xs font-semibold text-ink-muted">fora da soma</span>}
                  </th>
                  {marcas.map((m) => {
                    const p = m.premissas[comp.linha];
                    const taxa = fmtTaxaPremissa(p?.taxa);
                    return (
                      <td key={m.marca} className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                        {taxa ?? <span className="text-xs text-ink-faint">{p ? "Ausente na matriz" : "Sem premissa"}</span>}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScrollHint>
    </Panel>
  );
}
