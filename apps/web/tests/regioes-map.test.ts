// Testes do mapa regional (Gate 6D.2): normalizacao de intensidade de GMV,
// escala de cor, e integridade da grade do cartograma. Roda via
// `node --test` com type-stripping nativo do Node.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  BRAZIL_UF_GRID, computeGmvIntensity, intensityToColor, textColorForIntensity, coverageGlyph,
} from "../src/lib/regioes-map.ts";

const ALL_27_UFS = [
  "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
  "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
];

// ---------------------------------------------------------------------------
// Integridade da grade — regressao critica: nenhuma UF duplicada/faltando,
// nenhuma celula (col,row) compartilhada por duas UFs, XX nunca presente.
// ---------------------------------------------------------------------------
test("BRAZIL_UF_GRID: contem exatamente as 27 UFs oficiais, sem duplicatas", () => {
  const ufs = BRAZIL_UF_GRID.map((p) => p.uf);
  assert.equal(ufs.length, 27);
  assert.equal(new Set(ufs).size, 27);
  assert.deepEqual([...ufs].sort(), [...ALL_27_UFS].sort());
});

test("BRAZIL_UF_GRID: nunca inclui XX", () => {
  assert.ok(!BRAZIL_UF_GRID.some((p) => p.uf === "XX"));
});

test("BRAZIL_UF_GRID: nenhuma celula (col,row) e compartilhada por duas UFs", () => {
  const cells = BRAZIL_UF_GRID.map((p) => `${p.col},${p.row}`);
  const dupes = cells.filter((c, i) => cells.indexOf(c) !== i);
  assert.deepEqual(dupes, []);
});

test("BRAZIL_UF_GRID: todas as posicoes sao nao-negativas", () => {
  assert.ok(BRAZIL_UF_GRID.every((p) => p.col >= 0 && p.row >= 0));
});

// ---------------------------------------------------------------------------
// computeGmvIntensity — maior GMV vira intensidade maxima; zero/ausente
// vira neutro (0); XX e' sempre excluida do resultado.
// ---------------------------------------------------------------------------
test("computeGmvIntensity: UF com maior gmv recebe intensidade 1", () => {
  const map = computeGmvIntensity([
    { uf: "SP", gmv: 1000 },
    { uf: "RJ", gmv: 500 },
    { uf: "MG", gmv: 250 },
  ]);
  assert.equal(map.get("SP"), 1);
  assert.equal(map.get("RJ"), 0.5);
  assert.equal(map.get("MG"), 0.25);
});

test("computeGmvIntensity: gmv zero vira intensidade neutra (0)", () => {
  const map = computeGmvIntensity([{ uf: "SP", gmv: 1000 }, { uf: "AC", gmv: 0 }]);
  assert.equal(map.get("AC"), 0);
});

test("computeGmvIntensity: XX e sempre excluida do resultado, mesmo com gmv alto", () => {
  const map = computeGmvIntensity([{ uf: "SP", gmv: 100 }, { uf: "XX", gmv: 999999 }]);
  assert.equal(map.has("XX"), false);
  // XX (mesmo maior) nao deveria "puxar" a escala para baixo o valor de SP.
  assert.equal(map.get("SP"), 1);
});

test("computeGmvIntensity: lista vazia retorna mapa vazio, sem lancar excecao", () => {
  const map = computeGmvIntensity([]);
  assert.equal(map.size, 0);
});

test("computeGmvIntensity: todas as UFs com gmv zero -- todas neutras, sem divisao por zero", () => {
  const map = computeGmvIntensity([{ uf: "SP", gmv: 0 }, { uf: "RJ", gmv: 0 }]);
  assert.equal(map.get("SP"), 0);
  assert.equal(map.get("RJ"), 0);
});

// ---------------------------------------------------------------------------
// intensityToColor — monotonico, neutro explicito, clamp fora de [0,1]
// ---------------------------------------------------------------------------
function parseRgb(css: string): [number, number, number] {
  const m = css.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
  assert.ok(m, `cor inesperada: ${css}`);
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

test("intensityToColor: intensidade 0 (ou negativa) vira a cor neutra explicita", () => {
  const neutral = intensityToColor(0);
  assert.equal(intensityToColor(-1), neutral);
  assert.equal(intensityToColor(-0.001), neutral);
});

test("intensityToColor: intensidade maior produz uma cor mais 'forte' (canal azul cresce em direcao ao violeta)", () => {
  const low = parseRgb(intensityToColor(0.1));
  const high = parseRgb(intensityToColor(0.9));
  // violeta forte (destino) tem b=0xb6=182, mais alto que o lilas claro
  // (0xfe=254) tem b maior -- o canal que cresce monotonicamente aqui e' o
  // "quao perto do azul/violeta profundo" via r: 0xed(237) -> 0x5b(91),
  // entao o canal r DECRESCE com a intensidade (fica mais escuro/saturado).
  assert.ok(low[0] > high[0], `r deveria decrescer: low=${low[0]} high=${high[0]}`);
});

test("intensityToColor: clampa intensidade acima de 1", () => {
  assert.equal(intensityToColor(1), intensityToColor(5));
});

test("intensityToColor: e' deterministico (mesma entrada, mesma saida)", () => {
  assert.equal(intensityToColor(0.42), intensityToColor(0.42));
});

// ---------------------------------------------------------------------------
// textColorForIntensity
// ---------------------------------------------------------------------------
test("textColorForIntensity: intensidade baixa usa texto escuro, alta usa texto claro", () => {
  assert.equal(textColorForIntensity(0), "#312e81");
  assert.equal(textColorForIntensity(1), "#ffffff");
});

// ---------------------------------------------------------------------------
// coverageGlyph — "ok" nunca tem marcador; os demais sao distintos entre si
// (acessibilidade: nao depender so da cor)
// ---------------------------------------------------------------------------
test("coverageGlyph: 'ok' nao tem marcador (silencioso)", () => {
  assert.equal(coverageGlyph("ok"), "");
});

test("coverageGlyph: partial/low/not_applicable tem marcadores distintos entre si", () => {
  const partial = coverageGlyph("partial");
  const low = coverageGlyph("low");
  const na = coverageGlyph("not_applicable");
  assert.notEqual(partial, "");
  assert.notEqual(low, "");
  assert.notEqual(na, "");
  assert.equal(new Set([partial, low, na]).size, 3);
});

// ---------------------------------------------------------------------------
// Sem dependencia pesada nova — nenhuma lib de mapa/geo/scale foi
// adicionada ao package.json para implementar o cartograma.
// ---------------------------------------------------------------------------
test("package.json: nenhuma dependencia pesada de mapa/geo foi adicionada", () => {
  const pkgPath = path.join(import.meta.dirname, "..", "package.json");
  const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf-8"));
  const allDeps = { ...(pkg.dependencies ?? {}), ...(pkg.devDependencies ?? {}) };
  // Comparacao por nome EXATO de pacote (nao substring) -- "echarts" como
  // substring falso-positivaria em "recharts", ja presente e legitimo
  // (usado por TrendChart.tsx desde antes deste Gate).
  const bannedPackageNames = new Set([
    "react-simple-maps", "d3-geo", "d3-scale", "topojson", "topojson-client",
    "mapbox-gl", "leaflet", "react-leaflet", "react-map-gl", "echarts",
    "highcharts", "amcharts", "amcharts4",
  ]);
  const found = Object.keys(allDeps).filter((name) => bannedPackageNames.has(name));
  assert.deepEqual(found, []);
});
