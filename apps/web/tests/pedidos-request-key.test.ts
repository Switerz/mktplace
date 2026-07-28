// Testes da identidade de requisicao e da cobertura Shopee de Pedidos
// (Gate U5). Cobre a chave (canais, marcas, datas, retry — sem `compare`,
// que o endpoint nao aceita) e a decisao pura de cobertura Shopee (Task 3).
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildPedidosRequestKey, computePedidosCoverage } from "../src/lib/pedidos-request-key.ts";

const BASE = { channels: ["ml"], brands: ["barbours"], dateFrom: "2026-06-01", dateTo: "2026-06-30", retryKey: 0 };

test("identidade muda com canais", () => {
  const a = buildPedidosRequestKey(BASE);
  const b = buildPedidosRequestKey({ ...BASE, channels: ["ml", "tiktok"] });
  assert.notEqual(a, b);
});

test("identidade muda com marcas", () => {
  const a = buildPedidosRequestKey(BASE);
  const b = buildPedidosRequestKey({ ...BASE, brands: ["kokeshi"] });
  assert.notEqual(a, b);
});

test("identidade muda com datas", () => {
  const a = buildPedidosRequestKey(BASE);
  const b = buildPedidosRequestKey({ ...BASE, dateTo: "2026-07-01" });
  assert.notEqual(a, b);
});

test("identidade muda com retryKey (permite forcar refetch sem mudar filtros)", () => {
  const a = buildPedidosRequestKey(BASE);
  const b = buildPedidosRequestKey({ ...BASE, retryKey: 1 });
  assert.notEqual(a, b);
});

test("mesma combinacao de filtros produz sempre a mesma chave", () => {
  assert.equal(buildPedidosRequestKey(BASE), buildPedidosRequestKey({ ...BASE }));
});

test("cobertura: Shopee isolada -> showShopeeOnly=true, showShopeeMixed=false", () => {
  const c = computePedidosCoverage({ showTiktok: false, showMl: false, showShopee: true });
  assert.equal(c.showShopeeOnly, true);
  assert.equal(c.showShopeeMixed, false);
});

test("cobertura: Shopee + TikTok -> showShopeeMixed=true, showShopeeOnly=false", () => {
  const c = computePedidosCoverage({ showTiktok: true, showMl: false, showShopee: true });
  assert.equal(c.showShopeeOnly, false);
  assert.equal(c.showShopeeMixed, true);
});

test("cobertura: Shopee + ML -> showShopeeMixed=true, showShopeeOnly=false", () => {
  const c = computePedidosCoverage({ showTiktok: false, showMl: true, showShopee: true });
  assert.equal(c.showShopeeOnly, false);
  assert.equal(c.showShopeeMixed, true);
});

test("cobertura: Shopee + TikTok + ML (todos) -> showShopeeMixed=true", () => {
  const c = computePedidosCoverage({ showTiktok: true, showMl: true, showShopee: true });
  assert.equal(c.showShopeeOnly, false);
  assert.equal(c.showShopeeMixed, true);
});

test("cobertura: sem Shopee selecionada -> ambos false, independente de TikTok/ML", () => {
  assert.deepEqual(computePedidosCoverage({ showTiktok: true, showMl: true, showShopee: false }), {
    showShopeeOnly: false, showShopeeMixed: false,
  });
  assert.deepEqual(computePedidosCoverage({ showTiktok: false, showMl: false, showShopee: false }), {
    showShopeeOnly: false, showShopeeMixed: false,
  });
});
