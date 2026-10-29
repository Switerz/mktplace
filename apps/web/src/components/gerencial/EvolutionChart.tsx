"use client";

/**
 * Grafico da evolucao temporal (Gate V2-1, Task E) — carregado via
 * `next/dynamic({ ssr: false })` pelo card, para manter o recharts fora do
 * bundle inicial da Gerencial.
 *
 * Altura 100% dentro de um container `flex-1`: nao existe pixel fixo aqui. Era
 * exatamente o `height={260}` fixo do grafico antigo, combinado com
 * `items-start` na grade, que produzia a faixa branca sob o card.
 *
 * Uma linha por canal. `connectNulls={false}` e' deliberado: bucket ausente e'
 * LACUNA e a linha se interrompe — nunca e' costurado como se houvesse dado.
 */
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Marketplace } from "@/lib/mock-data";
import type { MergedBucket } from "@/lib/gerencial/trend-series";
import type { TrendMetric } from "@/lib/gerencial/request-key";
import { CHANNEL_LABEL } from "@/lib/gerencial/kpi-band";
import { CHANNEL_DOT } from "@/lib/gerencial/channel-colors";
import { fmtBrlFull, fmtNumber } from "@/lib/formatters";
import { temaDoGrafico } from "@/lib/chart-theme";
import { useTema } from "@/components/theme/ThemeProvider";

interface Props {
  buckets: MergedBucket[];
  channels: Marketplace[];
  metric: TrendMetric;
  /** Total desenhavel apenas quando todos os buckets estao completos. */
  showTotal: boolean;
  /** Gate V2-2: desenha o TOTAL do periodo anterior. So' vem `true` quando a
   * comparacao esta completa — total parcial nunca e' desenhado. */
  showComparisonTotal: boolean;
  /** Canal em foco enquanto o dialogo da serie esta aberto: as demais linhas
   * (e o total) recuam visualmente, isolando a serie sob explicacao. */
  highlightedChannel: Marketplace | null;
  onSelectBucket: (date: string) => void;
}

/**
 * Eixo Y: uma casa decimal em milhoes e milhar sem decimal desnecessario.
 * O formatador antigo usava `toFixed(0)` em milhoes, exibindo R$1,4M e R$1,6M
 * ambos como "R$2M" / "R$1M" — perda de precisao no proprio eixo.
 */
export function axisTick(value: number, metric: TrendMetric): string {
  const prefix = metric === "gmv" ? "R$ " : "";
  if (Math.abs(value) >= 1_000_000) {
    const millions = value / 1_000_000;
    const text = Number.isInteger(millions) ? String(millions) : millions.toFixed(1).replace(".", ",");
    return `${prefix}${text} mi`;
  }
  if (Math.abs(value) >= 1_000) {
    const thousands = value / 1_000;
    const text = Number.isInteger(thousands) ? String(thousands) : thousands.toFixed(1).replace(".", ",");
    return `${prefix}${text} mil`;
  }
  return `${prefix}${value}`;
}

function formatValue(value: number, metric: TrendMetric): string {
  return metric === "gmv" ? fmtBrlFull(value) : fmtNumber(value);
}

function ChartTooltip({
  active,
  payload,
  label,
  metric,
  showTotal,
  showComparisonTotal,
}: {
  active?: boolean;
  payload?: { dataKey: string; value: number | null; payload?: MergedBucket }[];
  label?: string;
  metric: TrendMetric;
  showTotal: boolean;
  showComparisonTotal: boolean;
}) {
  if (!active || !payload?.length) return null;
  const channelRows = payload.filter((p) => p.dataKey.startsWith("values."));
  const totalRow = payload.find((p) => p.dataKey === "total");
  // O bucket completo vem no payload: dele saem a data e o rotulo REAIS do
  // periodo anterior, nunca a data atual deslocada.
  const bucket = payload[0]?.payload;
  const cmp = bucket?.comparison ?? null;
  return (
    <div className="bg-surface border border-line rounded-xl shadow-lg px-3 py-2 text-xs min-w-[168px]">
      <p className="font-semibold text-ink-muted">{label}</p>
      {showComparisonTotal && (
        <p className="text-xs font-semibold text-ink-faint uppercase tracking-wide mb-1">Período atual</p>
      )}
      {!showComparisonTotal && <span className="block mb-1.5" />}
      <ul className="flex flex-col gap-0.5">
        {channelRows.map((row) => {
          const channel = row.dataKey.replace("values.", "") as Marketplace;
          return (
            <li key={channel} className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-1.5 text-ink-faint">
                <span
                  aria-hidden="true"
                  className={`w-2 h-2 rounded-full shrink-0 ${CHANNEL_DOT[channel]}`}
                />
                {CHANNEL_LABEL[channel]}
              </span>
              <span className="tabular-nums font-medium text-ink">
                {row.value == null ? <span className="text-ink-faint">sem dado</span> : formatValue(row.value, metric)}
              </span>
            </li>
          );
        })}
      </ul>
      {showTotal && totalRow?.value != null && (
        <p className="mt-1.5 pt-1.5 border-t border-line flex items-center justify-between gap-3">
          <span className="text-ink-faint">Total</span>
          <span className="tabular-nums font-semibold text-ink">{formatValue(totalRow.value, metric)}</span>
        </p>
      )}
      {showComparisonTotal && (
        <div className="mt-1.5 pt-1.5 border-t border-line">
          <p className="text-xs font-semibold text-ink-faint uppercase tracking-wide">Período anterior</p>
          {cmp ? (
            <>
              <p className="text-xs text-ink-faint tabular-nums">{cmp.label} · {cmp.date}</p>
              <p className="flex items-center justify-between gap-3">
                <span className="text-ink-faint">Total anterior</span>
                <span className="tabular-nums font-medium text-ink-muted">
                  {cmp.total != null ? formatValue(cmp.total, metric) : <span className="text-ink-faint">incompleto</span>}
                </span>
              </p>
            </>
          ) : (
            <p className="text-xs text-ink-faint">Sem ponto correspondente na janela anterior.</p>
          )}
        </div>
      )}
      <p className="mt-1.5 text-xs text-ink-faint">Clique para abrir o detalhe do período</p>
    </div>
  );
}

export default function EvolutionChart({
  buckets,
  channels,
  metric,
  showTotal,
  showComparisonTotal,
  highlightedChannel,
  onSelectBucket,
}: Props) {
  // Rotulos do eixo X (Gate V2-3, correcao consolidada).
  //
  // Antes: `interval = 0` para <=16 buckets, o que em Recharts significa
  // "renderize TODOS os rotulos" e faz `minTickGap` ser IGNORADO. No grao
  // semanal do V2-2 o rotulo ficou mais largo (`Sem. 29/06` ~60px contra ~34px
  // de `29/06`), e cinco deles nao cabem nos ~308px de area util do mobile de
  // 390px: medido, dois pares colidiam com folga minima de -1px.
  //
  // Agora `preserveStartEnd` + `minTickGap` deixa o proprio Recharts derrubar os
  // rotulos do meio que nao caibam, sempre preservando o primeiro e o ultimo.
  // Vale para todos os graos (o diario com 30 buckets tinha o mesmo problema no
  // mobile) e nao mexe em bucket, granularidade, comparacao nem requisicao. A
  // fonte permanece em 12px: nada e' encolhido para caber.
  const interval = "preserveStartEnd" as const;

  // Isolamento visual do canal em foco: as demais series recuam em opacidade e
  // espessura, sem sair do grafico (o contexto de comparacao continua legivel).
  const isolating = highlightedChannel != null;
  const dim = (channel: Marketplace) => isolating && channel !== highlightedChannel;
  // SVG nao le classe de token: a cor resolvida vem da paleta do tema.
  const tema = temaDoGrafico(useTema().resolvido);

  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart
        data={buckets}
        // `right: 28` (era 8) reserva a metade do ultimo rotulo, que o Recharts
        // centra no ultimo ponto — ele ficava 22px fora da area do grafico e
        // aparecia cortado na borda do card no mobile.
        margin={{ top: 6, right: 28, left: 0, bottom: 0 }}
        onClick={(state) => {
          const point = state?.activePayload?.[0]?.payload as MergedBucket | undefined;
          if (point?.date) onSelectBucket(point.date);
        }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke={tema.grade} vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fontSize: 12, fill: tema.eixo }}
          axisLine={false}
          tickLine={false}
          interval={interval}
          // Folga suficiente para o rotulo semanal (`Sem. 29/06`) nunca encostar
          // no vizinho. Era 4px, que so' servia ao rotulo diario curto.
          minTickGap={20}
        />
        <YAxis
          tickFormatter={(v: number) => axisTick(v, metric)}
          tick={{ fontSize: 12, fill: tema.eixo }}
          axisLine={false}
          tickLine={false}
          // UI-DENSIDADE-1: rotulo em pt-BR ("R$ 900 mil") cabe numa linha so'.
          width={metric === "gmv" ? 78 : 56}
        />
        <Tooltip
          content={
            <ChartTooltip metric={metric} showTotal={showTotal} showComparisonTotal={showComparisonTotal} />
          }
        />
        {/* UI-DENSIDADE-1: o TOTAL e' a leitura principal — area suave por baixo
            da linha mais grossa. Canais ficam finos e claros, de apoio. */}
        {showTotal && (
          <Area
            type="monotone"
            dataKey="total"
            name="Total"
            fill={tema.total}
            fillOpacity={isolating ? 0.03 : 0.12}
            stroke="none"
            activeDot={false}
            legendType="none"
            tooltipType="none"
            connectNulls={false}
            isAnimationActive={false}
          />
        )}
        {showTotal && (
          <Line
            type="monotone"
            dataKey="total"
            name="Total"
            stroke={tema.total}
            strokeWidth={2.75}
            strokeOpacity={isolating ? 0.18 : 1}
            dot={false}
            activeDot={{ r: 4 }}
            connectNulls={false}
            isAnimationActive={false}
          />
        )}
        {/* Periodo anterior: UMA linha (o total), tracejada e neutra. Fica atras
            das series atuais na ordem de render e nao concorre com a leitura
            principal. O tracejado distingue sem depender de cor. */}
        {showComparisonTotal && (
          <Line
            type="monotone"
            dataKey="comparison.total"
            name="Total do período anterior"
            stroke={tema.referencia}
            strokeWidth={1.75}
            strokeDasharray="6 4"
            strokeOpacity={isolating ? 0.15 : 0.9}
            dot={false}
            activeDot={{ r: 3 }}
            connectNulls={false}
            isAnimationActive={false}
          />
        )}
        {channels.map((channel) => (
          <Line
            key={channel}
            type="monotone"
            dataKey={`values.${channel}`}
            name={CHANNEL_LABEL[channel]}
            stroke={tema.canais[channel]}
            strokeWidth={dim(channel) ? 1 : isolating ? 2.5 : showTotal ? 1.25 : 2}
            strokeOpacity={dim(channel) ? 0.2 : 1}
            // De apoio quando o total aparece: fino, solido e mais claro.
            opacity={!isolating && showTotal ? 0.55 : 1}
            dot={false}
            activeDot={{ r: 4 }}
            // Lacuna interrompe a linha: nunca costura buckets ausentes.
            connectNulls={false}
            isAnimationActive={false}
          />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
