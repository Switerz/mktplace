// Formatação decimal pt-BR da Inteligência (Gate V3-1A, correção pós-QA).
//
// Achado do QA visual: todo percentual, ROAS e nota renderizava com PONTO
// decimal — `12.0x`, `0.0%`, `8.0%` — porque vinham de `Number.toFixed()`, que
// é insensível a locale. Na mesma tela, `fmtBrl`/`fmtNumber` usam pt-BR, então
// não havia um único número com vírgula: a página misturava duas convenções
// numéricas numa interface brasileira.
//
// Estes helpers cuidam SOMENTE da apresentação. Nenhum cálculo, métrica,
// threshold ou arredondamento de negócio muda: a quantidade de casas decimais
// é a mesma que o `toFixed` anterior produzia.
//
// Não altero `src/lib/formatters.ts`: `fmtPct` de lá é formatador de DELTA
// (acrescenta `+` para positivos) e é compartilhado por outras telas.
//
// Módulo puro, sem React.

/** Decimal em pt-BR com casas fixas — `12` → `"12,0"`. */
export function decBr(value: number, decimals = 1): string {
  return value.toLocaleString("pt-BR", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/** Percentual em pt-BR, sem sinal de delta — `8` → `"8,0%"`. */
export function pctBr(value: number, decimals = 1): string {
  return `${decBr(value, decimals)}%`;
}

/** ROAS em pt-BR — `12` → `"12,0x"`. */
export function roasBr(value: number): string {
  return `${decBr(value, 1)}x`;
}

/**
 * `revenue_share_pct` chega como FRAÇÃO no contrato (0,1 = 10%), ao contrário
 * de `ad_acos_pct` e `cancel_rate_pct`, que já vêm em percentual. A conversão
 * fica nomeada aqui para que a diferença não se perca no meio do JSX.
 */
export function fractionAsPctBr(fraction: number, decimals = 1): string {
  return pctBr(fraction * 100, decimals);
}
