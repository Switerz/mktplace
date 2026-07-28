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

test("Inteligencia: filtro local de marca (brandFilter) so filtra as secoes ML (urgent/scale/organic), nunca pareto/ltv/tk_products", () => {
  const src = readSource("app", "inteligencia", "page.tsx");
  // As 3 secoes ML filtradas por dado: filteredUrgent, filteredScale, filteredOrganic.
  const dataFilterUses = (src.match(/r\.brand === brandFilter/g) ?? []).length;
  assert.equal(dataFilterUses, 3, `esperado brandFilter filtrando dado exatamente 3 vezes (urgent/scale/organic), encontrado ${dataFilterUses}`);
  // Pareto/LTV/TikTok products nunca devem referenciar brandFilter.
  const paretoBlock = src.slice(src.indexOf("paretoByBrand: Record"), src.indexOf("Ordenação — LTV"));
  assert.doesNotMatch(paretoBlock, /brandFilter/, "bloco de Pareto nao deve ser afetado pelo filtro local de marca");
});

test("Operacoes: filtro local de marca (creatorBrand) so filtra a tabela de criadores, nunca alertas/lives/ml_velocity/tk_daily", () => {
  const src = readSource("app", "operacoes", "page.tsx");
  const dataFilterUses = (src.match(/r\.brand === creatorBrand/g) ?? []).length;
  assert.equal(dataFilterUses, 1, `esperado creatorBrand filtrando dado exatamente 1 vez (filteredCreators), encontrado ${dataFilterUses}`);
});
