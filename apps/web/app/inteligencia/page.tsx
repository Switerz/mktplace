"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import LiveStatusBadge from "@/components/LiveStatusBadge";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import DrilldownContextLine from "@/components/drilldown/DrilldownContextLine";
import DrilldownMetricPair from "@/components/drilldown/DrilldownMetricPair";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import DrilldownCta from "@/components/drilldown/DrilldownCta";
import PriorityCards from "@/components/inteligencia/PriorityCards";
import ConcentrationBars from "@/components/inteligencia/ConcentrationBars";
import EvidenceQueue from "@/components/inteligencia/EvidenceQueue";
import { SkeletonKpiCard, SkeletonTableRows } from "@/components/Skeleton";
import SortableHeader from "@/components/SortableHeader";
import TableScrollHint from "@/components/TableScrollHint";
import OpportunityMatrix from "@/components/inteligencia/OpportunityMatrix";
import {
  BAND_META, QUADRANT_META, freshnessLabel, isSample, matrixState,
  moedaExata, readPoint, referenceOrigins, sampleDeclaration, trueSampleNote,
  type BandKey, type QuadrantKey,
} from "@/lib/inteligencia/opportunity";
import { fetchInteligencia, type InteligenciaData, type LtvRow, type OpportunityHighlight } from "@/lib/api-client";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { decBr, pctBr, roasBr } from "@/lib/inteligencia/format";
import { useSortableTable, type SortColumnType } from "@/lib/use-sortable-table";
import { computeRequestStatus } from "@/lib/request-freshness";
import {
  BRAND_ALL, brandLabel, brandScopeLabel, filterByBrand, mlBrandsFromPayload, parseBrandSelection,
} from "@/lib/inteligencia/brands";
import {
  buildLensHref, INTELIGENCIA_ANCHORS, LENSES, LENS_LABELS, parseLens, readSingleParam, type Lens,
} from "@/lib/inteligencia/lens";
import {
  buildQueue, KIND_LABELS, KIND_RULES, lensSampleNote, listSampleNote, queueForLens,
  sampleNote, sortForLens, TK_PRODUCTS_LIMIT, tkSampleNote, type EvidenceItem,
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
  const oppState = matrixState(oppMap, mlScope);
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

  return (
    <PageContainer>
      {/* ---------------- Bloco 1 — cabeçalho e regimes ---------------- */}
      <PageHeader
        title="Inteligência"
        subtitle="Radar de portfólio: o que exige atenção, quanto representa e para onde seguir."
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
      </div>

      <div className="bg-slate-50 border border-slate-200 rounded-2xl p-3">
        <p className="text-xs text-slate-600">
          Os blocos de Mercado Livre vêm de uma <strong>fotografia acumulada sem janela de datas</strong> — não
          respondem ao período global e não têm intervalo. Só o bloco de produtos TikTok tem janela, de 30 dias.
          O filtro de marca abaixo é <strong>local</strong> e afeta as análises ML compatíveis.
        </p>
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
          <p className="text-slate-500 text-xs mt-1">Use &quot;Tentar novamente&quot; no banner de erro acima.</p>
        </div>
      ) : status.fresh && !hasData ? (
        <div className="bg-white border border-violet-100 rounded-2xl shadow-sm px-6 py-12 text-center">
          <p className="text-slate-500 text-sm font-medium">Dados de inteligência indisponíveis no momento.</p>
          <p className="text-slate-500 text-xs mt-1">
            Não é modo demonstração — a fonte não retornou dados. Tente novamente em instantes.
          </p>
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
            <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-1">
              Prioridades da fotografia ML atual
            </h2>
            <p className="text-xs text-slate-500 mb-3">
              {REGIME_ML} · escopo {scope}. As contagens são dos registros recebidos, não do portfólio.
            </p>
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
            subtitle={
              oppState === "available"
                ? "Retorno × volume no snapshot ML inteiro. Clique num quadrante para os agregados, ou num ponto para o produto."
                : "Retorno × volume no snapshot ML inteiro."
            }
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
            <p className="px-4 sm:px-6 pb-5 text-xs text-slate-500">
              Escopo <code>ml_snapshot</code>: fotografia do Mercado Livre, sem janela
              temporal. TikTok não entra neste mapa porque a fonte dele não tem ROAS.
              {" "}{frescorMl}.
            </p>
          </SectionCard>

          {/* ---------------- Bloco 4 — concentração ---------------- */}
          <SectionCard
            id={INTELIGENCIA_ANCHORS.concentracao}
            title="Concentração por marca"
            subtitle="Distribuição do GMV entre os buckets A/B/C/D. Clique num bucket para ver os agregados."
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
                                className={panel.kind === "parar" ? "h-full bg-rose-400" : "h-full bg-emerald-400"}
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
                            : listSampleNote(panel.kind, panel.rows.length, received)} · 3 no mobile é apresentação, não cobertura.`}
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
            <SectionCard title="Produtos TikTok em destaque" regime={REGIME_TK}>
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
              <p className="px-4 sm:px-6 py-3 text-xs text-slate-500 border-t border-slate-100">
                {tkRows.length === 0
                  ? `O payload traz até ${TK_PRODUCTS_LIMIT} produtos nesta janela.`
                  : `${tkSampleNote(tkRows.length, tkReceived)} · 3 no mobile é apresentação, não cobertura.`}{" "}
                Agrupado por nome de produto, como o payload entrega. Sem ROAS ou Ads: o contrato do TikTok
                não traz investimento de mídia.
              </p>
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
                subtitle="Comportamento de compradores por marca. A fonte não tem dimensão temporal."
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
                                href={`/brand/${r.brand}?brands=${r.brand}`}
                                aria-label={`Abrir a visão da marca ${brandLabel(r.brand)}`}
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

            <SectionCard title="Próximos destinos" subtitle="Navegação limpa, sem contexto de origem.">
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
              <p className="px-4 sm:px-6 pb-4 text-xs text-slate-500">
                Selecione uma única marca para habilitar o destino de marca.
              </p>
            </SectionCard>
          </div>
        </>
      )}

      {/* ---------------- Diálogo único (contrato §3 do G2) ---------------- */}
      <KpiDrilldownDialog open={dialog != null} onClose={() => setDialog(null)} title={dialogTitle}>
        {/* ---- V3-1B: quadrante. Regra, fronteiras, origem de cada
             referência, agregados do UNIVERSO e quantos destaques. ---- */}
        {dialog?.kind === "quadrant" && oppMap && (() => {
          const q = oppMap.quadrants.find((x) => x.key === dialog.key);
          const meta = QUADRANT_META[dialog.key];
          const gmvRef = oppMap.gmv_reference == null ? "sem referência" : moedaExata(oppMap.gmv_reference);
          return (
            <div className="flex flex-col gap-4">
              <DrilldownContextLine leading={`${scope} · fotografia ML`} periodLabel="sem janela temporal" refreshedAt={mlRefreshedAt} />
              <p className="text-sm text-slate-700">
                <strong>Regra:</strong> {meta.regra(roasBr(oppMap.roas_reference), gmvRef)}.{" "}
                Fronteiras altas são inclusivas. {meta.leitura}
              </p>
              <DrilldownMetricPair
                label="Produtos no quadrante (universo)"
                value={fmtNumber(q?.count ?? 0)}
                referenceLabel="Destaques plotados"
                referenceValue={fmtNumber(q?.returned_count ?? 0)}
              />
              <DrilldownMetricPair
                label="GMV do quadrante"
                value={fmtBrl(q?.gmv ?? 0)}
                referenceLabel="Ad spend do quadrante"
                referenceValue={fmtBrl(q?.ad_spend ?? 0)}
              />
              <div>
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
                  De onde vem cada referência
                </p>
                <ul className="flex flex-col gap-1 list-none p-0 m-0">
                  {referenceOrigins(oppMap, moedaExata).map((t) => (
                    <li key={t} className="text-xs text-slate-600">{t}</li>
                  ))}
                </ul>
              </div>
              <DataQualityNote
                note={`Fotografia ML sem janela temporal: o quadrante descreve o estado do snapshot, não um período. ${sampleDeclaration(oppMap)}`}
              />
              <button
                type="button"
                onClick={() => { setDialog(null); setLens(dialog.key === "escalar" ? "escalar" : dialog.key === "reduzir_parar" ? "parar" : "todos"); }}
                aria-label={`Ver a fila de evidências filtrada a partir do quadrante ${meta.label}`}
                className="self-start inline-flex items-center min-h-11 text-sm font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
              >
                Ver evidências na fila →
              </button>
            </div>
          );
        })()}

        {/* ---- V3-1B: ponto. Produto, as duas comparações e o porquê. ---- */}
        {dialog?.kind === "point" && oppMap && (() => {
          const h = dialog.highlight;
          const leitura = readPoint(h, oppMap, moedaExata, roasBr);
          return (
            <div className="flex flex-col gap-4">
              <DrilldownContextLine leading={`${brandLabel(h.brand)} · fotografia ML`} periodLabel="sem janela temporal" refreshedAt={mlRefreshedAt} />
              <DrilldownMetricPair label="GMV" value={fmtBrl(h.gmv)} referenceLabel="Ad spend" referenceValue={fmtBrl(h.ad_spend)} />
              <DrilldownMetricPair
                label="ROAS"
                value={h.ad_roas == null ? "sem dado" : roasBr(h.ad_roas)}
                referenceLabel="Referência de ROAS"
                referenceValue={roasBr(oppMap.roas_reference)}
              />
              <ul className="flex flex-col gap-1 list-none p-0 m-0">
                <li className="text-sm text-slate-700">{leitura.roasComparacao}</li>
                <li className="text-sm text-slate-700">{leitura.gmvComparacao}</li>
              </ul>
              <p className="text-sm text-slate-700">{leitura.porque}</p>
              <DataQualityNote
                note="Fotografia ML sem janela temporal. As duas referências descrevem o portfólio do escopo atual; não são metas."
              />
              <Link
                // Link FRIO, de proposito. O plano reserva o contexto QUENTE de
                // chegada ao V3-2, e so' "com wiring real": a pagina de Marca nao tem
                // hoje nenhum consumidor de foco vindo da Inteligencia, e emitir
                // contexto que ninguem le seria divida sem retorno. O filtro de
                // marca, sim, viaja.
                href={`/brand/${h.brand}?brands=${h.brand}`}
                aria-label={`Abrir a visão da marca ${brandLabel(h.brand)} a partir deste produto`}
                className="self-start inline-flex items-center min-h-11 text-sm font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
              >
                Abrir marca deste produto →
              </Link>
            </div>
          );
        })()}

        {/* ---- V3-1B: faixa. As duas explicações são DIFERENTES, e cada uma
             diz o que NÃO é, porque é justamente o que se confunde. ---- */}
        {dialog?.kind === "band" && oppMap && (() => {
          const b = oppMap.bands.find((x) => x.key === dialog.key);
          const meta = BAND_META[dialog.key];
          return (
            <div className="flex flex-col gap-4">
              <DrilldownContextLine leading={`${scope} · fotografia ML`} periodLabel="sem janela temporal" refreshedAt={mlRefreshedAt} />
              <p className="text-sm text-slate-700">{meta.explicacao}</p>
              <DrilldownMetricPair
                label="Produtos na faixa"
                value={fmtNumber(b?.count ?? 0)}
                referenceLabel="GMV da faixa"
                referenceValue={fmtBrl(b?.gmv ?? 0)}
              />
              <DrilldownMetricPair
                label="Ad spend da faixa"
                value={fmtBrl(b?.ad_spend ?? 0)}
                referenceLabel="Universo classificado"
                referenceValue={fmtNumber(oppMap.total_count)}
              />
              <DataQualityNote note={`${meta.naoConfundir} Faixa não produz destaque plotado: ela fica fora dos quadrantes.`} />
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

        {dialog?.kind === "priority" && (
          <div className="flex flex-col gap-4">
            <DrilldownContextLine leading={`${scope} · fotografia ML`} periodLabel="sem janela temporal" refreshedAt={null} />
            <p className="text-sm text-slate-700">
              {dialog.priority.received} registro{dialog.priority.received === 1 ? "" : "s"} nesta lista porque são{" "}
              {KIND_RULES[dialog.priority.kind]}.
            </p>
            <DrilldownMetricPair
              label={dialog.priority.amountLabel}
              value={fmtBrl(dialog.priority.amount)}
              referenceLabel="Registros exibidos"
              referenceValue={String(dialog.priority.received)}
            />
            <div>
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
                Maiores contribuintes (até 5)
              </p>
              <ul className="flex flex-col gap-1 list-none p-0 m-0">
                {dialog.priority.contributors.map((c, i) => (
                  <EvidenceRow
                    key={`${c.brand}-${i}`}
                    label={`${brandLabel(c.brand)} · ${c.title && c.title.length > 30 ? `${c.title.slice(0, 30)}…` : c.title}`}
                    value={dialog.priority.kind === "parar"
                      ? (c.ad_spend == null ? "sem dado" : fmtBrl(c.ad_spend))
                      : (c.gmv == null ? "sem dado" : fmtBrl(c.gmv))}
                    tone={(dialog.priority.kind === "parar" ? c.ad_spend : c.gmv) == null ? "muted" : "value"}
                  />
                ))}
              </ul>
            </div>
            <DataQualityNote
              note={`${dialog.priority.limitation}. A lista é capada em até ${priorityLimit(dialog.priority.kind)} no backend, então este número não é o total do portfólio.`}
            />
            <DrilldownCta href={buildLensHref("/inteligencia", dialog.priority.kind, searchParams)}>
              Ver evidências na lente {LENS_LABELS[dialog.priority.kind]} →
            </DrilldownCta>
          </div>
        )}

        {dialog?.kind === "bucket" && (
          <div className="flex flex-col gap-4">
            <DrilldownContextLine leading={`${brandLabel(dialog.brand)} · fotografia ML`} periodLabel="sem janela temporal" refreshedAt={null} />
            <p className="text-sm text-slate-700">
              {BUCKET_LABEL[dialog.share.bucket]} concentra{" "}
              {dialog.share.sharePct == null ? "uma parcela não calculável" : pctBr(dialog.share.sharePct)}{" "}
              do GMV de {brandLabel(dialog.brand)}.
            </p>
            <DrilldownMetricPair
              label="GMV do bucket"
              value={fmtBrl(dialog.share.gmv)}
              referenceLabel="GMV da marca"
              referenceValue={fmtBrl(dialog.totalGmv)}
            />
            <ul className="flex flex-col gap-1 list-none p-0 m-0">
              <EvidenceRow label="Produtos no bucket" value={fmtNumber(dialog.share.n_products)} />
              <EvidenceRow label="Ad spend no bucket" value={fmtBrl(dialog.share.ad_spend)} />
              <EvidenceRow
                label="Participação no GMV da marca"
                value={dialog.share.sharePct == null ? "sem dado" : pctBr(dialog.share.sharePct)}
                tone={dialog.share.sharePct == null ? "muted" : "value"}
              />
            </ul>
            <DataQualityNote note="Este payload traz apenas os agregados do bucket — ele não contém a lista de produtos. Use o destino abaixo, onde a consulta por bucket existe." />
            <DrilldownCta
              href={buildParetoProdutosHref(dialog.brand, dialog.share.bucket)}
              ariaLabel={`Ver produtos do bucket ${dialog.share.bucket.charAt(0)} de ${brandLabel(dialog.brand)} em Produtos`}
            >
              Ver produtos deste bucket →
            </DrilldownCta>
          </div>
        )}

        {dialog?.kind === "evidence" && (
          <div className="flex flex-col gap-4">
            <DrilldownContextLine
              leading={`${KIND_LABELS[dialog.item.kind]} · fotografia ML`}
              periodLabel="sem janela temporal"
              refreshedAt={null}
            />
            <p className="text-sm text-slate-700">
              Este produto está na lista porque são {KIND_RULES[dialog.item.kind]}.
            </p>
            <DrilldownMetricPair
              label={dialog.item.kind === "parar" ? "Ad spend" : "GMV"}
              value={dialog.item.kind === "parar"
                ? (dialog.item.ad_spend == null ? "sem dado" : fmtBrl(dialog.item.ad_spend))
                : (dialog.item.gmv == null ? "sem dado" : fmtBrl(dialog.item.gmv))}
              referenceLabel={dialog.item.kind === "escalar" ? "ROAS" : "Velocidade"}
              referenceValue={dialog.item.kind === "escalar"
                ? (dialog.item.ad_roas == null ? "sem dado" : roasBr(dialog.item.ad_roas))
                : (dialog.item.revenue_velocity ?? "sem dado")}
            />
            <ul className="flex flex-col gap-1 list-none p-0 m-0">
              <EvidenceRow label="GMV" value={dialog.item.gmv == null ? "sem dado" : fmtBrl(dialog.item.gmv)} tone={dialog.item.gmv == null ? "muted" : "value"} />
              <EvidenceRow label="Ad spend" value={dialog.item.ad_spend == null ? "sem dado" : fmtBrl(dialog.item.ad_spend)} tone={dialog.item.ad_spend == null ? "muted" : "value"} />
              <EvidenceRow label="ROAS" value={dialog.item.ad_roas == null ? "sem dado" : roasBr(dialog.item.ad_roas)} tone={dialog.item.ad_roas == null ? "muted" : "value"} />
              <EvidenceRow label="ACOS" value={dialog.item.ad_acos_pct == null ? "sem dado" : pctBr(dialog.item.ad_acos_pct)} tone={dialog.item.ad_acos_pct == null ? "muted" : "value"} />
              <EvidenceRow label="Dias com ads" value={dialog.item.days_advertised == null ? "sem dado" : fmtNumber(dialog.item.days_advertised)} tone={dialog.item.days_advertised == null ? "muted" : "value"} />
              <EvidenceRow label="Bucket Pareto" value={dialog.item.pareto_bucket ?? "sem dado"} tone={dialog.item.pareto_bucket == null ? "muted" : "value"} />
            </ul>
            <DataQualityNote note="Fotografia ML sem janela temporal, e sem CMV — não há margem real por produto. O detalhe usa a linha já carregada; ele não é compartilhável por identificador de produto nesta fase." />
            <DrilldownCta
              href={`/brand/${dialog.item.brand}?brands=${dialog.item.brand}`}
              ariaLabel={`Abrir a visão da marca ${brandLabel(dialog.item.brand)}`}
            >
              Abrir marca {brandLabel(dialog.item.brand)} →
            </DrilldownCta>
          </div>
        )}
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
