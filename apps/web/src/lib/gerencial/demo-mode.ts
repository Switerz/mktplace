/**
 * Decisao de MODO DEMONSTRACAO da Gerencial V2 (reparacao de stop-loss do V2-1).
 *
 * Os fetchers da Torre nao rejeitam: quando a API nao responde, devolvem MOCK com
 * `live: false`. Duas coisas dependem de acertar essa decisao:
 *
 * 1. Fora do modo demonstracao, uma resposta mock e' INDISPONIBILIDADE daquela
 *    fonte — nunca numeros mockados ao lado de numeros reais.
 * 2. No modo demonstracao a pagina inteira esta rotulada, e o mock e' coerente
 *    entre os blocos.
 *
 * A versao anterior usava `settled.every((s) => !s.live)` sobre uma lista
 * filtrada, e `every` de uma lista PARCIAL e' vacuamente satisfeito: bastava o
 * overview mock concluir primeiro para `demoMode` virar `true` enquanto as
 * demais fontes ainda carregavam, e KPIs mockados podiam aparecer por alguns
 * frames. Pior: a lista de series vinha de `Object.values(seriesState)`, que
 * inclui canais fora da selecao atual e chaves de requisicoes antigas.
 *
 * Aqui a decisao e' explicita sobre o CONJUNTO ESPERADO: as quatro fontes
 * agregadas com fallback (`overview`, `brands`, `canais`, `quality`) mais UMA
 * serie por canal atualmente selecionado. O `executive-summary` nao entra —
 * nao tem fallback mock; ele falha de verdade.
 */
import type { Marketplace } from "../mock-data.ts";

/** Estado minimo necessario de uma fonte com fallback mock. */
export interface FallbackSourceState {
  loading: boolean;
  errored: boolean;
  /** Chave da ultima resposta concluida; `null` antes da primeira. */
  resolvedKey: string | null;
  /** `true` = veio da API; `false` = mock substituido. */
  live: boolean;
}

export const AGGREGATE_FALLBACK_SOURCES = ["overview", "brands", "canais", "quality"] as const;
export type AggregateFallbackSource = (typeof AGGREGATE_FALLBACK_SOURCES)[number];

export interface DemoModeInput {
  /** Chave da requisicao atual das fontes agregadas. */
  requestKey: string;
  aggregates: Record<AggregateFallbackSource, FallbackSourceState>;
  selectedChannels: readonly Marketplace[];
  /** Estado da serie por canal. Canal ausente = requisicao nao despachada. */
  seriesByChannel: Partial<Record<Marketplace, FallbackSourceState>>;
  /** Chave esperada da serie daquele canal na requisicao atual. */
  expectedSeriesKey: (channel: Marketplace) => string;
}

export interface DemoModeDecision {
  /** `true` somente quando TODA fonte esperada concluiu com `live: false`. */
  demoMode: boolean;
  /**
   * `true` enquanto a decisao global ainda pode mudar — ou seja, enquanto alguma
   * fonte esperada nao concluiu para a requisicao atual E nenhuma conclusao
   * TERMINAL (live ou erro) ja resolveu a questao. Nesse intervalo, um bloco cuja
   * resposta foi substituida por mock fica em estado NEUTRO de carregamento:
   * nunca exibe os numeros mockados nem afirma indisponibilidade definitiva.
   */
  pending: boolean;
}

/**
 * Classificacao de uma fonte esperada.
 *
 * `terminal_error` existe porque um erro TAMBEM e' uma conclusao: sem ele, uma
 * fonte em erro deixava `pending` verdadeiro para sempre, os mocks das demais
 * ficavam presos em carregamento neutro, `anyLoading` nunca encerrava e a
 * interface podia ficar em "Atualizando…" indefinidamente.
 */
export type FallbackClassification = "pending" | "live" | "mock" | "terminal_error";

export function classifyFallbackSource(
  state: FallbackSourceState | undefined,
  expectedKey: string,
): FallbackClassification {
  // Estado ausente = requisicao nao despachada.
  if (!state) return "pending";
  // Chave antiga => a resposta pertence a outro filtro. Um erro com chave antiga
  // NAO e' terminal para a requisicao nova.
  if (state.resolvedKey !== expectedKey) return "pending";
  if (state.loading) return "pending";
  if (state.errored) return "terminal_error";
  return state.live ? "live" : "mock";
}

export function decideDemoMode(input: DemoModeInput): DemoModeDecision {
  const expected: { state: FallbackSourceState | undefined; key: string }[] = [
    ...AGGREGATE_FALLBACK_SOURCES.map((name) => ({
      state: input.aggregates[name],
      key: input.requestKey,
    })),
    // Somente os canais da selecao ATUAL, cada um com a sua propria chave.
    ...input.selectedChannels.map((channel) => ({
      state: input.seriesByChannel[channel],
      key: input.expectedSeriesKey(channel),
    })),
  ];

  const kinds = expected.map(({ state, key }) => classifyFallbackSource(state, key));

  // 1. Uma unica fonte live derruba a demonstracao imediatamente, mesmo com as
  //    demais carregando: a pagina tem dado real, logo mock nao pode passar por
  //    dado real em nenhum bloco.
  if (kinds.includes("live")) return { demoMode: false, pending: false };

  // 2. Um erro terminal tambem RESOLVE a questao: a demonstracao ja nao pode ser
  //    confirmada, entao nao ha o que esperar. As fontes em mock passam a
  //    indisponiveis em vez de ficarem carregando para sempre.
  if (kinds.includes("terminal_error")) return { demoMode: false, pending: false };

  // 3. Todas as fontes esperadas concluiram em mock.
  if (kinds.length > 0 && kinds.every((k) => k === "mock")) {
    return { demoMode: true, pending: false };
  }

  // 4. Resta alguma fonte ausente, carregando ou com chave antiga.
  return { demoMode: false, pending: true };
}
