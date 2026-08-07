"use client";

/**
 * Coordenacao das seis fontes da Gerencial V2 (Gate V2-1, Task B).
 *
 * A Gerencial passou de tres para SEIS superficies logicas: overview, brands,
 * executive-summary, canais, quality e a tendencia por canal (ate tres
 * chamadas de `/trend`, uma por canal selecionado).
 *
 * Por que nao um `Promise.all` unico: ele transforma qualquer falha parcial em
 * falha total da pagina. Aqui cada fonte tem estado independente, com a mesma
 * identidade de requisicao (`resolvedKey` x `requestKey`) e o mesmo contrato de
 * frescor dos gates U2-U5, via `computeRequestStatus`. Uma fonte com erro nao
 * apaga as demais fontes frescas; o bloco afetado nomeia o que faltou.
 *
 * Sobre `live`: `apiFetch` nunca rejeita — quando a API nao responde, os
 * fetchers devolvem MOCK com `live: false`. Isso cria uma armadilha: uma serie
 * mockada desenhada junto de series reais seria mock parecendo dado live. A
 * decisao de demonstracao vive em `lib/gerencial/demo-mode.ts` e exige que TODAS
 * as fontes com fallback esperadas para a requisicao atual tenham concluido com
 * `live: false`. Fora dela, uma resposta mockada e' INDISPONIBILIDADE daquela
 * fonte, nomeada na interface; enquanto a decisao nao conclui, o bloco fica em
 * estado neutro de carregamento.
 *
 * A metrica do grafico (GMV | Pedidos) NAO participa de nenhuma identidade de
 * fetch: `/trend` entrega as duas na mesma resposta.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchBrands,
  fetchCanais,
  fetchExecutiveSummary,
  fetchOverview,
  fetchQuality,
  fetchTrend,
  type BrandRow,
  type CanaisChannelMedian,
  type CanaisChannelRow,
  type ExecutiveSummaryData,
  type OverviewData,
  type QualityBrandRow,
  type QualityKpis,
  type TrendComparisonOutcome,
  type TrendGranularity,
  type TrendGranularityRequest,
  type TrendPoint,
} from "@/lib/api-client";
import { computeRequestStatus, type RequestStatus } from "@/lib/request-freshness";
import type { Marketplace } from "@/lib/mock-data";
import {
  buildChannelSeriesKey,
  buildGerencialRequestKey,
  type GerencialKeyInput,
} from "@/lib/gerencial/request-key";
import type {
  ChannelSeries,
  ChannelSeriesStatus,
  ComparisonStatus,
} from "@/lib/gerencial/trend-series";
import { decideDemoMode } from "@/lib/gerencial/demo-mode";

interface SourceState<T> {
  data: T | null;
  live: boolean;
  loading: boolean;
  errored: boolean;
  resolvedKey: string | null;
  refreshedAt: string | null;
}

function initialSource<T>(): SourceState<T> {
  return { data: null, live: false, loading: true, errored: false, resolvedKey: null, refreshedAt: null };
}

export interface Source<T> {
  data: T | null;
  live: boolean;
  status: RequestStatus;
  refreshedAt: string | null;
}

/**
 * Fonte pronta para consumo: dado antigo NUNCA vaza quando a chave nao bate.
 *
 * A decisao de demonstracao fecha a armadilha do fallback. Os fetchers nao
 * rejeitam: quando a API nao responde, devolvem MOCK com `live: false`. Sem
 * tratamento, um `/quality` que falhou renderizaria numeros mockados de
 * cancelamento ao lado de KPIs reais — mock parecendo dado live.
 *
 * Tres situacoes para uma resposta substituida por mock:
 * - `demoMode` verdadeiro: a pagina inteira esta rotulada, o mock e' coerente
 *   entre os blocos e pode ser exibido;
 * - decisao AINDA PENDENTE (`demoPending`): estado NEUTRO de carregamento — nao
 *   se exibem os numeros mockados, nem se afirma indisponibilidade definitiva;
 * - decisao concluida como "nao e' demonstracao": INDISPONIBILIDADE daquela
 *   fonte, e o bloco declara a falha.
 */
function toSource<T>(
  state: SourceState<T>,
  requestKey: string,
  demoMode: boolean,
  demoPending: boolean,
): Source<T> {
  const substitutedByMock = !state.loading && !state.errored && !state.live;
  const mockNotAllowed = substitutedByMock && !demoMode;
  const status = computeRequestStatus({
    // Mock sem decisao global ainda => segue como carregando (neutro).
    loading: state.loading || (mockNotAllowed && demoPending),
    error: state.errored || (mockNotAllowed && !demoPending),
    resolvedKey: state.resolvedKey,
    requestKey,
  });
  return {
    data: status.fresh ? state.data : null,
    live: status.fresh ? state.live : false,
    status,
    refreshedAt: status.fresh ? state.refreshedAt : null,
  };
}

export interface CanaisSlice {
  channelRows: CanaisChannelRow[];
  channelMedians: CanaisChannelMedian[];
}

export interface QualitySlice {
  kpis: QualityKpis;
  brands: QualityBrandRow[];
}

interface TrendSlice {
  /** Granularidade EFETIVA devolvida pelo backend (pode diferir da pedida — e
   * quando difere de um pedido explicito, e' contrato incompativel). */
  granularity: TrendGranularity;
  points: TrendPoint[];
  /** Gate V2-2: comparacao da MESMA resposta, com os tres estados de contrato
   * (`not_requested`, `unsupported`, `ok`) e a janela REAL quando `ok`. */
  comparison: TrendComparisonOutcome;
}

export interface GerencialSources {
  overview: Source<OverviewData>;
  brands: Source<BrandRow[]>;
  executiveSummary: Source<ExecutiveSummaryData>;
  canais: Source<CanaisSlice>;
  quality: Source<QualitySlice>;
  /** Uma entrada por canal selecionado — no maximo tres. */
  series: ChannelSeries[];
  /** Modo demonstracao da pagina: TODAS as fontes com fallback esperadas para a
   * requisicao atual concluiram com `live: false`. Ver `lib/gerencial/demo-mode.ts`. */
  demoMode: boolean;
  /** Qualquer fonte ainda carregando. */
  anyLoading: boolean;
  /** Toda fonte concluiu (fresca ou em erro). */
  allSettled: boolean;
  retry: () => void;
  retryKey: number;
}

export interface UseGerencialSourcesInput {
  channels: Marketplace[];
  brands: string[];
  dateFrom: string;
  dateTo: string;
  compare: boolean;
  /** Gate V2-2: granularidade PEDIDA. Entra somente na identidade das series
   * de `/trend` — trocar de granularidade nao refaz as outras cinco fontes. */
  granularity: TrendGranularityRequest;
}

export function useGerencialSources(input: UseGerencialSourcesInput): GerencialSources {
  const [retryKey, setRetryKey] = useState(0);

  const keyInput: GerencialKeyInput = useMemo(
    () => ({
      channels: input.channels,
      brands: input.brands,
      dateFrom: input.dateFrom,
      dateTo: input.dateTo,
      compare: input.compare,
      retryKey,
    }),
    [input.channels, input.brands, input.dateFrom, input.dateTo, input.compare, retryKey],
  );

  const requestKey = useMemo(() => buildGerencialRequestKey(keyInput), [keyInput]);

  const [overviewState, setOverviewState] = useState<SourceState<OverviewData>>(initialSource);
  const [brandsState, setBrandsState] = useState<SourceState<BrandRow[]>>(initialSource);
  const [execState, setExecState] = useState<SourceState<ExecutiveSummaryData>>(initialSource);
  const [canaisState, setCanaisState] = useState<SourceState<CanaisSlice>>(initialSource);
  const [qualityState, setQualityState] = useState<SourceState<QualitySlice>>(initialSource);
  const [seriesState, setSeriesState] = useState<Record<string, SourceState<TrendSlice>>>({});

  // Serializa os filtros para as chamadas — identico ao contrato ja usado.
  const filterOpts = useMemo(
    () => ({
      brands: input.brands,
      dateFrom: input.dateFrom,
      dateTo: input.dateTo,
      compare: input.compare,
    }),
    [input.brands, input.dateFrom, input.dateTo, input.compare],
  );

  const channelsKey = input.channels.join(",");

  useEffect(() => {
    let ignore = false;
    setOverviewState((s) => ({ ...s, loading: true, errored: false }));
    fetchOverview(input.channels, undefined, filterOpts)
      .then((res) => {
        if (ignore) return;
        setOverviewState({
          data: res.data,
          live: res.live,
          loading: false,
          errored: false,
          resolvedKey: requestKey,
          refreshedAt: res.meta.refreshedAt,
        });
      })
      .catch(() => {
        if (ignore) return;
        // Conclui a chave mesmo no erro: sem isso a fonte fica presa em
        // "carregando" para sempre (mesmo Finding do U4).
        setOverviewState({ data: null, live: false, loading: false, errored: true, resolvedKey: requestKey, refreshedAt: null });
      });
    return () => {
      ignore = true;
    };
  }, [requestKey, channelsKey, filterOpts]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let ignore = false;
    setBrandsState((s) => ({ ...s, loading: true, errored: false }));
    fetchBrands(input.channels, undefined, filterOpts)
      .then((res) => {
        if (ignore) return;
        setBrandsState({
          data: res.data,
          live: res.live,
          loading: false,
          errored: false,
          resolvedKey: requestKey,
          refreshedAt: res.meta.refreshedAt,
        });
      })
      .catch(() => {
        if (ignore) return;
        setBrandsState({ data: null, live: false, loading: false, errored: true, resolvedKey: requestKey, refreshedAt: null });
      });
    return () => {
      ignore = true;
    };
  }, [requestKey, channelsKey, filterOpts]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let ignore = false;
    setExecState((s) => ({ ...s, loading: true, errored: false }));
    fetchExecutiveSummary(input.channels, filterOpts)
      .then((res) => {
        if (ignore) return;
        // `data: null` aqui e' indisponibilidade real (nao ha mock de resumo).
        setExecState({
          data: res.data,
          live: res.data != null,
          loading: false,
          errored: res.data == null,
          resolvedKey: requestKey,
          refreshedAt: res.data?.period.refreshed_at ?? null,
        });
      })
      .catch(() => {
        if (ignore) return;
        setExecState({ data: null, live: false, loading: false, errored: true, resolvedKey: requestKey, refreshedAt: null });
      });
    return () => {
      ignore = true;
    };
  }, [requestKey, channelsKey, filterOpts]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let ignore = false;
    setCanaisState((s) => ({ ...s, loading: true, errored: false }));
    fetchCanais(input.channels, undefined, filterOpts)
      .then((res) => {
        if (ignore) return;
        setCanaisState({
          data: { channelRows: res.channelRows, channelMedians: res.channelMedians },
          live: res.live,
          loading: false,
          errored: false,
          resolvedKey: requestKey,
          refreshedAt: res.meta.refreshedAt,
        });
      })
      .catch(() => {
        if (ignore) return;
        setCanaisState({ data: null, live: false, loading: false, errored: true, resolvedKey: requestKey, refreshedAt: null });
      });
    return () => {
      ignore = true;
    };
  }, [requestKey, channelsKey, filterOpts]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let ignore = false;
    setQualityState((s) => ({ ...s, loading: true, errored: false }));
    fetchQuality(input.channels, undefined, filterOpts)
      .then((res) => {
        if (ignore) return;
        setQualityState({
          data: { kpis: res.kpis, brands: res.brands },
          live: res.live,
          loading: false,
          errored: false,
          resolvedKey: requestKey,
          refreshedAt: res.meta.refreshedAt,
        });
      })
      .catch(() => {
        if (ignore) return;
        setQualityState({ data: null, live: false, loading: false, errored: true, resolvedKey: requestKey, refreshedAt: null });
      });
    return () => {
      ignore = true;
    };
  }, [requestKey, channelsKey, filterOpts]); // eslint-disable-line react-hooks/exhaustive-deps

  // Tendencia: UMA chamada por canal selecionado, disparadas CONCORRENTEMENTE.
  // Nenhuma quarta chamada agregada — o total por bucket e' a soma das series.
  useEffect(() => {
    let ignore = false;
    const channels = input.channels;
    setSeriesState((prev) => {
      const next: Record<string, SourceState<TrendSlice>> = {};
      for (const channel of channels) {
        const existing = prev[channel];
        next[channel] = existing
          ? { ...existing, loading: true, errored: false }
          : initialSource<TrendSlice>();
      }
      return next;
    });

    for (const channel of channels) {
      const channelKey = buildChannelSeriesKey(keyInput, channel, input.granularity);
      fetchTrend([channel], filterOpts, input.granularity)
        .then((res) => {
          if (ignore) return;
          setSeriesState((prev) => ({
            ...prev,
            [channel]: {
              data: { granularity: res.granularity, points: res.data, comparison: res.comparison },
              live: res.live,
              loading: false,
              errored: false,
              resolvedKey: channelKey,
              refreshedAt: res.meta.refreshedAt,
            },
          }));
        })
        .catch(() => {
          if (ignore) return;
          setSeriesState((prev) => ({
            ...prev,
            [channel]: {
              data: null,
              live: false,
              loading: false,
              errored: true,
              resolvedKey: channelKey,
              refreshedAt: null,
            },
          }));
        });
    }

    return () => {
      ignore = true;
    };
    // `input.granularity` participa: trocar o grao refaz SO' as series.
  }, [requestKey, channelsKey, filterOpts, keyInput, input.granularity]); // eslint-disable-line react-hooks/exhaustive-deps

  /**
   * Modo demonstracao e' propriedade da PAGINA e exige que TODAS as fontes com
   * fallback esperadas para a requisicao ATUAL tenham concluido com `live: false`
   * — as quatro agregadas mais uma serie por canal selecionado.
   *
   * A regra vive num modulo puro e testavel (`lib/gerencial/demo-mode.ts`)
   * porque a versao anterior errava de duas formas silenciosas: `every` sobre uma
   * lista filtrada e' vacuamente verdadeiro (bastava o overview mock concluir
   * primeiro para a pagina virar "demonstracao" enquanto o resto carregava), e
   * `Object.values(seriesState)` incluia canais fora da selecao e chaves de
   * requisicoes antigas.
   */
  const demoDecision = useMemo(
    () =>
      decideDemoMode({
        requestKey,
        aggregates: {
          overview: overviewState,
          brands: brandsState,
          canais: canaisState,
          quality: qualityState,
        },
        selectedChannels: input.channels,
        seriesByChannel: seriesState,
        expectedSeriesKey: (channel) => buildChannelSeriesKey(keyInput, channel, input.granularity),
      }),
    [requestKey, overviewState, brandsState, canaisState, qualityState, input.channels, seriesState, keyInput],
  );
  const demoMode = demoDecision.demoMode;
  const demoPending = demoDecision.pending;

  const overview = useMemo(
    () => toSource(overviewState, requestKey, demoMode, demoPending),
    [overviewState, requestKey, demoMode, demoPending],
  );
  const brands = useMemo(
    () => toSource(brandsState, requestKey, demoMode, demoPending),
    [brandsState, requestKey, demoMode, demoPending],
  );
  // O executive-summary NAO tem fallback mock: `data: null` ja e' erro real,
  // entao a decisao de demonstracao nao muda nada para ele.
  const executiveSummary = useMemo(
    () => toSource(execState, requestKey, true, false),
    [execState, requestKey],
  );
  const canais = useMemo(
    () => toSource(canaisState, requestKey, demoMode, demoPending),
    [canaisState, requestKey, demoMode, demoPending],
  );
  const quality = useMemo(
    () => toSource(qualityState, requestKey, demoMode, demoPending),
    [qualityState, requestKey, demoMode, demoPending],
  );

  const compareRequested = input.compare;
  const series = useMemo<ChannelSeries[]>(() => {
    return input.channels.map((channel) => {
      const state = seriesState[channel];
      const channelKey = buildChannelSeriesKey(keyInput, channel, input.granularity);
      const status = computeRequestStatus({
        loading: state?.loading ?? true,
        error: state?.errored ?? false,
        resolvedKey: state?.resolvedKey ?? null,
        requestKey: channelKey,
      });
      // Mesma regra de tres estados do `toSource`: mock so' e' desenhavel em
      // demonstracao; com a decisao pendente a serie segue NEUTRA (carregando).
      const mockNotAllowed = status.fresh && !state?.live && !demoMode;
      let seriesStatus: ChannelSeriesStatus;
      if (status.loading || (mockNotAllowed && demoPending)) seriesStatus = "loading";
      else if (status.error || mockNotAllowed) seriesStatus = "error";
      else seriesStatus = "fresh";
      // Estado da COMPARACAO, independente do da serie atual: ela pode falhar
      // sozinha, e `compare=false` e' `not_requested` (nunca erro).
      const cmp = state?.data?.comparison ?? null;
      const cmpOk = cmp?.status === "ok" ? cmp : null;
      let comparisonStatus: ComparisonStatus;
      if (!compareRequested) comparisonStatus = "not_requested";
      else if (seriesStatus === "loading") comparisonStatus = "loading";
      else if (seriesStatus === "error") comparisonStatus = "error";
      // `unsupported`: a comparacao foi PEDIDA e a API nao respondeu o campo.
      // Nao e' "nao solicitada" nem uma falha de rede — e' contrato ausente.
      else if (cmp == null || cmp.status === "unsupported") comparisonStatus = "unsupported";
      else if (cmp.status === "not_requested") comparisonStatus = "unsupported";
      else if (cmpOk!.data.length === 0) comparisonStatus = "empty";
      else comparisonStatus = "fresh";
      const settled = comparisonStatus === "fresh" || comparisonStatus === "empty";
      return {
        channel,
        status: seriesStatus,
        granularity: state?.data?.granularity ?? "day",
        points: seriesStatus === "fresh" ? (state?.data?.points ?? []) : [],
        comparisonStatus,
        comparisonPoints: comparisonStatus === "fresh" ? (cmpOk?.data ?? []) : [],
        // Janela do CONTRATO, transportada intacta e apenas quando a comparacao
        // concluiu. Vazia (`data: []`) tambem tem janela.
        comparisonDateFrom: settled ? (cmpOk?.dateFrom ?? null) : null,
        comparisonDateTo: settled ? (cmpOk?.dateTo ?? null) : null,
      };
    });
  }, [input.channels, seriesState, keyInput, demoMode, demoPending, compareRequested, input.granularity]);

  const sourceStatuses = [overview.status, brands.status, executiveSummary.status, canais.status, quality.status];
  const anyLoading =
    sourceStatuses.some((s) => s.loading) || series.some((s) => s.status === "loading");
  const allSettled = !anyLoading;

  const retry = useCallback(() => setRetryKey((k) => k + 1), []);

  return {
    overview,
    brands,
    executiveSummary,
    canais,
    quality,
    series,
    demoMode,
    anyLoading,
    allSettled,
    retry,
    retryKey,
  };
}
