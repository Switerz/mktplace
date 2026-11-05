"use client";

/**
 * EXPEDICAO-TIKTOK-ABERTOS — "quais pedidos do TikTok estao em aberto agora?"
 *
 * O mesmo modelo do "Risco atual" da Shopee e do Mercado Livre: vencidos em
 * destaque e, ao lado, vence hoje, vence no proximo dia util e acima de 48 h,
 * todos clicaveis (lista dos pedidos, quebra por marca e etapa, CSV). A tabela
 * por marca vira o "Backlog por marca", como nos outros canais
 * (EXPEDICAO-TIKTOK-PADRAO).
 *
 * Fonte: `/api/v1/expedicao/tiktok/abertos` (Data Mart ao vivo). O prazo e'
 * RECONSTRUIDO da politica (despacho 1 dia util, coleta 2 dias uteis), como
 * na LDR do TikTok — dito na descricao da secao e no rodape do popup.
 */
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import DrilldownSecao from "@/components/drilldown/DrilldownSecao";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import CartaoDeRisco from "@/components/expedicao/CartaoDeRisco";
import TikTokColetaPerdida, { BotaoCsvCompleto, baixarCsv } from "@/components/expedicao/TikTokColetaPerdida";
import { CLASSE_CABECALHO, CLASSE_TABELA, EstadoSecao } from "@/components/expedicao/LayoutExpedicao";
import RiscoEmDestaque from "@/components/expedicao/RiscoEmDestaque";
import { SecaoGerencial, SeloLeitura } from "@/components/expedicao/SecoesExpedicao";
import InfoDica from "@/components/ui/InfoDica";
import Panel from "@/components/ui/Panel";
import { formatarHoras, type Kpi } from "@/lib/expedicao-contract";
import { buscarExpedicao } from "@/lib/expedicao-demo";
import { formatarInteiro } from "@/lib/expedicao-formato";
import {
  CAMPO_DA_LISTA,
  CONFIG_LISTA,
  ROTULO_ETAPA,
  buscarTodasAsPaginas,
  csvDosAbertos,
  diaCurto,
  etapasDaLista,
  faixaDaPagina,
  instanteCurto,
  nomeDoCsvAbertos,
  ondeAgirNosAbertos,
  queryAbertos,
  type ListaAbertos,
  type RespostaAbertos,
} from "@/lib/expedicao-tiktok-abertos";
import { brandLabel } from "@/lib/filters/brands";

type Carga = { tipo: "carregando" } | { tipo: "erro" } | { tipo: "ok"; dados: RespostaAbertos };

function pct(n: number, base: number): string {
  return base > 0 ? `${((n / base) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%` : "—";
}

// ---------------------------------------------------------------------------
// Popup com a lista
// ---------------------------------------------------------------------------
function PopupAbertos({
  api,
  marca,
  lista,
  total,
  aoFechar,
}: {
  api: string;
  marca: string | null;
  lista: ListaAbertos | null;
  total: number;
  aoFechar: () => void;
}) {
  const [carga, setCarga] = useState<Carga>({ tipo: "carregando" });
  const [tentativa, setTentativa] = useState(0);
  const [pagina, setPagina] = useState(1);

  // Outra lista ou outra marca recomeca da primeira pagina.
  useEffect(() => setPagina(1), [marca, lista]);

  useEffect(() => {
    if (!lista) return;
    let vivo = true;
    setCarga({ tipo: "carregando" });
    buscarExpedicao(`${api}${queryAbertos(marca, lista, pagina)}`, { cache: "no-store" })
      .then(async (r) => {
        if (!r.ok) throw new Error(String(r.status));
        const dados = (await r.json()) as RespostaAbertos;
        if (vivo) setCarga(dados.orders_unavailable ? { tipo: "erro" } : { tipo: "ok", dados });
      })
      .catch(() => vivo && setCarga({ tipo: "erro" }));
    return () => {
      vivo = false;
    };
  }, [api, marca, lista, pagina, tentativa]);

  const cfg = lista ? CONFIG_LISTA[lista] : null;
  const pedidos = carga.tipo === "ok" ? carga.dados.orders : [];
  const dados = carga.tipo === "ok" ? carga.dados : null;
  const variasMarcas = !marca && (dados?.brands.length ?? 0) > 1;
  // Quebras EXATAS, do resumo que vem em toda pagina — nunca so' da pagina.
  const totalLista = dados && lista ? dados.list_total : total;
  const porMarca =
    dados && lista
      ? dados.brands
          .map((b) => ({ rotulo: brandLabel(b.brand), n: b[CAMPO_DA_LISTA[lista]] }))
          .filter((b) => b.n > 0)
          .sort((a, b) => b.n - a.n)
      : [];
  const porEtapa = dados && lista ? etapasDaLista(lista, dados.totals) : [];
  const temMaisDeUmaPagina = Boolean(dados && (dados.has_more || pagina > 1));

  return (
    <KpiDrilldownDialog open={Boolean(lista)} onClose={aoFechar} title={cfg?.titulo ?? ""} largura="larga" focusResetKey={lista ?? ""}>
      {lista && cfg ? (
        <div className="flex flex-col gap-4 text-sm" data-testid="exp-tiktok-abertos-popup">
          <div className="flex flex-wrap items-end gap-x-3 gap-y-1.5">
            <span className={`text-[32px] font-bold leading-none tracking-tight tabular-nums ${lista === "overdue" ? "text-crit-ink" : "text-ink"}`}>
              {formatarInteiro(total)}
            </span>
            <span className="pb-0.5 text-xs text-ink-faint">{marca ? brandLabel(marca) : "todas as marcas"}</span>
          </div>

          {carga.tipo === "carregando" ? (
            <p role="status" className="text-sm text-ink-muted">Carregando pedidos…</p>
          ) : carga.tipo === "erro" ? (
            <p role="status" className="text-sm text-crit-ink">
              Não foi possível carregar os pedidos.{" "}
              <button type="button" onClick={() => setTentativa((n) => n + 1)} className="font-semibold underline underline-offset-4">
                Tentar de novo
              </button>
            </p>
          ) : pedidos.length === 0 ? (
            <p role="status" className="text-sm text-ink-muted">{cfg.vazio}</p>
          ) : (
            <>
              {porEtapa.length > 1 ? (
                <DrilldownSecao titulo="Em que etapa estão">
                  <ul className="flex flex-col">
                    {porEtapa.map((i) => (
                      <EvidenceRow key={i.rotulo} label={i.rotulo} pct={(i.n / totalLista) * 100} barra="bg-warn"
                        value={`${formatarInteiro(i.n)} · ${pct(i.n, totalLista)}`} />
                    ))}
                  </ul>
                </DrilldownSecao>
              ) : null}
              {porMarca.length > 1 ? (
                <DrilldownSecao titulo="Por marca">
                  <ul className="flex flex-col">
                    {porMarca.map((i) => (
                      <EvidenceRow key={i.rotulo} label={i.rotulo} pct={(i.n / totalLista) * 100} barra="bg-warn"
                        value={`${formatarInteiro(i.n)} · ${pct(i.n, totalLista)}`} />
                    ))}
                  </ul>
                </DrilldownSecao>
              ) : null}
              <DrilldownSecao
                titulo="Pedidos"
                acao={
                  <>
                    <span className="tabular-nums">
                      {dados
                        ? `${faixaDaPagina(pagina, dados.page_size, pedidos.length, totalLista)} · prazo mais antigo primeiro`
                        : "prazo mais antigo primeiro"}
                    </span>
                    <BotaoCsvCompleto
                      total={totalLista}
                      testId="exp-tiktok-abertos-csv"
                      baixar={async (aoAvancar) => {
                        const todos = await buscarTodasAsPaginas(async (p) => {
                          const r = await buscarExpedicao(`${api}${queryAbertos(marca, lista, p)}`, { cache: "no-store" });
                          if (!r.ok) throw new Error(String(r.status));
                          const d = (await r.json()) as RespostaAbertos;
                          if (d.availability !== "available" || d.orders_unavailable) throw new Error("indisponivel");
                          return d;
                        }, aoAvancar);
                        baixarCsv(csvDosAbertos(todos), nomeDoCsvAbertos(lista, marca, new Date()));
                      }}
                    />
                  </>
                }
              >
                <div className="max-h-[50vh] overflow-auto rounded-lg border border-line">
                  <table className="w-full text-sm [&_td]:px-3 [&_td]:py-1.5 [&_th]:whitespace-nowrap [&_th]:px-3 [&_th]:py-1.5">
                    <caption className="sr-only">{cfg.titulo}, prazo mais antigo primeiro</caption>
                    <thead className="sticky top-0 bg-raised text-left text-xs uppercase tracking-wide text-ink-faint">
                      <tr>
                        <th scope="col">Pedido</th>
                        {variasMarcas ? <th scope="col">Marca</th> : null}
                        <th scope="col">Etapa</th>
                        <th scope="col">Pago em</th>
                        <th scope="col">Prazo</th>
                        <th scope="col" className="text-right">Aberto há</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pedidos.map((p) => (
                        <tr key={p.order_id} className="border-t border-line">
                          <td className="whitespace-nowrap font-mono text-xs text-ink">{p.order_id}</td>
                          {variasMarcas ? <td className="whitespace-nowrap text-ink">{brandLabel(p.brand)}</td> : null}
                          <td className="whitespace-nowrap font-medium text-ink">{ROTULO_ETAPA[p.stage] ?? p.stage}</td>
                          <td className="whitespace-nowrap tabular-nums text-ink-muted">{instanteCurto(p.paid_at)}</td>
                          <td className={`whitespace-nowrap tabular-nums ${p.situation === "overdue" ? "font-semibold text-crit-ink" : "text-ink-muted"}`}>
                            {diaCurto(p.deadline)}
                          </td>
                          <td className="whitespace-nowrap text-right font-semibold tabular-nums text-ink">{formatarHoras(p.hours_open)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {temMaisDeUmaPagina && dados ? (
                  <nav aria-label="Páginas da lista" className="mt-2 flex items-center justify-between gap-3" data-testid="exp-tiktok-abertos-paginas">
                    <button
                      type="button"
                      onClick={() => setPagina((n) => Math.max(1, n - 1))}
                      disabled={pagina <= 1}
                      className="inline-flex min-h-11 items-center gap-1 rounded-md border border-line bg-surface px-3 text-sm font-semibold text-ink hover:border-line-strong disabled:cursor-default disabled:opacity-40"
                    >
                      <ChevronLeft aria-hidden="true" className="h-4 w-4" />
                      Anterior
                    </button>
                    <span className="text-xs tabular-nums text-ink-muted" aria-live="polite">
                      Página {formatarInteiro(pagina)} de {formatarInteiro(Math.max(pagina, Math.ceil(totalLista / dados.page_size)))}
                    </span>
                    <button
                      type="button"
                      onClick={() => setPagina((n) => n + 1)}
                      disabled={!dados.has_more}
                      className="inline-flex min-h-11 items-center gap-1 rounded-md border border-line bg-surface px-3 text-sm font-semibold text-ink hover:border-line-strong disabled:cursor-default disabled:opacity-40"
                    >
                      Próxima
                      <ChevronRight aria-hidden="true" className="h-4 w-4" />
                    </button>
                  </nav>
                ) : null}
              </DrilldownSecao>
            </>
          )}
          <p className="text-xs text-ink-faint">
            Prazo reconstruído pela política do TikTok (envio até 1 dia útil e coleta até 2 dias úteis após o
            pagamento, com feriados nacionais); não é o SLA por pedido da plataforma.
          </p>
        </div>
      ) : null}
    </KpiDrilldownDialog>
  );
}

// ---------------------------------------------------------------------------
// Dados (uma busca, duas secoes)
// ---------------------------------------------------------------------------
export type AbertosTikTok = {
  carga: Carga;
  carregar: () => void;
  abrir: (lista: ListaAbertos, total: number, marca?: string | null) => void;
  /** O popup da lista, montado UMA vez pelo painel. */
  popup: ReactNode;
};

/**
 * EXPEDICAO-TIKTOK-PADRAO — os pedidos em aberto alimentam DUAS secoes do
 * esqueleto comum: o "Risco atual" (cartoes) e o "Backlog por marca"
 * (tabela). Uma busca so', um popup so'.
 */
export function useTikTokAbertos(api: string, marca: string | null): AbertosTikTok {
  const [carga, setCarga] = useState<Carga>({ tipo: "carregando" });
  const [aberta, setAberta] = useState<{ lista: ListaAbertos; total: number; marca: string | null } | null>(null);

  const carregar = useCallback(async () => {
    setCarga({ tipo: "carregando" });
    try {
      const r = await buscarExpedicao(`${api}${queryAbertos(marca)}`, { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      setCarga({ tipo: "ok", dados: (await r.json()) as RespostaAbertos });
    } catch {
      setCarga({ tipo: "erro" });
    }
  }, [api, marca]);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  return {
    carga,
    carregar: () => void carregar(),
    abrir: (lista, total, m = marca) => setAberta({ lista, total, marca: m }),
    popup: (
      <PopupAbertos
        api={api}
        marca={aberta?.marca ?? null}
        lista={aberta?.lista ?? null}
        total={aberta?.total ?? 0}
        aoFechar={() => setAberta(null)}
      />
    ),
  };
}

const DICA_ABERTOS =
  "Pedidos pagos nos últimos 30 dias que ainda não saíram: aguardando envio (sem NF-e, na mesa de embalo) ou " +
  "aguardando coleta (embalado, com NF-e). Prazo reconstruído pela política do TikTok: envio até 1 dia útil e " +
  "coleta até 2 dias úteis após o pagamento.";

function EstadoAbertos({ abertos }: { abertos: AbertosTikTok }) {
  const carregando = abertos.carga.tipo === "carregando";
  return (
    <EstadoSecao
      tipo={carregando ? "carregando" : "erro"}
      titulo={carregando ? "Carregando os pedidos em aberto…" : "Não foi possível carregar os pedidos em aberto do TikTok."}
      aoTentarDeNovo={carregando ? undefined : abertos.carregar}
    />
  );
}

function prontos(abertos: AbertosTikTok): RespostaAbertos | null {
  return abertos.carga.tipo === "ok" && abertos.carga.dados.availability === "available" ? abertos.carga.dados : null;
}

// ---------------------------------------------------------------------------
// Risco atual — o MESMO desenho da Shopee e do ML
// ---------------------------------------------------------------------------
export function RiscoAtualTikTok({ abertos, children }: { abertos: AbertosTikTok; children?: ReactNode }) {
  const d = prontos(abertos);
  if (!d) {
    return (
      <SecaoGerencial titulo="Risco atual" dica={DICA_ABERTOS} selo={<SeloLeitura>agora</SeloLeitura>}>
        <EstadoAbertos abertos={abertos} />
        {children}
      </SecaoGerencial>
    );
  }
  const t = d.totals;
  const proximo = diaCurto(d.next_business_day);
  const principal: Kpi = { chave: "overdue", rotulo: "Vencidos", valor: t.overdue, alerta: t.overdue > 0 };
  const backlog: Kpi = { chave: "backlog", rotulo: "Em aberto", valor: t.open, alerta: false };
  const etapas =
    t.overdue > 0 ? (
      <p className="text-sm text-ink-muted">
        {formatarInteiro(t.overdue_collection)} aguardando coleta · {formatarInteiro(t.overdue_shipment)} aguardando envio
      </p>
    ) : undefined;
  return (
    <SecaoGerencial titulo="Risco atual" dica={DICA_ABERTOS} selo={<SeloLeitura>agora</SeloLeitura>}>
      <div data-testid="exp-tiktok-abertos">
        <RiscoEmDestaque
          principal={principal}
          backlog={backlog}
          prazo={etapas}
          ondeAgir={ondeAgirNosAbertos(t, d.brands, brandLabel)}
          aoAbrir={t.overdue > 0 ? () => abertos.abrir("overdue", t.overdue) : undefined}
          secundarios={
            <>
              <CartaoDeRisco
                chave="due24h"
                rotulo="Vence hoje"
                valor={formatarInteiro(t.due_today)}
                detalhe={`${pct(t.due_today, t.open)} do total`}
                tom={t.due_today > 0 ? "atencao" : "neutro"}
                aoAbrir={t.due_today > 0 ? () => abertos.abrir("due_today", t.due_today) : undefined}
                rotuloAbrir={`Ver os ${formatarInteiro(t.due_today)} pedidos do TikTok que vencem hoje`}
              />
              <CartaoDeRisco
                chave="due_next_day"
                rotulo={`Vence ${proximo}`}
                valor={formatarInteiro(t.due_next_day)}
                detalhe="próximo dia útil"
                aoAbrir={t.due_next_day > 0 ? () => abertos.abrir("due_next_day", t.due_next_day) : undefined}
                rotuloAbrir={`Ver os ${formatarInteiro(t.due_next_day)} pedidos do TikTok que vencem ${proximo}`}
              />
              <CartaoDeRisco
                chave="over48h"
                rotulo="Acima de 48 h"
                valor={formatarInteiro(t.over_48h)}
                detalhe={`${pct(t.over_48h, t.open)} do total`}
                tom={t.over_48h > 0 ? "atencao" : "neutro"}
                aoAbrir={t.over_48h > 0 ? () => abertos.abrir("over_48h", t.over_48h) : undefined}
                rotuloAbrir={`Ver os ${formatarInteiro(t.over_48h)} pedidos do TikTok abertos há mais de 48 h`}
              />
            </>
          }
        />
      </div>
      {children}
    </SecaoGerencial>
  );
}

// ---------------------------------------------------------------------------
// Backlog por marca — o MESMO titulo e o mesmo painel da Shopee e do ML
// ---------------------------------------------------------------------------
export function BacklogPorMarcaTikTok({
  abertos,
  api,
  marca,
}: {
  abertos: AbertosTikTok;
  api: string;
  marca: string | null;
}) {
  const d = prontos(abertos);
  if (!d) {
    return (
      <Panel titulo="Backlog por marca">
        <EstadoAbertos abertos={abertos} />
      </Panel>
    );
  }
  const t = d.totals;
  const proximo = diaCurto(d.next_business_day);
  return (
    <Panel
      semPadding
      titulo="Backlog por marca"
      acao={<InfoDica texto={DICA_ABERTOS} />}
      descricao={
        <>
          {formatarInteiro(t.open)} em aberto · {formatarInteiro(t.awaiting_shipment)} aguardando envio ·{" "}
          {formatarInteiro(t.awaiting_collection)} aguardando coleta
          {t.on_hold > 0 ? ` · ${formatarInteiro(t.on_hold)} retidos pelo TikTok` : ""}
        </>
      }
      // EXPEDICAO-TIKTOK-COLETA-PERDIDA: por dia de prazo, inclusive os ja' coletados depois.
      rodape={<TikTokColetaPerdida api={api} marca={marca} embutida />}
    >
      {d.brands.length ? (
        <div className="overflow-x-auto">
          <table className={CLASSE_TABELA}>
            <caption className="sr-only">Pedidos do TikTok em aberto por marca</caption>
            <thead className={CLASSE_CABECALHO}>
              <tr>
                <th scope="col">Marca</th>
                <th scope="col" className="text-right">Em aberto</th>
                <th scope="col" className="text-right">Ag. envio</th>
                <th scope="col" className="text-right">Ag. coleta</th>
                <th scope="col" className="text-right">Vencidos</th>
                <th scope="col" className="text-right">Vence hoje</th>
                <th scope="col" className="text-right">Vence {proximo}</th>
                <th scope="col" className="text-right">Acima de 48 h</th>
              </tr>
            </thead>
            <tbody>
              {d.brands.map((b) => {
                const celula = (lista: ListaAbertos, n: number, cor = "text-ink") =>
                  n > 0 ? (
                    <button
                      type="button"
                      onClick={() => abertos.abrir(lista, n, b.brand)}
                      className={`tabular-nums underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${cor}`}
                      aria-label={`Ver ${formatarInteiro(n)} pedidos (${CONFIG_LISTA[lista].titulo}) de ${brandLabel(b.brand)}`}
                    >
                      {formatarInteiro(n)}
                    </button>
                  ) : (
                    <span className="tabular-nums text-ink-faint">0</span>
                  );
                return (
                  <tr key={b.brand} className="border-t border-line hover:bg-raised/60">
                    <th scope="row" className="text-left font-semibold text-ink">{brandLabel(b.brand)}</th>
                    <td className="text-right">{celula("all", b.open)}</td>
                    <td className="text-right">{celula("awaiting_shipment", b.awaiting_shipment)}</td>
                    <td className="text-right">{celula("awaiting_collection", b.awaiting_collection)}</td>
                    <td className="text-right font-semibold">{celula("overdue", b.overdue, "text-crit-ink")}</td>
                    <td className="text-right">{celula("due_today", b.due_today, "text-warn-ink")}</td>
                    <td className="text-right">{celula("due_next_day", b.due_next_day)}</td>
                    <td className="text-right">{celula("over_48h", b.over_48h)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p role="status" className="px-4 py-3 text-sm text-ink-muted">
          Nenhum pedido do TikTok em aberto neste recorte.
        </p>
      )}
    </Panel>
  );
}
