/**
 * Categorias de erro sanitizadas do MCP do Oraculo.
 *
 * Nada que saia daqui pode conter URL completa, host interno, DSN, token,
 * header de autenticacao, corpo HTML (ex: pagina 403 do WAF, que inclui o IP
 * do solicitante) ou stack trace. O texto e' curto, acionavel e estavel.
 */

export type ErrorCategory =
  | "INVALID_INPUT"
  | "SOURCE_UNAVAILABLE"
  | "SOURCE_TIMEOUT"
  | "INVALID_UPSTREAM_RESPONSE"
  | "MISSING_CONFIGURATION"
  | "RATE_LIMITED";

/**
 * Erro de execucao de tool. Vira `isError: true` no resultado MCP — a spec
 * 2026-07-28 reserva erro de protocolo para request malformado, e manda
 * devolver falha de negocio/API como resultado de tool para o modelo poder
 * se corrigir.
 */
export class OracleToolError extends Error {
  readonly category: ErrorCategory;

  constructor(category: ErrorCategory, message: string) {
    super(message);
    this.name = "OracleToolError";
    this.category = category;
  }
}

/** Mensagens fixas — nunca interpoladas com dado vindo do upstream. */
const MESSAGES: Record<ErrorCategory, string> = {
  INVALID_INPUT: "Parametros invalidos para esta tool. Revise os valores e tente novamente.",
  SOURCE_UNAVAILABLE:
    "A fonte de dados da Torre esta indisponivel no momento. Nenhum numero foi retornado — nao trate isso como zero.",
  SOURCE_TIMEOUT:
    "A fonte de dados da Torre demorou demais para responder. Nenhum numero foi retornado — nao trate isso como zero.",
  INVALID_UPSTREAM_RESPONSE:
    "A fonte de dados da Torre respondeu em formato inesperado. Nenhum numero foi retornado — nao trate isso como zero.",
  MISSING_CONFIGURATION: "O conector da Torre nao esta configurado para responder a esta chamada.",
  RATE_LIMITED: "Limite de chamadas atingido. Aguarde antes de repetir a consulta.",
};

export function toolError(category: ErrorCategory, detail?: string): OracleToolError {
  // `detail` e' opcional e SEMPRE de origem interna (string literal do nosso
  // codigo), nunca o corpo do upstream.
  const base = MESSAGES[category];
  return new OracleToolError(category, detail ? `${base} (${detail})` : base);
}

/**
 * Converte qualquer coisa lancada em um erro sanitizado. Erros desconhecidos
 * viram SOURCE_UNAVAILABLE sem repassar a mensagem original — e' exatamente o
 * ponto onde stack trace e corpo HTML seriam vazados se fossemos ingenuos.
 */
export function sanitizeUnknownError(err: unknown): OracleToolError {
  if (err instanceof OracleToolError) return err;
  return toolError("SOURCE_UNAVAILABLE");
}
