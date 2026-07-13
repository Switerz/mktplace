// Testes do modulo puro do Resumo Executivo da Gerencial (Gate 2 Fase 1).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  sortBySeverity,
  severityLabel,
  severityTone,
  SEVERITY_LABEL,
  HEALTH_STATUS_LABEL,
  type ExecutiveSeverity,
} from "../src/lib/executive-summary.ts";

function item(severity: ExecutiveSeverity, tag: string) {
  return { severity, tag };
}

test("sortBySeverity coloca critico antes de warning antes de info", () => {
  const items = [item("info", "a"), item("critical", "b"), item("warning", "c")];
  const sorted = sortBySeverity(items);
  assert.deepEqual(sorted.map((i) => i.tag), ["b", "c", "a"]);
});

test("sortBySeverity e estavel dentro da mesma severidade", () => {
  const items = [item("warning", "first"), item("critical", "x"), item("warning", "second")];
  const sorted = sortBySeverity(items);
  assert.deepEqual(sorted.map((i) => i.tag), ["x", "first", "second"]);
});

test("sortBySeverity nao muta o array original", () => {
  const items = [item("info", "a"), item("critical", "b")];
  const copy = [...items];
  sortBySeverity(items);
  assert.deepEqual(items, copy);
});

test("severityLabel cobre os 3 niveis do contrato", () => {
  assert.equal(severityLabel("info"), "Info");
  assert.equal(severityLabel("warning"), "Atenção");
  assert.equal(severityLabel("critical"), "Crítico");
});

test("severityLabel nunca quebra em severidade desconhecida (fallback = valor bruto)", () => {
  assert.equal(severityLabel("unknown"), "unknown");
});

test("severityTone tem fallback para severidade desconhecida", () => {
  assert.equal(severityTone("unknown"), "text-slate-500 bg-slate-100 border border-slate-200");
});

test("SEVERITY_LABEL e HEALTH_STATUS_LABEL cobrem exatamente os valores do contrato do backend", () => {
  assert.deepEqual(Object.keys(SEVERITY_LABEL).sort(), ["critical", "info", "warning"]);
  assert.deepEqual(Object.keys(HEALTH_STATUS_LABEL).sort(), ["attention", "critical", "ok"]);
});
