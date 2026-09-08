"use client";

/**
 * Gate AVH-4B-S Task 2/2 — /referencias-externas/avoe
 *
 * Visualiza o ultimo snapshot manual do Avoe Hub. Pagina PROPRIA, fora de
 * /canais e da Gerencial, porque estes numeros nao sao KPI da Torre e nao
 * podem dividir superficie com metrica oficial.
 *
 * NADA e' calculado aqui. Nao existe total, subtotal, atingimento, margem,
 * variacao nem comparacao — nem no codigo, nem na tela. A pagina apresenta o
 * contrato recebido, filtra no cliente e formata.
 *
 * Persona: quem precisa consultar a meta que a agencia digitou e o valor que
 * ela informou nos canais que a Torre nao integra, sabendo o tempo todo que a
 * fonte e' de terceiro e a carga e' manual.
 */

import { useCallback, useEffect, useId, useMemo, useState } from "react";
import {
  fetchAvoeSnapshot,
  AvoeSnapshotError,
  COPY,
  FILTROS_VAZIOS,
  NAO_INFORMADO,
  PAGE_TITLE,
  SELO_FONTE_EXTERNA,
  TODOS,
  buildProvenance,
  buildWarnings,
  canalOptions,
  channelLabel,
  competenciaOptions,
  coverageLabel,
  coverageNotes,
  describeFilters,
  filterChannels,
  filterTargets,
  formatBrlExato,
  formatCompetencia,
  formatContagem,
  formatData,
  formatValorInformado,
  isCapturaAntiga,
  marcaOptions,
  resolvePhase,
  unavailableLabel,
  type AvoeFilters,
  type AvoeSnapshotResponse,
} from "@/lib/api-client";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import TableScrollHint from "@/components/TableScrollHint";

const CELULA = "px-3 py-2 text-sm";
const CABECALHO =
  "px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600";

/** Alvo de toque >= 44px em todo controle interativo (a11y do projeto). */
const CONTROLE =
  "min-h-[44px] rounded-lg border border-slate-300 bg-white px-3 text-sm " +
  "text-slate-900 focus:outline-none focus:ring-2 focus:ring-sky-500";

export default function ReferenciasExternasAvoePage() {
  const [data, setData] = useState<AvoeSnapshotResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [erro, setErro] = useState(false);
  const [filtros, setFiltros] = useState<AvoeFilters>(FILTROS_VAZIOS);

  const idCompetencia = useId();
  const idMarca = useId();
  const idCanal = useId();

  const carregar = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setErro(false);
    fetchAvoeSnapshot(signal)
      .then((resposta) => {
        setData(resposta);
        setLoading(false);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        // A mensagem tecnica NAO vai para a tela: o backend ja devolve texto
        // fixo em 500, e um erro de rede nao tem nada de util para o operador.
        setData(null);
        setErro(true);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    carregar(ctrl.signal);
    return () => ctrl.abort();
  }, [carregar]);

  const fase = resolvePhase({ loading, error: erro, data });

  const metas = useMemo(
    () => (data ? filterTargets(data.targets, filtros) : []),
    [data, filtros],
  );
  const canais = useMemo(
    () => (data ? filterChannels(data.extra_channels, filtros) : []),
    [data, filtros],
  );

  const opcoesCompetencia = useMemo(() => {
    if (!data) return competenciaOptions([]);
    return competenciaOptions([
      ...data.targets.map((t) => t.ref_month),
      ...data.extra_channels.map((c) => c.ref_month),
    ]);
  }, [data]);

  const opcoesMarca = useMemo(() => {
    if (!data) return marcaOptions([]);
    return marcaOptions([
      ...data.targets.map((t) => t.brand),
      ...data.extra_channels.map((c) => c.brand),
    ]);
  }, [data]);

  const opcoesCanal = useMemo(
    () => canalOptions(data ? data.extra_channels.map((c) => c.channel) : []),
    [data],
  );

  const avisos = useMemo(() => (data ? buildWarnings(data) : []), [data]);
  const proveniencia = useMemo(
    () => (data ? buildProvenance(data.meta) : []),
    [data],
  );

  const antiga = data ? isCapturaAntiga(data.meta.captured_age_days) : false;

  return (
    <PageContainer>
      <PageHeader
        title={PAGE_TITLE}
        subtitle={COPY.subtitulo}
        status={
          <span
            className="inline-flex min-h-[32px] items-center rounded-full border border-amber-300 bg-amber-50 px-3 text-xs font-semibold text-amber-900"
            data-testid="selo-fonte-externa"
          >
            {SELO_FONTE_EXTERNA}
          </span>
        }
      />

      {fase === "loading" && (
        <section
          aria-busy="true"
          aria-live="polite"
          className="rounded-xl border border-slate-200 bg-white p-6"
        >
          <p className="text-sm text-slate-600">{COPY.carregando}</p>
        </section>
      )}

      {fase === "error" && (
        <section
          role="alert"
          className="rounded-xl border border-rose-300 bg-rose-50 p-6"
        >
          <h2 className="text-sm font-semibold text-rose-900">
            Snapshot indisponível
          </h2>
          <p className="mt-2 text-sm text-rose-900">{COPY.erro}</p>
          <button
            type="button"
            onClick={() => carregar()}
            className="mt-4 min-h-[44px] min-w-[44px] rounded-lg border border-rose-300 bg-white px-4 text-sm font-medium text-rose-900 focus:outline-none focus:ring-2 focus:ring-rose-500"
          >
            Tentar novamente
          </button>
        </section>
      )}

      {(fase === "available" || fase === "unavailable") && data && (
        <>
          {/* ---------------- 1. Proveniencia ---------------- */}
          <section
            aria-labelledby="avoe-proveniencia"
            className="rounded-xl border border-slate-200 bg-white p-4 sm:p-6"
          >
            <h2
              id="avoe-proveniencia"
              className="text-sm font-semibold text-slate-900"
            >
              Proveniência da captura
            </h2>
            {antiga && (
              <p
                className="mt-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
                data-testid="aviso-captura-antiga"
              >
                {COPY.capturaAntiga}
              </p>
            )}
            <dl className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {proveniencia.map((item) => (
                <div key={item.label} className="min-w-0">
                  <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
                    {item.label}
                  </dt>
                  <dd className="mt-1 break-words text-sm font-semibold text-slate-900">
                    {item.value}
                  </dd>
                  {item.note && (
                    <p className="mt-1 break-words text-xs text-slate-600">
                      {item.note}
                    </p>
                  )}
                </div>
              ))}
            </dl>
          </section>

          {/* ---------------- 2. Avisos ---------------- */}
          <section
            aria-labelledby="avoe-avisos"
            className="rounded-xl border border-amber-300 bg-amber-50 p-4 sm:p-6"
          >
            <h2
              id="avoe-avisos"
              className="text-sm font-semibold text-amber-900"
            >
              Antes de usar estes números
            </h2>
            {/* Lista simples, sem acordeao: nenhum aviso relevante fica oculto. */}
            <ul className="mt-3 space-y-2" data-testid="lista-avisos">
              {avisos.map((aviso) => (
                <li
                  key={aviso}
                  className="flex gap-2 text-sm leading-relaxed text-amber-900"
                >
                  <span aria-hidden="true">•</span>
                  <span className="min-w-0 break-words">{aviso}</span>
                </li>
              ))}
            </ul>
            <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-amber-900">
              Cobertura documentada
            </h3>
            <ul className="mt-2 space-y-2" data-testid="notas-cobertura">
              {coverageNotes().map((nota) => (
                <li
                  key={nota}
                  className="flex gap-2 text-sm leading-relaxed text-amber-900"
                >
                  <span aria-hidden="true">•</span>
                  <span className="min-w-0 break-words">{nota}</span>
                </li>
              ))}
            </ul>
          </section>

          {fase === "unavailable" ? (
            <section
              aria-labelledby="avoe-indisponivel"
              className="rounded-xl border border-slate-300 bg-slate-50 p-4 sm:p-6"
              data-testid="estado-indisponivel"
            >
              <h2
                id="avoe-indisponivel"
                className="text-sm font-semibold text-slate-900"
              >
                Nenhuma captura válida para exibir
              </h2>
              <p className="mt-2 text-sm text-slate-700">
                {unavailableLabel(data.meta.unavailable_reason)}
              </p>
              <p className="mt-2 text-xs text-slate-600">
                Não há tabela de metas nem de canais nesta situação — ausência de
                captura não é o mesmo que valores iguais a zero.
              </p>
            </section>
          ) : (
            <>
              {/* ---------------- Filtros ---------------- */}
              <section
                aria-labelledby="avoe-filtros"
                className="rounded-xl border border-slate-200 bg-white p-4 sm:p-6"
              >
                <h2
                  id="avoe-filtros"
                  className="text-sm font-semibold text-slate-900"
                >
                  Filtros
                </h2>
                <p className="mt-1 text-xs text-slate-600">
                  Aplicados no navegador, sobre as linhas já recebidas. Nenhuma
                  nova consulta à API e nenhuma agregação entre linhas.
                </p>
                <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:flex-wrap">
                  <div className="flex min-w-0 flex-col gap-1">
                    <label
                      htmlFor={idCompetencia}
                      className="text-xs font-medium text-slate-700"
                    >
                      Competência
                    </label>
                    <select
                      id={idCompetencia}
                      className={CONTROLE}
                      value={filtros.competencia}
                      onChange={(e) =>
                        setFiltros((f) => ({ ...f, competencia: e.target.value }))
                      }
                    >
                      {opcoesCompetencia.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="flex min-w-0 flex-col gap-1">
                    <label
                      htmlFor={idMarca}
                      className="text-xs font-medium text-slate-700"
                    >
                      Marca
                    </label>
                    <select
                      id={idMarca}
                      className={CONTROLE}
                      value={filtros.marca}
                      onChange={(e) =>
                        setFiltros((f) => ({ ...f, marca: e.target.value }))
                      }
                    >
                      {opcoesMarca.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="flex min-w-0 flex-col gap-1">
                    <label
                      htmlFor={idCanal}
                      className="text-xs font-medium text-slate-700"
                    >
                      Canal <span className="text-slate-500">(só canais)</span>
                    </label>
                    <select
                      id={idCanal}
                      className={CONTROLE}
                      value={filtros.canal}
                      onChange={(e) =>
                        setFiltros((f) => ({ ...f, canal: e.target.value }))
                      }
                    >
                      {opcoesCanal.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  {(filtros.competencia !== TODOS ||
                    filtros.marca !== TODOS ||
                    filtros.canal !== TODOS) && (
                    <div className="flex items-end">
                      <button
                        type="button"
                        onClick={() => setFiltros(FILTROS_VAZIOS)}
                        className="min-h-[44px] min-w-[44px] rounded-lg border border-slate-300 bg-white px-4 text-sm font-medium text-slate-800 focus:outline-none focus:ring-2 focus:ring-sky-500"
                      >
                        Limpar filtros
                      </button>
                    </div>
                  )}
                </div>
              </section>

              {/* ---------------- 3. Metas ---------------- */}
              <section
                aria-labelledby="avoe-metas"
                className="rounded-xl border border-slate-200 bg-white p-4 sm:p-6"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2
                    id="avoe-metas"
                    className="text-sm font-semibold text-slate-900"
                  >
                    Metas informadas pela Avoe
                  </h2>
                  <p className="text-xs text-slate-600">
                    {formatContagem(metas.length)} de{" "}
                    {formatContagem(data.targets.length)} linhas ·{" "}
                    {describeFilters(filtros, false)}
                  </p>
                </div>
                <p className="mt-1 text-xs text-slate-600">
                  Meta comercial por competência e marca. Sem realizado, sem
                  atingimento, sem total.
                </p>
                {metas.length === 0 ? (
                  <p
                    className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-4 text-sm text-slate-700"
                    data-testid="metas-vazio"
                  >
                    {COPY.filtroSemResultado}
                  </p>
                ) : (
                  /* `TableScrollHint` ja aplica `overflow-x-auto`; a altura
                     maxima e o eixo Y vem por `className`, sem aninhar um
                     segundo container de rolagem. */
                  <div className="mt-4">
                    <TableScrollHint className="max-h-[26rem] overflow-y-auto rounded-lg border border-slate-200">
                      <table className="w-full min-w-[44rem] border-collapse">
                        <caption className="sr-only">
                          Metas informadas pela Avoe por competência e marca
                        </caption>
                        <thead className="sticky top-0 bg-slate-100">
                          <tr>
                            <th scope="col" className={CABECALHO}>
                              Competência
                            </th>
                            <th scope="col" className={CABECALHO}>
                              Marca
                            </th>
                            <th scope="col" className={`${CABECALHO} text-right`}>
                              Meta
                            </th>
                            <th scope="col" className={CABECALHO}>
                              Moeda
                            </th>
                            <th scope="col" className={CABECALHO}>
                              Registrado na origem
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {metas.map((t) => (
                            <tr
                              key={`${t.ref_month}|${t.brand}`}
                              className="border-t border-slate-200"
                            >
                              <td className={CELULA}>
                                {formatCompetencia(t.ref_month)}
                              </td>
                              <td className={`${CELULA} font-medium`}>{t.brand}</td>
                              <td
                                className={`${CELULA} text-right tabular-nums`}
                              >
                                {formatBrlExato(t.target_amount)}
                              </td>
                              <td className={CELULA}>
                                <span className="text-slate-900">
                                  {t.currency_code}
                                </span>
                                {t.currency_status !== "confirmed" && (
                                  <span
                                    className="ml-2 inline-flex items-center rounded border border-amber-300 bg-amber-50 px-1.5 text-xs text-amber-900"
                                    title={t.currency_warning ?? undefined}
                                  >
                                    assumida
                                  </span>
                                )}
                              </td>
                              <td className={`${CELULA} text-slate-700`}>
                                {t.source_recorded_at === null
                                  ? NAO_INFORMADO
                                  : formatData(t.source_recorded_at)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </TableScrollHint>
                  </div>
                )}
              </section>

              {/* ---------------- 4. Canais adicionais ---------------- */}
              <section
                aria-labelledby="avoe-canais"
                className="rounded-xl border border-slate-200 bg-white p-4 sm:p-6"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2
                    id="avoe-canais"
                    className="text-sm font-semibold text-slate-900"
                  >
                    Canais adicionais
                  </h2>
                  <p className="text-xs text-slate-600">
                    {formatContagem(canais.length)} de{" "}
                    {formatContagem(data.extra_channels.length)} linhas ·{" "}
                    {describeFilters(filtros, true)}
                  </p>
                </div>
                <p className="mt-1 text-xs text-slate-600">
                  Canais que a Torre não integra. {COPY.definicaoNaoConfirmada}
                </p>
                {canais.length === 0 ? (
                  <p
                    className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-4 text-sm text-slate-700"
                    data-testid="canais-vazio"
                  >
                    {COPY.filtroSemResultado}
                  </p>
                ) : (
                  <div className="mt-4">
                    <TableScrollHint className="max-h-[32rem] overflow-y-auto rounded-lg border border-slate-200">
                      <table className="w-full min-w-[56rem] border-collapse">
                        <caption className="sr-only">
                          Valores informados pela Avoe em canais adicionais
                        </caption>
                        <thead className="sticky top-0 bg-slate-100">
                          <tr>
                            <th scope="col" className={CABECALHO}>
                              Competência
                            </th>
                            <th scope="col" className={CABECALHO}>
                              Marca
                            </th>
                            <th scope="col" className={CABECALHO}>
                              Canal
                            </th>
                            <th scope="col" className={`${CABECALHO} text-right`}>
                              {COPY.rotuloValorCanal}
                            </th>
                            <th scope="col" className={`${CABECALHO} text-right`}>
                              Dias cobertos
                            </th>
                            <th scope="col" className={CABECALHO}>
                              Cobertura
                            </th>
                            <th scope="col" className={CABECALHO}>
                              Janela
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {canais.map((c) => (
                            <tr
                              key={`${c.ref_month}|${c.brand}|${c.channel}`}
                              className="border-t border-slate-200"
                            >
                              <td className={CELULA}>
                                {formatCompetencia(c.ref_month)}
                              </td>
                              <td className={`${CELULA} font-medium`}>{c.brand}</td>
                              <td className={CELULA}>
                                {channelLabel(c.channel)}
                                <span className="ml-2 inline-flex items-center rounded border border-slate-300 bg-slate-50 px-1.5 text-xs text-slate-700">
                                  proxy
                                </span>
                              </td>
                              <td
                                className={`${CELULA} text-right tabular-nums`}
                              >
                                {c.reported_amount === null ? (
                                  <span
                                    className="text-slate-500"
                                    data-testid="valor-nao-informado"
                                  >
                                    {NAO_INFORMADO}
                                  </span>
                                ) : (
                                  <span className="text-slate-900">
                                    {formatValorInformado(c.reported_amount)}
                                  </span>
                                )}
                              </td>
                              <td
                                className={`${CELULA} text-right tabular-nums`}
                              >
                                {formatContagem(c.days_covered)}
                              </td>
                              <td className={CELULA}>
                                {coverageLabel(c.coverage_status)}
                              </td>
                              <td className={`${CELULA} text-slate-700`}>
                                {formatData(c.first_business_date)} a{" "}
                                {formatData(c.last_business_date)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </TableScrollHint>
                  </div>
                )}
                <p className="mt-3 text-xs text-slate-600">
                  Sem total consolidado, de propósito: somar estes valores entre
                  si ou com o GMV oficial produziria um número que não existe em
                  fonte nenhuma.
                </p>
              </section>
            </>
          )}
        </>
      )}
    </PageContainer>
  );
}
