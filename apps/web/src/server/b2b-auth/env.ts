/**
 * Leitura do ambiente das rotas de login/BFF da aba B2B (SO' servidor).
 *
 * Acesso LITERAL a `process.env`: o Next substitui `NEXT_PUBLIC_*` em build.
 * As demais sao lidas em runtime no servidor e nunca chegam ao bundle do
 * navegador — este modulo so' e' importado por `app/api/b2b/**` (route
 * handlers), e `tests/b2b-auth.test.ts` trava isso.
 */
import type { B2BAuthEnv } from "./bff.ts";

export function lerEnvB2B(): B2BAuthEnv {
  return {
    NEXT_PUBLIC_B2B_REVENDA_MODE: process.env.NEXT_PUBLIC_B2B_REVENDA_MODE,
    B2B_AUTH_ISSUER: process.env.B2B_AUTH_ISSUER,
    B2B_AUTH_CLIENT_ID: process.env.B2B_AUTH_CLIENT_ID,
    B2B_AUTH_CLIENT_SECRET: process.env.B2B_AUTH_CLIENT_SECRET,
    B2B_AUTH_AUDIENCE: process.env.B2B_AUTH_AUDIENCE,
    B2B_AUTH_SESSION_SECRET: process.env.B2B_AUTH_SESSION_SECRET,
    B2B_AUTH_APP_BASE_URL: process.env.B2B_AUTH_APP_BASE_URL,
    B2B_API_BASE_URL: process.env.B2B_API_BASE_URL,
  };
}
