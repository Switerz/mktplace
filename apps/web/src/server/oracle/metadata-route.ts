/**
 * Corpo compartilhado dos endpoints de Protected Resource Metadata (RFC 9728).
 *
 * Existe como modulo proprio para que as duas rotas — a forma path-specific
 * (`/.well-known/oauth-protected-resource/api/mcp`) e a forma root
 * (`/.well-known/oauth-protected-resource`) — devolvam corpo IDENTICO, sem
 * chance de divergirem por copia.
 *
 * O documento e' PUBLICO e nao exige bearer: e' assim que o cliente descobre o
 * authorization server. Mas sua DISPONIBILIDADE segue exatamente a mesma
 * decisao fail-closed da rota MCP — anunciar metadata de uma rota que responde
 * 404 apenas produziria uma falha de conexao confusa mais adiante.
 */
import { evaluateAccess, type AccessEnv } from "./access.ts";
import { protectedResourceMetadata } from "./oauth.ts";

/**
 * Le do ambiente os MESMOS sinais que a rota MCP le.
 *
 * Precisa ser o conjunto completo (nao apenas as tres `ORACLE_AUTH_*`) porque
 * a disponibilidade da metadata e' decidida por `evaluateAccess`, que considera
 * ambiente, feature flag e backend.
 */
export function metadataEnv(): AccessEnv {
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

function notFound(): Response {
  return new Response(JSON.stringify({ error: "not_found" }), {
    status: 404,
    headers: { "content-type": "application/json" },
  });
}

/**
 * Serve o documento RFC 9728, ou 404.
 *
 * A decisao vem de `evaluateAccess` — a MESMA funcao que governa a rota MCP,
 * reusada em vez de reimplementada. So' publica quando a rota esta
 * operacionalmente elegivel:
 *
 *   - ambiente permitido (producao Vercel ou local; nunca Preview/custom);
 *   - `ORACLE_MCP_ENABLED=1`;
 *   - `MCP_BACKEND_API_URL` valido;
 *   - issuer, audience e permission canonica completos (`auth !== null`).
 *
 * `auth !== null` e' a parte que fecha o caso local: localmente a rota pode
 * rodar SEM OAuth (modo de desenvolvimento do OM1), e nesse caso nao existe
 * authorization server para anunciar.
 *
 * Nenhuma autenticacao acontece aqui — o endpoint permanece publico.
 */
export function protectedResourceMetadataResponse(env: AccessEnv): Response {
  const decision = evaluateAccess(env);
  if (!decision.allowed || decision.auth === null) return notFound();

  return new Response(JSON.stringify(protectedResourceMetadata(decision.auth)), {
    status: 200,
    headers: {
      "content-type": "application/json",
      // Documento de descoberta publico: cache curto e explicito.
      "cache-control": "public, max-age=300",
    },
  });
}
