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
 *
 * OBSERVABILIDADE (Gate OM2-E). Cada requisicao faz EXATAMENTE UMA TENTATIVA de
 * emissao de um evento sanitizado, em qualquer caminho de saida, dizendo em que
 * fronteira ela terminou — no maximo um evento persistido, e exatamente um
 * entregue quando o logger funciona. Como logging e' best-effort, uma falha do
 * logger resulta em zero evento, nunca em resposta alterada. O evento nao muda
 * a resposta, nao acrescenta header e nao pode lancar; seu vocabulario e sua
 * sanitizacao vivem em `observability.ts`.
 */
import {
  bearerAuthChallengeResponse,
  createMcpHandler,
  verifyBearerToken,
  type AuthInfo,
} from "@modelcontextprotocol/server";

import { evaluateAccess, type AccessEnv } from "./access.ts";
import {
  classifyBearerShape,
  classifyProtocolStatus,
  emitOracleMcpEvent,
  newCorrelationId,
  normalizeHttpMethod,
  observedJsonRpcMethod,
  type OracleFailureCategory,
  type OracleJsonRpcMethod,
  type OracleLogger,
  type OracleOutcome,
  type OraclePhase,
} from "./observability.ts";
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
   * Coletor do evento de observabilidade. Injetavel para que a suite capture
   * os eventos deterministicamente, sem depender de stdout global concorrente.
   * Ausente, escreve uma linha JSON em `console.info`.
   */
  logger?: OracleLogger;
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
  const startedAt = Date.now();
  // Gerado AQUI, sempre. Nenhum identificador vindo do cliente e' aceito.
  const correlationId = newCorrelationId();
  // Normalizado UMA vez: o caminho de emissao nunca volta a tocar a requisicao.
  const httpMethod = normalizeHttpMethod(request.method);
  let alreadyEmitted = false;

  /**
   * Tenta emitir o evento terminal. O guarda `alreadyEmitted` e' o que torna
   * "no maximo uma emissao por requisicao" uma propriedade estrutural, e nao
   * uma disciplina que cada `return` precisa lembrar de manter. A ENTREGA em si
   * e' best-effort: `emitOracleMcpEvent` engole falha do logger de proposito.
   *
   * `httpStatus` e' `null` quando nenhuma `Response` foi observada por esta
   * camada — ver a documentacao do campo em `observability.ts`.
   */
  function emit(
    phase: OraclePhase,
    outcome: OracleOutcome,
    httpStatus: number | null,
    jsonRpcMethod: OracleJsonRpcMethod,
    failureCategory: OracleFailureCategory,
  ): void {
    if (alreadyEmitted) return;
    alreadyEmitted = true;
    emitOracleMcpEvent(
      {
        event: "oracle_mcp_request",
        correlation_id: correlationId,
        phase,
        outcome,
        http_status: httpStatus,
        http_method: httpMethod,
        jsonrpc_method: jsonRpcMethod,
        failure_category: failureCategory,
        duration_ms: Math.max(0, Date.now() - startedAt),
      },
      opts.logger,
    );
  }

  // 1. FRONTEIRA DE AMBIENTE. Numa negacao, nenhuma requisicao upstream
  //    acontece e nada sobre a configuracao e' revelado.
  //
  //    O corpo NAO e' inspecionado aqui: `jsonrpc_method` fica `unknown`. Ler
  //    o payload de uma requisicao que a fronteira acabou de recusar seria
  //    processar entrada nao autenticada so' para enriquecer um log.
  const decision = evaluateAccess(env);
  if (!decision.allowed) {
    const denied = notFoundResponse();
    emit("environment", "denied", denied.status, "unknown", "environment_denied");
    return denied;
  }

  // 2. AUTENTICACAO, antes de instanciar o MCP e antes do backend.
  //    `decision.auth` e' non-null sempre que OAuth esta configurado — e em
  //    contexto hospedado isso e' garantido pela propria fronteira.
  if (decision.auth !== null) {
    const auth = await authenticate(request, decision.auth, opts.verifierOptions ?? {});
    if (auth instanceof Response) {
      // 403 significa token verificado, porem sem a permission: e' autorizacao.
      // Qualquer outra negacao e' autenticacao. A categoria vem da FORMA do
      // bearer, nunca do motivo interno da `jose` — que segue nao observado.
      const isForbidden = auth.status === 403;
      const shape = classifyBearerShape(request.headers.get("authorization"));
      const category: OracleFailureCategory = isForbidden
        ? "insufficient_scope"
        : shape === "jwt_shaped"
          ? "jwt_verification_failed"
          : shape;

      // Tambem aqui o corpo fica intocado: a requisicao nao passou pelo bearer,
      // entao `jsonrpc_method` e' `unknown`. OAuth-first vale para o payload
      // tanto quanto para as tools.
      emit(isForbidden ? "authorization" : "authentication", "denied", auth.status, "unknown", category);
      return auth;
    }
    // O `AuthInfo` verificado NAO e' repassado adiante: as tools sao
    // read-only e single-tenant, e o bearer encerra sua funcao nesta
    // fronteira. Nada do token viaja para o `torre-client`.
  }

  // 3. AUTENTICADO. So' agora o corpo pode ser inspecionado — e ainda assim
  //    apenas o campo `method`, sob o teto de tamanho, por `clone()`. Precisa
  //    acontecer ANTES de `handler.fetch`, que consome o corpo original.
  const jsonRpcMethod = await observedJsonRpcMethod(request);

  // O handler e' criado DENTRO do `try`: se a propria fabrica falhar, a
  // requisicao ainda produz o seu evento, em vez de sumir sem registro.
  let handler: ReturnType<typeof createMcpHandler> | null = null;

  try {
    handler = createMcpHandler(() =>
      buildOracleServer({
        backendBaseUrl: decision.backendBaseUrl,
        fetchImpl: opts.fetchImpl,
      }),
    );

    const response = await handler.fetch(request);
    const { outcome, failureCategory } = classifyProtocolStatus(response.status);
    emit("protocol", outcome, response.status, jsonRpcMethod, failureCategory);
    return response;
  } catch (err) {
    // Registra e RE-LANCA: o tratamento HTTP existente e' preservado tal como
    // esta. Observabilidade nao converte excecao em resposta — e por isso o
    // status vai `null`: nenhuma `Response` foi observada por esta camada.
    const { outcome, failureCategory } = classifyProtocolStatus(null);
    emit("protocol", outcome, null, jsonRpcMethod, failureCategory);
    throw err;
  } finally {
    // Fecha o leg moderno mesmo em erro: nada de transporte pendurado entre
    // invocacoes serverless.
    await handler?.close().catch(() => {
      /* cleanup best-effort: nao pode mascarar a resposta ja produzida */
    });
  }
}
