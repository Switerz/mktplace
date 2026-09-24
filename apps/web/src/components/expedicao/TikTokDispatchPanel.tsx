"use client";

/**
 * Gate EXP-TK-OPS-1/2 — painel da LDR do TikTok Shop.
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
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  AVISO_PRAZO_RECONSTRUIDO,
  EVENTOS,
  EVENTO_PADRAO,
  EXPLICACAO_EVENTO,
  EXPLICACAO_FLUXO,
  EXPLICACAO_LDR,
  JANELAS,
  JANELA_INICIAL_DIAS,
  META_TIKTOK_LDR,
  ROTULO_EVENTO,
  ROTULO_LIMIAR_INTERNO,
  ROTULO_META,
  ROTULO_SEVERIDADE,
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
  type TikTokPayload,
} from "@/lib/expedicao-tiktok-contract";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

const COR_SEVERIDADE: Record<Severidade, string> = {
  ok: "bg-emerald-500",
  fora_da_meta: "bg-amber-500",
  critico: "bg-rose-600",
  // Hachurado de proposito: coorte parcial nao pode parecer um resultado
  // fechado, nem bom nem ruim.
  parcial:
    "bg-[repeating-linear-gradient(45deg,theme(colors.slate.400)_0px,theme(colors.slate.400)_4px,transparent_4px,transparent_8px)]",
};

const TEXTO_SEVERIDADE: Record<Severidade, string> = {
  ok: "text-emerald-700 dark:text-emerald-400",
  fora_da_meta: "text-amber-700 dark:text-amber-400",
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
  // manda o padrao de radiogroup.
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
            className="pointer-events-none absolute inset-x-0 z-10 border-t border-dashed border-amber-500"
            style={{ bottom: `calc(${meta}% + 1.25rem)` }}
            aria-hidden="true"
          >
            <span className="absolute -top-4 right-0 rounded bg-amber-100 px-1 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">
              {ROTULO_META} {formatarTaxa(META_TIKTOK_LDR, 0)}
            </span>
          </div>
        ) : null}
        <div
          className="flex h-48 items-stretch gap-1 overflow-x-auto pb-1"
          role="img"
          aria-label={`Taxa por dia de vencimento. Máximo da série: ${formatarTaxa(
            maximo,
          )}. Meta do TikTok: ${formatarTaxa(META_TIKTOK_LDR, 0)}.`}
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
                <span className="text-xs tabular-nums text-slate-500 dark:text-slate-400">
                  {formatarDiaCurto(d.due_date)}
                </span>
              </div>
            );
          })}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-500 dark:text-slate-400">
        {(["ok", "fora_da_meta", "critico", "parcial"] as Severidade[]).map((s) => (
          <span key={s} className="inline-flex items-center gap-1.5">
            <span className={`inline-block h-2.5 w-2.5 rounded-sm ${COR_SEVERIDADE[s]}`} />
            {ROTULO_SEVERIDADE[s]}
          </span>
        ))}
        <span>Números em % dos pedidos que venciam no dia.</span>
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

  return (
    <section className="space-y-4" aria-labelledby="tk-titulo">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="tk-titulo" className="text-lg font-semibold text-slate-900 dark:text-slate-100">
            Expedição — TikTok Shop
          </h2>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
            {EXPLICACAO_EVENTO[evento]}
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

      {estado === "ok" && dados?.availability === "available" && ldr ? (
        <>
          {dados.alerts.length ? (
            <ul className="space-y-1.5">
              {dados.alerts.map((a) => (
                <li
                  key={a.code}
                  className={`rounded-md border px-3 py-2 text-sm ${
                    a.severity === "critical"
                      ? "border-rose-300 bg-rose-50 text-rose-900 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-200"
                      : a.severity === "warning"
                        ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
                        : "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
                  }`}
                >
                  {a.message}
                </li>
              ))}
            </ul>
          ) : null}

          <div>
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              {TITULO_LDR}
            </h3>
            <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">
              {EXPLICACAO_LDR}
              {sla ? ` Prazo do evento: ${sla} dia(s) útil(eis).` : ""}
            </p>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Cartao
                titulo="LDR da janela"
                valor={formatarTaxa(ldr.rate)}
                detalhe={
                  ldr.rate === null
                    ? "Sem vencimento maduro na janela."
                    : `${ROTULO_META}: ${formatarTaxa(META_TIKTOK_LDR, 0)} · ${
                        ldr.above_target ? "FORA DA META" : "dentro da meta"
                      }`
                }
                tom={ldr.above_target ? "critico" : "bom"}
              />
              <Cartao
                titulo="Venciam no período"
                valor={formatarInteiro(ldr.base)}
                detalhe={`${ldr.mature_due_days} dia(s) fechado(s), ${ldr.partial_due_days} ainda no prazo.`}
              />
              <Cartao
                titulo="Já atrasaram"
                valor={formatarInteiro(ldr.late)}
                detalhe={`${formatarInteiro(ldr.pending_overdue)} venceram e ainda não saíram.`}
                tom={ldr.late > 0 ? "critico" : "bom"}
              />
              <Cartao
                titulo="Ainda dá para tratar"
                valor={formatarInteiro(ldr.pending_at_risk)}
                detalhe={`De ${formatarInteiro(
                  ldr.pending_on_time,
                )} pendentes no prazo, estes vencem hoje ou amanhã.`}
                tom={ldr.pending_at_risk > 0 ? "alerta" : "neutro"}
              />
            </div>
          </div>

          <div className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                Evolução por dia de vencimento
              </h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {foraDaMeta > 0
                  ? `${foraDaMeta} dia(s) fora da meta, ${criticos} acima do ${ROTULO_LIMIAR_INTERNO.toLowerCase()}.${
                      incidente ? ` ${incidente}` : ""
                    }`
                  : "Nenhum dia fora da meta na janela."}
              </p>
            </div>
            <Grafico dias={ldr.daily} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="overflow-hidden rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
              <div className="border-b border-slate-200 px-4 py-2.5 dark:border-slate-700">
                <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  {TITULO_FLUXO}
                </h3>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  {EXPLICACAO_FLUXO}
                </p>
              </div>
              <div className="max-h-96 overflow-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-slate-50 text-xs uppercase text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                    <tr>
                      <th className="px-2 py-2 text-left font-medium">Pago em</th>
                      <th className="px-2 py-2 text-right font-medium">Pagos</th>
                      <th className="px-2 py-2 text-right font-medium">No prazo</th>
                      <th className="px-2 py-2 text-right font-medium">Atrasados</th>
                      <th className="px-2 py-2 text-right font-medium">Vencidos s/ envio</th>
                      <th className="px-2 py-2 text-right font-medium">No prazo s/ envio</th>
                      <th className="px-2 py-2 text-right font-medium">Taxa</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fluxo.map((f) => (
                      <tr
                        key={f.paid_date}
                        className="border-t border-slate-100 dark:border-slate-800"
                      >
                        <td className="whitespace-nowrap px-2 py-1.5 text-slate-700 dark:text-slate-300">
                          {formatarDiaLongo(f.paid_date)}
                          {!f.is_mature ? (
                            <span className="ml-1.5 rounded bg-slate-200 px-1.5 py-0.5 text-xs text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                              parcial
                            </span>
                          ) : null}
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                          {formatarInteiro(f.paid_orders)}
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                          {formatarInteiro(f.shipped_on_time)}
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                          {formatarInteiro(f.shipped_late)}
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                          {formatarInteiro(f.pending_overdue)}
                        </td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                          {formatarInteiro(f.pending_on_time)}
                        </td>
                        <td
                          className={`px-2 py-1.5 text-right font-medium tabular-nums ${
                            !f.is_mature
                              ? "text-slate-500"
                              : (f.rate ?? 0) > META_TIKTOK_LDR
                                ? "text-rose-700 dark:text-rose-400"
                                : "text-emerald-700 dark:text-emerald-400"
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
            </div>

            <div className="overflow-hidden rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
              <h3 className="border-b border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-900 dark:border-slate-700 dark:text-slate-100">
                Por loja
                {piores.length ? (
                  <span className="ml-2 text-xs font-normal text-rose-700 dark:text-rose-400">
                    {piores.length} fora da meta
                  </span>
                ) : null}
              </h3>
              <div className="max-h-96 overflow-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-slate-50 text-xs uppercase text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">Loja</th>
                      <th className="px-3 py-2 text-right font-medium">Venciam</th>
                      <th className="px-3 py-2 text-right font-medium">Atrasados</th>
                      <th className="px-3 py-2 text-right font-medium">LDR</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dados.brands.map((m) => (
                      <tr key={m.brand} className="border-t border-slate-100 dark:border-slate-800">
                        <td className="px-3 py-1.5 text-slate-700 dark:text-slate-300">
                          {m.shop_name ?? m.brand}
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                          {formatarInteiro(m.base)}
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-slate-700 dark:text-slate-300">
                          {formatarInteiro(m.late)}
                        </td>
                        <td
                          className={`px-3 py-1.5 text-right font-medium tabular-nums ${
                            m.above_target
                              ? "text-rose-700 dark:text-rose-400"
                              : "text-emerald-700 dark:text-emerald-400"
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
                Só lojas com base suficiente no período. Vencimentos ainda no
                prazo ficam de fora.
              </p>
            </div>
          </div>
        </>
      ) : null}
    </section>
  );
}
