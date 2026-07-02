// Logica pura (sem React) para gerenciar o estado de uma requisicao
// assincrona por canal (ML/TikTok/Shopee, tabela ou summary). Extraida da
// pagina Produtos para ser testavel sem navegador/JSDOM e para centralizar
// a regra de "resposta obsoleta nunca sobrescreve estado mais recente".
export interface ChannelState<T> {
  data: T | null;
  loading: boolean;
  // true somente se a ULTIMA requisicao concluida deste canal teve sucesso.
  // Nunca deve refletir o sucesso de outro canal/aba.
  live: boolean;
}

export function initialChannelState<T>(): ChannelState<T> {
  return { data: null, loading: false, live: false };
}

// Chamado ao DISPARAR uma nova requisicao (troca de marca, periodo, bucket
// ou aba/marketplace, ou paginacao/ordenacao). Limpa os dados IMEDIATAMENTE
// (data -> null) em vez de manter a pagina anterior visivel durante o
// carregamento — o shell da tabela mostra skeleton quando data=null+loading,
// nunca dados que podem pertencer a um filtro diferente do atual.
export function startFetch<T>(state: ChannelState<T>): ChannelState<T> {
  return { data: null, loading: true, live: state.live };
}

// Chamado quando a requisicao com id `requestId` termina (sucesso ou falha).
// `isCurrent` deve ser `requestId === <ultimo id disparado para este canal>`,
// calculado pelo chamador no momento da resolucao (nao antes). Se uma
// requisicao mais recente ja foi disparada, esta resposta e descartada sem
// tocar no estado — nunca sobrescreve dados/loading mais novos.
export function resolveFetch<T>(state: ChannelState<T>, isCurrent: boolean, result: T | null): ChannelState<T> {
  if (!isCurrent) return state;
  if (result === null) {
    // Falha ou API offline: nunca deixa dados antigos exibidos como se
    // pertencessem ao filtro/pagina atual — volta para o estado "sem dados".
    return { data: null, loading: false, live: false };
  }
  return { data: result, loading: false, live: true };
}

// Equivalente a resolveFetch(state, isCurrent, null), para o caminho de
// excecao (catch) — mantido separado por legibilidade no call site.
export function resolveFetchError<T>(state: ChannelState<T>, isCurrent: boolean): ChannelState<T> {
  return resolveFetch(state, isCurrent, null);
}
