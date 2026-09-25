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
  mensagemDeFalha,
  MONITORAMENTO_PRECO_MAX_LIMIT,
  type MonitoramentoPrecoResponse,
  type MonitoramentoPrecoRow,
  type ComparisonStatus,
  type Marketplace,
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
  fmtParticipacao,
  fmtPercentual,
  listingStatusLabel,
  matchLabel,
  matchQualityLabel,
  statusLabel,
  freshnessLabel,
  tituloLinha,
  // Gate PMA-OPS-2 — causas, relogios e estado do link
  decomporSemReferencia,
  painelDeFrescor,
  linkAnuncioView,
  // Gate PMA-2C4B — multicanal
  PRODUCT_TYPE_ORDER,
  canaisDisponiveis,
  canalComPreposicao,
  canalLabel,
  coberturaPorConta,
  filtrosDoCanal,
  fmtPrecoObservado,
  politicaDataView,
  foraDoEscopo,
  marcaParaEscopo,
  productTypeLabel,
  resumoCobertura,
  ROTULO_FORA_DE_ESCOPO,
  situacaoFrescor,
  situacaoFrescorLabel,
  temPrecoObservado,
  type EscopoFiltro,
} from "@/lib/monitoramento-preco";
import PageContainer from "@/components/layout/PageContainer";
import PageHeader from "@/components/layout/PageHeader";
import TableScrollHint from "@/components/TableScrollHint";
import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import { computeRequestStatus } from "@/lib/request-freshness";

const LIMITE = MONITORAMENTO_PRECO_MAX_LIMIT;

/** Gate PMA-H1 — a data observada vive na URL, para o link ser compartilhavel. */
const PARAM_DATA = "observed_date";
const RE_DATA = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Le a data da URL no primeiro render. Usa `window.location` em vez de
 * `useSearchParams` de proposito: `useSearchParams` exige fronteira de Suspense
 * na renderizacao estatica do App Router, e esta tela nao precisa disso.
 *
 * Valor fora do formato e' IGNORADO em vez de enviado: mandar lixo ao backend
 * so' renderizaria um 422 para um erro que e' do proprio link.
 */
function dataInicialDaUrl(): string {
  if (typeof window === "undefined") return "";
  const bruto = new URLSearchParams(window.location.search).get(PARAM_DATA);
  return bruto && RE_DATA.test(bruto) ? bruto : "";
}

function sincronizaUrl(data: string) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (data) url.searchParams.set(PARAM_DATA, data);
  else url.searchParams.delete(PARAM_DATA);
  window.history.replaceState(null, "", url.toString());
}

const TONE_FRESCOR: Record<string, string> = {
  fresh: "bg-emerald-50 text-emerald-800 border-emerald-200",
  stale: "bg-amber-50 text-amber-900 border-amber-300",
  historical: "bg-sky-50 text-sky-900 border-sky-200",
  unavailable: "bg-slate-50 text-slate-600 border-slate-200",
};

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
  const dataId = useId();

  const canalId = useId();
  const contaId = useId();
  const tipoId = useId();
  const escopoId = useId();

  // O Mercado Livre continua sendo o padrao: e' o unico canal publicado.
  const [marketplace, setMarketplace] = useState<Marketplace>("ml");
  const [shopAccount, setShopAccount] = useState("");
  const [escopo, setEscopo] = useState<EscopoFiltro>("todos");
  const [productType, setProductType] = useState("");
  const [brand, setBrand] = useState("");
  const [status, setStatus] = useState("");
  const [buscaInput, setBuscaInput] = useState("");
  const [productQuery, setProductQuery] = useState("");
  const [observedDate, setObservedDate] = useState<string>(dataInicialDaUrl);
  const [offset, setOffset] = useState(0);

  const [dados, setDados] = useState<MonitoramentoPrecoResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  // Gate PMA-2C4D3-H3 — o status fica guardado para ESCOLHER a redacao, nunca
  // para ser exibido. `erro` continua existindo como diagnostico.
  const [erroStatus, setErroStatus] = useState<number | null>(null);
  const [resolvedKey, setResolvedKey] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  const [linhaAberta, setLinhaAberta] = useState<MonitoramentoPrecoRow | null>(null);

  // O escopo e' traduzido para a dimensao que a API ja' aceita: a lista de
  // marcas monitoradas. Uma marca escolhida a mao pelo operador vence o escopo
  // — ela e' mais especifica, e sobrepo-la seria ignorar o pedido dele.
  // Le de `dados` e nao de `meta`, que so' e' derivado mais abaixo.
  const marcaEfetiva =
    brand || marcaParaEscopo(escopo, dados?.meta?.monitored_brands ?? []);

  const requestKey = useMemo(
    () =>
      buildMonitoramentoRequestKey({
        marketplace,
        brand: marcaEfetiva,
        status,
        productQuery,
        observedDate,
        shopAccount,
        productType,
        limit: LIMITE,
        offset,
      }),
    [marketplace, marcaEfetiva, status, productQuery, observedDate, shopAccount, productType, offset],
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
    setErroStatus(null);

    fetchMonitoramentoPreco(
      {
        marketplace,
        brand: marcaEfetiva || undefined,
        status: status || undefined,
        productQuery: productQuery || undefined,
        observedDate: observedDate || undefined,
        // Estes dois so' existem nos canais novos. No ML o backend os recusa
        // com 422, entao nem sao montados na query.
        shopAccount: shopAccount || undefined,
        productType: productType || undefined,
        limit: LIMITE,
        offset,
      },
      controller.signal,
    )
      .then((res) => {
        if (latestKey.current !== requestKey) return;
        setDados(res);
        setErro(null);
        setErroStatus(null);
        setResolvedKey(requestKey);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (latestKey.current !== requestKey) return;
        // Sem fallback em mock: indisponibilidade e' declarada.
        //
        // Gate PMA-2C4D3-H3 — `setBrand` NAO e' chamado aqui. Falha de rede ou
        // de API nao e' prova de que a marca deixou de ser observavel; limpar a
        // selecao faria o operador perder o filtro por um problema que nao e'
        // dele. A limpeza automatica so' acontece quando uma COBERTURA nova
        // chega e prova que a marca saiu — no efeito mais abaixo.
        setDados(null);
        setErro(
          err instanceof MonitoramentoPrecoError
            ? err.message
            : "Falha inesperada ao carregar o monitoramento.",
        );
        setErroStatus(
          err instanceof MonitoramentoPrecoError ? err.status : null,
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

  /**
   * Troca de canal. Zera a paginacao e LIMPA os filtros da selecao anterior.
   *
   * Nao e' higiene: as dimensoes sao diferentes por canal. `apice` e' marca
   * valida na Shopee e recusada com 422 no ML; `shop_account` e `product_type`
   * nao existem no ML; e uma data D0 escolhida na Shopee esta acima do teto
   * D-1 do ML. Carregar qualquer um desses para o canal novo produziria 422
   * sobre uma escolha que o operador nem fez.
   *
   * `setDados(null)` e' deliberado: sem ele, os KPIs e a tabela do canal
   * anterior ficariam na tela sob o titulo do canal novo enquanto a requisicao
   * corre. A guarda de frescor ja' impede a resposta ATRASADA de sobrescrever
   * a atual; isto impede o dado ANTIGO de parecer o novo.
   */
  const trocaCanal = useCallback(
    (novo: Marketplace) => {
      setMarketplace((atual) => {
        if (atual === novo) return atual;
        setOffset(0);
        setBrand("");
        setStatus("");
        setBuscaInput("");
        setProductQuery("");
        setShopAccount("");
        setProductType("");
        setEscopo("todos");
        setObservedDate("");
        sincronizaUrl("");
        setDados(null);
        setLinhaAberta(null);
        return novo;
      });
    },
    [],
  );

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
    setShopAccount("");
    setProductType("");
    setEscopo("todos");
    // A DATA nao e' um filtro de conteudo: ela define QUAL dia esta na tela.
    // "Limpar filtros" nao pode teleportar o operador para outro dia.
  }, []);

  /** Troca da data observada: volta a pagina 1 e reflete na URL. */
  const trocaData = useCallback((valor: string) => {
    setOffset(0);
    setObservedDate(valor);
    sincronizaUrl(valor);
  }, []);

  const temFiltro =
    brand !== "" ||
    status !== "" ||
    productQuery !== "" ||
    shopAccount !== "" ||
    productType !== "" ||
    escopo !== "todos";

  const meta = dados?.meta ?? null;
  const kpis = dados?.kpis ?? null;
  const linhas = estado.fresh && dados ? dados.rows : [];

  // ---- Gate PMA-2C4D3-H2: o filtro de marca sai da COBERTURA ---------------
  // `observed_brands` diz o que esta fotografia contem; `monitored_brands` diz
  // o que o negocio monitora, e vem igual para Shopee e TikTok — por isso a
  // Shopee oferecia Kokeshi e respondia "0".
  //
  // O `??` nao e' decoracao: backend e frontend sao publicados separados, e na
  // janela entre os dois deploys a resposta ainda nao traz o campo. Sem cair
  // para `monitored_brands`, o filtro ficaria VAZIO nessa janela — pior que o
  // defeito que estamos corrigindo.
  const marcasDaCobertura = meta?.observed_brands ?? meta?.monitored_brands ?? [];
  // Gate PMA-2C4D3-H3 — a marca escolhida continua LISTADA mesmo quando a
  // cobertura nao veio (falha de carga zera `dados`, e com ela `meta`).
  //
  // Sem isto, a lista ficava vazia, o `<select>` nao achava a opcao escolhida e
  // caia sozinho para "Todas as monitoradas": a tela parecia ter descartado o
  // filtro do operador, embora o estado ainda o guardasse. O controle passa a
  // mostrar o que a pessoa pediu ate que uma cobertura nova diga outra coisa.
  const marcasDoFiltro =
    brand && !marcasDaCobertura.includes(brand)
      ? [...marcasDaCobertura, brand]
      : marcasDaCobertura;
  const naoObservadas = meta?.monitored_unobserved_brands ?? [];

  // Marca escolhida que deixou de existir na cobertura (troca de data, ou
  // troca de canal com a resposta antiga ainda em tela) e' limpa: manter o
  // filtro exibiria "0" pela razao errada.
  useEffect(() => {
    if (!brand || !meta?.observed_brands) return;
    if (!meta.observed_brands.includes(brand)) {
      setBrand("");
      setOffset(0);
    }
  }, [brand, meta?.observed_brands]);

  const kpiViews = kpis ? buildKpiViews(kpis, marketplace) : [];
  const qualidadeViews = kpis ? buildQualidadeViews(kpis) : [];
  const paginacao = dados
    ? calcPaginacao(dados.total_count, dados.returned_count, LIMITE, offset)
    : null;
  const truncamento = dados
    ? avisoTruncamento(dados.truncated, dados.returned_count, dados.total_count)
    : null;

  // Gate PMA-OPS-2 — tres estados, nao dois. "A fonte nao fornece URL" e'
  // limite da origem; "o canal fornece e esta linha veio sem" e' defeito. A
  // mensagem antiga dizia a mesma frase para os dois.
  const linkAnuncio = linhaAberta
    ? linkAnuncioView(linhaAberta.permalink, marketplace)
    : null;

  // Gate PMA-OPS-2 — os quatro relogios e a decomposicao das causas.
  const frescor = painelDeFrescor(meta);
  const causas = decomporSemReferencia(
    dados?.metrics?.no_reference_breakdown,
    dados?.kpis?.no_reference_count ?? 0,
  );

  // ---- Gate PMA-2C4B: vistas multicanal -----------------------------------
  const canais = canaisDisponiveis();
  const filtros = filtrosDoCanal(marketplace);
  const politica = politicaDataView(meta);
  const cobertura = resumoCobertura(meta);
  const contas = coberturaPorConta(meta?.account_clocks);
  const canalIndisponivel = meta?.availability === "unavailable";
  const contasDoFiltro = contas.map((c) => c.conta);
  // Os QUATRO estados, sempre. `product_type_counts` conta so' as ATIVAS, e
  // filtrar as opcoes por ele esconderia do filtro um tipo que existe apenas
  // entre as inativas — visivel na coluna e inalcancavel pelo filtro, que e'
  // exatamente o defeito que este gate proibe.
  const tiposDoCanal = PRODUCT_TYPE_ORDER;

  return (
    <PageContainer>
      <PageHeader
        title={`Monitoramento de preços — ${canalLabel(marketplace)}`}
        subtitle={
          marketplace === "ml"
            ? // Texto PUBLICADO do Mercado Livre — inalterado.
              "Compara os preços anunciados das lojas próprias no Mercado Livre com o preço sugerido de revenda (PDV) das tabelas B2B."
            : `Compara os preços anunciados das lojas próprias na ${canalLabel(marketplace)} com o preço sugerido de revenda (PDV) das tabelas B2B.`
        }
      />

      {/* ---------------- seletor de canal ----------------
          So' aparece quando ha mais de um canal habilitado. Com Shopee e
          TikTok desligados — o padrao — a tela fica exatamente como estava. */}
      {canais.length > 1 && (
        <section
          aria-label="Canal"
          className="bg-white border border-violet-100 rounded-2xl shadow-sm p-3 mb-4"
        >
          <div
            role="group"
            aria-labelledby={canalId}
            className="flex flex-wrap items-center gap-2"
          >
            <span id={canalId} className="text-xs text-slate-500 mr-1">
              Canal
            </span>
            {canais.map((c) => {
              const ativo = c.id === marketplace;
              return (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => trocaCanal(c.id)}
                  // `aria-pressed` e nao `aria-current`: os tres sao botoes de
                  // alternancia, e o leitor de tela precisa saber o estado dos
                  // TRES, nao so' do ativo. Com `aria-current`, o inativo nao
                  // carregava atributo nenhum — "Shopee, botao" nao diz se esta
                  // ligado ou desligado. Com `aria-pressed`, cada um anuncia
                  // pressionado ou nao, e o estado deixa de depender da cor.
                  aria-pressed={ativo}
                  className={`min-h-[44px] min-w-[44px] px-4 rounded-lg border text-sm font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                    ativo
                      ? "bg-violet-600 border-violet-600 text-white"
                      : "bg-white border-slate-300 text-slate-700 hover:bg-slate-50"
                  }`}
                >
                  {c.rotulo}
                </button>
              );
            })}
          </div>
        </section>
      )}

      {/* ---------------- contexto e frescor ---------------- */}
      <section
        aria-label="Contexto dos dados"
        className="bg-white border border-violet-100 rounded-2xl shadow-sm p-4 mb-4"
      >
        <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-sm">
          <div>
            <dt className="text-slate-500 text-xs uppercase tracking-wide">Canal</dt>
            <dd className="font-semibold text-slate-800">
              {canalLabel(marketplace)}
              {/* A politica de data e' do CANAL e viaja no payload. So' os
                  canais novos a exibem: no ML o teto D-1 ja' era conhecido e
                  acrescentar o chip mudaria uma tela publicada. */}
              {meta && marketplace !== "ml" && (
                <span className="ml-2 inline-block text-xs font-normal text-slate-500 align-middle">
                  {politica.tetoRotulo}
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500 text-xs uppercase tracking-wide">
              Preço observado em
            </dt>
            <dd className="font-semibold text-slate-800">
              {fmtData(meta?.observed_ref_date)}
              {meta && (
                <span
                  className={`ml-2 inline-block text-xs font-semibold border rounded-full px-2 py-0.5 align-middle ${TONE_FRESCOR[meta.freshness_status] ?? TONE_FRESCOR.unavailable}`}
                >
                  {freshnessLabel(meta.freshness_status)}
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500 text-xs uppercase tracking-wide">
              Referência PDV capturada em
            </dt>
            <dd className="font-semibold text-slate-800">
              {fmtInstanteBrt(meta?.reference_captured_at)}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500 text-xs uppercase tracking-wide">Sync executado em</dt>
            <dd className="font-semibold text-slate-800">
              {fmtInstanteBrt(meta?.refreshed_at)}
            </dd>
          </div>
        </dl>

        {/* Gate PMA-2C4B — D0 NAO e' periodo fechado. O aviso vem antes da
            frase de base da comparacao porque muda como TUDO abaixo se le'. */}
        {politica.avisoMutavel && (
          <p className="text-xs text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mt-3">
            {politica.avisoMutavel}
          </p>
        )}

        {/* Canal reconhecido, porem sem nada publicado. Nao e' erro. */}
        {canalIndisponivel && (
          <p className="text-xs text-slate-700 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 mt-3">
            Este canal está reconhecido, mas não há fotografia publicada para
            esta consulta. Nenhum dado de outro canal é exibido no lugar.
          </p>
        )}

        {/* Cobertura por CONTA: so' os canais publicam mais de um relogio. */}
        {contas.length > 0 && meta?.date_policy === "snapshot_current" && (
          <div className="mt-3 border-t border-violet-100 pt-3">
            <p className="text-xs text-slate-500 uppercase tracking-wide mb-2">
              Cobertura por conta — {cobertura.contasObservadas} observada(s)
              {cobertura.revistas != null && (
                <> · {fmtContagem(cobertura.revistas)} oferta(s) revista(s),{" "}
                  {fmtContagem(cobertura.naoRevistas ?? 0)} não revista(s)</>
              )}
              {cobertura.ofertasForaDeEscopo > 0 && (
                <> · {fmtContagem(cobertura.ofertasForaDeEscopo)} fora do escopo
                  de beleza, sem entrar em nenhum denominador</>
              )}
            </p>
            <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 text-xs">
              {contas.map((c) => (
                <li
                  key={c.conta}
                  className="border border-slate-200 rounded-lg px-3 py-2"
                >
                  <span className="font-semibold text-slate-800">{c.conta}</span>
                  <span className="block text-slate-600">
                    {c.ofertas == null ? INDISPONIVEL : fmtContagem(c.ofertas)} oferta(s)
                    {c.revistas != null && (
                      <> · {fmtContagem(c.revistas)} revista(s)</>
                    )}
                  </span>
                  <span className="block text-slate-500">
                    carga: {c.relogio}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* A frase que declara as DUAS datas e a ausencia de vigencia. */}
        {meta?.comparison_basis_text && (
          <p className="text-xs text-slate-600 mt-3 border-t border-violet-100 pt-3">
            {meta.comparison_basis_text}
          </p>
        )}
        {meta && (
          <p className="text-xs text-slate-500 mt-3">
            Marcas monitoradas: {meta.monitored_brands.map(brandLabel).join(", ")}.
            {/* Gate PMA-2C4D3-H2 — a ausencia e' DECLARADA, e declarada como
                ausencia de observacao. Antes, Kokeshi aparecia no filtro da
                Shopee e devolvia "0", que se le como "Kokeshi nao vende".
                Nenhum nome e' fixo no codigo: a lista vem da API. */}
            {naoObservadas.length > 0 && (
              <> Sem observação nesta fotografia:{" "}
                {naoObservadas.map(brandLabel).join(", ")} — a fonte deste canal
                não devolveu essas marcas, o que não significa zero anúncios.</>
            )}
            {meta.no_reference_brands.length > 0 && (
              <> Sem tabela de referência: {meta.no_reference_brands.map(brandLabel).join(", ")}.</>
            )}
            {/* `out_of_scope_brands` volta igual nos tres canais — sempre
                `{apice, yenzah: out_of_scope_no_ml_catalog}` — porque o motivo
                e' "nao tem catalogo proprio no MERCADO LIVRE". Numa tela de
                Shopee a frase nao so' cita o canal errado: ela CONTRADIZ o
                dado, porque Ápice tem 225 anuncios e e' uma das quatro contas
                da Shopee. Por isso a clausula so' aparece no ML, dono do
                criterio. */}
            {marketplace === "ml" && Object.keys(meta.out_of_scope_brands).length > 0 && (
              <> Fora do escopo por não terem catálogo próprio no Mercado Livre:{" "}
                {Object.keys(meta.out_of_scope_brands).map(brandLabel).join(", ")}.</>
            )}
          </p>
        )}
      </section>

      {/* ---------------- defasagem ou retrospectiva ---------------- */}
      {/*
        Gate PMA-H1: antes, um sync atrasado zerava os cartoes comerciais e a
        tela nao dizia por que. Agora o atraso e' um BANNER, os numeros
        continuam visiveis, e a data escolhida deliberadamente NAO e' chamada de
        atraso.
      */}
      {meta?.freshness_status === "stale" && (
        <section
          role="status"
          aria-label="Aviso de defasagem dos dados"
          className="bg-amber-50 border border-amber-300 rounded-2xl p-4 mb-4"
        >
          <p className="text-sm font-semibold text-amber-900">
            Sincronização atrasada em {fmtContagem(meta.lag_days ?? 0)} dia(s).
          </p>
          <p className="text-xs text-amber-800 mt-1">
            A observação mais recente disponível é de{" "}
            <strong>{fmtData(meta.observed_ref_date)}</strong>, e o último dia
            elegível seria <strong>{fmtData(meta.eligible_ref_date)}</strong>. As
            comparações abaixo valem para o dia observado e{" "}
            <strong>não descrevem o preço de hoje</strong>. Verifique a última
            execução do sync de preços.
          </p>
        </section>
      )}

      {meta?.freshness_status === "historical" && (
        <section
          role="status"
          aria-label="Aviso de consulta retrospectiva"
          className="bg-sky-50 border border-sky-200 rounded-2xl p-4 mb-4"
        >
          <p className="text-sm font-semibold text-sky-900">
            Consulta retrospectiva — {fmtData(meta.observed_ref_date)}
          </p>
          <p className="text-xs text-sky-900 mt-1">
            O preço anunciado é o daquele dia. A referência usada é o snapshot
            PDV <strong>mais recente disponível hoje</strong>, capturado em{" "}
            <strong>{fmtInstanteBrt(meta.reference_captured_at)}</strong>.{" "}
            <strong>
              A origem não declara a vigência histórica dessa referência
            </strong>
            , portanto este resultado pode mudar se uma nova referência PDV for
            importada. Não é defasagem do pipeline.
          </p>
        </section>
      )}

      {meta?.freshness_status === "unavailable" && (
        <section
          role="status"
          aria-label="Sem observação para a data escolhida"
          className="bg-slate-50 border border-slate-200 rounded-2xl p-4 mb-4"
        >
          <p className="text-sm font-semibold text-slate-800">
            Sem observação para{" "}
            {fmtData(meta.requested_observed_date) || INDISPONIVEL}.
          </p>
          <p className="text-xs text-slate-600 mt-1">
            Esse dia não foi sincronizado. Nenhum número é apresentado —
            ausência não é zero. Escolha uma das datas disponíveis no seletor.
          </p>
        </section>
      )}

      {/* ------- Gate PMA-OPS-2: os relogios, separados e nomeados -------- */}
      {/*
        Este painel existe para impedir UMA frase: "a ingestao rodou hoje de
        manha, entao a tela esta atual". Sao relogios diferentes, e o unico que
        descreve o que a tela mostra e' o da publicacao. O quarto — o estado
        atual da ingestao bruta — e' declarado como NAO OBSERVAVEL aqui, porque
        omiti-lo e' justamente o que produz aquela conclusao.
      */}
      <section
        aria-label="Frescor da fotografia"
        aria-busy={estado.loading}
        className="bg-white border border-violet-100 rounded-2xl shadow-sm p-4 mb-4"
      >
        <div className="flex flex-wrap items-baseline gap-2 mb-3">
          <h2 className="text-sm font-semibold text-slate-800">
            Frescor da fotografia
          </h2>
          <span
            className={`inline-block text-xs font-semibold border rounded-full px-2 py-0.5 ${
              frescor.situacao === "em_dia"
                ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                : frescor.situacao === "atrasada"
                  ? "bg-amber-50 border-amber-300 text-amber-900"
                  : frescor.situacao === "retrospectiva"
                    ? "bg-sky-50 border-sky-200 text-sky-900"
                    : "bg-slate-100 border-slate-300 text-slate-700"
            }`}
          >
            {frescor.rotuloSituacao}
          </span>
        </div>
        {estado.loading && !meta ? (
          <div
            className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3"
            aria-hidden="true"
          >
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="h-20 rounded-xl bg-slate-100 animate-pulse"
              />
            ))}
          </div>
        ) : (
          <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {frescor.relogios.map((r) => (
              <div
                key={r.chave}
                className={`rounded-xl border p-3 ${
                  r.chave === "ingestao_bruta"
                    ? "border-dashed border-slate-300 bg-slate-50"
                    : "border-slate-200 bg-white"
                }`}
              >
                <dt className="text-xs uppercase tracking-wide text-slate-500">
                  {r.rotulo}
                </dt>
                <dd className="text-sm font-semibold text-slate-900 mt-0.5 tabular-nums">
                  {r.valor}
                </dd>
                <dd className="text-xs text-slate-500 mt-1">{r.explicacao}</dd>
              </div>
            ))}
          </dl>
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

      {/* ------- Gate PMA-OPS-2: POR QUE nao ha referencia ---------------- */}
      {/*
        O cartao "Sem referencia" e' um numero grande e mudo. Estas sao as
        causas, e elas tem donos diferentes: marca sem tabela e' pauta de
        Trade, kit e chave sao de cadastro, produto ausente e' o unico caso em
        que "nao esta na tabela B2B" se le ao pe da letra.

        A soma e' CONFERIDA contra o cartao e o resultado da conferencia fica
        visivel. Um detalhamento que nao fecha e' defeito a mostrar, nao a
        maquiar — por isso existe a linha "Nao reconciliado".
      */}
      <section
        aria-label="Causas da ausência de referência"
        aria-busy={estado.loading}
        className="bg-white border border-violet-100 rounded-2xl shadow-sm p-4 mb-4"
      >
        <div className="flex flex-wrap items-baseline justify-between gap-2 mb-3">
          <h2 className="text-sm font-semibold text-slate-800">
            Por que não há referência
          </h2>
          {!causas.indisponivel && causas.total > 0 && (
            <span
              className={`text-xs font-semibold rounded-full border px-2 py-0.5 ${
                causas.fecha
                  ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                  : "bg-rose-50 border-rose-300 text-rose-900"
              }`}
            >
              {causas.fecha
                ? `Fecha: ${fmtContagem(causas.somado)} de ${fmtContagem(causas.total)}`
                : `Não fecha: ${fmtContagem(causas.somado)} de ${fmtContagem(causas.total)}`}
            </span>
          )}
        </div>

        {estado.loading && causas.linhas === null ? (
          <div className="space-y-2" aria-hidden="true">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-12 rounded-xl bg-slate-100 animate-pulse" />
            ))}
          </div>
        ) : estado.error ? (
          <p className="text-sm text-slate-600">
            Não foi possível carregar as causas — a falha está no aviso acima.
            Nenhum número é apresentado no lugar.
          </p>
        ) : causas.indisponivel ? (
          /* Deploy antigo da API: AUSENCIA DE CAMPO, nao ausencia de causa. */
          <p className="text-sm text-slate-600">
            Esta versão da API ainda não decompõe as causas. O total sem
            referência continua correto no cartão acima — o que falta é o
            detalhamento, não o dado.
          </p>
        ) : causas.total === 0 ? (
          /* Zero MEDIDO, e e' uma boa noticia. Diferente de "nao medimos". */
          <p className="text-sm text-emerald-800">
            Nenhuma oferta sem referência nesta fotografia. Zero medido, não
            ausência de medição.
          </p>
        ) : (
          <ul className="space-y-2">
            {(causas.linhas ?? []).map((c) => (
              <li
                key={c.chave}
                className={`rounded-xl border p-3 ${
                  c.residual
                    ? "border-rose-300 bg-rose-50"
                    : "border-slate-200 bg-white"
                }`}
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span
                    className={`text-sm font-semibold ${
                      c.residual ? "text-rose-900" : "text-slate-800"
                    }`}
                  >
                    {c.rotulo}
                  </span>
                  <span className="text-sm font-bold text-slate-900 tabular-nums whitespace-nowrap">
                    {fmtContagem(c.valor)}
                    {c.fracao != null && (
                      <span className="ml-1 text-xs font-normal text-slate-500">
                        ({fmtParticipacao(c.fracao)})
                      </span>
                    )}
                  </span>
                </div>
                {c.detalhe && (
                  <p
                    className={`text-xs mt-1 ${
                      c.residual ? "text-rose-800" : "text-slate-500"
                    }`}
                  >
                    {c.detalhe}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-slate-500 mt-3">
          Conta as ofertas <strong>ativas</strong> sem referência — kits e
          marcas fora do escopo de beleza inclusive. Por isso soma o cartão
          &ldquo;Sem referência&rdquo; e não o denominador de cobertura, que
          exclui os dois de propósito.
        </p>
      </section>

      {/* ---------------- filtros ---------------- */}
      <section
        aria-label="Filtros"
        className="bg-white border border-violet-100 rounded-2xl shadow-sm p-4 mb-4"
      >
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex flex-col">
            <label htmlFor={dataId} className="text-xs text-slate-500 mb-1">
              Data observada
            </label>
            <select
              id={dataId}
              value={observedDate}
              onChange={(e) => trocaData(e.target.value)}
              className="border border-slate-300 rounded-lg px-3 min-h-[44px] text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
            >
              {/*
                "Mais recente" e' o modo `latest`: acompanha o sync. As demais
                opcoes vem SOMENTE de `available_observed_dates` — datas
                materializadas. A tela nao oferece dia que devolveria vazio.
              */}
              <option value="">Mais recente disponível</option>
              {(meta?.available_observed_dates ?? []).map((d) => (
                <option key={d} value={d}>
                  {fmtData(d)}
                </option>
              ))}
            </select>
          </div>

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
              {marcasDoFiltro.map((b) => (
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

          {/* Gate PMA-2C4B — dimensoes que so' existem nos canais novos. No
              Mercado Livre nem sao renderizadas: o backend as recusa com 422, e
              oferecer um controle que so' produz erro seria pior que omiti-lo. */}
          {filtros.conta && contasDoFiltro.length > 0 && (
            <div className="flex flex-col">
              <label htmlFor={contaId} className="text-xs text-slate-500 mb-1">
                Conta de loja
              </label>
              <select
                id={contaId}
                value={shopAccount}
                onChange={(e) => trocaFiltro(() => setShopAccount(e.target.value))}
                className="border border-slate-300 rounded-lg px-3 min-h-[44px] text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
              >
                <option value="">Todas as contas</option>
                {contasDoFiltro.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Gate PMA-2C4B-R — ESCOPO. Existe porque Gocase e Denavita
              aparecem na coluna Marca do TikTok e nao estao em
              `monitored_brands`: sem este controle, o operador veria linhas
              que nao consegue estreitar por dimensao nenhuma. O filtro e' do
              SERVIDOR (manda a lista de marcas monitoradas), entao total e
              paginacao continuam corretos. */}
          {filtros.tipoDeProduto && (meta?.out_of_scope_offer_count ?? 0) > 0 && (
            <div className="flex flex-col">
              <label htmlFor={escopoId} className="text-xs text-slate-500 mb-1">
                Escopo
              </label>
              <select
                id={escopoId}
                value={escopo}
                onChange={(e) =>
                  trocaFiltro(() => setEscopo(e.target.value as EscopoFiltro))
                }
                className="border border-slate-300 rounded-lg px-3 min-h-[44px] text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
              >
                <option value="todos">
                  Todas as marcas da fotografia
                </option>
                <option value="monitoradas">Somente marcas monitoradas</option>
              </select>
            </div>
          )}

          {filtros.tipoDeProduto && tiposDoCanal.length > 0 && (
            <div className="flex flex-col">
              <label htmlFor={tipoId} className="text-xs text-slate-500 mb-1">
                Tipo de produto
              </label>
              <select
                id={tipoId}
                value={productType}
                onChange={(e) => trocaFiltro(() => setProductType(e.target.value))}
                className="border border-slate-300 rounded-lg px-3 min-h-[44px] text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
              >
                <option value="">Todos os tipos</option>
                {tiposDoCanal.map((tp) => (
                  <option key={tp} value={tp}>
                    {productTypeLabel(tp)}
                  </option>
                ))}
              </select>
            </div>
          )}

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
            {/* Gate PMA-2C4D3-H3 — texto para a PESSOA. Antes vinha `{erro}`,
                que e' diagnostico ("A API respondeu 422.") e expunha o status
                HTTP a quem opera. O status continua no estado, escolhendo a
                redacao; nao aparece. */}
            <p className="text-xs text-slate-600 mt-2">
              {mensagemDeFalha(erroStatus)} Nenhum dado é exibido: a tela não
              substitui a medição real por estimativa.
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
                  Anúncios próprios {canalComPreposicao(marketplace)} com preço
                  anunciado e preço sugerido de revenda, ordenados pela maior
                  diferença negativa.
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
                    {filtros.tipoDeProduto && (
                      <th scope="col" className="px-4 py-3 font-semibold">Tipo</th>
                    )}
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
                      <td className="px-4 py-3 whitespace-nowrap">
                        {brandLabel(row.brand)}
                        {/* A linha e' valida e fica visivel, mas o operador
                            precisa saber que ela nao entra em elegiveis nem na
                            cobertura. Sem o rotulo, 223 ofertas de fora do
                            produto passariam por monitoramento de beleza. */}
                        {foraDoEscopo(row) && (
                          <span className="ml-2 inline-block text-xs font-semibold border border-slate-300 text-slate-600 rounded-full px-2 py-0.5 align-middle">
                            {ROTULO_FORA_DE_ESCOPO}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-slate-600 text-xs">
                        {row.item_id}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-slate-600 text-xs">
                        {row.seller_sku ?? INDISPONIVEL}
                      </td>
                      <td
                        className={`px-4 py-3 text-right tabular-nums whitespace-nowrap ${
                          temPrecoObservado(row) ? "" : "text-slate-500 italic"
                        }`}
                      >
                        {/* `null` vira "Não observado", nunca R$ 0,00. */}
                        {fmtPrecoObservado(row.advertised_price)}
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
                      {filtros.tipoDeProduto && (
                        <td className="px-4 py-3 text-xs text-slate-600 whitespace-nowrap">
                          {/* MATERIALIZADO pelo publisher. A tela nao
                              reclassifica kit — apenas traduz o rotulo. */}
                          {productTypeLabel(row.product_type)}
                        </td>
                      )}
                      <td className="px-4 py-3">
                        <StatusBadge status={row.comparison_status} />
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-600 whitespace-nowrap">
                        {matchLabel(row.match_method, row.match_quality)}
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-600 whitespace-nowrap">
                        {fmtCapturaPreco(row.observed_at)}
                        {/* Frescor da LINHA, distinto do da fotografia: com a
                            fotografia em dia, `stale` quer dizer que ESTA
                            oferta nao foi revista — nao que o sync atrasou. */}
                        {/* So' sob `snapshot_current`: la' o frescor VARIA por
                            oferta dentro da mesma carga. No ML todas as linhas
                            compartilham o frescor da resposta, que ja' aparece
                            no cabecalho — repetir por linha mudaria a tela
                            publicada sem acrescentar informacao. */}
                        {meta?.date_policy === "snapshot_current" &&
                          row.freshness_status !== "fresh" && (
                          <span className="block text-xs text-slate-500">
                            {situacaoFrescorLabel(
                              situacaoFrescor(row, meta.freshness_status),
                            )}
                          </span>
                        )}
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
                <dd className="font-semibold">
                  {brandLabel(linhaAberta.brand)}
                  {foraDoEscopo(linhaAberta) && (
                    <span className="ml-2 inline-block text-xs font-semibold border border-slate-300 text-slate-600 rounded-full px-2 py-0.5 align-middle">
                      {ROTULO_FORA_DE_ESCOPO}
                    </span>
                  )}
                </dd>
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
                    {fmtPrecoObservado(linhaAberta.advertised_price)}
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
              {/*
                Gate PMA-OPS-2 — TRES estados. A mensagem anterior dizia a
                mesma frase para "a fonte nao publica URL" (Shopee e TikTok,
                limite da origem) e para "o canal publica e esta linha veio
                sem" (ML, defeito do dado). A URL nunca e' montada: so'
                validada contra a allowlist do canal.
              */}
              {linkAnuncio?.estado === "disponivel" && linkAnuncio.url ? (
                <a
                  href={linkAnuncio.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block bg-violet-600 text-white rounded-lg px-4 py-3 min-h-[44px] text-sm font-semibold hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
                >
                  {linkAnuncio.rotulo}
                </a>
              ) : linkAnuncio?.estado === "ausente_na_fonte" ? (
                <p className="text-xs text-slate-500">{linkAnuncio.rotulo}</p>
              ) : linkAnuncio ? (
                <p className="text-xs text-amber-800">{linkAnuncio.rotulo}</p>
              ) : null}
            </div>
          </div>
        )}
      </KpiDrilldownDialog>
    </PageContainer>
  );
}
