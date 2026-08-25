/**
 * Observabilidade sanitizada do endpoint MCP — no maximo um evento por requisicao.
 *
 * O CONTRATO, com precisao: cada requisicao faz **exatamente uma tentativa de
 * emissao**, e no maximo um evento e' persistido. Com um logger funcional, isso
 * significa exatamente um evento entregue. Mas logging aqui e' best-effort por
 * decisao explicita — falha do logger ou do provedor nunca pode afetar a rota —,
 * entao uma falha de entrega resulta em ZERO evento persistido, e nao em erro.
 * Nao ha retry nem fallback: preferimos perder um log a degradar a resposta.
 *
 * Existe por um motivo estreito: descobrir em qual fronteira a conexao real do
 * Claude.ai termina (ambiente, autenticacao, autorizacao ou protocolo), sem
 * nunca expor token, claim, identidade ou dado interno.
 *
 * REGRAS DE PRIVACIDADE, inegociaveis. O evento carrega SOMENTE vocabulario
 * fechado gerado por este modulo. Nao existe caminho de codigo que copie para
 * dentro dele: header `Authorization`, access token ou qualquer segmento dele,
 * tamanho do token, header/payload do JWT, `kid`, `iss`, `aud`, `sub`, `azp`,
 * scope ou permissions recebidos, e-mail, nome, IP, user-agent, cookie, Client
 * ID/Secret, authorization code, refresh token, DSN, mensagem bruta da `jose`
 * ou do Auth0, corpo da requisicao, argumentos de tool ou resposta de tool.
 *
 * A classificacao de bearer usa apenas TRES sinais estruturais — presenca do
 * header, presenca do esquema `Bearer` e quantidade de segmentos separados por
 * ponto — e devolve unicamente um rotulo do enum. O conteudo nunca e' lido.
 */
import { randomUUID } from "node:crypto";

/** Fase em que a requisicao terminou. */
export type OraclePhase = "environment" | "authentication" | "authorization" | "protocol";

/**
 * Desfecho terminal.
 *
 * `allowed` faz parte do vocabulario acordado, mas nao e' emitido: como existe
 * no maximo UM evento por requisicao, e ele e' sempre terminal, uma requisicao
 * autorizada resolve para `completed` ou `failed` na fase `protocol`.
 */
export type OracleOutcome = "allowed" | "denied" | "completed" | "failed";

/** Categoria interna de falha. Nunca deriva de texto de biblioteca. */
export type OracleFailureCategory =
  | "none"
  | "environment_denied"
  | "bearer_missing"
  | "token_malformed_or_opaque"
  | "jwt_verification_failed"
  | "insufficient_scope"
  | "unexpected_redirect"
  | "rate_limited"
  | "protocol_rejected"
  | "internal_error";

/**
 * Vocabulario FECHADO de metodo HTTP.
 *
 * A rota exporta apenas GET, POST e DELETE. Qualquer outro verbo — incluindo um
 * sintetico — colapsa em `OTHER`, para que nenhum valor cru vindo da requisicao
 * entre no evento.
 */
export type OracleHttpMethod = "GET" | "POST" | "DELETE" | "OTHER";

const KNOWN_HTTP_METHODS: ReadonlySet<string> = new Set(["GET", "POST", "DELETE"]);

/** Normalizador puro. Fora da allowlist, `OTHER` — nunca o verbo recebido. */
export function normalizeHttpMethod(raw: string | undefined): OracleHttpMethod {
  if (typeof raw !== "string") return "OTHER";
  const upper = raw.toUpperCase();
  return KNOWN_HTTP_METHODS.has(upper) ? (upper as OracleHttpMethod) : "OTHER";
}

/** Allowlist FECHADA. Metodo fora dela nunca e' registrado pelo nome cru. */
export type OracleJsonRpcMethod =
  | "initialize"
  | "notifications/initialized"
  | "tools/list"
  | "tools/call"
  | "unknown";

export type OracleMcpEvent = {
  event: "oracle_mcp_request";
  correlation_id: string;
  phase: OraclePhase;
  outcome: OracleOutcome;
  /**
   * Status da `Response` que ESTA camada observou.
   *
   * `null` significa exatamente uma coisa: **nenhuma `Response` foi observada
   * aqui** — o caminho terminou em excecao relancada, e quem produz o status
   * final e' a camada acima (Next/Vercel). Nao e' status zero, nem
   * "desconhecido convertido em 500": fabricar 500 diria que este codigo viu
   * uma resposta que nunca existiu.
   */
  http_status: number | null;
  http_method: OracleHttpMethod;
  jsonrpc_method: OracleJsonRpcMethod;
  failure_category: OracleFailureCategory;
  duration_ms: number;
};

/**
 * Decisao de classificacao do leg de protocolo. Pura, para ser testada direto.
 *
 * `null` = excecao sem `Response` observada. Nos demais casos, cada faixa tem
 * semantica propria: um 3xx nao e' sucesso (o endpoint MCP nunca redireciona,
 * entao um redirect indica proxy ou reescrita no caminho), e 429 e' limite de
 * taxa, nao rejeicao de protocolo.
 */
export function classifyProtocolStatus(status: number | null): {
  outcome: OracleOutcome;
  failureCategory: OracleFailureCategory;
} {
  if (status === null) return { outcome: "failed", failureCategory: "internal_error" };
  if (status >= 200 && status < 300) return { outcome: "completed", failureCategory: "none" };
  if (status >= 300 && status < 400) return { outcome: "failed", failureCategory: "unexpected_redirect" };
  if (status === 429) return { outcome: "failed", failureCategory: "rate_limited" };
  if (status >= 400 && status < 500) return { outcome: "failed", failureCategory: "protocol_rejected" };
  if (status >= 500) return { outcome: "failed", failureCategory: "internal_error" };
  // 1xx nunca chega aqui como resposta terminal; classificado como anomalia.
  return { outcome: "failed", failureCategory: "protocol_rejected" };
}

export type OracleLogger = (event: OracleMcpEvent) => void;

const ALLOWED_JSONRPC_METHODS: ReadonlySet<string> = new Set([
  "initialize",
  "notifications/initialized",
  "tools/list",
  "tools/call",
]);

/**
 * Teto conservador para o corpo que a OBSERVABILIDADE aceita inspecionar.
 *
 * Nao e' limite de requisicao: o handler real continua processando o corpo
 * inteiro. Acima disto (ou sem `content-length` confiavel) o metodo vira
 * `unknown` — observabilidade nunca pode virar vetor de custo ou de memoria.
 */
export const MAX_OBSERVED_BODY_BYTES = 64 * 1024;

/**
 * Le APENAS o campo `method` do corpo JSON-RPC, e so' se ele couber no teto.
 *
 * Usa `request.clone()`: o corpo original permanece intacto para o handler.
 * Qualquer anomalia — metodo nao-POST, tamanho ausente/insuguro, corpo nao-JSON,
 * array, `method` ausente ou fora da allowlist — resolve para `unknown`.
 * `params`, `id`, `_meta` e o corpo em si nunca saem daqui.
 */
export async function observedJsonRpcMethod(request: Request): Promise<OracleJsonRpcMethod> {
  try {
    if (request.method !== "POST") return "unknown";

    // Teto ANTES de tocar o corpo: sem tamanho declarado e confiavel, desiste.
    const declared = request.headers.get("content-length");
    if (declared === null) return "unknown";
    const size = Number(declared);
    if (!Number.isInteger(size) || size <= 0 || size > MAX_OBSERVED_BODY_BYTES) return "unknown";

    const parsed: unknown = JSON.parse(await request.clone().text());
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return "unknown";

    const method = (parsed as { method?: unknown }).method;
    if (typeof method !== "string") return "unknown";

    // Allowlist: um metodo desconhecido NUNCA e' registrado pelo nome.
    return ALLOWED_JSONRPC_METHODS.has(method) ? (method as OracleJsonRpcMethod) : "unknown";
  } catch {
    return "unknown";
  }
}

/** Forma estrutural do bearer. Nao revela conteudo nem tamanho. */
export type BearerShape = "bearer_missing" | "token_malformed_or_opaque" | "jwt_shaped";

/**
 * Classifica o header `Authorization` por ESTRUTURA apenas.
 *
 * Ausente, vazio, esquema diferente de `Bearer` ou credencial vazia contam como
 * `bearer_missing` — nao ha bearer para avaliar. Havendo credencial, o unico
 * teste e' a contagem de segmentos: exatamente tres e' formato de JWS compacto
 * (`jwt_shaped`); qualquer outra coisa e' opaca ou malformada.
 *
 * O valor do header NAO e' guardado, medido nem devolvido.
 */
export function classifyBearerShape(authorizationHeader: string | null): BearerShape {
  if (authorizationHeader === null) return "bearer_missing";

  const trimmed = authorizationHeader.trim();
  if (trimmed.length === 0) return "bearer_missing";

  const parts = trimmed.split(/\s+/);
  const scheme = parts[0];
  if (scheme === undefined || scheme.toLowerCase() !== "bearer") return "bearer_missing";

  const credential = parts.slice(1);
  if (credential.length === 0) return "bearer_missing";
  // Credencial com espaco nao e' um bearer valido; classifica como malformada.
  if (credential.length > 1) return "token_malformed_or_opaque";

  return credential[0].split(".").length === 3 ? "jwt_shaped" : "token_malformed_or_opaque";
}

/** `correlation_id` SEMPRE gerado aqui. Nenhum valor do cliente e' aceito. */
export function newCorrelationId(): string {
  return randomUUID();
}

const defaultLogger: OracleLogger = (event) => {
  // Uma unica linha estruturada; sem provider, fila, endpoint ou dependencia.
  console.info(JSON.stringify(event));
};

/**
 * Emite o evento. Falha de logging NUNCA pode afetar a resposta — por isso o
 * `catch` vazio e' deliberado, e nao um engolir descuidado de erro.
 */
export function emitOracleMcpEvent(event: OracleMcpEvent, logger?: OracleLogger): void {
  try {
    (logger ?? defaultLogger)(event);
  } catch {
    /* observabilidade e' best-effort: jamais derruba a rota */
  }
}
