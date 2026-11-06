/**
 * GERENCIAL-METAS-1 — progresso contra a meta mensal (src/lib/gerencial/meta-progress.ts).
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  avisoQueAfetaOGmv,
  celulasDoEscopo,
  diaDaMeta,
  diasNoMes,
  mesDaMeta,
  metaDoEscopo,
  progressoMeta,
  realizadoDoEscopo,
  ultimoDiaComDado,
} from "../src/lib/gerencial/meta-progress.ts";
import { presetRange } from "../src/lib/filters/presets.ts";

const HOJE = "2026-10-29";

test("mesDaMeta: so' mes unico, do dia 1, inteiro ou corrente ate' ontem", () => {
  assert.equal(mesDaMeta("2026-09-01", "2026-09-30", HOJE), "2026-09");
  assert.equal(mesDaMeta("2026-10-01", "2026-10-29", HOJE), "2026-10");
  // "Mes atual" termina em D-1 (presets ate' ontem): continua com meta
  assert.equal(mesDaMeta("2026-10-01", "2026-10-28", HOJE), "2026-10");
  assert.equal(mesDaMeta("2026-10-01", "2026-10-31", HOJE), "2026-10");
  // parcial de mes passado, nao comeca no dia 1, ou atravessa meses: sem meta
  assert.equal(mesDaMeta("2026-09-01", "2026-09-15", HOJE), null);
  assert.equal(mesDaMeta("2026-10-02", "2026-10-29", HOJE), null);
  assert.equal(mesDaMeta("2026-09-01", "2026-10-29", HOJE), null);
  // mes corrente parado antes de ontem (ex.: 01 a 20/10) tambem nao
  assert.equal(mesDaMeta("2026-10-01", "2026-10-20", HOJE), null);
  assert.equal(mesDaMeta("2026-10-01", "2026-10-27", HOJE), null);
});

test("mesDaMeta casa com o preset Mes atual (D-1), inclusive na virada de mes", () => {
  const range = (hoje: string) => presetRange("mes_atual", new Date(`${hoje}T12:00:00`));
  for (const hoje of ["2026-10-30", "2026-10-02", "2026-11-01", "2026-03-01"]) {
    const r = range(hoje);
    assert.notEqual(mesDaMeta(r.dateFrom, r.dateTo, hoje), null, hoje);
  }
});

test("diasNoMes", () => {
  assert.equal(diasNoMes("2026-02"), 28);
  assert.equal(diasNoMes("2028-02"), 29);
  assert.equal(diasNoMes("2026-10"), 31);
});

const CELULAS = [
  { brand: "kokeshi", channel: "tiktok", meta_gmv: 5_000_000 },
  { brand: "kokeshi", channel: "shopee", meta_gmv: 4_000_000 },
  { brand: "apice", channel: "tiktok", meta_gmv: 2_500_000 },
];

test("metaDoEscopo respeita canais e marcas filtrados", () => {
  const todos = metaDoEscopo(CELULAS, ["tiktok", "ml", "shopee"], []);
  assert.equal(todos.total, 11_500_000);
  assert.deepEqual(todos.porMarca, { kokeshi: 9_000_000, apice: 2_500_000 });
  assert.equal(todos.celulasComMeta, 3);
  assert.equal(metaDoEscopo(CELULAS, ["tiktok"], []).total, 7_500_000);
  assert.equal(metaDoEscopo(CELULAS, ["tiktok", "ml", "shopee"], ["apice"]).total, 2_500_000);
});

test("escopo sem nenhuma meta e' null, nunca zero", () => {
  const r = metaDoEscopo(CELULAS, ["ml"], []);
  assert.equal(r.total, null);
  assert.equal(r.celulasComMeta, 0);
});

test("ultimoDiaComDado ignora cauda sem venda e grao nao diario", () => {
  const b = [
    { date: "2026-10-27", values: { tiktok: 10, ml: null } },
    { date: "2026-10-28", values: { tiktok: 5, ml: 0 } },
    { date: "2026-10-29", values: { tiktok: 0, ml: null } },
  ];
  assert.equal(ultimoDiaComDado(b, "day"), "2026-10-28");
  assert.equal(ultimoDiaComDado(b, "week"), null);
  assert.equal(ultimoDiaComDado([], "day"), null);
});

test("progresso do mes corrente: projecao pelo ritmo dos dias com dado", () => {
  const p = progressoMeta({ meta: 20_000_000, realizado: 19_607_780, mes: "2026-10", hoje: HOJE, ultimoDia: "2026-10-28" })!;
  assert.equal(p.diasComDado, 28);
  assert.equal(p.diasRestantes, 3);
  assert.equal(p.encerrado, false);
  assert.ok(Math.abs(p.projecao! - 21_708_613.6) < 1);
  assert.equal(p.status, "no_caminho");
  assert.ok(Math.abs(p.falta - 392_220) < 1);
  assert.ok(Math.abs(p.porDiaNecessario! - 130_740) < 1);
});

test("status pelo limiar de 95% da projecao", () => {
  const base = { mes: "2026-10", hoje: HOJE, ultimoDia: "2026-10-28" };
  // projecao = realizado/28*31
  const comProjecao = (pct: number) => (pct * 1_000_000 * 28) / 31;
  assert.equal(progressoMeta({ ...base, meta: 1_000_000, realizado: comProjecao(0.97) })!.status, "atencao");
  assert.equal(progressoMeta({ ...base, meta: 1_000_000, realizado: comProjecao(0.9) })!.status, "abaixo");
  assert.equal(progressoMeta({ ...base, meta: 1_000_000, realizado: 1_000_000 })!.status, "atingida");
});

test("mes encerrado: projecao = realizado, sem dias restantes", () => {
  const nao = progressoMeta({ meta: 23_000_000, realizado: 22_121_164, mes: "2026-09", hoje: HOJE, ultimoDia: null })!;
  assert.equal(nao.encerrado, true);
  assert.equal(nao.status, "nao_atingida");
  assert.equal(nao.projecao, 22_121_164);
  assert.equal(nao.porDiaNecessario, null);
  const sim = progressoMeta({ meta: 20_000_000, realizado: 22_121_164, mes: "2026-09", hoje: HOJE, ultimoDia: null })!;
  assert.equal(sim.status, "atingida");
});

test("sem ultimo dia conhecido, conta ate' ontem", () => {
  const p = progressoMeta({ meta: 1_000, realizado: 500, mes: "2026-10", hoje: HOJE, ultimoDia: null })!;
  assert.equal(p.diasComDado, 28);
});

test("primeiro dia do mes sem dado: sem projecao, status atencao", () => {
  const p = progressoMeta({ meta: 1_000, realizado: 0, mes: "2026-11", hoje: "2026-11-01", ultimoDia: null })!;
  assert.equal(p.diasComDado, 0);
  assert.equal(p.projecao, null);
  assert.equal(p.status, "atencao");
});

test("sem meta, meta zero ou sem realizado: nao ha progresso", () => {
  const base = { mes: "2026-10", hoje: HOJE, ultimoDia: null };
  assert.equal(progressoMeta({ ...base, meta: null, realizado: 1 }), null);
  assert.equal(progressoMeta({ ...base, meta: 0, realizado: 1 }), null);
  assert.equal(progressoMeta({ ...base, meta: 1, realizado: null }), null);
});

test("avisoQueAfetaOGmv: so' aviso de atencao da fonte do GMV", () => {
  const regiao = { key: "r", severity: "attention" as const, source: "fact_marketplace_region_daily" };
  const gmv = { key: "g", severity: "attention" as const, source: "fact_marketplace_daily_performance" };
  const nota = { key: "n", severity: "note" as const, source: "fact_marketplace_daily_performance" };
  assert.equal(avisoQueAfetaOGmv([regiao, nota]), null);
  assert.equal(avisoQueAfetaOGmv([regiao, nota, gmv])?.key, "g");
  assert.equal(avisoQueAfetaOGmv([{ key: "x", severity: "attention" as const, source: null }]), null);
});

// ---------------------------------------------------------------------------
// METAS-DIARIAS-1 — so' marca x canal com meta; plano pela curva
// ---------------------------------------------------------------------------

test("celulasDoEscopo filtra canais e marcas como metaDoEscopo", () => {
  assert.deepEqual(
    celulasDoEscopo(CELULAS, ["tiktok"], ["kokeshi"]).map((c) => `${c.brand}:${c.channel}`),
    ["kokeshi:tiktok"],
  );
  assert.equal(celulasDoEscopo(CELULAS, ["tiktok", "ml", "shopee"], []).length, 3);
});

test("realizadoDoEscopo soma so' marca x canal que tem meta (TikTok sem meta fica fora)", () => {
  const linhas = [
    { brand: "kokeshi", tiktok_gmv: 5_000, ml_gmv: 2_000, shopee_gmv: 3_000 },
    { brand: "apice", tiktok_gmv: 4_000, ml_gmv: null, shopee_gmv: 600 },
  ];
  const celulas = [
    { brand: "kokeshi", channel: "ml", meta_gmv: 1 },
    { brand: "kokeshi", channel: "shopee", meta_gmv: 1 },
    { brand: "apice", channel: "shopee", meta_gmv: 1 },
    { brand: "barbours", channel: "ml", meta_gmv: 1 },
  ];
  const r = realizadoDoEscopo(linhas, celulas);
  assert.equal(r.total, 5_600);
  assert.deepEqual(r.porMarca, { kokeshi: 5_000, apice: 600, barbours: 0 });
  assert.deepEqual(r.canaisPorMarca, { kokeshi: ["ml", "shopee"], apice: ["shopee"], barbours: ["ml"] });
});

test("diaDaMeta: so' periodo de um dia", () => {
  assert.equal(diaDaMeta("2026-11-11", "2026-11-11", "2026-11-12"), "2026-11-11");
  assert.equal(diaDaMeta("2026-11-12", "2026-11-12", "2026-11-12"), "2026-11-12");
  assert.equal(diaDaMeta("2026-11-01", "2026-11-11", "2026-11-12"), null);
});

test("diaDaMeta no dia 1: 'Hoje' e' meta do dia; 'Mes atual' do dia 2 continua mensal", () => {
  assert.equal(diaDaMeta("2026-11-01", "2026-11-01", "2026-11-01"), "2026-11-01");
  const r = presetRange("mes_atual", new Date("2026-11-02T12:00:00"));
  assert.equal(diaDaMeta(r.dateFrom, r.dateTo, "2026-11-02"), null);
  assert.equal(mesDaMeta(r.dateFrom, r.dateTo, "2026-11-02"), "2026-11");
});

test("progressoMeta com plano pela curva: no plano = no caminho", () => {
  // Em 22/11 a curva de novembro espera ~63,5% do mes (a conta linear, 73%).
  const plano = (dia: number) => (dia === 22 ? 0.635 : dia / 30);
  const base = { meta: 10_000_000, mes: "2026-11", hoje: "2026-11-23", ultimoDia: "2026-11-22" };
  const p = progressoMeta({ ...base, realizado: 6_350_000, fracaoAte: plano })!;
  assert.ok(Math.abs(p.planoAteData - 6_350_000) < 1e-6);
  assert.ok(Math.abs(p.atingimentoDoPlano - 1) < 1e-9);
  assert.ok(Math.abs(p.projecao! - 10_000_000) < 1e-6);
  assert.equal(p.status, "no_caminho");
  // A mesma venda pela conta linear (22/30) ficaria abaixo do ritmo.
  assert.equal(progressoMeta({ ...base, realizado: 6_350_000 })!.status, "abaixo");
});

test("progressoMeta sem curva: plano linear, mesma projecao de antes", () => {
  const p = progressoMeta({ meta: 20_000_000, realizado: 19_607_780, mes: "2026-10", hoje: HOJE, ultimoDia: "2026-10-28" })!;
  assert.ok(Math.abs(p.planoAteData - (20_000_000 * 28) / 31) < 1e-6);
  assert.ok(Math.abs(p.projecao! - 21_708_613.6) < 1);
});

test("porDiaNecessario pela curva = falta / peso dos dias restantes (Nova meta dia normal)", async () => {
  const { fracaoDoEscopo, metaDiaNormalDoEscopo } = await import("../src/lib/metas-curva.ts");
  // Meta ficticia, so' Shopee. Pesos da aba Curva: total 39,26; 01–05/11 = 4,99.
  const celulas = [{ brand: "apice", channel: "shopee", meta_gmv: 1_000_000 }];
  const p = progressoMeta({
    meta: 1_000_000,
    realizado: 100_000,
    mes: "2026-11",
    hoje: "2026-11-06",
    ultimoDia: "2026-11-05",
    fracaoAte: fracaoDoEscopo(celulas, "2026-11"),
    metaDiaNormal: metaDiaNormalDoEscopo(celulas, "2026-11"),
  })!;
  assert.ok(Math.abs(p.porDiaNecessario! - 900_000 / (39.26 - 4.99)) < 1e-6);
  assert.ok(Math.abs(metaDiaNormalDoEscopo(celulas, "2026-11") - 1_000_000 / 39.26) < 1e-6);
});

test("divisaoFull: aplica a proporcao Full de cada canal ao GMV do overview; TikTok fica em normal", async () => {
  const { divisaoFull } = await import("../src/lib/gerencial/divisao-full.ts");
  assert.deepEqual(
    divisaoFull({ total: 1500, ml: 1000, shopee: 400 }, { ml: 0.5, shopee: 0.25 }),
    { full: 600, normal: 900 },
  );
  // canal com venda sem proporcao medida: nao inventa divisao
  assert.equal(divisaoFull({ total: 1400, ml: 1000, shopee: 400 }, { ml: 0.5, shopee: null }), null);
  // canal sem venda nao precisa de proporcao
  assert.deepEqual(divisaoFull({ total: 1000, ml: 1000, shopee: null }, { ml: 0.3, shopee: null }), { full: 300, normal: 700 });
  assert.equal(divisaoFull({ total: 1000, ml: 1000, shopee: null }, { ml: 0, shopee: null }), null);
});

test("fullDaMarca: divide cada canal pela proporcao; TikTok e' sem Full; canal sem proporcao e' sem dado", async () => {
  const { fullDaMarca } = await import("../src/lib/gerencial/divisao-full.ts");
  assert.deepEqual(fullDaMarca({ ml: 1000, shopee: 400, tiktok: 300 }, { ml: 0.8, shopee: null }), {
    full: { ml: 800 },
    semFull: { ml: 200, tiktok: 300 },
    semDado: { shopee: 400 },
  });
  assert.deepEqual(fullDaMarca({ ml: 0, shopee: 100 }, { shopee: 1 }), { full: { shopee: 100 }, semFull: {}, semDado: {} });
});
