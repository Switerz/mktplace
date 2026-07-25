// Testes da exportacao CSV de Produtos (Gate U4, docs/UI_REVAMP_PLAN.md
// Task 2/6 — substituido de XLSX para CSV na rodada de correcao consolidada,
// FINDING 4: xlsx@0.18.5 tinha vulnerabilidades de alta severidade sem
// correcao disponivel). Cobre o contrato: somente colunas ja exibidas nas
// tabelas de cada canal, null nunca vira zero, nome de arquivo
// deterministico e sanitizado terminado em .csv, e o contrato de escape/
// seguranca do CSV (separador ;, aspas, quebra de linha, formula injection,
// BOM UTF-8).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  mlRowToExportRecord, tiktokRowToExportRecord, shopeeRowToExportRecord, buildProdutosExportFilename,
  buildProdutosCsv, buildProdutosCsvFile, ML_EXPORT_COLUMNS, TIKTOK_EXPORT_COLUMNS, SHOPEE_EXPORT_COLUMNS,
} from "../src/lib/produtos-export.ts";
import type { ProdutoMLRow, ProdutoShopeeRow, ProdutoTikTokRow } from "../src/lib/api-client.ts";

const ML_ROW: ProdutoMLRow = {
  brand: "barbours", item_id: "MLB123", seller_sku: "SKU-1", title: "Perfume X",
  gross_revenue: 1000, units_sold: 10, unique_buyers: 8, avg_price: 100,
  cancel_rate_pct: 2.5, pareto_bucket: "A_top50", revenue_velocity: "high",
  ad_roas: 4.2, ad_acos_pct: 23.8, ad_spend: 238, ad_efficiency: "star",
  action_signal: "ACAO: aumentar investimento (ROAS > 15x)",
  estimated_margin: 999, revenue_share_pct: 12.3, product_status: "sells+advertised",
};

test("ML: exporta somente colunas visiveis na tabela — nunca estimated_margin/product_status/item_id", () => {
  const record = mlRowToExportRecord(ML_ROW);
  const keys = Object.values(record);
  assert.ok(!keys.includes(999), "estimated_margin (999) nao deve aparecer em nenhum valor exportado");
  assert.ok(!("estimated_margin" in record));
  assert.ok(!("product_status" in record));
  assert.ok(!("item_id" in record));
  assert.equal(record["Produto"], "Perfume X");
  assert.equal(record["Receita"], 1000);
  assert.equal(record["Cancelamento (%)"], 2.5);
});

test("ML: null preservado sem virar zero", () => {
  const row: ProdutoMLRow = { ...ML_ROW, ad_roas: null, ad_acos_pct: null, cancel_rate_pct: null, unique_buyers: null };
  const record = mlRowToExportRecord(row);
  assert.equal(record["ROAS Ads"], null);
  assert.notEqual(record["ROAS Ads"], 0);
  assert.equal(record["Cancelamento (%)"], null);
  assert.equal(record["Compradores Únicos"], null);
});

const TIKTOK_ROW: ProdutoTikTokRow = {
  brand: "kokeshi", product_id: "TT456", product_name: "Kit Skincare", gmv: 5000, orders: 40,
  items_sold: 45, avg_price: 111.1, pct_gmv_video: 60, pct_gmv_live: 30, pct_gmv_card: 10,
  problem_rate: 1.2, rating_avg: 4.8, total_ratings: 120, pareto_bucket: "B_next30",
};

test("TikTok: exporta somente colunas visiveis, incluindo ID do produto (exibido como subtexto na tabela)", () => {
  const record = tiktokRowToExportRecord(TIKTOK_ROW);
  assert.equal(record["Produto"], "Kit Skincare");
  assert.equal(record["ID do Produto"], "TT456");
  assert.equal(record["GMV"], 5000);
  assert.equal(record["GMV Vídeo (%)"], 60);
});

test("TikTok: null preservado sem virar zero (rating/problem_rate ausentes)", () => {
  const row: ProdutoTikTokRow = { ...TIKTOK_ROW, rating_avg: null, total_ratings: null, problem_rate: null };
  const record = tiktokRowToExportRecord(row);
  assert.equal(record["Avaliação Média"], null);
  assert.equal(record["Taxa de Problemas (%)"], null);
});

const SHOPEE_ROW: ProdutoShopeeRow = {
  brand: "apice", sku_ref: "20587", product_name: "Leave-in Antifrizz", variation_name: "300ml",
  gmv: 800, units_sold: 8, orders: 8, canceled_orders: 1, cancel_rate_pct: 11.1,
  unique_buyers: 7, avg_price: 100, pareto_bucket: "C_next15",
};

test("Shopee: exporta somente colunas visiveis — nunca canceled_orders/unique_buyers (nao exibidos na tabela)", () => {
  const record = shopeeRowToExportRecord(SHOPEE_ROW);
  assert.ok(!("canceled_orders" in record));
  assert.ok(!("unique_buyers" in record));
  assert.equal(record["Variação"], "300ml");
  assert.equal(record["SKU"], "20587");
  assert.equal(record["Ticket Médio"], 100);
});

test("Shopee: null preservado sem virar zero (variacao/sku ausentes)", () => {
  const row: ProdutoShopeeRow = { ...SHOPEE_ROW, variation_name: null, sku_ref: null, avg_price: null };
  const record = shopeeRowToExportRecord(row);
  assert.equal(record["Variação"], null);
  assert.equal(record["SKU"], null);
  assert.equal(record["Ticket Médio"], null);
});

test("filename: deterministico e sanitizado, contem canal e escopo (marca+periodo+pagina), termina em .csv", () => {
  const name = buildProdutosExportFilename({ channel: "tiktok", brand: "barbours", period: "2026-07", pageNumber: 2 });
  assert.equal(name, "produtos_tiktok_barbours_2026-07_pagina-2.csv");
});

test("filename: 'todas as marcas' (brand vazio) e mes ausente (ML) ficam explicitos e sanitizados", () => {
  const name = buildProdutosExportFilename({ channel: "ml", brand: "", period: null, pageNumber: 1 });
  assert.equal(name, "produtos_ml_todas-marcas_pagina-1.csv");
});

test("filename: mesmo escopo gera sempre o mesmo nome (deterministico, sem timestamp)", () => {
  const scope = { channel: "shopee" as const, brand: "kokeshi", period: "2026-06", pageNumber: 3 };
  assert.equal(buildProdutosExportFilename(scope), buildProdutosExportFilename(scope));
});

// ── Contrato do CSV (FINDING 4) ──────────────────────────────────────────

test("CSV: colunas exatas por canal, na ordem das tabelas — ML", () => {
  const csv = buildProdutosCsv(ML_EXPORT_COLUMNS, [mlRowToExportRecord(ML_ROW)]);
  const [header] = csv.split("\r\n");
  assert.equal(header, ML_EXPORT_COLUMNS.join(";"));
});

test("CSV: colunas exatas por canal, na ordem das tabelas — TikTok", () => {
  const csv = buildProdutosCsv(TIKTOK_EXPORT_COLUMNS, [tiktokRowToExportRecord(TIKTOK_ROW)]);
  const [header] = csv.split("\r\n");
  assert.equal(header, TIKTOK_EXPORT_COLUMNS.join(";"));
});

test("CSV: colunas exatas por canal, na ordem das tabelas — Shopee", () => {
  const csv = buildProdutosCsv(SHOPEE_EXPORT_COLUMNS, [shopeeRowToExportRecord(SHOPEE_ROW)]);
  const [header] = csv.split("\r\n");
  assert.equal(header, SHOPEE_EXPORT_COLUMNS.join(";"));
});

test("CSV: somente as linhas recebidas sao exportadas, na ordem recebida (nunca busca outras paginas)", () => {
  const rowA: ProdutoMLRow = { ...ML_ROW, title: "Produto A" };
  const rowB: ProdutoMLRow = { ...ML_ROW, title: "Produto B" };
  const csv = buildProdutosCsv(ML_EXPORT_COLUMNS, [rowA, rowB].map(mlRowToExportRecord));
  const lines = csv.split("\r\n");
  assert.equal(lines.length, 3); // header + 2 linhas, nunca mais
  assert.ok(lines[1].startsWith("Produto A;"));
  assert.ok(lines[2].startsWith("Produto B;"));
});

test("CSV: null vira celula vazia, nunca '0'", () => {
  const row: ProdutoMLRow = { ...ML_ROW, ad_roas: null, unique_buyers: null };
  const csv = buildProdutosCsv(ML_EXPORT_COLUMNS, [mlRowToExportRecord(row)]);
  const dataLine = csv.split("\r\n")[1];
  const cells = dataLine.split(";");
  const roasIdx = ML_EXPORT_COLUMNS.indexOf("ROAS Ads");
  assert.equal(cells[roasIdx], "");
  assert.notEqual(cells[roasIdx], "0");
});

test("CSV: escapa separador ';' envolvendo a celula em aspas", () => {
  const row: ProdutoMLRow = { ...ML_ROW, title: "Perfume; Edição Especial" };
  const csv = buildProdutosCsv(ML_EXPORT_COLUMNS, [mlRowToExportRecord(row)]);
  const dataLine = csv.split("\r\n")[1];
  assert.ok(dataLine.startsWith("\"Perfume; Edição Especial\";"));
});

test("CSV: escapa aspas duplicando-as dentro do campo entre aspas", () => {
  const row: ProdutoMLRow = { ...ML_ROW, title: 'Produto "Premium"' };
  const csv = buildProdutosCsv(ML_EXPORT_COLUMNS, [mlRowToExportRecord(row)]);
  const dataLine = csv.split("\r\n")[1];
  assert.ok(dataLine.startsWith('"Produto ""Premium""";'));
});

test("CSV: escapa quebra de linha (LF) envolvendo a celula em aspas", () => {
  const row: ProdutoMLRow = { ...ML_ROW, title: "Linha 1\nLinha 2" };
  const csv = buildProdutosCsv(ML_EXPORT_COLUMNS, [mlRowToExportRecord(row)]);
  // A quebra de linha do CAMPO fica dentro das aspas; a separacao real entre
  // linhas do arquivo continua sendo o CRLF entre registros.
  assert.ok(csv.includes('"Linha 1\nLinha 2"'));
});

test("CSV: protege contra formula injection em texto iniciado por =, +, - ou @", () => {
  const row: ProdutoMLRow = { ...ML_ROW, title: "=SOMA(A1:A9)", seller_sku: "+1", ad_efficiency: "-star", action_signal: "@mention" };
  const record = mlRowToExportRecord(row);
  const csv = buildProdutosCsv(ML_EXPORT_COLUMNS, [record]);
  const dataLine = csv.split("\r\n")[1];
  const cells = dataLine.split(";");
  assert.equal(cells[ML_EXPORT_COLUMNS.indexOf("Produto")], "'=SOMA(A1:A9)");
  assert.equal(cells[ML_EXPORT_COLUMNS.indexOf("SKU")], "'+1");
  assert.equal(cells[ML_EXPORT_COLUMNS.indexOf("Eficiência Ads")], "'-star");
  assert.equal(cells[ML_EXPORT_COLUMNS.indexOf("Sinal de Ação")], "'@mention");
});

test("CSV: numeros negativos legitimos NAO recebem prefixo de escape (guarda so se aplica a texto)", () => {
  const row: ProdutoMLRow = { ...ML_ROW, gross_revenue: -50 };
  const csv = buildProdutosCsv(ML_EXPORT_COLUMNS, [mlRowToExportRecord(row)]);
  const dataLine = csv.split("\r\n")[1];
  const cells = dataLine.split(";");
  assert.equal(cells[ML_EXPORT_COLUMNS.indexOf("Receita")], "-50");
});

test("CSV: arquivo final comeca com o BOM UTF-8 (U+FEFF)", () => {
  const file = buildProdutosCsvFile(ML_EXPORT_COLUMNS, [mlRowToExportRecord(ML_ROW)]);
  assert.equal(file.codePointAt(0), 0xfeff);
  assert.ok(file.slice(1).startsWith(ML_EXPORT_COLUMNS.join(";")));
});

test("ausencia de import/uso funcional de 'xlsx' no modulo de exportacao e no botao (comentarios historicos documentando a remocao sao permitidos)", () => {
  const exportLibPath = path.join(import.meta.dirname, "..", "src", "lib", "produtos-export.ts");
  const buttonPath = path.join(import.meta.dirname, "..", "src", "components", "ProductExportButton.tsx");
  const exportLibSrc = fs.readFileSync(exportLibPath, "utf8");
  const buttonSrc = fs.readFileSync(buttonPath, "utf8");
  const FUNCTIONAL_XLSX_USE = /from\s+["']xlsx["']|require\(["']xlsx["']\)|import\(["']xlsx["']\)/;
  assert.ok(!FUNCTIONAL_XLSX_USE.test(exportLibSrc), "produtos-export.ts nao deve importar xlsx");
  assert.ok(!FUNCTIONAL_XLSX_USE.test(buttonSrc), "ProductExportButton.tsx nao deve importar xlsx (estatico ou dinamico)");
  assert.ok(!buttonSrc.includes("XLSX."), "ProductExportButton.tsx nao deve chamar a API da lib XLSX");
});
