"use client";

/**
 * Matriz Marca x Canal (Gate V2-1, Task H; completada na rodada consolidada,
 * Tasks B.2, B.4 e C).
 *
 * Intensidade e participacao calculadas DENTRO de cada canal — nunca entre
 * canais, cujas ordens de grandeza produziriam comparacao falsa.
 *
 * Duas fontes, dois estados: o GMV/share/variacao vem de `/brands`; os SINAIS e
 * as referencias comparativas vem de `/canais`. Por isso a matriz tem estado
 * PARCIAL: com `/brands` fresco e `/canais` em falha, a grade de GMV continua
 * inteira e apenas os sinais sao declarados indisponiveis. Somente um erro de
 * `/brands` impede a matriz toda.
 *
 * Alvos clicaveis, sem aninhamento: a celula e' um `<button>` e os chips de
 * sinal dentro dela sao `<span>` — o nome acessivel da celula ja anuncia os
 * sinais, e a explicacao completa vive no dialogo comparativo.
 */
import Link from "next/link";
import TableScrollHint from "@/components/TableScrollHint";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import type { BrandChannelMatrix as MatrixData, MatrixCell } from "@/lib/gerencial/brand-matrix";
import { CHANNEL_LABEL } from "@/lib/gerencial/kpi-band";
import { signalLabel } from "@/lib/canais-channel-metrics";
import { fmtBrlFull, fmtPct } from "@/lib/formatters";

/** Estado da fonte de sinais/referencias (`/canais`), independente de `/brands`. */
export type SignalsState = "fresh" | "loading" | "error";

interface Props {
  matrix: MatrixData;
  loading: boolean;
  error: boolean;
  /** Sinais por celula, chaveados por `${brand}:${channel}`. */
  signalsByCell: Record<string, string[]>;
  signalsState: SignalsState;
  onOpenCell: (brand: string, channel: MatrixCell["channel"]) => void;
  onOpenChannelHeader: (channel: MatrixCell["channel"]) => void;
  brandHref: (brand: string) => string;
  onRetry: () => void;
}

export function cellKey(brand: string, channel: string): string {
  return `${brand}:${channel}`;
}

/** Fundo violeta proporcional a intensidade dentro do canal. */
function cellBackground(intensity: number): string | undefined {
  if (intensity <= 0) return undefined;
  // Teto em 0.16 de opacidade: mantem o texto em contraste AA sobre o violeta.
  return `rgba(124, 58, 237, ${(intensity * 0.16).toFixed(3)})`;
}

/** Nome acessivel da celula — inclui os sinais para que o foco os anuncie. */
function cellAriaLabel(
  brandLabel: string,
  cell: MatrixCell,
  signals: string[],
  signalsState: SignalsState,
): string {
  const base = `${brandLabel} em ${CHANNEL_LABEL[cell.channel]}`;
  if (!cell.available) return `${base} — sem dado no período, abrir explicação`;
  const parts = [base];
  if (signals.length > 0) {
    parts.push(`sinais: ${signals.map(signalLabel).join(", ")}`);
  } else if (signalsState === "error") {
    parts.push("sinais indisponíveis nesta carga");
  }
  parts.push("abrir explicação");
  return parts.join(" — ");
}

export default function BrandChannelMatrix({
  matrix,
  loading,
  error,
  signalsByCell,
  signalsState,
  onOpenCell,
  onOpenChannelHeader,
  brandHref,
  onRetry,
}: Props) {
  if (loading) {
    return (
      <section
        className="bg-white border border-violet-100 rounded-2xl shadow-sm p-4 flex flex-col gap-3 animate-pulse"
        role="status"
        aria-busy="true"
      >
        <span className="sr-only">Carregando matriz marca × canal…</span>
        <div className="h-3 w-52 bg-violet-100 rounded" />
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="h-8 w-full bg-violet-50 rounded" />
        ))}
      </section>
    );
  }

  // Somente erro de /brands impede a matriz inteira.
  if (error) {
    return (
      <section className="bg-slate-50 border border-slate-200 rounded-2xl p-4 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-slate-700">Marca × Canal</h2>
          <p className="text-xs text-slate-500">
            Desempenho por marca indisponível nesta carga. Os demais blocos do período seguem exibidos.
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

  if (matrix.rows.length === 0 || matrix.channels.length === 0) {
    return (
      <section className="bg-white border border-violet-100 rounded-2xl shadow-sm p-4">
        <h2 className="text-sm font-semibold text-slate-700 mb-1">Marca × Canal</h2>
        <p className="text-xs text-slate-500">Sem marcas com dado no período e filtros selecionados.</p>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="matriz-heading"
      className="bg-white border border-violet-100 rounded-2xl shadow-sm p-4 flex flex-col gap-3"
    >
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <h2 id="matriz-heading" className="text-sm font-semibold text-slate-700">
          Marca × Canal
        </h2>
        <p className="text-xs text-slate-500">
          Intensidade e participação calculadas dentro de cada canal
        </p>
      </div>

      <TableScrollHint>
        <table className="w-full min-w-[680px] border-collapse">
          <caption className="sr-only">
            GMV por marca e canal no período, com participação dentro do canal, variação contra o período
            anterior e sinais de eficiência. Cada célula abre a explicação da marca no canal; cada cabeçalho de
            canal abre a distribuição daquele canal entre as marcas.
          </caption>
          <thead>
            <tr className="border-b border-violet-100">
              <th scope="col" className="text-left py-2 pr-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Marca
              </th>
              {matrix.channels.map((channel) => (
                <th key={channel} scope="col" className="py-1 px-1">
                  <button
                    type="button"
                    onClick={() => onOpenChannelHeader(channel)}
                    aria-haspopup="dialog"
                    aria-label={`${CHANNEL_LABEL[channel]} — abrir distribuição do canal entre as marcas`}
                    className="w-full min-h-11 text-right rounded-lg px-2 py-1 hover:bg-violet-50/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 transition-colors"
                  >
                    <span className="block text-xs font-semibold uppercase tracking-wide text-slate-500">
                      {CHANNEL_LABEL[channel]}
                    </span>
                    <span className="block text-xs font-medium text-slate-500 tabular-nums">
                      {fmtBrlFull(matrix.channelTotals[channel] ?? 0)}
                    </span>
                  </button>
                </th>
              ))}
              <th scope="col" className="text-right py-2 pl-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                <span className="block">Total</span>
                <span className="block font-medium normal-case tracking-normal text-slate-400 tabular-nums">
                  {fmtBrlFull(matrix.grandTotal)}
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {matrix.rows.map((row) => (
              <tr key={row.brand} className="border-b border-violet-50 last:border-0">
                <th scope="row" className="text-left py-1.5 pr-3 align-middle">
                  <Link
                    href={brandHref(row.brand)}
                    className="inline-flex items-center min-h-11 text-sm font-semibold text-slate-700 hover:text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
                  >
                    {row.label}
                  </Link>
                </th>

                {row.cells.map((cell) => {
                  const signals = signalsByCell[cellKey(row.brand, cell.channel)] ?? [];
                  const [main, ...rest] = signals;
                  return (
                    <td key={cell.channel} className="py-1.5 px-1 align-middle">
                      <button
                        type="button"
                        onClick={() => onOpenCell(row.brand, cell.channel)}
                        aria-haspopup="dialog"
                        aria-label={cellAriaLabel(row.label, cell, signals, signalsState)}
                        style={{ background: cell.available ? cellBackground(cell.intensity) : undefined }}
                        className={`w-full min-w-0 min-h-11 rounded-lg px-2 py-1 text-right transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                          cell.available ? "hover:ring-1 hover:ring-violet-300" : "bg-slate-50 hover:bg-slate-100"
                        }`}
                      >
                        {cell.available ? (
                          <>
                            <span className="block text-sm font-semibold text-slate-800 tabular-nums">
                              {fmtBrlFull(cell.gmv as number)}
                            </span>
                            <span className="block text-xs text-slate-500 tabular-nums">
                              {cell.sharePctInChannel != null && <>{cell.sharePctInChannel.toFixed(1)}%</>}
                              {cell.momPct != null && (
                                <span className={cell.momPct >= 0 ? "text-emerald-700" : "text-rose-700"}>
                                  {" "}
                                  {cell.momPct >= 0 ? "▲" : "▼"} {fmtPct(cell.momPct).replace("+", "")}
                                </span>
                              )}
                            </span>
                            {/* Chips de sinal: <span>, nunca <button> aninhado.
                                Rotulo humano do contrato de Canais — jamais o
                                identificador snake_case cru. */}
                            {main && (
                              <span className="mt-1 flex items-center justify-end gap-1">
                                <span className="text-xs font-medium rounded px-1.5 py-0.5 bg-amber-50 text-amber-800 border border-amber-200 truncate max-w-full">
                                  {signalLabel(main)}
                                </span>
                                {rest.length > 0 && (
                                  <span className="text-xs font-medium text-slate-500 shrink-0">
                                    +{rest.length}
                                  </span>
                                )}
                              </span>
                            )}
                          </>
                        ) : (
                          <>
                            <span className="block text-sm text-slate-400">—</span>
                            <span className="block text-xs text-slate-500">sem dado</span>
                          </>
                        )}
                      </button>
                    </td>
                  );
                })}

                <td className="py-1.5 pl-3 text-right align-middle">
                  <span className="text-sm font-semibold text-slate-800 tabular-nums">
                    {fmtBrlFull(row.brandTotal)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScrollHint>

      {/* Estado PARCIAL: o GMV veio, os sinais nao. A matriz nao se apresenta
          como completa. */}
      {signalsState === "error" && (
        <DataQualityNote note="Sinais de eficiência e referências comparativas (mediana e p75 do canal) indisponíveis nesta carga — a matriz mostra GMV, participação e variação, mas não está completa." />
      )}
      {signalsState === "loading" && (
        <p className="text-xs text-slate-500">Verificando sinais de eficiência e referências do canal…</p>
      )}
    </section>
  );
}
