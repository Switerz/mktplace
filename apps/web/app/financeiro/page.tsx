"use client";

/**
 * Financeiro — UX-TORRE-B2B-1.
 *
 * Narrativa da tela, de cima para baixo:
 *   1. Por canal: receita (GMV) e cada componente de custo que a fonte do
 *      canal ENTREGA, em R$ e % do GMV — ou a declaracao de que nao entrega.
 *   2. O que ainda esta FORA da conta (margem nao fechada), nomeado.
 *   3. Taxa do marketplace por marca x canal, em grafico.
 *   4. As tres tabelas por marca, densas, com ordenacao.
 *
 * Nenhuma regra de negocio mudou: mesma requisicao, mesma guarda de frescor,
 * mesmas faixas de cor (ROAS, ACOS, taxas). A unica informacao NOVA na tela
 * e' a tarifa do Mercado Livre (`ml_fees` / `ml_avg_fee_pct`), campo que a API
 * ja' entrega desde o MARGEM-REAL-2 e que a tela ainda declarava ausente.
 * Nada aqui se chama "margem": componentes conhecidos nao fecham margem.
 */
import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowUpRight, BarChart3, CircleSlash, Megaphone, Percent, Receipt, Wallet } from "lucide-react";
import {
  fetchFinanceiro,
  type FinanceiroKpis,
  type FinanceiroBrandRow,
} from "@/lib/api-client";
import { isMarketplaceSelected } from "@/lib/marketplace-filter";
import { useGlobalFilters } from "@/hooks/useGlobalFilters";
import { mergeFilteredHref } from "@/lib/filters/nav-links";
import GlobalFilterControls from "@/components/layout/GlobalFilterControls";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import Freshness, { PeriodChip } from "@/components/ui/Freshness";
import Note from "@/components/ui/Note";
import Panel from "@/components/ui/Panel";
import Disclosure from "@/components/ui/Disclosure";
import StateBlock, { RetryButton } from "@/components/ui/StateBlock";
import ChannelCostPanel from "@/components/financeiro/ChannelCostPanel";
import dynamic from "next/dynamic";
import type { LinhaTaxa } from "@/components/financeiro/FeeByBrandChart";

import { fmtBrl } from "@/lib/formatters";
import { fmtPeriodo, mockLimitationNote } from "@/lib/filters/format";
import { detectPreset } from "@/lib/filters/presets";
import { useSortableTable } from "@/lib/use-sortable-table";
import SortHeader from "@/components/ui/SortHeader";
import TableScrollHint from "@/components/TableScrollHint";
import { buildFinanceiroRequestKey } from "@/lib/financeiro-request-key";
import { computeRequestStatus } from "@/lib/request-freshness";
import type { CanalDoGrafico } from "@/lib/chart-theme";

// Recharts so' chega quando o grafico entra na tela: a primeira carga do
// Financeiro fica com KPIs e tabelas, sem os ~110 kB da biblioteca.
const FeeByBrandChart = dynamic(() => import("@/components/financeiro/FeeByBrandChart"), {
  ssr: false,
  loading: () => <div className="h-[220px] animate-pulse rounded-lg bg-raised" aria-hidden="true" />,
});

function fmtPct(v: number | null, decimals = 1): string {
  if (v == null) return "—";
  return v.toFixed(decimals) + "%";
}

function fmtRoas(v: number | null): string {
  if (v == null) return "—";
  return v.toFixed(1) + "x";
}

function roasColor(v: number | null): string {
  if (v == null) return "text-ink-faint";
  if (v >= 10) return "text-ok-ink";
  if (v >= 5) return "text-warn-ink";
  return "text-crit-ink";
}

function acosColor(v: number | null): string {
  if (v == null) return "text-ink-faint";
  if (v <= 15) return "text-ok-ink";
  if (v <= 25) return "text-warn-ink";
  return "text-crit-ink";
}

function totalCostColor(v: number | null): string {
  if (v == null) return "text-ink-faint";
  if (v <= 18) return "text-ok-ink";
  if (v <= 25) return "text-warn-ink";
  return "text-crit-ink";
}

function feePctColor(v: number | null): string {
  if (v == null) return "text-ink-faint";
  if (v < 20) return "text-ink-muted";
  if (v < 30) return "text-warn-ink";
  return "text-crit-ink";
}

function CostBar({ adPct, freteP }: { adPct: number | null; freteP: number | null }) {
  if (adPct == null && freteP == null) return null;
  const ad = Math.max(0, adPct ?? 0);
  const frete = Math.max(0, freteP ?? 0);
  const total = Math.min(100, ad + frete);
  const rest = Math.max(0, 100 - total);
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full overflow-hidden bg-raised flex">
        {/* Composicao, nao estado: cor de serie, nunca ok/warn/crit. */}
        <div className="h-full bg-chart-2" style={{ width: `${ad}%` }} />
        <div className="h-full bg-chart-4" style={{ width: `${frete}%` }} />
        <div className="h-full bg-raised" style={{ width: `${rest}%` }} />
      </div>
      <span className={`text-xs tabular-nums w-11 text-right shrink-0 font-semibold ${totalCostColor(ad + frete)}`}>
        {(ad + frete).toFixed(1)}%
      </span>
    </div>
  );
}

/** Legenda de faixa: ponto + rotulo, numa linha. Mesmos limiares de sempre. */
function Faixa({ ponto, tinta, children }: { ponto: string; tinta: string; children: React.ReactNode }) {
  return (
    <span className={`flex items-center gap-1.5 text-xs ${tinta}`}>
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${ponto}`} />
      {children}
    </span>
  );
}

/** Classes das celulas: densidade do benchmark com o piso de 13px de dado. */
const TD = "px-4 py-2.5 text-right tabular-nums";
const TD_MARCA = "px-4 py-2.5 font-semibold text-ink whitespace-nowrap";
const TR = "hover:bg-raised/70 transition-colors";
const LINK_MARCA =
  "hover:text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded";

function FinanceiroPageInner() {
  const [filters, setFilters] = useGlobalFilters({ defaultPreset: "mes_anterior" });
  const searchParams = useSearchParams();
  const filter = filters.channels; // alias — preserva as referencias existentes abaixo
  const [kpis, setKpis] = useState<FinanceiroKpis | null>(null);
  const [brands, setBrands] = useState<FinanceiroBrandRow[]>([]);
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  // Chave da ultima requisicao resolvida com sucesso — comparada com a
  // chave atual para decidir se o estado em memoria reflete de fato os
  // filtros exibidos agora.
  const [resolvedKey, setResolvedKey] = useState<string | null>(null);

  const requestKey = useMemo(
    () => buildFinanceiroRequestKey({ channels: filters.channels, brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, compare: filters.compare, retryKey }),
    [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey],
  );

  useEffect(() => {
    // Ignora a resposta se os filtros mudarem antes dela chegar.
    let ignore = false;
    setLoading(true);
    setError(null);
    const key = buildFinanceiroRequestKey({ channels: filters.channels, brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, compare: filters.compare, retryKey });
    const opts = { brands: filters.brands, dateFrom: filters.dateFrom, dateTo: filters.dateTo, compare: filters.compare };
    fetchFinanceiro(filters.channels, undefined, opts)
      .then((result) => {
        if (ignore) return;
        setKpis(result.kpis);
        setBrands(result.brands);
        setIsLive(result.live);
        setRefreshedAt(result.meta.refreshedAt);
        setResolvedKey(key);
        setLoading(false);
      })
      .catch(() => {
        if (ignore) return;
        setError("Falha ao carregar dados financeiros.");
        // A chave precisa ser marcada como resolvida MESMO na falha — senao
        // `computeRequestStatus` nunca sai de "loading" (resolvedKey nunca
        // bate com requestKey) e a falha da requisicao atual nunca vira
        // "error" de fato (fica presa em loading para sempre).
        setResolvedKey(key);
        setLoading(false);
      });
    return () => { ignore = true; };
  }, [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, filters.compare, retryKey]);

  // FINDING 2 (rodada de correcao) — loading/error/fresh SEPARADOS: depois
  // de um erro definitivo, `dataIsFresh` fica `false` para sempre, mas
  // `isLoadingState` tambem precisa ficar `false` (senao skeleton/opacidade/
  // aria-busy continuariam ligados como se ainda estivesse buscando).
  const requestStatus = computeRequestStatus({ loading, error: error != null, resolvedKey, requestKey });
  const dataIsFresh = requestStatus.fresh;
  const isLoadingState = requestStatus.loading;
  const isErrorState = requestStatus.error;

  // Versoes protegidas do estado bruto — nenhum calculo/card/tabela/link
  // abaixo deve ler kpis/brands/isLive/refreshedAt diretamente.
  const displayKpis = dataIsFresh ? kpis : null;
  const displayBrands = dataIsFresh ? brands : [];
  const displayIsLive = dataIsFresh ? isLive : false;
  const displayRefreshedAt = dataIsFresh ? refreshedAt : null;

  const buildHref = useMemo(() => (href: string) => mergeFilteredHref(href, searchParams), [searchParams]);

  const periodLabel = fmtPeriodo(filters.dateFrom, filters.dateTo);

  const showTiktok = isMarketplaceSelected(filter, "tiktok");
  const showMl = isMarketplaceSelected(filter, "ml");
  const showShopee = isMarketplaceSelected(filter, "shopee");

  const tkBrands = displayBrands.filter((b) => b.tiktok_gmv != null);
  const mlBrands = displayBrands.filter((b) => b.ml_ad_spend != null);
  const shBrands = displayBrands.filter((b) => b.shopee_gmv != null);

  const tkColumnTypes = useMemo(() => ({
    brand: "text" as const, gmv: "numeric" as const, fees: "numeric" as const,
    fee_pct: "numeric" as const, settlement_pct: "numeric" as const, settlement: "numeric" as const,
  }), []);
  const tkGetValue = (row: FinanceiroBrandRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "gmv": return row.tiktok_gmv;
      case "fees": return row.tiktok_fees;
      case "fee_pct": return row.tiktok_avg_fee_pct;
      case "settlement_pct": return row.tiktok_avg_settlement_pct;
      case "settlement": return row.tiktok_settlement;
      default: return null;
    }
  };
  const tkSort = useSortableTable(tkBrands, tkGetValue, tkColumnTypes);

  const mlColumnTypes = useMemo(() => ({
    brand: "text" as const, gmv: "numeric" as const, fees: "numeric" as const, fee_pct: "numeric" as const,
    ad_spend: "numeric" as const, ad_revenue: "numeric" as const, roas: "numeric" as const, acos: "numeric" as const,
    frete: "numeric" as const, cost_pct: "numeric" as const,
  }), []);
  const mlGetValue = (row: FinanceiroBrandRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "gmv": return row.ml_gmv;
      // UX-TORRE-B2B-1: tarifa ML, campo ja' entregue pela API (MARGEM-REAL-2).
      case "fees": return row.ml_fees ?? null;
      case "fee_pct": return row.ml_avg_fee_pct ?? null;
      case "ad_spend": return row.ml_ad_spend;
      case "ad_revenue": return row.ml_ad_revenue;
      case "roas": return row.ml_roas;
      case "acos": return row.ml_acos_pct;
      case "frete": return row.ml_seller_shipping_cost;
      case "cost_pct": return (row.ml_ad_spend != null && row.ml_gmv != null && row.ml_gmv > 0)
        ? (row.ml_ad_spend / row.ml_gmv) * 100 + (row.ml_shipping_pct_of_gmv ?? 0)
        : null;
      default: return null;
    }
  };
  const mlSort = useSortableTable(mlBrands, mlGetValue, mlColumnTypes);

  const shColumnTypes = useMemo(() => ({
    brand: "text" as const, gmv: "numeric" as const, fees: "numeric" as const,
    fee_pct: "numeric" as const, settlement: "numeric" as const, ad_spend: "numeric" as const,
    roas: "numeric" as const, frete: "numeric" as const,
  }), []);
  const shGetValue = (row: FinanceiroBrandRow, column: string): string | number | null => {
    switch (column) {
      case "brand": return row.label;
      case "gmv": return row.shopee_gmv ?? null;
      case "fees": return row.shopee_fees ?? null;
      case "fee_pct": return row.shopee_avg_fee_pct ?? null;
      case "settlement": return row.shopee_settlement ?? null;
      case "ad_spend": return row.shopee_ad_spend ?? null;
      case "roas": return row.shopee_roas ?? null;
      case "frete": return row.shopee_shipping_cost ?? null;
      default: return null;
    }
  };
  const shSort = useSortableTable(shBrands, shGetValue, shColumnTypes);

  // Grafico de taxa por marca: so' os canais selecionados, so' valores que a
  // API entrega por linha. Marca sem nenhum valor nos canais visiveis sai.
  const canaisDoGrafico: CanalDoGrafico[] = [
    ...(showTiktok ? (["tiktok"] as const) : []),
    ...(showMl ? (["ml"] as const) : []),
    ...(showShopee ? (["shopee"] as const) : []),
  ];
  const linhasTaxa: LinhaTaxa[] = displayBrands
    .map((b) => ({
      marca: b.label,
      tiktok: showTiktok ? b.tiktok_avg_fee_pct : null,
      ml: showMl ? b.ml_avg_fee_pct ?? null : null,
      shopee: showShopee ? b.shopee_avg_fee_pct ?? null : null,
    }))
    .filter((l) => l.tiktok != null || l.ml != null || l.shopee != null);

  const k = displayKpis;
  // Marcas com venda no ML no recorte mas sem tarifa carregada.
  const mlSemTarifa = displayBrands.filter((b) => b.ml_gmv != null && b.ml_gmv > 0 && b.ml_fees == null).length;
  const vazio = dataIsFresh && displayBrands.length === 0;

  return (
    <PageContainer>
      <PageHeader
        title="Financeiro"
        subtitle="Receita e custos conhecidos por canal; a margem ainda não fecha."
        icon={Wallet}
        meta={
          <>
            <PeriodChip label={periodLabel} />
            {/* Selo so' com dado fresco — nunca o estado da requisicao ANTERIOR
                durante a transicao de filtro. */}
            <Freshness
              live={dataIsFresh ? displayIsLive : null}
              refreshedAt={displayRefreshedAt}
              loading={isLoadingState}
            />
          </>
        }
        filters={<GlobalFilterControls filters={filters} onChange={setFilters} hideCompare loading={isLoadingState} />}
      />

      {dataIsFresh && (() => {
        const isCustomPeriod = detectPreset(filters.dateFrom, filters.dateTo) !== "mes_anterior";
        const note = mockLimitationNote(displayIsLive, filters.brands, isCustomPeriod);
        return note && <Note tom="atencao">{note}</Note>;
      })()}

      <span className="sr-only" aria-live="polite" aria-atomic="true">
        {isLoadingState ? "Carregando dados financeiros..." : isErrorState ? "Falha ao carregar." : "Dados financeiros carregados."}
      </span>

      {isErrorState ? (
        // FINDING 2: erro definitivo mostra so cabecalho/filtros e o estado de
        // erro — nunca skeleton, nunca dado antigo, nunca `aria-busy=true`.
        <StateBlock
          tipo="erro"
          titulo="Não foi possível carregar os dados financeiros."
          descricao={error ?? undefined}
          acao={<RetryButton onClick={() => { setError(null); setRetryKey((n) => n + 1); }} />}
        />
      ) : vazio ? (
        <StateBlock
          tipo="vazio"
          titulo="Sem dados financeiros no período e filtros selecionados."
          descricao="Tente ampliar o intervalo de datas ou revisar canal/marca."
        />
      ) : (
      <>
        {/* ── 1. Receita e componentes conhecidos, por canal ── */}
        <section aria-labelledby="fin-canais" className="flex flex-col gap-3">
          <h2 id="fin-canais" className="sr-only">Receita e componentes de custo por canal</h2>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
            {showTiktok && (
              <ChannelCostPanel
                titulo="TikTok Shop"
                ponto="bg-canal-tiktok"
                barra="bg-canal-tiktok"
                carregando={isLoadingState}
                receita={k?.tiktok_gmv != null ? fmtBrl(k.tiktok_gmv) : "—"}
                linhas={[
                  {
                    rotulo: "Taxas e encargos",
                    valor: k?.tiktok_fees != null ? fmtBrl(k.tiktok_fees) : null,
                    pct: k?.tiktok_avg_fee_pct ?? null,
                    pctTexto: k?.tiktok_avg_fee_pct != null ? fmtPct(k.tiktok_avg_fee_pct) : "—",
                  },
                  {
                    rotulo: "Repasse recebido",
                    valor: k?.tiktok_settlement != null ? fmtBrl(k.tiktok_settlement) : "—",
                    pct: k?.tiktok_avg_settlement_pct ?? null,
                    pctTexto: k?.tiktok_avg_settlement_pct != null ? `${fmtPct(k.tiktok_avg_settlement_pct)} do GMV` : null,
                    detalhe: "Competências podem divergir.",
                  },
                  { rotulo: "Ads (mídia)", ausente: "Sem fonte" },
                  { rotulo: "Frete do seller", ausente: "Sem fonte" },
                ]}
              />
            )}
            {showMl && (
              <ChannelCostPanel
                titulo="Mercado Livre"
                ponto="bg-canal-ml"
                barra="bg-canal-ml"
                carregando={isLoadingState}
                receita={k?.ml_gmv != null ? fmtBrl(k.ml_gmv) : "—"}
                linhas={[
                  k?.ml_fees != null
                    ? {
                        rotulo: "Tarifa ML",
                        valor: fmtBrl(k.ml_fees),
                        pct: k.ml_avg_fee_pct ?? null,
                        pctTexto: fmtPct(k.ml_avg_fee_pct ?? null),
                        // O backend soma marca sem tarifa como zero: com
                        // carga parcial o total parece completo e sai baixo.
                        detalhe:
                          mlSemTarifa > 0
                            ? `Parcial: ${mlSemTarifa} ${mlSemTarifa === 1 ? "marca sem tarifa" : "marcas sem tarifa"}. Só a tarifa; sem Ads e frete.`
                            : "Só a tarifa; sem Ads e frete.",
                      }
                    : { rotulo: "Tarifa ML", ausente: "Não carregada neste período" },
                  {
                    rotulo: "Ads (mídia)",
                    valor: k?.ml_ad_spend != null ? fmtBrl(k.ml_ad_spend) : "—",
                    // Sem barra de proposito: Ads/GMV do canal nao e' campo da
                    // API, e uma barra sem numero seria uma razao nova muda.
                    detalhe: (
                      <>
                        ROAS <span className={`font-semibold ${roasColor(k?.ml_roas ?? null)}`}>{fmtRoas(k?.ml_roas ?? null)}</span>
                        {k?.ml_acos_pct != null && <> · ACOS {fmtPct(k.ml_acos_pct)}</>}
                      </>
                    ),
                  },
                  {
                    rotulo: "Ads + frete / GMV",
                    pct: k?.ml_total_cost_pct ?? null,
                    pctTexto: k?.ml_total_cost_pct != null ? fmtPct(k.ml_total_cost_pct) : "—",
                    detalhe: "Não inclui a tarifa ML.",
                  },
                ]}
              />
            )}
            {showShopee && (
              <ChannelCostPanel
                titulo="Shopee"
                ponto="bg-canal-shopee"
                barra="bg-canal-shopee"
                carregando={isLoadingState}
                receita={k?.shopee_gmv != null ? fmtBrl(k.shopee_gmv) : "—"}
                linhas={[
                  {
                    rotulo: "Taxas e encargos",
                    valor: k?.shopee_fees != null ? fmtBrl(k.shopee_fees) : null,
                    pct: k?.shopee_avg_fee_pct ?? null,
                    pctTexto: k?.shopee_avg_fee_pct != null ? fmtPct(k.shopee_avg_fee_pct) : "—",
                  },
                  {
                    rotulo: "Ads (mídia)",
                    valor: k?.shopee_ad_spend != null ? fmtBrl(k.shopee_ad_spend) : "—",
                    detalhe: (
                      <>
                        ROAS{" "}
                        <span className={`font-semibold ${roasColor(k?.shopee_roas ?? null)}`}>
                          {k?.shopee_roas != null ? fmtRoas(k.shopee_roas) : "—"}
                        </span>
                      </>
                    ),
                  },
                  { rotulo: "Frete do seller", ausente: "Só por marca" },
                ]}
              />
            )}
          </div>

          {/* ── 2. Margem nao fechada: o que esta FORA desta tela ── */}
          <Note tom="neutro">
            <span className="flex items-start gap-2">
              <CircleSlash aria-hidden="true" className="mt-px h-3.5 w-3.5 shrink-0" />
              <span>
                <strong className="font-semibold text-ink">Margem ainda não fechada.</strong>{" "}
                Fora da conta: CMV, impostos, devoluções
                {showTiktok && (
                  <>
                    , frete e afiliados do TikTok (
                    <Link href={buildHref("/canais#afiliados")} className="font-semibold text-accent underline-offset-2 hover:underline">
                      ver em Canais
                    </Link>
                    )
                  </>
                )}
                .
              </span>
            </span>
          </Note>
        </section>

        {/* ── 3. Taxa do marketplace por marca ── */}
        {canaisDoGrafico.length > 0 && (dataIsFresh ? linhasTaxa.length > 0 : true) && (
          <Panel
            titulo="Taxa do marketplace sobre o GMV, por marca"
            descricao="Cada canal mede um conjunto próprio de taxas — barras lado a lado, nunca somadas."
            icone={BarChart3}
          >
            {dataIsFresh ? (
              <FeeByBrandChart linhas={linhasTaxa} canais={canaisDoGrafico} />
            ) : (
              <div className="h-[220px] animate-pulse rounded-lg bg-raised" aria-hidden="true" />
            )}
          </Panel>
        )}

        {/* Navegacao interna compacta (Task 4) */}
        <nav aria-label="Navegação interna da página" className="flex flex-wrap items-center gap-1">
          <span className="mr-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">Tabelas por marca</span>
          {showTiktok && <a href="#tiktok" className="inline-flex min-h-11 items-center gap-1 px-2.5 rounded-lg text-xs font-semibold text-accent hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">TikTok Shop</a>}
          {showMl && <a href="#mercado-livre" className="inline-flex min-h-11 items-center gap-1 px-2.5 rounded-lg text-xs font-semibold text-accent hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">Mercado Livre</a>}
          {showShopee && <a href="#shopee" className="inline-flex min-h-11 items-center gap-1 px-2.5 rounded-lg text-xs font-semibold text-accent hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">Shopee</a>}
        </nav>

        {/* ── 4a. Tabela: Repasses TikTok ── */}
        {showTiktok && (
          <Panel
            id="tiktok"
            titulo="Repasses TikTok"
            descricao="GMV bruto, taxas e encargos e repasse recebido por marca"
            icone={Receipt}
            semPadding
            rodape={
              <div className="flex flex-col gap-1">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  <span className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Taxas e encargos %</span>
                  <Faixa ponto="bg-ink-faint" tinta="text-ink-muted">abaixo de 20%</Faixa>
                  <Faixa ponto="bg-warn" tinta="text-warn-ink">20–30%</Faixa>
                  <Faixa ponto="bg-crit" tinta="text-crit-ink">acima de 30%</Faixa>
                </div>
                <Disclosure className="-ml-2">
                  Taxas e encargos / GMV · Repasse % = Repasse Recebido / GMV · repasses e GMV podem ter competencias diferentes (o repasse pode incluir pedidos de outros meses)
                </Disclosure>
              </div>
            }
          >
            <TableScrollHint>
              <table className="w-full text-data" aria-label="Repasses TikTok por marca">
                <thead>
                  <tr className="bg-raised text-left">
                    <SortHeader rotulo="Marca" coluna="brand" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} alinhamento="left" />
                    <SortHeader rotulo="GMV" coluna="gmv" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                    <SortHeader rotulo="Taxas (R$)" coluna="fees" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                    <SortHeader rotulo="Taxas e encargos %" coluna="fee_pct" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                    <SortHeader rotulo="Repasse %" coluna="settlement_pct" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                    <SortHeader rotulo="Repasse Recebido" coluna="settlement" sort={tkSort.sort} aoOrdenar={tkSort.toggleSort} />
                  </tr>
                </thead>
                <tbody className={`divide-y divide-line transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                  {tkBrands.length === 0 && dataIsFresh && (
                    <tr>
                      <td colSpan={6} className="px-6 py-8 text-center text-ink-faint text-sm">
                        Sem dados para o periodo selecionado.
                      </td>
                    </tr>
                  )}
                  {tkSort.sortedRows.map((b) => (
                    <tr key={b.brand} className={TR}>
                      <td className={TD_MARCA}>
                        <Link href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=tiktok`)} className={LINK_MARCA}>
                          {b.label}
                        </Link>
                      </td>
                      <td className={`${TD} text-ink-muted font-medium`}>{fmtBrl(b.tiktok_gmv!)}</td>
                      <td className={`${TD} text-ink-muted`}>{fmtBrl(b.tiktok_fees!)}</td>
                      <td className={`${TD} font-semibold ${feePctColor(b.tiktok_avg_fee_pct)}`}>
                        {fmtPct(b.tiktok_avg_fee_pct)}
                      </td>
                      <td className={`${TD} text-ink-muted`}>{fmtPct(b.tiktok_avg_settlement_pct)}</td>
                      <td className={`${TD} text-ink font-bold`}>{fmtBrl(b.tiktok_settlement!)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScrollHint>
          </Panel>
        )}

        {/* ── 4b. Tabela: Tarifa, publicidade e custos ML ── */}
        {showMl && (
          <Panel
            id="mercado-livre"
            titulo="Tarifa, publicidade e custos ML"
            descricao="Tarifa do marketplace, Ad Spend, receita atribuída, frete e Ads + frete como % do GMV"
            icone={Megaphone}
            semPadding
            rodape={
              <div className="flex flex-col gap-1">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  <span className="text-xs font-semibold uppercase tracking-wide text-ink-faint">ROAS</span>
                  <Faixa ponto="bg-ok" tinta="text-ok-ink">acima de 10x</Faixa>
                  <Faixa ponto="bg-warn" tinta="text-warn-ink">5x–10x</Faixa>
                  <Faixa ponto="bg-crit" tinta="text-crit-ink">abaixo de 5x</Faixa>
                  <span aria-hidden="true" className="h-3 w-px bg-line-strong" />
                  <span className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Ads + Frete / GMV</span>
                  <Faixa ponto="bg-chart-2" tinta="text-ink-muted">Ads</Faixa>
                  <Faixa ponto="bg-chart-4" tinta="text-ink-muted">Frete</Faixa>
                </div>
                <Disclosure className="-ml-2">
                  Ads + Frete / GMV · nao inclui a tarifa do Mercado Livre, que tem coluna propria · Tarifa ML = so' a tarifa/comissao do marketplace (pedidos pagos), sem Ads, frete, imposto, devolucao nem afiliado — nao e' margem · Receita Ads = GMV atribuido a anuncios
                </Disclosure>
              </div>
            }
          >
            <TableScrollHint>
              <table className="w-full text-data" aria-label="Tarifa, publicidade e custos ML por marca">
                <thead>
                  <tr className="bg-raised text-left">
                    <SortHeader rotulo="Marca" coluna="brand" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} alinhamento="left" />
                    <SortHeader rotulo="GMV ML" coluna="gmv" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="Tarifa ML" coluna="fees" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="Tarifa %" coluna="fee_pct" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="Ad Spend" coluna="ad_spend" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="Receita Ads" coluna="ad_revenue" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="ROAS" coluna="roas" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="ACOS" coluna="acos" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="Frete" coluna="frete" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} />
                    <SortHeader rotulo="Ads + Frete / GMV" coluna="cost_pct" sort={mlSort.sort} aoOrdenar={mlSort.toggleSort} alinhamento="left" />
                  </tr>
                </thead>
                <tbody className={`divide-y divide-line transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                  {mlBrands.length === 0 && dataIsFresh && (
                    <tr>
                      <td colSpan={10} className="px-6 py-8 text-center text-ink-faint text-sm">
                        Sem dados de anuncios ML para o periodo selecionado.
                      </td>
                    </tr>
                  )}
                  {mlSort.sortedRows.map((b) => {
                    const adPct = b.ml_ad_spend != null && b.ml_gmv != null && b.ml_gmv > 0
                      ? b.ml_ad_spend / b.ml_gmv * 100
                      : null;
                    return (
                      <tr key={b.brand} className={TR}>
                        <td className={TD_MARCA}>
                          <Link href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=ml`)} className={LINK_MARCA}>
                            {b.label}
                          </Link>
                        </td>
                        <td className={`${TD} text-ink-muted font-medium`}>{fmtBrl(b.ml_gmv!)}</td>
                        <td className={`${TD} text-ink-muted`}>{b.ml_fees != null ? fmtBrl(b.ml_fees) : "—"}</td>
                        <td className={`${TD} text-ink-muted`}>{fmtPct(b.ml_avg_fee_pct ?? null)}</td>
                        <td className={`${TD} text-ink-muted`}>{fmtBrl(b.ml_ad_spend!)}</td>
                        <td className={`${TD} text-ink-muted font-medium`}>
                          {b.ml_ad_revenue != null ? fmtBrl(b.ml_ad_revenue) : "—"}
                        </td>
                        <td className={`${TD} font-semibold ${roasColor(b.ml_roas)}`}>{fmtRoas(b.ml_roas)}</td>
                        <td className={`${TD} font-semibold ${acosColor(b.ml_acos_pct)}`}>{fmtPct(b.ml_acos_pct)}</td>
                        <td className={`${TD} text-ink-muted`}>
                          {b.ml_seller_shipping_cost != null ? fmtBrl(b.ml_seller_shipping_cost) : "—"}
                        </td>
                        <td className="px-4 py-2.5 min-w-[160px]">
                          <CostBar adPct={adPct} freteP={b.ml_shipping_pct_of_gmv} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableScrollHint>
          </Panel>
        )}

        {/* ── 4c. Tabela: Taxas e Custos Shopee ── */}
        {showShopee && (
          <Panel
            id="shopee"
            titulo="Taxas e Custos Shopee"
            descricao="GMV, taxas e encargos, total global dos pedidos, anúncios e frete por marca"
            icone={Percent}
            semPadding
            rodape={
              <div className="flex flex-col gap-1">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  <span className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Taxas e encargos %</span>
                  <Faixa ponto="bg-ink-faint" tinta="text-ink-muted">abaixo de 20%</Faixa>
                  <Faixa ponto="bg-warn" tinta="text-warn-ink">20–30%</Faixa>
                  <Faixa ponto="bg-crit" tinta="text-crit-ink">acima de 30%</Faixa>
                </div>
                <Disclosure className="-ml-2">
                  Taxas e encargos = comissao liquida + taxa de servico liquida, sobre o GMV · o indicador de liquidacao foi removido: o campo antigo (Total Global do pedido) nao representa repasse liquido
                </Disclosure>
              </div>
            }
          >
            <TableScrollHint>
              <table className="w-full text-data" aria-label="Taxas e custos Shopee por marca">
                <thead>
                  <tr className="bg-raised text-left">
                    <SortHeader rotulo="Marca" coluna="brand" sort={shSort.sort} aoOrdenar={shSort.toggleSort} alinhamento="left" />
                    <SortHeader rotulo="GMV" coluna="gmv" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                    <SortHeader rotulo="Taxas (R$)" coluna="fees" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                    <SortHeader rotulo="Taxas e encargos %" coluna="fee_pct" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                    <SortHeader rotulo="Total Global (pedidos)" coluna="settlement" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                    <SortHeader rotulo="Ad Spend" coluna="ad_spend" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                    <SortHeader rotulo="ROAS" coluna="roas" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                    <SortHeader rotulo="Frete" coluna="frete" sort={shSort.sort} aoOrdenar={shSort.toggleSort} />
                  </tr>
                </thead>
                <tbody className={`divide-y divide-line transition-opacity duration-200 ${isLoadingState ? "opacity-50" : ""}`}>
                  {shBrands.length === 0 && dataIsFresh && (
                    <tr>
                      <td colSpan={8} className="px-6 py-8 text-center text-ink-faint text-sm">
                        Sem dados Shopee para o periodo selecionado.
                      </td>
                    </tr>
                  )}
                  {shSort.sortedRows.map((b) => (
                    <tr key={b.brand} className={TR}>
                      <td className={TD_MARCA}>
                        <Link href={buildHref(`/brand/${b.brand}?brands=${b.brand}&channels=shopee`)} className={LINK_MARCA}>
                          {b.label}
                        </Link>
                      </td>
                      <td className={`${TD} text-ink-muted font-medium`}>{fmtBrl(b.shopee_gmv!)}</td>
                      <td className={`${TD} text-ink-muted`}>{b.shopee_fees != null ? fmtBrl(b.shopee_fees) : "—"}</td>
                      <td className={`${TD} font-semibold ${feePctColor(b.shopee_avg_fee_pct ?? null)}`}>
                        {fmtPct(b.shopee_avg_fee_pct ?? null)}
                      </td>
                      <td className={`${TD} text-ink font-bold`}>
                        {b.shopee_settlement != null ? fmtBrl(b.shopee_settlement) : "—"}
                      </td>
                      <td className={`${TD} text-ink-muted`}>{b.shopee_ad_spend != null ? fmtBrl(b.shopee_ad_spend) : "—"}</td>
                      <td className={`${TD} font-semibold ${roasColor(b.shopee_roas ?? null)}`}>
                        {fmtRoas(b.shopee_roas ?? null)}
                      </td>
                      <td className={`${TD} text-ink-muted`}>
                        {b.shopee_shipping_cost != null ? fmtBrl(b.shopee_shipping_cost) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScrollHint>
          </Panel>
        )}

        {dataIsFresh && (
          <p className="flex items-center gap-1.5 text-xs text-ink-faint">
            <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
            O nome da marca abre a visão da marca no canal, com os mesmos filtros.
          </p>
        )}
      </>
      )}
    </PageContainer>
  );
}

export default function FinanceiroPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-canvas" />}>
      <FinanceiroPageInner />
    </Suspense>
  );
}
