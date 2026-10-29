/**
 * KPIS-COMPARACAO-1 — comparacao com o periodo anterior e tendencia nos cards
 * de KPI da Gerencial (`src/lib/gerencial/kpi-band.ts` e `kpi-trend.ts`).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { buildKpiBand, deltaPct } from "../src/lib/gerencial/kpi-band.ts";
import { tendencia, tendenciasDosKpis, ticketPorPonto, totaisDaSerie } from "../src/lib/gerencial/kpi-trend.ts";
import type { OverviewData } from "../src/lib/api-client.ts";
import type { Marketplace } from "../src/lib/mock-data.ts";

const ALL: Marketplace[] = ["tiktok", "ml", "shopee"];

function overview(patch: Partial<OverviewData> = {}): OverviewData {
  return {
    gmv: 19_607_780, tiktok_gmv: 11_625_519, ml_gmv: 4_396_347, shopee_gmv: 3_585_914,
    orders: 308_360, avg_ticket: 63.59, ad_spend: 345_337, ml_roas: 11.98, ml_cancel_rate_pct: 3,
    shopee_roas: 14.66, tiktok_customers: null, ml_unique_buyers: null, shopee_unique_buyers: null,
    gmv_mom_pct: -4.3, prev_gmv: 20_488_241,
    prev_orders: 333_588, prev_avg_ticket: 61.42, prev_ad_spend: 367_816, prev_ml_roas: 12.6, prev_shopee_roas: 13.64,
    ...patch,
  };
}

const porChave = (band: ReturnType<typeof buildKpiBand>) => Object.fromEntries(band.map((k) => [k.key, k]));

test("deltaPct so' com base positiva e finita", () => {
  assert.equal(deltaPct(110, 100), 10);
  assert.equal(deltaPct(1, 0), null);
  assert.equal(deltaPct(1, null), null);
  assert.equal(deltaPct(null, 1), null);
  assert.equal(deltaPct(Number.NaN, 1), null);
});

test("8c. com o periodo anterior no contrato, cada card compara", () => {
  const k = porChave(buildKpiBand(overview(), ALL));
  assert.ok(Math.abs(k.orders.deltaPct! - -7.56) < 0.01);
  assert.match(k.orders.reference!, /^antes /);
  assert.equal(k.orders.comparisonNote, null);
  assert.ok(Math.abs(k.avg_ticket.deltaPct! - 3.53) < 0.01);
  assert.match(k.avg_ticket.reference!, /^antes R\$/);
  assert.ok(Math.abs(k.ad_spend.deltaPct! - -6.11) < 0.01);
  assert.match(k.ad_spend.reference!, /^antes R\$/);
});

test("investimento em Ads e' neutro; pedidos e ticket: subir e' bom", () => {
  const k = porChave(buildKpiBand(overview(), ALL));
  assert.equal(k.ad_spend.deltaTone, "neutral");
  assert.equal(k.orders.deltaTone, "higher_is_better");
  assert.equal(k.avg_ticket.deltaTone, "higher_is_better");
});

test("ROAS compara por canal, nunca agregado", () => {
  const roas = porChave(buildKpiBand(overview(), ALL)).roas;
  assert.equal(roas.value, null);
  const ml = roas.channelValues!.find((c) => c.channel === "ml")!;
  const sh = roas.channelValues!.find((c) => c.channel === "shopee")!;
  assert.deepEqual([ml.direction, ml.previous], ["down", "12.6x"]);
  assert.deepEqual([sh.direction, sh.previous], ["up", "13.6x"]);
  const tk = roas.channelValues!.find((c) => c.channel === "tiktok")!;
  assert.equal(tk.direction ?? null, null);
});

test("comparar desligado: nenhum delta alem do GMV, e sem nota de indisponivel", () => {
  const k = porChave(buildKpiBand(overview(), ALL, false));
  for (const key of ["orders", "avg_ticket", "ad_spend"]) {
    assert.equal(k[key].deltaPct, null, key);
    assert.equal(k[key].comparisonNote, null, key);
  }
  assert.ok(k.roas.channelValues!.every((c) => c.direction == null));
});

test("anterior zerado ou ausente vira indisponivel, nunca delta", () => {
  const k = porChave(buildKpiBand(overview({ prev_orders: 0, prev_avg_ticket: null }), ALL));
  assert.equal(k.orders.deltaPct, null);
  assert.equal(k.orders.comparisonNote, "Comparação indisponível");
  assert.equal(k.avg_ticket.deltaPct, null);
});

// ---------------------------------------------------------------------------
// Tendencia
// ---------------------------------------------------------------------------

test("totaisDaSerie corta a cauda sem dado (o hoje zerado do Mes atual)", () => {
  assert.deepEqual(totaisDaSerie([{ total: 5 }, { total: null }, { total: 3 }, { total: 0 }, { total: null }]), [5, null, 3]);
  assert.deepEqual(totaisDaSerie([]), []);
});

test("tendencia: ultimos 7 contra os 7 anteriores", () => {
  const pontos = [...Array(7).fill(100), ...Array(7).fill(110)];
  const t = tendencia(pontos, "day")!;
  assert.ok(Math.abs(t.variacaoPct! - 10) < 1e-9);
  assert.equal(t.direcao, "up");
  assert.equal(t.rotulo, "últimos 7 dias vs 7 anteriores");
});

test("tendencia em periodo curto usa metade contra metade, e sem ponto suficiente nao existe", () => {
  const t = tendencia([10, 10, 10, 9, 9, 9], "week")!;
  assert.equal(t.rotulo, "últimos 3 semanas vs 3 anteriores");
  assert.equal(t.direcao, "down");
  assert.equal(tendencia([1, 2, 3], "day"), null);
  assert.equal(tendencia([100, 100, 100.2, 100.2], "day")!.direcao, "flat");
});

test("ticket ponto a ponto e' GMV / pedidos, so' onde ha' pedido", () => {
  assert.deepEqual(ticketPorPonto([100, 50, null, 30], [2, 0, 3, 3]), [50, null, null, 10]);
});

test("tendenciasDosKpis monta pedidos e ticket a partir das duas series", () => {
  const gmv = { granularity: "day", buckets: [...Array(14)].map((_, i) => ({ total: i < 7 ? 1000 : 1200 })) };
  const ped = { granularity: "day", buckets: [...Array(14)].map(() => ({ total: 10 })) };
  const t = tendenciasDosKpis(gmv, ped);
  assert.deepEqual(Object.keys(t).sort(), ["avg_ticket", "orders"]);
  assert.equal(t.orders!.direcao, "flat");
  assert.ok(Math.abs(t.avg_ticket!.variacaoPct! - 20) < 1e-9);
});

test("o card usa o tom do KPI e a tendencia; a pagina passa as tendencias", () => {
  const card = readFileSync(new URL("../src/components/gerencial/KpiBand.tsx", import.meta.url), "utf8");
  assert.match(card, /<DeltaBadge pct=\{kpi\.deltaPct\} tone=\{kpi\.deltaTone\} \/>/);
  assert.match(card, /<LinhaDeTendencia /);
  const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(page, /tendencias=\{tendencias\}/);
  assert.match(page, /buildKpiBand\(overview, filters\.channels, filters\.compare\)/);
});

test("o detalhe do card usa a mesma comparacao do card", () => {
  const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(page, /comparacao=\{kpis\.find\(\(k\) => k\.key === dialog\.key\)\}/);
  const det = readFileSync(new URL("../src/components/KpiDrilldownContent.tsx", import.meta.url), "utf8");
  assert.match(det, /<KpiReference kind=\{kind\} overview=\{overview\} comparacao=\{comparacao\} \/>/);
});

test("o card nao esconde valor e variacao do leitor de tela", () => {
  const card = readFileSync(new URL("../src/components/gerencial/KpiBand.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(card, /aria-label=\{`\$\{kpi\.label\} — abrir decomposição`\}/);
  assert.match(card, /<span className="sr-only"> \(abrir decomposição\)<\/span>/);
});

test("tendencia e' so' um indicativo: tende a subir / descer / estavel", () => {
  const card = readFileSync(new URL("../src/components/gerencial/KpiBand.tsx", import.meta.url), "utf8");
  for (const t of ["↗ tende a subir", "↘ tende a descer", "→ estável"]) assert.ok(card.includes(t), t);
  // o detalhe numerico sai do texto e vai para o title
  assert.doesNotMatch(card, />\s*Tendência:/);
  assert.match(card, /title=\{detalhe\}/);
});

test("DM Sans no corpo e Inter so' nas tabelas (DM Sans nao tem tnum)", () => {
  const layout = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
  assert.match(layout, /DM_Sans\(\{ subsets: \["latin"\], variable: "--font-sans"/);
  assert.match(layout, /Inter\(\{ subsets: \["latin"\], variable: "--font-num"/);
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /table \{\s*font-family: var\(--font-num\)/);
});
