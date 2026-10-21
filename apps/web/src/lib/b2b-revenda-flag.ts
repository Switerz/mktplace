/**
 * Feature flag da aba "B2B / Revendedores" — Gate TORRE-B2B-OCTAPRICE.
 *
 * TRES MODOS, FAIL-CLOSED
 * -----------------------
 *   (ausente / qualquer outro valor)  desligada: a rota devolve 404 e o
 *                                     seletor "Lojas proprias | B2B" nem
 *                                     aparece. A tela de lojas proprias fica
 *                                     EXATAMENTE como era.
 *   "fixture"                         dados FICTICIOS, com faixa de aviso em
 *                                     toda a tela. Serve para QA visual sem a
 *                                     credencial da Torre B2B. Nenhuma
 *                                     requisicao sai do navegador.
 *   "api"                             le a nossa API (`/api/v1/b2b-revenda`),
 *                                     que le a Torre B2B. O navegador NUNCA
 *                                     fala com a Torre B2B.
 *
 * "fixture" e' explicito de proposito: se ligar a aba caisse em dado de
 * exemplo por padrao, um deploy com a variavel incompleta mostraria numero
 * inventado com cara de real.
 *
 * DUAS BARREIRAS, como nas demais telas novas: (1) a rota responde 404 com a
 * flag desligada; (2) o seletor some. A API tem a propria flag
 * (`B2B_REVENDA_ENABLED`), desligada por padrao.
 *
 * O acesso a `process.env.NEXT_PUBLIC_*` PRECISA ser literal: o Next substitui
 * a expressao em BUILD por analise estatica do texto.
 */

export type ModoB2BRevenda = "off" | "fixture" | "api";

export const B2B_REVENDA_FLAG = "NEXT_PUBLIC_B2B_REVENDA_MODE";

export function modoDoValor(valor: string | undefined): ModoB2BRevenda {
  if (valor === "fixture") return "fixture";
  if (valor === "api") return "api";
  return "off";
}

/** `override` existe SO' para teste. */
export function modoB2BRevenda(override?: NodeJS.ProcessEnv): ModoB2BRevenda {
  if (override) return modoDoValor(override.NEXT_PUBLIC_B2B_REVENDA_MODE);
  return modoDoValor(process.env.NEXT_PUBLIC_B2B_REVENDA_MODE);
}
