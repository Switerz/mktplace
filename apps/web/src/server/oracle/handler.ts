/**
 * Handler HTTP do endpoint MCP, com o ambiente INJETADO.
 *
 * A rota do App Router e' uma casca fina que passa `process.env` para ca.
 * Manter a decisao aqui, sem ler variavel global, torna a fronteira
 * fail-closed testavel de forma determinista — sem mutar `process.env` entre
 * testes, que e' fonte classica de teste intermitente.
 */
import { createMcpHandler } from "@modelcontextprotocol/server";

import { evaluateAccess, type AccessEnv } from "./access.ts";
import { buildOracleServer } from "./server.ts";

/**
 * Resposta unica de negacao: 404, corpo generico.
 *
 * 404 (e nao 401/403) e' deliberado enquanto nao ha autenticacao real: a rota
 * simplesmente NAO EXISTE para quem nao deveria alcanca-la, e a resposta nao
 * revela se ela esta desabilitada, mal configurada ou protegida. Quando houver
 * auth de verdade, 401 com WWW-Authenticate passa a fazer sentido para
 * "credencial ausente"; hoje seria anunciar uma porta fechada.
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
};

export async function handleMcpRequest(
  request: Request,
  env: AccessEnv,
  opts: HandleOptions = {},
): Promise<Response> {
  // FAIL-CLOSED: decidir ANTES de instanciar o MCP e ANTES de qualquer
  // chamada ao FastAPI. Numa negacao, nenhuma requisicao upstream acontece.
  const decision = evaluateAccess(env);
  if (!decision.allowed) return notFoundResponse();

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
