// Testes da logica pura de ordenacao (apps/web/src/lib/use-sortable-table.ts).
// computeSortedRows/nextSortState sao puras (sem React) para permitir testes
// diretos via `node --test`, sem adicionar um test-renderer como dependencia.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  computeSortedRows,
  nextSortState,
  type SortState,
} from "../src/lib/use-sortable-table.ts";

interface Row {
  label: string;
  value: number | null;
}

const getValue = (row: Row, column: string): string | number | null => {
  if (column === "label") return row.label;
  if (column === "value") return row.value;
  return null;
};

const COLUMN_TYPES = { label: "text" as const, value: "numeric" as const };

const NO_SORT: SortState = { column: null, direction: null };

test("numerico: primeiro clique ordena descendente", () => {
  const rows: Row[] = [{ label: "a", value: 1 }, { label: "b", value: 3 }, { label: "c", value: 2 }];
  const sort = nextSortState(NO_SORT, "value", COLUMN_TYPES);
  assert.deepEqual(sort, { column: "value", direction: "desc" });
  const sorted = computeSortedRows(rows, sort, getValue, COLUMN_TYPES);
  assert.deepEqual(sorted.map((r) => r.value), [3, 2, 1]);
});

test("numerico: segundo clique inverte para ascendente", () => {
  const rows: Row[] = [{ label: "a", value: 1 }, { label: "b", value: 3 }, { label: "c", value: 2 }];
  let sort = nextSortState(NO_SORT, "value", COLUMN_TYPES);
  sort = nextSortState(sort, "value", COLUMN_TYPES);
  assert.deepEqual(sort, { column: "value", direction: "asc" });
  const sorted = computeSortedRows(rows, sort, getValue, COLUMN_TYPES);
  assert.deepEqual(sorted.map((r) => r.value), [1, 2, 3]);
});

test("numerico: terceiro clique volta ao padrao (ordem original, sem coluna ativa)", () => {
  const rows: Row[] = [{ label: "a", value: 1 }, { label: "b", value: 3 }, { label: "c", value: 2 }];
  let sort = nextSortState(NO_SORT, "value", COLUMN_TYPES);
  sort = nextSortState(sort, "value", COLUMN_TYPES);
  sort = nextSortState(sort, "value", COLUMN_TYPES);
  assert.deepEqual(sort, { column: null, direction: null });
  const sorted = computeSortedRows(rows, sort, getValue, COLUMN_TYPES);
  assert.deepEqual(sorted.map((r) => r.value), [1, 3, 2]); // ordem original preservada
});

test("texto: primeiro clique ordena ascendente usando localeCompare pt-BR (acentos)", () => {
  const rows: Row[] = [{ label: "Écio", value: 1 }, { label: "Ana", value: 2 }, { label: "Éder", value: 3 }];
  const sort = nextSortState(NO_SORT, "label", COLUMN_TYPES);
  assert.deepEqual(sort, { column: "label", direction: "asc" });
  const sorted = computeSortedRows(rows, sort, getValue, COLUMN_TYPES);
  // pt-BR: Ana < Écio < Éder (compara letra a letra ignorando acento: a < c < d)
  assert.deepEqual(sorted.map((r) => r.label), ["Ana", "Écio", "Éder"]);
});

test("texto: segundo clique inverte para descendente", () => {
  const rows: Row[] = [{ label: "Ana", value: 1 }, { label: "Beatriz", value: 2 }, { label: "Carlos", value: 3 }];
  let sort = nextSortState(NO_SORT, "label", COLUMN_TYPES);
  sort = nextSortState(sort, "label", COLUMN_TYPES);
  assert.deepEqual(sort, { column: "label", direction: "desc" });
  const sorted = computeSortedRows(rows, sort, getValue, COLUMN_TYPES);
  assert.deepEqual(sorted.map((r) => r.label), ["Carlos", "Beatriz", "Ana"]);
});

test("nulos, undefined e '—'/N/D ficam sempre ao final, em qualquer direcao", () => {
  const rows: Row[] = [
    { label: "a", value: 5 },
    { label: "b", value: null },
    { label: "c", value: 1 },
  ];
  const asc = computeSortedRows(rows, { column: "value", direction: "asc" }, getValue, COLUMN_TYPES);
  assert.deepEqual(asc.map((r) => r.label), ["c", "a", "b"]);

  const desc = computeSortedRows(rows, { column: "value", direction: "desc" }, getValue, COLUMN_TYPES);
  assert.deepEqual(desc.map((r) => r.label), ["a", "c", "b"]);

  const textRows = [{ label: "—", value: 1 }, { label: "N/D", value: 2 }, { label: "Ana", value: 3 }];
  const textAsc = computeSortedRows(textRows, { column: "label", direction: "asc" }, getValue, COLUMN_TYPES);
  assert.deepEqual(textAsc.map((r) => r.value), [3, 1, 2]); // "Ana" primeiro, "—"/"N/D" ao final (ordem estavel entre nulos)
});

test("estabilidade: linhas com valores iguais preservam a ordem original (empate)", () => {
  const rows: Row[] = [
    { label: "primeiro", value: 10 },
    { label: "segundo", value: 10 },
    { label: "terceiro", value: 5 },
  ];
  const sorted = computeSortedRows(rows, { column: "value", direction: "desc" }, getValue, COLUMN_TYPES);
  assert.deepEqual(sorted.map((r) => r.label), ["primeiro", "segundo", "terceiro"]);
});

test("computeSortedRows nunca muta o array de entrada", () => {
  const rows: Row[] = [{ label: "b", value: 2 }, { label: "a", value: 1 }];
  const original = [...rows];
  computeSortedRows(rows, { column: "value", direction: "asc" }, getValue, COLUMN_TYPES);
  assert.deepEqual(rows, original);
});

test("coluna ausente na allowlist usa direcao padrao numerica (desc)", () => {
  const sort = nextSortState(NO_SORT, "coluna_desconhecida", {});
  assert.deepEqual(sort, { column: "coluna_desconhecida", direction: "desc" });
});
