import type { ProdutoShopeeRow } from "@/lib/api-client";
import type { SortState } from "@/lib/use-sortable-table";
import SortableHeader from "@/components/SortableHeader";
import ProductTableShell from "@/components/ProductTableShell";
import { fmtBrl, fmtNumber } from "@/lib/formatters";

const PARETO_LABEL: Record<string, string> = {
  A_top50: "A", B_next30: "B", C_next15: "C", D_tail: "D",
};
const PARETO_COLOR: Record<string, string> = {
  A_top50: "bg-violet-100 text-violet-800",
  B_next30: "bg-cyan-100 text-cyan-800",
  C_next15: "bg-amber-100 text-amber-800",
  D_tail: "bg-slate-100 text-slate-500",
};

interface Props {
  items: { total: number; items: ProdutoShopeeRow[] } | null;
  loading: boolean;
  sort: SortState;
  onSort: (column: string) => void;
  pagination: { limit: number; offset: number; onChange: (offset: number) => void };
}

/** Colunas especificas da Shopee: variacao, SKU, pedidos, cancelamentos,
 * ticket medio e compradores (quando confiavel). */
export default function ShopeeProductTable({ items, loading, sort, onSort, pagination }: Props) {
  const COL_COUNT = 10;
  return (
    <ProductTableShell<ProdutoShopeeRow>
      ariaLabel="Produtos Shopee"
      colCount={COL_COUNT}
      loading={loading}
      items={items?.items ?? null}
      pagination={items ? { total: items.total, ...pagination } : undefined}
      thead={
        <tr className="bg-slate-50 text-left">
          <SortableHeader label="Produto" column="product_name" sort={sort} onSort={onSort} align="left" />
          <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Variação</th>
          <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">SKU</th>
          <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Marca</th>
          <SortableHeader label="GMV" column="gmv" sort={sort} onSort={onSort} align="right" />
          <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-center">Pareto</th>
          <SortableHeader label="Unid." column="units_sold" sort={sort} onSort={onSort} align="right" />
          <SortableHeader label="Pedidos" column="orders" sort={sort} onSort={onSort} align="right" />
          <SortableHeader label="Cancel.%" column="cancel_rate_pct" sort={sort} onSort={onSort} align="right" />
          <SortableHeader label="Ticket Médio" column="avg_price" sort={sort} onSort={onSort} align="right" />
        </tr>
      }
      renderRow={(p, i) => (
        <tr key={`${p.brand}-${p.sku_ref ?? "sem-sku"}-${p.variation_name ?? ""}-${i}`} className="hover:bg-slate-50 transition-colors">
          <td className="px-6 py-3 max-w-xs">
            <p className="text-slate-700 font-medium truncate leading-tight">{p.product_name}</p>
          </td>
          <td className="px-4 py-3 text-xs text-slate-500 max-w-[120px] truncate">
            {p.variation_name ?? <span className="text-slate-300">—</span>}
          </td>
          <td className="px-4 py-3 text-[11px] text-slate-500 tabular-nums whitespace-nowrap">
            {p.sku_ref ?? <span className="text-slate-300">—</span>}
          </td>
          <td className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase whitespace-nowrap">{p.brand}</td>
          <td className="px-4 py-3 text-right tabular-nums text-slate-700 font-medium whitespace-nowrap">{fmtBrl(p.gmv)}</td>
          <td className="px-4 py-3 text-center">
            {p.pareto_bucket && (
              <span className={`inline-block text-[10px] font-bold px-1.5 py-0.5 rounded ${PARETO_COLOR[p.pareto_bucket] ?? "bg-slate-100 text-slate-500"}`}>
                {PARETO_LABEL[p.pareto_bucket] ?? p.pareto_bucket}
              </span>
            )}
          </td>
          <td className="px-4 py-3 text-right tabular-nums text-slate-600 whitespace-nowrap">{fmtNumber(p.units_sold)}</td>
          <td className="px-4 py-3 text-right tabular-nums text-slate-600 whitespace-nowrap">{fmtNumber(p.orders)}</td>
          <td className="px-4 py-3 text-right tabular-nums whitespace-nowrap">
            {p.cancel_rate_pct != null ? (
              <span className={p.cancel_rate_pct < 2 ? "text-emerald-700" : p.cancel_rate_pct < 5 ? "text-amber-700 font-semibold" : "text-rose-700 font-semibold"}>
                {p.cancel_rate_pct.toFixed(1)}%
              </span>
            ) : <span className="text-slate-300">—</span>}
          </td>
          <td className="px-4 py-3 text-right tabular-nums text-slate-600 whitespace-nowrap">
            {p.avg_price != null ? fmtBrl(p.avg_price) : <span className="text-slate-300">—</span>}
          </td>
        </tr>
      )}
    />
  );
}
