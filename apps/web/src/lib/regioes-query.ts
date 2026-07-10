/**
 * Montagem da querystring dos endpoints /api/v1/regioes/* — modulo isolado
 * (sem depender de api-client.ts, que importa mock-data.ts com imports sem
 * extensao — so' resolvem sob o bundler do Next, nao sob `node --test`) para
 * poder ser testado diretamente. Nao usa ref_month/compare (o contrato de
 * filtros globais completo vive em api-client.ts::buildFilterQuery; esta
 * tela sempre resolve date_from/date_to explicitos via useGlobalFilters).
 */

export interface RegioesQueryFilters {
  brands?: string[];
  dateFrom?: string;
  dateTo?: string;
  /** UF(s) — filtro local da tela, so aceito por summary/by-uf. */
  uf?: string[];
}

/** Pura — testável sem rede. `channels` ja deve vir serializado ("all",
 * "tiktok", "tiktok,ml" etc). */
export function buildRegioesQueryParams(channels: string, filters?: RegioesQueryFilters): URLSearchParams {
  const qs = new URLSearchParams();
  qs.set("channels", channels);
  if (filters?.brands && filters.brands.length > 0) {
    qs.set("brands", [...filters.brands].sort().join(","));
  }
  if (filters?.dateFrom && filters?.dateTo) {
    qs.set("date_from", filters.dateFrom);
    qs.set("date_to", filters.dateTo);
  }
  if (filters?.uf && filters.uf.length > 0) {
    qs.set("uf", [...filters.uf].sort().join(","));
  }
  return qs;
}
