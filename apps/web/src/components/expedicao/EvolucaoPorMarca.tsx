"use client";

/**
 * Gate EXPEDICAO-UX-3 — evolucao do backlog por marca, em TABELA gerencial.
 *
 * O UX-2 trocou o grafico unico por quatro cartoes grandes; na producao eles
 * ocupavam meia tela para quatro horas de historico e exibiam "Escala do
 * cartao: X a Y". Aqui a evolucao vira uma linha por conta:
 *
 *   Marca | Backlog atual | Variacao | Acima de 48 h | Travados | Tendencia
 *
 * - a MARCA e' a identidade; a conta aparece so' como apelido ("conta 1 de
 *   2") quando a marca tem mais de uma, e o identificador so' no tooltip;
 * - serie constante diz "Estavel em N" e desenha uma linha reta, sem faixa;
 * - o percentual so' aparece com base e historico suficientes
 *   (`percentualValido`); com historico curto a variacao e' "desde o inicio
 *   da serie", dito no cabecalho da coluna;
 * - a sparkline usa tinta neutra: cor e' para risco, nao para decorar linha;
 * - o tooltip le a resposta que ja' esta' na tela — nenhuma chamada por hover.
 *
 * UI-EXPEDICAO-1 — com `fila`, vira a TABELA UNICA de marcas: a fotografia
 * de cada conta (backlog, vencidos, +48 h, travados e a barra de prazo) ao
 * lado da tendencia do periodo. Antes eram duas tabelas com as mesmas marcas
 * ("Concentracao" e "Evolucao"). As linhas vem da fotografia — a conta sem
 * ponto no periodo continua na tabela, so' sem tendencia.
 */
import { useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";

import InfoDica from "@/components/ui/InfoDica";

import {
  coberturaDaEvolucao,
  descreverCobertura,
  dominioHorizontal,
  faixaDaSerie,
  formatarHoraBrt,
  lerVariacao,
  montarEvolucaoPorMarca,
  periodoDaVariacao,
  type SerieMarca,
} from "@/lib/expedicao-evolucao";
import type { RespostaTendencia } from "@/lib/expedicao-contract";
import { formatarInteiro } from "@/lib/expedicao-formato";

/** A fotografia da fila de UMA conta, para a tabela unica. */
export interface LinhaDaFila {
  conta: string;
  titulo: string;
  apelido: string | null;
  backlog: number;
  /** `null` no canal sem prazo publicado: a coluna nem existe. */
  vencidos: number | null;
  acima48h: number;
  travados: number;
  /** "5,3%" dos travados sobre o backlog da conta. */
  pctTravados?: string | null;
  /** Abre a lista dos pedidos travados da conta. */
  aoVerTravados?: () => void;
  /** EXPEDICAO-VENCE-AMANHA: "1,0%" dos vencidos sobre o backlog da conta. */
  pctVencidos?: string | null;
  /** Abre a lista dos pedidos vencidos da conta. */
  aoVerVencidos?: () => void;
  /**
   * EXPEDICAO-PERTO-DE-VENCER: vencem hoje ate' as 23:59 + no proximo dia de
   * prazo — o MESMO numero do cartao "Perto de vencer", por conta.
   * `null`/ausente em todas as linhas (ML, API antiga): a coluna nem existe.
   */
  pertoDeVencer?: number | null;
  pctPertoDeVencer?: string | null;
  aoVerPertoDeVencer?: () => void;
  /** Barra de prazo (ou de distribuicao) desenhada pelo cliente do canal. */
  barra: ReactNode;
}

/** O "ⓘ" de cada classificacao no cabecalho da tabela, montado pelo canal. */
export type AjudasDaTabela = Partial<Record<"vencidos" | "pertoDeVencer" | "acima48h" | "travados", ReactNode>>;

/**
 * EXPEDICAO-TABELA-AMANHA — vencido e' o numero mais importante da tabela:
 * etiqueta vermelha (so' com valor; zero nao pinta).
 */
const ETIQUETA_VENCIDO = "rounded-md bg-crit-soft px-2 py-0.5 font-bold text-crit-ink ring-1 ring-crit/30";

const L = 120;
const A = 28;
const PAD = 3;

const inteiro = formatarInteiro;

function Sparkline({ serie, dominioX }: { serie: SerieMarca; dominioX: [number, number] }) {
  const [ativo, setAtivo] = useState<number | null>(null);
  const [x0, x1] = dominioX;
  const faixa = faixaDaSerie(serie);
  const x = (t: number) => (x1 === x0 ? L / 2 : PAD + ((t - x0) / (x1 - x0)) * (L - 2 * PAD));
  const y = (v: number) => (!faixa ? A / 2 : A - PAD - ((v - faixa[0]) / (faixa[1] - faixa[0])) * (A - 2 * PAD));
  const ponto = ativo !== null ? serie.pontos[ativo] : null;

  const aoMover = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * L;
    let melhor = 0;
    serie.pontos.forEach((p, i) => {
      if (Math.abs(x(p.t) - px) < Math.abs(x(serie.pontos[melhor].t) - px)) melhor = i;
    });
    setAtivo(melhor);
  };
  const aoTeclar = (e: KeyboardEvent<HTMLDivElement>) => {
    const n = serie.pontos.length;
    const atual = ativo ?? n - 1;
    const mapa: Record<string, number> = {
      ArrowRight: Math.min(n - 1, atual + 1),
      ArrowLeft: Math.max(0, atual - 1),
      Home: 0,
      End: n - 1,
    };
    if (e.key in mapa) {
      e.preventDefault();
      setAtivo(mapa[e.key]);
    } else if (e.key === "Escape") {
      setAtivo(null);
    }
  };

  return (
    <div
      className="relative inline-block rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      tabIndex={0}
      role="group"
      aria-label={`Tendência de ${serie.titulo}${serie.apelido ? `, ${serie.apelido}` : ""}: ${serie.pontos.length} ponto(s) de ${formatarHoraBrt(serie.primeiro)} a ${formatarHoraBrt(serie.ultimo)}. Use as setas para percorrer.`}
      onKeyDown={aoTeclar}
      onFocus={() => setAtivo((v) => v ?? serie.pontos.length - 1)}
      onBlur={() => setAtivo(null)}
    >
      <svg
        viewBox={`0 0 ${L} ${A}`}
        className="block h-7 w-[120px] touch-pan-y overflow-visible"
        aria-hidden="true"
        onPointerMove={aoMover}
        onPointerDown={aoMover}
        onPointerLeave={() => setAtivo(null)}
      >
        {serie.trechos.map((trecho, i) =>
          trecho.length > 1 ? (
            <polyline
              key={i}
              points={trecho.map((p) => `${x(p.t)},${y(p.backlog)}`).join(" ")}
              fill="none"
              className="stroke-ink-muted"
              strokeWidth={1.5}
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
            />
          ) : null,
        )}
        {serie.pontos.map((p, i) => (
          <circle
            key={p.t}
            cx={x(p.t)}
            cy={y(p.backlog)}
            r={ativo === i ? 3 : 1.75}
            className={ativo === i ? "fill-ink" : "fill-ink-muted"}
          />
        ))}
      </svg>
      {ponto ? (
        <div
          role="status"
          aria-live="polite"
          className="pointer-events-none absolute bottom-full right-0 z-20 mb-1.5 w-56 rounded-md border border-line bg-surface px-2.5 py-2 text-left text-xs shadow-lg"
        >
          <p className="font-semibold text-ink">{serie.titulo}</p>
          <p className="text-ink-faint">conta {serie.conta}</p>
          <p className="mt-1 text-ink-muted">{formatarHoraBrt(ponto.hora)} (Brasília)</p>
          <dl className="mt-1 grid grid-cols-[1fr_auto] gap-x-3 tabular-nums">
            <dt className="text-ink-muted">Backlog</dt>
            <dd className="text-right font-semibold text-ink">{inteiro(ponto.backlog)}</dd>
            <dt className="text-ink-muted">Acima de 48 h</dt>
            <dd className="text-right text-ink">{inteiro(ponto.over48h)}</dd>
            <dt className="text-ink-muted">Travados</dt>
            <dd className="text-right text-ink">{inteiro(ponto.stalled)}</dd>
          </dl>
        </div>
      ) : null}
    </div>
  );
}

const CLASSE_TABELA =
  "w-full text-sm [&_td]:py-1.5 [&_td]:pr-4 [&_th]:py-1.5 [&_th]:pr-4";

const NOTA_DO_EIXO =
  "Tendência no mesmo eixo de tempo para todas as marcas; hora sem ponto interrompe a linha.";

export default function EvolucaoPorMarca({
  resposta,
  fila,
  rotuloBarra = "Prazo",
  ajudas = {},
}: {
  resposta: RespostaTendencia | null;
  fila?: LinhaDaFila[];
  rotuloBarra?: string;
  ajudas?: AjudasDaTabela;
}) {
  if (fila) {
    return (
      <TabelaUnica
        resposta={resposta}
        fila={fila}
        rotuloBarra={rotuloBarra}
        ajudas={ajudas}
      />
    );
  }
  if (!resposta) return null;
  return <SoTendencia resposta={resposta} />;
}

function SoTendencia({ resposta }: { resposta: RespostaTendencia }) {
  const series = montarEvolucaoPorMarca(resposta.points);
  const dominioX = dominioHorizontal(series);
  const cob = coberturaDaEvolucao(resposta);
  const cobertura = descreverCobertura(cob);
  const periodo = periodoDaVariacao(cob);
  if (!dominioX || !series.length) return null;

  return (
    <div className="flex flex-col gap-2">
      {cobertura.desde ? (
        <p className="text-xs text-ink-muted" data-testid="exp-cobertura-real">
          <span className="font-medium text-ink">{cobertura.desde}</span> {cobertura.exibindo}
        </p>
      ) : null}
      <div className="overflow-x-auto">
        <table className={CLASSE_TABELA}>
          <caption className="sr-only">
            Evolução do backlog por marca — uma linha por conta, contas nunca somadas
          </caption>
          <thead className="text-left text-xs uppercase tracking-wide text-ink-faint">
            <tr className="border-b border-line">
              <th scope="col">Marca</th>
              <th scope="col" className="text-right">Backlog atual</th>
              <th scope="col" className="text-right">
                Variação
                <span className="block font-normal normal-case tracking-normal">{periodo}</span>
              </th>
              <th scope="col" className="text-right">Acima de 48 h</th>
              <th scope="col" className="text-right">Travados</th>
              <th scope="col">Tendência</th>
            </tr>
          </thead>
          <tbody>
            {series.map((s) => {
              const v = lerVariacao(s, cob.horasCobertas);
              const ultimo = s.pontos[s.pontos.length - 1];
              return (
                <tr key={s.chave} className="border-b border-line last:border-b-0" data-estado={s.estado}>
                  <th scope="row" className="text-left font-semibold text-ink">
                    {s.titulo}
                    {s.apelido ? (
                      <span className="ml-1.5 text-xs font-normal text-ink-faint">{s.apelido}</span>
                    ) : null}
                  </th>
                  <td className="text-right tabular-nums font-semibold text-ink">{inteiro(s.atual)}</td>
                  <td className="whitespace-nowrap text-right tabular-nums text-ink">
                    {/* T-0021: o " · " so' para o leitor de tela; sem ele, "−12" e
                        "−5,0%" eram lidos colados ("−12−5,0%"). O `sr-only` e'
                        absoluto: o invólucro `relative` (inline, sem mudar o
                        layout) o prende a' celula, nao ao BODY (QA F1). */}
                    <span className="relative">
                      {v.principal}
                      {v.secundario ? (
                        <>
                          <span className="sr-only"> · </span>
                          <span className="ml-1 text-xs text-ink-muted">{v.secundario}</span>
                        </>
                      ) : null}
                    </span>
                  </td>
                  <td className="text-right tabular-nums text-ink">{inteiro(ultimo.over48h)}</td>
                  <td className="text-right tabular-nums text-ink">{inteiro(ultimo.stalled)}</td>
                  <td>
                    <Sparkline serie={s} dominioX={dominioX} />
                    {s.lacunas > 0 ? (
                      <span className="ml-2 text-xs text-ink-faint">{s.lacunas} hora(s) sem ponto</span>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Nome da coluna e, colado nele, o "ⓘ" da classificacao (quando o canal manda). */
function ComAjuda({ ajuda }: { ajuda?: ReactNode }) {
  return ajuda ? <span className="ml-1 inline-flex align-middle normal-case">{ajuda}</span> : null;
}

function TabelaUnica({
  resposta,
  fila,
  rotuloBarra,
  ajudas,
}: {
  resposta: RespostaTendencia | null;
  fila: LinhaDaFila[];
  rotuloBarra: string;
  ajudas: AjudasDaTabela;
}) {
  const series = resposta ? montarEvolucaoPorMarca(resposta.points) : [];
  const dominioX = dominioHorizontal(series);
  const cob = resposta ? coberturaDaEvolucao(resposta) : null;
  const cobertura = cob ? descreverCobertura(cob) : null;
  const periodo = cob ? periodoDaVariacao(cob) : null;
  const comVencidos = fila.some((l) => l.vencidos !== null);
  const comPerto = fila.some((l) => l.pertoDeVencer != null);
  const naFila = new Set(fila.map((l) => l.conta));
  // A fotografia manda nas linhas; conta so' com tendencia (fora da fila
  // agora) entra no fim, com os numeros do ultimo ponto e sem barra.
  const linhas: { fila: LinhaDaFila | null; serie: SerieMarca | null }[] = [
    ...fila.map((l) => ({ fila: l, serie: series.find((s) => s.conta === l.conta) ?? null })),
    ...series.filter((s) => !naFila.has(s.conta)).map((s) => ({ fila: null, serie: s })),
  ];
  if (!linhas.length) return null;

  return (
    <div className="flex flex-col gap-2">
      {cobertura?.desde ? (
        <p className="flex items-center gap-1.5 text-xs text-ink-muted" data-testid="exp-cobertura-real">
          <span>
            <span className="font-medium text-ink">{cobertura.desde}</span> {cobertura.exibindo}
          </span>
          <InfoDica texto={NOTA_DO_EIXO} />
        </p>
      ) : null}
      <div className="overflow-x-auto">
        <table className={CLASSE_TABELA}>
          <caption className="sr-only">
            Backlog por marca — a fila de agora e a tendência do período, uma linha por conta, contas nunca somadas
          </caption>
          <thead className="text-left text-xs uppercase tracking-wide text-ink-faint">
            <tr className="border-b border-line">
              <th scope="col">Marca</th>
              <th scope="col" className="text-right">Backlog</th>
              {comVencidos ? (
                <th scope="col" className="text-right text-crit-ink">
                  Vencidos<ComAjuda ajuda={ajudas.vencidos} />
                </th>
              ) : null}
              {comPerto ? (
                <th scope="col" className="text-right">
                  Perto de vencer<ComAjuda ajuda={ajudas.pertoDeVencer} />
                </th>
              ) : null}
              <th scope="col" className="text-right">
                Acima de 48 h<ComAjuda ajuda={ajudas.acima48h} />
              </th>
              <th scope="col" className="text-right">
                Travados<ComAjuda ajuda={ajudas.travados} />
              </th>
              <th scope="col" className="w-[22%]">{rotuloBarra}</th>
              {/* EXPEDICAO-TRAVADOS-1: variacao e linha falam do MESMO periodo,
                  por isso moram na mesma coluna, com o periodo no cabecalho. */}
              <th scope="col">
                Tendência
                {periodo ? (
                  <span className="block font-normal normal-case tracking-normal">{periodo}</span>
                ) : null}
              </th>
            </tr>
          </thead>
          <tbody>
            {linhas.map(({ fila: f, serie: s }) => {
              const ultimo = s ? s.pontos[s.pontos.length - 1] : null;
              const v = s && cob ? lerVariacao(s, cob.horasCobertas) : null;
              const titulo = s?.titulo ?? f?.titulo ?? "";
              // A fotografia numera TODAS as contas da marca; a serie so' as
              // que tem ponto. Com fotografia, vale a dela (uma numeracao so').
              const apelido = f ? f.apelido : (s?.apelido ?? null);
              return (
                <tr
                  key={f?.conta ?? s?.chave}
                  className="border-b border-line last:border-b-0"
                  data-estado={s?.estado ?? "sem_ponto"}
                >
                  <th scope="row" className="text-left font-semibold text-ink">
                    {titulo}
                    {apelido ? (
                      <span className="ml-1.5 text-xs font-normal text-ink-faint">{apelido}</span>
                    ) : null}
                  </th>
                  <td className="text-right tabular-nums font-semibold text-ink">
                    {inteiro(f ? f.backlog : (s?.atual ?? 0))}
                  </td>
                  {comVencidos ? (
                    <td className="whitespace-nowrap text-right tabular-nums text-ink">
                      {f?.vencidos == null ? (
                        "—"
                      ) : f.vencidos === 0 ? (
                        inteiro(0)
                      ) : (
                        // EXPEDICAO-TABELA-AMANHA: etiqueta vermelha; clicavel,
                        // abre a lista. O % da conta, com o " · " so' para o
                        // leitor de tela.
                        <span className="relative">
                          {f.aoVerVencidos ? (
                            <button
                              type="button"
                              onClick={f.aoVerVencidos}
                              aria-label={`Ver os ${inteiro(f.vencidos)} pedidos vencidos de ${titulo}`}
                              className="-my-3 inline-flex min-h-[44px] items-center rounded-md hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-crit"
                            >
                              <span className={ETIQUETA_VENCIDO}>{inteiro(f.vencidos)}</span>
                            </button>
                          ) : (
                            <span className={ETIQUETA_VENCIDO}>{inteiro(f.vencidos)}</span>
                          )}
                          {f.pctVencidos ? (
                            <>
                              <span className="sr-only"> · </span>
                              <span className="ml-1.5 text-xs font-normal text-ink-faint">{f.pctVencidos}</span>
                            </>
                          ) : null}
                        </span>
                      )}
                    </td>
                  ) : null}
                  {comPerto ? (
                    <td className="whitespace-nowrap text-right tabular-nums text-ink">
                      {f?.pertoDeVencer == null ? (
                        "—"
                      ) : (
                        <span className="relative">
                          {f.aoVerPertoDeVencer && f.pertoDeVencer > 0 ? (
                            <button
                              type="button"
                              onClick={f.aoVerPertoDeVencer}
                              aria-label={`Ver os ${inteiro(f.pertoDeVencer)} pedidos de ${titulo} perto de vencer`}
                              className="-my-3 inline-flex min-h-[44px] items-center font-semibold underline decoration-dotted underline-offset-4 hover:text-accent"
                            >
                              {inteiro(f.pertoDeVencer)}
                            </button>
                          ) : (
                            inteiro(f.pertoDeVencer)
                          )}
                          {f.pctPertoDeVencer && f.pertoDeVencer > 0 ? (
                            <>
                              <span className="sr-only"> · </span>
                              <span className="ml-1 text-xs font-normal text-ink-faint">{f.pctPertoDeVencer}</span>
                            </>
                          ) : null}
                        </span>
                      )}
                    </td>
                  ) : null}
                  <td className="text-right tabular-nums text-ink">
                    {inteiro(f ? f.acima48h : (ultimo?.over48h ?? 0))}
                  </td>
                  <td className="whitespace-nowrap text-right tabular-nums text-ink">
                    {/* T-0021: "12" e "5,3%" eram lidos colados ("125,3%"); o
                        " · " so' para o leitor de tela, preso ao invólucro
                        `relative` (QA F1). */}
                    <span className="relative">
                      {f?.aoVerTravados ? (
                        <button
                          type="button"
                          onClick={f.aoVerTravados}
                          aria-label={`Ver os ${inteiro(f.travados)} pedidos travados de ${titulo}`}
                          className="-my-3 inline-flex min-h-[44px] items-center font-semibold underline decoration-dotted underline-offset-4 hover:text-accent"
                        >
                          {inteiro(f.travados)}
                        </button>
                      ) : (
                        inteiro(f ? f.travados : (ultimo?.stalled ?? 0))
                      )}
                      {f?.pctTravados && f.travados > 0 ? (
                        <>
                          <span className="sr-only"> · </span>
                          <span className="ml-1 text-xs text-ink-faint">{f.pctTravados}</span>
                        </>
                      ) : null}
                    </span>
                  </td>
                  <td>{f?.barra ?? null}</td>
                  <td>
                    {s && dominioX ? (
                      <span className="inline-flex items-center gap-2">
                        <Sparkline serie={s} dominioX={dominioX} />
                        {v ? (
                          // T-0021: o " · " so' para o leitor de tela (sem ele,
                          // "−12" e "−5,0%" eram lidos colados), preso ao
                          // invólucro `relative` (QA F1).
                          <span className="relative whitespace-nowrap text-xs tabular-nums text-ink">
                            {v.principal}
                            {v.secundario ? (
                              <>
                                <span className="sr-only"> · </span>
                                <span className="ml-1 text-ink-muted">{v.secundario}</span>
                              </>
                            ) : null}
                          </span>
                        ) : null}
                        {s.lacunas > 0 ? (
                          <span className="text-xs text-ink-faint">{s.lacunas} hora(s) sem ponto</span>
                        ) : null}
                      </span>
                    ) : (
                      <span className="text-xs text-ink-faint">sem ponto no período</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
