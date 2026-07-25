// Transformacao pura de linhas de Produtos (ML/TikTok/Shopee) para o
// formato exportavel em CSV (Gate U4, docs/UI_REVAMP_PLAN.md Task 2;
// substituido de XLSX para CSV na rodada de correcao consolidada —
// `xlsx@0.18.5` tinha vulnerabilidades de alta severidade sem correcao
// disponivel, ver FINDING 4). Contrato: exporta SOMENTE campos ja exibidos
// nas 3 tabelas (MercadoLivreProductTable/TikTokProductTable/
// ShopeeProductTable) — nunca `estimated_margin`/`product_status`/
// `item_id`/`unique_buyers` (Shopee) ou qualquer outro campo interno nao
// apresentado na coluna correspondente. `null` e sempre preservado como
// celula vazia (nunca 0).
import type { ProdutoMLRow, ProdutoShopeeRow, ProdutoTikTokRow } from "./api-client";

export type ExportRecord = Record<string, string | number | null>;

export const ML_EXPORT_COLUMNS = [
  "Produto", "SKU", "Marca", "Receita", "Participação no Total (%)", "Preço Médio",
  "Unidades Vendidas", "Compradores Únicos", "Bucket Pareto", "Velocidade de Receita",
  "ROAS Ads", "ACOS Ads (%)", "Ad Spend", "Eficiência Ads", "Sinal de Ação", "Cancelamento (%)",
];

export function mlRowToExportRecord(row: ProdutoMLRow): ExportRecord {
  return {
    "Produto": row.title,
    "SKU": row.seller_sku,
    "Marca": row.brand,
    "Receita": row.gross_revenue,
    "Participação no Total (%)": row.revenue_share_pct,
    "Preço Médio": row.avg_price,
    "Unidades Vendidas": row.units_sold,
    "Compradores Únicos": row.unique_buyers,
    "Bucket Pareto": row.pareto_bucket,
    "Velocidade de Receita": row.revenue_velocity,
    "ROAS Ads": row.ad_roas,
    "ACOS Ads (%)": row.ad_acos_pct,
    "Ad Spend": row.ad_spend,
    "Eficiência Ads": row.ad_efficiency,
    "Sinal de Ação": row.action_signal,
    "Cancelamento (%)": row.cancel_rate_pct,
  };
}

export const TIKTOK_EXPORT_COLUMNS = [
  "Produto", "ID do Produto", "Marca", "GMV", "Preço Médio", "Pedidos", "Unidades Vendidas",
  "Bucket Pareto", "GMV Vídeo (%)", "GMV Live (%)", "GMV Card (%)", "Taxa de Problemas (%)",
  "Avaliação Média", "Total de Avaliações",
];

export function tiktokRowToExportRecord(row: ProdutoTikTokRow): ExportRecord {
  return {
    "Produto": row.product_name,
    "ID do Produto": row.product_id,
    "Marca": row.brand,
    "GMV": row.gmv,
    "Preço Médio": row.avg_price,
    "Pedidos": row.orders,
    "Unidades Vendidas": row.items_sold,
    "Bucket Pareto": row.pareto_bucket,
    "GMV Vídeo (%)": row.pct_gmv_video,
    "GMV Live (%)": row.pct_gmv_live,
    "GMV Card (%)": row.pct_gmv_card,
    "Taxa de Problemas (%)": row.problem_rate,
    "Avaliação Média": row.rating_avg,
    "Total de Avaliações": row.total_ratings,
  };
}

export const SHOPEE_EXPORT_COLUMNS = [
  "Produto", "Variação", "SKU", "Marca", "GMV", "Bucket Pareto", "Unidades Vendidas",
  "Pedidos", "Cancelamento (%)", "Ticket Médio",
];

export function shopeeRowToExportRecord(row: ProdutoShopeeRow): ExportRecord {
  return {
    "Produto": row.product_name,
    "Variação": row.variation_name,
    "SKU": row.sku_ref,
    "Marca": row.brand,
    "GMV": row.gmv,
    "Bucket Pareto": row.pareto_bucket,
    "Unidades Vendidas": row.units_sold,
    "Pedidos": row.orders,
    "Cancelamento (%)": row.cancel_rate_pct,
    "Ticket Médio": row.avg_price,
  };
}

export interface ProdutosExportScope {
  channel: "ml" | "tiktok" | "shopee";
  /** Slug da marca ("" = todas as marcas selecionadas). */
  brand: string;
  /** "YYYY-MM" para TikTok/Shopee; `null` para ML (ranking acumulado, sem mes). */
  period: string | null;
  /** Pagina exibida no momento do clique, 1-based (offset/limit + 1). */
  pageNumber: number;
}

function sanitizeForFilename(value: string): string {
  const cleaned = value
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return cleaned || "todas-marcas";
}

/**
 * Nome de arquivo deterministico e sanitizado — sempre contem canal e
 * escopo (marca, periodo quando aplicavel, pagina), nunca depende de
 * `Date.now()`/timestamp de geracao (dois cliques com o mesmo escopo geram
 * o mesmo nome, por design).
 */
export function buildProdutosExportFilename(scope: ProdutosExportScope): string {
  const parts = ["produtos", scope.channel, sanitizeForFilename(scope.brand)];
  if (scope.period) parts.push(scope.period);
  parts.push(`pagina-${scope.pageNumber}`);
  return `${parts.join("_")}.csv`;
}

const CSV_SEPARATOR = ";";
// Excel/Sheets tratam uma celula de TEXTO iniciada por um destes caracteres
// como possivel formula (CSV/formula injection) — nunca aplicado a valores
// numericos (um GMV negativo legitimo nao e uma string arbitraria).
const FORMULA_TRIGGER_CHARS = ["=", "+", "-", "@"];

function sanitizeFormulaInjection(value: string): string {
  return FORMULA_TRIGGER_CHARS.includes(value.charAt(0)) ? `'${value}` : value;
}

/** Escapa uma celula CSV: aspas duplicadas e o campo inteiro entre aspas
 * quando contiver o separador, aspas ou quebra de linha (CRLF/LF/CR). */
function escapeCsvCell(raw: string): string {
  const needsQuoting = raw.includes(CSV_SEPARATOR) || raw.includes("\"") || raw.includes("\n") || raw.includes("\r");
  const escaped = raw.replace(/"/g, "\"\"");
  return needsQuoting ? `"${escaped}"` : escaped;
}

/** `null`/`undefined` viram celula vazia (nunca "0"); numeros nunca passam
 * pela guarda de formula (so texto pode comecar com =/+/-/@ de forma
 * ambigua para o Excel). */
function formatCsvCell(value: string | number | null | undefined): string {
  if (value == null) return "";
  const raw = typeof value === "number" ? String(value) : sanitizeFormulaInjection(value);
  return escapeCsvCell(raw);
}

/**
 * Monta o conteudo CSV (sem BOM) a partir de uma lista fixa de colunas e das
 * linhas ja transformadas em `ExportRecord` — a ordem de `columns` define a
 * ordem exata das colunas exportadas, igual a ordem das tabelas na tela.
 * Separador `;` (Excel/Sheets pt-BR); quebra de linha CRLF.
 */
export function buildProdutosCsv(columns: string[], rows: ExportRecord[]): string {
  const lines = [columns.map(escapeCsvCell).join(CSV_SEPARATOR)];
  for (const row of rows) {
    lines.push(columns.map((col) => formatCsvCell(row[col])).join(CSV_SEPARATOR));
  }
  return lines.join("\r\n");
}

const UTF8_BOM = "﻿";

/** Conteudo final do arquivo, com BOM UTF-8 (necessario para o Excel pt-BR
 * reconhecer acentuacao/til corretamente ao abrir um CSV). */
export function buildProdutosCsvFile(columns: string[], rows: ExportRecord[]): string {
  return UTF8_BOM + buildProdutosCsv(columns, rows);
}
