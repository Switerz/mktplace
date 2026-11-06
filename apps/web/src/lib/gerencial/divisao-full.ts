/**
 * Receita do periodo entre Full (ML Full + Shopee FBS) e normal.
 *
 * A PROPORCAO vem das fatos de modalidade logistica (/ml-fulfillment e
 * /shopee-fbs); o VALOR, do GMV do overview, para a barra fechar com o total
 * do bloco. TikTok nao tem Full: a venda dele entra em normal.
 */
export interface DivisaoFull {
  full: number;
  normal: number;
}

export function divisaoFull(
  gmv: { total: number | null; ml: number | null; shopee: number | null },
  share: { ml: number | null; shopee: number | null },
): DivisaoFull | null {
  if (!gmv.total) return null;
  let full = 0;
  for (const c of ["ml", "shopee"] as const) {
    const v = gmv[c] ?? 0;
    if (v <= 0) continue;
    const s = share[c];
    // Canal com venda e sem modalidade medida: nao chuta, some a divisao.
    if (s == null) return null;
    full += v * s;
  }
  return full > 0 ? { full, normal: gmv.total - full } : null;
}

/** GMV de uma marca por canal, separado em Full, sem Full e sem dado de Full. */
export interface FullDaMarca {
  full: Partial<Record<"tiktok" | "ml" | "shopee", number>>;
  semFull: Partial<Record<"tiktok" | "ml" | "shopee", number>>;
  /** Canal com venda e sem modalidade medida (ex.: Kokeshi na Shopee). */
  semDado: Partial<Record<"tiktok" | "ml" | "shopee", number>>;
}

/**
 * Mesma regra de `divisaoFull`, por canal de uma marca: a proporcao vem da
 * fato de modalidade, o valor do GMV do card. TikTok nao tem Full; canal sem
 * proporcao medida vai para "sem dado", nunca para "sem Full".
 */
export function fullDaMarca(
  gmv: Partial<Record<"tiktok" | "ml" | "shopee", number>>,
  share: { ml?: number | null; shopee?: number | null },
): FullDaMarca {
  const r: FullDaMarca = { full: {}, semFull: {}, semDado: {} };
  for (const [c, v] of Object.entries(gmv) as ["tiktok" | "ml" | "shopee", number][]) {
    if (!(v > 0)) continue;
    const s = c === "tiktok" ? 0 : share[c];
    if (s == null) r.semDado[c] = v;
    else {
      if (s > 0) r.full[c] = v * s;
      if (s < 1) r.semFull[c] = v - v * s;
    }
  }
  return r;
}
