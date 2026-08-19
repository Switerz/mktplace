/**
 * Fronteira de acesso do MCP do Oraculo — FAIL-CLOSED.
 *
 * Contrato (docs/ORACLE_MCP_PLAN.md secao 13.4.1):
 * - QUALQUER execucao hospedada NEGA enquanto nao existir provedor real de
 *   autenticacao. Isso inclui Production, Preview e custom environments da
 *   Vercel — nao apenas `NODE_ENV=production`;
 * - execucao LOCAL exige habilitacao EXPLICITA;
 * - configuracao ausente NEGA;
 * - o stub de identidade so pode ser injetado por teste, e so vale sob
 *   NODE_ENV=test;
 * - a negacao acontece ANTES de instanciar o MCP e ANTES de qualquer chamada
 *   ao FastAPI.
 *
 * Modulo puro: nao le `process.env` por conta propria, nao faz I/O e nao
 * conhece HTTP. Quem chama injeta o ambiente, o que permite testar a matriz
 * inteira sem mexer em variavel global de processo.
 */

export type AccessEnv = {
  NODE_ENV?: string | undefined;
  /** `"1"` em qualquer execucao na plataforma Vercel (build ou runtime). */
  VERCEL?: string | undefined;
  /** `production` | `preview` | `development` — ambiente resolvido da Vercel. */
  VERCEL_ENV?: string | undefined;
  /** Alvo do deployment; assume o NOME do custom environment quando houver. */
  VERCEL_TARGET_ENV?: string | undefined;
  /** Habilitacao explicita para uso local. Unico valor aceito: "1". */
  ORACLE_MCP_ENABLED?: string | undefined;
  /** Base allowlisted do FastAPI. Sem ela nao ha o que servir. */
  MCP_BACKEND_API_URL?: string | undefined;
};

export type DenyReason =
  /** Producao, por qualquer um dos sinais. */
  | "production_disabled"
  /** Hospedado fora de producao (Preview, custom environment, build na Vercel). */
  | "hosted_deployment_disabled"
  /** Falta ORACLE_MCP_ENABLED=1. */
  | "not_enabled"
  /** Falta MCP_BACKEND_API_URL, ou ela nao passa na validacao. */
  | "missing_backend_config"
  /** Tentativa de usar stub fora de NODE_ENV=test. */
  | "stub_not_allowed";

export type AccessDecision =
  | { allowed: true; mode: "development" | "test"; backendBaseUrl: string }
  | { allowed: false; reason: DenyReason };

/**
 * Identidade de teste. So existe para provar a fronteira nos testes; nunca e
 * construida a partir de header, cookie, token ou bypass de plataforma.
 */
export type TestIdentity = { readonly subject: string };

export type EvaluateOptions = {
  /**
   * Injetado EXCLUSIVAMENTE pela suite de testes. Se vier preenchido e o
   * ambiente nao for `test`, a decisao e' NEGAR — nunca ignorar em silencio.
   */
  testIdentity?: TestIdentity | undefined;
};

function present(v: string | undefined): v is string {
  return typeof v === "string" && v.trim().length > 0;
}

/**
 * `true` quando o contexto e' de PRODUCAO por qualquer sinal.
 *
 * `VERCEL_TARGET_ENV` entra aqui porque um custom environment pode ser
 * publicado como producao sem que `VERCEL_ENV` diga isso.
 */
export function isProductionEnv(env: AccessEnv): boolean {
  return (
    env.NODE_ENV === "production" ||
    env.VERCEL_ENV === "production" ||
    env.VERCEL_TARGET_ENV === "production"
  );
}

/**
 * `true` quando o codigo esta rodando HOSPEDADO (deployed), em qualquer
 * ambiente da Vercel.
 *
 * Deliberadamente amplo: a presenca de QUALQUER sinal oficial de deployment
 * basta. Preview e custom environments recebem URL publica, e o contrato
 * aprovado proibe expor a rota sem autenticacao real — depender de
 * `NODE_ENV=production` para isso seria proteger Preview por efeito colateral,
 * que e' exatamente o que nao queremos.
 *
 * Consequencia aceita: `vercel dev` (que tambem define `VERCEL=1`) fica fora
 * do caminho permitido. O piloto local roda por `npm run dev`.
 */
export function isHostedEnv(env: AccessEnv): boolean {
  return env.VERCEL === "1" || present(env.VERCEL_ENV) || present(env.VERCEL_TARGET_ENV);
}

/**
 * Hostname UNICO autorizado como backend da Torre.
 *
 * Constante server-only e fechada, de proposito: o backend e' unico e conhecido,
 * e uma segunda variavel de ambiente para "allowlist" apenas moveria o problema
 * (uma configuracao errada continuaria destravando qualquer origem). Com a
 * constante, uma configuracao divergente falha FECHADA.
 */
export const ALLOWED_BACKEND_HOSTNAME = "mktplace-api.onrender.com";

/**
 * Valida a base do backend. Estrita de proposito:
 * - `https:` obrigatorio;
 * - hostname EXATAMENTE igual ao canonico (nunca `endsWith`, que aceitaria
 *   `mktplace-api.onrender.com.evil.invalid`);
 * - porta explicita ausente (somente a porta padrao de HTTPS);
 * - sem credencial embutida;
 * - sem query, sem fragment;
 * - path apenas raiz — o path e' definido pelo codigo, e descartar em silencio
 *   um path configurado esconderia erro de configuracao.
 */
export function isUsableBackendUrl(raw: string | undefined): raw is string {
  if (!present(raw)) return false;

  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return false;
  }

  if (parsed.protocol !== "https:") return false;
  // Credencial embutida na URL nunca e' aceitavel.
  if (parsed.username !== "" || parsed.password !== "") return false;
  // `URL` normaliza a porta padrao para "", entao porta preenchida = customizada.
  if (parsed.port !== "") return false;
  // Correspondencia EXATA. Cobre subdominio prefixado/sufixado, sufixo
  // malicioso e trailing dot (que a `URL` preserva no hostname).
  if (parsed.hostname !== ALLOWED_BACKEND_HOSTNAME) return false;
  if (parsed.search !== "") return false;
  if (parsed.hash !== "") return false;
  // Aceita apenas raiz: "/" ou vazio.
  if (parsed.pathname !== "/" && parsed.pathname !== "") return false;

  return true;
}

/**
 * Decide se a rota MCP pode servir a requisicao.
 *
 * A ordem importa: producao e hospedagem sao avaliadas ANTES de qualquer
 * flag, para que nenhuma configuracao local possa destravar um deployment.
 */
export function evaluateAccess(env: AccessEnv, opts: EvaluateOptions = {}): AccessDecision {
  const isTest = env.NODE_ENV === "test";

  // 1. Producao: negacao incondicional. `ORACLE_MCP_ENABLED` nao e' consultado
  //    neste ramo, de proposito.
  if (isProductionEnv(env)) {
    return { allowed: false, reason: "production_disabled" };
  }

  // 2. Qualquer outro contexto hospedado (Preview, custom env, build na
  //    Vercel): tambem negado enquanto nao houver autenticacao real.
  if (isHostedEnv(env)) {
    return { allowed: false, reason: "hosted_deployment_disabled" };
  }

  // 3. Stub: so sob NODE_ENV=test. Fora disso e' negacao explicita, nunca
  //    "ignora o stub e segue" — um stub vazando seria falha material.
  if (opts.testIdentity && !isTest) {
    return { allowed: false, reason: "stub_not_allowed" };
  }

  // 4. Habilitacao explicita. Ausencia de configuracao NEGA.
  if (env.ORACLE_MCP_ENABLED !== "1") {
    return { allowed: false, reason: "not_enabled" };
  }

  // 5. Backend allowlisted obrigatorio.
  if (!isUsableBackendUrl(env.MCP_BACKEND_API_URL)) {
    return { allowed: false, reason: "missing_backend_config" };
  }

  return {
    allowed: true,
    mode: isTest ? "test" : "development",
    backendBaseUrl: env.MCP_BACKEND_API_URL,
  };
}
