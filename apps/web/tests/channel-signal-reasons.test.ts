// Testes da explicação humana dos sinais marca × canal (Gate G2, Task 2 —
// ver docs/DRILLDOWN_ARCHITECTURE.md §5). Cobrem: sem sinal, custo alto com
// evidência, igualdade exata com o p75 ("no p75 ou acima"), múltiplos
// sinais, métrica/referência ausente, null ≠ zero e fallback para sinal
// desconhecido. A função é pura: nunca cria threshold nem reclassifica
// severidade — só descreve dados já carregados.
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildChannelDiagnosis } from "../src/lib/channel-signal-reasons.ts";
import type { CanaisChannelMedian, CanaisChannelRow } from "../src/lib/api-client.ts";

function row(overrides: Partial<CanaisChannelRow> = {}): CanaisChannelRow {
  return {
    brand: "kokeshi", label: "KOKESHI", channel: "shopee", channel_label: "Shopee",
    gmv: 100_000, orders: 1_500, ad_spend: 5_000, ad_revenue: 25_000,
    ads_gmv_pct: 5.0, roas: 5.0, acos_pct: 20.0,
    marketplace_cost_pct: 18.0, seller_shipping_pct: 8.0,
    ads_available: true, marketplace_cost_available: true, seller_shipping_available: true,
    ads_applicable: true, marketplace_cost_applicable: true, seller_shipping_applicable: true,
    data_warning: null, signals: [],
    ...overrides,
  };
}

function median(overrides: Partial<CanaisChannelMedian> = {}): CanaisChannelMedian {
  return {
    channel: "shopee", channel_label: "Shopee",
    gmv_median: 80_000, ads_gmv_pct_median: 6.0, roas_median: 4.0,
    marketplace_cost_pct_median: 15.0, marketplace_cost_pct_p75: 17.5,
    seller_shipping_pct_median: 7.0, seller_shipping_pct_p75: 9.0,
    brands_with_data: 5,
    ...overrides,
  };
}

test("sem sinais: headline neutra, zero explicacoes, sem nextAction", () => {
  const d = buildChannelDiagnosis(row(), median());
  assert.match(d.headline, /Nenhum sinal de atenção/);
  assert.equal(d.explanations.length, 0);
  assert.equal(d.nextAction, null);
});

test("custo alto com evidencia: valor + p75 + mediana do MESMO canal", () => {
  const d = buildChannelDiagnosis(row({ signals: ["custo_alto"], marketplace_cost_pct: 18.0 }), median());
  assert.equal(d.explanations.length, 1);
  const r = d.explanations[0].reason;
  assert.match(r, /18,0%/);                 // valor da linha
  assert.match(r, /p75 do canal \(17,5%\) ou acima/); // 18 > 17.5
  assert.match(r, /mediana: 15,0%/);        // mediana do mesmo canal
  assert.match(d.headline, /custo alto/i);
  assert.ok(d.nextAction, "sinal de atenção comercial deve sugerir próximo passo");
});

test("igualdade exata com o p75 é descrita como 'no p75 ou acima', nunca 'acima'", () => {
  const d = buildChannelDiagnosis(
    row({ signals: ["custo_alto"], marketplace_cost_pct: 17.5 }),
    median({ marketplace_cost_pct_p75: 17.5 }),
  );
  assert.match(d.explanations[0].reason, /no p75 do canal \(17,5%\) ou acima/);
  assert.doesNotMatch(d.explanations[0].reason, /abaixo do p75/);
});

test("sinal 'alto' com valor ABAIXO do p75 carregado: inconsistência explícita, sem reclassificar nem fingir corte", () => {
  // Contraprova (Task 3, Finding 2): o backend só emite frete_alto com
  // valor >= p75; se a referência carregada não confirma o corte, a
  // explicação DIZ a inconsistência — nunca "ou acima", nunca silêncio.
  const d = buildChannelDiagnosis(
    row({ signals: ["frete_alto"], seller_shipping_pct: 8.0 }),
    median({ seller_shipping_pct_p75: 9.0 }),
  );
  const r = d.explanations[0].reason;
  assert.match(r, /abaixo do p75 do canal carregado \(9,0%\)/);
  assert.match(r, /inconsistência entre o sinal recebido e a referência exibida/);
  assert.doesNotMatch(r, /ou acima/);
  // o sinal NÃO é reclassificado: headline continua reportando o sinal do canal
  assert.match(d.headline, /frete alto/i);
});

test("multiplos sinais: uma explicacao por sinal, ordem preservada, headline separa atencao de destaque", () => {
  const d = buildChannelDiagnosis(row({ signals: ["roas_forte", "custo_alto", "frete_alto"] }), median());
  assert.equal(d.explanations.length, 3);
  assert.deepEqual(d.explanations.map((e) => e.signal), ["roas_forte", "custo_alto", "frete_alto"]);
  assert.match(d.headline, /Atenção no período: custo alto, frete alto\./i);
  assert.match(d.headline, /Destaque: ROAS forte\./);
});

test("metrica ausente (null): diz claramente que nao da para detalhar — nunca zero fabricado", () => {
  const d = buildChannelDiagnosis(row({ signals: ["custo_alto"], marketplace_cost_pct: null }), median());
  assert.match(d.explanations[0].reason, /não permitem detalhar a causa/);
  assert.doesNotMatch(d.explanations[0].reason, /0,0%/);
});

test("referencia ausente (mediana/p75 null): valor aparece e a indisponibilidade é explicita", () => {
  const d = buildChannelDiagnosis(row({ signals: ["custo_alto"], marketplace_cost_pct: 18.0 }), null);
  assert.match(d.explanations[0].reason, /18,0%/);
  assert.match(d.explanations[0].reason, /referência do canal indisponível/);
});

// ---------------------------------------------------------------------------
// ads_subutilizado — explicação completa espelhando a regra real do backend
// (Task 3, Finding 1): GMV vs mediana de GMV + Ads/GMV vs mediana + ROAS/gasto.
// ---------------------------------------------------------------------------
test("ads_subutilizado: GMV na mediana ou acima + Ads/GMV abaixo da mediana", () => {
  const d = buildChannelDiagnosis(
    row({ signals: ["ads_subutilizado"], gmv: 100_000, ads_gmv_pct: 4.7, roas: null, ad_spend: 1_000 }),
    median({ gmv_median: 80_000, ads_gmv_pct_median: 6.0, roas_median: 4.0 }),
  );
  const r = d.explanations[0].reason;
  assert.match(r, /GMV de R\$\s?100\.000 na mediana do canal \(R\$\s?80\.000\) ou acima/);
  assert.match(r, /investimento em Ads de 4,7% do GMV, abaixo da mediana do canal \(6,0%\)/);
  assert.match(r, /ROAS indisponível/); // roas null com gasto > 0: dito, nunca inventado
});

test("ads_subutilizado: ROAS na mediana ou acima aparece como evidencia (corte inclusivo)", () => {
  const d = buildChannelDiagnosis(
    row({ signals: ["ads_subutilizado"], roas: 4.0, ads_gmv_pct: 3.0 }),
    median({ roas_median: 4.0 }),
  );
  assert.match(d.explanations[0].reason, /ROAS de 4,00x na mediana do canal \(4,00x\) ou acima/);
});

test("ads_subutilizado: gasto de Ads igual a zero é a evidencia quando presente", () => {
  const d = buildChannelDiagnosis(
    row({ signals: ["ads_subutilizado"], ad_spend: 0, ads_gmv_pct: 0, roas: null }),
    median(),
  );
  const r = d.explanations[0].reason;
  assert.match(r, /sem gasto de Ads no período/);
  assert.match(r, /0,0% do GMV/); // zero real segue sendo número, nunca "indisponível"
});

test("ads_subutilizado: referencia parcial — mediana de GMV ausente é dita explicitamente", () => {
  const d = buildChannelDiagnosis(
    row({ signals: ["ads_subutilizado"], ads_gmv_pct: 4.0 }),
    median({ gmv_median: null }),
  );
  const r = d.explanations[0].reason;
  assert.match(r, /mediana de GMV do canal indisponível/);
  assert.match(r, /investimento em Ads de 4,0% do GMV/); // o que existe continua aparecendo
});

test("ads_subutilizado: null nunca vira zero — percentual ausente é parte da regra e é dito", () => {
  const d = buildChannelDiagnosis(row({ signals: ["ads_subutilizado"], ads_gmv_pct: null }), median());
  const r = d.explanations[0].reason;
  assert.match(r, /percentual de Ads indisponível \(a regra do canal trata a ausência como subutilização\)/);
  assert.doesNotMatch(r, /0,0% do GMV/);
});

test("sem_dado: usa o data_warning quando existir e sugere conferir cobertura antes de investigar", () => {
  const d = buildChannelDiagnosis(
    row({ signals: ["sem_dado"], data_warning: "Custo de marketplace indisponível para ML no período." }),
    median(),
  );
  assert.equal(d.explanations[0].reason, "Custo de marketplace indisponível para ML no período.");
  assert.match(d.nextAction ?? "", /observação de cobertura/);
});

test("sinal desconhecido: fallback seguro, nunca quebra nem inventa causa", () => {
  const d = buildChannelDiagnosis(row({ signals: ["sinal_novo_do_backend"] }), median());
  assert.equal(d.explanations.length, 1);
  assert.match(d.explanations[0].reason, /Sinal sem explicação mapeada/);
  assert.match(d.headline, /Outros sinais: sinal_novo_do_backend\./);
});

test("roas_forte sem mediana: valor proprio + indisponibilidade da referencia", () => {
  const d = buildChannelDiagnosis(row({ signals: ["roas_forte"], roas: 8.12 }), median({ roas_median: null }));
  assert.match(d.explanations[0].reason, /8,12x/);
  assert.match(d.explanations[0].reason, /mediana do canal indisponível/);
});
