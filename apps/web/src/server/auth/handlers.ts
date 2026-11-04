/**
 * Handlers puros de /api/auth/* (login Google, logout, me).
 *
 * Recebem o ambiente por parametro (testavel sem mutar `process.env`). O
 * `credential` enviado pelo cliente nunca entra em corpo de resposta nem em log.
 */
import type { JWTVerifyGetKey } from "jose";

import {
  SESSION_COOKIE,
  allowedDomains,
  authEnabled,
  createSessionToken,
  isAllowedEmail,
  readCookie,
  sessionCookieHeader,
  verifyGoogleIdToken,
  verifySessionToken,
  type AuthEnv,
} from "./session.ts";

function json(body: unknown, status: number, extra?: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store", ...extra },
  });
}

export async function handleGoogleLogin(
  req: Request,
  env: AuthEnv,
  keys?: JWTVerifyGetKey,
): Promise<Response> {
  if (!authEnabled(env)) return json({ error: "auth_desligada" }, 404);

  const body: unknown = await req.json().catch(() => null);
  const credential =
    body && typeof body === "object" && typeof (body as { credential?: unknown }).credential === "string"
      ? (body as { credential: string }).credential.trim()
      : "";
  if (!credential) return json({ error: "credential_ausente" }, 400);

  let identity;
  try {
    identity = await verifyGoogleIdToken(credential, env, keys);
  } catch {
    return json({ error: "credencial_invalida" }, 401);
  }

  // Sem email (claim ausente) conta como nao verificado: nunca ganha cookie.
  if (!identity.email || !identity.emailVerified) {
    return json({ error: "email_nao_verificado" }, 403);
  }
  if (!isAllowedEmail(identity.email, env)) {
    return json(
      {
        error: "dominio_nao_permitido",
        message: `Acesso restrito a contas @${allowedDomains(env).join(" / @")}.`,
      },
      403,
    );
  }

  let token: string;
  try {
    token = await createSessionToken(
      { email: identity.email, name: identity.name, picture: identity.picture },
      env,
    );
  } catch {
    return json({ error: "sessao_indisponivel" }, 500);
  }

  console.info("auth_login email=%s", identity.email);
  return json(
    { ok: true, email: identity.email, name: identity.name, picture: identity.picture },
    200,
    { "set-cookie": sessionCookieHeader(token, env) },
  );
}

export async function handleLogout(_req: Request, env: AuthEnv): Promise<Response> {
  return json({ ok: true }, 200, { "set-cookie": sessionCookieHeader("", env, 0) });
}

export async function handleMe(req: Request, env: AuthEnv): Promise<Response> {
  if (!authEnabled(env)) return json({ authEnabled: false, email: "dev@local" }, 200);
  const session = await verifySessionToken(readCookie(req, SESSION_COOKIE), env);
  if (!session) {
    return json({ error: "nao_autenticado", clientId: (env.AUTH_GOOGLE_ID ?? "").trim() }, 401);
  }
  return json(
    { authEnabled: true, email: session.email, name: session.name, picture: session.picture },
    200,
  );
}
