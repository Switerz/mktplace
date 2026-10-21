/**
 * Proxy autenticado da aba B2B -> FastAPI `/api/v1/b2b-revenda/<rota>`.
 * Casca fina: a sessao e' aberta no servidor e o token nunca chega ao navegador
 * (`src/server/b2b-auth/bff.ts`).
 */
import { handleProxy } from "../../../../../src/server/b2b-auth/bff.ts";
import { lerEnvB2B } from "../../../../../src/server/b2b-auth/env.ts";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request, ctx: { params: Promise<{ rota: string }> }): Promise<Response> {
  const { rota } = await ctx.params;
  return handleProxy(req, rota, lerEnvB2B());
}
