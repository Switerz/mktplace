/**
 * METAS-1 — logica pura da aba "Metas" (`src/lib/metas.ts`) e contratos de
 * montagem lidos do codigo-fonte (`node --test` nao tem DOM).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  CANAIS,
  MARCAS,
  TODAS_AS_CHAVES,
  analisar,
  chave,
  classificarFalha,
  deslocarMes,
  fmtCompacto,
  hojeBrt,
  intervaloDoMes,
  lerValor,
  mesAtualBrt,
  parseMes,
  problemaNaResposta,
  rascunhoDe,
  realizadoDasMarcas,
  rotuloMes,
  textoDoCampo,
  totais,
  valoresDaResposta,
} from "../src/lib/metas.ts";

const src = (rel: string) => readFileSync(new URL(`../${rel}`, import.meta.url), "utf8");

// ---------------------------------------------------------------------------
// Listas fechadas iguais as do backend
// ---------------------------------------------------------------------------

test("marcas e canais sao os mesmos da API (metas_service.py)", () => {
  const py = src("../api/app/services/metas_service.py");
  const tupla = (nome: string) =>
    [...(py.match(new RegExp(`^${nome} = \\(([^)]*)\\)`, "m"))![1].matchAll(/"([a-z]+)"/g))].map((m) => m[1]);
  assert.deepEqual(MARCAS.map((m) => m.id), tupla("MARCAS"));
  assert.deepEqual(CANAIS.map((c) => c.id), tupla("CANAIS"));
  assert.equal(TODAS_AS_CHAVES.length, 15);
});

// ---------------------------------------------------------------------------
// Mes
// ---------------------------------------------------------------------------

test("parseMes aceita AAAA-MM na faixa da API e recusa o resto", () => {
  assert.equal(parseMes("2026-10"), "2026-10");
  assert.equal(parseMes("2024-01"), "2024-01");
  assert.equal(parseMes("2030-12"), "2030-12");
  for (const ruim of [null, "", "2026-13", "2026-00", "2026-1", "2023-12", "2031-01", "2026-10-01"]) {
    assert.equal(parseMes(ruim), null, String(ruim));
  }
});

test("deslocarMes atravessa a virada de ano nos dois sentidos", () => {
  assert.equal(deslocarMes("2026-10", -1), "2026-09");
  assert.equal(deslocarMes("2026-01", -1), "2025-12");
  assert.equal(deslocarMes("2026-12", 1), "2027-01");
  assert.equal(deslocarMes("2026-10", 0), "2026-10");
});

test("mes atual e' o de Brasilia, nao o de UTC", () => {
  // 01/11 02:00 UTC ainda e' 31/10 23:00 em Brasilia.
  assert.equal(mesAtualBrt(new Date("2026-11-01T02:00:00Z")), "2026-10");
  assert.equal(mesAtualBrt(new Date("2026-11-01T04:00:00Z")), "2026-11");
});

test("rotulo do mes", () => {
  assert.equal(rotuloMes("2026-03"), "março de 2026");
});

test("intervalo do realizado: mes inteiro no passado, cortado em hoje, nada no futuro", () => {
  const hoje = "2026-10-29";
  assert.deepEqual(intervaloDoMes("2026-02", hoje), { dateFrom: "2026-02-01", dateTo: "2026-02-28" });
  assert.deepEqual(intervaloDoMes("2024-02", hoje), { dateFrom: "2024-02-01", dateTo: "2024-02-29" });
  // A API recusa date_to futuro com 422: o mes corrente para em hoje.
  assert.deepEqual(intervaloDoMes("2026-10", hoje), { dateFrom: "2026-10-01", dateTo: "2026-10-29" });
  assert.deepEqual(intervaloDoMes("2026-10", "2026-10-01"), { dateFrom: "2026-10-01", dateTo: "2026-10-01" });
  assert.equal(intervaloDoMes("2026-11", hoje), null);
});

test("hojeBrt e' o dia de Brasilia", () => {
  assert.equal(hojeBrt(new Date("2026-11-01T02:00:00Z")), "2026-10-31");
});

// ---------------------------------------------------------------------------
// Valor digitado
// ---------------------------------------------------------------------------

test("lerValor entende os formatos que o time digita", () => {
  const casos: [string, number | null][] = [
    ["", null],
    ["   ", null],
    ["1500000", 1_500_000],
    ["1.500.000", 1_500_000],
    ["1.500", 1_500],
    ["1.500.000,50", 1_500_000.5],
    ["1500000,5", 1_500_000.5],
    ["R$ 4.216.608", 4_216_608],
    ["1500.5", 1500.5],
    ["0", 0],
    ["0,00", 0],
  ];
  for (const [texto, esperado] of casos) {
    assert.deepEqual(lerValor(texto), { ok: true, valor: esperado }, texto);
  }
});

test("lerValor recusa o que nao e' valor em reais", () => {
  for (const ruim of ["-10", "abc", "1,2,3", "1.50.000", "12,345", "1.5.0", "1e6", "10000000000"]) {
    assert.equal(lerValor(ruim).ok, false, ruim);
  }
});

test("textoDoCampo escreve no formato brasileiro e vazio para sem meta", () => {
  assert.equal(textoDoCampo(1_500_000), "1.500.000");
  assert.equal(textoDoCampo(1_500_000.5), "1.500.000,5");
  assert.equal(textoDoCampo(0), "0");
  assert.equal(textoDoCampo(null), "");
  // ida e volta
  for (const v of [0, 1, 999.99, 4_216_608, 9_999_999_999.99]) {
    assert.deepEqual(lerValor(textoDoCampo(v)), { ok: true, valor: v });
  }
});

test("fmtCompacto", () => {
  assert.equal(fmtCompacto(4_467_318), "R$ 4,47 mi");
  assert.equal(fmtCompacto(854_370), "R$ 854 mil");
  assert.equal(fmtCompacto(320), "R$ 320");
  assert.equal(fmtCompacto(null), "—");
});

// ---------------------------------------------------------------------------
// Grade: alteracoes, invalidas e totais
// ---------------------------------------------------------------------------

const K = chave("kokeshi", "tiktok");
const KML = chave("kokeshi", "ml");

test("sem digitar nada, nada esta alterado", () => {
  const salvo = { [K]: 4_216_608 };
  const a = analisar(salvo, rascunhoDe(salvo));
  assert.deepEqual(a.alteradas, []);
  assert.deepEqual(a.invalidas, {});
});

test("analisar separa alteradas, remocoes e invalidas", () => {
  const salvo = { [K]: 4_216_608, [KML]: 1_000 };
  const rascunho = { ...rascunhoDe(salvo), [K]: "4.500.000", [KML]: "", [chave("apice", "shopee")]: "abc" };
  const a = analisar(salvo, rascunho);
  assert.deepEqual(a.alteradas, [
    { brand: "kokeshi", channel: "tiktok", meta_gmv: 4_500_000 },
    { brand: "kokeshi", channel: "ml", meta_gmv: null },
  ]);
  assert.deepEqual(Object.keys(a.invalidas), [chave("apice", "shopee")]);
  // invalida nao entra no total como valor digitado: vale o salvo (nenhum)
  assert.equal(a.efetivos[chave("apice", "shopee")], null);
});

test("zero e' meta; vazio e' sem meta", () => {
  const a = analisar({}, { [KML]: "0" });
  assert.deepEqual(a.alteradas, [{ brand: "kokeshi", channel: "ml", meta_gmv: 0 }]);
  assert.equal(totais(a.efetivos).porMarca.kokeshi, 0);
  assert.equal(totais({}).porMarca.kokeshi, null);
});

test("totais somam por marca, por canal e global, sem inventar zero", () => {
  const t = totais({
    [K]: 4_000_000,
    [KML]: 1_000_000,
    [chave("barbours", "tiktok")]: 2_500_000.5,
  });
  assert.equal(t.porMarca.kokeshi, 5_000_000);
  assert.equal(t.porMarca.barbours, 2_500_000.5);
  assert.equal(t.porMarca.apice, null);
  assert.equal(t.porCanal.tiktok, 6_500_000.5);
  assert.equal(t.porCanal.shopee, null);
  assert.equal(t.global, 7_500_000.5);
});

test("valoresDaResposta ignora celula fora da grade", () => {
  const v = valoresDaResposta({
    mes: "2026-10",
    marcas: [],
    canais: [],
    ultima_alteracao: null,
    celulas: [
      { brand: "kokeshi", channel: "tiktok", meta_gmv: 10 },
      { brand: "gocase", channel: "tiktok", meta_gmv: 99 },
    ],
  });
  assert.deepEqual(v, { [K]: 10 });
});

test("realizado vem das linhas de /performance/brands", () => {
  const r = realizadoDasMarcas([
    { brand: "kokeshi", tiktok_gmv: 4_467_318, ml_gmv: 1_743_344, shopee_gmv: null },
    { brand: "azbuy", tiktok_gmv: 1, ml_gmv: null, shopee_gmv: null },
  ]);
  assert.equal(r[K], 4_467_318);
  assert.equal(r[chave("kokeshi", "shopee")], null);
  assert.equal(Object.keys(r).length, 3);
});

// ---------------------------------------------------------------------------
// Falhas e forma da resposta
// ---------------------------------------------------------------------------

test("classificarFalha", () => {
  assert.equal(classificarFalha(null, null).tipo, "rede");
  assert.equal(classificarFalha(429, null).tipo, "limite");
  assert.equal(classificarFalha(503, { detail: "x" }).tipo, "banco");
  const inv = classificarFalha(422, { detail: "mes deve estar no formato AAAA-MM" });
  assert.equal(inv.tipo, "invalido");
  assert.match(inv.mensagem, /AAAA-MM/);
  assert.equal(classificarFalha(500, null).tipo, "desconhecido");
});

test("problemaNaResposta", () => {
  assert.equal(problemaNaResposta({ mes: "2026-10", celulas: [] }), null);
  assert.notEqual(problemaNaResposta(null), null);
  assert.notEqual(problemaNaResposta({ mes: "2026-10" }), null);
  assert.notEqual(problemaNaResposta({ mes: "2026-10", celulas: [{ brand: "a", channel: "b", meta_gmv: "1" }] }), null);
});

// ---------------------------------------------------------------------------
// Montagem (codigo-fonte)
// ---------------------------------------------------------------------------

test("a tela nao tem modo demonstracao nem mock", () => {
  for (const rel of ["app/metas/MetasClient.tsx", "src/lib/metas-api.ts"]) {
    assert.ok(!/mock|demo/i.test(src(rel).replace(/SEM MODO DEMONSTRACAO|modo demonstracao/gi, "")), rel);
  }
});

test("cada campo tem rotulo acessivel e alvo de 44px", () => {
  const grade = src("src/components/metas/MetasGrade.tsx");
  assert.match(grade, /<label htmlFor=\{id\} className="sr-only">/);
  assert.match(grade, /\bmin-h-11\b/);
  assert.match(grade, /aria-invalid=/);
});

test("trocar de mes com alteracao pendente pede confirmacao", () => {
  const cliente = src("app/metas/MetasClient.tsx");
  assert.match(cliente, /window\.confirm\(/);
  assert.match(cliente, /beforeunload/);
});
