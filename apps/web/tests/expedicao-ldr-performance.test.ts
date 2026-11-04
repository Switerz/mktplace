// CX-MKT-2/3 — faixa de LDR no topo da Expedicao e secao "Performance" oficial.
// Roda via `node --test` com type-stripping nativo do Node.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  CONTAS_POR_CANAL,
  META_TETO,
  MOTIVO_ML_AGUARDANDO,
  celula,
  classificar,
  escalaBarra,
  lerRespostaLdr,
  montarMatriz,
  pct,
  urlLdr,
  type FonteCanal,
} from "../src/lib/expedicao-ldr-topo.ts";
import {
  performanceHabilitada,
  problemaNaPerformance,
  quantidadeMetrica,
  urlPerformance,
  valorMetrica,
} from "../src/lib/expedicao-performance.ts";

const AQUI = dirname(fileURLToPath(import.meta.url));

// ---------------------------------------------------------------------------
// LDR do topo
// ---------------------------------------------------------------------------
test("meta e teto: limite exato conta como dentro", () => {
  const mt = { meta: 0.02, teto: 0.05, origemTeto: "x" };
  assert.equal(classificar(0.02, mt), "dentro");
  assert.equal(classificar(0.0201, mt), "entre");
  assert.equal(classificar(0.05, mt), "entre");
  assert.equal(classificar(0.0593, mt), "acima");
});

test("teto sempre acima da meta em todos os canais", () => {
  for (const mt of Object.values(META_TETO)) assert.ok(mt.teto > mt.meta && mt.meta > 0);
});

test("escala da barra: teto nunca encosta na borda e taxa alta satura", () => {
  const mt = META_TETO.shopee;
  const b = escalaBarra(0.0062, mt);
  assert.ok(b.teto < 100 && b.meta < b.teto);
  assert.equal(escalaBarra(0.5, mt).preenchido, 100);
});

test("le brands[] das rotas e recusa forma quebrada", () => {
  const ok = lerRespostaLdr({ availability: "available", brands: [{ brand: "apice", base: 371, late: 22, rate: 0.0593 }] });
  assert.equal(ok.estado, "dado");
  assert.equal(lerRespostaLdr({ availability: "unavailable", unavailable_reason: "disabled" }).estado, "indisponivel");
  assert.equal(lerRespostaLdr({ availability: "available", brands: [{ brand: "x", base: "1", late: 0, rate: 0 }] }).estado,
    "indisponivel");
  assert.equal(lerRespostaLdr(null).estado, "indisponivel");
});

test("celula: sem conta, sem base e taxa nula nunca viram 0%", () => {
  const fonte: FonteCanal = { estado: "dado", marcas: [
    { brand: "apice", base: 371, late: 22, rate: 0.0593 },
    { brand: "lescent", base: 0, late: 0, rate: null },
  ] };
  assert.deepEqual(celula(fonte, "shopee", "kokeshi"), { tipo: "sem_conta" });
  assert.equal(celula(fonte, "shopee", "lescent").tipo, "sem_base");
  assert.equal(celula(fonte, "shopee", "barbours").tipo, "sem_base");
  const a = celula(fonte, "shopee", "apice");
  assert.equal(a.tipo, "dado");
  assert.equal(a.tipo === "dado" && a.faixa, "acima");
});

test("ML: rota propria, janela parcial dita e serie ainda nao iniciada com motivo", () => {
  assert.equal(urlLdr("http://api", "mercadolivre", "kokeshi"),
    "http://api/api/v1/expedicao/ml/ldr?days=7&brands=kokeshi");
  const parcial = lerRespostaLdr({ availability: "available", mature_days: 2, requested_days: 7,
    brands: [{ brand: "kokeshi", base: 340, late: 3, rate: 3 / 340 }] });
  const c = celula(parcial, "mercadolivre", "kokeshi");
  assert.equal(c.tipo === "dado" && c.parcial, "janela parcial: 2 de 7 dias");
  const cheio = lerRespostaLdr({ availability: "available", mature_days: 7, requested_days: 7,
    brands: [{ brand: "kokeshi", base: 340, late: 3, rate: 3 / 340 }] });
  const c2 = celula(cheio, "mercadolivre", "kokeshi");
  assert.equal(c2.tipo === "dado" && c2.parcial, null);
  const naoIniciada = lerRespostaLdr({ availability: "unavailable", unavailable_reason: "series_not_started" });
  assert.equal(naoIniciada.estado, "indisponivel");
  assert.match(naoIniciada.estado === "indisponivel" ? naoIniciada.motivo : "", /03\/11\/2026/);
});

test("ML aguardando com motivo; Apice sem conta ML", () => {
  const linhas = montarMatriz({
    shopee: { estado: "carregando" },
    mercadolivre: { estado: "indisponivel", motivo: MOTIVO_ML_AGUARDANDO },
    tiktokshop: { estado: "dado", marcas: [] },
  }, ["apice", "kokeshi"]);
  assert.deepEqual(linhas[0].celulas.mercadolivre, { tipo: "sem_conta" });
  assert.deepEqual(linhas[1].celulas.mercadolivre, { tipo: "indisponivel", motivo: MOTIVO_ML_AGUARDANDO });
  assert.deepEqual(linhas[1].celulas.shopee, { tipo: "sem_conta" });
  assert.deepEqual(linhas[0].celulas.shopee, { tipo: "carregando" });
});

test("urls das rotas existentes, 7 dias e marca", () => {
  assert.equal(urlLdr("http://api", "shopee", "apice"), "http://api/api/v1/expedicao/shopee/ldr?days=7&brands=apice");
  assert.equal(urlLdr("http://api", "tiktokshop", null), "http://api/api/v1/expedicao/tiktok?days=7&event=coleta");
});

test("cobertura por canal", () => {
  assert.ok(!CONTAS_POR_CANAL.shopee.includes("kokeshi"));
  assert.ok(!CONTAS_POR_CANAL.mercadolivre.includes("apice"));
  assert.equal(pct(0.0593), "5,93%");
});

// ---------------------------------------------------------------------------
// Performance oficial
// ---------------------------------------------------------------------------
function payload() {
  return {
    gerado_em: "2026-11-04T12:00:00Z", canal: "mercadolivre", estado: "dado", motivo: null,
    fonte: "ML", cobertura: { marcas: ["kokeshi"], fora: [{ marca: "apice", motivo: "Sem conta." }] },
    contas: [{
      marca: "kokeshi", dia: "2026-11-04", capturado_em: "2026-11-04T10:10:00Z", nivel: "MercadoLíder Platinum",
      tom: "bom", termometro: 5, termometro_max: 5, resumo: "59.990 vendas", destaques: [], avisos: [],
      metricas: [{ codigo: "ml_claims", rotulo: "Reclamações", grupo: null, valor: 0.61, unidade: "pct", quantidade: 366,
                   anterior: null, meta: 1, comparador: "<=", rotulo_meta: "Até 1%", situacao: "ok", ajuda: null }],
    }],
    sem_foto: [],
  };
}

test("flag da performance so' liga com 'true'", () => {
  assert.equal(performanceHabilitada({ NEXT_PUBLIC_EXPEDICAO_PERFORMANCE_ENABLED: "true" }), true);
  assert.equal(performanceHabilitada({ NEXT_PUBLIC_EXPEDICAO_PERFORMANCE_ENABLED: "1" }), false);
});

test("payload valido passa e quebrado e' recusado", () => {
  assert.equal(problemaNaPerformance(payload()), null);
  const casos: ((p: any) => void)[] = [
    (p) => { p.canal = "amazon"; },
    (p) => { p.estado = "talvez"; },
    (p) => { p.contas[0].tom = "verde"; },
    (p) => { p.contas[0].metricas[0].valor = "0,61"; },
    (p) => { p.contas[0].metricas[0].situacao = "bom"; },
    (p) => { p.estado = "sem_fonte"; },
  ];
  for (const estraga of casos) {
    const p = payload();
    estraga(p);
    assert.notEqual(problemaNaPerformance(p), null);
  }
});

test("valor ausente e' travessao; unidades da plataforma", () => {
  assert.equal(valorMetrica({ valor: null, unidade: "pct" }), "—");
  assert.equal(valorMetrica({ valor: 0, unidade: "pct" }), "0%");
  assert.equal(valorMetrica({ valor: 0.61, unidade: "pct" }), "0,61%");
  assert.equal(valorMetrica({ valor: 1.5, unidade: "dias" }), "1,5 dia(s)");
  assert.equal(valorMetrica({ valor: 5400, unidade: "segundos" }), "1,5 h");
  assert.equal(valorMetrica({ valor: 600, unidade: "segundos" }), "10 min");
  assert.equal(quantidadeMetrica({ quantidade: 366 }), "366 vendas");
  assert.equal(quantidadeMetrica({ quantidade: null }), null);
  assert.equal(urlPerformance("http://api", "shopee", "apice"),
    "http://api/api/v1/expedicao/performance?canal=shopee&brands=apice");
});

// ---------------------------------------------------------------------------
// Fiacao na casca da Expedicao
// ---------------------------------------------------------------------------
test("casca: LDR no topo e Performance so' com a flag, Performance so' na visao do gestor", () => {
  const fonte = readFileSync(join(AQUI, "..", "app", "expedicao", "ExpedicaoShell.tsx"), "utf-8");
  assert.match(fonte, /process\.env\.NEXT_PUBLIC_EXPEDICAO_PERFORMANCE_ENABLED === "true"/);
  assert.match(fonte, /PERFORMANCE_LIGADA \?/);
  const iLdr = fonte.indexOf("<LdrTopo");
  const iCorpo = fonte.indexOf("{corpoDoCanal}");
  const iPerf = fonte.indexOf("<PerformanceOficial");
  assert.ok(iLdr > 0 && iLdr < iCorpo && iCorpo < iPerf, "ordem: LDR, corpo do canal, Performance");
  assert.match(fonte, /visao === "gestor" \? \(\s*<div[^>]*>\s*<PerformanceOficial/);
});

test("esqueleto: LDR logo abaixo do cabecalho, nos dois corpos de canal", () => {
  const layout = readFileSync(join(AQUI, "..", "src", "components", "expedicao", "LayoutExpedicao.tsx"), "utf-8");
  assert.match(layout, /\{cabecalho\}\s*\{destaque\}/);
  for (const arq of [["app", "expedicao", "ExpedicaoClient.tsx"], ["src", "components", "expedicao", "TikTokDispatchPanel.tsx"]]) {
    assert.match(readFileSync(join(AQUI, "..", ...arq), "utf-8"), /destaque=\{faixaTopo\}/);
  }
});

