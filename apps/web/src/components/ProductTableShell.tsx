import type { ReactNode } from "react";
import { SkeletonTableRows } from "@/components/Skeleton";
import { fmtNumber } from "@/lib/formatters";

interface PaginationProps {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}

function Pagination({ total, limit, offset, onChange }: PaginationProps) {
  const pages = Math.ceil(total / limit);
  const current = Math.floor(offset / limit);
  if (pages <= 1) return null;

  const visible: (number | "…")[] = [];
  for (let i = 0; i < pages; i++) {
    if (i === 0 || i === pages - 1 || Math.abs(i - current) <= 2) {
      visible.push(i);
    } else if (visible[visible.length - 1] !== "…") {
      visible.push("…");
    }
  }

  return (
    <div className="flex items-center justify-between px-6 py-3 border-t border-slate-50">
      <p className="text-xs text-slate-500 tabular-nums">
        {offset + 1}–{Math.min(offset + limit, total)} de {fmtNumber(total)} produtos
      </p>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onChange(Math.max(0, offset - limit))}
          disabled={offset === 0}
          className="px-2 py-1 text-xs rounded text-slate-500 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-default transition-colors"
        >
          ‹
        </button>
        {visible.map((v, i) =>
          v === "…" ? (
            <span key={`e${i}`} className="px-1 text-xs text-slate-400">…</span>
          ) : (
            <button
              key={v}
              onClick={() => onChange((v as number) * limit)}
              className={`w-7 h-7 text-xs rounded transition-colors ${
                v === current ? "bg-violet-600 text-white font-semibold" : "text-slate-500 hover:bg-slate-100"
              }`}
            >
              {(v as number) + 1}
            </button>
          )
        )}
        <button
          onClick={() => onChange(Math.min((pages - 1) * limit, offset + limit))}
          disabled={offset + limit >= total}
          className="px-2 py-1 text-xs rounded text-slate-500 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-default transition-colors"
        >
          ›
        </button>
      </div>
    </div>
  );
}

interface Props<T> {
  ariaLabel: string;
  colCount: number;
  loading: boolean;
  /** null = API offline/sem resposta ainda; array (mesmo vazio) = resposta real da API. */
  items: T[] | null;
  renderRow: (item: T, index: number) => ReactNode;
  thead: ReactNode;
  offlineMessage?: string;
  emptyMessage?: string;
  pagination?: PaginationProps;
}

/**
 * Shell compartilhado pelas 3 tabelas de Produtos: estados de loading,
 * offline (API sem resposta), vazio (0 resultados para o filtro) e
 * paginacao. As colunas/celulas ficam inteiramente a cargo de quem chama
 * (thead + renderRow) — este componente nao conhece nenhuma coluna
 * especifica de canal.
 */
export default function ProductTableShell<T>({
  ariaLabel, colCount, loading, items, renderRow, thead,
  offlineMessage = "API offline — dados de produtos indisponíveis sem conexão.",
  emptyMessage = "Nenhum produto encontrado para os filtros selecionados.",
  pagination,
}: Props<T>) {
  const isEmpty = items !== null && items.length === 0;
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full text-sm" aria-label={ariaLabel}>
          <thead>{thead}</thead>
          <tbody className="divide-y divide-slate-50">
            {loading && items === null && <SkeletonTableRows rows={8} cols={colCount} />}
            {!loading && items === null && (
              <tr>
                <td colSpan={colCount} className="px-6 py-12 text-center text-slate-500">
                  {offlineMessage}
                </td>
              </tr>
            )}
            {items !== null && isEmpty && !loading && (
              <tr>
                <td colSpan={colCount} className="px-6 py-12 text-center text-slate-500">
                  {emptyMessage}
                </td>
              </tr>
            )}
            {items !== null && items.map((item, i) => renderRow(item, i))}
            {items !== null && !isEmpty && loading && (
              <tr>
                <td colSpan={colCount} className="px-6 py-3 text-center">
                  <span className="text-xs text-violet-400 animate-pulse">Atualizando...</span>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {pagination && <Pagination {...pagination} />}
    </>
  );
}
