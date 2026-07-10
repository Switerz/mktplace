// Testes da tela Regioes (Gate 6D.1): formatacao/coverage e montagem de
// querystring. Roda via `node --test` com type-stripping nativo do Node.
// Nao importa api-client.ts diretamente: esse modulo importa mock-data.ts
// sem extensao de arquivo, que so resolve sob o bundler do Next — por isso
// a logica pura de querystring vive em regioes-query.ts (sem essa cadeia).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  fmtPctOrNA, coverageLabel, coverageBadgeClass, semCoberturaAviso, fmtShareOfTotalPct,
} from "../src/lib/regioes-format.ts";
import { buildRegioesQueryParams } from "../src/lib/regioes-query.ts";

// ---------------------------------------------------------------------------
// fmtPctOrNA — denominador nulo/ausente NUNCA vira "0%"
// ---------------------------------------------------------------------------
test("fmtPctOrNA: null vira 'N/A', nunca '0%'", () => {
  assert.equal(fmtPctOrNA(null), "N/A");
  assert.notEqual(fmtPctOrNA(null), "0%");
});

test("fmtPctOrNA: zero real (0%) e' diferente de null (N/A)", () => {
  assert.equal(fmtPctOrNA(0), "0.0%");
});

test("fmtPctOrNA: formata com 1 casa decimal", () => {
  assert.equal(fmtPctOrNA(42.259), "42.3%");
  assert.equal(fmtPctOrNA(100), "100.0%");
});

// ---------------------------------------------------------------------------
// coverageLabel / coverageBadgeClass
// ---------------------------------------------------------------------------
test("coverageLabel: mapeia os 4 niveis", () => {
  assert.equal(coverageLabel("ok"), "Cobertura OK");
  assert.equal(coverageLabel("partial"), "Cobertura parcial");
  assert.equal(coverageLabel("low"), "Cobertura baixa");
  assert.equal(coverageLabel("not_applicable"), "N/A");
});

test("coverageBadgeClass: nunca retorna vazio para nenhum nivel", () => {
  for (const level of ["ok", "partial", "low", "not_applicable"] as const) {
    assert.ok(coverageBadgeClass(level).length > 0);
  }
});

test("coverageBadgeClass: low e' visualmente mais forte (rose) que partial (amber)", () => {
  assert.match(coverageBadgeClass("low"), /rose/);
  assert.match(coverageBadgeClass("partial"), /amber/);
  assert.match(coverageBadgeClass("ok"), /emerald/);
});

// ---------------------------------------------------------------------------
// semCoberturaAviso — TikTok vira aviso explicito, nunca silencioso
// ---------------------------------------------------------------------------
test("semCoberturaAviso: vazio retorna null (sem aviso)", () => {
  assert.equal(semCoberturaAviso([]), null);
});

test("semCoberturaAviso: tiktok gera aviso mencionando TikTok Shop e 'nao significa venda zero'", () => {
  const aviso = semCoberturaAviso(["tiktok"]);
  assert.ok(aviso != null);
  assert.match(aviso, /TikTok Shop/);
  assert.match(aviso, /não significa venda zero/i);
});

test("semCoberturaAviso: nunca teria 'GMV=0' ou 'venda zero' sem qualificar como ausencia de dado", () => {
  const aviso = semCoberturaAviso(["tiktok"]);
  assert.ok(aviso != null);
  assert.doesNotMatch(aviso, /^TikTok.*R\$\s?0/);
});

// ---------------------------------------------------------------------------
// fmtShareOfTotalPct — denominador zero/negativo vira null (nunca 0%)
// ---------------------------------------------------------------------------
test("fmtShareOfTotalPct: total zero vira null", () => {
  assert.equal(fmtShareOfTotalPct(100, 0), null);
});

test("fmtShareOfTotalPct: calculo normal com 1 casa decimal", () => {
  assert.equal(fmtShareOfTotalPct(25, 100), 25.0);
  assert.equal(fmtShareOfTotalPct(1, 3), 33.3);
});

// ---------------------------------------------------------------------------
// buildRegioesQueryParams — montagem de querystring, pura
// ---------------------------------------------------------------------------
test("buildRegioesQueryParams: channels sempre presente", () => {
  const qs = buildRegioesQueryParams("all");
  assert.equal(qs.get("channels"), "all");
  assert.equal(qs.get("brands"), null);
  assert.equal(qs.get("date_from"), null);
  assert.equal(qs.get("uf"), null);
});

test("buildRegioesQueryParams: brands ordenadas e unidas por virgula", () => {
  const qs = buildRegioesQueryParams("ml", { brands: ["kokeshi", "barbours"] });
  assert.equal(qs.get("brands"), "barbours,kokeshi");
});

test("buildRegioesQueryParams: date_from/date_to so aparecem juntos", () => {
  const qs = buildRegioesQueryParams("all", { dateFrom: "2026-01-01", dateTo: "2026-01-31" });
  assert.equal(qs.get("date_from"), "2026-01-01");
  assert.equal(qs.get("date_to"), "2026-01-31");
});

test("buildRegioesQueryParams: uf ordenada e unida por virgula, so quando presente", () => {
  const qs = buildRegioesQueryParams("shopee", { uf: ["RJ", "SP"] });
  assert.equal(qs.get("uf"), "RJ,SP");
});

test("buildRegioesQueryParams: uf vazio nao aparece na querystring", () => {
  const qs = buildRegioesQueryParams("all", { uf: [] });
  assert.equal(qs.get("uf"), null);
});

test("buildRegioesQueryParams: combina todos os filtros ao mesmo tempo", () => {
  const qs = buildRegioesQueryParams("ml,shopee", {
    brands: ["barbours"], dateFrom: "2026-01-01", dateTo: "2026-01-31", uf: ["SP"],
  });
  assert.equal(qs.get("channels"), "ml,shopee");
  assert.equal(qs.get("brands"), "barbours");
  assert.equal(qs.get("date_from"), "2026-01-01");
  assert.equal(qs.get("date_to"), "2026-01-31");
  assert.equal(qs.get("uf"), "SP");
});
