// Testes da regra de frescor de requisicao compartilhada entre Regioes e
// Financeiro (Gate U4) — mesmo padrao "Finding 2" do Gate U3 (Canais).
import { test } from "node:test";
import assert from "node:assert/strict";
import { isRequestFresh, computeRequestStatus } from "../src/lib/request-freshness.ts";

test("fresco: nao carregando, sem erro, chaves batem", () => {
  assert.equal(isRequestFresh({ loading: false, error: false, resolvedKey: "a", requestKey: "a" }), true);
});

test("carregando nunca e fresco, mesmo com chaves batendo (dado antigo nao e considerado fresco)", () => {
  assert.equal(isRequestFresh({ loading: true, error: false, resolvedKey: "a", requestKey: "a" }), false);
});

test("erro definitivo nunca e fresco, mesmo sem estar carregando (erro != loading)", () => {
  assert.equal(isRequestFresh({ loading: false, error: true, resolvedKey: "a", requestKey: "a" }), false);
});

test("chave resolvida desatualizada (filtro mudou, fetch ainda nao resolveu) nunca e fresco", () => {
  assert.equal(isRequestFresh({ loading: false, error: false, resolvedKey: "old", requestKey: "new" }), false);
});

test("resolvedKey nulo (nenhuma requisicao concluida ainda) nunca e fresco", () => {
  assert.equal(isRequestFresh({ loading: false, error: false, resolvedKey: null, requestKey: "a" }), false);
});

// ── computeRequestStatus — FINDING 2 (rodada de correcao consolidada) ────
// `!dataIsFresh` sozinho nao distinguia "carregando" de "erro definitivo".
// computeRequestStatus separa os 3 estados, sempre mutuamente exclusivos.

test("fresh: chaves batem, sem loading, sem erro", () => {
  const s = computeRequestStatus({ loading: false, error: false, resolvedKey: "a", requestKey: "a" });
  assert.deepEqual(s, { loading: false, error: false, fresh: true });
});

test("loading: requisicao em andamento, mesmo com chaves batendo e sem erro", () => {
  const s = computeRequestStatus({ loading: true, error: false, resolvedKey: "a", requestKey: "a" });
  assert.deepEqual(s, { loading: true, error: false, fresh: false });
});

test("loading: chave atual diferente da resolvida (frame de render anterior ao efeito) — mesmo com loading=false e error=false", () => {
  const s = computeRequestStatus({ loading: false, error: false, resolvedKey: "old", requestKey: "new" });
  assert.deepEqual(s, { loading: true, error: false, fresh: false });
});

test("error: erro definitivo SO conta quando a chave atual ja foi resolvida (nao esta em loading)", () => {
  const s = computeRequestStatus({ loading: false, error: true, resolvedKey: "a", requestKey: "a" });
  assert.deepEqual(s, { loading: false, error: true, fresh: false });
});

test("erro com chave desatualizada nunca vira 'error' — loading tem precedencia (retry: retryKey muda a chave ANTES do erro ser limpo)", () => {
  const s = computeRequestStatus({ loading: false, error: true, resolvedKey: "old", requestKey: "new" });
  assert.deepEqual(s, { loading: true, error: false, fresh: false });
});

test("loading e error sao sempre mutuamente exclusivos com fresh, para qualquer combinacao de entrada", () => {
  const combos = [
    { loading: false, error: false, resolvedKey: "a", requestKey: "a" },
    { loading: true, error: false, resolvedKey: "a", requestKey: "a" },
    { loading: false, error: true, resolvedKey: "a", requestKey: "a" },
    { loading: false, error: false, resolvedKey: "a", requestKey: "b" },
    { loading: true, error: true, resolvedKey: "a", requestKey: "b" },
  ];
  for (const input of combos) {
    const s = computeRequestStatus(input);
    const trueCount = [s.loading, s.error, s.fresh].filter(Boolean).length;
    assert.equal(trueCount, 1, `exatamente um estado deve ser verdadeiro para ${JSON.stringify(input)}`);
  }
});
