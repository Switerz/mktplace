"use client";

/**
 * VENDAS-POR-HORA-1 — "Hoje até 13h": os canais cortados na MESMA hora cheia,
 * contra ontem e a semana passada até a mesma hora.
 *
 * So' aparece com o periodo "Hoje". Busca sozinho (`/hoje-por-hora`) e falha
 * sozinho: erro aqui nao apaga o resto da Gerencial. A leitura do contrato
 * vive em `lib/gerencial/hoje-por-hora.ts` (pura e testada).
 */
import { useEffect, useState } from "react";
import { ArrowDownRight, ArrowUpRight, Clock } from "lucide-react";
import { fetchHojePorHora } from "@/lib/api-client";
import { fmtBrlFull, fmtPctBr } from "@/lib/formatters";
import {
  canaisQueLimitam,
  nomeDoCanal,
  rotuloDoCorte,
  variacao,
  type BlocoVendas,
  type HojePorHora,
} from "@/lib/gerencial/hoje-por-hora";
import type { Marketplace } from "@/lib/mock-data";

interface Props {
  channels: Marketplace[];
  brands: string[];
}

type Estado = { status: "loading" } | { status: "error" } | { status: "ok"; data: HojePorHora };

function Variacao({ atual, base, rotulo }: { atual: number; base: number; rotulo: string }) {
  const pct = variacao(atual, base);
  if (pct == null) return <span className="text-xs text-ink-faint">{rotulo}: sem base</span>;
  const sobe = pct >= 0;
  const Seta = sobe ? ArrowUpRight : ArrowDownRight;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-ink-muted">
      <span
        className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 font-semibold tabular-nums ${
          sobe ? "bg-ok-soft text-ok-ink" : "bg-crit-soft text-crit-ink"
        }`}
      >
        <Seta aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={2.25} />
        {fmtPctBr(pct)}
      </span>
      {rotulo}
    </span>
  );
}

function Numero({
  titulo,
  valor,
  hoje,
  ontem,
  semana,
}: {
  titulo: string;
  valor: string;
  hoje: number;
  ontem: number;
  semana: number;
}) {
  return (
    <div className="min-w-0 flex flex-col gap-1">
      <span className="text-xs font-semibold uppercase tracking-wide text-ink-faint">{titulo}</span>
      <span className="text-2xl font-semibold tabular-nums text-ink">{valor}</span>
      <span className="flex flex-wrap gap-x-3 gap-y-1">
        <Variacao atual={hoje} base={ontem} rotulo="vs ontem" />
        <Variacao atual={hoje} base={semana} rotulo="vs semana passada" />
      </span>
    </div>
  );
}

/** GMV em reais inteiros: "R$ 182.686", sem a casa decimal solta do Intl. */
function reais(v: number): string {
  return fmtBrlFull(Math.round(v));
}

function ticketDe(b: BlocoVendas | null | undefined): number {
  return b?.ticket ?? 0;
}

export default function HojeAteAHora({ channels, brands }: Props) {
  const [estado, setEstado] = useState<Estado>({ status: "loading" });
  const chave = `${channels.join(",")}|${brands.join(",")}`;

  useEffect(() => {
    let vivo = true;
    setEstado({ status: "loading" });
    fetchHojePorHora(channels, brands).then((data) => {
      if (!vivo) return;
      setEstado(data ? { status: "ok", data } : { status: "error" });
    });
    return () => {
      vivo = false;
    };
    // `chave` resume channels/brands por valor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chave]);

  if (estado.status === "loading") {
    return (
      <section aria-busy="true" className="bg-surface border border-line rounded-xl p-4 animate-pulse">
        <span className="sr-only">Carregando vendas de hoje por hora…</span>
        <div className="h-3 w-40 bg-raised rounded" />
        <div className="mt-3 h-7 w-56 bg-raised rounded" />
      </section>
    );
  }

  if (estado.status === "error" || !estado.data.disponivel) {
    const motivo = estado.status === "ok" ? estado.data.motivo : null;
    return (
      <section className="bg-surface border border-line rounded-xl p-4 text-sm text-ink-muted">
        Vendas de hoje por hora indisponíveis{motivo ? `: ${motivo}` : " no momento."}
      </section>
    );
  }

  const d = estado.data;
  const rotulo = rotuloDoCorte(d.corte_hora);
  const limitam = canaisQueLimitam(d);

  return (
    <section
      aria-labelledby="hoje-ate-a-hora-titulo"
      className="bg-surface border border-kpi-gmv/30 rounded-xl p-4 flex flex-col gap-3"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="hoje-ate-a-hora-titulo" className="flex items-center gap-1.5 text-sm font-semibold text-ink">
          <Clock aria-hidden="true" className="h-4 w-4 text-kpi-gmv" />
          <span className="whitespace-nowrap">{rotulo ? `Hoje ${rotulo}` : "Hoje"}</span>
          <span className="font-normal text-ink-faint">· mesmo horário em todos os canais</span>
        </h2>
        {limitam.length > 0 && (
          <span className="text-xs text-ink-faint">
            Corte limitado por {limitam.map(nomeDoCanal).join(" e ")}, que ainda não fechou a hora seguinte.
          </span>
        )}
      </header>

      {!rotulo || !d.hoje || !d.ontem || !d.semana_passada ? (
        <p className="text-sm text-ink-muted">Nenhuma hora de hoje fechou ainda em todos os canais.</p>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <Numero titulo="GMV" valor={reais(d.hoje.gmv)} hoje={d.hoje.gmv} ontem={d.ontem.gmv} semana={d.semana_passada.gmv} />
            <Numero
              titulo="Pedidos"
              valor={d.hoje.pedidos.toLocaleString("pt-BR")}
              hoje={d.hoje.pedidos}
              ontem={d.ontem.pedidos}
              semana={d.semana_passada.pedidos}
            />
            <Numero
              titulo="Ticket médio"
              valor={d.hoje.ticket != null ? fmtBrlFull(d.hoje.ticket) : "—"}
              hoje={ticketDe(d.hoje)}
              ontem={ticketDe(d.ontem)}
              semana={ticketDe(d.semana_passada)}
            />
          </div>

          {d.por_canal.length > 1 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm tabular-nums">
                <caption className="sr-only">GMV de hoje {rotulo} por canal, contra ontem {rotulo}</caption>
                <thead>
                  <tr className="text-xs text-ink-faint">
                    <th scope="col" className="text-left font-medium py-1">Canal</th>
                    <th scope="col" className="text-right font-medium py-1">Hoje {rotulo}</th>
                    <th scope="col" className="text-right font-medium py-1">Ontem {rotulo}</th>
                    <th scope="col" className="text-right font-medium py-1">Variação</th>
                  </tr>
                </thead>
                <tbody>
                  {d.por_canal.map((c) => {
                    const pct = variacao(c.hoje.gmv, c.ontem.gmv);
                    return (
                      <tr key={c.channel} className="border-t border-line">
                        <th scope="row" className="text-left font-normal py-1.5 text-ink">{nomeDoCanal(c.channel)}</th>
                        <td className="text-right py-1.5 text-ink">{reais(c.hoje.gmv)}</td>
                        <td className="text-right py-1.5 text-ink-muted">{reais(c.ontem.gmv)}</td>
                        <td
                          className={`text-right py-1.5 font-semibold ${
                            pct == null ? "text-ink-faint" : pct >= 0 ? "text-ok-ink" : "text-crit-ink"
                          }`}
                        >
                          {pct == null ? "—" : fmtPctBr(pct)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {d.limitacoes.map((l) => (
        <p key={l} className="text-xs text-ink-faint">
          {l}
        </p>
      ))}
    </section>
  );
}
