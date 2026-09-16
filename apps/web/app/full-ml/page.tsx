"use client";

/**
 * Tela "Full Mercado Livre" — Gate FULL-1D.
 *
 * MODALIDADE LOGISTICA, NAO ESTOQUE
 * ----------------------------------
 * Esta superficie mede por onde o pedido foi enviado, nao quanto ha em
 * estoque. Nao existe aqui disponibilidade, cobertura em dias nem ruptura, e
 * isso e' decisao medida: o gate FULL-0R provou que o campo de estoque
 * anunciado do ML nao se comporta como estoque fisico (correlacao de -0,006
 * entre a variacao diaria e as vendas). Misturar as duas coisas nesta tela
 * seria afirmar o que ninguem mediu.
 *
 * A tela NAO recalcula nada que a API entrega. Shares, taxas e medias vem
 * prontos. A unica divisao feita aqui e' o share DIARIO da serie, porque a API
 * entrega o share agregado do periodo, nao por dia -- e ela usa exatamente a
 * mesma regra do backend (denominador `full + non_full`, sem `unknown`).
 */

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import KpiCard from "@/components/KpiCard";
import MLFulfillmentTrend from "@/components/MLFulfillmentTrend";
import { fetchMLFulfillment, MLFulfillmentError } from "@/lib/api-client";
import {
  AVISO_COBERTURA,
  AVISO_JANELA,
  AVISO_MANUAL_SNAPSHOT,
  AVISO_SEM_ESTOQUE,
  CLASS_DESCRICAO,
  CLASS_LABEL,
  FRESHNESS_LABEL,
  LOAD_MODE_LABEL,
  PERIOD_PRESETS,
  SEM_DADO,
  alertas,
  buildQuery,
  byClass,
  fmtCount,
  fmtGmv,
  fmtShare,
  fmtTempo,
  isVazioReal,
  presetRange,
  serieDiaria,
  validateRange,
  type FulfillmentClass,
  type MLFulfillmentResponse,
  type MetricaSerie,
} from "@/lib/ml-fulfillment";

const MARCAS = [
  { key: "", label: "Todas as marcas" },
  { key: "barbours", label: "Barbours" },
  { key: "kokeshi", label: "Kokeshi" },
  { key: "lescent", label: "Lescent" },
  { key: "rituaria", label: "Rituária" },
];

const METRICAS: { key: MetricaSerie; label: string }[] = [
  { key: "gmv", label: "GMV" },
  { key: "orders", label: "Pedidos" },
  { key: "units", label: "Unidades" },
];

/** Alvo minimo de toque e foco visivel — o mesmo contrato do Gate V3-1A. */
const CONTROLE =
  "min-h-11 px-3 rounded-lg border border-slate-300 bg-white text-sm " +
  "text-slate-800 focus-visible:outline focus-visible:outline-2 " +
  "focus-visible:outline-offset-2 focus-visible:outline-violet-600";

function hojeIsoBrt(): string {
  // `sv-SE` devolve YYYY-MM-DD; o fuso e' explicito porque o dia operacional da
  // Torre e' America/Sao_Paulo, nao o do navegador.
  return new Date().toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" });
}

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

function FullMLPageInner() {
  const hoje = useMemo(hojeIsoBrt, []);
  const inicial = useMemo(() => presetRange(30, hoje), [hoje]);

  const [from, setFrom] = useState(inicial.from);
  const [to, setTo] = useState(inicial.to);
  const [marca, setMarca] = useState("");
  const [metrica, setMetrica] = useState<MetricaSerie>("gmv");

  const [dados, setDados] = useState<MLFulfillmentResponse | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);

  // Validacao ANTES da requisicao: o 422 do backend ecoa o valor recebido, e
  // esse corpo tecnico nao deve chegar a tela.
  const erroJanela = useMemo(
    () => validateRange(from, to, hoje),
    [from, to, hoje],
  );

  useEffect(() => {
    if (erroJanela) {
      setCarregando(false);
      return;
    }
    const ctrl = new AbortController();
    setCarregando(true);
    setErro(null);
    const query = buildQuery({ from, to, brands: marca ? [marca] : undefined });
    fetchMLFulfillment(query, ctrl.signal)
      .then((d) => {
        setDados(d);
        setCarregando(false);
      })
      .catch((e: unknown) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setDados(null);
        setErro(
          e instanceof MLFulfillmentError
            ? e.message
            : "Nao foi possivel carregar a superficie Full.",
        );
        setCarregando(false);
      });
    return () => ctrl.abort();
  }, [from, to, marca, erroJanela]);

  const aplicarPreset = useCallback(
    (dias: number) => {
      const r = presetRange(dias, hoje);
      setFrom(r.from);
      setTo(r.to);
    },
    [hoje],
  );

  const limpar = useCallback(() => {
    const r = presetRange(30, hoje);
    setFrom(r.from);
    setTo(r.to);
    setMarca("");
    setMetrica("gmv");
  }, [hoje]);

  const full = byClass(dados, "full");
  const naoFull = byClass(dados, "non_full");
  const desconhecido = byClass(dados, "unknown");
  const serie = useMemo(() => serieDiaria(dados, metrica), [dados, metrica]);
  const listaAlertas = useMemo(() => alertas(dados), [dados]);
  const vazio = isVazioReal(dados);

  const frescor = dados?.freshness;
  const statusBadge = frescor ? (
    <span
      className={
        "text-xs font-medium px-2 py-1 rounded-full " +
        (frescor.freshness_status === "fresh"
          ? "bg-emerald-50 text-emerald-800 border border-emerald-200"
          : "bg-amber-50 text-amber-900 border border-amber-300")
      }
    >
      {FRESHNESS_LABEL[frescor.freshness_status]} · {LOAD_MODE_LABEL[frescor.load_mode]}
    </span>
  ) : null;

  const filtros = (
    <div className="flex flex-wrap items-end gap-2">
      <div className="flex flex-col gap-1">
        <label htmlFor="full-ml-de" className="text-xs font-medium text-slate-600">
          De
        </label>
        <input
          id="full-ml-de"
          type="date"
          value={from}
          max={to}
          onChange={(e) => setFrom(e.target.value)}
          className={CONTROLE}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="full-ml-ate" className="text-xs font-medium text-slate-600">
          Até
        </label>
        <input
          id="full-ml-ate"
          type="date"
          value={to}
          min={from}
          onChange={(e) => setTo(e.target.value)}
          className={CONTROLE}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="full-ml-marca" className="text-xs font-medium text-slate-600">
          Marca
        </label>
        <select
          id="full-ml-marca"
          value={marca}
          onChange={(e) => setMarca(e.target.value)}
          className={CONTROLE}
        >
          {MARCAS.map((m) => (
            <option key={m.key} value={m.key}>
              {m.label}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-xs font-medium text-slate-600">Períodos</span>
        <div className="flex gap-1" role="group" aria-label="Períodos predefinidos">
          {PERIOD_PRESETS.map((p) => (
            <button
              key={p.days}
              type="button"
              onClick={() => aplicarPreset(p.days)}
              className={`${CONTROLE} font-medium hover:bg-slate-50`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>
      <button
        type="button"
        onClick={limpar}
        className={`${CONTROLE} font-medium text-slate-600 hover:bg-slate-50`}
      >
        Limpar filtros
      </button>
    </div>
  );

  const escopo = (
    <span className="text-xs text-slate-600">
      {from} a {to}
      {dados?.freshness.source_max_date && (
        <> · fonte até {dados.freshness.source_max_date}</>
      )}
      {marca && <> · {MARCAS.find((m) => m.key === marca)?.label}</>}
    </span>
  );

  return (
    <PageContainer>
      <PageHeader
        title="Full Mercado Livre"
        subtitle={
          "Quanto do negócio no Mercado Livre passa pelo fulfillment (Full) " +
          "contra as demais modalidades logísticas de envio."
        }
        scopeLine={escopo}
        status={statusBadge}
        filters={filtros}
      />

      {/* --- Avisos permanentes de contrato ------------------------------ */}
      <div className="flex flex-col gap-2">
        <Aviso tom="alerta">{AVISO_MANUAL_SNAPSHOT}</Aviso>
        <Aviso tom="info">{AVISO_SEM_ESTOQUE}</Aviso>
        <Aviso tom="info">{AVISO_JANELA}</Aviso>
        <Aviso tom="info">{AVISO_COBERTURA}</Aviso>
        {listaAlertas.map((a) => (
          <Aviso key={a} tom="alerta">
            {a}
          </Aviso>
        ))}
      </div>

      {/* --- Estados ----------------------------------------------------- */}
      {erroJanela && (
        <div
          role="alert"
          className="border border-amber-300 bg-amber-50 rounded-xl px-4 py-3 text-sm text-amber-900"
        >
          {erroJanela}
        </div>
      )}

      {!erroJanela && erro && (
        <div
          role="alert"
          className="border border-red-300 bg-red-50 rounded-xl px-4 py-3 text-sm text-red-800"
        >
          {erro}
        </div>
      )}

      {!erroJanela && !erro && carregando && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <div
              key={i}
              className="h-24 rounded-xl bg-slate-100 animate-pulse"
              aria-hidden="true"
            />
          ))}
          <p className="sr-only" role="status">
            Carregando indicadores de Full do Mercado Livre.
          </p>
        </div>
      )}

      {!erroJanela && !erro && !carregando && vazio && (
        <div className="border border-slate-200 bg-white rounded-xl px-4 py-10 text-center">
          <p className="text-sm font-medium text-slate-700">
            Nenhum pedido no período e filtro selecionados.
          </p>
          <p className="text-xs text-slate-500 mt-1">
            Isto é ausência de venda, não falha de carga. A série publicada
            começa em 01/08/2025.
          </p>
        </div>
      )}

      {/* --- Conteudo ---------------------------------------------------- */}
      {!erroJanela && !erro && !carregando && dados && !vazio && (
        <>
          <section aria-labelledby="full-ml-kpis" className="flex flex-col gap-3">
            <h2 id="full-ml-kpis" className="text-sm font-semibold text-slate-800">
              Participação do Full
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
              <KpiCard
                label="Share de GMV Full"
                value={fmtShare(dados.share_full_gmv)}
                subvalue={`Não-Full: ${fmtGmv(naoFull?.paid_gmv ?? null)}`}
              />
              <KpiCard
                label="Share de pedidos Full"
                value={fmtShare(dados.share_full_orders)}
                subvalue={`Não-Full: ${fmtCount(naoFull?.paid_orders ?? null)} pedidos`}
                accent="bg-sky-600"
              />
              <KpiCard
                label="Share de unidades Full"
                value={fmtShare(dados.share_full_units)}
                subvalue={`Não-Full: ${fmtCount(naoFull?.paid_units ?? null)} unidades`}
                accent="bg-teal-600"
              />
              <KpiCard
                label="Cancelamento Full"
                value={fmtShare(full?.cancellation_rate ?? null)}
                subvalue={`Não-Full: ${fmtShare(naoFull?.cancellation_rate ?? null)}`}
                accent="bg-rose-600"
              />
              <KpiCard
                label="GMV Full"
                value={fmtGmv(full?.paid_gmv ?? null)}
                subvalue={`Total pago: ${fmtGmv(dados.paid_gmv_total)}`}
              />
              <KpiCard
                label="Pedidos Full"
                value={fmtCount(full?.paid_orders ?? null)}
                subvalue={`Total pago: ${fmtCount(dados.paid_orders_total)}`}
                accent="bg-sky-600"
              />
              <KpiCard
                label="Unidades Full"
                value={fmtCount(full?.paid_units ?? null)}
                subvalue={`Total pago: ${fmtCount(dados.paid_units_total)}`}
                accent="bg-teal-600"
              />
              <KpiCard
                label="Entrega Full"
                value={fmtTempo(full?.delivery ?? null)}
                subvalue={`Não-Full: ${fmtTempo(naoFull?.delivery ?? null)}`}
                accent="bg-indigo-600"
              />
            </div>
          </section>

          {/* --- Tendencia ------------------------------------------------ */}
          <section
            aria-labelledby="full-ml-trend"
            className="bg-white border border-slate-200 rounded-xl p-4 flex flex-col gap-3"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 id="full-ml-trend" className="text-sm font-semibold text-slate-800">
                Tendência diária do share de Full
              </h2>
              <div className="flex gap-1" role="group" aria-label="Métrica da série">
                {METRICAS.map((m) => (
                  <button
                    key={m.key}
                    type="button"
                    aria-pressed={metrica === m.key}
                    onClick={() => setMetrica(m.key)}
                    className={
                      `${CONTROLE} font-medium ` +
                      (metrica === m.key
                        ? "bg-violet-600 text-white border-violet-600"
                        : "hover:bg-slate-50")
                    }
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>
            <MLFulfillmentTrend pontos={serie} metrica={metrica} />
          </section>

          {/* --- Decomposicao por classe ---------------------------------- */}
          <section
            aria-labelledby="full-ml-classes"
            className="bg-white border border-slate-200 rounded-xl p-4 flex flex-col gap-3"
          >
            <h2 id="full-ml-classes" className="text-sm font-semibold text-slate-800">
              Decomposição por modalidade
            </h2>
            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[720px]">
                <caption className="sr-only">
                  GMV, pedidos, unidades e cancelamento por modalidade logística
                </caption>
                <thead>
                  <tr className="border-b border-slate-200">
                    <th scope="col" className="text-left py-2 pr-3 font-semibold text-slate-700">Modalidade</th>
                    <th scope="col" className="text-right py-2 px-3 font-semibold text-slate-700">GMV pago</th>
                    <th scope="col" className="text-right py-2 px-3 font-semibold text-slate-700">Pedidos</th>
                    <th scope="col" className="text-right py-2 px-3 font-semibold text-slate-700">Unidades</th>
                    <th scope="col" className="text-right py-2 px-3 font-semibold text-slate-700">Elegíveis</th>
                    <th scope="col" className="text-right py-2 px-3 font-semibold text-slate-700">Cancelados</th>
                    <th scope="col" className="text-right py-2 px-3 font-semibold text-slate-700">Taxa cancel.</th>
                    <th scope="col" className="text-right py-2 px-3 font-semibold text-slate-700">Handling</th>
                    <th scope="col" className="text-right py-2 pl-3 font-semibold text-slate-700">Entrega</th>
                  </tr>
                </thead>
                <tbody>
                  {(["full", "non_full", "unknown"] as FulfillmentClass[]).map((c) => {
                    const linha = byClass(dados, c);
                    if (!linha) return null;
                    return (
                      <tr key={c} className="border-b border-slate-100">
                        <th scope="row" className="text-left py-2 pr-3 font-medium text-slate-800">
                          {CLASS_LABEL[c]}
                        </th>
                        <td className="text-right py-2 px-3 tabular-nums">{fmtGmv(linha.paid_gmv)}</td>
                        <td className="text-right py-2 px-3 tabular-nums">{fmtCount(linha.paid_orders)}</td>
                        <td className="text-right py-2 px-3 tabular-nums">{fmtCount(linha.paid_units)}</td>
                        <td className="text-right py-2 px-3 tabular-nums text-slate-600">{fmtCount(linha.eligible_orders)}</td>
                        <td className="text-right py-2 px-3 tabular-nums text-slate-600">{fmtCount(linha.cancelled_orders)}</td>
                        <td className="text-right py-2 px-3 tabular-nums">{fmtShare(linha.cancellation_rate)}</td>
                        <td className="text-right py-2 px-3 tabular-nums text-slate-600">{fmtTempo(linha.handling)}</td>
                        <td className="text-right py-2 pl-3 tabular-nums text-slate-600">{fmtTempo(linha.delivery)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <dl className="text-xs text-slate-600 flex flex-col gap-1">
              {(["full", "non_full", "unknown"] as FulfillmentClass[]).map((c) => (
                <div key={c} className="flex gap-2">
                  <dt className="font-semibold shrink-0">{CLASS_LABEL[c]}:</dt>
                  <dd>{CLASS_DESCRICAO[c]}</dd>
                </div>
              ))}
            </dl>
          </section>

          {/* --- Composicao por tipo logistico bruto ---------------------- */}
          {dados.by_logistic_type.length > 0 && (
            <section
              aria-labelledby="full-ml-tipos"
              className="bg-white border border-slate-200 rounded-xl p-4 flex flex-col gap-3"
            >
              <h2 id="full-ml-tipos" className="text-sm font-semibold text-slate-800">
                Composição por tipo logístico registrado
              </h2>
              <p className="text-xs text-slate-600">
                Rótulo bruto do Mercado Livre, preservado inclusive quando já
                extinto. Não-Full não equivale a uma modalidade única.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[480px]">
                  <caption className="sr-only">GMV por tipo logístico registrado</caption>
                  <thead>
                    <tr className="border-b border-slate-200">
                      <th scope="col" className="text-left py-2 pr-3 font-semibold text-slate-700">Tipo</th>
                      <th scope="col" className="text-left py-2 px-3 font-semibold text-slate-700">Classe</th>
                      <th scope="col" className="text-right py-2 px-3 font-semibold text-slate-700">GMV pago</th>
                      <th scope="col" className="text-right py-2 pl-3 font-semibold text-slate-700">Pedidos</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dados.by_logistic_type.map((t) => (
                      <tr key={`${t.logistic_type_original}-${t.fulfillment_class}`} className="border-b border-slate-100">
                        <td className="py-2 pr-3 font-mono text-xs text-slate-800">{t.logistic_type_original}</td>
                        <td className="py-2 px-3 text-slate-600">{CLASS_LABEL[t.fulfillment_class]}</td>
                        <td className="text-right py-2 px-3 tabular-nums">{fmtGmv(t.paid_gmv)}</td>
                        <td className="text-right py-2 pl-3 tabular-nums">{fmtCount(t.paid_orders)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {/* --- Anuncios -------------------------------------------------- */}
          <section
            aria-labelledby="full-ml-anuncios"
            className="bg-white border border-slate-200 rounded-xl p-4 flex flex-col gap-3"
          >
            <h2 id="full-ml-anuncios" className="text-sm font-semibold text-slate-800">
              Anúncios por comportamento
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="border border-slate-200 rounded-lg p-3">
                <p className="text-xs font-semibold text-slate-600 uppercase">Somente Full</p>
                <p className="text-2xl font-bold tabular-nums text-slate-900">{fmtCount(dados.listings.full_only)}</p>
              </div>
              <div className="border border-amber-300 bg-amber-50/40 rounded-lg p-3">
                <p className="text-xs font-semibold text-amber-900 uppercase">Mistos</p>
                <p className="text-2xl font-bold tabular-nums text-slate-900">{fmtCount(dados.listings.mixed)}</p>
                <p className="text-xs text-slate-600 mt-1">
                  Venderam pelos dois modais na janela: Full não é atributo do anúncio.
                </p>
              </div>
              <div className="border border-slate-200 rounded-lg p-3">
                <p className="text-xs font-semibold text-slate-600 uppercase">Somente não-Full</p>
                <p className="text-2xl font-bold tabular-nums text-slate-900">{fmtCount(dados.listings.non_full_only)}</p>
              </div>
            </div>

            {dados.migration_opportunities.length > 0 && (
              <>
                <h3 className="text-xs font-semibold text-slate-700 mt-1">
                  Maior GMV fora do Full ({dados.migration_opportunities.length} anúncios)
                </h3>
                <div className="max-h-96 overflow-y-auto overflow-x-auto border border-slate-200 rounded-lg">
                  <table className="w-full text-sm min-w-[560px]">
                    <caption className="sr-only">Anúncios com GMV fora do Full</caption>
                    <thead className="bg-slate-50 sticky top-0">
                      <tr>
                        <th scope="col" className="text-left px-3 py-2 font-semibold text-slate-700">Anúncio</th>
                        <th scope="col" className="text-left px-3 py-2 font-semibold text-slate-700">Marca</th>
                        <th scope="col" className="text-left px-3 py-2 font-semibold text-slate-700">Comportamento</th>
                        <th scope="col" className="text-right px-3 py-2 font-semibold text-slate-700">GMV não-Full</th>
                        <th scope="col" className="text-right px-3 py-2 font-semibold text-slate-700">GMV Full</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dados.migration_opportunities.map((o) => (
                        <tr key={`${o.brand}-${o.item_id}`} className="border-t border-slate-100">
                          <td className="px-3 py-2 font-mono text-xs text-slate-800">{o.item_id}</td>
                          <td className="px-3 py-2 text-slate-700">{o.brand}</td>
                          <td className="px-3 py-2 text-slate-600">
                            {o.listing_class === "mixed" ? "Misto" : "Somente não-Full"}
                          </td>
                          <td className="text-right px-3 py-2 tabular-nums font-medium">{fmtGmv(o.non_full_gmv)}</td>
                          <td className="text-right px-3 py-2 tabular-nums text-slate-600">{fmtGmv(o.full_gmv)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </section>

          {/* --- Qualidade ------------------------------------------------- */}
          <section
            aria-labelledby="full-ml-qualidade"
            className="bg-white border border-slate-200 rounded-xl p-4 flex flex-col gap-2"
          >
            <h2 id="full-ml-qualidade" className="text-sm font-semibold text-slate-800">
              Qualidade e limitações
            </h2>
            <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
              <div>
                <dt className="text-xs text-slate-600">Pedidos sem envio</dt>
                <dd className="font-semibold tabular-nums">{fmtCount(dados.quality.unknown_logistic_type_orders)}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-600">Envios sem itens</dt>
                <dd className="font-semibold tabular-nums">{fmtCount(dados.quality.missing_shipping_items)}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-600">Janela completa</dt>
                <dd className="font-semibold">{dados.quality.is_complete ? "Sim" : "Não"}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-600">Última carga</dt>
                <dd className="font-semibold text-xs">
                  {dados.freshness.refreshed_at?.slice(0, 16).replace("T", " ") ?? SEM_DADO}
                </dd>
              </div>
            </dl>
            <ul className="text-xs text-slate-600 list-disc pl-5 flex flex-col gap-1 mt-1">
              {dados.quality.limitations.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
            {desconhecido && desconhecido.eligible_orders > 0 && (
              <Aviso tom="alerta">
                {fmtCount(desconhecido.eligible_orders)} pedido(s) em Desconhecido.{" "}
                {CLASS_DESCRICAO.unknown}
              </Aviso>
            )}
          </section>
        </>
      )}
    </PageContainer>
  );
}

export default function FullMLPage() {
  return (
    <Suspense fallback={<PageContainer><div className="h-64" /></PageContainer>}>
      <FullMLPageInner />
    </Suspense>
  );
}
