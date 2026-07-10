// Testes do asset de geometria do mapa (Gate 6D.3): garante que o mapa real
// por UF tem exatamente as 27 UFs oficiais, nunca inclui XX, nenhum path
// vazio/duplicado, e o viewBox tem o formato esperado por um <svg>.
import { test } from "node:test";
import assert from "node:assert/strict";
import { BRAZIL_UF_PATHS, BRAZIL_MAP_VIEWBOX, UF_NAME } from "../src/lib/brazil-uf-paths.ts";

const ALL_27_UFS = [
  "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
  "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
];

test("BRAZIL_UF_PATHS: contem exatamente as 27 UFs oficiais, sem duplicatas", () => {
  const ufs = BRAZIL_UF_PATHS.map((p) => p.uf);
  assert.equal(ufs.length, 27);
  assert.equal(new Set(ufs).size, 27);
  assert.deepEqual([...ufs].sort(), [...ALL_27_UFS].sort());
});

test("BRAZIL_UF_PATHS: nunca inclui XX (UF desconhecida fica sempre fora do mapa)", () => {
  assert.ok(!BRAZIL_UF_PATHS.some((p) => p.uf === "XX"));
});

test("BRAZIL_UF_PATHS: todo path e' uma string SVG nao vazia (comeca com comando de path valido)", () => {
  for (const p of BRAZIL_UF_PATHS) {
    assert.ok(p.path.length > 10, `path muito curto para ${p.uf}`);
    assert.match(p.path, /^[Mm]/, `path de ${p.uf} nao comeca com comando moveto`);
  }
});

test("BRAZIL_UF_PATHS: todo estado tem nome legivel (nao vazio, diferente da sigla)", () => {
  for (const p of BRAZIL_UF_PATHS) {
    assert.ok(p.name.length > 0, `sem nome para ${p.uf}`);
    assert.notEqual(p.name, p.uf);
  }
});

test("UF_NAME: tem entrada para cada uma das 27 UFs, sincronizada com BRAZIL_UF_PATHS", () => {
  for (const p of BRAZIL_UF_PATHS) {
    assert.equal(UF_NAME[p.uf], p.name);
  }
  assert.equal(Object.keys(UF_NAME).length, 27);
});

test("BRAZIL_MAP_VIEWBOX: formato valido para atributo viewBox de <svg> ('minX minY width height')", () => {
  const parts = BRAZIL_MAP_VIEWBOX.trim().split(/\s+/);
  assert.equal(parts.length, 4);
  for (const part of parts) assert.ok(Number.isFinite(Number(part)), `parte nao numerica: ${part}`);
  const [, , width, height] = parts.map(Number);
  assert.ok(width > 0 && height > 0);
});
