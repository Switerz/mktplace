"use client";

/**
 * Marcas contra a meta (GERENCIAL-METAS-1).
 *
 * Uma barra por marca, empilhada pelos canais selecionados, com um TRACO de
 * referencia: a meta da marca quando existe, senao o periodo anterior. A
 * direita, o atingimento (com a cor do status projetado) ou a variacao.
 *
 * A legenda diz qual referencia cada traco usa — misturar meta e periodo
 * anterior sem dizer seria ler um como o outro.
 */
import Link from "next/link";

import type { BrandRow } from "@/lib/api-client";
import type { Marketplace } from "@/lib/mock-data";
import { CHANNEL_DOT as PONTO } from "@/lib/gerencial/channel-colors";
import { CHANNEL_LABEL } from "@/lib/gerencial/kpi-band";
import type { ProgressoMeta, StatusMeta } from "@/lib/gerencial/meta-progress";
import { MARCAS, fmtCompacto } from "@/lib/metas";
import { fmtPctBr } from "@/lib/formatters";
import InfoDica from "@/components/ui/InfoDica";

const CHIP: Record<StatusMeta, string> = {
  atingida: "bg-ok-soft text-ok-ink",
  no_caminho: "bg-ok-soft text-ok-ink",
  atencao: "bg-warn-soft text-warn-ink",
  abaixo: "bg-crit-soft text-crit-ink",
  nao_atingida: "bg-crit-soft text-crit-ink",
};

const GMV_DO_CANAL: Record<Marketplace, keyof BrandRow> = {
  tiktok: "tiktok_gmv",
  ml: "ml_gmv",
  shopee: "shopee_gmv",
};

interface Props {
  brands: BrandRow[];
  channels: Marketplace[];
  /** Progresso por marca; ausente = marca sem meta no recorte. */
  progressoPorMarca: Record<string, ProgressoMeta | null>;
  compareActive: boolean;
  loading: boolean;
  error: boolean;
  brandHref: (brand: string) => string;
}

export default function MarcasMeta({ brands, channels, progressoPorMarca, compareActive, loading, error, brandHref }: Props) {
  // Marca com meta e sem linha em `/brands` (nada vendido no recorte) entra
  // com tudo zerado: mostrar 0% da meta e' o ponto, nao esconder a marca.
  const semVenda: BrandRow[] = Object.keys(progressoPorMarca)
    .filter((id) => progressoPorMarca[id] && !brands.some((b) => b.brand === id))
    .map((id) => linhaZerada(id));
  const linhas = [...brands, ...semVenda]
    .filter((b) => b.total_gmv > 0 || progressoPorMarca[b.brand])
    .sort((a, b) => b.total_gmv - a.total_gmv);
  const algumaMeta = linhas.some((b) => progressoPorMarca[b.brand]);
  const referencia = (b: BrandRow) =>
    progressoPorMarca[b.brand]?.meta ?? (compareActive && b.total_gmv_prev > 0 ? b.total_gmv_prev : null);
  const escala = Math.max(1, ...linhas.map((b) => Math.max(b.total_gmv, referencia(b) ?? 0)));

  return (
    <section aria-labelledby="marcas-meta-titulo" className="flex h-full flex-col rounded-xl border border-line bg-surface p-5">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5">
            <h2 id="marcas-meta-titulo" className="text-sm font-semibold text-ink">
              Marcas
            </h2>
            <InfoDica
              texto={`GMV no período. Traço = ${algumaMeta ? "meta da marca (ou período anterior, sem meta)" : "período anterior"}.`}
            />
          </div>
        </div>
        <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-muted">
          {channels.map((c) => (
            <li key={c} className="inline-flex items-center gap-1.5">
              <span aria-hidden="true" className={`h-2 w-2 rounded-full ${PONTO[c]}`} />
              {CHANNEL_LABEL[c]}
            </li>
          ))}
        </ul>
      </div>

      {error ? (
        <p className="text-sm text-crit-ink">Desempenho por marca indisponível.</p>
      ) : loading && linhas.length === 0 ? (
        <div className="flex flex-col gap-3" aria-hidden="true">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="h-8 animate-pulse rounded bg-raised motion-reduce:animate-none" />
          ))}
        </div>
      ) : linhas.length === 0 ? (
        <p className="text-sm text-ink-faint">Sem venda por marca no período e filtros selecionados.</p>
      ) : (
        <ul className="flex flex-col">
          {linhas.map((b) => {
            const p = progressoPorMarca[b.brand] ?? null;
            const ref = referencia(b);
            return (
              <li key={b.brand} className="border-t border-line first:border-t-0">
                <Link
                  href={brandHref(b.brand)}
                  className="group grid min-h-11 grid-cols-[88px_1fr_auto] items-center gap-3 rounded-lg px-1.5 py-2.5 transition-colors duration-200 hover:bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  <span className="truncate text-sm font-semibold text-ink transition-transform duration-200 ease-out group-hover:translate-x-0.5 motion-reduce:transition-none motion-reduce:group-hover:translate-x-0">
                    {b.label}
                  </span>
                  <span className="relative h-2.5 rounded-full bg-raised" aria-hidden="true">
                    <span
                      className="tc-barra absolute inset-y-0 left-0 flex overflow-hidden rounded-full"
                      style={{ width: `${(b.total_gmv / escala) * 100}%` }}
                    >
                      {channels.map((c) => {
                        const v = (b[GMV_DO_CANAL[c]] as number | null) ?? 0;
                        return v > 0 ? <span key={c} className={PONTO[c]} style={{ flex: v }} /> : null;
                      })}
                    </span>
                    {ref != null && (
                      <span className="absolute -inset-y-1 w-0.5 bg-ink" style={{ left: `${(ref / escala) * 100}%` }} />
                    )}
                  </span>
                  <span className="flex flex-col items-end text-right tabular-nums">
                    <span className="text-sm font-semibold text-ink">{fmtCompacto(b.total_gmv)}</span>
                    {p ? (
                      <span className={`rounded px-1.5 text-xs font-semibold ${CHIP[p.status]}`}>
                        {Math.round(p.atingimento * 100)}% da meta
                      </span>
                    ) : compareActive && b.mom_pct != null ? (
                      <span className={`text-xs font-semibold ${b.mom_pct >= 0 ? "text-ok-ink" : "text-crit-ink"}`}>
                        {fmtPctBr(b.mom_pct)}
                      </span>
                    ) : null}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function linhaZerada(brand: string): BrandRow {
  const label = MARCAS.find((m) => m.id === brand)?.label ?? brand.toUpperCase();
  return {
    brand, label, tiktok_gmv: null, ml_gmv: null, shopee_gmv: null, total_gmv: 0, orders: 0,
    avg_ticket: null, tiktok_avg_ticket: null, ml_avg_ticket: null,
    tiktok_gmv_prev: null, ml_gmv_prev: null, shopee_gmv_prev: null, total_gmv_prev: 0,
    mom_pct: null, cos_pct: null, gpm: null, ml_roas: null, ml_cancel_rate_pct: null,
  };
}
