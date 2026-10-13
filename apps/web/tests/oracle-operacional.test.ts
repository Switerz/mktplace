// MCP-COVERAGE-2 — tools operacionais, extensao de Canais e contrato da DRE.
// Grupos: ausencia x zero, payload vazio, fonte defasada, PII, limites de
// linha/periodo, parametro desconhecido/texto livre antes do GET, DRE fora do
// tools/list. Nada aqui toca rede.
import { test } from "node:test";
import assert from "node:assert/strict";

import { Client } from "@modelcontextprotocol/client";
import { InMemoryTransport } from "@modelcontextprotocol/server";

import { buildOracleServer } from "../src/server/oracle/server.ts";
import { SlidingWindowRateLimiter } from "../src/server/oracle/rate-limit.ts";
import { ENDPOINTS, TorreClient } from "../src/server/oracle/torre-client.ts";
import { OracleToolError } from "../src/server/oracle/errors.ts";
import { ORACLE_TOOLS } from "../src/server/oracle/tools.ts";
import { DRE_ENDPOINT_PATH, DRE_TOOL_NAME, dreInput, projectDre } from "../src/server/oracle/dre-contract.ts";
import { CANAIS, FIXED_NOW } from "./oracle-fixtures.ts";
import {
  AFFILIATE_COSTS, dreCelula, dreResponse, EXP_ML, EXP_SHOPEE, EXP_TIKTOK, EXP_TREND_ML,
  EXP_TREND_SHOPEE, ML_FULL, ML_FULL_SEM_PEDIDO, PMA, SHOPEE_ESTOQUE, SHOPEE_ESTOQUE_INDISPONIVEL,
  SHOPEE_FBS, SHOPEE_FBS_SEM_SERIE, TIKTOK_DISCOUNTS,
} from "./oracle-fixtures-operacional.ts";

type Route = unknown | ((u: URL) => unknown);
type Calls = URL[];

function routedFetch(routes: Record<string, Route>, calls: Calls): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    const u = new URL(typeof input === "string" ? input : input.toString());
    calls.push(u);
    const r = routes[u.pathname];
    const payload = typeof r === "function" ? (r as (u: URL) => unknown)(u) : r;
    if (payload === undefined) {
      return new Response(JSON.stringify({ detail: "not found" }), {
        status: 404, headers: { "content-type": "application/json" },
      });
    }
    return new Response(JSON.stringify(payload), {
      status: 200, headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

const EXP_ROUTES: Record<string, Route> = {
  "/api/v1/expedicao": (u: URL) => (u.searchParams.get("channel") === "mercadolivre" ? EXP_ML : EXP_SHOPEE),
  "/api/v1/expedicao/trend": (u: URL) => (u.searchParams.get("channel") === "mercadolivre" ? EXP_TREND_ML : EXP_TREND_SHOPEE),
  "/api/v1/expedicao/tiktok": EXP_TIKTOK,
};

const STOCK_ROUTES: Record<string, Route> = {
  "/api/v1/performance/ml-fulfillment": ML_FULL,
  "/api/v1/performance/shopee-fbs": SHOPEE_FBS,
  "/api/v1/performance/shopee-fbs-estoque": SHOPEE_ESTOQUE,
};

async function connect(routes: Record<string, Route>, calls: Calls = []) {
  const server = buildOracleServer({
    backendBaseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: routedFetch(routes, calls),
    now: () => FIXED_NOW,
    rateLimiter: new SlidingWindowRateLimiter({ limit: 1000, windowMs: 60_000 }),
  });
  const [ct, st] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "operacional", version: "0.0.0" });
  await Promise.all([client.connect(ct), server.connect(st)]);
  return client;
}

async function call(client: Client, name: string, args: Record<string, unknown> = {}) {
  const res = await client.callTool({ name, arguments: args });
  assert.notEqual(res.isError, true, `${name} deveria ter sucesso: ${JSON.stringify(res.content)}`);
  return res.structuredContent as { meta: Record<string, any>; data: any };
}

async function callError(client: Client, name: string, args: Record<string, unknown>) {
  const res = await client.callTool({ name, arguments: args });
  assert.equal(res.isError, true, `${name} deveria falhar`);
  return (res.content as Array<{ text: string }>)[0].text;
}

// ===========================================================================
// Registro
// ===========================================================================

test("tools/list: exatamente 8, e a DRE NAO esta registrada nem alcancavel", async () => {
  const { tools } = await (await connect({})).listTools();
  assert.equal(tools.length, 8);
  assert.ok(!tools.some((t) => t.name === DRE_TOOL_NAME), "DRE nao pode aparecer no tools/list");
  assert.ok(!ORACLE_TOOLS.some((t) => t.name === DRE_TOOL_NAME));
  assert.ok(!Object.values(ENDPOINTS).includes(DRE_ENDPOINT_PATH as never), "path da DRE fora da allowlist");
  for (const name of ["torre_expedicao_operacional", "torre_estoque_full", "torre_monitoramento_precos"]) {
    const t = tools.find((x) => x.name === name)!;
    assert.ok(t, `${name} registrada`);
    assert.equal(t.annotations?.readOnlyHint, true, `${name} read-only`);
    assert.equal(t.annotations?.destructiveHint, false);
  }
});

// ===========================================================================
// Expedicao
// ===========================================================================

test("expedicao: include_queue=false sempre, nenhuma linha/identificador de pedido na saida (PII)", async () => {
  const calls: Calls = [];
  const env = await call(await connect(EXP_ROUTES, calls), "torre_expedicao_operacional", {});
  const resumo = calls.filter((u) => u.pathname === "/api/v1/expedicao");
  assert.equal(resumo.length, 2, "shopee + ml");
  for (const u of resumo) assert.equal(u.searchParams.get("include_queue"), "false");

  const s = JSON.stringify(env);
  for (const proibido of ["REF-SECRETO-123", "order_ref", "paid_at", "dispatch_deadline", "TRANSPORTADORA-X", "hours_overdue"]) {
    assert.ok(!s.includes(proibido), `saida nao pode conter ${proibido}`);
  }
  for (const canal of ["shopee", "ml"]) {
    assert.ok(!("queue" in env.data[canal]) && !("fila" in env.data[canal]), "nenhum bloco de fila");
  }
  assert.equal(env.meta.filters_applied.include_queue, false);
  assert.ok(env.data.limitations.some((l: any) => l.topic === "expedicao_sem_pedido"));
});

test("expedicao: o adapter recusa /expedicao sem include_queue=false ANTES do fetch", async () => {
  const calls: Calls = [];
  const c = new TorreClient({ baseUrl: "https://mktplace-api.onrender.com", fetchImpl: routedFetch(EXP_ROUTES, calls) });
  for (const params of [{ channel: "shopee" }, { channel: "shopee", include_queue: "true" }]) {
    await assert.rejects(
      () => c.get("expedicao", params),
      (e: unknown) => e instanceof OracleToolError && e.category === "MISSING_CONFIGURATION",
    );
  }
  assert.equal(calls.length, 0, "nenhuma chamada sai");
});

test("expedicao: ML sem prazo publicado -> vencidos NULO (nao zero); Shopee com prazo -> zero real preservado", async () => {
  const env = await call(await connect(EXP_ROUTES), "torre_expedicao_operacional", { canais: ["shopee", "ml"] });
  const ml = env.data.ml;
  assert.equal(ml.prazo, "nao_publicado_pelo_canal");
  assert.equal(ml.totais.vencidos, null);
  assert.equal(ml.totais.em_risco_24h, null);
  assert.equal(ml.totais.no_prazo, null);
  assert.equal(ml.totais.sem_prazo_publicado, 12);
  assert.equal(ml.totais.backlog, 12);
  assert.equal(ml.por_conta[0].vencidos, null);

  const sh = env.data.shopee;
  assert.equal(sh.prazo, "medido");
  assert.equal(sh.totais.vencidos, 0, "zero medido continua zero");
  assert.equal(sh.totais.em_risco_24h, 5);
  assert.ok(env.data.limitations.some((l: any) => l.topic === "expedicao_prazo_nao_publicado"));
  assert.match((await (await connect(EXP_ROUTES)).callTool({ name: "torre_expedicao_operacional", arguments: { canais: ["ml"] } })).content[0].text as string, /vencidos nao medidos/);
});

test("expedicao: filtro de marca e' LOCAL (fonte ignora brands no resumo), marca sem conta nao vira zero", async () => {
  const calls: Calls = [];
  const env = await call(await connect(EXP_ROUTES, calls), "torre_expedicao_operacional", {
    canais: ["shopee", "ml"], marcas: ["apice", "kokeshi"],
  });
  for (const u of calls.filter((x) => x.pathname.startsWith("/api/v1/expedicao") && !x.pathname.endsWith("tiktok"))) {
    assert.equal(u.searchParams.get("brands"), null, "brands nao e' enviado a /expedicao");
  }
  const sh = env.data.shopee;
  assert.equal(sh.totais.backlog, 10, "so' a conta da apice");
  assert.deepEqual(sh.por_conta.map((a: any) => a.brand), ["apice"]);
  assert.deepEqual(sh.marcas_sem_conta, ["kokeshi"]);
  assert.deepEqual(env.data.ml.marcas_sem_conta, ["apice"]);
  assert.ok(env.meta.warnings.some((w: string) => /sem conta para kokeshi/.test(w)));
  assert.ok(env.data.limitations.some((l: any) => l.topic === "expedicao_filtro_marca"));
});

test("expedicao: tendencia soma contas da mesma hora e marca hora incompleta", async () => {
  const env = await call(await connect(EXP_ROUTES), "torre_expedicao_operacional", { canais: ["shopee"] });
  const pts = env.data.shopee.tendencia.pontos;
  assert.equal(pts.length, 2);
  assert.equal(pts[0].snapshot_hour, "2026-08-18T09:00:00Z");
  assert.equal(pts[0].completo, false, "09h so' tem a apice");
  assert.equal(pts[0].backlog, 10);
  assert.equal(pts[1].completo, true);
  assert.equal(pts[1].backlog, 30);
});

test("expedicao: TikTok medicao interna declarada; taxa sem base continua NULA", async () => {
  const env = await call(await connect(EXP_ROUTES), "torre_expedicao_operacional", { canais: ["tiktok"] });
  const tk = env.data.tiktok;
  assert.equal(tk.medicao, "interna_reconstruida");
  assert.equal(tk.sla_dias_uteis, 2);
  assert.equal(tk.ldr.taxa, 0.05);
  assert.equal(tk.ldr_por_vencimento[1].taxa, null);
  assert.equal(tk.por_loja[1].taxa, 0, "zero medido na loja continua 0");
  assert.ok(env.data.limitations.some((l: any) => l.topic === "ldr_tiktok_interna"));
});

test("expedicao: payload vazio/indisponivel nao vira backlog ou LDR zero", async () => {
  const env = await call(
    await connect({
      "/api/v1/expedicao": { ...EXP_SHOPEE, availability: "unavailable", unavailable_reason: "no_snapshot_published", snapshot: null, totals: null, accounts: [], freshness: [], coverage: null },
      "/api/v1/expedicao/trend": { ...EXP_TREND_SHOPEE, availability: "unavailable", points: [] },
      "/api/v1/expedicao/tiktok": { ...EXP_TIKTOK, availability: "unavailable", unavailable_reason: "no_series_published", ldr: null, brands: [], alerts: [], snapshot: null },
    }),
    "torre_expedicao_operacional",
    { canais: ["shopee", "tiktok"] },
  );
  assert.equal(env.data.shopee.totais, null);
  assert.equal(env.data.shopee.prazo, null);
  assert.equal(env.data.tiktok.ldr, null);
  assert.ok(env.meta.warnings.some((w: string) => /Shopee: expedicao indisponivel/.test(w)));
  assert.ok(env.meta.warnings.some((w: string) => /nao trate como LDR zero/.test(w)));
});

test("expedicao: fonte defasada e fotografia anterior sao avisadas", async () => {
  const velho = {
    ...EXP_SHOPEE,
    snapshot: { ...EXP_SHOPEE.snapshot, source_health: "watermark_missing" },
    freshness: [{ brand: "apice", freshness: "stale", source_age_hours: 30, derived_from_batch: false }],
    coverage: { ...EXP_SHOPEE.coverage, missing_accounts: ["barbours"] },
  };
  const env = await call(
    await connect({ ...EXP_ROUTES, "/api/v1/expedicao": velho }),
    "torre_expedicao_operacional",
    { canais: ["shopee"] },
  );
  const w = env.meta.warnings.join(" | ");
  assert.match(w, /fotografia servida e' a ANTERIOR/);
  assert.match(w, /apice \(stale\)/);
  assert.match(w, /INCOMPLETO/);
});

test("expedicao: limites de janela e parametro desconhecido falham ANTES do GET", async () => {
  const calls: Calls = [];
  const client = await connect(EXP_ROUTES, calls);
  for (const args of [
    { tendencia_horas: 337 },
    { tiktok_dias: 91 },
    { tiktok_dias: 0 },
    { canais: ["amazon"] },
    { include_queue: true },
    { situacao: "overdue" },
    { marcas: ["apice", "apice"] },
  ]) {
    await callError(client, "torre_expedicao_operacional", args);
  }
  assert.equal(calls.length, 0, "nenhum input invalido toca a fonte");
});

// ===========================================================================
// Full e estoque
// ===========================================================================

test("estoque: ML e' nao_aplicavel para estoque; tempos sem amostra sao nulos", async () => {
  const env = await call(await connect(STOCK_ROUTES), "torre_estoque_full", { canais: ["ml"] });
  const ml = env.data.ml_full;
  assert.equal(ml.estoque, "nao_aplicavel");
  assert.equal(ml.gmv_pago_total, 1000.5, "Decimal em string vira numero");
  const unknown = ml.por_modalidade.find((m: any) => m.modalidade === "unknown");
  assert.equal(unknown.dias_ate_despacho, null);
  assert.equal(unknown.gmv_pago, 0, "GMV zero medido na classe continua 0");
  assert.ok(env.data.limitations.some((l: any) => l.topic === "full_ml_nao_e_estoque"));
  assert.equal(env.data.shopee_estoque, null);
});

test("estoque: marca sem pedido no ML -> GMV nulo (sem_dado), nao R$ 0", async () => {
  const env = await call(
    await connect({ ...STOCK_ROUTES, "/api/v1/performance/ml-fulfillment": ML_FULL_SEM_PEDIDO }),
    "torre_estoque_full",
    { canais: ["ml"], marcas: ["apice"] },
  );
  assert.equal(env.data.ml_full.estado, "sem_dado");
  assert.equal(env.data.ml_full.gmv_pago_total, null);
  assert.notEqual(env.data.ml_full.gmv_pago_total, 0);
  assert.ok(env.meta.warnings.some((w: string) => /nenhum pedido pago/.test(w)));
});

test("estoque: FBS antes da serie -> sem_dado; share FBS zero real continua 0", async () => {
  const semSerie = await call(
    await connect({ ...STOCK_ROUTES, "/api/v1/performance/shopee-fbs": SHOPEE_FBS_SEM_SERIE }),
    "torre_estoque_full",
    { canais: ["shopee"] },
  );
  assert.equal(semSerie.data.shopee_fbs.estado, "sem_dado");
  assert.equal(semSerie.data.shopee_fbs.gmv_bruto, null);

  const env = await call(await connect(STOCK_ROUTES), "torre_estoque_full", { canais: ["shopee"] });
  assert.equal(env.data.shopee_fbs.share_fbs_gmv, 0, "mediu e deu zero");
  assert.equal(env.data.shopee_fbs.por_marca[0].share_fbs_gmv, 0);
});

test("estoque: quatro estados por item — zero_real, medido, sem_dado, nao_conciliado", async () => {
  const env = await call(await connect(STOCK_ROUTES), "torre_estoque_full", { canais: ["shopee"] });
  const it = Object.fromEntries(env.data.shopee_estoque.itens.map((i: any) => [i.item_id, i]));
  assert.equal(it["1"].estado_estoque, "zero_real");
  assert.equal(it["1"].estoque_vendavel, 0);
  assert.equal(it["1"].cobertura_dias, 0);
  assert.equal(it["1"].estado_cobertura, "zero_real");
  assert.equal(it["2"].estado_estoque, "medido");
  assert.equal(it["2"].cobertura_dias, 10);
  assert.equal(it["3"].cobertura_dias, null, "sem demanda medida nao e' 0 dia");
  assert.equal(it["3"].estado_cobertura, "sem_dado");
  assert.equal(it["4"].estado_estoque, "nao_conciliado");
  assert.equal(it["4"].cobertura_dias, null, "kit nao conciliado nao e' 0 dia");
  assert.equal(env.data.shopee_estoque.itens[0].item_id, "1", "ordem de risco da fonte preservada");
});

test("estoque: estoque indisponivel nao vira estoque zero", async () => {
  const env = await call(
    await connect({ ...STOCK_ROUTES, "/api/v1/performance/shopee-fbs-estoque": SHOPEE_ESTOQUE_INDISPONIVEL }),
    "torre_estoque_full",
    { canais: ["shopee"] },
  );
  const e = env.data.shopee_estoque;
  assert.equal(e.disponibilidade, "unavailable");
  assert.equal(e.estado, "sem_dado");
  assert.equal(e.indicadores, null);
  assert.deepEqual(e.itens, []);
  assert.ok(env.meta.warnings.some((w: string) => /nao trate como estoque zero/.test(w)));
});

test("estoque: marca fora da esteira e' lida da fonte e NAO e' enviada (evita 422)", async () => {
  const calls: Calls = [];
  const env = await call(await connect(STOCK_ROUTES, calls), "torre_estoque_full", {
    canais: ["shopee"], marcas: ["kokeshi", "apice"], severidade: "exige_acao", limite: 3,
  });
  const est = calls.filter((u) => u.pathname === "/api/v1/performance/shopee-fbs-estoque");
  assert.equal(est.length, 2, "preflight + chamada filtrada");
  assert.deepEqual(est[1].searchParams.getAll("brands"), ["apice"]);
  assert.equal(est[1].searchParams.get("somente_acao"), "true");
  assert.equal(est[1].searchParams.get("limite"), "3");
  const fbs = calls.filter((u) => u.pathname === "/api/v1/performance/shopee-fbs");
  assert.ok(fbs.every((u) => !u.searchParams.getAll("brands").includes("kokeshi")));
  assert.ok(env.data.shopee_estoque.marcas_nao_cobertas.includes("kokeshi"));
  assert.ok(env.meta.warnings.some((w: string) => /kokeshi fora da esteira/.test(w)));
});

test("estoque: fotografia antiga e FBS atrasado sao avisados", async () => {
  const env = await call(
    await connect({
      ...STOCK_ROUTES,
      "/api/v1/performance/shopee-fbs-estoque": { ...SHOPEE_ESTOQUE, frescor: { ...SHOPEE_ESTOQUE.frescor, dias_desde_a_fotografia: 3 } },
      "/api/v1/performance/shopee-fbs": { ...SHOPEE_FBS, meta: { ...SHOPEE_FBS.meta, freshness: { ...SHOPEE_FBS.meta.freshness, freshness_status: "stale", closed_days_behind: 2 } } },
    }),
    "torre_estoque_full",
    { canais: ["shopee"] },
  );
  const w = env.meta.warnings.join(" | ");
  assert.match(w, /3 dia\(s\)/);
  assert.match(w, /frescor 'stale'/);
  assert.match(w, /2 dia\(s\) fechado\(s\) atras/);
});

test("estoque: limites e texto livre falham antes do GET; periodo so' com dias fechados", async () => {
  const calls: Calls = [];
  const client = await connect(STOCK_ROUTES, calls);
  for (const args of [
    { limite: 51 },
    { busca: "esfoliante" },
    { classificacoes: ["ruptura"] },
    { severidade: "alta" },
    { periodo: "personalizado", data_inicio: "2026-08-01", data_fim: "2026-08-18" }, // D0
    { periodo: "personalizado", data_inicio: "2026-03-01", data_fim: "2026-08-10" }, // > 92 dias
  ]) {
    await callError(client, "torre_estoque_full", args);
  }
  assert.equal(calls.length, 0);

  const c = new TorreClient({ baseUrl: "https://mktplace-api.onrender.com", fetchImpl: routedFetch(STOCK_ROUTES, calls) });
  for (const p of [{ busca: "x" }, { accounts: ["apice"] }, { limit: 10 }]) {
    await assert.rejects(() => c.get("shopeeFbsEstoque", p as never), (e: unknown) => e instanceof OracleToolError);
  }
  assert.equal(calls.length, 0, "texto livre nunca sai");
});

test("estoque: no maximo 50 itens e truncamento declarado", async () => {
  const muitos = {
    ...SHOPEE_ESTOQUE,
    produtos: Array.from({ length: 60 }, (_, i) => ({ ...SHOPEE_ESTOQUE.produtos[1], item_id: String(i) })),
    total_no_filtro: 60,
  };
  const env = await call(
    await connect({ ...STOCK_ROUTES, "/api/v1/performance/shopee-fbs-estoque": muitos }),
    "torre_estoque_full",
    { canais: ["shopee"], limite: 50 },
  );
  assert.equal(env.data.shopee_estoque.itens.length, 50);
  assert.equal(env.meta.total_count, 60);
  assert.equal(env.meta.truncated, true);
});

// ===========================================================================
// Monitoramento de precos
// ===========================================================================

const PMA_ROUTES: Record<string, Route> = { "/api/v1/performance/monitoramento-preco": PMA };

test("precos: sem referencia -> diferenca NULA; preco ausente nunca inventado; kit exposto", async () => {
  const env = await call(await connect(PMA_ROUTES), "torre_monitoramento_precos", { canal: "ml" });
  const a = Object.fromEntries(env.data.anuncios.map((x: any) => [x.item_id, x]));
  assert.equal(a.MLB3.referencia_pdv, null);
  assert.equal(a.MLB3.diferenca_pct, null);
  assert.notEqual(a.MLB3.diferenca_pct, 0);
  assert.equal(a.MLB4.preco_anunciado, null, "preco ausente continua nulo");
  assert.equal(a.MLB2.kit.componentes, 2);
  assert.equal(a.MLB1.kit, null);
  assert.equal(env.data.kpis.kits_com_referencia_derivada, 1);
  assert.equal(env.data.anuncios[0].item_id, "MLB1", "ordem da fonte (maior diferenca negativa) preservada");
  const s = JSON.stringify(env);
  assert.ok(!s.includes("exemplo.invalid"), "permalink nao e' repassado");
  assert.ok(!s.includes("checkout_price"));
});

test("precos: situacao mapeada para o enum da fonte; KPIs nao seguem o filtro", async () => {
  const calls: Calls = [];
  const env = await call(await connect(PMA_ROUTES, calls), "torre_monitoramento_precos", {
    canal: "shopee", situacoes: ["abaixo_da_referencia", "sem_referencia"], tipos_produto: ["kit_confirmed"], limite: 7,
  });
  assert.equal(calls.length, 1, "sem marca/data: sem preflight");
  assert.equal(calls[0].searchParams.get("status"), "below_reference,no_reference");
  assert.equal(calls[0].searchParams.get("product_type"), "kit_confirmed");
  assert.equal(calls[0].searchParams.get("marketplace"), "shopee");
  assert.equal(calls[0].searchParams.get("limit"), "7");
  assert.equal(env.data.kpis.monitorados, 10);
});

test("precos: marca nao observada vira marcas_pedidas_fora (sem 422), nunca 'zero anuncio'", async () => {
  const calls: Calls = [];
  const env = await call(await connect(PMA_ROUTES, calls), "torre_monitoramento_precos", {
    canal: "ml", marcas: ["apice", "kokeshi"],
  });
  assert.equal(calls.length, 2);
  assert.equal(calls[1].searchParams.get("brand"), "kokeshi", "apice nao e' enviada");
  assert.deepEqual(env.data.marcas_pedidas_fora, [{ brand: "apice", motivo: "out_of_scope_no_ml_catalog" }]);

  const so = await call(await connect(PMA_ROUTES), "torre_monitoramento_precos", { canal: "ml", marcas: ["apice"] });
  assert.equal(so.data.kpis, null, "sem marca valida: KPIs nulos, nao zero");
  assert.equal(so.data.motivo_indisponivel, "marca_nao_observada");
  assert.ok(so.meta.warnings.some((w: string) => /nao e' 'zero anuncios/.test(w)));
});

test("precos: data sem observacao nao faz a chamada principal e lista as datas", async () => {
  const calls: Calls = [];
  const env = await call(await connect(PMA_ROUTES, calls), "torre_monitoramento_precos", {
    canal: "ml", data_observada: "2026-08-01",
  });
  assert.equal(calls.length, 1, "so' o preflight");
  assert.equal(env.data.kpis, null);
  assert.deepEqual(env.data.datas_disponiveis, ["2026-08-17", "2026-08-16"]);
});

test("precos: indisponivel e defasado sao avisados, sem KPI zerado", async () => {
  const off = await call(
    await connect({ "/api/v1/performance/monitoramento-preco": { ...PMA, meta: { ...PMA.meta, availability: "unavailable", unavailable_reason: "sem_fotografia" } } }),
    "torre_monitoramento_precos", { canal: "ml" },
  );
  assert.equal(off.data.kpis, null);
  assert.deepEqual(off.data.anuncios, []);
  assert.ok(off.meta.warnings.some((w: string) => /nao trate como zero anuncio/.test(w)));

  const stale = await call(
    await connect({ "/api/v1/performance/monitoramento-preco": { ...PMA, meta: { ...PMA.meta, freshness_status: "stale", lag_days: 3 } } }),
    "torre_monitoramento_precos", { canal: "ml" },
  );
  assert.ok(stale.meta.warnings.some((w: string) => /3 dia\(s\) de defasagem/.test(w)));
});

test("precos: product_query, canal fora do enum e limite > 50 falham antes do GET", async () => {
  const calls: Calls = [];
  const client = await connect(PMA_ROUTES, calls);
  for (const args of [
    { canal: "ml", product_query: "bruma" },
    { canal: "amazon" },
    { canal: "ml", limite: 51 },
    { canal: "ml", situacoes: ["below_reference"] },
    { canal: "ml", data_observada: "2026-02-31" },
    {},
  ]) {
    await callError(client, "torre_monitoramento_precos", args);
  }
  assert.equal(calls.length, 0);
  const c = new TorreClient({ baseUrl: "https://mktplace-api.onrender.com", fetchImpl: routedFetch(PMA_ROUTES, calls) });
  for (const p of [{ product_query: "x" }, { shop_account: "a" }, { offset: 1 }, { order_by: "x" }]) {
    await assert.rejects(() => c.get("monitoramentoPreco", p as never), (e: unknown) => e instanceof OracleToolError);
  }
  assert.equal(calls.length, 0);
});

// ===========================================================================
// Canais — afiliados e descontos
// ===========================================================================

const canaisCom = (extra: Record<string, unknown>) => ({ "/api/v1/performance/canais": { ...CANAIS, ...extra } });

test("canais: afiliados com snapshot manual; zero medido 0, ausente null; receita e retorno nulos", async () => {
  const env = await call(
    await connect(canaisCom({ affiliate_costs: AFFILIATE_COSTS, tiktok_order_discounts: TIKTOK_DISCOUNTS })),
    "torre_comparar_canais_marcas", { periodo: "mes_anterior" },
  );
  const a = env.data.afiliados;
  assert.equal(a.estado, "available");
  assert.equal(a.snapshot_manual, true);
  assert.equal(a.linhas[0].comissao_criador, -100);
  assert.equal(a.linhas[0].comissao_parceiro, 0, "zero medido");
  assert.equal(a.linhas[0].comissao_ads_afiliado, null, "ausente");
  assert.equal(a.receita_atribuida, null);
  assert.equal(a.retorno_roas, null);
  assert.equal(a.retorno_disponibilidade, "unavailable_no_attributed_revenue");
  assert.ok(env.data.limitations.some((l: any) => l.topic === "afiliados_sem_retorno"));

  const d = env.data.descontos_tiktok;
  assert.equal(d.linhas[0].desconto_marca, 0, "desconto zero medido");
  assert.equal(d.linhas[0].subsidio_plataforma, 15);
  assert.equal(env.data.rows.length, CANAIS.channel_rows.length, "comparacao intacta");
});

test("canais: bloco disponivel sem linhas = ausencia de registro, nao custo/desconto zero", async () => {
  const env = await call(
    await connect(canaisCom({
      affiliate_costs: { ...AFFILIATE_COSTS, period_status: "partial_month", rows: [], months_included: [] },
      tiktok_order_discounts: { ...TIKTOK_DISCOUNTS, rows: [] },
    })),
    "torre_comparar_canais_marcas", { periodo: "mes_anterior" },
  );
  assert.deepEqual(env.data.afiliados.linhas, []);
  const topics = env.data.limitations.map((l: any) => l.topic);
  assert.ok(topics.includes("afiliados_sem_linhas"));
  assert.ok(topics.includes("descontos_sem_linhas"));
  assert.ok(env.meta.warnings.some((w: string) => /partial_month/.test(w)));
});

test("canais: bloco ausente = 'ausente'; bloco quebrado = 'formato_inesperado' sem derrubar a comparacao", async () => {
  const ausente = await call(await connect(canaisCom({})), "torre_comparar_canais_marcas", { periodo: "mes_anterior" });
  assert.equal(ausente.data.afiliados.estado, "ausente");
  assert.equal(ausente.data.descontos_tiktok.estado, "ausente");

  const quebrado = await call(
    await connect(canaisCom({
      affiliate_costs: { ...AFFILIATE_COSTS, availability_status: "talvez" },
      tiktok_order_discounts: { rows: "x" },
    })),
    "torre_comparar_canais_marcas", { periodo: "mes_anterior" },
  );
  assert.equal(quebrado.data.afiliados.estado, "formato_inesperado");
  assert.deepEqual(quebrado.data.afiliados.linhas, []);
  assert.equal(quebrado.data.descontos_tiktok.estado, "formato_inesperado");
  assert.equal(quebrado.data.rows.length, CANAIS.channel_rows.length);
});

test("canais: descontos com carga antiga e chave faltando sao avisados", async () => {
  const env = await call(
    await connect(canaisCom({ tiktok_order_discounts: { ...TIKTOK_DISCOUNTS, freshness_status: "stale_load", coverage_missing_keys: 2 } })),
    "torre_comparar_canais_marcas", { periodo: "mes_anterior" },
  );
  const w = env.meta.warnings.join(" | ");
  assert.match(w, /stale_load/);
  assert.match(w, /2 combinacao/);
});

// ===========================================================================
// DRE — contrato futuro (nao registrado)
// ===========================================================================

const NOW_DRE = new Date("2026-08-18T12:00:00Z");

test("dre: status mapeado; nao calculavel tem valor NULO mesmo se a fonte mandar 0", () => {
  const env = projectDre(dreResponse([dreCelula()]), { mes: "2026-07" }, NOW_DRE) as any;
  const c = env.data.celulas[0];
  assert.equal(c.receita.status, "CALCULADO");
  assert.equal(c.receita.valor, 1000);
  assert.equal(c.ads.status, "NAO_APLICAVEL");
  assert.equal(c.ads.valor, null);
  assert.equal(c.impostos.status, "NAO_CALCULAVEL");
  assert.equal(c.impostos.valor, null, "fonte mandou 0 com status indisponivel: sai nulo");
  assert.equal(c.impostos.status_fonte, "indisponivel");
  assert.equal(c.devolucoes.valor, 0, "zero CALCULADO continua 0");
  assert.equal(c.resultado.status, "CALCULADO");
  assert.equal(c.resultado.valor, 450);
  assert.equal(c.margem_pct, 45);
  assert.equal(c.custos_conhecidos.valor, 550, "devolucoes+taxas+frete+cmv");
  assert.equal(env.data.snapshot.insumos_disponiveis, true);
});

test("dre: sem CMV calculado nao existe margem", () => {
  const env = projectDre(
    dreResponse([dreCelula({ cmv: { valor: null, pct_receita: null, status: "provisorio", motivo: "x", fonte: null, somavel: true, partes: null, publicado_em: null } })]),
    { mes: "2026-07" }, NOW_DRE,
  ) as any;
  const c = env.data.celulas[0];
  assert.equal(c.cmv.status, "NAO_CALCULAVEL");
  assert.equal(c.resultado.valor, null);
  assert.equal(c.resultado.status, "NAO_CALCULAVEL");
  assert.equal(c.margem_pct, null);
  assert.equal(c.custos_conhecidos.valor, null);
});

test("dre: lote acima de 48 h derruba CMV/taxas/frete mesmo que a fonte diga disponivel", () => {
  const velho = "2026-08-15T00:00:00+00:00"; // 84 h antes de NOW_DRE
  const env = projectDre(dreResponse([dreCelula({}, velho)], "ok", velho), { mes: "2026-07" }, NOW_DRE) as any;
  const c = env.data.celulas[0];
  for (const k of ["cmv", "taxas", "frete"]) {
    assert.equal(c[k].status, "NAO_CALCULAVEL", k);
    assert.equal(c[k].valor, null, k);
    assert.equal(c[k].status_fonte, "snapshot_vencido", k);
  }
  assert.equal(c.receita.status, "CALCULADO", "receita nao depende do lote");
  assert.equal(c.resultado.valor, null);
  assert.equal(env.data.snapshot.insumos_disponiveis, false);
  assert.ok(env.meta.warnings.some((w: string) => /Insumos da DRE indisponiveis/.test(w)));
});

test("dre: lote com estado nao-ok e contrato quebrado", () => {
  const env = projectDre(dreResponse([dreCelula()], "parcialmente_vencido"), { mes: "2026-07" }, NOW_DRE) as any;
  assert.equal(env.data.celulas[0].cmv.status, "NAO_CALCULAVEL");
  assert.throws(
    () => projectDre({ por_canal_marca: [] }, { mes: "2026-07" }, NOW_DRE),
    (e: unknown) => e instanceof OracleToolError && e.category === "INVALID_UPSTREAM_RESPONSE",
  );
});

test("dre: entrada estrita (mes YYYY-MM, enums fechados)", () => {
  assert.ok(dreInput.safeParse({ mes: "2026-07", canais: ["ml"], marcas: ["kokeshi"] }).success);
  for (const bad of [{ mes: "2026-13" }, { mes: "2026-07", canais: ["amazon"] }, { mes: "2026-07", sql: "x" }, {}]) {
    assert.equal(dreInput.safeParse(bad).success, false, JSON.stringify(bad));
  }
});

// ===========================================================================
// Guardrails comuns
// ===========================================================================

test("novos endpoints: GET literal sob /api/v1, sem rota generica e sem DRE", () => {
  for (const k of ["expedicao", "expedicaoTrend", "expedicaoTiktok", "mlFulfillment", "shopeeFbs", "shopeeFbsEstoque", "monitoramentoPreco"] as const) {
    assert.match(ENDPOINTS[k], /^\/api\/v1\/[a-z0-9/-]+$/);
    assert.ok(!ENDPOINTS[k].includes("{") && !ENDPOINTS[k].includes(":"), "sem path parametrizavel");
  }
});

test("erro upstream nas tools novas e' sanitizado (sem URL, sem status)", async () => {
  const client = await connect({});
  for (const [name, args] of [
    ["torre_expedicao_operacional", { canais: ["shopee"] }],
    ["torre_estoque_full", { canais: ["shopee"] }],
    ["torre_monitoramento_precos", { canal: "ml" }],
  ] as const) {
    const text = await callError(client, name, args);
    assert.ok(!/onrender|https?:|404|\/api\//.test(text), `${name}: ${text}`);
    assert.match(text, /nao trate isso como zero/);
  }
});

test("rate limit tambem cobre as tools novas", async () => {
  const calls: Calls = [];
  const server = buildOracleServer({
    backendBaseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: routedFetch(PMA_ROUTES, calls),
    now: () => FIXED_NOW,
    rateLimiter: new SlidingWindowRateLimiter({ limit: 1, windowMs: 60_000 }),
  });
  const [ct, st] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "rl", version: "0" });
  await Promise.all([client.connect(ct), server.connect(st)]);
  await call(client, "torre_monitoramento_precos", { canal: "ml" });
  const text = await callError(client, "torre_monitoramento_precos", { canal: "ml" });
  assert.match(text, /Limite de chamadas/);
  assert.equal(calls.length, 1, "chamada barrada nao toca a fonte");
});
