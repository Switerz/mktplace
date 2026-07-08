/**
 * Toggle de multi-selecao generico. Ao contrario de toggleMarketplace (que
 * nunca permite selecao vazia), aqui a selecao vazia e um estado valido —
 * usado por BrandFilter, onde "vazio" significa "todas as marcas" (sem
 * filtro), nao "nenhuma marca".
 */
export function toggleMultiSelect<T>(selection: readonly T[], item: T): T[] {
  return selection.includes(item)
    ? selection.filter((s) => s !== item)
    : [...selection, item];
}
