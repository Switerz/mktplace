"use client";

import Link from "next/link";
import type { BrandRow, OverviewData } from "@/lib/api-client";
import type { Marketplace } from "@/lib/mock-data";
import {
  KPI_META,
  avgTicketBrandBreakdown,
  gmvBrandBreakdown,
  gmvChannelBreakdown,
  ordersBrandBreakdown,
  roasBreakdown,
  type KpiKind,
} from "@/lib/kpi-drilldown";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { fmtRefreshedAt } from "@/lib/filters/format";

interface Props {
  kind: KpiKind;
  value: string;
  periodLabel: string;
  refreshedAt: string | null;
  overview: OverviewData;
  brands: BrandRow[];
  /** Selecao atual de marketplace (Finding 3) — repassada para a
   * decomposicao de GMV por canal, para distinguir canal nao selecionado
   * (omitido) de canal selecionado sem valor no contrato ("Sem dado"). */
  channels: Marketplace[];
  buildHref: (href: string) => string;
}

function fmtPct1(v: number | null): string {
  return v == null ? "—" : `${v.toFixed(1)}%`;
}

/**
 * Conteudo interno do KpiDrilldownDialog, um por KPI (Gate U2, Task 4) —
 * decomposicao usando somente dados ja carregados (OverviewData/BrandRow),
 * sem fetch/endpoint novo. Nao existe um framework generico de KPI aqui, so
 * um `switch` direto sobre os 4 casos suportados.
 */
export default function KpiDrilldownContent({ kind, value, periodLabel, refreshedAt, overview, brands, channels, buildHref }: Props) {
  const meta = KPI_META[kind];

  return (
    <div className="flex flex-col gap-4 text-sm">
      <div>
        <p className="text-3xl font-bold text-gray-900 tabular-nums leading-none">{value}</p>
        <p className="text-xs text-slate-400 mt-1">
          {periodLabel}
          {refreshedAt && <> · Atualizado em {fmtRefreshedAt(refreshedAt)}</>}
        </p>
      </div>

      <div className="flex flex-col gap-1">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Como é calculado</p>
        <p className="text-slate-700">{meta.definition}</p>
        <p className="text-xs text-slate-500 font-mono mt-1">{meta.formula}</p>
        {meta.caveat && <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5 mt-1">{meta.caveat}</p>}
      </div>

      <div className="flex flex-col gap-2">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Decomposição</p>
        {kind === "gmv" && <GmvBreakdown overview={overview} brands={brands} channels={channels} />}
        {kind === "orders" && <OrdersBreakdown brands={brands} />}
        {kind === "avg_ticket" && <AvgTicketBreakdown brands={brands} />}
        {kind === "roas" && <RoasBreakdown overview={overview} />}
      </div>

      <Link
        href={buildHref(meta.nextHref)}
        className="text-sm font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded"
      >
        {meta.nextLabel} →
      </Link>
    </div>
  );
}

function GmvBreakdown({ overview, brands, channels }: { overview: OverviewData; brands: BrandRow[]; channels: Marketplace[] }) {
  const byChannel = gmvChannelBreakdown(overview, channels);
  const byBrand = gmvBrandBreakdown(brands);
  return (
    <>
      <p className="text-xs text-slate-500">Por canal</p>
      <ul className="flex flex-col gap-1">
        {byChannel.map((c) => (
          <li key={c.channel} className="flex items-center justify-between text-xs tabular-nums">
            <span className="text-slate-600">{c.label}</span>
            <span className="font-semibold text-slate-800">
              {c.value != null ? `${fmtBrl(c.value)} · ${fmtPct1(c.pct)}` : <span className="text-slate-400 font-normal">Sem dado</span>}
            </span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-slate-500 mt-2">Por marca</p>
      <ul className="flex flex-col gap-1">
        {byBrand.map((b) => (
          <li key={b.brand} className="flex items-center justify-between text-xs tabular-nums">
            <span className="text-slate-600">{b.label}</span>
            <span className="font-semibold text-slate-800">{fmtBrl(b.value)} · {fmtPct1(b.pct)}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

function OrdersBreakdown({ brands }: { brands: BrandRow[] }) {
  const byBrand = ordersBrandBreakdown(brands);
  return (
    <ul className="flex flex-col gap-1">
      {byBrand.map((b) => (
        <li key={b.brand} className="flex items-center justify-between text-xs tabular-nums">
          <span className="text-slate-600">{b.label}</span>
          <span className="font-semibold text-slate-800">{fmtNumber(b.value)} · {fmtPct1(b.pct)}</span>
        </li>
      ))}
    </ul>
  );
}

function AvgTicketBreakdown({ brands }: { brands: BrandRow[] }) {
  const rows = avgTicketBrandBreakdown(brands);
  return (
    <ul className="flex flex-col gap-1">
      {rows.map((r) => (
        <li key={r.brand} className="flex items-center justify-between text-xs tabular-nums">
          <span className="text-slate-600">{r.label}</span>
          <span className="font-semibold text-slate-800">{r.avgTicket != null ? fmtBrl(r.avgTicket) : "—"}</span>
        </li>
      ))}
    </ul>
  );
}

function RoasBreakdown({ overview }: { overview: OverviewData }) {
  const r = roasBreakdown(overview);
  return (
    <ul className="flex flex-col gap-1">
      <li className="flex items-center justify-between text-xs tabular-nums">
        <span className="text-slate-600">Mercado Livre</span>
        <span className="font-semibold text-slate-800">{r.ml != null ? `${r.ml.toFixed(1)}x` : "—"}</span>
      </li>
      <li className="flex items-center justify-between text-xs tabular-nums">
        <span className="text-slate-600">Shopee</span>
        <span className="font-semibold text-slate-800">{r.shopee != null ? `${r.shopee.toFixed(1)}x` : "—"}</span>
      </li>
      <li className="flex items-center justify-between text-xs tabular-nums">
        <span className="text-slate-600">TikTok Shop</span>
        <span className="font-semibold text-slate-400">Não disponível</span>
      </li>
    </ul>
  );
}
