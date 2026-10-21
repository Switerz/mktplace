/**
 * Login corporativo e BFF da aba "B2B / Revendedores" — Gate OCTAPRICE-3.
 *
 * REUSA o tenant Auth0 do Oraculo (mesma allowlist de issuer, mesmas regras de
 * permission em `../oracle/oauth.ts`). Nenhum sistema de usuarios novo.
 *
 * FLUXO (Authorization Code + PKCE, cliente confidencial, tudo no servidor):
 *   /api/b2b/auth/login     -> state + nonce + PKCE num cookie selado de 10 min
 *                              e redirect ao /authorize do Auth0;
 *   /api/b2b/auth/callback  -> confere state, troca o code no /oauth/token com
 *                              o client secret, valida access token (issuer,
 *                              audience, RS256) e id token (nonce), grava a
 *                              SESSAO num cookie criptografado (A256GCM);
 *   /api/b2b/auth/sessao    -> diz ao navegador SE ha sessao (nunca o token);
 *   /api/b2b/auth/logout    -> apaga a sessao e encerra no Auth0;
 *   /api/b2b/revenda/<rota> -> PROXY: abre a sessao no servidor e chama a
 *                              FastAPI com `Authorization: Bearer`.
 *
 * O navegador NUNCA ve o access token: ele mora so' dentro do cookie
 * `HttpOnly; Secure; SameSite=Lax`, criptografado com chave do servidor.
 * Nada vai para localStorage/sessionStorage.
 *
 * SO' A ABA B2B. Nao ha middleware global: o resto da Torre continua sem login.
 * Com `NEXT_PUBLIC_B2B_REVENDA_MODE` diferente de "api", TODA rota daqui
 * responde 404 — a superficie nem existe.
 *
 * Modulo puro de I/O injetavel: recebe `Request`, ambiente e dependencias
 * (fetch, chaves, relogio) e devolve `Response`. As rotas do App Router sao
 * cascas finas.
 */
import {
  EncryptJWT,
  base64url,
  createRemoteJWKSet,
  errors,
  jwtDecrypt,
  jwtVerify,
  type JWTPayload,
  type JWTVerifyGetKey,
} from "jose";

import {
  ALLOWED_JWT_ALGORITHMS,
  effectiveScopes,
  hasRequiredScope,
  normalizeIssuer,
} from "../oracle/oauth.ts";

// ---------------------------------------------------------------------------
// Configuracao
// ---------------------------------------------------------------------------
export const ESCOPO_B2B = "b2b_revenda:read";
export const COOKIE_SESSAO = "__Host-b2b_sessao";
export const COOKIE_TX = "__Host-b2b_tx";
export const ROTA_ABA = "/monitoramento-preco/revendedores";
export const ROTA_SAIDA = "/monitoramento-preco";
export const CALLBACK_PATH = "/api/b2b/auth/callback";
/** Rotas da FastAPI que o proxy aceita. Nada alem disto passa. */
export const ROTAS_PROXY = ["frescor", "resumo", "canais-marcas", "skus"] as const;
/** Parametros de consulta repassados. O resto e' descartado. */
export const PARAMS_PROXY = ["data", "marcas", "canais", "severidades", "skus", "ufs", "limite"] as const;

const TX_SEGUNDOS = 600;
/** Teto da sessao, mesmo que o access token dure mais. */
const SESSAO_MAX_SEGUNDOS = 12 * 3600;
const TOLERANCIA_RELOGIO_S = 30;

export type B2BAuthEnv = {
  NEXT_PUBLIC_B2B_REVENDA_MODE?: string | undefined;
  B2B_AUTH_ISSUER?: string | undefined;
  B2B_AUTH_CLIENT_ID?: string | undefined;
  B2B_AUTH_CLIENT_SECRET?: string | undefined;
  B2B_AUTH_AUDIENCE?: string | undefined;
  B2B_AUTH_SESSION_SECRET?: string | undefined;
  B2B_AUTH_APP_BASE_URL?: string | undefined;
  B2B_API_BASE_URL?: string | undefined;
};

export type B2BAuthConfig = {
  readonly issuer: string;
  readonly clientId: string;
  readonly clientSecret: string;
  readonly audience: string;
  readonly sessionSecret: string;
  readonly appBaseUrl: string;
  readonly apiBaseUrl: string;
};

function present(v: string | undefined): v is string {
  return typeof v === "string" && v.trim().length > 0;
}

/** Origem HTTPS (ou http://localhost para desenvolvimento), sem caminho. */
export function normalizarOrigem(raw: string | undefined): string | null {
  if (!present(raw)) return null;
  let u: URL;
  try {
    u = new URL(raw.trim());
  } catch {
    return null;
  }
  const local = u.protocol === "http:" && (u.hostname === "localhost" || u.hostname === "127.0.0.1");
  if (u.protocol !== "https:" && !local) return null;
  if (u.username || u.password || u.search || u.hash) return null;
  if (u.pathname !== "/" && u.pathname !== "") return null;
  return u.origin;
}

/** Configuracao completa e valida, ou `null` (= 503 em toda rota de auth). */
export function parseB2BAuthConfig(env: B2BAuthEnv): B2BAuthConfig | null {
  const issuer = normalizeIssuer(env.B2B_AUTH_ISSUER);
  const appBaseUrl = normalizarOrigem(env.B2B_AUTH_APP_BASE_URL);
  const apiBaseUrl = normalizarOrigem(env.B2B_API_BASE_URL);
  const clientId = (env.B2B_AUTH_CLIENT_ID ?? "").trim();
  const clientSecret = env.B2B_AUTH_CLIENT_SECRET ?? "";
  const audience = (env.B2B_AUTH_AUDIENCE ?? "").trim();
  const sessionSecret = env.B2B_AUTH_SESSION_SECRET ?? "";
  if (!issuer || !appBaseUrl || !apiBaseUrl) return null;
  if (!clientId || /\s/.test(clientId)) return null;
  if (clientSecret.length < 16) return null;
  if (!audience.startsWith("https://") || /\s/.test(audience)) return null;
  if (sessionSecret.length < 32) return null;
  return { issuer, clientId, clientSecret, audience, sessionSecret, appBaseUrl, apiBaseUrl };
}

export function modoApi(env: B2BAuthEnv): boolean {
  return env.NEXT_PUBLIC_B2B_REVENDA_MODE === "api";
}

// ---------------------------------------------------------------------------
// Dependencias injetaveis
// ---------------------------------------------------------------------------
export type B2BAuthDeps = {
  fetch: typeof fetch;
  /** Resolvedor de chave publica do issuer (JWKS em producao). */
  chaves: (cfg: B2BAuthConfig) => JWTVerifyGetKey;
  agoraMs: () => number;
};

const jwksPorIssuer = new Map<string, JWTVerifyGetKey>();

export const depsPadrao: B2BAuthDeps = {
  fetch: (...a) => fetch(...a),
  chaves: (cfg) => {
    let jwks = jwksPorIssuer.get(cfg.issuer);
    if (!jwks) {
      // URL derivada do issuer allowlisted — nunca configurada a mao.
      jwks = createRemoteJWKSet(new URL(`${cfg.issuer}.well-known/jwks.json`));
      jwksPorIssuer.set(cfg.issuer, jwks);
    }
    return jwks;
  },
  agoraMs: () => Date.now(),
};

// ---------------------------------------------------------------------------
// Cookies selados (JWE dir + A256GCM)
// ---------------------------------------------------------------------------
async function chaveDeSelo(segredo: string): Promise<Uint8Array> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(segredo));
  return new Uint8Array(digest);
}

type Tipo = "b2b-sessao" | "b2b-tx";

export async function selar(payload: JWTPayload, tipo: Tipo, segredo: string, expS: number): Promise<string> {
  return new EncryptJWT(payload)
    .setProtectedHeader({ alg: "dir", enc: "A256GCM", typ: tipo })
    .setAudience(tipo)
    .setIssuedAt()
    .setExpirationTime(expS)
    .encrypt(await chaveDeSelo(segredo));
}

export type Aberto = { ok: true; payload: JWTPayload } | { ok: false; motivo: "expirada" | "invalida" };

export async function abrir(token: string, tipo: Tipo, segredo: string, agoraMs: number): Promise<Aberto> {
  try {
    const { payload } = await jwtDecrypt(token, await chaveDeSelo(segredo), {
      audience: tipo,
      currentDate: new Date(agoraMs),
      keyManagementAlgorithms: ["dir"],
      contentEncryptionAlgorithms: ["A256GCM"],
    });
    return { ok: true, payload };
  } catch (e) {
    return { ok: false, motivo: e instanceof errors.JWTExpired ? "expirada" : "invalida" };
  }
}

export function lerCookie(req: Request, nome: string): string | null {
  const bruto = req.headers.get("cookie");
  if (!bruto) return null;
  for (const parte of bruto.split(";")) {
    const i = parte.indexOf("=");
    if (i > 0 && parte.slice(0, i).trim() === nome) return parte.slice(i + 1).trim() || null;
  }
  return null;
}

/** `__Host-`: Secure, Path=/, sem Domain. HttpOnly sempre. */
export function cookie(nome: string, valor: string, maxAgeS: number): string {
  return `${nome}=${valor}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${Math.max(0, Math.floor(maxAgeS))}`;
}

export function apagar(nome: string): string {
  return cookie(nome, "", 0);
}

// ---------------------------------------------------------------------------
// Respostas
// ---------------------------------------------------------------------------
const SEM_CACHE = { "Cache-Control": "no-store", Pragma: "no-cache" };

function json(status: number, corpo: unknown, cookies: string[] = []): Response {
  const h = new Headers({ ...SEM_CACHE, "Content-Type": "application/json; charset=utf-8" });
  for (const c of cookies) h.append("Set-Cookie", c);
  return new Response(JSON.stringify(corpo), { status, headers: h });
}

function redirecionar(destino: string, cookies: string[] = []): Response {
  const h = new Headers({ ...SEM_CACHE, Location: destino });
  for (const c of cookies) h.append("Set-Cookie", c);
  return new Response(null, { status: 302, headers: h });
}

const NAO_ENCONTRADO = () => json(404, { erro: "nao_encontrado" });
const NAO_CONFIGURADO = () => json(503, { erro: "auth_nao_configurada" });

// ---------------------------------------------------------------------------
// PKCE / aleatorios
// ---------------------------------------------------------------------------
function aleatorio(bytes: number): string {
  return base64url.encode(crypto.getRandomValues(new Uint8Array(bytes)));
}

async function desafioPkce(verifier: string): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url.encode(new Uint8Array(d));
}

/** So' caminhos da propria aba. Qualquer outra coisa vira a aba. */
export function destinoSeguro(raw: string | null): string {
  if (!raw || !raw.startsWith(ROTA_ABA)) return ROTA_ABA;
  const resto = raw.slice(ROTA_ABA.length);
  if (resto !== "" && !resto.startsWith("?")) return ROTA_ABA;
  if (/[\\\r\n]/.test(raw) || raw.startsWith("//")) return ROTA_ABA;
  return raw;
}

// ---------------------------------------------------------------------------
// Validacao dos tokens do Auth0
// ---------------------------------------------------------------------------
export async function validarAccessToken(token: string, cfg: B2BAuthConfig, deps: B2BAuthDeps): Promise<JWTPayload> {
  const { payload } = await jwtVerify(token, deps.chaves(cfg), {
    issuer: cfg.issuer,
    audience: cfg.audience,
    algorithms: [...ALLOWED_JWT_ALGORITHMS],
    requiredClaims: ["exp", "iat", "sub"],
    currentDate: new Date(deps.agoraMs()),
    clockTolerance: TOLERANCIA_RELOGIO_S,
  });
  return payload;
}

async function validarIdToken(token: string, cfg: B2BAuthConfig, deps: B2BAuthDeps, nonce: string): Promise<JWTPayload> {
  const { payload } = await jwtVerify(token, deps.chaves(cfg), {
    issuer: cfg.issuer,
    audience: cfg.clientId,
    algorithms: [...ALLOWED_JWT_ALGORITHMS],
    requiredClaims: ["exp", "iat", "sub"],
    currentDate: new Date(deps.agoraMs()),
    clockTolerance: TOLERANCIA_RELOGIO_S,
  });
  if (payload.nonce !== nonce) throw new Error("nonce");
  return payload;
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------
export async function handleLogin(req: Request, env: B2BAuthEnv, deps: B2BAuthDeps = depsPadrao): Promise<Response> {
  if (!modoApi(env)) return NAO_ENCONTRADO();
  const cfg = parseB2BAuthConfig(env);
  if (!cfg) return NAO_CONFIGURADO();
  const retorno = destinoSeguro(new URL(req.url).searchParams.get("returnTo"));
  const state = aleatorio(32);
  const verifier = aleatorio(32);
  const nonce = aleatorio(16);
  const expS = Math.floor(deps.agoraMs() / 1000) + TX_SEGUNDOS;
  const tx = await selar({ state, verifier, nonce, retorno }, "b2b-tx", cfg.sessionSecret, expS);
  const q = new URLSearchParams({
    response_type: "code",
    client_id: cfg.clientId,
    redirect_uri: `${cfg.appBaseUrl}${CALLBACK_PATH}`,
    scope: "openid profile email",
    audience: cfg.audience,
    state,
    nonce,
    code_challenge: await desafioPkce(verifier),
    code_challenge_method: "S256",
  });
  return redirecionar(`${cfg.issuer}authorize?${q}`, [cookie(COOKIE_TX, tx, TX_SEGUNDOS)]);
}

/** Codigos FIXOS que voltam para a aba em `?login=` (nada vindo do Auth0). */
export type ResultadoLogin = "negado" | "estado_invalido" | "troca_falhou" | "token_invalido";

export async function handleCallback(req: Request, env: B2BAuthEnv, deps: B2BAuthDeps = depsPadrao): Promise<Response> {
  if (!modoApi(env)) return NAO_ENCONTRADO();
  const cfg = parseB2BAuthConfig(env);
  if (!cfg) return NAO_CONFIGURADO();
  const falha = (r: ResultadoLogin) =>
    redirecionar(`${cfg.appBaseUrl}${ROTA_ABA}?login=${r}`, [apagar(COOKIE_TX)]);

  const params = new URL(req.url).searchParams;
  const txBruto = lerCookie(req, COOKIE_TX);
  const tx = txBruto ? await abrir(txBruto, "b2b-tx", cfg.sessionSecret, deps.agoraMs()) : null;
  if (!tx || !tx.ok) return falha("estado_invalido");
  const { state, verifier, nonce, retorno } = tx.payload as {
    state?: string; verifier?: string; nonce?: string; retorno?: string;
  };
  if (params.get("error")) return falha("negado");
  const code = params.get("code");
  if (!code || !state || params.get("state") !== state || !verifier || !nonce) return falha("estado_invalido");

  let corpo: { access_token?: unknown; id_token?: unknown };
  try {
    const res = await deps.fetch(`${cfg.issuer}oauth/token`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        grant_type: "authorization_code",
        client_id: cfg.clientId,
        client_secret: cfg.clientSecret,
        code,
        redirect_uri: `${cfg.appBaseUrl}${CALLBACK_PATH}`,
        code_verifier: verifier,
      }),
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    });
    if (res.status !== 200) return falha("troca_falhou");
    corpo = (await res.json()) as typeof corpo;
  } catch {
    return falha("troca_falhou");
  }
  const at = typeof corpo.access_token === "string" ? corpo.access_token : "";
  const idt = typeof corpo.id_token === "string" ? corpo.id_token : "";
  let atPayload: JWTPayload;
  let idPayload: JWTPayload;
  try {
    atPayload = await validarAccessToken(at, cfg, deps);
    idPayload = await validarIdToken(idt, cfg, deps, nonce);
  } catch {
    return falha("token_invalido");
  }
  if (atPayload.sub !== idPayload.sub) return falha("token_invalido");

  const agoraS = Math.floor(deps.agoraMs() / 1000);
  const expS = Math.min(Number(atPayload.exp), agoraS + SESSAO_MAX_SEGUNDOS);
  const nome = [idPayload.email, idPayload.name].find((v) => typeof v === "string" && v) as string | undefined;
  const sessao = await selar(
    {
      sub: atPayload.sub,
      nome: nome ?? "conta corporativa",
      at,
      perm: hasRequiredScope(effectiveScopes(atPayload), ESCOPO_B2B),
    },
    "b2b-sessao",
    cfg.sessionSecret,
    expS,
  );
  return redirecionar(`${cfg.appBaseUrl}${destinoSeguro(retorno ?? null)}`, [
    cookie(COOKIE_SESSAO, sessao, expS - agoraS),
    apagar(COOKIE_TX),
  ]);
}

export async function handleLogout(_req: Request, env: B2BAuthEnv): Promise<Response> {
  if (!modoApi(env)) return NAO_ENCONTRADO();
  const cfg = parseB2BAuthConfig(env);
  if (!cfg) return redirecionar(ROTA_SAIDA, [apagar(COOKIE_SESSAO), apagar(COOKIE_TX)]);
  const q = new URLSearchParams({ client_id: cfg.clientId, returnTo: `${cfg.appBaseUrl}${ROTA_SAIDA}` });
  return redirecionar(`${cfg.issuer}v2/logout?${q}`, [apagar(COOKIE_SESSAO), apagar(COOKIE_TX)]);
}

type SessaoAberta =
  | { ok: true; nome: string; at: string; perm: boolean; expS: number }
  | { ok: false; motivo: "sessao_ausente" | "sessao_expirada" | "sessao_invalida" };

async function sessaoDe(req: Request, cfg: B2BAuthConfig, deps: B2BAuthDeps): Promise<SessaoAberta> {
  const bruto = lerCookie(req, COOKIE_SESSAO);
  if (!bruto) return { ok: false, motivo: "sessao_ausente" };
  const r = await abrir(bruto, "b2b-sessao", cfg.sessionSecret, deps.agoraMs());
  if (!r.ok) return { ok: false, motivo: r.motivo === "expirada" ? "sessao_expirada" : "sessao_invalida" };
  const p = r.payload as { at?: unknown; nome?: unknown; perm?: unknown; exp?: unknown };
  if (typeof p.at !== "string" || !p.at) return { ok: false, motivo: "sessao_invalida" };
  return { ok: true, nome: String(p.nome ?? ""), at: p.at, perm: p.perm === true, expS: Number(p.exp) };
}

export async function handleSessao(req: Request, env: B2BAuthEnv, deps: B2BAuthDeps = depsPadrao): Promise<Response> {
  if (!modoApi(env)) return NAO_ENCONTRADO();
  const cfg = parseB2BAuthConfig(env);
  if (!cfg) return NAO_CONFIGURADO();
  const s = await sessaoDe(req, cfg, deps);
  if (!s.ok) {
    const limpar = s.motivo === "sessao_ausente" ? [] : [apagar(COOKIE_SESSAO)];
    return json(200, { autenticado: false, motivo: s.motivo }, limpar);
  }
  // NUNCA o token: so' o que a tela precisa para decidir o que mostrar.
  return json(200, {
    autenticado: true,
    nome: s.nome,
    tem_permissao: s.perm,
    expira_em: new Date(s.expS * 1000).toISOString(),
  });
}

/** Status da FastAPI -> resposta do BFF. Corpo de erro da API nao e' repassado. */
const MAPA_ERRO: Record<number, { status: number; erro: string }> = {
  401: { status: 401, erro: "token_recusado" },
  403: { status: 403, erro: "sem_permissao" },
  503: { status: 503, erro: "api_sem_autenticacao" },
};

export async function handleProxy(
  req: Request,
  rota: string,
  env: B2BAuthEnv,
  deps: B2BAuthDeps = depsPadrao,
): Promise<Response> {
  if (!modoApi(env)) return NAO_ENCONTRADO();
  if (!(ROTAS_PROXY as readonly string[]).includes(rota)) return NAO_ENCONTRADO();
  const cfg = parseB2BAuthConfig(env);
  if (!cfg) return NAO_CONFIGURADO();

  const s = await sessaoDe(req, cfg, deps);
  if (!s.ok) {
    const limpar = s.motivo === "sessao_ausente" ? [] : [apagar(COOKIE_SESSAO)];
    return json(401, { erro: s.motivo }, limpar);
  }
  // Sem a permission nem chama a FastAPI (ela tambem recusaria com 403).
  if (!s.perm) return json(403, { erro: "sem_permissao" });

  const origem = new URL(req.url).searchParams;
  const q = new URLSearchParams();
  for (const nome of PARAMS_PROXY) {
    for (const v of origem.getAll(nome).slice(0, 60)) if (v.length <= 120) q.append(nome, v);
  }
  const consulta = q.toString();
  let res: Response;
  try {
    res = await deps.fetch(`${cfg.apiBaseUrl}/api/v1/b2b-revenda/${rota}${consulta ? `?${consulta}` : ""}`, {
      headers: { Authorization: `Bearer ${s.at}`, Accept: "application/json" },
      redirect: "error",
      signal: AbortSignal.timeout(20_000),
    });
  } catch {
    return json(502, { erro: "api_indisponivel" });
  }
  const mapeado = MAPA_ERRO[res.status];
  if (mapeado) {
    const limpar = res.status === 401 ? [apagar(COOKIE_SESSAO)] : [];
    return json(mapeado.status, { erro: mapeado.erro }, limpar);
  }
  if (res.status !== 200 && res.status !== 422) return json(502, { erro: "api_indisponivel" });
  let corpo: unknown;
  try {
    corpo = await res.json();
  } catch {
    return json(502, { erro: "api_indisponivel" });
  }
  return json(res.status, corpo);
}
