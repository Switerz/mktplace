// Testes da agregacao diaria do Brand Detail (apps/web/src/lib/brand-daily-summary.ts).
// Cobre especificamente a selecao isolada de Shopee (Ponto 1 da correcao:
// Shopee nao pode aparecer com tendencia zerada/incompleta quando ha dado real).
import { test } from "node:test";
import assert from "node:assert/strict";
import { summarize } from "../src/lib/brand-daily-summary.ts";
import type { DailyRow } from "../src/lib/mock-daily.ts";

function row(overrides: Partial<DailyRow>): DailyRow {
  return {
    date: "2026-06-01",
    tiktok_gmv: null,
    ml_gmv: null,
    shopee_gmv: null,
    total_gmv: 0,
    orders: 0,
    avg_ticket: null,
    ad_spend: null,
    ...overrides,
  };
}

const MIXED_ROWS: DailyRow[] = [
  row({ date: "2026-06-01", tiktok_gmv: 1000, ml_gmv: 500, shopee_gmv: 300, total_gmv: 1800, orders: 20, ad_spend: 50 }),
  row({ date: "2026-06-02", tiktok_gmv: 1100, ml_gmv: 600, shopee_gmv: 400, total_gmv: 2100, orders: 25, ad_spend: 60 }),
];

test("selecao isolada de Shopee: soma apenas shopee_gmv, nao reaproveita total_gmv (tiktok+ml+shopee)", () => {
  const result = summarize(MIXED_ROWS, ["shopee"]);
  assert.equal(result.gmv, 300 + 400); // NAO deve ser 1800+2100 (total_gmv inclui tiktok/ml)
  assert.equal(result.orders, 20 + 25); // orders ja vem filtrado por canal na API/mock upstream
});

test("selecao isolada de Shopee com ad_spend real: nao fica N/D", () => {
  const result = summarize(MIXED_ROWS, ["shopee"]);
  assert.equal(result.adSpend, 50 + 60);
  assert.ok(result.adSpend !== null);
});

test("selecao isolada de TikTok: soma apenas tiktok_gmv e nao inclui shopee", () => {
  const result = summarize(MIXED_ROWS, ["tiktok"]);
  assert.equal(result.gmv, 1000 + 1100);
  assert.equal(result.adSpend, null); // TikTok Shop nao tem ad spend rastreado
});

test("combinacao TikTok + Shopee: soma os dois, exclui ML", () => {
  const result = summarize(MIXED_ROWS, ["tiktok", "shopee"]);
  assert.equal(result.gmv, (1000 + 300) + (1100 + 400));
});

test("combinacao ML + Shopee: soma os dois, exclui TikTok", () => {
  const result = summarize(MIXED_ROWS, ["ml", "shopee"]);
  assert.equal(result.gmv, (500 + 300) + (600 + 400));
});

test("todos os canais selecionados: soma bate com total_gmv das linhas", () => {
  const result = summarize(MIXED_ROWS, ["tiktok", "ml", "shopee"]);
  const expectedTotal = MIXED_ROWS.reduce((s, r) => s + r.total_gmv, 0);
  assert.equal(result.gmv, expectedTotal);
});

test("Shopee selecionado mas sem dado no periodo (shopee_gmv null em todas as linhas): gmv fica 0, nao mascarado por outro canal", () => {
  const rowsNoShopee: DailyRow[] = [
    row({ tiktok_gmv: 1000, ml_gmv: 500, shopee_gmv: null, total_gmv: 1500, orders: 10 }),
  ];
  const result = summarize(rowsNoShopee, ["shopee"]);
  assert.equal(result.gmv, 0); // zero real (canal sem venda no periodo), nao 1500 do total
});

test("ticket medio da selecao isolada de Shopee usa gmv/orders da propria selecao", () => {
  const result = summarize(MIXED_ROWS, ["shopee"]);
  assert.equal(result.avgTicket, result.gmv / result.orders);
});
