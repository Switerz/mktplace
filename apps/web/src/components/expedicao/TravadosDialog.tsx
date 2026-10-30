"use client";

/**
 * EXPEDICAO-TRAVADOS-1 — "quais sao os travados?", ao clicar no numero.
 *
 * Lista os pedidos travados da marca (ou da conta) pela MESMA rota da fila,
 * filtrada por `situacao=stalled` e ordenada pelos mais antigos. Nada e'
 * recalculado: o motivo vem das flags do pipeline (`is_slow_vs_baseline`,
 * `is_source_zombie`) e a idade de `hours_open`.
 *
 * A ETAPA do pedido vem da fila (T-0015); sem ela, o rodape diz, nunca inventa.
 *
 * EXPEDICAO-NUMERO-PEDIDO — numero real do pedido (decisao de 30/10/2026, ate'
 * a Torre ter login) e "Exportar CSV" com a lista carregada, para mandar adiante.
 *
 * POPUPS-1 — numero grande com a fatia da fila em pilula; depois as quebras
 * que respondem "por que", "onde" e "de quem" (contagem das linhas ja'
 * carregadas, com barra de participacao) e so' entao a lista de pedidos.
 */
import { Download } from "lucide-react";
import { useEffect, useState } from "react";

import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import DrilldownSecao from "@/components/drilldown/DrilldownSecao";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import NumeroAnimado from "@/components/ui/NumeroAnimado";
import {
  canalTemPrazo,
  formatarHoras,
  numeroDoPedido,
  rotuloDeadline,
  rotuloDoNumero,
  type Canal,
  type LinhaFila,
  type RespostaExpedicao,
} from "@/lib/expedicao-contract";
import {
  csvDosTravados,
  motivoDoTravado,
  nomeDoCsvTravados,
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

/** POPUPS-1 — contagem por rotulo, da maior para a menor. */
function contarPor(linhas: LinhaFila[], rotulo: (l: LinhaFila) => string): { rotulo: string; n: number }[] {
  const n = new Map<string, number>();
  for (const l of linhas) {
    const k = rotulo(l);
    n.set(k, (n.get(k) ?? 0) + 1);
  }
  return [...n.entries()].map(([r, qtd]) => ({ rotulo: r, n: qtd })).sort((a, b) => b.n - a.n);
}

/** Baixa o CSV no navegador (Blob + link sintetico, URL sempre revogada). */
function baixarCsv(conteudo: string, nome: string): void {
  let url: string | null = null;
  try {
    url = URL.createObjectURL(new Blob([conteudo], { type: "text/csv;charset=utf-8;" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = nome;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  } finally {
    if (url) URL.revokeObjectURL(url);
  }
}

/** POPUPS-1 — uma quebra em linhas com barra; a base e' a lista carregada. */
function Quebra({ itens, base }: { itens: { rotulo: string; n: number }[]; base: number }) {
  return (
    <ul className="flex flex-col">
      {itens.map((i) => (
        <EvidenceRow
          key={i.rotulo}
          label={i.rotulo}
          pct={base > 0 ? (i.n / base) * 100 : null}
          barra="bg-warn"
          value={`${formatarInteiro(i.n)} · ${percentualDaFila(i.n, base) ?? "—"}`}
        />
      ))}
    </ul>
  );
}

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
  // Numero real (ou `order_ref`); com a chave desligada na API todos vem
  // nulos, e uma coluna inteira de "—" nao diz nada, entao ela sai.
  const temCodigo = estado.tipo === "ok" && estado.linhas.some((l) => numeroDoPedido(l) !== null);
  // Sem prazo publicado (Mercado Livre) a coluna seria "Sem prazo" em todas.
  const temPrazo = canalTemPrazo(canal);
  // Etapa (T-0015): so' com a migration 033 aplicada e o publicador novo.
  const temEtapa = estado.tipo === "ok" && temEtapaPublicada(estado.linhas);
  const porEtapa = estado.tipo === "ok" && temEtapa ? travadosPorEtapa(estado.linhas) : [];
  // POPUPS-1 — quebras sobre as linhas carregadas. Com a lista cortada no
  // limite, elas falam dos N mais antigos, e o rotulo da secao diz isso.
  const linhas = estado.tipo === "ok" ? estado.linhas : [];
  const porMotivo = contarPor(linhas, motivoDoTravado);
  const porMarca = variasMarcas ? contarPor(linhas, (l) => brandLabel(l.brand)) : [];
  const cortada = estado.tipo === "ok" && estado.total > estado.linhas.length;
  const amostra = cortada ? <span className="tabular-nums">nos {formatarInteiro(linhas.length)} mais antigos</span> : undefined;
  // Rodape so' com o que falta dizer; sem nada, nenhum paragrafo vazio.
  const rodape = [
    alvo?.incluiFbs ? "Lista da fila publicada, com pedidos FBS." : null,
    estado.tipo === "ok" && !temEtapa ? "Etapa ainda não publicada pela fonte." : null,
    estado.tipo === "ok" && !temCodigo ? "Por segurança, sem número do pedido." : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <KpiDrilldownDialog open={alvo !== null} onClose={aoFechar} title={alvo?.titulo ?? "Travados"} largura="larga">
      {alvo ? (
        <div className="flex flex-col gap-4 text-sm" data-testid="exp-travados">
          {/* POPUPS-1 — o numero e a fatia da fila, sem frase em volta. */}
          <div className="flex flex-wrap items-end gap-x-3 gap-y-1.5">
            <NumeroAnimado
              texto={formatarInteiro(travados)}
              className="text-[32px] font-bold leading-none tracking-tight text-warn-ink tabular-nums"
            />
            <span className="flex flex-wrap items-center gap-2 pb-0.5">
              {pct ? (
                <span className="rounded-md bg-warn-soft px-2 py-0.5 text-sm font-semibold tabular-nums text-warn-ink">
                  {pct} da fila
                </span>
              ) : null}
              <span className="text-xs text-ink-faint tabular-nums">
                travados{alvo.backlog > 0 ? ` de ${formatarInteiro(alvo.backlog)} na fila` : ""}
              </span>
            </span>
          </div>

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
              <DrilldownSecao titulo="Por que travou" acao={amostra}>
                <Quebra itens={porMotivo} base={linhas.length} />
              </DrilldownSecao>

              {porEtapa.length ? (
                <div data-testid="exp-travados-por-etapa">
                  {/* Etapa na ordem do fluxo, nao por tamanho: e' o caminho do pedido. */}
                  <DrilldownSecao titulo="Onde estão travados" acao={amostra}>
                    <Quebra itens={porEtapa} base={linhas.length} />
                  </DrilldownSecao>
                </div>
              ) : null}

              {porMarca.length > 1 ? (
                <DrilldownSecao titulo="Por marca" acao={amostra}>
                  <Quebra itens={porMarca} base={linhas.length} />
                </DrilldownSecao>
              ) : null}

              <DrilldownSecao
                titulo="Pedidos"
                acao={
                  <>
                    <span className="tabular-nums">
                      {cortada
                        ? `${formatarInteiro(linhas.length)} mais antigos de ${formatarInteiro(estado.total)}`
                        : "mais antigos primeiro"}
                    </span>
                    <button
                      type="button"
                      data-testid="exp-travados-csv"
                      title={`Baixa os ${formatarInteiro(linhas.length)} listados em CSV`}
                      onClick={() => baixarCsv(csvDosTravados(linhas, canal), nomeDoCsvTravados(canal, alvo.marca, new Date()))}
                      className="-my-3 ml-2 inline-flex min-h-[44px] items-center gap-1.5 rounded-lg px-2 text-xs font-semibold text-accent-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    >
                      <Download aria-hidden="true" className="h-3.5 w-3.5" />
                      Exportar CSV
                    </button>
                  </>
                }
              >
                <div className="max-h-[50vh] overflow-auto rounded-lg border border-line">
                  {/* Popup largo + celulas curtas sem quebra: cabe sem rolagem
                      lateral no desktop; so' o motivo pode quebrar linha. */}
                  <table className="w-full text-sm [&_td]:px-3 [&_td]:py-1.5 [&_th]:whitespace-nowrap [&_th]:px-3 [&_th]:py-1.5">
                    <caption className="sr-only">Pedidos travados, do mais antigo ao mais novo</caption>
                    <thead className="sticky top-0 bg-raised text-left text-xs uppercase tracking-wide text-ink-faint">
                      <tr>
                        {temCodigo ? <th scope="col">{rotuloDoNumero(canal)}</th> : null}
                        {variasMarcas ? <th scope="col">Marca</th> : null}
                        {temEtapa ? <th scope="col">Etapa</th> : null}
                        <th scope="col" className="text-right">Aberto há</th>
                        <th scope="col">Por que travou</th>
                        {temPrazo ? <th scope="col">Prazo</th> : null}
                      </tr>
                    </thead>
                    <tbody>
                      {estado.linhas.map((l, i) => (
                        <tr key={`${l.shop_account}-${numeroDoPedido(l) ?? i}`} className="border-t border-line">
                          {temCodigo ? (
                            <td className="whitespace-nowrap font-mono text-xs text-ink">
                              {numeroDoPedido(l) ?? "—"}
                            </td>
                          ) : null}
                          {variasMarcas ? <td className="whitespace-nowrap text-ink">{brandLabel(l.brand)}</td> : null}
                          {temEtapa ? <td className="whitespace-nowrap font-medium text-ink">{rotuloDaEtapa(l.etapa)}</td> : null}
                          <td className="whitespace-nowrap text-right font-semibold tabular-nums text-ink">{formatarHoras(l.hours_open)}</td>
                          <td className="text-ink-muted">{motivoDoTravado(l)}</td>
                          {temPrazo ? <td className="whitespace-nowrap text-ink-muted">{rotuloDeadline(l.deadline_status)}</td> : null}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </DrilldownSecao>
            </>
          )}
          {rodape ? <p className="text-xs text-ink-faint">{rodape}</p> : null}
        </div>
      ) : null}
    </KpiDrilldownDialog>
  );
}
