"use client";

import { useMemo, useState } from "react";
import type { RegiaoUfRow } from "@/lib/api-client";
import { BRAZIL_UF_PATHS, BRAZIL_MAP_VIEWBOX, UF_NAME } from "@/lib/brazil-uf-paths";
import { computeGmvIntensity, intensityToColor, coverageStrokeColor, topUfsByGmv } from "@/lib/regioes-map";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { fmtPctOrNA, coverageLabel, coverageBadgeClass, fmtShareOfTotalPct } from "@/lib/regioes-format";

interface Props {
  /** Linhas de /regioes/by-uf — pode incluir "XX", o componente a exclui do
   * mapa e mostra separadamente, em uma faixa discreta abaixo. */
  rows: RegiaoUfRow[];
  /** summary.gmv — denominador da participação exibida no painel de detalhe. */
  totalGmv: number;
  loading?: boolean;
}

const NEUTRAL_STROKE = "#cbd5e1"; // slate-300 — contorno padrão entre estados
const TOP_N = 3;

function StatRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 lg:justify-between">
      <dt className="text-[11px] text-slate-500 whitespace-nowrap">{label}</dt>
      <dd className="text-xs font-semibold tabular-nums text-slate-700 whitespace-nowrap">{value}</dd>
    </div>
  );
}

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

  const top3 = useMemo(() => topUfsByGmv(rows, TOP_N), [rows]);

  const xx = byUf.get("XX") ?? null;
  const hasAnyData = rows.some((r) => r.uf !== "XX" && r.gmv > 0);
  const active = displayedUf ? byUf.get(displayedUf) ?? null : null;
  const activeName = displayedUf ? UF_NAME[displayedUf] : null;

  return (
    <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
      <div className="px-6 py-4 border-b border-violet-50 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-700">Mapa regional</h2>
          <p className="text-xs text-slate-500 mt-0.5">Onde a marca vende mais — intensidade de cor por GMV, UF a UF</p>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-slate-500 shrink-0">
          <span className="font-semibold uppercase tracking-wide text-slate-400">GMV</span>
          <span className="w-16 h-2.5 rounded-full" style={{ background: "linear-gradient(to right, rgb(238,242,247), rgb(91,33,182))" }} aria-hidden="true" />
          <span>menor</span>
          <span className="text-slate-300">→</span>
          <span>maior</span>
        </div>
      </div>

      <div
        className={`flex flex-col lg:flex-row gap-4 p-4 lg:p-6 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}
        aria-busy={loading}
      >
        <div className="flex-1 min-w-0 flex flex-col items-center justify-center gap-2">
          <svg
            viewBox={BRAZIL_MAP_VIEWBOX}
            className="w-full h-auto max-h-[440px]"
            role="img"
            aria-label="Mapa do Brasil, estados coloridos por intensidade de GMV no período e filtros selecionados"
          >
            {BRAZIL_UF_PATHS.map(({ uf, name, path }) => {
              const row = byUf.get(uf);
              const t = intensity.get(uf) ?? 0;
              const fill = intensityToColor(t);
              const level = row?.coverage_level ?? "not_applicable";
              const warnStroke = coverageStrokeColor(level);
              const isSelected = selectedUf === uf;
              const isActive = displayedUf === uf;
              const gmv = row?.gmv ?? 0;
              const orders = row?.orders ?? 0;
              const share = fmtShareOfTotalPct(gmv, totalGmv);

              return (
                <path
                  key={uf}
                  d={path}
                  fill={fill}
                  stroke={isActive ? "#5b21b6" : warnStroke ?? NEUTRAL_STROKE}
                  strokeWidth={isActive ? 2.5 : warnStroke ? 1.4 : 0.75}
                  strokeDasharray={!isActive && level === "low" ? "3,2" : undefined}
                  strokeLinejoin="round"
                  role="button"
                  tabIndex={0}
                  aria-pressed={isSelected}
                  aria-label={`${name} (${uf}): GMV ${fmtBrl(gmv)}, ${fmtNumber(orders)} pedidos${share != null ? `, ${share.toFixed(1)}% de participação` : ""}. ${coverageLabel(level)}.`}
                  className="cursor-pointer outline-none focus-visible:brightness-95 transition-[filter] duration-100"
                  style={{ filter: isActive ? "brightness(1.05)" : undefined }}
                  onMouseEnter={() => setHoverUf(uf)}
                  onMouseLeave={() => setHoverUf((cur) => (cur === uf ? null : cur))}
                  onFocus={() => setHoverUf(uf)}
                  onBlur={() => setHoverUf((cur) => (cur === uf ? null : cur))}
                  onClick={() => setSelectedUf((prev) => (prev === uf ? null : uf))}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelectedUf((prev) => (prev === uf ? null : uf));
                    }
                  }}
                >
                  <title>{`${name} (${uf})`}</title>
                </path>
              );
            })}
          </svg>
          {!hasAnyData && !loading && (
            <p className="text-xs text-slate-400 max-w-xs text-center">
              Sem dados de GMV por UF para o período e filtros selecionados.
            </p>
          )}
        </div>

        {/* Painel de detalhe — compacto, nunca disputa espaço com o mapa */}
        <div className="w-full lg:w-64 shrink-0 bg-slate-50 rounded-xl p-3.5 flex flex-col gap-2.5 self-start">
          {active ? (
            <>
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-bold text-slate-700 leading-none">{activeName ?? active.uf} <span className="text-slate-400 font-semibold">· {active.uf}</span></p>
                <span className={`inline-block text-[9px] font-semibold uppercase tracking-wide border rounded-full px-1.5 py-0.5 whitespace-nowrap ${coverageBadgeClass(active.coverage_level)}`}>
                  {coverageLabel(active.coverage_level)}
                </span>
              </div>
              <dl className="flex flex-wrap lg:flex-col gap-x-4 gap-y-1.5 lg:gap-1.5">
                <StatRow label="GMV" value={fmtBrl(active.gmv)} />
                <StatRow label="Pedidos" value={fmtNumber(active.orders)} />
                <StatRow
                  label="Participação"
                  value={(() => {
                    const s = fmtShareOfTotalPct(active.gmv, totalGmv);
                    return s != null ? `${s.toFixed(1)}%` : "N/A";
                  })()}
                />
                <StatRow label="Cobertura UF" value={fmtPctOrNA(active.uf_fill_pct)} />
                <StatRow label="Cobertura Frete" value={fmtPctOrNA(active.shipping_cost_coverage_pct)} />
              </dl>
            </>
          ) : (
            <>
              <p className="text-xs text-slate-400">
                Passe o mouse, foque (Tab) ou clique numa UF no mapa para ver os detalhes.
              </p>
              {top3.length > 0 && (
                <div className="pt-2 border-t border-slate-200 flex flex-col gap-1.5">
                  <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide">Top {top3.length} UFs por GMV</p>
                  {top3.map((row, i) => {
                    const share = fmtShareOfTotalPct(row.gmv, totalGmv);
                    return (
                      <div key={row.uf} className="flex items-center justify-between gap-2 text-xs">
                        <span className="font-semibold text-slate-600">{i + 1}. {row.uf}</span>
                        <span className="tabular-nums text-slate-500">
                          {fmtBrl(row.gmv)}{share != null && ` · ${share.toFixed(1)}%`}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {xx && (
        <div className="px-6 py-2.5 border-t border-slate-100 bg-slate-50/60">
          <p className="text-[11px] text-slate-500">
            <span className="font-semibold text-slate-600">UF desconhecida (XX)</span> — não entra no mapa: {fmtBrl(xx.gmv)} · {fmtNumber(xx.orders)} pedidos sem UF identificada no período/filtros selecionados.
          </p>
        </div>
      )}

      {/* Atribuicao obrigatoria da geometria do mapa (CC BY 4.0) — ver
         header de brazil-uf-paths.ts e THIRD_PARTY_NOTICES.md */}
      <div className="px-6 py-1.5 border-t border-slate-50">
        <p className="text-[10px] text-slate-300 text-right">
          Mapa base:{" "}
          <a
            href="https://github.com/VictorCazanave/svg-maps"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-slate-400 underline decoration-dotted"
          >
            @svg-maps/brazil
          </a>{" "}
          (CC BY 4.0)
        </p>
      </div>
    </div>
  );
}
