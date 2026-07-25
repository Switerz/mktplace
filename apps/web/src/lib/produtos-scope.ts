// Logica pura da linha compacta "Escopo atual" da aba Produtos (Gate U4,
// docs/UI_REVAMP_PLAN.md Task 2). Extraida para ser testavel sem depender do
// estado assincrono por canal (async-channel-state.ts) ou do React.

export interface ProdutosScopeInput {
  /** Rotulo do marketplace ja resolvido pelo chamador (TABS.find(...).label). */
  channelLabel: string;
  /** Rotulo da marca ja resolvido pelo chamador ("Todas as marcas" ou o
   * rotulo em maiusculas, ex: "BARBOURS") — nunca o slug cru. */
  brandLabel: string;
  /** Rotulo do mes (TikTok/Shopee) — `null` para ML, que nao tem periodo
   * mensal na fonte (ranking acumulado, ver produtos_audit.md secao 3.1). */
  periodLabel: string | null;
  /** `total` da resposta da API para a pagina atual — `null` enquanto nao
   * ha resposta ainda (nunca mostra contagem inventada). */
  total: number | null;
  offset: number;
  limit: number;
}

/**
 * Monta a linha "Escopo atual": canal · marca · periodo (ou "ranking
 * acumulado atual" para ML) · intervalo de produtos exibidos na pagina.
 * Nunca inclui a contagem quando `total` ainda e `null` (API sem resposta).
 */
export function formatProdutosScope(input: ProdutosScopeInput): string {
  const parts = [input.channelLabel, input.brandLabel, input.periodLabel ?? "ranking acumulado atual"];
  if (input.total != null) {
    const from = input.total === 0 ? 0 : input.offset + 1;
    const to = Math.min(input.offset + input.limit, input.total);
    parts.push(`${from}–${to} de ${input.total} produtos`);
  }
  return parts.join(" · ");
}
