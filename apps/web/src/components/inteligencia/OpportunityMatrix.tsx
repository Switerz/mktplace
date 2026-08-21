"use client";

import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { brandLabel } from "@/lib/inteligencia/brands";
import { decBr, roasBr } from "@/lib/inteligencia/format";
import {
  BAND_KEYS, BAND_META, isSample, labelledPoints, matrixState, plotPoints,
  moedaExata, pointRadius, QUADRANT_KEYS, QUADRANT_META, sampleDeclaration,
  type BandKey, type QuadrantKey,
} from "@/lib/inteligencia/opportunity";
import type { OpportunityHighlight, OpportunityMap } from "@/lib/api-client";

interface Props {
  map: OpportunityMap | null;
  mlScope: readonly string[];
  onOpenQuadrant: (key: QuadrantKey) => void;
  onOpenPoint: (h: OpportunityHighlight) => void;
  onOpenBand: (key: BandKey) => void;
  /** Só no mobile: abre a própria matriz no diálogo (§13, <640). */
  onOpenMatrix?: () => void;
  disabled?: boolean;
  /** Dentro do diálogo o plano é sempre exibido, sem o corte de breakpoint. */
  semBreakpoint?: boolean;
}

const VB = 320; // lado do viewBox; o SVG escala por CSS

/**
 * Bloco 3 — matriz definitiva de oportunidades (Gate V3-1B, §7.3).
 *
 * Consome exclusivamente `opportunity_map`. Não recalcula mediana, referência de
 * ROAS, classificação, agregados, faixas nem seleção de destaques: tudo isso é
 * contrato do BE6. O que existe aqui é desenho.
 *
 * Três coisas ficam deliberadamente separadas na tela, porque são grandezas
 * diferentes: o UNIVERSO (`total_count`), os AGREGADOS de cada quadrante (que
 * cobrem o universo) e os DESTAQUES plotados (`returned_count`). Os pontos nunca
 * são apresentados como "todos os produtos".
 */
export default function OpportunityMatrix({
  map, mlScope, onOpenQuadrant, onOpenPoint, onOpenBand, onOpenMatrix,
  disabled, semBreakpoint,
}: Props) {
  const estado = matrixState(map, mlScope);

  if (estado === "out_of_scope") {
    return (
      <p className="px-6 py-8 text-center text-sm text-slate-500">
        A seleção atual não inclui nenhuma marca do Mercado Livre, e o mapa de
        oportunidades é da fotografia ML. Nenhum dado foi estimado para preencher
        o bloco.
      </p>
    );
  }
  if (!map || estado === "empty") {
    return (
      <p className="px-6 py-8 text-center text-sm text-slate-500">
        Nenhum produto classificado neste escopo. O mapa aparece quando o snapshot
        ML traz produtos com <code>product_status</code> definido.
      </p>
    );
  }

  const quadrantes = new Map(map.quadrants.map((q) => [q.key, q]));
  const faixas = new Map(map.bands.map((b) => [b.key, b]));
  const indisponivel = estado === "unavailable";
  const pontos = indisponivel ? [] : plotPoints(map);
  const rotulados = labelledPoints(pontos);

  return (
    <div className="px-4 sm:px-6 py-5 flex flex-col gap-5">
      {/* ── universo, referências e a separação amostra × universo ───────── */}
      <div className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <p className="text-xs text-slate-500 tabular-nums">
            Universo classificado: <strong className="text-slate-800">{fmtNumber(map.total_count)}</strong>{" "}
            produtos · destaques plotados:{" "}
            <strong className="text-slate-800">{fmtNumber(map.returned_count)}</strong>
          </p>
          <p className="text-xs text-slate-500">
            Referências: ROAS {roasBr(map.roas_reference)} ·{" "}
            {map.gmv_reference == null ? "GMV indisponível" : `GMV ${moedaExata(map.gmv_reference)}`}
          </p>
        </div>
        <p className="text-xs text-slate-500">{sampleDeclaration(map)}</p>
        <p className="text-xs text-slate-400">{map.reference_note}</p>
      </div>

      {indisponivel ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-4">
          <p className="text-sm font-semibold text-amber-900">
            Matriz indisponível: não há GMV positivo neste escopo.
          </p>
          <p className="text-xs text-amber-800 mt-1">
            Sem nenhum produto com venda, não existe eixo de volume — e sem eixo de
            volume não existem quatro quadrantes. Os {fmtNumber(map.unclassified_count)}{" "}
            produtos com investimento e ROAS medido ficam sem classificação em vez de
            serem empurrados para um quadrante inventado. As duas faixas abaixo
            continuam válidas, porque não dependem do eixo de GMV.
          </p>
        </div>
      ) : (
        <>
          {/* ── a matriz: visível a partir de sm; no mobile vai para o diálogo ── */}
          <div className={semBreakpoint ? "block" : "hidden sm:block"}>
            <Plano
              map={map}
              pontos={pontos}
              rotulados={rotulados}
              onOpenPoint={onOpenPoint}
              disabled={disabled}
            />
          </div>

          {/* ── <640: matriz colapsada em diálogo (§13) ─────────────────── */}
          {!semBreakpoint && onOpenMatrix && (
            <button
              type="button"
              onClick={onOpenMatrix}
              disabled={disabled}
              aria-label="Abrir a matriz de oportunidades em tela cheia"
              className="sm:hidden inline-flex items-center justify-center min-h-11 px-4 rounded-xl border border-violet-200 bg-violet-50 text-sm font-semibold text-violet-700 hover:bg-violet-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 disabled:opacity-50"
            >
              Abrir a matriz ({fmtNumber(map.returned_count)} destaques)
            </button>
          )}
        </>
      )}

      {/* ── agregados por quadrante: cobrem o UNIVERSO, não a amostra ───── */}
      <div>
        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
          Agregados por quadrante · universo completo
        </h3>
        <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2 list-none p-0 m-0">
          {QUADRANT_KEYS.map((k) => {
            const q = quadrantes.get(k);
            const meta = QUADRANT_META[k];
            return (
              <li key={k}>
                <button
                  type="button"
                  onClick={() => onOpenQuadrant(k)}
                  disabled={disabled}
                  aria-label={`Detalhe do quadrante ${meta.label}: ${fmtNumber(q?.count ?? 0)} produtos no universo`}
                  className="w-full text-left min-h-11 rounded-xl border border-slate-200 bg-white px-3 py-2.5 hover:border-violet-300 hover:bg-violet-50/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 disabled:pointer-events-none disabled:opacity-50"
                >
                  <span className="flex items-center gap-2 flex-wrap">
                    <span className={`w-2.5 h-2.5 rounded-full inline-block ${meta.dot}`} aria-hidden="true" />
                    <span className="text-sm font-semibold text-slate-800">{meta.label}</span>
                    <span className="text-xs text-slate-500 tabular-nums ml-auto">
                      {fmtNumber(q?.count ?? 0)} produtos
                    </span>
                  </span>
                  <span className="block mt-1 text-xs text-slate-500 tabular-nums">
                    {fmtBrl(q?.gmv ?? 0)} de GMV · {fmtBrl(q?.ad_spend ?? 0)} de Ads ·{" "}
                    {fmtNumber(q?.returned_count ?? 0)} destaque
                    {(q?.returned_count ?? 0) === 1 ? "" : "s"} plotado
                    {(q?.returned_count ?? 0) === 1 ? "" : "s"}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      {/* ── as duas faixas, sempre separadas ─────────────────────────────── */}
      <div>
        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
          Fora dos quadrantes · duas faixas distintas
        </h3>
        <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2 list-none p-0 m-0">
          {BAND_KEYS.map((k) => {
            const b = faixas.get(k);
            const meta = BAND_META[k];
            return (
              <li key={k}>
                <button
                  type="button"
                  onClick={() => onOpenBand(k)}
                  disabled={disabled}
                  aria-label={`Detalhe da faixa ${meta.label}: ${fmtNumber(b?.count ?? 0)} produtos`}
                  className={`w-full text-left min-h-11 rounded-xl border px-3 py-2.5 hover:brightness-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 disabled:pointer-events-none disabled:opacity-50 ${meta.chip}`}
                >
                  <span className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-semibold">{meta.label}</span>
                    <span className="text-xs tabular-nums ml-auto">
                      {fmtNumber(b?.count ?? 0)} produtos
                    </span>
                  </span>
                  <span className="block mt-1 text-xs tabular-nums opacity-90">
                    {fmtBrl(b?.gmv ?? 0)} de GMV · {fmtBrl(b?.ad_spend ?? 0)} de Ads
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// O plano cartesiano
// ---------------------------------------------------------------------------

function Plano({
  map, pontos, rotulados, onOpenPoint, disabled,
}: {
  map: OpportunityMap;
  pontos: ReturnType<typeof plotPoints>;
  rotulados: Set<string>;
  onOpenPoint: (h: OpportunityHighlight) => void;
  disabled?: boolean;
}) {
  return (
    <figure className="m-0">
      <figcaption className="sr-only">
        Matriz de oportunidades: eixo horizontal é o retorno comparado à referência de
        ROAS {roasBr(map.roas_reference)}; eixo vertical é o volume comparado à
        referência de GMV{" "}
        {map.gmv_reference == null ? "indisponível" : moedaExata(map.gmv_reference)}. O
        tamanho do ponto é proporcional ao investimento em Ads. Estão plotados{" "}
        {map.returned_count} destaques de um universo de {map.total_count} produtos.
      </figcaption>

      <div className="relative w-full">
        {/* rótulos dos eixos */}
        <div className="flex justify-between text-xs text-slate-500 mb-1">
          <span>← retorno abaixo da referência</span>
          <span>retorno acima →</span>
        </div>

        <div className="flex gap-2">
          <div className="flex flex-col justify-between text-xs text-slate-500 shrink-0 w-6">
            <span className="[writing-mode:vertical-rl] rotate-180 leading-none">volume ↑</span>
            <span className="[writing-mode:vertical-rl] rotate-180 leading-none">↓ volume</span>
          </div>

          <svg
            viewBox={`0 0 ${VB} ${VB}`}
            role="img"
            aria-label={`Matriz de oportunidades com ${map.returned_count} destaques plotados`}
            className="w-full h-auto rounded-xl border border-slate-200 bg-slate-50/60"
          >
            {/* fundo por quadrante, cor consistente com a legenda */}
            {QUADRANT_KEYS.map((k) => {
              const m = QUADRANT_META[k];
              const x = m.roasHigh ? VB / 2 : 0;
              const y = m.gmvHigh ? 0 : VB / 2;
              return (
                <rect
                  key={k} x={x} y={y} width={VB / 2} height={VB / 2}
                  className={`${m.dot} opacity-[0.06]`} fill="currentColor" aria-hidden="true"
                />
              );
            })}
            {/* as duas linhas de referência */}
            <line x1={VB / 2} y1={0} x2={VB / 2} y2={VB} stroke="currentColor"
              className="text-slate-300" strokeWidth={1} strokeDasharray="4 4" aria-hidden="true" />
            <line x1={0} y1={VB / 2} x2={VB} y2={VB / 2} stroke="currentColor"
              className="text-slate-300" strokeWidth={1} strokeDasharray="4 4" aria-hidden="true" />

            {/* nome de cada quadrante no canto correspondente */}
            {QUADRANT_KEYS.map((k) => {
              const m = QUADRANT_META[k];
              return (
                <text
                  key={`t-${k}`}
                  x={m.roasHigh ? VB - 6 : 6}
                  y={m.gmvHigh ? 14 : VB - 6}
                  textAnchor={m.roasHigh ? "end" : "start"}
                  className="fill-slate-400"
                  style={{ fontSize: 12 }}
                  aria-hidden="true"
                >
                  {m.label}
                </text>
              );
            })}

            {pontos.map((p) => {
              const cx = p.x * VB;
              const cy = (1 - p.y) * VB;
              const r = pointRadius(p.weight);
              const h = p.highlight;
              const meta = QUADRANT_META[p.quadrant];
              const nome = `${h.title ?? h.item_id} · ${brandLabel(h.brand)} · ${meta.label} · GMV ${fmtBrl(h.gmv)} · Ads ${fmtBrl(h.ad_spend)} · ROAS ${h.ad_roas == null ? "indisponível" : roasBr(h.ad_roas)}`;
              return (
                <g key={`${h.brand}:${h.item_id}`}>
                  <circle cx={cx} cy={cy} r={r} className={`${meta.dot} opacity-80`}
                    fill="currentColor" aria-hidden="true" />
                  {rotulados.has(h.item_id) && (
                    <text x={cx} y={cy - r - 4} textAnchor="middle" className="fill-slate-600"
                      style={{ fontSize: 12 }} aria-hidden="true">
                      {(h.title ?? h.item_id).slice(0, 14)}
                    </text>
                  )}
                  {/* alvo de toque de 44px, invisível e concêntrico ao ponto */}
                  <circle
                    cx={cx} cy={cy} r={22} fill="transparent"
                    role="button" tabIndex={disabled ? -1 : 0}
                    aria-label={`Detalhe do destaque ${nome}`}
                    className="cursor-pointer focus-visible:outline-none focus-visible:stroke-violet-600 [stroke-width:2]"
                    onClick={() => !disabled && onOpenPoint(h)}
                    onKeyDown={(e) => {
                      if (disabled) return;
                      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpenPoint(h); }
                    }}
                  />
                </g>
              );
            })}
          </svg>
        </div>
      </div>

      {/* legenda textual: a cor nunca carrega a informação sozinha */}
      <ul className="flex gap-x-4 gap-y-1 mt-2 flex-wrap list-none p-0 m-0">
        {QUADRANT_KEYS.map((k) => (
          <li key={k} className="flex items-center gap-1.5 text-xs text-slate-500">
            <span className={`w-2.5 h-2.5 rounded-full inline-block ${QUADRANT_META[k].dot}`} aria-hidden="true" />
            <span>{QUADRANT_META[k].label}</span>
          </li>
        ))}
        <li className="text-xs text-slate-400">
          tamanho do ponto = investimento em Ads
        </li>
      </ul>
      {isSample(map) && (
        <p className="text-xs text-slate-500 mt-1">
          Cada quadrante plota no máximo {map.highlight_limit_per_quadrant} destaques;
          os agregados acima cobrem o universo inteiro.
        </p>
      )}
    </figure>
  );
}
