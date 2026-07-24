"use client";

import Link from "next/link";
import type { CanaisChannelMedian, CanaisChannelRow } from "@/lib/api-client";
import { formatChannelMetric, signalLabel, signalTone } from "@/lib/canais-channel-metrics";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { fmtRefreshedAt } from "@/lib/filters/format";

interface Props {
  row: CanaisChannelRow;
  /** null quando o canal nao tem mediana calculada (ex: modo demonstracao) —
   * nunca inventa uma referencia de comparacao nesse caso. */
  median: CanaisChannelMedian | null;
  periodLabel: string;
  refreshedAt: string | null;
  /** Combina o destino com os filtros globais atuais (mesmo padrao de
   * `mergeFilteredHref` usado no resto da Torre) — a marca e o canal desta
   * linha sempre sobrescrevem a selecao atual. */
  buildHref: (href: string) => string;
}

const fmtPct1 = (v: number) => `${v.toFixed(1)}%`;
const fmtRoas = (v: number) => `${v.toFixed(2)}x`;

function MetricRow({
  label, value, applicable, available, format, median, medianLabel, warning,
}: {
  label: string;
  value: number | null;
  applicable: boolean;
  available: boolean;
  format: (v: number) => string;
  median?: number | null;
  medianLabel?: string;
  warning?: string | null;
}) {
  const { text, tone } = formatChannelMetric(value, applicable, available, format);
  const toneClass = tone === "value" ? "text-slate-800 font-semibold" : tone === "warning" ? "text-amber-700" : "text-slate-400";
  return (
    <li className="flex items-center justify-between gap-3 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="text-right">
        <span className={toneClass} title={warning ?? undefined}>{text}</span>
        {median != null && (
          <span className="block text-[10px] text-slate-400 tabular-nums">
            {medianLabel ?? "Mediana do canal"}: {format(median)}
          </span>
        )}
      </span>
    </li>
  );
}

/**
 * Conteudo do drill-down marca x canal aberto a partir da matriz
 * "Comparativo entre Canais" (Gate U3, Task 4) — reutiliza o mesmo
 * `KpiDrilldownDialog` generico do Gate U2, sem fetch novo (so os dados de
 * `channelRows`/`channelMedians` ja carregados pela pagina Canais).
 */
export default function ChannelComparisonDialogContent({ row, median, periodLabel, refreshedAt, buildHref }: Props) {
  return (
    <div className="flex flex-col gap-4 text-sm">
      <div>
        <p className="text-xs text-slate-400">
          {periodLabel}
          {refreshedAt && <> · Atualizado em {fmtRefreshedAt(refreshedAt)}</>}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide">GMV</p>
          <p className="text-xl font-bold text-gray-900 tabular-nums">{fmtBrl(row.gmv)}</p>
          {median?.gmv_median != null && (
            <p className="text-[10px] text-slate-400 tabular-nums">Mediana do canal: {fmtBrl(median.gmv_median)}</p>
          )}
        </div>
        <div>
          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide">Pedidos</p>
          <p className="text-xl font-bold text-gray-900 tabular-nums">{fmtNumber(row.orders)}</p>
        </div>
      </div>

      <ul className="flex flex-col gap-2 border-t border-violet-50 pt-3">
        <MetricRow
          label="Ads/GMV" value={row.ads_gmv_pct} applicable={row.ads_applicable} available={row.ads_available}
          format={fmtPct1} median={median?.ads_gmv_pct_median}
        />
        <MetricRow
          label="ROAS" value={row.roas} applicable={row.ads_applicable} available={row.ads_available}
          format={fmtRoas} median={median?.roas_median}
        />
        <MetricRow
          label="ACOS" value={row.acos_pct} applicable={row.ads_applicable} available={row.ads_available}
          format={fmtPct1}
        />
        <MetricRow
          label="Custo marketplace/GMV" value={row.marketplace_cost_pct} applicable={row.marketplace_cost_applicable}
          available={row.marketplace_cost_available} format={fmtPct1} median={median?.marketplace_cost_pct_median}
          medianLabel="Mediana" warning={row.data_warning}
        />
        {median?.marketplace_cost_pct_p75 != null && (
          <li className="flex items-center justify-between gap-3 text-xs">
            <span className="text-slate-400">P75 do canal (custo marketplace/GMV)</span>
            <span className="text-slate-400 tabular-nums">{fmtPct1(median.marketplace_cost_pct_p75)}</span>
          </li>
        )}
        <MetricRow
          label="Frete seller/GMV" value={row.seller_shipping_pct} applicable={row.seller_shipping_applicable}
          available={row.seller_shipping_available} format={fmtPct1} median={median?.seller_shipping_pct_median}
          medianLabel="Mediana"
        />
        {median?.seller_shipping_pct_p75 != null && (
          <li className="flex items-center justify-between gap-3 text-xs">
            <span className="text-slate-400">P75 do canal (frete seller/GMV)</span>
            <span className="text-slate-400 tabular-nums">{fmtPct1(median.seller_shipping_pct_p75)}</span>
          </li>
        )}
      </ul>

      {row.data_warning && (
        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5">
          {row.data_warning}
        </p>
      )}

      <div className="flex flex-col gap-1.5">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Sinais</p>
        {row.signals.length === 0 ? (
          <span className="text-slate-300 text-xs">Nenhum sinal no período.</span>
        ) : (
          <div className="flex flex-wrap gap-1">
            {row.signals.map((s) => (
              <span key={s} className={`text-[10px] px-1.5 py-0.5 rounded-full font-semibold whitespace-nowrap ${signalTone(s)}`}>
                {signalLabel(s)}
              </span>
            ))}
          </div>
        )}
      </div>

      <Link
        href={buildHref(`/brand/${row.brand}?brands=${row.brand}&channels=${row.channel}`)}
        className="text-sm font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
      >
        Abrir visão completa da marca →
      </Link>
    </div>
  );
}
