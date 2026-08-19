// Regressao estatica (Gate U5, rodada de correcao — Finding 2). Antes desta
// correcao, a selecao Shopee-isolada em Pedidos (`showShopeeOnly`) nunca
// chamava `fetchPedidos` (correto), mas `dataIsFresh` ficava `true` (a
// identidade da requisicao foi marcada como resolvida, sem erro/loading) —
// isso fazia o cabecalho renderizar `<LiveStatusBadge live={false}>` ("Sem
// dados · API offline") e o `aria-live` anunciar "Dados de pedidos
// carregados.", confundindo "fonte sem cobertura Shopee" com "API offline"/
// "sucesso". Os testes abaixo confirmam, estaticamente (sem harness de
// componente React), que essa confusao foi eliminada.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

// Normalizacao para LF, UMA vez. O repositorio esta com `core.autocrlf=true` e
// sem `.gitattributes`, entao no Windows o arquivo chega em CRLF — e os
// marcadores multilinha abaixo (`"useEffect(() => {\n    // Ignora..."`) nunca
// casavam, deixando este teste vermelho por motivo de plataforma, nao de
// semantica. `app/pedidos/page.tsx` NAO foi alterado.
const SRC = fs
  .readFileSync(path.join(import.meta.dirname, "..", "app", "pedidos", "page.tsx"), "utf8")
  .replace(/\r\n/g, "\n");

function sliceBetween(src: string, startMarker: string, endMarker: string): string {
  const start = src.indexOf(startMarker);
  assert.notEqual(start, -1, `marcador de inicio nao encontrado: ${startMarker}`);
  const end = src.indexOf(endMarker, start);
  assert.notEqual(end, -1, `marcador de fim nao encontrado: ${endMarker}`);
  return src.slice(start, end);
}

test("cabecalho: showShopeeOnly e' verificado ANTES de renderizar o LiveStatusBadge (nunca 'API offline' para cobertura ausente)", () => {
  const header = sliceBetween(SRC, "        status={", "        filters={");
  const shopeeOnlyIdx = header.indexOf("showShopeeOnly");
  const liveBadgeIdx = header.indexOf("<LiveStatusBadge");
  assert.notEqual(shopeeOnlyIdx, -1, "cabecalho deve checar showShopeeOnly");
  assert.notEqual(liveBadgeIdx, -1, "cabecalho deve continuar usando LiveStatusBadge para os demais casos");
  assert.ok(shopeeOnlyIdx < liveBadgeIdx, "a checagem de showShopeeOnly deve vir ANTES do LiveStatusBadge no ternario (senao ele so seria alcancado quando showShopeeOnly for falso)");
});

test("cabecalho: Shopee isolada mostra indicador neutro, nunca o LiveStatusBadge com estado offline", () => {
  const header = sliceBetween(SRC, "        status={", "        filters={");
  const branchStart = header.indexOf("dataIsFresh && showShopeeOnly");
  assert.notEqual(branchStart, -1, "deve existir um branch dedicado para dataIsFresh && showShopeeOnly");
  const branchEnd = header.indexOf(") : dataIsFresh ? (", branchStart);
  assert.notEqual(branchEnd, -1, "branch de showShopeeOnly deve ser seguido do branch geral dataIsFresh");
  const shopeeOnlyBranch = header.slice(branchStart, branchEnd);
  assert.doesNotMatch(shopeeOnlyBranch, /<LiveStatusBadge/, "branch de Shopee isolada nao deve renderizar LiveStatusBadge");
  assert.doesNotMatch(shopeeOnlyBranch, /offline/i, "branch de Shopee isolada nao deve mencionar 'offline' — nao e' uma falha de API");
  assert.match(shopeeOnlyBranch, /sem cobertura/i, "branch de Shopee isolada deve comunicar falta de cobertura, nao falha de rede");
});

test("aria-live: Shopee isolada anuncia falta de cobertura, nunca 'dados carregados' nem estado de API offline", () => {
  const liveRegion = sliceBetween(SRC, '<span className="sr-only" aria-live="polite" aria-atomic="true">', "</span>");
  assert.match(liveRegion, /showShopeeOnly/, "aria-live deve checar showShopeeOnly explicitamente");
  const shopeeOnlyIdx = liveRegion.indexOf("showShopeeOnly");
  const shopeeOnlyMsgIdx = liveRegion.indexOf('"', liveRegion.indexOf("?", shopeeOnlyIdx));
  const shopeeOnlyMsgEnd = liveRegion.indexOf('"', shopeeOnlyMsgIdx + 1);
  const shopeeOnlyMessage = liveRegion.slice(shopeeOnlyMsgIdx, shopeeOnlyMsgEnd + 1);
  assert.match(shopeeOnlyMessage, /cobertura Shopee/i, `mensagem do branch showShopeeOnly deveria falar de cobertura Shopee, obteve: ${shopeeOnlyMessage}`);
  assert.doesNotMatch(shopeeOnlyMessage, /carregados/i, "mensagem do branch showShopeeOnly nao deve afirmar sucesso de carregamento");
  assert.doesNotMatch(shopeeOnlyMessage, /offline/i, "mensagem do branch showShopeeOnly nao deve mencionar API offline");
});

test("Shopee isolada continua sem chamar fetchPedidos (branch de skip permanece antes do fetch real)", () => {
  const effectBody = sliceBetween(SRC, "useEffect(() => {\n    // Ignora a resposta", "}, [filters.channels, filters.brands, filters.dateFrom, filters.dateTo, retryKey, showShopeeOnly]);");
  const skipBranchIdx = effectBody.indexOf("if (showShopeeOnly) {");
  const fetchCallIdx = effectBody.indexOf("fetchPedidos(");
  assert.notEqual(skipBranchIdx, -1, "efeito deve conter o branch de skip 'if (showShopeeOnly)'");
  assert.notEqual(fetchCallIdx, -1, "efeito deve conter a chamada real a fetchPedidos para os demais casos");
  assert.ok(skipBranchIdx < fetchCallIdx, "o branch de skip de Shopee isolada deve vir antes (e sair via return) da chamada real a fetchPedidos");
  const skipBranch = effectBody.slice(skipBranchIdx, fetchCallIdx);
  assert.doesNotMatch(skipBranch, /fetchPedidos\(/, "o branch de Shopee isolada nao deve chamar fetchPedidos");
  assert.match(skipBranch, /return \(\) => \{ ignore = true; \};/, "o branch de Shopee isolada deve retornar (encerrar o efeito) antes de chegar na chamada real de fetch");
});

test("selecao mista (Shopee + TikTok/ML) preserva o comportamento existente: LiveStatusBadge e aria-live de sucesso/loading normais", () => {
  const header = sliceBetween(SRC, "        status={", "        filters={");
  // O branch dedicado a Shopee isolada usa `dataIsFresh && showShopeeOnly`
  // — quando a selecao e mista, `showShopeeOnly` e' falso e o fluxo cai no
  // branch geral (`dataIsFresh ? <LiveStatusBadge ... /> : ...`), que segue
  // inalterado.
  assert.match(header, /dataIsFresh && showShopeeOnly \? \(/);
  assert.match(header, /\) : dataIsFresh \? \(\s*<LiveStatusBadge live=\{displayIsLive\} offlineLabel="Sem dados · API offline" \/>/);

  const liveRegion = sliceBetween(SRC, '<span className="sr-only" aria-live="polite" aria-atomic="true">', "</span>");
  assert.match(liveRegion, /"Dados de pedidos carregados\."/, "selecao mista com sucesso deve continuar anunciando 'Dados de pedidos carregados.'");

  // O aviso de cobertura mista (Shopee + outro canal) continua existindo e
  // separado do aviso de Shopee isolada.
  assert.match(SRC, /showShopeeMixed/, "aviso de selecao mista (showShopeeMixed) deve continuar presente");
});
