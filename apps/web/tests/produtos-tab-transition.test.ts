// Testes da logica de padronizacao da pagina Produtos (troca de marketplace
// e filtro por bucket Pareto via clique nos cards).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  brandsForTab,
  brandSurvivesTabChange,
  toggleBucketSelection,
  zeroGmvNote,
  avgPriceNote,
  marginUnavailableNote,
  lastNMonths,
  ML_BRAND_VALUES,
  TK_SH_BRAND_VALUES,
} from "../src/lib/produtos-tab-transition.ts";

test("brandsForTab: ML nao inclui apice; TikTok/Shopee incluem", () => {
  assert.deepEqual(brandsForTab("ml"), ML_BRAND_VALUES);
  assert.ok(!brandsForTab("ml").includes("apice"));
  assert.deepEqual(brandsForTab("tiktok"), TK_SH_BRAND_VALUES);
  assert.deepEqual(brandsForTab("shopee"), TK_SH_BRAND_VALUES);
  assert.ok(brandsForTab("tiktok").includes("apice"));
});

test("brandSurvivesTabChange: nenhuma marca selecionada sempre sobrevive", () => {
  assert.ok(brandSurvivesTabChange("", "ml"));
  assert.ok(brandSurvivesTabChange("", "tiktok"));
});

test("brandSurvivesTabChange: marca valida nos dois canais sobrevive (ex: barbours ML -> TikTok)", () => {
  assert.ok(brandSurvivesTabChange("barbours", "tiktok"));
  assert.ok(brandSurvivesTabChange("barbours", "shopee"));
  assert.ok(brandSurvivesTabChange("barbours", "ml"));
});

test("brandSurvivesTabChange: marca invalida no novo canal e resetada (apice TikTok -> ML)", () => {
  assert.equal(brandSurvivesTabChange("apice", "ml"), false);
});

test("brandSurvivesTabChange: apice sobrevive entre TikTok e Shopee (ambos aceitam)", () => {
  assert.ok(brandSurvivesTabChange("apice", "shopee"));
  assert.ok(brandSurvivesTabChange("apice", "tiktok"));
});

test("toggleBucketSelection: clicar em bucket novo troca o filtro", () => {
  assert.equal(toggleBucketSelection(null, "A_top50"), "A_top50");
  assert.equal(toggleBucketSelection("A_top50", "B_next30"), "B_next30");
});

test("toggleBucketSelection: clicar de novo no bucket ja ativo remove o filtro (equivalente a 'Todos')", () => {
  assert.equal(toggleBucketSelection("A_top50", "A_top50"), null);
});

test("zeroGmvNote: sem exclusao (0/undefined) nao mostra nota", () => {
  assert.equal(zeroGmvNote(0), "");
  assert.equal(zeroGmvNote(undefined), "");
});

test("zeroGmvNote: 1 produto excluido usa singular", () => {
  assert.match(zeroGmvNote(1), /1 produto sem GMV está fora dos buckets/);
});

test("zeroGmvNote: mais de 1 produto excluido usa plural", () => {
  assert.match(zeroGmvNote(239), /239 produtos sem GMV estão fora dos buckets/);
});

test("avgPriceNote: null/undefined nao mostra nota (dirigido pela API, nunca hardcoded)", () => {
  assert.equal(avgPriceNote(null), "");
  assert.equal(avgPriceNote(undefined), "");
});

test("avgPriceNote: valor presente formata como moeda BRL cheia", () => {
  assert.match(avgPriceNote(123.456), /R\$\s*123,46/);
});

test("marginUnavailableNote: ML menciona ROAS/ACOS, nao 'margem' calculavel", () => {
  const note = marginUnavailableNote("ml");
  assert.match(note, /Margem real indisponível/);
  assert.match(note, /ROAS\/ACOS/);
});

test("marginUnavailableNote: TikTok e Shopee bloqueiam qualquer eficiencia de ads por produto", () => {
  assert.match(marginUnavailableNote("tiktok"), /indisponível nesta fonte/);
  assert.match(marginUnavailableNote("shopee"), /indisponível nesta fonte/);
});

test("lastNMonths: mes atual e o primeiro da lista e leva o sufixo '(atual)'", () => {
  const months = lastNMonths(3, new Date(2026, 5, 15)); // Jun/2026 (mes 0-indexado)
  assert.deepEqual(months.map((m) => m.value), ["2026-06", "2026-05", "2026-04"]);
  assert.equal(months[0].label, "Jun/26 (atual)");
  assert.equal(months[1].label, "Mai/26");
});

test("lastNMonths: atravessa virada de ano corretamente", () => {
  const months = lastNMonths(3, new Date(2026, 0, 10)); // Jan/2026
  assert.deepEqual(months.map((m) => m.value), ["2026-01", "2025-12", "2025-11"]);
});
