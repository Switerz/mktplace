"use client";

/**
 * Card da evolucao temporal — o bloco DOMINANTE da Gerencial V2 (Gate V2-1,
 * Task E). Sete colunas no desktop, ao lado do item Pulso+Canais em cinco.
 *
 * Estrutura que elimina a lacuna diagnosticada no V2-0: este card **e'** o item
 * da grade (nenhum wrapper vazio no meio), e' `h-full flex flex-col`, e a area
 * do grafico e' `flex-1` com piso `min-h`. Nenhuma altura de grafico em pixel
 * fixo. O container da grade nao usa `items-start`.
 *
 * Controles: seletor GMV | Pedidos (troca local, sem novo fetch — `/trend` ja
 * entrega as duas metricas). A granularidade e' EXIBIDA, nao selecionavel, e a
 * comparacao com o periodo anterior e' declarada como indisponivel na serie —
 * nenhum controle morto na tela.
 */
import dynamic from "next/dynamic";
import type { Marketplace } from "@/lib/mock-data";
import type { MergedSeries, Reconciliation } from "@/lib/gerencial/trend-series";
import type { TrendMetric } from "@/lib/gerencial/request-key";
import { CHANNEL_LABEL } from "@/lib/gerencial/kpi-band";
import { CHANNEL_STROKE } from "@/lib/gerencial/channel-colors";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import { fmtBrlFull, fmtNumber } from "@/lib/formatters";

const EvolutionChart = dynamic(() => import("./EvolutionChart"), {
  ssr: false,
  loading: () => (
    <div className="h-full w-full rounded-xl bg-violet-50/60 animate-pulse" role="status" aria-busy="true">
      <span className="sr-only">Carregando gráfico…</span>
    </div>
  ),
});

export const COMPARISON_SERIES_NOTE =
  "Comparação ativa nos KPIs; série do período anterior indisponível neste gráfico.";

const METRIC_LABEL: Record<TrendMetric, string> = { gmv: "GMV", orders: "Pedidos" };

interface Props {
  merged: MergedSeries;
  metric: TrendMetric;
  onMetricChange: (metric: TrendMetric) => void;
  reconciliation: Reconciliation;
  compareActive: boolean;
  loading: boolean;
  onSelectBucket: (date: string) => void;
  /** Legenda de canal (Task B.1) — abre a explicacao da serie no shell unico. */
  onOpenChannelSeries: (channel: Marketplace) => void;
  /** Canal realcado enquanto o dialogo da serie esta aberto; `null` = nenhum. */
  highlightedChannel: Marketplace | null;
}

function granularityLabel(granularity: "day" | "month"): string {
  return granularity === "day"
    ? "Granularidade diária — definida pelo intervalo"
    : "Granularidade mensal — definida pelo intervalo";
}

function channelList(channels: readonly Marketplace[]): string {
  return channels.map((c) => CHANNEL_LABEL[c]).join(", ");
}

export default function EvolutionCard({
  merged,
  metric,
  onMetricChange,
  reconciliation,
  compareActive,
  loading,
  onSelectBucket,
  onOpenChannelSeries,
  highlightedChannel,
}: Props) {
  const showTotal = merged.everyBucketComplete && merged.availableChannels.length > 1;
  const mismatch = reconciliation.status === "mismatch";
  const hasSomething = merged.availableChannels.length > 0 && merged.buckets.length > 0;

  return (
    <section
      aria-labelledby="evolucao-heading"
      className="h-full bg-white border border-violet-100 rounded-2xl shadow-sm p-4 flex flex-col gap-3"
    >
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h2 id="evolucao-heading" className="text-sm font-semibold text-slate-700">
            Evolução de {METRIC_LABEL[metric]}
          </h2>
          <p className="text-xs text-slate-500">{granularityLabel(merged.granularity)}</p>
        </div>

        <div
          className="flex items-center gap-1 bg-violet-50/70 rounded-lg p-0.5 shrink-0"
          role="group"
          aria-label="Métrica do gráfico"
        >
          {(["gmv", "orders"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => onMetricChange(m)}
              aria-pressed={metric === m}
              className={`min-h-11 px-3 rounded-md text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                metric === m
                  ? "bg-white text-violet-700 shadow-sm"
                  : "text-slate-500 hover:text-violet-700"
              }`}
            >
              {METRIC_LABEL[m]}
            </button>
          ))}
        </div>
      </div>

      {/* Area do grafico: flex-1 + piso. Absorve toda a sobra da coluna, que e'
          o que mantem o final deste card alinhado ao item Pulso+Canais. */}
      <div className="flex-1 min-h-[240px] lg:min-h-[280px]">
        {loading ? (
          <div className="h-full w-full rounded-xl bg-violet-50/60 animate-pulse" role="status" aria-busy="true">
            <span className="sr-only">Carregando série…</span>
          </div>
        ) : mismatch ? (
          <div className="h-full flex flex-col items-center justify-center gap-2 text-center px-4">
            <p className="text-sm font-semibold text-rose-700">Série não reconciliada</p>
            <p className="text-xs text-rose-800 max-w-sm">
              A soma das séries por canal não bate com o total do período no mesmo escopo. O gráfico não é
              exibido para não sugerir um número que não fecha.
            </p>
            <p className="text-xs text-slate-500 tabular-nums">
              Séries {metric === "gmv" ? fmtBrlFull(reconciliation.seriesTotal) : fmtNumber(reconciliation.seriesTotal)} ·
              período {metric === "gmv" ? fmtBrlFull(reconciliation.referenceValue) : fmtNumber(reconciliation.referenceValue)}
            </p>
          </div>
        ) : hasSomething ? (
          <EvolutionChart
            buckets={merged.buckets}
            channels={merged.availableChannels}
            metric={metric}
            showTotal={showTotal}
            highlightedChannel={highlightedChannel}
            onSelectBucket={onSelectBucket}
          />
        ) : (
          <div className="h-full flex flex-col items-center justify-center gap-1 text-center px-4">
            <p className="text-sm text-slate-500">Sem série para o período e filtros selecionados.</p>
            <p className="text-xs text-slate-500">Tente ampliar o intervalo de datas ou revisar canal e marca.</p>
          </div>
        )}
      </div>

      {/* Legenda por canal + estado do total */}
      {!loading && merged.availableChannels.length > 0 && (
        <ul className="flex items-center gap-x-1 gap-y-1 flex-wrap text-xs">
          {merged.availableChannels.map((channel) => (
            <li key={channel}>
              <button
                type="button"
                onClick={() => onOpenChannelSeries(channel)}
                aria-haspopup="dialog"
                aria-pressed={highlightedChannel === channel}
                aria-label={`${CHANNEL_LABEL[channel]} — abrir detalhe da série do canal`}
                className={`inline-flex items-center gap-1.5 min-h-11 px-2 rounded-lg text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                  highlightedChannel === channel
                    ? "bg-violet-50 text-violet-800 font-semibold"
                    : // Hover NEUTRO no estado inativo, de proposito: cinza sobre
                      // violeta fica lavado, e reservar o violeta ao estado ativo
                      // respeita a disciplina de violeta do sistema.
                      "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                }`}
              >
                <span
                  aria-hidden="true"
                  className="w-2.5 h-0.5 rounded-full shrink-0"
                  style={{ background: CHANNEL_STROKE[channel] }}
                />
                {CHANNEL_LABEL[channel]}
              </button>
            </li>
          ))}
          {showTotal && (
            <li className="inline-flex items-center gap-1.5 px-2 text-slate-700 font-medium">
              <span aria-hidden="true" className="w-2.5 h-0.5 rounded-full shrink-0 bg-violet-900" />
              Total
            </li>
          )}
        </ul>
      )}

      <div className="flex flex-col gap-1.5">
        {/* Falha parcial: canal indisponivel e' NOMEADO e o total nao e' desenhado. */}
        {merged.failedChannels.length > 0 && (
          <DataQualityNote
            note={`Série indisponível para ${channelList(merged.failedChannels)}. As demais séries continuam exibidas e o total do período não é apresentado como completo.`}
          />
        )}
        {merged.emptyChannels.length > 0 && (
          <DataQualityNote
            note={`Sem linhas no período para ${channelList(merged.emptyChannels)} — ausência de dado, não zero.`}
          />
        )}
        {!showTotal && merged.availableChannels.length > 1 && merged.failedChannels.length === 0 && (
          <p className="text-xs text-slate-500">
            Total por ponto não exibido: há períodos sem valor em todos os canais selecionados.
          </p>
        )}
        {compareActive && <p className="text-xs text-slate-500">{COMPARISON_SERIES_NOTE}</p>}
        {reconciliation.status === "ok" && (
          <p className="text-xs text-slate-500">Soma das séries reconciliada com o total do período.</p>
        )}
      </div>
    </section>
  );
}
