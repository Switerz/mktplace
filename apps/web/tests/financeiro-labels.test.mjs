// Testes estaticos (sem dependencias novas) que garantem que a tela Financeiro
// nao volte a exibir rotulos semanticamente incorretos identificados na auditoria
// de 2026-07-01 (docs/sections/financeiro_audit.md, secao 11).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SOURCE_PATH = path.join(__dirname, "..", "app", "financeiro", "page.tsx");
const source = readFileSync(SOURCE_PATH, "utf-8");

test("nao exibe mais rotulos de settlement/liquidacao da Shopee", () => {
  assert.doesNotMatch(source, /Receita Liquida Shopee/);
  assert.doesNotMatch(source, /Liq\.\s*%/);
  assert.doesNotMatch(source, /fees positivos na fonte Shopee/);
});

test("Shopee expõe o valor renomeado para Total Global (pedidos)", () => {
  assert.match(source, /Total Global \(pedidos\)/);
  assert.match(source, /Taxas e encargos Shopee/);
});

test("ML nao chama mais o indicador de 'Custo Total'", () => {
  assert.doesNotMatch(source, /Custo Total/);
  assert.match(source, /Ads \+ Frete \/ GMV/);
  assert.match(source, /Nao inclui comissao do Mercado Livre/i);
});

test("TikTok nao afirma mais comissao de afiliados e usa nomenclatura de repasse", () => {
  assert.doesNotMatch(source, /comissao de afiliados/i);
  assert.doesNotMatch(source, /Receita Liquida TikTok/);
  assert.doesNotMatch(source, /Taxa Media TikTok/);
  assert.match(source, /Repasse recebido TikTok/);
  assert.match(source, /Taxas e encargos \/ GMV/);
});

test("TikTok expõe aviso de competencia diferente entre repasse e GMV", () => {
  assert.match(source, /competencias? (podem|diferentes)/i);
});

test("secao TikTok chama-se 'Repasses TikTok', nao mais 'Liquidacao TikTok'", () => {
  assert.doesNotMatch(source, /Liquidacao TikTok/);
  assert.match(source, /Repasses TikTok/);
});
