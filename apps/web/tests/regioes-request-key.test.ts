// Testes da identidade de requisicao e do escopo de fetch de Regioes
// (Gate U4) — cobre que a UF local faz parte da identidade da requisicao, e
// que a diferenca de escopo (UF em summary/by-uf, nao em by-brand/trend) e
// um contrato explicito, nao uma omissao.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildRegioesRequestKey, buildRegioesFetchScopes, describeRegioesPartialSections, formatRegioesPartialWarning,
} from "../src/lib/regioes-request-key.ts";

const BASE = { channels: ["ml", "tiktok"], brands: ["barbours"], dateFrom: "2026-06-01", dateTo: "2026-06-30", retryKey: 0 };

test("identidade da requisicao muda quando a UF local muda, mesmo com os demais filtros iguais", () => {
  const semUf = buildRegioesRequestKey({ ...BASE, uf: "" });
  const comUf = buildRegioesRequestKey({ ...BASE, uf: "SP" });
  assert.notEqual(semUf, comUf);
});

test("identidade da requisicao muda quando retryKey muda (permite forcar refetch)", () => {
  const a = buildRegioesRequestKey({ ...BASE, uf: "SP", retryKey: 0 });
  const b = buildRegioesRequestKey({ ...BASE, uf: "SP", retryKey: 1 });
  assert.notEqual(a, b);
});

test("mesma combinacao de filtros + UF + retryKey produz sempre a mesma chave", () => {
  const a = buildRegioesRequestKey({ ...BASE, uf: "RJ" });
  const b = buildRegioesRequestKey({ ...BASE, uf: "RJ" });
  assert.equal(a, b);
});

test("escopo UF: summary/by-uf recebem o filtro de UF quando selecionado", () => {
  const { ufScoped } = buildRegioesFetchScopes({ brands: ["barbours"], dateFrom: "2026-06-01", dateTo: "2026-06-30" }, "SP");
  assert.deepEqual(ufScoped.uf, ["SP"]);
});

test("escopo nacional: by-brand/trend NUNCA recebem o filtro de UF, mesmo com UF selecionada", () => {
  const { national } = buildRegioesFetchScopes({ brands: ["barbours"], dateFrom: "2026-06-01", dateTo: "2026-06-30" }, "SP");
  assert.ok(!("uf" in national), "escopo nacional nao deve conter a chave uf");
});

test("sem UF selecionada, escopo ufScoped tambem nao envia uf (equivalente ao nacional)", () => {
  const { ufScoped, national } = buildRegioesFetchScopes({ brands: ["barbours"], dateFrom: "2026-06-01", dateTo: "2026-06-30" }, "");
  assert.ok(!("uf" in ufScoped));
  assert.deepEqual(ufScoped, national);
});

// ── describeRegioesPartialSections / formatRegioesPartialWarning ─────────
// FINDING 3: `summary == null` continua sendo erro TOTAL (tratado no
// componente, nao aqui). Estas funcoes cobrem o caso em que `summary`
// funcionou mas uma ou mais das 3 secoes veio `null` (endpoint individual
// falhou) — nunca confundido com um array vazio de sucesso real.

test("sucesso vazio: as 3 secoes resolveram como array vazio -> nenhuma indisponivel", () => {
  const unavailable = describeRegioesPartialSections({ byUf: [], byBrand: [], trend: [] });
  assert.deepEqual(unavailable, []);
  assert.equal(formatRegioesPartialWarning(unavailable), null);
});

test("indisponibilidade/null: uma secao veio null -> listada como indisponivel, com aviso mencionando o rotulo certo", () => {
  const unavailable = describeRegioesPartialSections({ byUf: null, byBrand: [], trend: [] });
  assert.deepEqual(unavailable, ["byUf"]);
  const msg = formatRegioesPartialWarning(unavailable);
  assert.ok(msg && msg.includes("Ranking por UF"));
});

test("resposta parcial: duas de tres secoes indisponiveis, terceira com sucesso (mesmo vazio) — nao e' confundida", () => {
  const unavailable = describeRegioesPartialSections({ byUf: null, byBrand: null, trend: [] });
  assert.deepEqual(unavailable, ["byUf", "byBrand"]);
  const msg = formatRegioesPartialWarning(unavailable);
  assert.ok(msg && msg.includes("Ranking por UF") && msg.includes("Cobertura por Marca × Canal"));
  assert.ok(!msg.includes("Tendência"));
});

test("erro total (equivalente): as 3 secoes null -> todas listadas (o componente trata separadamente via summary==null, mas a funcao pura ainda descreve corretamente)", () => {
  const unavailable = describeRegioesPartialSections({ byUf: null, byBrand: null, trend: null });
  assert.deepEqual(unavailable, ["byUf", "byBrand", "trend"]);
});

test("array vazio [] nunca e' tratado como indisponivel — distinto de null", () => {
  const unavailable = describeRegioesPartialSections({ byUf: [], byBrand: null, trend: [] });
  assert.deepEqual(unavailable, ["byBrand"]);
});
