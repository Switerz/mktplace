/**
 * Fronteira de acesso do MCP do Oraculo — FAIL-CLOSED.
 *
 * Contrato (docs/ORACLE_MCP_PLAN.md secoes 13.4.1 e 30):
 * - **Producao** e' elegivel SOMENTE com configuracao OAuth completa; sem ela,
 *   responde 404 generico;
 * - **Preview e custom environments** continuam negados nesta etapa;
 * - execucao LOCAL exige habilitacao EXPLICITA, e passa a exigir bearer se
 *   OAuth estiver configurado;
 * - configuracao ausente ou divergente NEGA;
 * - o stub de identidade so pode ser injetado por teste, so vale sob
 *   NODE_ENV=test, e nunca em producao;
 * - a negacao acontece ANTES de instanciar o MCP e ANTES de qualquer chamada
 *   ao FastAPI.
 *
 * Modulo puro: nao le `process.env` por conta propria, nao faz I/O e nao
 * conhece HTTP. Quem chama injeta o ambiente, o que permite testar a matriz
 * inteira sem mexer em variavel global de processo.
 */
import { parseOAuthConfig, type OAuthConfig } from "./oauth.ts";

export type AccessEnv = {
  NODE_ENV?: string | undefined;
  /** `"1"` em qualquer execucao na plataforma Vercel (build ou runtime). */
  VERCEL?: string | undefined;
  /** `production` | `preview` | `development` — ambiente resolvido da Vercel. */
  VERCEL_ENV?: string | undefined;
  /** Alvo do deployment; assume o NOME do custom environment quando houver. */
  VERCEL_TARGET_ENV?: string | undefined;
  /** Habilitacao explicita. Unico valor aceito: "1". */
  ORACLE_MCP_ENABLED?: string | undefined;
  /** Base allowlisted do FastAPI. Sem ela nao ha o que servir. */
  MCP_BACKEND_API_URL?: string | undefined;
  /** Issuer do authorization server (Auth0). Ver `oauth.ts`. */
  ORACLE_AUTH_ISSUER?: string | undefined;
  /** Audience exigida — precisa ser o resource canonico da rota. */
  ORACLE_AUTH_AUDIENCE?: string | undefined;
  /** Permission/scope exigida (`oracle:read`). */
  ORACLE_AUTH_REQUIRED_SCOPE?: string | undefined;
};

export type DenyReason =
  /** Hospedado fora de producao (Preview, custom environment, build na Vercel). */
  | "hosted_deployment_disabled"
  /** Producao (ou qualquer contexto hospedado) sem configuracao OAuth completa. */
  | "missing_oauth_config"
  /** Falta ORACLE_MCP_ENABLED=1. */
  | "not_enabled"
  /** Falta MCP_BACKEND_API_URL, ou ela nao passa na validacao. */
  | "missing_backend_config"
  /** Tentativa de usar stub fora de NODE_ENV=test. */
  | "stub_not_allowed"
  /** Host desconhecido: sem sinal de plataforma e sem `NODE_ENV` de dev/test. */
  | "environment_not_permitted";

export type AccessDecision =
  | {
      allowed: true;
      mode: "development" | "test" | "production";
      backendBaseUrl: string;
      /**
       * Configuracao OAuth em vigor. Quando presente, o handler DEVE exigir e
       * validar o bearer antes de qualquer outra coisa.
       *
       * `null` acontece SOMENTE em execucao local sem OAuth configurado — o
       * modo de desenvolvimento que ja existia. Em contexto hospedado isso e'
       * impossivel por construcao: sem OAuth completo, a decisao e' negar.
       */
      auth: OAuthConfig | null;
    }
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
 * Classificacao do ambiente de execucao.
 *
 * - `vercel-production` — deployment de PRODUCAO na Vercel, o unico contexto
 *   hospedado elegivel (e ainda assim so' com todos os guardrails);
 * - `vercel-other` — qualquer outro deployment: Preview, `development`, custom
 *   environment, ou `VERCEL=1` sem `VERCEL_ENV` confiavel. Sempre negado;
 * - `local` — nenhum sinal de deployment.
 */
export type EnvClass =
  | "vercel-production"
  | "vercel-other"
  /** Sem sinal de deployment E `NODE_ENV` de desenvolvimento/teste. */
  | "local"
  /**
   * Nenhum sinal de plataforma, mas `NODE_ENV` nao e' de desenvolvimento nem
   * de teste (tipicamente `production`, ou ausente). Host desconhecido: pode
   * estar exposto e nao temos como saber. Sempre negado.
   */
  | "unknown-host";

/**
 * `true` quando ha QUALQUER sinal oficial de execucao na Vercel.
 *
 * Consequencia aceita: `vercel dev` (que tambem define `VERCEL=1`) fica fora
 * do caminho permitido. O uso local roda por `npm run dev`.
 */
export function isHostedEnv(env: AccessEnv): boolean {
  return env.VERCEL === "1" || present(env.VERCEL_ENV) || present(env.VERCEL_TARGET_ENV);
}

/**
 * Classifica o ambiente. **`NODE_ENV` NAO decide nada quando ha sinal Vercel.**
 *
 * Esta e' a correcao central do finding de Preview fail-open: a Vercel executa
 * **Preview com `NODE_ENV=production`**, entao classificar por `NODE_ENV`
 * primeiro faria um Preview com OAuth completo cair no ramo de producao e
 * ficar exposto. Quando qualquer sinal Vercel esta presente, o ambiente
 * explicito da plataforma tem PRECEDENCIA ABSOLUTA sobre `NODE_ENV`.
 *
 * Producao exige as duas condicoes ao mesmo tempo:
 *   - `VERCEL_ENV === "production"`; e
 *   - `VERCEL_TARGET_ENV` ausente ou tambem `"production"` — um custom
 *     environment nomeado nunca e' producao, mesmo que `VERCEL_ENV` diga isso.
 *
 * `VERCEL=1` sem `VERCEL_ENV` legivel e' tratado como NAO-producao: na duvida
 * sobre qual deployment esta rodando, a resposta e' negar.
 */
export function classifyEnv(env: AccessEnv): EnvClass {
  if (isHostedEnv(env)) {
    const vercelEnv = env.VERCEL_ENV?.trim();
    const targetEnv = env.VERCEL_TARGET_ENV?.trim();

    const isProdEnv = vercelEnv === "production";
    const targetIsProdOrAbsent =
      targetEnv === undefined || targetEnv === "" || targetEnv === "production";

    return isProdEnv && targetIsProdOrAbsent ? "vercel-production" : "vercel-other";
  }

  // Sem nenhum sinal de plataforma. Somente `development` e `test` contam como
  // execucao local; `production` (ou `NODE_ENV` ausente) e' host DESCONHECIDO e
  // e' negado. Sem isso, um deployment fora da Vercel com a flag ligada e sem
  // OAuth cairia no ramo local e serviria sem autenticacao.
  const nodeEnv = env.NODE_ENV?.trim();
  return nodeEnv === "development" || nodeEnv === "test" ? "local" : "unknown-host";
}

/**
 * `true` somente para deployment de PRODUCAO na Vercel.
 *
 * Deliberadamente NAO consulta `NODE_ENV`: ver `classifyEnv`. Uma execucao sem
 * nenhum sinal Vercel e com `NODE_ENV` diferente de `development`/`test` — por
 * exemplo `next start` — NAO e' classificada como `local`: e' `unknown-host`, e
 * a rota responde **404** com `environment_not_permitted`. Nao ha como saber se
 * um host desconhecido esta exposto, entao a resposta e' negar.
 */
export function isProductionEnv(env: AccessEnv): boolean {
  return classifyEnv(env) === "vercel-production";
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
 * Mudanca do Gate OM2 em relacao ao OM1: **producao passa a ser tecnicamente
 * elegivel**, mas SOMENTE quando a configuracao OAuth esta completa. Preview e
 * custom environments continuam negados — nao ha decisao documental que os
 * libere, e a ausencia de decisao nao e' permissao.
 *
 * A ordem importa: hospedagem e configuracao OAuth sao avaliadas ANTES de
 * qualquer coisa, para que nenhuma flag isolada destrave um deployment.
 */
export function evaluateAccess(env: AccessEnv, opts: EvaluateOptions = {}): AccessDecision {
  const isTest = env.NODE_ENV === "test";
  const oauth = parseOAuthConfig(env);
  const envClass = classifyEnv(env);

  // ---- Deployment NAO-producao na Vercel ---------------------------------
  // Avaliado ANTES de producao: Preview roda com NODE_ENV=production, e
  // inverter esta ordem foi exatamente a falha de fail-open corrigida aqui.
  // Preview, `development`, custom environment e `VERCEL=1` sem VERCEL_ENV
  // confiavel sao negados SEMPRE — nenhuma flag, backend ou OAuth destrava.
  if (envClass === "vercel-other") {
    return { allowed: false, reason: "hosted_deployment_disabled" };
  }

  // ---- Host desconhecido -------------------------------------------------
  // Nenhum sinal de plataforma, mas o runtime nao e' de desenvolvimento nem de
  // teste. Nao ha como saber se esta exposto, entao nega.
  if (envClass === "unknown-host") {
    return { allowed: false, reason: "environment_not_permitted" };
  }

  // ---- Deployment de PRODUCAO na Vercel ----------------------------------
  if (envClass === "vercel-production") {
    // Sem OAuth completo, producao continua respondendo 404 generico: nunca
    // anunciamos uma rota parcialmente configurada.
    if (oauth === null) return { allowed: false, reason: "missing_oauth_config" };
    if (env.ORACLE_MCP_ENABLED !== "1") return { allowed: false, reason: "not_enabled" };
    if (!isUsableBackendUrl(env.MCP_BACKEND_API_URL)) {
      return { allowed: false, reason: "missing_backend_config" };
    }
    // Stub NUNCA vale em producao, mesmo sob NODE_ENV=test simultaneo.
    if (opts.testIdentity) return { allowed: false, reason: "stub_not_allowed" };

    return {
      allowed: true,
      mode: "production",
      backendBaseUrl: env.MCP_BACKEND_API_URL,
      // Em producao `auth` e' SEMPRE non-null: garantido pelo check acima.
      auth: oauth,
    };
  }

  // ---- Execucao LOCAL / teste -------------------------------------------
  // Alcancado somente quando `classifyEnv` devolveu "local", isto e', sem
  // NENHUM sinal de deployment. Nao ha URL publica aqui.
  // Stub: so sob NODE_ENV=test. Fora disso e' negacao explicita, nunca
  // "ignora o stub e segue" — um stub vazando seria falha material.
  if (opts.testIdentity && !isTest) {
    return { allowed: false, reason: "stub_not_allowed" };
  }

  if (env.ORACLE_MCP_ENABLED !== "1") {
    return { allowed: false, reason: "not_enabled" };
  }

  if (!isUsableBackendUrl(env.MCP_BACKEND_API_URL)) {
    return { allowed: false, reason: "missing_backend_config" };
  }

  return {
    allowed: true,
    mode: isTest ? "test" : "development",
    backendBaseUrl: env.MCP_BACKEND_API_URL,
    // Localmente, OAuth e' OPCIONAL: configurado, passa a ser exigido; ausente,
    // mantem o modo de desenvolvimento do OM1 (que nunca alcanca hospedagem).
    auth: oauth,
  };
}
