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

// UX-TORRE-B2B-1: os KPIs viraram um painel POR CANAL (`ChannelCostPanel`),
// e o canal passou a vir do titulo do painel em vez de um sufixo no rotulo
// ("Taxas e encargos Shopee" -> linha "Taxas e encargos" do painel Shopee).
// As afirmacoes travadas sao as mesmas; o recorte abaixo garante que cada
// rotulo esta no painel do canal certo.
const painel = (titulo) => {
  const i = source.indexOf(`titulo="${titulo}"`);
  assert.ok(i > 0, `painel ${titulo} ausente`);
  const fim = source.indexOf("<ChannelCostPanel", i + 1);
  return source.slice(i, fim > 0 ? fim : source.indexOf("Margem ainda não fechada", i));
};

test("Shopee expõe o valor renomeado para Total Global (pedidos)", () => {
  assert.match(source, /Total Global \(pedidos\)/);
  assert.match(painel("Shopee"), /rotulo: "Taxas e encargos"/);
});

test("ML nao chama mais o indicador de 'Custo Total'", () => {
  assert.doesNotMatch(source, /Custo Total/);
  assert.match(source, /Ads \+ Frete \/ GMV/);
  // Antes: "Nao inclui comissao do Mercado Livre". Agora a tarifa e' exibida
  // (MARGEM-REAL-2) e o indicador declara que NAO a inclui.
  assert.match(painel("Mercado Livre"), /Não inclui a tarifa ML/);
  assert.match(source, /n[aã]o inclui a tarifa do Mercado Livre/i);
  // A tarifa nunca e' apresentada como margem.
  assert.match(painel("Mercado Livre"), /rotulo: "Tarifa ML"/);
  assert.match(source, /n[aã]o (e'|é) margem/);
  assert.doesNotMatch(source, /margem real/i);
});

test("TikTok nao afirma mais comissao de afiliados e usa nomenclatura de repasse", () => {
  assert.doesNotMatch(source, /comissao de afiliados/i);
  assert.doesNotMatch(source, /Receita Liquida TikTok/);
  assert.doesNotMatch(source, /Taxa Media TikTok/);
  assert.match(painel("TikTok Shop"), /rotulo: "Repasse recebido"/);
  assert.match(painel("TikTok Shop"), /rotulo: "Taxas e encargos"/);
  assert.match(source, /Taxas e encargos \/ GMV/);
});

test("TikTok expõe aviso de competencia diferente entre repasse e GMV", () => {
  assert.match(source, /compet[eê]ncias? (podem|diferentes)/i);
});

test("secao TikTok chama-se 'Repasses TikTok', nao mais 'Liquidacao TikTok'", () => {
  assert.doesNotMatch(source, /Liquidacao TikTok/);
  assert.match(source, /Repasses TikTok/);
});
