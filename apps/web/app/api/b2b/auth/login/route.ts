/** Casca fina — toda a logica (e a decisao de acesso) vive em `src/server/b2b-auth/bff.ts`. */
import { handleLogin } from "../../../../../src/server/b2b-auth/bff.ts";
import { lerEnvB2B } from "../../../../../src/server/b2b-auth/env.ts";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export function GET(req: Request): Promise<Response> {
  return handleLogin(req, lerEnvB2B());
}
