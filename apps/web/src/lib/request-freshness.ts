// Logica pura de frescor de requisicao (Gate U4) — mesmo padrao "Finding 2"
// adotado em Canais (Gate U3) e na Gerencial (Gate U2), extraida para ser
// compartilhada e testavel sem depender de React. Dados so sao "frescos"
// quando: nao ha carregamento em andamento, nao ha erro definitivo, e a
// chave da ultima requisicao resolvida bate com a chave da requisicao
// atual (fecha o frame de render em que os filtros ja mudaram mas o efeito
// de fetch ainda nao rodou).
export interface RequestFreshnessInput {
  loading: boolean;
  error: boolean;
  resolvedKey: string | null;
  requestKey: string;
}

export function isRequestFresh(input: RequestFreshnessInput): boolean {
  return !input.loading && !input.error && input.resolvedKey === input.requestKey;
}

/**
 * Rodada de correcao consolidada (FINDING 2) — `!dataIsFresh` sozinho nao
 * distingue "ainda carregando" de "erro definitivo": depois de um erro,
 * `!dataIsFresh` fica `true` para sempre, deixando skeleton/opacidade/
 * `aria-busy=true` ligados como se a pagina ainda estivesse buscando dado.
 *
 * `computeRequestStatus` separa os 3 estados possiveis, sempre mutuamente
 * exclusivos:
 * - `loading`: requisicao em andamento OU a chave atual ainda nao bate com
 *   a ultima chave resolvida (cobre o frame de render anterior ao efeito
 *   rodar — ex.: troca de filtro, ou `retryKey` incrementado no clique de
 *   "Tentar novamente", que ja produz este estado ANTES do proprio efeito
 *   de fetch executar);
 * - `error`: a chave atual JA foi resolvida (nao esta carregando) e essa
 *   resolucao foi um erro definitivo;
 * - `fresh`: a chave atual foi resolvida com sucesso.
 */
export interface RequestStatus {
  loading: boolean;
  error: boolean;
  fresh: boolean;
}

export function computeRequestStatus(input: RequestFreshnessInput): RequestStatus {
  const keyPending = input.resolvedKey !== input.requestKey;
  const loading = input.loading || keyPending;
  const error = !loading && input.error;
  const fresh = !loading && !error;
  return { loading, error, fresh };
}
