import { fmtBrlFull } from "./formatters.ts";

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

/**
 * Preco medio ponderado do escopo filtrado (receita total / unidades
 * totais, nunca media simples de avg_price por linha) — dirigido pela API,
 * mesma logica de zeroGmvNote: so aparece quando o summary reporta um
 * valor calculavel.
 */
export function avgPriceNote(avgPriceWeighted: number | null | undefined): string {
  if (avgPriceWeighted == null) return "";
  return ` Preço médio do escopo (ponderado por unidade): ${fmtBrlFull(avgPriceWeighted)}.`;
}

/**
 * Nota fixa sobre disponibilidade de margem/eficiencia de Ads por
 * marketplace (Gate 1, docs/sections/produtos_audit.md secao 10.3): nenhum
 * dos 3 canais tem CMV/custo de produto, entao margem real nunca aparece.
 * So o ML tem ad_spend/roas/acos reais por produto.
 */
export function marginUnavailableNote(tab: ProdutosTab): string {
  if (tab === "ml") {
    return "Margem real indisponível: não há CMV/custo de produto na fonte atual. ROAS/ACOS abaixo refletem eficiência de Ads, não margem.";
  }
  return "Margem/eficiência de Ads por produto indisponível nesta fonte: não há dado de ads/custo por produto para este marketplace.";
}

const MONTH_ABBR = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];

export interface MonthOption {
  value: string;
  label: string;
}

/**
 * Gera os ultimos `count` meses (o atual primeiro) a partir de `today` — em
 * vez de uma lista hardcoded (bug de manutencao corrigido no Gate 2, ver
 * produtos_audit.md secao 10.4). `today` e injetado pelo chamador porque
 * `new Date()` sem argumento nao e permitido em scripts de workflow e,
 * mesmo fora deles, injetar a data deixa a funcao pura e testavel.
 */
export function lastNMonths(count: number, today: Date): MonthOption[] {
  const months: MonthOption[] = [];
  for (let i = 0; i < count; i++) {
    const d = new Date(today.getFullYear(), today.getMonth() - i, 1);
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    const yy = String(d.getFullYear()).slice(2);
    const label = `${MONTH_ABBR[d.getMonth()]}/${yy}${i === 0 ? " (atual)" : ""}`;
    months.push({ value, label });
  }
  return months;
}
