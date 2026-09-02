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
  ComparisonStatus,
  MatchMethod,
  MatchQuality,
  MonitoramentoPrecoKpis,
  MonitoramentoPrecoRow,
} from "./monitoramento-preco-contract";

/** Marcador unico de valor indisponivel. NUNCA "R$ 0,00", nunca "0%". */
export const INDISPONIVEL = "—";

// ---------------------------------------------------------------------------
// Rotulos
// ---------------------------------------------------------------------------

export const STATUS_LABELS: Record<ComparisonStatus, string> = {
  below_reference: "Abaixo da referência",
  at_or_above_reference: "Na ou acima da referência",
  no_reference: "Sem referência B2B",
  non_comparable_reference_ambiguous: "Referência ambígua",
  inactive_listing: "Anúncio inativo",
  stale_observation: "Observação desatualizada",
};

/** Ordem de exibicao no filtro — a mesma do contrato do backend. */
export const STATUS_ORDER: ComparisonStatus[] = [
  "below_reference",
  "at_or_above_reference",
  "no_reference",
  "non_comparable_reference_ambiguous",
  "inactive_listing",
  "stale_observation",
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
  stale_observation: "muted",
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
  brand: string;
  status: string;
  productQuery: string;
  limit: number;
  offset: number;
}): string {
  return [
    "ml",
    params.brand || "all",
    params.status || "all",
    params.productQuery.trim() || "-",
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
 * Devolve a URL somente se for HTTPS e de dominio do Mercado Livre.
 * Qualquer outra coisa devolve `null`, e a tela mostra texto sem link.
 */
export function urlAnuncioSegura(permalink: string | null | undefined): string | null {
  if (!permalink) return null;
  let u: URL;
  try {
    u = new URL(permalink);
  } catch {
    return null;
  }
  if (u.protocol !== "https:") return null;
  const host = u.hostname.toLowerCase();
  const ok = DOMINIOS_ML.some((d) => host === d || host.endsWith(`.${d}`));
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

export function buildKpiViews(kpis: MonitoramentoPrecoKpis): KpiView[] {
  const total = kpis.monitored_count;
  const de = (n: number) => `${fmtContagem(n)} de ${fmtContagem(total)} monitorados`;
  return [
    {
      chave: "monitored",
      rotulo: "Anúncios monitorados",
      valor: total,
      detalhe: "Anúncios próprios no Mercado Livre na data observada",
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
      rotulo: "Observação desatualizada",
      valor: kpis.stale_count,
      detalhe: "Observação anterior ao último dia elegível",
      status: "stale_observation",
      qualidade: true,
    },
  ];
}

/**
 * Prova de fechamento: a soma dos seis status tem de dar `monitored_count`.
 * Exposta para a tela poder afirmar isso ao operador, e testada.
 */
export function statusFecha(kpis: MonitoramentoPrecoKpis): boolean {
  const soma =
    kpis.below_reference_count +
    kpis.at_or_above_reference_count +
    kpis.no_reference_count +
    kpis.ambiguous_reference_count +
    kpis.stale_count +
    kpis.inactive_count;
  return soma === kpis.monitored_count;
}

/** Titulo do drill-down de uma linha, sem vazar nada sensivel. */
export function tituloLinha(row: MonitoramentoPrecoRow): string {
  const nome = row.listing_title || row.product_name || row.item_id;
  return nome;
}
