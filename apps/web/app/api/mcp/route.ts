/**
 * Endpoint MCP da Torre — Streamable HTTP, stateless, OAuth 2.1 resource server.
 *
 * ESTADO (Gate OM2): **Producao na Vercel e' elegivel SOMENTE com todos os
 * guardrails presentes** — OAuth completo (issuer Auth0 canonico, audience
 * exata desta rota, permission `oracle:read`), `ORACLE_MCP_ENABLED=1` e backend
 * allowlisted. Faltando qualquer um, a rota responde **404 generico**.
 *
 * **Preview, custom environments e qualquer deployment Vercel que nao seja
 * producao continuam NEGADOS**, mesmo com configuracao completa. A
 * classificacao usa os sinais explicitos da Vercel, nunca `NODE_ENV` — a
 * plataforma executa Preview com `NODE_ENV=production`, e decidir por
 * `NODE_ENV` deixaria Preview exposto (ver `classifyEnv`).
 *
 * Com a rota elegivel, a autenticacao e' obrigatoria: **401** sem credencial
 * valida, **403** sem `oracle:read`, ambos antes de instanciar as tools.
 *
 * O runtime Node e' obrigatorio: `@modelcontextprotocol/server@2` declara
 * `engines.node >= 20`. Edge nao satisfaz esse contrato.
 *
 * Esta rota e' de proposito uma casca fina: toda a logica (e toda a decisao de
 * acesso) vive em `src/server/oracle/handler.ts`, que recebe o ambiente por
 * parametro e por isso e' testavel sem mutar `process.env`.
 */
import { handleMcpRequest } from "../../../src/server/oracle/handler.ts";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Sinais lidos do ambiente.
 *
 * `VERCEL`, `VERCEL_ENV` e `VERCEL_TARGET_ENV` distinguem execucao hospedada de
 * local: Preview e custom environments seguem negados; producao passa a ser
 * elegivel apenas com OAuth completo.
 *
 * As tres variaveis `ORACLE_AUTH_*` sao **server-side** e publicas por
 * natureza (issuer, audience e nome da permission). Nenhum Client ID ou Client
 * Secret entra aqui: a validacao de JWT usa somente issuer e JWKS publicos, e
 * o segredo da aplicacao Auth0 pertence ao Claude.ai.
 */
function currentEnv() {
  return {
    NODE_ENV: process.env.NODE_ENV,
    VERCEL: process.env.VERCEL,
    VERCEL_ENV: process.env.VERCEL_ENV,
    VERCEL_TARGET_ENV: process.env.VERCEL_TARGET_ENV,
    ORACLE_MCP_ENABLED: process.env.ORACLE_MCP_ENABLED,
    MCP_BACKEND_API_URL: process.env.MCP_BACKEND_API_URL,
    ORACLE_AUTH_ISSUER: process.env.ORACLE_AUTH_ISSUER,
    ORACLE_AUTH_AUDIENCE: process.env.ORACLE_AUTH_AUDIENCE,
    ORACLE_AUTH_REQUIRED_SCOPE: process.env.ORACLE_AUTH_REQUIRED_SCOPE,
  };
}

export async function POST(request: Request): Promise<Response> {
  return handleMcpRequest(request, currentEnv());
}

/**
 * GET e DELETE sao operacoes de sessao do transporte 2025. Como servimos
 * stateless, o proprio handler do SDK responde `405`. Passam pelo mesmo
 * caminho de decisao (fail-closed primeiro) para nao revelar a rota em
 * producao — la o resultado e' 404, nao 405.
 */
export async function GET(request: Request): Promise<Response> {
  return handleMcpRequest(request, currentEnv());
}

export async function DELETE(request: Request): Promise<Response> {
  return handleMcpRequest(request, currentEnv());
}
