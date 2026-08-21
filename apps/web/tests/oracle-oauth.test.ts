// Gate OM2 Task 1/2 — autenticacao OAuth 2.1 do /api/mcp.
//
// Todas as chaves e tokens sao SINTETICOS, gerados em runtime. Nenhum PEM,
// JWT ou fixture com aparencia de credencial real e' versionado, e nenhum
// teste toca o Auth0 real, a rede ou producao.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { exportJWK, generateKeyPair, SignJWT, type JWK, type KeyObject } from "jose";

import { evaluateAccess, type AccessEnv } from "../src/server/oracle/access.ts";
import { handleMcpRequest } from "../src/server/oracle/handler.ts";
import {
  ALLOWED_AUTH_ISSUER_HOSTNAME, CANONICAL_MCP_RESOURCE, CANONICAL_ORACLE_SCOPE,
  effectiveScopes,
  hasRequiredScope, normalizeIssuer, parseOAuthConfig, protectedResourceMetadata,
  protectedResourceMetadataUrl, pseudonymizeSubject,
} from "../src/server/oracle/oauth.ts";
import { protectedResourceMetadataResponse } from "../src/server/oracle/metadata-route.ts";

const HERE = dirname(fileURLToPath(import.meta.url));

const ISSUER = `https://${ALLOWED_AUTH_ISSUER_HOSTNAME}/`;
const AUDIENCE = CANONICAL_MCP_RESOURCE;
const SCOPE = "oracle:read";

/** Ambiente de PRODUCAO completamente configurado. */
const PROD_ENV: AccessEnv = {
  NODE_ENV: "production",
  VERCEL: "1",
  VERCEL_ENV: "production",
  ORACLE_MCP_ENABLED: "1",
  MCP_BACKEND_API_URL: "https://mktplace-api.onrender.com",
  ORACLE_AUTH_ISSUER: ISSUER,
  ORACLE_AUTH_AUDIENCE: AUDIENCE,
  ORACLE_AUTH_REQUIRED_SCOPE: SCOPE,
};

const OAUTH_ENV = {
  ORACLE_AUTH_ISSUER: ISSUER,
  ORACLE_AUTH_AUDIENCE: AUDIENCE,
  ORACLE_AUTH_REQUIRED_SCOPE: SCOPE,
};

// ---------------------------------------------------------------------------
// Par de chaves sintetico, gerado uma vez por processo de teste
// ---------------------------------------------------------------------------

type Keys = { privateKey: KeyObject; publicJwk: JWK; kid: string };
let keysPromise: Promise<Keys> | undefined;

async function keys(): Promise<Keys> {
  keysPromise ??= (async () => {
    const { privateKey, publicKey } = await generateKeyPair("RS256", { extractable: true });
    const publicJwk = await exportJWK(publicKey);
    const kid = "test-key-1";
    publicJwk.kid = kid;
    publicJwk.alg = "RS256";
    return { privateKey: privateKey as KeyObject, publicJwk, kid };
  })();
  return keysPromise;
}

/** Resolvedor local de chaves — substitui o JWKS remoto, sem rede. */
async function localKeyResolver() {
  const { publicJwk } = await keys();
  const { importJWK } = await import("jose");
  const key = await importJWK(publicJwk, "RS256");
  return () => Promise.resolve(key as never);
}

type TokenOverrides = {
  issuer?: string;
  audience?: string | string[];
  scope?: string | null;
  permissions?: string[] | null;
  sub?: string | null;
  expiresIn?: number;
  notBefore?: number;
  alg?: string;
  omitExp?: boolean;
};

/** Emite um access token sintetico no formato que o Auth0 produz. */
async function mintToken(o: TokenOverrides = {}): Promise<string> {
  const { privateKey, kid } = await keys();
  const now = Math.floor(Date.now() / 1000);

  const claims: Record<string, unknown> = {
    iss: o.issuer ?? ISSUER,
    aud: o.audience ?? AUDIENCE,
    azp: "synthetic-client-id",
  };
  if (o.sub !== null) claims.sub = o.sub ?? "auth0|synthetic-subject";
  if (o.scope !== null) claims.scope = o.scope ?? SCOPE;
  if (o.permissions !== null) claims.permissions = o.permissions ?? [SCOPE];

  let jwt = new SignJWT(claims)
    .setProtectedHeader({ alg: o.alg ?? "RS256", kid, typ: "JWT" })
    .setIssuedAt(now);

  if (!o.omitExp) jwt = jwt.setExpirationTime(now + (o.expiresIn ?? 3600));
  if (o.notBefore !== undefined) jwt = jwt.setNotBefore(now + o.notBefore);

  return jwt.sign(privateKey);
}

/** `fetch` espiao: qualquer chamada upstream numa negacao seria falha grave. */
function spyFetch() {
  const calls: string[] = [];
  const impl = (async (input: RequestInfo | URL) => {
    calls.push(String(input));
    return new Response("{}", { headers: { "content-type": "application/json" } });
  }) as unknown as typeof fetch;
  return { calls, impl };
}

const REQUEST_META = {
  "io.modelcontextprotocol/protocolVersion": "2026-07-28",
  "io.modelcontextprotocol/clientInfo": { name: "oauth-test", version: "0.0.0" },
  "io.modelcontextprotocol/clientCapabilities": {},
};

function mcpRequest(authorization?: string, method = "tools/list"): Request {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
    "mcp-method": method,
  };
  if (authorization !== undefined) headers.authorization = authorization;

  return new Request("https://mktplace-gobeaute.vercel.app/api/mcp", {
    method: "POST",
    headers,
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params: { _meta: REQUEST_META } }),
  });
}

async function call(authorization: string | undefined, env: AccessEnv = PROD_ENV) {
  const spy = spyFetch();
  const keyResolver = await localKeyResolver();
  const res = await handleMcpRequest(mcpRequest(authorization), env, {
    fetchImpl: spy.impl,
    verifierOptions: { keyResolver: keyResolver as never },
  });
  return { res, calls: spy.calls, body: await res.text() };
}

// ===========================================================================
// Metadata publica (RFC 9728)
// ===========================================================================

test("metadata: documento tem exatamente os quatro campos do contrato", () => {
  const cfg = parseOAuthConfig(OAUTH_ENV);
  assert.ok(cfg);
  const doc = protectedResourceMetadata(cfg);

  assert.deepEqual(Object.keys(doc).sort(), [
    "authorization_servers",
    "bearer_methods_supported",
    "resource",
    "scopes_supported",
  ]);
  assert.equal(doc.resource, "https://mktplace-gobeaute.vercel.app/api/mcp");
  assert.deepEqual(doc.authorization_servers, [ISSUER]);
  assert.deepEqual(doc.scopes_supported, [SCOPE]);
  assert.deepEqual(doc.bearer_methods_supported, ["header"]);
});

test("metadata: scopes_supported tem UM unico scope", () => {
  const cfg = parseOAuthConfig(OAUTH_ENV);
  assert.equal(protectedResourceMetadata(cfg!).scopes_supported.length, 1);
});

test("metadata: URL e' a forma path-specific do RFC 9728", () => {
  const cfg = parseOAuthConfig(OAUTH_ENV);
  assert.equal(
    protectedResourceMetadataUrl(cfg!),
    "https://mktplace-gobeaute.vercel.app/.well-known/oauth-protected-resource/api/mcp",
  );
});

test("metadata: 200 em producao Vercel completa", async () => {
  const res = protectedResourceMetadataResponse(PROD_ENV);
  assert.equal(res.status, 200);
  assert.match(res.headers.get("content-type") ?? "", /application\/json/);
  const doc = JSON.parse(await res.text());
  assert.equal(doc.resource, AUDIENCE);
  assert.deepEqual(doc.authorization_servers, [ISSUER]);
});

test("metadata: 200 em local completo (OAuth + flag + backend)", async () => {
  const res = protectedResourceMetadataResponse({
    NODE_ENV: "development",
    ORACLE_MCP_ENABLED: "1",
    MCP_BACKEND_API_URL: "https://mktplace-api.onrender.com",
    ...OAUTH_ENV,
  });
  assert.equal(res.status, 200);
});

test("metadata: sem OAuth configurado responde 404, nao um documento invalido", async () => {
  const res = protectedResourceMetadataResponse({});
  assert.equal(res.status, 404);
  assert.deepEqual(JSON.parse(await res.text()), { error: "not_found" });
});

test("metadata: disponibilidade segue a MESMA decisao da rota MCP", async () => {
  const cases: Array<[string, AccessEnv]> = [
    ["OAuth completo mas SEM feature flag", { ...PROD_ENV, ORACLE_MCP_ENABLED: undefined }],
    ["OAuth completo mas backend ausente", { ...PROD_ENV, MCP_BACKEND_API_URL: undefined }],
    ["OAuth completo mas backend divergente", { ...PROD_ENV, MCP_BACKEND_API_URL: "https://atacante.invalid" }],
    ["Preview com TUDO configurado", { ...PROD_ENV, VERCEL_ENV: "preview" }],
    ["custom environment com TUDO configurado", { ...PROD_ENV, VERCEL_TARGET_ENV: "staging-qa" }],
    ["VERCEL=1 sem VERCEL_ENV confiavel", { ...PROD_ENV, VERCEL_ENV: undefined }],
    ["OAuth incompleto (issuer)", { ...PROD_ENV, ORACLE_AUTH_ISSUER: undefined }],
    ["OAuth incompleto (audience)", { ...PROD_ENV, ORACLE_AUTH_AUDIENCE: undefined }],
    ["scope nao canonico", { ...PROD_ENV, ORACLE_AUTH_REQUIRED_SCOPE: "openid" }],
    ["local sem OAuth (modo OM1)", {
      NODE_ENV: "development",
      ORACLE_MCP_ENABLED: "1",
      MCP_BACKEND_API_URL: "https://mktplace-api.onrender.com",
    }],
  ];

  for (const [label, env] of cases) {
    const res = protectedResourceMetadataResponse(env);
    assert.equal(res.status, 404, `${label} deveria dar 404`);
    assert.deepEqual(JSON.parse(await res.text()), { error: "not_found" }, label);
  }
});

test("metadata: as duas rotas devolvem corpo IDENTICO", async () => {
  const { GET: rootGet } = await import("../app/.well-known/oauth-protected-resource/route.ts");
  const { GET: pathGet } = await import(
    "../app/.well-known/oauth-protected-resource/api/mcp/route.ts"
  );

  // As rotas leem `process.env`; configuramos o minimo local completo.
  const saved = { ...process.env };
  Object.assign(process.env, {
    NODE_ENV: "development",
    ORACLE_MCP_ENABLED: "1",
    MCP_BACKEND_API_URL: "https://mktplace-api.onrender.com",
    ORACLE_AUTH_ISSUER: ISSUER,
    ORACLE_AUTH_AUDIENCE: AUDIENCE,
    ORACLE_AUTH_REQUIRED_SCOPE: SCOPE,
  });
  delete process.env.VERCEL;
  delete process.env.VERCEL_ENV;
  delete process.env.VERCEL_TARGET_ENV;

  try {
    const a = await rootGet();
    const b = await pathGet();
    assert.equal(a.status, 200);
    assert.equal(b.status, 200);
    assert.equal(await a.text(), await b.text(), "os dois caminhos servem o mesmo documento");
  } finally {
    for (const k of Object.keys(process.env)) if (!(k in saved)) delete process.env[k];
    Object.assign(process.env, saved);
  }
});

test("metadata: as duas rotas existem e apontam para o mesmo modulo", () => {
  const app = resolve(HERE, "../app/.well-known/oauth-protected-resource");
  assert.ok(existsSync(resolve(app, "route.ts")), "forma root");
  assert.ok(existsSync(resolve(app, "api/mcp/route.ts")), "forma path-specific");
});

// ===========================================================================
// Configuracao: issuer, audience, scope
// ===========================================================================

test("issuer: normaliza a barra final para a forma canonica do Auth0", () => {
  assert.equal(normalizeIssuer(`https://${ALLOWED_AUTH_ISSUER_HOSTNAME}`), ISSUER);
  assert.equal(normalizeIssuer(`https://${ALLOWED_AUTH_ISSUER_HOSTNAME}/`), ISSUER);
  assert.equal(normalizeIssuer(`  https://${ALLOWED_AUTH_ISSUER_HOSTNAME}/  `), ISSUER);
});

test("issuer: host arbitrario e variacoes maliciosas sao recusados", () => {
  const H = ALLOWED_AUTH_ISSUER_HOSTNAME;
  const rejected = [
    `https://${H}.evil.invalid/`,
    `https://evil-${H}/`,
    `https://sub.${H}/`,
    `https://${H}.br/`,
    `https://${H}./`,
    "https://outro-tenant.us.auth0.com/",
    "https://auth0.com/",
    `http://${H}/`,
    `https://${H}:8443/`,
    `https://user:pass@${H}/`,
    `https://${H}/?x=1`,
    `https://${H}/#f`,
    `https://${H}/tenant`,
    "nao-e-url",
    "",
    undefined,
  ];
  for (const v of rejected) {
    assert.equal(normalizeIssuer(v as string | undefined), null, `deveria recusar: ${String(v)}`);
  }
});

test("config: audience precisa ser EXATAMENTE o resource canonico", () => {
  assert.ok(parseOAuthConfig(OAUTH_ENV));
  for (const aud of [
    "https://mktplace-gobeaute.vercel.app/api/mcp/",
    "https://mktplace-gobeaute.vercel.app/api/mcp?x=1",
    "https://mktplace-gobeaute.vercel.app",
    "https://outro.vercel.app/api/mcp",
    "",
    undefined,
  ]) {
    assert.equal(
      parseOAuthConfig({ ...OAUTH_ENV, ORACLE_AUTH_AUDIENCE: aud as string }),
      null,
      `deveria recusar audience: ${String(aud)}`,
    );
  }
});

test("config: scope invalido recusa a configuracao inteira", () => {
  for (const s of ["", "  ", "a b", "oracle:read extra", "oracle read", undefined]) {
    assert.equal(
      parseOAuthConfig({ ...OAUTH_ENV, ORACLE_AUTH_REQUIRED_SCOPE: s as string }),
      null,
      `deveria recusar scope: ${String(s)}`,
    );
  }
});

test("config: JWKS e' DERIVADO do issuer, nunca configurado a mao", () => {
  const cfg = parseOAuthConfig(OAUTH_ENV);
  assert.equal(cfg!.jwksUri, `${ISSUER}.well-known/jwks.json`);
});

test("config: nenhum campo de Client ID/Secret existe no contrato", () => {
  const cfg = parseOAuthConfig(OAUTH_ENV);
  const keys = Object.keys(cfg!);
  for (const forbidden of ["clientId", "clientSecret", "client_id", "client_secret", "secret"]) {
    assert.ok(!keys.includes(forbidden), `${forbidden} nao pode existir na config`);
  }
});

// ===========================================================================
// Permission x scope (regra final)
// ===========================================================================

test("permission: une as claims `permissions` e `scope` do Auth0", () => {
  assert.deepEqual(effectiveScopes({ permissions: ["oracle:read"] } as never), ["oracle:read"]);
  assert.deepEqual(effectiveScopes({ scope: "oracle:read" } as never), ["oracle:read"]);
  assert.deepEqual(
    effectiveScopes({ permissions: ["a"], scope: "b c" } as never).sort(),
    ["a", "b", "c"],
  );
  assert.deepEqual(effectiveScopes({} as never), []);
});

test("permission: comparacao e' de ELEMENTO completo, nunca substring", () => {
  assert.equal(hasRequiredScope(["oracle:read"], "oracle:read"), true);
  assert.equal(hasRequiredScope(["oracle:reader"], "oracle:read"), false);
  assert.equal(hasRequiredScope(["oracle:read:all"], "oracle:read"), false);
  assert.equal(hasRequiredScope(["xoracle:read"], "oracle:read"), false);
  assert.equal(hasRequiredScope(["openid", "profile"], "oracle:read"), false);
});

// ===========================================================================
// 401 — credencial ausente ou invalida
// ===========================================================================

test("401: sem header Authorization", async () => {
  const { res, calls } = await call(undefined);
  assert.equal(res.status, 401);
  assert.equal(calls.length, 0, "nenhuma chamada ao backend");
});

test("401: esquema diferente de Bearer", async () => {
  for (const h of ["Basic dXNlcjpwYXNz", "Token abc", "bearerabc", "Bearer"]) {
    const { res, calls } = await call(h);
    assert.equal(res.status, 401, `esquema recusado: ${h}`);
    assert.equal(calls.length, 0);
  }
});

test("401: bearer vazio ou malformado", async () => {
  for (const h of ["Bearer ", "Bearer nao-e-jwt", "Bearer a.b", "Bearer a.b.c.d"]) {
    const { res, calls } = await call(h);
    assert.equal(res.status, 401, `token recusado: ${h}`);
    assert.equal(calls.length, 0);
  }
});

test("401: assinatura invalida", async () => {
  const token = await mintToken();
  const tampered = token.slice(0, -6) + "AAAAAA";
  const { res, calls } = await call(`Bearer ${tampered}`);
  assert.equal(res.status, 401);
  assert.equal(calls.length, 0);
});

test("401: algoritmo `none` e' rejeitado", async () => {
  // Token "alg: none" montado a mao: header.payload. sem assinatura.
  const b64 = (o: unknown) =>
    Buffer.from(JSON.stringify(o)).toString("base64url");
  const none = `${b64({ alg: "none", typ: "JWT" })}.${b64({
    iss: ISSUER, aud: AUDIENCE, sub: "x", exp: Math.floor(Date.now() / 1000) + 600,
    permissions: [SCOPE],
  })}.`;
  const { res, calls } = await call(`Bearer ${none}`);
  assert.equal(res.status, 401);
  assert.equal(calls.length, 0);
});

test("401: HS256 e' rejeitado mesmo com claims corretos", async () => {
  const { SignJWT: Sign } = await import("jose");
  const secret = new TextEncoder().encode("segredo-sintetico-de-teste-0123456789");
  const hs = await new Sign({
    iss: ISSUER, aud: AUDIENCE, sub: "x", permissions: [SCOPE],
  })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setIssuedAt()
    .setExpirationTime("10m")
    .sign(secret);

  const { res, calls } = await call(`Bearer ${hs}`);
  assert.equal(res.status, 401);
  assert.equal(calls.length, 0);
});

test("401: issuer divergente", async () => {
  const token = await mintToken({ issuer: "https://outro-tenant.us.auth0.com/" });
  const { res, calls } = await call(`Bearer ${token}`);
  assert.equal(res.status, 401);
  assert.equal(calls.length, 0);
});

test("401: audience divergente", async () => {
  for (const aud of ["https://outro.vercel.app/api/mcp", "https://mktplace-gobeaute.vercel.app"]) {
    const token = await mintToken({ audience: aud });
    const { res, calls } = await call(`Bearer ${token}`);
    assert.equal(res.status, 401, `audience recusada: ${aud}`);
    assert.equal(calls.length, 0);
  }
});

test("401: token expirado", async () => {
  const token = await mintToken({ expiresIn: -600 });
  const { res, calls } = await call(`Bearer ${token}`);
  assert.equal(res.status, 401);
  assert.equal(calls.length, 0);
});

test("401: `nbf` no futuro", async () => {
  const token = await mintToken({ notBefore: 3600 });
  const { res, calls } = await call(`Bearer ${token}`);
  assert.equal(res.status, 401);
  assert.equal(calls.length, 0);
});

test("401: `sub` ausente", async () => {
  const token = await mintToken({ sub: null });
  const { res, calls } = await call(`Bearer ${token}`);
  assert.equal(res.status, 401);
  assert.equal(calls.length, 0);
});

test("401: `exp` ausente", async () => {
  const token = await mintToken({ omitExp: true });
  const { res, calls } = await call(`Bearer ${token}`);
  assert.equal(res.status, 401);
  assert.equal(calls.length, 0);
});

test("401: audience como array SEM a audience canonica e' rejeitada", async () => {
  const token = await mintToken({ audience: ["https://outra/api", "https://terceira/api"] });
  const { res } = await call(`Bearer ${token}`);
  assert.equal(res.status, 401);
});

// ===========================================================================
// 403 — permission insuficiente
// ===========================================================================

test("403: token valido sem a permission", async () => {
  const token = await mintToken({ permissions: [], scope: "openid profile" });
  const { res, calls } = await call(`Bearer ${token}`);
  assert.equal(res.status, 403);
  assert.equal(calls.length, 0, "backend nao e' tocado sem autorizacao");
});

test("403: permission PARECIDA nao passa (oracle:reader)", async () => {
  const token = await mintToken({ permissions: ["oracle:reader"], scope: "oracle:reader" });
  const { res, calls } = await call(`Bearer ${token}`);
  assert.equal(res.status, 403);
  assert.equal(calls.length, 0);
});

test("403: nem oracle:read:all nem prefixos passam", async () => {
  for (const p of ["oracle:read:all", "xoracle:read", "read"]) {
    const token = await mintToken({ permissions: [p], scope: p });
    const { res } = await call(`Bearer ${token}`);
    assert.equal(res.status, 403, `nao deveria autorizar: ${p}`);
  }
});

// ===========================================================================
// Desafios WWW-Authenticate
// ===========================================================================

test("401: WWW-Authenticate traz Bearer, resource_metadata e scope", async () => {
  const { res } = await call(undefined);
  const h = res.headers.get("www-authenticate") ?? "";

  assert.match(h, /^Bearer/, "esquema Bearer");
  assert.match(
    h,
    /resource_metadata="https:\/\/mktplace-gobeaute\.vercel\.app\/\.well-known\/oauth-protected-resource\/api\/mcp"/,
    "resource_metadata aponta para a forma path-specific",
  );
  assert.match(h, /scope="oracle:read"/, "scope guia o cliente");
});

test("403: WWW-Authenticate traz insufficient_scope, scope e resource_metadata", async () => {
  const token = await mintToken({ permissions: ["oracle:reader"], scope: "oracle:reader" });
  const { res } = await call(`Bearer ${token}`);
  const h = res.headers.get("www-authenticate") ?? "";

  assert.equal(res.status, 403);
  assert.match(h, /^Bearer/);
  assert.match(h, /error="insufficient_scope"/);
  assert.match(h, /scope="oracle:read"/);
  assert.match(h, /resource_metadata="https:\/\/mktplace-gobeaute\.vercel\.app\//);
});

test("desafio nao duplica o parametro scope", async () => {
  const token = await mintToken({ permissions: [], scope: null });
  const { res } = await call(`Bearer ${token}`);
  const h = res.headers.get("www-authenticate") ?? "";
  assert.equal((h.match(/scope=/g) ?? []).length, 1);
});

// ===========================================================================
// Nao vazamento
// ===========================================================================

test("o token NUNCA aparece na resposta de erro", async () => {
  const token = await mintToken({ permissions: ["oracle:reader"], scope: "oracle:reader" });
  const { res, body } = await call(`Bearer ${token}`);
  const all = body + JSON.stringify([...res.headers.entries()]);

  assert.ok(!all.includes(token), "o JWT inteiro nao pode aparecer");
  assert.ok(!all.includes(token.split(".")[2]), "nem a assinatura");
  assert.ok(!all.includes("auth0|synthetic-subject"), "nem o subject");
  assert.ok(!/\bat\s+\w+\s+\(/.test(all), "sem stack trace");
  assert.ok(!all.includes("onrender.com"), "sem host do backend");
  assert.ok(!all.includes(ALLOWED_AUTH_ISSUER_HOSTNAME) || /resource_metadata|scope=/.test(all));
});

test("erro nao revela QUAL validacao falhou", async () => {
  const bodies: string[] = [];
  for (const t of [
    await mintToken({ issuer: "https://outro-tenant.us.auth0.com/" }),
    await mintToken({ audience: "https://outro.vercel.app/api/mcp" }),
    await mintToken({ expiresIn: -600 }),
  ]) {
    const { body } = await call(`Bearer ${t}`);
    bodies.push(body);
  }
  // Todas as falhas de token convergem para a MESMA resposta.
  assert.equal(new Set(bodies).size, 1, "sem oraculo de diagnostico");
});

test("subject pseudonimizado nao contem o sub cru", async () => {
  const p = await pseudonymizeSubject("auth0|synthetic-subject");
  assert.match(p, /^[0-9a-f]{12}$/);
  assert.ok(!p.includes("auth0"));
  assert.ok(!p.includes("synthetic"));
  // Deterministico e estavel, para correlacionar duas linhas de log.
  assert.equal(p, await pseudonymizeSubject("auth0|synthetic-subject"));
  assert.notEqual(p, await pseudonymizeSubject("auth0|outro"));
});

// ===========================================================================
// Caminho autorizado
// ===========================================================================

test("200: token valido com a permission alcanca o MCP", async () => {
  const token = await mintToken();
  const { res, body } = await call(`Bearer ${token}`);

  assert.equal(res.status, 200);
  assert.ok(body.includes("torre_desempenho_marketplaces"), "as tools ficam alcancaveis");
});

test("200: permission vinda SO da claim `permissions` autoriza", async () => {
  // O cliente nao pediu o scope; RBAC do Auth0 ainda entrega `permissions`.
  const token = await mintToken({ scope: null, permissions: [SCOPE] });
  const { res } = await call(`Bearer ${token}`);
  assert.equal(res.status, 200);
});

test("200: permission vinda SO da claim `scope` autoriza", async () => {
  const token = await mintToken({ permissions: null, scope: `openid ${SCOPE}` });
  const { res } = await call(`Bearer ${token}`);
  assert.equal(res.status, 200);
});

test("200: audience como array CONTENDO a canonica e' aceita", async () => {
  const token = await mintToken({ audience: [AUDIENCE, "https://outra/api"] });
  const { res } = await call(`Bearer ${token}`);
  assert.equal(res.status, 200);
});

test("tools/list autorizado nao gera trafego para o backend", async () => {
  const token = await mintToken();
  const { res, calls } = await call(`Bearer ${token}`);
  assert.equal(res.status, 200);
  assert.equal(calls.length, 0, "descoberta de tools e' local");
});

test("o bearer NAO e' repassado ao backend numa chamada de tool", async () => {
  const token = await mintToken();
  const keyResolver = await localKeyResolver();

  const seen: Array<{ url: string; headers: Record<string, string> }> = [];
  const capture = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const h: Record<string, string> = {};
    new Headers(init?.headers).forEach((v, k) => {
      h[k] = v;
    });
    seen.push({ url: String(input), headers: h });
    return new Response(JSON.stringify({ active_source: "neon_marts", db_connected: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;

  const body = {
    jsonrpc: "2.0",
    id: 7,
    method: "tools/call",
    params: {
      name: "torre_qualidade_dados",
      arguments: {},
      _meta: REQUEST_META,
    },
  };
  const req = new Request("https://mktplace-gobeaute.vercel.app/api/mcp", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      "mcp-method": "tools/call",
      // O transporte 2026-07-28 exige `Mcp-Name` concordando com
      // `params.name` — mesma familia da regra do `Mcp-Method`.
      "mcp-name": "torre_qualidade_dados",
      authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });

  await handleMcpRequest(req, PROD_ENV, {
    fetchImpl: capture,
    verifierOptions: { keyResolver: keyResolver as never },
  });

  assert.ok(seen.length > 0, "a tool precisa ter chamado o backend");
  for (const c of seen) {
    const serialized = JSON.stringify(c);
    assert.ok(!serialized.includes(token), "o bearer nao pode viajar ao backend");
    assert.ok(!("authorization" in c.headers), "nenhum header Authorization upstream");
    assert.ok(!serialized.includes("auth0|"), "nenhum subject upstream");
    assert.ok(!serialized.includes("permissions"), "nenhuma claim upstream");
    assert.ok(!c.url.includes("access_token") && !c.url.includes("token="));
  }
});

// ===========================================================================
// Fronteira de ambiente com OAuth
// ===========================================================================

test("producao com OAuth completo torna-se elegivel", () => {
  const d = evaluateAccess(PROD_ENV);
  assert.equal(d.allowed, true);
  assert.equal(d.allowed === true && d.mode, "production");
  assert.ok(d.allowed === true && d.auth !== null, "auth obrigatoria em producao");
});

test("producao com OAuth INCOMPLETO -> 404 generico", async () => {
  for (const missing of [
    "ORACLE_AUTH_ISSUER",
    "ORACLE_AUTH_AUDIENCE",
    "ORACLE_AUTH_REQUIRED_SCOPE",
  ] as const) {
    const env = { ...PROD_ENV, [missing]: undefined };
    const d = evaluateAccess(env);
    assert.equal(d.allowed, false, `sem ${missing} deve negar`);
    assert.equal(d.allowed === false && d.reason, "missing_oauth_config");

    const { res, calls } = await call(undefined, env);
    assert.equal(res.status, 404, "404 generico, nao 401");
    assert.equal(res.headers.get("www-authenticate"), null, "nada e' anunciado");
    assert.equal(calls.length, 0);
  }
});

test("producao com issuer de host errado -> 404 (nao 401)", async () => {
  const env = { ...PROD_ENV, ORACLE_AUTH_ISSUER: "https://atacante.invalid/" };
  const { res } = await call(undefined, env);
  assert.equal(res.status, 404);
});

test("producao sem ORACLE_MCP_ENABLED continua negada", () => {
  const d = evaluateAccess({ ...PROD_ENV, ORACLE_MCP_ENABLED: undefined });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "not_enabled");
});

test("producao com backend divergente continua negada", () => {
  const d = evaluateAccess({ ...PROD_ENV, MCP_BACKEND_API_URL: "https://atacante.invalid" });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "missing_backend_config");
});

test("stub NUNCA vale em producao, mesmo com OAuth completo", () => {
  const d = evaluateAccess(PROD_ENV, { testIdentity: { subject: "fake" } });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "stub_not_allowed");
});

test("Preview segue fail-closed MESMO com OAuth completo", async () => {
  for (const hosted of [
    { VERCEL_ENV: "preview" },
    { VERCEL_ENV: "development" },
    { VERCEL_TARGET_ENV: "staging-qa" },
    { VERCEL: "1" },
  ]) {
    const env: AccessEnv = {
      ...PROD_ENV,
      NODE_ENV: "development",
      VERCEL: undefined,
      VERCEL_ENV: undefined,
      VERCEL_TARGET_ENV: undefined,
      ...hosted,
    };
    const d = evaluateAccess(env);
    assert.equal(d.allowed, false, `${JSON.stringify(hosted)} deve negar`);
    assert.equal(d.allowed === false && d.reason, "hosted_deployment_disabled");

    const { res, calls } = await call(undefined, env);
    assert.equal(res.status, 404);
    assert.equal(calls.length, 0);
  }
});

test("local sem OAuth mantem o comportamento do OM1 (sem auth)", () => {
  const d = evaluateAccess({
    NODE_ENV: "development",
    ORACLE_MCP_ENABLED: "1",
    MCP_BACKEND_API_URL: "https://mktplace-api.onrender.com",
  });
  assert.equal(d.allowed, true);
  assert.equal(d.allowed === true && d.mode, "development");
  assert.equal(d.allowed === true && d.auth, null, "sem OAuth local, sem exigencia de bearer");
});

test("local COM OAuth configurado passa a exigir bearer", async () => {
  const env: AccessEnv = {
    NODE_ENV: "development",
    ORACLE_MCP_ENABLED: "1",
    MCP_BACKEND_API_URL: "https://mktplace-api.onrender.com",
    ...OAUTH_ENV,
  };
  const d = evaluateAccess(env);
  assert.ok(d.allowed === true && d.auth !== null);

  const { res } = await call(undefined, env);
  assert.equal(res.status, 401, "OAuth configurado => bearer exigido tambem local");
});

test("GET e DELETE seguem a MESMA fronteira", async () => {
  const keyResolver = await localKeyResolver();
  const mk = (method: string) =>
    new Request("https://mktplace-gobeaute.vercel.app/api/mcp", { method });

  for (const method of ["GET", "DELETE"]) {
    // Sem token: 401 antes de qualquer coisa.
    const spy = spyFetch();
    const unauth = await handleMcpRequest(mk(method), PROD_ENV, {
      fetchImpl: spy.impl,
      verifierOptions: { keyResolver: keyResolver as never },
    });
    assert.equal(unauth.status, 401, `${method} sem token`);
    assert.equal(spy.calls.length, 0);

    // Producao sem OAuth: 404, nao 401.
    const noOauth = await handleMcpRequest(
      mk(method),
      { ...PROD_ENV, ORACLE_AUTH_ISSUER: undefined },
      { verifierOptions: { keyResolver: keyResolver as never } },
    );
    assert.equal(noOauth.status, 404, `${method} sem OAuth`);
  }
});

// ===========================================================================
// Anti-escopo: nada de authorization server proprio, tools seguem read-only
// ===========================================================================

test("nenhum endpoint proprio de OAuth foi criado", () => {
  const app = resolve(HERE, "../app");
  for (const forbidden of [
    "api/mcp/register", "api/mcp/authorize", "api/mcp/token", "api/mcp/callback",
    "api/oauth", "api/auth", "api/login", "oauth", "register",
  ]) {
    assert.ok(!existsSync(resolve(app, forbidden)), `${forbidden} nao pode existir`);
  }
});

test("nenhum Client ID/Secret e' lido do ambiente", async () => {
  const { readFileSync } = await import("node:fs");
  for (const f of ["access.ts", "oauth.ts", "handler.ts", "metadata-route.ts"]) {
    const src = readFileSync(resolve(HERE, "../src/server/oracle", f), "utf8");
    for (const forbidden of [
      "CLIENT_SECRET", "CLIENT_ID", "GOOGLE_CLIENT", "AUTH0_CLIENT",
      "client_secret", "NEXT_PUBLIC_",
    ]) {
      assert.ok(!src.includes(forbidden), `${f} nao pode referenciar ${forbidden}`);
    }
  }
});

test("as cinco tools continuam read-only e sem escrita", async () => {
  const { ORACLE_TOOLS } = await import("../src/server/oracle/tools.ts");
  assert.equal(ORACLE_TOOLS.length, 5);
  const serialized = JSON.stringify(ORACLE_TOOLS.map((t) => ({ n: t.name, d: t.config.title })));
  for (const forbidden of ["create", "update", "delete", "write", "insert"]) {
    assert.ok(!serialized.toLowerCase().includes(forbidden), `sem verbo ${forbidden}`);
  }
});

// ===========================================================================
// Correcao de seguranca — matriz REALISTA de ambientes
//
// A Vercel executa **Preview com NODE_ENV=production**. Toda esta secao usa
// NODE_ENV=production de proposito: classificar por NODE_ENV deixaria Preview
// exposto, e usar NODE_ENV=development como contraprova mascarava o defeito.
// ===========================================================================

/** Base de deployment: producao-like no runtime, tudo configurado. */
const DEPLOY_BASE: AccessEnv = {
  NODE_ENV: "production",
  ORACLE_MCP_ENABLED: "1",
  MCP_BACKEND_API_URL: "https://mktplace-api.onrender.com",
  ORACLE_AUTH_ISSUER: ISSUER,
  ORACLE_AUTH_AUDIENCE: AUDIENCE,
  ORACLE_AUTH_REQUIRED_SCOPE: SCOPE,
};

test("ambiente: producao Vercel real e' elegivel (NODE_ENV=production + VERCEL_ENV=production)", () => {
  const d = evaluateAccess({ ...DEPLOY_BASE, VERCEL: "1", VERCEL_ENV: "production" });
  assert.equal(d.allowed, true);
  assert.equal(d.allowed === true && d.mode, "production");
  assert.ok(d.allowed === true && d.auth !== null);
});

test("ambiente: PREVIEW com NODE_ENV=production e OAuth completo -> 404", async () => {
  const env = { ...DEPLOY_BASE, VERCEL: "1", VERCEL_ENV: "preview" };
  const d = evaluateAccess(env);
  assert.equal(d.allowed, false, "Preview NUNCA e' elegivel");
  assert.equal(d.allowed === false && d.reason, "hosted_deployment_disabled");

  const { res, calls } = await call(undefined, env);
  assert.equal(res.status, 404);
  assert.equal(res.headers.get("www-authenticate"), null, "nada e' anunciado em Preview");
  assert.equal(calls.length, 0);
});

test("ambiente: VERCEL=1 sem VERCEL_ENV confiavel -> 404", async () => {
  const env = { ...DEPLOY_BASE, VERCEL: "1" };
  const d = evaluateAccess(env);
  assert.equal(d.allowed, false, "na duvida sobre o deployment, negar");
  assert.equal(d.allowed === false && d.reason, "hosted_deployment_disabled");
  assert.equal((await call(undefined, env)).res.status, 404);
});

test("ambiente: VERCEL_TARGET_ENV=staging-qa -> 404", async () => {
  for (const env of [
    { ...DEPLOY_BASE, VERCEL_TARGET_ENV: "staging-qa" },
    { ...DEPLOY_BASE, VERCEL: "1", VERCEL_TARGET_ENV: "staging-qa" },
    // Mesmo com VERCEL_ENV=production: um target nomeado nao e' producao.
    { ...DEPLOY_BASE, VERCEL: "1", VERCEL_ENV: "production", VERCEL_TARGET_ENV: "staging-qa" },
  ]) {
    const d = evaluateAccess(env);
    assert.equal(d.allowed, false, JSON.stringify(env));
    assert.equal(d.allowed === false && d.reason, "hosted_deployment_disabled");
    assert.equal((await call(undefined, env)).res.status, 404);
  }
});

test("ambiente: VERCEL_ENV=development -> 404", async () => {
  const env = { ...DEPLOY_BASE, VERCEL: "1", VERCEL_ENV: "development" };
  assert.equal(evaluateAccess(env).allowed, false);
  assert.equal((await call(undefined, env)).res.status, 404);
});

test("ambiente: VERCEL_TARGET_ENV=production com VERCEL_ENV=production e' elegivel", () => {
  const d = evaluateAccess({
    ...DEPLOY_BASE,
    VERCEL: "1",
    VERCEL_ENV: "production",
    VERCEL_TARGET_ENV: "production",
  });
  assert.equal(d.allowed, true);
});

test("ambiente: NODE_ENV=production SEM sinal de plataforma e' host desconhecido -> 404", async () => {
  const env = { ...DEPLOY_BASE };
  const d = evaluateAccess(env);
  assert.equal(d.allowed, false, "host desconhecido pode estar exposto");
  assert.equal(d.allowed === false && d.reason, "environment_not_permitted");
  assert.equal((await call(undefined, env)).res.status, 404);
});

test("ambiente: NODE_ENV ausente tambem e' host desconhecido -> 404", () => {
  const { NODE_ENV: _drop, ...noNodeEnv } = DEPLOY_BASE;
  const d = evaluateAccess(noNodeEnv as AccessEnv);
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "environment_not_permitted");
});

test("ambiente: nenhuma flag, backend ou OAuth destrava Preview", async () => {
  const variants: AccessEnv[] = [
    { ...DEPLOY_BASE, VERCEL: "1", VERCEL_ENV: "preview", ORACLE_MCP_ENABLED: "1" },
    {
      ...DEPLOY_BASE,
      VERCEL: "1",
      VERCEL_ENV: "preview",
      MCP_BACKEND_API_URL: "https://mktplace-api.onrender.com",
    },
    { ...DEPLOY_BASE, VERCEL: "1", VERCEL_ENV: "preview", ORACLE_AUTH_REQUIRED_SCOPE: SCOPE },
  ];
  for (const env of variants) {
    assert.equal(evaluateAccess(env).allowed, false);
    const { res, calls } = await call(undefined, env);
    assert.equal(res.status, 404);
    assert.equal(calls.length, 0);
  }
});

test("ambiente: local dev/test seguem permitidos com habilitacao explicita", () => {
  for (const nodeEnv of ["development", "test"]) {
    const d = evaluateAccess({
      NODE_ENV: nodeEnv,
      ORACLE_MCP_ENABLED: "1",
      MCP_BACKEND_API_URL: "https://mktplace-api.onrender.com",
    });
    assert.equal(d.allowed, true, nodeEnv);
    assert.equal(d.allowed === true && d.auth, null, "sem OAuth local, sem bearer exigido");
  }
});

// ===========================================================================
// Correcao de seguranca — permission canonica fail-closed
// ===========================================================================

test("scope canonico: a constante e' oracle:read", () => {
  assert.equal(CANONICAL_ORACLE_SCOPE, "oracle:read");
});

test("scope canonico: somente oracle:read e' configuracao valida", () => {
  assert.ok(parseOAuthConfig({ ...OAUTH_ENV, ORACLE_AUTH_REQUIRED_SCOPE: "oracle:read" }));
  // Trim e' aceito; o valor precisa ser exatamente o canonico.
  assert.ok(parseOAuthConfig({ ...OAUTH_ENV, ORACLE_AUTH_REQUIRED_SCOPE: "  oracle:read  " }));

  const invalid = [
    "oracle:reader",
    "oracle:read:all",
    "openid",
    "profile",
    "read",
    "oracle:write",
    "oracle:read openid",
    "openid oracle:read",
    "oracle read",
    "",
    "   ",
    undefined,
  ];
  for (const s of invalid) {
    assert.equal(
      parseOAuthConfig({ ...OAUTH_ENV, ORACLE_AUTH_REQUIRED_SCOPE: s as string }),
      null,
      `scope deveria ser invalido: ${JSON.stringify(s)}`,
    );
  }
});

test("scope canonico: config invalida em producao -> 404 sem WWW-Authenticate", async () => {
  for (const s of ["openid", "oracle:reader", "read", ""]) {
    const env = { ...PROD_ENV, ORACLE_AUTH_REQUIRED_SCOPE: s };
    const d = evaluateAccess(env);
    assert.equal(d.allowed, false, `scope ${s}`);
    assert.equal(d.allowed === false && d.reason, "missing_oauth_config");

    const { res, calls } = await call(undefined, env);
    assert.equal(res.status, 404, `scope ${s} deveria dar 404`);
    assert.equal(res.headers.get("www-authenticate"), null, "nada anunciado");
    assert.equal(calls.length, 0);
  }
});

test("scope canonico: a config resolvida usa a CONSTANTE, nao o valor do ambiente", () => {
  const cfg = parseOAuthConfig({ ...OAUTH_ENV, ORACLE_AUTH_REQUIRED_SCOPE: "  oracle:read  " });
  assert.equal(cfg!.requiredScope, CANONICAL_ORACLE_SCOPE, "sem espacos residuais");
});

test("scope canonico: token continua exigindo oracle:read como elemento completo", async () => {
  // Config canonica; o token e' que varia.
  for (const p of ["oracle:reader", "oracle:read:all", "openid", "read"]) {
    const token = await mintToken({ permissions: [p], scope: p });
    const { res } = await call(`Bearer ${token}`);
    assert.equal(res.status, 403, `token com ${p} nao pode autorizar`);
  }
  const ok = await mintToken();
  assert.equal((await call(`Bearer ${ok}`)).res.status, 200);
});
