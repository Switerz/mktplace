// Testes da logica de padronizacao da pagina Produtos (troca de marketplace
// e filtro por bucket Pareto via clique nos cards).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  brandsForTab,
  brandSurvivesTabChange,
  toggleBucketSelection,
  zeroGmvNote,
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
