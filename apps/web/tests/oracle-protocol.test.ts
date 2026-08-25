// Testes de PROTOCOLO (P1-P10 do plano) com cliente MCP real in-process.
// Prova initialize -> tools/list -> tools/call sem rede: o transporte e' o
// InMemoryTransport do proprio SDK e o upstream e' um `fetch` falso.
import { test } from "node:test";
import assert from "node:assert/strict";

import { Client } from "@modelcontextprotocol/client";
import { InMemoryTransport } from "@modelcontextprotocol/server";

import { buildOracleServer } from "../src/server/oracle/server.ts";
import { SlidingWindowRateLimiter } from "../src/server/oracle/rate-limit.ts";
import { jsonFetch, OVERVIEW, BRANDS, FIXED_NOW } from "./oracle-fixtures.ts";

const EXPECTED_TOOLS = [
  "torre_comparar_canais_marcas",
  "torre_desempenho_marketplaces",
  "torre_produtos_prioritarios",
  "torre_qualidade_dados",
  "torre_regioes_vendas",
];

async function connect(fetchImpl: typeof fetch) {
  const server = buildOracleServer({
    backendBaseUrl: "https://mktplace-api.onrender.com",
    fetchImpl,
    now: () => FIXED_NOW,
    // Limitador dedicado por teste: sem vazamento de contagem entre casos.
    rateLimiter: new SlidingWindowRateLimiter({ limit: 1000, windowMs: 60_000 }),
  });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "test-client", version: "0.0.0" });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  return { client, server };
}

// P1/P2/P3 — initialize implicito no connect, depois tools/list
test("P2/P3: tools/list expoe exatamente as cinco tools do MVP", async () => {
  const { client } = await connect(jsonFetch({}));
  const listed = await client.listTools();
  const names = listed.tools.map((t) => t.name).sort();

  assert.deepEqual(names, EXPECTED_TOOLS);
  assert.equal(names.length, 5, "nenhuma tool a mais");

  for (const t of listed.tools) {
    assert.ok(t.description && t.description.length > 40, `${t.name} precisa de description util`);
    assert.ok(t.inputSchema, `${t.name} precisa de inputSchema`);
    // Nome compativel com ^[a-zA-Z0-9_-]{1,64}$
    assert.match(t.name, /^[a-zA-Z0-9_-]{1,64}$/);
  }
});

// P4 — chamada valida devolve structuredContent E fallback textual
test("P4/P10: chamada valida devolve structuredContent e bloco de texto", async () => {
  const { client } = await connect(
    jsonFetch({
      "/api/v1/performance/overview": OVERVIEW,
      "/api/v1/performance/brands": BRANDS,
    }),
  );

  const res = await client.callTool({
    name: "torre_desempenho_marketplaces",
    arguments: { periodo: "mes_anterior" },
  });

  assert.notEqual(res.isError, true, "chamada valida nao pode ser erro");
  assert.ok(res.structuredContent, "structuredContent obrigatorio");

  const content = res.content as Array<{ type: string; text?: string }>;
  assert.ok(
    content.some((c) => c.type === "text" && (c.text?.length ?? 0) > 0),
    "fallback textual obrigatorio para interoperabilidade",
  );

  const env = res.structuredContent as { meta: Record<string, unknown> };
  assert.equal(env.meta.currency, "BRL");
  assert.equal(env.meta.monetary_unit, "reais");
  assert.equal(env.meta.timezone, "America/Sao_Paulo");
});

// P5 — input invalido vira erro de execucao de tool, nao erro de protocolo
test("P5: input invalido devolve isError sem derrubar o protocolo", async () => {
  const { client } = await connect(jsonFetch({}));

  const res = await client.callTool({
    name: "torre_produtos_prioritarios",
    arguments: { canal: "canal_que_nao_existe" },
  });

  assert.equal(res.isError, true);
  const text = (res.content as Array<{ text?: string }>).map((c) => c.text).join(" ");
  assert.match(text, /invalid/i);
  // Nao pode vazar o valor recebido nem stack trace.
  assert.ok(!text.includes("canal_que_nao_existe"));
  assert.ok(!/\bat\s+\w+\s+\(/.test(text), "sem stack trace");
});

// P6 — tool inexistente e' erro de protocolo
test("P6: tool desconhecida e' rejeitada", async () => {
  const { client } = await connect(jsonFetch({}));
  await assert.rejects(
    () => client.callTool({ name: "run_sql", arguments: {} }),
    /unknown tool|not found|-32602|invalid/i,
  );
});

// P9 — stateless: duas conexoes independentes produzem o mesmo resultado
test("P9: servidor e' stateless entre conexoes", async () => {
  const fetchImpl = jsonFetch({
    "/api/v1/performance/overview": OVERVIEW,
    "/api/v1/performance/brands": BRANDS,
  });

  const a = await connect(fetchImpl);
  const b = await connect(fetchImpl);

  const argsIn = { name: "torre_desempenho_marketplaces", arguments: { periodo: "mes_anterior" } };
  const r1 = await a.client.callTool(argsIn);
  const r2 = await b.client.callTool(argsIn);

  assert.deepEqual(r1.structuredContent, r2.structuredContent);
});

// ---------------------------------------------------------------------------
// P11 — cliente MCP OFICIAL sobre HTTP REAL, atravessando a fronteira OAuth.
//
// Os testes acima usam `InMemoryTransport`: provam as tools, nao o transporte.
// Este sobe um servidor HTTP de verdade na frente de `handleMcpRequest` com
// ambiente de PRODUCAO e OAuth completo, e conecta o `Client` oficial pelo
// `StreamableHTTPClientTransport` com `versionNegotiation.mode: 'legacy'`
// EXPLICITO — "the plain 2025 connect sequence". O modo e' passado de proposito
// em vez de herdado do padrao: o teste precisa afirmar o que testa.
//
// LIMITE DE ESCOPO, deliberado. Isto prova que o SERVIDOR cumpre o contrato
// legacy quando um cliente OFICIAL o exercita. NAO e' captura do Claude.ai:
// nenhum trafego do conector real foi observado, e nada sobre o que o
// Claude.ai envia pode ser derivado daqui. O cliente oficial negocia a versao
// mais alta que ambos falam (2025-11-25 na pratica); a versao EXATA 2025-06-18
// esta fixada nos testes claim-less de `oracle-route.test.ts`.
//
// A sequencia emitida pelo cliente e' GRAVADA e asseverada — nao presumida.
// ---------------------------------------------------------------------------
import { createServer } from "node:http";
import { exportJWK, generateKeyPair, importJWK, SignJWT } from "jose";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/client";

import { handleMcpRequest } from "../src/server/oracle/handler.ts";
import type { AccessEnv } from "../src/server/oracle/access.ts";

const OAUTH_ISSUER = "https://gobeaute-oraculo.us.auth0.com/";
const OAUTH_AUDIENCE = "https://mktplace-gobeaute.vercel.app/api/mcp";

const PROD_ENV: AccessEnv = {
  NODE_ENV: "production",
  VERCEL: "1",
  VERCEL_ENV: "production",
  ORACLE_MCP_ENABLED: "1",
  MCP_BACKEND_API_URL: "https://mktplace-api.onrender.com",
  ORACLE_AUTH_ISSUER: OAUTH_ISSUER,
  ORACLE_AUTH_AUDIENCE: OAUTH_AUDIENCE,
  ORACLE_AUTH_REQUIRED_SCOPE: "oracle:read",
};

/** Par de chaves efemero: nenhum PEM versionado, nenhum token real do Auth0. */
async function syntheticIdentity() {
  const { privateKey, publicKey } = await generateKeyPair("RS256", { extractable: true });
  const jwk = await exportJWK(publicKey);
  jwk.kid = "test-key";
  jwk.alg = "RS256";
  jwk.use = "sig";

  const token = await new SignJWT({ permissions: ["oracle:read"] })
    .setProtectedHeader({ alg: "RS256", kid: "test-key" })
    .setIssuer(OAUTH_ISSUER)
    .setAudience(OAUTH_AUDIENCE)
    .setSubject("google-oauth2|test")
    .setIssuedAt()
    .setExpirationTime("10m")
    .sign(privateKey);

  return { token, keyResolver: (async () => importJWK(jwk, "RS256")) as never };
}

test("P11: cliente oficial conecta por HTTP real e cumpre o lifecycle 2025", async () => {
  const { token, keyResolver } = await syntheticIdentity();
  const log: { url: string }[] = [];
  const fetchImpl = jsonFetch(
    {
      "/api/v1/performance/overview": OVERVIEW,
      "/api/v1/performance/brands": BRANDS,
    },
    log,
  );

  /** O que o cliente EMITIU de fato — gravado, para nao presumir o SDK. */
  const wire: { method: string; jsonRpc: string; proprietaryHeaders: string[]; meta2026: boolean }[] =
    [];

  const server = createServer((req, res) => {
    void (async () => {
      const chunks: Buffer[] = [];
      for await (const c of req) chunks.push(c as Buffer);
      const hasBody = chunks.length > 0 && req.method !== "GET" && req.method !== "DELETE";
      const rawBody = chunks.length > 0 ? Buffer.concat(chunks) : undefined;

      let jsonRpc = "(sem corpo)";
      if (rawBody !== undefined) {
        try {
          const parsed = JSON.parse(rawBody.toString()) as { method?: string };
          jsonRpc = parsed.method ?? "(resposta)";
        } catch {
          jsonRpc = "(nao-json)";
        }
      }
      wire.push({
        method: req.method ?? "?",
        jsonRpc,
        proprietaryHeaders: ["mcp-method", "mcp-name"].filter((h) => req.headers[h] !== undefined),
        meta2026: rawBody?.toString().includes("io.modelcontextprotocol/protocolVersion") ?? false,
      });

      const request = new Request(`http://127.0.0.1${req.url ?? "/"}`, {
        method: req.method,
        headers: req.headers as never,
        body: hasBody ? rawBody : undefined,
      });
      const out = await handleMcpRequest(request, PROD_ENV, {
        fetchImpl,
        verifierOptions: { keyResolver },
      });
      res.writeHead(out.status, Object.fromEntries(out.headers));
      res.end(out.body === null ? undefined : Buffer.from(await out.arrayBuffer()));
    })();
  });

  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as { port: number };

  const transport = new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${port}/api/mcp`), {
    requestInit: { headers: { authorization: `Bearer ${token}` } },
  });
  // Modo EXPLICITO: o teste nao pode depender de o padrao do SDK ser 'legacy'.
  const client = new Client(
    { name: "official-http-client", version: "1.0.0" },
    { versionNegotiation: { mode: "legacy" } } as never,
  );

  try {
    // connect() = initialize + notifications/initialized, emitidos pelo SDK.
    await client.connect(transport);

    assert.deepEqual(client.getServerVersion(), { name: "torre-marketplace", version: "0.1.0" });
    assert.ok(client.getServerCapabilities()?.tools, "capabilities.tools obrigatorio");
    assert.equal(log.length, 0, "o handshake NAO pode tocar o backend");

    // O handshake foi REALMENTE emitido pelo cliente, nao simulado pelo teste.
    const emitted = wire.map((w) => w.jsonRpc);
    assert.ok(emitted.includes("initialize"), `cliente precisa emitir initialize (viu: ${emitted})`);
    assert.ok(
      emitted.includes("notifications/initialized"),
      `cliente precisa emitir notifications/initialized (viu: ${emitted})`,
    );
    assert.equal(emitted[0], "initialize", "initialize e' a PRIMEIRA mensagem do lifecycle");

    const listed = await client.listTools();
    assert.equal(listed.tools.length, 5, "exatamente cinco tools");
    assert.deepEqual(
      listed.tools.map((t) => t.name).sort(),
      EXPECTED_TOOLS,
      "inventario identico ao do transporte in-process",
    );
    assert.equal(log.length, 0, "tools/list continua local");

    const called = await client.callTool({
      name: "torre_desempenho_marketplaces",
      arguments: { periodo: "mes_anterior" },
    });
    assert.notEqual(called.isError, true, "tools/call valido nao pode ser erro");
    assert.ok(called.structuredContent, "envelope estruturado preservado sobre HTTP");
    assert.ok(log.length > 0, "so' tools/call alcanca o FastAPI");

    // Em NENHUM momento do lifecycle o cliente legacy manda header proprietario
    // da era 2026 ou o envelope `_meta` — e' isso que o mantem no leg 2025.
    for (const w of wire) {
      assert.deepEqual(
        w.proprietaryHeaders,
        [],
        `${w.method} ${w.jsonRpc} nao pode carregar Mcp-Method/Mcp-Name`,
      );
      assert.equal(w.meta2026, false, `${w.method} ${w.jsonRpc} nao pode carregar _meta de 2026`);
    }
  } finally {
    await client.close();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test("P11: cliente oficial sem bearer e' barrado antes de qualquer tool", async () => {
  const { keyResolver } = await syntheticIdentity();
  const log: { url: string }[] = [];

  const server = createServer((req, res) => {
    void (async () => {
      const chunks: Buffer[] = [];
      for await (const c of req) chunks.push(c as Buffer);
      const hasBody = chunks.length > 0 && req.method !== "GET" && req.method !== "DELETE";
      const request = new Request(`http://127.0.0.1${req.url ?? "/"}`, {
        method: req.method,
        headers: req.headers as never,
        body: hasBody ? Buffer.concat(chunks) : undefined,
      });
      const out = await handleMcpRequest(request, PROD_ENV, {
        fetchImpl: jsonFetch({}, log),
        verifierOptions: { keyResolver },
      });
      res.writeHead(out.status, Object.fromEntries(out.headers));
      res.end(out.body === null ? undefined : Buffer.from(await out.arrayBuffer()));
    })();
  });

  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as { port: number };

  const transport = new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${port}/api/mcp`));
  const client = new Client({ name: "no-bearer-client", version: "1.0.0" });

  try {
    await assert.rejects(() => client.connect(transport), "connect sem bearer precisa falhar");
    assert.equal(log.length, 0, "negacao de auth nunca gera trafego upstream");
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
