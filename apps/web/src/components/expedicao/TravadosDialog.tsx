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
 *
 * EXPEDICAO-VENCE-AMANHA — o mesmo popup lista vencidos, vence em 24 h, vence
 * no proximo dia de prazo e acima de 48 h (`alvo.lista`). So' os travados tem
 * "por que travou"; os vencidos ganham o atraso; as listas de prazo mostram a
 * data do prazo em vez da situacao, que e' a mesma em todas as linhas.
 */
import { Download } from "lucide-react";
import { useEffect, useState } from "react";

import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import DrilldownSecao from "@/components/drilldown/DrilldownSecao";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import NumeroAnimado from "@/components/ui/NumeroAnimado";
import Segmented from "@/components/ui/Segmented";
import {
  canalTemPrazo,
  diaDoProximoVencimento,
  formatarHoras,
  numeroDoPedido,
  rotuloDeadline,
  rotuloDoNumero,
  type Canal,
  type LinhaFila,
  type RespostaExpedicao,
} from "@/lib/expedicao-contract";
import {
  LISTAS,
  csvDaLista,
  diaBrtDe,
  filtrarPorVencimento,
  motivoDoTravado,
  nomeDoCsv,
  percentualDaFila,
  prazoCurtoBrt,
  queryDaLista,
  rotuloDaEtapa,
  temEtapaPublicada,
  travadosPorEtapa,
  type AlvoTravados,
  type FiltroVencimento,
} from "@/lib/expedicao-travados";
import { buscarExpedicao } from "@/lib/expedicao-demo";
import { formatarInteiro } from "@/lib/expedicao-formato";
import { brandLabel } from "@/lib/filters/brands";

export type { AlvoTravados };

type Estado =
  | { tipo: "carregando" }
  | { tipo: "erro" }
  | {
      tipo: "ok";
      linhas: LinhaFila[];
      total: number;
      /** Dia de Brasilia da fotografia e o proximo dia de prazo (filtro rapido). */
      hoje: string | null;
      proximo: string | null;
      efetivoEm: string | null;
      /** Contagem EXATA da API (`due_today_count`/`due_next_day_count`) no recorte. */
      contagemHoje: number | null;
      contagemProximo: number | null;
    };

/**
 * EXPEDICAO-PERTO-DE-VENCER-FILTRO — contagem exata de hoje / proximo dia no
 * recorte do popup. `accounts[]` e `totals` sao do canal inteiro (os filtros
 * de marca/conta recortam so' a lista), entao o recorte soma as contas.
 */
function contagemDoRecorte(
  json: RespostaExpedicao,
  marca: string | null,
  conta: string | null,
  campo: "due_today_count" | "due_next_day_count",
): number | null {
  if (!marca && !conta) {
    const v = json.totals?.[campo];
    return typeof v === "number" ? v : null;
  }
  const contas = (json.accounts ?? []).filter((a) => (conta ? a.shop_account === conta : a.brand === marca));
  if (!contas.length || contas.some((a) => typeof a[campo] !== "number")) return null;
  return contas.reduce((soma, a) => soma + (a[campo] as number), 0);
}

/** Lista do proximo dia buscada a parte quando a de "perto de vencer" vem cortada. */
type ListaDoProximo = { tipo: "carregando" } | { tipo: "erro" } | { tipo: "ok"; linhas: LinhaFila[] };

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
  const [filtroDia, setFiltroDia] = useState<FiltroVencimento>("todos");
  const lista = alvo?.lista ?? "travados";
  const cfg = LISTAS[lista];
  const ehTravados = lista === "travados";
  const ehVencidos = lista === "vencidos";
  // Vencidos e perto de vencer vem pelo prazo; o resto, pelo pagamento mais antigo.
  const ordem = cfg.ordemTexto ?? "mais antigos";
  const query = alvo ? queryDaLista(lista, canal, alvo.marca, alvo.conta) : null;

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
        const efetivoEm = json.snapshot?.effective_at ?? null;
        setEstado({
          tipo: "ok",
          linhas,
          total: json.pagination?.total ?? linhas.length,
          hoje: diaBrtDe(efetivoEm),
          proximo: json.snapshot?.next_due_date ?? null,
          efetivoEm,
          contagemHoje: contagemDoRecorte(json, alvo?.marca ?? null, alvo?.conta ?? null, "due_today_count"),
          contagemProximo: contagemDoRecorte(json, alvo?.marca ?? null, alvo?.conta ?? null, "due_next_day_count"),
        });
      })
      .catch(() => {
        if (vivo) setEstado({ tipo: "erro" });
      });
    return () => {
      vivo = false;
    };
    // `alvo` muda junto com `query` (o dialogo remonta por `key`).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, query, tentativa]);

  // Com a lista de "perto de vencer" cortada no limite da API, o proximo dia
  // (que vem depois de hoje na ordem por prazo) pode ter ficado de fora: o
  // filtro busca a lista propria dele (`due_next_day`), na mesma rota.
  const cortadaNaFonte = estado.tipo === "ok" && estado.total > estado.linhas.length;
  const precisaDoProximo = lista === "perto_de_vencer" && filtroDia === "proximo" && cortadaNaFonte;
  const queryProximo = alvo && precisaDoProximo ? queryDaLista("vence_proximo", canal, alvo.marca, alvo.conta) : null;
  const [listaProximo, setListaProximo] = useState<ListaDoProximo | null>(null);
  useEffect(() => {
    if (!queryProximo || listaProximo?.tipo === "ok") return;
    let vivo = true;
    setListaProximo({ tipo: "carregando" });
    buscarExpedicao(`${api}/api/v1/expedicao?${queryProximo}`, { headers: { Accept: "application/json" } })
      .then(async (r) => {
        if (!r.ok) throw new Error("http");
        return (await r.json()) as RespostaExpedicao;
      })
      .then((json) => {
        if (vivo) setListaProximo({ tipo: "ok", linhas: json.queue ?? [] });
      })
      .catch(() => {
        if (vivo) setListaProximo({ tipo: "erro" });
      });
    return () => {
      vivo = false;
    };
    // `listaProximo` fora das dependencias: buscada uma vez, fica em memoria.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, queryProximo]);

  // Carregada a lista, o total e' o dela (mesma leitura que os pedidos abaixo).
  const travados = estado.tipo === "ok" ? estado.total : (alvo?.travados ?? 0);
  const pct = alvo ? percentualDaFila(travados, alvo.backlog) : null;
  const variasMarcas = alvo?.marca === null;
  // Numero real (ou `order_ref`); com a chave desligada na API todos vem
  // nulos, e uma coluna inteira de "—" nao diz nada, entao ela sai.
  const temCodigo = estado.tipo === "ok" && estado.linhas.some((l) => numeroDoPedido(l) !== null);
  // Sem prazo publicado a coluna seria "Sem prazo" em todas. No Mercado Livre
  // o prazo e' o SLA oficial, que so' existe em parte dos lotes
  // (EXPEDICAO-ML-PRAZO): a coluna aparece quando alguma linha o traz.
  const temPrazo =
    canalTemPrazo(canal) ||
    (estado.tipo === "ok" && estado.linhas.some((l) => l.dispatch_deadline != null));
  // Etapa (T-0015): so' com a migration 033 aplicada e o publicador novo.
  const temEtapa = estado.tipo === "ok" && temEtapaPublicada(estado.linhas);
  // POPUPS-1 — quebras sobre as linhas carregadas. Com a lista cortada no
  // limite, elas falam dos N mais antigos, e o rotulo da secao diz isso.
  const todasAsLinhas = estado.tipo === "ok" ? estado.linhas : [];
  // EXPEDICAO-PERTO-DE-VENCER-FILTRO — so' na lista "perto de vencer" e com o
  // dia da fotografia conhecido; etapa, tabela e CSV seguem o recorte.
  const temFiltroDia = lista === "perto_de_vencer" && estado.tipo === "ok" && estado.hoje !== null;
  const hojeFoto = estado.tipo === "ok" ? estado.hoje : null;
  const proximoFoto = estado.tipo === "ok" ? estado.proximo : null;
  const filtroAtivo: FiltroVencimento = temFiltroDia ? filtroDia : "todos";
  const linhas =
    filtroAtivo === "proximo" && precisaDoProximo
      ? listaProximo?.tipo === "ok"
        ? listaProximo.linhas
        : []
      : filtrarPorVencimento(todasAsLinhas, filtroAtivo, hojeFoto, proximoFoto);
  const carregandoProximo = filtroAtivo === "proximo" && precisaDoProximo && listaProximo?.tipo !== "ok";
  // O numero do chip e' o da API (exato); sem ele, o das linhas carregadas.
  const nHoje =
    estado.tipo === "ok" && estado.contagemHoje !== null
      ? estado.contagemHoje
      : filtrarPorVencimento(todasAsLinhas, "hoje", hojeFoto, proximoFoto).length;
  const nProximo =
    estado.tipo === "ok" && estado.contagemProximo !== null
      ? estado.contagemProximo
      : filtrarPorVencimento(todasAsLinhas, "proximo", hojeFoto, proximoFoto).length;
  const diaProximo = estado.tipo === "ok" ? diaDoProximoVencimento(estado.proximo, estado.efetivoEm) : "amanhã";
  const porEtapa = estado.tipo === "ok" && temEtapa ? travadosPorEtapa(linhas) : [];
  const porMotivo = ehTravados ? contarPor(linhas, motivoDoTravado) : [];
  const porMarca = variasMarcas ? contarPor(linhas, (l) => brandLabel(l.brand)) : [];
  const cortada = cortadaNaFonte;
  // Com filtro, a ressalva so' aparece se o recorte carregado nao tem todos.
  const nDoFiltro = filtroAtivo === "hoje" ? nHoje : filtroAtivo === "proximo" ? nProximo : null;
  const amostra =
    filtroAtivo === "todos" ? (
      cortada ? <span className="tabular-nums">nos {formatarInteiro(todasAsLinhas.length)} {ordem}</span> : undefined
    ) : nDoFiltro !== null && !carregandoProximo && linhas.length < nDoFiltro ? (
      <span className="tabular-nums">nos {formatarInteiro(linhas.length)} de {formatarInteiro(nDoFiltro)}</span>
    ) : undefined;
  const tinta = cfg.tom === "critico" ? "text-crit-ink" : "text-warn-ink";
  const pilula = cfg.tom === "critico" ? "bg-crit-soft text-crit-ink" : "bg-warn-soft text-warn-ink";
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
              className={`text-[32px] font-bold leading-none tracking-tight tabular-nums ${tinta}`}
            />
            <span className="flex flex-wrap items-center gap-2 pb-0.5">
              {pct ? (
                <span className={`rounded-md px-2 py-0.5 text-sm font-semibold tabular-nums ${pilula}`}>
                  {pct} da fila
                </span>
              ) : null}
              <span className="text-xs text-ink-faint tabular-nums">
                {cfg.nome}{alvo.backlog > 0 ? ` de ${formatarInteiro(alvo.backlog)} na fila` : ""}
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
            <p role="status" className="text-sm text-ink-muted">{cfg.vazio}</p>
          ) : (
            <>
              {temFiltroDia ? (
                <Segmented<FiltroVencimento>
                  rotulo="Filtrar por dia do prazo"
                  className="self-start"
                  opcoes={[
                    { valor: "todos", texto: `Todos · ${formatarInteiro(estado.total)}` },
                    { valor: "hoje", texto: `Vence hoje · ${formatarInteiro(nHoje)}` },
                    { valor: "proximo", texto: `Vence ${diaProximo} · ${formatarInteiro(nProximo)}` },
                  ]}
                  valor={filtroAtivo}
                  aoEscolher={setFiltroDia}
                />
              ) : null}

              {carregandoProximo ? (
                <p role="status" className="text-sm text-ink-muted">
                  {listaProximo?.tipo === "erro" ? "Não foi possível carregar os pedidos do próximo dia." : "Carregando pedidos…"}
                </p>
              ) : null}

              {ehTravados ? (
                <DrilldownSecao titulo="Por que travou" acao={amostra}>
                  <Quebra itens={porMotivo} base={linhas.length} />
                </DrilldownSecao>
              ) : null}

              {porEtapa.length ? (
                <div data-testid="exp-travados-por-etapa">
                  {/* Etapa na ordem do fluxo, nao por tamanho: e' o caminho do pedido. */}
                  <DrilldownSecao titulo={ehTravados ? "Onde estão travados" : "Em que etapa estão"} acao={amostra}>
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
                      {filtroAtivo !== "todos"
                        ? `${formatarInteiro(linhas.length)}${nDoFiltro !== null && linhas.length < nDoFiltro ? ` de ${formatarInteiro(nDoFiltro)}` : ""} ${filtroAtivo === "hoje" ? "vencem hoje" : `vencem ${diaProximo}`}`
                        : cortada
                          ? `${formatarInteiro(linhas.length)} ${ordem} de ${formatarInteiro(estado.total)}`
                          : `${ordem} primeiro`}
                    </span>
                    <button
                      type="button"
                      data-testid="exp-travados-csv"
                      title={`Baixa os ${formatarInteiro(linhas.length)} listados em CSV`}
                      onClick={() => baixarCsv(csvDaLista(linhas, canal, lista), nomeDoCsv(lista, canal, alvo.marca, new Date()))}
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
                    <caption className="sr-only">Pedidos {cfg.nome}, {ordem} primeiro</caption>
                    <thead className="sticky top-0 bg-raised text-left text-xs uppercase tracking-wide text-ink-faint">
                      <tr>
                        {temCodigo ? <th scope="col">{rotuloDoNumero(canal)}</th> : null}
                        {variasMarcas ? <th scope="col">Marca</th> : null}
                        {temEtapa ? <th scope="col">Etapa</th> : null}
                        <th scope="col" className="text-right">Aberto há</th>
                        {ehTravados ? <th scope="col">Por que travou</th> : null}
                        {ehVencidos ? <th scope="col" className="text-right">Atraso</th> : null}
                        {temPrazo ? <th scope="col">Prazo</th> : null}
                      </tr>
                    </thead>
                    <tbody>
                      {linhas.map((l, i) => (
                        <tr key={`${l.shop_account}-${numeroDoPedido(l) ?? i}`} className="border-t border-line">
                          {temCodigo ? (
                            <td className="whitespace-nowrap font-mono text-xs text-ink">
                              {numeroDoPedido(l) ?? "—"}
                            </td>
                          ) : null}
                          {variasMarcas ? <td className="whitespace-nowrap text-ink">{brandLabel(l.brand)}</td> : null}
                          {temEtapa ? <td className="whitespace-nowrap font-medium text-ink">{rotuloDaEtapa(l.etapa)}</td> : null}
                          <td className="whitespace-nowrap text-right font-semibold tabular-nums text-ink">{formatarHoras(l.hours_open)}</td>
                          {ehTravados ? <td className="text-ink-muted">{motivoDoTravado(l)}</td> : null}
                          {ehVencidos ? (
                            <td className="whitespace-nowrap text-right tabular-nums text-crit-ink">{formatarHoras(l.hours_overdue)}</td>
                          ) : null}
                          {/* Na lista de prazo a situacao e' a mesma em todas: vale a data. */}
                          {temPrazo ? (
                            <td className="whitespace-nowrap text-ink-muted">
                              {ehTravados ? rotuloDeadline(l.deadline_status) : (prazoCurtoBrt(l.dispatch_deadline) ?? "—")}
                            </td>
                          ) : null}
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
