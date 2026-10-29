// SHOPEE-PRODUCTS-TRUTH-KOKESHI-2 — verdade visual do selo de Produtos Shopee.
//
// Fixtures recortadas do payload REAL medido no Neon em 13/10/2026 (somente
// leitura): agosto "todas" (kokeshi com 63 linhas de GMV zero), setembro so
// kokeshi (nenhuma linha) e julho apice (completo).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

import { partialTotalNote, scopeUnavailableReason } from "../src/lib/scope-quality-display.ts";

function readSource(...segments: string[]): string {
  return readFileSync(fileURLToPath(new URL(`../${segments.join("/")}`, import.meta.url)), "utf8");
}

const api = (brand: string, published = "2026-10-13T14:53:54+00:00") => ({
  brand, active_source: "api", expected: true, status: "with_data" as const,
  rows_present: 100, eligible_rows: 100, published_at: published, published_in_scope: true,
});

const AGO_TODAS = {
  brand: null, load_status: "load_present", eligibility_status: "partially_eligible",
  brand_coverage: {
    brands: [api("apice"), api("barbours"),
      { brand: "kokeshi", active_source: "manual_export", expected: true, status: "zero_value" as const,
        rows_present: 63, eligible_rows: 0, published_at: "2026-08-05T14:29:05+00:00", published_in_scope: true },
      api("lescent"), api("rituaria")],
    expected: ["apice", "barbours", "kokeshi", "lescent", "rituaria"],
    with_data: ["apice", "barbours", "lescent", "rituaria"],
    missing: ["kokeshi"], total_is_partial: true,
    oldest_published_at: "2026-08-05T14:29:05+00:00", oldest_published_brands: ["kokeshi"],
    newest_published_at: "2026-10-13T14:53:54+00:00",
  },
} as never;

const SET_KOKESHI = {
  brand: "kokeshi", load_status: "load_absent", eligibility_status: "no_eligible_rows",
  brand_coverage: { brands: [], expected: ["kokeshi"], with_data: [], missing: ["kokeshi"],
    total_is_partial: true, oldest_published_at: null, oldest_published_brands: [], newest_published_at: null },
} as never;

const AGO_KOKESHI = { brand: "kokeshi", load_status: "load_behind_daily", eligibility_status: "no_eligible_rows" } as never;
const JUL_APICE = { brand: "apice", load_status: "load_present", eligibility_status: "eligible", brand_coverage: null } as never;

test("ausencia nunca vira zero: sem linha ou so GMV zero tem motivo explicito", () => {
  assert.match(scopeUnavailableReason(SET_KOKESHI) ?? "", /ausência de dado, não venda zero/);
  assert.match(scopeUnavailableReason(AGO_KOKESHI) ?? "", /GMV zero/);
  assert.equal(scopeUnavailableReason(JUL_APICE), null);
  assert.equal(scopeUnavailableReason(AGO_TODAS), null, "o total parcial TEM valor: so e parcial");
  assert.equal(scopeUnavailableReason(null), null, "selo nao medido nao e ausencia afirmada");
});

test("total de todas as marcas com marca esperada ausente e rotulado PARCIAL", () => {
  assert.equal(partialTotalNote(AGO_TODAS), "Total PARCIAL — fora do total: kokeshi.");
  assert.equal(partialTotalNote(JUL_APICE), null);
  // No filtro de uma marca o aviso e de ausencia, nao de total parcial.
  assert.equal(partialTotalNote(SET_KOKESHI), null);
});

test("o selo titula o total parcial e mostra o detalhe por marca", () => {
  const src = readSource("src", "components", "ScopeQualityBanner.tsx");
  assert.match(src, /Total PARCIAL — fora do total/);
  assert.match(src, /Marcas com dados: .* esperadas: .* ausentes:/);
  assert.match(src, /Fonte ativa/);
  assert.match(src, /total_is_partial/);
  // total parcial nunca cai no caminho calmo "madura nos seis eixos"
  assert.match(src, /calmo = quality\.definitive && pior === "info" && !parcial/);
});

test("a data geral mostra a publicacao mais antiga e quem e", () => {
  const src = readSource("src", "components", "ScopeQualityBanner.tsx");
  assert.match(src, /Publicado no mart entre \$\{antiga\} \(mais antiga: /);
  assert.match(src, /oldest_published_brands/);
});

test("a tela troca os cards e o contador pela ausencia, sem recalcular nada", () => {
  const page = readSource("app", "produtos", "page.tsx");
  assert.match(page, /unavailableReason=\{shUnavailable\}/);
  assert.match(page, /ProductCount total=\{shUnavailable \? null :/);
  // nem "0–0 de 0 produtos" no escopo, nem "63 produtos sem GMV" na nota
  assert.match(page, /total: shUnavailable \? null : shDisplayData\?\.total/);
  assert.match(page, /shUnavailable \? null : semGmv\(/);
  const cards = readSource("src", "components", "ProductParetoSummary.tsx");
  assert.match(cards, /if \(unavailableReason\)/);
  assert.match(cards, /data-testid="pareto-unavailable"/);
});
