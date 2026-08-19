// Fixtures determinISticas do MCP do Oraculo.
// Nenhum teste da suite toca a rede, producao ou banco: o upstream e' sempre
// um `fetch` falso construido aqui. Os numeros sao ficticios.

/** Relogio fixo: 2026-08-18T12:00:00Z -> 2026-08-18 em America/Sao_Paulo. */
export const FIXED_NOW = new Date("2026-08-18T12:00:00.000Z");

export const REFRESHED_AT = "2026-08-05T18:53:53.827542+00:00";

export const OVERVIEW = {
  ref_month: "2026-07",
  marketplace: "all",
  current: {
    gmv: 1000,
    tiktok_gmv: 600,
    ml_gmv: 400,
    // Shopee ausente de proposito: precisa continuar `null`, nunca virar 0.
    shopee_gmv: null,
    orders: 40,
    avg_ticket: 25,
    ml_roas: null,
    ml_cancel_rate_pct: 1.5,
  },
  previous: { gmv: 800, orders: 32, avg_ticket: 25 },
  gmv_mom_pct: 25,
  date_from: "2026-07-01",
  date_to: "2026-07-31",
  filters: { channels: "all", brands: null },
  refreshed_at: REFRESHED_AT,
};

export const BRANDS = {
  ref_month: "2026-07",
  brands: [
    { brand: "barbours", label: "BARBOURS", total_gmv: 600, orders: 24, mom_pct: 10 },
    // GMV zero REAL: precisa continuar 0, nunca virar null.
    { brand: "kokeshi", label: "KOKESHI", total_gmv: 0, orders: 0, mom_pct: null },
  ],
  refreshed_at: REFRESHED_AT,
};

export const CANAIS = {
  ref_month: "2026-07",
  marketplace: "all",
  kpis: {},
  brands: [],
  channel_rows: [
    {
      brand: "barbours", label: "BARBOURS", channel: "ml", channel_label: "Mercado Livre",
      gmv: 400, orders: 16,
      ad_spend: 40, ad_revenue: 200, ads_gmv_pct: 10, roas: 5, acos_pct: 20,
      // Comissao do ML: aplicavel mas INDISPONIVEL -> null, nunca 0.
      marketplace_cost_pct: null, seller_shipping_pct: 3,
      ads_available: true, marketplace_cost_available: false, seller_shipping_available: true,
      ads_applicable: true, marketplace_cost_applicable: true, seller_shipping_applicable: true,
      data_warning: "Comissao do Mercado Livre nao esta disponivel no mart.",
      signals: ["roas_forte"],
    },
    {
      brand: "barbours", label: "BARBOURS", channel: "tiktok", channel_label: "TikTok Shop",
      gmv: 600, orders: 24,
      // Ads NAO aplicavel ao TikTok: distinto de "sem dado".
      ad_spend: null, ad_revenue: null, ads_gmv_pct: null, roas: null, acos_pct: null,
      marketplace_cost_pct: 8, seller_shipping_pct: null,
      ads_available: false, marketplace_cost_available: true, seller_shipping_available: false,
      ads_applicable: false, marketplace_cost_applicable: true, seller_shipping_applicable: false,
      data_warning: null,
      signals: [],
    },
  ],
  channel_medians: [
    {
      channel: "ml", channel_label: "Mercado Livre",
      // Uma marca so: mediana NAO existe. Precisa continuar null.
      gmv_median: null, ads_gmv_pct_median: null, roas_median: null,
      marketplace_cost_pct_median: null, seller_shipping_pct_median: null,
      brands_with_data: 1,
    },
  ],
  filters: { channels: "all", brands: null },
  refreshed_at: REFRESHED_AT,
};

export const PRODUTOS_ML = {
  total: 1648,
  limit: 2,
  offset: 0,
  scope: "ranking_acumulado_atual",
  items: [
    {
      brand: "barbours", item_id: "MLB1", seller_sku: "SKU-1", title: "Produto A",
      gross_revenue: 5000, units_sold: 100, unique_buyers: 90, avg_price: 50,
      cancel_rate_pct: 2, pareto_bucket: "A_top50", revenue_velocity: "high",
      ad_roas: 12, ad_acos_pct: 8, ad_spend: 400, ad_efficiency: "otima",
      action_signal: "ACAO: aumentar investimento (ROAS > 15x)",
      // Precisa ser DESCARTADO pela tool.
      estimated_margin: 0.42,
      revenue_share_pct: 12.5, product_status: "sells+advertised",
    },
    {
      brand: "kokeshi", item_id: "MLB2", seller_sku: null, title: "Produto B",
      gross_revenue: 0, units_sold: 0, unique_buyers: null, avg_price: null,
      cancel_rate_pct: null, pareto_bucket: "D_tail", revenue_velocity: "zero",
      ad_roas: null, ad_acos_pct: null, ad_spend: null, ad_efficiency: null,
      action_signal: null, estimated_margin: null,
      revenue_share_pct: 0, product_status: "inactive",
    },
  ],
  refreshed_at: REFRESHED_AT,
};

export const QUALITY = {
  ref_month: "2026-07",
  marketplace: "all",
  kpis: {
    // TikTok: 0 = NAO MENSURADO, jamais "0% de cancelamento".
    tiktok_cancel_rate: 0,
    tiktok_problem_rate: null,
    ml_cancel_rate_pct: 1.5,
    shopee_cancel_rate_pct: 2.25,
    shopee_return_rate_pct: 0.75,
  },
  brands: [],
  filters: { channels: "all", brands: null },
  refreshed_at: REFRESHED_AT,
};

export const HEALTH = { active_source: "neon_marts", db_connected: true };

export const REGIOES_SUMMARY = {
  gmv: 700,
  orders: 30,
  units_sold: 25,
  ufs_com_venda: 2,
  uf_known_orders: 30,
  uf_eligible_orders: 40,
  uf_fill_pct: 75,
  shipping_cost_covered_orders: 0,
  shipping_cost_eligible_orders: 0,
  shipping_cost_coverage_pct: null,
  seller_shipping_cost: null,
  coverage_level: "partial",
  coverage_warning: true,
  date_from: "2026-07-01",
  date_to: "2026-07-31",
  filters: { channels: "all", brands: null },
  refreshed_at: REFRESHED_AT,
  // Lido da fonte pela tool — nunca uma lista fixa no nosso codigo.
  channels_sem_cobertura_regional: ["tiktok"],
};

export const REGIOES_BY_UF = {
  data: [
    {
      uf: "SP", gmv: 500, orders: 20, units_sold: 18, canceled_orders: 1, returned_orders: 0,
      seller_shipping_cost: null, uf_known_orders: 20, uf_eligible_orders: 25,
      shipping_cost_covered_orders: 0, shipping_cost_eligible_orders: 0,
      uf_fill_pct: 80, shipping_cost_coverage_pct: null,
      coverage_level: "ok", coverage_warning: false,
    },
    {
      uf: "RJ", gmv: 200, orders: 10, units_sold: 7, canceled_orders: 0, returned_orders: 0,
      seller_shipping_cost: null, uf_known_orders: 10, uf_eligible_orders: 15,
      shipping_cost_covered_orders: 0, shipping_cost_eligible_orders: 0,
      uf_fill_pct: 66.67, shipping_cost_coverage_pct: null,
      coverage_level: "partial", coverage_warning: true,
    },
  ],
  date_from: "2026-07-01",
  date_to: "2026-07-31",
  filters: { channels: "all", brands: null },
  refreshed_at: REFRESHED_AT,
  channels_sem_cobertura_regional: ["tiktok"],
};

export const TREND = {
  granularity: "day",
  data: [
    { date: "2026-07-01", label: "01/07", gmv: 500, orders: 20 },
    { date: "2026-07-02", label: "02/07", gmv: 500, orders: 20 },
  ],
  comparison: null,
  date_from: "2026-07-01",
  date_to: "2026-07-31",
  refreshed_at: REFRESHED_AT,
};

export type CallLog = { url: string }[];

/**
 * `fetch` falso: responde JSON conforme o mapa path -> payload.
 * Path desconhecido devolve 404, o que exercita o caminho de erro.
 */
export function jsonFetch(
  routes: Record<string, unknown>,
  log?: CallLog,
): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    log?.push({ url });
    const path = new URL(url).pathname;
    const payload = routes[path];
    if (payload === undefined) {
      return new Response(JSON.stringify({ detail: "not found" }), {
        status: 404,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

/** Simula a pagina HTML 403 do WAF (que inclui o IP do solicitante). */
export function wafFetch(): typeof fetch {
  return (async () =>
    new Response(
      "<!DOCTYPE html><html><body><h1>403 - Forbidden</h1><p>Your IP address: 203.0.113.7</p></body></html>",
      { status: 403, headers: { "content-type": "text/html; charset=utf-8" } },
    )) as unknown as typeof fetch;
}

/** 200 com corpo HTML — resposta invalida, ainda que "ok". */
export function htmlOkFetch(): typeof fetch {
  return (async () =>
    new Response("<html><body>oops</body></html>", {
      status: 200,
      headers: { "content-type": "text/html" },
    })) as unknown as typeof fetch;
}

/** 200 com JSON malformado. */
export function badJsonFetch(): typeof fetch {
  return (async () =>
    new Response("{ not json", {
      status: 200,
      headers: { "content-type": "application/json" },
    })) as unknown as typeof fetch;
}

/** Aborta como um timeout real (AbortError). */
export function timeoutFetch(): typeof fetch {
  return ((_input: RequestInfo | URL, init?: RequestInit) =>
    new Promise((_resolve, reject) => {
      const signal = init?.signal;
      const fail = () => {
        const e = new Error("aborted");
        e.name = "AbortError";
        reject(e);
      };
      if (signal?.aborted) return fail();
      signal?.addEventListener("abort", fail, { once: true });
    })) as unknown as typeof fetch;
}

/** Falha de rede generica. */
export function networkErrorFetch(): typeof fetch {
  return (async () => {
    throw new TypeError("fetch failed: ECONNREFUSED 10.0.0.5:5432");
  }) as unknown as typeof fetch;
}

// ---------------------------------------------------------------------------
// Produtos com competencia mensal (TikTok / Shopee)
// ---------------------------------------------------------------------------

export const PRODUTOS_TIKTOK = {
  ref_month: "2026-07",
  total: 523,
  limit: 2,
  offset: 0,
  items: [
    {
      brand: "kokeshi", product_id: "TK1", product_name: "Produto TK",
      gmv: 3000, orders: 40, items_sold: 60, avg_price: 50,
      pct_gmv_video: 60, pct_gmv_live: 30, pct_gmv_card: 10,
      problem_rate: null, pareto_bucket: "A_top50",
    },
  ],
};

export const PRODUTOS_SHOPEE = {
  // Fevereiro de ano bissexto: o periodo derivado precisa terminar em 29.
  ref_month: "2024-02",
  total: 472,
  limit: 2,
  offset: 0,
  items: [
    {
      brand: "apice", sku_ref: "SKU-SH-1", product_name: "Produto SH",
      variation_name: null, gmv: 1500, units_sold: 30, orders: 25,
      canceled_orders: 1, cancel_rate_pct: 4, unique_buyers: 22,
      avg_price: 50, pareto_bucket: "B_next30",
    },
  ],
};

// ---------------------------------------------------------------------------
// Cenarios de timeout e teto de bytes
// ---------------------------------------------------------------------------

/**
 * Entrega os HEADERS imediatamente e deixa o CORPO pendurado para sempre.
 * Exercita exatamente o bug de o timer ser limpo quando o fetch resolve.
 */
export function headersThenHangFetch(): typeof fetch {
  return (async () => {
    const stream = new ReadableStream<Uint8Array>({
      pull() {
        // Nunca resolve: o corpo fica pendente.
        return new Promise<void>(() => {});
      },
    });
    return new Response(stream, {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

/**
 * Corpo em streaming, sem `content-length`, com `charCount` caracteres
 * multibyte (3 bytes cada em UTF-8). Prova que o teto e' medido em BYTES.
 */
export function multibyteStreamFetch(charCount: number): typeof fetch {
  return (async () => {
    const payload = '"' + "€".repeat(charCount) + '"';
    const bytes = new TextEncoder().encode(payload);
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes);
        controller.close();
      },
    });
    return new Response(stream, {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

/**
 * Resposta SEM stream (`body: null`), para exercitar o caminho de fallback
 * `res.text()` — onde o teto tambem precisa contar bytes, nao caracteres.
 */
export function noStreamFetch(text: string): typeof fetch {
  return (async () =>
    ({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      body: null,
      text: async () => text,
    }) as unknown as Response) as unknown as typeof fetch;
}

/**
 * Pior caso do cleanup: headers imediatos, `read()` que nunca resolve E
 * `cancel()` que TAMBEM nunca resolve.
 *
 * Se o adapter aguardasse `cancel()` no `finally`, a tool ficaria pendurada
 * mesmo depois de o deadline expirar — anulando o timeout.
 */
export function hangingCancelFetch(): { impl: typeof fetch; cancelCalls: () => number } {
  let cancels = 0;
  const impl = (async () =>
    ({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      body: {
        getReader: () => ({
          read: () => new Promise(() => {}), // nunca resolve
          cancel: () => {
            cancels += 1;
            return new Promise(() => {}); // nunca resolve
          },
        }),
      },
    }) as unknown as Response) as unknown as typeof fetch;

  return { impl, cancelCalls: () => cancels };
}

/**
 * Caso normal: corpo entregue de uma vez, com `cancel()` que resolve.
 * Serve para provar que o cleanup do caminho feliz nao gera rejeicao solta.
 */
export function countingCancelFetch(payload: unknown): {
  impl: typeof fetch;
  cancelCalls: () => number;
} {
  let cancels = 0;
  const impl = (async () => {
    const bytes = new TextEncoder().encode(JSON.stringify(payload));
    let sent = false;
    return {
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      body: {
        getReader: () => ({
          read: async () => {
            if (sent) return { done: true, value: undefined };
            sent = true;
            return { done: false, value: bytes };
          },
          cancel: async () => {
            cancels += 1;
          },
        }),
      },
    } as unknown as Response;
  }) as unknown as typeof fetch;

  return { impl, cancelCalls: () => cancels };
}
