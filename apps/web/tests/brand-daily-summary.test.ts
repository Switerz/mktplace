// Testes da agregacao diaria do Brand Detail (apps/web/src/lib/brand-daily-summary.ts).
// Cobre especificamente a selecao isolada de Shopee (Ponto 1 da correcao:
// Shopee nao pode aparecer com tendencia zerada/incompleta quando ha dado real).
import { test } from "node:test";
import assert from "node:assert/strict";
import { summarize, isOrdersReliable, projectDailyRowsBySelection } from "../src/lib/brand-daily-summary.ts";
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
  assert.equal(result.avgTicket, result.gmv! / result.orders!);
});

// isOrdersReliable / fallback mock da pagina de marca (Gate U3, Task 5) — o
// DailyRow mock (mock-daily.ts) gera `orders` combinando os 3 canais juntos,
// sem separacao por canal; so e confiavel ao vivo (API ja filtra) ou quando
// a selecao cobre os 3 canais (o total combinado bate com o selecionado).
test("isOrdersReliable: ao vivo, confiavel independente da selecao (API ja filtra por canal)", () => {
  assert.equal(isOrdersReliable(true, ["shopee"]), true);
  assert.equal(isOrdersReliable(true, ["tiktok", "ml"]), true);
});

test("isOrdersReliable: modo demonstracao com selecao parcial NAO e confiavel", () => {
  assert.equal(isOrdersReliable(false, ["shopee"]), false);
  assert.equal(isOrdersReliable(false, ["tiktok", "ml"]), false);
});

test("isOrdersReliable: modo demonstracao com os 3 canais selecionados e confiavel", () => {
  assert.equal(isOrdersReliable(false, ["tiktok", "ml", "shopee"]), true);
});

test("summarize com ordersReliable=false: pedidos e ticket medio ficam null, nunca o total combinado do mock", () => {
  const result = summarize(MIXED_ROWS, ["shopee"], false);
  assert.equal(result.orders, null);
  assert.equal(result.avgTicket, null);
  assert.equal(result.gmv, 300 + 400); // GMV por canal continua filtravel normalmente
});

test("summarize com ordersReliable=true (default, retrocompativel): comportamento identico ao anterior", () => {
  const result = summarize(MIXED_ROWS, ["tiktok", "ml", "shopee"]);
  assert.equal(result.orders, 45);
  assert.ok(result.avgTicket !== null);
});

// projectDailyRowsBySelection — mesma projecao usada pelo grafico de
// tendencia e pela tabela "Ultimos 7 Dias" (Gate U3, Finding 2). Garante que
// canais nao selecionados nunca vazam para total_gmv, inclusive em
// combinacoes parciais de 2 canais (o bug real: o patch anterior so cobria
// exatamente 1 canal selecionado).
test("projectDailyRowsBySelection: TikTok isolado zera ml/shopee e recalcula total_gmv so com tiktok", () => {
  const [projected] = projectDailyRowsBySelection(MIXED_ROWS, ["tiktok"]);
  assert.equal(projected.tiktok_gmv, 1000);
  assert.equal(projected.ml_gmv, null);
  assert.equal(projected.shopee_gmv, null);
  assert.equal(projected.total_gmv, 1000); // NAO 1800 (total_gmv original da linha)
});

test("projectDailyRowsBySelection: combinacao TikTok + Shopee exclui ML do total_gmv", () => {
  const [projected] = projectDailyRowsBySelection(MIXED_ROWS, ["tiktok", "shopee"]);
  assert.equal(projected.tiktok_gmv, 1000);
  assert.equal(projected.ml_gmv, null);
  assert.equal(projected.shopee_gmv, 300);
  assert.equal(projected.total_gmv, 1000 + 300); // NAO 1800 (que inclui ml_gmv=500)
});

test("projectDailyRowsBySelection: combinacao ML + Shopee exclui TikTok do total_gmv", () => {
  const [projected] = projectDailyRowsBySelection(MIXED_ROWS, ["ml", "shopee"]);
  assert.equal(projected.tiktok_gmv, null);
  assert.equal(projected.ml_gmv, 500);
  assert.equal(projected.shopee_gmv, 300);
  assert.equal(projected.total_gmv, 500 + 300); // NAO 1800 (que inclui tiktok_gmv=1000)
});

test("projectDailyRowsBySelection: canais excluidos nunca entram em total_gmv (2a linha, mesma checagem)", () => {
  const [, second] = projectDailyRowsBySelection(MIXED_ROWS, ["ml"]);
  assert.equal(second.tiktok_gmv, null);
  assert.equal(second.shopee_gmv, null);
  assert.equal(second.ml_gmv, 600);
  assert.equal(second.total_gmv, 600); // NAO 2100 (total_gmv original, que soma os 3 canais)
});

test("projectDailyRowsBySelection: GMV dos canais selecionados permanece correto (nao zera nem altera valor)", () => {
  const projected = projectDailyRowsBySelection(MIXED_ROWS, ["tiktok", "ml", "shopee"]);
  assert.equal(projected[0].tiktok_gmv, 1000);
  assert.equal(projected[0].ml_gmv, 500);
  assert.equal(projected[0].shopee_gmv, 300);
});

test("projectDailyRowsBySelection: selecao dos tres canais reconcilia com o total_gmv original das linhas", () => {
  const projected = projectDailyRowsBySelection(MIXED_ROWS, ["tiktok", "ml", "shopee"]);
  projected.forEach((r, i) => assert.equal(r.total_gmv, MIXED_ROWS[i].total_gmv));
});

test("projectDailyRowsBySelection: canal selecionado mas sem dado no periodo (null) fica null, nao vira zero fabricado no campo do canal", () => {
  const rowsNoShopee: DailyRow[] = [
    row({ tiktok_gmv: 1000, ml_gmv: 500, shopee_gmv: null, total_gmv: 1500, orders: 10 }),
  ];
  const [projected] = projectDailyRowsBySelection(rowsNoShopee, ["shopee"]);
  assert.equal(projected.shopee_gmv, null);
  assert.equal(projected.total_gmv, 0); // soma dos selecionados (so shopee, que e null) -> 0 real
});

test("projectDailyRowsBySelection: nao regride isOrdersReliable/summarize — GMV projetado bate com summarize() para a mesma selecao", () => {
  for (const filter of [["tiktok"], ["tiktok", "shopee"], ["ml", "shopee"], ["tiktok", "ml", "shopee"]] as const) {
    const projected = projectDailyRowsBySelection(MIXED_ROWS, filter as string[]);
    const projectedGmvTotal = projected.reduce((s, r) => s + r.total_gmv, 0);
    const summarized = summarize(MIXED_ROWS, filter as string[]);
    assert.equal(projectedGmvTotal, summarized.gmv, `divergencia para filtro ${filter.join(",")}`);
  }
});
