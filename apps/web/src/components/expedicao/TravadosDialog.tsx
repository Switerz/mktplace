"use client";

/**
 * EXPEDICAO-TRAVADOS-1 — "quais sao os travados?", ao clicar no numero.
 *
 * Lista os pedidos travados da marca (ou da conta) pela MESMA rota da fila,
 * filtrada por `situacao=stalled` e ordenada pelos mais antigos. Nada e'
 * recalculado: o motivo vem das flags do pipeline (`is_slow_vs_baseline`,
 * `is_source_zombie`) e a idade de `hours_open`.
 *
 * A ETAPA do pedido (a preparar x pronto para envio; substatus do ML) ainda
 * nao e' publicada na fila — dito no rodape, nunca inventado.
 */
import { useEffect, useState } from "react";

import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import {
  canalTemPrazo,
  formatarHoras,
  rotuloDeadline,
  type Canal,
  type LinhaFila,
  type RespostaExpedicao,
} from "@/lib/expedicao-contract";
import {
  motivoDoTravado,
  percentualDaFila,
  queryDosTravados,
  rotuloDaEtapa,
  temEtapaPublicada,
  travadosPorEtapa,
  type AlvoTravados,
} from "@/lib/expedicao-travados";
import { buscarExpedicao } from "@/lib/expedicao-demo";
import { formatarInteiro } from "@/lib/expedicao-formato";
import { brandLabel } from "@/lib/filters/brands";

export type { AlvoTravados };

type Estado =
  | { tipo: "carregando" }
  | { tipo: "erro" }
  | { tipo: "ok"; linhas: LinhaFila[]; total: number };

export default function TravadosDialog({
  api,
  canal,
  alvo,
  aoFechar,
}: {
  api: string;
  canal: Canal;
  alvo: AlvoTravados | null;
  aoFechar: () => void;
}) {
  const [estado, setEstado] = useState<Estado>({ tipo: "carregando" });
  const [tentativa, setTentativa] = useState(0);
  const query = alvo ? queryDosTravados(canal, alvo.marca, alvo.conta) : null;

  useEffect(() => {
    if (!query) return;
    let vivo = true;
    setEstado({ tipo: "carregando" });
    void tentativa; // "Tentar de novo" refaz a mesma busca
    buscarExpedicao(`${api}/api/v1/expedicao?${query}`, { headers: { Accept: "application/json" } })
      .then(async (r) => {
        if (!r.ok) throw new Error("http");
        return (await r.json()) as RespostaExpedicao;
      })
      .then((json) => {
        if (!vivo) return;
        const linhas = json.queue ?? [];
        setEstado({ tipo: "ok", linhas, total: json.pagination?.total ?? linhas.length });
      })
      .catch(() => {
        if (vivo) setEstado({ tipo: "erro" });
      });
    return () => {
      vivo = false;
    };
  }, [api, query, tentativa]);

  // Carregada a lista, o total e' o dela (mesma leitura que os pedidos abaixo).
  const travados = estado.tipo === "ok" ? estado.total : (alvo?.travados ?? 0);
  const pct = alvo ? percentualDaFila(travados, alvo.backlog) : null;
  const variasMarcas = alvo?.marca === null;
  // Sem o segredo do `order_ref` configurado na API, todo codigo vem nulo:
  // uma coluna inteira de "—" nao diz nada, entao ela sai.
  const temCodigo = estado.tipo === "ok" && estado.linhas.some((l) => l.order_ref);
  // Sem prazo publicado (Mercado Livre) a coluna seria "Sem prazo" em todas.
  const temPrazo = canalTemPrazo(canal);
  // Etapa (T-0015): so' com a migration 033 aplicada e o publicador novo.
  const temEtapa = estado.tipo === "ok" && temEtapaPublicada(estado.linhas);
  const porEtapa = estado.tipo === "ok" && temEtapa ? travadosPorEtapa(estado.linhas) : [];
  // Rodape so' com o que falta dizer; sem nada, nenhum paragrafo vazio.
  const rodape = [
    alvo?.incluiFbs ? "Lista da fila publicada, com pedidos FBS." : null,
    estado.tipo === "ok" && !temEtapa ? "Etapa ainda não publicada pela fonte." : null,
    estado.tipo === "ok" && !temCodigo ? "Por segurança, sem número do pedido." : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <KpiDrilldownDialog open={alvo !== null} onClose={aoFechar} title={alvo?.titulo ?? "Travados"}>
      {alvo ? (
        <div className="flex flex-col gap-3" data-testid="exp-travados">
          <p className="flex items-baseline gap-2">
            <span className="text-3xl font-bold tabular-nums text-warn-ink">{formatarInteiro(travados)}</span>
            <span className="text-sm text-ink-muted">
              travados{pct ? ` · ${pct} da fila` : ""}
            </span>
          </p>

          {estado.tipo === "carregando" ? (
            <p role="status" className="text-sm text-ink-muted">Carregando pedidos…</p>
          ) : estado.tipo === "erro" ? (
            <p role="status" className="text-sm text-crit-ink">
              Não foi possível carregar os pedidos.{" "}
              <button
                type="button"
                onClick={() => setTentativa((n) => n + 1)}
                className="font-semibold underline underline-offset-4"
              >
                Tentar de novo
              </button>
            </p>
          ) : estado.linhas.length === 0 ? (
            <p role="status" className="text-sm text-ink-muted">Nenhum pedido travado nesta fotografia.</p>
          ) : (
            <>
              {porEtapa.length ? (
                <div data-testid="exp-travados-por-etapa">
                  <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-faint">
                    Onde estão travados
                    {estado.total > estado.linhas.length
                      ? ` (nos ${formatarInteiro(estado.linhas.length)} mais antigos)`
                      : ""}
                  </p>
                  <ul className="flex flex-wrap gap-1.5">
                    {porEtapa.map((e) => (
                      <li
                        key={e.etapa}
                        className="inline-flex items-baseline gap-1.5 rounded-full border border-line bg-raised px-2.5 py-1 text-xs"
                      >
                        <span className="text-ink-muted">{e.rotulo}</span>
                        <span className="font-semibold tabular-nums text-ink">{formatarInteiro(e.n)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <div className="max-h-[50vh] overflow-auto rounded-lg border border-line">
                <table className="w-full text-sm [&_td]:px-3 [&_td]:py-1.5 [&_th]:px-3 [&_th]:py-1.5">
                  <caption className="sr-only">Pedidos travados, do mais antigo ao mais novo</caption>
                  <thead className="sticky top-0 bg-surface text-left text-xs uppercase tracking-wide text-ink-faint">
                    <tr>
                      {temCodigo ? <th scope="col">Pedido</th> : null}
                      {variasMarcas ? <th scope="col">Marca</th> : null}
                      {temEtapa ? <th scope="col">Etapa</th> : null}
                      <th scope="col" className="text-right">Aberto há</th>
                      <th scope="col">Por que travou</th>
                      {temPrazo ? <th scope="col">Prazo</th> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {estado.linhas.map((l, i) => (
                      <tr key={`${l.shop_account}-${l.order_ref ?? i}`} className="border-t border-line">
                        {temCodigo ? (
                          <td className="font-mono text-xs text-ink-muted" title={l.order_ref ?? undefined}>
                            {l.order_ref ? l.order_ref.slice(0, 10) : "—"}
                          </td>
                        ) : null}
                        {variasMarcas ? <td className="text-ink">{brandLabel(l.brand)}</td> : null}
                        {temEtapa ? <td className="font-medium text-ink">{rotuloDaEtapa(l.etapa)}</td> : null}
                        <td className="text-right font-semibold tabular-nums text-ink">{formatarHoras(l.hours_open)}</td>
                        <td className="text-ink">{motivoDoTravado(l)}</td>
                        {temPrazo ? <td className="text-ink-muted">{rotuloDeadline(l.deadline_status)}</td> : null}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {estado.total > estado.linhas.length ? (
                <p className="text-xs text-ink-muted">
                  Mostrando os {formatarInteiro(estado.linhas.length)} mais antigos de{" "}
                  {formatarInteiro(estado.total)}.
                </p>
              ) : null}
            </>
          )}
          {rodape ? <p className="text-xs text-ink-faint">{rodape}</p> : null}
        </div>
      ) : null}
    </KpiDrilldownDialog>
  );
}
