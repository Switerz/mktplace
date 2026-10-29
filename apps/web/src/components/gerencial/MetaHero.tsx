"use client";

/**
 * Bloco principal da Gerencial (GERENCIAL-METAS-1): a resposta em 5 segundos.
 *
 * Com meta definida para o mes do periodo, responde "vamos bater a meta?":
 * atingimento, projecao pelo ritmo, quanto falta por dia e um selo de status.
 * Sem meta (ou periodo que nao e' um mes), cai no GMV contra o periodo
 * anterior e aponta onde definir a meta — nunca inventa uma.
 *
 * O aviso de dado que pode estar distorcendo o total aparece aqui, colado no
 * numero, e nao numa faixa separada no topo: e' aqui que ele muda a leitura.
 */
import Link from "next/link";
import { ArrowDownRight, ArrowUpRight, Target, TriangleAlert } from "lucide-react";

import type { Marketplace } from "@/lib/mock-data";
import { CHANNEL_DOT as PONTO } from "@/lib/gerencial/channel-colors";
import { CHANNEL_LABEL } from "@/lib/gerencial/kpi-band";
import { ROTULO_STATUS, type ProgressoMeta, type StatusMeta } from "@/lib/gerencial/meta-progress";
import { fmtCompacto, rotuloMes } from "@/lib/metas";
import { fmtPct } from "@/lib/formatters";

const SELO: Record<StatusMeta, string> = {
  atingida: "border-ok/40 bg-ok-soft text-ok-ink",
  no_caminho: "border-ok/40 bg-ok-soft text-ok-ink",
  atencao: "border-warn/40 bg-warn-soft text-warn-ink",
  abaixo: "border-crit/40 bg-crit-soft text-crit-ink",
  nao_atingida: "border-crit/40 bg-crit-soft text-crit-ink",
};

const pct0 = (f: number) => `${Math.round(f * 100)}%`;

interface Props {
  periodLabel: string;
  loading: boolean;
  error: boolean;
  gmv: number | null;
  prevGmv: number | null;
  momPct: number | null;
  compareActive: boolean;
  channels: Marketplace[];
  gmvByChannel: Record<Marketplace, number | null>;
  /** Mes comparavel com a meta; `null` = periodo nao e' um mes. */
  mesMeta: string | null;
  metasStatus: "loading" | "error" | "fresh";
  /** Nova tentativa de ler as metas (so' elas; os demais blocos tem a propria). */
  onRetryMetas: () => void;
  progresso: ProgressoMeta | null;
  /** Ate' onde o realizado tem dado, para o rotulo ("até 28/10"). */
  ultimoDia: string | null;
  aviso: { texto: string; onOpen: () => void } | null;
  metasHref: string;
  /** Abre o detalhe do GMV (o mesmo drill-down que o card de GMV abria). */
  onOpenGmv: () => void;
}

export default function MetaHero(props: Props) {
  const { gmv, loading, error } = props;
  return (
    <section aria-labelledby="meta-hero-titulo" className="rounded-xl border border-line bg-surface">
      <div className="grid grid-cols-1 gap-5 p-5 lg:grid-cols-[minmax(240px,1fr)_2fr_auto] lg:items-center lg:gap-8 lg:p-6">
        <Resultado {...props} />
        <div className="min-w-0">
          {loading && gmv == null ? (
            <div className="h-20 animate-pulse rounded-lg bg-raised motion-reduce:animate-none" aria-hidden="true" />
          ) : error ? null : props.progresso ? (
            <BarraDaMeta {...props} progresso={props.progresso} />
          ) : (
            <SemMeta {...props} />
          )}
          <Divisao channels={props.channels} gmvByChannel={props.gmvByChannel} total={gmv} />
        </div>
        <Selo progresso={props.progresso} metasHref={props.metasHref} mesMeta={props.mesMeta} />
      </div>
      {props.aviso && (
        <div className="px-5 pb-5 lg:px-6">
          <div
            role="note"
            className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-xs leading-snug text-warn-ink"
          >
            <TriangleAlert aria-hidden="true" className="h-4 w-4 shrink-0" />
            <span className="min-w-0 flex-1">{props.aviso.texto}</span>
            <button
              type="button"
              onClick={props.aviso.onOpen}
              className="inline-flex min-h-11 items-center font-semibold text-accent-ink underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Ver confiança no dado →
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function Resultado({ periodLabel, gmv, prevGmv, momPct, compareActive, loading, error, ultimoDia, onOpenGmv }: Props) {
  const ate = ultimoDia ? ` · até ${ultimoDia.slice(8, 10)}/${ultimoDia.slice(5, 7)}` : "";
  return (
    <div className="min-w-0">
      <h2 id="meta-hero-titulo" className="text-xs font-semibold uppercase tracking-wide text-ink-faint">
        GMV do período{ate}
      </h2>
      {error ? (
        <p className="mt-2 text-sm text-crit-ink">Indicadores do período indisponíveis.</p>
      ) : loading && gmv == null ? (
        <div className="mt-2 h-9 w-48 animate-pulse rounded bg-raised motion-reduce:animate-none" aria-hidden="true" />
      ) : (
        <>
          <p className="mt-1 text-3xl font-bold tracking-tight text-ink tabular-nums">{fmtCompacto(gmv)}</p>
          {compareActive && momPct != null && (
            <p className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-ink-muted tabular-nums">
              <span
                className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 font-semibold ${
                  momPct >= 0 ? "bg-ok-soft text-ok-ink" : "bg-crit-soft text-crit-ink"
                }`}
              >
                {momPct >= 0 ? (
                  <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
                ) : (
                  <ArrowDownRight aria-hidden="true" className="h-3.5 w-3.5" />
                )}
                {fmtPct(momPct)}
              </span>
              vs período anterior{prevGmv != null ? ` (${fmtCompacto(prevGmv)})` : ""}
            </p>
          )}
          <p className="mt-1 text-xs text-ink-faint">{periodLabel}</p>
          <button
            type="button"
            aria-haspopup="dialog"
            onClick={onOpenGmv}
            className="mt-1 inline-flex min-h-11 items-center text-xs font-semibold text-accent-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            Detalhar GMV por marca e canal →
          </button>
        </>
      )}
    </div>
  );
}

function BarraDaMeta({ progresso: p, mesMeta }: Props & { progresso: ProgressoMeta }) {
  // A escala vai ate' o maior entre meta, realizado e projecao: passar da meta
  // tem que aparecer como passar do traco, nao como barra cheia.
  const escala = Math.max(1, p.atingimento, p.projecaoSobreMeta ?? 0);
  const larg = (f: number) => `${(Math.min(f, escala) / escala) * 100}%`;
  const projecaoAlem = p.projecaoSobreMeta != null && p.projecaoSobreMeta > p.atingimento;

  return (
    <div>
      <div className="mb-2 flex items-end justify-between gap-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink-faint">
          Meta de {mesMeta ? rotuloMes(mesMeta) : "o mês"} ·{" "}
          <span className="tabular-nums text-ink-muted">{fmtCompacto(p.meta)}</span>
        </p>
        <p className="text-2xl font-bold leading-none text-accent tabular-nums">
          {pct0(p.atingimento)}
          <span className="sr-only"> da meta atingidos</span>
        </p>
      </div>
      <div
        className="relative h-3.5 rounded-full bg-raised"
        role="img"
        aria-label={`Realizado ${pct0(p.atingimento)} da meta${
          p.projecaoSobreMeta != null && !p.encerrado ? `; projeção ${pct0(p.projecaoSobreMeta)}` : ""
        }`}
      >
        {projecaoAlem && !p.encerrado && (
          <div
            className="absolute inset-y-0 left-0 rounded-full"
            style={{
              width: larg(p.projecaoSobreMeta!),
              backgroundImage:
                "repeating-linear-gradient(135deg, rgb(var(--tc-accent) / 0.45) 0 5px, rgb(var(--tc-accent) / 0.12) 5px 10px)",
            }}
          />
        )}
        <div className="absolute inset-y-0 left-0 rounded-full bg-accent" style={{ width: larg(p.atingimento) }} />
        <div className="absolute -inset-y-1 w-0.5 bg-ink" style={{ left: larg(1) }} aria-hidden="true" />
      </div>
      <div className="mt-2 flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-ink-muted tabular-nums">
        <span>
          realizado <b className="font-semibold text-ink">{fmtCompacto(p.realizado)}</b> ·{" "}
          {p.encerrado ? "mês encerrado" : `${p.diasComDado} de ${p.diasNoMes} dias`}
        </span>
        {!p.encerrado && p.projecao != null && (
          <span>
            projeção <b className="font-semibold text-ink">{fmtCompacto(p.projecao)}</b> ({pct0(p.projecaoSobreMeta!)} da
            meta)
          </span>
        )}
      </div>
      {p.porDiaNecessario != null && (
        <p className="mt-1.5 text-xs text-ink-muted tabular-nums">
          Faltam <b className="font-semibold text-ink">{fmtCompacto(p.falta)}</b>: {fmtCompacto(p.porDiaNecessario)} por dia nos{" "}
          {p.diasRestantes} {p.diasRestantes === 1 ? "dia restante" : "dias restantes"}.
        </p>
      )}
    </div>
  );
}

function SemMeta({ mesMeta, metasStatus, metasHref, onRetryMetas }: Props) {
  let texto: string;
  if (!mesMeta) texto = "A meta é mensal: escolha “Mês atual” ou um mês fechado no período para ver o atingimento.";
  else if (metasStatus === "loading") texto = "Carregando a meta do mês…";
  else if (metasStatus === "error") texto = "Não foi possível ler as metas agora.";
  else texto = `Nenhuma meta definida para ${rotuloMes(mesMeta)} neste recorte.`;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-dashed border-line-strong px-3 py-3 text-sm text-ink-muted">
      <Target aria-hidden="true" className="h-4 w-4 shrink-0 text-ink-faint" />
      <span className="min-w-0 flex-1">{texto}</span>
      {metasStatus === "error" && (
        <button
          type="button"
          onClick={onRetryMetas}
          className="inline-flex min-h-11 items-center text-sm font-semibold text-accent-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          Tentar novamente
        </button>
      )}
      {mesMeta && metasStatus === "fresh" && (
        <Link
          href={metasHref}
          className="inline-flex min-h-11 items-center text-sm font-semibold text-accent-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          Definir metas →
        </Link>
      )}
    </div>
  );
}

function Divisao({
  channels,
  gmvByChannel,
  total,
}: {
  channels: Marketplace[];
  gmvByChannel: Record<Marketplace, number | null>;
  total: number | null;
}) {
  const partes = channels
    .map((c) => ({ c, v: gmvByChannel[c] }))
    .filter((x): x is { c: Marketplace; v: number } => x.v != null && x.v > 0);
  if (!total || partes.length < 2) return null;
  return (
    <div className="mt-4">
      <div className="flex h-1.5 gap-0.5 overflow-hidden rounded-full" aria-hidden="true">
        {partes.map(({ c, v }) => (
          <div key={c} className={PONTO[c]} style={{ flex: v }} />
        ))}
      </div>
      <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted tabular-nums">
        {partes.map(({ c, v }) => (
          <li key={c} className="inline-flex items-center gap-1.5">
            <span aria-hidden="true" className={`h-2 w-2 rounded-full ${PONTO[c]}`} />
            {CHANNEL_LABEL[c]} {fmtCompacto(v)} · {Math.round((v / total) * 100)}%
          </li>
        ))}
      </ul>
    </div>
  );
}

function Selo({
  progresso,
  metasHref,
  mesMeta,
}: {
  progresso: ProgressoMeta | null;
  metasHref: string;
  mesMeta: string | null;
}) {
  return (
    <div className="flex flex-row items-center justify-between gap-3 border-t border-line pt-4 lg:flex-col lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
      <div className="text-left lg:text-center">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Fechamento</p>
        {progresso ? (
          <p
            className={`mt-1.5 inline-flex rounded-full border px-3 py-1.5 text-xs font-bold uppercase tracking-wide ${SELO[progresso.status]}`}
          >
            {ROTULO_STATUS[progresso.status]}
          </p>
        ) : (
          <p className="mt-1.5 text-xs text-ink-faint">sem meta</p>
        )}
      </div>
      {mesMeta && (
        <Link
          href={metasHref}
          className="inline-flex min-h-11 items-center text-xs font-semibold text-accent-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {progresso ? "Editar metas →" : "Ir para Metas →"}
        </Link>
      )}
    </div>
  );
}
