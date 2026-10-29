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
import { Activity } from "lucide-react";
import TableScrollHint from "@/components/TableScrollHint";
import type { VolumeHealthRow } from "@/lib/gerencial/volume-health";
import { CANCEL_RATE_FORMULA, NO_CROSS_CHANNEL_RANKING } from "@/lib/gerencial/volume-health";
import { fmtBrlFull } from "@/lib/formatters";
import { CHANNEL_DOT } from "@/lib/gerencial/channel-colors";

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
        className="bg-surface border border-line rounded-xl p-4 flex flex-col gap-3 animate-pulse"
        role="status"
        aria-busy="true"
      >
        <span className="sr-only">Carregando saúde do volume…</span>
        <div className="h-3 w-48 bg-accent-soft rounded" />
        <div className="h-12 w-full bg-accent-soft rounded" />
        <div className="h-12 w-full bg-accent-soft rounded" />
      </section>
    );
  }

  if (error) {
    return (
      <section className="bg-raised border border-line rounded-xl p-4 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-ink-muted">Saúde do volume por canal</h2>
          <p className="text-xs text-ink-faint">
            Fonte de qualidade indisponível nesta carga. Os demais blocos do período seguem exibidos.
          </p>
        </div>
        <button
          type="button"
          onClick={onRetry}
          className="text-xs font-semibold text-ink-muted border border-line-strong rounded-lg px-3 min-h-11 hover:bg-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          Tentar novamente
        </button>
      </section>
    );
  }

  if (rows.length === 0) return null;

  // UX-TORRE-B2B-1: os motivos de N/D saem de dentro de cada linha e viram
  // notas numeradas no rodape — continuam visiveis, mas a tabela volta a ser
  // lida coluna a coluna. Mesmo texto, sem reescrita.
  const notas = rows.flatMap((row) => {
    if (!row.cancelAvailable && row.cancelUnavailableReason) return [row.cancelUnavailableReason];
    if (row.cancelAvailable && !row.returnAvailable && row.returnUnavailableReason) return [row.returnUnavailableReason];
    return [];
  });

  const TH = "px-3 py-2 text-xs font-semibold uppercase tracking-wide text-ink-faint";

  return (
    <section
      aria-labelledby="volume-heading"
      className="bg-surface border border-line rounded-xl flex flex-col overflow-hidden"
    >
      <div className="flex items-center justify-between gap-3 flex-wrap border-b border-line px-4 py-2.5">
        <h2 id="volume-heading" className="flex items-center gap-2 text-sm font-semibold text-ink">
          <Activity aria-hidden="true" className="h-4 w-4 text-ink-faint" strokeWidth={1.75} />
          Saúde do volume por canal
        </h2>
        <p className="text-xs text-ink-faint">{CANCEL_RATE_FORMULA}</p>
      </div>

      <TableScrollHint>
        <table className="w-full text-data" aria-labelledby="volume-heading">
          <thead>
            <tr className="bg-raised">
              <th scope="col" className={`${TH} text-left`}>Canal</th>
              <th scope="col" className={`${TH} text-right`}>Pedidos</th>
              <th scope="col" className={`${TH} text-right`}>Cancelados</th>
              <th scope="col" className={`${TH} text-left min-w-[11rem]`}>Taxa de cancelamento</th>
              <th scope="col" className={`${TH} text-right`}>Devolução</th>
              <th scope="col" className={`${TH} text-right`}>GMV</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((row) => (
              <tr key={row.channel} className="hover:bg-raised/70 transition-colors">
                <th scope="row" className="px-1.5 py-0.5 text-left font-normal">
                  <button
                    type="button"
                    onClick={() => onOpenChannel(row.channel)}
                    aria-haspopup="dialog"
                    aria-label={`${row.channelLabel} — abrir definição, valores e limitações`}
                    className="inline-flex min-h-11 items-center gap-2 rounded-md px-1.5 text-sm font-semibold text-ink hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent whitespace-nowrap"
                  >
                    <span aria-hidden="true" className={`w-2 h-2 rounded-full shrink-0 ${CHANNEL_DOT[row.channel]}`} />
                    {row.channelLabel}
                  </button>
                </th>
                <td className="px-3 py-2 text-right tabular-nums text-ink-muted">
                  <span className="sr-only">{row.ordersLabel}: </span>
                  {intCount(row.orders)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-ink-muted">
                  {row.cancelAvailable ? intCount(row.canceled) : <span className="text-ink-faint">N/D</span>}
                </td>
                <td className="px-3 py-2">
                  {/* A barra representa a TAXA do proprio canal (0-100%), nunca
                      uma parcela monetaria. Canal sem taxa nao ganha barra. */}
                  {row.cancelAvailable && row.cancelRatePct != null ? (
                    <span className="flex items-center gap-2">
                      <span className="block h-1.5 flex-1 overflow-hidden rounded-full bg-raised" aria-hidden="true">
                        <span
                          className="tc-barra block h-1.5 rounded-full bg-ink-faint"
                          style={{ width: `${Math.min(row.cancelRatePct, 100)}%` }}
                        />
                      </span>
                      <span className="w-12 shrink-0 text-right font-semibold tabular-nums text-ink-muted">
                        {ratePct(row.cancelRatePct)}
                      </span>
                    </span>
                  ) : (
                    <span className="text-ink-faint">N/D</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-ink-muted whitespace-nowrap">
                  {row.returnAvailable ? (
                    <>
                      {intCount(row.returned)} · {ratePct(row.returnRatePct)}
                    </>
                  ) : (
                    <span className="text-ink-faint">N/D</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right tabular-nums font-semibold text-ink whitespace-nowrap">
                  {row.gmv != null ? fmtBrlFull(row.gmv) : <span className="font-normal text-ink-faint">GMV N/D</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScrollHint>

      <div className="flex flex-col gap-0.5 border-t border-line px-4 py-2 text-xs text-ink-faint">
        {notas.map((nota) => (
          <p key={nota}>N/D — {nota}</p>
        ))}
        <p>{NO_CROSS_CHANNEL_RANKING} A taxa é a do próprio canal.</p>
      </div>
    </section>
  );
}
