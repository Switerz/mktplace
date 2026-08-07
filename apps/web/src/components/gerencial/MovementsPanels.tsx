"use client";

/**
 * Movimentos e Concentracao por marca (Gate V2-1, Task I).
 *
 * Tres paineis de alturas coordenadas (`h-full` dentro de uma grade sem
 * `items-start`; listas com `max-h` e rolagem interna).
 *
 * A terceira coluna e' **Concentracao por marca**, nao ranking de produtos:
 * produtos nao tem escopo temporal uniforme entre canais (ML e' acumulado
 * atual; TikTok e Shopee sao competencia mensal), entao um Top Produtos sob o
 * periodo global compararia tres janelas diferentes sob o mesmo rotulo.
 */
import type {
  Concentration,
  ConcentrationEntry,
  Movement,
  Movements,
} from "@/lib/gerencial/brand-matrix";
import { MOVEMENT_FLOOR_NOTE } from "@/lib/gerencial/brand-matrix";
import { CHANNEL_LABEL } from "@/lib/gerencial/kpi-band";
import { fmtBrlFull } from "@/lib/formatters";
import { CHANNEL_STROKE } from "@/lib/gerencial/channel-colors";

interface Props {
  movements: Movements;
  concentration: Concentration;
  loading: boolean;
  error: boolean;
  onOpenMovement: (movement: Movement) => void;
  /** Task B.3 — clicar numa marca abre a explicacao ANTES de navegar. */
  onOpenConcentration: (entry: ConcentrationEntry) => void;
  produtosHref: string;
  onRetry: () => void;
}

const PANEL = "h-full bg-white border border-violet-100 rounded-2xl shadow-sm p-4 flex flex-col gap-2";

function PanelSkeleton() {
  return (
    <div className={`${PANEL} animate-pulse`} role="status" aria-busy="true">
      <span className="sr-only">Carregando…</span>
      <div className="h-3 w-28 bg-violet-100 rounded" />
      {Array.from({ length: 3 }, (_, i) => (
        <div key={i} className="h-7 w-full bg-violet-50 rounded" />
      ))}
    </div>
  );
}

function MovementList({
  title,
  items,
  emptyLabel,
  floorNote,
  onOpenMovement,
}: {
  title: string;
  items: Movement[];
  emptyLabel: string;
  floorNote: boolean;
  onOpenMovement: (movement: Movement) => void;
}) {
  const headingId = `mov-${title.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <section aria-labelledby={headingId} className={PANEL}>
      <h3 id={headingId} className="text-sm font-semibold text-slate-700">
        {title}
      </h3>
      {items.length === 0 ? (
        <p className="text-xs text-slate-500">{emptyLabel}</p>
      ) : (
        <ul className="flex flex-col gap-1 max-h-64 overflow-y-auto pr-1">
          {items.map((m) => {
            const up = m.deltaAbs > 0;
            return (
              <li key={`${m.brand}:${m.channel}`}>
                <button
                  type="button"
                  onClick={() => onOpenMovement(m)}
                  aria-haspopup="dialog"
                  aria-label={`${m.brandLabel} em ${CHANNEL_LABEL[m.channel]} — abrir detalhe da variação`}
                  className="w-full min-h-11 text-left rounded-lg px-2 -mx-2 py-1 hover:bg-violet-50/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 transition-colors"
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 min-w-0">
                      <span
                        aria-hidden="true"
                        className="w-1.5 h-1.5 rounded-full shrink-0"
                        style={{ background: CHANNEL_STROKE[m.channel] }}
                      />
                      <span className="text-xs font-medium text-slate-700 truncate">{m.brandLabel}</span>
                    </span>
                    <span
                      className={`text-xs font-semibold tabular-nums shrink-0 ${
                        up ? "text-emerald-600" : "text-rose-600"
                      }`}
                    >
                      {up ? "+" : "−"}
                      {fmtBrlFull(Math.abs(m.deltaAbs))}
                    </span>
                  </span>
                  <span className="flex items-center justify-between gap-2 text-xs text-slate-500 tabular-nums">
                    <span>{CHANNEL_LABEL[m.channel]}</span>
                    {/* Percentual e' CONTEXTO; a ordenacao usa o absoluto. */}
                    <span>
                      {m.deltaPct != null ? `${up ? "▲" : "▼"} ${Math.abs(m.deltaPct).toFixed(1)}%` : "% n/d"} · antes{" "}
                      {fmtBrlFull(m.previous)}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {floorNote && <p className="mt-auto pt-1 text-xs text-slate-500">{MOVEMENT_FLOOR_NOTE}</p>}
    </section>
  );
}

function ConcentrationPanel({
  concentration,
  onOpenConcentration,
}: {
  concentration: Concentration;
  onOpenConcentration: (entry: ConcentrationEntry) => void;
}) {
  const top = concentration.entries.slice(0, 5);
  return (
    <section aria-labelledby="concentracao-heading" className={PANEL}>
      <h3 id="concentracao-heading" className="text-sm font-semibold text-slate-700">
        Concentração por marca
      </h3>

      {top.length === 0 ? (
        <p className="text-xs text-slate-500">Sem marcas com GMV positivo no período.</p>
      ) : (
        <>
          <ul className="flex flex-col gap-1.5 max-h-56 overflow-y-auto pr-1">
            {top.map((entry) => (
              <li key={entry.brand}>
                {/* Explicacao primeiro: o clique abre o drill-down, nunca navega
                    direto — mesma regra dos demais blocos do V2. */}
                <button
                  type="button"
                  onClick={() => onOpenConcentration(entry)}
                  aria-haspopup="dialog"
                  aria-label={`${entry.label} — abrir explicação da concentração`}
                  className="w-full min-w-0 min-h-11 text-left rounded-lg px-2 -mx-2 py-1 hover:bg-violet-50/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 transition-colors"
                >
                  <span className="flex items-center justify-between gap-2 text-xs">
                    <span className="font-medium text-slate-700 truncate">{entry.label}</span>
                    <span className="tabular-nums text-slate-600 shrink-0">
                      {fmtBrlFull(entry.gmv)} <span className="text-slate-500">· {entry.sharePct.toFixed(1)}%</span>
                    </span>
                  </span>
                  <span className="mt-1 block w-full bg-violet-50 rounded-full h-1.5 overflow-hidden">
                    <span
                      className="block h-1.5 rounded-full bg-violet-500"
                      style={{ width: `${Math.min(entry.sharePct, 100)}%` }}
                    />
                  </span>
                </button>
              </li>
            ))}
          </ul>

          {/* Sem aproximacao: Top 3 so' aparece com >= 3 marcas positivas. */}
          <dl className="mt-auto pt-1.5 border-t border-violet-100 flex items-center gap-4 text-xs">
            <div className="flex items-baseline gap-1">
              <dt className="text-slate-500">Top 1</dt>
              <dd className="font-semibold text-slate-700 tabular-nums">
                {concentration.top1Pct != null ? `${concentration.top1Pct.toFixed(1)}%` : "N/D"}
              </dd>
            </div>
            <div className="flex items-baseline gap-1">
              <dt className="text-slate-500">Top 3</dt>
              <dd className="font-semibold text-slate-700 tabular-nums">
                {concentration.top3Pct != null ? (
                  `${concentration.top3Pct.toFixed(1)}%`
                ) : (
                  <span
                    className="font-medium text-slate-500"
                    title="Menos de três marcas com GMV positivo no período"
                  >
                    base insuficiente
                  </span>
                )}
              </dd>
            </div>
          </dl>
        </>
      )}
    </section>
  );
}

export default function MovementsPanels({
  movements,
  concentration,
  loading,
  error,
  onOpenMovement,
  onOpenConcentration,
  produtosHref,
  onRetry,
}: Props) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <PanelSkeleton />
        <PanelSkeleton />
        <PanelSkeleton />
      </div>
    );
  }

  if (error) {
    return (
      <section className="bg-slate-50 border border-slate-200 rounded-2xl p-4 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-slate-700">Movimentos e concentração</h2>
          <p className="text-xs text-slate-500">
            Desempenho por marca indisponível nesta carga — sem base para calcular variações e participação.
          </p>
        </div>
        <button
          type="button"
          onClick={onRetry}
          className="text-xs font-semibold text-slate-700 border border-slate-300 rounded-lg px-3 min-h-11 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
        >
          Tentar novamente
        </button>
      </section>
    );
  }

  const floorNote = movements.excludedByFloor > 0;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
      <MovementList
        title="Maiores altas"
        items={movements.gains}
        emptyLabel="Nenhuma alta com base anterior suficiente no período."
        floorNote={floorNote}
        onOpenMovement={onOpenMovement}
      />
      <MovementList
        title="Maiores quedas"
        items={movements.drops}
        emptyLabel="Nenhuma queda com base anterior suficiente no período."
        floorNote={floorNote}
        onOpenMovement={onOpenMovement}
      />
      <ConcentrationPanel concentration={concentration} onOpenConcentration={onOpenConcentration} />
    </div>
  );
}
