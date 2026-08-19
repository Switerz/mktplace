// Grupo de SEGURANCA (S1-S12 do plano): sanitizacao do upstream, limites,
// injecao, rate limit e ausencia de vazamento.
import { test } from "node:test";
import assert from "node:assert/strict";

import { Client } from "@modelcontextprotocol/client";
import { InMemoryTransport } from "@modelcontextprotocol/server";

import { buildOracleServer } from "../src/server/oracle/server.ts";
import { SlidingWindowRateLimiter } from "../src/server/oracle/rate-limit.ts";
import { TorreClient, ENDPOINTS } from "../src/server/oracle/torre-client.ts";
import { OracleToolError } from "../src/server/oracle/errors.ts";
import {
  badJsonFetch, FIXED_NOW, htmlOkFetch, jsonFetch, networkErrorFetch,
  OVERVIEW, BRANDS, timeoutFetch, wafFetch, type CallLog,
} from "./oracle-fixtures.ts";

async function connect(fetchImpl: typeof fetch, limiter?: SlidingWindowRateLimiter) {
  const server = buildOracleServer({
    backendBaseUrl: "https://mktplace-api.onrender.com",
    fetchImpl,
    now: () => FIXED_NOW,
    rateLimiter: limiter ?? new SlidingWindowRateLimiter({ limit: 1000, windowMs: 60_000 }),
  });
  const [ct, st] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "sec", version: "0.0.0" });
  await Promise.all([client.connect(ct), server.connect(st)]);
  return client;
}

function textOf(res: { content: unknown }): string {
  return (res.content as Array<{ text?: string }>).map((c) => c.text ?? "").join(" ");
}

// ---------------------------------------------------------------------------
// S9 — corpo do upstream NUNCA chega ao modelo
// ---------------------------------------------------------------------------

test("S9: 403 HTML do WAF e' sanitizado — sem HTML e sem IP do solicitante", async () => {
  const client = await connect(wafFetch());
  const res = await client.callTool({
    name: "torre_desempenho_marketplaces",
    arguments: { periodo: "mes_anterior" },
  });

  assert.equal(res.isError, true);
  const text = textOf(res);
  assert.ok(!text.includes("<"), "nenhuma tag HTML pode vazar");
  assert.ok(!text.includes("203.0.113.7"), "IP do solicitante nao pode vazar");
  assert.ok(!/forbidden/i.test(text), "corpo do WAF nao pode vazar");
  assert.match(text, /indisponivel/i);
});

test("200 com corpo HTML e' tratado como resposta invalida", async () => {
  const client = await connect(htmlOkFetch());
  const res = await client.callTool({
    name: "torre_qualidade_dados",
    arguments: {},
  });
  assert.equal(res.isError, true);
  assert.ok(!textOf(res).includes("<"));
});

test("JSON malformado vira categoria sanitizada", async () => {
  const client = await connect(badJsonFetch());
  const res = await client.callTool({
    name: "torre_qualidade_dados",
    arguments: {},
  });
  assert.equal(res.isError, true);
  assert.match(textOf(res), /formato inesperado/i);
});

test("timeout do upstream e' sanitizado", async () => {
  const server = buildOracleServer({
    backendBaseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: timeoutFetch(),
    now: () => FIXED_NOW,
    timeoutMs: 20,
    rateLimiter: new SlidingWindowRateLimiter({ limit: 100, windowMs: 60_000 }),
  });
  const [ct, st] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "sec", version: "0.0.0" });
  await Promise.all([client.connect(ct), server.connect(st)]);

  const res = await client.callTool({
    name: "torre_qualidade_dados",
    arguments: {},
  });
  assert.equal(res.isError, true);
  assert.match(textOf(res), /demorou demais/i);
});

test("erro de rede nao vaza host interno nem porta", async () => {
  const client = await connect(networkErrorFetch());
  const res = await client.callTool({ name: "torre_qualidade_dados", arguments: {} });

  assert.equal(res.isError, true);
  const text = textOf(res);
  // A mensagem original continha "10.0.0.5:5432".
  assert.ok(!text.includes("10.0.0.5"), "IP interno nao pode vazar");
  assert.ok(!text.includes("5432"), "porta de banco nao pode vazar");
  assert.ok(!text.includes("ECONNREFUSED"));
});

test("404/500 do upstream nao expoem status nem URL", async () => {
  // jsonFetch com mapa vazio devolve 404 para qualquer caminho.
  const client = await connect(jsonFetch({}));
  const res = await client.callTool({ name: "torre_qualidade_dados", arguments: {} });

  assert.equal(res.isError, true);
  const text = textOf(res);
  assert.ok(!text.includes("mktplace-api.onrender.com"), "host upstream nao pode vazar");
  assert.ok(!text.includes("404"));
  assert.ok(!text.includes("/api/v1/"), "path interno nao pode vazar");
});

// ---------------------------------------------------------------------------
// S5/S8 — nenhum path arbitrario, nenhuma injecao
// ---------------------------------------------------------------------------

test("S5: nao existe parametro que permita escolher URL/path", async () => {
  const client = await connect(jsonFetch({}));
  const listed = await client.listTools();
  const schemas = JSON.stringify(listed.tools.map((t) => t.inputSchema));

  for (const forbidden of ["url", "path", "endpoint", "host", "query", "sql"]) {
    assert.ok(
      !new RegExp(`"${forbidden}"\\s*:`, "i").test(schemas),
      `nenhum input pode se chamar "${forbidden}"`,
    );
  }
});

test("S8: string de injecao e' rejeitada pelo enum, sem sair requisicao", async () => {
  const log: CallLog = [];
  const client = await connect(jsonFetch({}, log));

  const res = await client.callTool({
    name: "torre_desempenho_marketplaces",
    arguments: { periodo: "mes_anterior", marcas: ["x' OR 1=1--"] },
  });

  assert.equal(res.isError, true);
  assert.equal(log.length, 0, "input invalido nao pode gerar chamada upstream");
});

test("parametro desconhecido e' rejeitado (objeto estrito)", async () => {
  const log: CallLog = [];
  const client = await connect(jsonFetch({}, log));

  const res = await client.callTool({
    name: "torre_qualidade_dados",
    arguments: { extra: "nao deveria existir" },
  });

  assert.equal(res.isError, true);
  assert.equal(log.length, 0);
});

test("F5: parametro interno desconhecido FALHA antes do fetch (nao e' descartado)", async () => {
  const log: CallLog = [];
  const c = new TorreClient({
    baseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: jsonFetch({ "/api/v1/performance/overview": OVERVIEW }, log),
  });

  await assert.rejects(
    () => c.get("overview", { channels: "all", evil: "1", path: "/etc/passwd" } as never),
    (e: unknown) => e instanceof OracleToolError && e.category === "MISSING_CONFIGURATION",
  );
  assert.equal(log.length, 0, "nao pode sair requisicao com parametro desconhecido");
});

test("F5: typo em nome de filtro FALHA em vez de ampliar a consulta", async () => {
  const log: CallLog = [];
  const c = new TorreClient({
    baseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: jsonFetch({ "/api/v1/performance/overview": OVERVIEW }, log),
  });

  // `brand` existe na allowlist; `brandss` e `brnads` sao typos de `brands`.
  // Descartar em silencio removeria o filtro e devolveria TODAS as marcas.
  for (const typo of ["brandss", "brnads", "channel", "date_form"]) {
    await assert.rejects(
      () => c.get("overview", { channels: "all", [typo]: "barbours" } as never),
      (e: unknown) => e instanceof OracleToolError && e.category === "MISSING_CONFIGURATION",
      `"${typo}" deveria falhar`,
    );
  }
  assert.equal(log.length, 0);
});

test("parametro allowlisted continua chegando na querystring", async () => {
  const log: CallLog = [];
  const c = new TorreClient({
    baseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: jsonFetch({ "/api/v1/performance/overview": OVERVIEW }, log),
  });
  await c.get("overview", { channels: "all", brands: "barbours" });

  assert.equal(log.length, 1);
  assert.ok(log[0].url.includes("channels=all"));
  assert.ok(log[0].url.includes("brands=barbours"));
});

test("F5: base URL e' validada estritamente", () => {
  const bad: Array<[string, string]> = [
    ["http://mktplace-api.onrender.com", "http nao e' aceito"],
    ["https://user:pass@mktplace-api.onrender.com", "credencial embutida"],
    ["https://mktplace-api.onrender.com/api/v1", "path significativo"],
    ["https://mktplace-api.onrender.com/?x=1", "querystring"],
    ["https://mktplace-api.onrender.com/#frag", "fragment"],
    ["not-a-url", "url invalida"],
  ];
  for (const [url, why] of bad) {
    assert.throws(
      () => new TorreClient({ baseUrl: url }),
      (e: unknown) => e instanceof OracleToolError && e.category === "MISSING_CONFIGURATION",
      `deveria rejeitar: ${why}`,
    );
  }
  // Raiz explicita e origem pura sao aceitas.
  assert.ok(new TorreClient({ baseUrl: "https://mktplace-api.onrender.com" }));
  assert.ok(new TorreClient({ baseUrl: "https://mktplace-api.onrender.com/" }));
});

// ---------------------------------------------------------------------------
// S6/S7 — limites
// ---------------------------------------------------------------------------

test("S6: limite acima do teto e' rejeitado", async () => {
  const log: CallLog = [];
  const client = await connect(jsonFetch({}, log));
  const res = await client.callTool({
    name: "torre_produtos_prioritarios",
    arguments: { canal: "ml", limite: 500 },
  });
  assert.equal(res.isError, true);
  assert.equal(log.length, 0);
});

test("S7: intervalo maior que 366 dias e' rejeitado antes do HTTP", async () => {
  const log: CallLog = [];
  const client = await connect(jsonFetch({}, log));
  const res = await client.callTool({
    name: "torre_desempenho_marketplaces",
    arguments: {
      periodo: "personalizado",
      data_inicio: "2024-01-01",
      data_fim: "2026-08-18",
    },
  });
  assert.equal(res.isError, true);
  assert.equal(log.length, 0, "nada pode chegar ao Render");
});

test("data futura e' rejeitada", async () => {
  const log: CallLog = [];
  const client = await connect(jsonFetch({}, log));
  const res = await client.callTool({
    name: "torre_desempenho_marketplaces",
    arguments: { periodo: "personalizado", data_inicio: "2026-08-01", data_fim: "2030-01-01" },
  });
  assert.equal(res.isError, true);
  assert.equal(log.length, 0);
});

// ---------------------------------------------------------------------------
// S11/S12 — rate limit (requisito normativo MUST da spec)
// ---------------------------------------------------------------------------

test("S11: exceder o rate limit vira isError, nao erro de protocolo", async () => {
  const limiter = new SlidingWindowRateLimiter({ limit: 2, windowMs: 60_000 });
  const client = await connect(
    jsonFetch({
      "/api/v1/performance/overview": OVERVIEW,
      "/api/v1/performance/brands": BRANDS,
    }),
    limiter,
  );

  const call = () =>
    client.callTool({
      name: "torre_desempenho_marketplaces",
      arguments: { periodo: "mes_anterior" },
    });

  assert.notEqual((await call()).isError, true);
  assert.notEqual((await call()).isError, true);

  const third = await call();
  assert.equal(third.isError, true, "terceira chamada excede o limite");
  assert.match(textOf(third), /limite de chamadas/i);
});

test("S12: varredura em massa e' barrada antes de sobrecarregar o upstream", async () => {
  const log: CallLog = [];
  const limiter = new SlidingWindowRateLimiter({ limit: 1, windowMs: 60_000 });
  const client = await connect(
    jsonFetch(
      {
        "/api/v1/performance/overview": OVERVIEW,
        "/api/v1/performance/brands": BRANDS,
        "/api/v1/performance/trend": { granularity: "day", data: [] },
      },
      log,
    ),
    limiter,
  );

  const heavy = {
    name: "torre_desempenho_marketplaces",
    arguments: {
      periodo: "personalizado",
      data_inicio: "2025-08-19",
      data_fim: "2026-08-18",
      granularidade: "day",
    },
  };

  await client.callTool(heavy);
  const callsAfterFirst = log.length;

  for (let i = 0; i < 5; i++) await client.callTool(heavy);

  assert.equal(log.length, callsAfterFirst, "chamadas barradas nao tocam o Render");
});

// ---------------------------------------------------------------------------
// Config e superficie
// ---------------------------------------------------------------------------

test("base http (nao https) e' recusada na construcao do adapter", () => {
  assert.throws(
    () => new TorreClient({ baseUrl: "http://mktplace-api.onrender.com" }),
    (e: unknown) => e instanceof OracleToolError && e.category === "MISSING_CONFIGURATION",
  );
});

test("todo endpoint allowlisted e' GET read-only sob /api/v1", () => {
  for (const path of Object.values(ENDPOINTS)) {
    assert.match(path, /^\/api\/v1\//, `${path} fora do prefixo esperado`);
  }
  // Superficies proibidas no MVP nao podem estar na allowlist.
  const all = Object.values(ENDPOINTS).join(" ");
  for (const forbidden of ["operacoes", "inteligencia", "brand-detail", "tempo-real", "debug"]) {
    assert.ok(!all.includes(forbidden), `${forbidden} nao pode ser alcancavel`);
  }
});
