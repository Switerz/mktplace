"use client";

import { useMemo, useState } from "react";
import type { RegiaoUfRow } from "@/lib/api-client";
import { BRAZIL_UF_GRID, computeGmvIntensity, intensityToColor, textColorForIntensity, coverageGlyph } from "@/lib/regioes-map";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { fmtPctOrNA, coverageLabel, coverageBadgeClass, fmtShareOfTotalPct } from "@/lib/regioes-format";

interface Props {
  /** Linhas de /regioes/by-uf — pode incluir "XX", o componente a exclui da
   * grade e mostra separadamente. */
  rows: RegiaoUfRow[];
  /** summary.gmv — denominador da participação exibida no painel lateral. */
  totalGmv: number;
  loading?: boolean;
}

const CELL_PX = 40;
const GAP_PX = 4;

export default function RegioesBrazilMap({ rows, totalGmv, loading }: Props) {
  // Dois estados independentes, de propósito: `hoverUf` e' um preview
  // efêmero (mouse/teclado passando por cima), `selectedUf` e' a seleção
  // que persiste após o mouse sair (clique/Enter). Um único estado
  // compartilhado fazia o clique comparar contra o valor que o próprio
  // onMouseEnter tinha acabado de setar — clicar numa UF em hover sempre
  // "desmarcava" em vez de fixar a seleção (bug encontrado em QA visual).
  const [hoverUf, setHoverUf] = useState<string | null>(null);
  const [selectedUf, setSelectedUf] = useState<string | null>(null);
  const displayedUf = hoverUf ?? selectedUf;

  const byUf = useMemo(() => {
    const m = new Map<string, RegiaoUfRow>();
    for (const r of rows) m.set(r.uf, r);
    return m;
  }, [rows]);

  const intensity = useMemo(
    () => computeGmvIntensity(rows.map((r) => ({ uf: r.uf, gmv: r.gmv }))),
    [rows],
  );

  const maxCol = Math.max(...BRAZIL_UF_GRID.map((p) => p.col));
  const maxRow = Math.max(...BRAZIL_UF_GRID.map((p) => p.row));

  const xx = byUf.get("XX") ?? null;
  const hasAnyData = rows.some((r) => r.uf !== "XX" && r.gmv > 0);
  const active = displayedUf ? byUf.get(displayedUf) ?? null : null;

  return (
    <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
      <div className="px-6 py-4 border-b border-violet-50">
        <h2 className="text-sm font-semibold text-slate-700">Mapa do Brasil por UF</h2>
        <p className="text-xs text-slate-500 mt-0.5">
          Cor = intensidade de GMV relativa ao maior estado do período (cartograma em grade simplificado — posições aproximadas por região, não é uma projeção geográfica precisa).
        </p>
      </div>

      <div
        className={`p-4 flex flex-col md:flex-row gap-4 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}
        aria-busy={loading}
      >
        <div className="overflow-x-auto">
          <div
            role="group"
            aria-label="Mapa do Brasil por UF, cartograma em grade colorido por intensidade de GMV"
            className="grid"
            style={{
              gridTemplateColumns: `repeat(${maxCol + 1}, ${CELL_PX}px)`,
              gridTemplateRows: `repeat(${maxRow + 1}, ${CELL_PX}px)`,
              gap: `${GAP_PX}px`,
              minWidth: (maxCol + 1) * (CELL_PX + GAP_PX),
            }}
          >
            {BRAZIL_UF_GRID.map((pos) => {
              const row = byUf.get(pos.uf);
              const t = intensity.get(pos.uf) ?? 0;
              const bg = intensityToColor(t);
              const fg = textColorForIntensity(t);
              const level = row?.coverage_level ?? "not_applicable";
              const glyph = coverageGlyph(level);
              const gmv = row?.gmv ?? 0;
              const orders = row?.orders ?? 0;
              const share = fmtShareOfTotalPct(gmv, totalGmv);
              const isSelected = selectedUf === pos.uf;
              const warnBorder = level === "low"
                ? "border-2 border-dashed border-rose-500"
                : level === "partial"
                ? "border-2 border-dashed border-amber-500"
                : "border border-transparent";

              return (
                <button
                  key={pos.uf}
                  type="button"
                  style={{ gridColumn: pos.col + 1, gridRow: pos.row + 1, backgroundColor: bg, color: fg }}
                  className={`rounded-md text-[11px] font-bold flex flex-col items-center justify-center leading-none transition-transform hover:scale-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-600 focus-visible:ring-offset-1 ${warnBorder} ${isSelected ? "ring-2 ring-violet-700 ring-offset-1 scale-105" : ""}`}
                  aria-pressed={isSelected}
                  aria-label={`${pos.uf}: GMV ${fmtBrl(gmv)}, ${fmtNumber(orders)} pedidos${share != null ? `, ${share.toFixed(1)}% de participação` : ""}. ${coverageLabel(level)}.`}
                  onMouseEnter={() => setHoverUf(pos.uf)}
                  onMouseLeave={() => setHoverUf((cur) => (cur === pos.uf ? null : cur))}
                  onFocus={() => setHoverUf(pos.uf)}
                  onBlur={() => setHoverUf((cur) => (cur === pos.uf ? null : cur))}
                  onClick={() => setSelectedUf((prev) => (prev === pos.uf ? null : pos.uf))}
                >
                  <span>{pos.uf}</span>
                  {glyph && <span aria-hidden="true" className="text-[9px]">{glyph}</span>}
                </button>
              );
            })}
          </div>
          {!hasAnyData && !loading && (
            <p className="text-xs text-slate-400 mt-3 max-w-xs">
              Sem dados de GMV por UF para o período e filtros selecionados.
            </p>
          )}
        </div>

        {/* Painel lateral — detalhe da UF em foco/hover/seleção */}
        <div className="flex-1 min-w-[220px] bg-slate-50 rounded-xl p-4 flex flex-col gap-2">
          {active ? (
            <>
              <p className="text-sm font-bold text-slate-700">{active.uf}</p>
              <dl className="text-xs text-slate-600 flex flex-col gap-1.5">
                <div className="flex justify-between gap-2">
                  <dt>GMV</dt>
                  <dd className="font-semibold tabular-nums">{fmtBrl(active.gmv)}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt>Pedidos</dt>
                  <dd className="font-semibold tabular-nums">{fmtNumber(active.orders)}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt>Participação</dt>
                  <dd className="font-semibold tabular-nums">
                    {(() => {
                      const s = fmtShareOfTotalPct(active.gmv, totalGmv);
                      return s != null ? `${s.toFixed(1)}%` : "N/A";
                    })()}
                  </dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt>Cobertura UF</dt>
                  <dd className="font-semibold tabular-nums">{fmtPctOrNA(active.uf_fill_pct)}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt>Cobertura Frete</dt>
                  <dd className="font-semibold tabular-nums">{fmtPctOrNA(active.shipping_cost_coverage_pct)}</dd>
                </div>
                <div className="flex justify-between items-center gap-2">
                  <dt>Nível</dt>
                  <dd>
                    <span className={`inline-block text-[10px] font-semibold uppercase tracking-wide border rounded-full px-2 py-0.5 ${coverageBadgeClass(active.coverage_level)}`}>
                      {coverageLabel(active.coverage_level)}
                    </span>
                  </dd>
                </div>
              </dl>
            </>
          ) : (
            <p className="text-xs text-slate-400">
              Passe o mouse, foque (Tab) ou clique numa UF no mapa para ver os detalhes.
            </p>
          )}

          {xx && (
            <div className="mt-2 pt-2 border-t border-slate-200">
              <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide mb-1">
                UF desconhecida (XX) — não entra no mapa
              </p>
              <p className="text-xs text-slate-500">
                {fmtBrl(xx.gmv)} · {fmtNumber(xx.orders)} pedidos sem UF identificada no período/filtros selecionados.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
