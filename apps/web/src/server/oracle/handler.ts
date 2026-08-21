/**
 * Handler HTTP do endpoint MCP, com o ambiente INJETADO.
 *
 * A rota do App Router e' uma casca fina que passa `process.env` para ca.
 * Manter a decisao aqui, sem ler variavel global, torna a fronteira
 * fail-closed testavel de forma determinista — sem mutar `process.env` entre
 * testes, que e' fonte classica de teste intermitente.
 *
 * ORDEM DE EXECUCAO (Gate OM2), inegociavel:
 *   1. fronteira de ambiente  -> 404 generico quando nao elegivel;
 *   2. verificacao do bearer  -> 401/403 com WWW-Authenticate;
 *   3. so entao o MCP e' instanciado e as tools ficam alcancaveis;
 *   4. so entao o FastAPI pode ser chamado.
 * Uma chamada sem autenticacao NAO produz trafego para o backend.
 */
import {
  bearerAuthChallengeResponse,
  createMcpHandler,
  verifyBearerToken,
  type AuthInfo,
} from "@modelcontextprotocol/server";

import { evaluateAccess, type AccessEnv } from "./access.ts";
import {
  createAuth0Verifier,
  protectedResourceMetadataUrl,
  type OAuthConfig,
  type VerifierOptions,
} from "./oauth.ts";
import { buildOracleServer } from "./server.ts";

/**
 * Resposta unica de negacao de AMBIENTE: 404, corpo generico.
 *
 * 404 (e nao 401) e' deliberado quando a rota nao esta elegivel: ela
 * simplesmente NAO EXISTE para quem nao deveria alcanca-la, e a resposta nao
 * revela se esta desabilitada, mal configurada ou protegida.
 *
 * O 401 aparece somente quando a rota ESTA habilitada e configurada — ali
 * anunciar a porta e' correto e necessario, porque e' o que permite ao cliente
 * descobrir o authorization server (RFC 9728).
 */
export function notFoundResponse(): Response {
  return new Response(JSON.stringify({ error: "not_found" }), {
    status: 404,
    headers: { "content-type": "application/json" },
  });
}

export type HandleOptions = {
  /** Injetavel para teste; em producao o adapter usa o `fetch` global. */
  fetchImpl?: typeof fetch;
  /**
   * Resolvedor de chaves para a verificacao do JWT. Injetavel para que os
   * testes usem chaves sinteticas em runtime, sem rede e sem PEM versionado.
   */
  verifierOptions?: VerifierOptions;
};

/**
 * Garante que o desafio 401/403 carregue `scope`.
 *
 * O `scope` no `WWW-Authenticate` e' o que controla quais scopes o cliente do
 * Claude solicita; sem ele, o cliente cai no `scopes_supported` do documento
 * PRM. Emitimos os dois — o desafio e o documento — em vez de depender de um.
 */
function withScopeChallenge(response: Response, cfg: OAuthConfig): Response {
  const header = response.headers.get("www-authenticate");
  if (header === null || /(^|[,\s])scope=/.test(header)) return response;

  const headers = new Headers(response.headers);
  headers.set("www-authenticate", `${header}, scope="${cfg.requiredScope}"`);
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

/**
 * Exige e valida o bearer. Devolve o `AuthInfo` verificado, ou a `Response` de
 * desafio pronta (401 `invalid_token` / 403 `insufficient_scope`).
 *
 * Nenhum detalhe interno vaza: o corpo e' o JSON de erro OAuth padrao, e o
 * texto de diagnostico e' sempre string literal do nosso codigo.
 */
async function authenticate(
  request: Request,
  cfg: OAuthConfig,
  opts: VerifierOptions,
): Promise<AuthInfo | Response> {
  const bearerOptions = {
    verifier: createAuth0Verifier(cfg, opts),
    requiredScopes: [cfg.requiredScope],
    resourceMetadataUrl: protectedResourceMetadataUrl(cfg),
  };

  try {
    return await verifyBearerToken(request.headers.get("authorization"), bearerOptions);
  } catch (err) {
    return withScopeChallenge(bearerAuthChallengeResponse(err, bearerOptions), cfg);
  }
}

export async function handleMcpRequest(
  request: Request,
  env: AccessEnv,
  opts: HandleOptions = {},
): Promise<Response> {
  // 1. FRONTEIRA DE AMBIENTE. Numa negacao, nenhuma requisicao upstream
  //    acontece e nada sobre a configuracao e' revelado.
  const decision = evaluateAccess(env);
  if (!decision.allowed) return notFoundResponse();

  // 2. AUTENTICACAO, antes de instanciar o MCP e antes do backend.
  //    `decision.auth` e' non-null sempre que OAuth esta configurado — e em
  //    contexto hospedado isso e' garantido pela propria fronteira.
  if (decision.auth !== null) {
    const auth = await authenticate(request, decision.auth, opts.verifierOptions ?? {});
    if (auth instanceof Response) return auth;
    // O `AuthInfo` verificado NAO e' repassado adiante: as tools sao
    // read-only e single-tenant, e o bearer encerra sua funcao nesta
    // fronteira. Nada do token viaja para o `torre-client`.
  }

  // 3. Somente agora o MCP existe e as tools ficam alcancaveis.
  const handler = createMcpHandler(() =>
    buildOracleServer({
      backendBaseUrl: decision.backendBaseUrl,
      fetchImpl: opts.fetchImpl,
    }),
  );

  try {
    return await handler.fetch(request);
  } finally {
    // Fecha o leg moderno mesmo em erro: nada de transporte pendurado entre
    // invocacoes serverless.
    await handler.close().catch(() => {
      /* cleanup best-effort: nao pode mascarar a resposta ja produzida */
    });
  }
}
