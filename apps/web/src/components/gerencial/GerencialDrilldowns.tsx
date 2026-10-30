"use client";

/**
 * Conteudos de drill-down proprios da Gerencial V2 (Gate V2-1, com as correcoes
 * das Tasks B, D e F da rodada consolidada).
 *
 * Todos seguem o contrato transversal do Gate G2 (docs/DRILLDOWN_ARCHITECTURE.md
 * §3), renderizados dentro do unico `KpiDrilldownDialog`.
 *
 * POPUPS-1 — mesmo desenho do detalhe de KPI: numero principal grande, variacao
 * em pilula quando ha' base, selos de contexto e um "ⓘ" com a definicao e a
 * ressalva. Depois, secoes com as quebras que respondem a proxima pergunta
 * (por canal, por marca), do maior ao menor, com barra de participacao, ponto
 * de canal e variacao por linha. Paragrafos de metodologia sairam; a ressalva
 * que muda a leitura ficou em uma linha ou no "ⓘ".
 */
import { useEffect, useState, type ReactNode } from "react";
import { ArrowDownRight, ArrowRight, ArrowUpRight } from "lucide-react";
import DrilldownContextLine from "@/components/drilldown/DrilldownContextLine";
import DrilldownSecao from "@/components/drilldown/DrilldownSecao";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import DrilldownCta from "@/components/drilldown/DrilldownCta";
import InfoDica from "@/components/ui/InfoDica";
import NumeroAnimado from "@/components/ui/NumeroAnimado";
import { fetchBrands, fetchOverview, type BrandRow, type OverviewData } from "@/lib/api-client";
import type { Marketplace } from "@/lib/mock-data";
import { fmtBrlFull, fmtNumber, fmtPctBr } from "@/lib/formatters";
import { CHANNEL_LABEL } from "@/lib/gerencial/kpi-band";
import { CHANNEL_DOT } from "@/lib/gerencial/channel-colors";
import type {
  ConfidenceStripData,
  CommercialAttentionItem,
  DataConfidenceItem,
} from "@/lib/gerencial/attention";
import { AVAILABILITY_LABEL, REFERENCE_KIND_LABEL } from "@/lib/gerencial/attention";
import { formatDeltaAbs, formatMetricByType, formatReferenceValue } from "@/lib/executive-pulse";
import type { MergedBucket, MergedSeries } from "@/lib/gerencial/trend-series";
import { bucketRange } from "@/lib/gerencial/trend-series";
import type { TrendMetric } from "@/lib/gerencial/request-key";
import type { BrandChannelMatrix, ConcentrationEntry, MatrixRow, Movement } from "@/lib/gerencial/brand-matrix";
import { PRODUCTS_SCOPE_NOTE } from "@/lib/gerencial/brand-matrix";
import type { VolumeHealthRow } from "@/lib/gerencial/volume-health";
import { CANCEL_RATE_FORMULA, NO_CROSS_CHANNEL_RANKING } from "@/lib/gerencial/volume-health";

/** Escopo do ponto por grao. O grao semanal (Gate V2-2) nao e' "competência". */
const BUCKET_SCOPE_LABEL: Record<"day" | "week" | "month", string> = {
  day: "do dia",
  week: "da semana",
  month: "da competência",
};

/** Cor de canal como TEXTO (para o SVG, que pinta com currentColor). Literal
 * para o Tailwind enxergar a classe. */
const CHANNEL_TEXT: Record<Marketplace, string> = {
  tiktok: "text-canal-tiktok",
  ml: "text-canal-ml",
  shopee: "text-canal-shopee",
};

function metricLabel(metric: TrendMetric): string {
  return metric === "gmv" ? "GMV" : "Pedidos";
}

function formatMetric(value: number, metric: TrendMetric): string {
  return metric === "gmv" ? fmtBrlFull(value) : fmtNumber(value);
}

function fmtPct1(v: number): string {
  return `${v.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
}

function variacao(atual: number | null | undefined, anterior: number | null | undefined): number | null {
  return atual != null && anterior != null && anterior > 0 ? (atual / anterior - 1) * 100 : null;
}

/** "2026-07-05" -> "05/07". */
function diaMes(iso: string): string {
  const [, m, d] = iso.split("-");
  return d && m ? `${d}/${m}` : iso;
}

// ---------------------------------------------------------------------------
// POPUPS-1 — topo comum: numero, pilula e selos
// ---------------------------------------------------------------------------

type Pele = "ok" | "crit" | "warn" | "neutro";

const PELE: Record<Pele, string> = {
  ok: "bg-ok-soft text-ok-ink",
  crit: "bg-crit-soft text-crit-ink",
  warn: "bg-warn-soft text-warn-ink",
  neutro: "bg-raised text-ink-muted",
};

/** Variacao em pilula + a base nomeada ("vs R$ X no período anterior"). */
function Variacao({ pct, base, neutro }: { pct: number; base?: string | null; neutro?: boolean }) {
  const sobe = pct >= 0;
  const Seta = sobe ? ArrowUpRight : ArrowDownRight;
  return (
    <span className="flex flex-wrap items-center gap-2 pb-0.5">
      <span
        className={`inline-flex items-center gap-0.5 rounded-md px-2 py-0.5 text-sm font-semibold tabular-nums ${
          PELE[neutro ? "neutro" : sobe ? "ok" : "crit"]
        }`}
      >
        <Seta aria-hidden="true" className="h-4 w-4" strokeWidth={2.25} />
        {fmtPctBr(pct)}
      </span>
      {base && <span className="text-xs text-ink-faint tabular-nums">{base}</span>}
    </span>
  );
}

/** Pilula de leitura sem seta (participacao, contagem de avisos). */
function Selo({ children, pele = "neutro" }: { children: ReactNode; pele?: Pele }) {
  return (
    <span className={`mb-0.5 inline-flex items-center rounded-md px-2 py-0.5 text-sm font-semibold tabular-nums ${PELE[pele]}`}>
      {children}
    </span>
  );
}

function Topo({
  valor,
  vazio,
  tom = "text-ink",
  extra,
  leading,
  periodLabel,
  refreshedAt,
  dica,
}: {
  /** Valor ja' formatado; null mostra `vazio` (nunca um zero inventado). */
  valor: string | null;
  vazio?: string;
  tom?: string;
  extra?: ReactNode;
  leading?: string | null;
  periodLabel: string;
  refreshedAt: string | null;
  dica?: string;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-x-3 gap-y-1.5">
        {valor != null ? (
          <NumeroAnimado texto={valor} className={`text-[32px] font-bold leading-none tracking-tight tabular-nums ${tom}`} />
        ) : (
          <span className="text-xl font-semibold leading-none text-ink-faint">{vazio ?? "Sem dado"}</span>
        )}
        {extra}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <DrilldownContextLine leading={leading} periodLabel={periodLabel} refreshedAt={refreshedAt} />
        {dica && <InfoDica texto={dica} />}
      </div>
    </div>
  );
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
  // POPUPS-1: topo = quantos canais tem serie; a pilula diz se ha' aviso, ou
  // se os avisos nem foram verificados (o que nao e' "sem aviso").
  const avisos = !data.warningsChecked ? (
    <Selo>Avisos não verificados</Selo>
  ) : items.length > 0 ? (
    <Selo pele="warn">
      {items.length} {items.length === 1 ? "aviso" : "avisos"}
    </Selo>
  ) : (
    <Selo pele="ok">Sem avisos</Selo>
  );

  return (
    <div className="flex flex-col gap-4 text-sm">
      <Topo
        valor={`${data.availableCount} de ${data.selectedCount}`}
        extra={avisos}
        leading="Canais com série"
        periodLabel={periodLabel}
        refreshedAt={refreshedAt}
        dica="Série disponível = a fonte respondeu com registros no período, inclusive registros de valor zero. Não comprova completude. Sem aviso de defasagem também não garante que toda fonte está atualizada até hoje."
      />

      <DrilldownSecao titulo="Por canal">
        <ul className="flex flex-col">
          {data.channels.map((c) => (
            <EvidenceRow
              key={c.channel}
              label={c.label}
              ponto={CHANNEL_DOT[c.channel]}
              value={AVAILABILITY_LABEL[c.availability]}
              tone={c.availability === "available" ? "value" : c.availability === "unavailable" ? "warning" : "muted"}
            />
          ))}
        </ul>
      </DrilldownSecao>

      <DrilldownSecao titulo="Defasagem">
        {!data.warningsChecked ? (
          <DataQualityNote note="Defasagem e avisos não verificados: o resumo executivo não respondeu nesta carga." />
        ) : (
          <ul className="flex flex-col">
            <EvidenceRow
              label="Maior defasagem"
              value={data.maxStalenessDays != null ? `${data.maxStalenessDays} dia(s)` : "Sem aviso"}
              tone={data.maxStalenessDays == null ? "muted" : data.maxStalenessDays > 2 ? "warning" : "value"}
              reference={data.maxStalenessDays != null && data.sources.length ? data.sources.join(" · ") : null}
            />
          </ul>
        )}
      </DrilldownSecao>

      {data.warningsChecked && items.length > 0 && (
        <DrilldownSecao titulo="Avisos ativos" acao={<span>{items.length}</span>}>
          <ul className="flex flex-col divide-y divide-line">
            {[...items]
              .sort((a, b) => (b.stalenessDays ?? -1) - (a.stalenessDays ?? -1))
              .map((item) => (
                <li key={item.key} className="flex items-start justify-between gap-3 py-1.5">
                  <span className="min-w-0 text-ink-muted leading-snug">
                    {item.message}
                    {(item.source || item.lastDate) && (
                      <span className="block text-xs text-ink-faint tabular-nums">
                        {[item.source, item.lastDate && `até ${item.lastDate}`].filter(Boolean).join(" · ")}
                      </span>
                    )}
                  </span>
                  {item.stalenessDays != null && (
                    <span
                      className={`shrink-0 tabular-nums font-semibold ${
                        item.thresholdDays != null && item.stalenessDays > item.thresholdDays ? "text-warn-ink" : "text-ink"
                      }`}
                      title={item.thresholdDays != null ? `Limiar: ${item.thresholdDays} dia(s)` : undefined}
                    >
                      {item.stalenessDays} d
                      {item.thresholdDays != null && (
                        <span className="font-normal text-ink-faint"> / {item.thresholdDays} d</span>
                      )}
                    </span>
                  )}
                </li>
              ))}
          </ul>
        </DrilldownSecao>
      )}

      {/* O limite epistemico da faixa, em uma linha. */}
      <DataQualityNote note="Série disponível não é dado completo: o canal pode estar com carga parcial ou atrasada." />
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
  bounds,
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
  /** Periodo global atual. No grao SEMANAL a primeira e a ultima semana quase
   * sempre sao parciais, e o CTA nunca deve aplicar datas fora do que o
   * usuario estava vendo. */
  bounds: { dateFrom: string; dateTo: string };
  onPinRange: (dateFrom: string, dateTo: string) => void;
}) {
  const [state, setState] = useState<BucketDetailState>({
    overview: null,
    brands: null,
    overviewStatus: "loading",
    brandsStatus: "loading",
  });

  const range = bucketRange(bucket.date, merged.granularity, bounds);

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

  // POPUPS-1: canais do maior ao menor, com participacao no total do ponto
  // (so' quando o total existe — denominador parcial inflaria o share) e
  // variacao contra a mesma posicao do periodo anterior.
  const cmp = bucket.comparison;
  const porCanal = channels
    .map((channel) => {
      const value = bucket.values[channel] ?? null;
      return {
        channel,
        value,
        pct: value != null && bucket.total != null && bucket.total > 0 ? (value / bucket.total) * 100 : null,
        delta: variacao(value, cmp?.values[channel]),
      };
    })
    .sort((a, b) => (b.value ?? -1) - (a.value ?? -1));
  const deltaTotal = variacao(bucket.total, cmp?.total);

  const marcas = brandsUsable ? state.brands!.filter((b) => b.total_gmv > 0).sort((a, b) => b.total_gmv - a.total_gmv) : [];
  const somaMarcas = marcas.reduce((a, b) => a + b.total_gmv, 0);
  const ticket =
    overviewUsable && state.overview!.orders > 0 ? state.overview!.gmv / state.overview!.orders : null;

  return (
    <div className="flex flex-col gap-4 text-sm">
      <Topo
        valor={bucket.total != null ? formatMetric(bucket.total, metric) : null}
        vazio="Total indisponível"
        extra={
          deltaTotal != null &&
          cmp?.total != null && (
            <Variacao pct={deltaTotal} base={`vs ${formatMetric(cmp.total, metric)} em ${cmp.label}`} />
          )
        }
        leading={`${bucket.label} · ${granularityLabel}`}
        periodLabel={periodLabel}
        refreshedAt={null}
        dica={`Ponto ${position} de ${merged.buckets.length} na série de ${metricLabel(metric)}. A variação compara com a mesma posição do período anterior.`}
      />
      {bucket.total == null && (
        <DataQualityNote note="Sem total: pelo menos um canal selecionado não tem valor neste ponto." />
      )}

      {/* Evidencia que NAO depende de fetch: ja veio com a serie. */}
      <DrilldownSecao titulo="Por canal">
        <ul className="flex flex-col">
          {porCanal.map((c) => (
            <EvidenceRow
              key={c.channel}
              label={CHANNEL_LABEL[c.channel]}
              ponto={CHANNEL_DOT[c.channel]}
              barra={CHANNEL_DOT[c.channel]}
              pct={c.pct}
              delta={c.delta != null ? { pct: c.delta } : null}
              value={
                c.value == null
                  ? "Sem dado"
                  : c.pct != null
                    ? `${formatMetric(c.value, metric)} · ${fmtPct1(c.pct)}`
                    : formatMetric(c.value, metric)
              }
              tone={c.value == null ? "muted" : "value"}
            />
          ))}
        </ul>
      </DrilldownSecao>

      {/* Lado comparativo alinhado por posicao ordinal, com a data REAL da
          janela anterior. O CTA abaixo continua fixando o periodo ATUAL. */}
      {cmp && (
        <DrilldownSecao
          titulo="Mesma posição no período anterior"
          acao={<span className="tabular-nums">{cmp.label}</span>}
        >
          <ul className="flex flex-col">
            {porCanal.map(({ channel }) => {
              const value = cmp.values[channel];
              return (
                <EvidenceRow
                  key={channel}
                  label={CHANNEL_LABEL[channel]}
                  ponto={CHANNEL_DOT[channel]}
                  value={value == null ? "Sem dado" : formatMetric(value, metric)}
                  tone={value == null ? "muted" : "value"}
                />
              );
            })}
          </ul>
          {cmp.total == null && <DataQualityNote note="Sem total anterior: falta valor em um canal nesta posição." />}
          {/* Janela do CONTRATO, nao derivada dos buckets: o primeiro bucket
              semanal comeca antes dela. */}
          {merged.comparison.dateFrom && merged.comparison.dateTo && (
            <p className="text-xs text-ink-faint tabular-nums">
              Janela anterior completa: {merged.comparison.dateFrom} a {merged.comparison.dateTo}
            </p>
          )}
        </DrilldownSecao>
      )}

      <DrilldownSecao titulo={`Detalhe ${BUCKET_SCOPE_LABEL[merged.granularity]}`}>
        {anyLoading ? (
          <p className="text-xs text-ink-faint" role="status" aria-busy="true">
            Carregando detalhe…
          </p>
        ) : bothUnavailable ? (
          <p className="text-xs text-crit-ink">
            Detalhe indisponível nesta carga. Os valores por canal acima vêm da série e seguem válidos.
          </p>
        ) : overviewUsable ? (
          <ul className="flex flex-col">
            <EvidenceRow label="GMV" value={fmtBrlFull(state.overview!.gmv)} />
            <EvidenceRow label="Pedidos" value={fmtNumber(state.overview!.orders)} />
            {ticket != null && <EvidenceRow label="Ticket médio" value={fmtBrlFull(ticket)} />}
          </ul>
        ) : (
          <p className="text-xs text-ink-faint">Agregado do recorte indisponível nesta carga.</p>
        )}
      </DrilldownSecao>

      {!anyLoading && !bothUnavailable && (
        <DrilldownSecao
          titulo="Por marca"
          acao={marcas.length > 5 ? <span className="tabular-nums">5 de {marcas.length}</span> : undefined}
        >
          {marcas.length > 0 ? (
            <ul className="flex flex-col">
              {marcas.slice(0, 5).map((b) => {
                const pct = somaMarcas > 0 ? (b.total_gmv / somaMarcas) * 100 : null;
                return (
                  <EvidenceRow
                    key={b.brand}
                    label={b.label}
                    pct={pct}
                    value={pct != null ? `${fmtBrlFull(b.total_gmv)} · ${fmtPct1(pct)}` : fmtBrlFull(b.total_gmv)}
                  />
                );
              })}
            </ul>
          ) : (
            <p className="text-xs text-ink-faint">Desempenho por marca indisponível nesta carga.</p>
          )}
        </DrilldownSecao>
      )}

      {/* Parcial: nomeia exatamente a fonte ausente. */}
      {!anyLoading && !bothUnavailable && overviewUsable !== brandsUsable && (
        <DataQualityNote
          note={`Detalhe parcial: ${overviewUsable ? "o desempenho por marca" : "o agregado do recorte"} não respondeu nesta carga.`}
        />
      )}
      {!anyLoading && isDemo && <DataQualityNote note="Detalhe com dados de demonstração." />}

      {/* Task D2: o CTA promete exatamente o recorte que aplica. */}
      <button
        type="button"
        onClick={() => onPinRange(range.dateFrom, range.dateTo)}
        title="Só o intervalo de datas muda; canais, marcas e comparação são preservados."
        className="group mt-1 flex min-h-11 w-full items-center justify-between gap-3 rounded-xl border border-line px-4 text-left text-sm font-semibold text-accent transition-colors hover:border-accent/40 hover:bg-accent-soft/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <span>Fixar {range.label} como período</span>
        <span className="flex shrink-0 items-center gap-2">
          <span className="text-xs font-normal text-ink-faint tabular-nums">
            {range.dateFrom === range.dateTo ? diaMes(range.dateFrom) : `${diaMes(range.dateFrom)} – ${diaMes(range.dateTo)}`}
          </span>
          <ArrowRight aria-hidden="true" className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-0.5" />
        </span>
      </button>
      {range.clamped && (
        <DataQualityNote note="Intervalo cortado pelo período selecionado: este ponto é parcial na borda do filtro." />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Serie de um canal (Task B.1)
// ---------------------------------------------------------------------------

/** POPUPS-1 — a serie do canal em tamanho legivel. Lacuna interrompe a linha
 * (ausencia nao vira zero); escala de zero ao maior ponto. */
function SerieCanal({ values, channel }: { values: (number | null)[]; channel: Marketplace }) {
  const max = Math.max(0, ...values.map((v) => v ?? 0)) || 1;
  const passo = values.length > 1 ? 300 / (values.length - 1) : 0;
  const y = (v: number) => 62 - (v / max) * 56;
  const trechos: string[][] = [];
  let atual: string[] = [];
  values.forEach((v, i) => {
    if (v == null) {
      if (atual.length) trechos.push(atual);
      atual = [];
      return;
    }
    atual.push(`${(i * passo).toFixed(1)},${y(v).toFixed(1)}`);
  });
  if (atual.length) trechos.push(atual);
  return (
    <svg viewBox="0 0 300 64" preserveAspectRatio="none" aria-hidden="true" className={`tc-revela h-16 w-full ${CHANNEL_TEXT[channel]}`}>
      {trechos.map((t, i) =>
        t.length > 1 ? (
          <g key={i}>
            <polygon points={`${t[0].split(",")[0]},64 ${t.join(" ")} ${t[t.length - 1].split(",")[0]},64`} fill="currentColor" opacity={0.12} />
            <polyline points={t.join(" ")} fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
          </g>
        ) : (
          <circle key={i} cx={t[0].split(",")[0]} cy={t[0].split(",")[1]} r={2} fill="currentColor" />
        ),
      )}
    </svg>
  );
}

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
  const values = merged.buckets.map((b) => b.values[channel] ?? null);
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
  const temValor = !failed && !empty && numeric.length > 0;

  return (
    <div className="flex flex-col gap-4 text-sm">
      <Topo
        valor={temValor ? formatMetric(sum, metric) : null}
        vazio={failed ? "Série indisponível" : "Sem registros"}
        extra={sharePct != null && temValor && <Selo>{fmtPct1(sharePct)} do total</Selo>}
        leading={`${metricLabel(metric)} no período`}
        periodLabel={periodLabel}
        refreshedAt={null}
        dica="Soma dos pontos deste canal na série exibida; não substitui o agregado do período, que tem cláusula de filtro própria. A participação só aparece com todos os canais selecionados com série completa."
      />

      {failed ? (
        <DataQualityNote note="A série deste canal não respondeu nesta carga; o total por ponto não é completo." />
      ) : empty ? (
        <DataQualityNote note="Sem registros deste canal no período — ausência de dado, não zero." />
      ) : (
        <>
          {values.length > 1 && numeric.length > 0 && (
            <DrilldownSecao
              titulo="Série"
              acao={
                <span className={`tabular-nums ${gaps > 0 ? "text-warn-ink" : ""}`}>
                  {numeric.length} de {values.length} pontos com valor
                </span>
              }
            >
              <SerieCanal values={values} channel={channel} />
              {gaps > 0 && <DataQualityNote note={`${gaps} ponto(s) sem registro: a linha se interrompe.`} />}
            </DrilldownSecao>
          )}

          {numeric.length > 0 && (
            <DrilldownSecao titulo="Resumo">
              <ul className="flex flex-col">
                {peak && max != null && (
                  <EvidenceRow label={`Maior ponto · ${peak.label}`} value={formatMetric(max, metric)} />
                )}
                <EvidenceRow label="Média por ponto com valor" value={formatMetric(sum / numeric.length, metric)} />
                <EvidenceRow
                  label="Participação no total"
                  ponto={CHANNEL_DOT[channel]}
                  barra={CHANNEL_DOT[channel]}
                  pct={sharePct}
                  value={sharePct != null ? fmtPct1(sharePct) : "Indisponível"}
                  tone={sharePct != null ? "value" : "muted"}
                />
              </ul>
            </DrilldownSecao>
          )}
        </>
      )}

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
  const withData = rows
    .filter((r) => r.cell!.available)
    .sort((a, b) => (b.cell!.gmv ?? 0) - (a.cell!.gmv ?? 0));
  const withoutData = rows.filter((r) => !r.cell!.available);
  const doTotal = matrix.channels.length > 1 && matrix.grandTotal > 0 ? (total / matrix.grandTotal) * 100 : null;

  return (
    <div className="flex flex-col gap-4 text-sm">
      <Topo
        valor={fmtBrlFull(total)}
        extra={doTotal != null && <Selo>{fmtPct1(doTotal)} do GMV dos canais</Selo>}
        leading="GMV do canal"
        periodLabel={periodLabel}
        refreshedAt={refreshedAt}
        dica="Participação de cada marca calculada dentro deste canal. Comparar o share de um canal com o de outro não é válido: ordens de grandeza e regras de GMV diferem por marketplace."
      />

      <DrilldownSecao titulo="Por marca" acao={<span className="tabular-nums">{withData.length}</span>}>
        <ul className="flex flex-col">
          {withData.map((r) => (
            <EvidenceRow
              key={r.label}
              label={r.label}
              barra={CHANNEL_DOT[channel]}
              pct={r.cell!.sharePctInChannel}
              delta={r.cell!.momPct != null ? { pct: r.cell!.momPct } : null}
              value={
                r.cell!.sharePctInChannel != null
                  ? `${fmtBrlFull(r.cell!.gmv as number)} · ${fmtPct1(r.cell!.sharePctInChannel)}`
                  : fmtBrlFull(r.cell!.gmv as number)
              }
            />
          ))}
          {withoutData.map((r) => (
            <EvidenceRow key={r.label} label={r.label} value="Sem dado" tone="muted" mutedLabel />
          ))}
        </ul>
      </DrilldownSecao>

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
  ranking,
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
  /** POPUPS-1 — as marcas do periodo ja' ordenadas (mesma base da faixa), para
   * mostrar quem esta' a frente e atras desta marca. */
  ranking?: ConcentrationEntry[];
  periodLabel: string;
  refreshedAt: string | null;
  brandHref: string;
  produtosHref: string;
}) {
  const lista = ranking ?? [];
  return (
    <div className="flex flex-col gap-4 text-sm">
      <Topo
        valor={fmtBrlFull(entry.gmv)}
        extra={<Selo>{fmtPct1(entry.sharePct)} do total</Selo>}
        leading={`${position}ª de ${positiveBrands} marcas`}
        periodLabel={periodLabel}
        refreshedAt={refreshedAt}
        dica="Participação sobre o GMV total do escopo selecionado. Trocar canal ou período muda o denominador: o percentual não é comparável entre filtros. Top 3 exige pelo menos três marcas com GMV positivo — não se aproxima."
      />

      <DrilldownSecao
        titulo="Concentração"
        acao={
          <span className="tabular-nums">
            Top 1 {top1Pct != null ? fmtPct1(top1Pct) : "N/D"} · Top 3 {top3Pct != null ? fmtPct1(top3Pct) : "base insuficiente"}
          </span>
        }
      >
        {lista.length > 1 ? (
          <ul className="flex flex-col">
            {lista.map((e, i) => (
              <EvidenceRow
                key={e.brand}
                label={`${i + 1}. ${e.label}`}
                mutedLabel={e.brand !== entry.brand}
                barra={e.brand === entry.brand ? "bg-accent" : "bg-line-strong"}
                pct={e.sharePct}
                value={`${fmtBrlFull(e.gmv)} · ${fmtPct1(e.sharePct)}`}
                tone={e.brand === entry.brand ? "value" : "muted"}
              />
            ))}
          </ul>
        ) : (
          <ul className="flex flex-col">
            <EvidenceRow label={entry.label} pct={entry.sharePct} value={fmtPct1(entry.sharePct)} />
          </ul>
        )}
      </DrilldownSecao>

      <div className="flex flex-col gap-2">
        <DrilldownCta href={brandHref}>Abrir visão completa da marca →</DrilldownCta>
        <div className="flex flex-col gap-1">
          <DrilldownCta href={produtosHref}>Ver produtos desta seleção →</DrilldownCta>
          <p className="text-xs text-ink-faint">{PRODUCTS_SCOPE_NOTE}.</p>
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
  // POPUPS-1: a pergunta de quem abre e' "quanto se perde"; a taxa de
  // cancelamento vira o numero principal. A definicao do denominador, a
  // formula e a ressalva entre canais vao para o "ⓘ".
  const taxa = row.cancelAvailable && row.cancelRatePct != null ? row.cancelRatePct : null;
  const pct2 = (v: number) => `${v.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
  const int = (v: number) => v.toLocaleString("pt-BR");
  return (
    <div className="flex flex-col gap-4 text-sm">
      <Topo
        valor={taxa != null ? pct2(taxa) : null}
        vazio="Cancelamento N/D"
        leading="Taxa de cancelamento"
        periodLabel={periodLabel}
        refreshedAt={refreshedAt}
        dica={`${row.ordersDefinition} ${CANCEL_RATE_FORMULA}; taxa servida pelo endpoint de qualidade, sem recálculo. Devolução é métrica independente, não parcela do total. ${NO_CROSS_CHANNEL_RANKING}`}
      />
      {!row.cancelAvailable && <DataQualityNote note={row.cancelUnavailableReason} />}

      <DrilldownSecao titulo="Volume">
        <ul className="flex flex-col">
          <EvidenceRow
            label="GMV"
            value={row.gmv != null ? fmtBrlFull(row.gmv) : "Sem dado"}
            tone={row.gmv != null ? "value" : "muted"}
          />
          <EvidenceRow
            label={row.ordersLabel}
            value={row.orders != null ? int(row.orders) : "Sem dado"}
            tone={row.orders != null ? "value" : "muted"}
          />
        </ul>
      </DrilldownSecao>

      <DrilldownSecao titulo="Perdas">
        <ul className="flex flex-col">
          <EvidenceRow
            label="Cancelados"
            value={
              row.cancelAvailable && row.canceled != null
                ? `${int(row.canceled)}${row.cancelRatePct != null ? ` · ${pct2(row.cancelRatePct)}` : ""}`
                : "N/D"
            }
            tone={row.cancelAvailable ? "value" : "muted"}
          />
          <EvidenceRow
            label="Devolvidos"
            value={
              row.returnAvailable && row.returned != null
                ? `${int(row.returned)}${row.returnRatePct != null ? ` · ${pct2(row.returnRatePct)}` : ""}`
                : "N/D"
            }
            tone={row.returnAvailable ? "value" : "muted"}
          />
        </ul>
        {row.cancelAvailable && !row.returnAvailable && <DataQualityNote note={row.returnUnavailableReason} />}
      </DrilldownSecao>

      <DrilldownCta href={qualidadeHref}>Ver qualidade por marca →</DrilldownCta>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Atencao comercial / aviso de dado
// ---------------------------------------------------------------------------

/** Formata pelo tipo do insight quando conhecido; senao, o numero cru. */
function porTipo(fmt: (type: string, v: number) => string, type: string, v: number): string {
  const s = fmt(type, v);
  return s === "—" ? v.toLocaleString("pt-BR") : s;
}

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
  const referencia =
    item.referenceValue != null && referenceLabel
      ? `vs ${porTipo(formatReferenceValue, item.type, item.referenceValue)} (${referenceLabel})`
      : null;
  // Crescimento e queda tem direcao de juizo; custo e cancelamento altos sao
  // ruins quando sobem — ai' a pilula fica neutra em vez de pintar de verde.
  const comJuizo = item.type === "growth" || item.type === "drop";
  return (
    <div className="flex flex-col gap-4 text-sm">
      <Topo
        valor={item.metricValue != null ? porTipo(formatMetricByType, item.type, item.metricValue) : null}
        vazio="Sem valor no contrato"
        extra={
          item.deltaPct != null ? (
            <Variacao pct={item.deltaPct} base={referencia} neutro={!comJuizo} />
          ) : (
            referencia && <span className="pb-0.5 text-xs text-ink-faint tabular-nums">{referencia}</span>
          )
        }
        leading={[item.brand, item.marketplace].filter(Boolean).join(" · ") || null}
        periodLabel={periodLabel}
        refreshedAt={refreshedAt}
      />

      {item.description && <p className="text-ink-muted leading-snug">{item.description}</p>}

      <DrilldownSecao titulo="Evidência">
        <ul className="flex flex-col">
          {item.deltaAbs != null && (
            <EvidenceRow label="Diferença absoluta" value={porTipo(formatDeltaAbs, item.type, item.deltaAbs)} />
          )}
          <EvidenceRow label="Fonte" value={item.source ?? "Não declarada"} tone={item.source ? "value" : "muted"} />
          {item.lastDate && <EvidenceRow label="Dado até" value={item.lastDate} />}
        </ul>
        <DataQualityNote note={item.confidenceNote} />
      </DrilldownSecao>

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
  const acima = item.stalenessDays != null && item.thresholdDays != null && item.stalenessDays > item.thresholdDays;
  return (
    <div className="flex flex-col gap-4 text-sm">
      <Topo
        valor={item.stalenessDays != null ? `${item.stalenessDays} dia(s)` : null}
        vazio="Defasagem não declarada"
        tom={acima ? "text-warn-ink" : "text-ink"}
        extra={item.thresholdDays != null && <Selo pele={acima ? "warn" : "neutro"}>limiar {item.thresholdDays} dia(s)</Selo>}
        leading="Defasagem"
        periodLabel={periodLabel}
        refreshedAt={refreshedAt}
        dica="Um aviso de dado descreve o que a fonte não sustenta neste período. Não é diagnóstico comercial e não deve ser lido como queda de desempenho."
      />

      <p className="text-ink-muted leading-snug">{item.message}</p>

      <DrilldownSecao titulo="Evidência">
        <ul className="flex flex-col">
          <EvidenceRow label="Fonte" value={item.source ?? "Não declarada"} tone={item.source ? "value" : "muted"} />
          <EvidenceRow label="Última data" value={item.lastDate ?? "Não declarada"} tone={item.lastDate ? "value" : "muted"} />
        </ul>
      </DrilldownSecao>

      {href && <DrilldownCta href={href}>Entender esta limitação →</DrilldownCta>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Movimento (alta / queda)
// ---------------------------------------------------------------------------

export function MovementDrilldownContent({
  movement,
  marca,
  periodLabel,
  refreshedAt,
  brandHref,
}: {
  movement: Movement;
  /** POPUPS-1 — a linha da matriz desta marca (ja' carregada), para responder
   * se o movimento e' do canal ou da marca inteira. */
  marca?: MatrixRow | null;
  periodLabel: string;
  refreshedAt: string | null;
  brandHref: string;
}) {
  const up = movement.deltaAbs > 0;
  const maior = Math.max(movement.current, movement.previous) || 1;
  const outros = (marca?.cells ?? [])
    .filter((c) => c.available && c.gmv != null)
    .sort((a, b) => (b.gmv ?? 0) - (a.gmv ?? 0));
  return (
    <div className="flex flex-col gap-4 text-sm">
      <Topo
        valor={`${up ? "+" : "−"}${fmtBrlFull(Math.abs(movement.deltaAbs))}`}
        tom={up ? "text-ok-ink" : "text-crit-ink"}
        extra={movement.deltaPct != null && <Variacao pct={movement.deltaPct} base={`vs ${fmtBrlFull(movement.previous)} no período anterior`} />}
        leading={`Variação em ${CHANNEL_LABEL[movement.channel]}`}
        periodLabel={periodLabel}
        refreshedAt={refreshedAt}
        dica="A lista ordena pelo valor absoluto; o percentual é contexto. Pares marca × canal com base anterior inferior a R$ 1 mil ficam fora: sobre base pequena, o percentual engana."
      />

      <DrilldownSecao titulo="Antes e agora">
        <ul className="flex flex-col">
          <EvidenceRow
            label="Período atual"
            ponto={CHANNEL_DOT[movement.channel]}
            barra={CHANNEL_DOT[movement.channel]}
            pct={(movement.current / maior) * 100}
            value={fmtBrlFull(movement.current)}
          />
          <EvidenceRow
            label="Período anterior"
            barra="bg-line-strong"
            pct={(movement.previous / maior) * 100}
            value={fmtBrlFull(movement.previous)}
            tone="muted"
          />
        </ul>
      </DrilldownSecao>

      {outros.length > 1 && marca && (
        <DrilldownSecao titulo={`${movement.brandLabel} por canal`}>
          <ul className="flex flex-col">
            {outros.map((c) => {
              const pct = marca.brandTotal > 0 ? ((c.gmv as number) / marca.brandTotal) * 100 : null;
              return (
                <EvidenceRow
                  key={c.channel}
                  label={CHANNEL_LABEL[c.channel]}
                  ponto={CHANNEL_DOT[c.channel]}
                  barra={CHANNEL_DOT[c.channel]}
                  pct={pct}
                  delta={c.momPct != null ? { pct: c.momPct } : null}
                  value={pct != null ? `${fmtBrlFull(c.gmv as number)} · ${fmtPct1(pct)}` : fmtBrlFull(c.gmv as number)}
                />
              );
            })}
          </ul>
        </DrilldownSecao>
      )}

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
 *
 * POPUPS-1: uma frase por causa, mais uma linha curta do que segue valendo.
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
      <div className="flex flex-col gap-1.5 text-sm" role="status" aria-busy="true">
        <p className="text-ink-muted">Verificando sinais e referências comparativas deste canal…</p>
        <p className="text-xs text-ink-faint">GMV, participação e variação de {scope} já estão na matriz.</p>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="flex flex-col gap-1.5 text-sm">
        <p className="text-ink-muted">Fonte de sinais e referências indisponível nesta carga.</p>
        <p className="text-xs text-ink-faint">GMV, participação e variação de {scope} seguem válidos na matriz.</p>
      </div>
    );
  }

  // A fonte respondeu bem, mas nao trouxe esta combinacao.
  if (demoMode) {
    return (
      <p className="text-sm text-ink-muted">
        O modo demonstração não modela Ads, custos, frete, sinais ou medianas por marca × canal.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-1.5 text-sm">
      <p className="text-ink-muted">Não há registro comparativo para {scope} no período.</p>
      <p className="text-xs text-ink-faint">A fonte de canais respondeu. Isso não indica falha de carga.</p>
    </div>
  );
}
