"use client";

/**
 * Faixa de cinco KPIs da Gerencial V2 (Gate V2-1, Task D).
 *
 * Componente NOVO em vez de estender `KpiCard`: aquele e' consumido por sete
 * outras rotas e este gate nao pode alterar a aparencia delas. A logica de
 * disponibilidade/comparacao vive em `lib/gerencial/kpi-band.ts` (pura e
 * testada); aqui so' existe apresentacao.
 *
 * Alturas iguais por construcao: cada card e' `h-full flex flex-col` dentro de
 * uma grade de uma linha, e o rodape (`mt-auto`) empurra a nota para a base.
 */
import type { KpiDescriptor, KpiKey } from "@/lib/gerencial/kpi-band";
import { fmtPct } from "@/lib/formatters";

interface Props {
  kpis: KpiDescriptor[];
  loading: boolean;
  /** Task A — o bloco falha SOZINHO: `/overview` em erro nao apaga a evolucao,
   * o Pulso, a matriz, os movimentos nem a fila, que tem fontes proprias. */
  error: boolean;
  onOpen: (key: KpiKey) => void;
  onRetry: () => void;
}

function DeltaBadge({ pct }: { pct: number }) {
  const up = pct >= 0;
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs font-semibold tabular-nums ${
        up ? "text-emerald-600" : "text-rose-600"
      }`}
    >
      <span aria-hidden="true">{up ? "▲" : "▼"}</span>
      {fmtPct(pct)}
    </span>
  );
}

function KpiCardSkeleton() {
  return (
    <div
      className="h-full bg-white border border-violet-100 rounded-2xl shadow-sm p-4 flex flex-col gap-3 animate-pulse"
      role="status"
      aria-busy="true"
    >
      <span className="sr-only">Carregando indicador…</span>
      <div className="h-2.5 w-20 bg-violet-100 rounded" />
      <div className="h-7 w-32 bg-violet-50 rounded" />
      <div className="h-2.5 w-24 bg-violet-50 rounded mt-auto" />
    </div>
  );
}

function KpiCard({ kpi, onOpen }: { kpi: KpiDescriptor; onOpen: (key: KpiKey) => void }) {
  const hasChannelList = kpi.channelValues != null && kpi.channelValues.length > 0;

  return (
    <button
      type="button"
      onClick={() => onOpen(kpi.key)}
      aria-haspopup="dialog"
      aria-label={`${kpi.label} — abrir decomposição`}
      // `w-full min-w-0`: sem eles o <button> se dimensiona pelo conteudo e
      // estoura a trilha da grade — era a origem de 5px de overflow horizontal
      // no mobile (390px), medido no QA.
      className="group w-full min-w-0 h-full min-h-11 text-left bg-white border border-violet-100 rounded-2xl shadow-sm p-4 flex flex-col gap-1.5 transition-shadow hover:shadow-[0_4px_12px_0_rgba(124,58,237,0.08),0_1px_3px_0_rgba(0,0,0,0.06)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
    >
      <span className="flex items-center gap-2">
        <span aria-hidden="true" className={`w-1.5 h-1.5 rounded-full shrink-0 ${kpi.accent}`} />
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          {kpi.label}
        </span>
      </span>

      {/* ROAS nunca tem valor unico: renderiza a lista por canal. */}
      {hasChannelList ? (
        <span className="flex flex-col gap-0.5 mt-0.5">
          {kpi.channelValues!.map((cv) => {
            const highlighted = kpi.highlightChannel === cv.channel;
            return (
              <span key={cv.channel} className="flex items-baseline justify-between gap-2">
                <span className="text-xs text-slate-500 truncate">{cv.label}</span>
                {cv.value ? (
                  <span
                    className={`tabular-nums shrink-0 ${
                      highlighted ? "text-xl font-bold text-slate-900" : "text-sm font-semibold text-slate-800"
                    }`}
                  >
                    {cv.value}
                  </span>
                ) : (
                  <span className="text-xs text-slate-500 shrink-0">N/D</span>
                )}
              </span>
            );
          })}
        </span>
      ) : kpi.value ? (
        <span className="text-2xl font-bold text-slate-900 tabular-nums leading-tight">
          {kpi.value}
        </span>
      ) : (
        <span className="text-2xl font-bold text-slate-400 tabular-nums leading-tight">N/D</span>
      )}

      {kpi.deltaPct != null && kpi.value && <DeltaBadge pct={kpi.deltaPct} />}
      {kpi.reference && <span className="text-xs text-slate-500 tabular-nums">{kpi.reference}</span>}

      <span className="mt-auto flex flex-col gap-0.5 pt-1">
        {kpi.subvalue && <span className="text-xs text-slate-500 leading-snug">{kpi.subvalue}</span>}
        {kpi.comparisonNote && <span className="text-xs text-slate-500">{kpi.comparisonNote}</span>}
        {kpi.coverageNote && <span className="text-xs text-slate-500 leading-snug">{kpi.coverageNote}</span>}
        {kpi.unavailableReason && (
          <span className="text-xs text-slate-500 leading-snug">{kpi.unavailableReason}</span>
        )}
      </span>
    </button>
  );
}

export default function KpiBand({ kpis, loading, error, onOpen, onRetry }: Props) {
  if (error) {
    return (
      <section
        aria-label="Indicadores do período"
        className="bg-slate-50 border border-slate-200 rounded-2xl p-4 flex items-center justify-between gap-3 flex-wrap"
      >
        <div>
          <h2 className="text-sm font-semibold text-slate-700">Indicadores do período</h2>
          <p className="text-xs text-slate-500">
            O agregado do período não respondeu nesta carga — GMV, Pedidos, Ticket, Investimento e ROAS ficam
            indisponíveis. Os demais blocos abaixo seguem com as próprias fontes.
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

  return (
    <section aria-label="Indicadores do período">
      <h2 className="sr-only">Indicadores do período</h2>
      {/* 2 / 3 / 5 colunas. O ROAS (indice 4) ocupa duas colunas fora do
          desktop para que a fileira de cinco nunca deixe celula vazia — e ele
          e' justamente o card que precisa de mais largura, por listar canais. */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3" aria-busy={loading}>
        {loading
          ? Array.from({ length: 5 }, (_, i) => (
              // O ROAS ocupa duas colunas no tablet para nao deixar celula
              // vazia na fileira (grade de 5 em 2 colunas no mobile).
              <div key={i} className={i === 4 ? "col-span-2 lg:col-span-1" : undefined}>
                <KpiCardSkeleton />
              </div>
            ))
          : kpis.map((kpi, i) => (
              <div key={kpi.key} className={i === 4 ? "col-span-2 lg:col-span-1" : undefined}>
                <KpiCard kpi={kpi} onOpen={onOpen} />
              </div>
            ))}
      </div>
    </section>
  );
}
