import type { DailyRow } from "./mock-daily";
import type { MarketplaceSelection } from "./marketplace-filter";

export interface DailySummary {
  gmv: number;
  orders: number;
  adSpend: number | null;
  avgTicket: number;
}

/**
 * Agrega uma janela de dias (`DailyRow[]`) para os canais selecionados.
 * Soma explicitamente apenas tiktok_gmv/ml_gmv/shopee_gmv dos canais
 * marcados como selecionados — nunca reaproveita `total_gmv` (que reflete
 * "todos os canais consultados na API") como atalho para uma seleção
 * parcial, para não misturar silenciosamente canais fora do filtro.
 */
export function summarize(rows: DailyRow[], filter: MarketplaceSelection): DailySummary {
  const showTk = filter.includes("tiktok");
  const showMl = filter.includes("ml");
  const showSh = filter.includes("shopee");

  const gmv = rows.reduce((s, r) => {
    let v = 0;
    if (showTk) v += r.tiktok_gmv ?? 0;
    if (showMl) v += r.ml_gmv ?? 0;
    if (showSh) v += r.shopee_gmv ?? 0;
    return s + v;
  }, 0);
  const orders = rows.reduce((s, r) => s + r.orders, 0);
  const adSpend = (showMl || showSh) ? rows.reduce((s, r) => s + (r.ad_spend ?? 0), 0) : null;

  return { gmv, orders, adSpend, avgTicket: orders > 0 ? gmv / orders : 0 };
}
