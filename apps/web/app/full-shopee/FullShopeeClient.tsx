"use client";

/**
 * Tela "Full Shopee" — Gate FULL-SH-1D.
 *
 * COBERTURA PARCIAL, DECLARADA NO TOPO
 * -------------------------------------
 * A esteira de API cobre QUATRO contas. Kokeshi, a maior marca Shopee por
 * volume, NAO esta na API e nao entra em nenhum total, share, tabela ou
 * grafico. Ela aparece como NAO COBERTA -- nunca como zero, 0% ou "sem FBS".
 *
 * Apice e' o oposto: esta coberta e teve share FBS de 0% em agosto/2026. Isso
 * e' medicao, nao ausencia, e a tela mostra "0,00%" com nota, nao travessao.
 *
 * A TELA NAO RECALCULA O QUE A API ENTREGA
 * -----------------------------------------
 * Shares consolidados, taxa de cancelamento e handling medio vem prontos, ja'
 * agregados sobre o periodo inteiro. A unica divisao feita aqui e' o share
 * DIARIO da serie, com a mesma regra do backend.
 */

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import KpiCard from "@/components/KpiCard";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import { fetchShopeeFbs, ShopeeFbsError } from "@/lib/api-client";
import { MOTIVO_DESLIGADA } from "@/lib/shopee-fbs-flag";
import {
  AVISO_CARGA_MANUAL,
  AVISO_COBERTURA,
  AVISO_DIVERGENCIA,
  AVISO_GMV_BRUTO,
  AVISO_HANDLING,
  AVISO_JANELA,
  CLASS_LABEL,
  FRESHNESS_LABEL,
  PERIOD_PRESETS,
  SEM_DADO,
  alertas,
  buildQuery,
  byClass,
  fmtCount,
  fmtGmv,
  fmtHandling,
  fmtShare,
  handlingPoucaCobertura,
  hojeIsoBrt,
  isUnavailable,
  isVazioReal,
  parseGmv,
  presetRange,
  serieDiaria,
  validateRange,
  type FbsClass,
  type MetricaSerie,
  type ShopeeFbsResponse,
} from "@/lib/shopee-fbs";

const CONTAS = ["apice", "barbours", "lescent", "rituaria"] as const;
const MARCAS = CONTAS;

const METRICAS: { key: MetricaSerie; label: string }[] = [
  { key: "gmv", label: "GMV" },
  { key: "orders", label: "Pedidos" },
  { key: "units", label: "Unidades" },
];

/** Alvo minimo de toque e foco visivel. SEM cor: so' geometria e anel.
 *  Sobrepor cor a uma base que ja' a fixa produz branco-sobre-branco quando
 *  as utilities do Tailwind competem pela ordem do stylesheet -- defeito
 *  medido e corrigido no Gate FULL-1D-R/V. */
const CONTROLE_BASE =
  "min-h-11 px-3 rounded-lg border text-sm " +
  "focus-visible:outline focus-visible:outline-2 " +
  "focus-visible:outline-offset-2 focus-visible:outline-violet-600";
const CONTROLE = `${CONTROLE_BASE} border-slate-300 bg-white text-slate-800`;

function Aviso({ tom, children }: { tom: "info" | "alerta"; children: React.ReactNode }) {
  const cor =
    tom === "alerta"
      ? "bg-amber-50 border-amber-300 text-amber-900"
      : "bg-slate-50 border-slate-200 text-slate-700";
  return (
    <p className={`text-xs leading-relaxed border rounded-lg px-3 py-2 ${cor}`}>
      {children}
    </p>
  );
}

function FullShopeeInner() {
  const hoje = useMemo(hojeIsoBrt, []);
  const inicial = useMemo(() => presetRange(30, hoje), [hoje]);

  const [from, setFrom] = useState(inicial.from);
  const [to, setTo] = useState(inicial.to);
  const [marca, setMarca] = useState("");
  const [conta, setConta] = useState("");
  const [metrica, setMetrica] = useState<MetricaSerie>("gmv");

  const [dados, setDados] = useState<ShopeeFbsResponse | null>(null);
  const [desligadoBackend, setDesligadoBackend] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);

  // Validacao ANTES da requisicao: o 422 do backend nao deve ser provocado a
  // toa, e a mensagem daqui e' melhor que a generica.
  const erroJanela = useMemo(() => validateRange(from, to, hoje), [from, to, hoje]);

  //: Sequencia monotonica. Sem ela, uma resposta LENTA de um filtro antigo
  //: poderia chegar DEPOIS da resposta do filtro atual e sobrescreve-la --
  //: o abort cobre a maioria dos casos, mas nao a corrida em que a resposta
  //: ja' estava em transito quando o abort disparou.
  const seq = useRef(0);

  useEffect(() => {
    if (erroJanela) {
      setCarregando(false);
      return;
    }
    const meu = ++seq.current;
    const ctrl = new AbortController();
    setCarregando(true);
    setErro(null);

    const query = buildQuery({
      from, to,
      brands: marca ? [marca] : undefined,
      accounts: conta ? [conta] : undefined,
    });

    fetchShopeeFbs(query, ctrl.signal)
      .then((p) => {
        if (meu !== seq.current) return;      // resposta obsoleta: descarta
        if (isUnavailable(p)) {
          setDesligadoBackend(p.unavailable_reason);
          setDados(null);
        } else {
          setDesligadoBackend(null);
          setDados(p);
        }
        setCarregando(false);
      })
      .catch((e: unknown) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        if (meu !== seq.current) return;
        setDados(null);
        setErro(
          e instanceof ShopeeFbsError
            ? e.message
            : "Nao foi possivel carregar a superficie FBS da Shopee.",
        );
        setCarregando(false);
      });
    return () => ctrl.abort();
  }, [from, to, marca, conta, erroJanela]);

  const aplicarPreset = useCallback((dias: number) => {
    const r = presetRange(dias, hoje);
    setFrom(r.from);
    setTo(r.to);
  }, [hoje]);

  const limpar = useCallback(() => {
    const r = presetRange(30, hoje);
    setFrom(r.from);
    setTo(r.to);
    setMarca("");
    setConta("");
    setMetrica("gmv");
  }, [hoje]);

  const fbs = byClass(dados, "fbs");
  const seller = byClass(dados, "seller");
  const serie = useMemo(() => serieDiaria(dados, metrica), [dados, metrica]);
  const listaAlertas = useMemo(() => alertas(dados), [dados]);
  const vazio = isVazioReal(dados);
  const frescor = dados?.meta.freshness;

  const filtros = (
    <div className="flex flex-wrap items-end gap-2">
      <div className="flex flex-col gap-1">
        <label htmlFor="sh-de" className="text-xs font-medium text-slate-600">De</label>
        <input id="sh-de" type="date" value={from} max={to}
          onChange={(e) => setFrom(e.target.value)} className={CONTROLE} />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="sh-ate" className="text-xs font-medium text-slate-600">Até</label>
        <input id="sh-ate" type="date" value={to} min={from}
          onChange={(e) => setTo(e.target.value)} className={CONTROLE} />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="sh-marca" className="text-xs font-medium text-slate-600">Marca</label>
        <select id="sh-marca" value={marca} onChange={(e) => setMarca(e.target.value)}
          className={CONTROLE}>
          <option value="">Todas as marcas cobertas</option>
          {MARCAS.map((m) => (
            <option key={m} value={m}>{m[0].toUpperCase() + m.slice(1)}</option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="sh-conta" className="text-xs font-medium text-slate-600">Conta</label>
        <select id="sh-conta" value={conta} onChange={(e) => setConta(e.target.value)}
          className={CONTROLE}>
          <option value="">Todas as contas cobertas</option>
          {CONTAS.map((c) => (
            <option key={c} value={c}>{c[0].toUpperCase() + c.slice(1)}</option>
          ))}
        </select>
      </div>
      <div className="flex gap-1" role="group" aria-label="Períodos predefinidos">
        {PERIOD_PRESETS.map((d) => (
          <button key={d} type="button" onClick={() => aplicarPreset(d)}
            className={`${CONTROLE} font-medium hover:bg-slate-50`}>
            {d} dias
          </button>
        ))}
      </div>
      <button type="button" onClick={limpar}
        className={`${CONTROLE} font-medium hover:bg-slate-50`}>
        Limpar filtros
      </button>
    </div>
  );

  return (
    <PageContainer>
      <PageHeader
        title="Full Shopee"
        subtitle="Quanto da operação Shopee coberta pela API sai pelo fulfillment da Shopee (FBS) contra o envio pelo próprio vendedor."
        filters={filtros}
      />

      {/* ---- Frescor e escopo: sempre visiveis, nunca "tempo real" ---- */}
      <section aria-labelledby="sh-frescor" className="flex flex-col gap-2">
        <h2 id="sh-frescor" className="sr-only">Cobertura e frescor</h2>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
          <span className="font-medium text-slate-800">{from} a {to}</span>
          {frescor && (
            <>
              <span className={
                "px-2 py-1 rounded-full font-medium border " +
                (frescor.freshness_status === "fresh"
                  ? "bg-emerald-50 text-emerald-800 border-emerald-200"
                  : "bg-amber-50 text-amber-900 border-amber-300")
              }>
                {FRESHNESS_LABEL[frescor.freshness_status]} · carga manual
              </span>
              <span>Fonte até <strong>{frescor.source_max_date ?? SEM_DADO}</strong></span>
              <span>
                Publicado{" "}
                {frescor.refreshed_at
                  ? new Date(frescor.refreshed_at).toLocaleString("pt-BR",
                      { timeZone: "America/Sao_Paulo" })
                  : SEM_DADO}
              </span>
              <span>
                Idade do snapshot:{" "}
                {frescor.snapshot_age_hours !== null
                  ? `${frescor.snapshot_age_hours.toFixed(1).replace(".", ",")} h`
                  : SEM_DADO}
              </span>
              <span className="text-slate-500">load_mode: manual_snapshot</span>
            </>
          )}
        </div>

        <Aviso tom="alerta">{AVISO_COBERTURA}</Aviso>
        <Aviso tom="info">{AVISO_CARGA_MANUAL}</Aviso>
        <Aviso tom="info">{AVISO_GMV_BRUTO} {AVISO_DIVERGENCIA}</Aviso>
        <Aviso tom="info">{AVISO_JANELA}</Aviso>

        {dados && (
          <div className="flex flex-wrap gap-4 text-xs text-slate-700 border border-slate-200 rounded-lg px-3 py-2 bg-white">
            <span>
              <strong>Contas cobertas:</strong>{" "}
              {dados.meta.observed_accounts.join(", ") || SEM_DADO}
            </span>
            <span>
              <strong>Marcas cobertas:</strong>{" "}
              {dados.meta.covered_brands.join(", ") || SEM_DADO}
            </span>
            <span className="text-amber-900">
              <strong>Fora da cobertura da API:</strong>{" "}
              {dados.meta.brands_not_covered.join(", ")} — não aparece como zero
              porque não há medição, e não porque não vendeu.
            </span>
          </div>
        )}

        {listaAlertas.map((a) => (
          <Aviso key={a} tom="alerta">{a}</Aviso>
        ))}
      </section>

      {/* ---- Estados ---- */}
      {erroJanela && (
        <p role="alert" className="text-sm border border-amber-300 bg-amber-50 text-amber-900 rounded-lg px-3 py-2">
          {erroJanela}
        </p>
      )}

      {!erroJanela && desligadoBackend && (
        <p role="status" className="text-sm border border-slate-300 bg-slate-50 text-slate-700 rounded-lg px-3 py-2">
          {desligadoBackend}
        </p>
      )}

      {!erroJanela && erro && (
        <p role="alert" className="text-sm border border-rose-300 bg-rose-50 text-rose-900 rounded-lg px-3 py-2">
          {erro}
        </p>
      )}

      {!erroJanela && carregando && (
        <div role="status" aria-live="polite" className="flex flex-col gap-3">
          <span className="sr-only">Carregando desempenho FBS da Shopee…</span>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="h-24 rounded-xl bg-slate-100 animate-pulse" />
            ))}
          </div>
        </div>
      )}

      {!erroJanela && !carregando && !erro && !desligadoBackend && vazio && (
        <p role="status" className="text-sm border border-slate-300 bg-slate-50 text-slate-700 rounded-lg px-3 py-2">
          Nenhum pedido publicado nesta janela para o filtro selecionado. Isso
          significa <strong>ausência de dado publicado</strong>, não queda de
          vendas.
        </p>
      )}

      {/* ---- Conteudo ---- */}
      {!erroJanela && !carregando && !erro && !desligadoBackend && dados && !vazio && (
        <>
          {/* KPIs */}
          <section aria-labelledby="sh-kpis" className="flex flex-col gap-2">
            <h2 id="sh-kpis" className="text-sm font-semibold text-slate-800">
              Participação do FBS no período
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <KpiCard label="GMV bruto (total)" value={fmtGmv(dados.totals.gross_gmv)}
                subvalue="Não cancelados · inclui devolução e não pago" />
              <KpiCard label="GMV FBS" value={fmtGmv(fbs?.gross_gmv ?? null)}
                subvalue={`Vendedor: ${fmtGmv(seller?.gross_gmv ?? null)}`} />
              <KpiCard label="Share FBS — GMV" value={fmtShare(dados.shares.share_fbs_gmv)}
                subvalue="FBS ÷ (FBS + vendedor), no período inteiro"
                accent="bg-violet-600" />
              <KpiCard label="Share FBS — pedidos" value={fmtShare(dados.shares.share_fbs_orders)}
                subvalue={`${fmtCount(dados.totals.eligible_orders)} pedidos elegíveis`}
                accent="bg-sky-600" />
              <KpiCard label="Share FBS — unidades" value={fmtShare(dados.shares.share_fbs_units)}
                subvalue={`${fmtCount(dados.totals.gross_units)} unidades`}
                accent="bg-sky-600" />
              <KpiCard label="Taxa de cancelamento" value={fmtShare(dados.rates.cancellation_rate)}
                subvalue={`${fmtCount(dados.totals.cancelled_orders)} de ${fmtCount(dados.totals.created_orders)} pedidos criados`} />
              <KpiCard label="Handling médio" value={fmtHandling(dados.totals.handling)}
                subvalue="Pagamento até a coleta — não é entrega" />
              <KpiCard label="Cobertura do handling"
                value={fmtShare(dados.totals.handling.coverage_ratio, 1)}
                subvalue={`${fmtCount(dados.totals.handling.sample_count)} pedidos com coleta registrada`} />
            </div>
            <p className="text-xs text-slate-500">
              Todos os valores referem-se a {from} a {to}, apenas para as contas
              cobertas pela API. {SEM_DADO} indica denominador zero — nunca
              zero medido.
            </p>
            {handlingPoucaCobertura(dados.totals.handling) && (
              <Aviso tom="alerta">
                {AVISO_HANDLING} A amostra cobre apenas{" "}
                {fmtShare(dados.totals.handling.coverage_ratio, 0)} dos pedidos
                elegíveis: a média descreve essa amostra, não o período inteiro.
              </Aviso>
            )}
            {!handlingPoucaCobertura(dados.totals.handling) && (
              <Aviso tom="info">{AVISO_HANDLING}</Aviso>
            )}
          </section>

          {/* Composicao logistica */}
          <section aria-labelledby="sh-composicao" className="flex flex-col gap-2">
            <h2 id="sh-composicao" className="text-sm font-semibold text-slate-800">
              Composição: FBS × envio pelo vendedor
            </h2>
            <div className="overflow-x-auto border border-slate-200 rounded-xl bg-white">
              <table className="min-w-full text-sm">
                <caption className="sr-only">
                  GMV bruto, pedidos e unidades por modalidade de envio.
                </caption>
                <thead className="bg-slate-50 text-slate-700">
                  <tr>
                    <th scope="col" className="text-left px-3 py-2 font-medium">Modalidade</th>
                    <th scope="col" className="text-right px-3 py-2 font-medium">GMV bruto</th>
                    <th scope="col" className="text-right px-3 py-2 font-medium">Pedidos</th>
                    <th scope="col" className="text-right px-3 py-2 font-medium">Unidades</th>
                    <th scope="col" className="text-right px-3 py-2 font-medium">Cancelados</th>
                    <th scope="col" className="text-right px-3 py-2 font-medium">Handling</th>
                  </tr>
                </thead>
                <tbody>
                  {(["fbs", "seller"] as FbsClass[]).map((c) => {
                    const l = c === "fbs" ? fbs : seller;
                    return (
                      <tr key={c} className="border-t border-slate-100">
                        <th scope="row" className="text-left px-3 py-2 font-medium text-slate-800">
                          {CLASS_LABEL[c]}
                        </th>
                        <td className="text-right px-3 py-2 tabular-nums">{fmtGmv(l?.gross_gmv ?? null)}</td>
                        <td className="text-right px-3 py-2 tabular-nums">{fmtCount(l?.eligible_orders)}</td>
                        <td className="text-right px-3 py-2 tabular-nums">{fmtCount(l?.gross_units)}</td>
                        <td className="text-right px-3 py-2 tabular-nums">{fmtCount(l?.cancelled_orders)}</td>
                        <td className="text-right px-3 py-2 tabular-nums">{fmtHandling(l?.handling)}</td>
                      </tr>
                    );
                  })}
                  <tr className="border-t-2 border-slate-300 bg-slate-50 font-medium">
                    <th scope="row" className="text-left px-3 py-2">Total (cobertura API)</th>
                    <td className="text-right px-3 py-2 tabular-nums">{fmtGmv(dados.totals.gross_gmv)}</td>
                    <td className="text-right px-3 py-2 tabular-nums">{fmtCount(dados.totals.eligible_orders)}</td>
                    <td className="text-right px-3 py-2 tabular-nums">{fmtCount(dados.totals.gross_units)}</td>
                    <td className="text-right px-3 py-2 tabular-nums">{fmtCount(dados.totals.cancelled_orders)}</td>
                    <td className="text-right px-3 py-2 tabular-nums">{fmtHandling(dados.totals.handling)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="text-xs text-slate-500">
              As duas modalidades somam o total: não existe terceira classe nesta
              fonte. Devolução (to_return): {fmtGmv(dados.totals.to_return_gmv)} em{" "}
              {fmtCount(dados.totals.to_return_orders)} pedidos · não pagos
              (unpaid): {fmtGmv(dados.totals.unpaid_gmv)} em{" "}
              {fmtCount(dados.totals.unpaid_orders)} pedidos — <strong>já
              incluídos</strong> no GMV bruto acima, exibidos aqui para que o
              peso deles seja visível. Não some novamente.
            </p>
          </section>

          {/* Tendencia */}
          <section aria-labelledby="sh-tendencia" className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 id="sh-tendencia" className="text-sm font-semibold text-slate-800">
                Tendência diária do share de FBS
              </h2>
              <div className="flex gap-1" role="group" aria-label="Métrica da série">
                {METRICAS.map((m) => (
                  <button key={m.key} type="button" aria-pressed={metrica === m.key}
                    onClick={() => setMetrica(m.key)}
                    className={
                      `${CONTROLE_BASE} font-medium ` +
                      (metrica === m.key
                        ? "border-violet-600 bg-violet-600 text-white"
                        : "border-slate-300 bg-white text-slate-800 hover:bg-slate-50")
                    }>
                    {m.label}
                  </button>
                ))}
              </div>
            </div>
            <ShopeeFbsTrend pontos={serie} metrica={metrica} />
            <p className="text-xs text-slate-500">
              O share de cada dia é FBS ÷ (FBS + vendedor) daquele dia. O share
              do período, nos cartões acima, vem dos totais consolidados — não é
              a média destes pontos, que daria o mesmo peso a um dia fraco e a
              um dia forte.
            </p>
          </section>

          {/* Por marca */}
          <Comparativo
            id="sh-marcas" titulo="Adoção por marca" chave="brand"
            linhas={dados.by_brand.map((b) => ({
              nome: b.brand,
              gmv: b.gross_gmv,
              share: b.share_fbs_gmv,
              shareOrders: b.share_fbs_orders,
              pedidos: b.eligible_orders,
              unidades: b.gross_units,
              cancelados: b.cancelled_orders,
              criados: b.created_orders,
            }))}
            naoCobertas={dados.meta.brands_not_covered}
          />

          {/* Por conta */}
          <Comparativo
            id="sh-contas" titulo="Adoção por conta" chave="account"
            linhas={dados.by_account.map((a) => ({
              nome: a.shop_account,
              gmv: a.gross_gmv,
              share: a.share_fbs_gmv,
              shareOrders: null,
              pedidos: a.eligible_orders,
              unidades: null,
              cancelados: null,
              criados: a.created_orders,
              watermark: a.source_watermark_at,
            }))}
            ausentes={dados.meta.missing_accounts}
          />

          {/* Qualidade */}
          <section aria-labelledby="sh-qualidade" className="flex flex-col gap-2">
            <h2 id="sh-qualidade" className="text-sm font-semibold text-slate-800">
              Qualidade e limitações
            </h2>
            <ul className="list-disc pl-5 text-xs text-slate-600 flex flex-col gap-1">
              {dados.meta.limitations.map((l) => <li key={l}>{l}</li>)}
            </ul>
            <p className="text-xs text-slate-500">
              Definição servida pela API: {dados.meta.gmv_definition}
            </p>
          </section>
        </>
      )}
    </PageContainer>
  );
}

// ---------------------------------------------------------------------------

interface LinhaComparativo {
  nome: string;
  gmv: string;
  share: number | null;
  shareOrders: number | null;
  pedidos: number;
  unidades: number | null;
  cancelados: number | null;
  criados: number;
  watermark?: string | null;
}

/**
 * Tabela comparativa. Ordenacao EXPLICITA por GMV decrescente e, em empate,
 * por nome -- deterministica entre execucoes.
 *
 * `share` nulo vira travessao; `share` ZERO vira "0,00%" com nota. Apice e' o
 * caso real: coberta, vendeu, e nao teve FBS.
 */
function Comparativo({
  id, titulo, chave, linhas, naoCobertas, ausentes,
}: {
  id: string;
  titulo: string;
  chave: "brand" | "account";
  linhas: LinhaComparativo[];
  naoCobertas?: string[];
  ausentes?: string[];
}) {
  const ordenadas = [...linhas].sort((a, b) => {
    const ga = parseGmv(a.gmv) ?? 0;
    const gb = parseGmv(b.gmv) ?? 0;
    if (gb !== ga) return gb - ga;
    return a.nome.localeCompare(b.nome, "pt-BR");
  });

  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <h2 id={id} className="text-sm font-semibold text-slate-800">{titulo}</h2>
      <div className="overflow-x-auto border border-slate-200 rounded-xl bg-white">
        <table className="min-w-full text-sm">
          <caption className="sr-only">
            {titulo}: GMV bruto, share de FBS e volumes, ordenados por GMV
            decrescente.
          </caption>
          <thead className="bg-slate-50 text-slate-700">
            <tr>
              <th scope="col" className="text-left px-3 py-2 font-medium">
                {chave === "brand" ? "Marca" : "Conta"}
              </th>
              <th scope="col" className="text-right px-3 py-2 font-medium">GMV bruto</th>
              <th scope="col" className="text-right px-3 py-2 font-medium">Share FBS</th>
              <th scope="col" className="text-right px-3 py-2 font-medium">Pedidos</th>
              {chave === "brand" && (
                <>
                  <th scope="col" className="text-right px-3 py-2 font-medium">Unidades</th>
                  <th scope="col" className="text-right px-3 py-2 font-medium">Cancelados</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {ordenadas.map((l) => (
              <tr key={l.nome} className="border-t border-slate-100">
                <th scope="row" className="text-left px-3 py-2 font-medium text-slate-800 whitespace-nowrap">
                  {l.nome[0].toUpperCase() + l.nome.slice(1)}
                  {l.share === 0 && (
                    <span className="ml-2 text-xs font-normal text-slate-500">
                      (coberta, sem FBS)
                    </span>
                  )}
                </th>
                <td className="text-right px-3 py-2 tabular-nums">{fmtGmv(l.gmv)}</td>
                <td className="text-right px-3 py-2 tabular-nums">{fmtShare(l.share)}</td>
                <td className="text-right px-3 py-2 tabular-nums">{fmtCount(l.pedidos)}</td>
                {chave === "brand" && (
                  <>
                    <td className="text-right px-3 py-2 tabular-nums">{fmtCount(l.unidades)}</td>
                    <td className="text-right px-3 py-2 tabular-nums">{fmtCount(l.cancelados)}</td>
                  </>
                )}
              </tr>
            ))}
            {(naoCobertas ?? []).map((n) => (
              <tr key={`nc-${n}`} className="border-t border-slate-100 bg-amber-50/40">
                <th scope="row" className="text-left px-3 py-2 font-medium text-amber-900 whitespace-nowrap">
                  {n[0].toUpperCase() + n.slice(1)}
                </th>
                <td colSpan={chave === "brand" ? 5 : 3}
                  className="px-3 py-2 text-xs text-amber-900">
                  Fora da cobertura da API — sem medição. Não é zero.
                </td>
              </tr>
            ))}
            {(ausentes ?? []).map((n) => (
              <tr key={`au-${n}`} className="border-t border-slate-100 bg-amber-50/40">
                <th scope="row" className="text-left px-3 py-2 font-medium text-amber-900 whitespace-nowrap">
                  {n[0].toUpperCase() + n.slice(1)}
                </th>
                <td colSpan={3} className="px-3 py-2 text-xs text-amber-900">
                  Sem movimento na janela — fora da cobertura medida, não é 0%.
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/**
 * Serie diaria em SVG puro. Nenhuma biblioteca nova: a linha do share cabe em
 * `polyline`, e o grafico tem alternativa textual navegavel.
 *
 * Dia com denominador zero vira LACUNA (segmento interrompido), nunca queda a
 * zero -- "nao houve venda classificada" nao e' "o FBS caiu a zero".
 */
function ShopeeFbsTrend({ pontos, metrica }: { pontos: ReturnType<typeof serieDiaria>; metrica: MetricaSerie }) {
  if (!pontos.length) {
    return (
      <p className="text-xs text-slate-500 border border-slate-200 rounded-lg px-3 py-6 text-center bg-white">
        Sem série diária publicada para esta janela.
      </p>
    );
  }
  const W = 720, H = 180, P = 28;
  const n = pontos.length;
  const x = (i: number) => P + (n === 1 ? (W - 2 * P) / 2 : (i * (W - 2 * P)) / (n - 1));
  const y = (v: number) => H - P - v * (H - 2 * P);

  // Segmentos contiguos: quebra onde o share e' null.
  const segmentos: string[][] = [];
  let atual: string[] = [];
  pontos.forEach((p, i) => {
    if (p.share === null) {
      if (atual.length) segmentos.push(atual);
      atual = [];
    } else {
      atual.push(`${x(i).toFixed(1)},${y(p.share).toFixed(1)}`);
    }
  });
  if (atual.length) segmentos.push(atual);

  const rotulo = METRICAS.find((m) => m.key === metrica)?.label ?? metrica;
  const comDado = pontos.filter((p) => p.share !== null);
  const min = comDado.length ? Math.min(...comDado.map((p) => p.share!)) : 0;
  const max = comDado.length ? Math.max(...comDado.map((p) => p.share!)) : 0;

  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-x-auto border border-slate-200 rounded-xl bg-white p-2">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" className="w-full h-auto min-w-[320px]"
          aria-label={
            `Share diário de FBS por ${rotulo}, de ${pontos[0].data} a ` +
            `${pontos[n - 1].data}. Mínimo ${(min * 100).toFixed(1)}%, ` +
            `máximo ${(max * 100).toFixed(1)}%. ` +
            `${pontos.length - comDado.length} dia(s) sem denominador.`
          }>
          {[0, 0.25, 0.5, 0.75, 1].map((g) => (
            <g key={g}>
              <line x1={P} x2={W - P} y1={y(g)} y2={y(g)} stroke="#e2e8f0" strokeWidth="1" />
              {/* 12px e o piso de legibilidade da Torre. O SVG escala com o
                  viewBox, mas o rotulo precisa nascer >= 12 para nao ficar
                  abaixo do piso quando o grafico encolhe. */}
              <text x={4} y={y(g) + 4} fontSize="12" fill="#64748b">{g * 100}%</text>
            </g>
          ))}
          {segmentos.map((seg, i) => (
            <polyline key={i} points={seg.join(" ")} fill="none"
              stroke="#7c3aed" strokeWidth="2" strokeLinejoin="round" />
          ))}
        </svg>
      </div>
      <details className="text-xs text-slate-600">
        <summary className="cursor-pointer min-h-11 flex items-center">
          Ver a série como tabela
        </summary>
        <div className="overflow-x-auto mt-2 max-h-72">
          <table className="min-w-full text-xs">
            <caption className="sr-only">Share diário de FBS por {rotulo}.</caption>
            <thead className="bg-slate-50">
              <tr>
                <th scope="col" className="text-left px-2 py-1 font-medium">Dia</th>
                <th scope="col" className="text-right px-2 py-1 font-medium">FBS</th>
                <th scope="col" className="text-right px-2 py-1 font-medium">Vendedor</th>
                <th scope="col" className="text-right px-2 py-1 font-medium">Share FBS</th>
              </tr>
            </thead>
            <tbody>
              {pontos.map((p) => (
                <tr key={p.data} className="border-t border-slate-100">
                  <th scope="row" className="text-left px-2 py-1 font-normal">{p.data}</th>
                  <td className="text-right px-2 py-1 tabular-nums">
                    {metrica === "gmv" ? fmtGmv(String(p.fbs)) : fmtCount(p.fbs)}
                  </td>
                  <td className="text-right px-2 py-1 tabular-nums">
                    {metrica === "gmv" ? fmtGmv(String(p.seller)) : fmtCount(p.seller)}
                  </td>
                  <td className="text-right px-2 py-1 tabular-nums">{fmtShare(p.share)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

export default function FullShopeeClient() {
  return (
    <Suspense fallback={<PageContainer><div className="h-24" /></PageContainer>}>
      <FullShopeeInner />
    </Suspense>
  );
}
