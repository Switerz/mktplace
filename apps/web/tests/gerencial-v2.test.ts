/**
 * Regressao dos contratos da Gerencial V2 (Gate V2-1, Task M).
 *
 * Tudo aqui e' logica PURA — os modulos de `src/lib/gerencial/*` foram escritos
 * para nao depender de React justamente para poderem ser testados com
 * `node:test`. Os poucos invariantes que so' existem no wiring (JSX/hook) sao
 * verificados por leitura de codigo-fonte, no mesmo padrao dos testes estaticos
 * dos gates U4/U5/G2 — o harness atual nao renderiza React.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import {
  buildChannelSeriesKey,
  buildGerencialRequestKey,
  buildGerencialViewKey,
  type GerencialKeyInput,
} from "../src/lib/gerencial/request-key.ts";
import {
  bucketRange,
  gmvTolerance,
  mergeChannelSeries,
  reconcileSeriesTotal,
  type ChannelSeries,
} from "../src/lib/gerencial/trend-series.ts";
import { adsCoverageNote, avgTicketDisplay, buildKpiBand } from "../src/lib/gerencial/kpi-band.ts";
import {
  buildVolumeHealth,
  ORDERS_CONSIDERED_LABEL,
  ORDERS_REGISTERED_LABEL,
  volumeHealthHasAnyRate,
} from "../src/lib/gerencial/volume-health.ts";
import {
  MOVEMENT_MIN_PREV_BASE,
  buildBrandChannelMatrix,
  buildConcentration,
  buildMovements,
} from "../src/lib/gerencial/brand-matrix.ts";
import {
  buildAttentionQueue,
  buildConfidenceStrip,
  seriesAvailability,
} from "../src/lib/gerencial/attention.ts";
import {
  classifyFallbackSource,
  decideDemoMode,
  type AggregateFallbackSource,
  type DemoModeInput,
  type FallbackSourceState,
} from "../src/lib/gerencial/demo-mode.ts";
import {
  UNKNOWN_SIGNAL_LABEL,
  isUnmappedSignal,
  signalLabel,
} from "../src/lib/canais-channel-metrics.ts";
import type { BrandRow, OverviewData, QualityBrandRow, QualityKpis, TrendPoint } from "../src/lib/api-client.ts";
import type { Marketplace } from "../src/lib/mock-data.ts";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf-8");

/**
 * Remove comentarios de bloco e de linha antes de asseverar sobre o codigo.
 *
 * Sem isso, uma proibicao se volta contra a propria documentacao: o comentario
 * que EXPLICA por que nao usamos `items-start` ou `Promise.all` contem o termo
 * proibido e reprovaria o arquivo correto. A proibicao vale para CODIGO.
 */
function codeOnly(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .map((line) => {
      const trimmed = line.trim();
      if (trimmed.startsWith("//") || trimmed.startsWith("*")) return "";
      return line.replace(/\/\/.*$/, "");
    })
    .join("\n");
}

const KEY: GerencialKeyInput = {
  channels: ["tiktok", "ml", "shopee"],
  brands: [],
  dateFrom: "2026-07-01",
  dateTo: "2026-07-03",
  compare: true,
  retryKey: 0,
};

function point(date: string, gmv: number, orders: number): TrendPoint {
  return { date, label: date.slice(8), gmv, orders };
}

function series(channel: Marketplace, points: TrendPoint[], status: ChannelSeries["status"] = "fresh"): ChannelSeries {
  return { channel, status, granularity: "day", points };
}

// ---------------------------------------------------------------------------
// 1. Uniao de buckets
// ---------------------------------------------------------------------------

test("1. uniao de buckets cobre todas as datas de todos os canais, ordenadas", () => {
  const merged = mergeChannelSeries(
    [
      series("ml", [point("2026-07-03", 30, 3), point("2026-07-01", 10, 1)]),
      series("shopee", [point("2026-07-02", 20, 2)]),
    ],
    ["ml", "shopee"],
    "gmv",
  );
  assert.deepEqual(
    merged.buckets.map((b) => b.date),
    ["2026-07-01", "2026-07-02", "2026-07-03"],
  );
});

// ---------------------------------------------------------------------------
// 2. null != zero
// ---------------------------------------------------------------------------

test("2. bucket ausente e' null (lacuna); zero explicito permanece zero", () => {
  const merged = mergeChannelSeries(
    [
      series("ml", [point("2026-07-01", 0, 0)]), // zero EXPLICITO
      series("shopee", []), // nenhuma linha: lacuna
    ],
    ["ml", "shopee"],
    "gmv",
  );
  const b = merged.buckets[0];
  assert.equal(b.values.ml, 0, "zero explícito não pode virar null");
  assert.equal(b.values.shopee, null, "ausência não pode virar zero");
});

// ---------------------------------------------------------------------------
// 3. Total somente com series completas
// ---------------------------------------------------------------------------

test("3. total do bucket so' existe quando todos os canais selecionados tem numero", () => {
  const merged = mergeChannelSeries(
    [
      series("ml", [point("2026-07-01", 10, 1), point("2026-07-02", 20, 2)]),
      series("shopee", [point("2026-07-01", 5, 1)]), // falta 07-02
    ],
    ["ml", "shopee"],
    "gmv",
  );
  assert.equal(merged.buckets[0].total, 15);
  assert.equal(merged.buckets[1].total, null, "bucket incompleto não pode ter total");
  assert.equal(merged.everyBucketComplete, false);
  assert.equal(merged.seriesTotal, null, "sem todos os buckets completos, não há total de série");
});

test("3b. canal selecionado sem entrada de serie conta como carregando e impede o total", () => {
  const merged = mergeChannelSeries(
    [series("ml", [point("2026-07-01", 10, 1)])],
    ["ml", "shopee"], // shopee selecionado mas sem serie
    "gmv",
  );
  assert.deepEqual(merged.loadingChannels, ["shopee"]);
  assert.equal(merged.allChannelsFresh, false);
  assert.equal(merged.buckets[0].total, null);
});

// ---------------------------------------------------------------------------
// 4 e 5. Reconciliacao
// ---------------------------------------------------------------------------

test("4. reconciliacao de GMV admite centavos e reprova divergencia real", () => {
  const merged = mergeChannelSeries(
    [series("ml", [point("2026-07-01", 100.0, 1), point("2026-07-02", 200.0, 2)])],
    ["ml"],
    "gmv",
  );
  assert.equal(merged.seriesTotal, 300);
  assert.equal(reconcileSeriesTotal(merged, "gmv", 300.01).status, "ok", "1 centavo está na tolerância");
  const bad = reconcileSeriesTotal(merged, "gmv", 310);
  assert.equal(bad.status, "mismatch");
  if (bad.status === "mismatch") {
    assert.equal(bad.diff, -10);
    assert.ok(bad.tolerance < 1, "tolerância é de centavos, não de reais");
  }
});

test("5. reconciliacao de Pedidos exige igualdade inteira", () => {
  const merged = mergeChannelSeries(
    [series("ml", [point("2026-07-01", 100, 3), point("2026-07-02", 200, 4)])],
    ["ml"],
    "orders",
  );
  assert.equal(merged.seriesTotal, 7);
  assert.equal(reconcileSeriesTotal(merged, "orders", 7).status, "ok");
  assert.equal(reconcileSeriesTotal(merged, "orders", 8).status, "mismatch", "1 pedido de diferença já reprova");
});

test("5b. tolerancia de GMV escala com o numero de buckets, sem folga percentual", () => {
  assert.equal(gmvTolerance(1), 0.05);
  assert.equal(gmvTolerance(31), 0.31);
});

// ---------------------------------------------------------------------------
// 6. Falha parcial de um canal
// ---------------------------------------------------------------------------

test("6. canal em falha e' nomeado, as demais series ficam, e o total desaparece", () => {
  const merged = mergeChannelSeries(
    [
      series("ml", [point("2026-07-01", 10, 1)]),
      series("shopee", [], "error"),
    ],
    ["ml", "shopee"],
    "gmv",
  );
  assert.deepEqual(merged.failedChannels, ["shopee"]);
  assert.deepEqual(merged.availableChannels, ["ml"], "a série disponível permanece");
  assert.equal(merged.buckets[0].total, null, "total não pode ser apresentado como completo");
  assert.equal(reconcileSeriesTotal(merged, "gmv", 10).status, "skipped");
});

test("6b. canal fresco sem linhas e' empty, nao zero fabricado", () => {
  const merged = mergeChannelSeries([series("ml", [])], ["ml"], "gmv");
  assert.deepEqual(merged.emptyChannels, ["ml"]);
  assert.deepEqual(merged.failedChannels, []);
  assert.equal(merged.buckets.length, 0, "sem buckets inventados");
});

// ---------------------------------------------------------------------------
// 7. Troca GMV/Pedidos sem novo fetch
// ---------------------------------------------------------------------------

test("7. metrica muda a identidade visual e NAO muda nenhuma identidade de fetch", () => {
  const fetchKeyGmv = buildGerencialRequestKey(KEY);
  const viewGmv = buildGerencialViewKey(KEY, "gmv");
  const viewOrders = buildGerencialViewKey(KEY, "orders");
  assert.notEqual(viewGmv, viewOrders, "identidade visual precisa distinguir a métrica");
  assert.equal(fetchKeyGmv, buildGerencialRequestKey(KEY), "chave de fetch não depende da métrica");
  assert.ok(!fetchKeyGmv.includes("gmv") && !fetchKeyGmv.includes("orders"));
  // e a chave por canal tambem nao carrega metrica
  const channelKey = buildChannelSeriesKey(KEY, "ml");
  assert.ok(!channelKey.includes("orders"));
  assert.ok(channelKey.startsWith("ml|"), "chave do canal isola a seleção unitária");
});

test("7b. metrica nao aparece em nenhuma dependencia de efeito do hook", () => {
  const hook = codeOnly(read("src/hooks/useGerencialSources.ts"));
  assert.doesNotMatch(hook, /metric/i, "o código do hook das fontes não deve conhecer a métrica");
  const page = read("app/page.tsx");
  // a metrica e' estado local da pagina, nao entra no input do hook
  const hookCall = page.slice(page.indexOf("useGerencialSources({"), page.indexOf("});", page.indexOf("useGerencialSources({")));
  assert.doesNotMatch(hookCall, /metric/, "metric não pode entrar no input do hook de fontes");
});

// ---------------------------------------------------------------------------
// 8 a 12. KPIs
// ---------------------------------------------------------------------------

function makeOverview(patch: Partial<OverviewData> = {}): OverviewData {
  return {
    gmv: 1000, tiktok_gmv: 400, ml_gmv: 400, shopee_gmv: 200,
    orders: 50, avg_ticket: 20, ad_spend: 100,
    ml_roas: 4, ml_cancel_rate_pct: 5, shopee_roas: 3,
    tiktok_customers: 10, ml_unique_buyers: 10, shopee_unique_buyers: 5,
    gmv_mom_pct: 8, prev_gmv: 900,
    ...patch,
  };
}

const ALL: Marketplace[] = ["tiktok", "ml", "shopee"];

test("8. a faixa tem exatamente os 5 KPIs, nesta ordem, e confianca NAO e' um deles", () => {
  const band = buildKpiBand(makeOverview(), ALL);
  assert.deepEqual(band.map((k) => k.key), ["gmv", "orders", "avg_ticket", "ad_spend", "roas"]);
  assert.ok(!band.some((k) => /confian/i.test(k.label)), "confiança não pode ser KPI");
});

test("8b. delta e referencia existem SO' no GMV; os demais declaram indisponibilidade", () => {
  const band = buildKpiBand(makeOverview(), ALL);
  const byKey = Object.fromEntries(band.map((k) => [k.key, k]));
  assert.equal(byKey.gmv.deltaPct, 8);
  assert.match(byKey.gmv.reference ?? "", /período anterior/);
  for (const key of ["orders", "avg_ticket", "ad_spend", "roas"]) {
    assert.equal(byKey[key].deltaPct, null, `${key} não pode ter delta`);
    assert.equal(byKey[key].reference, null, `${key} não pode ter referência`);
  }
  assert.equal(byKey.orders.comparisonNote, "Comparação indisponível");
  assert.equal(byKey.avg_ticket.comparisonNote, "Comparação indisponível");
});

test("9. ROAS nunca agrega: sem valor unico, uma linha por canal, TikTok N/D", () => {
  const band = buildKpiBand(makeOverview(), ALL);
  const roas = band.find((k) => k.key === "roas")!;
  assert.equal(roas.value, null, "não existe valor consolidado de ROAS");
  const tiktok = roas.channelValues!.find((c) => c.channel === "tiktok")!;
  assert.equal(tiktok.value, null);
  assert.match(tiktok.unavailableReason ?? "", /Não disponível nesta fonte/);
  const ml = roas.channelValues!.find((c) => c.channel === "ml")!;
  assert.equal(ml.value, "4.0x");
});

test("9b. com um unico canal compativel selecionado, esse canal e' destacado", () => {
  const only = buildKpiBand(makeOverview(), ["ml"]).find((k) => k.key === "roas")!;
  assert.equal(only.highlightChannel, "ml");
  const both = buildKpiBand(makeOverview(), ["ml", "shopee"]).find((k) => k.key === "roas")!;
  assert.equal(both.highlightChannel, null, "com dois canais não há destaque único");
});

test("10. Investimento em Ads declara cobertura e cai para N/D sem canal compativel", () => {
  const withAds = buildKpiBand(makeOverview(), ALL).find((k) => k.key === "ad_spend")!;
  assert.ok(withAds.value);
  assert.match(withAds.coverageNote ?? "", /Mercado Livre e Shopee/);
  assert.match(withAds.coverageNote ?? "", /TikTok/);

  const tiktokOnly = buildKpiBand(makeOverview(), ["tiktok"]).find((k) => k.key === "ad_spend")!;
  assert.equal(tiktokOnly.value, null);
  assert.match(tiktokOnly.unavailableReason ?? "", /Nenhum canal com investimento/);
});

test("12b. ticket medio com zero pedidos e' N/D — nunca NaN nem Infinity", () => {
  const zero = avgTicketDisplay(makeOverview({ orders: 0, avg_ticket: Number.POSITIVE_INFINITY }));
  assert.equal(zero.value, null);
  assert.match(zero.reason ?? "", /Sem pedidos/);
  const nan = avgTicketDisplay(makeOverview({ orders: 5, avg_ticket: Number.NaN }));
  assert.equal(nan.value, null);
  const band = buildKpiBand(makeOverview({ orders: 0, avg_ticket: Number.NaN }), ALL);
  const ticket = band.find((k) => k.key === "avg_ticket")!;
  assert.equal(ticket.value, null);
  assert.ok(!JSON.stringify(band).includes("NaN"), "nenhum NaN pode chegar à interface");
  assert.ok(!JSON.stringify(band).includes("Infinity"));
});

// ---------------------------------------------------------------------------
// 11, 12, 13. Saude do volume
// ---------------------------------------------------------------------------

function qualityBrand(patch: Partial<QualityBrandRow> = {}): QualityBrandRow {
  return {
    brand: "barbours", label: "BARBOURS",
    tiktok_orders: 100, tiktok_canceled: 0, tiktok_refunded: 0, tiktok_returned: 0,
    tiktok_problem_rate: null, tiktok_cancel_rate: null, tiktok_avg_delivery_days: null,
    ml_cancel_rate_pct: null, ml_not_delivered_rate_pct: null,
    ml_cancelled_orders: 8, ml_total_orders: 108,
    ml_not_delivered_shipments: null, ml_avg_delivery_days: null,
    ml_repeat_buyer_rate_pct: null, ml_gmv_per_buyer: null, ml_gmv_mom_pct: null,
    ml_new_buyers: null, ml_unique_buyers: null, ml_shipping_pct_of_gmv: null,
    shopee_orders: 90, shopee_canceled_orders: 10, shopee_returned_orders: 4,
    shopee_cancel_rate_pct: null, shopee_return_rate_pct: null,
    ...patch,
  };
}

const QUALITY_KPIS: QualityKpis = {
  tiktok_problem_rate: null, tiktok_cancel_rate: 0, tiktok_avg_delivery_days: null,
  ml_cancel_rate_pct: 7.41, ml_not_delivered_rate_pct: null, ml_avg_delivery_days: null,
  shopee_cancel_rate_pct: 10, shopee_return_rate_pct: 4.44,
};

test("11. pedidos considerados: ML usa ml_total_orders; Shopee soma nao cancelados + cancelados", () => {
  const rows = buildVolumeHealth({
    kpis: QUALITY_KPIS,
    brands: [qualityBrand()],
    channels: ALL,
    gmvByChannel: { tiktok: 1, ml: 2, shopee: 3 },
  });
  const ml = rows.find((r) => r.channel === "ml")!;
  assert.equal(ml.ordersLabel, ORDERS_CONSIDERED_LABEL);
  assert.equal(ml.orders, 108, "ml_total_orders já é não cancelados + cancelados");
  const shopee = rows.find((r) => r.channel === "shopee")!;
  assert.equal(shopee.ordersLabel, ORDERS_CONSIDERED_LABEL);
  assert.equal(shopee.orders, 100, "90 não cancelados + 10 cancelados");
});

test("11b. rotulos nunca chamam pedidos considerados de 'elegiveis'", () => {
  assert.ok(!/eleg/i.test(ORDERS_CONSIDERED_LABEL));
  assert.ok(!/eleg/i.test(ORDERS_REGISTERED_LABEL));
  const src = codeOnly(read("src/lib/gerencial/volume-health.ts"));
  assert.doesNotMatch(src, /"[^"]*eleg[íi]ve[^"]*"/i, "nenhum rótulo exibido usa 'elegíveis'");
});

test("12. TikTok e' 'Pedidos registrados' com cancelamento e devolucao em N/D, nunca zero", () => {
  const rows = buildVolumeHealth({
    kpis: QUALITY_KPIS,
    brands: [qualityBrand()],
    channels: ALL,
    gmvByChannel: {},
  });
  const tk = rows.find((r) => r.channel === "tiktok")!;
  assert.equal(tk.ordersLabel, ORDERS_REGISTERED_LABEL);
  assert.equal(tk.orders, 100);
  assert.equal(tk.cancelAvailable, false);
  assert.equal(tk.canceled, null, "campo zerado da fonte não pode virar 0 exibido");
  assert.equal(tk.cancelRatePct, null);
  assert.equal(tk.returnAvailable, false);
  assert.match(tk.cancelUnavailableReason ?? "", /TikTok Shop/);
});

test("13. devolucao da Shopee e' independente; ML declara devolucao indisponivel", () => {
  const rows = buildVolumeHealth({
    kpis: QUALITY_KPIS,
    brands: [qualityBrand()],
    channels: ALL,
    gmvByChannel: {},
  });
  const shopee = rows.find((r) => r.channel === "shopee")!;
  assert.equal(shopee.returnAvailable, true);
  assert.equal(shopee.returned, 4);
  assert.equal(shopee.returnRatePct, 4.44);
  // devolvidos NAO sao subconjunto do total considerado exibido
  assert.notEqual(shopee.returned! + shopee.canceled!, shopee.orders);

  const ml = rows.find((r) => r.channel === "ml")!;
  assert.equal(ml.returnAvailable, false);
  assert.match(ml.returnUnavailableReason ?? "", /Devolu[çc][ãa]o/);
});

test("13b. taxa exibida e' a servida — nunca recalculada pelo helper", () => {
  const rows = buildVolumeHealth({
    kpis: { ...QUALITY_KPIS, ml_cancel_rate_pct: 99.9 },
    brands: [qualityBrand()],
    channels: ["ml"],
    gmvByChannel: {},
  });
  // 8/108 seria 7,41% — o helper devolve o valor servido, sem recalcular
  assert.equal(rows[0].cancelRatePct, 99.9);
  const src = read("src/lib/gerencial/volume-health.ts");
  assert.doesNotMatch(src, /canceled\s*\/\s*\(/, "não pode existir divisão de taxa no helper");
});

test("13c. sem nenhuma taxa confiavel, o bloco nao tem o que renderizar", () => {
  const rows = buildVolumeHealth({
    kpis: null,
    brands: [qualityBrand()],
    channels: ["tiktok"],
    gmvByChannel: {},
  });
  assert.equal(volumeHealthHasAnyRate(rows), false);
});

// ---------------------------------------------------------------------------
// 14 a 17. Movimentos e concentracao
// ---------------------------------------------------------------------------

function brandRow(patch: Partial<BrandRow> = {}): BrandRow {
  return {
    brand: "barbours", label: "BARBOURS",
    tiktok_gmv: null, ml_gmv: null, shopee_gmv: null,
    total_gmv: 0, orders: 0, avg_ticket: null,
    tiktok_avg_ticket: null, ml_avg_ticket: null,
    tiktok_gmv_prev: null, ml_gmv_prev: null, shopee_gmv_prev: null,
    total_gmv_prev: 0, mom_pct: null, cos_pct: null, gpm: null,
    ml_roas: null, ml_cancel_rate_pct: null,
    ...patch,
  };
}

test("14. piso de R$ 1.000 na base anterior exclui o movimento da lista", () => {
  assert.equal(MOVEMENT_MIN_PREV_BASE, 1000);
  const m = buildMovements(
    [
      brandRow({ brand: "a", label: "A", ml_gmv: 5000, ml_gmv_prev: 999 }), // base abaixo do piso
      brandRow({ brand: "b", label: "B", ml_gmv: 5000, ml_gmv_prev: 1000 }), // exatamente no piso: entra
    ],
    ["ml"],
  );
  assert.deepEqual(m.gains.map((x) => x.brand), ["b"]);
  assert.equal(m.excludedByFloor, 1);
});

test("15. empate de variacao absoluta e' resolvido de forma deterministica", () => {
  const rows = [
    brandRow({ brand: "zeta", label: "ZETA", ml_gmv: 3000, ml_gmv_prev: 2000, shopee_gmv: 3000, shopee_gmv_prev: 2000 }),
    brandRow({ brand: "alfa", label: "ALFA", ml_gmv: 3000, ml_gmv_prev: 2000 }),
  ];
  const first = buildMovements(rows, ["tiktok", "ml", "shopee"]);
  const second = buildMovements([...rows].reverse(), ["shopee", "ml", "tiktok"]);
  const keys = (m: ReturnType<typeof buildMovements>) => m.gains.map((x) => `${x.brand}:${x.channel}`);
  // mesma ordem independentemente da ordem de entrada: marca, depois canal canonico
  assert.deepEqual(keys(first), ["alfa:ml", "zeta:ml", "zeta:shopee"]);
  assert.deepEqual(keys(second), keys(first));
});

test("16. base anterior zero nao produz percentual infinito", () => {
  // base 0 fica abaixo do piso, entao nem entra; mas o contrato do tipo
  // continua garantindo deltaPct null se o piso mudar no futuro.
  const m = buildMovements([brandRow({ ml_gmv: 5000, ml_gmv_prev: 0 })], ["ml"]);
  assert.equal(m.gains.length, 0);
  const all = [...m.gains, ...m.drops];
  for (const mv of all) {
    assert.ok(mv.deltaPct == null || Number.isFinite(mv.deltaPct));
  }
  assert.ok(!JSON.stringify(m).includes("Infinity"));
});

test("16b. no maximo cinco itens por painel", () => {
  const rows = Array.from({ length: 9 }, (_, i) =>
    brandRow({ brand: `b${i}`, label: `B${i}`, ml_gmv: 10_000 + i * 1000, ml_gmv_prev: 2000 }),
  );
  const m = buildMovements(rows, ["ml"]);
  assert.equal(m.gains.length, 5);
});

test("17. Top 1 exige uma marca positiva; Top 3 exige tres — sem aproximar", () => {
  const one = buildConcentration([brandRow({ brand: "a", total_gmv: 100 })]);
  assert.ok(one.top1Pct != null);
  assert.equal(one.top3Pct, null, "com uma marca não existe Top 3");

  const two = buildConcentration([
    brandRow({ brand: "a", total_gmv: 100 }),
    brandRow({ brand: "b", total_gmv: 50 }),
  ]);
  assert.equal(two.top3Pct, null, "com duas marcas ainda não existe Top 3");

  const three = buildConcentration([
    brandRow({ brand: "a", total_gmv: 100 }),
    brandRow({ brand: "b", total_gmv: 50 }),
    brandRow({ brand: "c", total_gmv: 50 }),
  ]);
  assert.ok(three.top3Pct != null);
  assert.equal(Math.round(three.top3Pct!), 100);

  const none = buildConcentration([brandRow({ brand: "a", total_gmv: 0 })]);
  assert.equal(none.top1Pct, null);
  assert.equal(none.positiveBrands, 0);
});

// ---------------------------------------------------------------------------
// 18. Matriz
// ---------------------------------------------------------------------------

test("18. intensidade e participacao da celula sao relativas ao PROPRIO canal", () => {
  const matrix = buildBrandChannelMatrix(
    [
      // TikTok tem ordem de grandeza muito maior que Shopee
      brandRow({ brand: "a", label: "A", tiktok_gmv: 1_000_000, shopee_gmv: 100, total_gmv: 1_000_100 }),
      brandRow({ brand: "b", label: "B", tiktok_gmv: 500_000, shopee_gmv: 100, total_gmv: 500_100 }),
    ],
    ["tiktok", "shopee"],
  );
  const a = matrix.rows.find((r) => r.brand === "a")!;
  const b = matrix.rows.find((r) => r.brand === "b")!;
  const aShopee = a.cells.find((c) => c.channel === "shopee")!;
  const bTiktok = b.cells.find((c) => c.channel === "tiktok")!;
  // Shopee de A e' o maximo do canal Shopee => intensidade 1, apesar de R$ 100
  assert.equal(aShopee.intensity, 1);
  assert.equal(bTiktok.intensity, 0.5);
  assert.equal(aShopee.sharePctInChannel, 50, "share é dentro do canal");
});

test("18b. celula sem dado e' indisponivel com motivo, nunca zero silencioso", () => {
  const matrix = buildBrandChannelMatrix(
    [brandRow({ brand: "a", label: "A", ml_gmv: null, tiktok_gmv: 10, total_gmv: 10 })],
    ["tiktok", "ml"],
  );
  const cell = matrix.rows[0].cells.find((c) => c.channel === "ml")!;
  assert.equal(cell.available, false);
  assert.equal(cell.gmv, null);
  assert.ok(cell.unavailableReason);
});

// ---------------------------------------------------------------------------
// 19. Fila comercial separada da confianca
// ---------------------------------------------------------------------------

const SUMMARY = {
  period: { date_from: "2026-07-01", date_to: "2026-07-31", compare_date_from: null, compare_date_to: null, refreshed_at: "2026-08-01T10:00:00Z" },
  health: { status: "attention" as const, gmv: 1, gmv_mom_pct: null, orders: 1, avg_ticket: 1, summary: "" },
  changes: [],
  risks: [
    { type: "high_cost", severity: "critical" as const, title: "Custo alto", description: "d", brand: "kokeshi", marketplace: "shopee", metric_value: 29.4, href: "/canais", category: "efficiency_ops" as const, reference_value: 21.1, reference_kind: "median" as const },
    { type: "stale_data", severity: "critical" as const, title: "Dado velho", description: "d2", brand: null, marketplace: null, metric_value: null, href: "/regioes", category: "data_confidence" as const, staleness_days: 4, threshold_days: 2, source: "marts" },
  ],
  data_warnings: [
    { type: "low_regional_coverage", severity: "warning" as const, message: "cobertura parcial", href: "/regioes", category: "data_confidence" as const },
  ],
};

test("19. insight de dado nunca entra na lista comercial nem recebe severidade 'Critico'", () => {
  const q = buildAttentionQueue(SUMMARY);
  assert.deepEqual(q.commercial.map((c) => c.type), ["high_cost"]);
  assert.equal(q.commercial[0].severity, "critical", "risco comercial preserva a severidade da fonte");
  assert.deepEqual(q.dataConfidence.map((d) => d.type).sort(), ["low_regional_coverage", "stale_data"]);
  for (const item of q.dataConfidence) {
    // a escala de dado nao tem "critical": um `critical` da fonte vira "attention"
    assert.ok(item.severity === "attention" || item.severity === "note");
  }
  const stale = q.dataConfidence.find((d) => d.type === "stale_data")!;
  assert.equal(stale.stalenessDays, 4);
  assert.equal(stale.source, "marts");
});

test("19b. faixa de confianca deriva disponibilidade da SERIE, nunca do valor de GMV", () => {
  const q = buildAttentionQueue(SUMMARY);
  const strip = buildConfidenceStrip(
    [
      // serie com pontos de valor ZERO continua sendo disponivel
      { channel: "tiktok", status: "fresh", pointCount: 3 },
      // fonte respondeu sem nenhuma linha: sem registros, nao "sem cobertura"
      { channel: "ml", status: "fresh", pointCount: 0 },
      // fonte falhou: indisponivel
      { channel: "shopee", status: "error", pointCount: 0 },
    ],
    q,
    ALL,
    true,
  );
  assert.equal(strip.selectedCount, 3);
  assert.equal(strip.availableCount, 1);
  assert.equal(strip.noRecordsCount, 1);
  assert.equal(strip.unavailableCount, 1);
  assert.equal(strip.maxStalenessDays, 4);
  assert.equal(strip.warningCount, 2);
  assert.equal(strip.warningsChecked, true);
});

test("19c. os quatro estados de disponibilidade de serie sao distintos", () => {
  assert.equal(seriesAvailability({ channel: "ml", status: "loading", pointCount: 0 }), "checking");
  assert.equal(seriesAvailability({ channel: "ml", status: "error", pointCount: 0 }), "unavailable");
  assert.equal(seriesAvailability({ channel: "ml", status: "fresh", pointCount: 0 }), "no_records");
  // zero explicito NAO e' ausencia: um ponto ja basta para "disponivel"
  assert.equal(seriesAvailability({ channel: "ml", status: "fresh", pointCount: 1 }), "available");
});

test("19d. executive-summary em falha nao zera a faixa: avisos ficam NAO VERIFICADOS", () => {
  const strip = buildConfidenceStrip(
    [{ channel: "ml", status: "fresh", pointCount: 5 }],
    buildAttentionQueue(SUMMARY),
    ["ml"],
    false,
  );
  // a disponibilidade da serie continua valendo
  assert.equal(strip.availableCount, 1);
  // e defasagem/avisos nao sao afirmados como ausentes
  assert.equal(strip.warningsChecked, false);
  assert.equal(strip.warningCount, 0);
  assert.equal(strip.maxStalenessDays, null);
});

// ---------------------------------------------------------------------------
// Rodada consolidada — Task E: cobertura de Ads pelas 7 combinacoes
// ---------------------------------------------------------------------------

test("E. a nota de cobertura de Ads reflete ESTRITAMENTE a selecao (7 combinacoes)", () => {
  const cases: [Marketplace[], RegExp[], RegExp[]][] = [
    // selecao,                cita,                              NAO cita
    [["tiktok"], [/Sem cobertura de m.dia/, /TikTok Shop: n.o dispon.vel/], [/Mercado Livre/, /Cobertura: Shopee/]],
    [["ml"], [/Cobertura: Mercado Livre/], [/Shopee/, /TikTok/]],
    [["shopee"], [/Cobertura: Shopee/], [/Mercado Livre/, /TikTok/]],
    [["ml", "shopee"], [/Mercado Livre e Shopee/], [/TikTok/]],
    [["tiktok", "ml"], [/Cobertura: Mercado Livre/, /TikTok Shop: não disponível/], [/Shopee/]],
    [["tiktok", "shopee"], [/Cobertura: Shopee/, /TikTok Shop: não disponível/], [/Mercado Livre/]],
    [["tiktok", "ml", "shopee"], [/Mercado Livre e Shopee/, /TikTok Shop: não disponível/], []],
  ];
  for (const [selection, cites, omits] of cases) {
    const note = adsCoverageNote(selection);
    for (const re of cites) assert.match(note, re, `${selection.join("+")} deveria citar ${re}`);
    for (const re of omits) assert.doesNotMatch(note, re, `${selection.join("+")} NAO deveria citar ${re}`);
    // e o card correspondente usa exatamente essa nota
    const card = buildKpiBand(makeOverview(), selection).find((k) => k.key === "ad_spend")!;
    assert.equal(card.coverageNote, note);
  }
});

test("E2. ausencia de investimento nunca vira R$ 0", () => {
  const card = buildKpiBand(makeOverview({ ad_spend: null }), ["ml"]).find((k) => k.key === "ad_spend")!;
  assert.equal(card.value, null, "sem valor no contrato => N/D, nunca zero");
  assert.match(card.unavailableReason ?? "", /não disponível/i);
  const tiktokOnly = buildKpiBand(makeOverview(), ["tiktok"]).find((k) => k.key === "ad_spend")!;
  assert.equal(tiktokOnly.value, null);
});

// ---------------------------------------------------------------------------
// Rodada consolidada — Task D2: intervalo do bucket por grao
// ---------------------------------------------------------------------------

test("D2. grao diario fixa o dia; grao mensal fixa o mes inteiro", () => {
  const day = bucketRange("2026-07-15", "day");
  // Gate V2-2 acrescentou `clamped`: o dia nunca e cortado.
  assert.deepEqual(day, { dateFrom: "2026-07-15", dateTo: "2026-07-15", label: "este dia", clamped: false });

  const month = bucketRange("2026-07-01", "month");
  assert.equal(month.dateFrom, "2026-07-01");
  assert.equal(month.dateTo, "2026-07-31");
  assert.equal(month.label, "este mês");
});

test("D2b. ultimo dia do mes correto em fevereiro comum, bissexto e dezembro", () => {
  assert.equal(bucketRange("2026-02-01", "month").dateTo, "2026-02-28", "2026 não é bissexto");
  assert.equal(bucketRange("2024-02-01", "month").dateTo, "2024-02-29", "2024 é bissexto");
  assert.equal(bucketRange("2100-02-01", "month").dateTo, "2100-02-28", "2100 não é bissexto (regra do século)");
  assert.equal(bucketRange("2000-02-01", "month").dateTo, "2000-02-29", "2000 é bissexto (divisível por 400)");
  assert.equal(bucketRange("2026-12-01", "month").dateTo, "2026-12-31");
  assert.equal(bucketRange("2026-04-01", "month").dateTo, "2026-04-30");
});

test("D2c. bucket mensal em dia diferente do primeiro ainda cobre o mes inteiro", () => {
  // robustez: se a fonte devolver o bucket no meio do mes, o intervalo nao muda
  const r = bucketRange("2026-02-17", "month");
  assert.equal(r.dateFrom, "2026-02-01");
  assert.equal(r.dateTo, "2026-02-28");
});

test("D2d. nenhum erro de fuso: a conversao nao passa por Date", () => {
  const src = codeOnly(read("src/lib/gerencial/trend-series.ts"));
  const fn = src.slice(src.indexOf("export function bucketRange"), src.indexOf("export function gmvTolerance"));
  // `\b` é essencial: sem ele, `bucketDate.split(...)` contaria como uso de
  // `Date.` e o teste reprovaria justamente a implementação correta.
  assert.doesNotMatch(fn, /\bnew Date\b|\bDate\./, "bucketRange não pode usar Date");
});

// ---------------------------------------------------------------------------
// 20 a 22. Wiring
// ---------------------------------------------------------------------------

test("20. CTAs preservam filtros via mergeFilteredHref e ctx_* fica fora da allowlist", () => {
  const page = read("app/page.tsx");
  assert.match(page, /mergeFilteredHref\(href, searchParams\)/);
  assert.doesNotMatch(page, /ctx_/, "a Gerencial não produz contexto de chegada neste gate");
  const navLinks = read("src/lib/filters/nav-links.ts");
  assert.match(navLinks, /FILTER_QUERY_KEYS = \["channels", "brands", "date_from", "date_to", "compare"\]/);
  assert.doesNotMatch(navLinks, /ctx_/, "ctx_* nunca entra em FILTER_QUERY_KEYS");
});

test("21. nenhuma regra hard-coded por marca sobrou no JSX da Gerencial", () => {
  const page = read("app/page.tsx");
  assert.doesNotMatch(page, /lescent/i, "o alerta hard-coded de Lescent foi removido");
  for (const brand of ["barbours", "kokeshi", "apice", "rituaria"]) {
    assert.doesNotMatch(page, new RegExp(`=== "${brand}"`, "i"), `sem comparação literal com ${brand}`);
  }
  // a fila de atencao vem do contrato, nao de condicional local
  assert.match(page, /commercial=\{queue\.commercial\}/);
  assert.match(page, /dataConfidence=\{queue\.dataConfidence\}/);
});

test("22. as seis fontes tem identidade propria e dado obsoleto nunca vaza", () => {
  const hookRaw = read("src/hooks/useGerencialSources.ts");
  const hook = codeOnly(hookRaw);
  // uma fonte por superficie logica
  for (const fetcher of ["fetchOverview", "fetchBrands", "fetchExecutiveSummary", "fetchCanais", "fetchQuality", "fetchTrend"]) {
    assert.match(hook, new RegExp(`\\b${fetcher}\\(`), `${fetcher} deve ser consumido`);
  }
  // sem Promise.all cobrindo fontes distintas (falha parcial viraria total)
  assert.doesNotMatch(hook, /Promise\.all/, "nenhum Promise.all entre fontes diferentes");
  // frescor via helper compartilhado e ja testado
  assert.match(hook, /computeRequestStatus/);
  assert.match(hook, /resolvedKey: requestKey/);
  // cada caminho de falha conclui a chave (senao a fonte fica presa em loading)
  const catches = hook.match(/\.catch\(\(\) => \{/g) ?? [];
  assert.ok(catches.length >= 6, `esperado >= 6 caminhos de falha, achei ${catches.length}`);
});

// Achado do QA visual do V2-1: os fetchers NAO rejeitam — quando a API nao
// responde eles devolvem MOCK com `live: false`. Sem tratamento, um `/quality`
// que falhou renderizava numeros mockados de cancelamento ao lado de KPIs
// reais. A regra corrigida esta em `toSource`: fora do modo demonstracao,
// `live: false` e' indisponibilidade da fonte.
test("22a. fallback de mock nunca passa por dado live fora do modo demonstracao", () => {
  const hook = read("src/hooks/useGerencialSources.ts");
  assert.match(
    hook,
    /const substitutedByMock = !state\.loading && !state\.errored && !state\.live;/,
    "toSource precisa detectar a substituição por mock",
  );
  // A reparacao de stop-loss trocou os dois estados por TRES: mock com decisao
  // pendente vira carregamento neutro, e so' apos a decisao vira erro.
  assert.match(hook, /const mockNotAllowed = substitutedByMock && !demoMode;/);
  assert.match(
    hook,
    /error: state\.errored \|\| \(mockNotAllowed && !demoPending\)/,
    "decidido que nao e demonstracao => mock substituido e erro da fonte",
  );
  assert.match(
    hook,
    /loading: state\.loading \|\| \(mockNotAllowed && demoPending\)/,
    "decisao pendente => estado neutro, nunca numeros mockados",
  );
  // as cinco fontes derivadas recebem o demoMode decidido pelo overview
  for (const src of ["overviewState", "brandsState", "canaisState", "qualityState"]) {
    assert.match(
      hook,
      new RegExp(`toSource\\(${src}, requestKey, demoMode, demoPending\\)`),
      `${src} deve ser resolvido com a decisao de demonstracao da pagina`,
    );
  }
  // a serie por canal aplica a MESMA regra
  assert.match(
    hook,
    /const mockNotAllowed = status\.fresh && !state\?\.live && !demoMode;/,
    "a serie aplica a MESMA regra de tres estados",
  );
});

test("22b. a grade da segunda dobra nao usa items-start nem row-span", () => {
  const page = codeOnly(read("app/page.tsx"));

  // A regra vale para GRADE de cards vizinhos (plano V2 §7.1). Um `items-start`
  // numa barra de filtros em flex e' legitimo e nao produz faixa orfa alguma —
  // por isso a assercao inspeciona cada className que declara `grid`, em vez de
  // proibir o token na pagina inteira.
  const classNames = page.match(/className="[^"]*"/g) ?? [];
  const gridClasses = classNames.filter((c) => /\bgrid\b/.test(c));
  assert.ok(gridClasses.length >= 1, "a página precisa ter ao menos uma grade");
  for (const cls of gridClasses) {
    assert.doesNotMatch(cls, /items-start/, `grade com items-start (causa da faixa vazia): ${cls}`);
  }
  assert.doesNotMatch(page, /row-span/, "row-span criava a linha órfã");
  // o card e' o item da grade e estica
  const evolution = codeOnly(read("src/components/gerencial/EvolutionCard.tsx"));
  assert.match(evolution, /h-full/);
  assert.match(evolution, /flex-1 min-h-\[240px\]/, "área do gráfico absorve a sobra");
  const chart = codeOnly(read("src/components/gerencial/EvolutionChart.tsx"));
  assert.match(chart, /height="100%"/, "sem altura de gráfico em pixel fixo");
  assert.doesNotMatch(chart, /height=\{\d+\}/, "altura de gráfico em pixel fixo não pode voltar");
});

test("22c. o grafico e' carregado sob demanda (dynamic, ssr false)", () => {
  const card = read("src/components/gerencial/EvolutionCard.tsx");
  assert.match(card, /dynamic\(\(\) => import\("\.\/EvolutionChart"\)/);
  assert.match(card, /ssr:\s*false/);
});

// ===========================================================================
// Rodada consolidada — findings A, B, C, D1, G, H
// ===========================================================================

// Task A — a estrutura da pagina fica montada; nenhum bloco depende do erro de
// uma fonte que nao e a sua.
test("A. nenhum gate global de erro/vazio envolve os blocos da pagina", () => {
  const page = codeOnly(read("app/page.tsx"));

  // O ramo antigo `overviewStatus.error ? ... : isEmpty ? ... : (<>todos</>)`
  // nao pode voltar.
  assert.doesNotMatch(page, /const isEmpty/, "sem estado de vazio global");
  assert.doesNotMatch(page, /overviewStatus\.error \?/, "sem ramo global condicionado ao overview");

  // Cada bloco recebe o estado da SUA fonte.
  const expected: [string, RegExp][] = [
    ["KpiBand", /error=\{overviewStatus\.error\}/],
    ["VolumeHealthCard", /error=\{sources\.quality\.status\.error\}/],
    ["BrandChannelMatrix", /error=\{sources\.brands\.status\.error\}/],
    ["MovementsPanels", /error=\{sources\.brands\.status\.error\}/],
    ["AttentionQueue", /commercialError=\{sources\.executiveSummary\.status\.error\}/],
  ];
  for (const [block, re] of expected) {
    const start = page.indexOf("<" + block);
    assert.ok(start > 0, block + " deve estar montado");
    const jsx = page.slice(start, page.indexOf("/>", start));
    assert.match(jsx, re, block + " deve receber o erro da propria fonte");
  }

  // O anuncio de acessibilidade nao pode dizer "Dados carregados" havendo falha.
  assert.match(page, /Dados carregados parcialmente\. Indisponível:/);
  assert.match(page, /const failedSources = \[/);
});

// Task B — os quatro caminhos novos existem e usam o shell unico.
test("B. os tres novos conteudos de drill-down abrem no shell unico", () => {
  const page = codeOnly(read("app/page.tsx"));
  for (const kind of ["channelSeries", "matrixChannel", "concentration"]) {
    assert.match(page, new RegExp('kind: "' + kind + '"'), kind + " deve ser um estado do dialogo");
  }
  assert.equal((page.match(/<KpiDrilldownDialog/g) ?? []).length, 1, "um unico shell");
  assert.doesNotMatch(page, /createPortal/);

  const drills = codeOnly(read("src/components/gerencial/GerencialDrilldowns.tsx"));
  for (const fn of [
    "ChannelSeriesDrilldownContent",
    "MatrixChannelDrilldownContent",
    "ConcentrationDrilldownContent",
  ]) {
    assert.match(drills, new RegExp("export function " + fn), fn + " deve existir");
  }
  assert.doesNotMatch(drills, /role="dialog"/, "nenhum segundo dialog");
});

test("B1. legenda de canal e botao acessivel e realca a serie", () => {
  const card = codeOnly(read("src/components/gerencial/EvolutionCard.tsx"));
  const legend = card.slice(card.indexOf("merged.availableChannels.map"), card.indexOf("{showTotal && ("));
  assert.match(legend, /<button/, "a legenda precisa ser botao, nao <li> inerte");
  assert.match(legend, /aria-haspopup="dialog"/);
  assert.match(legend, /aria-pressed=\{highlightedChannel === channel\}/);
  assert.match(legend, /min-h-11/, "alvo de toque de 44px");
  assert.match(card, /highlightedChannel=\{highlightedChannel\}/);

  const chart = codeOnly(read("src/components/gerencial/EvolutionChart.tsx"));
  assert.match(chart, /strokeOpacity=\{dim\(channel\) \? 0\.2 : 1\}/, "canais fora de foco recuam");
  assert.match(chart, /strokeOpacity=\{isolating \? 0\.18 : 1\}/, "o total tambem recua");
});

test("B2. cabecalho de canal da matriz e clicavel e o destino vence o filtro atual", () => {
  const matrix = codeOnly(read("src/components/gerencial/BrandChannelMatrix.tsx"));
  const head = matrix.slice(matrix.indexOf("matrix.channels.map"), matrix.indexOf("</thead>"));
  assert.match(head, /<button/);
  assert.match(head, /onOpenChannelHeader\(channel\)/);
  assert.match(head, /aria-haspopup="dialog"/);
  const page = codeOnly(read("app/page.tsx"));
  assert.match(page, /channels=\$\{dialog\.channel\}/, "destino explicito com o canal");
});

test("B3. concentracao explica antes de navegar", () => {
  const panels = codeOnly(read("src/components/gerencial/MovementsPanels.tsx"));
  const conc = panels.slice(
    panels.indexOf("function ConcentrationPanel"),
    panels.indexOf("export default function"),
  );
  assert.match(conc, /onOpenConcentration\(entry\)/, "clique abre explicacao");
  assert.match(conc, /aria-haspopup="dialog"/);
  assert.doesNotMatch(conc, /<Link/, "nao pode navegar direto sem explicacao");

  const drills = read("src/components/gerencial/GerencialDrilldowns.tsx");
  const cd = drills.slice(drills.indexOf("export function ConcentrationDrilldownContent"));
  assert.match(cd, /brandHref/, "CTA primario para a marca");
  assert.match(cd, /PRODUCTS_SCOPE_NOTE/, "CTA secundario declara o escopo proprio de Produtos");
});

test("B4. chips de sinal usam rotulo humano e nao criam botao aninhado", () => {
  const matrix = read("src/components/gerencial/BrandChannelMatrix.tsx");
  assert.match(matrix, /signalLabel\(main\)/, "rotulo humano, nunca o snake_case cru");
  assert.match(matrix, /\+\{rest\.length\}/, "sinal principal + contagem dos demais");
  const from = matrix.indexOf("{main && (");
  const cell = matrix.slice(from, matrix.indexOf("</button>", from));
  assert.doesNotMatch(cell, /<button/, "chip nao pode ser botao dentro de botao");
  assert.match(matrix, /signals\.map\(signalLabel\)/, "o nome acessivel anuncia os sinais");
});

// Task C — matriz parcial
test("C. matriz preserva GMV quando /canais falha e declara o que falta", () => {
  const matrix = codeOnly(read("src/components/gerencial/BrandChannelMatrix.tsx"));
  assert.match(matrix, /if \(error\) \{/, "somente /brands bloqueia a matriz");
  assert.match(matrix, /signalsState === "error"/);
  assert.match(matrix, /signalsState === "loading"/);
  const page = codeOnly(read("app/page.tsx"));
  assert.match(page, /sources\.canais\.status\.loading/);
  // A mensagem generica foi substituida pelos quatro estados (finding 2 do
  // stop-loss); a assercao detalhada vive no teste F2.
  assert.match(page, /<MatrixCellUnavailableContent/);
  assert.match(page, /status=\{signalsState\}/);
});

// Task D1 — guarda de mock no detalhe temporal
test("D1. detalhe do ponto nunca renderiza mock em pagina live", () => {
  const drills = codeOnly(read("src/components/gerencial/GerencialDrilldowns.tsx"));
  assert.match(
    drills,
    /return demoMode \? "demo" : "unavailable";/,
    "resposta nao-live vira indisponibilidade fora do modo demonstracao",
  );
  const bucket = drills.slice(
    drills.indexOf("export function TrendBucketDrilldownContent"),
    drills.indexOf("export function ChannelSeriesDrilldownContent"),
  );
  assert.doesNotMatch(bucket, /Promise\.all/, "overview e brands falham de forma independente");
  assert.match(bucket, /Detalhe parcial: /, "parcial nomeia a fonte ausente");
  assert.match(bucket, /let ignore = false;/, "ignore guard preservado");
  const page = codeOnly(read("app/page.tsx"));
  assert.match(page, /demoMode=\{sources\.demoMode\}/, "a pagina informa o modo ao drill-down");
});

// Task G — fechamento do dialogo por qualquer mudanca efetiva de filtro
test("G. dialogo fecha por identidade de filtro, nao apenas pelos controles", () => {
  const page = codeOnly(read("app/page.tsx"));
  assert.match(page, /const filterIdentity = \[/, "identidade estavel dos filtros efetivos");
  for (const part of [
    "filters.channels.join",
    "filters.brands.join",
    "filters.dateFrom",
    "filters.dateTo",
    "String(filters.compare)",
  ]) {
    assert.ok(page.includes(part), "identidade deve incluir " + part);
  }
  assert.match(page, /\}, \[filterIdentity\]\)/, "efeito dedicado cobre back/forward e URL externa");
  assert.match(page, /\}, \[sources\.retryKey\]\)/, "retry inicia novo ciclo e fecha");
  assert.doesNotMatch(page, /const applyFilters/, "applyFilters deixou de ser o unico gatilho");
  assert.match(page, /onMetricChange=\{setMetric\}/, "trocar a metrica nao fecha o dialogo");
});

// Task H — piso de legibilidade nos arquivos do V2
test("H. nenhum arquivo do V2 usa fonte abaixo de 12px", () => {
  const files = readdirSync(join(ROOT, "src/components/gerencial")).filter((f) => f.endsWith(".tsx"));
  assert.ok(files.length >= 11, "todos os componentes do V2 sao inspecionados");
  const targets = [...files.map((x) => "src/components/gerencial/" + x), "app/page.tsx"];
  for (const f of targets) {
    const under = (read(f).match(/text-\[(\d+)px\]/g) ?? []).filter(
      (t) => Number(t.replace(/\D/g, "")) < 12,
    );
    assert.deepEqual(under, [], f + " nao pode ter fonte < 12px: " + under.join(", "));
  }
});

// Achado do QA da rodada consolidada: decidir o modo demonstracao SO' pelo
// overview produzia um estado misto — KPIs mockados ao lado de matriz e evolucao
// live, com apenas um badge global. Agora demonstracao exige que TODAS as fontes
// com fallback tenham caido para mock.
test("A2. a decisao de demonstracao vive num modulo puro, nao inline no hook", () => {
  const hook = codeOnly(read("src/hooks/useGerencialSources.ts"));
  // A versao inline usava `every` sobre uma lista filtrada — vacuamente
  // verdadeiro com uma unica fonte concluida. Ver os testes F1* para o contrato.
  assert.doesNotMatch(hook, /settledWithFallback/, "a heuristica inline foi removida");
  assert.doesNotMatch(hook, /Object\.values\(seriesState\)/);
  assert.match(hook, /decideDemoMode\(\{/, "a decisao vem de lib/gerencial/demo-mode.ts");
  assert.match(hook, /const demoMode = demoDecision\.demoMode;/);
  assert.match(hook, /const demoPending = demoDecision\.pending;/);
});


// ===========================================================================
// Reparacao de stop-loss do V2-1 — findings 1 a 5
// ===========================================================================

// --- Finding 1: decisao de modo demonstracao ------------------------------

function fb(patch: Partial<FallbackSourceState> = {}): FallbackSourceState {
  return { loading: false, errored: false, resolvedKey: "K", live: false, ...patch };
}
const LOADING = fb({ loading: true, resolvedKey: null });
const MOCK = fb({ live: false });
const LIVE = fb({ live: true });

function demoInput(
  aggregates: Partial<Record<AggregateFallbackSource, FallbackSourceState>>,
  seriesByChannel: Partial<Record<Marketplace, FallbackSourceState>>,
  selectedChannels: Marketplace[] = ["ml"],
): DemoModeInput {
  return {
    requestKey: "K",
    aggregates: {
      overview: aggregates.overview ?? MOCK,
      brands: aggregates.brands ?? MOCK,
      canais: aggregates.canais ?? MOCK,
      quality: aggregates.quality ?? MOCK,
    },
    selectedChannels,
    seriesByChannel,
    // chave esperada por canal, no mesmo formato do hook
    expectedSeriesKey: (c) => `S:${c}`,
  };
}

test("F1. somente overview mock, demais carregando => NAO e demonstracao (e fica pendente)", () => {
  const d = decideDemoMode(
    demoInput({ overview: MOCK, brands: LOADING, canais: LOADING, quality: LOADING }, {}),
  );
  assert.equal(d.demoMode, false, "every sobre lista parcial nao pode confirmar demonstracao");
  assert.equal(d.pending, true, "a decisao ainda nao concluiu");
});

test("F1b. quatro agregadas mock mas serie ainda carregando => NAO e demonstracao", () => {
  const d = decideDemoMode(demoInput({}, { ml: fb({ loading: true, resolvedKey: null }) }));
  assert.equal(d.demoMode, false);
  assert.equal(d.pending, true);
});

test("F1c. todas as fontes atuais mock => demonstracao confirmada", () => {
  const d = decideDemoMode(demoInput({}, { ml: fb({ resolvedKey: "S:ml" }) }));
  assert.equal(d.demoMode, true);
  assert.equal(d.pending, false);
});

test("F1d. uma fonte live derruba a demonstracao imediatamente", () => {
  const viaAggregate = decideDemoMode(
    demoInput({ brands: LIVE }, { ml: fb({ resolvedKey: "S:ml" }) }),
  );
  assert.equal(viaAggregate.demoMode, false);
  assert.equal(viaAggregate.pending, false, "com dado real, a decisao esta tomada");

  const viaSeries = decideDemoMode(demoInput({}, { ml: fb({ resolvedKey: "S:ml", live: true }) }));
  assert.equal(viaSeries.demoMode, false);
});

test("F1e. fonte com erro nunca confirma demonstracao", () => {
  const d = decideDemoMode(
    demoInput({ quality: fb({ errored: true }) }, { ml: fb({ resolvedKey: "S:ml" }) }),
  );
  assert.equal(d.demoMode, false);
  // Correcao terminal: erro e' uma CONCLUSAO, nao uma espera. A expectativa
  // anterior (`pending: true`) era justamente o bug — deixava os mocks das outras
  // fontes presos em carregamento neutro e `anyLoading` nunca encerrava. Ver T1.
  assert.equal(d.pending, false);
});

test("F1f. resolvedKey antigo nao conta como concluido", () => {
  const stale = decideDemoMode(
    demoInput({ overview: fb({ resolvedKey: "CHAVE_ANTIGA" }) }, { ml: fb({ resolvedKey: "S:ml" }) }),
  );
  assert.equal(stale.demoMode, false);

  const staleSeries = decideDemoMode(demoInput({}, { ml: fb({ resolvedKey: "S:ml:ANTIGA" }) }));
  assert.equal(staleSeries.demoMode, false, "chave de serie antiga tambem invalida");
});

test("F1g. serie de canal NAO selecionado nao influencia a decisao", () => {
  // shopee mock e concluida, mas nao esta selecionada: nao conta
  const d = decideDemoMode(
    demoInput({}, { ml: fb({ resolvedKey: "S:ml" }), shopee: fb({ resolvedKey: "S:shopee", live: true }) }, ["ml"]),
  );
  assert.equal(d.demoMode, true, "a serie live de um canal fora da selecao nao derruba nem confirma");
});

test("F1h. serie de canal selecionado AUSENTE => nunca confirma", () => {
  const d = decideDemoMode(demoInput({}, {}, ["ml", "shopee"]));
  assert.equal(d.demoMode, false);
  assert.equal(d.pending, true);
});

test("F1i. troca de filtro invalida a decisao anterior", () => {
  // todas concluidas na chave "K"
  const antes = decideDemoMode(demoInput({}, { ml: fb({ resolvedKey: "S:ml" }) }));
  assert.equal(antes.demoMode, true);
  // nova requisicao: as chaves resolvidas passam a ser antigas
  const depois = decideDemoMode({
    ...demoInput({}, { ml: fb({ resolvedKey: "S:ml" }) }),
    requestKey: "K2",
  });
  assert.equal(depois.demoMode, false, "a decisao nao pode ser reaproveitada entre requisicoes");
  assert.equal(depois.pending, true);
});

test("F1j. o hook usa a funcao pura e nao Object.values(seriesState)", () => {
  const hook = codeOnly(read("src/hooks/useGerencialSources.ts"));
  assert.match(hook, /decideDemoMode\(\{/, "a decisao vem do modulo puro");
  assert.doesNotMatch(hook, /Object\.values\(seriesState\)/, "series fora da selecao nao podem entrar");
  // Gate V2-2: a chave da serie passou a incluir a granularidade PEDIDA, para
  // que trocar o grao refaca as series sem tocar as outras cinco fontes.
  assert.match(
    hook,
    /expectedSeriesKey: \(channel\) => buildChannelSeriesKey\(keyInput, channel, input\.granularity\)/,
  );
  // mock com decisao pendente => estado NEUTRO de carregamento, nunca numeros
  assert.match(hook, /loading: state\.loading \|\| \(mockNotAllowed && demoPending\)/);
  assert.match(hook, /error: state\.errored \|\| \(mockNotAllowed && !demoPending\)/);
  // o executive-summary nao participa (nao tem fallback mock)
  assert.match(hook, /toSource\(execState, requestKey, true, false\)/);
});

// --- Finding 2: quatro estados do detalhe da matriz ----------------------

test("F2. o detalhe da celula distingue as quatro causas de ausencia", () => {
  const drills = read("src/components/gerencial/GerencialDrilldowns.tsx");
  const fn = drills.slice(drills.indexOf("export function MatrixCellUnavailableContent"));
  assert.match(fn, /Verificando sinais e referências comparativas deste canal/, "loading");
  assert.match(fn, /role="status"/, "loading anunciado");
  assert.match(fn, /aria-busy="true"/);
  assert.match(fn, /Fonte de sinais e referências indisponível nesta carga/, "erro de fonte");
  assert.match(fn, /modo demonstração não modela Ads, custos, frete, sinais ou medianas/, "demonstracao");
  assert.match(fn, /Não há registro comparativo para/, "fresh sem linha");
  assert.match(fn, /Isso não indica falha de carga/, "fresh sem linha nao culpa a fonte");

  const page = codeOnly(read("app/page.tsx"));
  assert.match(page, /<MatrixCellUnavailableContent/);
  assert.match(page, /status=\{signalsState\}/);
  assert.match(page, /demoMode=\{sources\.demoMode\}/);
  // a mensagem generica antiga saiu
  assert.doesNotMatch(page, /detalhe comparativo desta marca no canal está indisponível/);
});

// --- Finding 3: sinal desconhecido -------------------------------------------

test("F3. sinal desconhecido nunca aparece cru", () => {
  for (const known of ["custo_alto", "frete_alto", "ads_subutilizado", "roas_forte", "sem_dado"]) {
    const label = signalLabel(known);
    assert.notEqual(label, known, `${known} deve ter rótulo humano`);
    assert.equal(isUnmappedSignal(known), false);
  }
  assert.equal(signalLabel("unknown_signal_from_backend"), UNKNOWN_SIGNAL_LABEL);
  assert.equal(UNKNOWN_SIGNAL_LABEL, "Sinal não mapeado");
  assert.equal(isUnmappedSignal("unknown_signal_from_backend"), true);
  // o identificador cru nao pode sobreviver em nenhuma forma de saida
  assert.doesNotMatch(signalLabel("unknown_signal_from_backend"), /unknown_signal_from_backend/);
  assert.doesNotMatch(signalLabel("unknown_signal_from_backend"), /_/, "sem snake_case no rótulo");
});

test("F3b. matriz e dialogo derivam o texto de signalLabel, nunca do identificador", () => {
  const matrix = codeOnly(read("src/components/gerencial/BrandChannelMatrix.tsx"));
  // nao existe interpolacao do sinal cru nem no texto nem no aria-label
  assert.doesNotMatch(matrix, /\$\{main\}/, "o chip não pode interpolar o identificador");
  assert.doesNotMatch(matrix, /\{main\}/, "o chip não pode renderizar o identificador");
  assert.match(matrix, /signalLabel\(main\)/);
  assert.match(matrix, /signals\.map\(signalLabel\)/);
  const dialog = codeOnly(read("src/components/ChannelComparisonDialogContent.tsx"));
  assert.match(dialog, /signalLabel\(/);
});

// --- Finding 4: tipografia, incluindo estilo inline do Recharts ------------

test("F4. nenhum tamanho abaixo de 12px nos arquivos do V2, em qualquer forma", () => {
  const dir = join(ROOT, "src/components/gerencial");
  const targets = [
    ...readdirSync(dir).map((f) => "src/components/gerencial/" + f),
    "app/page.tsx",
    ...readdirSync(join(ROOT, "src/lib/gerencial")).map((f) => "src/lib/gerencial/" + f),
  ].filter((f) => /\.(tsx|ts)$/.test(f));

  const patterns: [RegExp, string][] = [
    [/text-\[(\d+)px\]/g, "classe Tailwind"],
    [/fontSize:\s*(\d+)\b/g, "estilo inline / Recharts"],
    [/font-size:\s*(\d+)px/g, "CSS"],
  ];
  const offenders: string[] = [];
  for (const f of targets) {
    const src = read(f);
    for (const [re, kind] of patterns) {
      for (const m of src.matchAll(re)) {
        if (Number(m[1]) < 12) offenders.push(`${f}: ${kind} ${m[0]}`);
      }
    }
  }
  assert.deepEqual(offenders, [], "fontes < 12px encontradas:\n" + offenders.join("\n"));
});

test("F4b. os ticks do grafico estao em 12px", () => {
  const chart = read("src/components/gerencial/EvolutionChart.tsx");
  const ticks = [...chart.matchAll(/tick=\{\{ fontSize: (\d+)/g)].map((m) => Number(m[1]));
  assert.equal(ticks.length, 2, "eixo X e eixo Y");
  for (const t of ticks) assert.ok(t >= 12, `tick de ${t}px é ilegível`);
});

// --- Finding 5: contagem documental ----------------------------------------

test("F5. a documentacao usa contagem de drill-down nao ambigua", () => {
  const status = read("../../docs/PROJECT_STATUS.md");
  const spec = read("../../docs/GERENCIAL_V2_SPEC.md");
  assert.doesNotMatch(status, /doze caminhos de drill-down/, "contagem ambígua removida");
  assert.match(status, /dezesseis tipos de acionamento/);
  assert.match(spec, /16 tipos de acionamento/);
  assert.match(spec, /Os cinco KPIs contam\ncomo \*\*cinco\*\* tipos/, "o critério está explicado");
});

// ===========================================================================
// Correcao terminal do demoMode — erro TAMBEM conclui a decisao
// ===========================================================================
//
// Bug corrigido: uma fonte esperada em erro deixava `pending` verdadeiro para
// sempre. Como `pending` converte os mocks das demais fontes em carregamento
// neutro, `anyLoading` nunca encerrava e a interface podia ficar em
// "Atualizando…" indefinidamente. Erro e' uma CONCLUSAO: com ele, a demonstracao
// ja nao pode ser confirmada, entao nao ha o que esperar.

const ERRORED = fb({ errored: true });

test("T1. erro terminal atual + demais mock => false/false (nao trava)", () => {
  const d = decideDemoMode(
    demoInput({ quality: ERRORED }, { ml: fb({ resolvedKey: "S:ml" }) }),
  );
  assert.equal(d.demoMode, false, "demonstracao nao pode ser confirmada");
  assert.equal(d.pending, false, "a decisao esta TOMADA — os mocks viram indisponiveis");
});

test("T2. erro terminal atual + uma fonte ainda loading => false/false", () => {
  const d = decideDemoMode(
    demoInput({ quality: ERRORED, brands: LOADING }, { ml: fb({ resolvedKey: "S:ml" }) }),
  );
  assert.equal(d.demoMode, false);
  assert.equal(d.pending, false, "o erro resolve a questao antes de a outra fonte chegar");
});

test("T3. erro terminal numa SERIE selecionada => false/false", () => {
  const d = decideDemoMode(
    demoInput({}, { ml: fb({ resolvedKey: "S:ml", errored: true }) }),
  );
  assert.equal(d.demoMode, false);
  assert.equal(d.pending, false);
});

test("T4. erro com resolvedKey ANTIGO nao e terminal => false/true", () => {
  const agg = decideDemoMode(
    demoInput({ quality: fb({ errored: true, resolvedKey: "CHAVE_ANTIGA" }) }, { ml: fb({ resolvedKey: "S:ml" }) }),
  );
  assert.equal(agg.demoMode, false);
  assert.equal(agg.pending, true, "erro de outra requisicao nao conclui a atual");

  const serie = decideDemoMode(
    demoInput({}, { ml: fb({ errored: true, resolvedKey: "S:ml:ANTIGA" }) }),
  );
  assert.equal(serie.pending, true, "idem para serie com chave antiga");
});

test("T5. uma fonte live + outra com erro => false/false (live tem precedencia)", () => {
  const d = decideDemoMode(
    demoInput({ brands: LIVE, quality: ERRORED }, { ml: fb({ resolvedKey: "S:ml" }) }),
  );
  assert.equal(d.demoMode, false);
  assert.equal(d.pending, false);
});

test("T6. todas mock => true/false", () => {
  const d = decideDemoMode(demoInput({}, { ml: fb({ resolvedKey: "S:ml" }) }));
  assert.equal(d.demoMode, true);
  assert.equal(d.pending, false);
});

test("T7. estado ausente, sem live e sem erro => false/true", () => {
  const d = decideDemoMode(demoInput({}, {}, ["ml", "shopee"]));
  assert.equal(d.demoMode, false);
  assert.equal(d.pending, true);
});

test("T8. a classificacao das quatro categorias e explicita", () => {
  assert.equal(classifyFallbackSource(undefined, "K"), "pending", "estado ausente");
  assert.equal(classifyFallbackSource(fb({ loading: true }), "K"), "pending", "carregando");
  assert.equal(classifyFallbackSource(fb({ resolvedKey: "OUTRA" }), "K"), "pending", "chave antiga");
  assert.equal(classifyFallbackSource(fb({ errored: true, resolvedKey: "OUTRA" }), "K"), "pending", "erro antigo");
  assert.equal(classifyFallbackSource(fb({ errored: true }), "K"), "terminal_error");
  assert.equal(classifyFallbackSource(fb({ live: true }), "K"), "live");
  assert.equal(classifyFallbackSource(fb({ live: false }), "K"), "mock");
});

// --- Contraprova de wiring -------------------------------------------------

test("T9. com decisao terminal nao demonstrativa, mock usa ERROR e anyLoading pode encerrar", () => {
  const hook = codeOnly(read("src/hooks/useGerencialSources.ts"));

  // `demoPending` falso => o caminho de mock cai no ramo de ERRO, nao de loading.
  assert.match(hook, /loading: state\.loading \|\| \(mockNotAllowed && demoPending\)/);
  assert.match(hook, /error: state\.errored \|\| \(mockNotAllowed && !demoPending\)/);

  // A serie aplica a MESMA regra, senao `anyLoading` ficaria preso nela.
  assert.match(hook, /if \(status\.loading \|\| \(mockNotAllowed && demoPending\)\) seriesStatus = "loading";/);
  assert.match(hook, /else if \(status\.error \|\| mockNotAllowed\) seriesStatus = "error";/);

  // `anyLoading` deriva dos status derivados — logo, sem pending, ele encerra.
  assert.match(hook, /sourceStatuses\.some\(\(s\) => s\.loading\) \|\| series\.some\(\(s\) => s\.status === "loading"\)/);

  // Nenhuma fonte mock e renderizada fora de demonstracao: `data` so' sai fresco.
  assert.match(hook, /data: status\.fresh \? state\.data : null/);
  assert.match(hook, /points: seriesStatus === "fresh" \? \(state\?\.data\?\.points \?\? \[\]\) : \[\]/);

  // E a decisao continua vindo do modulo puro, sem varrer series fora da selecao.
  assert.match(hook, /decideDemoMode\(\{/);
  assert.doesNotMatch(hook, /Object\.values\(seriesState\)/);
});

test("T10. erro terminal nao esconde as fontes que responderam", () => {
  // Com erro em `quality` e as demais em mock, a decisao e' terminal: nenhuma
  // fonte fica "carregando", e o bloco de cada mock declara indisponibilidade.
  const d = decideDemoMode(demoInput({ quality: ERRORED }, { ml: fb({ resolvedKey: "S:ml" }) }));
  assert.equal(d.pending, false);
  // A regra do hook, aplicada a mao: mockNotAllowed && !pending => erro.
  const mockNotAllowed = !d.demoMode;
  assert.equal(mockNotAllowed && !d.pending, true, "mock viraria erro, nunca loading");
});
