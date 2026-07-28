// Identidade da requisicao da pagina Pedidos (Gate U5) — mesmo padrao
// "Finding 2" ja adotado em Canais/Financeiro/Regioes (Gate U3/U4). Sem
// `compare`: o endpoint de pedidos nao aceita comparacao (DateRangeFilter
// usa `hideCompare` nesta tela).

export interface PedidosRequestKeyInput {
  channels: readonly string[];
  brands: readonly string[];
  dateFrom: string;
  dateTo: string;
  retryKey: number;
}

export function buildPedidosRequestKey(input: PedidosRequestKeyInput): string {
  return `${input.channels.join(",")}|${input.brands.join(",")}|${input.dateFrom}|${input.dateTo}|${input.retryKey}`;
}

export interface PedidosCoverageInput {
  showTiktok: boolean;
  showMl: boolean;
  showShopee: boolean;
}

export interface PedidosCoverage {
  /** Somente Shopee selecionada — a fonte nao cobre nenhum canal selecionado;
   * nunca disparar fetch, nunca mostrar KPIs/breakdown/tabela. */
  showShopeeOnly: boolean;
  /** Shopee selecionada junto de TikTok e/ou ML — os numeros exibidos
   * refletem so os canais suportados, nunca Shopee; aviso obrigatorio. */
  showShopeeMixed: boolean;
}

/** Decide o estado de cobertura Shopee da pagina Pedidos (Task 3) a partir
 * da selecao de canais — extraida para ser testavel sem React/JSDOM. */
export function computePedidosCoverage(input: PedidosCoverageInput): PedidosCoverage {
  const showShopeeOnly = input.showShopee && !input.showTiktok && !input.showMl;
  const showShopeeMixed = input.showShopee && (input.showTiktok || input.showMl);
  return { showShopeeOnly, showShopeeMixed };
}
