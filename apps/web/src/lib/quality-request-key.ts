// Identidade da requisicao da pagina Qualidade (Gate U5) — mesmo padrao
// "Finding 2" ja adotado em Canais/Financeiro/Regioes (Gate U3/U4), extraida
// para ser testavel sem React/JSDOM.

export interface QualityRequestKeyInput {
  channels: readonly string[];
  brands: readonly string[];
  dateFrom: string;
  dateTo: string;
  compare: boolean;
  retryKey: number;
}

export function buildQualityRequestKey(input: QualityRequestKeyInput): string {
  return `${input.channels.join(",")}|${input.brands.join(",")}|${input.dateFrom}|${input.dateTo}|${input.compare}|${input.retryKey}`;
}
