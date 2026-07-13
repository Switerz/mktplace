"use client";

import type { SortState } from "@/lib/use-sortable-table";

interface Props {
  label: React.ReactNode;
  column: string;
  sort: SortState;
  onSort: (column: string) => void;
  align?: "left" | "right";
  className?: string;
}

function SortIcon({ active, direction }: { active: boolean; direction: SortState["direction"] }) {
  if (!active || !direction) {
    return <span className="text-slate-300 text-[10px] leading-none" aria-hidden="true">↕</span>;
  }
  return (
    <span className="text-violet-600 text-[10px] leading-none" aria-hidden="true">
      {direction === "asc" ? "▲" : "▼"}
    </span>
  );
}

/**
 * Cabeçalho de coluna ordenável reutilizável. Substitui um <th> estático:
 * `<SortableHeader label="GMV" column="gmv" sort={sort} onSort={toggleSort} />`
 *
 * A área clicável é a célula inteira (o <button> preenche o <th> via
 * w-full h-full), não só o texto/ícone — mais fácil de acertar no touch e
 * com mouse, sem abrir mão do <button> semântico nem do foco visível.
 */
export default function SortableHeader({ label, column, sort, onSort, align = "right", className = "" }: Props) {
  const active = sort.column === column;
  const ariaSort = active
    ? sort.direction === "asc" ? "ascending" : "descending"
    : "none";

  return (
    <th
      scope="col"
      aria-sort={ariaSort}
      className={`text-xs font-semibold text-slate-600 uppercase tracking-wider p-0 ${
        align === "right" ? "text-right" : "text-left"
      } ${className}`}
    >
      <button
        type="button"
        onClick={() => onSort(column)}
        className={`w-full h-full flex items-center gap-1 px-4 py-3 hover:text-violet-700 hover:bg-slate-100/60 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500 ${
          align === "right" ? "justify-end flex-row-reverse" : "justify-start"
        } ${active ? "text-violet-700" : ""}`}
      >
        <span>{label}</span>
        <SortIcon active={active} direction={sort.direction} />
      </button>
    </th>
  );
}
