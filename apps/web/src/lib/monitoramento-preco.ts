/**
 * Gate PMA-3 — apresentacao do monitoramento de precos proprios.
 *
 * MODULO PURO. So rotulo e formatacao. NADA aqui deriva negocio:
 *  - a diferenca em reais e em % vem do backend (`difference_amount`,
 *    `difference_pct`) e e' apenas formatada;
 *  - a situacao vem de `comparison_status` e e' apenas traduzida;
 *  - o metodo/qualidade do match vem do backend e e' apenas traduzido.
 *
 * PROIBIDO neste modulo (e verificado por teste):
 *  - recalcular diferenca;
 *  - `Math.abs()` sobre diferenca — o sinal E' a informacao;
 *  - classificar anuncio ou criar severidade;
 *  - chamar "abaixo da referencia" de infracao/violacao/sancao;
 *  - inferir preco de checkout;
 *  - transformar ausencia em R$ 0,00;
 *  - inventar vigencia para a referencia B2B.
 */
import type {
  AccountClock,
  ComparisonStatus,
  FreshnessStatus,
  Marketplace,
  MatchMethod,
  MatchQuality,
  MonitoramentoPrecoKpis,
  MonitoramentoPrecoMeta,
  MonitoramentoPrecoRow,
  ProductType,
} from "./monitoramento-preco-contract";

/** Marcador unico de valor indisponivel. NUNCA "R$ 0,00", nunca "0%". */
export const INDISPONIVEL = "—";

/**
 * Gate PMA-2C4B — preco que a fonte NAO observou.
 *
 * Separado de `INDISPONIVEL` de proposito: o travessao diz "nao temos esse
 * campo"; aqui sabemos exatamente o que aconteceu — a fotografia rodou e nao
 * trouxe preco para aquela oferta. Nunca "R$ 0,00", que afirmaria um preco.
 */
export const PRECO_NAO_OBSERVADO = "Não observado";

// ---------------------------------------------------------------------------
// Gate PMA-2C4B — canais
// ---------------------------------------------------------------------------

export interface CanalView {
  id: Marketplace;
  rotulo: string;
  /** Nome curto para caber em chip e titulo. */
  curto: string;
}

export const CANAIS: readonly CanalView[] = [
  { id: "ml", rotulo: "Mercado Livre", curto: "Mercado Livre" },
  { id: "shopee", rotulo: "Shopee", curto: "Shopee" },
  { id: "tiktok", rotulo: "TikTok Shop", curto: "TikTok Shop" },
];

export function canalLabel(m: Marketplace | string): string {
  return CANAIS.find((c) => c.id === m)?.rotulo ?? String(m);
}

/**
 * Nome do canal com a preposicao certa. "de Mercado Livre" e' agramatical, e
 * o texto publicado sempre disse "do Mercado Livre" — a comparacao com o
 * controle pegou a regressao.
 */
export function canalComPreposicao(m: Marketplace | string): string {
  return m === "ml" ? "do Mercado Livre" : `da ${canalLabel(m)}`;
}

/**
 * CAPACIDADE do canal no frontend. FAIL-CLOSED.
 *
 * O Mercado Livre e' publicado e nao tem flag. Shopee e TikTok dependem de
 * DUAS chaves independentes: esta, que decide se o botao existe, e a do
 * backend (`PMA_SHOPEE_ENABLED` / `PMA_TIKTOK_ENABLED`), que decide se ha o que
 * servir. Ligar so' uma das duas nao expoe o canal — ligar so' o frontend
 * mostraria um botao que devolve envelope `unavailable`, e ligar so' o backend
 * nao cria botao nenhum. A ativacao e' COORDENADA, de proposito.
 *
 * Ausencia da variavel resolve `false`. So' a string exata `"true"` liga.
 * As referencias a `process.env` sao literais porque o Next as substitui em
 * tempo de build; uma leitura dinamica nao chegaria ao bundle do navegador.
 */
export function canalHabilitado(m: Marketplace): boolean {
  if (m === "ml") return true;
  if (m === "shopee") return process.env.NEXT_PUBLIC_PMA_SHOPEE_ENABLED === "true";
  if (m === "tiktok") return process.env.NEXT_PUBLIC_PMA_TIKTOK_ENABLED === "true";
  return false;
}

/** Canais que a tela pode oferecer AGORA. O ML sempre esta' aqui. */
export function canaisDisponiveis(): CanalView[] {
  return CANAIS.filter((c) => canalHabilitado(c.id));
}

/**
 * Filtros que fazem sentido no canal. O ML nao modela conta de loja nem
 * `product_type` materializado, e o backend recusa os dois com 422 — mandar
 * assim mesmo transformaria uma limitacao conhecida num erro de borda.
 */
export function filtrosDoCanal(m: Marketplace): {
  conta: boolean;
  tipoDeProduto: boolean;
} {
  const canal = m !== "ml";
  return { conta: canal, tipoDeProduto: canal };
}

export const PRODUCT_TYPE_LABELS: Record<ProductType, string> = {
  kit_confirmed: "Kit confirmado",
  kit_suspected: "Possível kit",
  no_kit_signal: "Sem sinal de kit",
  // NAO e' "produto simples confirmado": a diferenca e' entre nao ter
  // encontrado evidencia e ter evidencia de ausencia.
  product_type_unknown: "Sinal de kit desconhecido",
};

export function productTypeLabel(t: ProductType | null | undefined): string {
  if (!t) return INDISPONIVEL;
  return PRODUCT_TYPE_LABELS[t] ?? t;
}

/**
 * Gate PMA-2C4B-R — rotulo de ESCOPO de negocio, por linha.
 *
 * Gocase e Denavita aparecem no TikTok e continuam VISIVEIS: o backend as
 * grava, as conta em `monitored_offers` e as declara em
 * `meta.out_of_scope_offer_count`. O que faltava era a tela DIZER isso. Uma
 * linha de marca fora do escopo sem rotulo faz o operador ler 223 ofertas de
 * capinha de celular como se fossem do monitoramento de beleza.
 */
export function foraDoEscopo(
  linha: Pick<MonitoramentoPrecoRow, "business_scope">,
): boolean {
  return linha.business_scope === "out_of_business_scope";
}

export const ROTULO_FORA_DE_ESCOPO = "Fora do escopo";

/**
 * Opcoes do filtro de ESCOPO. `monitoradas` manda a lista de marcas que a
 * propria API declarou monitoradas — filtro do SERVIDOR, entao total e
 * paginacao continuam corretos. Sem isto, as linhas fora do escopo ficariam
 * visiveis e nao haveria como estreitar a tabela por uma dimensao que o
 * operador ve na coluna Marca.
 */
export type EscopoFiltro = "todos" | "monitoradas";

export function marcaParaEscopo(
  escopo: EscopoFiltro,
  monitoradas: string[],
): string {
  return escopo === "monitoradas" ? monitoradas.join(",") : "";
}

export const PRODUCT_TYPE_ORDER: ProductType[] = [
  "kit_confirmed",
  "kit_suspected",
  "no_kit_signal",
  "product_type_unknown",
];

// ---------------------------------------------------------------------------
// Rotulos
// ---------------------------------------------------------------------------

export const STATUS_LABELS: Record<ComparisonStatus, string> = {
  below_reference: "Abaixo da referência",
  at_or_above_reference: "Na ou acima da referência",
  no_reference: "Sem referência B2B",
  non_comparable_reference_ambiguous: "Referência ambígua",
  inactive_listing: "Anúncio inativo",
};

/** Gate PMA-H1 — frescor e' dimensao propria, com rotulo proprio. */
export const FRESHNESS_LABELS: Record<FreshnessStatus, string> = {
  fresh: "Em dia",
  stale: "Sincronização atrasada",
  historical: "Consulta retrospectiva",
  unavailable: "Sem observação",
};

export function freshnessLabel(f: FreshnessStatus): string {
  return FRESHNESS_LABELS[f] ?? f;
}

/** Ordem de exibicao no filtro — a mesma do contrato do backend. */
export const STATUS_ORDER: ComparisonStatus[] = [
  "below_reference",
  "at_or_above_reference",
  "no_reference",
  "non_comparable_reference_ambiguous",
  "inactive_listing",
];

/**
 * Tom visual por situacao. E' TOM, nao severidade comercial: nenhum limiar
 * foi aprovado, e "abaixo da referencia" nao e' falta — e' um caso que pede
 * revisao humana.
 */
export const STATUS_TONE: Record<ComparisonStatus, "attention" | "neutral" | "muted"> = {
  below_reference: "attention",
  at_or_above_reference: "neutral",
  no_reference: "muted",
  non_comparable_reference_ambiguous: "muted",
  inactive_listing: "muted",
};

export function statusLabel(status: ComparisonStatus): string {
  return STATUS_LABELS[status] ?? status;
}

export const MATCH_METHOD_LABELS: Record<MatchMethod, string> = {
  brand_gtin_exact: "EAN exato na marca",
  brand_sku_exact_unique: "SKU único na marca",
};

export const MATCH_QUALITY_LABELS: Record<MatchQuality, string> = {
  primary_gtin_exact: "Chave primária (EAN)",
  secondary_sku_unique_in_brand: "Chave secundária (SKU)",
  ambiguous_multiple_candidates: "Ambígua — vários candidatos",
  unmatched: "Sem correspondência",
};

export function matchLabel(
  method: MatchMethod | null,
  quality: MatchQuality,
): string {
  if (method && MATCH_METHOD_LABELS[method]) return MATCH_METHOD_LABELS[method];
  return MATCH_QUALITY_LABELS[quality] ?? INDISPONIVEL;
}

export function matchQualityLabel(quality: MatchQuality): string {
  return MATCH_QUALITY_LABELS[quality] ?? INDISPONIVEL;
}

export const BRAND_LABELS: Record<string, string> = {
  apice: "Ápice",
  barbours: "Barbours",
  kokeshi: "Kokeshi",
  lescent: "Lescent",
  rituaria: "Rituária",
  yenzah: "Yenzah",
};

export function brandLabel(brand: string): string {
  return BRAND_LABELS[brand] ?? brand;
}

export const LISTING_STATUS_LABELS: Record<string, string> = {
  active: "Ativo",
  paused: "Pausado",
  under_review: "Em revisão",
  inactive: "Inativo",
};

export function listingStatusLabel(status: string | null): string {
  if (!status) return INDISPONIVEL;
  return LISTING_STATUS_LABELS[status] ?? status;
}

// ---------------------------------------------------------------------------
// Formatacao — pt-BR, sem abreviacao
// ---------------------------------------------------------------------------

const BRL = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const INTEIRO = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });

/** Moeda em pt-BR, duas casas, SEM K/M. Ausencia -> "—", nunca R$ 0,00. */
export function fmtMoeda(valor: number | null | undefined): string {
  if (valor == null || Number.isNaN(valor)) return INDISPONIVEL;
  return BRL.format(valor);
}

/**
 * Diferenca em reais, com SINAL EXPLICITO. Preserva o negativo — nunca
 * `Math.abs()`: um anuncio R$ 90,10 abaixo da referencia e' `-R$ 90,10`, e
 * esconder o sinal inverteria a leitura.
 */
export function fmtDiferenca(valor: number | null | undefined): string {
  if (valor == null || Number.isNaN(valor)) return INDISPONIVEL;
  const corpo = BRL.format(valor < 0 ? -valor : valor);
  if (valor < 0) return `-${corpo}`;
  if (valor > 0) return `+${corpo}`;
  return corpo;
}

/** Percentual com sinal e duas casas. Ausencia -> "—", nunca "0%". */
export function fmtPercentual(valor: number | null | undefined): string {
  if (valor == null || Number.isNaN(valor)) return INDISPONIVEL;
  const abs = Math.abs(valor).toLocaleString("pt-BR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const sinal = valor < 0 ? "-" : valor > 0 ? "+" : "";
  return `${sinal}${abs}%`;
}

/** Contagem inteira em pt-BR. Sem K/M: estes numeros sao auditaveis. */
export function fmtContagem(valor: number | null | undefined): string {
  if (valor == null || Number.isNaN(valor)) return INDISPONIVEL;
  return INTEIRO.format(valor);
}

/** `ref_date` (AAAA-MM-DD) -> DD/MM/AAAA, sem conversao de fuso. */
export function fmtData(iso: string | null | undefined): string {
  if (!iso) return INDISPONIVEL;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return INDISPONIVEL;
  return `${m[3]}/${m[2]}/${m[1]}`;
}

/**
 * `price_captured_at` — timestamp SEM fuso declarado pela origem.
 *
 * Renderizado como veio, sem converter e SEM rotular como BRT: a Silver guarda
 * `timestamp without time zone` e nao declara seu relogio. Rotular seria
 * inventar precisao.
 */
export function fmtCapturaPreco(iso: string | null | undefined): string {
  if (!iso) return INDISPONIVEL;
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(iso);
  if (!m) return INDISPONIVEL;
  return `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}`;
}

/**
 * Timestamps COM fuso (`refreshed_at`, `reference_captured_at`) — estes podem
 * ser exibidos em America/Sao_Paulo, porque a origem declara o offset.
 */
export function fmtInstanteBrt(iso: string | null | undefined): string {
  if (!iso) return INDISPONIVEL;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return INDISPONIVEL;
  return d.toLocaleString("pt-BR", {
    timeZone: "America/Sao_Paulo",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// Textos de escopo e limitacao
// ---------------------------------------------------------------------------

export const AVISO_OBSERVACIONAL = [
  "Ferramenta observacional: não é fiscalização de revendedores, não é política jurídica de preço mínimo anunciado e não altera preços automaticamente.",
  "A referência é o preço sugerido de revenda (PDV) das tabelas B2B, sem vigência declarada na origem.",
  "O preço observado é apenas o preço anunciado do produto: não inclui frete, cupom de vitrine, subsídio de plataforma nem preço de checkout.",
  "Como o checkout se compõe de produto + frete − cupom, e o frete eleva enquanto o cupom reduz, a direção do desvio é indeterminada — a diferença pode mudar de valor e de sinal.",
];

export const NOTA_DENOMINADOR =
  "Os KPIs descrevem sempre o conjunto do canal, marca e busca selecionados. O filtro de situação altera apenas a tabela, para preservar o denominador.";

export const COMPOSICAO_INDISPONIVEL = [
  { rotulo: "Frete", valor: INDISPONIVEL },
  { rotulo: "Cupom de vitrine", valor: INDISPONIVEL },
  { rotulo: "Subsídio da plataforma", valor: INDISPONIVEL },
  { rotulo: "Preço de checkout", valor: INDISPONIVEL },
];

// ---------------------------------------------------------------------------
// Paginacao — aritmetica puramente visual
// ---------------------------------------------------------------------------

export interface PaginacaoView {
  pagina: number;
  totalPaginas: number;
  primeiraLinha: number;
  ultimaLinha: number;
  temAnterior: boolean;
  temProxima: boolean;
  rotulo: string;
}

export function calcPaginacao(
  totalCount: number,
  returnedCount: number,
  limit: number,
  offset: number,
): PaginacaoView {
  const seguro = limit > 0 ? limit : 1;
  const totalPaginas = totalCount > 0 ? Math.ceil(totalCount / seguro) : 1;
  const pagina = Math.floor(offset / seguro) + 1;
  const primeiraLinha = returnedCount > 0 ? offset + 1 : 0;
  const ultimaLinha = offset + returnedCount;
  return {
    pagina,
    totalPaginas,
    primeiraLinha,
    ultimaLinha,
    temAnterior: offset > 0,
    temProxima: ultimaLinha < totalCount,
    rotulo:
      returnedCount > 0
        ? `${fmtContagem(primeiraLinha)}–${fmtContagem(ultimaLinha)} de ${fmtContagem(totalCount)}`
        : `0 de ${fmtContagem(totalCount)}`,
  };
}

/**
 * Aviso de truncamento. Existe porque o endpoint tem 855 linhas e devolve no
 * maximo 500: a pagina nunca pode dar a entender que 500 sao o universo.
 */
export function avisoTruncamento(
  truncated: boolean,
  returnedCount: number,
  totalCount: number,
): string | null {
  if (!truncated) return null;
  return `Exibindo ${fmtContagem(returnedCount)} de ${fmtContagem(totalCount)} anúncios. Use a paginação para ver os demais.`;
}

// ---------------------------------------------------------------------------
// Chave de requisicao — protege contra resposta antiga
// ---------------------------------------------------------------------------

export function buildMonitoramentoRequestKey(params: {
  /**
   * Gate PMA-2C4B: o CANAL faz parte da identidade. Sem ele, trocar de canal
   * reusaria a chave anterior e a guarda de frescor descartaria a resposta
   * nova por achar que nada mudou — a tela ficaria com os dados do canal
   * antigo sob o titulo do novo.
   */
  marketplace: Marketplace;
  brand: string;
  status: string;
  productQuery: string;
  /** Gate PMA-H1: a data observada FAZ PARTE da identidade da requisicao. */
  observedDate?: string;
  /** So' existem nos canais novos; no ML entram como `all`. */
  shopAccount?: string;
  productType?: string;
  limit: number;
  offset: number;
}): string {
  return [
    params.marketplace,
    params.brand || "all",
    params.status || "all",
    params.productQuery.trim() || "-",
    // Sem isto, trocar a data reusaria a resposta da data anterior e a guarda
    // de frescor descartaria a nova por achar que a chave nao mudou.
    params.observedDate || "latest",
    params.shopAccount || "all",
    params.productType || "all",
    String(params.limit),
    String(params.offset),
  ].join("|");
}

// ---------------------------------------------------------------------------
// Link externo do anuncio — allowlist de dominio
// ---------------------------------------------------------------------------

/** Dominios do Mercado Livre aceitos. Fora daqui, o link vira texto. */
const DOMINIOS_ML = [
  "mercadolivre.com.br",
  "produto.mercadolivre.com.br",
  "articulo.mercadolibre.com.br",
  "mercadolibre.com",
  "mercadolibre.com.br",
];

/**
 * Gate PMA-2C4B — allowlist POR MARKETPLACE. Um permalink de Shopee num item
 * de ML (ou o contrario) nao vira link: dominio errado e' sinal de payload
 * errado, e abrir mesmo assim levaria o operador para fora do que a tela diz
 * estar mostrando.
 *
 * A fato dos canais nao guarda URL, entao hoje `permalink` vem nulo em Shopee e
 * TikTok e a tela mostra texto sem link. A allowlist existe para quando a
 * origem passar a fornecer — e NUNCA se constroi URL a partir de `offer_key`,
 * que produziria link quebrado com aparencia de link bom.
 */
const DOMINIOS_POR_CANAL: Record<Marketplace, string[]> = {
  ml: DOMINIOS_ML,
  shopee: ["shopee.com.br"],
  tiktok: ["tiktok.com", "shop.tiktok.com"],
};

/**
 * Devolve a URL somente se for HTTPS e de dominio do canal informado.
 * Qualquer outra coisa devolve `null`, e a tela mostra texto sem link.
 */
export function urlAnuncioSegura(
  permalink: string | null | undefined,
  marketplace: Marketplace = "ml",
): string | null {
  if (!permalink) return null;
  let u: URL;
  try {
    u = new URL(permalink);
  } catch {
    return null;
  }
  if (u.protocol !== "https:") return null;
  const host = u.hostname.toLowerCase();
  const permitidos = DOMINIOS_POR_CANAL[marketplace] ?? [];
  const ok = permitidos.some((d) => host === d || host.endsWith(`.${d}`));
  return ok ? u.toString() : null;
}

// ---------------------------------------------------------------------------
// KPIs para exibicao
// ---------------------------------------------------------------------------

export interface KpiView {
  chave: string;
  rotulo: string;
  valor: number;
  /** Denominador para leitura honesta do numero. */
  detalhe: string;
  /** Situacao que o KPI abre na tabela, quando aplicavel. */
  status?: ComparisonStatus;
  qualidade?: boolean;
}

/**
 * Gate PMA-2C4B — o detalhe do cartao nomeava o Mercado Livre em QUALQUER
 * canal. Com a Shopee na tela, "Anúncios próprios no Mercado Livre" sobre 692
 * ofertas da Shopee era simplesmente falso. O texto do ML fica intacto como
 * padrao: quem nao passa o canal continua vendo o que ja' estava publicado.
 */
function escopoDoCanal(m: Marketplace): string {
  return m === "ml" ? "no Mercado Livre" : `na ${canalLabel(m)}`;
}

export function buildKpiViews(
  kpis: MonitoramentoPrecoKpis,
  marketplace: Marketplace = "ml",
): KpiView[] {
  const total = kpis.monitored_count;
  const de = (n: number) => `${fmtContagem(n)} de ${fmtContagem(total)} monitorados`;
  return [
    {
      chave: "monitored",
      rotulo: "Anúncios monitorados",
      valor: total,
      detalhe: `Anúncios próprios ${escopoDoCanal(marketplace)} na data observada`,
    },
    {
      chave: "comparable",
      rotulo: "Comparáveis",
      valor: kpis.comparable_count,
      detalhe: `${de(kpis.comparable_count)} — ativos e com referência resolvida`,
    },
    {
      chave: "below",
      rotulo: "Abaixo da referência",
      valor: kpis.below_reference_count,
      detalhe: `${fmtContagem(kpis.below_reference_count)} de ${fmtContagem(kpis.comparable_count)} comparáveis`,
      status: "below_reference",
    },
    {
      chave: "at_or_above",
      rotulo: "Na ou acima da referência",
      valor: kpis.at_or_above_reference_count,
      detalhe: `${fmtContagem(kpis.at_or_above_reference_count)} de ${fmtContagem(kpis.comparable_count)} comparáveis`,
      status: "at_or_above_reference",
    },
    {
      chave: "no_reference",
      rotulo: "Sem referência",
      valor: kpis.no_reference_count,
      detalhe: de(kpis.no_reference_count),
      status: "no_reference",
    },
    {
      chave: "inactive",
      rotulo: "Anúncios inativos",
      valor: kpis.inactive_count,
      detalhe: de(kpis.inactive_count),
      status: "inactive_listing",
    },
  ];
}

/**
 * Indicadores de QUALIDADE do dado, separados dos KPIs de negocio.
 * Nao sao somados aos de cima: descrevem o estado da medicao.
 */
export function buildQualidadeViews(kpis: MonitoramentoPrecoKpis): KpiView[] {
  return [
    {
      chave: "ambiguous",
      rotulo: "Referência ambígua",
      valor: kpis.ambiguous_reference_count,
      detalhe: "Chave disputada por mais de uma linha da tabela de origem",
      status: "non_comparable_reference_ambiguous",
      qualidade: true,
    },
    {
      chave: "stale",
      rotulo: "Observação atrasada",
      valor: kpis.stale_count,
      detalhe:
        "Linhas de um dia anterior ao último elegível. Sobrepõe os cartões " +
        "comerciais — não os substitui.",
      qualidade: true,
    },
    {
      chave: "historical",
      rotulo: "Consulta retrospectiva",
      valor: kpis.historical_count,
      detalhe: "Data observada escolhida deliberadamente. Não é atraso.",
      qualidade: true,
    },
  ];
}

/**
 * Prova de fechamento da PARTICAO COMERCIAL: os CINCO status somam
 * `monitored_count`.
 *
 * Gate PMA-H1: `stale_count` saiu desta soma. Ele conta FRESCOR, que e'
 * sobreposto — incluir os dois na mesma conta faria uma linha atrasada e
 * abaixo da referencia contar duas vezes.
 */
export function statusFecha(kpis: MonitoramentoPrecoKpis): boolean {
  const soma =
    kpis.below_reference_count +
    kpis.at_or_above_reference_count +
    kpis.no_reference_count +
    kpis.ambiguous_reference_count +
    kpis.inactive_count;
  return soma === kpis.monitored_count;
}

/** Fechamento da dimensao de FRESCOR, tambem em `monitored_count`. */
export function frescorFecha(kpis: MonitoramentoPrecoKpis): boolean {
  return (
    kpis.fresh_count + kpis.stale_count + kpis.historical_count ===
    kpis.monitored_count
  );
}

/** Titulo do drill-down de uma linha, sem vazar nada sensivel. */
export function tituloLinha(row: MonitoramentoPrecoRow): string {
  const nome = row.listing_title || row.product_name || row.item_id;
  return nome;
}

// ---------------------------------------------------------------------------
// Gate PMA-2C4B — politica de data, frescor e cobertura
// ---------------------------------------------------------------------------

export interface PoliticaDataView {
  /** Rotulo curto do teto, para o seletor de data. */
  tetoRotulo: string;
  /** Frase que explica o teto DESTE canal. */
  explicacao: string;
  /** Aviso de fotografia ainda mutavel; nulo quando nao se aplica. */
  avisoMutavel: string | null;
}

/**
 * Texto da politica de data DO CANAL. O do Mercado Livre e' o publicado e nao
 * muda; o dos canais novos NUNCA reutiliza a frase de D-1, porque o teto deles
 * e' o proprio dia operacional e chamar isso de "dia fechado" seria falso.
 */
export function politicaDataView(
  meta: Pick<
    MonitoramentoPrecoMeta,
    "date_policy" | "eligible_ref_date" | "snapshot_mutability"
  > | null,
): PoliticaDataView {
  if (meta?.date_policy === "snapshot_current") {
    const teto = meta.eligible_ref_date;
    return {
      tetoRotulo: teto ? `até ${fmtData(teto)} (hoje)` : "até hoje",
      explicacao:
        "Fotografia do estado corrente das vitrines: o dia de hoje já é " +
        "consultável, porque a observação é derivada do relógio de carga de " +
        "cada conta. Datas futuras não são observáveis.",
      avisoMutavel:
        meta.snapshot_mutability === "mutable_operational_snapshot"
          ? "Fotografia do dia corrente: é o estado observado até o relógio de " +
            "cada conta e AINDA PODE MUDAR hoje se a origem recarregar. Não é " +
            "período fechado nem contagem definitiva do dia."
          : null,
    };
  }
  const teto = meta?.eligible_ref_date;
  return {
    tetoRotulo: teto ? `até ${fmtData(teto)} (D−1)` : "até D−1",
    explicacao:
      "Série diária: apenas um dia fechado sustenta a comparação, então o " +
      "teto é D−1 no fuso America/Sao_Paulo. O dia corrente e o futuro não " +
      "são publicáveis pelo sync.",
    avisoMutavel: null,
  };
}

/**
 * As quatro situacoes de qualidade que a tela precisa distinguir. Sao
 * ORTOGONAIS ao status comercial e nunca o substituem.
 */
export type SituacaoFrescor =
  | "fotografia_em_dia"
  | "oferta_nao_revista"
  | "fotografia_atrasada"
  | "consulta_historica"
  | "sem_observacao";

/**
 * Classifica o frescor de UMA linha combinando os dois niveis.
 *
 * A distincao que importa: com a fotografia em dia, uma linha `stale` significa
 * que AQUELA OFERTA nao foi revista na carga — nao que o pipeline atrasou. Na
 * Shopee ha modelo com carimbo de ate 18 dias dentro de uma carga de hoje.
 * Mandar "verificar o sync" nesse caso apontaria o operador para o lugar errado.
 */
export function situacaoFrescor(
  linha: Pick<MonitoramentoPrecoRow, "freshness_status">,
  frescorDaFotografia: FreshnessStatus,
): SituacaoFrescor {
  if (linha.freshness_status === "historical") return "consulta_historica";
  if (linha.freshness_status === "unavailable") return "sem_observacao";
  if (linha.freshness_status === "stale") {
    return frescorDaFotografia === "stale"
      ? "fotografia_atrasada"
      : "oferta_nao_revista";
  }
  return "fotografia_em_dia";
}

export const SITUACAO_FRESCOR_LABELS: Record<SituacaoFrescor, string> = {
  fotografia_em_dia: "Revista nesta fotografia",
  oferta_nao_revista: "Não revista nesta fotografia",
  fotografia_atrasada: "Fotografia atrasada",
  consulta_historica: "Consulta retrospectiva",
  sem_observacao: "Sem observação",
};

export function situacaoFrescorLabel(s: SituacaoFrescor): string {
  return SITUACAO_FRESCOR_LABELS[s] ?? s;
}

/**
 * Preco ANUNCIADO da oferta. `null` vira "Não observado", nunca "R$ 0,00":
 * zero afirmaria um preco que ninguem viu.
 */
export function fmtPrecoObservado(valor: number | null | undefined): string {
  if (valor == null || Number.isNaN(valor)) return PRECO_NAO_OBSERVADO;
  return fmtMoeda(valor);
}

/** A linha tem preco observado? Sem ele nao ha diferenca a apresentar. */
export function temPrecoObservado(
  linha: Pick<MonitoramentoPrecoRow, "advertised_price">,
): boolean {
  return linha.advertised_price != null && !Number.isNaN(linha.advertised_price);
}

export interface CoberturaContaView {
  conta: string;
  ofertas: number | null;
  revistas: number | null;
  naoRevistas: number | null;
  relogio: string;
}

/**
 * Cobertura por CONTA de loja, direto de `meta.account_clocks`. Nada e'
 * somado aqui alem do que a API ja' entrega por conta — a tela apresenta.
 */
export function coberturaPorConta(
  relogios: AccountClock[] | null | undefined,
): CoberturaContaView[] {
  return (relogios ?? []).map((r) => ({
    conta: r.account,
    ofertas: r.offers ?? null,
    revistas: r.current_offers ?? null,
    naoRevistas: r.stale_offers ?? null,
    relogio: fmtInstanteBrt(r.account_watermark_at ?? r.refreshed_at ?? null),
  }));
}

/**
 * Resumo de cobertura: contas observadas e ofertas fora do escopo de negocio.
 * `esperadas` vem do que a API devolveu — a tela nao mantem lista propria de
 * contas, que ficaria desatualizada em silencio quando a origem mudar.
 */
export function resumoCobertura(
  meta: Pick<
    MonitoramentoPrecoMeta,
    "account_clocks" | "out_of_scope_offer_count" | "snapshot_status_counts"
  > | null,
): {
  contasObservadas: number;
  ofertasForaDeEscopo: number;
  revistas: number | null;
  naoRevistas: number | null;
} {
  const relogios = meta?.account_clocks ?? [];
  const contagem = meta?.snapshot_status_counts ?? {};
  const temContagem = Object.keys(contagem).length > 0;
  return {
    contasObservadas: relogios.length,
    ofertasForaDeEscopo: meta?.out_of_scope_offer_count ?? 0,
    revistas: temContagem ? (contagem.current ?? 0) : null,
    naoRevistas: temContagem ? (contagem.stale ?? 0) : null,
  };
}
