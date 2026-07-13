"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchProdutosML, fetchProdutosTikTok, fetchProdutosShopee,
  fetchProdutosMLSummary, fetchProdutosTikTokSummary, fetchProdutosShopeeSummary,
  type ProdutosMLResponse, type ProdutosTikTokResponse, type ProdutosShopeeListResponse,
  type ProdutosMLSummary, type ProdutosChannelSummary,
} from "@/lib/api-client";
import AppNav from "@/components/AppNav";
import PeriodSelector from "@/components/PeriodSelector";
import ProductMarketplaceTabs from "@/components/ProductMarketplaceTabs";
import ProductFilterBar, { ProductSelect } from "@/components/ProductFilterBar";
import ProductParetoSummary from "@/components/ProductParetoSummary";
import ProductCount from "@/components/ProductCount";
import MercadoLivreProductTable from "@/components/MercadoLivreProductTable";
import TikTokProductTable from "@/components/TikTokProductTable";
import ShopeeProductTable from "@/components/ShopeeProductTable";
import { useSortableTable, type SortColumnType } from "@/lib/use-sortable-table";
import {
  brandSurvivesTabChange, toggleBucketSelection, zeroGmvNote, avgPriceNote, marginUnavailableNote,
  lastNMonths, type ProdutosTab,
} from "@/lib/produtos-tab-transition";
import { initialChannelState, startFetch, resolveFetch, resolveFetchError, type ChannelState } from "@/lib/async-channel-state";

type Tab = ProdutosTab;
type ProductStatus = "" | "sells+advertised" | "sells_organic_only" | "ad_spend_no_sales" | "inactive";
type VelocityFilter = "" | "high" | "medium" | "low" | "zero";

const PAGE_SIZE = 25;

const TABS: { value: Tab; label: string }[] = [
  { value: "ml", label: "Mercado Livre" },
  { value: "tiktok", label: "TikTok Shop" },
  { value: "shopee", label: "Shopee" },
];

// Marcas validas por canal — usado para decidir se a marca selecionada
// sobrevive a troca de aba ("preserve a marca se ela existir no canal").
const ML_BRANDS = [
  { value: "barbours", label: "BARBOURS" },
  { value: "kokeshi", label: "KOKESHI" },
  { value: "lescent", label: "LESCENT" },
  { value: "rituaria", label: "RITUARIA" },
];
const TK_SH_BRANDS = [
  { value: "apice", label: "APICE" },
  { value: "barbours", label: "BARBOURS" },
  { value: "kokeshi", label: "KOKESHI" },
  { value: "lescent", label: "LESCENT" },
  { value: "rituaria", label: "RITUARIA" },
];

function fmtRefreshedAt(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return "";
  }
}

const PERIOD_MONTHS_BACK = 7;

export default function ProdutosPage() {
  const [tab, setTab] = useState<Tab>("ml");
  // Gerado a partir da data atual em vez de uma lista fixa hardcoded (Gate 2,
  // produtos_audit.md secao 10.4) — calculado uma vez por montagem da pagina.
  const monthOptions = useMemo(() => lastNMonths(PERIOD_MONTHS_BACK, new Date()), []);
  const [period, setPeriod] = useState(() => monthOptions[0].value);

  // Filtros padronizados (marca + bucket) — marca e compartilhada entre
  // abas (preservada quando existe no canal); bucket e sempre por aba,
  // porque cada canal calcula seu proprio ranking Pareto.
  const [brand, setBrand] = useState("");
  const [mlBucket, setMlBucket] = useState<string | null>(null);
  const [tkBucket, setTkBucket] = useState<string | null>(null);
  const [shBucket, setShBucket] = useState<string | null>(null);

  // Filtros especificos do ML (nao existem em TikTok/Shopee)
  const [mlSignal, setMlSignal] = useState("");
  const [mlStatus, setMlStatus] = useState<ProductStatus>("");
  const [mlVelocity, setMlVelocity] = useState<VelocityFilter>("");

  const [mlOffset, setMlOffset] = useState(0);
  const [tkOffset, setTkOffset] = useState(0);
  const [shOffset, setShOffset] = useState(0);

  // Estado assincrono por canal (dados + loading + "ao vivo") centralizado em
  // async-channel-state.ts: cada resolucao so e aplicada se ainda for a
  // requisicao mais recente daquele canal (guarda contra resposta obsoleta
  // sobrescrever uma mais nova — ex: troca rapida de marca/periodo). Uma
  // falha nunca deixa dados antigos exibidos como se pertencessem ao filtro
  // atual: volta para data=null (mesmo estado de "API offline").
  const [mlState, setMlState] = useState<ChannelState<ProdutosMLResponse>>(initialChannelState);
  const [tkState, setTkState] = useState<ChannelState<ProdutosTikTokResponse>>(initialChannelState);
  const [shState, setShState] = useState<ChannelState<ProdutosShopeeListResponse>>(initialChannelState);
  const mlReqId = useRef(0);
  const tkReqId = useRef(0);
  const shReqId = useRef(0);

  const [mlSummaryState, setMlSummaryState] = useState<ChannelState<ProdutosMLSummary>>(initialChannelState);
  const [tkSummaryState, setTkSummaryState] = useState<ChannelState<ProdutosChannelSummary>>(initialChannelState);
  const [shSummaryState, setShSummaryState] = useState<ChannelState<ProdutosChannelSummary>>(initialChannelState);
  const mlSummaryReqId = useRef(0);
  const tkSummaryReqId = useRef(0);
  const shSummaryReqId = useRef(0);

  // "Ao vivo" reflete exclusivamente o ultimo resultado do PROPRIO canal —
  // nunca fica true so porque outra aba teve sucesso antes.
  const isLive = tab === "ml" ? mlState.live : tab === "tiktok" ? tkState.live : shState.live;

  // Ordenacao e server-side (cada pagina ja vem ordenada pela API); usamos
  // apenas sort/toggleSort do hook para dirigir a query.
  const mlColumnTypes: Record<string, SortColumnType> = { title: "text" };
  const mlGetValue = (row: NonNullable<ProdutosMLResponse["items"]>[number], column: string) => (row as unknown as Record<string, string | number | null | undefined>)[column];
  const mlSort = useSortableTable(mlState.data?.items ?? [], mlGetValue, mlColumnTypes);

  const tkColumnTypes: Record<string, SortColumnType> = { product_name: "text" };
  const tkGetValue = (row: NonNullable<ProdutosTikTokResponse["items"]>[number], column: string) => (row as unknown as Record<string, string | number | null | undefined>)[column];
  const tkSort = useSortableTable(tkState.data?.items ?? [], tkGetValue, tkColumnTypes);

  const shColumnTypes: Record<string, SortColumnType> = { product_name: "text" };
  const shGetValue = (row: NonNullable<ProdutosShopeeListResponse["items"]>[number], column: string) => (row as unknown as Record<string, string | number | null | undefined>)[column];
  const shSort = useSortableTable(shState.data?.items ?? [], shGetValue, shColumnTypes);

  const loadML = useCallback(() => {
    const id = ++mlReqId.current;
    setMlState(startFetch);
    (async () => {
      let result: ProdutosMLResponse | null = null;
      let failed = false;
      try {
        result = await fetchProdutosML({
          brand: brand || undefined,
          pareto_bucket: mlBucket || undefined,
          action_signal: mlSignal || undefined,
          product_status: mlStatus || undefined,
          revenue_velocity: mlVelocity || undefined,
          limit: PAGE_SIZE,
          offset: mlOffset,
          sort_by: mlSort.sort.column ?? undefined,
          sort_dir: mlSort.sort.direction ?? undefined,
        });
      } catch {
        failed = true;
      } finally {
        const isCurrent = id === mlReqId.current;
        setMlState((s) => (failed ? resolveFetchError(s, isCurrent) : resolveFetch(s, isCurrent, result)));
      }
    })();
  }, [brand, mlBucket, mlSignal, mlStatus, mlVelocity, mlOffset, mlSort.sort.column, mlSort.sort.direction]);

  const loadTK = useCallback(() => {
    const id = ++tkReqId.current;
    setTkState(startFetch);
    (async () => {
      let result: ProdutosTikTokResponse | null = null;
      let failed = false;
      try {
        result = await fetchProdutosTikTok({
          brand: brand || undefined,
          period,
          pareto_bucket: tkBucket || undefined,
          limit: PAGE_SIZE,
          offset: tkOffset,
          sort_by: tkSort.sort.column ?? undefined,
          sort_dir: tkSort.sort.direction ?? undefined,
        });
      } catch {
        failed = true;
      } finally {
        const isCurrent = id === tkReqId.current;
        setTkState((s) => (failed ? resolveFetchError(s, isCurrent) : resolveFetch(s, isCurrent, result)));
      }
    })();
  }, [brand, period, tkBucket, tkOffset, tkSort.sort.column, tkSort.sort.direction]);

  const loadSH = useCallback(() => {
    const id = ++shReqId.current;
    setShState(startFetch);
    (async () => {
      let result: ProdutosShopeeListResponse | null = null;
      let failed = false;
      try {
        result = await fetchProdutosShopee({
          brand: brand || undefined,
          period,
          pareto_bucket: shBucket || undefined,
          limit: PAGE_SIZE,
          offset: shOffset,
          sort_by: shSort.sort.column ?? undefined,
          sort_dir: shSort.sort.direction ?? undefined,
        });
      } catch {
        failed = true;
      } finally {
        const isCurrent = id === shReqId.current;
        setShState((s) => (failed ? resolveFetchError(s, isCurrent) : resolveFetch(s, isCurrent, result)));
      }
    })();
  }, [brand, period, shBucket, shOffset, shSort.sort.column, shSort.sort.direction]);

  useEffect(() => { if (tab === "ml") loadML(); }, [tab, loadML]);
  useEffect(() => { if (tab === "tiktok") loadTK(); }, [tab, loadTK]);
  useEffect(() => { if (tab === "shopee") loadSH(); }, [tab, loadSH]);

  // Summary (cards A/B/C/D) — mesmos filtros da tabela, exceto o proprio bucket.
  useEffect(() => {
    if (tab !== "ml") return;
    const id = ++mlSummaryReqId.current;
    setMlSummaryState(startFetch);
    (async () => {
      let result: ProdutosMLSummary | null = null;
      let failed = false;
      try {
        result = await fetchProdutosMLSummary({
          brand: brand || undefined, action_signal: mlSignal || undefined,
          product_status: mlStatus || undefined, revenue_velocity: mlVelocity || undefined,
        });
      } catch {
        failed = true;
      } finally {
        const isCurrent = id === mlSummaryReqId.current;
        setMlSummaryState((s) => (failed ? resolveFetchError(s, isCurrent) : resolveFetch(s, isCurrent, result)));
      }
    })();
  }, [tab, brand, mlSignal, mlStatus, mlVelocity]);

  useEffect(() => {
    if (tab !== "tiktok") return;
    const id = ++tkSummaryReqId.current;
    setTkSummaryState(startFetch);
    (async () => {
      let result: ProdutosChannelSummary | null = null;
      let failed = false;
      try {
        result = await fetchProdutosTikTokSummary({ brand: brand || undefined, period });
      } catch {
        failed = true;
      } finally {
        const isCurrent = id === tkSummaryReqId.current;
        setTkSummaryState((s) => (failed ? resolveFetchError(s, isCurrent) : resolveFetch(s, isCurrent, result)));
      }
    })();
  }, [tab, brand, period]);

  useEffect(() => {
    if (tab !== "shopee") return;
    const id = ++shSummaryReqId.current;
    setShSummaryState(startFetch);
    (async () => {
      let result: ProdutosChannelSummary | null = null;
      let failed = false;
      try {
        result = await fetchProdutosShopeeSummary({ brand: brand || undefined, period });
      } catch {
        failed = true;
      } finally {
        const isCurrent = id === shSummaryReqId.current;
        setShSummaryState((s) => (failed ? resolveFetchError(s, isCurrent) : resolveFetch(s, isCurrent, result)));
      }
    })();
  }, [tab, brand, period]);

  // Reset offset quando filtros mudam (nunca usa a pagina visivel como base)
  useEffect(() => { setMlOffset(0); }, [brand, mlBucket, mlSignal, mlStatus, mlVelocity]);
  useEffect(() => { setTkOffset(0); }, [brand, tkBucket, period]);
  useEffect(() => { setShOffset(0); }, [brand, shBucket, period]);

  // Reset offset quando a ordenacao muda
  useEffect(() => { setMlOffset(0); }, [mlSort.sort.column, mlSort.sort.direction]);
  useEffect(() => { setTkOffset(0); }, [tkSort.sort.column, tkSort.sort.direction]);
  useEffect(() => { setShOffset(0); }, [shSort.sort.column, shSort.sort.direction]);

  // Reset de paginacao ao ENTRAR em cada aba (nao herda a pagina da ultima visita)
  useEffect(() => { if (tab === "ml") setMlOffset(0); }, [tab]);
  useEffect(() => { if (tab === "tiktok") setTkOffset(0); }, [tab]);
  useEffect(() => { if (tab === "shopee") setShOffset(0); }, [tab]);

  function handleTabChange(next: Tab) {
    setTab(next);
    // preserva a marca somente se ela existir no novo canal
    if (!brandSurvivesTabChange(brand, next)) {
      setBrand("");
    }
    // bucket e especifico de cada ranking (marketplace+periodo+marca) — nunca
    // atravessa a troca de aba, mesmo que o nome do bucket coincida (A_top50
    // do ML nao corresponde ao mesmo conjunto de produtos do A_top50 do TikTok)
    setMlBucket(null);
    setTkBucket(null);
    setShBucket(null);
  }

  const periodLabel = monthOptions.find((m) => m.value === period)?.label ?? period;

  return (
    <div className="min-h-screen bg-[#f8f7ff]">
      <header className="bg-white border-b border-violet-100 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-violet-600 flex items-center justify-center">
              <span className="text-white font-bold text-xs tracking-tight">TC</span>
            </div>
            <div>
              <h1 className="text-lg font-bold text-gray-900 leading-none">Torre de Controle</h1>
              <p className="text-xs text-slate-500">Gobeaute · Marketplaces</p>
            </div>
          </div>
          <span className={`text-xs rounded-lg px-3 py-1.5 font-medium ${isLive ? "text-emerald-700 bg-emerald-50 border border-emerald-200" : "text-amber-700 bg-amber-50 border border-amber-200"}`}>
            {isLive ? "Dados ao vivo · API conectada" : "Demonstração · API offline"}
          </span>
        </div>
      </header>

      <AppNav />

      <main className="max-w-7xl mx-auto px-6 py-8 flex flex-col gap-6">
        <ProductMarketplaceTabs tabs={TABS} active={tab} onChange={handleTabChange} />

        {/* ML view */}
        {tab === "ml" && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            <ProductFilterBar>
              <ProductSelect value={brand} onChange={(e) => setBrand(e.target.value)} aria-label="Marca">
                <option value="">Todas as marcas</option>
                {ML_BRANDS.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
              </ProductSelect>
              <ProductSelect value={mlStatus} onChange={(e) => setMlStatus(e.target.value as ProductStatus)} aria-label="Status">
                <option value="">Todos os status</option>
                <option value="sells+advertised">Vende + anunciado</option>
                <option value="sells_organic_only">Vende organico</option>
                <option value="ad_spend_no_sales">Gasta ads, sem venda</option>
                <option value="inactive">Inativo</option>
              </ProductSelect>
              <ProductSelect value={mlVelocity} onChange={(e) => setMlVelocity(e.target.value as VelocityFilter)} aria-label="Velocidade">
                <option value="">Toda velocidade</option>
                <option value="high">Alta velocidade</option>
                <option value="medium">Media velocidade</option>
                <option value="low">Baixa velocidade</option>
                <option value="zero">Sem vendas</option>
              </ProductSelect>
              <ProductSelect value={mlSignal} onChange={(e) => setMlSignal(e.target.value)} aria-label="Sinal de acao">
                <option value="">Todos os sinais</option>
                <option value="ACAO: aumentar investimento (ROAS > 15x)">Aumentar investimento</option>
                <option value="ACAO: considerar pausar ads (ROAS &lt; 3x)">Considerar pausar ads</option>
                <option value="ALERTA: taxa cancelamento alta (> 10%)">Cancelamento alto</option>
                <option value="OPORTUNIDADE: produto vende organico, considerar ads">Oportunidade organica</option>
                <option value="REVIEW: spend sem vendas no período de orders">Review spend</option>
                <option value="ATENCAO: grande variacao de preco">Variacao de preco</option>
              </ProductSelect>
              <ProductCount total={mlState.data?.total ?? null} />
            </ProductFilterBar>

            <ProductParetoSummary
              buckets={mlSummaryState.data?.buckets ?? null}
              loading={mlSummaryState.loading}
              activeBucket={mlBucket}
              onSelectBucket={setMlBucket}
              scopeNote={
                mlSummaryState.data
                  ? `Escopo: ranking acumulado atual${mlSummaryState.data.refreshed_at ? ` · atualizado em ${fmtRefreshedAt(mlSummaryState.data.refreshed_at)}` : ""} — o Mercado Livre nao possui competência mensal na fonte atual, por isso não há seletor de período aqui.${zeroGmvNote(mlSummaryState.data.excluded_zero_gmv_count)}${avgPriceNote(mlSummaryState.data.avg_price_weighted)} ${marginUnavailableNote("ml")}`
                  : undefined
              }
            />

            <MercadoLivreProductTable
              items={mlState.data}
              loading={mlState.loading}
              sort={mlSort.sort}
              onSort={mlSort.toggleSort}
              pagination={{ limit: PAGE_SIZE, offset: mlOffset, onChange: setMlOffset }}
            />
          </div>
        )}

        {/* TikTok view */}
        {tab === "tiktok" && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            <ProductFilterBar>
              <ProductSelect value={brand} onChange={(e) => setBrand(e.target.value)} aria-label="Marca">
                <option value="">Todas as marcas</option>
                {TK_SH_BRANDS.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
              </ProductSelect>
              <PeriodSelector value={period} onChange={setPeriod} months={monthOptions} />
              <ProductCount total={tkState.data?.total ?? null} />
            </ProductFilterBar>

            <ProductParetoSummary
              buckets={tkSummaryState.data?.buckets ?? null}
              loading={tkSummaryState.loading}
              activeBucket={tkBucket}
              onSelectBucket={setTkBucket}
              scopeNote={`Periodo: ${periodLabel}${zeroGmvNote(tkSummaryState.data?.excluded_zero_gmv_count)}${avgPriceNote(tkSummaryState.data?.avg_price_weighted)} ${marginUnavailableNote("tiktok")}`}
            />

            <TikTokProductTable
              items={tkState.data}
              loading={tkState.loading}
              sort={tkSort.sort}
              onSort={tkSort.toggleSort}
              pagination={{ limit: PAGE_SIZE, offset: tkOffset, onChange: setTkOffset }}
            />
          </div>
        )}

        {/* Shopee view */}
        {tab === "shopee" && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            <ProductFilterBar>
              <ProductSelect value={brand} onChange={(e) => setBrand(e.target.value)} aria-label="Marca">
                <option value="">Todas as marcas</option>
                {TK_SH_BRANDS.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
              </ProductSelect>
              <PeriodSelector value={period} onChange={setPeriod} months={monthOptions} />
              <ProductCount total={shState.data?.total ?? null} />
            </ProductFilterBar>

            <ProductParetoSummary
              buckets={shSummaryState.data?.buckets ?? null}
              loading={shSummaryState.loading}
              activeBucket={shBucket}
              onSelectBucket={setShBucket}
              scopeNote={`Periodo: ${periodLabel}${zeroGmvNote(shSummaryState.data?.excluded_zero_gmv_count)}${avgPriceNote(shSummaryState.data?.avg_price_weighted)} ${marginUnavailableNote("shopee")}`}
            />

            <ShopeeProductTable
              items={shState.data}
              loading={shState.loading}
              sort={shSort.sort}
              onSort={shSort.toggleSort}
              pagination={{ limit: PAGE_SIZE, offset: shOffset, onChange: setShOffset }}
            />
          </div>
        )}
      </main>
    </div>
  );
}
