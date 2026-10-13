// MCP-COVERAGE-1 — contratos das cinco tools contra o payload medido em
// 2026-10-13. Cada teste trava uma afirmacao que estava congelada e passou a
// contradizer a API, ou um zero que a fonte fabrica a partir de ausencia.
import { test } from "node:test";
import assert from "node:assert/strict";

import { Client } from "@modelcontextprotocol/client";
import { InMemoryTransport } from "@modelcontextprotocol/server";

import { buildOracleServer } from "../src/server/oracle/server.ts";
import { SlidingWindowRateLimiter } from "../src/server/oracle/rate-limit.ts";
import {
  BRANDS, CANAIS, FIXED_NOW, HEALTH, jsonFetch, OVERVIEW, PRODUTOS_ML,
  QUALITY, REGIOES_BY_UF, REGIOES_SUMMARY, TREND,
} from "./oracle-fixtures.ts";

const ROUTES = {
  "/api/v1/performance/overview": OVERVIEW,
  "/api/v1/performance/brands": BRANDS,
  "/api/v1/performance/trend": TREND,
  "/api/v1/performance/canais": CANAIS,
  "/api/v1/performance/quality": QUALITY,
  "/api/v1/performance/produtos/ml": PRODUTOS_ML,
  "/api/v1/performance/health-datasource": HEALTH,
  "/api/v1/regioes/summary": REGIOES_SUMMARY,
  "/api/v1/regioes/by-uf": REGIOES_BY_UF,
};

async function connect(overrides: Record<string, unknown> = {}) {
  const server = buildOracleServer({
    backendBaseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: jsonFetch({ ...ROUTES, ...overrides }),
    now: () => FIXED_NOW,
    rateLimiter: new SlidingWindowRateLimiter({ limit: 1000, windowMs: 60_000 }),
  });
  const [ct, st] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "coverage", version: "0.0.0" });
  await Promise.all([client.connect(ct), server.connect(st)]);
  return client;
}

async function call(client: Client, name: string, args: Record<string, unknown> = {}) {
  const res = await client.callTool({ name, arguments: args });
  assert.notEqual(res.isError, true, `${name} deveria ter sucesso`);
  return res.structuredContent as { meta: Record<string, any>; data: any };
}

const mlRow = (brand: string, available: boolean, pct: number | null) => ({
  ...CANAIS.channel_rows[0],
  brand,
  label: brand.toUpperCase(),
  marketplace_cost_available: available,
  marketplace_cost_pct: pct,
  data_warning: available ? null : "Comissao do Mercado Livre ainda nao carregada para este periodo.",
});

// ---------------------------------------------------------------------------
// Afirmacoes congeladas nas descricoes
// ---------------------------------------------------------------------------

test("tools/list continua com exatamente as cinco tools", async () => {
  const { tools } = await (await connect()).listTools();
  assert.deepEqual(tools.map((t) => t.name).sort(), [
    "torre_comparar_canais_marcas",
    "torre_desempenho_marketplaces",
    "torre_produtos_prioritarios",
    "torre_qualidade_dados",
    "torre_regioes_vendas",
  ]);
});

test("nenhuma descricao afirma que a comissao ML nao existe ou que o TikTok vem como 0", async () => {
  const { tools } = await (await connect()).listTools();
  for (const t of tools) {
    const d = t.description ?? "";
    assert.doesNotMatch(d, /comissao do Mercado Livre NAO existe/i, t.name);
    assert.doesNotMatch(d, /vem como 0/i, t.name);
  }
  const canais = tools.find((t) => t.name === "torre_comparar_canais_marcas")!;
  assert.match(canais.description ?? "", /POR LINHA/);
});

// ---------------------------------------------------------------------------
// Comissao ML: disponibilidade por marca, sem esconder nenhuma das duas
// ---------------------------------------------------------------------------

test("comissao ML mista: limitacao nomeia so as marcas sem carga e o valor carregado e' preservado", async () => {
  const canais = {
    ...CANAIS,
    channel_rows: [mlRow("barbours", false, null), mlRow("kokeshi", true, 16.7), CANAIS.channel_rows[1]],
  };
  const env = await call(await connect({ "/api/v1/performance/canais": canais }),
    "torre_comparar_canais_marcas", { periodo: "mes_anterior" });

  const byBrand = Object.fromEntries(
    env.data.rows.filter((r: any) => r.channel === "ml").map((r: any) => [r.brand, r]),
  );
  assert.equal(byBrand.barbours.marketplace_cost_pct, null);
  assert.equal(byBrand.barbours.marketplace_cost_available, false);
  assert.equal(byBrand.kokeshi.marketplace_cost_pct, 16.7);
  assert.equal(byBrand.kokeshi.marketplace_cost_available, true);

  const missing = env.data.limitations.find((l: any) => l.topic === "comissao_ml");
  assert.ok(missing, "marca sem carga precisa de limitacao");
  assert.match(missing.scope, /barbours/);
  assert.doesNotMatch(missing.scope, /kokeshi/, "marca coberta nao pode ser dada como ausente");
  assert.doesNotMatch(missing.description, /nao esta disponivel no mart/i);
  assert.ok(env.data.limitations.some((l: any) => l.topic === "cobertura_comissao_ml"));
});

test("comissao ML carregada em todas as marcas: sem limitacao de ausencia, com aviso de cobertura", async () => {
  const canais = { ...CANAIS, channel_rows: [mlRow("barbours", true, 15), mlRow("kokeshi", true, 16.7)] };
  const env = await call(await connect({ "/api/v1/performance/canais": canais }),
    "torre_comparar_canais_marcas", { periodo: "mes_anterior" });
  const topics = env.data.limitations.map((l: any) => l.topic);
  assert.ok(!topics.includes("comissao_ml"));
  assert.ok(topics.includes("cobertura_comissao_ml"));
});

test("comissao ML ausente em todas as marcas: sem aviso de cobertura parcial", async () => {
  const env = await call(await connect(), "torre_comparar_canais_marcas", { periodo: "mes_anterior" });
  const topics = env.data.limitations.map((l: any) => l.topic);
  assert.ok(topics.includes("comissao_ml"));
  assert.ok(!topics.includes("cobertura_comissao_ml"));
});

// ---------------------------------------------------------------------------
// Cancelamento TikTok chega nulo hoje — continua NAO MENSURADO, nunca 0%
// ---------------------------------------------------------------------------

test("cancelamento TikTok nulo na fonte: measured=false e value_pct nulo", async () => {
  const quality = { ...QUALITY, kpis: { ...QUALITY.kpis, tiktok_cancel_rate: null } };
  const env = await call(await connect({ "/api/v1/performance/quality": quality }), "torre_qualidade_dados");
  const tk = env.data.quality_indicators.find((i: any) => i.channel === "tiktok");
  assert.equal(tk.measured, false);
  assert.equal(tk.value_pct, null);
  assert.notEqual(tk.value_pct, 0);
  assert.match(tk.note, /NAO MENSURADO/);
});

// ---------------------------------------------------------------------------
// Regioes: unidades, escopo vazio e defasagem
// ---------------------------------------------------------------------------

test("regioes: units_sold sai nulo mesmo quando a fonte manda numero (zero e ausencia indistinguiveis)", async () => {
  const env = await call(await connect(), "torre_regioes_vendas", { periodo: "mes_anterior" });
  assert.equal(env.data.summary.units_sold, null);
  for (const r of env.data.by_uf) assert.equal(r.units_sold, null);
  assert.ok(env.data.limitations.some((l: any) => l.topic === "unidades_regioes"));
  // GMV e pedidos com carga continuam intactos.
  assert.equal(env.data.summary.gmv, 700);
  assert.equal(env.data.summary.orders, 30);
});

test("regioes: escopo sem nenhuma linha (refreshed_at nulo) nao vira venda zero", async () => {
  const vazio = {
    ...REGIOES_SUMMARY,
    gmv: 0, orders: 0, units_sold: 0, ufs_com_venda: 0, uf_known_orders: 0, uf_eligible_orders: 0,
    uf_fill_pct: null, coverage_level: "not_applicable", coverage_warning: false,
    refreshed_at: null, channels_sem_cobertura_regional: [],
  };
  const env = await call(
    await connect({
      "/api/v1/regioes/summary": vazio,
      "/api/v1/regioes/by-uf": { ...REGIOES_BY_UF, data: [], refreshed_at: null, channels_sem_cobertura_regional: [] },
    }),
    "torre_regioes_vendas",
    { periodo: "mes_anterior", canais: ["shopee"] },
  );
  assert.equal(env.data.summary.gmv, null);
  assert.equal(env.data.summary.orders, null);
  assert.equal(env.data.summary.ufs_with_sales, null);
  assert.equal(env.meta.refreshed_at, null);
  assert.ok(env.meta.warnings.some((w: string) => /ausencia de carga/.test(w)));
});

test("regioes: carga anterior ao fim do periodo gera aviso de total incompleto", async () => {
  const stale = "2026-07-20T10:00:00+00:00";
  const env = await call(
    await connect({ "/api/v1/regioes/summary": { ...REGIOES_SUMMARY, refreshed_at: stale } }),
    "torre_regioes_vendas",
    { periodo: "mes_anterior" },
  );
  const w = env.meta.warnings.find((x: string) => /INCOMPLETOS/.test(x));
  assert.ok(w, "defasagem precisa ser dita");
  assert.match(w, /2026-07-20/);
  assert.match(w, /2026-07-31/);
  // O numero continua o da fonte: aviso, nao ajuste.
  assert.equal(env.data.summary.gmv, 700);
});

test("regioes: carga posterior ao fim do periodo nao gera aviso de defasagem", async () => {
  const env = await call(await connect(), "torre_regioes_vendas", { periodo: "mes_anterior" });
  assert.ok(!env.meta.warnings.some((w: string) => /INCOMPLETOS|ausencia de carga/.test(w)));
});
