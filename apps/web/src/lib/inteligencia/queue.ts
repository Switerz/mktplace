// Fila priorizada de evidencias da Inteligencia (Gate V3-1A).
//
// Substitui as TRES tabelas ML (`urgent`, `scale`, `organic`) por uma uniao
// discriminada com lentes. O que este modulo NAO faz, de proposito:
//
// - NAO deduplica. Cada linha recebida do payload aparece exatamente uma vez.
//   O grao da tabela-fonte e' `(brand, item_id)`, e o payload NAO entrega
//   `item_id` — so' `brand` e `title`. Logo, duas linhas com o mesmo
//   `(brand, title)` podem ser DOIS produtos distintos, e o frontend nao tem
//   como provar o contrario. Uma versao anterior deste modulo deduplicava por
//   `(brand, title)` "como guarda" e, com isso, apagava registros legitimos:
//   duas linhas de `urgent` com o mesmo titulo e ad spend 100 e 200 viravam
//   uma linha e uma soma de 100 em vez de 300. Perda silenciosa numa cifra
//   monetaria — o oposto de uma guarda;
// - nao altera o grao nem inventa identificador de produto;
// - nao produz total do universo: as contagens sao SEMPRE dos registros
//   retornados, porque as tres listas vem capadas no backend
//   (LIMIT 30/20/20) e `*_total_count` so' existe com BE3;
// - nao cria a faixa `roas_indisponivel_com_investimento`, que depende de BE6.
//
// Modulo puro, sem React.

import type { InteligenciaData, ProductSignalRow } from "../api-client.ts";
import { filterByBrand, type BrandSelection } from "./brands.ts";
import type { Lens } from "./lens.ts";

/** Origem da evidencia — espelha o `product_status` que a formou no SQL. */
export type EvidenceKind = "parar" | "escalar" | "testar";

export interface EvidenceItem extends ProductSignalRow {
  kind: EvidenceKind;
}

/**
 * Limites REAIS das tres listas, como estao no SQL de `get_inteligencia`
 * (`LIMIT 30` / `LIMIT 20` / `LIMIT 20`). Usados apenas para escrever a
 * limitacao com honestidade — nunca para afirmar cobertura.
 */
export const LIST_LIMITS: Record<EvidenceKind, number> = {
  parar: 30,
  escalar: 20,
  testar: 20,
};

/** Regra real que formou cada lista, para o texto do bloco e do dialogo. */
export const KIND_RULES: Record<EvidenceKind, string> = {
  parar: "produtos com ad spend e nenhuma venda (product_status = ad_spend_no_sales)",
  escalar: "produtos com ads ativos e ROAS ≥ 8 (product_status = sells+advertised)",
  testar: "produtos que vendem sem ads (product_status = sells_organic_only)",
};

export const KIND_LABELS: Record<EvidenceKind, string> = {
  parar: "Desperdício de Ads",
  escalar: "Oportunidade de escala",
  testar: "Teste de mídia em orgânico",
};

/** Ordem deterministica das origens, usada tambem no desempate de `todos`. */
export const KIND_ORDER: readonly EvidenceKind[] = ["parar", "escalar", "testar"];

function tag(rows: readonly ProductSignalRow[] | undefined, kind: EvidenceKind): EvidenceItem[] {
  return (rows ?? []).map((r) => ({ ...r, kind }));
}

/**
 * Uniao discriminada das tres listas, ja filtrada pela marca local.
 *
 * Concatenacao pura na ordem de `KIND_ORDER`, com o discriminador `kind` como
 * unica adicao. **Cada linha recebida aparece exatamente uma vez** — nao existe
 * deduplicacao, nem dentro de uma lista, nem entre listas.
 *
 * Isso e' obrigatorio, nao preferencia. Sem `item_id` no payload, duas linhas
 * com o mesmo `(brand, title)` sao indistinguiveis de dois produtos diferentes,
 * e descartar uma delas descartaria dado real. Quando o backend passar a
 * entregar `item_id` (parte do contrato BE6), a identidade podera' ser provada
 * e a questao volta a' mesa — com dado, nao com suposicao.
 */
export function buildQueue(
  data: InteligenciaData | null | undefined,
  selection: BrandSelection,
): EvidenceItem[] {
  if (!data) return [];
  return [
    ...tag(filterByBrand(data.urgent, selection), "parar"),
    ...tag(filterByBrand(data.scale, selection), "escalar"),
    ...tag(filterByBrand(data.organic, selection), "testar"),
  ];
}

/** Linhas de uma lente. `todos` devolve a uniao inteira, linha por linha. */
export function queueForLens(rows: readonly EvidenceItem[], lens: Lens): EvidenceItem[] {
  if (lens === "todos") return [...rows];
  return rows.filter((r) => r.kind === lens);
}

function num(v: number | null | undefined): number {
  // Ordenacao: `null` vai para o fim, e NUNCA e' tratado como zero na
  // exibicao — aqui e' so' posicao de ordenacao.
  return v == null ? Number.NEGATIVE_INFINITY : v;
}

/**
 * Ordenacao padrao por lente — a metrica que define a propria lente:
 * desperdicio pelo gasto, escala pelo retorno, teste pelo faturamento.
 * `todos` usa a ordem das origens e, dentro de cada uma, o gasto/GMV.
 */
export function sortForLens(rows: readonly EvidenceItem[], lens: Lens): EvidenceItem[] {
  const list = [...rows];
  if (lens === "parar") return list.sort((a, b) => num(b.ad_spend) - num(a.ad_spend));
  if (lens === "escalar") return list.sort((a, b) => num(b.ad_roas) - num(a.ad_roas));
  if (lens === "testar") return list.sort((a, b) => num(b.gmv) - num(a.gmv));
  return list.sort((a, b) => {
    const d = KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind);
    if (d !== 0) return d;
    const metric = a.kind === "escalar" ? num(b.ad_roas) - num(a.ad_roas) : num(b.ad_spend) - num(a.ad_spend);
    if (metric !== 0) return metric;
    return num(b.gmv) - num(a.gmv);
  });
}

/** Colunas relevantes por lente — nao repetimos as 9 colunas em todas. */
export const LENS_COLUMNS: Record<Lens, readonly string[]> = {
  parar: ["brand", "title", "ad_spend", "days_advertised", "pareto_bucket", "revenue_velocity"],
  escalar: ["brand", "title", "gmv", "ad_roas", "ad_acos_pct", "revenue_share_pct", "revenue_velocity"],
  testar: ["brand", "title", "gmv", "units_sold", "cancel_rate_pct", "pareto_bucket"],
  todos: ["kind", "brand", "title", "gmv", "ad_spend", "ad_roas", "pareto_bucket"],
};

/**
 * Texto de truncamento HONESTO. Nunca "N produtos no total", nunca
 * "todo o portfolio", nunca "N de N".
 */
export function sampleNote(kind: EvidenceKind, returned: number): string {
  const limit = LIST_LIMITS[kind];
  return returned >= limit
    ? `${returned} registros exibidos · amostra limitada a até ${limit} nesta lista`
    : `${returned} registros exibidos nesta lista`;
}

/**
 * Nota de truncamento das listas compactas do bloco 5.
 *
 * Quando a lista bateu o LIMIT do backend, o texto e' "N de ao menos LIMIT" —
 * "N de LIMIT" sugeriria que LIMIT e' o total, e LIMIT e' so' onde a query
 * parou de contar. Abaixo do LIMIT, o numero recebido E' tudo o que existe
 * naquela lista, e ai' pode ser nomeado.
 */
export function listSampleNote(kind: EvidenceKind, shown: number, received: number): string {
  const limit = LIST_LIMITS[kind];
  return received >= limit
    ? `${shown} de ao menos ${limit} registros nesta lista`
    : `${shown} de ${received} registro${received === 1 ? "" : "s"} recebido${received === 1 ? "" : "s"} nesta lista`;
}

/**
 * Limite do bloco `tk_products` no backend (`LIMIT 25` no SQL). Vive aqui
 * junto dos outros limites para que nenhuma tela invente um teto.
 */
export const TK_PRODUCTS_LIMIT = 25;

/** Nota de amostra do card TikTok — mesma disciplina, teto proprio. */
export function tkSampleNote(shown: number, received: number): string {
  return received >= TK_PRODUCTS_LIMIT
    ? `${shown} de ao menos ${TK_PRODUCTS_LIMIT} produtos nesta janela`
    : `${shown} de ${received} produto${received === 1 ? "" : "s"} recebido${received === 1 ? "" : "s"} nesta janela`;
}

/** Nota de amostra para uma lente (inclui `todos`, que soma as origens). */
export function lensSampleNote(lens: Lens, counts: Record<EvidenceKind, number>): string {
  if (lens !== "todos") return sampleNote(lens, counts[lens]);
  const total = KIND_ORDER.reduce((s, k) => s + counts[k], 0);
  const teto = KIND_ORDER.reduce((s, k) => s + LIST_LIMITS[k], 0);
  return `${total} registros exibidos · amostra limitada a até ${teto} (soma das três listas)`;
}
