import type { ScopeQuality } from "./api-client";

// Regras de EXIBICAO do selo de qualidade dos Produtos Shopee. Ficam fora do
// api-client para serem testaveis sem o resto do cliente: elas decidem o que a
// tela desenha quando o escopo nao tem valor — e errar aqui e exatamente o
// defeito de desenhar ausencia como "R$ 0K".

/**
 * O escopo NAO tem valor confiavel para exibir: nada carregado, ou so linhas
 * de GMV zero. A tela tem de dizer isso em vez de desenhar "R$ 0K" — ausencia
 * nunca vira zero. `null` quando ha valor (ou quando o selo nao foi medido).
 */
export function scopeUnavailableReason(q: ScopeQuality | null | undefined): string | null {
  if (!q) return null;
  if (q.load_status === "load_absent") {
    return "Sem dado de produto nesta competência — ausência de dado, não venda zero.";
  }
  if (q.eligibility_status === "no_eligible_rows") {
    return "Sem valor confiável: as linhas carregadas têm todas GMV zero (nenhum pedido Concluído na carga) — ausência de dado, não venda zero.";
  }
  return null;
}

/** Nota curta para o total quando alguma marca esperada ficou de fora. */
export function partialTotalNote(q: ScopeQuality | null | undefined): string | null {
  const bc = q?.brand_coverage;
  if (!bc || !bc.total_is_partial || q?.brand) return null;
  return `Total PARCIAL — fora do total: ${bc.missing.join(", ")}.`;
}
