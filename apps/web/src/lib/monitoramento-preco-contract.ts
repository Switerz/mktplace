/**
 * Gate PMA-3 — contrato do monitoramento de precos, SEM dependencias.
 *
 * Separado de `api-client.ts` de proposito: aquele modulo importa
 * `./mock-data` e outros helpers, e o type-stripping nativo do Node (usado
 * por `node --test`) nao resolve import sem extensao. Com o contrato isolado,
 * os tipos e a montagem de query ficam testaveis sem carregar a arvore
 * inteira do cliente. `api-client.ts` reexporta tudo daqui.
 */
// ---------------------------------------------------------------------------
// Monitoramento de precos proprios (Gate PMA-3)
// ---------------------------------------------------------------------------
// Tipos FIEIS ao schema Python de `apps/api/app/schemas/monitoramento_preco.py`.
// Nenhum campo e' derivado aqui: diferenca, match e classificacao vem do
// backend. O frontend so apresenta.

/**
 * Gate PMA-2C4B — os TRES canais que a API serve.
 *
 * `ml` e' o unico publicado em producao; `shopee` e `tiktok` dependem de flag
 * no backend E de capacidade no frontend, as duas desligadas por padrao.
 */
export type Marketplace = "ml" | "shopee" | "tiktok";

export const MARKETPLACES: readonly Marketplace[] = ["ml", "shopee", "tiktok"];

/** Guarda de runtime: um valor fora do contrato NAO vira requisicao. */
export function isMarketplace(valor: unknown): valor is Marketplace {
  return typeof valor === "string" && (MARKETPLACES as readonly string[]).includes(valor);
}

/**
 * POLITICA DE DATA, escolhida pelo contrato da FONTE — nunca global.
 *   closed_day        Mercado Livre: serie diaria, teto D-1, D0 recusado;
 *   snapshot_current  Shopee/TikTok: fotografia do estado corrente, D0 normal.
 */
export type DatePolicy = "closed_day" | "snapshot_current";

/**
 * O que a fotografia servida ainda pode fazer.
 *   mutable_operational_snapshot  e' do dia corrente e pode mudar hoje se a
 *                                 origem recarregar. NAO e' periodo fechado,
 *                                 definitivo nem completo;
 *   settled_snapshot              o dia ja' virou; aquela fotografia nao muda.
 */
export type SnapshotMutability = "mutable_operational_snapshot" | "settled_snapshot";

/** Tipo de produto MATERIALIZADO pelo publisher. Nunca reclassificado aqui. */
export type ProductType =
  | "kit_confirmed"
  | "kit_suspected"
  | "no_kit_signal"
  | "product_type_unknown";

export type BusinessScope = "in_scope" | "out_of_business_scope";

/**
 * Por que uma oferta elegivel nao foi comparada. Vive AO LADO de
 * `comparison_status`, nunca no lugar dele.
 */
export type NonComparableReason =
  /** Havia tabela B2B da marca, chave e tipo simples — e a busca nao achou. */
  | "reference_missing_for_product"
  /** Gate PMA-REF-LINK-1: a MARCA nao tem tabela B2B publicada. */
  | "brand_without_b2b_reference"
  /** Gate PMA-REF-LINK-1: kit sem composicao confirmada. Nada foi estimado. */
  | "kit_composition_missing"
  /** Gate PMA-REF-LINK-1: anuncio sem GTIN e sem SKU — busca impossivel. */
  | "offer_without_match_key"
  | "sku_not_in_internal_catalog"
  | "product_without_ean"
  | "product_ean_not_consumer"
  | "ambiguous_multiple_candidates"
  | "invalid_reference_price"
  | "invalid_channel_price";

export type MetricVersion = "v1_all_active" | "v2_product_type_aware";
export type ObservationMode = "daily_series" | "snapshot_current";
export type PromoContext = "available" | "unavailable";
export type Availability = "available" | "unavailable";

/**
 * Relogio de UMA conta de loja. O Mercado Livre publica um so'; a Shopee
 * publica um por conta, porque suas quatro contas terminam em lotes distintos
 * e um MAX() global marcaria as tres primeiras como atrasadas.
 *
 * Os campos de contagem so' existem nos canais novos — no ML vem ausentes, e
 * ausente aqui significa "este canal nao tem esse conceito".
 */
export interface AccountClock {
  account: string;
  observed_at: string | null;
  refreshed_at: string | null;
  account_watermark_at?: string | null;
  offers?: number;
  current_offers?: number;
  stale_offers?: number;
  snapshot_status?: string | null;
}

/**
 * PARTICAO COMERCIAL — cinco valores que somam `monitored_count`.
 *
 * Gate PMA-H1: `stale_observation` SAIU daqui. Era um status comercial e
 * substituia a classificacao, de modo que um sync atrasado zerava os cinco
 * cartoes comerciais e a tela aparentava "nenhum desvio de preco". Frescor
 * virou dimensao propria, em `FreshnessStatus`.
 */
export type ComparisonStatus =
  | "below_reference"
  | "at_or_above_reference"
  | "no_reference"
  | "non_comparable_reference_ambiguous"
  | "inactive_listing";

/**
 * QUALIDADE TRANSVERSAL — sobreposta a `ComparisonStatus`, nunca no lugar dele.
 *   fresh       observacao de D-1;
 *   stale       o modo `latest` caiu num dia anterior porque o sync atrasou;
 *   historical  o usuario ESCOLHEU um dia anterior (nao e' atraso);
 *   unavailable nao ha observacao materializada para responder.
 */
export type FreshnessStatus = "fresh" | "stale" | "historical" | "unavailable";

export type QueryMode = "latest" | "selected_date";

/** Alias DEPRECIADO aceito pelo backend como filtro de frescor. */
export const DEPRECATED_STALE_STATUS = "stale_observation";

export type MatchMethod = "brand_gtin_exact" | "brand_sku_exact_unique";

export type MatchQuality =
  | "primary_gtin_exact"
  | "secondary_sku_unique_in_brand"
  | "ambiguous_multiple_candidates"
  | "unmatched";

export interface MonitoramentoPrecoMeta {
  timezone: string;
  currency: string;
  marketplace: Marketplace;
  /** `latest` (maior observacao <= D-1) ou `selected_date` (dia pedido). */
  mode: QueryMode;
  refreshed_at: string | null;
  /** O que foi PEDIDO em `observed_date`. Nulo no modo `latest`. */
  requested_observed_date: string | null;
  /**
   * A data efetivamente USADA. Nula quando a data pedida nao tem observacao —
   * nunca substituida pela mais proxima, porque aproximar responderia outra
   * pergunta.
   */
  observed_ref_date: string | null;
  /**
   * Teto do que e' consultavel NESTE canal: D-1 sob `closed_day`, o proprio
   * dia operacional sob `snapshot_current`. Nao e' sempre D-1.
   */
  eligible_ref_date: string | null;
  /** Datas MATERIALIZADAS ate o teto, mais recentes primeiro. Sem calendario. */
  available_observed_dates: string[];
  available_observed_dates_limit: number;
  /** Dias entre a observacao e D-1. Zero = em dia. Nulo = sem observacao. */
  lag_days: number | null;
  freshness_status: FreshnessStatus;
  reference_snapshot_id: string | null;
  reference_captured_at: string | null;
  /** Sempre o snapshot mais recente — inclusive numa data historica. */
  reference_basis: "latest_available_snapshot";
  /** Frase que declara as DUAS datas e a ausencia de vigencia historica. */
  comparison_basis_text: string | null;
  reference_type: "suggested_retail_pdv";
  policy_status: "not_applicable_to_own_store_monitoring";
  /** So `missing`: a referencia B2B nao tem vigencia declarada. */
  validity_status: "missing";
  /** So `advertised_only`: sem frete, cupom, subsidio ou checkout. */
  coverage_status: "advertised_only";
  monitored_brands: string[];
  /**
   * Gate PMA-2C4D3-H2 — marcas que ESTA fotografia contém, por marketplace e
   * `observed_date`, calculadas antes de qualquer filtro ou paginação.
   *
   * É a fonte do seletor de marca. `monitored_brands` responde outra coisa —
   * o escopo de monitoramento do negócio — e vem igual para Shopee e TikTok,
   * com Kokeshi incluída; usá-la no filtro oferecia Kokeshi na Shopee e
   * respondia "0", lido como "Kokeshi não tem anúncios".
   *
   * Opcional porque backend e frontend são publicados separadamente: durante a
   * janela entre os dois deploys a resposta antiga não traz o campo.
   */
  observed_brands?: string[];
  /**
   * `monitored_brands` menos `observed_brands`. NÃO é zero anúncio: é ausência
   * de observação nesta fotografia. Também opcional, pelo mesmo motivo.
   */
  monitored_unobserved_brands?: string[];
  comparable_brands: string[];
  no_reference_brands: string[];
  out_of_scope_brands: Record<string, string>;
  order_by: string;
  warnings: string[];

  // ---------------- Gate PMA-2C1A / 2C4A: multicanal -------------------
  metric_version: MetricVersion;
  observation_mode: ObservationMode;
  promo_context: PromoContext;
  /** `unavailable` quando o canal e' reconhecido mas nao ha o que servir. */
  availability: Availability;
  unavailable_reason: string | null;
  /** Alias de `observed_ref_date` com o nome comum aos tres canais. */
  observed_date: string | null;
  /** Instante da captura do PRECO. Nao e' a atualizacao cadastral. */
  observed_at: string | null;
  /** Conceito dos canais novos. Vem VAZIO no ML. */
  snapshot_status_counts: Record<string, number>;
  product_type_counts: Record<string, number>;
  account_clocks: AccountClock[];

  // ---------------- Gate PMA-2C4A: politica de data --------------------
  date_policy: DatePolicy;
  /** Nulo quando nao ha fotografia. */
  snapshot_mutability: SnapshotMutability | null;
  /**
   * Ofertas de marca fora do escopo de beleza: aparecem na tabela e NAO entram
   * em `eligible_offers` nem na cobertura. Explicito para que a diferenca
   * entre `total_count` e `metrics.monitored_offers` seja reconciliavel.
   */
  out_of_scope_offer_count: number;
}

/**
 * Bloco multicanal, SEPARADO de `kpis`. `kpis` mantem a forma publicada do ML;
 * `metrics` e' o vocabulario dos tres canais.
 */
export interface MonitoramentoPrecoMetrics {
  monitored_offers: number;
  active_offers: number;
  inactive_offers: number;
  kit_confirmed: number;
  kit_suspected: number;
  no_kit_signal: number;
  product_type_unknown: number;
  eligible_offers: number;
  comparable_offers: number;
  below_reference: number;
  at_or_above_reference: number;
  /** Motivo -> contagem. Soma com `comparable_offers` em `eligible_offers`. */
  non_comparable_reasons: Record<string, number>;
  /**
   * Gate PMA-REF-LINK-1 — motivo -> contagem sobre as ofertas ATIVAS sem
   * referencia, kits e marcas fora do escopo INCLUSIVE. Soma exatamente
   * `kpis.no_reference_count`.
   *
   * Existe porque os dois numeros respondem perguntas diferentes:
   * `non_comparable_reasons` fecha o DENOMINADOR (so' elegiveis) e
   * `no_reference_breakdown` explica o CARTAO da tela. No TikTok eram 810 no
   * cartao contra 251 nos motivos, e nada no payload dizia que os 559 de
   * diferenca eram 404 kits e 155 ofertas fora do escopo de beleza.
   */
  no_reference_breakdown: Record<string, number>;
  /** NULO quando nada foi medido. Zero afirmaria "medimos e deu 0%". */
  coverage_rate: number | null;
  distinct_b2b_products: number;
  b2b_reach: number | null;
}

export interface MonitoramentoPrecoKpis {
  monitored_count: number;
  comparable_count: number;
  below_reference_count: number;
  at_or_above_reference_count: number;
  no_reference_count: number;
  ambiguous_reference_count: number;
  inactive_count: number;
  /** Frescor: SOBREPOSTO a particao comercial, nao competindo com ela. */
  fresh_count: number;
  stale_count: number;
  historical_count: number;
}

export interface MonitoramentoPrecoRow {
  product_name: string | null;
  brand: string;
  marketplace: string;
  item_id: string;
  seller_sku: string | null;
  gtin: string | null;
  listing_title: string | null;
  permalink: string | null;
  listing_status: string | null;
  currency: string | null;

  ref_date: string;
  /** Captura do PRECO. Sem fuso declarado pela origem — nao rotular como BRT. */
  observed_at: string | null;
  /** Alteracao do CADASTRO. NUNCA e' horario de preco. */
  listing_metadata_updated_at: string | null;
  /**
   * Gate PMA-2C4B — passou a admitir NULO. A fato dos canais permite
   * `observed_price IS NULL` ("nao observamos preco"), e o Mercado Livre NUNCA
   * produz nulo aqui. `null` NAO e' zero: a tela mostra "Nao observado" e nao
   * calcula diferenca.
   */
  advertised_price: number | null;
  original_price: number | null;

  /** Segue `advertised_price`, inclusive na ausencia. */
  observed_effective_amount: number | null;
  /** Sempre null neste MVP: a fonte do ML nao fornece. NULL != 0. */
  shipping_amount: number | null;
  seller_coupon_amount: number | null;
  platform_subsidy_amount: number | null;
  checkout_price: number | null;
  coverage_status: "advertised_only";

  suggested_retail_amount: number | null;
  reference_type: "suggested_retail_pdv";
  validity_status: "missing";
  policy_status: "not_applicable_to_own_store_monitoring";
  reference_captured_at: string | null;
  reference_row_id: string | null;

  difference_amount: number | null;
  difference_pct: number | null;
  match_method: MatchMethod | null;
  match_quality: MatchQuality;
  reference_candidate_count: number;
  comparison_status: ComparisonStatus;
  /**
   * Por que a oferta elegivel nao foi comparada. `no_reference` cobre tres
   * causas distintas, e este campo e' o que as separa — sem ele, "nao casou" e
   * "preco nao observado" ficariam indistinguiveis.
   */
  non_comparable_reason: NonComparableReason | null;
  /**
   * Frescor da linha, ao LADO do status comercial. E' o PIOR de dois niveis: a
   * fotografia ser a mais recente E esta oferta ter sido revista dentro dela.
   */
  freshness_status: FreshnessStatus;
  limitations: string[];

  // ---------------- Gate PMA-2C4A: campos de CANAL ---------------------
  // Todos MATERIALIZADOS na fato. Nulos no Mercado Livre, que nao os modela —
  // e nulo aqui significa "este canal nao tem esse conceito", nao "faltou".
  /** Identidade da oferta no canal. Igual a `item_id`. */
  offer_key: string | null;
  /** Conta de loja na origem, e escopo da substituicao na publicacao. */
  shop_account: string | null;
  parent_item_id: string | null;
  model_id: string | null;
  observed_date: string | null;
  date_policy: DatePolicy | null;
  /** Decidido pelo publisher. NUNCA reclassificado no frontend. */
  product_type: ProductType | null;
  product_type_source: string | null;
  /** Frescor da OFERTA como o publisher a carimbou na carga. */
  snapshot_status: string | null;
  /** Fim da carga da PROPRIA conta. */
  account_watermark_at: string | null;
  /** Coluna de ORIGEM do preco: `current_price` na Shopee, `sale_price` no TikTok. */
  observed_price_source: string | null;
  list_price: number | null;
  promo_context: PromoContext | null;
  promo_id: string | null;
  promo_discount_pct: number | null;
  business_scope: BusinessScope | null;
  batch_id: string | null;
  source_run_id: string | null;
}

export interface MonitoramentoPrecoResponse {
  meta: MonitoramentoPrecoMeta;
  /** Bloco PUBLICADO do ML — forma inalterada. */
  kpis: MonitoramentoPrecoKpis;
  /** Bloco multicanal, separado. Ver `MonitoramentoPrecoMetrics`. */
  metrics: MonitoramentoPrecoMetrics;
  rows: MonitoramentoPrecoRow[];
  returned_count: number;
  total_count: number;
  truncated: boolean;
}

export interface MonitoramentoPrecoParams {
  /** `ml` (padrao), `shopee` ou `tiktok`. */
  marketplace?: Marketplace;
  brand?: string;
  status?: string;
  productQuery?: string;
  /** Data OBSERVADA, YYYY-MM-DD. Omitida = modo `latest`. */
  observedDate?: string;
  /**
   * Conta de loja. So' se aplica a `shopee` e `tiktok`: a fato do ML nao
   * modela conta, e o backend recusa com 422 em vez de ignorar.
   */
  shopAccount?: string;
  /** Tipo de produto materializado. Tambem so' nos canais novos. */
  productType?: string;
  limit?: number;
  offset?: number;
}

/** Teto do backend por pagina. As 855 linhas nao cabem numa resposta. */
export const MONITORAMENTO_PRECO_MAX_LIMIT = 500;

export function buildMonitoramentoPrecoQuery(
  params: MonitoramentoPrecoParams,
): URLSearchParams {
  const qs = new URLSearchParams();
  // Um valor fora do contrato nunca vira requisicao: cai para `ml`, que e' o
  // canal publicado. Enviar lixo faria o backend responder 422 sobre um erro
  // que nasceu aqui.
  qs.set("marketplace", isMarketplace(params.marketplace) ? params.marketplace : "ml");
  if (params.brand) qs.set("brand", params.brand);
  if (params.status) qs.set("status", params.status);
  if (params.productQuery) qs.set("product_query", params.productQuery);
  // Gate PMA-H1: a data OBSERVADA e' o parametro publico de data.
  if (params.observedDate) qs.set("observed_date", params.observedDate);
  // Gate PMA-2C4B — filtros exclusivos dos canais novos. Nao sao enviados para
  // o ML: la' o backend os recusa com 422, e mandar assim mesmo transformaria
  // uma limitacao conhecida num erro de borda.
  if (params.shopAccount) qs.set("shop_account", params.shopAccount);
  if (params.productType) qs.set("product_type", params.productType);
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.offset != null) qs.set("offset", String(params.offset));
  // `ref_date` NUNCA e' enviado: o nome e' ambiguo entre a data observada e a
  // data de captura da referencia, e o backend responde 422 fixo a ele.
  return qs;
}

export class MonitoramentoPrecoError extends Error {
  readonly status: number | null;
  constructor(message: string, status: number | null) {
    super(message);
    this.name = "MonitoramentoPrecoError";
    this.status = status;
  }
}

/**
 * Gate PMA-2C4D3-H3 — texto que a PESSOA lê quando a carga falha.
 *
 * O `message` do erro é diagnóstico ("A API respondeu 422.") e ia direto para
 * a tela: número de status não diz nada a quem opera e é detalhe de
 * implementação. O `status` continua existindo no erro, para log e para
 * decidir a redação — só não aparece.
 *
 * Nenhuma variante ecoa a resposta do servidor nem o filtro pedido.
 */
export function mensagemDeFalha(status: number | null): string {
  if (status === null) {
    return "Não foi possível falar com o servidor. Verifique a conexão e tente novamente.";
  }
  if (status === 422 || status === 400) {
    return "Esta combinação de filtros não pôde ser consultada. Ajuste os filtros e tente novamente.";
  }
  if (status === 404) {
    return "Não há monitoramento publicado para esta consulta.";
  }
  if (status >= 500) {
    return "O serviço de monitoramento está indisponível no momento. Tente novamente em alguns instantes.";
  }
  return "Não foi possível carregar o monitoramento de preços. Tente novamente.";
}
