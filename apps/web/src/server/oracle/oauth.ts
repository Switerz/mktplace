/**
 * Camada OAuth 2.1 do MCP do Oraculo — RESOURCE SERVER apenas.
 *
 * O que este modulo E':
 * - validador de access token JWT emitido pelo Auth0 (authorization server);
 * - produtor do documento Protected Resource Metadata (RFC 9728);
 * - fonte da configuracao OAuth, validada de forma fail-closed.
 *
 * O que este modulo NAO E', e nunca deve se tornar:
 * - `/authorize`, `/token`, `/register`, login, consentimento ou callback —
 *   tudo isso pertence ao Auth0;
 * - armazenamento de sessao, cookie ou refresh token;
 * - consumidor de Client ID/Secret. A validacao de JWT usa APENAS issuer e
 *   JWKS publicos. O Client Secret pertence ao Claude.ai, nao a este servidor.
 *
 * Identidade: o Google e' apenas o provedor de login DENTRO do Auth0. Nenhum
 * token do Google (nem access token, nem ID token) e' credencial aceita aqui —
 * so' access token emitido pelo Auth0 para a audience canonica desta rota.
 */
import { createRemoteJWKSet, jwtVerify, type JWTPayload } from "jose";
import {
  getOAuthProtectedResourceMetadataUrl,
  OAuthError,
  OAuthErrorCode,
  type AuthInfo,
  type OAuthTokenVerifier,
} from "@modelcontextprotocol/server";

/**
 * Host UNICO aceito como issuer, no mesmo espirito do
 * `ALLOWED_BACKEND_HOSTNAME`: constante server-only, valor publico, e uma
 * configuracao divergente falha FECHADA em vez de aceitar qualquer origem.
 *
 * Trocar de tenant (ou adotar custom domain do Auth0) exige mudar esta
 * constante — uma alteracao deliberada e revisavel, nao um efeito de ambiente.
 */
export const ALLOWED_AUTH_ISSUER_HOSTNAME = "gobeaute-oraculo.us.auth0.com";

/**
 * Resource canonico desta rota, igual ao identifier da API no Auth0 e ao
 * `resource` do documento RFC 9728. A audience do token precisa ser exatamente
 * esta string — apontar o resource para outro lugar e' erro de configuracao.
 */
export const CANONICAL_MCP_RESOURCE = "https://mktplace-gobeaute.vercel.app/api/mcp";

/**
 * Permission CANONICA e unica desta rota.
 *
 * A variavel de ambiente continua existindo como confirmacao explicita de
 * configuracao, mas **nao pode escolher outra permission**: aceitar qualquer
 * token sintaticamente valido permitiria configurar `openid` — que todo token
 * OIDC carrega — e esvaziar a autorizacao sem que nada falhasse.
 */
export const CANONICAL_ORACLE_SCOPE = "oracle:read";

/** Unico algoritmo de assinatura aceito. Exclui `none` e toda a familia HS*. */
export const ALLOWED_JWT_ALGORITHMS = ["RS256"] as const;

/** Tolerancia de relogio na validacao de `exp`/`nbf`, em segundos. */
const CLOCK_TOLERANCE_SECONDS = 5;

export type OAuthEnv = {
  ORACLE_AUTH_ISSUER?: string | undefined;
  ORACLE_AUTH_AUDIENCE?: string | undefined;
  ORACLE_AUTH_REQUIRED_SCOPE?: string | undefined;
};

export type OAuthConfig = {
  /** Issuer canonico, SEMPRE com barra final (forma do claim `iss` do Auth0). */
  readonly issuer: string;
  /** URL do JWKS, derivada do issuer — nunca configurada a mao. */
  readonly jwksUri: string;
  /** Audience exigida no claim `aud`. */
  readonly audience: string;
  /** Permission/scope exigida, comparada como ELEMENTO completo. */
  readonly requiredScope: string;
};

function present(v: string | undefined): v is string {
  return typeof v === "string" && v.trim().length > 0;
}

/**
 * Normaliza o issuer para a forma canonica do Auth0: origem + barra final.
 *
 * O claim `iss` do Auth0 e' `https://<tenant>/` COM barra. Aceitamos a
 * configuracao com ou sem barra e normalizamos aqui, num unico ponto, para que
 * a comparacao adiante seja por igualdade exata de string.
 *
 * Retorna `null` quando a URL nao e' um issuer aceitavel. Estrito de proposito:
 * https obrigatorio, host EXATO (nunca `endsWith`), sem porta, sem credencial,
 * sem query, sem fragment e sem path significativo.
 */
export function normalizeIssuer(raw: string | undefined): string | null {
  if (!present(raw)) return null;

  let parsed: URL;
  try {
    parsed = new URL(raw.trim());
  } catch {
    return null;
  }

  if (parsed.protocol !== "https:") return null;
  if (parsed.username !== "" || parsed.password !== "") return null;
  if (parsed.port !== "") return null;
  if (parsed.search !== "" || parsed.hash !== "") return null;
  // Correspondencia EXATA do host. `endsWith(".auth0.com")` aceitaria
  // `gobeaute-oraculo.us.auth0.com.evil.invalid` — por isso nao e' usado.
  if (parsed.hostname !== ALLOWED_AUTH_ISSUER_HOSTNAME) return null;
  // Auth0 emite `iss` na raiz; qualquer path aqui e' configuracao errada.
  if (parsed.pathname !== "/" && parsed.pathname !== "") return null;

  return `https://${parsed.hostname}/`;
}

/**
 * A permission exigida precisa ser EXATAMENTE a canonica.
 *
 * Nao e' validacao de formato: e' igualdade. `oracle:reader`,
 * `oracle:read:all`, `openid`, `profile`, `read`, multiplos scopes, vazio e
 * ausente todos recusam a configuracao inteira — e, em contexto hospedado,
 * isso vira 404.
 */
function isCanonicalScope(raw: string | undefined): raw is string {
  return present(raw) && raw.trim() === CANONICAL_ORACLE_SCOPE;
}

/**
 * Le a configuracao OAuth do ambiente. Devolve `null` quando ela esta
 * INCOMPLETA ou INVALIDA — quem chama decide o que fazer com isso (em contexto
 * hospedado, a decisao e' 404 generico, nunca servir sem autenticacao).
 */
export function parseOAuthConfig(env: OAuthEnv): OAuthConfig | null {
  const issuer = normalizeIssuer(env.ORACLE_AUTH_ISSUER);
  if (issuer === null) return null;

  // A audience precisa ser exatamente o resource canonico desta rota.
  const audience = present(env.ORACLE_AUTH_AUDIENCE) ? env.ORACLE_AUTH_AUDIENCE.trim() : "";
  if (audience !== CANONICAL_MCP_RESOURCE) return null;

  // A variavel confirma a configuracao; a constante e' quem manda.
  if (!isCanonicalScope(env.ORACLE_AUTH_REQUIRED_SCOPE)) return null;

  return {
    issuer,
    jwksUri: `${issuer}.well-known/jwks.json`,
    audience,
    requiredScope: CANONICAL_ORACLE_SCOPE,
  };
}

// ---------------------------------------------------------------------------
// Protected Resource Metadata (RFC 9728)
// ---------------------------------------------------------------------------

export type ProtectedResourceMetadata = {
  resource: string;
  authorization_servers: string[];
  scopes_supported: string[];
  bearer_methods_supported: string[];
};

/**
 * Documento RFC 9728 desta rota.
 *
 * Deliberadamente MINIMO: apenas os quatro campos que descrevem o que este
 * resource server realmente e'. Nenhuma capability e' inventada.
 *
 * `authorization_servers` traz o issuer numa lista de UM elemento — o cliente
 * do Claude usa a primeira entrada e nao faz fallback para as seguintes.
 */
export function protectedResourceMetadata(cfg: OAuthConfig): ProtectedResourceMetadata {
  return {
    resource: cfg.audience,
    authorization_servers: [cfg.issuer],
    scopes_supported: [cfg.requiredScope],
    bearer_methods_supported: ["header"],
  };
}

/**
 * URL do documento PRM, na forma path-specific exigida por um resource com
 * path (`/api/mcp` -> `/.well-known/oauth-protected-resource/api/mcp`).
 * Derivada pelo helper do SDK, nao montada a mao.
 */
export function protectedResourceMetadataUrl(cfg: OAuthConfig): string {
  return getOAuthProtectedResourceMetadataUrl(new URL(cfg.audience));
}

// ---------------------------------------------------------------------------
// Verificacao do access token
// ---------------------------------------------------------------------------

/**
 * Extrai as permissions efetivas do token, unindo as DUAS claims do Auth0:
 *
 * - `permissions` — com RBAC + "Add Permissions in the Access Token", contem
 *   TODAS as permissions atribuidas ao usuario, sem filtro pelo pedido;
 * - `scope` — com RBAC, contem a INTERSECAO entre o que o cliente pediu e o
 *   que o usuario tem.
 *
 * Unir as duas torna a autorizacao correta independentemente de o cliente ter
 * solicitado o scope. Mesmo assim emitimos `scope` no desafio 401, para que o
 * Claude o solicite — as duas coisas juntas, nao uma no lugar da outra.
 */
export function effectiveScopes(payload: JWTPayload): string[] {
  const out = new Set<string>();

  const permissions = (payload as { permissions?: unknown }).permissions;
  if (Array.isArray(permissions)) {
    for (const p of permissions) if (typeof p === "string" && p.length > 0) out.add(p);
  }

  const scope = (payload as { scope?: unknown }).scope;
  if (typeof scope === "string") {
    for (const s of scope.split(" ")) if (s.length > 0) out.add(s);
  }

  return [...out];
}

/**
 * `true` somente quando a permission exigida aparece como ELEMENTO completo.
 * Comparacao por substring aceitaria `oracle:reader` para `oracle:read` — por
 * isso a checagem e' de igualdade dentro do conjunto.
 */
export function hasRequiredScope(scopes: readonly string[], required: string): boolean {
  return scopes.includes(required);
}

function invalidToken(detail: string): OAuthError {
  // `detail` e' SEMPRE string literal do nosso codigo: nunca o token, nunca o
  // payload, nunca a mensagem original da biblioteca.
  return new OAuthError(OAuthErrorCode.InvalidToken, detail);
}

export type VerifierOptions = {
  /**
   * Resolvedor de chaves. Em producao e' o JWKS remoto do issuer, com cache e
   * rotacao por `kid` administrados pela `jose`. Injetavel para que os testes
   * usem chaves sinteticas geradas em runtime, sem rede e sem PEM versionado.
   */
  keyResolver?: Parameters<typeof jwtVerify>[1];
};

/**
 * Constroi o verificador de token no contrato `OAuthTokenVerifier` do SDK.
 *
 * Valida obrigatoriamente: assinatura por JWKS, algoritmo RS256, `iss` exato,
 * `aud` contendo exatamente a audience, `exp`, `nbf` quando presente, `sub`
 * nao vazio e a permission exigida como elemento completo.
 */
export function createAuth0Verifier(
  cfg: OAuthConfig,
  opts: VerifierOptions = {},
): OAuthTokenVerifier {
  // `createRemoteJWKSet` cuida de cache e de rotacao por `kid`. Nenhuma chave
  // publica e' fixada em codigo.
  const keys = opts.keyResolver ?? createRemoteJWKSet(new URL(cfg.jwksUri));

  return {
    async verifyAccessToken(token: string): Promise<AuthInfo> {
      if (!present(token)) throw invalidToken("token ausente");

      let payload: JWTPayload;
      try {
        const verified = await jwtVerify(token, keys, {
          issuer: cfg.issuer,
          audience: cfg.audience,
          algorithms: [...ALLOWED_JWT_ALGORITHMS],
          clockTolerance: CLOCK_TOLERANCE_SECONDS,
          // `requiredClaims` faz a ausencia de `exp` falhar aqui, em vez de
          // adiante: o SDK rejeita `AuthInfo.expiresAt` indefinido.
          requiredClaims: ["exp", "sub", "iss", "aud"],
        });
        payload = verified.payload;
      } catch {
        // Nada da mensagem da `jose` e' propagado: assinatura invalida,
        // algoritmo recusado, issuer/audience divergentes, expirado e `nbf`
        // futuro convergem para a MESMA resposta, sem oraculo de diagnostico.
        throw invalidToken("token invalido");
      }

      const sub = payload.sub;
      if (typeof sub !== "string" || sub.trim().length === 0) {
        throw invalidToken("token invalido");
      }
      if (typeof payload.exp !== "number") {
        throw invalidToken("token invalido");
      }

      const scopes = effectiveScopes(payload);

      // A checagem de permission acontece aqui E no `requiredScopes` do SDK.
      // Redundancia deliberada: se um dos dois for removido por engano no
      // futuro, o outro continua negando.
      if (!hasRequiredScope(scopes, cfg.requiredScope)) {
        throw new OAuthError(OAuthErrorCode.InsufficientScope, "permissao insuficiente");
      }

      const azp = (payload as { azp?: unknown }).azp;
      const clientId = typeof azp === "string" && azp.length > 0 ? azp : "";

      return {
        token,
        clientId,
        scopes,
        expiresAt: payload.exp,
        // `resource` e `extra` sao deliberadamente omitidos: nenhum claim
        // integral e' anexado ao AuthInfo, para que nada disso possa vazar
        // adiante por acidente.
      };
    },
  };
}

/**
 * Sujeito pseudonimizado para log. Nunca o `sub` cru, nunca e-mail.
 * Prefixo curto de SHA-256 — suficiente para correlacionar duas linhas de log,
 * insuficiente para identificar a pessoa.
 */
export async function pseudonymizeSubject(sub: string): Promise<string> {
  const data = new TextEncoder().encode(`oracle-mcp:${sub}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  const bytes = new Uint8Array(digest).subarray(0, 6);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}
