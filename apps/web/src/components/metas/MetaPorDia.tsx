"use client";

/**
 * METAS-DIARIAS-1 — a meta do mes aberta dia a dia, como a aba "Meta diaria"
 * da planilha de novembro/26.
 *
 * Nada aqui e' digitado: cada dia e' a meta do mes x peso do dia / soma dos
 * pesos (curva do canal em `metas-curva.ts`). Mes sem curva divide igual. Os
 * valores seguem o que esta' na grade, inclusive alteracao ainda nao salva —
 * dai' o chip de previa.
 */
import { CalendarDays } from "lucide-react";

import TableScrollHint from "@/components/TableScrollHint";
import { CANAIS, MARCAS, hojeBrt, type Valores } from "@/lib/metas";
import { escopoTemCurva, tabelaMetaPorDia } from "@/lib/metas-curva";
import type { CelulaMetaGmv } from "@/lib/gerencial/meta-progress";

const PONTO_DO_CANAL: Record<string, string> = {
  tiktok: "bg-canal-tiktok",
  ml: "bg-canal-ml",
  shopee: "bg-canal-shopee",
};

const FMT = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const FMT_PCT = new Intl.NumberFormat("pt-BR", { style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1 });

const rotuloMarca = (id: string) => MARCAS.find((m) => m.id === id)?.label ?? id;
const rotuloCanal = (id: string) => CANAIS.find((c) => c.id === id)?.label ?? id;

function celulasDe(valores: Valores): CelulaMetaGmv[] {
  return Object.entries(valores)
    .filter((e): e is [string, number] => e[1] != null)
    .map(([k, meta_gmv]) => {
      const [brand, channel] = k.split(":");
      return { brand, channel, meta_gmv };
    });
}

interface Props {
  mes: string;
  /** Valores efetivos da grade (o digitado quando valido, senao o salvo). */
  valores: Valores;
  /** Ha alteracao nao salva: a tabela e' uma previa. */
  previa: boolean;
}

export default function MetaPorDia({ mes, valores, previa }: Props) {
  const celulas = celulasDe(valores);
  const t = tabelaMetaPorDia(celulas, mes);
  if (t.colunas.length === 0) return null;
  const canais = CANAIS.map((c) => c.id).filter((c) => t.colunas.some((x) => x.channel === c));
  const comCurva = escopoTemCurva(celulas, mes);
  const hoje = hojeBrt();
  const th = "whitespace-nowrap px-3 py-2.5 text-right font-semibold";

  return (
    <section aria-labelledby="meta-por-dia-titulo" className="rounded-xl border border-line bg-surface">
      <div className="flex flex-wrap items-center gap-2 px-4 pt-4">
        <CalendarDays aria-hidden="true" className="h-4 w-4 text-ink-faint" />
        <h3 id="meta-por-dia-titulo" className="text-sm font-semibold text-ink">
          Meta por dia
        </h3>
        <span
          className="inline-flex min-h-7 items-center rounded-full border border-line px-2.5 text-xs text-ink-muted"
          title={
            comCurva
              ? "Meta do mês × peso do dia ÷ soma dos pesos. Pesos por canal: dia da semana e eventos (11/11, semana Black)."
              : "Este mês não tem curva: a meta do mês é dividida igualmente pelos dias."
          }
        >
          {comCurva ? "pela curva do mês" : "sem curva: dividida igual"}
        </span>
        {previa && (
          <span className="inline-flex min-h-7 items-center rounded-full bg-accent-soft px-2.5 text-xs font-semibold text-accent-ink">
            prévia, não salva
          </span>
        )}
      </div>
      <TableScrollHint className="mt-3">
        <table className="w-full min-w-[960px] border-collapse text-[13px] tabular-nums">
          <caption className="sr-only">Meta de GMV por dia, marca e canal, em reais</caption>
          <thead>
            <tr className="border-y border-line bg-raised text-xs uppercase tracking-wide text-ink-faint">
              <th scope="col" className="px-3 py-2.5 text-left font-semibold">Dia</th>
              <th scope="col" className="px-3 py-2.5 text-left font-semibold">Evento</th>
              <th scope="col" className={th}>Total do dia</th>
              <th scope="col" className={th}>Acumulado</th>
              <th scope="col" className={th}>% do mês</th>
              {canais.length > 1 &&
                canais.map((c) => (
                  <th key={c} scope="col" className={th}>
                    <span className="inline-flex items-center gap-1.5">
                      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${PONTO_DO_CANAL[c]}`} />
                      {rotuloCanal(c)}
                    </span>
                  </th>
                ))}
              {t.colunas.map((c) => (
                <th key={`${c.brand}:${c.channel}`} scope="col" className={th}>
                  <span className="inline-flex items-center gap-1.5" title={`${rotuloMarca(c.brand)} em ${rotuloCanal(c.channel)}`}>
                    <span aria-hidden="true" className={`h-2 w-2 rounded-full ${PONTO_DO_CANAL[c.channel]}`} />
                    {rotuloMarca(c.brand)}
                    <span className="sr-only"> em {rotuloCanal(c.channel)}</span>
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {t.linhas.map((l) => {
              const ehHoje = l.dia === hoje;
              return (
                <tr
                  key={l.dia}
                  aria-current={ehHoje ? "date" : undefined}
                  className={`border-b border-line last:border-b-0 hover:bg-raised ${ehHoje ? "bg-accent-soft" : ""}`}
                >
                  <th scope="row" className="whitespace-nowrap px-3 py-2 text-left font-medium text-ink">
                    {l.dia.slice(8, 10)}/{l.dia.slice(5, 7)} <span className="text-ink-faint">{l.diaSemana}</span>
                  </th>
                  <td className="whitespace-nowrap px-3 py-2 text-left">
                    {l.evento && (
                      <span className="inline-flex rounded-full bg-accent-soft px-2 py-0.5 text-xs font-semibold text-accent-ink">
                        {l.evento}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right font-semibold text-ink">{FMT.format(l.total)}</td>
                  <td className="px-3 py-2 text-right text-ink-muted">{FMT.format(l.acumulado)}</td>
                  <td className="px-3 py-2 text-right text-ink-muted">{FMT_PCT.format(l.pctAcumulado)}</td>
                  {canais.length > 1 &&
                    canais.map((c) => (
                      <td key={c} className="px-3 py-2 text-right text-ink">{FMT.format(l.porCanal[c] ?? 0)}</td>
                    ))}
                  {l.valores.map((v, i) => (
                    <td key={i} className="px-3 py-2 text-right text-ink-muted">{FMT.format(v)}</td>
                  ))}
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="border-t-2 border-line-strong bg-raised font-semibold text-ink">
              <th scope="row" colSpan={2} className="px-3 py-2.5 text-left">Total</th>
              <td className="px-3 py-2.5 text-right">{FMT.format(t.total)}</td>
              <td colSpan={2} />
              {canais.length > 1 &&
                canais.map((c) => (
                  <td key={c} className="px-3 py-2.5 text-right">
                    {FMT.format(t.colunas.filter((x) => x.channel === c).reduce((a, x) => a + x.meta_gmv, 0))}
                  </td>
                ))}
              {t.colunas.map((c) => (
                <td key={`${c.brand}:${c.channel}`} className="px-3 py-2.5 text-right">{FMT.format(c.meta_gmv)}</td>
              ))}
            </tr>
          </tfoot>
        </table>
      </TableScrollHint>
    </section>
  );
}
