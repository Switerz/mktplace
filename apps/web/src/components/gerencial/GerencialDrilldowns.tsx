"use client";

/**
 * Conteudos de drill-down proprios da Gerencial V2 (Gate V2-1, com as correcoes
 * das Tasks B, D e F da rodada consolidada).
 *
 * Todos seguem o contrato transversal do Gate G2 (docs/DRILLDOWN_ARCHITECTURE.md
 * §3): contexto -> diagnostico -> evidencia -> limitacao -> acao, compostos com
 * os quatro primitives existentes. Nenhum shell novo: sao renderizados dentro do
 * unico `KpiDrilldownDialog`.
 */
import { useEffect, useState } from "react";
import DrilldownContextLine from "@/components/drilldown/DrilldownContextLine";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import DrilldownCta from "@/components/drilldown/DrilldownCta";
import { fetchBrands, fetchOverview, type BrandRow, type OverviewData } from "@/lib/api-client";
import type { Marketplace } from "@/lib/mock-data";
import { fmtBrlFull, fmtNumber, fmtPct } from "@/lib/formatters";
import { CHANNEL_LABEL } from "@/lib/gerencial/kpi-band";
import type {
  ConfidenceStripData,
  CommercialAttentionItem,
  DataConfidenceItem,
} from "@/lib/gerencial/attention";
import { AVAILABILITY_LABEL, REFERENCE_KIND_LABEL } from "@/lib/gerencial/attention";
import type { MergedBucket, MergedSeries } from "@/lib/gerencial/trend-series";
import { bucketRange } from "@/lib/gerencial/trend-series";
import type { TrendMetric } from "@/lib/gerencial/request-key";
import type { BrandChannelMatrix, ConcentrationEntry, Movement } from "@/lib/gerencial/brand-matrix";
import { PRODUCTS_SCOPE_NOTE } from "@/lib/gerencial/brand-matrix";
import type { VolumeHealthRow } from "@/lib/gerencial/volume-health";
import { CANCEL_RATE_FORMULA, NO_CROSS_CHANNEL_RANKING } from "@/lib/gerencial/volume-health";

const SECTION_LABEL = "text-xs font-semibold text-slate-500 uppercase tracking-wide";

function metricLabel(metric: TrendMetric): string {
  return metric === "gmv" ? "GMV" : "Pedidos";
}

function formatMetric(value: number, metric: TrendMetric): string {
  return metric === "gmv" ? fmtBrlFull(value) : fmtNumber(value);
}

// ---------------------------------------------------------------------------
// Confianca no dado (Task F — disponibilidade de serie, nao "cobertura")
// ---------------------------------------------------------------------------

export function ConfidenceDrilldownContent({
  data,
  items,
  periodLabel,
  refreshedAt,
  regioesHref,
}: {
  data: ConfidenceStripData;
  items: DataConfidenceItem[];
  periodLabel: string;
  refreshedAt: string | null;
  regioesHref: string;
}) {
  return (
    <div className="flex flex-col gap-4 text-sm">
      <DrilldownContextLine periodLabel={periodLabel} refreshedAt={refreshedAt} />

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>Disponibilidade da série por canal</p>
        <ul className="flex flex-col gap-1">
          {data.channels.map((c) => (
            <EvidenceRow
              key={c.channel}
              label={c.label}
              value={AVAILABILITY_LABEL[c.availability]}
              tone={
                c.availability === "available"
                  ? "value"
                  : c.availability === "unavailable"
                    ? "warning"
                    : "muted"
              }
              reference={
                c.availability === "available"
                  ? "Há registros no período — inclusive registros de valor zero"
                  : c.availability === "no_records"
                    ? "A fonte respondeu, mas não há linhas no período"
                    : null
              }
            />
          ))}
        </ul>
      </div>

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>Defasagem e avisos</p>
        {!data.warningsChecked ? (
          <p className="text-xs text-slate-600">
            O resumo executivo não respondeu nesta carga, então defasagem e avisos de dado{" "}
            <strong>não foram verificados</strong>. Isso não é o mesmo que ausência de avisos.
          </p>
        ) : data.maxStalenessDays != null ? (
          <EvidenceRow
            label="Maior defasagem observada"
            value={`${data.maxStalenessDays} dia(s)`}
            tone={data.maxStalenessDays > 2 ? "warning" : "value"}
            reference={data.sources.length ? `Fontes: ${data.sources.join(", ")}` : null}
          />
        ) : (
          <p className="text-xs text-slate-500">
            Nenhum aviso de defasagem para o período — o que não é o mesmo que garantir que toda fonte está
            atualizada até hoje.
          </p>
        )}
      </div>

      {data.warningsChecked && (
        <div className="flex flex-col gap-1.5">
          <p className={SECTION_LABEL}>Avisos ativos ({items.length})</p>
          {items.length === 0 ? (
            <p className="text-xs text-slate-500">Nenhum aviso de confiança no dado neste período.</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {items.map((item) => (
                <li key={item.key} className="text-xs text-slate-700 leading-snug">
                  {item.message}
                  {(item.source || item.lastDate) && (
                    <span className="block text-xs text-slate-500 tabular-nums">
                      {item.source && <>fonte {item.source}</>}
                      {item.lastDate && <> · até {item.lastDate}</>}
                      {item.thresholdDays != null && <> · limiar {item.thresholdDays}d</>}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* O limite epistemico da faixa, dito sem rodeio. */}
      <DataQualityNote note="Disponibilidade de série indica que a fonte respondeu com registros no período. Ela NÃO comprova completude do dado: um canal pode ter série disponível e ainda assim estar com carga parcial ou atrasada." />
      <DrilldownCta href={regioesHref}>Ver cobertura regional em Regiões →</DrilldownCta>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Ponto da serie (Task D1: guarda de mock; Task D2: intervalo por grao)
// ---------------------------------------------------------------------------

type DetailStatus = "loading" | "live" | "unavailable" | "demo";

interface BucketDetailState {
  overview: OverviewData | null;
  brands: BrandRow[] | null;
  overviewStatus: DetailStatus;
  brandsStatus: DetailStatus;
}

/** Traduz uma resposta em status, aplicando a guarda de mock da pagina. */
function detailStatus(ok: boolean, live: boolean, demoMode: boolean): DetailStatus {
  if (!ok) return "unavailable";
  if (live) return "live";
  // Resposta veio, mas nao e' live: e' mock. Em pagina live isso e'
  // indisponibilidade; em pagina demonstrativa, e' mock rotulado.
  return demoMode ? "demo" : "unavailable";
}

const USABLE: DetailStatus[] = ["live", "demo"];

/**
 * Detalhe de um ponto da serie. Carrega `overview` + `brands` **sob demanda**
 * para aquele recorte, reutilizando os endpoints existentes — nenhum endpoint
 * novo. Os filtros globais NAO mudam ao abrir.
 *
 * Guarda de mock (Task D1): `fetchOverview`/`fetchBrands` nao rejeitam — sem API
 * devolvem mock com `live: false`. Em pagina live, esses numeros mockados **nao
 * sao renderizados**; cada fonte tem estado proprio, entao uma pode aparecer
 * enquanto a outra e' nomeada como ausente.
 */
export function TrendBucketDrilldownContent({
  bucket,
  merged,
  metric,
  channels,
  granularityLabel,
  periodLabel,
  brandsFilter,
  demoMode,
  onPinRange,
}: {
  bucket: MergedBucket;
  merged: MergedSeries;
  metric: TrendMetric;
  channels: Marketplace[];
  granularityLabel: string;
  periodLabel: string;
  brandsFilter: string[];
  demoMode: boolean;
  onPinRange: (dateFrom: string, dateTo: string) => void;
}) {
  const [state, setState] = useState<BucketDetailState>({
    overview: null,
    brands: null,
    overviewStatus: "loading",
    brandsStatus: "loading",
  });

  const range = bucketRange(bucket.date, merged.granularity);

  useEffect(() => {
    let ignore = false;
    setState({ overview: null, brands: null, overviewStatus: "loading", brandsStatus: "loading" });
    const opts = { brands: brandsFilter, dateFrom: range.dateFrom, dateTo: range.dateTo, compare: false };

    // Estados INDEPENDENTES: uma fonte em falha nao apaga a outra.
    fetchOverview(channels, undefined, opts)
      .then((res) => {
        if (ignore) return;
        const status = detailStatus(true, res.live, demoMode);
        setState((s) => ({
          ...s,
          overview: USABLE.includes(status) ? res.data : null,
          overviewStatus: status,
        }));
      })
      .catch(() => {
        if (ignore) return;
        setState((s) => ({ ...s, overview: null, overviewStatus: "unavailable" }));
      });

    fetchBrands(channels, undefined, opts)
      .then((res) => {
        if (ignore) return;
        const status = detailStatus(true, res.live, demoMode);
        setState((s) => ({
          ...s,
          brands: USABLE.includes(status) ? res.data : null,
          brandsStatus: status,
        }));
      })
      .catch(() => {
        if (ignore) return;
        setState((s) => ({ ...s, brands: null, brandsStatus: "unavailable" }));
      });

    return () => {
      ignore = true;
    };
  }, [range.dateFrom, range.dateTo, channels, brandsFilter, demoMode]);

  const position = merged.buckets.findIndex((b) => b.date === bucket.date) + 1;
  const anyLoading = state.overviewStatus === "loading" || state.brandsStatus === "loading";
  const overviewUsable = USABLE.includes(state.overviewStatus) && state.overview != null;
  const brandsUsable = USABLE.includes(state.brandsStatus) && state.brands != null;
  const bothUnavailable =
    state.overviewStatus === "unavailable" && state.brandsStatus === "unavailable";
  const isDemo = state.overviewStatus === "demo" || state.brandsStatus === "demo";

  return (
    <div className="flex flex-col gap-4 text-sm">
      <div>
        <p className="text-2xl font-bold text-gray-900 tabular-nums leading-none">
          {bucket.total != null ? (
            formatMetric(bucket.total, metric)
          ) : (
            <span className="text-slate-400">Total indisponível</span>
          )}
        </p>
        <div className="mt-1">
          <DrilldownContextLine
            leading={`${bucket.label} · ${granularityLabel}`}
            periodLabel={periodLabel}
            refreshedAt={null}
          />
        </div>
        <p className="text-xs text-slate-500 mt-0.5">
          Ponto {position} de {merged.buckets.length} na série de {metricLabel(metric)}
        </p>
      </div>

      {/* Evidencia que NAO depende de fetch: ja veio com a serie. */}
      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>{metricLabel(metric)} por canal neste ponto</p>
        <ul className="flex flex-col gap-1">
          {channels.map((channel) => {
            const value = bucket.values[channel];
            return (
              <EvidenceRow
                key={channel}
                label={CHANNEL_LABEL[channel]}
                value={value == null ? "Sem dado neste ponto" : formatMetric(value, metric)}
                tone={value == null ? "muted" : "value"}
              />
            );
          })}
        </ul>
        {bucket.total == null && (
          <p className="text-xs text-slate-500 mt-1">
            O total não é exibido porque pelo menos um canal selecionado não tem valor neste ponto.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>
          Detalhe {merged.granularity === "day" ? "do dia" : "da competência"}
        </p>

        {anyLoading ? (
          <p className="text-xs text-slate-500" role="status" aria-busy="true">
            Carregando detalhe…
          </p>
        ) : bothUnavailable ? (
          <p className="text-xs text-rose-700">
            Detalhe indisponível nesta carga: nem o agregado nem o desempenho por marca responderam. Os valores
            por canal acima vêm da série já carregada e continuam válidos.
          </p>
        ) : (
          <>
            {overviewUsable ? (
              <ul className="flex flex-col gap-1">
                <EvidenceRow label="GMV do recorte" value={fmtBrlFull(state.overview!.gmv)} />
                <EvidenceRow label="Pedidos do recorte" value={fmtNumber(state.overview!.orders)} />
              </ul>
            ) : (
              <p className="text-xs text-slate-500">Agregado do recorte indisponível nesta carga.</p>
            )}

            {brandsUsable && state.brands!.filter((b) => b.total_gmv > 0).length > 0 ? (
              <>
                <p className="text-xs text-slate-500 mt-1.5">Por marca</p>
                <ul className="flex flex-col gap-1">
                  {state.brands!
                    .filter((b) => b.total_gmv > 0)
                    .slice(0, 5)
                    .map((b) => (
                      <EvidenceRow key={b.brand} label={b.label} value={fmtBrlFull(b.total_gmv)} />
                    ))}
                </ul>
              </>
            ) : (
              <p className="text-xs text-slate-500 mt-1.5">
                Desempenho por marca indisponível nesta carga.
              </p>
            )}

            {/* Parcial: nomeia exatamente a fonte ausente. */}
            {overviewUsable !== brandsUsable && (
              <div className="mt-1.5">
                <DataQualityNote
                  note={`Detalhe parcial: ${
                    overviewUsable ? "o desempenho por marca" : "o agregado do recorte"
                  } não respondeu nesta carga. O que está acima veio da fonte que respondeu.`}
                />
              </div>
            )}
            {isDemo && (
              <div className="mt-1.5">
                <DataQualityNote note="Detalhe em modo demonstração — a Torre inteira está com dados de demonstração nesta sessão." />
              </div>
            )}
          </>
        )}
      </div>

      {/* Task D2: o CTA promete exatamente o recorte que aplica. */}
      <button
        type="button"
        onClick={() => onPinRange(range.dateFrom, range.dateTo)}
        className="inline-flex items-center min-h-11 self-start text-sm font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
      >
        Fixar {range.label} como período →
      </button>
      <p className="text-xs text-slate-500 -mt-2 tabular-nums">
        Aplicará {range.dateFrom} a {range.dateTo}. Canais, marcas e comparação são preservados; apenas o
        intervalo de datas muda.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Serie de um canal (Task B.1)
// ---------------------------------------------------------------------------

export function ChannelSeriesDrilldownContent({
  channel,
  merged,
  metric,
  periodLabel,
  canaisHref,
}: {
  channel: Marketplace;
  merged: MergedSeries;
  metric: TrendMetric;
  periodLabel: string;
  canaisHref: string;
}) {
  // Tudo derivado da serie JA carregada — zero fetch novo.
  const values = merged.buckets.map((b) => b.values[channel]);
  const numeric = values.filter((v): v is number => typeof v === "number");
  const gaps = values.length - numeric.length;
  const sum = numeric.reduce((a, v) => a + v, 0);
  const failed = merged.failedChannels.includes(channel);
  const empty = merged.emptyChannels.includes(channel);

  // Participacao só existe quando todos os canais selecionados estao frescos —
  // um denominador parcial produziria um share inflado.
  const totalAllChannels = merged.everyBucketComplete
    ? merged.buckets.reduce((a, b) => a + (b.total ?? 0), 0)
    : null;
  const sharePct = totalAllChannels != null && totalAllChannels > 0 ? (sum / totalAllChannels) * 100 : null;

  const max = numeric.length ? Math.max(...numeric) : null;
  const peak = max != null ? merged.buckets.find((b) => b.values[channel] === max) : undefined;

  return (
    <div className="flex flex-col gap-4 text-sm">
      <DrilldownContextLine leading={CHANNEL_LABEL[channel]} periodLabel={periodLabel} refreshedAt={null} />

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>Disponibilidade</p>
        {failed ? (
          <p className="text-xs text-amber-800">
            A série deste canal não respondeu nesta carga. As demais séries continuam no gráfico e o total por
            ponto não é apresentado como completo.
          </p>
        ) : empty ? (
          <p className="text-xs text-slate-600">
            A fonte respondeu, mas não há registros deste canal no período — ausência de dado, não zero.
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            <EvidenceRow
              label="Pontos com valor"
              value={`${numeric.length} de ${values.length}`}
              reference={gaps > 0 ? `${gaps} ponto(s) sem registro — a linha se interrompe` : "série sem lacunas"}
            />
          </ul>
        )}
      </div>

      {numeric.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className={SECTION_LABEL}>{metricLabel(metric)} no período</p>
          <ul className="flex flex-col gap-1">
            <EvidenceRow label="Soma dos pontos deste canal" value={formatMetric(sum, metric)} />
            <EvidenceRow
              label="Participação no total"
              value={sharePct != null ? `${sharePct.toFixed(1)}%` : "Indisponível"}
              tone={sharePct != null ? "value" : "muted"}
              reference={
                sharePct == null
                  ? "Exige todos os canais selecionados com série completa — o denominador parcial seria enganoso"
                  : null
              }
            />
            {peak && max != null && (
              <EvidenceRow label="Maior ponto" value={formatMetric(max, metric)} reference={peak.label} />
            )}
          </ul>
        </div>
      )}

      <DataQualityNote note="A soma acima é dos pontos deste canal na série exibida. Ela não substitui o agregado do período, que tem a própria cláusula de filtro." />
      <DrilldownCta href={canaisHref}>Abrir este canal em Canais →</DrilldownCta>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cabecalho de canal da matriz (Task B.2)
// ---------------------------------------------------------------------------

export function MatrixChannelDrilldownContent({
  channel,
  matrix,
  periodLabel,
  refreshedAt,
  canaisHref,
}: {
  channel: Marketplace;
  matrix: BrandChannelMatrix;
  periodLabel: string;
  refreshedAt: string | null;
  canaisHref: string;
}) {
  // Evidencia derivada da matriz JA carregada — zero fetch novo.
  const total = matrix.channelTotals[channel] ?? 0;
  const rows = matrix.rows
    .map((r) => ({ label: r.label, cell: r.cells.find((c) => c.channel === channel) }))
    .filter((r) => r.cell != null);
  const withData = rows.filter((r) => r.cell!.available);
  const withoutData = rows.filter((r) => !r.cell!.available);

  return (
    <div className="flex flex-col gap-4 text-sm">
      <div>
        <p className="text-2xl font-bold text-gray-900 tabular-nums leading-none">{fmtBrlFull(total)}</p>
        <div className="mt-1">
          <DrilldownContextLine
            leading={`${CHANNEL_LABEL[channel]} · GMV do canal`}
            periodLabel={periodLabel}
            refreshedAt={refreshedAt}
          />
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>Distribuição entre as marcas</p>
        <ul className="flex flex-col gap-1">
          {withData
            .sort((a, b) => (b.cell!.gmv ?? 0) - (a.cell!.gmv ?? 0))
            .map((r) => (
              <EvidenceRow
                key={r.label}
                label={r.label}
                value={fmtBrlFull(r.cell!.gmv as number)}
                reference={
                  r.cell!.sharePctInChannel != null
                    ? `${r.cell!.sharePctInChannel.toFixed(1)}% deste canal${
                        r.cell!.momPct != null ? ` · ${fmtPct(r.cell!.momPct)} vs. anterior` : ""
                      }`
                    : null
                }
              />
            ))}
          {withoutData.map((r) => (
            <EvidenceRow key={r.label} label={r.label} value="Sem dado neste canal" tone="muted" />
          ))}
        </ul>
      </div>

      <DataQualityNote note="Participação calculada dentro deste canal. Comparar o share de um canal com o de outro não é válido: as ordens de grandeza e as regras de GMV diferem por marketplace." />
      {/* Destino explicito vence o filtro global atual. */}
      <DrilldownCta href={canaisHref}>Abrir {CHANNEL_LABEL[channel]} em Canais →</DrilldownCta>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Concentracao por marca (Task B.3)
// ---------------------------------------------------------------------------

export function ConcentrationDrilldownContent({
  entry,
  position,
  top1Pct,
  top3Pct,
  positiveBrands,
  periodLabel,
  refreshedAt,
  brandHref,
  produtosHref,
}: {
  entry: ConcentrationEntry;
  position: number;
  top1Pct: number | null;
  top3Pct: number | null;
  positiveBrands: number;
  periodLabel: string;
  refreshedAt: string | null;
  brandHref: string;
  produtosHref: string;
}) {
  return (
    <div className="flex flex-col gap-4 text-sm">
      <div>
        <p className="text-2xl font-bold text-gray-900 tabular-nums leading-none">{fmtBrlFull(entry.gmv)}</p>
        <div className="mt-1">
          <DrilldownContextLine
            leading={`${entry.label} · ${position}ª no período`}
            periodLabel={periodLabel}
            refreshedAt={refreshedAt}
          />
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>Participação</p>
        <ul className="flex flex-col gap-1">
          <EvidenceRow
            label="Participação no total selecionado"
            value={`${entry.sharePct.toFixed(1)}%`}
            reference={`Posição ${position} de ${positiveBrands} marcas com GMV positivo`}
          />
          <EvidenceRow
            label="Top 1 do período"
            value={top1Pct != null ? `${top1Pct.toFixed(1)}%` : "N/D"}
            tone={top1Pct != null ? "value" : "muted"}
          />
          <EvidenceRow
            label="Top 3 do período"
            value={top3Pct != null ? `${top3Pct.toFixed(1)}%` : "Base insuficiente"}
            tone={top3Pct != null ? "value" : "muted"}
            reference={
              top3Pct == null ? "Exige pelo menos três marcas com GMV positivo — não se aproxima" : null
            }
          />
        </ul>
      </div>

      <DataQualityNote note="A participação usa o GMV total do escopo selecionado. Trocar canal ou período muda o denominador, então este percentual não é comparável entre filtros diferentes." />

      <div className="flex flex-col gap-2">
        <DrilldownCta href={brandHref}>Abrir visão completa da marca →</DrilldownCta>
        <div className="flex flex-col gap-0.5">
          <DrilldownCta href={produtosHref}>Ver produtos desta seleção →</DrilldownCta>
          <p className="text-xs text-slate-500">{PRODUCTS_SCOPE_NOTE}.</p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Saude do volume por canal
// ---------------------------------------------------------------------------

export function VolumeHealthDrilldownContent({
  row,
  periodLabel,
  refreshedAt,
  qualidadeHref,
}: {
  row: VolumeHealthRow;
  periodLabel: string;
  refreshedAt: string | null;
  qualidadeHref: string;
}) {
  return (
    <div className="flex flex-col gap-4 text-sm">
      <DrilldownContextLine leading={row.channelLabel} periodLabel={periodLabel} refreshedAt={refreshedAt} />

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>Definição</p>
        <p className="text-slate-700">{row.ordersDefinition}</p>
        <p className="text-xs text-slate-500 font-mono mt-1">{CANCEL_RATE_FORMULA}</p>
        <p className="text-xs text-slate-500 mt-1">
          A taxa exibida é a servida pelo endpoint de qualidade. A Torre não a recalcula com outro denominador.
        </p>
      </div>

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>Valores do período</p>
        <ul className="flex flex-col gap-1">
          <EvidenceRow
            label="GMV"
            value={row.gmv != null ? fmtBrlFull(row.gmv) : "Sem dado"}
            tone={row.gmv != null ? "value" : "muted"}
          />
          <EvidenceRow
            label={row.ordersLabel}
            value={row.orders != null ? row.orders.toLocaleString("pt-BR") : "Sem dado"}
            tone={row.orders != null ? "value" : "muted"}
          />
          <EvidenceRow
            label="Cancelados"
            value={row.cancelAvailable && row.canceled != null ? row.canceled.toLocaleString("pt-BR") : "N/D"}
            tone={row.cancelAvailable ? "value" : "muted"}
          />
          <EvidenceRow
            label="Taxa de cancelamento"
            value={row.cancelAvailable && row.cancelRatePct != null ? `${row.cancelRatePct.toFixed(2)}%` : "N/D"}
            tone={row.cancelAvailable ? "value" : "muted"}
          />
          <EvidenceRow
            label="Devolvidos"
            value={row.returnAvailable && row.returned != null ? row.returned.toLocaleString("pt-BR") : "N/D"}
            tone={row.returnAvailable ? "value" : "muted"}
            reference={row.returnAvailable ? "Métrica independente, não é parcela do total considerado" : null}
          />
          <EvidenceRow
            label="Taxa de devolução"
            value={row.returnAvailable && row.returnRatePct != null ? `${row.returnRatePct.toFixed(2)}%` : "N/D"}
            tone={row.returnAvailable ? "value" : "muted"}
          />
        </ul>
      </div>

      <div className="flex flex-col gap-1.5">
        <p className={SECTION_LABEL}>Fonte e limitações</p>
        <p className="text-xs text-slate-500">
          GMV vem do agregado do período; contagens e taxas vêm do endpoint de qualidade, no mesmo escopo de
          canal, marca e datas.
        </p>
        {!row.cancelAvailable && <DataQualityNote note={row.cancelUnavailableReason} />}
        {row.cancelAvailable && !row.returnAvailable && <DataQualityNote note={row.returnUnavailableReason} />}
        <DataQualityNote note={NO_CROSS_CHANNEL_RANKING} />
      </div>

      <DrilldownCta href={qualidadeHref}>Ver qualidade por marca →</DrilldownCta>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Atencao comercial / aviso de dado
// ---------------------------------------------------------------------------

export function CommercialAttentionDrilldownContent({
  item,
  periodLabel,
  refreshedAt,
  href,
}: {
  item: CommercialAttentionItem;
  periodLabel: string;
  refreshedAt: string | null;
  href: string;
}) {
  const referenceLabel = item.referenceKind ? REFERENCE_KIND_LABEL[item.referenceKind] : null;
  return (
    <div className="flex flex-col gap-4 text-sm">
      <DrilldownContextLine
        leading={[item.brand, item.marketplace].filter(Boolean).join(" · ") || null}
        periodLabel={periodLabel}
        refreshedAt={refreshedAt}
      />

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>Diagnóstico</p>
        <p className="text-slate-700">{item.description}</p>
      </div>

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>Impacto</p>
        <ul className="flex flex-col gap-1">
          <EvidenceRow
            label="Métrica"
            value={item.metricValue != null ? item.metricValue.toLocaleString("pt-BR") : "Sem valor no contrato"}
            tone={item.metricValue != null ? "value" : "muted"}
            reference={
              item.referenceValue != null && referenceLabel
                ? `Referência (${referenceLabel}): ${item.referenceValue.toLocaleString("pt-BR")}`
                : null
            }
          />
          {item.deltaAbs != null && (
            <EvidenceRow
              label="Variação absoluta"
              value={item.deltaAbs.toLocaleString("pt-BR")}
              reference={item.deltaPct != null ? `${fmtPct(item.deltaPct)} vs. referência` : null}
            />
          )}
        </ul>
      </div>

      <div className="flex flex-col gap-1.5">
        <p className={SECTION_LABEL}>Evidência</p>
        <p className="text-xs text-slate-500 tabular-nums">
          {item.source ? `Fonte: ${item.source}` : "Fonte não declarada pelo resumo executivo"}
          {item.lastDate && ` · até ${item.lastDate}`}
        </p>
        <DataQualityNote note={item.confidenceNote} />
      </div>

      <DrilldownCta href={href}>Ver evidência →</DrilldownCta>
    </div>
  );
}

export function DataWarningDrilldownContent({
  item,
  periodLabel,
  refreshedAt,
  href,
}: {
  item: DataConfidenceItem;
  periodLabel: string;
  refreshedAt: string | null;
  href: string | null;
}) {
  return (
    <div className="flex flex-col gap-4 text-sm">
      <DrilldownContextLine periodLabel={periodLabel} refreshedAt={refreshedAt} />

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>O que está indisponível</p>
        <p className="text-slate-700">{item.message}</p>
      </div>

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>Evidência</p>
        <ul className="flex flex-col gap-1">
          <EvidenceRow label="Fonte" value={item.source ?? "Não declarada"} tone={item.source ? "value" : "muted"} />
          <EvidenceRow
            label="Última data"
            value={item.lastDate ?? "Não declarada"}
            tone={item.lastDate ? "value" : "muted"}
          />
          <EvidenceRow
            label="Defasagem"
            value={item.stalenessDays != null ? `${item.stalenessDays} dia(s)` : "Não declarada"}
            tone={item.stalenessDays != null ? "value" : "muted"}
            reference={item.thresholdDays != null ? `Limiar: ${item.thresholdDays} dia(s)` : null}
          />
        </ul>
      </div>

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>Impacto interpretativo</p>
        <p className="text-xs text-slate-600">
          Um aviso de dado descreve o que a fonte não sustenta neste período. Ele não é um diagnóstico comercial
          e não deve ser lido como queda de desempenho.
        </p>
      </div>

      {href && <DrilldownCta href={href}>Entender esta limitação →</DrilldownCta>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Movimento (alta / queda)
// ---------------------------------------------------------------------------

export function MovementDrilldownContent({
  movement,
  periodLabel,
  refreshedAt,
  brandHref,
}: {
  movement: Movement;
  periodLabel: string;
  refreshedAt: string | null;
  brandHref: string;
}) {
  const up = movement.deltaAbs > 0;
  return (
    <div className="flex flex-col gap-4 text-sm">
      <div>
        <p className={`text-2xl font-bold tabular-nums leading-none ${up ? "text-emerald-600" : "text-rose-600"}`}>
          {up ? "+" : "−"}
          {fmtBrlFull(Math.abs(movement.deltaAbs))}
        </p>
        <div className="mt-1">
          <DrilldownContextLine
            leading={`${movement.brandLabel} · ${CHANNEL_LABEL[movement.channel]}`}
            periodLabel={periodLabel}
            refreshedAt={refreshedAt}
          />
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <p className={SECTION_LABEL}>Base do cálculo</p>
        <ul className="flex flex-col gap-1">
          <EvidenceRow label="GMV no período" value={fmtBrlFull(movement.current)} />
          <EvidenceRow label="GMV no período anterior" value={fmtBrlFull(movement.previous)} />
          <EvidenceRow
            label="Variação absoluta"
            value={`${up ? "+" : "−"}${fmtBrlFull(Math.abs(movement.deltaAbs))}`}
            reference={
              movement.deltaPct != null
                ? `${fmtPct(movement.deltaPct)} — percentual é contexto; a ordenação usa o valor absoluto`
                : "Percentual indisponível: base anterior igual a zero"
            }
          />
        </ul>
      </div>

      <DataQualityNote note="Pares marca × canal com base anterior inferior a R$ 1 mil ficam fora desta lista: sobre base pequena, o percentual engana." />

      <DrilldownCta href={brandHref}>Abrir visão completa da marca →</DrilldownCta>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Celula da matriz sem linha comparativa (reparacao de stop-loss)
// ---------------------------------------------------------------------------

/**
 * Quatro causas distintas para nao existir linha de `/canais` para uma celula.
 * A versao anterior dizia "indisponivel nesta carga" nos quatro casos, o que
 * afirmava falha de fonte inclusive quando a fonte respondeu bem e apenas nao
 * tinha aquela combinacao — e escondia que o mock de demonstracao nao modela
 * Ads, custos, frete, sinais nem medianas por marca x canal.
 */
export function MatrixCellUnavailableContent({
  status,
  demoMode,
  brandLabel,
  channelLabel,
}: {
  status: "loading" | "error" | "fresh";
  demoMode: boolean;
  brandLabel: string;
  channelLabel: string;
}) {
  const scope = `${brandLabel} × ${channelLabel}`;

  if (status === "loading") {
    return (
      <div className="flex flex-col gap-2 text-sm" role="status" aria-busy="true">
        <p className="text-slate-700">Verificando sinais e referências comparativas deste canal…</p>
        <p className="text-xs text-slate-500">
          O GMV, a participação e a variação de {scope} já estão na matriz e vêm do desempenho por marca.
        </p>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="flex flex-col gap-2 text-sm">
        <p className="text-slate-700">Fonte de sinais e referências indisponível nesta carga.</p>
        <p className="text-xs text-slate-500">
          Ela fornece os sinais de eficiência e as referências de mediana e p75 do próprio canal. O GMV, a
          participação e a variação de {scope} continuam válidos, pois vêm do desempenho por marca.
        </p>
      </div>
    );
  }

  // A fonte respondeu bem, mas nao trouxe esta combinacao.
  if (demoMode) {
    return (
      <div className="flex flex-col gap-2 text-sm">
        <p className="text-slate-700">
          O modo demonstração não modela Ads, custos, frete, sinais ou medianas por marca × canal.
        </p>
        <p className="text-xs text-slate-500">
          Por isso não há detalhe comparativo para {scope} nesta sessão. Com a API respondendo, este diálogo
          traz o diagnóstico completo do canal.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 text-sm">
      <p className="text-slate-700">Não há registro comparativo para {scope} no período.</p>
      <p className="text-xs text-slate-500">
        A fonte de canais respondeu normalmente — apenas não existe linha desta marca neste canal no período
        selecionado. Isso não indica falha de carga.
      </p>
    </div>
  );
}
