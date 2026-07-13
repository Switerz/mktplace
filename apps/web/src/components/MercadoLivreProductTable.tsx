import type { ProdutoMLRow } from "@/lib/api-client";
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
const VELOCITY_COLOR: Record<string, string> = {
  high: "text-emerald-700",
  medium: "text-amber-700",
  low: "text-slate-500",
  zero: "text-rose-600",
};
const EFFICIENCY_COLOR: Record<string, string> = {
  star: "bg-amber-100 text-amber-800",
  efficient: "bg-emerald-100 text-emerald-800",
  marginal: "bg-slate-100 text-slate-600",
  inefficient: "bg-rose-100 text-rose-700",
  no_ads: "bg-slate-100 text-slate-500",
  no_return: "bg-rose-100 text-rose-800",
};
const EFFICIENCY_LABEL: Record<string, string> = {
  star: "estrela", efficient: "eficiente", marginal: "marginal",
  inefficient: "ineficiente", no_ads: "sem ads", no_return: "sem retorno",
};
const SIGNAL_COLOR: Record<string, string> = {
  "ACAO:": "bg-emerald-50 border-emerald-200 text-emerald-800",
  "ALERTA:": "bg-rose-50 border-rose-200 text-rose-800",
  "ATENCAO:": "bg-amber-50 border-amber-200 text-amber-800",
  "OPORTUNIDADE:": "bg-cyan-50 border-cyan-200 text-cyan-800",
  "REVIEW:": "bg-slate-50 border-slate-200 text-slate-600",
};

function signalStyle(signal: string | null): string {
  if (!signal) return "";
  const key = Object.keys(SIGNAL_COLOR).find((k) => signal.startsWith(k));
  return key ? SIGNAL_COLOR[key] : "bg-slate-50 border-slate-200 text-slate-600";
}

interface Props {
  items: { total: number; items: ProdutoMLRow[] } | null;
  loading: boolean;
  sort: SortState;
  onSort: (column: string) => void;
  pagination: { limit: number; offset: number; onChange: (offset: number) => void };
}

/** Colunas especificas do Mercado Livre: preco medio, cancelamento,
 * eficiencia de Ads (ROAS/ACOS/spend), velocidade, status e sinal de acao.
 * Nao inclui margem: `estimated_margin` tem formula desconhecida na fonte
 * (ver docs/sections/produtos_audit.md secao 10.3) e nunca deve ser exibido
 * como margem. */
export default function MercadoLivreProductTable({ items, loading, sort, onSort, pagination }: Props) {
  const COL_COUNT = 10;
  return (
    <ProductTableShell<ProdutoMLRow>
      ariaLabel="Produtos Mercado Livre"
      colCount={COL_COUNT}
      loading={loading}
      items={items?.items ?? null}
      pagination={items ? { total: items.total, ...pagination } : undefined}
      thead={
        <tr className="bg-slate-50 text-left">
          <SortableHeader label="Produto" column="title" sort={sort} onSort={onSort} align="left" />
          <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Marca</th>
          <SortableHeader label="Receita" column="gross_revenue" sort={sort} onSort={onSort} align="right" />
          <SortableHeader label="Preço Médio" column="avg_price" sort={sort} onSort={onSort} align="right" />
          <SortableHeader label="Unid." column="units_sold" sort={sort} onSort={onSort} align="right" />
          <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-center">Pareto</th>
          <SortableHeader label="Cancel." column="cancel_rate_pct" sort={sort} onSort={onSort} align="right" />
          <SortableHeader label="Eficiência Ads" column="ad_roas" sort={sort} onSort={onSort} align="right" />
          <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-center">Efic. Ads</th>
          <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Sinal</th>
        </tr>
      }
      renderRow={(p) => (
        <tr key={`${p.brand}-${p.item_id}`} className="hover:bg-slate-50 transition-colors">
          <td className="px-6 py-3 max-w-xs">
            <p className="text-slate-700 font-medium truncate leading-tight">{p.title}</p>
            {p.seller_sku && <p className="text-[11px] text-slate-500 tabular-nums mt-0.5">{p.seller_sku}</p>}
          </td>
          <td className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase whitespace-nowrap">{p.brand}</td>
          <td className="px-4 py-3 text-right whitespace-nowrap">
            <p className="tabular-nums text-slate-700 font-medium">{fmtBrl(p.gross_revenue)}</p>
            {p.revenue_share_pct != null && (
              <p className="text-[11px] text-slate-500 tabular-nums mt-0.5">{p.revenue_share_pct.toFixed(1)}% do total</p>
            )}
          </td>
          <td className="px-4 py-3 text-right whitespace-nowrap tabular-nums text-slate-600">
            {p.avg_price != null ? fmtBrl(p.avg_price) : <span className="text-slate-300">—</span>}
          </td>
          <td className="px-4 py-3 text-right whitespace-nowrap">
            <p className="tabular-nums text-slate-600">{fmtNumber(p.units_sold)}</p>
            {p.unique_buyers != null && (
              <p className="text-[11px] text-slate-500 tabular-nums mt-0.5">{fmtNumber(p.unique_buyers)} compr.</p>
            )}
          </td>
          <td className="px-4 py-3 text-center">
            <div className="flex flex-col items-center gap-1">
              {p.pareto_bucket && (
                <span className={`inline-block text-[10px] font-bold px-1.5 py-0.5 rounded ${PARETO_COLOR[p.pareto_bucket] ?? "bg-slate-100 text-slate-500"}`}>
                  {PARETO_LABEL[p.pareto_bucket] ?? p.pareto_bucket}
                </span>
              )}
              {p.revenue_velocity && (
                <span className={`text-[10px] font-medium ${VELOCITY_COLOR[p.revenue_velocity] ?? "text-slate-500"}`}>
                  {p.revenue_velocity}
                </span>
              )}
            </div>
          </td>
          <td className="px-4 py-3 text-right tabular-nums">
            {p.cancel_rate_pct != null ? (
              <span className={p.cancel_rate_pct < 2 ? "text-emerald-700" : p.cancel_rate_pct < 5 ? "text-amber-700 font-semibold" : "text-rose-700 font-semibold"}>
                {p.cancel_rate_pct.toFixed(1)}%
              </span>
            ) : <span className="text-slate-300">—</span>}
          </td>
          <td className="px-4 py-3 text-right tabular-nums whitespace-nowrap">
            {p.ad_roas != null ? (
              <span className={p.ad_roas >= 4 ? "text-emerald-700 font-semibold" : p.ad_roas >= 2.5 ? "text-amber-700" : "text-rose-700"}>
                {p.ad_roas.toFixed(1)}x ROAS
              </span>
            ) : <span className="text-slate-300">—</span>}
            {(p.ad_acos_pct != null || p.ad_spend != null) && (
              <p className="text-[11px] text-slate-500 tabular-nums mt-0.5">
                {[
                  p.ad_acos_pct != null && `ACOS ${p.ad_acos_pct.toFixed(1)}%`,
                  p.ad_spend != null && `Spend ${fmtBrl(p.ad_spend)}`,
                ].filter(Boolean).join(" · ")}
              </p>
            )}
          </td>
          <td className="px-4 py-3 text-center">
            {p.ad_efficiency && (
              <span className={`inline-block text-[10px] font-semibold px-1.5 py-0.5 rounded ${EFFICIENCY_COLOR[p.ad_efficiency] ?? "bg-slate-100 text-slate-500"}`}>
                {EFFICIENCY_LABEL[p.ad_efficiency] ?? p.ad_efficiency}
              </span>
            )}
          </td>
          <td className="px-4 py-3 max-w-[200px]">
            {p.action_signal && (
              <span className={`inline-block text-[10px] font-medium px-2 py-0.5 rounded border leading-tight ${signalStyle(p.action_signal)}`}>
                {p.action_signal}
              </span>
            )}
          </td>
        </tr>
      )}
    />
  );
}
