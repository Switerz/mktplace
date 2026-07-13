import { test } from "node:test";
import assert from "node:assert/strict";
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
