"use client";

/**
 * Coluna de apoio da segunda dobra (Gate V2-1, Task F): Pulso do periodo +
 * resumo de canais, num **unico item da grade**.
 *
 * O `lg:row-span-2` do layout antigo era a origem estrutural da lacuna: com ele
 * as alturas das duas linhas implicitas passavam a ser ditadas pela coluna
 * direita, e `items-start` impedia o grafico de esticar ate sua area. Aqui os
 * dois paineis vivem dentro de um `flex flex-col` que E' o item da grade — sem
 * row-span, sem aritmetica de linhas implicitas.
 *
 * Degradacao independente: Pulso indisponivel preserva o resumo de canais (e
 * vice-versa), sempre nomeando o que faltou, e nunca deixando um card grande
 * vazio no lugar.
 */
import Link from "next/link";
import PulsoPeriodoPanel from "@/components/PulsoPeriodoPanel";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import type { ExecutiveHealth, OverviewData } from "@/lib/api-client";
import type { Pulse } from "@/lib/executive-pulse";
import { gmvChannelBreakdown } from "@/lib/kpi-drilldown";
import type { Marketplace } from "@/lib/mock-data";
import { fmtBrlFull } from "@/lib/formatters";
import { CHANNEL_DOT } from "@/lib/gerencial/channel-colors";

interface Props {
  pulse: Pulse;
  health: ExecutiveHealth | null;
  pulseLoading: boolean;
  pulseUnavailable: boolean;
  onOpenInsight: (key: string) => void;
  onOpenAllInsights: () => void;

  overview: OverviewData | null;
  channels: Marketplace[];
  channelsLoading: boolean;
  channelsUnavailable: boolean;
  onOpenChannel: (channel: Marketplace) => void;
  channelsHref: string;
}

function ChannelSummary({
  overview,
  channels,
  loading,
  unavailable,
  onOpenChannel,
  channelsHref,
}: Pick<Props, "overview" | "channels" | "onOpenChannel" | "channelsHref"> & {
  loading: boolean;
  unavailable: boolean;
}) {
  const base =
    "flex-1 bg-surface border border-line rounded-xl p-4 flex flex-col gap-3";

  if (loading) {
    return (
      <div className={`${base} animate-pulse`} role="status" aria-busy="true">
        <span className="sr-only">Carregando desempenho por canal…</span>
        <div className="h-2.5 w-32 bg-accent-soft rounded" />
        <div className="h-8 w-full bg-accent-soft rounded" />
        <div className="h-8 w-full bg-accent-soft rounded" />
      </div>
    );
  }

  if (unavailable || !overview) {
    return (
      <div className="flex-1 bg-raised border border-line rounded-xl p-4 flex flex-col gap-1">
        <h3 className="text-sm font-semibold text-ink-muted">Canais</h3>
        <p className="text-xs text-ink-faint">
          Participação por canal indisponível no momento. Os demais blocos do período seguem exibidos.
        </p>
      </div>
    );
  }

  const shares = gmvChannelBreakdown(overview, channels);

  return (
    <section aria-labelledby="canais-resumo-heading" className={base}>
      <div className="flex items-baseline justify-between gap-2">
        <h3 id="canais-resumo-heading" className="text-sm font-semibold text-ink-muted">
          Canais
        </h3>
        <span className="text-xs text-ink-faint">participação no GMV</span>
      </div>

      {shares.length === 0 ? (
        <p className="text-xs text-ink-faint">Sem dados de canal para o período e filtros selecionados.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {shares.map((s) => (
            <li key={s.channel}>
              <button
                type="button"
                onClick={() => onOpenChannel(s.channel)}
                aria-haspopup="dialog"
                aria-label={`${s.label} — abrir explicação do canal`}
                className="w-full min-h-11 text-left flex flex-col gap-1.5 rounded-lg px-2 -mx-2 py-1 hover:bg-accent-soft/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent transition-colors"
              >
                <span className="flex items-center justify-between gap-2 text-xs">
                  <span className="flex items-center gap-1.5 min-w-0">
                    <span
                      aria-hidden="true"
                      className={`w-2 h-2 rounded-full shrink-0 ${CHANNEL_DOT[s.channel]}`}
                    />
                    <span className="font-medium text-ink-muted truncate">{s.label}</span>
                  </span>
                  <span className="tabular-nums text-ink-muted shrink-0">
                    {s.value != null ? fmtBrlFull(s.value) : <span className="text-ink-faint">Sem dado</span>}
                    {s.pct != null && <span className="text-ink-faint"> · {s.pct.toFixed(1)}%</span>}
                  </span>
                </span>
                <span className="block w-full bg-accent-soft rounded-full h-1.5 overflow-hidden">
                  <span
                    className={`block h-1.5 rounded-full ${s.value != null ? CHANNEL_DOT[s.channel] : "bg-line-strong"}`}
                    style={{ width: `${s.pct ?? 0}%` }}
                  />
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <Link
        href={channelsHref}
        className="mt-auto inline-flex items-center min-h-11 text-xs font-semibold text-accent hover:underline self-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
      >
        Ver todos os canais →
      </Link>
    </section>
  );
}

export default function PulseChannelsColumn(props: Props) {
  const bothUnavailable = props.pulseUnavailable && props.channelsUnavailable;

  return (
    <div className="h-full flex flex-col gap-3">
      <PulsoPeriodoPanel
        pulse={props.pulse}
        health={props.health}
        loading={props.pulseLoading}
        unavailable={props.pulseUnavailable}
        onOpenInsight={props.onOpenInsight}
        onOpenAll={props.onOpenAllInsights}
      />
      <ChannelSummary
        overview={props.overview}
        channels={props.channels}
        loading={props.channelsLoading}
        unavailable={props.channelsUnavailable}
        onOpenChannel={props.onOpenChannel}
        channelsHref={props.channelsHref}
      />
      {bothUnavailable && (
        <DataQualityNote note="Pulso do período e participação por canal indisponíveis nesta carga. Os KPIs, a evolução e os blocos abaixo continuam com os dados do período." />
      )}
    </div>
  );
}
