"use client";

/**
 * Gate EXPEDICAO-UX-2 — evolucao do backlog em small multiples, um cartao por
 * conta, titulado pela MARCA.
 *
 * Substitui o grafico unico (recharts, um eixo de 0 a 600 para todas as
 * contas, legenda com `seller_id`). Aqui:
 *
 *   - cada cartao tem a PROPRIA escala vertical, escrita no cartao, para a
 *     conta pequena nao virar linha plana;
 *   - o eixo do tempo e' REAL e comum a todos: pontos distantes ficam
 *     distantes, e hora sem ponto quebra a linha (nunca vira zero);
 *   - o identificador da conta so' aparece no tooltip;
 *   - o tooltip le o que ja' esta' na resposta: nenhuma chamada por hover.
 *
 * SVG proprio, sem recharts: sao poucos pontos por cartao, e o bloco deixa de
 * pagar a biblioteca inteira. Teclado: cada cartao e' UM ponto de tabulacao;
 * setas andam entre os pontos e o tooltip anuncia o ponto. A tabela "Ver
 * dados" do painel continua sendo a leitura completa para leitor de tela.
 */
import { useState, type KeyboardEvent, type PointerEvent } from "react";

import Segmented from "@/components/ui/Segmented";
import {
  ESCALAS,
  coberturaDaEvolucao,
  descreverCobertura,
  dominioHorizontal,
  dominioVertical,
  formatarHoraBrt,
  formatarVariacao,
  montarEvolucaoPorMarca,
  valorPlotado,
  type EscalaEvolucao,
  type SerieMarca,
} from "@/lib/expedicao-evolucao";
import type { RespostaTendencia } from "@/lib/expedicao-contract";

const L = 320;
const A = 96;
const PAD_X = 10;
const PAD_Y = 10;

function inteiro(n: number): string {
  return n.toLocaleString("pt-BR");
}

function rotuloEixo(v: number, escala: EscalaEvolucao): string {
  if (escala === "variacao") {
    const r = Math.round(v);
    return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r)}%`;
  }
  return inteiro(Math.round(v));
}

function Cartao({
  serie,
  series,
  escala,
  dominioX,
}: {
  serie: SerieMarca;
  series: SerieMarca[];
  escala: EscalaEvolucao;
  dominioX: [number, number];
}) {
  const [ativo, setAtivo] = useState<number | null>(null);
  const dominioY = dominioVertical(series, escala, serie);
  const [x0, x1] = dominioX;
  const x = (t: number) => (x1 === x0 ? L / 2 : PAD_X + ((t - x0) / (x1 - x0)) * (L - 2 * PAD_X));
  const y = (v: number) =>
    !dominioY ? A / 2 : A - PAD_Y - ((v - dominioY[0]) / (dominioY[1] - dominioY[0])) * (A - 2 * PAD_Y);

  const plotavel = serie.pontos.some((p) => valorPlotado(serie, p, escala) !== null);
  const variacao = formatarVariacao(serie);
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
    if (!n) return;
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

  const resumo =
    serie.estado === "insuficiente"
      ? "histórico insuficiente: um ponto só"
      : serie.estado === "sem_variacao"
        ? "sem variação no período"
        : `variação ${variacao}`;

  return (
    <li className="flex min-w-0 flex-col rounded-lg bg-raised p-3">
      <div className="flex items-baseline justify-between gap-2">
        <h4 className="min-w-0 truncate text-sm font-semibold text-ink">{serie.titulo}</h4>
        {serie.apelido ? (
          <span className="shrink-0 text-xs text-ink-faint">{serie.apelido}</span>
        ) : null}
      </div>
      <p className="mt-1 flex flex-wrap items-baseline gap-x-2">
        <span className="text-xl font-semibold tabular-nums text-ink">{inteiro(serie.atual)}</span>
        <span className="text-xs text-ink-muted">
          no último ponto · {formatarHoraBrt(serie.ultimo, false)}
        </span>
      </p>
      <p className="mt-0.5 text-xs text-ink-muted" data-estado={serie.estado}>
        {serie.estado === "insuficiente" ? (
          <span className="font-medium text-ink">Histórico insuficiente — um ponto só</span>
        ) : serie.estado === "sem_variacao" ? (
          <span className="font-medium text-ink">Sem variação no período</span>
        ) : (
          <>
            <span className="font-medium tabular-nums text-ink">{variacao}</span> no período
          </>
        )}
      </p>

      <div
        className="relative mt-2 rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        tabIndex={0}
        role="group"
        aria-label={`${serie.titulo}${serie.apelido ? `, ${serie.apelido}` : ""}: backlog de ${formatarHoraBrt(
          serie.primeiro,
        )} a ${formatarHoraBrt(serie.ultimo)}, ${resumo}. Use as setas para percorrer os pontos.`}
        onKeyDown={aoTeclar}
        onFocus={() => setAtivo((v) => v ?? serie.pontos.length - 1)}
        onBlur={() => setAtivo(null)}
      >
        {plotavel ? (
          <svg
            viewBox={`0 0 ${L} ${A}`}
            className="h-24 w-full touch-pan-y overflow-visible"
            aria-hidden="true"
            onPointerMove={aoMover}
            onPointerDown={aoMover}
            onPointerLeave={() => setAtivo(null)}
          >
            {escala === "variacao" && dominioY ? (
              <line
                x1={0}
                x2={L}
                y1={y(0)}
                y2={y(0)}
                className="stroke-line-strong"
                strokeDasharray="3 3"
                vectorEffect="non-scaling-stroke"
              />
            ) : null}
            {serie.trechos.map((trecho, i) => {
              const d = trecho
                .map((p) => valorPlotado(serie, p, escala))
                .map((v, j) => (v === null ? null : `${x(trecho[j].t)},${y(v)}`))
                .filter((s): s is string => s !== null);
              return d.length > 1 ? (
                <polyline
                  key={i}
                  points={d.join(" ")}
                  fill="none"
                  className="stroke-accent"
                  strokeWidth={2}
                  strokeLinejoin="round"
                  vectorEffect="non-scaling-stroke"
                />
              ) : null;
            })}
            {serie.pontos.map((p, i) => {
              const v = valorPlotado(serie, p, escala);
              if (v === null) return null;
              return (
                <circle
                  key={p.t}
                  cx={x(p.t)}
                  cy={y(v)}
                  r={ativo === i ? 4.5 : 3}
                  className={ativo === i ? "fill-accent stroke-raised" : "fill-accent"}
                  strokeWidth={ativo === i ? 2 : 0}
                />
              );
            })}
            {ponto ? (
              <line
                x1={x(ponto.t)}
                x2={x(ponto.t)}
                y1={0}
                y2={A}
                className="stroke-line-strong"
                vectorEffect="non-scaling-stroke"
              />
            ) : null}
          </svg>
        ) : (
          <p className="flex h-24 items-center justify-center rounded-md border border-dashed border-line text-center text-xs text-ink-faint">
            Variação % indisponível: o primeiro ponto tem backlog zero.
          </p>
        )}

        {/* Escala ESCRITA: as alturas nao se comparam entre cartoes no modo
            absoluto, e o operador precisa saber de que faixa e' a curva. */}
        {/* As pontas do eixo sao as do DOMINIO COMUM (primeiro e ultimo ponto
            do canal), nao as da conta: uma conta que so' tem o ultimo
            horario aparece como um ponto na ponta direita de 10:00-14:00. */}
        <div className="mt-1 flex justify-between gap-2 whitespace-nowrap text-xs tabular-nums text-ink-faint">
          <span>{formatarHoraBrt(new Date(x0).toISOString(), false)}</span>
          <span>{formatarHoraBrt(new Date(x1).toISOString(), false)}</span>
        </div>
        {dominioY && plotavel ? (
          <p className="text-xs tabular-nums text-ink-faint">
            {escala === "variacao" ? "Escala comum" : "Escala do cartão"}:{" "}
            {rotuloEixo(dominioY[0], escala)} a {rotuloEixo(dominioY[1], escala)}
          </p>
        ) : null}

        {ponto ? (
          <div
            role="status"
            aria-live="polite"
            className="pointer-events-none absolute -top-2 z-10 w-56 max-w-full -translate-y-full rounded-md border border-line bg-surface px-2.5 py-2 text-xs shadow-lg"
            style={{ left: `clamp(0px, calc(${(x(ponto.t) / L) * 100}% - 7rem), calc(100% - 14rem))` }}
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

      {serie.lacunas > 0 ? (
        <p className="mt-1 text-xs text-ink-faint">
          {serie.lacunas} hora(s) sem ponto desta conta — a linha se interrompe ali.
        </p>
      ) : null}
    </li>
  );
}

export default function EvolucaoPorMarca({ resposta }: { resposta: RespostaTendencia }) {
  const [escala, setEscala] = useState<EscalaEvolucao>("absoluto");
  const series = montarEvolucaoPorMarca(resposta.points);
  const dominioX = dominioHorizontal(series);
  const cobertura = descreverCobertura(coberturaDaEvolucao(resposta));
  if (!dominioX || !series.length) return null;

  return (
    <div className="flex flex-col gap-2">
      {cobertura.desde ? (
        <p className="text-xs text-ink-muted" data-testid="exp-cobertura-real">
          <span className="font-medium text-ink">{cobertura.desde}</span> {cobertura.exibindo}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <span id="exp-escala" className="text-xs text-ink-faint">
          Escala
        </span>
        <Segmented
          rotulo="Escala da evolução"
          rotuladoPor="exp-escala"
          opcoes={ESCALAS}
          valor={escala}
          aoEscolher={setEscala}
        />
        <span className="text-xs text-ink-faint">
          {escala === "absoluto"
            ? "Cada cartão tem a própria escala: compare a forma, não a altura."
            : "Mesma escala para todos: variação sobre o primeiro ponto de cada conta."}
        </span>
      </div>
      <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {series.map((s) => (
          <Cartao key={s.chave} serie={s} series={series} escala={escala} dominioX={dominioX} />
        ))}
      </ul>
    </div>
  );
}
