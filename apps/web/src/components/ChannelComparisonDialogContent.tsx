"use client";

import type { CanaisChannelMedian, CanaisChannelRow } from "@/lib/api-client";
import { formatChannelMetric, signalLabel, signalTone } from "@/lib/canais-channel-metrics";
import { buildChannelDiagnosis } from "@/lib/channel-signal-reasons";
import { buildArrivalParams } from "@/lib/brand-arrival-context";
import { CHANNEL_DOT } from "@/lib/gerencial/channel-colors";
import { fmtBrlCurto, fmtBrlFull, fmtNumber } from "@/lib/formatters";
import DrilldownContextLine from "@/components/drilldown/DrilldownContextLine";
import DrilldownSecao from "@/components/drilldown/DrilldownSecao";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import DrilldownCta from "@/components/drilldown/DrilldownCta";
import InfoDica from "@/components/ui/InfoDica";
import NumeroAnimado from "@/components/ui/NumeroAnimado";

interface Props {
  row: CanaisChannelRow;
  /** null quando o canal nao tem mediana calculada (ex: modo demonstracao) —
   * nunca inventa uma referencia de comparacao. */
  median: CanaisChannelMedian | null;
  /**
   * POPUPS-1 — as linhas marca x canal que a matriz JA exibe (mesmo
   * `displayChannelRows` da pagina, vazio fora do frescor). Alimentam as
   * quebras "marca por canal" e "canal por marca"; nenhum fetch novo.
   */
  rows?: readonly CanaisChannelRow[];
  periodLabel: string;
  refreshedAt: string | null;
  /** Combina o destino com os filtros globais atuais (mesmo padrao de
   * `mergeFilteredHref` usado no resto da Torre) — a marca e o canal desta
   * linha sempre sobrescrevem a selecao atual. */
  buildHref: (href: string) => string;
}

// POPUPS-1 — decimal pt-BR (antes `toFixed`, que imprimia ponto).
const dec = (v: number, casas: number) =>
  v.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
const fmtPct1 = (v: number) => `${dec(v, 1)}%`;
const fmtRoas = (v: number) => `${dec(v, 2)}x`;

/** O que as referencias sao — no ⓘ, nunca em paragrafo. */
const REFERENCIAS =
  "Referências do MESMO canal, entre as marcas com dado: mediana e P75. " +
  "N/A = não se aplica ao canal · Sem dado = deveria existir, mas falta · — = denominador zero.";

/** Referencia da linha: "Mediana X · P75 Y", so' com o que existe. */
function referencia(
  format: (v: number) => string,
  mediana: number | null | undefined,
  p75?: number | null,
): string | null {
  const partes = [
    mediana != null ? `Mediana ${format(mediana)}` : null,
    p75 != null ? `P75 ${format(p75)}` : null,
  ].filter(Boolean);
  return partes.length ? partes.join(" · ") : null;
}

/** Linha de métrica do canal no formato do contrato de 3 estados
 * (N/A / Sem dado / — / valor) + referência do MESMO canal na sub-linha. */
function MetricaDoCanal({
  label,
  value,
  applicable,
  available,
  format,
  reference,
  warning,
}: {
  label: string;
  value: number | null;
  applicable: boolean;
  available: boolean;
  format: (v: number) => string;
  reference?: string | null;
  warning?: string | null;
}) {
  const { text, tone } = formatChannelMetric(value, applicable, available, format);
  return <EvidenceRow label={label} value={text} tone={tone} reference={reference} title={warning ?? undefined} />;
}

const participacao = (v: number, total: number) => (total > 0 ? (v / total) * 100 : null);

/**
 * Conteudo do drill-down marca x canal aberto a partir da matriz
 * "Comparativo entre Canais" (Gate U3; evoluído no Gate G2 para o contrato
 * de docs/DRILLDOWN_ARCHITECTURE.md §3) — reutiliza o mesmo
 * `KpiDrilldownDialog` generico, sem fetch novo (so os dados de
 * `channelRows`/`channelMedians` ja carregados pela pagina Canais).
 *
 * POPUPS-1 — GMV grande no topo, sinais em selos (a evidencia de cada um no
 * ⓘ), indicadores contra a mediana/P75 do canal numa linha so', e as duas
 * quebras que respondem a proxima pergunta: onde mais a marca vende e que
 * posicao ela ocupa neste canal. Sai a frase de diagnostico (repetia os
 * selos) e o texto de proximo passo (o botao ja' diz).
 */
export default function ChannelComparisonDialogContent({
  row,
  median,
  rows = [],
  periodLabel,
  refreshedAt,
  buildHref,
}: Props) {
  const diagnosis = buildChannelDiagnosis(row, median);
  // Destino da marca: marca/canal explícitos (vencem os filtros herdados) +
  // contexto de chegada quando existe sinal conhecido (Gate G3).
  const arrivalParams = buildArrivalParams(row.signals, row.channel, row.brand);
  const brandHref =
    `/brand/${row.brand}?brands=${row.brand}&channels=${row.channel}` +
    (arrivalParams ? `&${arrivalParams}` : "");

  // Quebras so' com as linhas ja' exibidas na matriz.
  const daMarca = rows.filter((r) => r.brand === row.brand).sort((a, b) => b.gmv - a.gmv);
  const doCanal = rows.filter((r) => r.channel === row.channel).sort((a, b) => b.gmv - a.gmv);
  const totalMarca = daMarca.reduce((s, r) => s + r.gmv, 0);
  const totalCanal = doCanal.reduce((s, r) => s + r.gmv, 0);
  const posicao = doCanal.findIndex((r) => r.brand === row.brand) + 1;

  return (
    <div className="flex flex-col gap-4 text-sm">
      {/* 1. Numero principal + contexto (marca/canal ja' estao no titulo). */}
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-end gap-x-3 gap-y-1.5">
          <NumeroAnimado
            texto={fmtBrlFull(row.gmv)}
            className="text-[32px] font-bold leading-none tracking-tight text-ink tabular-nums"
          />
          <span className="pb-0.5 text-xs text-ink-faint tabular-nums">
            GMV · {fmtNumber(row.orders)} pedidos
            {median?.gmv_median != null && ` · mediana do canal ${fmtBrlCurto(median.gmv_median)}`}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <DrilldownContextLine periodLabel={periodLabel} refreshedAt={refreshedAt} />
          <InfoDica texto={REFERENCIAS} />
        </div>
        {/* Sinais em selos — chip nunca mudo: a evidencia de cada um, montada
            so' com o dado carregado (channel-signal-reasons), fica no ⓘ. */}
        {diagnosis.explanations.length > 0 ? (
          <ul className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5" aria-label="Sinais do período">
            {diagnosis.explanations.map((e) => (
              <li key={e.signal} className="flex items-center gap-1">
                <span className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${signalTone(e.signal)}`}>
                  {signalLabel(e.signal)}
                </span>
                <InfoDica texto={e.reason} />
              </li>
            ))}
          </ul>
        ) : (
          <span className="w-fit rounded-full border border-line px-2 py-0.5 text-xs text-ink-faint">Sem sinal no período</span>
        )}
        {/* Qualidade/cobertura do dado: muda a leitura, fica junto do numero. */}
        <DataQualityNote note={row.data_warning} />
      </div>

      {/* 2. Indicadores contra a referencia do MESMO canal (mediana · P75). */}
      <DrilldownSecao titulo="Contra o canal" acao={<span>mediana · P75</span>}>
        <ul className="flex flex-col">
          <MetricaDoCanal
            label="Ads/GMV" value={row.ads_gmv_pct} applicable={row.ads_applicable} available={row.ads_available}
            format={fmtPct1} reference={referencia(fmtPct1, median?.ads_gmv_pct_median)}
          />
          <MetricaDoCanal
            label="ROAS" value={row.roas} applicable={row.ads_applicable} available={row.ads_available}
            format={fmtRoas} reference={referencia(fmtRoas, median?.roas_median)}
          />
          <MetricaDoCanal
            label="ACOS" value={row.acos_pct} applicable={row.ads_applicable} available={row.ads_available}
            format={fmtPct1}
          />
          <MetricaDoCanal
            label="Custo marketplace/GMV" value={row.marketplace_cost_pct}
            applicable={row.marketplace_cost_applicable} available={row.marketplace_cost_available} format={fmtPct1}
            reference={referencia(fmtPct1, median?.marketplace_cost_pct_median, median?.marketplace_cost_pct_p75)}
            warning={row.data_warning}
          />
          <MetricaDoCanal
            label="Frete seller/GMV" value={row.seller_shipping_pct}
            applicable={row.seller_shipping_applicable} available={row.seller_shipping_available} format={fmtPct1}
            reference={referencia(fmtPct1, median?.seller_shipping_pct_median, median?.seller_shipping_pct_p75)}
          />
        </ul>
      </DrilldownSecao>

      {/* 3. Onde mais a marca vende — participacao no GMV da marca entre os
          canais exibidos. A linha aberta fica em destaque; as outras, suaves. */}
      {daMarca.length > 1 && (
        <DrilldownSecao titulo={`${row.label} por canal`}>
          <ul className="flex flex-col">
            {daMarca.map((r) => {
              const pct = participacao(r.gmv, totalMarca);
              return (
                <EvidenceRow
                  key={r.channel}
                  label={r.channel_label}
                  ponto={CHANNEL_DOT[r.channel]}
                  barra={CHANNEL_DOT[r.channel]}
                  pct={pct}
                  mutedLabel={r.channel !== row.channel}
                  value={`${fmtBrlCurto(r.gmv)}${pct != null ? ` · ${fmtPct1(pct)}` : ""}`}
                />
              );
            })}
          </ul>
        </DrilldownSecao>
      )}

      {/* 4. Posicao da marca no canal — participacao no GMV do canal entre as
          marcas exibidas, do maior ao menor. */}
      {doCanal.length > 1 && (
        <DrilldownSecao
          titulo={`${row.channel_label} por marca`}
          acao={posicao > 0 && <span className="tabular-nums">{posicao}º de {doCanal.length}</span>}
        >
          <ul className="flex flex-col">
            {doCanal.map((r) => {
              const pct = participacao(r.gmv, totalCanal);
              return (
                <EvidenceRow
                  key={r.brand}
                  label={r.label}
                  barra={CHANNEL_DOT[r.channel]}
                  pct={pct}
                  mutedLabel={r.brand !== row.brand}
                  value={`${fmtBrlCurto(r.gmv)}${pct != null ? ` · ${fmtPct1(pct)}` : ""}`}
                />
              );
            })}
          </ul>
        </DrilldownSecao>
      )}

      {/* 5. Próximo passo — filtros preservados via buildHref. O Gate G3 anexa
          o contexto de chegada (`ctx_*`) SOMENTE quando há sinal conhecido na
          linha: só identificadores, nenhum valor desta linha vai na URL. Sem
          sinal, o CTA continua idêntico ao do G2. */}
      <DrilldownCta href={buildHref(brandHref)}>
        Abrir visão completa da marca →
      </DrilldownCta>
    </div>
  );
}
