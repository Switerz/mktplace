// Testes da identidade de requisicao de Qualidade (Gate U5) — mesmo padrao
// "Finding 2" ja adotado em Canais/Financeiro/Regioes (Gate U3/U4). Cobre
// todos os filtros relevantes (canais, marcas, datas, compare, retry).
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildQualityRequestKey } from "../src/lib/quality-request-key.ts";

const BASE = { channels: ["ml"], brands: ["barbours"], dateFrom: "2026-06-01", dateTo: "2026-06-30", compare: false, retryKey: 0 };

test("identidade muda com canais", () => {
  const a = buildQualityRequestKey(BASE);
  const b = buildQualityRequestKey({ ...BASE, channels: ["ml", "shopee"] });
  assert.notEqual(a, b);
});

test("identidade muda com marcas", () => {
  const a = buildQualityRequestKey(BASE);
  const b = buildQualityRequestKey({ ...BASE, brands: ["kokeshi"] });
  assert.notEqual(a, b);
});

test("identidade muda com datas", () => {
  const a = buildQualityRequestKey(BASE);
  const b = buildQualityRequestKey({ ...BASE, dateTo: "2026-07-01" });
  assert.notEqual(a, b);
});

test("identidade muda com compare", () => {
  const a = buildQualityRequestKey(BASE);
  const b = buildQualityRequestKey({ ...BASE, compare: true });
  assert.notEqual(a, b);
});

test("identidade muda com retryKey (permite forcar refetch sem mudar filtros)", () => {
  const a = buildQualityRequestKey(BASE);
  const b = buildQualityRequestKey({ ...BASE, retryKey: 1 });
  assert.notEqual(a, b);
});

test("mesma combinacao de filtros produz sempre a mesma chave", () => {
  assert.equal(buildQualityRequestKey(BASE), buildQualityRequestKey({ ...BASE }));
});
