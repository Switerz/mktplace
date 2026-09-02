"use client";

/**
 * Gate PMA-3 — /monitoramento-preco
 *
 * Compara o PRECO ANUNCIADO das lojas PROPRIAS no Mercado Livre com o PRECO
 * SUGERIDO DE REVENDA (PDV) das tabelas B2B.
 *
 * Persona: operador de Trade/Pricing que precisa achar rapidamente anuncios
 * proprios abaixo da referencia e abrir o anuncio para revisao humana.
 *
 * NADA e' calculado aqui. Diferenca, situacao e match vem do backend; esta
 * pagina apenas apresenta. Nao ha acao de sancao, bloqueio, denuncia,
 * notificacao ou alteracao de preco — a tela e' observacional.
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  fetchMonitoramentoPreco,
  MonitoramentoPrecoError,
  MONITORAMENTO_PRECO_MAX_LIMIT,
  type MonitoramentoPrecoResponse,
  type MonitoramentoPrecoRow,
  type ComparisonStatus,
} from "@/lib/api-client";
import {
  AVISO_OBSERVACIONAL,
  COMPOSICAO_INDISPONIVEL,
  INDISPONIVEL,
  NOTA_DENOMINADOR,
  STATUS_ORDER,
  STATUS_TONE,
  avisoTruncamento,
  brandLabel,
  buildKpiViews,
  buildMonitoramentoRequestKey,
  buildQualidadeViews,
  calcPaginacao,
  fmtCapturaPreco,
  fmtContagem,
  fmtData,
  fmtDiferenca,
  fmtInstanteBrt,
  fmtMoeda,
  fmtPercentual,
  listingStatusLabel,
  matchLabel,
  matchQualityLabel,
  statusLabel,
  tituloLinha,
  urlAnuncioSegura,
} from "@/lib/monitoramento-preco";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import TableScrollHint from "@/components/TableScrollHint";
import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import { computeRequestStatus } from "@/lib/request-freshness";

const LIMITE = MONITORAMENTO_PRECO_MAX_LIMIT;

const TONE_CLASS: Record<"attention" | "neutral" | "muted", string> = {
  attention: "bg-amber-50 text-amber-800 border-amber-200",
  neutral: "bg-emerald-50 text-emerald-800 border-emerald-200",
  muted: "bg-slate-50 text-slate-600 border-slate-200",
};

function StatusBadge({ status }: { status: ComparisonStatus }) {
  return (
    <span
      className={`inline-block text-xs font-semibold border rounded-full px-2 py-0.5 whitespace-nowrap ${TONE_CLASS[STATUS_TONE[status]]}`}
    >
      {statusLabel(status)}
    </span>
  );
}

export default function MonitoramentoPrecoPage() {
  const buscaId = useId();
  const marcaId = useId();
  const situacaoId = useId();

  const [brand, setBrand] = useState("");
  const [status, setStatus] = useState("");
  const [buscaInput, setBuscaInput] = useState("");
  const [productQuery, setProductQuery] = useState("");
  const [offset, setOffset] = useState(0);

  const [dados, setDados] = useState<MonitoramentoPrecoResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [resolvedKey, setResolvedKey] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  const [linhaAberta, setLinhaAberta] = useState<MonitoramentoPrecoRow | null>(null);

  const requestKey = useMemo(
    () => buildMonitoramentoRequestKey({ brand, status, productQuery, limit: LIMITE, offset }),
    [brand, status, productQuery, offset],
  );

  // Guarda de frescor: uma resposta antiga que chegue depois de uma nova
  // requisicao NAO pode sobrescrever o estado. `latestKey` e' comparada no
  // retorno; o AbortController cancela a anterior.
  const latestKey = useRef(requestKey);

  useEffect(() => {
    latestKey.current = requestKey;
    const controller = new AbortController();
    setLoading(true);
    setErro(null);

    fetchMonitoramentoPreco(
      {
        brand: brand || undefined,
        status: status || undefined,
        productQuery: productQuery || undefined,
        limit: LIMITE,
        offset,
      },
      controller.signal,
    )
      .then((res) => {
        if (latestKey.current !== requestKey) return;
        setDados(res);
        setErro(null);
        setResolvedKey(requestKey);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (latestKey.current !== requestKey) return;
        // Sem fallback em mock: indisponibilidade e' declarada.
        setDados(null);
        setErro(
          err instanceof MonitoramentoPrecoError
            ? err.message
            : "Falha inesperada ao carregar o monitoramento.",
        );
        setResolvedKey(requestKey);
        setLoading(false);
      });

    return () => controller.abort();
    // `requestKey` ja e' derivada de marca, situacao, busca, limite e offset
    // via `useMemo`. Listar os constituintes de novo faria o efeito disparar
    // duas vezes na mesma troca de filtro.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey, retryKey]);

  const estado = computeRequestStatus({
    loading,
    error: erro != null,
    resolvedKey,
    requestKey,
  });

  // Troca de qualquer filtro volta para a primeira pagina.
  const trocaFiltro = useCallback((aplicar: () => void) => {
    setOffset(0);
    aplicar();
  }, []);

  const submeterBusca = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      trocaFiltro(() => setProductQuery(buscaInput.trim()));
    },
    [buscaInput, trocaFiltro],
  );

  const limparFiltros = useCallback(() => {
    setOffset(0);
    setBrand("");
    setStatus("");
    setBuscaInput("");
    setProductQuery("");
  }, []);

  const temFiltro = brand !== "" || status !== "" || productQuery !== "";

  const meta = dados?.meta ?? null;
  const kpis = dados?.kpis ?? null;
  const linhas = estado.fresh && dados ? dados.rows : [];

  const kpiViews = kpis ? buildKpiViews(kpis) : [];
  const qualidadeViews = kpis ? buildQualidadeViews(kpis) : [];
  const paginacao = dados
    ? calcPaginacao(dados.total_count, dados.returned_count, LIMITE, offset)
    : null;
  const truncamento = dados
    ? avisoTruncamento(dados.truncated, dados.returned_count, dados.total_count)
    : null;

  const linkAnuncio = linhaAberta ? urlAnuncioSegura(linhaAberta.permalink) : null;

  return (
    <PageContainer>
      <PageHeader
        title="Monitoramento de preços"
        subtitle="Compara os preços anunciados das lojas próprias no Mercado Livre com o preço sugerido de revenda (PDV) das tabelas B2B."
      />

      {/* ---------------- contexto e frescor ---------------- */}
      <section
        aria-label="Contexto dos dados"
        className="bg-white border border-violet-100 rounded-2xl shadow-sm p-4 mb-4"
      >
        <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-sm">
          <div>
            <dt className="text-slate-500 text-xs uppercase tracking-wide">Canal</dt>
            <dd className="font-semibold text-slate-800">Mercado Livre</dd>
          </div>
          <div>
            <dt className="text-slate-500 text-xs uppercase tracking-wide">Data observada</dt>
            <dd className="font-semibold text-slate-800">
              {fmtData(meta?.observed_ref_date)}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500 text-xs uppercase tracking-wide">Dados atualizados em</dt>
            <dd className="font-semibold text-slate-800">
              {fmtInstanteBrt(meta?.refreshed_at)}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500 text-xs uppercase tracking-wide">Referência capturada em</dt>
            <dd className="font-semibold text-slate-800">
              {fmtInstanteBrt(meta?.reference_captured_at)}
            </dd>
          </div>
        </dl>
        {meta && (
          <p className="text-xs text-slate-500 mt-3">
            Marcas monitoradas: {meta.monitored_brands.map(brandLabel).join(", ")}.
            {meta.no_reference_brands.length > 0 && (
              <> Sem tabela de referência: {meta.no_reference_brands.map(brandLabel).join(", ")}.</>
            )}
            {Object.keys(meta.out_of_scope_brands).length > 0 && (
              <> Fora do escopo por não terem catálogo próprio no Mercado Livre:{" "}
                {Object.keys(meta.out_of_scope_brands).map(brandLabel).join(", ")}.</>
            )}
          </p>
        )}
      </section>

      {/* ---------------- aviso de escopo ---------------- */}
      <section
        aria-label="Escopo e limitações"
        className="bg-amber-50 border border-amber-200 rounded-2xl p-4 mb-4"
      >
        <p className="text-sm font-semibold text-amber-900 mb-2">
          Modo observacional
        </p>
        <ul className="text-xs text-amber-900 space-y-1 list-disc pl-4">
          {AVISO_OBSERVACIONAL.map((t) => (
            <li key={t}>{t}</li>
          ))}
          <li>Cobertura: apenas anúncios das lojas próprias — nenhum vendedor terceiro.</li>
          <li>Comparação histórica indisponível: a referência não tem vigência declarada.</li>
        </ul>
      </section>

      {/* ---------------- KPIs ---------------- */}
      <section aria-label="Indicadores" aria-busy={estado.loading} className="mb-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3">
          {estado.loading && kpiViews.length === 0
            ? Array.from({ length: 6 }).map((_, i) => (
                <div
                  key={i}
                  className="bg-white border border-violet-100 rounded-2xl shadow-sm h-24 animate-pulse"
                  aria-hidden="true"
                />
              ))
            : kpiViews.map((k) => (
                <button
                  key={k.chave}
                  type="button"
                  onClick={
                    k.status
                      ? () => trocaFiltro(() => setStatus(k.status as string))
                      : () => limparFiltros()
                  }
                  className="text-left bg-white border border-violet-100 rounded-2xl shadow-sm p-4 min-h-[44px] hover:border-violet-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
                  title={k.detalhe}
                >
                  <span className="block text-xs text-slate-500">{k.rotulo}</span>
                  <span className="block text-2xl font-bold text-slate-900 tabular-nums">
                    {fmtContagem(k.valor)}
                  </span>
                  <span className="block text-xs text-slate-500 mt-1">{k.detalhe}</span>
                </button>
              ))}
        </div>
        {qualidadeViews.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
            {qualidadeViews.map((k) => (
              <button
                key={k.chave}
                type="button"
                onClick={() => trocaFiltro(() => setStatus(k.status as string))}
                className="text-left bg-slate-50 border border-slate-200 rounded-2xl p-3 min-h-[44px] hover:border-slate-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
                title={k.detalhe}
              >
                <span className="block text-xs text-slate-500">
                  Qualidade do dado · {k.rotulo}
                </span>
                <span className="block text-lg font-bold text-slate-800 tabular-nums">
                  {fmtContagem(k.valor)}
                </span>
                <span className="block text-xs text-slate-500">{k.detalhe}</span>
              </button>
            ))}
          </div>
        )}
        <p className="text-xs text-slate-500 mt-2">{NOTA_DENOMINADOR}</p>
      </section>

      {/* ---------------- filtros ---------------- */}
      <section
        aria-label="Filtros"
        className="bg-white border border-violet-100 rounded-2xl shadow-sm p-4 mb-4"
      >
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex flex-col">
            <label htmlFor={marcaId} className="text-xs text-slate-500 mb-1">
              Marca
            </label>
            <select
              id={marcaId}
              value={brand}
              onChange={(e) => trocaFiltro(() => setBrand(e.target.value))}
              className="border border-slate-300 rounded-lg px-3 min-h-[44px] text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
            >
              <option value="">Todas as monitoradas</option>
              {(meta?.monitored_brands ?? []).map((b) => (
                <option key={b} value={b}>
                  {brandLabel(b)}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col">
            <label htmlFor={situacaoId} className="text-xs text-slate-500 mb-1">
              Situação
            </label>
            <select
              id={situacaoId}
              value={status}
              onChange={(e) => trocaFiltro(() => setStatus(e.target.value))}
              className="border border-slate-300 rounded-lg px-3 min-h-[44px] text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
            >
              <option value="">Todas as situações</option>
              {STATUS_ORDER.map((s) => (
                <option key={s} value={s}>
                  {statusLabel(s)}
                </option>
              ))}
            </select>
          </div>

          <form onSubmit={submeterBusca} className="flex flex-col grow min-w-[220px]">
            <label htmlFor={buscaId} className="text-xs text-slate-500 mb-1">
              Buscar por título, SKU, EAN ou item
            </label>
            <div className="flex gap-2">
              <input
                id={buscaId}
                type="search"
                value={buscaInput}
                onChange={(e) => setBuscaInput(e.target.value)}
                maxLength={120}
                placeholder="Ex.: RT01016, 7897185070156, MLB..."
                className="border border-slate-300 rounded-lg px-3 min-h-[44px] text-sm grow focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
              />
              <button
                type="submit"
                className="bg-violet-600 text-white rounded-lg px-4 min-h-[44px] text-sm font-semibold hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
              >
                Buscar
              </button>
            </div>
          </form>

          <button
            type="button"
            onClick={limparFiltros}
            disabled={!temFiltro}
            className="border border-slate-300 rounded-lg px-4 min-h-[44px] text-sm font-semibold text-slate-700 disabled:opacity-40 hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
          >
            Limpar filtros
          </button>
        </div>
      </section>

      {/* ---------------- tabela ---------------- */}
      <section aria-label="Anúncios monitorados" aria-busy={estado.loading}>
        {estado.loading && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm p-8">
            <h2 className="text-sm font-semibold text-slate-700">
              Carregando monitoramento de preços…
            </h2>
            <div className="mt-4 space-y-2" aria-hidden="true">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-8 bg-slate-100 rounded animate-pulse" />
              ))}
            </div>
          </div>
        )}

        {estado.error && (
          <div
            role="alert"
            className="bg-white border border-rose-200 rounded-2xl shadow-sm p-8 text-center"
          >
            <h2 className="text-sm font-semibold text-rose-800">
              Monitoramento de preços indisponível
            </h2>
            <p className="text-xs text-slate-600 mt-2">
              {erro} Nenhum dado é exibido: a tela não substitui a medição real por
              estimativa.
            </p>
            <button
              type="button"
              onClick={() => setRetryKey((k) => k + 1)}
              className="mt-4 border border-slate-300 rounded-lg px-4 min-h-[44px] text-sm font-semibold hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
            >
              Tentar novamente
            </button>
          </div>
        )}

        {estado.fresh && linhas.length === 0 && (
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm p-8 text-center">
            <h2 className="text-sm font-semibold text-slate-700">
              Nenhum anúncio para os filtros selecionados
            </h2>
            <p className="text-xs text-slate-500 mt-2">
              {temFiltro
                ? "Ajuste marca, situação ou busca para ampliar o resultado."
                : "Não há anúncios publicados na data observada."}
            </p>
            {temFiltro && (
              <button
                type="button"
                onClick={limparFiltros}
                className="mt-4 border border-slate-300 rounded-lg px-4 min-h-[44px] text-sm font-semibold hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
              >
                Limpar filtros
              </button>
            )}
          </div>
        )}

        {estado.fresh && linhas.length > 0 && (
          // `overflow-hidden` corta a propagacao horizontal: as 12 colunas em
          // `whitespace-nowrap` dao a tabela um min-content de ~1587px, e sem
          // isso o overflow escapa do `overflow-x-auto` do TableScrollHint e a
          // PAGINA INTEIRA passa a rolar na lateral — medido em producao em
          // 1440/1024/390. E' o mesmo padrao que /pedidos ja usa; as outras
          // rotas nao expunham o problema porque as tabelas delas sao mais
          // estreitas. A rolagem interna da tabela continua funcionando.
          <div className="bg-white border border-violet-100 rounded-2xl shadow-sm overflow-hidden">
            {truncamento && (
              <p className="text-xs text-slate-600 px-4 pt-4">{truncamento}</p>
            )}
            <TableScrollHint>
              <table className="w-full text-sm">
                <caption className="sr-only">
                  Anúncios próprios do Mercado Livre com preço anunciado e preço
                  sugerido de revenda, ordenados pela maior diferença negativa.
                </caption>
                <thead>
                  <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                    <th scope="col" className="px-4 py-3 font-semibold">Produto / anúncio</th>
                    <th scope="col" className="px-4 py-3 font-semibold">Marca</th>
                    <th scope="col" className="px-4 py-3 font-semibold">Item</th>
                    <th scope="col" className="px-4 py-3 font-semibold">SKU</th>
                    <th scope="col" className="px-4 py-3 font-semibold text-right">Preço anunciado</th>
                    <th scope="col" className="px-4 py-3 font-semibold text-right">Preço sugerido</th>
                    <th scope="col" className="px-4 py-3 font-semibold text-right">Diferença</th>
                    <th scope="col" className="px-4 py-3 font-semibold text-right">Diferença %</th>
                    <th scope="col" className="px-4 py-3 font-semibold">Situação</th>
                    <th scope="col" className="px-4 py-3 font-semibold">Match</th>
                    <th scope="col" className="px-4 py-3 font-semibold">Captura do preço</th>
                    <th scope="col" className="px-4 py-3 font-semibold">
                      <span className="sr-only">Analisar</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {linhas.map((row) => (
                    <tr
                      key={`${row.item_id}-${row.ref_date}`}
                      className="border-b border-slate-100 last:border-0 hover:bg-violet-50/40"
                    >
                      <td className="px-4 py-3 max-w-[280px]">
                        <span className="block truncate text-slate-800" title={tituloLinha(row)}>
                          {tituloLinha(row)}
                        </span>
                        <span className="block text-xs text-slate-500">
                          {listingStatusLabel(row.listing_status)}
                        </span>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">{brandLabel(row.brand)}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-slate-600 text-xs">
                        {row.item_id}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-slate-600 text-xs">
                        {row.seller_sku ?? INDISPONIVEL}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums whitespace-nowrap">
                        {fmtMoeda(row.advertised_price)}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums whitespace-nowrap">
                        {fmtMoeda(row.suggested_retail_amount)}
                      </td>
                      <td
                        className={`px-4 py-3 text-right tabular-nums whitespace-nowrap font-semibold ${
                          row.difference_amount != null && row.difference_amount < 0
                            ? "text-amber-700"
                            : "text-slate-700"
                        }`}
                      >
                        {fmtDiferenca(row.difference_amount)}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums whitespace-nowrap">
                        {fmtPercentual(row.difference_pct)}
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={row.comparison_status} />
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-600 whitespace-nowrap">
                        {matchLabel(row.match_method, row.match_quality)}
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-600 whitespace-nowrap">
                        {fmtCapturaPreco(row.observed_at)}
                      </td>
                      <td className="px-4 py-3">
                        <button
                          type="button"
                          onClick={() => setLinhaAberta(row)}
                          className="border border-slate-300 rounded-lg px-3 min-h-[44px] text-xs font-semibold text-slate-700 hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
                        >
                          Analisar
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScrollHint>

            {paginacao && (
              <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-t border-slate-200">
                <p className="text-xs text-slate-600">
                  {paginacao.rotulo} · página {fmtContagem(paginacao.pagina)} de{" "}
                  {fmtContagem(paginacao.totalPaginas)}
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setOffset(Math.max(0, offset - LIMITE))}
                    disabled={!paginacao.temAnterior}
                    className="border border-slate-300 rounded-lg px-4 min-h-[44px] text-sm font-semibold disabled:opacity-40 hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
                  >
                    Anterior
                  </button>
                  <button
                    type="button"
                    onClick={() => setOffset(offset + LIMITE)}
                    disabled={!paginacao.temProxima}
                    className="border border-slate-300 rounded-lg px-4 min-h-[44px] text-sm font-semibold disabled:opacity-40 hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
                  >
                    Próxima
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      {/* ---------------- drill-down ---------------- */}
      <KpiDrilldownDialog
        open={linhaAberta != null}
        onClose={() => setLinhaAberta(null)}
        title={linhaAberta ? tituloLinha(linhaAberta) : "Anúncio"}
        focusResetKey={linhaAberta?.item_id}
      >
        {linhaAberta && (
          <div className="space-y-4 text-sm">
            <div>
              <StatusBadge status={linhaAberta.comparison_status} />
            </div>

            <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <dt className="text-xs text-slate-500">Marca</dt>
                <dd className="font-semibold">{brandLabel(linhaAberta.brand)}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Item</dt>
                <dd className="font-mono text-xs">{linhaAberta.item_id}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">SKU</dt>
                <dd className="font-mono text-xs">{linhaAberta.seller_sku ?? INDISPONIVEL}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">EAN</dt>
                <dd className="font-mono text-xs">{linhaAberta.gtin ?? INDISPONIVEL}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Situação do anúncio</dt>
                <dd>{listingStatusLabel(linhaAberta.listing_status)}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Data observada</dt>
                <dd>{fmtData(linhaAberta.ref_date)}</dd>
              </div>
            </dl>

            <div className="border-t border-slate-200 pt-3">
              <p className="text-xs font-semibold text-slate-700 mb-2">Preços</p>
              <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <dt className="text-xs text-slate-500">Preço anunciado</dt>
                  <dd className="font-semibold tabular-nums">
                    {fmtMoeda(linhaAberta.advertised_price)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Preço sugerido de revenda (PDV)</dt>
                  <dd className="font-semibold tabular-nums">
                    {fmtMoeda(linhaAberta.suggested_retail_amount)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Diferença</dt>
                  <dd className="font-semibold tabular-nums">
                    {fmtDiferenca(linhaAberta.difference_amount)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Diferença percentual</dt>
                  <dd className="font-semibold tabular-nums">
                    {fmtPercentual(linhaAberta.difference_pct)}
                  </dd>
                </div>
              </dl>
            </div>

            <div className="border-t border-slate-200 pt-3">
              <p className="text-xs font-semibold text-slate-700 mb-2">
                Correspondência com a referência
              </p>
              <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <dt className="text-xs text-slate-500">Método</dt>
                  <dd>{matchLabel(linhaAberta.match_method, linhaAberta.match_quality)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Qualidade</dt>
                  <dd>{matchQualityLabel(linhaAberta.match_quality)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Tipo de referência</dt>
                  <dd>Preço sugerido de revenda (PDV)</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Vigência da referência</dt>
                  <dd>Não informada</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Referência capturada em</dt>
                  <dd>{fmtInstanteBrt(linhaAberta.reference_captured_at)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Captura do preço</dt>
                  <dd>{fmtCapturaPreco(linhaAberta.observed_at)}</dd>
                </div>
              </dl>
              {linhaAberta.comparison_status === "non_comparable_reference_ambiguous" && (
                <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-3 mt-3">
                  Referência ambígua — {fmtContagem(linhaAberta.reference_candidate_count)}{" "}
                  linhas da tabela de origem disputam a mesma chave nesta marca. Nenhuma
                  diferença é calculada: a revisão da tabela de origem é necessária.
                </p>
              )}
            </div>

            <div className="border-t border-slate-200 pt-3">
              <p className="text-xs font-semibold text-slate-700 mb-2">
                Composição indisponível
              </p>
              <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {COMPOSICAO_INDISPONIVEL.map((c) => (
                  <div key={c.rotulo}>
                    <dt className="text-xs text-slate-500">{c.rotulo}</dt>
                    <dd className="font-semibold text-slate-500">{c.valor}</dd>
                  </div>
                ))}
              </dl>
            </div>

            <div className="border-t border-slate-200 pt-3">
              <p className="text-xs font-semibold text-slate-700 mb-2">Limitações</p>
              <ul className="text-xs text-slate-600 space-y-1 list-disc pl-4">
                {linhaAberta.limitations.map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
            </div>

            <div className="border-t border-slate-200 pt-3">
              {linkAnuncio ? (
                <a
                  href={linkAnuncio}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block bg-violet-600 text-white rounded-lg px-4 py-3 min-h-[44px] text-sm font-semibold hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
                >
                  Abrir anúncio no Mercado Livre
                </a>
              ) : (
                <p className="text-xs text-slate-500">
                  Link do anúncio indisponível ou fora dos domínios reconhecidos do
                  Mercado Livre.
                </p>
              )}
            </div>
          </div>
        )}
      </KpiDrilldownDialog>
    </PageContainer>
  );
}
