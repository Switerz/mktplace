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
