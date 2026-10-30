/**
 * KPIS-COMPARACAO-1 — comparacao com o periodo anterior e tendencia nos cards
 * de KPI da Gerencial (`src/lib/gerencial/kpi-band.ts` e `kpi-trend.ts`).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { buildKpiBand, deltaPct } from "../src/lib/gerencial/kpi-band.ts";
import { fmtBrlCurto, fmtNumCurto, fmtPctBr, fmtVezes } from "../src/lib/formatters.ts";
import { suavizar, tendencia, tendenciasDosKpis, ticketPorPonto, totaisDaSerie } from "../src/lib/gerencial/kpi-trend.ts";
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
  assert.deepEqual([ml.direction, ml.previous], ["down", "12,6x"]);
  assert.deepEqual([sh.direction, sh.previous], ["up", "13,6x"]);
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
  assert.match(page, /buildKpiBand\(overview, filters\.channels, comparar\)/);
  // POPUPS-1 — dia em andamento contra dia inteiro nao e' base justa.
  assert.match(page, /const comparar = filters\.compare && !diaEmAndamento;/);
  assert.match(page, /filters\.dateFrom === filters\.dateTo && filters\.dateTo === toISODate\(new Date\(\)\)/);
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

test("tendencia e' so' o mini grafico com ponto final, sem texto visivel", () => {
  const card = readFileSync(new URL("../src/components/gerencial/KpiBand.tsx", import.meta.url), "utf8");
  // nenhum rotulo visivel de tendencia
  assert.doesNotMatch(card, /↗ tende a subir|↘ tende a descer/);
  // o leitor de tela ainda ouve a direcao; o mouse ve' a conta no title
  assert.match(card, /<span className="sr-only">Tendência: \{DIRECAO_FALADA\[t\.direcao\]\}\.<\/span>/);
  assert.match(card, /title=\{detalhe\}/);
  // ponto final do grafico
  assert.match(card, /rounded-\[1px\] bg-current/);
});

test("DM Sans no corpo e Inter so' nas tabelas (DM Sans nao tem tnum)", () => {
  const layout = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
  assert.match(layout, /DM_Sans\(\{ subsets: \["latin"\], variable: "--font-sans"/);
  assert.match(layout, /Inter\(\{ subsets: \["latin"\], variable: "--font-num"/);
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /table \{\s*font-family: var\(--font-num\)/);
});

test("aviso do bloco principal corta na oracao, nunca no ponto decimal", async () => {
  const { primeiraOracao } = await import("../src/components/gerencial/MetaHero.tsx").catch(() => ({ primeiraOracao: null as never }));
  // MetaHero e' TSX (sem execucao no node --test): confere a regra pelo codigo.
  void primeiraOracao;
  const src = readFileSync(new URL("../src/components/gerencial/MetaHero.tsx", import.meta.url), "utf8");
  assert.match(src, /texto\.split\(\/\[;\.\]\(\?=\\s\|\$\)\/\)/);
  const re = /[;.](?=\s|$)/;
  assert.equal("Custo/GMV de 16.7% no canal. Mais".split(re)[0], "Custo/GMV de 16.7% no canal");
  assert.equal("KOKESHI no Shopee tem dados até 12/10/2026; o canal".split(re)[0], "KOKESHI no Shopee tem dados até 12/10/2026");
});

test("hover usa sombra preta nos dois temas", () => {
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  const bloco = css.slice(css.indexOf(".card-hover:hover"), css.indexOf(".card-hover:active"));
  assert.doesNotMatch(bloco, /--tc-ink/);
  assert.match(bloco, /rgb\(0 0 0 \/ 0\.35\)/);
});

test("a dica dentro do card nao e' focavel nem entra no nome do botao", () => {
  const card = readFileSync(new URL("../src/components/gerencial/KpiBand.tsx", import.meta.url), "utf8");
  assert.match(card, /<InfoDica emControle /);
  const dica = readFileSync(new URL("../src/components/ui/InfoDica.tsx", import.meta.url), "utf8");
  assert.match(dica, /tabIndex=\{0\}/);
  assert.match(dica, /onFocus=\{abrir\}/);
});

test("T-0028: o balao do ⓘ sai do card por portal (fixed relativo a' tela)", () => {
  // `.card-hover:hover` e a entrada `.tc-entrada` poem `transform` nos
  // ancestrais; dentro deles um `fixed` deixa de ser relativo a' viewport e o
  // balao abria longe do "ⓘ", fora da tela (medido: +488px em 390, rolagem de 8px).
  const dica = readFileSync(new URL("../src/components/ui/InfoDica.tsx", import.meta.url), "utf8");
  assert.match(dica, /import \{ createPortal \} from "react-dom";/);
  const portal = dica.slice(dica.indexOf("createPortal("));
  assert.match(portal, /^createPortal\(\s*<span\s+ref=\{balao\}/, "o balao e' o filho do portal");
  assert.match(portal, /<\/span>,\s*document\.body,\s*\)/, "destino: document.body");
  // o comportamento do colaborador fica: fixed, clamp pela largura, animacao
  assert.match(portal, /pointer-events-none fixed z-50/);
  assert.match(portal, /dica-entra_160ms/);
});

test("linha do mini grafico e' media movel de 7; serie curta fica como esta'", () => {
  const serra = [...Array(14)].map((_, i) => (i % 2 ? 110 : 90));
  const lisa = suavizar(serra);
  assert.equal(lisa.length, 8);
  assert.ok(lisa.every((v) => v > 95 && v < 105), "a serrilha some");
  assert.deepEqual(suavizar([1, 2, 3]), [1, 2, 3]);
});

test("numeros curtos em pt-BR (escolha: so' o numero curto)", () => {
  assert.equal(fmtNumCurto(308_360), "308,4 mil");
  assert.equal(fmtNumCurto(1_240_000), "1,24 mi");
  assert.equal(fmtNumCurto(850), "850");
  assert.equal(fmtBrlCurto(345_336.98), "R$ 345,3 mil");
  assert.equal(fmtBrlCurto(19_607_780), "R$ 19,61 mi");
  assert.equal(fmtBrlCurto(63.587), "R$ 63,59");
  assert.equal(fmtPctBr(-11.13), "−11,1%");
  assert.equal(fmtPctBr(3.55), "+3,6%");
  assert.equal(fmtPctBr(0), "0,0%");
  assert.equal(fmtVezes(12), "12,0x");
  const k = porChave(buildKpiBand(overview(), ALL));
  assert.equal(k.orders.value, "308,4 mil");
  assert.equal(k.ad_spend.value, "R$ 345,3 mil");
  assert.equal(k.orders.reference, "antes 333,6 mil");
});

test("fronteira de unidade: nunca \"1.000,0 mil\"", () => {
  assert.equal(fmtNumCurto(999_960), "1,0 mi");
  assert.equal(fmtNumCurto(999_400), "999,4 mil");
  assert.equal(fmtNumCurto(999.6), "1,0 mil");
  assert.equal(fmtBrlCurto(999_960), "R$ 1,00 mi");
  assert.equal(fmtBrlCurto(999.999), "R$ 1,0 mil");
  assert.equal(fmtBrlCurto(999_999_999), "R$ 1,00 bi");
});

test("o detalhe do card recebe o valor exato", () => {
  const k = porChave(buildKpiBand(overview(), ALL));
  assert.equal(k.orders.exactValue, "308.360");
  // Intl poe espaco nao separavel entre "R$" e o numero.
  assert.match(k.ad_spend.exactValue!, /^R\$\s345\.337$/);
  const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(page, /return k\?\.exactValue \?\? k\?\.value \?\? "—";/);
});
