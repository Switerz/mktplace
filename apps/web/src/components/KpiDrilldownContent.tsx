"use client";

import { ArrowDownRight, ArrowUpRight } from "lucide-react";
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
import { CHANNEL_DOT } from "@/lib/gerencial/channel-colors";
import { suavizar, type TendenciaKpi } from "@/lib/gerencial/kpi-trend";
import { fmtBrl, fmtNumber, fmtPctBr } from "@/lib/formatters";
import DrilldownContextLine from "@/components/drilldown/DrilldownContextLine";
import DrilldownSecao from "@/components/drilldown/DrilldownSecao";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import DrilldownCta from "@/components/drilldown/DrilldownCta";
import InfoDica from "@/components/ui/InfoDica";
import NumeroAnimado from "@/components/ui/NumeroAnimado";

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
  /**
   * KPIS-COMPARACAO-1 — a comparacao que o CARD mostrou (variacao % e "antes
   * X"). Vem pronta da faixa para o detalhe nunca contradizer o card; ausente
   * = sem comparacao.
   */
  comparacao?: { deltaPct: number | null; reference: string | null; deltaTone?: string };
  /** POPUPS-1 — a mesma serie do mini grafico do card, aqui em tamanho legivel. */
  tendencia?: TendenciaKpi | null;
}

function fmtPct1(v: number | null): string {
  return v == null ? "—" : `${v.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
}

function variacao(atual: number | null | undefined, anterior: number | null | undefined): number | null {
  return atual != null && anterior != null && anterior > 0 ? (atual / anterior - 1) * 100 : null;
}

/** Referência vs período anterior — SÓ com dado já carregado (Gate G2).
 * GMV lê `prev_gmv`/`gmv_mom_pct`; os demais (KPIS-COMPARACAO-1) recebem a
 * comparação que o card já calculou, para o detalhe nunca contradizer o card.
 * POPUPS-1: sem base, nao ha' linha nenhuma — a ausencia de pilula ja' diz. */
function KpiReference({
  kind,
  overview,
  comparacao,
}: {
  kind: KpiKind;
  overview: OverviewData;
  comparacao?: { deltaPct: number | null; reference: string | null; deltaTone?: string };
}) {
  const gmv = kind === "gmv" && overview.gmv_mom_pct != null;
  const outra = kind !== "gmv" && comparacao?.deltaPct != null && comparacao.reference ? comparacao : null;
  if (!gmv && !outra) return null;
  const pct = gmv ? overview.gmv_mom_pct! : outra!.deltaPct!;
  const antes = gmv ? fmtBrl(overview.prev_gmv) : outra!.reference!;
  const neutro = kind === "ad_spend" || outra?.deltaTone === "neutral";
  const sobe = pct >= 0;
  const Seta = sobe ? ArrowUpRight : ArrowDownRight;
  const pele = neutro ? "bg-raised text-ink-muted" : sobe ? "bg-ok-soft text-ok-ink" : "bg-crit-soft text-crit-ink";
  return (
    <span className="flex flex-wrap items-center gap-2 pb-0.5">
      <span className={`inline-flex items-center gap-0.5 rounded-md px-2 py-0.5 text-sm font-semibold tabular-nums ${pele}`}>
        <Seta aria-hidden="true" className="h-4 w-4" strokeWidth={2.25} />
        {fmtPctBr(pct)}
      </span>
      <span className="text-xs text-ink-faint tabular-nums">vs {antes} no período anterior</span>
    </span>
  );
}

/**
 * Conteudo interno do KpiDrilldownDialog, um por KPI (Gate U2, Task 4;
 * alinhado ao contrato transversal no Gate G2 — ver
 * docs/DRILLDOWN_ARCHITECTURE.md §3) — decomposicao usando somente dados ja
 * carregados (OverviewData/BrandRow), sem fetch/endpoint novo.
 *
 * POPUPS-1 — numero, variacao, tendencia e decomposicao com barras. Definicao,
 * formula e ressalva do indicador ficam no "ⓘ" ao lado do periodo.
 */
export default function KpiDrilldownContent({
  kind,
  value,
  periodLabel,
  refreshedAt,
  overview,
  brands,
  channels,
  buildHref,
  comparacao,
  tendencia,
}: Props) {
  const meta = KPI_META[kind];
  const explicacao = [meta.definition, meta.formula, meta.caveat].filter(Boolean).join(" ");

  return (
    <div className="flex flex-col gap-4 text-sm">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-end gap-x-3 gap-y-1.5">
          <NumeroAnimado texto={value} className="text-[32px] font-bold leading-none tracking-tight text-ink tabular-nums" />
          <KpiReference kind={kind} overview={overview} comparacao={comparacao} />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <DrilldownContextLine periodLabel={periodLabel} refreshedAt={refreshedAt} />
          <InfoDica texto={explicacao} />
        </div>
      </div>

      {tendencia && tendencia.pontos.length >= 4 && <Tendencia t={tendencia} />}

      {kind === "gmv" && <GmvBreakdown overview={overview} brands={brands} channels={channels} />}
      {kind === "orders" && <OrdersBreakdown brands={brands} />}
      {kind === "avg_ticket" && <AvgTicketBreakdown brands={brands} />}
      {kind === "ad_spend" && <AdSpendBreakdown overview={overview} channels={channels} />}
      {kind === "roas" && <RoasBreakdown overview={overview} brands={brands} channels={channels} />}

      <DrilldownCta href={buildHref(meta.nextHref)}>{meta.nextLabel} →</DrilldownCta>
    </div>
  );
}

/** Serie do periodo em area, suavizada como no card, com a variacao recente. */
function Tendencia({ t }: { t: TendenciaKpi }) {
  const pontos = suavizar(t.pontos);
  const min = Math.min(...pontos);
  const max = Math.max(...pontos);
  const faixa = max - min || 1;
  const passo = 300 / (pontos.length - 1);
  const y = (v: number) => 58 - ((v - min) / faixa) * 50;
  const linha = pontos.map((v, i) => `${(i * passo).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <DrilldownSecao
      titulo="Tendência"
      acao={
        t.variacaoPct != null && (
          <span className="tabular-nums">
            <b className={`font-semibold ${t.variacaoPct >= 0 ? "text-ok-ink" : "text-crit-ink"}`}>{fmtPctBr(t.variacaoPct)}</b> · {t.rotulo}
          </span>
        )
      }
    >
      <svg viewBox="0 0 300 64" preserveAspectRatio="none" aria-hidden="true" className="tc-revela h-16 w-full text-accent">
        <polygon points={`0,64 ${linha} 300,64`} fill="currentColor" opacity={0.12} />
        <polyline points={linha} fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      </svg>
    </DrilldownSecao>
  );
}

function GmvBreakdown({ overview, brands, channels }: { overview: OverviewData; brands: BrandRow[]; channels: Marketplace[] }) {
  const byChannel = gmvChannelBreakdown(overview, channels);
  const byBrand = [...gmvBrandBreakdown(brands)].sort((a, b) => b.value - a.value);
  const mom = new Map(brands.map((b) => [b.brand, b.mom_pct]));
  return (
    <>
      <DrilldownSecao titulo="Por canal">
        <ul className="flex flex-col">
          {byChannel.map((c) => (
            <EvidenceRow
              key={c.channel}
              label={c.label}
              ponto={CHANNEL_DOT[c.channel]}
              barra={CHANNEL_DOT[c.channel]}
              pct={c.pct}
              value={c.value != null ? `${fmtBrl(c.value)} · ${fmtPct1(c.pct)}` : "Sem dado"}
              tone={c.value != null ? "value" : "muted"}
            />
          ))}
        </ul>
      </DrilldownSecao>
      <DrilldownSecao titulo="Por marca" acao={<span>{byBrand.length}</span>}>
        <ul className="flex flex-col">
          {byBrand.map((b) => {
            const d = mom.get(b.brand);
            return (
              <EvidenceRow
                key={b.brand}
                label={b.label}
                pct={b.pct}
                delta={d != null ? { pct: d } : null}
                value={`${fmtBrl(b.value)} · ${fmtPct1(b.pct)}`}
              />
            );
          })}
        </ul>
      </DrilldownSecao>
    </>
  );
}

function OrdersBreakdown({ brands }: { brands: BrandRow[] }) {
  const byBrand = [...ordersBrandBreakdown(brands)].sort((a, b) => b.value - a.value);
  return (
    <DrilldownSecao titulo="Por marca" acao={<span>{byBrand.length}</span>}>
      <ul className="flex flex-col">
        {byBrand.map((b) => (
          <EvidenceRow key={b.brand} label={b.label} pct={b.pct} value={`${fmtNumber(b.value)} · ${fmtPct1(b.pct)}`} />
        ))}
      </ul>
    </DrilldownSecao>
  );
}

function AvgTicketBreakdown({ brands }: { brands: BrandRow[] }) {
  const rows = [...avgTicketBrandBreakdown(brands)].sort((a, b) => (b.avgTicket ?? -1) - (a.avgTicket ?? -1));
  const maior = Math.max(0, ...rows.map((r) => r.avgTicket ?? 0));
  return (
    <DrilldownSecao titulo="Por marca" acao={<span>maior → menor</span>}>
      <ul className="flex flex-col">
        {rows.map((r) => (
          <EvidenceRow
            key={r.brand}
            label={r.label}
            pct={r.avgTicket != null && maior > 0 ? (r.avgTicket / maior) * 100 : null}
            value={r.avgTicket != null ? fmtBrl(r.avgTicket) : "—"}
            tone={r.avgTicket != null ? "value" : "muted"}
          />
        ))}
      </ul>
    </DrilldownSecao>
  );
}

/**
 * Investimento em Ads (Gate V2-1): declara a COBERTURA em vez de fingir que a
 * soma cobre os tres canais. O TikTok aparece como indisponivel nesta fonte,
 * nunca como R$ 0.
 */
function AdSpendBreakdown({ overview, channels }: { overview: OverviewData; channels: Marketplace[] }) {
  const mlSelected = isMarketplaceSelected(channels, "ml");
  const shopeeSelected = isMarketplaceSelected(channels, "shopee");
  const tiktokSelected = isMarketplaceSelected(channels, "tiktok");
  return (
    <DrilldownSecao titulo="Por canal">
      <ul className="flex flex-col">
        {(mlSelected || shopeeSelected) && (
          <EvidenceRow
            label="Mercado Livre + Shopee"
            value={overview.ad_spend != null ? fmtBrl(overview.ad_spend) : "Sem dado"}
            tone={overview.ad_spend != null ? "value" : "muted"}
          />
        )}
        {tiktokSelected && <EvidenceRow label="TikTok Shop" ponto={CHANNEL_DOT.tiktok} value="Não reporta" tone="muted" />}
      </ul>
    </DrilldownSecao>
  );
}

/**
 * ROAS por canal (Gate V2-1): uma linha por canal, sem nenhum consolidado.
 * Canal nao selecionado e' omitido; canal selecionado sem valor fica explicito.
 * POPUPS-1: variacao de cada canal contra o periodo anterior, e o ROAS do
 * Mercado Livre por marca — a pergunta seguinte de quem abre o detalhe.
 */
function RoasBreakdown({ overview, brands, channels }: { overview: OverviewData; brands: BrandRow[]; channels: Marketplace[] }) {
  const r = roasBreakdown(overview);
  const mlSel = isMarketplaceSelected(channels, "ml");
  const porMarca = mlSel
    ? brands.filter((b) => b.ml_roas != null).sort((a, b) => (b.ml_roas ?? 0) - (a.ml_roas ?? 0))
    : [];
  const maior = Math.max(0, ...porMarca.map((b) => b.ml_roas ?? 0));
  const x = (v: number) => `${v.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}x`;
  const dMl = variacao(r.ml, overview.prev_ml_roas);
  const dShopee = variacao(r.shopee, overview.prev_shopee_roas);
  return (
    <>
      <DrilldownSecao titulo="Por canal">
        <ul className="flex flex-col">
          {mlSel && (
            <EvidenceRow
              label="Mercado Livre"
              ponto={CHANNEL_DOT.ml}
              delta={dMl != null ? { pct: dMl } : null}
              value={r.ml != null ? x(r.ml) : "Sem dado"}
              tone={r.ml != null ? "value" : "muted"}
            />
          )}
          {isMarketplaceSelected(channels, "shopee") && (
            <EvidenceRow
              label="Shopee"
              ponto={CHANNEL_DOT.shopee}
              delta={dShopee != null ? { pct: dShopee } : null}
              value={r.shopee != null ? x(r.shopee) : "Sem dado"}
              tone={r.shopee != null ? "value" : "muted"}
            />
          )}
          {isMarketplaceSelected(channels, "tiktok") && (
            <EvidenceRow label="TikTok Shop" ponto={CHANNEL_DOT.tiktok} value="Não reporta" tone="muted" />
          )}
        </ul>
      </DrilldownSecao>
      {porMarca.length > 1 && (
        <DrilldownSecao titulo="Mercado Livre por marca">
          <ul className="flex flex-col">
            {porMarca.map((b) => (
              <EvidenceRow
                key={b.brand}
                label={b.label}
                pct={maior > 0 ? ((b.ml_roas ?? 0) / maior) * 100 : null}
                barra={CHANNEL_DOT.ml}
                value={x(b.ml_roas!)}
              />
            ))}
          </ul>
        </DrilldownSecao>
      )}
    </>
  );
}
