/**
 * AUTH-GOOGLE-6 — textos da tela de login. Sem React: testavel direto.
 */
export const MENSAGEM_SESSAO_EXPIRADA = "Sua sessão expirou. Entre de novo para continuar.";

const MENSAGEM_FALHA = "Falha no login. Tente de novo.";

export function mensagemDeErroLogin(status: number, corpo: unknown): string {
  const c = (corpo && typeof corpo === "object" ? corpo : {}) as { error?: unknown; message?: unknown };
  if (status === 403 && c.error === "dominio_nao_permitido") {
    return typeof c.message === "string" && c.message ? c.message : MENSAGEM_FALHA;
  }
  if (status === 403 && c.error === "email_nao_verificado") {
    return "Seu e-mail Google não está verificado.";
  }
  if (status === 401) return "Não foi possível validar sua conta Google. Tente de novo.";
  return MENSAGEM_FALHA;
}
