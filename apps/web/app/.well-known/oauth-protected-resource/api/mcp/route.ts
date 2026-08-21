/**
 * Protected Resource Metadata (RFC 9728) — forma PATH-SPECIFIC.
 *
 * Esta e' a forma primaria para um resource cujo identificador tem path
 * (`/api/mcp`): o cliente do Claude sonda
 * `/.well-known/oauth-protected-resource/<mcp-path>` antes da forma raiz.
 *
 * Endpoint PUBLICO e sem autenticacao, por definicao do RFC 9728 — e' o que
 * permite descobrir o authorization server. Nao expõe segredo.
 */
import {
  metadataEnv,
  protectedResourceMetadataResponse,
} from "../../../../../src/server/oracle/metadata-route.ts";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return protectedResourceMetadataResponse(metadataEnv());
}
