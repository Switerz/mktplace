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
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Marketplace } from "@/lib/mock-data";
import type { MergedBucket } from "@/lib/gerencial/trend-series";
import type { TrendMetric } from "@/lib/gerencial/request-key";
import { CHANNEL_LABEL } from "@/lib/gerencial/kpi-band";
import { CHANNEL_STROKE, TOTAL_STROKE } from "@/lib/gerencial/channel-colors";
import { fmtBrlFull, fmtNumber } from "@/lib/formatters";

interface Props {
  buckets: MergedBucket[];
  channels: Marketplace[];
  metric: TrendMetric;
  /** Total desenhavel apenas quando todos os buckets estao completos. */
  showTotal: boolean;
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
    return `${prefix}${text}M`;
  }
  if (Math.abs(value) >= 1_000) {
    const thousands = value / 1_000;
    const text = Number.isInteger(thousands) ? String(thousands) : thousands.toFixed(1).replace(".", ",");
    return `${prefix}${text}k`;
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
}: {
  active?: boolean;
  payload?: { dataKey: string; value: number | null }[];
  label?: string;
  metric: TrendMetric;
  showTotal: boolean;
}) {
  if (!active || !payload?.length) return null;
  const channelRows = payload.filter((p) => p.dataKey.startsWith("values."));
  const totalRow = payload.find((p) => p.dataKey === "total");
  return (
    <div className="bg-white border border-violet-100 rounded-xl shadow-lg px-3 py-2 text-xs min-w-[168px]">
      <p className="font-semibold text-slate-700 mb-1.5">{label}</p>
      <ul className="flex flex-col gap-0.5">
        {channelRows.map((row) => {
          const channel = row.dataKey.replace("values.", "") as Marketplace;
          return (
            <li key={channel} className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-1.5 text-slate-500">
                <span
                  aria-hidden="true"
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ background: CHANNEL_STROKE[channel] }}
                />
                {CHANNEL_LABEL[channel]}
              </span>
              <span className="tabular-nums font-medium text-slate-800">
                {row.value == null ? <span className="text-slate-400">sem dado</span> : formatValue(row.value, metric)}
              </span>
            </li>
          );
        })}
      </ul>
      {showTotal && totalRow?.value != null && (
        <p className="mt-1.5 pt-1.5 border-t border-violet-100 flex items-center justify-between gap-3">
          <span className="text-slate-500">Total</span>
          <span className="tabular-nums font-semibold text-slate-900">{formatValue(totalRow.value, metric)}</span>
        </p>
      )}
      <p className="mt-1.5 text-xs text-slate-500">Clique para abrir o detalhe do período</p>
    </div>
  );
}

export default function EvolutionChart({
  buckets,
  channels,
  metric,
  showTotal,
  highlightedChannel,
  onSelectBucket,
}: Props) {
  // Rotulos do eixo X: com muitos buckets, mostra 1 a cada N para nao empilhar.
  const interval = buckets.length > 16 ? Math.ceil(buckets.length / 12) : 0;

  // Isolamento visual do canal em foco: as demais series recuam em opacidade e
  // espessura, sem sair do grafico (o contexto de comparacao continua legivel).
  const isolating = highlightedChannel != null;
  const dim = (channel: Marketplace) => isolating && channel !== highlightedChannel;

  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart
        data={buckets}
        margin={{ top: 6, right: 8, left: 0, bottom: 0 }}
        onClick={(state) => {
          const point = state?.activePayload?.[0]?.payload as MergedBucket | undefined;
          if (point?.date) onSelectBucket(point.date);
        }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#f3f0ff" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fontSize: 12, fill: "#64748b" }}
          axisLine={false}
          tickLine={false}
          interval={interval}
          minTickGap={4}
        />
        <YAxis
          tickFormatter={(v: number) => axisTick(v, metric)}
          tick={{ fontSize: 12, fill: "#64748b" }}
          axisLine={false}
          tickLine={false}
          width={metric === "gmv" ? 62 : 48}
        />
        <Tooltip content={<ChartTooltip metric={metric} showTotal={showTotal} />} />
        {showTotal && (
          <Line
            type="monotone"
            dataKey="total"
            name="Total"
            stroke={TOTAL_STROKE}
            strokeWidth={2.25}
            strokeOpacity={isolating ? 0.18 : 1}
            dot={false}
            activeDot={{ r: 4 }}
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
            stroke={CHANNEL_STROKE[channel]}
            strokeWidth={dim(channel) ? 1 : isolating ? 2.5 : showTotal ? 1.5 : 2}
            strokeOpacity={dim(channel) ? 0.2 : 1}
            strokeDasharray={isolating ? undefined : showTotal ? "4 3" : undefined}
            dot={false}
            activeDot={{ r: 4 }}
            // Lacuna interrompe a linha: nunca costura buckets ausentes.
            connectNulls={false}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
