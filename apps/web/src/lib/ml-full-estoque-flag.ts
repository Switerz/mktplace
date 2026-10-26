/**
 * Feature flag da tela "Estoque Full Mercado Livre" — ML-FULL-STOCK-API-1.
 *
 * FLAG PROPRIA. Nao reaproveita:
 *   - a do "Full Mercado Livre" (`/full-ml`), que mede MODALIDADE DE ENVIO do
 *     pedido e ja' esta' no ar;
 *   - a do "Estoque Full Shopee" (`NEXT_PUBLIC_SHOPEE_FBS_STOCK_ENABLED`), ja'
 *     ligada em producao.
 * Compartilhar faria esta tela nascer visivel sobre fatos (migration 029) que
 * nem existem no banco produtivo.
 *
 * FAIL-CLOSED: somente a string EXATA "true" liga.
 *
 * DUAS BARREIRAS: a rota devolve 404 (guard no servidor) e o item de menu
 * some. ATIVACAO COORDENADA: esta flag + `ML_FULL_STOCK_API_ENABLED` no
 * backend + migration 029 aplicada + publicador rodado. Ligar as flags sem a
 * 029 e' seguro: a tela desenha "indisponivel" com o motivo, nunca zero.
 *
 * Acesso LITERAL a `process.env.NEXT_PUBLIC_*`: o Next substitui em build por
 * analise estatica do texto.
 */

export const ML_FULL_ESTOQUE_FLAG = "NEXT_PUBLIC_ML_FULL_STOCK_ENABLED";

export const VALOR_QUE_LIGA = "true";

/** `override` existe so' para teste. */
export function mlFullEstoqueEnabled(override?: NodeJS.ProcessEnv): boolean {
  if (override) {
    return override.NEXT_PUBLIC_ML_FULL_STOCK_ENABLED === VALOR_QUE_LIGA;
  }
  return process.env.NEXT_PUBLIC_ML_FULL_STOCK_ENABLED === VALOR_QUE_LIGA;
}
