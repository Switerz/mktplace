// Testes da rodada de correcao consolidada (F1-F6 + endurecimento de inputs).
// DeterminIsticos, sem rede e sem relogio real.
import { test } from "node:test";
import assert from "node:assert/strict";

import { Client } from "@modelcontextprotocol/client";
import { InMemoryTransport } from "@modelcontextprotocol/server";

import { evaluateAccess, type AccessEnv } from "../src/server/oracle/access.ts";
import { handleMcpRequest } from "../src/server/oracle/handler.ts";
import { OracleToolError } from "../src/server/oracle/errors.ts";
import { SlidingWindowRateLimiter } from "../src/server/oracle/rate-limit.ts";
import { buildOracleServer } from "../src/server/oracle/server.ts";
import { TorreClient } from "../src/server/oracle/torre-client.ts";
import { monthBoundsOf } from "../src/server/oracle/upstream.ts";
import {
  BRANDS, CANAIS, FIXED_NOW, HEALTH, headersThenHangFetch, jsonFetch,
  countingCancelFetch, hangingCancelFetch, multibyteStreamFetch, noStreamFetch,
  OVERVIEW, PRODUTOS_ML, PRODUTOS_SHOPEE,
  PRODUTOS_TIKTOK, QUALITY, REGIOES_BY_UF, REGIOES_SUMMARY, TREND, type CallLog,
} from "./oracle-fixtures.ts";

const ROUTES: Record<string, unknown> = {
  "/api/v1/performance/overview": OVERVIEW,
  "/api/v1/performance/brands": BRANDS,
  "/api/v1/performance/trend": TREND,
  "/api/v1/performance/canais": CANAIS,
  "/api/v1/performance/quality": QUALITY,
  "/api/v1/performance/produtos/ml": PRODUTOS_ML,
  "/api/v1/performance/produtos/tiktok": PRODUTOS_TIKTOK,
  "/api/v1/performance/produtos/shopee": PRODUTOS_SHOPEE,
  "/api/v1/performance/health-datasource": HEALTH,
  "/api/v1/regioes/summary": REGIOES_SUMMARY,
  "/api/v1/regioes/by-uf": REGIOES_BY_UF,
};

async function connect(routes: Record<string, unknown> = ROUTES, log?: CallLog) {
  const server = buildOracleServer({
    backendBaseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: jsonFetch(routes, log),
    now: () => FIXED_NOW,
    rateLimiter: new SlidingWindowRateLimiter({ limit: 1000, windowMs: 60_000 }),
  });
  const [ct, st] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "findings", version: "0.0.0" });
  await Promise.all([client.connect(ct), server.connect(st)]);
  return client;
}

type Env = { meta: Record<string, any>; data: any };

async function callOk(client: Client, name: string, args: Record<string, unknown> = {}) {
  const res = await client.callTool({ name, arguments: args });
  assert.notEqual(res.isError, true, `${name} deveria ter sucesso`);
  return res.structuredContent as Env;
}

async function callErr(client: Client, name: string, args: Record<string, unknown> = {}) {
  const res = await client.callTool({ name, arguments: args });
  assert.equal(res.isError, true, `${name} deveria falhar`);
  return (res.content as Array<{ text?: string }>).map((c) => c.text ?? "").join(" ");
}

/** Substitui um endpoint por um payload quebrado. */
function withBroken(path: string, payload: unknown): Record<string, unknown> {
  return { ...ROUTES, [path]: payload };
}

// ===========================================================================
// F1 — negar TODO deployment, nao apenas producao
// ===========================================================================

const LOCAL_OK: AccessEnv = {
  NODE_ENV: "development",
  ORACLE_MCP_ENABLED: "1",
  MCP_BACKEND_API_URL: "https://mktplace-api.onrender.com",
};

test("F1: Preview da Vercel e' NEGADO mesmo com NODE_ENV=development", () => {
  const d = evaluateAccess({ ...LOCAL_OK, VERCEL_ENV: "preview" });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "hosted_deployment_disabled");
});

test("F1: VERCEL=1 (qualquer execucao hospedada) e' NEGADO", () => {
  const d = evaluateAccess({ ...LOCAL_OK, VERCEL: "1" });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "hosted_deployment_disabled");
});

test("F1: custom environment (VERCEL_TARGET_ENV) e' NEGADO", () => {
  const d = evaluateAccess({ ...LOCAL_OK, VERCEL_TARGET_ENV: "staging-qa" });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "hosted_deployment_disabled");
});

test("F1: custom environment publicado como producao e' NEGADO como producao", () => {
  const d = evaluateAccess({ ...LOCAL_OK, VERCEL_TARGET_ENV: "production" });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "production_disabled");
});

test("F1: VERCEL_ENV=development (vercel dev) tambem e' hospedado -> NEGADO", () => {
  const d = evaluateAccess({ ...LOCAL_OK, VERCEL_ENV: "development" });
  assert.equal(d.allowed, false);
  assert.equal(d.allowed === false && d.reason, "hosted_deployment_disabled");
});

test("F1: producao continua NEGADA por NODE_ENV e por VERCEL_ENV", () => {
  for (const env of [
    { ...LOCAL_OK, NODE_ENV: "production" },
    { ...LOCAL_OK, VERCEL_ENV: "production" },
  ]) {
    const d = evaluateAccess(env);
    assert.equal(d.allowed, false);
    assert.equal(d.allowed === false && d.reason, "production_disabled");
  }
});

test("F1: local SEM nenhum sinal da Vercel + flag explicita -> PERMITIDO", () => {
  const d = evaluateAccess(LOCAL_OK);
  assert.equal(d.allowed, true);
  assert.equal(d.allowed === true && d.mode, "development");
});

test("F1: nenhuma negacao de deployment gera chamada upstream (no handler real)", async () => {
  const hosted: AccessEnv[] = [
    { ...LOCAL_OK, VERCEL_ENV: "preview" },
    { ...LOCAL_OK, VERCEL: "1" },
    { ...LOCAL_OK, VERCEL_TARGET_ENV: "staging-qa" },
    { ...LOCAL_OK, VERCEL_TARGET_ENV: "production" },
    { ...LOCAL_OK, NODE_ENV: "production" },
  ];

  for (const env of hosted) {
    const calls: string[] = [];
    const spy = (async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return new Response("{}", { headers: { "content-type": "application/json" } });
    }) as unknown as typeof fetch;

    const res = await handleMcpRequest(
      new Request("https://local.test/api/mcp", {
        method: "POST",
        headers: { "content-type": "application/json", "mcp-method": "server/discover" },
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "server/discover", params: {} }),
      }),
      env,
      { fetchImpl: spy },
    );

    assert.equal(res.status, 404, `${JSON.stringify(env)} deveria dar 404`);
    assert.equal(calls.length, 0, "negacao nao pode tocar o upstream");
  }
});

// ===========================================================================
// F2 — payload malformado NUNCA vira sucesso vazio
// ===========================================================================

test("F2 desempenho: `current` ausente -> erro, nao sucesso vazio", async () => {
  const c = await connect(withBroken("/api/v1/performance/overview", { refreshed_at: null }));
  await callErr(c, "torre_desempenho_marketplaces", { periodo: "mes_anterior" });
});

test("F2 desempenho: `brands` ausente -> erro", async () => {
  const c = await connect(withBroken("/api/v1/performance/brands", { refreshed_at: null }));
  await callErr(c, "torre_desempenho_marketplaces", { periodo: "mes_anterior" });
});

test("F2 desempenho: `brands` com tipo errado -> erro", async () => {
  const c = await connect(withBroken("/api/v1/performance/brands", { brands: "nao e array" }));
  await callErr(c, "torre_desempenho_marketplaces", { periodo: "mes_anterior" });
});

test("F2 desempenho: marca sem identificador -> erro", async () => {
  const c = await connect(
    withBroken("/api/v1/performance/brands", {
      brands: [{ brand: "", label: "SEM ID", total_gmv: 10, orders: 1 }],
    }),
  );
  await callErr(c, "torre_desempenho_marketplaces", { periodo: "mes_anterior" });
});

test("F2 desempenho: `trend.data` ausente quando solicitado -> erro", async () => {
  const c = await connect(withBroken("/api/v1/performance/trend", { granularity: "day" }));
  await callErr(c, "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
    granularidade: "day",
  });
});

test("F2 canais: `channel_rows` ausente -> erro", async () => {
  const c = await connect(
    withBroken("/api/v1/performance/canais", { channel_medians: [], refreshed_at: null }),
  );
  await callErr(c, "torre_comparar_canais_marcas", { periodo: "mes_anterior" });
});

test("F2 canais: `channel_medians` ausente -> erro", async () => {
  const c = await connect(
    withBroken("/api/v1/performance/canais", { channel_rows: [], refreshed_at: null }),
  );
  await callErr(c, "torre_comparar_canais_marcas", { periodo: "mes_anterior" });
});

test("F2 canais: linha sem os booleanos de aplicabilidade -> erro", async () => {
  // Sem eles, "N/A" e "sem dado" ficam indistinguiveis.
  const c = await connect(
    withBroken("/api/v1/performance/canais", {
      channel_rows: [
        { brand: "barbours", label: "B", channel: "ml", channel_label: "ML", gmv: 1, orders: 1 },
      ],
      channel_medians: [],
    }),
  );
  await callErr(c, "torre_comparar_canais_marcas", { periodo: "mes_anterior" });
});

test("F2 produtos: `items` ausente -> erro", async () => {
  const c = await connect(withBroken("/api/v1/performance/produtos/ml", { total: 5 }));
  await callErr(c, "torre_produtos_prioritarios", { canal: "ml" });
});

test("F2 produtos: `total` negativo ou nao inteiro -> erro", async () => {
  for (const total of [-1, 1.5, "10", null]) {
    const c = await connect(
      withBroken("/api/v1/performance/produtos/ml", { total, items: [] }),
    );
    await callErr(c, "torre_produtos_prioritarios", { canal: "ml" });
  }
});

test("F2 produtos: item sem identificador -> erro", async () => {
  const c = await connect(
    withBroken("/api/v1/performance/produtos/ml", {
      total: 1,
      items: [{ brand: "barbours", item_id: "", gross_revenue: 1, units_sold: 1 }],
    }),
  );
  await callErr(c, "torre_produtos_prioritarios", { canal: "ml" });
});

test("F2 qualidade: `kpis` ausente -> erro", async () => {
  const c = await connect(withBroken("/api/v1/performance/quality", { brands: [] }));
  await callErr(c, "torre_qualidade_dados", {});
});

test("F2 qualidade: health com tipo errado -> erro", async () => {
  const c = await connect(
    withBroken("/api/v1/performance/health-datasource", {
      active_source: "neon_marts",
      db_connected: "sim",
    }),
  );
  await callErr(c, "torre_qualidade_dados", {});
});

test("F2 regioes: `by-uf.data` ausente -> erro", async () => {
  const c = await connect(withBroken("/api/v1/regioes/by-uf", { refreshed_at: null }));
  await callErr(c, "torre_regioes_vendas", {});
});

test("F2 regioes: UF sem identificador -> erro", async () => {
  const c = await connect(
    withBroken("/api/v1/regioes/by-uf", { data: [{ uf: "", gmv: 1, orders: 1 }] }),
  );
  await callErr(c, "torre_regioes_vendas", {});
});

test("F2 regioes: `channels_sem_cobertura_regional` com tipo errado -> erro", async () => {
  const c = await connect(
    withBroken("/api/v1/regioes/summary", {
      ...REGIOES_SUMMARY,
      channels_sem_cobertura_regional: "tiktok",
    }),
  );
  await callErr(c, "torre_regioes_vendas", {});
});

test("F2: o erro de contrato NAO vaza detalhe do Zod nem do payload", async () => {
  const c = await connect(withBroken("/api/v1/performance/overview", { lixo: "x" }));
  const text = await callErr(c, "torre_desempenho_marketplaces", { periodo: "mes_anterior" });

  assert.ok(!text.includes("lixo"));
  assert.ok(!/invalid_type|ZodError|expected|received/i.test(text), "sem detalhe do Zod");
  assert.match(text, /formato inesperado/i);
});

test("F2: array VERDADEIRAMENTE vazio continua sucesso vazio", async () => {
  const c = await connect({
    ...ROUTES,
    "/api/v1/regioes/by-uf": { ...REGIOES_BY_UF, data: [] },
  });
  const env = await callOk(c, "torre_regioes_vendas", {});
  assert.deepEqual(env.data.by_uf, []);
  assert.equal(env.meta.returned_count, 0);
});

test("F2: nenhum payload invalido produz sucesso com returned_count=0", async () => {
  const broken: Array<[string, string, unknown, Record<string, unknown>]> = [
    ["torre_desempenho_marketplaces", "/api/v1/performance/overview", {}, { periodo: "mes_anterior" }],
    ["torre_comparar_canais_marcas", "/api/v1/performance/canais", {}, { periodo: "mes_anterior" }],
    ["torre_produtos_prioritarios", "/api/v1/performance/produtos/ml", {}, { canal: "ml" }],
    ["torre_qualidade_dados", "/api/v1/performance/quality", {}, {}],
    ["torre_regioes_vendas", "/api/v1/regioes/by-uf", {}, {}],
  ];

  for (const [tool, path, payload, args] of broken) {
    const c = await connect(withBroken(path, payload));
    const res = await c.callTool({ name: tool, arguments: args });
    assert.equal(res.isError, true, `${tool} deveria falhar`);
    assert.equal(res.structuredContent, undefined, `${tool} nao pode devolver envelope`);
  }
});

// ===========================================================================
// F3 — competencia efetiva de Produtos
// ===========================================================================

test("F3 TikTok sem `mes`: envelope ecoa a competencia REAL da fonte", async () => {
  const c = await connect();
  const env = await callOk(c, "torre_produtos_prioritarios", { canal: "tiktok" });

  assert.equal(env.data.ref_month, "2026-07");
  assert.equal(env.data.temporal_scope, "mensal");
  assert.deepEqual(env.meta.period, { start: "2026-07-01", end: "2026-07-31", inclusive: true });
  assert.equal(env.meta.filters_applied.ref_month, "2026-07");
});

test("F3 Shopee: fevereiro de ano bissexto termina em 29", async () => {
  const c = await connect();
  const env = await callOk(c, "torre_produtos_prioritarios", { canal: "shopee" });

  assert.equal(env.data.ref_month, "2024-02");
  assert.deepEqual(env.meta.period, { start: "2024-02-01", end: "2024-02-29", inclusive: true });
});

test("F3: mes explicito IGUAL ao da fonte -> sucesso", async () => {
  const c = await connect();
  const env = await callOk(c, "torre_produtos_prioritarios", {
    canal: "tiktok",
    mes: "2026-07",
  });
  assert.equal(env.data.ref_month, "2026-07");
});

test("F3: mes explicito DIFERENTE do devolvido -> erro (nao apresenta resultado)", async () => {
  const c = await connect();
  const text = await callErr(c, "torre_produtos_prioritarios", {
    canal: "tiktok",
    mes: "2026-06",
  });
  assert.match(text, /formato inesperado/i);
});

test("F3: `ref_month` ausente ou malformado no TikTok/Shopee -> erro", async () => {
  for (const bad of [undefined, "2026-13", "julho", "2026-7", ""]) {
    const c = await connect(
      withBroken("/api/v1/performance/produtos/tiktok", {
        ...PRODUTOS_TIKTOK,
        ref_month: bad,
      }),
    );
    await callErr(c, "torre_produtos_prioritarios", { canal: "tiktok" });
  }
});

test("F3: ML permanece cumulativo, sem periodo e sem ref_month inventado", async () => {
  const c = await connect();
  const env = await callOk(c, "torre_produtos_prioritarios", { canal: "ml" });

  assert.equal(env.meta.period, null);
  assert.equal(env.data.temporal_scope, "cumulativo");
  assert.equal(env.data.ref_month, null);
  assert.equal(env.meta.filters_applied.ref_month, null);
  assert.ok(env.data.limitations.some((l: any) => l.topic === "produtos_ml_cumulativo"));
});

test("F3: monthBoundsOf resolve fevereiro comum e bissexto", () => {
  assert.deepEqual(monthBoundsOf("2024-02"), { start: "2024-02-01", end: "2024-02-29" });
  assert.deepEqual(monthBoundsOf("2026-02"), { start: "2026-02-01", end: "2026-02-28" });
  assert.deepEqual(monthBoundsOf("2026-12"), { start: "2026-12-01", end: "2026-12-31" });
  assert.deepEqual(monthBoundsOf("2026-01"), { start: "2026-01-01", end: "2026-01-31" });
});

// ===========================================================================
// F4 — timeout cobrindo o corpo inteiro
// ===========================================================================

test("F4: corpo pendurado APOS os headers termina como SOURCE_TIMEOUT", async () => {
  const client = new TorreClient({
    baseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: headersThenHangFetch(),
    timeoutMs: 60,
  });

  const started = Date.now();
  await assert.rejects(
    () => client.get("overview", { channels: "all" }),
    (e: unknown) => e instanceof OracleToolError && e.category === "SOURCE_TIMEOUT",
  );
  const elapsed = Date.now() - started;

  // Nao espera o default de 8s: o deadline cobre a leitura do corpo.
  assert.ok(elapsed < 2000, `demorou ${elapsed}ms — o deadline nao cobriu o corpo`);
});

test("F4: teto de bytes vale para conteudo multibyte (streaming)", async () => {
  // 200 caracteres "€" = 600 bytes UTF-8, acima do teto de 300.
  const client = new TorreClient({
    baseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: multibyteStreamFetch(200),
    maxBytes: 300,
    timeoutMs: 1000,
  });

  await assert.rejects(
    () => client.get("overview"),
    (e: unknown) => e instanceof OracleToolError && e.category === "INVALID_UPSTREAM_RESPONSE",
  );
});

test("F4: conteudo multibyte DENTRO do teto passa", async () => {
  const client = new TorreClient({
    baseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: multibyteStreamFetch(10), // 30 bytes + aspas
    maxBytes: 300,
    timeoutMs: 1000,
  });
  const parsed = await client.get("overview");
  assert.equal(typeof parsed, "string");
});

test("F4: fallback sem stream tambem mede BYTES, nao caracteres", async () => {
  // 150 caracteres = 150 chars, mas 450 bytes em UTF-8.
  const payload = '"' + "€".repeat(150) + '"';
  const client = new TorreClient({
    baseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: noStreamFetch(payload),
    maxBytes: 300,
    timeoutMs: 1000,
  });

  await assert.rejects(
    () => client.get("overview"),
    (e: unknown) => e instanceof OracleToolError && e.category === "INVALID_UPSTREAM_RESPONSE",
  );
});

test("F4: timeout na fase de headers continua SOURCE_TIMEOUT", async () => {
  const hangBeforeHeaders = ((_input: RequestInfo | URL, init?: RequestInit) =>
    new Promise((_resolve, reject) => {
      const signal = init?.signal;
      const fail = () => {
        const e = new Error("aborted");
        e.name = "AbortError";
        reject(e);
      };
      if (signal?.aborted) return fail();
      signal?.addEventListener("abort", fail, { once: true });
    })) as unknown as typeof fetch;

  const client = new TorreClient({
    baseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: hangBeforeHeaders,
    timeoutMs: 40,
  });

  await assert.rejects(
    () => client.get("overview"),
    (e: unknown) => e instanceof OracleToolError && e.category === "SOURCE_TIMEOUT",
  );
});

// ===========================================================================
// F6 — janelas compostas da tool de qualidade
// ===========================================================================

test("F6: qualidade declara DUAS janelas distintas e meta.period nulo", async () => {
  const c = await connect();
  const env = await callOk(c, "torre_qualidade_dados", {});

  // Nao existe janela unica nesta resposta.
  assert.equal(env.meta.period, null);

  // Frescor olha o mes corrente (relogio fixo em 2026-08-18).
  assert.deepEqual(env.data.freshness.checked_period, {
    start: "2026-08-01",
    end: "2026-08-18",
    inclusive: true,
  });

  // Indicadores olham o mes fechado anterior.
  assert.deepEqual(env.data.quality_indicators_checked_period, {
    start: "2026-07-01",
    end: "2026-07-31",
    inclusive: true,
  });

  // As duas janelas sao diferentes — nenhuma e' apresentada como a outra.
  assert.notDeepEqual(
    env.data.freshness.checked_period,
    env.data.quality_indicators_checked_period,
  );
});

test("F6: filters_applied nomeia as duas janelas separadamente", async () => {
  const c = await connect();
  const env = await callOk(c, "torre_qualidade_dados", {});

  assert.deepEqual(env.meta.filters_applied.freshness_period, {
    start: "2026-08-01",
    end: "2026-08-18",
    inclusive: true,
  });
  assert.deepEqual(env.meta.filters_applied.quality_indicators_period, {
    start: "2026-07-01",
    end: "2026-07-31",
    inclusive: true,
  });
});

test("F6: refreshed_at nao e' confundido com a competencia dos indicadores", async () => {
  const c = await connect();
  const env = await callOk(c, "torre_qualidade_dados", {});

  // O carimbo da carga e' de agosto; a competencia dos indicadores e' julho.
  assert.equal(env.data.freshness.refreshed_at, env.meta.refreshed_at);
  assert.notEqual(env.data.quality_indicators_checked_period.start, env.meta.refreshed_at);
});

test("F6: virada de ano — janeiro produz frescor de jan e indicadores de dez/ano-1", async () => {
  const server = buildOracleServer({
    backendBaseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: jsonFetch(ROUTES),
    now: () => new Date("2026-01-15T12:00:00.000Z"),
    rateLimiter: new SlidingWindowRateLimiter({ limit: 100, windowMs: 60_000 }),
  });
  const [ct, st] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "vy", version: "0.0.0" });
  await Promise.all([client.connect(ct), server.connect(st)]);

  const env = await callOk(client, "torre_qualidade_dados", {});
  assert.deepEqual(env.data.freshness.checked_period, {
    start: "2026-01-01",
    end: "2026-01-15",
    inclusive: true,
  });
  assert.deepEqual(env.data.quality_indicators_checked_period, {
    start: "2025-12-01",
    end: "2025-12-31",
    inclusive: true,
  });
});

test("F6: garantias antigas preservadas (dia parcial, TikTok, null != zero)", async () => {
  const c = await connect();
  const env = await callOk(c, "torre_qualidade_dados", {});

  assert.equal(env.data.freshness.current_day_partial, true);

  const tk = env.data.quality_indicators.find((i: any) => i.channel === "tiktok");
  assert.equal(tk.measured, false);
  assert.equal(tk.value_pct, null);

  const ml = env.data.quality_indicators.find(
    (i: any) => i.channel === "ml" && i.metric === "taxa_cancelamento",
  );
  assert.equal(ml.value_pct, 1.5);
  assert.equal(env.data.technical_health.active_source, "neon_marts");
});

// ===========================================================================
// Endurecimento de inputs — duplicatas sao rejeitadas
// ===========================================================================

test("duplicata em `canais` e' rejeitada, sem chamada upstream", async () => {
  const log: CallLog = [];
  const c = await connect(ROUTES, log);
  await callErr(c, "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
    canais: ["ml", "ml"],
  });
  assert.equal(log.length, 0);
});

test("duplicata em `marcas` e' rejeitada, sem chamada upstream", async () => {
  const log: CallLog = [];
  const c = await connect(ROUTES, log);
  await callErr(c, "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
    marcas: ["barbours", "barbours"],
  });
  assert.equal(log.length, 0);
});

test("duplicata em `ufs` e' rejeitada, sem chamada upstream", async () => {
  const log: CallLog = [];
  const c = await connect(ROUTES, log);
  await callErr(c, "torre_regioes_vendas", { ufs: ["SP", "SP"] });
  assert.equal(log.length, 0);
});

test("duplicata tambem e' rejeitada em canais/marcas de canais e regioes", async () => {
  const log: CallLog = [];
  const c = await connect(ROUTES, log);
  await callErr(c, "torre_comparar_canais_marcas", { canais: ["tiktok", "tiktok"] });
  await callErr(c, "torre_regioes_vendas", { marcas: ["kokeshi", "kokeshi"] });
  assert.equal(log.length, 0);
});

test("valores distintos continuam aceitos e nao duplicam linhas", async () => {
  const c = await connect();
  const env = await callOk(c, "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
    canais: ["ml", "tiktok"],
  });

  const channels = env.data.by_channel.map((r: any) => r.channel);
  assert.deepEqual(channels, ["ml", "tiktok"]);
  assert.equal(new Set(channels).size, channels.length, "nenhuma linha duplicada");

  const brands = env.data.by_brand.map((r: any) => r.brand);
  assert.equal(new Set(brands).size, brands.length, "nenhuma marca duplicada");
});

// ---------------------------------------------------------------------------
// Frestas fechadas na revisao adversarial
// ---------------------------------------------------------------------------

test("revisao: typo com valor `undefined` tambem falha (erro nao fica latente)", async () => {
  const log: CallLog = [];
  const c = new TorreClient({
    baseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: jsonFetch({ "/api/v1/performance/overview": OVERVIEW }, log),
  });

  await assert.rejects(
    () => c.get("overview", { channels: "all", brandss: undefined } as never),
    (e: unknown) => e instanceof OracleToolError && e.category === "MISSING_CONFIGURATION",
  );
  assert.equal(log.length, 0);
});

test("revisao: `filters` em forma de array e' contrato quebrado -> erro", async () => {
  // O contrato Zod (`z.record`) ja recusa array, entao isso nem chega ao
  // projetor: um eco de filtros em forma de lista nao descreve filtros.
  const c = await connect(
    withBroken("/api/v1/performance/overview", { ...OVERVIEW, filters: ["ml"] }),
  );
  const text = await callErr(c, "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
    canais: ["ml"],
  });
  assert.match(text, /formato inesperado/i);
});

test("revisao: filters_applied nunca e' array (defesa em profundidade)", async () => {
  // `echoFilters` tambem guarda contra array, para o caso de algum contrato
  // futuro ser mais permissivo que o atual.
  const c = await connect();
  for (const [tool, args] of [
    ["torre_desempenho_marketplaces", { periodo: "mes_anterior" }],
    ["torre_comparar_canais_marcas", { periodo: "mes_anterior" }],
    ["torre_regioes_vendas", {}],
  ] as Array<[string, Record<string, unknown>]>) {
    const env = await callOk(c, tool, args);
    assert.ok(!Array.isArray(env.meta.filters_applied), `${tool}: filtros nao podem ser array`);
    assert.equal(typeof env.meta.filters_applied, "object");
  }
});

test("F4: nenhum timer/recurso fica pendurado depois do timeout", async () => {
  const before = process.getActiveResourcesInfo().filter((r) => r === "Timeout").length;

  const client = new TorreClient({
    baseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: headersThenHangFetch(),
    timeoutMs: 40,
  });
  await assert.rejects(() => client.get("overview"));

  // Cede um tick para o `finally` do adapter rodar por completo.
  await new Promise((r) => setImmediate(r));

  const after = process.getActiveResourcesInfo().filter((r) => r === "Timeout").length;
  assert.ok(after <= before, `timers vazaram: antes=${before} depois=${after}`);
});

// ===========================================================================
// Correcao terminal — F2: cleanup do reader nunca prolonga o deadline
// ===========================================================================

test("terminal-F2: `cancel()` que NUNCA resolve nao segura a tool", async () => {
  const { impl, cancelCalls } = hangingCancelFetch();
  const client = new TorreClient({
    baseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: impl,
    timeoutMs: 50,
  });

  const started = Date.now();
  await assert.rejects(
    () => client.get("overview", { channels: "all" }),
    (e: unknown) => e instanceof OracleToolError && e.category === "SOURCE_TIMEOUT",
  );
  const elapsed = Date.now() - started;

  // Limite curto e determinIstico: o cancelamento e' disparado, nao aguardado.
  assert.ok(elapsed < 1500, `demorou ${elapsed}ms — o cleanup prolongou o deadline`);
  assert.equal(cancelCalls(), 1, "o cancelamento deve ser iniciado (best-effort)");
});

test("terminal-F2: caso normal continua liberando o reader sem erro solto", async () => {
  const { impl, cancelCalls } = countingCancelFetch({ current: { gmv: 1, orders: 1 } });
  const client = new TorreClient({
    baseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: impl,
    timeoutMs: 1000,
  });

  let unhandled: unknown = null;
  const onUnhandled = (err: unknown) => {
    unhandled = err;
  };
  process.on("unhandledRejection", onUnhandled);
  try {
    const parsed = (await client.get("overview")) as { current: { gmv: number } };
    assert.equal(parsed.current.gmv, 1);
    assert.equal(cancelCalls(), 1, "reader liberado tambem no caminho feliz");
    // Deixa o microtask queue drenar para capturar rejeicao solta, se houver.
    await new Promise((r) => setImmediate(r));
    assert.equal(unhandled, null, "nenhuma rejeicao nao tratada no cleanup");
  } finally {
    process.off("unhandledRejection", onUnhandled);
  }
});

test("terminal-F2: nenhum timer fica pendurado apos o cancel hostil", async () => {
  const before = process.getActiveResourcesInfo().filter((r) => r === "Timeout").length;

  const { impl } = hangingCancelFetch();
  const client = new TorreClient({
    baseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: impl,
    timeoutMs: 40,
  });
  await assert.rejects(() => client.get("overview"));
  await new Promise((r) => setImmediate(r));

  const after = process.getActiveResourcesInfo().filter((r) => r === "Timeout").length;
  assert.ok(after <= before, `timers vazaram: antes=${before} depois=${after}`);
});
