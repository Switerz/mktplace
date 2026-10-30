"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight } from "lucide-react";
import LiveStatusBadge from "@/components/LiveStatusBadge";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import DrilldownContextLine from "@/components/drilldown/DrilldownContextLine";
import DrilldownMetricPair from "@/components/drilldown/DrilldownMetricPair";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import DrilldownCta from "@/components/drilldown/DrilldownCta";
import DrilldownSecao from "@/components/drilldown/DrilldownSecao";
import InfoDica from "@/components/ui/InfoDica";
import NumeroAnimado from "@/components/ui/NumeroAnimado";
import PriorityCards from "@/components/inteligencia/PriorityCards";
import ConcentrationBars from "@/components/inteligencia/ConcentrationBars";
import EvidenceQueue from "@/components/inteligencia/EvidenceQueue";
import { SkeletonKpiCard, SkeletonTableRows } from "@/components/Skeleton";
import SortableHeader from "@/components/SortableHeader";
import TableScrollHint from "@/components/TableScrollHint";
import OpportunityMatrix from "@/components/inteligencia/OpportunityMatrix";
import {
  BAND_META, QUADRANT_META, freshnessLabel, isSample,
  contagemExata, moedaExata, readPoint, sampleDeclaration, trueSampleNote,
  type BandKey, type QuadrantKey,
} from "@/lib/inteligencia/opportunity";
import { fetchInteligencia, type InteligenciaData, type LtvRow, type OpportunityHighlight } from "@/lib/api-client";
import { fmtBrl, fmtBrlCurto, fmtNumber } from "@/lib/formatters";
import { decBr, pctBr, roasBr } from "@/lib/inteligencia/format";
import { useSortableTable, type SortColumnType } from "@/lib/use-sortable-table";
import { computeRequestStatus } from "@/lib/request-freshness";
import {
  buildInteligenciaArrivalParams, focusForEvidenceKind, focusForQuadrant,
} from "@/lib/brand-arrival-context";
import {
  BRAND_ALL, brandLabel, brandScopeLabel, filterByBrand, mlBrandsFromPayload, parseBrandSelection,
} from "@/lib/inteligencia/brands";
import {
  buildLensHref, INTELIGENCIA_ANCHORS, LENSES, LENS_LABELS, parseLens, readSingleParam, type Lens,
} from "@/lib/inteligencia/lens";
import {
  buildQueue, KIND_LABELS, KIND_RULES, lensSampleNote, listSampleNote, queueForLens,
  sampleNote, sortForLens, tkSampleNote, type EvidenceItem,
} from "@/lib/inteligencia/queue";
import { buildPriorities, priorityLimit, receivedByKind, type Priority } from "@/lib/inteligencia/priorities";
import {
  BUCKET_LABEL, buildParetoProdutosHref, concentrationByBrand, type BucketShare,
} from "@/lib/inteligencia/pareto";

// ---------------------------------------------------------------------------
// Regimes temporais — a página tem DOIS, e eles nunca se misturam.
//
// Os blocos ML leem `marts.fact_ml_produto_ranking`, uma fotografia acumulada
// SEM coluna de data: não respondem a `date_from`/`date_to` e não têm janela.
// `tk_products` é o único bloco com janela (30 dias). O texto do snapshot é
// literal enquanto BE4 não entregar `refreshed_at` — nunca um timestamp
// inventado.
// ---------------------------------------------------------------------------
const REGIME_ML = "Mercado Livre · fotografia do último carregamento";
const REGIME_TK = "TikTok Shop · últimos 30 dias";

function RegimeBadge({ text, tone = "ml" }: { text: string; tone?: "ml" | "tk" }) {
  const cls = tone === "ml"
    ? "bg-slate-100 text-slate-700 border-slate-200"
    : "bg-violet-50 text-violet-800 border-violet-200";
  return (
    <span className={`inline-block text-xs font-semibold px-2.5 py-1 rounded-lg border ${cls}`}>
      {text}
    </span>
  );
}

function SectionCard({ id, title, subtitle, regime, action, children }: {
  id?: string;
  title: string;
  subtitle?: string;
  regime?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      id={id}
      className={`bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden${id ? " scroll-mt-24" : ""}`}
    >
      <div className="px-4 sm:px-6 py-4 border-b border-violet-50 flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
          {subtitle && <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>}
          {regime && <p className="text-xs text-slate-400 mt-1">{regime}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// POPUPS-1 — pecas dos detalhes desta pagina (so' o diálogo as usa).
// ---------------------------------------------------------------------------

/** Topo de todo detalhe: numero grande, rotulo curto, selos e o "ⓘ". */
function TopoDetalhe({ valor, rotulo, contexto, dica }: {
  valor: string;
  rotulo: string;
  contexto: React.ReactNode;
  dica: string;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-x-3 gap-y-1.5">
        <NumeroAnimado texto={valor} className="text-[32px] font-bold leading-none tracking-tight text-ink tabular-nums" />
        <span className="pb-0.5 text-xs text-ink-faint tabular-nums">{rotulo}</span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {contexto}
        <InfoDica texto={dica} />
      </div>
    </div>
  );
}

/** Selo extra ao lado do periodo e do frescor (mesmo desenho do DrilldownContextLine). */
function Selo({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-full border border-line bg-raised px-2 py-0.5 text-xs text-ink-muted tabular-nums">{children}</span>
  );
}

/** Linha de medida: ausente vira "Sem dado" esmaecido, nunca zero. */
function Medida<T>({ label, v, fmt }: { label: string; v: T | null | undefined; fmt: (x: T) => string }) {
  return <EvidenceRow label={label} value={v == null ? "Sem dado" : fmt(v)} tone={v == null ? "muted" : "value"} />;
}

/** Botao que age na propria pagina, com o desenho do DrilldownCta. */
const CLASSE_CTA_BOTAO =
  "group mt-1 flex min-h-11 w-full items-center justify-between gap-3 rounded-xl border border-line px-4 text-sm font-semibold text-accent transition-colors hover:border-accent/40 hover:bg-accent-soft/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

/** `null` → "—" com título "sem dado"; zero continua sendo zero. */
function nullable(v: number | null | undefined, fmt: (x: number) => string) {
  if (v == null) return <span className="text-slate-400" title="sem dado">—</span>;
  return <span className="tabular-nums">{fmt(v)}</span>;
}

/**
 * Estrutura neutra durante `status.loading`.
 *
 * Existe porque o ramo de carregamento estava caindo no conteudo normal: com
 * `displayData` nulo, a pagina exibia "Nenhuma prioridade...", "Sem dados
 * TikTok..." e "Sem dados de LTV..." enquanto a requisicao ainda estava em voo.
 * Dizer "nao ha dado" antes de a resposta chegar e' falso — e o texto de vazio
 * da pagina afirma inclusive que "nao e' modo demonstracao", o que agrava a
 * mentira. Aqui nao ha valor, nao ha contagem zero, nao ha texto de vazio e
 * nao ha controle acionavel.
 */
function InteligenciaSkeleton() {
  return (
    <div role="status" aria-busy="true" aria-live="off" className="flex flex-col gap-6">
      <span className="sr-only">Carregando inteligência…</span>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <SkeletonKpiCard />
        <SkeletonKpiCard />
        <SkeletonKpiCard />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-stretch">
        {[0, 1, 2].map((i) => (
          <div key={i} className="bg-white border border-violet-100 rounded-2xl shadow-sm p-4 flex flex-col gap-3 animate-pulse min-h-[9rem]">
            <div className="h-3 bg-slate-100 rounded w-32" />
            <div className="h-3 bg-slate-100 rounded w-full" />
            <div className="h-8 bg-slate-200 rounded w-16 mt-auto" />
          </div>
        ))}
      </div>
      <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
        <div className="px-4 sm:px-6 py-4 border-b border-violet-50 animate-pulse">
          <div className="h-3 bg-slate-100 rounded w-48" />
        </div>
        <table className="w-full text-sm">
          <tbody><SkeletonTableRows rows={5} cols={6} /></tbody>
        </table>
      </div>
    </div>
  );
}

type DialogState =
  | { kind: "priority"; priority: Priority }
  | { kind: "bucket"; brand: string; share: BucketShare; totalGmv: number }
  | { kind: "evidence"; item: EvidenceItem }
  // ---- Gate V3-1B, bloco 3 ----
  | { kind: "quadrant"; key: QuadrantKey }
  | { kind: "point"; highlight: OpportunityHighlight }
  | { kind: "band"; key: BandKey }
  /** <640: a matriz inteira mora no diálogo (§13). */
  | { kind: "matrix" };

function InteligenciaPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [data, setData] = useState<InteligenciaData | null>(null);
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [resolvedKey, setResolvedKey] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  // Universo de marcas ML, aprendido da resposta SEM escopo. Com o filtro
  // aplicado no servidor (V3-1B), o payload traz só a marca pedida — derivar os
  // chips dele colapsaria o seletor para uma marca só.
  const [brandUniverse, setBrandUniverse] = useState<string[]>([]);

  // A tela não herda filtros globais (decisão do U5, preservada). A lente segue
  // recorte LOCAL. A MARCA, porém, passou a ser parâmetro de fetch no V3-1B:
  // `gmv_reference` do `opportunity_map` é a mediana DO ESCOPO, e recalculá-la
  // no cliente seria refazer no frontend uma decisão que é contrato. Por isso a
  // marca entra na identidade da requisição.
  const brandParamAtual = readSingleParam(searchParams, "brands");
  // QA (J3) mostrou um fallback SILENCIOSO aqui: exigir que a marca estivesse
  // no universo ML aprendido fazia `?brands=apice` cair no escopo GLOBAL, porque
  // apice nao e marca de ML. A tela mostrava o portfolio inteiro como se o
  // filtro nao existisse — exatamente o silencio que o plano proibe.
  //
  // Agora o parametro e encaminhado sempre que tiver forma de `brand_key`, e
  // quem decide o escopo ML e a API: apice devolve `ml_scope_brands: []`, que e
  // o estado correto de "sem escopo Mercado Livre". A allowlist de FORMA fica
  // aqui so para nao encaminhar lixo de URL; a allowlist de VALOR e do backend.
  const escopoPedido = useMemo(() => {
    const v = brandParamAtual?.trim().toLowerCase();
    if (!v) return null;
    return /^[a-z][a-z0-9_]{1,32}$/.test(v) ? [v] : null;
  }, [brandParamAtual]);
  const requestKey = useMemo(
    () => `${retryKey}|${escopoPedido ? escopoPedido.join(",") : "all"}`,
    [retryKey, escopoPedido],
  );

  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setError(null);
    const key = `${retryKey}|${escopoPedido ? escopoPedido.join(",") : "all"}`;
    fetchInteligencia(escopoPedido)
      .then((res) => {
        if (ignore) return;
        setData(res.data);
        setIsLive(res.live);
        // O universo só é aprendido de uma resposta SEM escopo; uma resposta
        // filtrada não conhece as outras marcas e não pode encolher a lista.
        if (!escopoPedido && res.data?.ml_scope_brands?.length) {
          setBrandUniverse(res.data.ml_scope_brands);
        }
        setResolvedKey(key);
        setLoading(false);
      })
      .catch(() => {
        if (ignore) return;
        setError("Falha ao carregar dados de inteligência. Verifique a conexão.");
        // Resolvida TAMBÉM na falha — senão `computeRequestStatus` nunca sai
        // de "loading" (Finding 2 do Gate U4).
        setResolvedKey(key);
        setLoading(false);
      });
    return () => { ignore = true; };
  }, [retryKey, escopoPedido]);

  const status = computeRequestStatus({ loading, error: error != null, resolvedKey, requestKey });
  const displayData = status.fresh ? data : null;
  const displayIsLive = status.fresh ? isLive : false;

  // ---- recorte local: marca e lente, ambos reproduzíveis pela URL ----
  // Universo aprendido tem precedência; o payload é a semente da primeira carga.
  const mlBrands = useMemo(() => {
    const doPayload = mlBrandsFromPayload(displayData);
    return brandUniverse.length >= doPayload.length ? brandUniverse : doPayload;
  }, [displayData, brandUniverse]);
  const brandParam = brandParamAtual;
  const brandSel = useMemo(() => parseBrandSelection(brandParam, mlBrands), [brandParam, mlBrands]);
  const lens: Lens = useMemo(() => parseLens(searchParams), [searchParams]);

  const setBrand = useCallback((next: string) => {
    const qs = new URLSearchParams();
    if (next !== BRAND_ALL) qs.set("brands", next);
    const current = readSingleParam(searchParams, "lens");
    if (current) qs.set("lens", current);
    const q = qs.toString();
    router.replace(q ? `/inteligencia?${q}` : "/inteligencia", { scroll: false });
  }, [router, searchParams]);

  const setLens = useCallback((next: Lens) => {
    router.replace(buildLensHref("/inteligencia", next, searchParams), { scroll: false });
  }, [router, searchParams]);

  // Troca de recorte fecha diálogo incompatível — nenhum detalhe sobrevive a
  // um escopo que não o produziu mais.
  useEffect(() => { setDialog(null); }, [brandSel, lens]);

  // ---- derivações puras sobre o payload já carregado ----
  const priorities = useMemo(() => buildPriorities(displayData, brandSel), [displayData, brandSel]);
  const counts = useMemo(() => receivedByKind(displayData, brandSel), [displayData, brandSel]);
  const queue = useMemo(() => buildQueue(displayData, brandSel), [displayData, brandSel]);
  const lensRows = useMemo(() => sortForLens(queueForLens(queue, lens), lens), [queue, lens]);
  const concentration = useMemo(() => concentrationByBrand(displayData, brandSel), [displayData, brandSel]);
  // Bloco 3: consumido DIRETO do contrato. Nada de mediana, referência,
  // classificação, agregado, faixa ou seleção de destaque recalculados aqui.
  const oppMap = displayData?.opportunity_map ?? null;
  const mlScope = displayData?.ml_scope_brands ?? [];
  // FINDING 1 (Task 2/2): um unico timestamp de exibicao para todo o bloco ML.
  //
  // Ele sai de `displayData`, nao de `data`. Essa distincao e a protecao: quando
  // `status.fresh` e falso — loading, erro ou resposta obsoleta cuja chave nao
  // bate com a atual — `displayData` ja e `null`, e o timestamp cai para `null`
  // junto. Nenhum dialogo pode exibir o frescor de uma requisicao anterior.
  //
  // `null` significa FRESCOR INDISPONIVEL, nunca "agora": em escopo ML vazio o
  // proprio backend devolve null, e `new Date()` mostraria a hora do navegador
  // como se fosse a da carga.
  const mlRefreshedAt = displayData?.ml_snapshot_refreshed_at ?? null;
  const frescorMl = freshnessLabel(mlRefreshedAt);
  const ltvRows = useMemo(() => filterByBrand(displayData?.ltv, brandSel), [displayData, brandSel]);
  const tkReceived = (displayData?.tk_products ?? []).length;
  const tkRows = useMemo(() => (displayData?.tk_products ?? []).slice(0, 5), [displayData]);

  const worst = useMemo(() => sortForLens(queueForLens(queue, "parar"), "parar").slice(0, 5), [queue]);
  const best = useMemo(() => sortForLens(queueForLens(queue, "escalar"), "escalar").slice(0, 5), [queue]);

  // ---- LTV ordenável, com as métricas existentes preservadas ----
  const ltvTypes: Record<string, SortColumnType> = {
    brand: "text", total_buyers: "numeric", repeat_buyers: "numeric", repeat_rate_pct: "numeric",
    avg_customer_ltv: "numeric", vip_buyers: "numeric", one_and_done_buyers: "numeric",
    at_risk_or_churned: "numeric", overall_roas: "numeric",
  };
  const ltvGet = (row: LtvRow, c: string): string | number | null =>
    c === "brand" ? brandLabel(row.brand) : ((row as unknown as Record<string, number | null>)[c] ?? null);
  const ltvSort = useSortableTable(ltvRows, ltvGet, ltvTypes);

  const scope = brandScopeLabel(brandSel);
  const hasData = status.fresh && displayData != null;
  const singleBrand = brandSel !== BRAND_ALL ? brandSel : null;

  const dialogTitle = dialog == null ? ""
    : dialog.kind === "priority" ? `${dialog.priority.title} · ${scope}`
    : dialog.kind === "bucket" ? `Bucket ${dialog.share.bucket.charAt(0)} · ${brandLabel(dialog.brand)}`
    : dialog.kind === "quadrant" ? `${QUADRANT_META[dialog.key].label} · ${scope}`
    : dialog.kind === "band" ? `${BAND_META[dialog.key].label} · ${scope}`
    : dialog.kind === "matrix" ? `Mapa de oportunidades · ${scope}`
    : dialog.kind === "point"
      ? `${dialog.highlight.title ?? dialog.highlight.item_id} · ${brandLabel(dialog.highlight.brand)}`
    : `${dialog.item.title ?? "Produto"} · ${brandLabel(dialog.item.brand)}`;

  // Identidade do conteúdo do diálogo, não da abertura. Abaixo de 640 a matriz
  // mora dentro do diálogo, e clicar num quadrante, faixa ou ponto troca
  // `dialog.kind` sem fechá-lo: o botão clicado é desmontado e, sem esta chave,
  // o foco cairia no `document.body`. O título não serve como chave porque
  // depende do escopo e pode repetir entre conteúdos.
  const dialogFocusKey = dialog == null ? "fechado"
    : dialog.kind === "priority" ? `priority:${dialog.priority.kind}`
    : dialog.kind === "bucket" ? `bucket:${dialog.brand}:${dialog.share.bucket}`
    : dialog.kind === "quadrant" ? `quadrant:${dialog.key}`
    : dialog.kind === "band" ? `band:${dialog.key}`
    : dialog.kind === "matrix" ? "matrix"
    : dialog.kind === "point" ? `point:${dialog.highlight.item_id}`
    : `evidence:${dialog.item.kind}:${dialog.item.brand}:${dialog.item.title}`;

  return (
    <PageContainer>
      {/* ---------------- Bloco 1 — cabeçalho e regimes ---------------- */}
      <PageHeader
        title="Inteligência"
        subtitle="Radar de portfólio."
        status={
          status.fresh ? <LiveStatusBadge live={displayIsLive} />
            : status.loading ? (
              <span className="text-xs text-slate-500 bg-slate-100 border border-slate-200 rounded-lg px-3 py-1.5 font-medium">
                Atualizando dados...
              </span>
            ) : null
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <RegimeBadge text={REGIME_ML} />
        <RegimeBadge text={REGIME_TK} tone="tk" />
        <InfoDica texto="Os blocos ML não têm janela de datas e não respondem ao período global. O filtro de marca é local." />
      </div>

      {/* Chips de marca: a lista vem do PAYLOAD, incluindo Rituária */}
      <div className="-mx-1 overflow-x-auto">
        <div className="flex items-center gap-2 px-1 py-0.5 w-max" role="group" aria-label="Filtro local de marca ML">
          <button
            type="button"
            onClick={() => setBrand(BRAND_ALL)}
            aria-pressed={brandSel === BRAND_ALL}
            disabled={status.loading}
            className={`min-h-11 px-3 rounded-lg text-xs font-semibold border transition-colors whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
              brandSel === BRAND_ALL ? "bg-violet-600 text-white border-violet-600" : "bg-white text-slate-600 border-violet-100 hover:border-violet-300"
            }`}
          >
            Todas as marcas
          </button>
          {mlBrands.map((b) => (
            <button
              key={b}
              type="button"
              onClick={() => setBrand(b)}
              aria-pressed={brandSel === b}
              disabled={status.loading}
              aria-label={`Filtrar por ${brandLabel(b)}`}
              className={`min-h-11 px-3 rounded-lg text-xs font-semibold border transition-colors whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                brandSel === b ? "bg-violet-600 text-white border-violet-600" : "bg-white text-slate-600 border-violet-100 hover:border-violet-300"
              }`}
            >
              {brandLabel(b)}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="bg-rose-50 border border-rose-200 rounded-2xl p-4 flex items-center justify-between gap-4 flex-wrap">
          <div>
            <p className="text-xs font-semibold text-rose-700 uppercase tracking-wider mb-1">Erro de carregamento</p>
            <p className="text-sm text-rose-800">{error}</p>
          </div>
          <button
            type="button"
            onClick={() => { setError(null); setRetryKey((k) => k + 1); }}
            className="min-h-11 px-3 text-xs font-semibold text-rose-700 border border-rose-300 rounded-lg hover:bg-rose-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
          >
            Tentar novamente
          </button>
        </div>
      )}

      <span className="sr-only" aria-live="polite" aria-atomic="true">
        {status.loading ? "Carregando inteligência..."
          : status.error ? "Falha ao carregar."
          : `Dados carregados. Escopo ${scope}, lente ${LENS_LABELS[lens]}.`}
      </span>

      {/* Precedencia: loading -> error -> indisponibilidade real -> fresh.
          `status.loading`, `status.error` e `status.fresh` sao mutuamente
          exclusivos por construcao (`computeRequestStatus`), e o ramo de
          carregamento vem PRIMEIRO para que nenhum texto de vazio apareca
          antes de a requisicao terminar. */}
      {status.loading ? (
        <InteligenciaSkeleton />
      ) : status.error ? (
        <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-10 text-center">
          <p className="text-slate-500 text-sm font-medium">Não foi possível carregar os dados de inteligência.</p>
        </div>
      ) : status.fresh && !hasData ? (
        <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-12 text-center">
          <p className="text-slate-500 text-sm font-medium">Dados de inteligência indisponíveis no momento.</p>
          <button
            type="button"
            onClick={() => setRetryKey((k) => k + 1)}
            className="mt-3 min-h-11 px-3 text-xs font-semibold text-violet-700 border border-violet-200 rounded-lg hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
          >
            Tentar novamente
          </button>
        </div>
      ) : (
        <>
          <nav aria-label="Navegação interna da página" className="flex flex-wrap gap-1 -mx-2.5">
            {[
              [INTELIGENCIA_ANCHORS.prioridades, "Prioridades"],
              [INTELIGENCIA_ANCHORS.oportunidades, "Oportunidades"],
              [INTELIGENCIA_ANCHORS.concentracao, "Concentração"],
              [INTELIGENCIA_ANCHORS.produtos, "Produtos e mídia"],
              [INTELIGENCIA_ANCHORS.fila, "Fila de evidências"],
              [INTELIGENCIA_ANCHORS.ltv, "LTV"],
            ].map(([anchor, label]) => (
              <a
                key={anchor}
                href={`#${anchor}`}
                className="inline-flex items-center justify-center min-h-11 min-w-11 px-2.5 rounded-lg text-xs font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
              >
                {label}
              </a>
            ))}
          </nav>

          {/* ---------------- Bloco 2 — prioridades ---------------- */}
          <section id={INTELIGENCIA_ANCHORS.prioridades} className="scroll-mt-24">
            <h2 className="flex items-center gap-1 text-xs font-semibold text-slate-500 uppercase tracking-widest mb-3">
              Prioridades da fotografia ML atual · {scope}
              <InfoDica texto="As contagens são dos registros recebidos, não do portfólio." />
            </h2>
            {priorities.length > 0 ? (
              <PriorityCards priorities={priorities} onOpen={(p) => setDialog({ kind: "priority", priority: p })} disabled={status.loading} />
            ) : (
              <p className="text-sm text-slate-500 bg-white border border-violet-100 rounded-2xl px-6 py-8 text-center">
                Nenhuma prioridade nas listas recebidas para {scope}.
              </p>
            )}
          </section>

          {/* ---------------- Bloco 3 — matriz definitiva (V3-1B, §7.3) ----------
              O bloco consome `opportunity_map` DIRETO. Nenhuma mediana,
              referência, classificação, agregado, faixa ou seleção de destaque
              é recalculada aqui: tudo isso é contrato do BE6, e refazer no
              cliente seria divergir dele na primeira mudança de regra.

              Universo, agregados e destaques ficam separados na tela porque são
              grandezas diferentes. Quando `returned_count < total_count`, a
              seção declara que os pontos são destaques e que os agregados
              cobrem o universo. */}
          <SectionCard
            id={INTELIGENCIA_ANCHORS.oportunidades}
            title="Mapa de oportunidades"
            subtitle="Retorno × volume no snapshot ML inteiro."
            regime={REGIME_ML}
          >
            <OpportunityMatrix
              map={oppMap}
              mlScope={mlScope}
              onOpenQuadrant={(key) => setDialog({ kind: "quadrant", key })}
              onOpenPoint={(highlight) => setDialog({ kind: "point", highlight })}
              onOpenBand={(key) => setDialog({ kind: "band", key })}
              onOpenMatrix={() => setDialog({ kind: "matrix" })}
              disabled={status.loading}
            />
            <p className="px-4 sm:px-6 pb-5 text-xs text-slate-500">{frescorMl}.</p>
          </SectionCard>

          {/* ---------------- Bloco 4 — concentração ---------------- */}
          <SectionCard
            id={INTELIGENCIA_ANCHORS.concentracao}
            title="Concentração por marca"
            subtitle="GMV por bucket A/B/C/D."
            regime={REGIME_ML}
          >
            <ConcentrationBars
              concentration={concentration}
              onOpenBucket={(brand, share, totalGmv) => setDialog({ kind: "bucket", brand, share, totalGmv })}
              disabled={status.loading}
            />
          </SectionCard>

          {/* ---------------- Bloco 5 — produtos e mídia ----------------
              Contrato §7.5: cada linha ML abre o detalhe do produto no diálogo
              ÚNICO, e cada lista tem CTA para a lente correspondente da fila.
              O truncamento é escrito como "de ao menos N" quando a lista bateu
              o LIMIT do backend — "de N" sugeriria que N é o portfólio. */}
          <section id={INTELIGENCIA_ANCHORS.produtos} className="scroll-mt-24 grid grid-cols-1 lg:grid-cols-3 gap-4 items-stretch">
            {([
              { title: "Maior desperdício de Ads", rows: worst, kind: "parar" as const, total: displayData?.urgent_total_count ?? null },
              { title: "Maior retorno com Ads", rows: best, kind: "escalar" as const, total: displayData?.scale_total_count ?? null },
            ]).map((panel) => {
              const received = counts[panel.kind];
              const max = Math.max(
                ...panel.rows.map((r) => Math.abs((panel.kind === "parar" ? r.ad_spend : r.gmv) ?? 0)),
                1,
              );
              return (
                <SectionCard key={panel.title} title={panel.title} regime={REGIME_ML}>
                  {panel.rows.length === 0 ? (
                    <p className="px-6 py-8 text-center text-sm text-slate-500">
                      Nenhum produto nesta faixa para {scope}.
                    </p>
                  ) : (
                    <ul className="divide-y divide-slate-100 list-none p-0 m-0">
                      {panel.rows.map((r, i) => {
                        const value = (panel.kind === "parar" ? r.ad_spend : r.gmv) ?? 0;
                        return (
                          <li key={`${r.brand}-${i}`} className={`px-4 sm:px-6 py-3 ${i >= 3 ? "hidden sm:block" : ""}`}>
                            <div className="flex items-baseline justify-between gap-3">
                              <p className="text-xs font-semibold text-slate-500">{brandLabel(r.brand)}</p>
                              <p className="text-sm font-semibold text-slate-900 tabular-nums">
                                {panel.kind === "parar"
                                  ? nullable(r.ad_spend, fmtBrl)
                                  : (r.ad_roas == null
                                      ? <span className="text-slate-400" title="sem dado">—</span>
                                      : roasBr(r.ad_roas))}
                              </p>
                            </div>
                            <p className="text-sm text-slate-700 leading-snug" title={r.title ?? undefined}>
                              {r.title && r.title.length > 46 ? `${r.title.slice(0, 46)}…` : (r.title ?? "—")}
                            </p>
                            <div className="mt-1.5 h-1.5 bg-slate-100 rounded-full overflow-hidden" aria-hidden="true">
                              <div
                                className={panel.kind === "parar" ? "tc-barra h-full bg-rose-400" : "tc-barra h-full bg-emerald-400"}
                                style={{ width: `${Math.max(4, (Math.abs(value) / max) * 100)}%` }}
                              />
                            </div>
                            <button
                              type="button"
                              onClick={() => setDialog({ kind: "evidence", item: r })}
                              aria-label={`Detalhe de ${r.title ?? "produto sem título"}, marca ${brandLabel(r.brand)}`}
                              className="mt-2 inline-flex items-center min-h-11 px-3 text-xs font-semibold text-violet-700 border border-violet-200 rounded-lg hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
                            >
                              Detalhe
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  <div className="px-4 sm:px-6 py-3 border-t border-slate-100 flex flex-col gap-1">
                    <p className="text-xs text-slate-500">
                      {panel.rows.length === 0
                        ? "Sem registros nesta lista."
                        : `${panel.total != null
                            ? trueSampleNote(panel.rows.length, panel.total, "registro")
                            : listSampleNote(panel.kind, panel.rows.length, received)}`}
                    </p>
                    <Link
                      href={buildLensHref("/inteligencia", panel.kind, searchParams, { anchor: INTELIGENCIA_ANCHORS.fila })}
                      aria-label={`Ver todos os registros da lente ${LENS_LABELS[panel.kind]} na fila de evidências`}
                      className="inline-flex items-center min-h-11 text-xs font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded self-start"
                    >
                      Ver todos na fila →
                    </Link>
                  </div>
                </SectionCard>
              );
            })}

            {/* TikTok: etiqueta e teto próprios; sem ROAS/Ads, que o contrato
                do TikTok não entrega, e sem drill-down sem evidência. */}
            <SectionCard
              id={INTELIGENCIA_ANCHORS.produtosTiktok}
              title="Produtos TikTok em destaque"
              regime={REGIME_TK}
            >
              {tkRows.length === 0 ? (
                <p className="px-6 py-8 text-center text-sm text-slate-500">Sem dados TikTok na janela de 30 dias.</p>
              ) : (
                <ul className="divide-y divide-slate-100 list-none p-0 m-0">
                  {tkRows.map((r, i) => (
                    <li key={`${r.brand}-tk-${i}`} className={`px-4 sm:px-6 py-3 ${i >= 3 ? "hidden sm:block" : ""}`}>
                      <div className="flex items-baseline justify-between gap-3">
                        <p className="text-xs font-semibold text-slate-500">{brandLabel(r.brand)}</p>
                        <p className="text-sm font-semibold text-slate-900 tabular-nums">{fmtBrl(r.gmv)}</p>
                      </div>
                      <p className="text-sm text-slate-700 leading-snug" title={r.product_name}>
                        {r.product_name.length > 46 ? `${r.product_name.slice(0, 46)}…` : r.product_name}
                      </p>
                      <p className="text-xs text-slate-500 tabular-nums mt-0.5">
                        {fmtNumber(r.orders)} pedidos · nota {r.avg_rating == null ? "—" : decBr(r.avg_rating)}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
              {tkRows.length > 0 && (
                <p className="px-4 sm:px-6 py-3 text-xs text-slate-500 border-t border-slate-100">
                  {tkSampleNote(tkRows.length, tkReceived)}
                </p>
              )}
            </SectionCard>
          </section>

          {/* ---------------- Bloco 6 — fila de evidências ---------------- */}
          <SectionCard
            id={INTELIGENCIA_ANCHORS.fila}
            title="Fila de evidências"
            subtitle={lensSampleNote(lens, counts)}
            regime={REGIME_ML}
          >
            <div className="px-4 sm:px-6 pt-4 flex flex-wrap gap-1" role="group" aria-label="Lente da fila de evidências">
              {LENSES.map((l) => {
                const count = l === "todos" ? counts.parar + counts.escalar + counts.testar : counts[l];
                return (
                  <button
                    key={l}
                    type="button"
                    onClick={() => setLens(l)}
                    aria-pressed={lens === l}
                    aria-label={`Lente ${LENS_LABELS[l]}, ${count} registros exibidos`}
                    className={`min-h-11 px-3 rounded-lg text-xs font-semibold border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                      lens === l ? "bg-violet-600 text-white border-violet-600" : "bg-white text-slate-600 border-violet-100 hover:border-violet-300"
                    }`}
                  >
                    {LENS_LABELS[l]}
                    <span className={`ml-1.5 tabular-nums ${lens === l ? "text-violet-100" : "text-slate-400"}`}>{count}</span>
                  </button>
                );
              })}
            </div>
            <div className="mt-4">
              <EvidenceQueue
                lens={lens}
                rows={lensRows}
                onOpenDetail={(item) => setDialog({ kind: "evidence", item })}
                loading={status.loading}
              />
            </div>
          </SectionCard>

          {/* ---------------- Bloco 7 — LTV e destinos ---------------- */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-stretch">
            <div className="lg:col-span-2">
              <SectionCard
                id={INTELIGENCIA_ANCHORS.ltv}
                title="LTV e fidelização"
                regime={REGIME_ML}
              >
                {ltvRows.length === 0 ? (
                  <p className="px-6 py-8 text-center text-sm text-slate-500">Sem dados de LTV para {scope}.</p>
                ) : (
                  <TableScrollHint>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-slate-50 text-left">
                          <SortableHeader label="Marca" column="brand" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="left" />
                          <SortableHeader label="Compradores" column="total_buyers" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="right" />
                          <SortableHeader label="Recorrência" column="repeat_rate_pct" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="right" />
                          <SortableHeader label="LTV médio" column="avg_customer_ltv" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="right" />
                          <SortableHeader label="Em risco" column="at_risk_or_churned" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="right" />
                          <SortableHeader label="ROAS geral" column="overall_roas" sort={ltvSort.sort} onSort={ltvSort.toggleSort} align="right" />
                          <th scope="col" className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">Detalhe</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {ltvSort.sortedRows.map((r) => (
                          <tr key={r.brand}>
                            <td className="px-4 py-3 text-xs font-semibold text-slate-700 whitespace-nowrap">{brandLabel(r.brand)}</td>
                            <td className="px-4 py-3 text-right text-sm text-slate-600">{nullable(r.total_buyers, fmtNumber)}</td>
                            <td className="px-4 py-3 text-right text-sm text-slate-600">
                              {r.repeat_rate_pct == null ? <span className="text-slate-400" title="sem dado">—</span> : <span className="tabular-nums">{pctBr(r.repeat_rate_pct)}</span>}
                            </td>
                            <td className="px-4 py-3 text-right text-sm font-semibold text-slate-900">{nullable(r.avg_customer_ltv, fmtBrl)}</td>
                            <td className="px-4 py-3 text-right text-sm text-slate-600">{nullable(r.at_risk_or_churned, fmtNumber)}</td>
                            <td className="px-4 py-3 text-right text-sm text-slate-600">
                              {r.overall_roas == null ? <span className="text-slate-400" title="sem dado">—</span> : <span className="tabular-nums">{roasBr(r.overall_roas)}</span>}
                            </td>
                            <td className="px-4 py-3 text-right">
                              <Link
                                // Contexto QUENTE (V3-2): a propria linha e' a
                                // evidencia de recorrencia/LTV da marca, entao o
                                // foco `ltv` e' demonstravel pelo dado exibido.
                                href={`/brand/${r.brand}?brands=${r.brand}&${buildInteligenciaArrivalParams("ltv", r.brand)}`}
                                aria-label={`Abrir a visão da marca ${brandLabel(r.brand)} pela evidência de LTV`}
                                className="inline-flex items-center justify-center min-h-11 px-3 text-xs font-semibold text-violet-700 border border-violet-200 rounded-lg hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
                              >
                                Abrir
                              </Link>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableScrollHint>
                )}
              </SectionCard>
            </div>

            <SectionCard title="Próximos destinos">
              <ul className="px-4 sm:px-6 py-4 flex flex-col gap-2 list-none m-0">
                <li>
                  <Link href="/canais" className="inline-flex items-center min-h-11 text-sm font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded">
                    Comparar canais →
                  </Link>
                </li>
                <li>
                  <Link
                    href={singleBrand ? `/produtos?channels=ml&brands=${singleBrand}` : "/produtos?channels=ml"}
                    className="inline-flex items-center min-h-11 text-sm font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
                  >
                    Abrir Produtos no Mercado Livre →
                  </Link>
                </li>
                {singleBrand && (
                  <li>
                    <Link
                      href={`/brand/${singleBrand}?brands=${singleBrand}`}
                      aria-label={`Abrir a visão da marca ${brandLabel(singleBrand)}`}
                      className="inline-flex items-center min-h-11 text-sm font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
                    >
                      Abrir marca {brandLabel(singleBrand)} →
                    </Link>
                  </li>
                )}
              </ul>
            </SectionCard>
          </div>
        </>
      )}

      {/* ---------------- Diálogo único (contrato §3 do G2) ----------------
          POPUPS-1 — todo detalhe abre com o numero grande, selos de contexto e
          um "ⓘ" com a regra; depois as quebras e a saida. Regra, metodologia e
          "o que nao e'" moram no ⓘ, nunca em paragrafo. */}
      <KpiDrilldownDialog open={dialog != null} onClose={() => setDialog(null)} title={dialogTitle} focusResetKey={dialogFocusKey}>
        {/* ---- V3-1B: quadrante. GMV do quadrante, criterio, universo x
             destaques e ad spend. A declaracao de amostra vai para o ⓘ. ---- */}
        {dialog?.kind === "quadrant" && oppMap && (() => {
          const q = oppMap.quadrants.find((x) => x.key === dialog.key);
          const meta = QUADRANT_META[dialog.key];
          const gmvRef = oppMap.gmv_reference == null ? "sem referência" : moedaExata(oppMap.gmv_reference);
          return (
            <div className="flex flex-col gap-4 text-sm">
              <TopoDetalhe
                valor={fmtBrlCurto(q?.gmv ?? 0)}
                rotulo="GMV do quadrante"
                contexto={
                  <>
                    <DrilldownContextLine leading={`${scope} · fotografia ML`} periodLabel="sem janela temporal" refreshedAt={mlRefreshedAt} />
                    <Selo>{meta.regra(roasBr(oppMap.roas_reference), gmvRef)}</Selo>
                  </>
                }
                dica={`${meta.leitura} Referências descritivas do portfólio, não metas.${isSample(oppMap) ? ` ${sampleDeclaration(oppMap)}` : ""}`}
              />
              {/* Contagem EXATA nas duas (V3F): o detalhe e' onde se confere
                  que N destaques saem de M produtos; `fmtNumber` abreviava. */}
              <DrilldownSecao titulo="Universo do quadrante" acao={<span className="tabular-nums">de {contagemExata(oppMap.total_count)} produtos</span>}>
                <ul className="flex flex-col">
                  <EvidenceRow
                    label="Produtos no quadrante (universo)"
                    value={contagemExata(q?.count ?? 0)}
                    pct={oppMap.total_count > 0 ? ((q?.count ?? 0) / oppMap.total_count) * 100 : null}
                  />
                  <EvidenceRow label="Ad spend do quadrante" value={fmtBrlCurto(q?.ad_spend ?? 0)} />
                  <EvidenceRow label="Destaques plotados" value={contagemExata(q?.returned_count ?? 0)} tone="muted" />
                </ul>
              </DrilldownSecao>
              {/* Botao, nao link: troca a lente na propria pagina. Mesmo
                  desenho do DrilldownCta. */}
              <button
                type="button"
                onClick={() => { setDialog(null); setLens(dialog.key === "escalar" ? "escalar" : dialog.key === "reduzir_parar" ? "parar" : "todos"); }}
                aria-label={`Ver a fila de evidências filtrada a partir do quadrante ${meta.label}`}
                className={CLASSE_CTA_BOTAO}
              >
                <span>Ver evidências na fila</span>
                <ArrowRight aria-hidden="true" className="h-4 w-4 shrink-0 transition-transform duration-200 group-hover:translate-x-0.5" />
              </button>
            </div>
          );
        })()}

        {/* ---- V3-1B: ponto. GMV e ROAS contra as referencias; o porque no ⓘ. ---- */}
        {dialog?.kind === "point" && oppMap && (() => {
          const h = dialog.highlight;
          const meta = QUADRANT_META[h.quadrant];
          const leitura = readPoint(h, oppMap, moedaExata, roasBr);
          const gmvRef = oppMap.gmv_reference == null ? "sem referência" : moedaExata(oppMap.gmv_reference);
          // "" quando o quadrante nao sustenta foco: CTA frio.
          const focoDoPonto = buildInteligenciaArrivalParams(focusForQuadrant(h.quadrant), h.brand);
          return (
            <div className="flex flex-col gap-4 text-sm">
              <TopoDetalhe
                valor={moedaExata(h.gmv)}
                rotulo={`GMV ${meta.gmvHigh ? "≥" : "<"} referência ${gmvRef}`}
                contexto={
                  <>
                    <DrilldownContextLine leading={`${brandLabel(h.brand)} · fotografia ML`} periodLabel="sem janela temporal" refreshedAt={mlRefreshedAt} />
                    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-semibold ${meta.chip}`}>
                      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${meta.dot}`} />
                      {meta.label}
                    </span>
                  </>
                }
                dica={leitura.porque}
              />
              <DrilldownSecao titulo="Retorno em ads">
                <DrilldownMetricPair
                  label={h.ad_roas == null ? "ROAS (indisponível no snapshot)" : `ROAS ${meta.roasHigh ? "≥" : "<"} referência`}
                  value={h.ad_roas == null ? "Sem dado" : roasBr(h.ad_roas)}
                  referenceLabel="Referência de ROAS"
                  referenceValue={roasBr(oppMap.roas_reference)}
                />
                <ul className="flex flex-col">
                  <EvidenceRow label="Ad spend" value={moedaExata(h.ad_spend)} />
                </ul>
              </DrilldownSecao>
              <DrilldownCta
                // V3-2: o consumidor de foco existe, e o CTA esquenta APENAS
                // quando o quadrante prova a populacao. `escalar` exige ROAS >=
                // referencia, a mesma da lista `scale` que forma `escala_ads`,
                // logo todo ponto dali pertence aquele foco. Os outros tres
                // quadrantes seguem FRIOS: `focusForQuadrant` devolve null e
                // `buildInteligenciaArrivalParams` devolve "" — inventar
                // classificacao para produzir contexto seria pior que nao ter.
                href={`/brand/${h.brand}?brands=${h.brand}${
                  focoDoPonto ? `&${focoDoPonto}` : ""
                }`}
                ariaLabel={`Abrir a visão da marca ${brandLabel(h.brand)} a partir deste produto`}
              >
                Abrir marca deste produto →
              </DrilldownCta>
            </div>
          );
        })()}

        {/* ---- V3-1B: faixa. Contagem exata; a explicacao (e o que a faixa
             NAO e') vai para o ⓘ. A unica ressalva que muda a leitura — ROAS
             nao medido nao e' ROAS baixo — fica numa linha. ---- */}
        {dialog?.kind === "band" && oppMap && (() => {
          const b = oppMap.bands.find((x) => x.key === dialog.key);
          const meta = BAND_META[dialog.key];
          return (
            <div className="flex flex-col gap-4 text-sm">
              <TopoDetalhe
                valor={contagemExata(b?.count ?? 0)}
                rotulo="produtos na faixa"
                contexto={<DrilldownContextLine leading={`${scope} · fotografia ML`} periodLabel="sem janela temporal" refreshedAt={mlRefreshedAt} />}
                dica={`${meta.explicacao} ${meta.naoConfundir}`}
              />
              <DataQualityNote
                note={dialog.key === "roas_indisponivel_com_investimento" ? "Falha de mensuração, não ROAS baixo." : null}
              />
              <DrilldownSecao titulo="Na faixa" acao={<span className="tabular-nums">universo classificado: {contagemExata(oppMap.total_count)}</span>}>
                <ul className="flex flex-col">
                  <EvidenceRow label="GMV da faixa" value={fmtBrlCurto(b?.gmv ?? 0)} />
                  <EvidenceRow label="Ad spend da faixa" value={fmtBrlCurto(b?.ad_spend ?? 0)} />
                </ul>
              </DrilldownSecao>
            </div>
          );
        })()}

        {/* ---- V3-1B: <640, a matriz inteira dentro do diálogo (§13) ---- */}
        {dialog?.kind === "matrix" && oppMap && (
          <OpportunityMatrix
            map={oppMap}
            mlScope={mlScope}
            onOpenQuadrant={(key) => setDialog({ kind: "quadrant", key })}
            onOpenPoint={(highlight) => setDialog({ kind: "point", highlight })}
            onOpenBand={(key) => setDialog({ kind: "band", key })}
            semBreakpoint
          />
        )}

        {/* ---- Prioridade: o dinheiro da amostra, quantos registros, e quem
             pesa mais nela (barra = fatia da amostra). ---- */}
        {dialog?.kind === "priority" && (() => {
          const p = dialog.priority;
          const valorDe = (c: EvidenceItem) => (p.kind === "parar" ? c.ad_spend : c.gmv);
          return (
            <div className="flex flex-col gap-4 text-sm">
              <TopoDetalhe
                valor={fmtBrlCurto(p.amount)}
                rotulo={p.amountLabel}
                contexto={
                  <>
                    <DrilldownContextLine leading={`${scope} · fotografia ML`} periodLabel="sem janela temporal" refreshedAt={null} />
                    <Selo>{p.received === 1 ? "1 registro exibido" : `${p.received} registros exibidos`}</Selo>
                  </>
                }
                dica={`Na lista: ${KIND_RULES[p.kind]}.`}
              />
              <DataQualityNote note={`Lista capada em ${priorityLimit(p.kind)} registros — não é o total do portfólio.`} />
              <DrilldownSecao titulo="Maiores contribuintes" acao={<span className="tabular-nums">top {p.contributors.length}</span>}>
                <ul className="flex flex-col">
                  {p.contributors.map((c, i) => {
                    const v = valorDe(c);
                    return (
                      <EvidenceRow
                        key={`${c.brand}-${i}`}
                        label={`${brandLabel(c.brand)} · ${c.title}`}
                        pct={v != null && p.amount > 0 ? (v / p.amount) * 100 : null}
                        value={v == null ? "Sem dado" : fmtBrlCurto(v)}
                        tone={v == null ? "muted" : "value"}
                      />
                    );
                  })}
                </ul>
              </DrilldownSecao>
              <DrilldownCta href={buildLensHref("/inteligencia", p.kind, searchParams)}>
                Ver evidências na lente {LENS_LABELS[p.kind]} →
              </DrilldownCta>
            </div>
          );
        })()}

        {/* ---- Bucket: a fatia do GMV da marca e, ao lado, os outros buckets
             da mesma marca (dado ja' carregado em `concentration`). ---- */}
        {dialog?.kind === "bucket" && (() => {
          const s = dialog.share;
          const daMarca = concentration.find((c) => c.brand === dialog.brand)?.buckets ?? [];
          return (
            <div className="flex flex-col gap-4 text-sm">
              <TopoDetalhe
                valor={s.sharePct == null ? "Sem dado" : pctBr(s.sharePct)}
                rotulo={`do GMV de ${brandLabel(dialog.brand)}`}
                contexto={
                  <>
                    <DrilldownContextLine leading={`${brandLabel(dialog.brand)} · fotografia ML`} periodLabel="sem janela temporal" refreshedAt={null} />
                    <Selo>{BUCKET_LABEL[s.bucket]}</Selo>
                  </>
                }
                dica={`Pareto do GMV da marca: ${Object.values(BUCKET_LABEL).join("; ")}.`}
              />
              <DrilldownSecao titulo="Este bucket">
                <ul className="flex flex-col">
                  <EvidenceRow label="GMV do bucket" value={fmtBrlCurto(s.gmv)} />
                  <EvidenceRow label="Produtos no bucket" value={fmtNumber(dialog.share.n_products)} />
                  <EvidenceRow label="Ad spend no bucket" value={fmtBrlCurto(s.ad_spend)} />
                </ul>
              </DrilldownSecao>
              {daMarca.length > 1 && (
                <DrilldownSecao titulo="Por bucket" acao={<span className="tabular-nums">GMV da marca {fmtBrlCurto(dialog.totalGmv)}</span>}>
                  <ul className="flex flex-col">
                    {daMarca.map((x) => (
                      <EvidenceRow
                        key={x.bucket}
                        label={BUCKET_LABEL[x.bucket]}
                        ponto={x.bucket === s.bucket ? "bg-accent" : undefined}
                        mutedLabel={x.bucket !== s.bucket}
                        pct={x.sharePct}
                        value={`${fmtBrlCurto(x.gmv)} · ${x.sharePct == null ? "—" : pctBr(x.sharePct)}`}
                      />
                    ))}
                  </ul>
                </DrilldownSecao>
              )}
              <DrilldownCta
                href={buildParetoProdutosHref(dialog.brand, dialog.share.bucket)}
                ariaLabel={`Ver produtos do bucket ${dialog.share.bucket.charAt(0)} de ${brandLabel(dialog.brand)} em Produtos`}
              >
                Ver produtos deste bucket →
              </DrilldownCta>
            </div>
          );
        })()}

        {/* ---- Evidencia: a metrica que pos o produto na lista em destaque;
             venda e midia separadas, sem repetir o numero do topo. ---- */}
        {dialog?.kind === "evidence" && (() => {
          const it = dialog.item;
          const parar = it.kind === "parar";
          const topo = parar ? it.ad_spend : it.gmv;
          return (
            <div className="flex flex-col gap-4 text-sm">
              <TopoDetalhe
                valor={topo == null ? "Sem dado" : fmtBrlCurto(topo)}
                rotulo={parar ? "Ad spend" : "GMV"}
                contexto={
                  <DrilldownContextLine
                    leading={`${KIND_LABELS[it.kind]} · fotografia ML`}
                    periodLabel="sem janela temporal"
                    refreshedAt={null}
                  />
                }
                dica={`Na lista porque são ${KIND_RULES[it.kind]}.`}
              />
              <DrilldownSecao titulo="Venda">
                <ul className="flex flex-col">
                  {parar && <Medida label="GMV" v={it.gmv} fmt={fmtBrlCurto} />}
                  <Medida label="Unidades vendidas" v={it.units_sold} fmt={contagemExata} />
                  <Medida label="Velocidade" v={it.revenue_velocity} fmt={(x) => x} />
                  <Medida label="Bucket Pareto" v={it.pareto_bucket} fmt={(x) => x} />
                  <Medida label="Cancelamento" v={it.cancel_rate_pct} fmt={pctBr} />
                </ul>
              </DrilldownSecao>
              <DrilldownSecao titulo="Mídia">
                <ul className="flex flex-col">
                  {!parar && <Medida label="Ad spend" v={it.ad_spend} fmt={fmtBrlCurto} />}
                  <Medida label="ROAS" v={it.ad_roas} fmt={roasBr} />
                  <Medida label="ACOS" v={it.ad_acos_pct} fmt={pctBr} />
                  <Medida label="Dias com ads" v={it.days_advertised} fmt={contagemExata} />
                </ul>
              </DrilldownSecao>
              <DrilldownCta
                // Mapeamento EXATO lente -> foco: as tres lentes SAO as tres
                // listas do payload, e os tres focos sao essas mesmas listas.
                href={`/brand/${dialog.item.brand}?brands=${dialog.item.brand}&${
                  buildInteligenciaArrivalParams(focusForEvidenceKind(dialog.item.kind), dialog.item.brand)
                }`}
                ariaLabel={`Abrir a visão da marca ${brandLabel(dialog.item.brand)} com o contexto desta evidência`}
              >
                Abrir marca {brandLabel(dialog.item.brand)} →
              </DrilldownCta>
            </div>
          );
        })()}
      </KpiDrilldownDialog>
    </PageContainer>
  );
}

export default function InteligenciaPage() {
  // `useSearchParams` exige limite de Suspense no App Router.
  return (
    <Suspense fallback={<PageContainer><p className="text-sm text-slate-500">Carregando inteligência…</p></PageContainer>}>
      <InteligenciaPageInner />
    </Suspense>
  );
}
