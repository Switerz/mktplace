/**
 * GERENCIAL-METAS-1 — progresso contra a meta mensal (src/lib/gerencial/meta-progress.ts).
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  avisoQueAfetaOGmv,
  topComerciais,
  diasNoMes,
  mesDaMeta,
  metaDoEscopo,
  progressoMeta,
  ultimoDiaComDado,
} from "../src/lib/gerencial/meta-progress.ts";

const HOJE = "2026-10-29";

test("mesDaMeta: so' mes unico, do dia 1, inteiro ou corrente ate' hoje", () => {
  assert.equal(mesDaMeta("2026-09-01", "2026-09-30", HOJE), "2026-09");
  assert.equal(mesDaMeta("2026-10-01", "2026-10-29", HOJE), "2026-10");
  assert.equal(mesDaMeta("2026-10-01", "2026-10-31", HOJE), "2026-10");
  // parcial de mes passado, nao comeca no dia 1, ou atravessa meses: sem meta
  assert.equal(mesDaMeta("2026-09-01", "2026-09-15", HOJE), null);
  assert.equal(mesDaMeta("2026-10-02", "2026-10-29", HOJE), null);
  assert.equal(mesDaMeta("2026-09-01", "2026-10-29", HOJE), null);
  // mes corrente parado antes de hoje (ex.: 01 a 20/10) tambem nao
  assert.equal(mesDaMeta("2026-10-01", "2026-10-20", HOJE), null);
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

test("topComerciais: por severidade, ordem da fila no empate, ate' 3", () => {
  const c = [
    { key: "a", severity: "info" as const },
    { key: "b", severity: "warning" as const },
    { key: "c", severity: "critical" as const },
    { key: "d", severity: "warning" as const },
  ];
  assert.deepEqual(topComerciais(c).map((x) => x.key), ["c", "b", "d"]);
  assert.deepEqual(topComerciais(c, 10).map((x) => x.key), ["c", "b", "d", "a"]);
  assert.deepEqual(topComerciais([]), []);
});

test("avisoQueAfetaOGmv: so' aviso de atencao da fonte do GMV", () => {
  const regiao = { key: "r", severity: "attention" as const, source: "fact_marketplace_region_daily" };
  const gmv = { key: "g", severity: "attention" as const, source: "fact_marketplace_daily_performance" };
  const nota = { key: "n", severity: "note" as const, source: "fact_marketplace_daily_performance" };
  assert.equal(avisoQueAfetaOGmv([regiao, nota]), null);
  assert.equal(avisoQueAfetaOGmv([regiao, nota, gmv])?.key, "g");
  assert.equal(avisoQueAfetaOGmv([{ key: "x", severity: "attention" as const, source: null }]), null);
});
