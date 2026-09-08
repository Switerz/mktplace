// Apresentacao do bloco "Descontos e subsidios do pedido — TikTok Shop" da aba
// Canais (contrato §28 de docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md). Modulo
// PURO, sem React, para ser testavel isoladamente — mesmo padrao de
// canais-affiliate-costs.ts.
//
// O QUE ESTE MODULO NAO FAZ, E POR QUE
// -----------------------------------
// - Nao soma o desconto da marca com o subsidio da plataforma. Sao dinheiros
//   de DONOS diferentes: um sai do bolso da marca e reduz a receita dela; o
//   outro e' ressarcido pelo TikTok e nao reduz. Somar funde dois caixas.
// - Nao produz total, margem, lucro, receita liquida, caixa, retorno nem
//   valor "recuperado".
// - Nao aplica Math.abs() e nao inverte sinal: a taxa da marca sai negativa e
//   a da plataforma, positiva. E' o sinal que diz quem financia o que.
// - Nao mistura cancelados com comerciais: populacoes disjuntas.
// - Nao converte ausencia em zero, nem zero em ausencia.
// - Nao usa o `refreshedAt` geral da pagina como frescor do bloco.
// - Nao rotula como BRT um timestamp que veio sem offset.

import type {
  DiscountFreshnessStatus,
  TikTokOrderDiscountRow,
  TikTokOrderDiscountsBlock,
} from "./api-client";
// Extensao `.ts` EXPLICITA: e' import de VALOR, e o runtime dos testes
// (`node --test`) nao resolve caminho sem extensao. Mesma convencao de
// `formatters.ts`, `marketplace-filter.ts` e `inteligencia/brands.ts`.
// `brandLabel` e `formatTimestamp` sao reusados, nunca duplicados: duas
// implementacoes de formatacao de fuso divergiriam caladas.
import {
  brandLabel,
  formatTimestamp,
  type AffiliateBlockPhase,
} from "./canais-affiliate-costs.ts";

export const DISCOUNTS_BLOCK_TITLE =
  "Descontos e subsídios do pedido — TikTok Shop";

/**
 * Rotulos dos componentes COMERCIAIS, sempre exibidos separadamente.
 *
 * "Desconto financiado pela marca" e "Subsidio financiado pelo TikTok" dizem o
 * DONO do dinheiro no proprio rotulo. "Desconto" e "subsidio" sozinhos
 * convidariam a somar.
 */
export const DISCOUNT_COMPONENT_LABELS = {
  full_product_value: "Valor cheio dos produtos",
  official_gmv: "GMV oficial",
  seller_discount_signed: "Desconto financiado pela marca",
  platform_subsidy_amount: "Subsídio financiado pelo TikTok",
} as const;

export type DiscountComponentKey = keyof typeof DISCOUNT_COMPONENT_LABELS;

/** Ordem FIXA de exibicao. Nao e' ranking por valor. */
export const DISCOUNT_COMPONENT_ORDER: DiscountComponentKey[] = [
  "full_product_value",
  "official_gmv",
  "seller_discount_signed",
  "platform_subsidy_amount",
];

/** Componentes de CANCELADO — so' aparecem no drill-down, em secao separada. */
export const CANCELLED_COMPONENT_LABELS = {
  cancelled_seller_discount_signed: "Desconto da marca em pedidos cancelados",
  cancelled_platform_subsidy_amount: "Subsídio do TikTok em pedidos cancelados",
} as const;

export type CancelledComponentKey = keyof typeof CANCELLED_COMPONENT_LABELS;

export const CANCELLED_COMPONENT_ORDER: CancelledComponentKey[] = [
  "cancelled_seller_discount_signed",
  "cancelled_platform_subsidy_amount",
];

export const CANCELLED_SECTION_TITLE = "Pedidos cancelados (contexto separado)";

// --- Copy OBRIGATORIA do contrato. Textos fixos, verificados por teste. -----

export const SELLER_COPY =
  "O desconto financiado pela marca sai do bolso da marca e reduz a receita dela.";

export const SUBSIDY_COPY =
  "O subsídio é financiado pelo TikTok Shop e não deve ser somado ao desconto da marca.";

export const RETROACTIVE_COPY =
  "A fonte é um retrato do pedido e pode ser revisada retroativamente.";

export const CANCELLED_COPY =
  "Pedidos cancelados são contexto separado e não integram as vendas comerciais.";

export const MANDATORY_COPY: string[] = [
  SELLER_COPY,
  SUBSIDY_COPY,
  RETROACTIVE_COPY,
  CANCELLED_COPY,
];

export type DiscountTone = "value" | "muted" | "warning";

export interface DiscountCell {
  text: string;
  tone: DiscountTone;
}

/**
 * Formata um valor monetario ASSINADO em BRL, pt-BR, integral.
 *
 * Nao abrevia: um valor contabil em "R$ 1,2M" nao reconcilia com relatorio
 * nenhum. `null` = ausencia de medicao -> travessao. `0` = medido zero ->
 * "R$ 0,00". Trocar um pelo outro inventaria ou apagaria informacao.
 */
export function formatSignedBrl(value: number | null): DiscountCell {
  if (value == null) return { text: "—", tone: "muted" };
  return {
    text: new Intl.NumberFormat("pt-BR", {
      style: "currency",
      currency: "BRL",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value),
    tone: "value",
  };
}

/**
 * Formata uma taxa percentual PRESERVANDO O SINAL.
 *
 * O sinal e' informacao, nao ruido de formatacao: negativo identifica o que a
 * marca financiou, positivo o que a plataforma ressarciu. `signDisplay:
 * "exceptZero"` mantem o `+` explicito no subsidio para que as duas colunas
 * nao pareçam a mesma grandeza.
 */
export function formatSignedPct(value: number | null): DiscountCell {
  if (value == null) return { text: "—", tone: "muted" };
  return {
    text: new Intl.NumberFormat("pt-BR", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
      signDisplay: "exceptZero",
    }).format(value) + "%",
    tone: "value",
  };
}

/** Contagem inteira em pt-BR. `null` nunca chega aqui: a API garante inteiro. */
export function formatCount(value: number): string {
  return new Intl.NumberFormat("pt-BR").format(value);
}

/** Intervalo `YYYY-MM-DD` -> `dd/mm/aaaa a dd/mm/aaaa`. */
export function formatWindow(
  from: string | null,
  to: string | null,
): string | null {
  if (!from || !to) return null;
  return `${formatTimestamp(from)} a ${formatTimestamp(to)}`;
}

/**
 * Frase de frescor PROPRIA do bloco. Fala da CARGA, nunca do dado.
 *
 * `recent_load` diz SOMENTE que o sync rodou ha pouco. A frase evita "atual",
 * "estavel", "maduro" e "fechado": a fonte pode ser revisada retroativamente,
 * e nenhuma dessas palavras seria verdadeira sobre o DADO.
 */
export const RECENT_LOAD_COPY =
  "Carga recente — isso não significa dado estável; a fonte pode ser revisada.";

export const STALE_LOAD_COPY =
  "Carga antiga: pode não refletir revisões recentes da fonte.";

export function describeFreshness(
  block: TikTokOrderDiscountsBlock,
): string | null {
  if (block.freshness_status === "unknown") return null;
  const carga = block.discounts_refreshed_at
    ? `Carga registrada em ${formatTimestamp(block.discounts_refreshed_at)}`
    : "Carga sem registro de data";
  // `source_max_date` e' o maximo do ESCOPO — janela e marcas filtradas —,
  // por isso a frase diz "nesta seleção" e nao "a fonte".
  const ate = block.source_max_date
    ? `; dados desta seleção até ${formatTimestamp(block.source_max_date)}`
    : "";
  const sufixo =
    block.freshness_status === "stale_load"
      ? ` ${STALE_LOAD_COPY}`
      : ` ${RECENT_LOAD_COPY}`;
  return `${carga}${ate}.${sufixo}`;
}

export interface DiscountBlockView {
  hasRows: boolean;
  /** Mensagem de estado quando nao ha linhas; `null` quando ha. */
  emptyMessage: string | null;
  emptyTone: DiscountTone;
  /** Janela efetiva formatada, ou `null`. */
  windowLabel: string | null;
  /** "N dia(s) com dado" — dias medidos, nao tamanho da janela. */
  dayCountLabel: string | null;
  /** `null` quando frescor nao se aplica. */
  freshnessLabel: string | null;
  /** Avisos do backend, na ordem em que vieram. */
  warnings: string[];
  /** Cobertura incompleta merece destaque proprio. */
  coverageIsIncomplete: boolean;
}

/**
 * Mensagem quando `available` mas sem linha nenhuma.
 *
 * Nao existe tabela por `period_status`, ao contrario do bloco mensal de
 * afiliados: la um periodo parcial SUPRIME os valores, porque o grao e' mensal
 * e meio mes pareceria comparavel a um mes fechado. **Aqui o grao e' diario**,
 * entao qualquer recorte devolve uma soma honesta de dias fechados e nenhum
 * `period_status` bloqueia a exibicao. Se nao ha linha, a causa e' ausencia de
 * pedido — nao o formato do periodo.
 */
const SEM_PEDIDO_MESSAGE = "Sem pedidos do TikTok Shop no recorte selecionado.";

export function deriveDiscountBlockView(
  block: TikTokOrderDiscountsBlock,
): DiscountBlockView {
  const windowLabel = formatWindow(block.date_from, block.date_to);
  const freshnessLabel = describeFreshness(block);

  let emptyMessage: string | null = null;
  let emptyTone: DiscountTone = "muted";

  if (block.availability_status === "error") {
    // Nota FIXA do backend: nunca SQL, DSN, host nem mensagem de driver.
    emptyMessage = block.limitation_note;
    emptyTone = "warning";
  } else if (block.availability_status === "no_eligible_brand") {
    emptyMessage = "Nenhuma marca elegível no filtro selecionado.";
  } else if (block.availability_status === "unavailable_no_source") {
    emptyMessage =
      "Descontos de pedido só têm fonte confirmada no TikTok Shop.";
  } else if (block.rows.length === 0) {
    emptyMessage = SEM_PEDIDO_MESSAGE;
  }

  return {
    hasRows: block.rows.length > 0,
    emptyMessage,
    emptyTone,
    windowLabel,
    dayCountLabel:
      block.date_count > 0
        ? `${formatCount(block.date_count)} dia(s) com dado`
        : null,
    freshnessLabel,
    warnings: block.warnings,
    coverageIsIncomplete:
      block.coverage_status === "incomplete_brand_coverage",
  };
}

/**
 * Nota de cobertura, com os NUMEROS da grade observada.
 *
 * "Faltam N chaves" nao significa nada sem saber de quantas, nem de qual
 * grade: a grade e' (dias com atividade) x (marcas com atividade), a mesma do
 * sync. `complete` aqui significa "grade observada completa", jamais
 * "ingestao comprovadamente completa".
 */
export function coverageNote(
  block: TikTokOrderDiscountsBlock,
): string | null {
  if (block.coverage_status !== "incomplete_brand_coverage") return null;
  return (
    `Cobertura incompleta: ${formatCount(block.coverage_missing_keys)} de ` +
    `${formatCount(block.coverage_expected_keys)} chaves (dia × marca) da ` +
    "grade observada não têm linha. Ausência de venda e falha de ingestão " +
    "continuam indistinguíveis com as fontes atuais, e as chaves ausentes " +
    "NÃO foram preenchidas com zero."
  );
}

/** Limite que acompanha `complete`. Nunca afirma ingestão comprovada. */
export const COVERAGE_LIMIT_COPY =
  "Cobertura medida sobre a grade observada (dias × marcas com atividade); não é prova de ingestão completa.";

/** Linha da TABELA por marca — somente componentes comerciais. */
export interface DiscountTableLine {
  brand: string;
  brandKey: string;
  commercialOrders: string;
  cells: { key: DiscountComponentKey; label: string; cell: DiscountCell }[];
  sellerRate: DiscountCell;
  platformRate: DiscountCell;
}

export function buildDiscountTable(
  rows: TikTokOrderDiscountRow[],
): DiscountTableLine[] {
  return rows.map((row) => ({
    brand: brandLabel(row.brand),
    brandKey: row.brand,
    commercialOrders: formatCount(row.commercial_orders),
    cells: DISCOUNT_COMPONENT_ORDER.map((key) => ({
      key,
      label: DISCOUNT_COMPONENT_LABELS[key],
      cell: formatSignedBrl(row[key]),
    })),
    sellerRate: formatSignedPct(row.seller_discount_rate),
    platformRate: formatSignedPct(row.platform_subsidy_rate),
  }));
}

/**
 * Linhas de CANCELADO para o drill-down. Secao separada de proposito: os
 * cancelados nao integram as vendas comerciais, e exibi-los na mesma tabela
 * convidaria a soma-los.
 */
export interface CancelledLine {
  brand: string;
  brandKey: string;
  cancelledOrders: string;
  components: { label: string; cell: DiscountCell }[];
}

export function buildCancelledDrilldown(
  rows: TikTokOrderDiscountRow[],
): CancelledLine[] {
  return rows.map((row) => ({
    brand: brandLabel(row.brand),
    brandKey: row.brand,
    cancelledOrders: formatCount(row.cancelled_orders),
    components: CANCELLED_COMPONENT_ORDER.map((key) => ({
      label: CANCELLED_COMPONENT_LABELS[key],
      cell: formatSignedBrl(row[key]),
    })),
  }));
}

export type { AffiliateBlockPhase as DiscountBlockPhase, DiscountFreshnessStatus };
