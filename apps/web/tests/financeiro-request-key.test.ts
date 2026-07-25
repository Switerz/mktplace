// Testes da identidade de requisicao de Financeiro (Gate U4) — mesmo padrao
// "Finding 2" do Gate U3 (Canais). Cobre todos os filtros relevantes
// (canais, marcas, datas, compare, retry).
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildFinanceiroRequestKey } from "../src/lib/financeiro-request-key.ts";

const BASE = { channels: ["ml"], brands: ["barbours"], dateFrom: "2026-06-01", dateTo: "2026-06-30", compare: false, retryKey: 0 };

test("identidade muda com canais", () => {
  const a = buildFinanceiroRequestKey(BASE);
  const b = buildFinanceiroRequestKey({ ...BASE, channels: ["ml", "shopee"] });
  assert.notEqual(a, b);
});

test("identidade muda com marcas", () => {
  const a = buildFinanceiroRequestKey(BASE);
  const b = buildFinanceiroRequestKey({ ...BASE, brands: ["kokeshi"] });
  assert.notEqual(a, b);
});

test("identidade muda com datas", () => {
  const a = buildFinanceiroRequestKey(BASE);
  const b = buildFinanceiroRequestKey({ ...BASE, dateTo: "2026-07-01" });
  assert.notEqual(a, b);
});

test("identidade muda com compare", () => {
  const a = buildFinanceiroRequestKey(BASE);
  const b = buildFinanceiroRequestKey({ ...BASE, compare: true });
  assert.notEqual(a, b);
});

test("identidade muda com retryKey (permite forcar refetch sem mudar filtros)", () => {
  const a = buildFinanceiroRequestKey(BASE);
  const b = buildFinanceiroRequestKey({ ...BASE, retryKey: 1 });
  assert.notEqual(a, b);
});

test("mesma combinacao de filtros produz sempre a mesma chave", () => {
  assert.equal(buildFinanceiroRequestKey(BASE), buildFinanceiroRequestKey({ ...BASE }));
});
