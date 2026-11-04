// AUTH-GOOGLE-3 — modulo de sessao do Next. Chaves e segredos SINTETICOS.
import { test } from "node:test";
import assert from "node:assert/strict";

import { createLocalJWKSet, decodeJwt, exportJWK, generateKeyPair, SignJWT } from "jose";

import {
  SESSION_AUDIENCE, SESSION_COOKIE, SESSION_ISSUER, SESSION_TTL_SECONDS,
  allowedDomains, authEnabled, createSessionToken, isAllowedEmail, readCookie,
  sessionCookieHeader, verifyGoogleIdToken, verifySessionToken,
  type AuthEnv,
} from "../src/server/auth/session.ts";

const SECRET = "s".repeat(40);
const CLIENT_ID = "client-id-sintetico.apps.googleusercontent.com";
const ENV: AuthEnv = { AUTH_GOOGLE_ID: CLIENT_ID, AUTH_SECRET: SECRET, NODE_ENV: "test" };

test("constantes do contrato", () => {
  assert.equal(SESSION_COOKIE, "torre_session");
  assert.equal(SESSION_ISSUER, "torre-mktplace");
  assert.equal(SESSION_AUDIENCE, "torre-mktplace-api");
  assert.equal(SESSION_TTL_SECONDS, 604800);
});

test("authEnabled depende de AUTH_GOOGLE_ID", () => {
  assert.equal(authEnabled(ENV), true);
  assert.equal(authEnabled({}), false);
  assert.equal(authEnabled({ AUTH_GOOGLE_ID: "  " }), false);
});

test("allowedDomains padrao e override", () => {
  assert.deepEqual(allowedDomains({}), ["gocase.com", "gobeaute.com.br"]);
  assert.deepEqual(allowedDomains({ ALLOWED_EMAIL_DOMAINS: " A.com , b.com " }), ["a.com", "b.com"]);
});

test("isAllowedEmail: match exato do dominio", () => {
  assert.equal(isAllowedEmail("JOAO@GOCASE.COM", ENV), true);
  assert.equal(isAllowedEmail("x@gocase.com.evil.com", ENV), false);
  assert.equal(isAllowedEmail("x@mail.gocase.com", ENV), false);
  assert.equal(isAllowedEmail("x@gocase.com@evil.com", ENV), false);
  assert.equal(isAllowedEmail(null, ENV), false);
  assert.equal(isAllowedEmail("semarroba", ENV), false);
});

test("sessao: ida e volta com iss/aud", async () => {
  const token = await createSessionToken({ email: "joao@gocase.com" }, ENV);
  assert.deepEqual(await verifySessionToken(token, ENV), { email: "joao@gocase.com" });
  const p = decodeJwt(token);
  assert.equal(p.iss, "torre-mktplace");
  assert.equal(p.aud, "torre-mktplace-api");
  assert.equal(p.exp! - p.iat!, 604800);
});

test("sessao: name e picture voltam", async () => {
  const token = await createSessionToken({ email: "a@gocase.com", name: "A", picture: "https://x/y.png" }, ENV);
  assert.deepEqual(await verifySessionToken(token, ENV), { email: "a@gocase.com", name: "A", picture: "https://x/y.png" });
});

test("sessao: dominio deixou de ser permitido -> null", async () => {
  const token = await createSessionToken({ email: "joao@gocase.com" }, ENV);
  assert.equal(await verifySessionToken(token, { ...ENV, ALLOWED_EMAIL_DOMAINS: "gobeaute.com.br" }), null);
});

test("sessao: segredo curto", async () => {
  const curto = { ...ENV, AUTH_SECRET: "curto" };
  await assert.rejects(createSessionToken({ email: "joao@gocase.com" }, curto));
  const token = await createSessionToken({ email: "joao@gocase.com" }, ENV);
  assert.equal(await verifySessionToken(token, curto), null);
});

test("sessao: token invalido, vazio, outro segredo, outro aud, nunca lanca", async () => {
  assert.equal(await verifySessionToken(undefined, ENV), null);
  assert.equal(await verifySessionToken("lixo", ENV), null);
  const token = await createSessionToken({ email: "joao@gocase.com" }, ENV);
  assert.equal(await verifySessionToken(token, { ...ENV, AUTH_SECRET: "t".repeat(40) }), null);
  const outroAud = await new SignJWT({ email: "joao@gocase.com" })
    .setProtectedHeader({ alg: "HS256" }).setIssuedAt().setExpirationTime("1h")
    .setIssuer(SESSION_ISSUER).setAudience("outro").sign(new TextEncoder().encode(SECRET));
  assert.equal(await verifySessionToken(outroAud, ENV), null);
});

async function googleSetup() {
  const { publicKey, privateKey } = await generateKeyPair("RS256");
  const jwk = { ...(await exportJWK(publicKey)), alg: "RS256", kid: "k1" };
  const keys = createLocalJWKSet({ keys: [jwk] });
  const sign = (claims: Record<string, unknown>, o: { iss?: string; aud?: string; exp?: string; key?: typeof privateKey } = {}) =>
    new SignJWT(claims).setProtectedHeader({ alg: "RS256", kid: "k1" })
      .setIssuedAt().setIssuer(o.iss ?? "https://accounts.google.com")
      .setAudience(o.aud ?? CLIENT_ID).setExpirationTime(o.exp ?? "1h").sign(o.key ?? privateKey);
  return { keys, sign };
}

test("verifyGoogleIdToken: valido", async () => {
  const { keys, sign } = await googleSetup();
  const t = await sign({ email: "Joao@Gocase.com", email_verified: true, name: "Joao", picture: "p" });
  assert.deepEqual(await verifyGoogleIdToken(t, ENV, keys),
    { email: "joao@gocase.com", emailVerified: true, name: "Joao", picture: "p" });
  const t2 = await sign({ email: "a@gocase.com", email_verified: "true" }, { iss: "accounts.google.com" });
  assert.equal((await verifyGoogleIdToken(t2, ENV, keys)).emailVerified, true);
});

test("verifyGoogleIdToken: rejeita aud, issuer, expirado, outra chave", async () => {
  const { keys, sign } = await googleSetup();
  const c = { email: "a@gocase.com", email_verified: true };
  await assert.rejects(verifyGoogleIdToken(await sign(c, { aud: "outro" }), ENV, keys));
  await assert.rejects(verifyGoogleIdToken(await sign(c, { iss: "https://evil.example" }), ENV, keys));
  await assert.rejects(verifyGoogleIdToken(await sign(c, { exp: "-1h" }), ENV, keys));
  const outra = await generateKeyPair("RS256");
  await assert.rejects(verifyGoogleIdToken(await sign(c, { key: outra.privateKey }), ENV, keys));
  await assert.rejects(verifyGoogleIdToken("x", {}, keys));
});

test("sessionCookieHeader", () => {
  const prod = sessionCookieHeader("tok", { NODE_ENV: "production" });
  for (const a of ["torre_session=tok", "HttpOnly", "Secure", "SameSite=Lax", "Path=/", "Max-Age=604800"]) {
    assert.ok(prod.includes(a), a);
  }
  assert.ok(!sessionCookieHeader("tok", { NODE_ENV: "development" }).includes("Secure"));
  assert.ok(sessionCookieHeader("", ENV, 0).includes("Max-Age=0"));
});

test("readCookie", () => {
  const req = new Request("http://x/", { headers: { cookie: "a=1; torre_session=abc.def; b=2" } });
  assert.equal(readCookie(req, "torre_session"), "abc.def");
  assert.equal(readCookie(req, "nada"), undefined);
  assert.equal(readCookie(new Request("http://x/"), "a"), undefined);
});
