/**
 * Gate OCTAPRICE-3 — login corporativo (BFF) da aba B2B / Revendedores.
 *
 * Sem rede: tokens SIMULADOS assinados com chave RSA local; Auth0 e FastAPI
 * substituidos por um `fetch` falso. Nenhum tenant e nenhuma API real.
 *
 * O que estes testes existem para impedir:
 *  - o navegador ver o access token (cookie legivel, corpo, localStorage);
 *  - token forjado/alheio/expirado virar sessao;
 *  - dado B2B sair sem sessao valida e sem `b2b_revenda:read`;
 *  - a superficie de login existir com a flag desligada;
 *  - redirect aberto (`returnTo`) ou rota/parametro arbitrario no proxy.
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import { SignJWT, generateKeyPair, type JWTVerifyGetKey } from "jose";

import {
  COOKIE_SESSAO,
  COOKIE_TX,
  ESCOPO_B2B,
  ROTA_ABA,
  destinoSeguro,
  handleCallback,
  handleLogin,
  handleLogout,
  handleProxy,
  handleSessao,
  parseB2BAuthConfig,
  selar,
  type B2BAuthDeps,
  type B2BAuthEnv,
} from "../src/server/b2b-auth/bff.ts";
import { acessoDoErro } from "../src/lib/b2b-revenda.ts";

const RAIZ = join(import.meta.dirname, "..");
const ISSUER = "https://gobeaute-oraculo.us.auth0.com/";
const AUD = "https://mktplace-api.onrender.com/api/v1/b2b-revenda";
const CLIENT = "client-id-de-teste";
const ENV: B2BAuthEnv = {
  NEXT_PUBLIC_B2B_REVENDA_MODE: "api",
  B2B_AUTH_ISSUER: ISSUER,
  B2B_AUTH_CLIENT_ID: CLIENT,
  B2B_AUTH_CLIENT_SECRET: "segredo-do-cliente-de-teste-123",
  B2B_AUTH_AUDIENCE: AUD,
  B2B_AUTH_SESSION_SECRET: "segredo-de-sessao-de-teste-com-mais-de-32-caracteres",
  B2B_AUTH_APP_BASE_URL: "https://torre.exemplo",
  B2B_API_BASE_URL: "https://api.exemplo",
};
const AGORA = Date.UTC(2026, 9, 21, 15, 0, 0);
const S = Math.floor(AGORA / 1000);

const { privateKey, publicKey } = await generateKeyPair("RS256");
const outra = await generateKeyPair("RS256");

type Chamada = { url: string; init?: RequestInit };

function deps(respostas: Record<string, (c: Chamada) => Response> = {}): B2BAuthDeps & { chamadas: Chamada[] } {
  const chamadas: Chamada[] = [];
  return {
    chamadas,
    agoraMs: () => AGORA,
    chaves: () => (async () => publicKey) as JWTVerifyGetKey,
    fetch: (async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      chamadas.push({ url, init });
      const chave = Object.keys(respostas).find((k) => url.includes(k));
      if (!chave) throw new Error(`fetch inesperado: ${url}`);
      return respostas[chave]({ url, init });
    }) as typeof fetch,
  };
}

async function accessToken(o: { perm?: string[]; scope?: string; iss?: string; aud?: string; exp?: number; chave?: CryptoKey; sub?: string } = {}) {
  const claims: Record<string, unknown> = {};
  if (o.perm) claims.permissions = o.perm;
  if (o.scope) claims.scope = o.scope;
  return new SignJWT(claims)
    .setProtectedHeader({ alg: "RS256", kid: "k" })
    .setIssuer(o.iss ?? ISSUER)
    .setAudience(o.aud ?? AUD)
    .setSubject(o.sub ?? "google-oauth2|123")
    .setIssuedAt(S - 10)
    .setExpirationTime(o.exp ?? S + 3600)
    .sign(o.chave ?? privateKey);
}

async function idToken(nonce: string, o: { aud?: string; sub?: string } = {}) {
  return new SignJWT({ nonce, email: "pessoa@gobeaute.com.br" })
    .setProtectedHeader({ alg: "RS256", kid: "k" })
    .setIssuer(ISSUER)
    .setAudience(o.aud ?? CLIENT)
    .setSubject(o.sub ?? "google-oauth2|123")
    .setIssuedAt(S - 10)
    .setExpirationTime(S + 3600)
    .sign(privateKey);
}

const req = (path: string, cookies: Record<string, string> = {}) =>
  new Request(`https://torre.exemplo${path}`, {
    headers: Object.keys(cookies).length
      ? { cookie: Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join("; ") }
      : {},
  });

function setCookies(res: Response): Map<string, string> {
  const m = new Map<string, string>();
  for (const c of res.headers.getSetCookie()) {
    const [par] = c.split(";");
    const i = par.indexOf("=");
    m.set(par.slice(0, i), c);
  }
  return m;
}
const valorDe = (c: string) => c.split(";")[0].split("=").slice(1).join("=");

/** Faz o login inteiro e devolve o cookie de sessao. */
async function logar(at: string, o: { idAud?: string; idSub?: string; nonceErrado?: boolean } = {}) {
  const d0 = deps();
  const login = await handleLogin(req(`/api/b2b/auth/login?returnTo=${encodeURIComponent(ROTA_ABA)}`), ENV, d0);
  const loc = new URL(login.headers.get("location")!);
  const tx = valorDe(setCookies(login).get(COOKIE_TX)!);
  const state = loc.searchParams.get("state")!;
  const nonce = loc.searchParams.get("nonce")!;
  const idt = await idToken(o.nonceErrado ? "outro" : nonce, { aud: o.idAud, sub: o.idSub });
  const d = deps({ "/oauth/token": () => Response.json({ access_token: at, id_token: idt, token_type: "Bearer" }) });
  const cb = await handleCallback(req(`/api/b2b/auth/callback?code=abc&state=${state}`, { [COOKIE_TX]: tx }), ENV, d);
  return { cb, d, sessao: setCookies(cb).get(COOKIE_SESSAO) };
}

// ---------------------------------------------------------------------------
// Flag e configuracao
// ---------------------------------------------------------------------------
test("flag fora de 'api': TODA rota de login/BFF e' 404", async () => {
  for (const modo of [undefined, "", "fixture", "true", "API"]) {
    const env = { ...ENV, NEXT_PUBLIC_B2B_REVENDA_MODE: modo };
    for (const r of [
      await handleLogin(req("/api/b2b/auth/login"), env, deps()),
      await handleCallback(req("/api/b2b/auth/callback"), env, deps()),
      await handleLogout(req("/api/b2b/auth/logout"), env),
      await handleSessao(req("/api/b2b/auth/sessao"), env, deps()),
      await handleProxy(req("/api/b2b/revenda/resumo"), "resumo", env, deps()),
    ]) {
      assert.equal(r.status, 404, String(modo));
    }
  }
});

test("configuracao ausente ou invalida: 503 (fecha), sem redirect nem chamada", async () => {
  const quebras: Partial<B2BAuthEnv>[] = [
    { B2B_AUTH_ISSUER: "" },
    { B2B_AUTH_ISSUER: "https://outro-tenant.us.auth0.com/" },
    { B2B_AUTH_CLIENT_ID: "" },
    { B2B_AUTH_CLIENT_SECRET: "curto" },
    { B2B_AUTH_AUDIENCE: "http://nao-https" },
    { B2B_AUTH_SESSION_SECRET: "curto-demais" },
    { B2B_AUTH_APP_BASE_URL: "http://torre.exemplo" },
    { B2B_API_BASE_URL: "https://api.exemplo/caminho" },
  ];
  for (const q of quebras) {
    const env = { ...ENV, ...q };
    assert.equal(parseB2BAuthConfig(env), null, JSON.stringify(q));
    const d = deps();
    assert.equal((await handleLogin(req("/api/b2b/auth/login"), env, d)).status, 503);
    assert.equal((await handleProxy(req("/api/b2b/revenda/resumo"), "resumo", env, d)).status, 503);
    assert.equal(d.chamadas.length, 0);
  }
  assert.notEqual(parseB2BAuthConfig({ ...ENV, B2B_AUTH_APP_BASE_URL: "http://localhost:3000" }), null);
});

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------
test("login: authorize do Auth0 com PKCE S256, state, nonce e audience; tx em cookie seguro", async () => {
  const r = await handleLogin(req("/api/b2b/auth/login"), ENV, deps());
  assert.equal(r.status, 302);
  const loc = new URL(r.headers.get("location")!);
  assert.equal(`${loc.origin}${loc.pathname}`, `${ISSUER}authorize`);
  const p = loc.searchParams;
  assert.equal(p.get("response_type"), "code");
  assert.equal(p.get("client_id"), CLIENT);
  assert.equal(p.get("redirect_uri"), "https://torre.exemplo/api/b2b/auth/callback");
  assert.equal(p.get("audience"), AUD);
  assert.equal(p.get("code_challenge_method"), "S256");
  assert.ok((p.get("state") ?? "").length >= 40 && (p.get("nonce") ?? "").length >= 20);
  assert.ok(!loc.search.includes("client_secret") && !loc.search.includes(ENV.B2B_AUTH_CLIENT_SECRET!));
  const tx = setCookies(r).get(COOKIE_TX)!;
  for (const attr of ["HttpOnly", "Secure", "SameSite=Lax", "Path=/", "Max-Age=600"]) assert.ok(tx.includes(attr), attr);
  assert.ok(!tx.includes("Domain"));
  assert.equal(r.headers.get("cache-control"), "no-store");
});

test("returnTo: so' a propria aba; qualquer outra coisa vira a aba (sem redirect aberto)", () => {
  assert.equal(destinoSeguro(`${ROTA_ABA}?data=2026-10-20`), `${ROTA_ABA}?data=2026-10-20`);
  for (const ruim of ["https://evil.com", "//evil.com", "/canais", `${ROTA_ABA}x`, `${ROTA_ABA}/../../x`, "/\\evil", null]) {
    assert.equal(destinoSeguro(ruim), ROTA_ABA, String(ruim));
  }
});

// ---------------------------------------------------------------------------
// Callback com tokens simulados
// ---------------------------------------------------------------------------
test("autorizado com b2b_revenda:read: sessao criada, cookie seguro e opaco, sem token legivel", async () => {
  const at = await accessToken({ perm: [ESCOPO_B2B] });
  const { cb, d, sessao } = await logar(at);
  assert.equal(cb.status, 302);
  assert.equal(cb.headers.get("location"), `https://torre.exemplo${ROTA_ABA}`);
  assert.ok(sessao);
  for (const attr of ["HttpOnly", "Secure", "SameSite=Lax", "Path=/"]) assert.ok(sessao!.includes(attr), attr);
  assert.ok(!sessao!.includes(at), "o access token nao pode aparecer no cookie em claro");
  assert.equal(valorDe(sessao!).split(".").length, 5, "JWE compacto (criptografado)");
  assert.ok(setCookies(cb).get(COOKIE_TX)!.includes("Max-Age=0"), "tx apagado");
  // Troca do code: client secret no CORPO do POST ao Auth0, com o verifier PKCE.
  const troca = JSON.parse(String(d.chamadas[0].init?.body));
  assert.equal(troca.grant_type, "authorization_code");
  assert.equal(troca.client_secret, ENV.B2B_AUTH_CLIENT_SECRET);
  assert.ok(troca.code_verifier.length >= 40);
});

test("sessao: informa nome e permissao, NUNCA o token", async () => {
  const at = await accessToken({ scope: `openid ${ESCOPO_B2B}` });
  const { sessao } = await logar(at);
  const r = await handleSessao(req("/api/b2b/auth/sessao", { [COOKIE_SESSAO]: valorDe(sessao!) }), ENV, deps());
  const corpo = await r.json();
  assert.equal(corpo.autenticado, true);
  assert.equal(corpo.tem_permissao, true);
  assert.equal(corpo.nome, "pessoa@gobeaute.com.br");
  assert.ok(!JSON.stringify(corpo).includes(at));
  assert.equal(r.headers.get("cache-control"), "no-store");
});

const casosRuins: [string, () => Promise<string>, string][] = [
  ["expirado", () => accessToken({ perm: [ESCOPO_B2B], exp: S - 3600 }), "token_invalido"],
  ["issuer incorreto", () => accessToken({ perm: [ESCOPO_B2B], iss: "https://outro-tenant.us.auth0.com/" }), "token_invalido"],
  ["audience incorreta (token do MCP)", () => accessToken({ perm: [ESCOPO_B2B], aud: "https://mktplace-gobeaute.vercel.app/api/mcp" }), "token_invalido"],
  ["assinado por outra chave", () => accessToken({ perm: [ESCOPO_B2B], chave: outra.privateKey }), "token_invalido"],
];
for (const [nome, gerar, motivo] of casosRuins) {
  test(`callback recusa access token ${nome}: sem sessao, volta com ?login=${motivo}`, async () => {
    const { cb, sessao } = await logar(await gerar());
    assert.equal(cb.headers.get("location"), `https://torre.exemplo${ROTA_ABA}?login=${motivo}`);
    assert.equal(sessao, undefined);
  });
}

test("callback recusa id token com nonce errado, de outro client ou de outro sujeito", async () => {
  const at = await accessToken({ perm: [ESCOPO_B2B] });
  for (const o of [{ nonceErrado: true }, { idAud: "outro-client" }, { idSub: "google-oauth2|outra-pessoa" }]) {
    const { cb, sessao } = await logar(at, o);
    assert.equal(cb.headers.get("location"), `https://torre.exemplo${ROTA_ABA}?login=token_invalido`, JSON.stringify(o));
    assert.equal(sessao, undefined);
  }
});

test("callback recusa state errado, tx ausente e erro devolvido pelo Auth0", async () => {
  const login = await handleLogin(req("/api/b2b/auth/login"), ENV, deps());
  const tx = valorDe(setCookies(login).get(COOKIE_TX)!);
  const d = deps({ "/oauth/token": () => { throw new Error("nao deveria trocar"); } });
  const semTx = await handleCallback(req("/api/b2b/auth/callback?code=a&state=x"), ENV, d);
  assert.ok(semTx.headers.get("location")!.endsWith("?login=estado_invalido"));
  const stateErrado = await handleCallback(req("/api/b2b/auth/callback?code=a&state=forjado", { [COOKIE_TX]: tx }), ENV, d);
  assert.ok(stateErrado.headers.get("location")!.endsWith("?login=estado_invalido"));
  const negado = await handleCallback(req("/api/b2b/auth/callback?error=access_denied&state=x", { [COOKIE_TX]: tx }), ENV, d);
  assert.ok(negado.headers.get("location")!.endsWith("?login=negado"));
  assert.equal(d.chamadas.length, 0, "nenhuma troca de code sem state valido");
});

test("troca do code falhando (Auth0 4xx ou fora do ar) volta com ?login=troca_falhou", async () => {
  const login = await handleLogin(req("/api/b2b/auth/login"), ENV, deps());
  const tx = valorDe(setCookies(login).get(COOKIE_TX)!);
  const state = new URL(login.headers.get("location")!).searchParams.get("state");
  for (const resp of [() => new Response("{}", { status: 403 }), () => { throw new TypeError("rede"); }]) {
    const r = await handleCallback(req(`/api/b2b/auth/callback?code=a&state=${state}`, { [COOKIE_TX]: tx }), ENV, deps({ "/oauth/token": resp }));
    assert.ok(r.headers.get("location")!.endsWith("?login=troca_falhou"));
  }
});

// ---------------------------------------------------------------------------
// Proxy (BFF) para a FastAPI
// ---------------------------------------------------------------------------
test("sessao ausente: 401 sessao_ausente, FastAPI nao e' chamada", async () => {
  const d = deps();
  const r = await handleProxy(req("/api/b2b/revenda/resumo"), "resumo", ENV, d);
  assert.equal(r.status, 401);
  assert.deepEqual(await r.json(), { erro: "sessao_ausente" });
  assert.equal(d.chamadas.length, 0);
});

test("sessao expirada: 401 sessao_expirada e cookie apagado", async () => {
  const at = await accessToken({ perm: [ESCOPO_B2B] });
  const vencida = await selar({ sub: "x", nome: "x", at, perm: true }, "b2b-sessao", ENV.B2B_AUTH_SESSION_SECRET!, S - 60);
  const d = deps();
  const r = await handleProxy(req("/api/b2b/revenda/resumo", { [COOKIE_SESSAO]: vencida }), "resumo", ENV, d);
  assert.equal(r.status, 401);
  assert.deepEqual(await r.json(), { erro: "sessao_expirada" });
  assert.ok(setCookies(r).get(COOKIE_SESSAO)!.includes("Max-Age=0"));
  assert.equal(d.chamadas.length, 0);
  const s = await handleSessao(req("/api/b2b/auth/sessao", { [COOKIE_SESSAO]: vencida }), ENV, deps());
  assert.deepEqual(await s.json(), { autenticado: false, motivo: "sessao_expirada" });
});

test("sessao adulterada ou de outra chave: 401 sessao_invalida", async () => {
  const at = await accessToken({ perm: [ESCOPO_B2B] });
  const { sessao } = await logar(at);
  const v = valorDe(sessao!);
  const adulterada = v.slice(0, -4) + (v.endsWith("AAAA") ? "BBBB" : "AAAA");
  const outraChave = await selar({ sub: "x", at, perm: true }, "b2b-sessao", "outro-segredo-de-sessao-com-32-caracteres-ou-mais", S + 600);
  for (const c of [adulterada, outraChave, "lixo"]) {
    const r = await handleProxy(req("/api/b2b/revenda/resumo", { [COOKIE_SESSAO]: c }), "resumo", ENV, deps());
    assert.equal(r.status, 401);
    assert.deepEqual(await r.json(), { erro: "sessao_invalida" });
  }
});

test("autenticado SEM permission: 403 sem chamar a FastAPI", async () => {
  const at = await accessToken({ scope: "openid profile", perm: ["oracle:read"] });
  const { sessao } = await logar(at);
  assert.ok(sessao, "login com conta valida cria sessao mesmo sem permission");
  const sv = await handleSessao(req("/api/b2b/auth/sessao", { [COOKIE_SESSAO]: valorDe(sessao!) }), ENV, deps());
  assert.equal((await sv.json()).tem_permissao, false);
  const d = deps();
  const r = await handleProxy(req("/api/b2b/revenda/resumo", { [COOKIE_SESSAO]: valorDe(sessao!) }), "resumo", ENV, d);
  assert.equal(r.status, 403);
  assert.deepEqual(await r.json(), { erro: "sem_permissao" });
  assert.equal(d.chamadas.length, 0);
});

test("autorizado: proxy chama a FastAPI com Bearer, filtra parametros e devolve o corpo", async () => {
  const at = await accessToken({ perm: [ESCOPO_B2B] });
  const { sessao } = await logar(at);
  const corpo = { status: "ok", kpis: { anuncios: 10 } };
  const d = deps({ "https://api.exemplo/api/v1/b2b-revenda/resumo": () => Response.json(corpo) });
  const r = await handleProxy(
    req("/api/b2b/revenda/resumo?marcas=AP&canais=Shopee&evil=1&__proto__=x", { [COOKIE_SESSAO]: valorDe(sessao!) }),
    "resumo", ENV, d,
  );
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), corpo);
  const chamada = d.chamadas[0];
  assert.equal(new Headers(chamada.init?.headers).get("authorization"), `Bearer ${at}`);
  const u = new URL(chamada.url);
  assert.deepEqual([...u.searchParams.keys()].sort(), ["canais", "marcas"]);
  assert.equal(chamada.init?.redirect, "error", "nunca seguir redirect levando o token");
  assert.equal(r.headers.get("cache-control"), "no-store");
});

test("proxy: status da FastAPI viram codigos fixos; corpo de erro dela nao passa", async () => {
  const at = await accessToken({ perm: [ESCOPO_B2B] });
  const { sessao } = await logar(at);
  const casos: [number, number, string][] = [
    [401, 401, "token_recusado"],
    [403, 403, "sem_permissao"],
    [503, 503, "api_sem_autenticacao"],
    [500, 502, "api_indisponivel"],
  ];
  for (const [deles, nosso, erro] of casos) {
    const d = deps({ "api.exemplo": () => Response.json({ detail: `segredo-interno-${deles}` }, { status: deles }) });
    const r = await handleProxy(req("/api/b2b/revenda/frescor", { [COOKIE_SESSAO]: valorDe(sessao!) }), "frescor", ENV, d);
    assert.equal(r.status, nosso);
    const txt = await r.text();
    assert.deepEqual(JSON.parse(txt), { erro });
    assert.ok(!txt.includes("segredo-interno"));
    if (deles === 401) assert.ok(setCookies(r).get(COOKIE_SESSAO)!.includes("Max-Age=0"));
  }
  const fora = deps({ "api.exemplo": () => { throw new TypeError("rede"); } });
  const r = await handleProxy(req("/api/b2b/revenda/skus", { [COOKIE_SESSAO]: valorDe(sessao!) }), "skus", ENV, fora);
  assert.equal(r.status, 502);
});

test("proxy: rota fora da allowlist e' 404, sem chamada", async () => {
  const at = await accessToken({ perm: [ESCOPO_B2B] });
  const { sessao } = await logar(at);
  for (const rota of ["clientes", "../admin", "resumo/../x", ""]) {
    const d = deps();
    const r = await handleProxy(req(`/api/b2b/revenda/${rota}`, { [COOKIE_SESSAO]: valorDe(sessao!) }), rota, ENV, d);
    assert.equal(r.status, 404, rota);
    assert.equal(d.chamadas.length, 0);
  }
});

test("logout: apaga sessao e tx e encerra no Auth0 voltando para a Torre", async () => {
  const r = await handleLogout(req("/api/b2b/auth/logout"), ENV);
  const loc = new URL(r.headers.get("location")!);
  assert.equal(`${loc.origin}${loc.pathname}`, `${ISSUER}v2/logout`);
  assert.equal(loc.searchParams.get("returnTo"), "https://torre.exemplo/monitoramento-preco");
  assert.equal(loc.searchParams.get("client_id"), CLIENT);
  const c = setCookies(r);
  assert.ok(c.get(COOKIE_SESSAO)!.includes("Max-Age=0") && c.get(COOKIE_TX)!.includes("Max-Age=0"));
});

// ---------------------------------------------------------------------------
// Frontend: codigos de acesso e fronteira
// ---------------------------------------------------------------------------
test("tela: 401/403/503 do BFF viram situacao de ACESSO", () => {
  assert.equal(acessoDoErro(401, "sessao_ausente"), "sem_sessao");
  assert.equal(acessoDoErro(401, "sessao_expirada"), "sessao_expirada");
  assert.equal(acessoDoErro(401, "token_recusado"), "sem_sessao");
  assert.equal(acessoDoErro(403, "sem_permissao"), "sem_permissao");
  assert.equal(acessoDoErro(503, "auth_nao_configurada"), "auth_nao_configurada");
  assert.equal(acessoDoErro(503, "api_sem_autenticacao"), "auth_nao_configurada");
  assert.equal(acessoDoErro(502, "api_indisponivel"), null);
});

function arquivos(dir: string): string[] {
  const out: string[] = [];
  for (const n of readdirSync(dir)) {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) out.push(...arquivos(p));
    else if (/\.(ts|tsx)$/.test(n)) out.push(p);
  }
  return out;
}

test("modulo do servidor so' e' importado pelas rotas app/api/b2b", () => {
  const importam = [...arquivos(join(RAIZ, "app")), ...arquivos(join(RAIZ, "src"))]
    .filter((p) => /server\/b2b-auth\//.test(readFileSync(p, "utf-8")) && !p.includes(join("src", "server", "b2b-auth")))
    .map((p) => p.slice(RAIZ.length + 1).replaceAll("\\", "/"));
  assert.ok(importam.length === 5);
  for (const p of importam) assert.match(p, /^app\/api\/b2b\//, p);
});

test("nada de token no navegador: sem localStorage/sessionStorage e sem cookie legivel", () => {
  const cliente = [
    "app/monitoramento-preco/revendedores/RevendedoresClient.tsx",
    "src/lib/b2b-revenda.ts",
  ].map((p) => readFileSync(join(RAIZ, p), "utf-8"));
  for (const src of cliente) {
    assert.doesNotMatch(src, /localStorage|sessionStorage|document\.cookie|Authorization|access_token/);
  }
  const bff = readFileSync(join(RAIZ, "src/server/b2b-auth/bff.ts"), "utf-8");
  assert.match(bff, /HttpOnly; Secure; SameSite=Lax/);
  assert.doesNotMatch(bff, /console\.(log|info|warn|error)/, "o BFF nao loga nada (nem token, nem codigo)");
});

test("resposta nao-ok sempre tem o corpo drenado (stream nao fica aberto)", () => {
  const lib = readFileSync(join(RAIZ, "src/lib/b2b-revenda.ts"), "utf-8");
  // Medido no QA: /sessao 503 sem ler o corpo deixava a requisicao pendente.
  assert.equal((lib.match(/await res\.body\?\.cancel\(\);/g) ?? []).length, 2);
});

test("a tela chama so' o BFF de mesma origem, nunca a FastAPI direto", () => {
  const lib = readFileSync(join(RAIZ, "src/lib/b2b-revenda.ts"), "utf-8");
  assert.doesNotMatch(lib, /NEXT_PUBLIC_API_URL|\/api\/v1\/b2b-revenda/);
  assert.match(lib, /revenda: "\/api\/b2b\/revenda"/);
  assert.match(lib, /credentials: "same-origin"/);
});
