/**
 * Feature flag da tela "Estoque Full" — Gate FULL-SOURCE-3.
 *
 * FLAG PROPRIA, SEPARADA DA DO FULL SHOPEE
 * -----------------------------------------
 * `NEXT_PUBLIC_SHOPEE_FBS_ENABLED` ja' esta LIGADA em producao e governa a
 * tela de DESEMPENHO, cuja fato (019) esta publicada. Esta tela le as fatos
 * de ESTOQUE (021), que nem existem no banco produtivo -- a cabeca do Alembic
 * la' e' 019. Reusar a flag ligada faria a tela nova aparecer, no menu e na
 * URL, no mesmo instante do merge.
 *
 * FAIL-CLOSED: somente a string EXATA "true" liga. Ausencia, "1", "TRUE" ou
 * "yes" mantem desligado.
 *
 * DUAS BARREIRAS, E A SEGUNDA NAO SUBSTITUI A PRIMEIRA
 * -----------------------------------------------------
 *   1. a ROTA devolve 404 com a flag desligada (guard no servidor);
 *   2. o item de MENU some.
 * Esconder apenas o menu deixaria a URL direta acessivel -- e nem a aplicacao
 * nem a API tem autenticacao.
 *
 * ATIVACAO COORDENADA, E AQUI SAO TRES COISAS
 * --------------------------------------------
 *   1. esta flag (build do frontend);
 *   2. `SHOPEE_FBS_STOCK_ENABLED` no backend;
 *   3. as migrations 020 e 021 aplicadas E uma fotografia publicada.
 * Ligar 1 e 2 sem 3 e' seguro: a tela desenha "indisponivel" com o motivo.
 * O que nao pode acontecer em hipotese alguma e' virar zero.
 *
 * `NEXT_PUBLIC_` e' obrigatorio e o acesso precisa ser LITERAL: o Next
 * substitui a expressao em BUILD, por analise estatica do texto. Ler de uma
 * variavel intermediaria nao e' substituido, e no browser o valor viraria
 * `undefined` -- a flag ficaria eternamente desligada no cliente enquanto
 * funcionaria no servidor.
 */

export const ESTOQUE_FULL_FLAG = "NEXT_PUBLIC_SHOPEE_FBS_STOCK_ENABLED";

/** Valor EXATO que liga. Qualquer outro mantem desligado. */
export const VALOR_QUE_LIGA = "true";

/** `override` existe SO' para teste, e por isso e' opcional e vem depois. */
export function estoqueFullEnabled(override?: NodeJS.ProcessEnv): boolean {
  if (override) {
    return override.NEXT_PUBLIC_SHOPEE_FBS_STOCK_ENABLED === VALOR_QUE_LIGA;
  }
  return process.env.NEXT_PUBLIC_SHOPEE_FBS_STOCK_ENABLED === VALOR_QUE_LIGA;
}

/** Motivo exibido quando a superficie esta desligada por decisao de negocio. */
export const MOTIVO_DESLIGADA =
  "Tela de Estoque Full da Shopee ainda nao habilitada. A fotografia de " +
  "estoque nao foi publicada e a ativacao e decisao de negocio.";
