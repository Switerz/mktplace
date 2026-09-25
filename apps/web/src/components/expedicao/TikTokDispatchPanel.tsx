"use client";

/**
 * Gate EXP-TK-OPS-1/2 — painel da LDR do TikTok Shop.
 * Retematizado no UX-TORRE-1.
 *
 * Componente PROPRIO, e nao um ramo dentro do `ExpedicaoClient`. Aquele serve
 * dois canais ja em producao e tem 43 KB; o TikTok tem outra forma de dado
 * (coorte por prazo, nao fila paginada), outros estados e outra pergunta. Um
 * `if (canal === "tiktokshop")` espalhado por ele poria Shopee e Mercado Livre
 * em risco a cada ajuste aqui.
 *
 * A tela responde, nesta ordem:
 *   1. estamos dentro da regua do TikTok?  -> cartao da LDR contra a meta de 4%
 *   2. quando o atraso comecou?            -> faixa do incidente + grafico
 *   3. quantos pedidos afetados?           -> cartoes de contagem
 *   4. quais lojas concentram?             -> tabela por marca
 *   5. quantos ainda da' para tratar?      -> cartao "ainda no prazo"
 *
 * As duas leituras aparecem SEPARADAS e NOMEADAS: a LDR (denominador = quem
 * vence no periodo) e o fluxo por data de pagamento (denominador = quem pagou
 * no dia). A segunda e' a visao que a gestao usa; usa-la como se fosse a LDR
 * inflaria a taxa toda vez que um feriado empurrasse tres dias de pagamento
 * para o mesmo vencimento.
 *
 * UX-TORRE-1 — O `dark:` ORFAO
 * ----------------------------
 * Este arquivo era o UNICO da Torre com classes `dark:*`, numa aplicacao sem
 * tema escuro global: quando o sistema operacional pedia escuro, so' este
 * painel ficava preto dentro de uma tela clara. Isso nunca foi significado de
 * negocio — era inconsistencia visual. Os pares `x dark:y` foram trocados pelo
 * token semantico correspondente, que ja' muda de valor com o tema. Nenhuma
 * severidade mudou de cor: `critico` continua sendo o papel critico.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import Chip from "@/components/ui/Chip";
import Disclosure from "@/components/ui/Disclosure";
import KpiTile from "@/components/ui/KpiTile";
import Note from "@/components/ui/Note";
import PageBar from "@/components/ui/PageBar";
import Panel from "@/components/ui/Panel";
import Segmented from "@/components/ui/Segmented";
import SortHeader from "@/components/ui/SortHeader";
import type { Tom } from "@/components/ui/tone";
import { useSortableTable, type SortColumnType } from "@/lib/use-sortable-table";

import {
  AVISO_PRAZO_RECONSTRUIDO,
  EVENTOS,
  EVENTO_PADRAO,
  EXPLICACAO_EVENTO,
  EXPLICACAO_FLUXO,
  EXPLICACAO_LDR,
  EXPLICACAO_META,
  JANELAS,
  JANELA_INICIAL_DIAS,
  META_TIKTOK_LDR,
  ROTULO_EVENTO,
  ROTULO_LIMIAR_INTERNO,
  ROTULO_META,
  ROTULO_SEVERIDADE,
  SELO_ACIMA_DA_META,
  SELO_DENTRO_DA_META,
  TITULO_FLUXO,
  TITULO_LDR,
  VALOR_SEM_TAXA,
  alturaDaBarra,
  alturaDaMeta,
  contarCriticos,
  contarForaDaMeta,
  descreverIncidente,
  explicarIndisponivel,
  formatarDiaCurto,
  formatarDiaLongo,
  formatarInteiro,
  formatarTaxa,
  marcasForaDaMeta,
  queryDoTikTok,
  severidadeDoDia,
  taxaMaxima,
  type Evento,
  type Severidade,
  type TikTokLdrDia,
  type TikTokMarca,
  type TikTokPayload,
} from "@/lib/expedicao-tiktok-contract";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

/**
 * Severidade -> token. `parcial` continua HACHURADO de proposito: coorte
 * parcial nao pode parecer um resultado fechado, nem bom nem ruim.
 */
const COR_SEVERIDADE: Record<Severidade, string> = {
  ok: "bg-ok",
  fora_da_meta: "bg-warn",
  critico: "bg-crit",
  parcial:
    "bg-[repeating-linear-gradient(45deg,rgb(var(--tc-line-strong))_0px,rgb(var(--tc-line-strong))_4px,transparent_4px,transparent_8px)]",
};

const TEXTO_SEVERIDADE: Record<Severidade, string> = {
  ok: "text-ok-ink",
  fora_da_meta: "text-warn-ink",
  critico: "text-crit-ink",
  parcial: "text-ink-faint",
};

const CLASSE_TABELA =
  "w-full text-[13px] [&_td]:py-1.5 [&_td]:px-3 [&_th]:py-1.5 [&_th]:px-3";
const CLASSE_CABECALHO =
  "sticky top-0 z-10 bg-raised text-left text-xs uppercase tracking-wide text-ink-faint";

const COLUNAS_MARCA: Record<string, SortColumnType> = {
  loja: "text",
  base: "numeric",
  late: "numeric",
  rate: "numeric",
};

function valorDaMarca(m: TikTokMarca, coluna: string): string | number | null {
  if (coluna === "loja") return m.shop_name ?? m.brand;
  if (coluna === "base") return m.base;
  if (coluna === "late") return m.late;
  return m.rate;
}

// ---------------------------------------------------------------------------
// Grafico
// ---------------------------------------------------------------------------
function Grafico({ dias }: { dias: TikTokLdrDia[] }) {
  const maximo = taxaMaxima(dias);
  const meta = alturaDaMeta(maximo);
  if (!dias.length) return null;
  return (
    <div>
      {/* `items-stretch` e nao `items-end`: com `items-end` cada coluna e'
          dimensionada pelo CONTEUDO, o `flex-1` de dentro nao recebe altura e
          a barra desaparece — o grafico renderiza numeros e datas sobre um
          retangulo vazio. Ja aconteceu neste componente. */}
      <div className="relative">
        {meta !== null ? (
          <div
            className="pointer-events-none absolute inset-x-0 z-10 border-t border-dashed border-warn"
            style={{ bottom: `calc(${meta}% + 1.25rem)` }}
            aria-hidden="true"
          >
            <span className="absolute -top-4 right-0 rounded bg-warn-soft px-1.5 py-0.5 text-xs font-medium text-warn-ink ring-1 ring-inset ring-warn/30">
              {ROTULO_META} {formatarTaxa(META_TIKTOK_LDR, 0)}
            </span>
          </div>
        ) : null}
        <div
          className="flex h-56 items-stretch gap-1 overflow-x-auto pb-1"
          role="img"
          aria-label={`Taxa por dia de vencimento. Máximo da série: ${formatarTaxa(
            maximo,
          )}. Referência do TikTok: ${formatarTaxa(META_TIKTOK_LDR, 0)} (medição interna).`}
        >
          {dias.map((d) => {
            const sev = severidadeDoDia(d);
            return (
              <div
                key={d.due_date}
                className="flex min-w-[38px] flex-1 flex-col items-center gap-1"
              >
                <span className={`text-xs tabular-nums ${TEXTO_SEVERIDADE[sev]}`}>
                  {d.rate === null ? VALOR_SEM_TAXA : `${Math.round(d.rate * 100)}`}
                </span>
                <div className="flex w-full flex-1 items-end">
                  <div
                    className={`w-full rounded-t ${COR_SEVERIDADE[sev]}`}
                    style={{ height: `${alturaDaBarra(d, maximo)}%` }}
                    title={`${formatarDiaLongo(d.due_date)} · ${formatarTaxa(d.rate)} · ${
                      ROTULO_SEVERIDADE[sev]
                    }`}
                  />
                </div>
                <span className="text-xs tabular-nums text-ink-faint">
                  {formatarDiaCurto(d.due_date)}
                </span>
              </div>
            );
          })}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
        {(["ok", "fora_da_meta", "critico", "parcial"] as Severidade[]).map((s) => (
          <span key={s} className="inline-flex items-center gap-1.5">
            <span className={`inline-block h-2.5 w-2.5 rounded-sm ${COR_SEVERIDADE[s]}`} />
            {ROTULO_SEVERIDADE[s]}
          </span>
        ))}
        <span className="text-ink-faint">Números em % dos pedidos que venciam no dia.</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Painel
// ---------------------------------------------------------------------------
export default function TikTokDispatchPanel({ abas }: { abas?: ReactNode }) {
  const [evento, setEvento] = useState<Evento>(EVENTO_PADRAO);
  const [dias, setDias] = useState<number>(JANELA_INICIAL_DIAS);
  const [dados, setDados] = useState<TikTokPayload | null>(null);
  const [estado, setEstado] = useState<"carregando" | "ok" | "erro">("carregando");
  const [erro, setErro] = useState<string>("");

  const url = useMemo(() => `${BASE}${queryDoTikTok(evento, dias)}`, [evento, dias]);

  const carregar = useCallback(async () => {
    setEstado("carregando");
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setDados((await r.json()) as TikTokPayload);
      setEstado("ok");
    } catch (e) {
      setErro(e instanceof Error ? e.message : "falha de rede");
      setEstado("erro");
    }
  }, [url]);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  const ldr = dados?.ldr ?? null;
  const fluxo = dados?.payment_flow ?? [];
  const foraDaMeta = ldr ? contarForaDaMeta(ldr.daily) : 0;
  const criticos = ldr ? contarCriticos(ldr.daily) : 0;
  const incidente = descreverIncidente(ldr);
  const piores = marcasForaDaMeta(dados?.brands ?? []);
  const sla = dados?.sla_business_days?.[evento];

  // Ordenacao client-side sobre `brands`, que vem INTEIRO no payload. Nao ha
  // paginacao aqui, entao ordenar nao esconde linha nenhuma nem recalcula
  // metrica: as taxas continuam sendo as que a API publicou.
  const { sort, toggleSort, sortedRows } = useSortableTable<TikTokMarca>(
    dados?.brands ?? [],
    valorDaMarca,
    COLUNAS_MARCA,
  );

  return (
    <div
      className="mx-auto flex max-w-[1400px] flex-col gap-3 p-3 sm:p-4"
      aria-busy={estado === "carregando"}
    >
      <PageBar
        titulo={
          <>
            <h2 id="tk-titulo" className="text-base font-semibold tracking-tight text-ink">
              Expedição — TikTok Shop
            </h2>
            {abas}
          </>
        }
        controles={
          <div className="flex flex-wrap items-center gap-2">
            <span id="tk-evento" className="text-xs text-ink-faint">
              Evento
            </span>
            <Segmented
              rotulo="Evento"
              rotuladoPor="tk-evento"
              opcoes={EVENTOS.map((e) => ({ valor: e, texto: ROTULO_EVENTO[e] }))}
              valor={evento}
              aoEscolher={setEvento}
            />
            <span id="tk-janela" className="text-xs text-ink-faint">
              Janela
            </span>
            <Segmented
              rotulo="Janela"
              rotuladoPor="tk-janela"
              opcoes={JANELAS.map((j) => ({ valor: String(j.dias), texto: j.rotulo }))}
              valor={String(dias)}
              aoEscolher={(v) => setDias(Number(v))}
            />
          </div>
        }
        meta={
          dados?.snapshot ? (
            <Chip title="Instante da última publicação da série">
              Atualizado em{" "}
              <time dateTime={dados.snapshot.effective_at}>
                {new Date(dados.snapshot.effective_at).toLocaleString("pt-BR")}
              </time>
            </Chip>
          ) : undefined
        }
      />

      {/* O aviso do prazo vem ANTES de qualquer numero, sempre. Ele encolheu
          para UMA linha, mas continua sendo a primeira coisa depois do
          cabecalho e segue colado a metrica que ele qualifica. */}
      {dados?.deadline_is_reconstructed ? (
        <Note tom="atencao" papel="note">
          {AVISO_PRAZO_RECONSTRUIDO}
        </Note>
      ) : null}

      {estado === "carregando" ? (
        <p role="status" className="py-8 text-center text-sm text-ink-muted">
          Carregando a série…
        </p>
      ) : null}

      {estado === "erro" ? (
        <Note
          tom="critico"
          papel="alert"
          acao={
            <button
              type="button"
              onClick={() => void carregar()}
              className="min-h-11 rounded-md border border-crit/40 px-3 text-xs font-semibold"
            >
              Tentar de novo
            </button>
          }
        >
          Não foi possível carregar a série ({erro}).
        </Note>
      ) : null}

      {estado === "ok" && dados?.availability === "unavailable" ? (
        <div className="rounded-xl border border-line bg-surface p-6 text-center">
          <p className="text-sm text-ink">{explicarIndisponivel(dados.unavailable_reason)}</p>
          <p className="mx-auto mt-1 max-w-prose text-xs text-ink-muted">
            A tela não mostra 0% enquanto não houver série: zero aqui seria lido
            como “nenhum atraso”, que é diferente de “sem medição”.
          </p>
        </div>
      ) : null}

      {estado === "ok" && dados?.availability === "available" && ldr ? (
        <>
          {dados.alerts.length ? (
            <ul className="flex flex-col gap-1.5">
              {dados.alerts.map((a) => (
                <li key={a.code}>
                  <Note
                    papel={a.severity === "critical" ? "alert" : "status"}
                    tom={
                      (a.severity === "critical"
                        ? "critico"
                        : a.severity === "warning"
                          ? "atencao"
                          : "bom") as Tom
                    }
                  >
                    {a.message}
                  </Note>
                </li>
              ))}
            </ul>
          ) : null}

          {/* 1. A REGUA ------------------------------------------------- */}
          <section aria-labelledby="tk-ldr-titulo" className="flex flex-col gap-2">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <h3 id="tk-ldr-titulo" className="text-sm font-semibold text-ink">
                {TITULO_LDR}
              </h3>
              {/* A ressalva fica COLADA no selo, nunca atras de um tooltip: o
                  numero sai desta tela para reuniao, e sem ela vira "o TikTok
                  nos penalizou". O texto e' o mesmo; deixou de ocupar dois
                  paragrafos antes dos cartoes. */}
              <Disclosure rotulo="Como ler esta medição">
                <p>
                  {EXPLICACAO_LDR}
                  {sla ? ` Prazo do evento: ${sla} dia(s) útil(eis).` : ""}
                </p>
                <p className="mt-1.5">{EXPLICACAO_META}</p>
                <p className="mt-1.5">{EXPLICACAO_EVENTO[evento]}</p>
              </Disclosure>
            </div>
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
              <KpiTile
                rotulo="LDR da janela"
                valor={formatarTaxa(ldr.rate)}
                destaque
                comparacao={
                  ldr.rate === null
                    ? "Sem vencimento maduro na janela."
                    : `${ROTULO_META}: ${formatarTaxa(META_TIKTOK_LDR, 0)} · ${
                        ldr.above_target ? SELO_ACIMA_DA_META : SELO_DENTRO_DA_META
                      }`
                }
                tom={ldr.above_target ? "critico" : "bom"}
              />
              <KpiTile
                rotulo="Venciam no período"
                valor={formatarInteiro(ldr.base)}
                destaque
                comparacao={`${ldr.mature_due_days} dia(s) fechado(s), ${ldr.partial_due_days} ainda no prazo.`}
              />
              <KpiTile
                rotulo="Já atrasaram"
                valor={formatarInteiro(ldr.late)}
                destaque
                comparacao={`${formatarInteiro(ldr.pending_overdue)} venceram e ainda não saíram.`}
                tom={ldr.late > 0 ? "critico" : "bom"}
              />
              <KpiTile
                rotulo="Ainda dá para tratar"
                valor={formatarInteiro(ldr.pending_at_risk)}
                destaque
                comparacao={`De ${formatarInteiro(
                  ldr.pending_on_time,
                )} pendentes no prazo, estes vencem hoje ou amanhã.`}
                tom={ldr.pending_at_risk > 0 ? "atencao" : "neutro"}
              />
            </div>
          </section>

          {/* 2. QUANDO COMECOU ----------------------------------------- */}
          <Panel
            titulo="Evolução por dia de vencimento"
            descricao={
              foraDaMeta > 0
                ? `${foraDaMeta} dia(s) acima da meta, ${criticos} acima do ${ROTULO_LIMIAR_INTERNO.toLowerCase()}.${
                    incidente ? ` ${incidente}` : ""
                  }`
                : "Nenhum dia acima da meta na janela."
            }
          >
            <Grafico dias={ldr.daily} />
          </Panel>

          {/* 3. ONDE CONCENTRA + A VISAO AUXILIAR ---------------------- */}
          <div className="grid gap-3 lg:grid-cols-5">
            <Panel
              className="lg:col-span-2"
              semPadding
              titulo={
                <span className="flex items-center gap-2">
                  Por loja
                  {piores.length ? (
                    <Chip tom="critico" ponto>
                      {piores.length} acima da meta
                    </Chip>
                  ) : null}
                </span>
              }
              rodape="Só lojas com base suficiente no período. Vencimentos ainda no prazo ficam de fora."
            >
              <div className="max-h-96 overflow-auto">
                <table className={CLASSE_TABELA}>
                  <caption className="sr-only">LDR por loja no período</caption>
                  <thead className={CLASSE_CABECALHO}>
                    <tr>
                      <SortHeader
                        rotulo="Loja"
                        coluna="loja"
                        sort={sort}
                        aoOrdenar={toggleSort}
                        alinhamento="left"
                      />
                      <SortHeader rotulo="Venciam" coluna="base" sort={sort} aoOrdenar={toggleSort} />
                      <SortHeader rotulo="Atrasados" coluna="late" sort={sort} aoOrdenar={toggleSort} />
                      <SortHeader rotulo="LDR" coluna="rate" sort={sort} aoOrdenar={toggleSort} />
                    </tr>
                  </thead>
                  <tbody>
                    {sortedRows.map((m) => (
                      <tr key={m.brand} className="border-t border-line">
                        <th scope="row" className="text-left font-medium text-ink">
                          {m.shop_name ?? m.brand}
                        </th>
                        <td className="text-right tabular-nums text-ink">
                          {formatarInteiro(m.base)}
                        </td>
                        <td className="text-right tabular-nums text-ink">
                          {formatarInteiro(m.late)}
                        </td>
                        <td
                          className={`text-right font-semibold tabular-nums ${
                            m.above_target ? "text-crit-ink" : "text-ok-ink"
                          }`}
                        >
                          {formatarTaxa(m.rate)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>

            <Panel
              className="lg:col-span-3"
              semPadding
              titulo={
                <span className="flex flex-wrap items-center gap-2">
                  {TITULO_FLUXO}
                  {/* Nomeado como VISAO AUXILIAR na propria etiqueta: quem le
                      de relance nao pode confundir este denominador com o da
                      LDR. */}
                  <Chip>visão auxiliar · não é a LDR</Chip>
                </span>
              }
              acao={<Disclosure rotulo="Como ler">{EXPLICACAO_FLUXO}</Disclosure>}
            >
              <div className="max-h-96 overflow-auto">
                <table className={CLASSE_TABELA}>
                  <caption className="sr-only">
                    Fluxo por data de pagamento — denominador diferente do da LDR
                  </caption>
                  <thead className={CLASSE_CABECALHO}>
                    <tr>
                      <th scope="col" className="font-semibold">Pago em</th>
                      <th scope="col" className="text-right font-semibold">Pagos</th>
                      <th scope="col" className="text-right font-semibold">No prazo</th>
                      <th scope="col" className="text-right font-semibold">Atrasados</th>
                      <th scope="col" className="text-right font-semibold">Vencidos s/ envio</th>
                      <th scope="col" className="text-right font-semibold">No prazo s/ envio</th>
                      <th scope="col" className="text-right font-semibold">Taxa</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fluxo.map((f) => (
                      <tr key={f.paid_date} className="border-t border-line">
                        <th scope="row" className="whitespace-nowrap text-left font-medium text-ink">
                          {formatarDiaLongo(f.paid_date)}
                          {!f.is_mature ? (
                            <span className="ml-1.5 rounded bg-raised px-1.5 py-0.5 text-xs text-ink-faint ring-1 ring-inset ring-line">
                              parcial
                            </span>
                          ) : null}
                        </th>
                        <td className="text-right tabular-nums text-ink">
                          {formatarInteiro(f.paid_orders)}
                        </td>
                        <td className="text-right tabular-nums text-ink">
                          {formatarInteiro(f.shipped_on_time)}
                        </td>
                        <td className="text-right tabular-nums text-ink">
                          {formatarInteiro(f.shipped_late)}
                        </td>
                        <td className="text-right tabular-nums text-ink">
                          {formatarInteiro(f.pending_overdue)}
                        </td>
                        <td className="text-right tabular-nums text-ink">
                          {formatarInteiro(f.pending_on_time)}
                        </td>
                        <td
                          className={`text-right font-semibold tabular-nums ${
                            !f.is_mature
                              ? "text-ink-faint"
                              : (f.rate ?? 0) > META_TIKTOK_LDR
                                ? "text-crit-ink"
                                : "text-ok-ink"
                          }`}
                        >
                          {/* Coorte parcial NUNCA mostra a taxa fechada: ela
                              ainda vai mudar. */}
                          {f.is_mature ? formatarTaxa(f.rate) : VALOR_SEM_TAXA}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          </div>
        </>
      ) : null}
    </div>
  );
}
