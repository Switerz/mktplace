// AUTH-GOOGLE-4 — handlers de /api/auth/*. Chaves e segredos SINTETICOS.
import { test } from "node:test";
import assert from "node:assert/strict";

import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT } from "jose";

import { handleGoogleLogin, handleLogout, handleMe } from "../src/server/auth/handlers.ts";
import { createSessionToken, type AuthEnv } from "../src/server/auth/session.ts";

const SECRET = "s".repeat(40);
const CLIENT_ID = "client-id-sintetico.apps.googleusercontent.com";
const ENV: AuthEnv = { AUTH_GOOGLE_ID: CLIENT_ID, AUTH_SECRET: SECRET, NODE_ENV: "test" };

async function googleSetup() {
  const { publicKey, privateKey } = await generateKeyPair("RS256");
  const jwk = { ...(await exportJWK(publicKey)), alg: "RS256", kid: "k1" };
  const keys = createLocalJWKSet({ keys: [jwk] });
  const sign = (claims: Record<string, unknown>) =>
    new SignJWT(claims).setProtectedHeader({ alg: "RS256", kid: "k1" })
      .setIssuedAt().setIssuer("https://accounts.google.com")
      .setAudience(CLIENT_ID).setExpirationTime("1h").sign(privateKey);
  return { keys, sign };
}

function post(body: unknown, raw = false): Request {
  return new Request("http://x/api/auth/google", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: raw ? (body as string) : JSON.stringify(body),
  });
}

function assertNoStore(res: Response) {
  assert.equal(res.headers.get("cache-control"), "no-store");
}

test("login: auth desligada -> 404", async () => {
  const res = await handleGoogleLogin(post({ credential: "x" }), {});
  assert.equal(res.status, 404);
  assert.deepEqual(await res.json(), { error: "auth_desligada" });
  assertNoStore(res);
});

test("login: sem credential -> 400", async () => {
  for (const b of [{}, { credential: 5 }, { credential: "" }]) {
    const res = await handleGoogleLogin(post(b), ENV);
    assert.equal(res.status, 400);
    assert.deepEqual(await res.json(), { error: "credential_ausente" });
    assertNoStore(res);
  }
  const res = await handleGoogleLogin(post("nao-json", true), ENV);
  assert.equal(res.status, 400);
});

test("login: credencial invalida -> 401 sem vazar o credential", async () => {
  const { keys } = await googleSetup();
  const logs: string[] = [];
  const orig = [console.info, console.error, console.warn, console.log];
  console.info = console.error = console.warn = console.log = (...a: unknown[]) => { logs.push(a.join(" ")); };
  let res: Response; let text: string;
  try {
    res = await handleGoogleLogin(post({ credential: "credencial-secreta-lixo" }), ENV, keys);
    text = await res.text();
  } finally {
    [console.info, console.error, console.warn, console.log] = orig;
  }
  assert.equal(res.status, 401);
  assert.deepEqual(JSON.parse(text), { error: "credencial_invalida" });
  assert.ok(!text.includes("credencial-secreta-lixo"));
  assert.ok(!logs.join("\n").includes("credencial-secreta-lixo"));
  assert.equal(res.headers.get("set-cookie"), null);
  assertNoStore(res);
});

test("login: email nao verificado -> 403", async () => {
  const { keys, sign } = await googleSetup();
  const res = await handleGoogleLogin(post({ credential: await sign({ email: "a@gocase.com", email_verified: false }) }), ENV, keys);
  assert.equal(res.status, 403);
  assert.deepEqual(await res.json(), { error: "email_nao_verificado" });
  assert.equal(res.headers.get("set-cookie"), null);
  assertNoStore(res);
});

test("login: token sem email nunca recebe cookie", async () => {
  const { keys, sign } = await googleSetup();
  const res = await handleGoogleLogin(post({ credential: await sign({ email_verified: true }) }), ENV, keys);
  assert.equal(res.status, 403);
  assert.equal(res.headers.get("set-cookie"), null);
  assertNoStore(res);
});

test("login: dominio barrado -> 403 com mensagem", async () => {
  const { keys, sign } = await googleSetup();
  const res = await handleGoogleLogin(post({ credential: await sign({ email: "x@gmail.com", email_verified: true }) }), ENV, keys);
  assert.equal(res.status, 403);
  assert.deepEqual(await res.json(), {
    error: "dominio_nao_permitido",
    message: "Acesso restrito a contas @gocase.com / @gobeaute.com.br.",
  });
  assert.equal(res.headers.get("set-cookie"), null);
  assertNoStore(res);
});

test("login: ok -> 200 + cookie + log sem credential", async () => {
  const { keys, sign } = await googleSetup();
  const credential = await sign({ email: "Joao@Gocase.com", email_verified: true, name: "Joao", picture: "p" });
  const calls: unknown[][] = [];
  const orig = console.info;
  console.info = (...a: unknown[]) => { calls.push(a); };
  let res: Response; let text: string;
  try {
    res = await handleGoogleLogin(post({ credential }), ENV, keys);
    text = await res.text();
  } finally {
    console.info = orig;
  }
  assert.equal(res.status, 200);
  assert.deepEqual(JSON.parse(text), { ok: true, email: "joao@gocase.com", name: "Joao", picture: "p" });
  const cookie = res.headers.get("set-cookie") ?? "";
  assert.ok(cookie.startsWith("torre_session="));
  assert.ok(cookie.includes("HttpOnly") && cookie.includes("Max-Age=604800"));
  assertNoStore(res);
  assert.deepEqual(calls, [["auth_login email=%s", "joao@gocase.com"]]);
  assert.ok(!text.includes(credential));
});

test("logout: sempre 200 e cookie Max-Age=0", async () => {
  const res = await handleLogout(new Request("http://x/api/auth/logout", { method: "POST" }), ENV);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true });
  const cookie = res.headers.get("set-cookie") ?? "";
  assert.ok(cookie.startsWith("torre_session=;"));
  assert.ok(cookie.includes("Max-Age=0"));
  assertNoStore(res);
  const off = await handleLogout(new Request("http://x/api/auth/logout", { method: "POST" }), {});
  assert.equal(off.status, 200);
});

test("me: auth desligada", async () => {
  const res = await handleMe(new Request("http://x/api/auth/me"), {});
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { authEnabled: false, email: "dev@local" });
  assertNoStore(res);
});

test("me: sessao valida", async () => {
  const token = await createSessionToken({ email: "joao@gocase.com", name: "Joao", picture: "p" }, ENV);
  const res = await handleMe(new Request("http://x/api/auth/me", { headers: { cookie: `torre_session=${token}` } }), ENV);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { authEnabled: true, email: "joao@gocase.com", name: "Joao", picture: "p" });
  assertNoStore(res);
});

test("me: sem sessao ou sessao invalida -> 401 com clientId", async () => {
  for (const headers of [{}, { cookie: "torre_session=lixo" }] as HeadersInit[]) {
    const res = await handleMe(new Request("http://x/api/auth/me", { headers }), ENV);
    assert.equal(res.status, 401);
    assert.deepEqual(await res.json(), { error: "nao_autenticado", clientId: CLIENT_ID });
    assertNoStore(res);
  }
});

test("login: content-type text/plain -> 415 sem cookie (anti login-CSRF)", async () => {
  const req = new Request("http://x/api/auth/google", {
    method: "POST",
    headers: { "content-type": "text/plain" },
    body: JSON.stringify({ credential: "x" }),
  });
  const res = await handleGoogleLogin(req, ENV);
  assert.equal(res.status, 415);
  assert.deepEqual(await res.json(), { error: "content_type_invalido" });
  assertNoStore(res);
  assert.equal(res.headers.get("set-cookie"), null);
});

test("login: application/json; charset=utf-8 segue o fluxo normal", async () => {
  const req = new Request("http://x/api/auth/google", {
    method: "POST",
    headers: { "content-type": "Application/JSON; charset=utf-8" },
    body: JSON.stringify({}),
  });
  const res = await handleGoogleLogin(req, ENV);
  assert.equal(res.status, 400); // passou do content-type; falta credential
});
