import type { ProdutoTikTokRow } from "@/lib/api-client";
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

function AttributionBar({ v, l, c }: { v: number | null; l: number | null; c: number | null }) {
  const vp = v ?? 0; const lp = l ?? 0; const cp = c ?? 0;
  const other = Math.max(0, 100 - vp - lp - cp);
  return (
    <div className="flex h-1.5 rounded-full overflow-hidden w-20 bg-slate-100">
      <div className="bg-violet-500" style={{ width: `${vp}%` }} />
      <div className="bg-rose-500" style={{ width: `${lp}%` }} />
      <div className="bg-sky-400" style={{ width: `${cp}%` }} />
      {other > 0.5 && <div className="bg-slate-300" style={{ width: `${other}%` }} />}
    </div>
  );
}

interface Props {
  items: { total: number; items: ProdutoTikTokRow[] } | null;
  loading: boolean;
  sort: SortState;
  onSort: (column: string) => void;
  pagination: { limit: number; offset: number; onChange: (offset: number) => void };
}

/** Colunas especificas do TikTok Shop: composicao do GMV por video/live/card,
 * taxa de problemas (ressalva de cobertura), rating e numero de avaliacoes. */
export default function TikTokProductTable({ items, loading, sort, onSort, pagination }: Props) {
  const COL_COUNT = 8;
  return (
    <ProductTableShell<ProdutoTikTokRow>
      ariaLabel="Produtos TikTok Shop"
      colCount={COL_COUNT}
      loading={loading}
      items={items?.items ?? null}
      pagination={items ? { total: items.total, ...pagination } : undefined}
      thead={
        <tr className="bg-slate-50 text-left">
          <SortableHeader label="Produto" column="product_name" sort={sort} onSort={onSort} align="left" />
          <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Marca</th>
          <SortableHeader label="GMV" column="gmv" sort={sort} onSort={onSort} align="right" />
          <SortableHeader label="Pedidos" column="orders" sort={sort} onSort={onSort} align="right" />
          <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-center">Pareto</th>
          <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Canal</th>
          <SortableHeader label="Prob.%" column="problem_rate" sort={sort} onSort={onSort} align="right" />
          <SortableHeader label="Rating" column="rating_avg" sort={sort} onSort={onSort} align="right" />
        </tr>
      }
      renderRow={(p) => (
        <tr key={`${p.brand}-${p.product_id}`} className="hover:bg-slate-50 transition-colors">
          <td className="px-6 py-3 max-w-xs">
            <p className="text-slate-700 font-medium truncate leading-tight">{p.product_name}</p>
            <p className="text-[11px] text-slate-500 tabular-nums mt-0.5">{p.product_id}</p>
          </td>
          <td className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase whitespace-nowrap">{p.brand}</td>
          <td className="px-4 py-3 text-right tabular-nums text-slate-700 font-medium whitespace-nowrap">{fmtBrl(p.gmv)}</td>
          <td className="px-4 py-3 text-right whitespace-nowrap">
            <p className="tabular-nums text-slate-600">{fmtNumber(p.orders)} ped.</p>
            {p.items_sold != null && p.items_sold !== p.orders && (
              <p className="text-[11px] text-slate-500 tabular-nums mt-0.5">{fmtNumber(p.items_sold)} unid.</p>
            )}
          </td>
          <td className="px-4 py-3 text-center">
            {p.pareto_bucket && (
              <span className={`inline-block text-[10px] font-bold px-1.5 py-0.5 rounded ${PARETO_COLOR[p.pareto_bucket] ?? "bg-slate-100 text-slate-500"}`}>
                {PARETO_LABEL[p.pareto_bucket] ?? p.pareto_bucket}
              </span>
            )}
          </td>
          <td className="px-4 py-3">
            <div className="flex flex-col gap-1">
              <AttributionBar v={p.pct_gmv_video} l={p.pct_gmv_live} c={p.pct_gmv_card} />
              <p className="text-[10px] text-slate-500 tabular-nums">
                {[
                  p.pct_gmv_video != null && `V ${p.pct_gmv_video.toFixed(0)}%`,
                  p.pct_gmv_live != null && `L ${p.pct_gmv_live.toFixed(0)}%`,
                  p.pct_gmv_card != null && `C ${p.pct_gmv_card.toFixed(0)}%`,
                ].filter(Boolean).join(" · ")}
              </p>
            </div>
          </td>
          <td className="px-4 py-3 text-right tabular-nums">
            {p.problem_rate != null ? (
              <span className={p.problem_rate < 2 ? "text-emerald-700" : p.problem_rate < 5 ? "text-amber-700" : "text-rose-700 font-semibold"}>
                {p.problem_rate.toFixed(1)}%
              </span>
            ) : <span className="text-slate-300">—</span>}
          </td>
          <td className="px-4 py-3 text-right whitespace-nowrap">
            {p.rating_avg != null ? (
              <>
                <p className="tabular-nums text-slate-600">{p.rating_avg.toFixed(1)}</p>
                {p.total_ratings != null && (
                  <p className="text-[11px] text-slate-500 tabular-nums mt-0.5">{fmtNumber(p.total_ratings)} aval.</p>
                )}
              </>
            ) : <span className="text-slate-300">—</span>}
          </td>
        </tr>
      )}
    />
  );
}
