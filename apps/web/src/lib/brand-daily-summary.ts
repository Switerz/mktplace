import type { DailyRow } from "./mock-daily";
import { isAllSelected, type MarketplaceSelection } from "./marketplace-filter.ts";

export interface DailySummary {
  gmv: number;
  /** null quando pedidos nao sao confiaveis para a selecao atual (ver
   * `isOrdersReliable`) — nunca um total estimado/rateado. */
  orders: number | null;
  adSpend: number | null;
  /** null pela mesma razao de `orders` (indisponivel), OU quando orders = 0
   * (sem pedidos no periodo) — nunca 0 fabricado nem NaN/Infinity. */
  avgTicket: number | null;
}

/**
 * Decide se o campo `orders` de `DailyRow` pode ser exibido para a selecao
 * de canal atual. Em modo ao vivo a API ja filtra pedidos por marketplace no
 * proprio endpoint, entao o total sempre reflete a selecao. Em modo
 * demonstracao (`generateDailyData`, mock-daily.ts) o mock gera um unico
 * `orders` combinando os 3 canais, sem separacao por canal — so e confiavel
 * quando a selecao cobre os 3 (o total "combinado" e o total "selecionado"
 * coincidem); qualquer selecao parcial no mock tornaria os pedidos
 * silenciosamente superestimados (Gate U3, Task 5).
 */
export function isOrdersReliable(isLive: boolean, filter: MarketplaceSelection): boolean {
  return isLive || isAllSelected(filter);
}

/**
 * Projeta uma janela de `DailyRow[]` pela selecao de canal atual — usado
 * tanto pelo grafico de tendencia quanto pela tabela "Ultimos 7 Dias" da
 * pagina de marca, para que os dois SEMPRE exibam o mesmo GMV (Gate U3,
 * Finding 2). Canais nao selecionados ficam `null` (nunca herdam o valor do
 * mock, que gera os 3 canais juntos independente do filtro); `total_gmv` e
 * sempre recalculado como a soma dos canais selecionados, nunca reaproveita
 * o `total_gmv` original da linha (que no mock sempre soma os 3 canais).
 * Em modo ao vivo o efeito e identico a um no-op (a API ja filtra por
 * marketplace, entao os campos nao selecionados ja chegam nulos e o total
 * recalculado bate com o `total_gmv` retornado).
 */
export function projectDailyRowsBySelection(rows: DailyRow[], filter: MarketplaceSelection): DailyRow[] {
  const showTk = filter.includes("tiktok");
  const showMl = filter.includes("ml");
  const showSh = filter.includes("shopee");

  return rows.map((r) => {
    const tiktok_gmv = showTk ? r.tiktok_gmv : null;
    const ml_gmv = showMl ? r.ml_gmv : null;
    const shopee_gmv = showSh ? r.shopee_gmv : null;
    return {
      ...r,
      tiktok_gmv,
      ml_gmv,
      shopee_gmv,
      total_gmv: (tiktok_gmv ?? 0) + (ml_gmv ?? 0) + (shopee_gmv ?? 0),
    };
  });
}

/**
 * Agrega uma janela de dias (`DailyRow[]`) para os canais selecionados.
 * Soma explicitamente apenas tiktok_gmv/ml_gmv/shopee_gmv dos canais
 * marcados como selecionados — nunca reaproveita `total_gmv` (que reflete
 * "todos os canais consultados na API") como atalho para uma seleção
 * parcial, para não misturar silenciosamente canais fora do filtro.
 * `ordersReliable` (default true, ver `isOrdersReliable`) zera pedidos e
 * ticket médio para null em vez de expor o total combinado do mock quando a
 * seleção de canal é parcial.
 */
export function summarize(rows: DailyRow[], filter: MarketplaceSelection, ordersReliable: boolean = true): DailySummary {
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
  const ordersTotal = rows.reduce((s, r) => s + r.orders, 0);
  const orders = ordersReliable ? ordersTotal : null;
  const adSpend = (showMl || showSh) ? rows.reduce((s, r) => s + (r.ad_spend ?? 0), 0) : null;

  return { gmv, orders, adSpend, avgTicket: orders != null && orders > 0 ? gmv / orders : (orders == null ? null : 0) };
}
