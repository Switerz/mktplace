// Concentracao Pareto da Inteligencia (Gate V3-1A, bloco 4).
//
// O payload `pareto` entrega SOMENTE agregados por `(brand, pareto_bucket)`:
// `n_products`, `gmv` e `ad_spend`. Ele NAO contem os produtos do bucket, e
// `urgent`/`scale`/`organic` nao recompoem o bucket (sao capadas e filtradas
// por `product_status`). Por isso o dialogo deste bloco nao lista produtos —
// ele explica o agregado e manda para `/produtos`, onde a consulta existe.
//
// Modulo puro, sem React.

import type { InteligenciaData, ParetoRow } from "../api-client.ts";
import { filterByBrand, type BrandSelection } from "./brands.ts";

/** Buckets allowlisted — mesmos valores que a API valida em VALID_PARETO_BUCKETS. */
export const PARETO_BUCKETS = ["A_top50", "B_next30", "C_next15", "D_tail"] as const;
export type ParetoBucket = (typeof PARETO_BUCKETS)[number];

export function isParetoBucket(v: unknown): v is ParetoBucket {
  return typeof v === "string" && (PARETO_BUCKETS as readonly string[]).includes(v);
}

/** Rotulo curto do bucket (a letra), para a barra e a legenda. */
export const BUCKET_LETTER: Record<ParetoBucket, string> = {
  A_top50: "A",
  B_next30: "B",
  C_next15: "C",
  D_tail: "D",
};

/** Rotulo humano completo, para nome acessivel e dialogo. */
export const BUCKET_LABEL: Record<ParetoBucket, string> = {
  A_top50: "A — top 50% do GMV",
  B_next30: "B — próximos 30%",
  C_next15: "C — próximos 15%",
  D_tail: "D — cauda",
};

export interface BucketShare {
  bucket: ParetoBucket;
  n_products: number;
  gmv: number;
  ad_spend: number;
  /** Participacao do bucket no GMV da marca. `null` quando o total e' zero —
   * divisao indefinida NAO vira 0%. */
  sharePct: number | null;
}

export interface BrandConcentration {
  brand: string;
  buckets: BucketShare[];
  totalGmv: number;
  totalProducts: number;
}

/**
 * Agrupa o payload `pareto` por marca, na ordem dos buckets, respeitando a
 * selecao local de marca. Marca sem nenhuma linha simplesmente nao aparece.
 *
 * Ordena as marcas por GMV total desc — a leitura util e' "quem concentra
 * mais primeiro".
 */
export function concentrationByBrand(
  data: InteligenciaData | null | undefined,
  selection: BrandSelection,
): BrandConcentration[] {
  const rows = filterByBrand<ParetoRow>(data?.pareto, selection);
  const byBrand = new Map<string, ParetoRow[]>();
  for (const r of rows) {
    if (!isParetoBucket(r.pareto_bucket)) continue;
    const list = byBrand.get(r.brand) ?? [];
    list.push(r);
    byBrand.set(r.brand, list);
  }
  const out: BrandConcentration[] = [];
  for (const [brand, list] of byBrand) {
    const totalGmv = list.reduce((s, r) => s + (r.gmv ?? 0), 0);
    const totalProducts = list.reduce((s, r) => s + (r.n_products ?? 0), 0);
    const buckets: BucketShare[] = [];
    for (const bucket of PARETO_BUCKETS) {
      const row = list.find((r) => r.pareto_bucket === bucket);
      if (!row) continue;
      buckets.push({
        bucket,
        n_products: row.n_products,
        gmv: row.gmv,
        ad_spend: row.ad_spend,
        sharePct: totalGmv > 0 ? (row.gmv / totalGmv) * 100 : null,
      });
    }
    out.push({ brand, buckets, totalGmv, totalProducts });
  }
  return out.sort((a, b) => b.totalGmv - a.totalGmv);
}

/**
 * Href canonico do CTA do bucket para a pagina Produtos (§9.2 do plano).
 *
 * A pagina usa `brands` no PLURAL (convencao de FILTER_QUERY_KEYS); o
 * endpoint continua recebendo `brand` no singular, e a traducao acontece
 * dentro de `/produtos` ao montar o request. Nenhuma metrica viaja: apenas
 * canal, marca e bucket, todos allowlisted.
 */
export function buildParetoProdutosHref(brand: string, bucket: ParetoBucket): string {
  const qs = new URLSearchParams();
  qs.set("channels", "ml");
  qs.set("brands", brand);
  qs.set("pareto_bucket", bucket);
  return `/produtos?${qs.toString()}`;
}
