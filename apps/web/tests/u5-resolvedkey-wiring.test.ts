// Regressao estatica (Gate U5) — mesmo padrao ja usado em
// regioes-financeiro-resolvedkey-wiring.test.ts (Gate U4): confere que os
// caminhos de FALHA de Qualidade, Pedidos, Inteligencia e Operacoes
// concluem `setResolvedKey(key)` antes de `setLoading(false)`. Sem essa
// chamada, `computeRequestStatus` nunca sai de "loading" numa requisicao
// que falhou: `resolvedKey` nunca bate com `requestKey`, entao a falha da
// identidade ATUAL nunca vira "error" de fato — fica presa em "loading"
// para sempre (ver src/lib/request-freshness.ts::computeRequestStatus).
//
// Nao ha harness de componente React neste projeto (node:test puro), por
// isso a regressao e' estatica: extrai cada trecho do codigo-fonte entre
// `setError(...)` e o `setLoading(false);` que o segue, e confere que
// `setResolvedKey(key)` aparece dentro desse trecho — ou seja, no MESMO
// caminho de execucao da falha, nao em outro lugar do arquivo.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

function readSource(...segments: string[]): string {
  return fs.readFileSync(path.join(import.meta.dirname, "..", ...segments), "utf8");
}

/** Cada bloco vai de um `setError(` que REALMENTE marca um erro (nunca o
 * `setError(null)` de reset no inicio do efeito) ate o `setLoading(false);`
 * que o segue (non-greedy) — cobre exatamente o caminho de codigo de UMA
 * falha. */
function extractErrorToLoadingBlocks(src: string): string[] {
  const re = /setError\((?!null\))[^;]*\);([\s\S]*?)setLoading\(false\);/g;
  const blocks: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(src)) !== null) {
    blocks.push(m[0]);
  }
  return blocks;
}

const PAGES_WITH_ONE_FAILURE_BLOCK = [
  ["Qualidade", ["app", "qualidade", "page.tsx"]],
  ["Pedidos", ["app", "pedidos", "page.tsx"]],
  ["Inteligencia", ["app", "inteligencia", "page.tsx"]],
  ["Operacoes", ["app", "operacoes", "page.tsx"]],
] as const;

for (const [label, segments] of PAGES_WITH_ONE_FAILURE_BLOCK) {
  test(`${label}: o caminho de falha (catch) conclui setResolvedKey(key) antes de encerrar o loading`, () => {
    const src = readSource(...segments);
    const blocks = extractErrorToLoadingBlocks(src);
    assert.equal(blocks.length, 1, `esperado exatamente 1 bloco setError(...)...setLoading(false) em ${segments.join("/")}`);
    assert.match(blocks[0], /setResolvedKey\(key\)/, `bloco de falha sem setResolvedKey(key):\n${blocks[0]}`);
  });

  test(`${label}: a guarda 'if (ignore) return' continua presente no caminho de falha`, () => {
    const src = readSource(...segments);
    const blocks = extractErrorToLoadingBlocks(src);
    const idx = src.indexOf(blocks[0]);
    const before = src.slice(Math.max(0, idx - 150), idx);
    assert.match(before, /if\s*\(ignore\)\s*return;/, `setError() sem guarda 'if (ignore) return' logo antes:\n${before}`);
  });

  test(`${label}: o efeito de fetch declara 'let ignore = false' e limpa no cleanup (resposta obsoleta de retry anterior nunca sobrescreve o estado atual)`, () => {
    const src = readSource(...segments);
    assert.match(src, /let ignore = false;/, "efeito sem 'let ignore = false' — retry/troca de filtro nao esta protegido contra resposta obsoleta");
    assert.match(src, /return \(\) => \{ ignore = true; \};/, "efeito sem cleanup 'ignore = true' no unmount/re-run");
  });
}

test("Pedidos: a selecao Shopee-isolada tambem marca setResolvedKey(key) (nunca fica presa em loading ao pular o fetch)", () => {
  const src = readSource("app", "pedidos", "page.tsx");
  const idx = src.indexOf("showShopeeOnly) {");
  assert.notEqual(idx, -1, "branch de showShopeeOnly nao encontrado");
  const block = src.slice(idx, idx + 400);
  assert.match(block, /setResolvedKey\(key\)/, "branch showShopeeOnly nao marca setResolvedKey(key)");
  assert.match(block, /setLoading\(false\);/, "branch showShopeeOnly nao encerra o loading");
});

test("Pedidos: cards/series de canal nao selecionado nunca aparecem (gating por showTiktokSel/showMlSel)", () => {
  const src = readSource("app", "pedidos", "page.tsx");
  assert.match(src, /\{tk &&/, "CanalCard do TikTok deve ser condicionado a variavel derivada da selecao");
  assert.match(src, /\{ml &&/, "CanalCard do ML deve ser condicionado a variavel derivada da selecao");
  assert.match(src, /\{showTiktokSel && <Bar dataKey="tiktok"/, "serie do grafico (TikTok) deve ser condicionada a showTiktokSel");
  assert.match(src, /\{showMlSel && <Bar dataKey="ml"/, "serie do grafico (ML) deve ser condicionada a showMlSel");
});

// CONTRATO INVERTIDO NO GATE V3-1A — leia antes de "corrigir" este teste.
//
// A versao anterior exigia que o filtro local de marca alcancasse SOMENTE
// urgent/scale/organic e "nunca pareto/ltv". Isso era o defeito B9 apontado na
// auditoria do V3-0: `ltv` e `pareto` sao blocos de Mercado Livre, tem coluna
// `brand`, e ficavam de fora do filtro — selecionar uma marca deixava duas
// secoes mostrando o portfolio inteiro, sem dizer isso ao leitor.
//
// O desenho aprovado (docs/INTELIGENCIA_BRAND_V3_PLAN.md §7.4 e §7.7) exige o
// oposto: a selecao de marca vale para TODOS os blocos ML. `tk_products`
// continua de fora, e por um motivo diferente e legitimo — e' TikTok, tem
// janela de 30 dias e regime temporal proprio.
test("Inteligencia: a selecao local de marca alcanca TODOS os blocos ML, e nunca o bloco TikTok", () => {
  const src = readSource("app", "inteligencia", "page.tsx");

  // O filtro e' uma funcao pura compartilhada, nao uma comparacao espalhada.
  assert.match(src, /filterByBrand/, "o filtro vem do modulo puro de marcas");
  assert.doesNotMatch(src, /r\.brand === brandFilter/,
    "comparacao inline substituida por filterByBrand");

  // urgent/scale/organic entram pela fila; pareto e ltv recebem a selecao
  // explicitamente. Os quatro pontos abaixo sao os consumidores do recorte.
  for (const [expr, quem] of [
    ["buildQueue(displayData, brandSel)", "fila (urgent/scale/organic)"],
    ["concentrationByBrand(displayData, brandSel)", "Pareto"],
    ["filterByBrand(displayData?.ltv, brandSel)", "LTV"],
    ["buildPriorities(displayData, brandSel)", "prioridades"],
  ] as const) {
    assert.ok(src.includes(expr), `${quem} precisa receber a selecao de marca: ${expr}`);
  }

  // tk_products NAO recebe a selecao — regime temporal proprio (30 dias).
  assert.match(src, /displayData\?\.tk_products \?\? \[\]\)\.slice\(0, 5\)/,
    "tk_products e' fatiado sem filtro de marca");
  assert.doesNotMatch(src, /filterByBrand\(displayData\?\.tk_products/,
    "o bloco TikTok nao participa do recorte de marca ML");
});

test("Operacoes: filtro local de marca (creatorBrand) so filtra a tabela de criadores, nunca alertas/lives/ml_velocity/tk_daily", () => {
  const src = readSource("app", "operacoes", "page.tsx");
  const dataFilterUses = (src.match(/r\.brand === creatorBrand/g) ?? []).length;
  assert.equal(dataFilterUses, 1, `esperado creatorBrand filtrando dado exatamente 1 vez (filteredCreators), encontrado ${dataFilterUses}`);
});
