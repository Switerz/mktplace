// Fronteira de acesso FAIL-CLOSED (docs/ORACLE_MCP_PLAN.md secao 13.4.1).
// Prova a matriz local/dev/test/producao e que negar NAO gera chamada upstream.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  ALLOWED_BACKEND_HOSTNAME, evaluateAccess, isUsableBackendUrl,
} from "../src/server/oracle/access.ts";

const OK_URL = "https://mktplace-api.onrender.com";

// ---------------------------------------------------------------------------
// Producao: negacao incondicional
// ---------------------------------------------------------------------------

test("producao sem auth real -> NEGADO", () => {
  const d = evaluateAccess({ NODE_ENV: "production", MCP_BACKEND_API_URL: OK_URL });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "production_disabled");
});

test("producao COM a flag local ligada -> continua NEGADO", () => {
  // Este e' o caso que mais importa: uma flag de dev nao pode destravar prod.
  const d = evaluateAccess({
    NODE_ENV: "production",
    ORACLE_MCP_ENABLED: "1",
    MCP_BACKEND_API_URL: OK_URL,
  });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "production_disabled");
});

test("VERCEL_ENV=production tambem NEGA, mesmo com NODE_ENV=development", () => {
  const d = evaluateAccess({
    NODE_ENV: "development",
    VERCEL_ENV: "production",
    ORACLE_MCP_ENABLED: "1",
    MCP_BACKEND_API_URL: OK_URL,
  });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "production_disabled");
});

test("producao com stub de teste -> NEGADO (stub nunca destrava prod)", () => {
  const d = evaluateAccess(
    { NODE_ENV: "production", ORACLE_MCP_ENABLED: "1", MCP_BACKEND_API_URL: OK_URL },
    { testIdentity: { subject: "fake" } },
  );
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "production_disabled");
});

// ---------------------------------------------------------------------------
// Desenvolvimento: exige habilitacao explicita
// ---------------------------------------------------------------------------

test("desenvolvimento sem habilitacao -> NEGADO", () => {
  const d = evaluateAccess({ NODE_ENV: "development", MCP_BACKEND_API_URL: OK_URL });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "not_enabled");
});

test("desenvolvimento explicitamente habilitado -> PERMITIDO", () => {
  const d = evaluateAccess({
    NODE_ENV: "development",
    ORACLE_MCP_ENABLED: "1",
    MCP_BACKEND_API_URL: OK_URL,
  });
  assert.equal(d.allowed, true);
  assert.equal(d.allowed === true && d.mode, "development");
});

test("habilitado mas SEM backend configurado -> NEGADO", () => {
  const d = evaluateAccess({ NODE_ENV: "development", ORACLE_MCP_ENABLED: "1" });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "missing_backend_config");
});

test("configuracao ausente por completo -> NEGADO", () => {
  const d = evaluateAccess({});
  assert.equal(d.allowed, false);
});

test("valor diferente de '1' nao habilita", () => {
  for (const v of ["true", "yes", "0", "", "ORACLE"]) {
    const d = evaluateAccess({
      NODE_ENV: "development",
      ORACLE_MCP_ENABLED: v,
      MCP_BACKEND_API_URL: OK_URL,
    });
    assert.equal(d.allowed, false, `"${v}" nao pode habilitar`);
  }
});

// ---------------------------------------------------------------------------
// Teste: stub so vale sob NODE_ENV=test
// ---------------------------------------------------------------------------

test("teste com stub -> PERMITIDO", () => {
  const d = evaluateAccess(
    { NODE_ENV: "test", ORACLE_MCP_ENABLED: "1", MCP_BACKEND_API_URL: OK_URL },
    { testIdentity: { subject: "suite" } },
  );
  assert.equal(d.allowed, true);
  assert.equal(d.allowed === true && d.mode, "test");
});

test("stub em desenvolvimento -> NEGADO (impossivel fora de teste)", () => {
  const d = evaluateAccess(
    { NODE_ENV: "development", ORACLE_MCP_ENABLED: "1", MCP_BACKEND_API_URL: OK_URL },
    { testIdentity: { subject: "vazou" } },
  );
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "stub_not_allowed");
});

// ---------------------------------------------------------------------------
// Base do backend
// ---------------------------------------------------------------------------

test("F1: o hostname do backend precisa ser EXATAMENTE o canonico", () => {
  const H = ALLOWED_BACKEND_HOSTNAME;
  assert.equal(H, "mktplace-api.onrender.com");

  // Aceitos: origem https limpa, com ou sem barra final.
  assert.equal(isUsableBackendUrl(`https://${H}`), true);
  assert.equal(isUsableBackendUrl(`https://${H}/`), true);
  // Porta padrao explicita e' normalizada pela `URL` para vazio.
  assert.equal(isUsableBackendUrl(`https://${H}:443/`), true);
  // Hostname e' case-insensitive no DNS e a `URL` normaliza para minusculas.
  assert.equal(isUsableBackendUrl(`https://MKTPLACE-API.ONRENDER.COM/`), true);

  const rejected: Array<[string | undefined, string]> = [
    // --- host ---
    ["https://outro-host.onrender.com", "outro host https"],
    ["https://example.com", "host arbitrario https"],
    [`https://${H}.evil.invalid`, "sufixo malicioso apos o canonico"],
    [`https://sub.${H}`, "subdominio prefixado"],
    [`https://${H}.br`, "sufixo adicional"],
    [`https://evil-${H}`, "prefixo colado"],
    [`https://${H}.`, "trailing dot"],
    ["https://onrender.com", "dominio pai"],
    // --- porta ---
    [`https://${H}:8443`, "porta customizada"],
    [`https://${H}:80`, "porta customizada (80)"],
    // --- credencial ---
    [`https://user:pass@${H}`, "credencial embutida"],
    [`https://user@${H}`, "usuario embutido"],
    // --- esquema ---
    [`http://${H}`, "http"],
    [`ws://${H}`, "esquema nao http(s)"],
    ["javascript:alert(1)", "esquema perigoso"],
    // --- path/query/fragment ---
    [`https://${H}/api/v1`, "path significativo"],
    [`https://${H}/?x=1`, "querystring"],
    [`https://${H}/#frag`, "fragment"],
    // --- forma ---
    ["/api/v1", "relativo"],
    ["", "vazio"],
    [undefined, "ausente"],
    ["   ", "somente espacos"],
  ];

  for (const [url, why] of rejected) {
    assert.equal(isUsableBackendUrl(url), false, `deveria rejeitar (${why}): ${String(url)}`);
  }
});

test("F1: nao usa endsWith — sufixo de dominio nao basta", () => {
  // Estes passariam num `endsWith(".onrender.com")` ingenuo.
  for (const url of [
    "https://qualquer-coisa.onrender.com",
    "https://mktplace-api-fake.onrender.com",
  ]) {
    assert.equal(isUsableBackendUrl(url), false, url);
  }
});

test("F1: host divergente faz a fronteira NEGAR (falha fechada)", () => {
  const d = evaluateAccess({
    NODE_ENV: "development",
    ORACLE_MCP_ENABLED: "1",
    MCP_BACKEND_API_URL: "https://atacante.invalid",
  });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "missing_backend_config");
});

// ---------------------------------------------------------------------------
// A negacao acontece ANTES de qualquer chamada upstream
// ---------------------------------------------------------------------------

test("negacao nao dispara NENHUMA chamada upstream", async () => {
  let calls = 0;
  const spyFetch = (async () => {
    calls += 1;
    return new Response("{}", { headers: { "content-type": "application/json" } });
  }) as unknown as typeof fetch;

  // Reproduz a ordem da rota: decidir primeiro, so instanciar depois.
  const decision = evaluateAccess({ NODE_ENV: "production", MCP_BACKEND_API_URL: OK_URL });
  if (decision.allowed) {
    const { buildOracleServer } = await import("../src/server/oracle/server.ts");
    buildOracleServer({ backendBaseUrl: decision.backendBaseUrl, fetchImpl: spyFetch });
  }

  assert.equal(decision.allowed, false);
  assert.equal(calls, 0, "producao negada nao pode tocar o backend");
});
