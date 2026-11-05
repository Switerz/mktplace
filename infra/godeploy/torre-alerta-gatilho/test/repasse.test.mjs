// node --test infra/godeploy/torre-alerta-gatilho/test/
import { test } from "node:test";
import assert from "node:assert/strict";

import { ROTAS, URL_DA_API, statusParaGoDeploy } from "../src/repasse.js";

const casos = [
  [200, 200], // ok
  [204, 200],
  [401, 424], // segredo errado: terminal, nao adianta repetir
  [422, 424],
  [500, 502], // API fora: o GoDeploy tenta de novo
  [503, 502],
  [null, 502], // falha de rede
];

for (const [upstream, esperado] of casos) {
  test(`status ${upstream} da API vira ${esperado} para o GoDeploy`, () => {
    assert.equal(statusParaGoDeploy(upstream), esperado);
  });
}

test("aponta para a rota do alerta na API do Render", () => {
  assert.equal(
    URL_DA_API,
    "https://mktplace-api.onrender.com/api/v1/alertas/expedicao-vencidos",
  );
});

test("cada cron do gatilho aponta para a sua rota na API", () => {
  const base = "https://mktplace-api.onrender.com/api/v1/alertas";
  assert.deepEqual(ROTAS, {
    "/cron/alerta-vencidos": `${base}/expedicao-vencidos`,
    "/cron/alertas-criticos": `${base}/criticos`,
    "/cron/report-diario": `${base}/report-diario`,
    "/cron/vence-hoje": `${base}/expedicao-dia?momento=manha`,
    "/cron/corte-coleta": `${base}/expedicao-dia?momento=corte`,
  });
});
