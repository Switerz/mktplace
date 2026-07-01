import { useMemo, useState } from "react";

export type SortDirection = "asc" | "desc";

export type SortColumnType = "numeric" | "text";

export interface SortState {
  column: string | null;
  direction: SortDirection | null;
}

const NULL_TEXT_VALUES = new Set(["", "—", "-", "n/d", "n/a"]);

export function isEmptyValue(v: unknown): boolean {
  if (v == null) return true;
  if (typeof v === "string") return NULL_TEXT_VALUES.has(v.trim().toLowerCase());
  if (typeof v === "number") return Number.isNaN(v);
  return false;
}

export function defaultDirectionFor(type: SortColumnType): SortDirection {
  return type === "text" ? "asc" : "desc";
}

/** Ciclo de 3 estados por coluna: padrão -> invertido -> sem ordenação (padrão original). Pura, sem React. */
export function nextSortState(
  prev: SortState,
  column: string,
  columnTypes: Record<string, SortColumnType>,
): SortState {
  const type = columnTypes[column] ?? "numeric";
  const primary = defaultDirectionFor(type);
  const secondary: SortDirection = primary === "asc" ? "desc" : "asc";
  if (prev.column !== column) return { column, direction: primary };
  if (prev.direction === primary) return { column, direction: secondary };
  return { column: null, direction: null };
}

/**
 * Ordena `rows` por `sort` usando `getValue`/`columnTypes`. Pura, sem React —
 * usada pelo hook `useSortableTable` e testável isoladamente. Nunca muta
 * `rows`; nulos/"—"/N/D sempre ao final; texto usa localeCompare("pt-BR");
 * empates preservam a ordem original (ordenação estável).
 */
export function computeSortedRows<T>(
  rows: readonly T[],
  sort: SortState,
  getValue: (row: T, column: string) => string | number | null | undefined,
  columnTypes: Record<string, SortColumnType>,
): T[] {
  if (!sort.column || !sort.direction) return [...rows];
  const column = sort.column;
  const direction = sort.direction;
  const type = columnTypes[column] ?? "numeric";
  const indexed = rows.map((row, index) => ({ row, index }));

  indexed.sort((a, b) => {
    const va = getValue(a.row, column);
    const vb = getValue(b.row, column);
    const aEmpty = isEmptyValue(va);
    const bEmpty = isEmptyValue(vb);
    if (aEmpty && bEmpty) return a.index - b.index;
    if (aEmpty) return 1;
    if (bEmpty) return -1;

    let cmp: number;
    if (type === "text") {
      cmp = String(va).localeCompare(String(vb), "pt-BR");
    } else {
      cmp = Number(va) - Number(vb);
    }
    if (cmp === 0) return a.index - b.index;
    return direction === "asc" ? cmp : -cmp;
  });

  return indexed.map((x) => x.row);
}

/**
 * Hook genérico de ordenação client-side para tabelas do dashboard.
 * Ciclo por coluna: 1º clique = direção padrão (desc p/ número, asc p/ texto),
 * 2º clique = direção invertida, 3º clique = volta à ordem original (padrão).
 * Nulos/"—"/N/D sempre ao final, em ambas as direções. Ordenação estável e
 * não destrutiva (nunca muta o array de entrada).
 */
export function useSortableTable<T>(
  rows: readonly T[],
  getValue: (row: T, column: string) => string | number | null | undefined,
  columnTypes: Record<string, SortColumnType>,
) {
  const [sort, setSort] = useState<SortState>({ column: null, direction: null });

  function toggleSort(column: string) {
    setSort((prev) => nextSortState(prev, column, columnTypes));
  }

  function resetSortIfColumnMissing(availableColumns: readonly string[]) {
    if (sort.column && !availableColumns.includes(sort.column)) {
      setSort({ column: null, direction: null });
    }
  }

  const sortedRows = useMemo(
    () => computeSortedRows(rows, sort, getValue, columnTypes),
    [rows, sort.column, sort.direction, getValue, columnTypes],
  );

  return { sort, toggleSort, sortedRows, resetSortIfColumnMissing };
}
