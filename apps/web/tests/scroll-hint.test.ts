import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { computeScrollEdges } from "../src/lib/scroll-hint.ts";

test("computeScrollEdges: sem overflow (scrollWidth <= clientWidth) nunca mostra sombra", () => {
  const edges = computeScrollEdges({ scrollLeft: 0, scrollWidth: 300, clientWidth: 340 });
  assert.equal(edges.isScrollable, false);
  assert.equal(edges.canScrollLeft, false);
  assert.equal(edges.canScrollRight, false);
});

test("computeScrollEdges: no inicio do scroll (scrollLeft=0) so mostra sombra a direita", () => {
  const edges = computeScrollEdges({ scrollLeft: 0, scrollWidth: 1000, clientWidth: 340 });
  assert.equal(edges.isScrollable, true);
  assert.equal(edges.canScrollLeft, false);
  assert.equal(edges.canScrollRight, true);
});

test("computeScrollEdges: no meio do scroll mostra sombra dos dois lados", () => {
  const edges = computeScrollEdges({ scrollLeft: 300, scrollWidth: 1000, clientWidth: 340 });
  assert.equal(edges.canScrollLeft, true);
  assert.equal(edges.canScrollRight, true);
});

test("computeScrollEdges: no fim do scroll (scrollLeft = max) so mostra sombra a esquerda", () => {
  const maxScrollLeft = 1000 - 340;
  const edges = computeScrollEdges({ scrollLeft: maxScrollLeft, scrollWidth: 1000, clientWidth: 340 });
  assert.equal(edges.canScrollLeft, true);
  assert.equal(edges.canScrollRight, false);
});

test("computeScrollEdges: threshold absorve diferenca de subpixel perto das bordas", () => {
  // 1px de diferenca nao deveria contar como overflow real nem como scroll pendente.
  const almostNoOverflow = computeScrollEdges({ scrollLeft: 0, scrollWidth: 341, clientWidth: 340 });
  assert.equal(almostNoOverflow.isScrollable, false);

  const maxScrollLeft = 1000 - 340;
  const almostAtEnd = computeScrollEdges({ scrollLeft: maxScrollLeft - 1, scrollWidth: 1000, clientWidth: 340 });
  assert.equal(almostAtEnd.canScrollRight, false);
});

test("computeScrollEdges: threshold customizado e respeitado", () => {
  const edges = computeScrollEdges({ scrollLeft: 0, scrollWidth: 345, clientWidth: 340 }, 10);
  assert.equal(edges.isScrollable, false);
});

// --- U6-01: Performance por Marca com scroll horizontal interno ---
//
// Regressao estatica (sem harness de componente React): a tabela larga da
// Gerencial precisa rolar internamente no mobile em vez de ser clipada por um
// container `overflow-hidden` — antes, ~340px de uma tabela de ~979px ficavam
// visiveis e GMV Total/metas eram inacessiveis. A correcao envolve SOMENTE a
// tabela num `TableScrollHint` (o mesmo ja validado em Canais/Financeiro/
// Produtos), preservando card, cabecalho, rodape, ordenacao e links.
const BRAND_TABLE_SRC = fs.readFileSync(
  path.join(import.meta.dirname, "..", "src", "components", "BrandPerformanceTable.tsx"),
  "utf8",
);
const HINT_SRC = fs.readFileSync(
  path.join(import.meta.dirname, "..", "src", "components", "TableScrollHint.tsx"),
  "utf8",
);

test("BrandPerformanceTable importa e usa TableScrollHint", () => {
  assert.match(BRAND_TABLE_SRC, /import\s+TableScrollHint\s+from\s+["']@\/components\/TableScrollHint["'];/, "deve importar TableScrollHint");
  assert.match(BRAND_TABLE_SRC, /<TableScrollHint>/, "deve renderizar <TableScrollHint>");
});

test("BrandPerformanceTable: o <table> fica DENTRO do TableScrollHint", () => {
  const openHint = BRAND_TABLE_SRC.indexOf("<TableScrollHint>");
  const openTable = BRAND_TABLE_SRC.indexOf("<table");
  const closeTable = BRAND_TABLE_SRC.indexOf("</table>");
  const closeHint = BRAND_TABLE_SRC.indexOf("</TableScrollHint>");
  assert.ok(openHint > -1 && openTable > -1 && closeTable > -1 && closeHint > -1, "abertura/fechamento de TableScrollHint e <table> devem existir");
  assert.ok(openHint < openTable, "TableScrollHint deve abrir ANTES do <table>");
  assert.ok(closeTable < closeHint, "</table> deve fechar ANTES de </TableScrollHint>");
});

test("BrandPerformanceTable: nao volta ao anti-padrao 'sem overflow-x para eliminar scroll lateral'", () => {
  assert.doesNotMatch(BRAND_TABLE_SRC, /sem overflow-x para eliminar scroll lateral/, "o comentario/regra que justificava remover o scroll lateral nao pode voltar");
});


// ═══════════════════════════════════════════════════════════════════════════
// Gate V3-1B — piso tipográfico de 12px na dica de rolagem
//
// O QA visual mediu 11px em tablet e mobile: a dica usava `text-[11px]`,
// abaixo do piso de 12px que o V3 declara. A troca é só de classe — texto,
// `aria-hidden`, cor, espaçamento e comportamento seguem idênticos.
// ═══════════════════════════════════════════════════════════════════════════

test("TableScrollHint: a dica de rolagem nao renderiza abaixo de 12px", () => {
  assert.doesNotMatch(HINT_SRC, /text-\[11px\]/, "11px era o valor medido no QA e esta abaixo do piso");
  // nenhum tamanho arbitrario abaixo do piso, em nenhum ponto do componente
  for (const m of HINT_SRC.matchAll(/text-\[(\d+)px\]/g)) {
    assert.ok(Number(m[1]) >= 12, `TableScrollHint renderiza texto a ${m[1]}px`);
  }
  assert.match(HINT_SRC, /sm:hidden text-center text-xs text-slate-400 pt-1/,
    "a dica passou a usar a classe de 12px, preservando alinhamento, cor e espacamento");
});

test("TableScrollHint: estrutura e semantica da dica preservadas na troca", () => {
  // a dica continua decorativa: quem usa leitor de tela nao ouve "arraste"
  assert.match(HINT_SRC, /<p aria-hidden="true" className="sm:hidden text-center text-xs/,
    "a dica continua aria-hidden e restrita ao mobile");
  assert.match(HINT_SRC, /← arraste para ver mais →/, "o texto da dica nao mudou");
  // e o componente segue com a mesma API e a mesma mecanica de bordas
  assert.match(HINT_SRC, /export default function TableScrollHint\(\{ children, className = "" \}: Props\)/,
    "API publica inalterada");
  assert.match(HINT_SRC, /edges\.canScrollRight/, "a dica continua condicionada a borda rolavel");
  assert.ok(!/text-\[10px\]|text-\[11px\]/.test(HINT_SRC));
});
