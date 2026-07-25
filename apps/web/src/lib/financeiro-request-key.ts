// Identidade da requisicao da pagina Financeiro (Gate U4) — mesmo padrao
// "Finding 2" adotado em Canais (Gate U3), extraida para ser testavel sem
// React/JSDOM.

export interface FinanceiroRequestKeyInput {
  channels: readonly string[];
  brands: readonly string[];
  dateFrom: string;
  dateTo: string;
  compare: boolean;
  retryKey: number;
}

export function buildFinanceiroRequestKey(input: FinanceiroRequestKeyInput): string {
  return `${input.channels.join(",")}|${input.brands.join(",")}|${input.dateFrom}|${input.dateTo}|${input.compare}|${input.retryKey}`;
}
