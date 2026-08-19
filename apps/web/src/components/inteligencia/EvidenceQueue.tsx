"use client";

import SortableHeader from "@/components/SortableHeader";
import TableScrollHint from "@/components/TableScrollHint";
import { useSortableTable, type SortColumnType } from "@/lib/use-sortable-table";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { brandLabel } from "@/lib/inteligencia/brands";
import { fractionAsPctBr, pctBr, roasBr } from "@/lib/inteligencia/format";
import { LENS_LABELS, type Lens } from "@/lib/inteligencia/lens";
import { KIND_LABELS, LENS_COLUMNS, type EvidenceItem } from "@/lib/inteligencia/queue";

/** `null` vira "—" com título explicativo; zero continua zero. */
function n(v: number | null | undefined, fmt: (x: number) => string): string {
  return v == null ? "—" : fmt(v);
}

function pct(v: number | null | undefined, decimals = 1): string {
  return v == null ? "—" : pctBr(v, decimals);
}

/** `revenue_share_pct` vem como FRAÇÃO no contrato; os demais já são percentuais. */
function sharePct(v: number | null | undefined): string {
  return v == null ? "—" : fractionAsPctBr(v);
}

const VELOCITY_LABEL: Record<string, string> = {
  high: "Alta", medium: "Média", low: "Baixa", zero: "Zero",
};
const VELOCITY_CLASS: Record<string, string> = {
  high: "bg-emerald-50 text-emerald-800 border-emerald-200",
  medium: "bg-amber-50 text-amber-800 border-amber-200",
  low: "bg-rose-50 text-rose-700 border-rose-200",
  zero: "bg-slate-100 text-slate-600 border-slate-200",
};

function VelocityChip({ v }: { v: string | null }) {
  if (!v) return <span className="text-slate-400">—</span>;
  return (
    <span className={`inline-block px-2 py-0.5 rounded-md text-xs font-semibold border ${VELOCITY_CLASS[v] ?? VELOCITY_CLASS.zero}`}>
      {VELOCITY_LABEL[v] ?? v}
    </span>
  );
}

const BUCKET_CLASS: Record<string, string> = {
  A_top50: "bg-violet-600 text-white",
  B_next30: "bg-violet-400 text-white",
  C_next15: "bg-violet-100 text-violet-800",
  D_tail: "bg-slate-100 text-slate-600",
};

function BucketChip({ v }: { v: string | null }) {
  if (!v) return <span className="text-slate-400">—</span>;
  return (
    <span className={`inline-block px-2 py-0.5 rounded-md text-xs font-bold ${BUCKET_CLASS[v] ?? "bg-slate-100 text-slate-600"}`}>
      {v.charAt(0)}
    </span>
  );
}

function truncate(s: string | null | undefined, max: number): string {
  if (!s) return "—";
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

interface Props {
  lens: Lens;
  rows: EvidenceItem[];
  onOpenDetail: (item: EvidenceItem) => void;
  loading?: boolean;
}

/**
 * Bloco 6 — fila priorizada de evidências (Gate V3-1A).
 *
 * Substitui as TRÊS tabelas ML da página anterior por uma superfície com
 * lentes. O que mudou de comportamento, e é o ponto do bloco:
 *
 * - a linha NÃO é clicável e NÃO recebe hover de elemento acionável — o
 *   acionamento é um `<button>` "Detalhe de <produto>" com nome acessível;
 * - a antiga coluna "Ação" era um `<span>` estilizado de botão (falsa
 *   affordance) e deixou de existir;
 * - colunas por lente, em vez das nove em todas;
 * - cabeçalho sticky DENTRO do card, com scroll interno — a página nunca
 *   rola na horizontal.
 *
 * No mobile a tabela de sete colunas não é comprimida: vira um card por
 * evidência, com duas métricas e o mesmo botão de detalhe.
 */
export default function EvidenceQueue({ lens, rows, onOpenDetail, loading }: Props) {
  const columns = LENS_COLUMNS[lens];

  const columnTypes: Record<string, SortColumnType> = {
    brand: "text", title: "text", kind: "text",
    gmv: "numeric", ad_spend: "numeric", ad_roas: "numeric", ad_acos_pct: "numeric",
    revenue_share_pct: "numeric", units_sold: "numeric", cancel_rate_pct: "numeric",
    days_advertised: "numeric",
  };
  function getValue(row: EvidenceItem, column: string): string | number | null {
    switch (column) {
      case "brand": return brandLabel(row.brand);
      case "title": return row.title ?? "";
      case "kind": return KIND_LABELS[row.kind];
      case "gmv": return row.gmv;
      case "ad_spend": return row.ad_spend;
      case "ad_roas": return row.ad_roas;
      case "ad_acos_pct": return row.ad_acos_pct;
      case "revenue_share_pct": return row.revenue_share_pct;
      case "units_sold": return row.units_sold;
      case "cancel_rate_pct": return row.cancel_rate_pct;
      case "days_advertised": return row.days_advertised;
      default: return null;
    }
  }
  const sort = useSortableTable(rows, getValue, columnTypes);

  const HEAD: Record<string, { label: string; align: "left" | "right"; sortable: boolean }> = {
    kind: { label: "Origem", align: "left", sortable: true },
    brand: { label: "Marca", align: "left", sortable: true },
    title: { label: "Produto", align: "left", sortable: true },
    gmv: { label: "GMV", align: "right", sortable: true },
    ad_spend: { label: "Ad spend", align: "right", sortable: true },
    ad_roas: { label: "ROAS", align: "right", sortable: true },
    ad_acos_pct: { label: "ACOS", align: "right", sortable: true },
    revenue_share_pct: { label: "Part. GMV", align: "right", sortable: true },
    units_sold: { label: "Unidades", align: "right", sortable: true },
    cancel_rate_pct: { label: "Cancel.", align: "right", sortable: true },
    days_advertised: { label: "Dias c/ ads", align: "right", sortable: true },
    pareto_bucket: { label: "Pareto", align: "left", sortable: false },
    revenue_velocity: { label: "Velocidade", align: "left", sortable: false },
  };

  function cell(row: EvidenceItem, column: string) {
    switch (column) {
      case "kind":
        return <span className="text-xs font-semibold text-slate-600">{KIND_LABELS[row.kind]}</span>;
      case "brand":
        return <span className="text-xs font-semibold text-slate-700 whitespace-nowrap">{brandLabel(row.brand)}</span>;
      case "title":
        return <span className="text-sm text-slate-700" title={row.title ?? undefined}>{truncate(row.title, 44)}</span>;
      case "gmv":
        return <span className="text-sm font-semibold text-slate-900 tabular-nums">{n(row.gmv, fmtBrl)}</span>;
      case "ad_spend":
        return <span className="text-sm font-semibold text-rose-700 tabular-nums">{n(row.ad_spend, fmtBrl)}</span>;
      case "ad_roas":
        return (
          <span className="text-sm font-semibold text-slate-800 tabular-nums" title={row.ad_roas == null ? "sem dado" : undefined}>
            {row.ad_roas == null ? "—" : roasBr(row.ad_roas)}
          </span>
        );
      case "ad_acos_pct":
        return <span className="text-sm text-slate-600 tabular-nums">{pct(row.ad_acos_pct)}</span>;
      case "revenue_share_pct":
        return <span className="text-sm text-slate-600 tabular-nums">{sharePct(row.revenue_share_pct)}</span>;
      case "units_sold":
        return <span className="text-sm text-slate-600 tabular-nums">{n(row.units_sold, fmtNumber)}</span>;
      case "cancel_rate_pct":
        return <span className="text-sm text-slate-600 tabular-nums">{pct(row.cancel_rate_pct)}</span>;
      case "days_advertised":
        return <span className="text-sm text-slate-600 tabular-nums">{n(row.days_advertised, fmtNumber)}</span>;
      case "pareto_bucket":
        return <BucketChip v={row.pareto_bucket} />;
      case "revenue_velocity":
        return <VelocityChip v={row.revenue_velocity} />;
      default:
        return null;
    }
  }

  if (!loading && rows.length === 0) {
    return (
      <p className="px-6 py-10 text-center text-sm text-slate-500">
        Nenhuma evidência na lente <span className="font-semibold">{LENS_LABELS[lens]}</span> para o escopo
        selecionado. Não é modo demonstração — a lista veio vazia.
      </p>
    );
  }

  return (
    <>
      {/* Desktop e tablet: uma tabela densa, cabeçalho sticky DENTRO do card */}
      <div className="hidden sm:block">
        <TableScrollHint>
          <div className="max-h-[520px] overflow-y-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50 text-left">
                  {columns.map((c) => {
                    const h = HEAD[c];
                    if (!h) return null;
                    return h.sortable ? (
                      <SortableHeader
                        key={c}
                        label={h.label}
                        column={c}
                        sort={sort.sort}
                        onSort={sort.toggleSort}
                        align={h.align}
                      />
                    ) : (
                      <th
                        key={c}
                        className={`sticky top-0 z-10 bg-slate-50 px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider ${h.align === "right" ? "text-right" : "text-left"}`}
                      >
                        {h.label}
                      </th>
                    );
                  })}
                  <th className="sticky top-0 z-10 bg-slate-50 px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider text-right">
                    Detalhe
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {sort.sortedRows.map((row, i) => (
                  <tr key={`${row.kind}-${row.brand}-${i}`}>
                    {columns.map((c) => (
                      <td key={c} className={`px-4 py-3 ${HEAD[c]?.align === "right" ? "text-right" : ""}`}>
                        {cell(row, c)}
                      </td>
                    ))}
                    <td className="px-4 py-3 text-right">
                      <button
                        type="button"
                        onClick={() => onOpenDetail(row)}
                        aria-label={`Detalhe de ${row.title ?? "produto sem título"}, marca ${brandLabel(row.brand)}`}
                        className="inline-flex items-center justify-center min-h-11 px-3 text-xs font-semibold text-violet-700 border border-violet-200 rounded-lg hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
                      >
                        Detalhe
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </TableScrollHint>
      </div>

      {/* Mobile: card por evidência — a tabela larga não é comprimida */}
      <ul className="sm:hidden divide-y divide-slate-100 list-none p-0 m-0">
        {sort.sortedRows.map((row, i) => (
          <li key={`${row.kind}-${row.brand}-m${i}`} className="px-4 py-4 flex flex-col gap-2">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-semibold text-slate-500">{brandLabel(row.brand)}</p>
                <p className="text-sm text-slate-800 leading-snug">{truncate(row.title, 60)}</p>
              </div>
              <BucketChip v={row.pareto_bucket} />
            </div>
            <dl className="grid grid-cols-2 gap-2 m-0">
              <div>
                <dt className="text-xs text-slate-500">{row.kind === "parar" ? "Ad spend" : "GMV"}</dt>
                <dd className="text-sm font-semibold text-slate-900 tabular-nums m-0">
                  {row.kind === "parar" ? n(row.ad_spend, fmtBrl) : n(row.gmv, fmtBrl)}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">{row.kind === "escalar" ? "ROAS" : "Velocidade"}</dt>
                <dd className="text-sm font-semibold text-slate-900 tabular-nums m-0">
                  {row.kind === "escalar"
                    ? (row.ad_roas == null ? "—" : roasBr(row.ad_roas))
                    : (VELOCITY_LABEL[row.revenue_velocity ?? ""] ?? "—")}
                </dd>
              </div>
            </dl>
            <button
              type="button"
              onClick={() => onOpenDetail(row)}
              aria-label={`Detalhe de ${row.title ?? "produto sem título"}, marca ${brandLabel(row.brand)}`}
              className="self-start inline-flex items-center min-h-11 px-3 text-xs font-semibold text-violet-700 border border-violet-200 rounded-lg hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
            >
              Detalhe
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}
