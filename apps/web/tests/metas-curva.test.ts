/**
 * METAS-DIARIAS-1 — curva de pesos e meta do dia (src/lib/metas-curva.ts).
 *
 * Pesos e % acumulados vem da aba Curva da planilha "Meta diaria Marketplace —
 * nov/26 (validacao)". As metas aqui sao FICTICIAS (o repositorio e' publico);
 * os esperados foram calculados direto dos pesos, fora desta implementacao.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  escopoTemCurva,
  eventoDoDia,
  fracaoAte,
  fracaoDoEscopo,
  metaDoDia,
  pesosDoMes,
  progressoDoDia,
  resumoSemDado,
  tabelaMetaPorDia,
  temCurva,
} from "../src/lib/metas-curva.ts";

const perto = (a: number, b: number, tol = 0.01) => assert.ok(Math.abs(a - b) <= tol, `${a} != ${b}`);

test("novembro/26 tem curva para ML e Shopee, nao para TikTok", () => {
  assert.equal(temCurva("2026-11", "ml"), true);
  assert.equal(temCurva("2026-11", "shopee"), true);
  assert.equal(temCurva("2026-11", "tiktok"), false);
  assert.equal(temCurva("2026-12", "ml"), false);
});

test("pesos de novembro somam como na aba Curva", () => {
  const soma = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
  assert.equal(pesosDoMes("2026-11", "ml").length, 30);
  perto(soma(pesosDoMes("2026-11", "ml")), 36.66, 1e-9);
  perto(soma(pesosDoMes("2026-11", "shopee")), 39.26, 1e-9);
});

test("sem curva, todo dia pesa igual (a conta linear de sempre)", () => {
  assert.deepEqual(pesosDoMes("2026-12", "ml"), Array(31).fill(1));
  assert.deepEqual(pesosDoMes("2026-11", "tiktok"), Array(30).fill(1));
});

test("meta do dia = meta do mes x peso do dia / soma dos pesos (aba Meta diaria)", () => {
  perto(metaDoDia(1_000_000, "2026-11", "shopee", 11), 89149.2613346918);
  perto(metaDoDia(1_000_000, "2026-11", "ml", 1), 26732.133115111836);
  perto(metaDoDia(1_000_000, "2026-11", "shopee", 2), 22414.671421293933);
  perto(metaDoDia(3_100_000, "2026-12", "ml", 5), 100_000);
});

test("as metas dos dias somam a meta do mes", () => {
  let total = 0;
  for (let d = 1; d <= 30; d++) total += metaDoDia(2_000_000, "2026-11", "ml", d);
  perto(total, 2_000_000);
});

test("fracaoAte acumula os pesos ate o fim do dia (aba Curva, % acum.)", () => {
  perto(fracaoAte("2026-11", "ml", 22), 0.637, 0.001);
  perto(fracaoAte("2026-11", "shopee", 22), 0.633, 0.001);
  perto(fracaoAte("2026-11", "shopee", 11), 0.35, 0.001);
  assert.equal(fracaoAte("2026-11", "ml", 0), 0);
  assert.equal(fracaoAte("2026-11", "ml", 30), 1);
  perto(fracaoAte("2026-12", "ml", 10), 10 / 31, 1e-12);
});

test("eventoDoDia traz o nome do evento da curva", () => {
  assert.equal(eventoDoDia("2026-11-11"), "11/11");
  assert.equal(eventoDoDia("2026-11-27"), "Black Friday");
  assert.equal(eventoDoDia("2026-11-02"), "Finados (feriado)");
  assert.equal(eventoDoDia("2026-11-03"), null);
  assert.equal(eventoDoDia("2026-12-01"), null);
});

// Mesma forma do mes real (5 marcas na Shopee, 4 no ML, sem TikTok), metas ficticias:
// Shopee R$ 8 mi, ML R$ 6 mi.
const NOVEMBRO = [
  { brand: "apice", channel: "shopee", meta_gmv: 1_000_000 },
  { brand: "barbours", channel: "shopee", meta_gmv: 2_000_000 },
  { brand: "kokeshi", channel: "shopee", meta_gmv: 3_000_000 },
  { brand: "lescent", channel: "shopee", meta_gmv: 1_000_000 },
  { brand: "rituaria", channel: "shopee", meta_gmv: 1_000_000 },
  { brand: "barbours", channel: "ml", meta_gmv: 2_000_000 },
  { brand: "kokeshi", channel: "ml", meta_gmv: 2_000_000 },
  { brand: "lescent", channel: "ml", meta_gmv: 1_000_000 },
  { brand: "rituaria", channel: "ml", meta_gmv: 1_000_000 },
];

test("tabelaMetaPorDia abre o mes como a aba Meta diaria", () => {
  const t = tabelaMetaPorDia(NOVEMBRO, "2026-11");
  assert.equal(t.colunas.length, 9);
  assert.equal(t.linhas.length, 30);

  const dia11 = t.linhas[10];
  assert.equal(dia11.dia, "2026-11-11");
  assert.equal(dia11.diaSemana, "qua");
  assert.equal(dia11.evento, "11/11");
  perto(dia11.total, 1_114_176.09);
  perto(dia11.porCanal.shopee!, 713_194.09);
  perto(dia11.porCanal.ml!, 400_982.0);
  perto(dia11.acumulado, 4_828_020.51);

  const ultimo = t.linhas[29];
  perto(ultimo.acumulado, 14_000_000);
  perto(ultimo.pctAcumulado, 1, 1e-9);
  perto(t.total, 14_000_000);
});

test("tabelaMetaPorDia ignora marca x canal sem meta e ordena como a grade", () => {
  const t = tabelaMetaPorDia(
    [
      { brand: "kokeshi", channel: "shopee", meta_gmv: 100 },
      { brand: "apice", channel: "shopee", meta_gmv: 100 },
      { brand: "kokeshi", channel: "ml", meta_gmv: 100 },
    ],
    "2026-11",
  );
  assert.deepEqual(
    t.colunas.map((c) => `${c.brand}:${c.channel}`),
    ["kokeshi:ml", "apice:shopee", "kokeshi:shopee"],
  );
  assert.equal(t.linhas[0].porCanal.tiktok, undefined);
  assert.equal(tabelaMetaPorDia([], "2026-11").colunas.length, 0);
});

// ---------------------------------------------------------------------------
// Escopo filtrado e progresso do dia
// ---------------------------------------------------------------------------

test("fracaoDoEscopo pondera a curva de cada canal pela meta (acumulado da aba Meta diaria)", () => {
  const fracao = fracaoDoEscopo(NOVEMBRO, "2026-11");
  perto(fracao(22) * 14_000_000, 8_888_956.33);
  perto(fracao(11) * 14_000_000, 4_828_020.51);
  assert.equal(fracao(30), 1);
  // Sem meta nenhuma, linear.
  perto(fracaoDoEscopo([], "2026-11")(15), 0.5, 1e-12);
});

test("escopoTemCurva: algum canal do escopo com curva no mes", () => {
  assert.equal(escopoTemCurva(NOVEMBRO, "2026-11"), true);
  assert.equal(escopoTemCurva([{ brand: "kokeshi", channel: "tiktok", meta_gmv: 1 }], "2026-11"), false);
  assert.equal(escopoTemCurva(NOVEMBRO, "2026-12"), false);
});

const sem = { tiktok_gmv: null, ml_gmv: null, shopee_gmv: null };

test("dia em andamento: so' compara marca x canal que ja' tem venda de hoje", () => {
  const p = progressoDoDia({
    celulas: NOVEMBRO,
    linhas: [{ brand: "kokeshi", ...sem, shopee_gmv: 50_000 }],
    dia: "2026-11-11",
    hoje: "2026-11-11",
  })!;
  assert.equal(p.emAndamento, true);
  assert.equal(p.status, "em_andamento");
  assert.equal(p.evento, "11/11");
  perto(p.metaDoDia, 1_114_176.09);
  perto(p.metaComDado, 267_447.78);
  perto(p.realizado, 50_000);
  perto(p.atingimento!, 50_000 / 267_447.78400407534, 1e-9);
  assert.equal(p.semDado.length, 8);
  assert.deepEqual(Object.keys(p.porMarca), ["kokeshi"]);
  assert.deepEqual(p.porMarca.kokeshi.canais, ["shopee"]);
  assert.equal(p.porMarca.kokeshi.status, "em_andamento");
});

test("dia fechado: canal que chegou conta zero para marca sem venda; canal que nao chegou fica fora", () => {
  const p = progressoDoDia({
    celulas: NOVEMBRO,
    linhas: [
      { brand: "kokeshi", ...sem, shopee_gmv: 300_000 },
      { brand: "barbours", ...sem, tiktok_gmv: 50_000 },
    ],
    dia: "2026-11-11",
    hoje: "2026-11-12",
  })!;
  assert.equal(p.emAndamento, false);
  // Shopee chegou (Kokeshi): as outras marcas da Shopee contam zero.
  // ML nao chegou para ninguem: as 4 celulas do ML ficam fora.
  assert.deepEqual(
    p.semDado.map((c) => `${c.brand}:${c.channel}`).sort(),
    ["barbours:ml", "kokeshi:ml", "lescent:ml", "rituaria:ml"],
  );
  perto(p.metaComDado, 713_194.09);
  perto(p.realizado, 300_000);
  assert.equal(p.status, "nao_atingida");
  assert.equal(p.porMarca.apice.realizado, 0);
  assert.equal(p.porMarca.apice.status, "nao_atingida");
});

test("dia fechado acima da meta: atingida", () => {
  const p = progressoDoDia({
    celulas: [{ brand: "kokeshi", channel: "shopee", meta_gmv: 1_000_000 }],
    linhas: [{ brand: "kokeshi", ...sem, shopee_gmv: 100_000 }],
    dia: "2026-11-11",
    hoje: "2026-11-12",
  })!;
  assert.equal(p.status, "atingida");
  perto(p.falta, 0);
});

test("nenhuma celula com venda: sem atingimento, meta do dia continua visivel", () => {
  const p = progressoDoDia({ celulas: NOVEMBRO, linhas: [], dia: "2026-11-11", hoje: "2026-11-11" })!;
  assert.equal(p.atingimento, null);
  assert.equal(p.metaComDado, 0);
  perto(p.metaDoDia, 1_114_176.09);
  assert.equal(p.semDado.length, 9);
});

test("resumoSemDado: canal inteiro pelo nome; parte do canal pelas marcas", () => {
  const p = progressoDoDia({
    celulas: NOVEMBRO,
    linhas: [{ brand: "kokeshi", ...sem, shopee_gmv: 1 }],
    dia: "2026-11-11",
    hoje: "2026-11-11",
  })!;
  assert.deepEqual(resumoSemDado(p.semDado, NOVEMBRO), [
    "Mercado Livre",
    "ÁPICE, BARBOURS, LESCENT e RITUÁRIA na Shopee",
  ]);
  assert.deepEqual(resumoSemDado([], NOVEMBRO), []);
  assert.deepEqual(
    resumoSemDado([{ brand: "kokeshi", channel: "ml", meta_gmv: 1 }], NOVEMBRO),
    ["KOKESHI no Mercado Livre"],
  );
});

test("escopo sem meta: sem progresso do dia", () => {
  assert.equal(progressoDoDia({ celulas: [], linhas: [], dia: "2026-11-11", hoje: "2026-11-11" }), null);
});
