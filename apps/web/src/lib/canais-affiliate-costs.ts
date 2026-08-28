// Apresentacao do bloco "Impacto de afiliados no resultado" da aba Canais
// (contrato §23 de docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md). Modulo PURO, sem
// React, para ser testavel isoladamente — mesmo padrao de
// canais-channel-metrics.ts e async-channel-state.ts.
//
// O QUE ESTE MODULO NAO FAZ, E POR QUE
// -----------------------------------
// - Nao soma os tres componentes. Qual subconjunto constitui "custo de
//   afiliado" e' decisao aberta, e a ausencia de sobreposicao entre eles NAO
//   esta provada: somar nao e' so decisao comercial pendente, e'
//   aritmeticamente nao validado.
// - Nao produz total, razao, ROI, ROAS nem receita atribuida.
// - Nao aplica Math.abs(): o valor sai assinado como esta na fonte.
// - Nao converte ausencia em zero, nem zero em ausencia.
// - Nao usa o `refreshedAt` geral da pagina como frescor do bloco.

import type {
  AffiliateChannelStatus,
  AffiliateCostRow,
  AffiliateCostsBlock,
  AffiliateCoverageStatus,
  AffiliatePeriodStatus,
} from "./api-client";

/** Titulo provisorio. "Custo de afiliados" simples esta BLOQUEADO no §23: */
/*  daria a entender que os tres lancamentos sao um custo unico e somavel.  */
export const AFFILIATE_BLOCK_TITLE = "Impacto de afiliados no resultado";

/** Rotulos dos tres componentes, sempre exibidos SEPARADAMENTE. */
export const AFFILIATE_COMPONENT_LABELS = {
  creator_commission_signed: "Comissão de criador",
  partner_commission_signed: "Comissão de parceiro",
  affiliate_ads_commission_signed: "Comissão de Ads de afiliado",
} as const;

export type AffiliateComponentKey = keyof typeof AFFILIATE_COMPONENT_LABELS;

/** Ordem FIXA de exibicao dos componentes. Nao e' ranking por valor. */
export const AFFILIATE_COMPONENT_ORDER: AffiliateComponentKey[] = [
  "creator_commission_signed",
  "partner_commission_signed",
  "affiliate_ads_commission_signed",
];

export const CHANNEL_LABELS: Record<string, string> = {
  tiktok: "TikTok Shop",
  mercadolivre: "Mercado Livre",
  shopee: "Shopee",
};

export const BRAND_LABELS: Record<string, string> = {
  apice: "Apice",
  barbours: "Barbours",
  kokeshi: "Kokeshi",
  lescent: "Lescent",
  rituaria: "Rituaria",
};

export type AffiliateTone = "value" | "muted" | "warning";

/**
 * Fase de exibicao do bloco, derivada do ciclo de requisicao da pagina.
 *
 * Existe porque `loading = !dataIsFresh` colapsa quatro situacoes em uma e
 * deixa o painel PULSANDO PARA SEMPRE quando a requisicao terminou em erro —
 * a promessa visual de "ja vem" que nunca se cumpre. Aqui sao quatro estados
 * separados, e `unavailable` e' terminal.
 */
export type AffiliateBlockPhase =
  /** Requisicao atual em voo. */
  | "loading"
  /** Frame de troca de `requestKey`: o efeito ainda nao disparou. Transitorio. */
  | "neutral"
  /** A requisicao TERMINOU em erro. Nao ha o que esperar. */
  | "unavailable"
  /** `resolvedKey` bate com `requestKey`: o dado em memoria e' deste filtro. */
  | "fresh";

export function resolveBlockPhase(input: {
  loading: boolean;
  error: boolean;
  requestKey: string;
  resolvedKey: string | null;
}): AffiliateBlockPhase {
  // `loading` vence um `error` remanescente: um retry ja esta em voo, e o erro
  // anterior deixou de ser o estado corrente.
  if (input.loading) return "loading";
  if (input.error) return "unavailable";
  if (input.resolvedKey === input.requestKey) return "fresh";
  return "neutral";
}

export interface AffiliateCell {
  text: string;
  tone: AffiliateTone;
}

/**
 * Formata um valor contabil ASSINADO em BRL, pt-BR.
 *
 * Deliberadamente NAO reusa `fmtBrl`: aquele abrevia para "R$ 1.2M", e um
 * custo contabil abreviado nao reconcilia com nenhum relatorio. Aqui o valor
 * sai integral, com centavos e com o sinal da fonte.
 *
 * `null` = ausencia de medicao -> travessao. `0` = medido zero -> "R$ 0,00".
 * Trocar um pelo outro inventaria ou apagaria informacao.
 */
export function formatSignedBrl(value: number | null): AffiliateCell {
  if (value == null) return { text: "—", tone: "muted" };
  const texto = new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
  return { text: texto, tone: "value" };
}

/** Competencia `YYYY-MM` -> `mm/aaaa`. Entrada malformada volta como veio. */
export function formatRefMonth(refMonth: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(refMonth);
  if (!m) return refMonth;
  return `${m[2]}/${m[1]}`;
}

export function channelLabel(channel: string): string {
  return CHANNEL_LABELS[channel] ?? channel;
}

export function brandLabel(brand: string): string {
  return BRAND_LABELS[brand] ?? brand;
}

/**
 * Frase de frescor PROPRIA do bloco — ou `null` quando frescor nao se aplica.
 *
 * So existe frescor quando uma fotografia do TikTok foi de fato CONSULTADA.
 * O backend marca isso com `freshness_status: "manual_snapshot"`; qualquer
 * outro estado (`unknown`) significa que nenhuma leitura aconteceu — canal sem
 * fonte, consulta falha, ou periodo parcial em que nem se consultou. Nesses
 * casos, dizer "carga manual sem registro de data" seria afirmar algo sobre um
 * dado que ninguem leu, e o leitor entenderia como "a carga esta sem data".
 *
 * `manual_snapshot` e' o estado atual e verdadeiro quando ha leitura: a carga
 * da fact e' manual e nao existe rotina nem SLA (frente UE2-C). `fresh`/
 * `stale` sao inatribuiveis antes disso — qualquer limiar seria inventado — e
 * por isso nunca reusamos o `refreshedAt` geral da pagina, que diria "agora"
 * para um dado congelado so' porque a rota respondeu agora.
 */
export function describeFreshness(block: AffiliateCostsBlock): string | null {
  if (block.freshness_status !== "manual_snapshot") return null;
  const carga = block.affiliate_refreshed_at
    ? `Carga manual gravada em ${formatTimestamp(block.affiliate_refreshed_at)}`
    : "Carga manual sem registro de data";
  const lida = block.source_watermark
    ? `; fonte lida até ${formatTimestamp(block.source_watermark)}`
    : "";
  return `${carga}${lida}. Sem atualização automática.`;
}

const BRT = "America/Sao_Paulo";

const BRT_FORMAT = new Intl.DateTimeFormat("pt-BR", {
  timeZone: BRT,
  day: "2-digit", month: "2-digit", year: "numeric",
  hour: "2-digit", minute: "2-digit", hour12: false,
});

/**
 * Formata um instante da API para leitura no fuso OPERACIONAL.
 *
 * Tres casos, deliberadamente distintos:
 *
 * 1. **Com offset ou `Z`** — e' um instante absoluto. Convertido para
 *    `America/Sao_Paulo` e rotulado `BRT`. Recortar a string mostraria a hora
 *    UTC como se fosse local: um snapshot das 23h30 BRT apareceria no dia
 *    seguinte, de madrugada.
 * 2. **Data pura (`YYYY-MM-DD`)** — nao e' instante, e' competencia de
 *    calendario. Devolvida como data, SEM deslocamento: converter fuso aqui
 *    moveria o dia sem que exista hora para justificar.
 * 3. **Sem offset, mas com hora** — instante de fuso DESCONHECIDO. Exibido
 *    como veio e sem rotulo de fuso: carimbar `BRT` afirmaria um fuso que a
 *    fonte nao declarou.
 *
 * Entrada invalida volta como veio, sem hora inventada.
 */
export function formatTimestamp(iso: string): string {
  const dataPura = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (dataPura) return `${dataPura[3]}/${dataPura[2]}/${dataPura[1]}`;

  const comHora =
    /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/
      .exec(iso);
  if (!comHora) return iso;

  const [, ano, mes, dia, hora, minuto, offset] = comHora;
  if (!offset) return `${dia}/${mes}/${ano} ${hora}:${minuto}`;

  const instante = new Date(iso.replace(" ", "T"));
  if (Number.isNaN(instante.getTime())) return iso;
  // `pt-BR` devolve "dd/mm/aaaa, hh:mm"; a virgula sai para casar o formato
  // do caso 3 e nao criar duas grafias para a mesma informacao.
  return `${BRT_FORMAT.format(instante).replace(",", "")} BRT`;
}

export interface AffiliateBlockView {
  /** Ha linhas para renderizar? */
  hasRows: boolean;
  /** Mensagem de estado quando nao ha linhas; `null` quando ha. */
  emptyMessage: string | null;
  /** Tom do aviso de estado. */
  emptyTone: AffiliateTone;
  /** Competencias no cabecalho, ja formatadas. */
  monthsLabel: string;
  /** Aviso de cobertura, quando alguma competencia esta incompleta. */
  coverageWarning: string | null;
  /** `null` quando frescor nao se aplica — ver `describeFreshness`. */
  freshnessLabel: string | null;
}

const PERIOD_MESSAGES: Record<AffiliatePeriodStatus, string | null> = {
  complete_month: null,
  complete_months: null,
  // Rule: um numero parcial pareceria comparavel a um mes fechado. Entao nao
  // se exibe numero nenhum — nem com marcador visual, que ainda seria um
  // numero na tela ao lado de meses completos.
  partial_month:
    "Competência em aberto: os valores ainda maturam na fonte e não são exibidos.",
  not_month_aligned:
    "O período selecionado não fecha mês(es) de competência. Selecione mês(es) completo(s) para ver os lançamentos.",
};

/**
 * Deriva o estado de exibicao do bloco. Le as quatro dimensoes de forma
 * INDEPENDENTE: um bloco pode ser `available` e ainda assim ter cobertura
 * incompleta, ou ter periodo completo e estar em `error`.
 */
export function deriveAffiliateBlockView(
  block: AffiliateCostsBlock,
): AffiliateBlockView {
  const monthsLabel = block.months_included.map(formatRefMonth).join(", ");
  const freshnessLabel = describeFreshness(block);
  const coverageWarning = coverageNote(block.coverage_status);

  let emptyMessage: string | null = null;
  let emptyTone: AffiliateTone = "muted";

  if (block.availability_status === "error") {
    // Nota FIXA vinda do backend: nunca SQL, DSN, host nem mensagem de driver.
    emptyMessage = block.limitation_note;
    emptyTone = "warning";
  } else if (block.availability_status === "no_eligible_brand") {
    emptyMessage = "Nenhuma marca elegível no filtro selecionado.";
  } else if (block.availability_status === "unavailable_no_source") {
    emptyMessage = "Fonte de custo de afiliados não confirmada para os canais selecionados.";
  } else if (block.rows.length === 0) {
    emptyMessage =
      PERIOD_MESSAGES[block.period_status] ??
      "Sem lançamentos de afiliado no recorte selecionado.";
  }

  return {
    hasRows: block.rows.length > 0,
    emptyMessage,
    emptyTone,
    monthsLabel,
    coverageWarning,
    freshnessLabel,
  };
}

export function coverageNote(status: AffiliateCoverageStatus): string | null {
  if (status === "incomplete_brand_coverage") {
    return "Cobertura incompleta: alguma competência tem menos marcas que o esperado. As marcas ausentes NÃO foram preenchidas com zero.";
  }
  return null;
}

/** Rotulo de cobertura por LINHA, com a contagem medida. */
export function rowCoverageNote(row: AffiliateCostRow): string | null {
  if (row.coverage_status !== "incomplete_brand_coverage") return null;
  const n = row.brands_present_in_month;
  return n == null
    ? "Competência incompleta"
    : `Competência com ${n} marca(s) medida(s)`;
}

/**
 * Status por canal para exibicao, na ordem em que o backend mandou.
 *
 * ML e Shopee sao "Dados indisponíveis", NUNCA "Não aplicável": afiliado
 * existe nesses canais — o que falta e' fonte confirmada. Chamar de "não
 * aplicável" afirmaria que o custo nao existe.
 */
export function describeChannelStatus(
  status: AffiliateChannelStatus,
): { label: string; text: string; tone: AffiliateTone } {
  const label = channelLabel(status.channel);
  switch (status.availability_status) {
    case "available":
      return { label, text: "Dados disponíveis", tone: "value" };
    case "unavailable_no_source":
      return { label, text: "Dados indisponíveis", tone: "warning" };
    case "no_eligible_brand":
      return { label, text: "Sem marca elegível", tone: "muted" };
    case "error":
      return { label, text: "Leitura indisponível", tone: "warning" };
  }
}

/**
 * Linhas do drilldown. Reusa as MESMAS linhas ja carregadas no bloco — o
 * dialogo nao dispara fetch adicional, entao nao pode divergir da tabela.
 */
export interface AffiliateDrilldownLine {
  brand: string;
  refMonth: string;
  components: { label: string; cell: AffiliateCell }[];
  coverageNote: string | null;
}

export function buildAffiliateDrilldown(
  rows: AffiliateCostRow[],
): AffiliateDrilldownLine[] {
  return rows.map((row) => ({
    brand: brandLabel(row.brand),
    refMonth: formatRefMonth(row.ref_month),
    components: AFFILIATE_COMPONENT_ORDER.map((key) => ({
      label: AFFILIATE_COMPONENT_LABELS[key],
      cell: formatSignedBrl(row[key]),
    })),
    coverageNote: rowCoverageNote(row),
  }));
}
