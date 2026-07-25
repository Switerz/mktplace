// Regressao estatica (Gate U4, patch final estreito) — confere que os
// caminhos de FALHA de Regioes e Financeiro concluem `setResolvedKey(key)`
// antes de `setLoading(false)`. Sem essa chamada, `computeRequestStatus`
// nunca sai de "loading" numa requisicao que falhou: `resolvedKey` nunca
// bate com `requestKey`, entao a falha da identidade ATUAL nunca vira
// "error" de fato — fica presa em "loading" para sempre (ver
// src/lib/request-freshness.ts::computeRequestStatus).
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

test("Regioes: os 2 caminhos de falha (summary==null e catch) concluem setResolvedKey(key) antes de encerrar o loading", () => {
  const src = readSource("app", "regioes", "page.tsx");
  const blocks = extractErrorToLoadingBlocks(src);
  assert.equal(blocks.length, 2, "esperado exatamente 2 blocos setError(...)...setLoading(false) em regioes/page.tsx (sm==null e catch)");
  for (const block of blocks) {
    assert.match(block, /setResolvedKey\(key\)/, `bloco de falha sem setResolvedKey(key):\n${block}`);
  }
});

test("Financeiro: o caminho de falha (catch) conclui setResolvedKey(key) antes de encerrar o loading", () => {
  const src = readSource("app", "financeiro", "page.tsx");
  const blocks = extractErrorToLoadingBlocks(src);
  assert.equal(blocks.length, 1, "esperado exatamente 1 bloco setError(...)...setLoading(false) em financeiro/page.tsx (catch)");
  assert.match(blocks[0], /setResolvedKey\(key\)/, `bloco de falha sem setResolvedKey(key):\n${blocks[0]}`);
});

test("Regioes: a guarda 'if (ignore) return' continua presente nos 2 caminhos de falha (resposta antiga nunca altera resolvedKey/error/loading atuais)", () => {
  const src = readSource("app", "regioes", "page.tsx");
  const blocks = extractErrorToLoadingBlocks(src);
  for (const block of blocks) {
    // A guarda fica imediatamente antes do trecho capturado (no corpo do
    // .then/.catch) — verificamos no arquivo inteiro que cada ocorrencia de
    // setError() e' precedida de perto por "if (ignore) return".
    const idx = src.indexOf(block);
    const before = src.slice(Math.max(0, idx - 120), idx);
    assert.match(before, /if\s*\(ignore\)\s*return;/, `setError() sem guarda 'if (ignore) return' logo antes:\n${before}`);
  }
});

test("Financeiro: a guarda 'if (ignore) return' continua presente no caminho de falha", () => {
  const src = readSource("app", "financeiro", "page.tsx");
  const blocks = extractErrorToLoadingBlocks(src);
  const idx = src.indexOf(blocks[0]);
  const before = src.slice(Math.max(0, idx - 120), idx);
  assert.match(before, /if\s*\(ignore\)\s*return;/, `setError() sem guarda 'if (ignore) return' logo antes:\n${before}`);
});
