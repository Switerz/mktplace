"use client";

/**
 * Card da evolucao temporal — o bloco DOMINANTE da Gerencial V2 (Gate V2-1,
 * Task E), estendido no Gate V2-2 com granularidade selecionavel e serie do
 * periodo anterior.
 *
 * Estrutura que elimina a lacuna diagnosticada no V2-0: este card **e'** o item
 * da grade (nenhum wrapper vazio no meio), e' `h-full flex flex-col`, e a area
 * do grafico e' `flex-1` com piso `min-h`. Nenhuma altura de grafico em pixel
 * fixo. O container da grade nao usa `items-start`.
 *
 * Controles: seletor de metrica (GMV | Pedidos), que e' troca puramente local, e
 * seletor de granularidade (Automatica | Diaria | Semanal | Mensal), que refaz
 * SOMENTE as chamadas de `/trend`. Nenhum controle morto: em "Automatica" o card
 * informa qual grao foi efetivamente resolvido.
 */
import dynamic from "next/dynamic";
import type { Marketplace } from "@/lib/mock-data";
import type { MergedSeries, Reconciliation } from "@/lib/gerencial/trend-series";
import type { TrendMetric } from "@/lib/gerencial/request-key";
import type { TrendGranularity, TrendGranularityRequest } from "@/lib/api-client";
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

const METRIC_LABEL: Record<TrendMetric, string> = { gmv: "GMV", orders: "Pedidos" };

const GRANULARITY_OPTIONS: { value: TrendGranularityRequest; label: string; short: string }[] = [
  { value: "auto", label: "Automática", short: "Auto" },
  { value: "day", label: "Diária", short: "Dia" },
  { value: "week", label: "Semanal", short: "Sem" },
  { value: "month", label: "Mensal", short: "Mês" },
];

const EFFECTIVE_LABEL: Record<TrendGranularity, string> = {
  day: "diária",
  week: "semanal (semana começando na segunda-feira)",
  month: "mensal",
};

/** Rotulo curto do grao, para nomear divergencias canal por canal. */
const EFFECTIVE_SHORT: Record<TrendGranularity, string> = {
  day: "diária",
  week: "semanal",
  month: "mensal",
};

const REQUESTED_SHORT: Record<TrendGranularityRequest, string> = {
  auto: "automática",
  day: "diária",
  week: "semanal",
  month: "mensal",
};

interface Props {
  merged: MergedSeries;
  metric: TrendMetric;
  onMetricChange: (metric: TrendMetric) => void;
  /** Granularidade PEDIDA — estado separado da efetivamente retornada. */
  granularity: TrendGranularityRequest;
  onGranularityChange: (granularity: TrendGranularityRequest) => void;
  reconciliation: Reconciliation;
  compareActive: boolean;
  loading: boolean;
  onSelectBucket: (date: string) => void;
  onOpenChannelSeries: (channel: Marketplace) => void;
  highlightedChannel: Marketplace | null;
}

function channelList(channels: readonly Marketplace[]): string {
  return channels.map((c) => CHANNEL_LABEL[c]).join(", ");
}

/** Texto do grao: em "Automática" diz qual foi resolvido, sem prometer escolha. */
function granularityCaption(
  requested: TrendGranularityRequest,
  effective: TrendGranularity,
): string {
  if (requested === "auto") {
    return `Granularidade ${EFFECTIVE_LABEL[effective]} — resolvida automaticamente pelo intervalo`;
  }
  return `Granularidade ${EFFECTIVE_LABEL[effective]}`;
}

export default function EvolutionCard({
  merged,
  metric,
  onMetricChange,
  granularity,
  onGranularityChange,
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

  const cmp = merged.comparison;
  // A linha anterior so' e' desenhada quando o total comparativo esta COMPLETO:
  // um total parcial nunca pode se passar por total.
  const showComparison = cmp.requested && cmp.everyBucketComplete;
  const cmpLoading = cmp.requested && cmp.loadingChannels.length > 0;
  const cmpPartial =
    cmp.requested &&
    !cmp.everyBucketComplete &&
    cmp.failedChannels.length === 0 &&
    cmp.unsupportedChannels.length === 0 &&
    cmp.windowIssue === null &&
    !cmpLoading;
  /** Grao divergente: nao ha serie combinada. Estado proprio, nunca "sem dados". */
  const grainIssue = merged.granularityIssue;
  const grainList = grainIssue
    ? grainIssue.grains
        .map((g) => `${CHANNEL_LABEL[g.channel]}: ${EFFECTIVE_SHORT[g.granularity]}`)
        .join(" · ")
    : "";

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
          <p className="text-xs text-slate-500">
            {grainIssue
              ? "Granularidade inconsistente entre os canais"
              : granularityCaption(granularity, merged.granularity)}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap shrink-0">
          <div
            className="flex items-center gap-1 bg-violet-50/70 rounded-lg p-0.5"
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
                  metric === m ? "bg-white text-violet-700 shadow-sm" : "text-slate-500 hover:text-violet-700"
                }`}
              >
                {METRIC_LABEL[m]}
              </button>
            ))}
          </div>

          {/* Granularidade: rotulo curto no mobile, completo a partir de sm.
              O nome acessivel usa sempre o rotulo completo. */}
          <div
            className="flex items-center gap-1 bg-violet-50/70 rounded-lg p-0.5"
            role="group"
            aria-label="Granularidade do gráfico"
          >
            {GRANULARITY_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => onGranularityChange(opt.value)}
                aria-pressed={granularity === opt.value}
                aria-label={opt.label}
                className={`min-h-11 px-2.5 rounded-md text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                  granularity === opt.value
                    ? "bg-white text-violet-700 shadow-sm"
                    : "text-slate-500 hover:text-violet-700"
                }`}
              >
                <span className="sm:hidden">{opt.short}</span>
                <span className="hidden sm:inline">{opt.label}</span>
              </button>
            ))}
          </div>
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
        ) : grainIssue ? (
          /* Grao divergente: nao se mescla, nao se converte e nao se reagrega.
             Estado PROPRIO — dizer "sem série" seria falso, e desenhar buckets de
             graos diferentes no mesmo eixo seria pior. */
          <div className="h-full flex flex-col items-center justify-center gap-2 text-center px-4">
            <p className="text-sm font-semibold text-amber-800">Granularidades incompatíveis</p>
            <p className="text-xs text-amber-900 max-w-sm">
              {grainIssue.kind === "unsupported_request"
                ? `A granularidade ${REQUESTED_SHORT[grainIssue.requested]} foi solicitada, mas a resposta veio em outro grão. O gráfico não é exibido para não sugerir uma escolha que não foi aplicada.`
                : "Os canais selecionados responderam em grãos diferentes. Buckets de grãos distintos não representam os mesmos períodos, então a série combinada não é montada."}
            </p>
            <p className="text-xs text-slate-500">{grainList}</p>
            <p className="text-xs text-slate-500">
              Nenhuma conversão é feita aqui. Recarregue ou selecione um grão explícito.
            </p>
          </div>
        ) : hasSomething ? (
          <EvolutionChart
            buckets={merged.buckets}
            channels={merged.availableChannels}
            metric={metric}
            showTotal={showTotal}
            showComparisonTotal={showComparison}
            highlightedChannel={highlightedChannel}
            onSelectBucket={onSelectBucket}
          />
        ) : (
          <div className="h-full flex flex-col items-center justify-center gap-1 text-center px-4">
            <p className="text-sm text-slate-500">Sem série para o período e filtros selecionados.</p>
            <p className="text-xs text-slate-400">Tente ampliar o intervalo de datas ou revisar canal e marca.</p>
          </div>
        )}
      </div>

      {/* Legenda por canal + estado do total */}
      {/* Sem grao comum nao existe linha desenhada: a legenda tambem nao aparece,
          para nao prometer series que o grafico nao mostra. */}
      {!loading && !grainIssue && merged.availableChannels.length > 0 && (
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
          {/* O periodo anterior entra na legenda como item SECUNDARIO, e o
              tracejado o distingue sem depender de cor. */}
          {showComparison && (
            <li className="inline-flex items-center gap-1.5 px-2 text-slate-500">
              <span
                aria-hidden="true"
                className="w-3.5 h-0 shrink-0 border-t-2 border-dashed border-slate-400"
              />
              Total do período anterior
            </li>
          )}
        </ul>
      )}

      <div className="flex flex-col gap-1.5">
        {/* Falha parcial da serie ATUAL: canal indisponivel e' NOMEADO e o total
            nao e' desenhado. */}
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

        {/* ---- estados da COMPARACAO, independentes da serie atual ----
            Com `compare=false` nada disto renderiza: nem linha, nem legenda,
            nem skeleton, nem aviso. */}
        {cmp.requested && (
          <>
            {cmpLoading && (
              <p className="text-xs text-slate-500" role="status" aria-busy="true">
                Carregando série do período anterior…
              </p>
            )}
            {cmp.failedChannels.length > 0 && (
              <DataQualityNote
                note={`Comparação indisponível para ${channelList(cmp.failedChannels)}. A série do período atual continua exibida e o total anterior não é apresentado.`}
              />
            )}
            {cmp.emptyChannels.length > 0 && (
              <DataQualityNote
                note={`Sem registros no período anterior para ${channelList(cmp.emptyChannels)} — ausência de dado, não R$ 0.`}
              />
            )}
            {/* Contrato comparativo ausente na API: o usuario PEDIU comparacao,
                entao isto e' indisponibilidade declarada, nunca silencio. */}
            {cmp.unsupportedChannels.length > 0 && (
              <DataQualityNote
                note={`Comparação não suportada pela API para ${channelList(cmp.unsupportedChannels)}: a resposta não trouxe a série do período anterior. A série do período atual continua exibida.`}
              />
            )}
            {cmp.windowIssue === "inconsistent" && (
              <DataQualityNote
                note={`Janelas comparativas divergentes entre canais (${cmp.windowsByChannel
                  .map((w) => `${CHANNEL_LABEL[w.channel]}: ${w.dateFrom ?? "?"} a ${w.dateTo ?? "?"}`)
                  .join(" · ")}). Somar janelas diferentes não produz um total comparável, então o período anterior não é desenhado. A série atual não é afetada.`}
              />
            )}
            {cmp.windowIssue === "unknown" && (
              <DataQualityNote
                note="A API não declarou as datas da janela anterior. Sem a janela real, a comparação fica indisponível — nenhum intervalo é inferido a partir dos pontos."
              />
            )}
            {cmpPartial && cmp.emptyChannels.length === 0 && (
              <p className="text-xs text-slate-500">
                Total do período anterior não exibido: há posições sem valor em todos os canais comparativos.
              </p>
            )}
            {/* A janela real e' exibida sempre que conhecida — inclusive quando a
                comparacao voltou VAZIA: a janela existe, os registros nao. */}
            {cmp.dateFrom && cmp.dateTo && (
              <p className="text-xs text-slate-500 tabular-nums">
                Período anterior: {cmp.dateFrom} a {cmp.dateTo}
                {showComparison ? " — alinhado ao atual pela posição do ponto." : "."}
              </p>
            )}
          </>
        )}

        {reconciliation.status === "ok" && (
          <p className="text-xs text-slate-500">Soma das séries reconciliada com o total do período.</p>
        )}
        {/* `compareActive` sem comparacao pedida nas series indica transicao de
            estado; nao inventamos aviso para isso. */}
        {compareActive && !cmp.requested && (
          <p className="text-xs text-slate-500">Preparando a série do período anterior…</p>
        )}
      </div>
    </section>
  );
}
