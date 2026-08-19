// Contrato canonico de querystring da pagina Produtos (Gate V3-1A, §9.2 do
// desenho em docs/INTELIGENCIA_BRAND_V3_PLAN.md).
//
//   /produtos?channels=ml&brands=<marca>&pareto_bucket=<bucket>
//
// Motivo de existir: a pagina guardava aba/marca/bucket SO' em estado local e
// nao lia `searchParams`, entao o filtro nao era reproduzivel por URL — e o
// CTA do bucket Pareto (Inteligencia) nao tinha destino honesto.
//
// Regras duras:
// - `channels` seleciona a aba; precisa ser UM canal conhecido;
// - `brands` recebe UMA marca valida PARA A ABA resolvida;
// - `pareto_bucket` recebe somente bucket allowlisted;
// - parametro ausente, repetido, vazio, invalido ou incompativel com o canal
//   e' IGNORADO com seguranca, voltando ao estado padrao correspondente;
// - a pagina usa `brands` (plural, convencao de FILTER_QUERY_KEYS); o
//   endpoint continua recebendo `brand` (singular). A traducao acontece
//   somente ao montar o request;
// - `pareto_bucket` NAO entra em FILTER_QUERY_KEYS, e `/produtos` nao e'
//   filter-aware: a sidebar nunca transporta esse parametro para outra tela;
// - nenhuma metrica viaja na URL.
//
// Modulo puro, sem React nem tipos do Next, para ser testavel com node:test.

import { brandsForTab, type ProdutosTab } from "./produtos-tab-transition.ts";
import { isParetoBucket, PARETO_BUCKETS, type ParetoBucket } from "./inteligencia/pareto.ts";

export { PARETO_BUCKETS, isParetoBucket };
export type { ParetoBucket };

/** Chaves que ESTA pagina entende na querystring. */
export const PRODUTOS_URL_KEYS = ["channels", "brands", "pareto_bucket"] as const;

/** Chave do bucket. Deliberadamente fora de FILTER_QUERY_KEYS. */
export const PARETO_BUCKET_QUERY_KEY = "pareto_bucket";

/** Canal na URL -> aba da pagina. Allowlist fechada. */
export const CHANNEL_TO_TAB: Record<string, ProdutosTab> = {
  ml: "ml",
  tiktok: "tiktok",
  shopee: "shopee",
};

export const TAB_TO_CHANNEL: Record<ProdutosTab, string> = {
  ml: "ml",
  tiktok: "tiktok",
  shopee: "shopee",
};

export interface ParamReader {
  get(key: string): string | null;
  getAll?(key: string): string[];
}

/** Le exatamente UM valor; repetido e' ambiguidade e vira `null`. */
export function readSingle(params: ParamReader, key: string): string | null {
  if (typeof params.getAll === "function") {
    const all = params.getAll(key);
    if (all.length !== 1) return null;
    return all[0];
  }
  return params.get(key);
}

export interface ProdutosUrlState {
  /** `null` = a pagina mantem a aba padrao dela. */
  tab: ProdutosTab | null;
  /** `null` = "todas as marcas" (o estado padrao da pagina e' string vazia). */
  brand: string | null;
  /** `null` = nenhum bucket selecionado. */
  bucket: ParetoBucket | null;
}

export const EMPTY_PRODUTOS_URL_STATE: ProdutosUrlState = { tab: null, brand: null, bucket: null };

/**
 * Interpreta a querystring da pagina Produtos.
 *
 * Nada aqui levanta: toda entrada ruim degrada para `null` no campo
 * correspondente, e os campos sao independentes — um `brands` invalido nao
 * derruba um `channels` valido.
 *
 * A marca e' validada contra as marcas DA ABA resolvida: `brands=apice` com
 * `channels=ml` e' incompativel (apice nao vende no ML) e por isso e'
 * ignorado.
 */
export function parseProdutosUrl(params: ParamReader): ProdutosUrlState {
  const rawChannel = readSingle(params, "channels");
  const tab = rawChannel != null ? (CHANNEL_TO_TAB[rawChannel.trim()] ?? null) : null;

  const rawBrand = readSingle(params, "brands");
  let brand: string | null = null;
  if (rawBrand != null) {
    const candidate = rawBrand.trim();
    // Sem aba resolvida nao ha' como validar compatibilidade de canal, e a
    // pagina tem aba padrao propria — nesse caso a marca e' ignorada em vez
    // de aplicada a uma aba que talvez nao a suporte.
    if (candidate !== "" && tab != null && brandsForTab(tab).includes(candidate)) {
      brand = candidate;
    }
  }

  const rawBucket = readSingle(params, PARETO_BUCKET_QUERY_KEY);
  let bucket: ParetoBucket | null = null;
  if (rawBucket != null) {
    const candidate = rawBucket.trim();
    if (isParetoBucket(candidate)) bucket = candidate;
  }

  return { tab, brand, bucket };
}

export interface BuildProdutosHrefInput {
  tab: ProdutosTab;
  /** String vazia ou `null` = todas as marcas; nao vai para a URL. */
  brand?: string | null;
  bucket?: ParetoBucket | null;
}

/**
 * Monta a querystring canonica da pagina. Omite o que estiver vazio — uma URL
 * com `brands=` pendurado nao e' estado, e' ruido.
 */
export function buildProdutosHref({ tab, brand, bucket }: BuildProdutosHrefInput): string {
  const qs = new URLSearchParams();
  qs.set("channels", TAB_TO_CHANNEL[tab]);
  if (brand) qs.set("brands", brand);
  if (bucket) qs.set(PARETO_BUCKET_QUERY_KEY, bucket);
  return `/produtos?${qs.toString()}`;
}

/**
 * Query relativa (sem o pathname) para `router.replace` — a pagina sincroniza
 * a URL sem navegar e sem empilhar historico.
 */
export function buildProdutosQuery(input: BuildProdutosHrefInput): string {
  return buildProdutosHref(input).replace("/produtos?", "");
}

/**
 * Traducao explicita para o endpoint: a pagina fala `brands`, a API fala
 * `brand`. Existe como funcao para que a diferenca fique registrada num
 * lugar so', e testavel.
 */
export function brandParamForEndpoint(brandFromPage: string): string | undefined {
  return brandFromPage === "" ? undefined : brandFromPage;
}
