/**
 * Fabrica do servidor MCP do Oraculo.
 *
 * Stateless por construcao: uma instancia de `McpServer` por requisicao, sem
 * nenhum estado compartilhado entre invocacoes alem do contador de rate limit
 * (que e' aproximado por design e esta documentado como tal).
 */
import { McpServer } from "@modelcontextprotocol/server";

import { sanitizeUnknownError, toolError } from "./errors.ts";
import { DEFAULT_RATE_LIMIT, SlidingWindowRateLimiter, type RateLimitConfig } from "./rate-limit.ts";
import { ORACLE_TOOLS, type ToolDeps } from "./tools.ts";
import { TorreClient } from "./torre-client.ts";

export const SERVER_INFO = { name: "torre-marketplace", version: "0.1.0" } as const;

export type BuildServerOptions = {
  backendBaseUrl: string;
  /** Injetavel para teste — sem isso a suite dependeria de rede. */
  fetchImpl?: typeof fetch;
  now?: () => Date;
  rateLimiter?: SlidingWindowRateLimiter;
  rateLimitConfig?: RateLimitConfig;
  /** Chave de rate limit; single-tenant no piloto. */
  rateLimitKey?: string;
  timeoutMs?: number;
};

/**
 * Limitador compartilhado do processo. Em serverless cada instancia tem o
 * seu, entao o limite e' por instancia — aproximado, nunca exato.
 */
const processLimiter = new SlidingWindowRateLimiter(DEFAULT_RATE_LIMIT);

export function buildOracleServer(opts: BuildServerOptions): McpServer {
  const server = new McpServer(SERVER_INFO);

  const client = new TorreClient({
    baseUrl: opts.backendBaseUrl,
    fetchImpl: opts.fetchImpl,
    timeoutMs: opts.timeoutMs,
  });
  const deps: ToolDeps = { client, now: opts.now ?? (() => new Date()) };
  const limiter = opts.rateLimiter ?? processLimiter;
  const limitKey = opts.rateLimitKey ?? "default";

  for (const tool of ORACLE_TOOLS) {
    server.registerTool(
      tool.name,
      {
        title: tool.config.title,
        description: tool.config.description,
        // `as never` apenas para reconciliar os generics do SDK com os
        // schemas Zod v4 — o schema em si e' o do contrato, sem afrouxamento.
        inputSchema: tool.config.inputSchema as never,
        outputSchema: tool.config.outputSchema as never,
        annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
      },
      (async (args: unknown) => {
        try {
          // Rate limit ANTES do adapter: excedente nunca toca o Render.
          const verdict = limiter.check(limitKey, deps.now().getTime());
          if (!verdict.allowed) {
            throw toolError(
              "RATE_LIMITED",
              `tente novamente em ${verdict.retryAfterSeconds}s`,
            );
          }
          return await tool.run(args, deps);
        } catch (err) {
          // Erro de validacao do Zod tambem chega aqui: vira INVALID_INPUT
          // com texto acionavel, sem stack trace e sem eco do valor recebido.
          const isZod =
            typeof err === "object" && err !== null && "issues" in (err as object);
          const safe = isZod ? toolError("INVALID_INPUT") : sanitizeUnknownError(err);
          return {
            content: [{ type: "text" as const, text: safe.message }],
            isError: true,
          };
        }
      }) as never,
    );
  }

  return server;
}
