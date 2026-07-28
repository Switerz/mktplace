// Testes da maquina de estados do polling de Tempo Real (Gate U5, Task 4/7).
// `computeTempoRealStatus` e' pura — sem React/JSDOM — e cobre a matriz de
// aceite: os 5 estados sao sempre mutuamente exclusivos para qualquer
// combinacao de entrada.
import { test } from "node:test";
import assert from "node:assert/strict";
import { computeTempoRealStatus, type TempoRealStatusInput } from "../src/lib/tempo-real-status.ts";

test("carga inicial: initialLoading=true sempre vence, mesmo com dado e falha presentes", () => {
  assert.equal(
    computeTempoRealStatus({ initialLoading: true, refreshing: false, hasData: false, lastFetchFailed: false }),
    "initial",
  );
  assert.equal(
    computeTempoRealStatus({ initialLoading: true, refreshing: true, hasData: true, lastFetchFailed: true }),
    "initial",
  );
});

test("atualizando: refreshing=true (fora da carga inicial) vence sobre stale/fresh", () => {
  assert.equal(
    computeTempoRealStatus({ initialLoading: false, refreshing: true, hasData: true, lastFetchFailed: false }),
    "updating",
  );
  assert.equal(
    computeTempoRealStatus({ initialLoading: false, refreshing: true, hasData: true, lastFetchFailed: true }),
    "updating",
  );
});

test("indisponivel: sem dado e sem carregar/atualizar -> unavailable, mesmo sem falha registrada", () => {
  assert.equal(
    computeTempoRealStatus({ initialLoading: false, refreshing: false, hasData: false, lastFetchFailed: false }),
    "unavailable",
  );
  assert.equal(
    computeTempoRealStatus({ initialLoading: false, refreshing: false, hasData: false, lastFetchFailed: true }),
    "unavailable",
  );
});

test("defasado (stale): ha dado, nao esta atualizando, mas a ultima tentativa falhou", () => {
  assert.equal(
    computeTempoRealStatus({ initialLoading: false, refreshing: false, hasData: true, lastFetchFailed: true }),
    "stale",
  );
});

test("fresco: ha dado, nao esta atualizando, e a ultima tentativa teve sucesso", () => {
  assert.equal(
    computeTempoRealStatus({ initialLoading: false, refreshing: false, hasData: true, lastFetchFailed: false }),
    "fresh",
  );
});

test("os 5 estados sao mutuamente exclusivos para qualquer combinacao booleana", () => {
  const bools = [true, false];
  const all = ["initial", "updating", "unavailable", "stale", "fresh"];
  for (const initialLoading of bools) {
    for (const refreshing of bools) {
      for (const hasData of bools) {
        for (const lastFetchFailed of bools) {
          const input: TempoRealStatusInput = { initialLoading, refreshing, hasData, lastFetchFailed };
          const status = computeTempoRealStatus(input);
          assert.ok(all.includes(status), `estado invalido: ${status}`);
        }
      }
    }
  }
});
