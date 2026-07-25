// Testes da linha compacta "Escopo atual" da aba Produtos (Gate U4).
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatProdutosScope } from "../src/lib/produtos-scope.ts";

test("ML: sem periodo mensal — mostra 'ranking acumulado atual'", () => {
  const line = formatProdutosScope({
    channelLabel: "Mercado Livre", brandLabel: "Todas as marcas", periodLabel: null,
    total: 1326, offset: 0, limit: 25,
  });
  assert.equal(line, "Mercado Livre · Todas as marcas · ranking acumulado atual · 1–25 de 1326 produtos");
});

test("TikTok/Shopee: usa o rotulo de periodo informado, nunca o texto de ML", () => {
  const line = formatProdutosScope({
    channelLabel: "TikTok Shop", brandLabel: "BARBOURS", periodLabel: "Jul/26 (atual)",
    total: 340, offset: 25, limit: 25,
  });
  assert.equal(line, "TikTok Shop · BARBOURS · Jul/26 (atual) · 26–50 de 340 produtos");
});

test("total ainda null (API sem resposta) nao inventa contagem", () => {
  const line = formatProdutosScope({
    channelLabel: "Shopee", brandLabel: "Todas as marcas", periodLabel: "Jun/26", total: null, offset: 0, limit: 25,
  });
  assert.equal(line, "Shopee · Todas as marcas · Jun/26");
});

test("total=0 mostra intervalo 0–0, nao 1–0", () => {
  const line = formatProdutosScope({
    channelLabel: "Shopee", brandLabel: "RITUARIA", periodLabel: "Jun/26", total: 0, offset: 0, limit: 25,
  });
  assert.equal(line, "Shopee · RITUARIA · Jun/26 · 0–0 de 0 produtos");
});

test("ultima pagina parcial trunca o 'ate' no total, nao no offset+limit", () => {
  const line = formatProdutosScope({
    channelLabel: "Mercado Livre", brandLabel: "Todas as marcas", periodLabel: null,
    total: 110, offset: 100, limit: 25,
  });
  assert.equal(line, "Mercado Livre · Todas as marcas · ranking acumulado atual · 101–110 de 110 produtos");
});
