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
 *
 * Marca com meta (METAS-DIARIAS-1): a barra so' soma os canais que tem meta,
 * para barra e traco medirem a mesma coisa (TikTok sem meta fica fora).
 *
 * Com a proporcao Full por marca (`fullPorMarca`), cada marca ganha duas
 * barras, Full e sem Full (mais "sem dado" quando um canal nao tem a medida),
 * e o traco sai: a meta continua no chip. A legenda filtra o canal.
 */
import Link from "next/link";

import type { BrandRow } from "@/lib/api-client";
import type { Marketplace } from "@/lib/mock-data";
import { CHANNEL_DOT as PONTO } from "@/lib/gerencial/channel-colors";
import { CHANNEL_LABEL } from "@/lib/gerencial/kpi-band";
import { GMV_DO_CANAL, type StatusMeta } from "@/lib/gerencial/meta-progress";
import type { StatusDia } from "@/lib/metas-curva";
import { MARCAS, fmtCompacto } from "@/lib/metas";
import { fmtPctBr } from "@/lib/formatters";
import { fullDaMarca, type FullDaMarca } from "@/lib/gerencial/divisao-full";
import { ALL_MARKETPLACES } from "@/lib/marketplace-filter";
import InfoDica from "@/components/ui/InfoDica";

const CHIP: Record<StatusMeta | StatusDia, string> = {
  atingida: "bg-ok-soft text-ok-ink",
  no_caminho: "bg-ok-soft text-ok-ink",
  atencao: "bg-warn-soft text-warn-ink",
  abaixo: "bg-crit-soft text-crit-ink",
  nao_atingida: "bg-crit-soft text-crit-ink",
  // Dia em andamento: o atingimento parcial ainda nao e' bom nem ruim.
  em_andamento: "bg-raised text-ink-muted",
};

/** Meta de uma marca no recorte: do mes (com status projetado) ou do dia. */
export interface MetaDaMarca {
  meta: number;
  realizado: number;
  atingimento: number;
  status: StatusMeta | StatusDia;
  /** Canais que entram no realizado (os que tem meta). */
  canais: Marketplace[];
}

interface Props {
  brands: BrandRow[];
  channels: Marketplace[];
  /** Meta por marca; ausente = marca sem meta no recorte. */
  progressoPorMarca: Record<string, MetaDaMarca | null>;
  /** "da meta" ou "da meta do dia", depois do percentual. */
  rotuloMeta?: string;
  compareActive: boolean;
  loading: boolean;
  error: boolean;
  brandHref: (brand: string) => string;
  /** Com "Hoje": onde cada marca tende a fechar o dia, e o dia inteiro de ontem. */
  tendenciaDoDia?: Record<string, { projecao: number; ontemDia: number }> | null;
  /** Proporcao Full por marca e canal; ausente = uma barra so', sem divisao. */
  fullPorMarca?: Record<string, { ml?: number | null; shopee?: number | null }> | null;
  /** Clique num canal da legenda (filtra a pagina por ele). */
  onCanal?: (c: Marketplace) => void;
}

export default function MarcasMeta({
  brands,
  channels,
  progressoPorMarca,
  rotuloMeta = "da meta",
  compareActive,
  loading,
  error,
  brandHref,
  tendenciaDoDia = null,
  fullPorMarca = null,
  onCanal,
}: Props) {
  // Marca com meta e sem linha em `/brands` (nada vendido no recorte) entra
  // com tudo zerado: mostrar 0% da meta e' o ponto, nao esconder a marca.
  const semVenda: BrandRow[] = Object.keys(progressoPorMarca)
    .filter((id) => progressoPorMarca[id] && !brands.some((b) => b.brand === id))
    .map((id) => linhaZerada(id));
  const canaisDa = (b: BrandRow) => progressoPorMarca[b.brand]?.canais ?? channels;
  const valor = (b: BrandRow) => progressoPorMarca[b.brand]?.realizado ?? b.total_gmv;
  const linhas = [...brands, ...semVenda]
    .filter((b) => b.total_gmv > 0 || progressoPorMarca[b.brand])
    .sort((a, b) => valor(b) - valor(a));
  const algumaMeta = linhas.some((b) => progressoPorMarca[b.brand]);
  const referencia = (b: BrandRow) =>
    progressoPorMarca[b.brand]?.meta ?? (compareActive && b.total_gmv_prev > 0 ? b.total_gmv_prev : null);
  const escala = Math.max(1, ...linhas.map((b) => Math.max(valor(b), referencia(b) ?? 0)));
  const canaisDaBarra = (b: BrandRow) => channels.filter((c) => canaisDa(b).includes(c));
  const divisao = (b: BrandRow): FullDaMarca | null =>
    fullPorMarca
      ? fullDaMarca(
          Object.fromEntries(canaisDaBarra(b).map((c) => [c, b[GMV_DO_CANAL[c]] ?? 0])),
          fullPorMarca[b.brand] ?? {},
        )
      : null;
  const soma = (p: Partial<Record<Marketplace, number>>) => Object.values(p).reduce((a, v) => a + (v ?? 0), 0);
  const escalaFull = Math.max(
    1,
    ...linhas.map((b) => {
      const d = divisao(b);
      return d ? Math.max(soma(d.full), soma(d.semFull), soma(d.semDado)) : 0;
    }),
  );

  return (
    <section aria-labelledby="marcas-meta-titulo" className="flex h-full flex-col rounded-xl border border-line bg-surface p-5">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5">
            <h2 id="marcas-meta-titulo" className="text-sm font-semibold text-ink">
              Marcas
            </h2>
            <InfoDica
              texto={
                algumaMeta
                  ? fullPorMarca
                    ? "GMV no período; com meta, só os canais que têm meta. Full = ML Full + Shopee FBS, pela proporção medida até ontem; TikTok entra em sem Full. Clique num canal para ver só ele."
                    : "GMV no período; com meta, só os canais que têm meta. Traço = meta da marca (ou período anterior, sem meta). Clique num canal para ver só ele."
                  : fullPorMarca
                    ? "GMV no período. Full = ML Full + Shopee FBS, pela proporção medida até ontem; TikTok entra em sem Full. Clique num canal para ver só ele."
                    : "GMV no período. Traço = período anterior. Clique num canal para ver só ele."
              }
            />
          </div>
        </div>
        <ul className="flex flex-wrap gap-1 text-xs" aria-label="Filtrar por canal">
          {ALL_MARKETPLACES.map((c) => {
            const ativo = channels.includes(c);
            const so = ativo && channels.length === 1;
            return (
              <li key={c}>
                <button
                  type="button"
                  onClick={() => onCanal?.(c)}
                  disabled={!onCanal}
                  aria-pressed={so}
                  title={so ? "Voltar a todos os canais" : `Ver só ${CHANNEL_LABEL[c]}`}
                  className={`inline-flex min-h-8 items-center gap-1.5 rounded-full px-2.5 transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                    so
                      ? "bg-surface font-semibold text-ink ring-1 ring-line-strong"
                      : ativo
                        ? "text-ink-muted hover:bg-raised"
                        : "text-ink-faint hover:bg-raised"
                  }`}
                >
                  <span aria-hidden="true" className={`h-2 w-2 rounded-full ${PONTO[c]} ${ativo ? "" : "opacity-40"}`} />
                  {CHANNEL_LABEL[c]}
                </button>
              </li>
            );
          })}
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
            const d = divisao(b);
            const comFull = d ? soma(d.full) + soma(d.semFull) : 0;
            const pctFull = d && comFull > 0 ? soma(d.full) / comFull : null;
            return (
              <li key={b.brand} className="border-t border-line first:border-t-0">
                <Link
                  href={brandHref(b.brand)}
                  className={`group grid min-h-11 ${d ? "grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1.5" : "grid-cols-[88px_1fr_auto] gap-3"} items-center rounded-lg px-1.5 py-2.5 transition-colors duration-200 hover:bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent`}
                >
                  <span className="truncate text-sm font-semibold text-ink transition-transform duration-200 ease-out group-hover:translate-x-0.5 motion-reduce:transition-none motion-reduce:group-hover:translate-x-0">
                    {b.label}
                  </span>
                  {d ? (
                    <span className="col-start-1 row-start-2 flex flex-col gap-1.5">
                      <BarraFull rotulo="Full" partes={d.full} canais={canaisDaBarra(b)} escala={escalaFull} />
                      <BarraFull rotulo="Sem Full" partes={d.semFull} canais={canaisDaBarra(b)} escala={escalaFull} />
                      {soma(d.semDado) > 0 && (
                        <BarraFull rotulo="Sem dado" partes={d.semDado} canais={canaisDaBarra(b)} escala={escalaFull} />
                      )}
                    </span>
                  ) : (
                    <span className="relative h-2.5 rounded-full bg-raised" aria-hidden="true">
                      <span
                        className="tc-barra absolute inset-y-0 left-0 flex overflow-hidden rounded-full"
                        style={{ width: `${(valor(b) / escala) * 100}%` }}
                      >
                        {canaisDaBarra(b).map((c) => {
                          const v = b[GMV_DO_CANAL[c]] ?? 0;
                          return v > 0 ? <span key={c} className={PONTO[c]} style={{ flex: v }} /> : null;
                        })}
                      </span>
                      {ref != null && (
                        <span className="absolute -inset-y-1 w-0.5 bg-ink" style={{ left: `${(ref / escala) * 100}%` }} />
                      )}
                    </span>
                  )}
                  <span className={`flex flex-col items-end text-right tabular-nums ${d ? "col-start-2 row-span-2 row-start-1" : ""}`}>
                    <span className="text-sm font-semibold text-ink">{fmtCompacto(valor(b))}</span>
                    {pctFull != null && <span className="text-xs text-ink-muted">{Math.round(pctFull * 100)}% Full</span>}
                    {p ? (
                      <span className={`rounded px-1.5 text-xs font-semibold ${CHIP[p.status]}`}>
                        {Math.round(p.atingimento * 100)}% {rotuloMeta}
                      </span>
                    ) : compareActive && b.mom_pct != null ? (
                      <span className={`text-xs font-semibold ${b.mom_pct >= 0 ? "text-ok-ink" : "text-crit-ink"}`}>
                        {fmtPctBr(b.mom_pct)}
                      </span>
                    ) : null}
                    <TendenciaDoDia t={tendenciaDoDia?.[b.brand] ?? null} />
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

/** Uma das barras da marca (Full, sem Full ou sem dado), empilhada por canal. */
function BarraFull({
  rotulo,
  partes,
  canais,
  escala,
}: {
  rotulo: string;
  partes: Partial<Record<Marketplace, number>>;
  canais: Marketplace[];
  escala: number;
}) {
  const total = Object.values(partes).reduce((a, v) => a + (v ?? 0), 0);
  return (
    <span className="grid grid-cols-[56px_minmax(48px,1fr)_auto] items-center gap-2 text-xs text-ink-muted">
      <span className="whitespace-nowrap">{rotulo}</span>
      <span className="relative h-2 rounded-full bg-raised" aria-hidden="true">
        <span
          className="tc-barra absolute inset-y-0 left-0 flex overflow-hidden rounded-full"
          style={{ width: `${(total / escala) * 100}%` }}
        >
          {canais.map((c) => {
            const v = partes[c] ?? 0;
            return v > 0 ? <span key={c} className={PONTO[c]} style={{ flex: v }} /> : null;
          })}
        </span>
      </span>
      <span className="text-right tabular-nums">{fmtCompacto(total)}</span>
    </span>
  );
}

/** "tende a R$ 1,2 mi · ▲ 8% vs ontem": a projecao contra o dia inteiro de ontem. */
function TendenciaDoDia({ t }: { t: { projecao: number; ontemDia: number } | null }) {
  if (!t) return null;
  const pct = t.ontemDia > 0 ? ((t.projecao - t.ontemDia) / t.ontemDia) * 100 : null;
  return (
    <span
      className="text-xs text-ink-muted"
      title={`Pelo ritmo de ontem até a mesma hora. Ontem fechou em ${fmtCompacto(t.ontemDia)}.`}
    >
      tende a <b className="font-semibold text-ink">{fmtCompacto(t.projecao)}</b>
      {pct != null && (
        <span className={`ml-1 font-semibold ${pct >= 0 ? "text-ok-ink" : "text-crit-ink"}`}>
          {pct >= 0 ? "▲" : "▼"} {fmtPctBr(Math.abs(pct)).replace("+", "")} vs ontem
        </span>
      )}
    </span>
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
