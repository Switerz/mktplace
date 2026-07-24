// Testes da formatacao/rotulos da matriz comparativa marca x canal da aba
// Canais (Gate 2, docs/sections/canais_audit.md secao 14). Garante os 3
// estados do contrato: N/A (nao aplicavel), Sem dado (aplicavel mas
// ausente) e valor real (inclusive zero real, que NUNCA deve virar "N/A"
// ou "Sem dado").
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  formatChannelMetric,
  signalLabel,
  signalTone,
  findChannelMedian,
  CHANNEL_SIGNAL_LABEL,
} from "../src/lib/canais-channel-metrics.ts";
import type { CanaisChannelMedian } from "../src/lib/api-client.ts";

const pct1 = (v: number) => `${v.toFixed(1)}%`;

test("nao aplicavel -> N/A, independente do valor", () => {
  const r = formatChannelMetric(0, false, false, pct1);
  assert.deepEqual(r, { text: "N/A", tone: "muted" });
});

test("aplicavel mas indisponivel -> Sem dado", () => {
  const r = formatChannelMetric(null, true, false, pct1);
  assert.deepEqual(r, { text: "Sem dado", tone: "warning" });
});

test("aplicavel e disponivel mas valor nulo (denominador zero) -> travessao, nunca 0%", () => {
  const r = formatChannelMetric(null, true, true, pct1);
  assert.deepEqual(r, { text: "—", tone: "muted" });
});

test("zero real (aplicavel, disponivel, valor=0) e exibido como 0.0%, nao como ausencia", () => {
  const r = formatChannelMetric(0, true, true, pct1);
  assert.deepEqual(r, { text: "0.0%", tone: "value" });
});

test("valor real positivo formatado normalmente", () => {
  const r = formatChannelMetric(12.345, true, true, pct1);
  assert.deepEqual(r, { text: "12.3%", tone: "value" });
});

test("nao aplicavel tem prioridade sobre indisponivel (nunca mostra Sem dado quando e N/A)", () => {
  const r = formatChannelMetric(null, false, false, pct1);
  assert.equal(r.text, "N/A");
});

test("rotulos de sinal cobrem os 5 codigos do contrato, sem desconto/afiliados", () => {
  const expectedKeys = ["roas_forte", "ads_subutilizado", "custo_alto", "frete_alto", "sem_dado"];
  assert.deepEqual(Object.keys(CHANNEL_SIGNAL_LABEL).sort(), expectedKeys.sort());
  for (const key of expectedKeys) {
    assert.doesNotMatch(key, /desconto|discount|afiliad|affiliate/i);
  }
});

test("signalLabel/signalTone tem fallback seguro para um sinal desconhecido", () => {
  assert.equal(signalLabel("codigo_novo_desconhecido"), "codigo_novo_desconhecido");
  assert.match(signalTone("codigo_novo_desconhecido"), /slate/);
});

// findChannelMedian — drill-down marca x canal da matriz "Comparativo entre
// Canais" (Gate U3, Task 4/8). Garante que a mediana de referencia nunca
// mistura canais e nunca inventa uma comparacao quando nao ha mediana.
const MEDIANS: CanaisChannelMedian[] = [
  {
    channel: "ml", channel_label: "Mercado Livre", gmv_median: 500_000, ads_gmv_pct_median: 5.2,
    roas_median: 12.5, marketplace_cost_pct_median: 16.5, marketplace_cost_pct_p75: 18.2,
    seller_shipping_pct_median: 11.9, seller_shipping_pct_p75: 13.4, brands_with_data: 4,
  },
  {
    channel: "shopee", channel_label: "Shopee", gmv_median: 150_000, ads_gmv_pct_median: null,
    roas_median: null, marketplace_cost_pct_median: 9.1, marketplace_cost_pct_p75: 10.0,
    seller_shipping_pct_median: null, seller_shipping_pct_p75: null, brands_with_data: 3,
  },
];

test("findChannelMedian encontra a mediana do canal correto", () => {
  const r = findChannelMedian(MEDIANS, "ml");
  assert.equal(r?.channel, "ml");
  assert.equal(r?.roas_median, 12.5);
});

test("findChannelMedian nao mistura mediana de canais diferentes", () => {
  const shopee = findChannelMedian(MEDIANS, "shopee");
  assert.equal(shopee?.channel, "shopee");
  assert.notEqual(shopee?.gmv_median, MEDIANS[0].gmv_median);
  assert.equal(shopee?.roas_median, null); // shopee nao tem ads modelado no mock — nunca herda o valor de ML
});

test("findChannelMedian retorna null (nao inventa comparacao) quando o canal nao tem mediana calculada", () => {
  const r = findChannelMedian(MEDIANS, "tiktok");
  assert.equal(r, null);
});

test("findChannelMedian com lista vazia (modo demonstracao) retorna null para qualquer canal", () => {
  assert.equal(findChannelMedian([], "ml"), null);
  assert.equal(findChannelMedian([], "tiktok"), null);
});
