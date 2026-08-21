/**
 * Protected Resource Metadata (RFC 9728) — forma ROOT.
 *
 * Existe apenas para interoperabilidade: e' o segundo caminho que o cliente do
 * Claude sonda quando nao encontra a forma path-specific. Descreve
 * EXATAMENTE o mesmo resource — o corpo vem do mesmo modulo, sem copia.
 *
 * Endpoint PUBLICO e sem autenticacao, por definicao do RFC 9728.
 */
import {
  metadataEnv,
  protectedResourceMetadataResponse,
} from "../../../src/server/oracle/metadata-route.ts";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return protectedResourceMetadataResponse(metadataEnv());
}
