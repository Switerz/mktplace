/**
 * Feature flag da tela "Full Shopee" — Gate FULL-SH-1D.
 *
 * FAIL-CLOSED: somente a string EXATA "true" liga. Ausencia, string vazia,
 * "1", "TRUE", "yes" ou qualquer outra coisa mantem a superficie desligada.
 * O default e' desligado porque esta tela afirma algo delicado -- um total de
 * Shopee que NAO inclui Kokeshi, a maior marca por volume.
 *
 * DUAS BARREIRAS, E A SEGUNDA NAO SUBSTITUI A PRIMEIRA
 * -----------------------------------------------------
 *   1. a ROTA devolve 404 com a flag desligada (guard no servidor);
 *   2. o item de MENU some.
 *
 * Esconder apenas o menu deixaria a URL direta acessivel -- e nem a aplicacao
 * nem a API tem autenticacao, entao a rota precisa falhar fechada por conta
 * propria.
 *
 * ATIVACAO COORDENADA
 * -------------------
 * Esta flag sozinha nao basta. O backend tem a sua (`SHOPEE_FBS_ENABLED`,
 * tambem default false) e, com ela desligada, o endpoint devolve
 * `status="unavailable"` sem consultar o banco. Ligar apenas o frontend
 * produz uma tela que carrega e nao mostra dado. As duas precisam ser ligadas
 * na MESMA decisao, em gate futuro.
 *
 * `NEXT_PUBLIC_` e' obrigatorio: o Next embute a variavel no bundle em BUILD,
 * nao em runtime. Mudar a flag exige rebuild -- por isso ela e' lida por
 * funcao e nao por constante de modulo, o que mantem os testes capazes de
 * variar o ambiente.
 */

export const SHOPEE_FBS_FLAG = "NEXT_PUBLIC_SHOPEE_FBS_ENABLED";

/** Valor EXATO que liga. Qualquer outro mantem desligado. */
export const VALOR_QUE_LIGA = "true";

/**
 * O acesso a `process.env.NEXT_PUBLIC_SHOPEE_FBS_ENABLED` PRECISA ser literal.
 *
 * O Next substitui a expressao inteira em build, por analise ESTATICA do
 * texto. Ler de uma variavel (`env.NEXT_PUBLIC_...`, com `env` recebido por
 * parametro) NAO e' substituido: no servidor funciona, porque `process.env`
 * existe de verdade; no browser o objeto nao existe e o valor vira
 * `undefined` -- a flag ficava eternamente desligada no cliente, e o item de
 * menu nunca aparecia mesmo com a variavel definida no build.
 *
 * Medido no Gate FULL-SH-1D: a rota respondia 200 e renderizava os dados
 * (guard do servidor via o valor certo), enquanto o menu ficava sem o item.
 *
 * `override` existe SO' para teste, e por isso e' opcional e vem depois.
 */
export function shopeeFbsEnabled(override?: NodeJS.ProcessEnv): boolean {
  if (override) return override.NEXT_PUBLIC_SHOPEE_FBS_ENABLED === VALOR_QUE_LIGA;
  return process.env.NEXT_PUBLIC_SHOPEE_FBS_ENABLED === VALOR_QUE_LIGA;
}

/** Motivo exibido quando a superficie esta desligada por decisao de negocio. */
export const MOTIVO_DESLIGADA =
  "Superficie de FBS da Shopee ainda nao habilitada. A camada de dados existe " +
  "e esta publicada, mas a cobertura e PARCIAL (quatro contas; Kokeshi fora) " +
  "e a ativacao e decisao de negocio.";
