"use client";

import type { BrandRow, OverviewData } from "@/lib/api-client";
import type { Marketplace } from "@/lib/mock-data";
import { isMarketplaceSelected } from "@/lib/marketplace-filter";
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
import DrilldownContextLine from "@/components/drilldown/DrilldownContextLine";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import DrilldownCta from "@/components/drilldown/DrilldownCta";

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

/** Referência vs período anterior — SÓ com dado já carregado (Gate G2).
 * O contrato de OverviewData só traz comparação para GMV (prev_gmv +
 * gmv_mom_pct); os demais KPIs informam indisponibilidade explicitamente,
 * nunca uma aproximação calculada aqui. `gmv_mom_pct == null` (ex: período
 * anterior sem dado) também vira indisponível — nunca um delta fabricado. */
function KpiReference({ kind, overview }: { kind: KpiKind; overview: OverviewData }) {
  const hasGmvComparison = kind === "gmv" && overview.gmv_mom_pct != null;
  return (
    <div>
      <p className="text-xs text-slate-400">Referência (período anterior)</p>
      {hasGmvComparison ? (
        <p className="text-sm font-semibold text-slate-700 tabular-nums">
          {fmtBrl(overview.prev_gmv)} · {overview.gmv_mom_pct! >= 0 ? "+" : ""}{overview.gmv_mom_pct!.toFixed(1)}%
        </p>
      ) : (
        <p className="text-sm text-slate-400">Comparação indisponível</p>
      )}
    </div>
  );
}

/**
 * Conteudo interno do KpiDrilldownDialog, um por KPI (Gate U2, Task 4;
 * alinhado ao contrato transversal no Gate G2 — ver
 * docs/DRILLDOWN_ARCHITECTURE.md §3) — decomposicao usando somente dados ja
 * carregados (OverviewData/BrandRow), sem fetch/endpoint novo. Nao existe um
 * framework generico de KPI aqui, so um `switch` direto sobre os 4 casos.
 */
export default function KpiDrilldownContent({ kind, value, periodLabel, refreshedAt, overview, brands, channels, buildHref }: Props) {
  const meta = KPI_META[kind];

  return (
    <div className="flex flex-col gap-4 text-sm">
      <div>
        <p className="text-3xl font-bold text-gray-900 tabular-nums leading-none">{value}</p>
        <div className="mt-1">
          <DrilldownContextLine periodLabel={periodLabel} refreshedAt={refreshedAt} />
        </div>
      </div>

      <KpiReference kind={kind} overview={overview} />

      <div className="flex flex-col gap-1">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Como é calculado</p>
        <p className="text-slate-700">{meta.definition}</p>
        <p className="text-xs text-slate-500 font-mono mt-1">{meta.formula}</p>
        {meta.caveat && <div className="mt-1"><DataQualityNote note={meta.caveat} /></div>}
      </div>

      <div className="flex flex-col gap-2">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Decomposição</p>
        {kind === "gmv" && <GmvBreakdown overview={overview} brands={brands} channels={channels} />}
        {kind === "orders" && <OrdersBreakdown brands={brands} />}
        {kind === "avg_ticket" && <AvgTicketBreakdown brands={brands} />}
        {kind === "ad_spend" && <AdSpendBreakdown overview={overview} channels={channels} />}
        {kind === "roas" && <RoasBreakdown overview={overview} channels={channels} />}
      </div>

      <DrilldownCta href={buildHref(meta.nextHref)}>{meta.nextLabel} →</DrilldownCta>
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
          <EvidenceRow
            key={c.channel}
            label={c.label}
            value={c.value != null ? `${fmtBrl(c.value)} · ${fmtPct1(c.pct)}` : "Sem dado"}
            tone={c.value != null ? "value" : "muted"}
          />
        ))}
      </ul>
      <p className="text-xs text-slate-500 mt-2">Por marca</p>
      <ul className="flex flex-col gap-1">
        {byBrand.map((b) => (
          <EvidenceRow key={b.brand} label={b.label} value={`${fmtBrl(b.value)} · ${fmtPct1(b.pct)}`} />
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
        <EvidenceRow key={b.brand} label={b.label} value={`${fmtNumber(b.value)} · ${fmtPct1(b.pct)}`} />
      ))}
    </ul>
  );
}

function AvgTicketBreakdown({ brands }: { brands: BrandRow[] }) {
  const rows = avgTicketBrandBreakdown(brands);
  return (
    <ul className="flex flex-col gap-1">
      {rows.map((r) => (
        <EvidenceRow
          key={r.brand}
          label={r.label}
          value={r.avgTicket != null ? fmtBrl(r.avgTicket) : "—"}
          tone={r.avgTicket != null ? "value" : "muted"}
        />
      ))}
    </ul>
  );
}

/**
 * Investimento em Ads (Gate V2-1): declara a COBERTURA em vez de fingir que a
 * soma cobre os tres canais. O TikTok aparece como indisponivel nesta fonte,
 * nunca como R$ 0, e nenhum delta e' exibido — o contrato nao traz o
 * investimento do periodo anterior.
 */
function AdSpendBreakdown({ overview, channels }: { overview: OverviewData; channels: Marketplace[] }) {
  const mlSelected = isMarketplaceSelected(channels, "ml");
  const shopeeSelected = isMarketplaceSelected(channels, "shopee");
  const tiktokSelected = isMarketplaceSelected(channels, "tiktok");
  return (
    <>
      <ul className="flex flex-col gap-1">
        <EvidenceRow
          label="Total dos canais com mídia"
          value={overview.ad_spend != null ? fmtBrl(overview.ad_spend) : "Sem dado"}
          tone={overview.ad_spend != null ? "value" : "muted"}
          reference={mlSelected || shopeeSelected ? "Cobertura: Mercado Livre e Shopee" : null}
        />
        {tiktokSelected && (
          <EvidenceRow label="TikTok Shop" value="Não disponível nesta fonte" tone="muted" />
        )}
      </ul>
      <div className="mt-1">
        <DataQualityNote note="O contrato não traz o investimento do período anterior, então este indicador não exibe variação." />
      </div>
    </>
  );
}

/**
 * ROAS por canal (Gate V2-1): uma linha por canal, sem nenhum consolidado.
 * Canal nao selecionado e' omitido; canal selecionado sem valor fica explicito.
 */
function RoasBreakdown({ overview, channels }: { overview: OverviewData; channels: Marketplace[] }) {
  const r = roasBreakdown(overview);
  return (
    <>
      <ul className="flex flex-col gap-1">
        {isMarketplaceSelected(channels, "ml") && (
          <EvidenceRow
            label="Mercado Livre"
            value={r.ml != null ? `${r.ml.toFixed(1)}x` : "Sem dado"}
            tone={r.ml != null ? "value" : "muted"}
          />
        )}
        {isMarketplaceSelected(channels, "shopee") && (
          <EvidenceRow
            label="Shopee"
            value={r.shopee != null ? `${r.shopee.toFixed(1)}x` : "Sem dado"}
            tone={r.shopee != null ? "value" : "muted"}
          />
        )}
        {isMarketplaceSelected(channels, "tiktok") && (
          <EvidenceRow label="TikTok Shop" value="Não disponível nesta fonte" tone="muted" />
        )}
      </ul>
      <div className="mt-1">
        <DataQualityNote note="Não existe ROAS consolidado: somar ou tirar média entre canais com investimentos e regras diferentes produziria um número sem significado." />
      </div>
    </>
  );
}
