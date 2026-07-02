export type ProdutosTab = "ml" | "tiktok" | "shopee";

export const ML_BRAND_VALUES = ["barbours", "kokeshi", "lescent", "rituaria"];
export const TK_SH_BRAND_VALUES = ["apice", "barbours", "kokeshi", "lescent", "rituaria"];

export function brandsForTab(tab: ProdutosTab): string[] {
  return tab === "ml" ? ML_BRAND_VALUES : TK_SH_BRAND_VALUES;
}

/**
 * Marca sobrevive a troca de aba somente se existir no novo canal (ex: ML
 * nao tem "apice" — trocar de TikTok/Shopee para ML com apice selecionado
 * deve resetar a marca). Nenhuma marca selecionada ("") sempre sobrevive.
 */
export function brandSurvivesTabChange(brand: string, nextTab: ProdutosTab): boolean {
  if (!brand) return true;
  return brandsForTab(nextTab).includes(brand);
}

/**
 * Bucket Pareto (A/B/C/D) nunca atravessa a troca de aba — cada canal
 * calcula um ranking proprio (marketplace + periodo + marca), entao um
 * bucket "A_top50" do ML nao corresponde ao mesmo conjunto de produtos do
 * "A_top50" do TikTok, mesmo com o nome igual.
 */
export function nextBucketAfterTabChange(): null {
  return null;
}

/**
 * Clicar num card de bucket ja ativo remove o filtro (equivalente a
 * "Todos"); clicar em outro bucket troca o filtro.
 */
export function toggleBucketSelection(current: string | null, clicked: string): string | null {
  return current === clicked ? null : clicked;
}

/**
 * Nota dirigida pela API (nunca hardcoded): so aparece quando o proprio
 * summary reporta produtos excluidos por GMV nao positivo. Mesma semantica
 * nos 3 canais — os buckets A/B/C/D nunca contam produtos com GMV<=0.
 */
export function zeroGmvNote(excluded: number | undefined): string {
  if (!excluded) return "";
  const plural = excluded !== 1;
  return ` Pareto considera apenas produtos com GMV positivo · ${excluded} produto${plural ? "s" : ""} sem GMV ${plural ? "estão" : "está"} fora dos buckets.`;
}
