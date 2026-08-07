"use client";

/**
 * "Saude do volume por canal" (Gate V2-1, Task G).
 *
 * NAO e' funil e NAO e' waterfall monetario. Cada canal e' uma linha
 * descritiva, legivel isoladamente, com cada metrica na sua propria unidade.
 * Nenhum segmento sugere particao exclusiva de um valor monetario: a unica
 * barra existente representa a TAXA do proprio canal (0-100%), nunca uma fatia
 * de GMV.
 *
 * A ordem das linhas segue a ordem canonica de canal, jamais a taxa — ordenar
 * por taxa criaria o ranking competitivo que o V2-0 proibiu.
 */
import type { VolumeHealthRow } from "@/lib/gerencial/volume-health";
import { CANCEL_RATE_FORMULA, NO_CROSS_CHANNEL_RANKING } from "@/lib/gerencial/volume-health";
import { fmtBrlFull } from "@/lib/formatters";
import { CHANNEL_STROKE } from "@/lib/gerencial/channel-colors";

interface Props {
  rows: VolumeHealthRow[];
  loading: boolean;
  error: boolean;
  onOpenChannel: (channel: VolumeHealthRow["channel"]) => void;
  onRetry: () => void;
}

function intCount(value: number | null): string {
  return value == null ? "N/D" : value.toLocaleString("pt-BR");
}

function ratePct(value: number | null): string {
  return value == null ? "N/D" : `${value.toFixed(1).replace(".", ",")}%`;
}

export default function VolumeHealthCard({ rows, loading, error, onOpenChannel, onRetry }: Props) {
  if (loading) {
    return (
      <section
        className="bg-white border border-violet-100 rounded-2xl shadow-sm p-4 flex flex-col gap-3 animate-pulse"
        role="status"
        aria-busy="true"
      >
        <span className="sr-only">Carregando saúde do volume…</span>
        <div className="h-3 w-48 bg-violet-100 rounded" />
        <div className="h-12 w-full bg-violet-50 rounded" />
        <div className="h-12 w-full bg-violet-50 rounded" />
      </section>
    );
  }

  if (error) {
    return (
      <section className="bg-slate-50 border border-slate-200 rounded-2xl p-4 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-slate-700">Saúde do volume por canal</h2>
          <p className="text-xs text-slate-500">
            Fonte de qualidade indisponível nesta carga. Os demais blocos do período seguem exibidos.
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

  if (rows.length === 0) return null;

  return (
    <section
      aria-labelledby="volume-heading"
      className="bg-white border border-violet-100 rounded-2xl shadow-sm p-4 flex flex-col gap-3"
    >
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <h2 id="volume-heading" className="text-sm font-semibold text-slate-700">
          Saúde do volume por canal
        </h2>
        <p className="text-xs text-slate-500">{CANCEL_RATE_FORMULA}</p>
      </div>

      <ul className="flex flex-col divide-y divide-violet-100">
        {rows.map((row) => (
          <li key={row.channel} className="py-2.5 first:pt-0 last:pb-0">
            <button
              type="button"
              onClick={() => onOpenChannel(row.channel)}
              aria-haspopup="dialog"
              aria-label={`${row.channelLabel} — abrir definição, valores e limitações`}
              className="w-full min-h-11 text-left rounded-lg px-2 -mx-2 py-1 hover:bg-violet-50/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 transition-colors"
            >
              <div className="flex items-center gap-2 mb-1.5">
                <span
                  aria-hidden="true"
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ background: CHANNEL_STROKE[row.channel] }}
                />
                <span className="text-sm font-semibold text-slate-800">{row.channelLabel}</span>
                <span className="ml-auto text-sm tabular-nums text-slate-700 shrink-0">
                  {row.gmv != null ? fmtBrlFull(row.gmv) : <span className="text-slate-400">GMV N/D</span>}
                </span>
              </div>

              <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1.5">
                <div className="min-w-0">
                  <dt className="text-xs text-slate-500 truncate">{row.ordersLabel}</dt>
                  <dd className="text-xs font-medium text-slate-700 tabular-nums">{intCount(row.orders)}</dd>
                </div>
                <div className="min-w-0">
                  <dt className="text-xs text-slate-500">Cancelados</dt>
                  <dd className="text-xs font-medium text-slate-700 tabular-nums">
                    {row.cancelAvailable ? intCount(row.canceled) : <span className="text-slate-400">N/D</span>}
                  </dd>
                </div>
                <div className="min-w-0">
                  <dt className="text-xs text-slate-500">Taxa de cancelamento</dt>
                  <dd className="text-xs font-medium tabular-nums">
                    {row.cancelAvailable ? (
                      <span className="text-slate-700">{ratePct(row.cancelRatePct)}</span>
                    ) : (
                      <span className="text-slate-400">N/D</span>
                    )}
                  </dd>
                </div>
                <div className="min-w-0">
                  <dt className="text-xs text-slate-500">Devolução</dt>
                  <dd className="text-xs font-medium tabular-nums">
                    {row.returnAvailable ? (
                      <span className="text-slate-700">
                        {intCount(row.returned)} · {ratePct(row.returnRatePct)}
                      </span>
                    ) : (
                      <span className="text-slate-400">N/D</span>
                    )}
                  </dd>
                </div>
              </dl>

              {/* A barra representa a TAXA do proprio canal (0-100%), nunca uma
                  parcela monetaria. Canal sem taxa nao ganha barra alguma. */}
              {row.cancelAvailable && row.cancelRatePct != null && (
                <div className="mt-2 flex items-center gap-2">
                  <span className="block flex-1 bg-violet-50 rounded-full h-1.5 overflow-hidden">
                    <span
                      className="block h-1.5 rounded-full bg-slate-400"
                      style={{ width: `${Math.min(row.cancelRatePct, 100)}%` }}
                    />
                  </span>
                  <span className="text-xs text-slate-500 shrink-0">taxa do próprio canal</span>
                </div>
              )}

              {!row.cancelAvailable && (
                <p className="mt-1.5 text-xs text-slate-500">{row.cancelUnavailableReason}</p>
              )}
              {row.cancelAvailable && !row.returnAvailable && row.returnUnavailableReason && (
                <p className="mt-1.5 text-xs text-slate-500">{row.returnUnavailableReason}</p>
              )}
            </button>
          </li>
        ))}
      </ul>

      <p className="text-xs text-slate-500">{NO_CROSS_CHANNEL_RANKING}</p>
    </section>
  );
}
