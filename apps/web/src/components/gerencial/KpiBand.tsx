"use client";

/**
 * Faixa de cinco KPIs da Gerencial V2 (Gate V2-1, Task D).
 *
 * Componente NOVO em vez de estender `KpiCard`: aquele e' consumido por sete
 * outras rotas e este gate nao pode alterar a aparencia delas. A logica de
 * disponibilidade/comparacao vive em `lib/gerencial/kpi-band.ts` (pura e
 * testada); aqui so' existe apresentacao.
 *
 * Alturas iguais por construcao: cada card e' `h-full flex flex-col` dentro de
 * uma grade de uma linha, e o rodape (`mt-auto`) empurra a nota para a base.
 */
import {
  ArrowDownRight,
  ArrowUpRight,
  CircleDollarSign,
  Gauge,
  Megaphone,
  Receipt,
  ShoppingBag,
  type LucideIcon,
} from "lucide-react";
import type { DeltaTone, KpiDescriptor, KpiKey } from "@/lib/gerencial/kpi-band";
import type { TendenciaKpi } from "@/lib/gerencial/kpi-trend";
import { fmtPct } from "@/lib/formatters";

interface Props {
  kpis: KpiDescriptor[];
  loading: boolean;
  /** Task A — o bloco falha SOZINHO: `/overview` em erro nao apaga a evolucao,
   * o Pulso, a matriz, os movimentos nem a fila, que tem fontes proprias. */
  error: boolean;
  onOpen: (key: KpiKey) => void;
  onRetry: () => void;
  /** KPIS-COMPARACAO-1 — tendencia dentro do periodo, so' para quem tem serie. */
  tendencias?: Partial<Record<KpiKey, TendenciaKpi | null>>;
  /** GERENCIAL-METAS-1 — o GMV ja' e' o numero do bloco principal; a faixa
   * mostra so' os quatro de apoio, sem repetir. */
  semGmv?: boolean;
}

/**
 * KPIS-COMPARACAO-1 — `neutral` (Investimento em Ads) mostra a variacao sem
 * cor de juizo: gastar mais nao e' bom nem ruim por si.
 */
function DeltaBadge({ pct, tone = "higher_is_better" }: { pct: number; tone?: DeltaTone }) {
  const up = pct >= 0;
  const Seta = up ? ArrowUpRight : ArrowDownRight;
  const pele =
    tone === "neutral" ? "bg-raised text-ink-muted" : up ? "bg-ok-soft text-ok-ink" : "bg-crit-soft text-crit-ink";
  return (
    <span
      className={`inline-flex w-fit items-center gap-0.5 rounded-md px-1.5 py-0.5 text-xs font-semibold tabular-nums ${pele}`}
    >
      <Seta aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={2.25} />
      {fmtPct(pct)}
    </span>
  );
}

/**
 * UX-TORRE-B2B-1 — um icone por INDICADOR (nao por estado), tingido pela cor
 * de serie que o KPI ja' tinha (`kpi.accent`). Ajuda a achar o numero de
 * relance sem acrescentar significado de bom/ruim.
 */
const ICONE_KPI: Record<KpiKey, { Icone: LucideIcon; tinta: string }> = {
  gmv: { Icone: CircleDollarSign, tinta: "text-chart-1" },
  orders: { Icone: ShoppingBag, tinta: "text-chart-2" },
  avg_ticket: { Icone: Receipt, tinta: "text-chart-4" },
  ad_spend: { Icone: Megaphone, tinta: "text-chart-5" },
  roas: { Icone: Gauge, tinta: "text-chart-3" },
};

/**
 * UX-TORRE-B2B-1 — ocupacao da grade por posicao (0 = GMV, 4 = ROAS).
 *
 * Medido: um GMV de oito digitos com centavos ("R$ 00.000.000,0") precisa de
 * ~180px, e o cartao tinha 107px
 * em 1024 e 131px no mobile — o numero era CORTADO em producao. Agora:
 *   mobile (2 col)  GMV ocupa a linha inteira; Ads e ROAS dividem a ultima.
 *   md/lg (3 col)   GMV, Pedidos, Ticket / Ads, ROAS (duas colunas).
 *   xl (5 col)      uma linha so'.
 * O tamanho do numero acompanha a largura real do cartao em cada faixa.
 */
const SPAN_DO_KPI: Record<number, string | undefined> = {
  0: "col-span-2 md:col-span-1",
  4: "md:col-span-2 xl:col-span-1",
};

function KpiCardSkeleton() {
  return (
    <div
      className="h-full bg-surface border border-line rounded-xl p-4 flex flex-col gap-3 animate-pulse"
      role="status"
      aria-busy="true"
    >
      <span className="sr-only">Carregando indicador…</span>
      <div className="h-3 w-20 bg-raised rounded" />
      <div className="h-7 w-32 bg-raised rounded" />
      <div className="h-3 w-24 bg-raised rounded mt-auto" />
    </div>
  );
}

const SETA: Record<"up" | "down" | "flat", string> = { up: "▲", down: "▼", flat: "=" };

/**
 * Mini grafico da tendencia. Decorativo (`aria-hidden`): a leitura acessivel e'
 * o texto ao lado, que diz a direcao e o recorte comparado.
 */
function Sparkline({ pontos, tinta }: { pontos: number[]; tinta: string }) {
  const min = Math.min(...pontos);
  const max = Math.max(...pontos);
  const faixa = max - min;
  const passo = 100 / (pontos.length - 1);
  // Serie constante fica no MEIO: colada no chao ela parece "caiu para zero".
  const y = (v: number) => (faixa === 0 ? 14 : 26 - ((v - min) / faixa) * 24);
  const coords = pontos.map((v, i) => `${(i * passo).toFixed(2)},${y(v).toFixed(2)}`);
  return (
    <svg viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true" className={`h-7 w-full ${tinta}`}>
      <polyline
        points={coords.join(" ")}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function LinhaDeTendencia({ t, tinta }: { t: TendenciaKpi; tinta: string }) {
  const cor =
    t.direcao === "up" ? "text-ok-ink" : t.direcao === "down" ? "text-crit-ink" : "text-ink-muted";
  return (
    <span className="mt-1 flex flex-col gap-0.5">
      <Sparkline pontos={t.pontos} tinta={tinta} />
      {t.variacaoPct != null && t.direcao && (
        <span className="text-xs leading-snug text-ink-faint tabular-nums">
          Tendência:{" "}
          <span className={`font-semibold ${cor}`}>
            {SETA[t.direcao]} {t.direcao === "flat" ? "estável" : fmtPct(t.variacaoPct)}
          </span>{" "}
          ({t.rotulo})
        </span>
      )}
    </span>
  );
}

function KpiCard({
  kpi,
  onOpen,
  tendencia,
}: {
  kpi: KpiDescriptor;
  onOpen: (key: KpiKey) => void;
  tendencia?: TendenciaKpi | null;
}) {
  const hasChannelList = kpi.channelValues != null && kpi.channelValues.length > 0;
  const { Icone, tinta } = ICONE_KPI[kpi.key];

  return (
    <button
      type="button"
      onClick={() => onOpen(kpi.key)}
      aria-haspopup="dialog"
      // `w-full min-w-0`: sem eles o <button> se dimensiona pelo conteudo e
      // estoura a trilha da grade — era a origem de 5px de overflow horizontal
      // no mobile (390px), medido no QA.
      className="group w-full min-w-0 h-full min-h-11 text-left bg-surface border border-line rounded-xl p-4 flex flex-col gap-1.5 transition-[border-color,box-shadow] hover:border-line-strong hover:shadow-[0_4px_14px_-4px_rgb(var(--tc-accent)/0.18)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <span className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-ink-faint">
          {kpi.label}
          {/* KPIS-COMPARACAO-1: sem `aria-label` no botao, o nome acessivel e'
              o proprio conteudo (valor, variacao, tendencia); a acao vai aqui. */}
          <span className="sr-only"> (abrir decomposição)</span>
        </span>
        <span
          aria-hidden="true"
          className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-raised ${tinta}`}
        >
          <Icone className="h-4 w-4" strokeWidth={1.75} />
        </span>
      </span>

      {/* ROAS nunca tem valor unico: renderiza a lista por canal. */}
      {hasChannelList ? (
        <span className="flex flex-col gap-0.5 mt-0.5">
          {kpi.channelValues!.map((cv) => {
            const highlighted = kpi.highlightChannel === cv.channel;
            return (
              <span key={cv.channel} className="flex items-baseline justify-between gap-2">
                <span className="text-xs text-ink-faint truncate">{cv.label}</span>
                {cv.value ? (
                  <span className="flex shrink-0 items-baseline gap-1.5">
                    {cv.direction && cv.previous && (
                      <span
                        className={`text-xs font-semibold tabular-nums ${
                          cv.direction === "up" ? "text-ok-ink" : cv.direction === "down" ? "text-crit-ink" : "text-ink-muted"
                        }`}
                      >
                        {SETA[cv.direction]} de {cv.previous}
                      </span>
                    )}
                    <span
                      className={`tabular-nums ${
                        highlighted ? "text-xl font-bold text-ink" : "text-sm font-semibold text-ink"
                      }`}
                    >
                      {cv.value}
                    </span>
                  </span>
                ) : (
                  <span className="text-xs text-ink-faint shrink-0">N/D</span>
                )}
              </span>
            );
          })}
        </span>
      ) : kpi.value ? (
        <span className="text-lg md:text-xl lg:text-2xl xl:text-lg min-[1440px]:text-2xl font-semibold tracking-tight text-ink tabular-nums leading-tight">
          {kpi.value}
        </span>
      ) : (
        <span className="text-lg md:text-xl lg:text-2xl xl:text-lg min-[1440px]:text-2xl font-semibold tracking-tight text-ink-faint tabular-nums leading-tight">N/D</span>
      )}

      {(kpi.deltaPct != null && kpi.value) || kpi.reference ? (
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {kpi.deltaPct != null && kpi.value && <DeltaBadge pct={kpi.deltaPct} tone={kpi.deltaTone} />}
          {kpi.reference && <span className="text-xs text-ink-faint tabular-nums">{kpi.reference}</span>}
        </span>
      ) : null}

      {tendencia && tendencia.pontos.length >= 4 && kpi.value && (
        <LinhaDeTendencia t={tendencia} tinta={ICONE_KPI[kpi.key].tinta} />
      )}

      <span className="mt-auto flex flex-col gap-0.5 pt-1">
        {kpi.subvalue && <span className="text-xs text-ink-faint leading-snug">{kpi.subvalue}</span>}
        {kpi.comparisonNote && <span className="text-xs text-ink-faint">{kpi.comparisonNote}</span>}
        {kpi.coverageNote && <span className="text-xs text-ink-faint leading-snug">{kpi.coverageNote}</span>}
        {kpi.unavailableReason && (
          <span className="text-xs text-ink-faint leading-snug">{kpi.unavailableReason}</span>
        )}
      </span>
    </button>
  );
}

export default function KpiBand({ kpis: todos, loading, error, onOpen, onRetry, semGmv = false, tendencias }: Props) {
  const kpis = semGmv ? todos.filter((k) => k.key !== "gmv") : todos;
  // Com quatro cards a grade fecha sem celula vazia em 2 e 4 colunas, entao os
  // spans da fileira de cinco nao se aplicam.
  const span = (i: number) => (semGmv ? undefined : SPAN_DO_KPI[i]);
  if (error) {
    return (
      <section
        aria-label="Indicadores do período"
        className="bg-raised border border-line rounded-xl p-4 flex items-center justify-between gap-3 flex-wrap"
      >
        <div>
          <h2 className="text-sm font-semibold text-ink-muted">Indicadores do período</h2>
          <p className="text-xs text-ink-faint">
            O agregado do período não respondeu nesta carga — GMV, Pedidos, Ticket, Investimento e ROAS ficam
            indisponíveis. Os demais blocos abaixo seguem com as próprias fontes.
          </p>
        </div>
        <button
          type="button"
          onClick={onRetry}
          className="text-xs font-semibold text-ink-muted border border-line-strong rounded-lg px-3 min-h-11 hover:bg-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          Tentar novamente
        </button>
      </section>
    );
  }

  return (
    <section aria-label="Indicadores do período">
      <h2 className="sr-only">Indicadores do período</h2>
      {/* 2 / 3 / 5 colunas. O ROAS (indice 4) ocupa duas colunas fora do
          desktop para que a fileira de cinco nunca deixe celula vazia — e ele
          e' justamente o card que precisa de mais largura, por listar canais. */}
      <div
        className={semGmv ? "grid grid-cols-2 xl:grid-cols-4 gap-3" : "grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3"}
        aria-busy={loading}
      >
        {loading
          ? Array.from({ length: semGmv ? 4 : 5 }, (_, i) => (
              // O ROAS ocupa duas colunas no tablet para nao deixar celula
              // vazia na fileira (grade de 5 em 2 colunas no mobile).
              <div key={i} className={span(i)}>
                <KpiCardSkeleton />
              </div>
            ))
          : kpis.map((kpi, i) => (
              <div key={kpi.key} className={span(i)}>
                <KpiCard kpi={kpi} onOpen={onOpen} tendencia={tendencias?.[kpi.key]} />
              </div>
            ))}
      </div>
    </section>
  );
}
