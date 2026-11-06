/** Top produtos por marca (Gerencial) — contas puras do detalhe. */
import { test } from "node:test";
import assert from "node:assert/strict";

import { agrupar, variacao } from "../src/lib/gerencial/top-produtos.ts";

const serie = [
  { dia: "2026-10-31", pecas: 2, receita: 20 },
  { dia: "2026-11-01", pecas: 1, receita: 10 },
  { dia: "2026-11-05", pecas: 3, receita: 30 },
];

test("dia: 30 barras ate o fim, dia sem venda vira zero", () => {
  const b = agrupar(serie, "2026-11-05", "dia");
  assert.equal(b.length, 30);
  assert.equal(b.at(-1)?.rotulo, "05/11");
  assert.equal(b.at(-1)?.receita, 30);
  assert.equal(b.at(-2)?.receita, 0);
  assert.equal(b.reduce((s, x) => s + x.pecas, 0), 6);
});

test("semana: blocos de 7 dias terminando no fim", () => {
  const b = agrupar(serie, "2026-11-05", "semana");
  assert.equal(b.length, 12);
  assert.deepEqual([b.at(-1)?.rotulo, b.at(-1)?.receita], ["30/10", 60]);
});

test("mes: 12 meses de calendario, virada de ano", () => {
  const b = agrupar(serie, "2026-11-05", "mes");
  assert.equal(b.length, 12);
  assert.deepEqual([b[0].rotulo, b.at(-1)?.rotulo], ["dez/25", "nov/26"]);
  assert.deepEqual([b.at(-2)?.receita, b.at(-1)?.receita], [20, 40]);
});

test("variacao sem base nao inventa 100%", () => {
  assert.equal(variacao(150, 100), 50);
  assert.equal(variacao(10, 0), null);
  assert.equal(variacao(10, null), null);
});
