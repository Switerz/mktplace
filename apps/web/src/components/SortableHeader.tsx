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
      className={`px-4 py-3 text-xs font-semibold text-slate-600 uppercase tracking-wider ${
        align === "right" ? "text-right" : "text-left"
      } ${className}`}
    >
      <button
        type="button"
        onClick={() => onSort(column)}
        className={`inline-flex items-center gap-1 hover:text-violet-700 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-1 rounded ${
          align === "right" ? "flex-row-reverse" : ""
        } ${active ? "text-violet-700" : ""}`}
      >
        <span>{label}</span>
        <SortIcon active={active} direction={sort.direction} />
      </button>
    </th>
  );
}
