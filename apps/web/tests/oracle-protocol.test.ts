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
