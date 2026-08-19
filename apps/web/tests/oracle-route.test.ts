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
 * `_meta` obrigatorio da era 2026-07-28: cada requisicao carrega versao,
 * clientInfo e capabilities. NAO existe handshake `initialize` nesta era —
 * `initialize` responde -32601 (Method not found), e a descoberta e' feita
 * por `server/discover`. O transporte e' stateless por construcao.
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
 * POST MCP valido para a spec 2026-07-28: o header `Mcp-Method` e' obrigatorio
 * e precisa concordar com o `method` do corpo (divergencia -> -32020).
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

test("P1: `initialize` NAO existe na era 2026-07-28 (handshake foi removido)", async () => {
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

test("header Mcp-Method ausente e' rejeitado sem vazar detalhe interno", async () => {
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
