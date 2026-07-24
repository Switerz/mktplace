// Testes da decomposicao e metadados dos 4 KPIs com drill-down agregado
// (Gate U2 — ver docs/UI_REVAMP_PLAN.md Task 4/8; Finding 3 da rodada de
// correcao pre-commit). Cobre composicao por canal (selecao de
// marketplace, canal selecionado com null vs zero real, percentuais, total
// zero, canal unico, ausencia de NaN/Infinity, reconciliacao explicita com
// overview.gmv) e conteudo dos drill-downs (4 KPIs suportados, TikTok ROAS
// indisponivel, ticket com zero pedidos, definicao por canal, proximo
// destino).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  KPI_META,
  gmvChannelBreakdown,
  gmvBrandBreakdown,
  ordersBrandBreakdown,
  avgTicketBrandBreakdown,
  roasBreakdown,
  type KpiKind,
} from "../src/lib/kpi-drilldown.ts";
import type { OverviewData, BrandRow } from "../src/lib/api-client.ts";
import { ALL_MARKETPLACES } from "../src/lib/marketplace-filter.ts";

function makeOverview(overrides: Partial<OverviewData> = {}): OverviewData {
  return {
    gmv: 300,
    tiktok_gmv: 100,
    ml_gmv: 100,
    shopee_gmv: 100,
    orders: 30,
    avg_ticket: 10,
    ad_spend: null,
    ml_roas: null,
    ml_cancel_rate_pct: null,
    shopee_roas: null,
    tiktok_customers: null,
    ml_unique_buyers: null,
    shopee_unique_buyers: null,
    gmv_mom_pct: null,
    prev_gmv: 0,
    ...overrides,
  };
}

function makeBrand(overrides: Partial<BrandRow> = {}): BrandRow {
  return {
    brand: "barbours",
    label: "Barbours",
    tiktok_gmv: null,
    ml_gmv: null,
    shopee_gmv: null,
    total_gmv: 0,
    orders: 0,
    avg_ticket: null,
    tiktok_avg_ticket: null,
    ml_avg_ticket: null,
    tiktok_gmv_prev: null,
    ml_gmv_prev: null,
    shopee_gmv_prev: null,
    total_gmv_prev: 0,
    mom_pct: null,
    cos_pct: null,
    gpm: null,
    ml_roas: null,
    ml_cancel_rate_pct: null,
    ...overrides,
  };
}

// --- Composicao por canal (GMV) ---

test("gmvChannelBreakdown: reconciliacao explicita — soma dos canais numericos === overview.gmv", () => {
  const overview = makeOverview({ tiktok_gmv: 100, ml_gmv: 200, shopee_gmv: 700, gmv: 1000 });
  const shares = gmvChannelBreakdown(overview, ALL_MARKETPLACES);
  assert.equal(shares.length, 3);
  const sumValue = shares.reduce((s, r) => s + (r.value ?? 0), 0);
  assert.equal(sumValue, overview.gmv);
  const sumPct = shares.reduce((s, r) => s + (r.pct ?? 0), 0);
  assert.ok(Math.abs(sumPct - 100) < 0.001);
});

test("gmvChannelBreakdown: total zero retorna pct null, nunca NaN/Infinity", () => {
  const shares = gmvChannelBreakdown(makeOverview({ tiktok_gmv: 0, ml_gmv: 0, shopee_gmv: 0 }), ALL_MARKETPLACES);
  for (const s of shares) {
    assert.equal(s.pct, null);
    assert.ok(Number.isFinite(s.value as number));
  }
});

test("gmvChannelBreakdown: decomposicao respeita a selecao — canal nao selecionado nunca aparece", () => {
  const overview = makeOverview({ tiktok_gmv: 100, ml_gmv: 200, shopee_gmv: 700 });
  const shares = gmvChannelBreakdown(overview, ["tiktok", "ml"]);
  assert.equal(shares.length, 2);
  assert.ok(shares.every((s) => s.channel !== "shopee"));
});

test("gmvChannelBreakdown: canal selecionado com valor null aparece como indisponivel, nunca R$ 0", () => {
  const overview = makeOverview({ tiktok_gmv: 100, ml_gmv: null, shopee_gmv: 700 });
  const shares = gmvChannelBreakdown(overview, ALL_MARKETPLACES);
  const mlShare = shares.find((s) => s.channel === "ml");
  assert.ok(mlShare, "canal ML selecionado deve permanecer na lista");
  assert.equal(mlShare?.value, null);
  assert.equal(mlShare?.pct, null);
  // e nao deve ser confundido com um zero real
  assert.notEqual(mlShare?.value, 0);
});

test("gmvChannelBreakdown: canal selecionado com zero real permanece distinguivel de 'sem dado'", () => {
  const overview = makeOverview({ tiktok_gmv: 100, ml_gmv: 0, shopee_gmv: 900 });
  const shares = gmvChannelBreakdown(overview, ALL_MARKETPLACES);
  const mlShare = shares.find((s) => s.channel === "ml");
  assert.equal(mlShare?.value, 0);
  assert.equal(mlShare?.pct, 0);
  assert.notEqual(mlShare?.value, null);
});

test("gmvChannelBreakdown: canal unico selecionado numerico recebe 100%", () => {
  const overview = makeOverview({ tiktok_gmv: 100, ml_gmv: 500, shopee_gmv: 700 });
  const shares = gmvChannelBreakdown(overview, ["ml"]);
  assert.equal(shares.length, 1);
  assert.equal(shares[0].channel, "ml");
  assert.equal(shares[0].pct, 100);
});

test("gmvChannelBreakdown: canal nao selecionado com null tambem nao aparece (nao confundir os dois motivos de omissao)", () => {
  const overview = makeOverview({ tiktok_gmv: null, ml_gmv: 500, shopee_gmv: null });
  const shares = gmvChannelBreakdown(overview, ["ml"]);
  assert.equal(shares.length, 1);
  assert.equal(shares[0].channel, "ml");
});

test("gmvChannelBreakdown: nunca produz NaN ou Infinity mesmo com valores extremos ou nulls misturados", () => {
  const shares = gmvChannelBreakdown(makeOverview({ tiktok_gmv: 0, ml_gmv: null, shopee_gmv: 0.0001 }), ALL_MARKETPLACES);
  for (const s of shares) {
    assert.ok(s.value === null || Number.isFinite(s.value));
    assert.ok(s.pct === null || Number.isFinite(s.pct));
  }
});

test("gmvBrandBreakdown/ordersBrandBreakdown: reconciliam com o total e nao geram NaN", () => {
  const brands = [
    makeBrand({ brand: "a", total_gmv: 400, orders: 40 }),
    makeBrand({ brand: "b", total_gmv: 600, orders: 0 }),
  ];
  const gmvShares = gmvBrandBreakdown(brands);
  assert.equal(gmvShares.reduce((s, r) => s + r.value, 0), 1000);
  assert.equal(gmvShares[1].pct, 60);

  const orderShares = ordersBrandBreakdown(brands);
  assert.equal(orderShares[1].pct, 0);
  assert.ok(Number.isFinite(orderShares[1].pct as number));
});

test("gmvBrandBreakdown: lista vazia de marcas nao quebra (pct null, sem NaN)", () => {
  const shares = gmvBrandBreakdown([]);
  assert.deepEqual(shares, []);
});

// --- Ticket medio ---

test("avgTicketBrandBreakdown: pedidos zero -> ticket indisponivel (null), nunca NaN/Infinity", () => {
  const brands = [makeBrand({ brand: "a", orders: 0, avg_ticket: 999 })];
  const rows = avgTicketBrandBreakdown(brands);
  assert.equal(rows[0].avgTicket, null);
});

test("avgTicketBrandBreakdown: pedidos > 0 preserva o ticket informado", () => {
  const brands = [makeBrand({ brand: "a", orders: 10, avg_ticket: 55.5 })];
  const rows = avgTicketBrandBreakdown(brands);
  assert.equal(rows[0].avgTicket, 55.5);
});

// --- ROAS ---

test("roasBreakdown: TikTok sempre indisponivel; ML/Shopee refletem o dado real", () => {
  const r = roasBreakdown(makeOverview({ ml_roas: 8.2, shopee_roas: null }));
  assert.equal(r.tiktokAvailable, false);
  assert.equal(r.ml, 8.2);
  assert.equal(r.shopee, null);
});

// --- Conteudo/metadados dos 4 drill-downs ---

test("KPI_META cobre exatamente os 4 KPIs suportados", () => {
  const kinds: KpiKind[] = ["gmv", "orders", "avg_ticket", "roas"];
  assert.deepEqual(Object.keys(KPI_META).sort(), [...kinds].sort());
});

test("KPI_META.roas menciona TikTok como indisponivel na definicao/caveat", () => {
  const text = `${KPI_META.roas.definition} ${KPI_META.roas.caveat ?? ""}`;
  assert.match(text, /TikTok/);
  assert.match(text, /indispon[íi]vel|n[ãa]o (tem|dispon[íi]vel)/i);
});

test("KPI_META.avg_ticket trata pedidos zero explicitamente no caveat", () => {
  assert.match(KPI_META.avg_ticket.caveat ?? "", /pedido/i);
  assert.equal(KPI_META.avg_ticket.formula, "Ticket Médio = GMV / Pedidos");
});

test("KPI_META.gmv define a regra por canal (TikTok/ML/Shopee) sem usar formula generica", () => {
  assert.match(KPI_META.gmv.definition, /TikTok Shop/);
  assert.match(KPI_META.gmv.definition, /Mercado Livre/);
  assert.match(KPI_META.gmv.definition, /Shopee/);
});

test("proximo destino: GMV/Pedidos/Ticket apontam para Canais; ROAS aponta para Financeiro", () => {
  assert.equal(KPI_META.gmv.nextHref, "/canais");
  assert.equal(KPI_META.orders.nextHref, "/canais");
  assert.equal(KPI_META.avg_ticket.nextHref, "/canais");
  assert.equal(KPI_META.roas.nextHref, "/financeiro");
});
