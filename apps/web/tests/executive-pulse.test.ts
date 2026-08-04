// Testes do modulo puro executive-pulse.ts (Gate G1). node:test, sem React.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildPulse, groupKeyOf, formatMetricByType,
  formatReferenceValue, formatDeltaAbs, formatDeltaPct, metricLabel,
} from "../src/lib/executive-pulse.ts";
import type { ExecutiveInsight, ExecutiveDataWarning, ExecutiveSummaryData } from "../src/lib/api-client.ts";

function ins(p: Partial<ExecutiveInsight>): ExecutiveInsight {
  return {
    type: "growth", severity: "info", title: "t", description: "d",
    brand: null, marketplace: null, metric_value: null, href: "/",
    category: null, reference_value: null, reference_kind: null,
    delta_abs: null, delta_pct: null, confidence_note: null,
    source: null, last_date: null, threshold_days: null, staleness_days: null,
    ...p,
  };
}
function warn(p: Partial<ExecutiveDataWarning>): ExecutiveDataWarning {
  return { type: "not_applicable", severity: "info", message: "m", href: "/regioes", category: "data_confidence", ...p };
}
function data(p: Partial<ExecutiveSummaryData>): ExecutiveSummaryData {
  return {
    period: { date_from: "2026-07-01", date_to: "2026-07-31", compare_date_from: null, compare_date_to: null, refreshed_at: null },
    health: { status: "ok", gmv: 1, gmv_mom_pct: 0, orders: 1, avg_ticket: 1, summary: "" },
    changes: [], risks: [], data_warnings: [],
    ...p,
  };
}

test("categoriza performance/eficiencia/dado separadamente", () => {
  const p = buildPulse(data({
    changes: [ins({ type: "growth", category: "performance", brand: "a" })],
    risks: [
      ins({ type: "high_cost", category: "efficiency_ops", marketplace: "ml", metric_value: 30, delta_abs: 10, severity: "warning" }),
      ins({ type: "stale_data", category: "data_confidence", marketplace: "shopee", source: "fact_marketplace_daily_performance", severity: "warning" }),
    ],
  }));
  const cats = p.commercial.map((g) => g.category).sort();
  assert.deepEqual([...new Set(cats)].sort(), ["efficiency_ops", "performance"]);
  assert.equal(p.dataConfidence.count, 1);
});

test("growth/drop de marcas distintas nunca colapsam", () => {
  const p = buildPulse(data({
    changes: [
      ins({ type: "drop", category: "performance", brand: "a", severity: "warning", delta_pct: -12 }),
      ins({ type: "drop", category: "performance", brand: "b", severity: "warning", delta_pct: -15 }),
    ],
  }));
  const drops = p.commercial.filter((g) => g.type === "drop");
  assert.equal(drops.length, 2);
  assert.equal(drops.every((g) => g.count === 1), true);
});

test("cinco high_cost do mesmo canal viram UM grupo com titulo proprio", () => {
  const risks = ["a", "b", "c", "d", "e"].map((b) =>
    ins({ type: "high_cost", category: "efficiency_ops", brand: b, marketplace: "tiktok", metric_value: 30, delta_abs: 5, reference_value: 25, reference_kind: "p75", severity: "warning" }),
  );
  const p = buildPulse(data({ risks }));
  const cost = p.commercial.filter((g) => g.type === "high_cost");
  assert.equal(cost.length, 1);
  assert.equal(cost[0].count, 5);
  assert.match(cost[0].title, /5 marcas com custo no p75 ou acima no TikTok Shop/);
  // titulo do grupo NAO reusa o titulo da marca representante
  assert.notEqual(cost[0].title, cost[0].representative.title);
});

test("titulo agrupado e' verdadeiro quando membros estao EXATAMENTE no p75 (delta_abs=0)", () => {
  // Regra aceita current >= p75; membros com delta_abs=0 estao NO p75, nao
  // "acima da referencia" — o titulo nunca pode afirmar "acima da referência".
  const risks = ["a", "b"].map((b) =>
    ins({ type: "high_cost", category: "efficiency_ops", brand: b, marketplace: "ml", metric_value: 25, delta_abs: 0, reference_value: 25, reference_kind: "p75", severity: "warning" }),
  );
  const cost = buildPulse(data({ risks })).commercial.find((g) => g.type === "high_cost");
  assert.ok(cost);
  assert.doesNotMatch(cost.title, /acima da referência/);
  assert.match(cost.title, /2 marcas com custo no p75 ou acima no Mercado Livre/);
});

test("stale diario e regional nao colapsam (source diferente)", () => {
  const p = buildPulse(data({
    risks: [
      ins({ type: "stale_data", category: "data_confidence", marketplace: "shopee", source: "fact_marketplace_daily_performance", severity: "warning" }),
      ins({ type: "stale_data", category: "data_confidence", marketplace: null, source: "fact_marketplace_region_daily", severity: "warning" }),
    ],
  }));
  assert.equal(p.dataConfidence.groups.length, 2);
});

test("top 3 considera apenas comercial; confianca no dado fica separada", () => {
  const p = buildPulse(data({
    changes: [
      ins({ type: "drop", category: "performance", brand: "a", severity: "warning", delta_pct: -12 }),
      ins({ type: "growth", category: "performance", brand: "b", severity: "info", delta_pct: 50 }),
    ],
    risks: [
      ins({ type: "stale_data", category: "data_confidence", marketplace: "shopee", source: "s1", severity: "warning" }),
      ins({ type: "low_regional_coverage", category: "data_confidence", severity: "warning" }),
    ],
    data_warnings: [warn({})],
  }));
  assert.equal(p.top.length, 2);            // so' os 2 comerciais
  assert.equal(p.top.every((g) => g.category !== "data_confidence"), true);
  assert.equal(p.dataConfidence.count, 3);  // 2 riscos de dado + 1 warning
});

test("ordenacao usa prioridade fixa por tipo, nunca compara unidades diferentes", () => {
  // Mesma severidade (warning); high_cost tem magnitude bem maior, mas drop
  // vem primeiro por prioridade de tipo (drop < high_cancel < high_cost).
  const p = buildPulse(data({
    changes: [ins({ type: "drop", category: "performance", brand: "z", severity: "warning", delta_pct: -11 })],
    risks: [
      ins({ type: "high_cost", category: "efficiency_ops", brand: "a", marketplace: "ml", severity: "warning", metric_value: 99, delta_abs: 90 }),
      ins({ type: "high_cancel_rate", category: "efficiency_ops", brand: "b", marketplace: "ml", severity: "warning", metric_value: 20, delta_abs: 14 }),
    ],
  }));
  assert.deepEqual(p.commercial.map((g) => g.type), ["drop", "high_cancel_rate", "high_cost"]);
});

test("severidade domina a prioridade de tipo", () => {
  const p = buildPulse(data({
    changes: [ins({ type: "growth", category: "performance", brand: "g", severity: "critical", delta_pct: 80 })],
    risks: [ins({ type: "drop", category: "performance", brand: "d", severity: "warning", delta_pct: -12 })],
  }));
  // growth critical vem antes de drop warning, apesar de drop ter prioridade
  // de tipo menor — severidade e' o primeiro criterio.
  assert.equal(p.commercial[0].type, "growth");
});

test("formatMetricByType formata por tipo e trata nulo", () => {
  assert.equal(formatMetricByType("growth", 12.34), "+12.3%");
  assert.equal(formatMetricByType("drop", -8.2), "-8.2%");
  assert.equal(formatMetricByType("high_cost", 30.0), "30.0%");
  assert.equal(formatMetricByType("stale_data", 4), "4 dia(s)");
  assert.equal(formatMetricByType("growth", null), "—");
});

test("payload legado sem campos aditivos recebe fallback seguro (categoria por tipo)", () => {
  // Sem `category` — deriva do tipo. high_cost -> efficiency_ops (comercial),
  // stale_data -> data_confidence.
  const legacy = data({
    risks: [
      { type: "high_cost", severity: "warning", title: "custo", description: "", brand: "a", marketplace: "ml", metric_value: 30, href: "/canais" } as ExecutiveInsight,
      { type: "stale_data", severity: "warning", title: "stale", description: "", brand: null, marketplace: "shopee", metric_value: 5, href: "/canais" } as ExecutiveInsight,
    ],
  });
  const p = buildPulse(legacy);
  assert.equal(p.commercial.length, 1);
  assert.equal(p.commercial[0].type, "high_cost");
  assert.equal(p.dataConfidence.count, 1);
});

test("groupKeyOf separa por regra: marca (growth) x canal (custo) x source (stale)", () => {
  const gA = groupKeyOf({ type: "growth", brand: "a", marketplace: null, category: "performance" } as never);
  const gB = groupKeyOf({ type: "growth", brand: "b", marketplace: null, category: "performance" } as never);
  assert.notEqual(gA, gB); // marcas distintas -> chaves distintas
  const c1 = groupKeyOf({ type: "high_cost", brand: "a", marketplace: "ml", category: "efficiency_ops" } as never);
  const c2 = groupKeyOf({ type: "high_cost", brand: "b", marketplace: "ml", category: "efficiency_ops" } as never);
  assert.equal(c1, c2); // mesmo canal -> mesma chave (agrupa marcas)
});

test("buildPulse com null nao quebra", () => {
  const p = buildPulse(null);
  assert.deepEqual(p.top, []);
  assert.equal(p.dataConfidence.count, 0);
  assert.equal(p.dataUnavailable, false);
});

// --- Finding 1: formatacao ORIENTADA AO CAMPO (unidades por campo) ---

test("formatMetricByType (valor principal) por tipo", () => {
  assert.equal(formatMetricByType("growth", 12.3), "+12.3%");
  assert.equal(formatMetricByType("high_cost", 30), "30.0%");
  assert.equal(formatMetricByType("stale_data", 4), "4 dia(s)");
  assert.equal(formatMetricByType("missing_data", 0), "—"); // nunca destaca "0"
  assert.equal(formatMetricByType("not_applicable", 0), "—");
});

test("formatReferenceValue: growth em BRL, custo em %, ausente em —", () => {
  assert.equal(formatReferenceValue("growth", 20_000), "R$ 20K");   // GMV anterior em moeda
  assert.equal(formatReferenceValue("drop", 50_000), "R$ 50K");
  assert.equal(formatReferenceValue("high_cost", 25), "25.0%");
  assert.equal(formatReferenceValue("high_cancel_rate", 6), "6.0%");
  assert.equal(formatReferenceValue("missing_data", 0), "—");
});

test("formatDeltaAbs: growth em BRL com sinal, custo/cancelamento em p.p.", () => {
  assert.equal(formatDeltaAbs("growth", 30_000), "+R$ 30K");        // diferenca de GMV em moeda
  assert.equal(formatDeltaAbs("high_cost", 5), "+5.0 p.p.");
  assert.equal(formatDeltaAbs("high_cost", 0), "+0.0 p.p.");        // custo == p75 (Finding 2)
  assert.equal(formatDeltaAbs("high_cancel_rate", -2), "-2.0 p.p.");
  assert.equal(formatDeltaAbs("missing_data", 0), "—");
});

test("formatDeltaPct sempre percentual com sinal", () => {
  assert.equal(formatDeltaPct(150), "+150.0%");
  assert.equal(formatDeltaPct(-8.2), "-8.2%");
  assert.equal(formatDeltaPct(null), "—");
});

test("metricLabel: 'Variação' para growth/drop, 'Defasagem' para stale", () => {
  assert.equal(metricLabel("growth"), "Variação");
  assert.equal(metricLabel("drop"), "Variação");
  assert.equal(metricLabel("stale_data"), "Defasagem");
  assert.equal(metricLabel("high_cost"), "Valor atual");
});

// --- Finding 5: severidade consolidada = pior de TODOS os membros ---

test("severidade do grupo/dado usa o pior membro, nao o representante por magnitude", () => {
  // Mesma chave (stale_data, mesma source/marketplace) -> um grupo. O
  // representante (maior magnitude) e' o INFO (staleness 10); um membro e'
  // WARNING (staleness 2). O pior (warning) deve prevalecer.
  const p = buildPulse(data({
    risks: [
      ins({ type: "stale_data", category: "data_confidence", marketplace: "shopee", source: "s1", severity: "info", metric_value: 10 }),
      ins({ type: "stale_data", category: "data_confidence", marketplace: "shopee", source: "s1", severity: "warning", metric_value: 2 }),
    ],
  }));
  assert.equal(p.dataConfidence.groups.length, 1);
  assert.equal(p.dataConfidence.groups[0].representative.metric_value, 10); // representante = maior magnitude (info)
  assert.equal(p.dataConfidence.groups[0].severity, "warning");             // mas severidade = pior membro
  assert.equal(p.dataConfidence.severity, "warning");
});

// --- Finding 3: dataUnavailable quando ha missing_data ---

test("dataUnavailable = true quando o payload traz missing_data", () => {
  const p = buildPulse(data({
    risks: [ins({ type: "missing_data", category: "data_confidence", severity: "critical", metric_value: null })],
  }));
  assert.equal(p.dataUnavailable, true);
  const p2 = buildPulse(data({ risks: [ins({ type: "stale_data", category: "data_confidence", marketplace: "ml", source: "s", severity: "warning" })] }));
  assert.equal(p2.dataUnavailable, false);
});

// --- Finding 4: evidencia de stale_data propaga ate o membro ---

test("campos de stale_data (source/last_date/threshold/staleness) chegam ao grupo", () => {
  const p = buildPulse(data({
    risks: [ins({
      type: "stale_data", category: "data_confidence", marketplace: "shopee",
      source: "fact_marketplace_daily_performance", last_date: "2026-07-20",
      threshold_days: 3, staleness_days: 12, severity: "warning", metric_value: 12,
    })],
  }));
  const m = p.dataConfidence.groups[0].representative;
  assert.equal(m.source, "fact_marketplace_daily_performance");
  assert.equal(m.last_date, "2026-07-20");
  assert.equal(m.threshold_days, 3);
  assert.equal(m.staleness_days, 12);
});
