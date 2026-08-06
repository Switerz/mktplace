"use client";

import type { CanaisChannelMedian, CanaisChannelRow } from "@/lib/api-client";
import { formatChannelMetric, signalLabel, signalTone } from "@/lib/canais-channel-metrics";
import { buildChannelDiagnosis } from "@/lib/channel-signal-reasons";
import { buildArrivalParams } from "@/lib/brand-arrival-context";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import DrilldownContextLine from "@/components/drilldown/DrilldownContextLine";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import DrilldownCta from "@/components/drilldown/DrilldownCta";

interface Props {
  row: CanaisChannelRow;
  /** null quando o canal nao tem mediana calculada (ex: modo demonstracao) —
   * nunca inventa uma referencia de comparacao. */
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

/** Linha de métrica do canal no formato do contrato de 3 estados
 * (N/A / Sem dado / — / valor) + referência do MESMO canal na sub-linha. */
function metricEvidence(
  label: string,
  value: number | null,
  applicable: boolean,
  available: boolean,
  format: (v: number) => string,
  median?: number | null,
  medianLabel?: string,
  warning?: string | null,
) {
  const { text, tone } = formatChannelMetric(value, applicable, available, format);
  return (
    <EvidenceRow
      label={label}
      value={text}
      tone={tone}
      reference={median != null ? `${medianLabel ?? "Mediana do canal"}: ${format(median)}` : null}
      title={warning ?? undefined}
    />
  );
}

/**
 * Conteudo do drill-down marca x canal aberto a partir da matriz
 * "Comparativo entre Canais" (Gate U3; evoluído no Gate G2 para o contrato
 * de docs/DRILLDOWN_ARCHITECTURE.md §3) — reutiliza o mesmo
 * `KpiDrilldownDialog` generico, sem fetch novo (so os dados de
 * `channelRows`/`channelMedians` ja carregados pela pagina Canais).
 * Ordem: contexto → diagnóstico humano → métricas principais → evidências
 * vs referências do mesmo canal → sinais explicados → qualidade → CTA.
 */
export default function ChannelComparisonDialogContent({ row, median, periodLabel, refreshedAt, buildHref }: Props) {
  const diagnosis = buildChannelDiagnosis(row, median);
  // Destino da marca: marca/canal explícitos (vencem os filtros herdados) +
  // contexto de chegada quando existe sinal conhecido (Gate G3).
  const arrivalParams = buildArrivalParams(row.signals, row.channel, row.brand);
  const brandHref =
    `/brand/${row.brand}?brands=${row.brand}&channels=${row.channel}` +
    (arrivalParams ? `&${arrivalParams}` : "");

  return (
    <div className="flex flex-col gap-4 text-sm">
      {/* 1. Contexto (marca/canal já estão no título do diálogo) */}
      <DrilldownContextLine periodLabel={periodLabel} refreshedAt={refreshedAt} />

      {/* 2. Diagnóstico em linguagem humana — derivado só dos sinais e
          referências já carregados (channel-signal-reasons). */}
      <div>
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">O que o período mostra</p>
        <p className="text-slate-700">{diagnosis.headline}</p>
      </div>

      {/* 3. Métricas principais (par local — sem componente compartilhado:
          este é o único consumidor deste layout, ver decisão anti-registry). */}
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

      {/* 4. Evidências vs referências do mesmo canal */}
      <ul className="flex flex-col gap-2 border-t border-violet-50 pt-3">
        {metricEvidence("Ads/GMV", row.ads_gmv_pct, row.ads_applicable, row.ads_available, fmtPct1, median?.ads_gmv_pct_median)}
        {metricEvidence("ROAS", row.roas, row.ads_applicable, row.ads_available, fmtRoas, median?.roas_median)}
        {metricEvidence("ACOS", row.acos_pct, row.ads_applicable, row.ads_available, fmtPct1)}
        {metricEvidence(
          "Custo marketplace/GMV", row.marketplace_cost_pct, row.marketplace_cost_applicable,
          row.marketplace_cost_available, fmtPct1, median?.marketplace_cost_pct_median, "Mediana", row.data_warning,
        )}
        {median?.marketplace_cost_pct_p75 != null && (
          <EvidenceRow label="P75 do canal (custo marketplace/GMV)" value={fmtPct1(median.marketplace_cost_pct_p75)} tone="muted" mutedLabel />
        )}
        {metricEvidence(
          "Frete seller/GMV", row.seller_shipping_pct, row.seller_shipping_applicable,
          row.seller_shipping_available, fmtPct1, median?.seller_shipping_pct_median, "Mediana",
        )}
        {median?.seller_shipping_pct_p75 != null && (
          <EvidenceRow label="P75 do canal (frete seller/GMV)" value={fmtPct1(median.seller_shipping_pct_p75)} tone="muted" mutedLabel />
        )}
      </ul>

      {/* 5. Sinais que explicam o diagnóstico — chip + evidência textual,
          nunca chip mudo (Gate G2). */}
      <div className="flex flex-col gap-1.5">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Sinais</p>
        {diagnosis.explanations.length === 0 ? (
          <span className="text-slate-300 text-xs">Nenhum sinal no período.</span>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {diagnosis.explanations.map((e) => (
              <li key={e.signal} className="flex items-start gap-2">
                <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-semibold whitespace-nowrap shrink-0 mt-0.5 ${signalTone(e.signal)}`}>
                  {signalLabel(e.signal)}
                </span>
                <span className="text-xs text-slate-600">{e.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* 6. Qualidade/cobertura do dado */}
      <DataQualityNote note={row.data_warning} />

      {/* 7. Próximo passo — filtros preservados via buildHref. O Gate G3 anexa
          o contexto de chegada (`ctx_*`) SOMENTE quando há sinal conhecido na
          linha: só identificadores, nenhum valor desta linha vai na URL. Sem
          sinal, o CTA continua idêntico ao do G2. */}
      <div className="flex flex-col gap-1">
        {diagnosis.nextAction && <p className="text-xs text-slate-500">{diagnosis.nextAction}</p>}
        <DrilldownCta href={buildHref(brandHref)}>
          Abrir visão completa da marca →
        </DrilldownCta>
      </div>
    </div>
  );
}
