"use client";

/**
 * Gate EXP-TK-OPS-1 — painel de atraso de despacho do TikTok Shop.
 *
 * Componente PROPRIO, e nao um ramo dentro do `ExpedicaoClient`. Aquele
 * componente serve dois canais ja em producao e tem 43 KB; o TikTok tem outra
 * forma de dado (serie por coorte, nao fila paginada), outros estados e outra
 * pergunta. Um `if (canal === "tiktokshop")` espalhado por ele poria Shopee e
 * Mercado Livre em risco a cada ajuste aqui.
 *
 * A tela responde, em ordem, as cinco perguntas da operacao:
 *   1. estamos dentro da regua?      -> cartao da taxa da janela
 *   2. quando o atraso comecou?      -> faixa do incidente + grafico
 *   3. quantos pedidos afetados?     -> cartoes de contagem
 *   4. quais lojas concentram?       -> tabela por marca
 *   5. quantos ainda da' para tratar -> cartao "ainda no prazo"
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  AVISO_PRAZO_RECONSTRUIDO,
  EVENTOS,
  EVENTO_PADRAO,
  EXPLICACAO_EVENTO,
  JANELAS,
  JANELA_INICIAL_DIAS,
  ROTULO_EVENTO,
  ROTULO_SEVERIDADE,
  VALOR_SEM_TAXA,
  alturaDaBarra,
  contarDiasCriticos,
  descreverIncidente,
  divergenciaEmPp,
  explicarIndisponivel,
  formatarDiaCurto,
  formatarDiaLongo,
  formatarInteiro,
  formatarTaxa,
  marcasCriticas,
  queryDoTikTok,
  severidadeDoDia,
  taxaMaxima,
  type Evento,
  type Severidade,
  type TikTokDia,
  type TikTokPayload,
} from "@/lib/expedicao-tiktok-contract";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

const COR_SEVERIDADE: Record<Severidade, string> = {
  ok: "bg-emerald-500",
  atencao: "bg-amber-500",
  critico: "bg-rose-600",
  // Hachurado de proposito: coorte parcial nao pode parecer um resultado
  // fechado, nem bom nem ruim.
  parcial:
    "bg-[repeating-linear-gradient(45deg,theme(colors.slate.400)_0px,theme(colors.slate.400)_4px,transparent_4px,transparent_8px)]",
};

const TEXTO_SEVERIDADE: Record<Severidade, string> = {
  ok: "text-emerald-700 dark:text-emerald-400",
  atencao: "text-amber-700 dark:text-amber-400",
  critico: "text-rose-700 dark:text-rose-400",
  parcial: "text-slate-500 dark:text-slate-400",
};

// ---------------------------------------------------------------------------
// Blocos
// ---------------------------------------------------------------------------
function Cartao({
  titulo,
  valor,
  detalhe,
  tom = "neutro",
}: {
  titulo: string;
  valor: string;
  detalhe?: string;
  tom?: "neutro" | "critico" | "alerta" | "bom";
}) {
  const borda = {
    neutro: "border-slate-200 dark:border-slate-700",
    critico: "border-rose-300 dark:border-rose-800",
    alerta: "border-amber-300 dark:border-amber-800",
    bom: "border-emerald-300 dark:border-emerald-800",
  }[tom];
  return (
    <div className={`rounded-lg border ${borda} bg-white p-4 dark:bg-slate-900`}>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {titulo}
      </p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">
        {valor}
      </p>
      {detalhe ? (
        <p className="mt-1 text-xs leading-snug text-slate-500 dark:text-slate-400">
          {detalhe}
        </p>
      ) : null}
    </div>
  );
}

function GrupoRadio<T extends string>({
  rotulo,
  opcoes,
  valor,
  aoMudar,
  idBase,
}: {
  rotulo: string;
  opcoes: readonly { valor: T; texto: string }[];
  valor: T;
  aoMudar: (v: T) => void;
  idBase: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  // Roving tabindex + setas: o grupo inteiro e' UMA parada de tabulacao, como
  // manda o padrao de radiogroup. Sem isto, cada opcao vira uma parada e a
  // navegacao por teclado fica insuportavel quando ha varios grupos.
  const aoTeclar = (e: React.KeyboardEvent, i: number) => {
    const n = opcoes.length;
    let alvo = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") alvo = (i + 1) % n;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") alvo = (i - 1 + n) % n;
    else if (e.key === "Home") alvo = 0;
    else if (e.key === "End") alvo = n - 1;
    if (alvo < 0) return;
    e.preventDefault();
    aoMudar(opcoes[alvo].valor);
    refs.current[alvo]?.focus();
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span id={idBase} className="text-xs text-slate-500 dark:text-slate-400">
        {rotulo}
      </span>
      <div role="radiogroup" aria-labelledby={idBase} className="flex flex-wrap gap-1">
        {opcoes.map((o, i) => {
          const ativo = o.valor === valor;
          return (
            <button
              key={o.valor}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="radio"
              aria-checked={ativo}
              tabIndex={ativo ? 0 : -1}
              onKeyDown={(e) => aoTeclar(e, i)}
              onClick={() => aoMudar(o.valor)}
              className={`rounded-md border px-3 py-1.5 text-sm transition ${
                ativo
                  ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900"
                  : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200"
              }`}
            >
              {o.texto}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function Grafico({ dias }: { dias: TikTokDia[] }) {
  const maximo = taxaMaxima(dias);
  if (!dias.length) return null;
  return (
    <div>
      {/* `items-stretch` e nao `items-end`: com `items-end` cada coluna e'
          dimensionada pelo CONTEUDO, o `h-full` de dentro resolve para zero e
          a barra (`height: X%`) desaparece — o grafico renderiza numeros e
          datas sobre um retangulo vazio. A coluna precisa herdar a altura do
          trilho, e a area da barra precisa de `flex-1` para receber o espaco
          que sobra entre o rotulo de cima e a data de baixo. */}
      <div
        className="flex h-48 items-stretch gap-1 overflow-x-auto pb-1"
        role="img"
        aria-label={`Taxa por dia de pagamento. Máximo da série: ${formatarTaxa(maximo)}.`}
      >
        {dias.map((d) => {
          const sev = severidadeDoDia(d);
          return (
            <div
              key={d.paid_date}
              className="flex min-w-[38px] flex-1 flex-col items-center gap-1"
            >
              <span className={`text-xs tabular-nums ${TEXTO_SEVERIDADE[sev]}`}>
                {d.rate === null ? VALOR_SEM_TAXA : `${Math.round(d.rate * 100)}`}
              </span>
              <div className="flex w-full flex-1 items-end">
                <div
                  className={`w-full rounded-t ${COR_SEVERIDADE[sev]}`}
                  style={{ height: `${alturaDaBarra(d, maximo)}%` }}
                  title={`${formatarDiaLongo(d.paid_date)} · ${formatarTaxa(d.rate)} · ${
                    ROTULO_SEVERIDADE[sev]
                  }`}
                />
              </div>
              <span className="text-xs tabular-nums text-slate-500 dark:text-slate-400">
                {formatarDiaCurto(d.paid_date)}
              </span>
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-500 dark:text-slate-400">
        {(["ok", "atencao", "critico", "parcial"] as Severidade[]).map((s) => (
          <span key={s} className="inline-flex items-center gap-1.5">
            <span className={`inline-block h-2.5 w-2.5 rounded-sm ${COR_SEVERIDADE[s]}`} />
            {ROTULO_SEVERIDADE[s]}
          </span>
        ))}
        <span>Números em % de pedidos pagos do dia.</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Painel
// ---------------------------------------------------------------------------
export default function TikTokDispatchPanel() {
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
      const j = (await r.json()) as TikTokPayload;
      setDados(j);
      setEstado("ok");
    } catch (e) {
      // A mensagem tecnica fica, mas nunca ecoa corpo de resposta: o texto de
      // erro do servidor pode conter conteudo que nao queremos renderizar.
      setErro(e instanceof Error ? e.message : "falha de rede");
      setEstado("erro");
    }
  }, [url]);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  const janela = dados?.window ?? null;
  const diasSerie = dados?.daily ?? [];
  const criticos = contarDiasCriticos(diasSerie);
  const incidente = descreverIncidente(janela);
  const divergencia = divergenciaEmPp(janela);
  const piores = marcasCriticas(dados?.brands ?? []);

  return (
    <section className="space-y-4" aria-labelledby="tk-titulo">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="tk-titulo" className="text-lg font-semibold text-slate-900 dark:text-slate-100">
            Expedição — TikTok Shop
          </h2>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
            Taxa de atraso por data de pagamento. {EXPLICACAO_EVENTO[evento]}
          </p>
        </div>
        {dados?.snapshot ? (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Atualizado em{" "}
            <time dateTime={dados.snapshot.effective_at}>
              {new Date(dados.snapshot.effective_at).toLocaleString("pt-BR")}
            </time>
          </p>
        ) : null}
      </div>

      {/* O aviso do prazo vem ANTES de qualquer numero, sempre. */}
      {dados?.deadline_is_reconstructed ? (
        <p
          role="note"
          className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs leading-snug text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
        >
          {AVISO_PRAZO_RECONSTRUIDO}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <GrupoRadio
          idBase="tk-evento"
          rotulo="Evento"
          opcoes={EVENTOS.map((e) => ({ valor: e, texto: ROTULO_EVENTO[e] }))}
          valor={evento}
          aoMudar={setEvento}
        />
        <GrupoRadio
          idBase="tk-janela"
          rotulo="Janela"
          opcoes={JANELAS.map((j) => ({ valor: String(j.dias), texto: j.rotulo }))}
          valor={String(dias)}
          aoMudar={(v) => setDias(Number(v))}
        />
      </div>

      {estado === "carregando" ? (
        <p className="py-8 text-center text-sm text-slate-500 dark:text-slate-400">
          Carregando a série…
        </p>
      ) : null}

      {estado === "erro" ? (
        <div className="rounded-md border border-rose-300 bg-rose-50 p-4 dark:border-rose-800 dark:bg-rose-950">
          <p className="text-sm text-rose-900 dark:text-rose-200">
            Não foi possível carregar a série ({erro}).
          </p>
          <button
            type="button"
            onClick={() => void carregar()}
            className="mt-2 rounded border border-rose-400 px-3 py-1 text-sm text-rose-900 dark:text-rose-200"
          >
            Tentar de novo
          </button>
        </div>
      ) : null}

      {estado === "ok" && dados?.availability === "unavailable" ? (
        <div className="rounded-md border border-slate-300 bg-slate-50 p-6 text-center dark:border-slate-700 dark:bg-slate-900">
          <p className="text-sm text-slate-700 dark:text-slate-300">
            {explicarIndisponivel(dados.unavailable_reason)}
          </p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            A tela não mostra 0% enquanto não houver série: zero aqui seria lido
            como “nenhum atraso”, que é diferente de “sem medição”.
          </p>
        </div>
      ) : null}

      {estado === "ok" && dados?.availability === "available" && janela ? (
        <>
          {dados.alerts.length ? (
            <ul className="space-y-1.5">
              {dados.alerts.map((a) => (
                <li
                  key={a.code}
                  className={`rounded-md border px-3 py-2 text-sm ${
                    a.severity === "critical"
                      ? "border-rose-300 bg-rose-50 text-rose-900 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-200"
                      : "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
                  }`}
                >
                  {a.message}
                </li>
              ))}
            </ul>
          ) : null}

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Cartao
              titulo="Taxa da janela"
              valor={formatarTaxa(janela.rate_ratio_of_totals)}
              detalhe={
                divergencia === null
                  ? `${janela.mature_cohorts} coorte(s) madura(s).`
                  : // Separador decimal em pt-BR: a tela inteira usa virgula,
                    // e um "+5.80 pp" ao lado de "23,09%" parece outro sistema.
                    `Média das diárias: ${formatarTaxa(
                      janela.rate_mean_of_daily,
                    )} (${divergencia >= 0 ? "+" : ""}${divergencia
                      .toFixed(2)
                      .replace(".", ",")} pp).`
              }
              tom={
                janela.rate_ratio_of_totals !== null &&
                janela.rate_ratio_of_totals >= 0.1
                  ? "critico"
                  : "neutro"
              }
            />
            <Cartao
              titulo="Pedidos pagos"
              valor={formatarInteiro(janela.paid_orders)}
              detalhe={`${janela.partial_cohorts} coorte(s) ainda no prazo, fora do cálculo.`}
            />
            <Cartao
              titulo="Já atrasaram"
              valor={formatarInteiro(janela.late_orders)}
              detalhe={`${formatarInteiro(janela.pending_overdue)} venceram e ainda não saíram.`}
              tom={janela.late_orders > 0 ? "critico" : "bom"}
            />
            <Cartao
              titulo="Ainda dá para tratar"
              valor={formatarInteiro(janela.pending_at_risk)}
              detalhe={`De ${formatarInteiro(
                janela.pending_on_time,
              )} pendentes no prazo, estes vencem hoje ou amanhã.`}
              tom={janela.pending_at_risk > 0 ? "alerta" : "neutro"}
            />
          </div>

          <div className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                Evolução por dia de pagamento
              </h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {criticos > 0
                  ? `${criticos} dia(s) crítico(s).${incidente ? ` ${incidente}` : ""}`
                  : "Nenhum dia acima do limiar na janela."}
              </p>
            </div>
            <Grafico dias={diasSerie} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="overflow-hidden rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
              <h3 className="border-b border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-900 dark:border-slate-700 dark:text-slate-100">
                Dia a dia
              </h3>
              <div className="max-h-80 overflow-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-slate-50 text-xs uppercase text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">Pago em</th>
                      <th className="px-3 py-2 text-right font-medium">Pagos</th>
                      <th className="px-3 py-2 text-right font-medium">Atrasados</th>
                      <th className="px-3 py-2 text-right font-medium">Taxa</th>
                    </tr>
                  </thead>
                  <tbody>
                    {diasSerie.map((d) => {
                      const sev = severidadeDoDia(d);
                      return (
                        <tr
                          key={d.paid_date}
                          className="border-t border-slate-100 dark:border-slate-800"
                        >
                          <td className="whitespace-nowrap px-3 py-1.5 text-slate-700 dark:text-slate-300">
                            {formatarDiaLongo(d.paid_date)}
                            {!d.is_mature ? (
                              <span className="ml-1.5 rounded bg-slate-200 px-1.5 py-0.5 text-xs text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                                parcial
                              </span>
                            ) : null}
                          </td>
                          <td className="px-3 py-1.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                            {formatarInteiro(d.pedidos_pagos)}
                          </td>
                          <td className="px-3 py-1.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                            {formatarInteiro(d.atrasados)}
                          </td>
                          <td
                            className={`px-3 py-1.5 text-right font-medium tabular-nums ${TEXTO_SEVERIDADE[sev]}`}
                          >
                            {/* Coorte parcial NUNCA mostra a taxa como numero
                                fechado: ela ainda vai mudar. */}
                            {d.is_mature ? formatarTaxa(d.rate) : VALOR_SEM_TAXA}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="overflow-hidden rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
              <h3 className="border-b border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-900 dark:border-slate-700 dark:text-slate-100">
                Por loja
                {piores.length ? (
                  <span className="ml-2 text-xs font-normal text-rose-700 dark:text-rose-400">
                    {piores.length} acima do limiar
                  </span>
                ) : null}
              </h3>
              <div className="max-h-80 overflow-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-slate-50 text-xs uppercase text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">Loja</th>
                      <th className="px-3 py-2 text-right font-medium">Pagos</th>
                      <th className="px-3 py-2 text-right font-medium">Atrasados</th>
                      <th className="px-3 py-2 text-right font-medium">Taxa</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dados.brands.map((m) => (
                      <tr key={m.brand} className="border-t border-slate-100 dark:border-slate-800">
                        <td className="px-3 py-1.5 text-slate-700 dark:text-slate-300">
                          {m.shop_name ?? m.brand}
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                          {formatarInteiro(m.pedidos_pagos)}
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                          {formatarInteiro(m.atrasados)}
                        </td>
                        <td
                          className={`px-3 py-1.5 text-right font-medium tabular-nums ${
                            m.rate !== null && m.rate >= 0.1
                              ? "text-rose-700 dark:text-rose-400"
                              : "text-slate-700 dark:text-slate-300"
                          }`}
                        >
                          {formatarTaxa(m.rate)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="border-t border-slate-100 px-4 py-2 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
                Só lojas com base suficiente no período. Coortes ainda no prazo
                ficam de fora.
              </p>
            </div>
          </div>
        </>
      ) : null}
    </section>
  );
}
