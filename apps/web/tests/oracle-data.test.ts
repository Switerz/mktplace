// Grupo de DADOS (D1-D10) + as reconciliacoes do plano.
// Foco: null nunca vira zero, zero nunca vira null, total so quando verdadeiro,
// nenhuma PII, e nenhuma "correcao" artificial de numero.
import { test } from "node:test";
import assert from "node:assert/strict";

import { Client } from "@modelcontextprotocol/client";
import { InMemoryTransport } from "@modelcontextprotocol/server";

import { buildOracleServer } from "../src/server/oracle/server.ts";
import { SlidingWindowRateLimiter } from "../src/server/oracle/rate-limit.ts";
import {
  BRANDS, CANAIS, FIXED_NOW, HEALTH, jsonFetch, OVERVIEW, PRODUTOS_ML,
  QUALITY, REFRESHED_AT, REGIOES_BY_UF, REGIOES_SUMMARY, TREND, type CallLog,
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

async function connect(routes: Record<string, unknown> = ROUTES, log?: CallLog) {
  const server = buildOracleServer({
    backendBaseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: jsonFetch(routes, log),
    now: () => FIXED_NOW,
    rateLimiter: new SlidingWindowRateLimiter({ limit: 1000, windowMs: 60_000 }),
  });
  const [ct, st] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "data", version: "0.0.0" });
  await Promise.all([client.connect(ct), server.connect(st)]);
  return client;
}

type Env = { meta: Record<string, any>; data: any };

async function call(client: Client, name: string, args: Record<string, unknown> = {}) {
  const res = await client.callTool({ name, arguments: args });
  assert.notEqual(res.isError, true, `${name} deveria ter sucesso`);
  return res.structuredContent as Env;
}

// ---------------------------------------------------------------------------
// D3/D4 — null preservado, zero preservado
// ---------------------------------------------------------------------------

test("D3: canal sem dado permanece null e NUNCA vira zero", async () => {
  const env = await call(await connect(), "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
  });

  const shopee = env.data.by_channel.find((c: any) => c.channel === "shopee");
  assert.equal(shopee.gmv, null, "shopee sem dado deve continuar null");
  assert.notEqual(shopee.gmv, 0);
  // Share tambem nao pode ser fabricado a partir de null.
  assert.equal(shopee.share_pct, null);
});

test("D4: GMV zero REAL permanece zero e nao vira null", async () => {
  const env = await call(await connect(), "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
  });

  const kokeshi = env.data.by_brand.find((b: any) => b.brand === "kokeshi");
  assert.equal(kokeshi.gmv, 0, "zero medido deve permanecer 0");
  assert.notEqual(kokeshi.gmv, null);
  assert.equal(kokeshi.orders, 0);
});

test("comissao do ML ausente permanece null, nunca zero", async () => {
  const env = await call(await connect(), "torre_comparar_canais_marcas", {
    periodo: "mes_anterior",
  });

  const ml = env.data.rows.find((r: any) => r.channel === "ml");
  assert.equal(ml.marketplace_cost_pct, null);
  assert.notEqual(ml.marketplace_cost_pct, 0);
  // "aplicavel mas indisponivel" precisa ser distinguivel de "nao aplicavel".
  assert.equal(ml.marketplace_cost_applicable, true);
  assert.equal(ml.marketplace_cost_available, false);
});

test("N/A (nao aplicavel) e' distinto de sem dado e de zero", async () => {
  const env = await call(await connect(), "torre_comparar_canais_marcas", {
    periodo: "mes_anterior",
  });

  const tiktok = env.data.rows.find((r: any) => r.channel === "tiktok");
  // TikTok nao opera ads neste contrato: nao aplicavel.
  assert.equal(tiktok.ads_applicable, false);
  assert.equal(tiktok.ads_available, false);
  assert.equal(tiktok.roas, null);
  assert.notEqual(tiktok.roas, 0);
});

test("mediana inexistente (uma marca so) permanece null — sem comparacao fabricada", async () => {
  const env = await call(await connect(), "torre_comparar_canais_marcas", {
    periodo: "mes_anterior",
  });

  const ml = env.data.channel_medians.find((m: any) => m.channel === "ml");
  assert.equal(ml.roas_median, null);
  assert.equal(ml.gmv_median, null);
  assert.equal(ml.brands_with_data, 1);
});

// ---------------------------------------------------------------------------
// D10 — total_count so quando verdadeiro
// ---------------------------------------------------------------------------

test("D10: produtos expoem total verdadeiro e marcam truncamento", async () => {
  const env = await call(await connect(), "torre_produtos_prioritarios", {
    canal: "ml",
    limite: 2,
  });

  assert.equal(env.meta.total_count, 1648, "total verdadeiro vem do backend");
  assert.equal(env.meta.returned_count, 2);
  assert.equal(env.meta.truncated, true);
  assert.match(env.meta.coverage, /top-2/);
});

test("D10: tools sem total verdadeiro NAO inventam total_count", async () => {
  const client = await connect();
  for (const name of [
    "torre_desempenho_marketplaces",
    "torre_comparar_canais_marcas",
    "torre_regioes_vendas",
  ]) {
    const env = await call(client, name, {});
    assert.equal(env.meta.total_count, null, `${name} nao pode fabricar total`);
    assert.equal(env.meta.truncated, false);
  }
});

// ---------------------------------------------------------------------------
// Proibicoes de conteudo
// ---------------------------------------------------------------------------

test("estimated_margin NUNCA aparece na saida", async () => {
  const env = await call(await connect(), "torre_produtos_prioritarios", {
    canal: "ml",
    limite: 2,
  });
  const serialized = JSON.stringify(env);
  assert.ok(!serialized.includes("estimated_margin"));
  assert.ok(!serialized.includes("0.42"), "valor da margem estimada nao pode vazar");
});

test("nenhuma resposta contem PII (creator, e-mail, pedido, cliente, CPF)", async () => {
  const client = await connect();
  const names = [
    "torre_desempenho_marketplaces",
    "torre_comparar_canais_marcas",
    "torre_qualidade_dados",
    "torre_regioes_vendas",
  ];
  for (const name of names) {
    const env = await call(client, name, {});
    const s = JSON.stringify(env).toLowerCase();
    for (const forbidden of ["creator", "buyer_name", "customer_name", "cpf", "email", "order_id", "@"]) {
      assert.ok(!s.includes(forbidden), `${name} nao pode conter "${forbidden}"`);
    }
  }
});

// ---------------------------------------------------------------------------
// Qualidade — 0 nao mensurado nunca vira 0%
// ---------------------------------------------------------------------------

test("cancelamento do TikTok = 0 e' reportado como NAO MENSURADO", async () => {
  const env = await call(await connect(), "torre_qualidade_dados", {});

  const tk = env.data.quality_indicators.find(
    (i: any) => i.channel === "tiktok" && i.metric === "taxa_cancelamento",
  );
  assert.equal(tk.measured, false, "0 do TikTok significa ausencia de medicao");
  assert.equal(tk.value_pct, null, "nao pode ser apresentado como 0%");
  assert.match(tk.note, /NAO MENSURADO/i);

  // E a limitacao precisa estar declarada explicitamente.
  const topics = env.data.limitations.map((l: any) => l.topic);
  assert.ok(topics.includes("cancelamento_tiktok"));
});

test("canais com medicao real preservam o valor, inclusive decimais", async () => {
  const env = await call(await connect(), "torre_qualidade_dados", {});
  const ml = env.data.quality_indicators.find(
    (i: any) => i.channel === "ml" && i.metric === "taxa_cancelamento",
  );
  assert.equal(ml.measured, true);
  assert.equal(ml.value_pct, 1.5);
});

test("saude tecnica e frescor vem da fonte, sem timestamp inventado", async () => {
  const env = await call(await connect(), "torre_qualidade_dados", {});
  assert.equal(env.data.technical_health.active_source, "neon_marts");
  assert.equal(env.data.technical_health.database_connected, true);
  assert.equal(env.data.freshness.refreshed_at, REFRESHED_AT);
  assert.equal(env.meta.refreshed_at, REFRESHED_AT);
});

test("refreshed_at ausente na fonte permanece null (nunca fabricado)", async () => {
  const semRefresh = { ...ROUTES, "/api/v1/performance/produtos/ml": { ...PRODUTOS_ML, refreshed_at: undefined } };
  const env = await call(await connect(semRefresh), "torre_produtos_prioritarios", {
    canal: "ml",
    limite: 2,
  });
  assert.equal(env.meta.refreshed_at, null);
});

// ---------------------------------------------------------------------------
// Regioes — cobertura declarada, sem reconciliacao artificial
// ---------------------------------------------------------------------------

test("R7: regioes sempre avisa cobertura parcial e NAO reconcilia com a gerencial", async () => {
  const client = await connect();
  const regioes = await call(client, "torre_regioes_vendas", { periodo: "mes_anterior" });
  const gerencial = await call(client, "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
  });

  // Aviso obrigatorio, mesmo sem o usuario pedir.
  assert.ok(
    regioes.meta.warnings.some((w: string) => /cobertura regional/i.test(w)),
    "aviso de cobertura e' obrigatorio",
  );
  assert.ok(regioes.data.limitations.some((l: any) => l.topic === "cobertura_regional"));

  // Os numeros DIVERGEM de proposito e nenhum ajuste e' aplicado.
  assert.equal(regioes.data.summary.gmv, 700);
  assert.equal(gerencial.data.total.gmv, 1000);
  assert.notEqual(regioes.data.summary.gmv, gerencial.data.total.gmv);
});

test("canais sem cobertura regional vem da FONTE, nao de lista fixa", async () => {
  const env = await call(await connect(), "torre_regioes_vendas", {});
  assert.deepEqual(env.data.channels_without_regional_coverage, ["tiktok"]);

  // Provar que e' lido: mudando a fonte, a saida muda.
  const alt = {
    ...ROUTES,
    "/api/v1/regioes/summary": {
      ...REGIOES_SUMMARY,
      channels_sem_cobertura_regional: ["tiktok", "shopee"],
    },
  };
  const env2 = await call(await connect(alt), "torre_regioes_vendas", {});
  assert.deepEqual(env2.data.channels_without_regional_coverage, ["tiktok", "shopee"]);
});

test("UFs sao ordenadas por GMV e respeitam o limite", async () => {
  const env = await call(await connect(), "torre_regioes_vendas", { limite: 1 });
  assert.equal(env.data.by_uf.length, 1);
  assert.equal(env.data.by_uf[0].uf, "SP", "maior GMV primeiro");
  assert.equal(env.meta.returned_count, 1);
});

// ---------------------------------------------------------------------------
// Reconciliacoes internas
// ---------------------------------------------------------------------------

test("R3: soma dos canais bate com o total do periodo", async () => {
  const env = await call(await connect(), "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
  });
  const soma = env.data.by_channel.reduce((acc: number, c: any) => acc + (c.gmv ?? 0), 0);
  assert.equal(soma, env.data.total.gmv);
});

test("R4: soma da serie bate com o total do periodo", async () => {
  const env = await call(await connect(), "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
    granularidade: "day",
  });
  const soma = env.data.series.reduce((acc: number, p: any) => acc + (p.gmv ?? 0), 0);
  assert.equal(soma, env.data.total.gmv);
});

// ---------------------------------------------------------------------------
// D5/D6/D7 — periodo, filtros e limite
// ---------------------------------------------------------------------------

test("D5: periodo resolvido e' inclusivo e corresponde ao mes anterior", async () => {
  const env = await call(await connect(), "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
  });
  // Relogio fixo em 2026-08-18 (BRT) -> mes anterior = julho inteiro.
  assert.deepEqual(env.meta.period, {
    start: "2026-07-01",
    end: "2026-07-31",
    inclusive: true,
  });
});

test("D6: filtros aplicados sao o ECO do backend", async () => {
  const env = await call(await connect(), "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
    canais: ["ml"],
  });
  // O fixture ecoa channels=all; prevalece o backend, nao o input do modelo.
  assert.equal(env.meta.filters_applied.channels, "all");
});

test("D6: o escopo pedido chega ao backend como querystring allowlisted", async () => {
  const log: CallLog = [];
  const client = await connect(ROUTES, log);
  await call(client, "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
    canais: ["ml", "tiktok"],
    marcas: ["barbours"],
  });

  const overviewCall = log.find((c) => c.url.includes("/overview"));
  assert.ok(overviewCall);
  assert.ok(overviewCall.url.includes("channels=ml%2Ctiktok"));
  assert.ok(overviewCall.url.includes("brands=barbours"));
  assert.ok(overviewCall.url.includes("date_from=2026-07-01"));
  assert.ok(overviewCall.url.includes("date_to=2026-07-31"));
});

test("D9: periodo que inclui hoje traz o aviso de dia parcial", async () => {
  const env = await call(await connect(), "torre_desempenho_marketplaces", {
    periodo: "mes_atual",
  });
  assert.equal(env.meta.period.end, "2026-08-18");
  assert.ok(
    env.meta.warnings.some((w: string) => /dia corrente/i.test(w)),
    "aviso de carga parcial e' obrigatorio",
  );
});

test("mes fechado NAO recebe aviso de dia parcial", async () => {
  const env = await call(await connect(), "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
  });
  assert.ok(!env.meta.warnings.some((w: string) => /dia corrente/i.test(w)));
});

// ---------------------------------------------------------------------------
// D2 — vazio verdadeiro e' diferente de fonte indisponivel
// ---------------------------------------------------------------------------

test("D2: resultado vazio VERDADEIRO devolve lista vazia, nao erro", async () => {
  const vazio = {
    ...ROUTES,
    "/api/v1/regioes/summary": { ...REGIOES_SUMMARY, gmv: 0, orders: 0, ufs_com_venda: 0 },
    "/api/v1/regioes/by-uf": { ...REGIOES_BY_UF, data: [] },
  };
  const env = await call(await connect(vazio), "torre_regioes_vendas", {});

  assert.deepEqual(env.data.by_uf, []);
  assert.equal(env.meta.returned_count, 0);
  // Zero medido continua zero — nao vira null nem erro.
  assert.equal(env.data.summary.gmv, 0);
});

test("comparacao so aparece quando solicitada", async () => {
  const client = await connect();

  const sem = await call(client, "torre_desempenho_marketplaces", { periodo: "mes_anterior" });
  assert.equal(sem.data.total.gmv_previous, null);
  assert.equal(sem.data.total.change_pct, null);

  const com = await call(client, "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
    comparar: true,
  });
  assert.equal(com.data.total.gmv_previous, 800);
  assert.equal(com.data.total.change_pct, 25);
});

test("serie e' null quando nao solicitada (distinto de vazia)", async () => {
  const client = await connect();
  const sem = await call(client, "torre_desempenho_marketplaces", { periodo: "mes_anterior" });
  assert.equal(sem.data.series, null, "null = nao solicitada");

  const com = await call(client, "torre_desempenho_marketplaces", {
    periodo: "mes_anterior",
    granularidade: "day",
  });
  assert.ok(Array.isArray(com.data.series));
});

// ---------------------------------------------------------------------------
// D8 — proveniencia completa
// ---------------------------------------------------------------------------

test("D8: toda tool devolve proveniencia completa no envelope", async () => {
  const client = await connect();
  for (const name of [
    "torre_desempenho_marketplaces",
    "torre_comparar_canais_marcas",
    "torre_qualidade_dados",
    "torre_regioes_vendas",
  ]) {
    const env = await call(client, name, {});
    assert.ok(env.meta.source.includes("Torre"), `${name}: source`);
    assert.equal(env.meta.layer, "marts (Neon)", `${name}: layer`);
    assert.equal(env.meta.timezone, "America/Sao_Paulo", `${name}: timezone`);
    assert.equal(env.meta.currency, "BRL", `${name}: currency`);
    assert.equal(env.meta.monetary_unit, "reais", `${name}: unidade monetaria`);
    assert.ok(env.meta.metric_definition.length > 20, `${name}: definicao da metrica`);
    assert.ok(env.meta.coverage.length > 5, `${name}: cobertura`);
    assert.ok(Array.isArray(env.meta.warnings), `${name}: warnings`);
  }
});

test("produtos do ML declaram escopo cumulativo e recusam filtro de mes", async () => {
  const client = await connect();
  const env = await call(client, "torre_produtos_prioritarios", { canal: "ml", limite: 2 });
  assert.equal(env.data.temporal_scope, "cumulativo");
  assert.equal(env.meta.period, null, "produtos ML nao tem periodo");
  assert.ok(env.data.limitations.some((l: any) => l.topic === "produtos_ml_cumulativo"));

  // Pedir mes para o ML e' erro explicito, nunca ignorado em silencio.
  const res = await client.callTool({
    name: "torre_produtos_prioritarios",
    arguments: { canal: "ml", mes: "2026-07" },
  });
  assert.equal(res.isError, true);
});
