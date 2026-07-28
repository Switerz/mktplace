// Logica pura de estado do polling da pagina Tempo Real (Gate U5) — extraida
// para ser testavel sem React/JSDOM. Os 5 estados sao sempre mutuamente
// exclusivos e cobrem exatamente a matriz de aceite do Task 7/4:
//
// - "initial"     : carga inicial em andamento, ainda sem nenhum dado.
// - "updating"    : refresh (automatico ou manual) em andamento, com ou sem
//                   dado anterior em memoria.
// - "unavailable" : nao ha (e nunca houve, ou deixou de haver) dado exibivel
//                   — nunca inventa numero.
// - "stale"       : ha dado anterior valido em memoria, mas a ULTIMA
//                   tentativa de atualizacao falhou — o dado e preservado e
//                   marcado explicitamente como possivelmente defasado
//                   (unica pagina do revamp com essa excecao, Task 7).
// - "fresh"       : ha dado e a ultima tentativa de atualizacao teve sucesso.
export type TempoRealStatus = "initial" | "updating" | "unavailable" | "stale" | "fresh";

export interface TempoRealStatusInput {
  /** true apenas durante a primeiríssima tentativa de fetch da tela. */
  initialLoading: boolean;
  /** true durante qualquer fetch subsequente (timer automatico ou botao manual). */
  refreshing: boolean;
  /** ha dado em memoria para exibir (mesmo que potencialmente defasado). */
  hasData: boolean;
  /** a ULTIMA tentativa de fetch concluida falhou (retornou null/erro). */
  lastFetchFailed: boolean;
}

export function computeTempoRealStatus(input: TempoRealStatusInput): TempoRealStatus {
  if (input.initialLoading) return "initial";
  if (input.refreshing) return "updating";
  if (!input.hasData) return "unavailable";
  if (input.lastFetchFailed) return "stale";
  return "fresh";
}
