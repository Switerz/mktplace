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
  CHANNEL_SIGNAL_LABEL,
} from "../src/lib/canais-channel-metrics.ts";

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
