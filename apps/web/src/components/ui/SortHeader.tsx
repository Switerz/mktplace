"use client";

import type { SortState } from "@/lib/use-sortable-table";

/**
 * UX-TORRE-1 — cabecalho de coluna ordenavel, tematizado.
 *
 * Irmao do `SortableHeader` legado (que serve as rotas ainda nao migradas e
 * esta' preso por testes de contrato de alvo de 44px). Este usa tokens, cabe
 * numa tabela densa e continua sendo um `<button>` de verdade dentro do `<th>`
 * — a area clicavel e' a celula inteira.
 *
 * `aria-sort` no `<th>` e' o que o leitor de tela anuncia; a seta e' so' a
 * pista visual do mesmo fato.
 */
export default function SortHeader({
  rotulo,
  coluna,
  sort,
  aoOrdenar,
  alinhamento = "right",
  className = "",
}: {
  rotulo: React.ReactNode;
  coluna: string;
  sort: SortState;
  aoOrdenar: (coluna: string) => void;
  alinhamento?: "left" | "right";
  className?: string;
}) {
  const ativo = sort.column === coluna;
  return (
    <th
      scope="col"
      aria-sort={ativo ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
      className={`p-0 text-xs font-semibold uppercase tracking-wide ${
        alinhamento === "right" ? "text-right" : "text-left"
      } ${className}`}
    >
      <button
        type="button"
        onClick={() => aoOrdenar(coluna)}
        className={`flex min-h-11 w-full items-center gap-1 px-3 py-1.5 transition-colors hover:text-ink ${
          alinhamento === "right" ? "flex-row-reverse justify-end" : "justify-start"
        } ${ativo ? "text-accent" : "text-ink-faint"}`}
      >
        <span>{rotulo}</span>
        <span aria-hidden="true" className="text-xs leading-none">
          {ativo ? (sort.direction === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </th>
  );
}
