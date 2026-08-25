// Testes do HANDLER REAL do endpoint /api/mcp: politica de metodos (P7/P8),
// fail-closed em producao e ausencia de transporte SSE paralelo.
//
// O ambiente e' INJETADO (nunca mutamos process.env): isso torna a matriz
// determinista e independente da ordem de execucao dos testes.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { handleMcpRequest } from "../src/server/oracle/handler.ts";
import type { AccessEnv } from "../src/server/oracle/access.ts";
import { BRANDS, jsonFetch, OVERVIEW, type CallLog } from "./oracle-fixtures.ts";

const HERE = dirname(fileURLToPath(import.meta.url));

const ENABLED: AccessEnv = {
  NODE_ENV: "development",
  ORACLE_MCP_ENABLED: "1",
  MCP_BACKEND_API_URL: "https://mktplace-api.onrender.com",
};

/** `fetch` espiao: qualquer chamada upstream numa negacao seria falha grave. */
function spyFetch() {
  const calls: string[] = [];
  const impl = (async (input: RequestInfo | URL) => {
    calls.push(String(input));
    return new Response("{}", { headers: { "content-type": "application/json" } });
  }) as unknown as typeof fetch;
  return { calls, impl };
}

/**
 * `_meta` da era 2026-07-28: versao, clientInfo e capabilities por requisicao.
 *
 * ATENCAO AO ESCOPO. Este envelope e' o que FAZ a requisicao ser classificada
 * como moderna — `isLegacyRequest` devolve `false` exatamente quando ele esta
 * presente. Tudo que os testes desta secao afirmam sobre `Mcp-Method`,
 * `server/discover` e a ausencia de `initialize` vale SOMENTE para requisicoes
 * que carregam este envelope, isto e', para clientes que se declaram 2026.
 *
 * O endpoint serve as DUAS eras: `createMcpHandler` roda com
 * `legacy: 'stateless'` (o padrao quando a opcao e' omitida), entao uma
 * requisicao SEM este envelope — a forma claim-less do fluxo 2025-06-18 — e'
 * roteada para o leg 2025 e recebe o handshake `initialize` normalmente. Esse
 * caminho esta coberto na secao "lifecycle 2025-06-18" no fim deste arquivo.
 * NAO generalize as afirmacoes desta secao para ele: foi essa generalizacao
 * que ja' produziu um diagnostico errado de "incompatibilidade de protocolo".
 */
const REQUEST_META = {
  "io.modelcontextprotocol/protocolVersion": "2026-07-28",
  "io.modelcontextprotocol/clientInfo": { name: "route-test", version: "0.0.0" },
  "io.modelcontextprotocol/clientCapabilities": {},
};

const DISCOVER = {
  jsonrpc: "2.0",
  id: 1,
  method: "server/discover",
  params: { _meta: REQUEST_META },
};

/**
 * POST MCP valido para a spec 2026-07-28: com o envelope presente, o header
 * `Mcp-Method` e' obrigatorio e precisa concordar com o `method` do corpo
 * (divergencia -> -32020).
 *
 * Exigencia do leg MODERNO apenas. O leg 2025 nao pede header nenhum — ver
 * `legacyPost` na secao "lifecycle 2025-06-18".
 */
function mcpRequest(body: { method?: string }, opts: { omitMcpMethod?: boolean } = {}): Request {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
  };
  if (!opts.omitMcpMethod && body.method) headers["mcp-method"] = body.method;

  return new Request("https://local.test/api/mcp", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Fail-closed
// ---------------------------------------------------------------------------

test("producao: POST devolve 404 e nao toca o upstream", async () => {
  const spy = spyFetch();
  const res = await handleMcpRequest(
    mcpRequest(DISCOVER),
    { ...ENABLED, NODE_ENV: "production" },
    { fetchImpl: spy.impl },
  );

  assert.equal(res.status, 404);
  assert.deepEqual(await res.json(), { error: "not_found" });
  assert.equal(spy.calls.length, 0, "negacao nao pode gerar chamada upstream");
});

test("producao COM a flag local ligada continua 404", async () => {
  const res = await handleMcpRequest(mcpRequest(DISCOVER), {
    ...ENABLED,
    NODE_ENV: "production",
    ORACLE_MCP_ENABLED: "1",
  });
  assert.equal(res.status, 404);
});

test("VERCEL_ENV=production devolve 404 mesmo em NODE_ENV=development", async () => {
  const res = await handleMcpRequest(mcpRequest(DISCOVER), {
    ...ENABLED,
    VERCEL_ENV: "production",
  });
  assert.equal(res.status, 404);
});

test("dev sem habilitacao explicita devolve 404", async () => {
  const res = await handleMcpRequest(mcpRequest(DISCOVER), {
    ...ENABLED,
    ORACLE_MCP_ENABLED: undefined,
  });
  assert.equal(res.status, 404);
});

test("dev sem backend configurado devolve 404", async () => {
  const res = await handleMcpRequest(mcpRequest(DISCOVER), {
    ...ENABLED,
    MCP_BACKEND_API_URL: undefined,
  });
  assert.equal(res.status, 404);
});

test("a negacao nao vaza nome de variavel de ambiente nem host", async () => {
  const res = await handleMcpRequest(mcpRequest(DISCOVER), {
    ...ENABLED,
    NODE_ENV: "production",
  });
  const text = await res.text();
  for (const leak of ["ORACLE_MCP_ENABLED", "MCP_BACKEND_API_URL", "VERCEL_ENV", "mktplace-api.onrender.com"]) {
    assert.ok(!text.includes(leak), `${leak} nao pode aparecer`);
  }
});

// ---------------------------------------------------------------------------
// Caminho habilitado e politica de metodos
// ---------------------------------------------------------------------------

test("P1: dev habilitado — server/discover responde com serverInfo", async () => {
  const res = await handleMcpRequest(mcpRequest(DISCOVER), ENABLED);
  assert.equal(res.status, 200);
  const text = await res.text();
  assert.ok(text.includes("torre-marketplace"), "serverInfo deve vir na resposta");
  assert.ok(text.includes("2026-07-28"), "versao suportada declarada");
});

// Escopo estrito: um `initialize` que SE DECLARA 2026 (envelope `_meta` +
// header `Mcp-Method`) e' roteado para o leg moderno, onde o handshake nao
// existe. Isto NAO diz nada sobre o `initialize` claim-less, coberto na secao
// "lifecycle 2025-06-18", que responde 200.
test("P1: `initialize` COM envelope 2026 cai no leg moderno e e' Method not found", async () => {
  const res = await handleMcpRequest(
    mcpRequest({ jsonrpc: "2.0", id: 9, method: "initialize", params: { _meta: REQUEST_META } } as never),
    ENABLED,
  );
  const body = await res.text();
  assert.match(body, /-32601|method not found/i, "initialize deve ser Method not found");
});

test("P8: GET (operacao de sessao 2025) responde 405 — servidor stateless", async () => {
  const res = await handleMcpRequest(
    new Request("https://local.test/api/mcp", {
      method: "GET",
      headers: { accept: "text/event-stream" },
    }),
    ENABLED,
  );
  assert.equal(res.status, 405, "sem sessao para abrir stream");
});

test("P8: DELETE responde 405 — nao ha sessao para encerrar", async () => {
  const res = await handleMcpRequest(
    new Request("https://local.test/api/mcp", { method: "DELETE" }),
    ENABLED,
  );
  assert.equal(res.status, 405);
});

test("GET/DELETE em producao continuam 404 (nao 405): a rota nao existe", async () => {
  const prod = { ...ENABLED, NODE_ENV: "production" };
  const get = await handleMcpRequest(new Request("https://local.test/api/mcp"), prod);
  const del = await handleMcpRequest(
    new Request("https://local.test/api/mcp", { method: "DELETE" }),
    prod,
  );
  assert.equal(get.status, 404);
  assert.equal(del.status, 404);
});

test("leg moderno: envelope 2026 SEM header Mcp-Method e' rejeitado sem vazar detalhe", async () => {
  const res = await handleMcpRequest(mcpRequest(DISCOVER, { omitMcpMethod: true }), ENABLED);
  assert.equal(res.status, 400, "transporte moderno exige concordancia header/corpo");
  const text = await res.text();
  assert.ok(!text.includes("mktplace-api.onrender.com"));
  assert.ok(!/\bat\s+\w+\s+\(/.test(text), "sem stack trace");
});

test("corpo JSON invalido nao derruba o handler nem vaza stack", async () => {
  const res = await handleMcpRequest(
    new Request("https://local.test/api/mcp", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        "mcp-method": "initialize",
      },
      body: "{ isso nao e json",
    }),
    ENABLED,
  );
  assert.ok(res.status >= 400 && res.status < 500, `status inesperado: ${res.status}`);
  const text = await res.text();
  assert.ok(!/\bat\s+\w+\s+\(/.test(text), "sem stack trace");
  assert.ok(!text.includes("mktplace-api.onrender.com"), "sem host interno");
});

test("tools/list via HTTP nao dispara nenhuma chamada upstream", async () => {
  const spy = spyFetch();
  // Sem handshake: no transporte stateless `tools/list` e' auto-suficiente.
  // O que importa aqui e' que descobrir tools NAO consulta o FastAPI.
  const res = await handleMcpRequest(
    mcpRequest({
      jsonrpc: "2.0",
      id: 2,
      method: "tools/list",
      params: { _meta: REQUEST_META },
    } as never),
    ENABLED,
    { fetchImpl: spy.impl },
  );
  assert.equal(res.status, 200);
  const listed = await res.text();
  assert.ok(listed.includes("torre_desempenho_marketplaces"));
  assert.equal(spy.calls.length, 0, "descoberta de tools e' local");
});

// ---------------------------------------------------------------------------
// lifecycle 2025-06-18 — fluxo claim-less equivalente ao contrato 2025
//
// Regressao para um diagnostico errado: os testes acima descrevem o leg
// MODERNO e ja' foram lidos como se dissessem que o endpoint nao fala 2025.
// Falam. `createMcpHandler` roda com `legacy: 'stateless'` (padrao), entao uma
// requisicao SEM o envelope `_meta` de 2026 e SEM header proprietario nenhum
// e' roteada para o leg 2025 e recebe o handshake completo.
//
// ESCOPO: isto fixa o contrato do SERVIDOR na versao exata 2025-06-18. Nao e'
// captura do conector do Claude.ai — nenhum trafego real dele foi observado.
//
// Nenhum destes testes envia `Mcp-Method`, `Mcp-Name` ou `_meta`.
// ---------------------------------------------------------------------------

/** POST claim-less: exatamente o que um cliente 2025-06-18 manda. */
function legacyPost(body: unknown, extraHeaders: Record<string, string> = {}): Request {
  return new Request("https://local.test/api/mcp", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      ...extraHeaders,
    },
    body: JSON.stringify(body),
  });
}

const initializeBody = (protocolVersion: string) => ({
  jsonrpc: "2.0",
  id: 1,
  method: "initialize",
  params: {
    protocolVersion,
    capabilities: {},
    clientInfo: { name: "claude-like", version: "1.0.0" },
  },
});

/**
 * O leg 2025 responde em Streamable HTTP: `text/event-stream` com frames
 * `event: message` / `data: <json>`. Extrai o primeiro payload JSON-RPC,
 * aceitando tambem `application/json` puro.
 */
async function jsonRpcOf(res: Response): Promise<Record<string, never>> {
  const raw = await res.text();
  const line = raw.split("\n").find((l) => l.startsWith("data:"));
  return JSON.parse(line === undefined ? raw : line.slice("data:".length).trim());
}

test("2025: initialize claim-less devolve InitializeResult com a versao pedida", async () => {
  const spy = spyFetch();
  const res = await handleMcpRequest(legacyPost(initializeBody("2025-06-18")), ENABLED, {
    fetchImpl: spy.impl,
  });

  assert.equal(res.status, 200);
  const msg = await jsonRpcOf(res);
  const result = (msg as { result?: Record<string, never> }).result;
  assert.ok(result, "initialize precisa devolver result, nunca error");

  const r = result as unknown as {
    protocolVersion: string;
    capabilities: { tools?: unknown };
    serverInfo: { name: string; version: string };
  };
  assert.equal(r.protocolVersion, "2025-06-18", "a versao negociada e' a que o cliente pediu");
  assert.ok(r.capabilities.tools, "capabilities.tools obrigatorio");
  assert.equal(r.serverInfo.name, "torre-marketplace");
  assert.ok(r.serverInfo.version, "serverInfo.version obrigatorio");
  assert.equal(spy.calls.length, 0, "initialize NUNCA pode chamar o backend");
});

test("2025: header MCP-Protocol-Version padrao e' aceito (nao e' Mcp-Method)", async () => {
  const spy = spyFetch();
  const res = await handleMcpRequest(
    legacyPost(initializeBody("2025-06-18"), { "mcp-protocol-version": "2025-06-18" }),
    ENABLED,
    { fetchImpl: spy.impl },
  );

  assert.equal(res.status, 200);
  const r = (await jsonRpcOf(res)) as unknown as { result: { protocolVersion: string } };
  assert.equal(r.result.protocolVersion, "2025-06-18");
  assert.equal(spy.calls.length, 0);
});

test("2025: versao nao suportada nao e' ecoada — servidor contrapropoe a sua", async () => {
  const res = await handleMcpRequest(legacyPost(initializeBody("1999-01-01")), ENABLED);
  assert.equal(res.status, 200);

  const r = (await jsonRpcOf(res)) as unknown as { result: { protocolVersion: string } };
  assert.notEqual(r.result.protocolVersion, "1999-01-01", "versao invalida nunca e' aceita");
  // Contraproposta e' o comportamento de spec: o servidor oferece a sua e o
  // cliente decide desconectar. Precisa ser uma versao que o servidor fala.
  assert.match(r.result.protocolVersion, /^20\d\d-\d\d-\d\d$/);
});

test("2025: notifications/initialized e' aceita, sem erro e sem backend", async () => {
  const spy = spyFetch();
  const res = await handleMcpRequest(
    legacyPost({ jsonrpc: "2.0", method: "notifications/initialized" }),
    ENABLED,
    { fetchImpl: spy.impl },
  );

  assert.equal(res.status, 202, "notificacao e' aceita sem corpo de resposta");
  assert.equal(await res.text(), "", "notificacao NAO pode receber result JSON-RPC fabricado");
  assert.equal(spy.calls.length, 0);
});

test("2025: tools/list apos o lifecycle expoe exatamente as cinco tools", async () => {
  const spy = spyFetch();
  await handleMcpRequest(legacyPost(initializeBody("2025-06-18")), ENABLED, { fetchImpl: spy.impl });
  await handleMcpRequest(legacyPost({ jsonrpc: "2.0", method: "notifications/initialized" }), ENABLED, {
    fetchImpl: spy.impl,
  });

  const res = await handleMcpRequest(
    legacyPost({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }, {
      "mcp-protocol-version": "2025-06-18",
    }),
    ENABLED,
    { fetchImpl: spy.impl },
  );

  assert.equal(res.status, 200);
  const msg = (await jsonRpcOf(res)) as unknown as {
    result: { tools: { name: string; inputSchema: unknown }[] };
  };
  const names = msg.result.tools.map((t) => t.name).sort();
  assert.deepEqual(names, [
    "torre_comparar_canais_marcas",
    "torre_desempenho_marketplaces",
    "torre_produtos_prioritarios",
    "torre_qualidade_dados",
    "torre_regioes_vendas",
  ]);
  assert.equal(names.length, 5, "nenhuma sexta tool");
  for (const t of msg.result.tools) assert.ok(t.inputSchema, `${t.name} precisa de inputSchema`);
  assert.equal(spy.calls.length, 0, "o lifecycle inteiro nao toca o backend");
});

test("2025: tools/call sem Mcp-Name executa a tool e so' ENTAO chama o backend", async () => {
  const log: CallLog = [];
  const res = await handleMcpRequest(
    legacyPost({
      jsonrpc: "2.0",
      id: 3,
      method: "tools/call",
      params: { name: "torre_desempenho_marketplaces", arguments: { periodo: "mes_anterior" } },
    }),
    ENABLED,
    {
      fetchImpl: jsonFetch(
        {
          "/api/v1/performance/overview": OVERVIEW,
          "/api/v1/performance/brands": BRANDS,
        },
        log,
      ),
    },
  );

  assert.equal(res.status, 200);
  const msg = (await jsonRpcOf(res)) as unknown as {
    result: { isError?: boolean; structuredContent?: unknown };
  };
  assert.notEqual(msg.result.isError, true, "chamada valida nao pode ser erro");
  assert.ok(msg.result.structuredContent, "envelope estruturado preservado");
  assert.ok(log.length > 0, "tools/call e' o UNICO ponto que pode alcancar o FastAPI");
});

test("2025: tool desconhecida e' erro JSON-RPC sanitizado", async () => {
  const spy = spyFetch();
  const res = await handleMcpRequest(
    legacyPost({ jsonrpc: "2.0", id: 4, method: "tools/call", params: { name: "run_sql", arguments: {} } }),
    ENABLED,
    { fetchImpl: spy.impl },
  );

  const msg = (await jsonRpcOf(res)) as unknown as { error?: { code: number; message: string } };
  assert.ok(msg.error, "tool fora da allowlist precisa falhar");
  assert.equal(msg.error.code, -32602);
  assert.ok(!/\bat\s+\w+\s+\(/.test(msg.error.message), "sem stack trace");
  assert.ok(!msg.error.message.includes("mktplace-api.onrender.com"), "sem host interno");
  assert.equal(spy.calls.length, 0, "tool inexistente nunca alcanca o backend");
});

test("2025: fronteira fail-closed vale igual para o leg legado", async () => {
  const spy = spyFetch();
  for (const env of [
    { ...ENABLED, NODE_ENV: "production" },
    { ...ENABLED, VERCEL: "1", VERCEL_ENV: "preview", NODE_ENV: "production" },
    { ...ENABLED, ORACLE_MCP_ENABLED: undefined },
    { ...ENABLED, MCP_BACKEND_API_URL: undefined },
  ]) {
    const res = await handleMcpRequest(legacyPost(initializeBody("2025-06-18")), env as AccessEnv, {
      fetchImpl: spy.impl,
    });
    assert.equal(res.status, 404, "claim-less nao destrava a fronteira de ambiente");
    assert.deepEqual(await res.json(), { error: "not_found" });
  }
  assert.equal(spy.calls.length, 0);
});

// ---------------------------------------------------------------------------
// /sse — nenhum transporte SSE paralelo foi criado
// ---------------------------------------------------------------------------

test("nao existe rota /sse nem /message no App Router", () => {
  const appDir = resolve(HERE, "../app");
  for (const legacy of ["api/mcp/sse", "api/mcp/message", "api/sse", "api/message"]) {
    assert.ok(
      !existsSync(resolve(appDir, legacy)),
      `${legacy} nao pode existir: nenhum transporte SSE paralelo`,
    );
  }
});

test("apenas a rota /api/mcp foi criada em app/api", () => {
  assert.ok(existsSync(resolve(HERE, "../app/api/mcp/route.ts")));
});

// ---------------------------------------------------------------------------
// OM2-E — observabilidade sanitizada
//
// Uma tentativa de emissao por requisicao, dizendo em QUE fronteira ela
// terminou, sem jamais carregar token, claim, identidade ou corpo. Os testes
// capturam o evento por logger INJETADO — nunca por leitura de stdout global,
// que seria nao-determinista sob execucao concorrente.
//
// Todo `events.length === 1` abaixo vale sob logger FUNCIONAL, que e' o caso
// aqui. O contrato geral e' "no maximo um evento persistido": logging e'
// best-effort, e uma falha do provedor produz zero evento (ver "obs 19").
// ---------------------------------------------------------------------------
import { exportJWK, generateKeyPair, importJWK, SignJWT } from "jose";

import {
  classifyBearerShape,
  classifyProtocolStatus,
  MAX_OBSERVED_BODY_BYTES,
  normalizeHttpMethod,
  type OracleMcpEvent,
} from "../src/server/oracle/observability.ts";

const OBS_ISSUER = "https://gobeaute-oraculo.us.auth0.com/";
const OBS_AUDIENCE = "https://mktplace-gobeaute.vercel.app/api/mcp";

const OBS_PROD: AccessEnv = {
  NODE_ENV: "production",
  VERCEL: "1",
  VERCEL_ENV: "production",
  ORACLE_MCP_ENABLED: "1",
  MCP_BACKEND_API_URL: "https://mktplace-api.onrender.com",
  ORACLE_AUTH_ISSUER: OBS_ISSUER,
  ORACLE_AUTH_AUDIENCE: OBS_AUDIENCE,
  ORACLE_AUTH_REQUIRED_SCOPE: "oracle:read",
};

/** Coletor deterministico dos eventos emitidos. */
function capture() {
  const events: OracleMcpEvent[] = [];
  return { events, logger: (e: OracleMcpEvent) => events.push(e) };
}

/**
 * POST com `content-length` explicito.
 *
 * Requisicoes HTTP reais sempre carregam o header; um `Request` construido a
 * mao no Node nao. Como a extracao do metodo so' acontece dentro do teto
 * declarado, o header precisa existir para exercitar esse caminho.
 */
function observedPost(body: unknown, extra: Record<string, string> = {}): Request {
  const payload = JSON.stringify(body);
  return new Request("https://local.test/api/mcp", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      "content-length": String(Buffer.byteLength(payload)),
      ...extra,
    },
    body: payload,
  });
}

const OBS_INITIALIZE = {
  jsonrpc: "2.0",
  id: 1,
  method: "initialize",
  params: {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "obs", version: "1" },
  },
};

async function obsIdentity() {
  const { privateKey, publicKey } = await generateKeyPair("RS256", { extractable: true });
  const jwk = await exportJWK(publicKey);
  jwk.kid = "obs-key";
  jwk.alg = "RS256";
  jwk.use = "sig";
  const keyResolver = (async () => importJWK(jwk, "RS256")) as never;

  const mint = async (permissions: string[]) =>
    new SignJWT({ permissions })
      .setProtectedHeader({ alg: "RS256", kid: "obs-key" })
      .setIssuer(OBS_ISSUER)
      .setAudience(OBS_AUDIENCE)
      .setSubject("google-oauth2|obs")
      .setIssuedAt()
      .setExpirationTime("10m")
      .sign(privateKey);

  return { keyResolver, mint };
}

// --- 1. ambiente ------------------------------------------------------------

test("obs 1: ambiente negado emite UM evento environment/denied/404", async () => {
  const { events, logger } = capture();
  const spy = spyFetch();

  const res = await handleMcpRequest(
    observedPost(OBS_INITIALIZE),
    { ...ENABLED, NODE_ENV: "production" },
    { fetchImpl: spy.impl, logger },
  );

  assert.equal(res.status, 404);
  assert.equal(events.length, 1, "exatamente um evento");
  const e = events[0];
  assert.equal(e.event, "oracle_mcp_request");
  assert.equal(e.phase, "environment");
  assert.equal(e.outcome, "denied");
  assert.equal(e.http_status, 404);
  assert.equal(e.http_method, "POST");
  assert.equal(e.failure_category, "environment_denied");
  assert.equal(spy.calls.length, 0);
});

// --- 2 a 6. autenticacao e autorizacao --------------------------------------

test("obs 2: Authorization ausente -> bearer_missing em authentication/401", async () => {
  const { events, logger } = capture();
  const { keyResolver } = await obsIdentity();

  const res = await handleMcpRequest(observedPost(OBS_INITIALIZE), OBS_PROD, {
    logger,
    verifierOptions: { keyResolver },
  });

  assert.equal(res.status, 401);
  assert.equal(events.length, 1);
  assert.equal(events[0].phase, "authentication");
  assert.equal(events[0].outcome, "denied");
  assert.equal(events[0].http_status, 401);
  assert.equal(events[0].failure_category, "bearer_missing");
});

test("obs 3: Bearer vazio e esquema nao-Bearer contam como bearer_missing", async () => {
  const { keyResolver } = await obsIdentity();

  for (const header of ["Bearer", "Bearer ", "Basic dXNlcjpwYXNz", "DPoP abc"]) {
    const { events, logger } = capture();
    const res = await handleMcpRequest(
      observedPost(OBS_INITIALIZE, { authorization: header }),
      OBS_PROD,
      { logger, verifierOptions: { keyResolver } },
    );

    assert.equal(res.status, 401, header + " deve ser 401");
    assert.equal(events.length, 1, header + ": um evento");
    assert.equal(events[0].failure_category, "bearer_missing", header + ": sem bearer a avaliar");
  }
});

test("obs 4: bearer opaco (sem tres segmentos) -> token_malformed_or_opaque", async () => {
  const { keyResolver } = await obsIdentity();

  // A hipotese "Auth0 devolveu token opaco" precisa ser distinguivel de um JWT
  // que simplesmente nao verifica — e' esta a linha que as separa.
  for (const opaque of ["v2.local.abcdef.x", "abcdefghijklmnop", "a.b"]) {
    const { events, logger } = capture();
    const res = await handleMcpRequest(
      observedPost(OBS_INITIALIZE, { authorization: "Bearer " + opaque }),
      OBS_PROD,
      { logger, verifierOptions: { keyResolver } },
    );

    assert.equal(res.status, 401);
    assert.equal(events.length, 1);
    assert.equal(events[0].failure_category, "token_malformed_or_opaque", opaque);
  }
});

test("obs 5: JWT com tres segmentos que nao verifica -> jwt_verification_failed", async () => {
  const { events, logger } = capture();
  const { keyResolver } = await obsIdentity();

  // Assinado por OUTRA chave: tem forma de JWT, mas a verificacao reprova.
  const { privateKey } = await generateKeyPair("RS256", { extractable: true });
  const alien = await new SignJWT({ permissions: ["oracle:read"] })
    .setProtectedHeader({ alg: "RS256", kid: "obs-key" })
    .setIssuer(OBS_ISSUER)
    .setAudience(OBS_AUDIENCE)
    .setSubject("google-oauth2|alien")
    .setIssuedAt()
    .setExpirationTime("10m")
    .sign(privateKey);

  const res = await handleMcpRequest(
    observedPost(OBS_INITIALIZE, { authorization: "Bearer " + alien }),
    OBS_PROD,
    { logger, verifierOptions: { keyResolver } },
  );

  assert.equal(res.status, 401);
  assert.equal(events.length, 1);
  assert.equal(events[0].phase, "authentication");
  assert.equal(events[0].failure_category, "jwt_verification_failed");
});

test("obs 6: JWT valido sem a permission -> authorization/403/insufficient_scope", async () => {
  const { events, logger } = capture();
  const { keyResolver, mint } = await obsIdentity();
  const token = await mint(["oracle:reader"]);

  const res = await handleMcpRequest(
    observedPost(OBS_INITIALIZE, { authorization: "Bearer " + token }),
    OBS_PROD,
    { logger, verifierOptions: { keyResolver } },
  );

  assert.equal(res.status, 403);
  assert.equal(events.length, 1);
  assert.equal(events[0].phase, "authorization");
  assert.equal(events[0].outcome, "denied");
  assert.equal(events[0].http_status, 403);
  assert.equal(events[0].failure_category, "insufficient_scope");
});

// --- 7 a 10. protocolo ------------------------------------------------------

test("obs 7-10: lifecycle autenticado emite protocol com o metodo correto", async () => {
  const { keyResolver, mint } = await obsIdentity();
  const token = await mint(["oracle:read"]);
  const auth = { authorization: "Bearer " + token };
  const log: CallLog = [];
  const fetchImpl = jsonFetch(
    { "/api/v1/performance/overview": OVERVIEW, "/api/v1/performance/brands": BRANDS },
    log,
  );
  const run = async (body: unknown) => {
    const { events, logger } = capture();
    const res = await handleMcpRequest(observedPost(body, auth), OBS_PROD, {
      fetchImpl,
      logger,
      verifierOptions: { keyResolver },
    });
    assert.equal(events.length, 1, "um evento por requisicao");
    return { res, e: events[0] };
  };

  // 7. initialize
  const init = await run(OBS_INITIALIZE);
  assert.equal(init.res.status, 200);
  assert.equal(init.e.phase, "protocol");
  assert.equal(init.e.outcome, "completed");
  assert.equal(init.e.http_status, 200);
  assert.equal(init.e.jsonrpc_method, "initialize");
  assert.equal(init.e.failure_category, "none");
  assert.equal(log.length, 0, "initialize nao toca o backend");

  // 8. notifications/initialized
  const notif = await run({ jsonrpc: "2.0", method: "notifications/initialized" });
  assert.equal(notif.res.status, 202);
  assert.equal(notif.e.http_status, 202);
  assert.equal(notif.e.outcome, "completed");
  assert.equal(notif.e.jsonrpc_method, "notifications/initialized");
  assert.equal(log.length, 0);

  // 9. tools/list
  const list = await run({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} });
  assert.equal(list.res.status, 200);
  assert.equal(list.e.jsonrpc_method, "tools/list");
  assert.equal(list.e.outcome, "completed");
  assert.equal(log.length, 0, "tools/list continua local");

  // 10. tools/call — unico ponto que alcanca o backend
  const call = await run({
    jsonrpc: "2.0",
    id: 3,
    method: "tools/call",
    params: { name: "torre_desempenho_marketplaces", arguments: { periodo: "mes_anterior" } },
  });
  assert.equal(call.res.status, 200);
  assert.equal(call.e.jsonrpc_method, "tools/call");
  assert.equal(call.e.outcome, "completed");
  assert.ok(log.length > 0, "so' tools/call alcanca o FastAPI");
});

// --- 11 a 14. extracao defensiva do metodo ----------------------------------

test("obs 11: metodo fora da allowlist vira unknown, sem o nome cru", async () => {
  const { events, logger } = capture();
  const segredo = "admin/drop_database_now";

  const res = await handleMcpRequest(
    observedPost({ jsonrpc: "2.0", id: 1, method: segredo, params: {} }),
    ENABLED,
    { logger, fetchImpl: spyFetch().impl },
  );

  assert.equal(events.length, 1);
  assert.equal(events[0].jsonrpc_method, "unknown");
  assert.ok(!JSON.stringify(events[0]).includes(segredo), "nome cru NUNCA pode aparecer");
  assert.ok(!JSON.stringify(events[0]).includes("drop_database"));
  assert.ok(res.status >= 200);
});

test("obs 12: corpo nao-JSON, array ou sem `method` vira unknown", async () => {
  const corpos = [
    "{ isso nao e json",
    JSON.stringify([{ method: "tools/list" }]),
    JSON.stringify({ id: 1 }),
  ];

  for (const raw of corpos) {
    const { events, logger } = capture();
    const req = new Request("https://local.test/api/mcp", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json, text/event-stream",
        "content-length": String(Buffer.byteLength(raw)),
      },
      body: raw,
    });

    await handleMcpRequest(req, ENABLED, { logger, fetchImpl: spyFetch().impl });
    assert.equal(events.length, 1, "um evento mesmo com corpo invalido");
    assert.equal(events[0].jsonrpc_method, "unknown");
  }
});

test("obs 13: corpo acima do teto, ou sem content-length, vira unknown", async () => {
  const payload = JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "tools/list",
    params: { pad: "x".repeat(MAX_OBSERVED_BODY_BYTES + 1000) },
  });

  // Acima do teto: a observabilidade se recusa a parsear.
  const tooBig = capture();
  await handleMcpRequest(
    new Request("https://local.test/api/mcp", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json, text/event-stream",
        "content-length": String(Buffer.byteLength(payload)),
      },
      body: payload,
    }),
    ENABLED,
    { logger: tooBig.logger, fetchImpl: spyFetch().impl },
  );
  assert.equal(tooBig.events.length, 1);
  assert.equal(tooBig.events[0].jsonrpc_method, "unknown", "acima do teto nao e' inspecionado");

  // Sem content-length confiavel: tambem desiste, por contrato.
  const noLength = capture();
  await handleMcpRequest(
    legacyPost({ jsonrpc: "2.0", id: 1, method: "tools/list", params: {} }),
    ENABLED,
    { logger: noLength.logger, fetchImpl: spyFetch().impl },
  );
  assert.equal(noLength.events.length, 1);
  assert.equal(noLength.events[0].jsonrpc_method, "unknown", "tamanho ausente -> unknown");
});

test("obs 14: GET e DELETE usam unknown e registram o status real", async () => {
  for (const method of ["GET", "DELETE"]) {
    const { events, logger } = capture();
    const res = await handleMcpRequest(
      new Request("https://local.test/api/mcp", { method, headers: { accept: "text/event-stream" } }),
      ENABLED,
      { logger, fetchImpl: spyFetch().impl },
    );

    assert.equal(events.length, 1);
    assert.equal(events[0].jsonrpc_method, "unknown");
    assert.equal(events[0].http_method, method);
    assert.equal(events[0].http_status, res.status, "status registrado e' o realmente devolvido");
    assert.equal(res.status, 405);
  }
});

// --- 15 a 19. robustez ------------------------------------------------------

test("obs 15: falha interna vira internal_error, sem mensagem bruta", async () => {
  const { events, logger } = capture();

  // Corpo ja consumido: o transporte falha e a rota devolve 5xx.
  const req = observedPost({ jsonrpc: "2.0", id: 1, method: "tools/list", params: {} });
  await req.text();

  const res = await handleMcpRequest(req, ENABLED, { logger, fetchImpl: spyFetch().impl });

  assert.ok(res.status >= 500, "esperado 5xx, veio " + res.status);
  assert.equal(events.length, 1);
  assert.equal(events[0].phase, "protocol");
  assert.equal(events[0].outcome, "failed");
  assert.equal(events[0].failure_category, "internal_error");

  const serialized = JSON.stringify(events[0]);
  assert.ok(!/\bat\s+\w+\s+\(/.test(serialized), "sem stack trace");
  assert.ok(!serialized.includes("mktplace-api.onrender.com"), "sem host interno");
  assert.ok(!/TypeError|disturbed|unusable/i.test(serialized), "sem mensagem bruta da runtime");
});

test("obs 16-17: um evento por request e correlation_id novo a cada request", async () => {
  const seen = new Set<string>();

  for (let i = 0; i < 5; i++) {
    const { events, logger } = capture();
    await handleMcpRequest(observedPost(OBS_INITIALIZE), ENABLED, {
      logger,
      fetchImpl: spyFetch().impl,
    });

    assert.equal(events.length, 1, "nunca mais de um evento");
    const id = events[0].correlation_id;
    assert.match(id, /^[0-9a-f-]{36}$/, "UUID gerado no servidor");
    assert.ok(!seen.has(id), "correlation_id nao pode repetir");
    seen.add(id);
    assert.ok(Number.isInteger(events[0].duration_ms) && events[0].duration_ms >= 0);
  }
  assert.equal(seen.size, 5);
});

test("obs 17b: correlation_id vindo do CLIENTE e' ignorado", async () => {
  const { events, logger } = capture();
  const forjado = "11111111-2222-3333-4444-555555555555";

  await handleMcpRequest(
    observedPost(OBS_INITIALIZE, { "x-correlation-id": forjado, "x-request-id": forjado }),
    ENABLED,
    { logger, fetchImpl: spyFetch().impl },
  );

  assert.equal(events.length, 1);
  assert.notEqual(events[0].correlation_id, forjado, "id do cliente NUNCA e' adotado");
});

test("obs 18: o evento serializado nao carrega chave nem valor sensivel", async () => {
  const { keyResolver, mint } = await obsIdentity();
  const token = await mint(["oracle:read"]);
  const collected: OracleMcpEvent[] = [];
  const logger = (e: OracleMcpEvent) => collected.push(e);
  const fetchImpl = jsonFetch({
    "/api/v1/performance/overview": OVERVIEW,
    "/api/v1/performance/brands": BRANDS,
  });

  await handleMcpRequest(observedPost(OBS_INITIALIZE), OBS_PROD, {
    logger,
    verifierOptions: { keyResolver },
  });
  await handleMcpRequest(
    observedPost(OBS_INITIALIZE, {
      authorization: "Bearer " + token,
      cookie: "sessao=abc",
      "user-agent": "Claude/1.0",
    }),
    OBS_PROD,
    { logger, fetchImpl, verifierOptions: { keyResolver } },
  );
  await handleMcpRequest(
    observedPost(
      {
        jsonrpc: "2.0",
        id: 9,
        method: "tools/call",
        params: { name: "torre_desempenho_marketplaces", arguments: { periodo: "mes_anterior" } },
      },
      { authorization: "Bearer " + token },
    ),
    OBS_PROD,
    { logger, fetchImpl, verifierOptions: { keyResolver } },
  );

  assert.equal(collected.length, 3);

  // O schema e' FECHADO: nenhuma chave alem destas nove.
  const allowedKeys = [
    "event",
    "correlation_id",
    "phase",
    "outcome",
    "http_status",
    "http_method",
    "jsonrpc_method",
    "failure_category",
    "duration_ms",
  ].sort();

  const segmentos = token.split(".");

  for (const e of collected) {
    assert.deepEqual(Object.keys(e).sort(), allowedKeys, "schema fechado");

    const serialized = JSON.stringify(e);
    // Valores sensiveis REAIS — nao os rotulos controlados do enum.
    const proibidos = [
      token,
      segmentos[0],
      segmentos[1],
      segmentos[2],
      "sessao=abc",
      "Claude/1.0",
      "google-oauth2|obs",
      OBS_ISSUER,
      OBS_AUDIENCE,
      "oracle:read",
      "obs-key",
      "mes_anterior",
      "mktplace-api.onrender.com",
    ];
    for (const forbidden of proibidos) {
      assert.ok(!serialized.includes(forbidden), "evento nao pode conter: " + forbidden.slice(0, 24));
    }

    // Chaves proibidas, mesmo vazias.
    const chaves = [
      "authorization",
      "token",
      "cookie",
      "sub",
      "email",
      "user-agent",
      "userAgent",
      "aud",
      "iss",
      "azp",
      "kid",
      "scope",
      "permissions",
      "ip",
      "params",
      "body",
      "arguments",
    ];
    for (const key of chaves) {
      assert.ok(!(key in (e as unknown as Record<string, unknown>)), "chave proibida: " + key);
    }

    // Nem o TAMANHO do token pode ser inferido a partir do evento.
    assert.ok(!serialized.includes(String(token.length)), "tamanho do token nao pode vazar");
  }
});

test("obs 19: logger que lanca nao altera a resposta nem derruba a rota", async () => {
  const explosivo = () => {
    throw new Error("logger quebrado de proposito");
  };

  const semLogger = await handleMcpRequest(observedPost(OBS_INITIALIZE), ENABLED, {
    fetchImpl: spyFetch().impl,
  });
  const comLoggerRuim = await handleMcpRequest(observedPost(OBS_INITIALIZE), ENABLED, {
    fetchImpl: spyFetch().impl,
    logger: explosivo,
  });

  assert.equal(comLoggerRuim.status, semLogger.status);
  assert.equal(comLoggerRuim.headers.get("content-type"), semLogger.headers.get("content-type"));
  assert.equal(await comLoggerRuim.text(), await semLogger.text(), "corpo identico");
});

// --- 20. nada regrediu ------------------------------------------------------

test("obs 20: resposta e' equivalente com e sem observabilidade", async () => {
  const { keyResolver, mint } = await obsIdentity();
  const token = await mint(["oracle:read"]);

  const casos: { label: string; env: AccessEnv; headers: Record<string, string> }[] = [
    { label: "404 ambiente", env: { ...ENABLED, NODE_ENV: "production" }, headers: {} },
    { label: "401 sem bearer", env: OBS_PROD, headers: {} },
    { label: "200 autenticado", env: OBS_PROD, headers: { authorization: "Bearer " + token } },
  ];

  for (const c of casos) {
    const sem = await handleMcpRequest(observedPost(OBS_INITIALIZE, c.headers), c.env, {
      verifierOptions: { keyResolver },
    });
    const semBody = await sem.text();

    const { logger } = capture();
    const com = await handleMcpRequest(observedPost(OBS_INITIALIZE, c.headers), c.env, {
      logger,
      verifierOptions: { keyResolver },
    });
    const comBody = await com.text();

    assert.equal(com.status, sem.status, c.label + ": status");
    assert.equal(comBody, semBody, c.label + ": corpo");
    assert.equal(
      com.headers.get("www-authenticate"),
      sem.headers.get("www-authenticate"),
      c.label + ": WWW-Authenticate preservado",
    );
    // Nenhum header de correlacao foi acrescentado a resposta.
    for (const h of ["x-correlation-id", "x-request-id", "x-oracle-correlation-id"]) {
      assert.equal(com.headers.get(h), null, c.label + ": sem header novo (" + h + ")");
    }
  }
});

test("obs: classifyBearerShape e' puro e nao devolve conteudo do token", () => {
  assert.equal(classifyBearerShape(null), "bearer_missing");
  assert.equal(classifyBearerShape(""), "bearer_missing");
  assert.equal(classifyBearerShape("   "), "bearer_missing");
  assert.equal(classifyBearerShape("Bearer"), "bearer_missing");
  assert.equal(classifyBearerShape("Bearer   "), "bearer_missing");
  assert.equal(classifyBearerShape("Basic xyz"), "bearer_missing");
  assert.equal(classifyBearerShape("Bearer opaco"), "token_malformed_or_opaque");
  assert.equal(classifyBearerShape("bearer a.b.c"), "jwt_shaped", "esquema e' case-insensitive");
  assert.equal(classifyBearerShape("Bearer a.b"), "token_malformed_or_opaque");
  assert.equal(classifyBearerShape("Bearer a.b.c.d"), "token_malformed_or_opaque");
  assert.equal(classifyBearerShape("Bearer a.b.c extra"), "token_malformed_or_opaque");

  // O retorno e' sempre um rotulo do enum — nunca deriva do valor recebido.
  const segredo = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJzZWdyZWRvIn0.assinatura";
  const rotulo = classifyBearerShape("Bearer " + segredo);
  assert.equal(rotulo, "jwt_shaped");
  assert.ok(!rotulo.includes("eyJ"), "rotulo nao carrega o token");
});

// ---------------------------------------------------------------------------
// OM2-E (correcao terminal) — quatro findings de precisao e seguranca
// ---------------------------------------------------------------------------

/**
 * Espia QUALQUER acesso ao corpo da requisicao.
 *
 * `headers` e `method` seguem livres — a autenticacao precisa deles. O que se
 * quer provar e' que nenhum acessor de PAYLOAD e' tocado antes de o bearer ser
 * validado: observabilidade jamais pode ser motivo para ler entrada nao
 * autenticada.
 */
function bodyAccessSpy(request: Request): { proxy: Request; touched: string[] } {
  const touched: string[] = [];
  const bodyAccessors = new Set(["clone", "text", "json", "arrayBuffer", "blob", "formData", "bytes", "body"]);

  const proxy = new Proxy(request, {
    get(target, prop, receiver) {
      if (typeof prop === "string" && bodyAccessors.has(prop)) touched.push(prop);
      const value = Reflect.get(target, prop, target);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });

  return { proxy: proxy as Request, touched };
}

// --- FINDING 1: corpo intocado antes da autenticacao ------------------------

test("f1: negacao de AMBIENTE nao toca o corpo e reporta jsonrpc_method unknown", async () => {
  const { events, logger } = capture();
  const spy = spyFetch();
  const { proxy, touched } = bodyAccessSpy(observedPost(OBS_INITIALIZE));

  const res = await handleMcpRequest(proxy, { ...ENABLED, NODE_ENV: "production" }, {
    fetchImpl: spy.impl,
    logger,
  });

  assert.equal(res.status, 404);
  assert.deepEqual(touched, [], "nenhum acessor de corpo pode ser tocado: " + touched.join(","));
  assert.equal(events.length, 1);
  assert.equal(events[0].jsonrpc_method, "unknown", "sem autenticacao, sem inspecao de payload");
  assert.equal(events[0].failure_category, "environment_denied");
  assert.equal(spy.calls.length, 0);
});

test("f1: negacoes 401 e 403 nao tocam o corpo e reportam unknown", async () => {
  const { keyResolver, mint } = await obsIdentity();
  const semScope = await mint(["oracle:reader"]);

  const casos: { label: string; headers: Record<string, string>; status: number; categoria: string }[] = [
    { label: "sem bearer", headers: {}, status: 401, categoria: "bearer_missing" },
    {
      label: "bearer opaco",
      headers: { authorization: "Bearer token-opaco-sem-pontos" },
      status: 401,
      categoria: "token_malformed_or_opaque",
    },
    {
      // Tres segmentos: tem FORMA de JWT, entao a falha e' de verificacao —
      // e' esta a linha que separa "token opaco" de "JWT que nao passou".
      label: "jwt que nao verifica",
      headers: { authorization: "Bearer aaa.bbb.ccc" },
      status: 401,
      categoria: "jwt_verification_failed",
    },
    {
      label: "sem a permission",
      headers: { authorization: "Bearer " + semScope },
      status: 403,
      categoria: "insufficient_scope",
    },
  ];

  for (const c of casos) {
    const { events, logger } = capture();
    const spy = spyFetch();
    // O corpo diz `initialize` bem claramente — e ainda assim nao pode ser lido.
    const { proxy, touched } = bodyAccessSpy(observedPost(OBS_INITIALIZE, c.headers));

    const res = await handleMcpRequest(proxy, OBS_PROD, {
      fetchImpl: spy.impl,
      logger,
      verifierOptions: { keyResolver },
    });

    assert.equal(res.status, c.status, c.label + ": status");
    assert.deepEqual(touched, [], c.label + ": corpo tocado -> " + touched.join(","));
    assert.equal(events.length, 1, c.label + ": um evento");
    assert.equal(events[0].jsonrpc_method, "unknown", c.label + ": payload nao inspecionado");
    assert.equal(events[0].failure_category, c.categoria, c.label + ": categoria");
    assert.equal(spy.calls.length, 0, c.label + ": zero upstream");
  }
});

test("f1: autenticado com sucesso volta a identificar o metodo", async () => {
  const { keyResolver, mint } = await obsIdentity();
  const token = await mint(["oracle:read"]);
  const auth = { authorization: "Bearer " + token };
  const log: CallLog = [];
  const fetchImpl = jsonFetch(
    { "/api/v1/performance/overview": OVERVIEW, "/api/v1/performance/brands": BRANDS },
    log,
  );

  const esperado: [unknown, string][] = [
    [OBS_INITIALIZE, "initialize"],
    [{ jsonrpc: "2.0", method: "notifications/initialized" }, "notifications/initialized"],
    [{ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }, "tools/list"],
    [
      {
        jsonrpc: "2.0",
        id: 3,
        method: "tools/call",
        params: { name: "torre_desempenho_marketplaces", arguments: { periodo: "mes_anterior" } },
      },
      "tools/call",
    ],
  ];

  for (const [body, metodo] of esperado) {
    const { events, logger } = capture();
    await handleMcpRequest(observedPost(body, auth), OBS_PROD, {
      fetchImpl,
      logger,
      verifierOptions: { keyResolver },
    });
    assert.equal(events.length, 1);
    assert.equal(events[0].jsonrpc_method, metodo, "depois do bearer, o metodo volta a ser legivel");
  }
});

test("f1: o teto de 64 KiB continua valendo depois da autenticacao", async () => {
  const { keyResolver, mint } = await obsIdentity();
  const token = await mint(["oracle:read"]);
  const { events, logger } = capture();

  const payload = JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "tools/list",
    params: { pad: "x".repeat(MAX_OBSERVED_BODY_BYTES + 1000) },
  });

  await handleMcpRequest(
    new Request("https://local.test/api/mcp", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json, text/event-stream",
        "content-length": String(Buffer.byteLength(payload)),
        authorization: "Bearer " + token,
      },
      body: payload,
    }),
    OBS_PROD,
    { logger, verifierOptions: { keyResolver }, fetchImpl: spyFetch().impl },
  );

  assert.equal(events.length, 1);
  assert.equal(events[0].jsonrpc_method, "unknown", "teto nao foi relaxado");
});

// --- FINDING 2: http_status null na excecao ---------------------------------

test("f2: excecao relancada registra http_status null, nao 500 fabricado", () => {
  // O ramo de excecao e' DEFENSIVO: nenhuma entrada HTTP construivel faz
  // `handler.fetch` lancar — foram sondados signal abortado, accept ausente,
  // content-type invalido, verbo desconhecido, corpo consumido e proxies que
  // lancam em body/text/json/arrayBuffer/bodyUsed/signal/url, e TODOS voltaram
  // como `Response`. A decisao e' por isso testada na funcao pura que o handler
  // usa, e o `throw err` do handler preserva a excecao original.
  const semResposta = classifyProtocolStatus(null);
  assert.equal(semResposta.outcome, "failed");
  assert.equal(semResposta.failureCategory, "internal_error");

  // Um 500 REAL, observado como Response, e' coisa diferente de `null`.
  const comResposta = classifyProtocolStatus(500);
  assert.equal(comResposta.outcome, "failed");
  assert.equal(comResposta.failureCategory, "internal_error");
});

test("f2: 5xx recebido como Response registra o status EXATO, nunca null", async () => {
  const { events, logger } = capture();

  const req = observedPost({ jsonrpc: "2.0", id: 1, method: "tools/list", params: {} });
  await req.text(); // corpo consumido -> transporte falha e devolve 5xx

  const res = await handleMcpRequest(req, ENABLED, { logger, fetchImpl: spyFetch().impl });

  assert.ok(res.status >= 500);
  assert.equal(events.length, 1);
  assert.equal(events[0].http_status, res.status, "houve Response: status exato");
  assert.notEqual(events[0].http_status, null, "null e' reservado a ausencia de Response");
  assert.equal(events[0].failure_category, "internal_error");
});

// --- FINDING 3: vocabulario fechado de http_method --------------------------

test("f3: normalizeHttpMethod fecha o vocabulario em GET/POST/DELETE/OTHER", () => {
  assert.equal(normalizeHttpMethod("GET"), "GET");
  assert.equal(normalizeHttpMethod("POST"), "POST");
  assert.equal(normalizeHttpMethod("DELETE"), "DELETE");
  assert.equal(normalizeHttpMethod("get"), "GET", "normaliza caixa");
  assert.equal(normalizeHttpMethod("PATCH"), "OTHER");
  assert.equal(normalizeHttpMethod("PUT"), "OTHER");
  assert.equal(normalizeHttpMethod("OPTIONS"), "OTHER");
  assert.equal(normalizeHttpMethod("HEAD"), "OTHER");
  assert.equal(normalizeHttpMethod("TRACE"), "OTHER");
  assert.equal(normalizeHttpMethod(undefined), "OTHER");
  assert.equal(normalizeHttpMethod(""), "OTHER");

  // Verbo sintetico/exotico nunca aparece cru no rotulo devolvido.
  const exotico = "X-DRENAR-BANCO";
  assert.equal(normalizeHttpMethod(exotico), "OTHER");
  assert.ok(!normalizeHttpMethod(exotico).includes("DRENAR"));
});

test("f3: verbo fora da allowlist e' registrado como OTHER, nao cru", async () => {
  const { events, logger } = capture();
  const corpo = JSON.stringify(OBS_INITIALIZE);

  const res = await handleMcpRequest(
    new Request("https://local.test/api/mcp", {
      method: "PATCH",
      headers: {
        "content-type": "application/json",
        accept: "application/json, text/event-stream",
        "content-length": String(Buffer.byteLength(corpo)),
      },
      body: corpo,
    }),
    ENABLED,
    { logger, fetchImpl: spyFetch().impl },
  );

  assert.equal(events.length, 1);
  assert.equal(events[0].http_method, "OTHER", "PATCH colapsa em OTHER");
  assert.ok(!JSON.stringify(events[0]).includes("PATCH"), "verbo cru nao pode aparecer");
  assert.equal(events[0].http_status, res.status);
});

test("f3: GET, POST e DELETE reais preservam o proprio rotulo", async () => {
  for (const method of ["GET", "DELETE"]) {
    const { events, logger } = capture();
    await handleMcpRequest(
      new Request("https://local.test/api/mcp", { method, headers: { accept: "text/event-stream" } }),
      ENABLED,
      { logger, fetchImpl: spyFetch().impl },
    );
    assert.equal(events[0].http_method, method);
  }

  const post = capture();
  await handleMcpRequest(observedPost(OBS_INITIALIZE), ENABLED, {
    logger: post.logger,
    fetchImpl: spyFetch().impl,
  });
  assert.equal(post.events[0].http_method, "POST");
});

// --- FINDING 4: matriz de status semanticamente verdadeira ------------------

test("f4: classifyProtocolStatus distingue 2xx, 3xx, 429, 4xx, 5xx e null", () => {
  const casos: [number | null, string, string][] = [
    [200, "completed", "none"],
    [202, "completed", "none"],
    [204, "completed", "none"],
    [301, "failed", "unexpected_redirect"],
    [302, "failed", "unexpected_redirect"],
    [307, "failed", "unexpected_redirect"],
    [400, "failed", "protocol_rejected"],
    [405, "failed", "protocol_rejected"],
    [406, "failed", "protocol_rejected"],
    [415, "failed", "protocol_rejected"],
    [429, "failed", "rate_limited"],
    [500, "failed", "internal_error"],
    [503, "failed", "internal_error"],
    [null, "failed", "internal_error"],
  ];

  for (const [status, outcome, categoria] of casos) {
    const r = classifyProtocolStatus(status);
    assert.equal(r.outcome, outcome, "outcome de " + String(status));
    assert.equal(r.failureCategory, categoria, "categoria de " + String(status));
  }

  // Um 3xx NAO pode passar por sucesso: era exatamente o defeito corrigido.
  assert.notEqual(classifyProtocolStatus(302).outcome, "completed");
  // E 429 nao e' "sem categoria".
  assert.notEqual(classifyProtocolStatus(429).failureCategory, "none");
});

test("f4: a matriz vale ponta a ponta nos status alcancaveis pelo handler", async () => {
  const corpo = JSON.stringify(OBS_INITIALIZE);
  const comLength = (extra: Record<string, string>) => ({
    "content-length": String(Buffer.byteLength(corpo)),
    ...extra,
  });

  const casos: { label: string; req: () => Request; status: number; outcome: string; categoria: string }[] = [
    {
      label: "200 initialize",
      req: () => observedPost(OBS_INITIALIZE),
      status: 200,
      outcome: "completed",
      categoria: "none",
    },
    {
      label: "202 notification",
      req: () => observedPost({ jsonrpc: "2.0", method: "notifications/initialized" }),
      status: 202,
      outcome: "completed",
      categoria: "none",
    },
    {
      label: "406 sem accept",
      req: () =>
        new Request("https://local.test/api/mcp", {
          method: "POST",
          headers: comLength({ "content-type": "application/json" }),
          body: corpo,
        }),
      status: 406,
      outcome: "failed",
      categoria: "protocol_rejected",
    },
    {
      label: "415 content-type errado",
      req: () =>
        new Request("https://local.test/api/mcp", {
          method: "POST",
          headers: comLength({ "content-type": "text/plain", accept: "application/json, text/event-stream" }),
          body: corpo,
        }),
      status: 415,
      outcome: "failed",
      categoria: "protocol_rejected",
    },
    {
      label: "405 GET",
      req: () => new Request("https://local.test/api/mcp", { method: "GET", headers: { accept: "text/event-stream" } }),
      status: 405,
      outcome: "failed",
      categoria: "protocol_rejected",
    },
  ];

  for (const c of casos) {
    const { events, logger } = capture();
    const res = await handleMcpRequest(c.req(), ENABLED, { logger, fetchImpl: spyFetch().impl });

    assert.equal(res.status, c.status, c.label + ": status HTTP");
    assert.equal(events.length, 1, c.label + ": um evento");
    assert.equal(events[0].http_status, c.status, c.label + ": status no evento");
    assert.equal(events[0].outcome, c.outcome, c.label + ": outcome");
    assert.equal(events[0].failure_category, c.categoria, c.label + ": categoria");
    assert.equal(events[0].phase, "protocol", c.label + ": fase");
  }
});

test("f4: nenhum status alcancavel produz 3xx (o endpoint nunca redireciona)", async () => {
  const { events, logger } = capture();
  const res = await handleMcpRequest(observedPost(OBS_INITIALIZE), ENABLED, {
    logger,
    fetchImpl: spyFetch().impl,
  });

  assert.ok(res.status < 300 || res.status >= 400, "sem redirect no caminho normal");
  assert.notEqual(events[0].failure_category, "unexpected_redirect");
});

// --- invariantes preservados ------------------------------------------------

test("f1-f4: schema segue com nove campos e so' http_status aceita null", async () => {
  const { keyResolver } = await obsIdentity();
  const coletados: OracleMcpEvent[] = [];
  const logger = (e: OracleMcpEvent) => coletados.push(e);

  await handleMcpRequest(observedPost(OBS_INITIALIZE), { ...ENABLED, NODE_ENV: "production" }, { logger });
  await handleMcpRequest(observedPost(OBS_INITIALIZE), OBS_PROD, {
    logger,
    verifierOptions: { keyResolver },
  });
  await handleMcpRequest(observedPost(OBS_INITIALIZE), ENABLED, {
    logger,
    fetchImpl: spyFetch().impl,
  });

  const chaves = [
    "correlation_id",
    "duration_ms",
    "event",
    "failure_category",
    "http_method",
    "http_status",
    "jsonrpc_method",
    "outcome",
    "phase",
  ];

  for (const e of coletados) {
    assert.deepEqual(Object.keys(e).sort(), chaves, "nove campos, sempre");
    assert.ok(e.http_status === null || Number.isInteger(e.http_status), "status e' inteiro ou null");
    assert.ok(["GET", "POST", "DELETE", "OTHER"].includes(e.http_method), "http_method fechado");
    assert.ok(Number.isInteger(e.duration_ms) && e.duration_ms >= 0);
    assert.match(e.correlation_id, /^[0-9a-f-]{36}$/);
  }
});
