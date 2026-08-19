/**
 * Endpoint MCP da Torre — Streamable HTTP, stateless.
 *
 * ESTADO: piloto LOCAL/DEV. Producao esta DESABILITADA por construcao
 * (ver `evaluateAccess`): sem provedor real de identidade, a rota nega com 404.
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
 * Sinais lidos do ambiente. `VERCEL`, `VERCEL_ENV` e `VERCEL_TARGET_ENV`
 * existem aqui para negar QUALQUER deployment (Production, Preview e custom
 * environments) enquanto nao houver autenticacao real — nao apenas producao.
 */
function currentEnv() {
  return {
    NODE_ENV: process.env.NODE_ENV,
    VERCEL: process.env.VERCEL,
    VERCEL_ENV: process.env.VERCEL_ENV,
    VERCEL_TARGET_ENV: process.env.VERCEL_TARGET_ENV,
    ORACLE_MCP_ENABLED: process.env.ORACLE_MCP_ENABLED,
    MCP_BACKEND_API_URL: process.env.MCP_BACKEND_API_URL,
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
