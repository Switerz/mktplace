// T-0027 — a barra fixa de filtros do PageHeader nao pode criar rolagem
// horizontal.
//
// A barra "sangra" ate a borda do container com margem negativa (`-mx-*`) e
// devolve o respiro com padding (`px-*`). Se a margem negativa passa do padding
// do PageContainer, a barra fica mais larga que a pagina: com o container em
// `px-4` (16px fixo desde UI-DENSIDADE-1) e a barra em `sm:-mx-6` (24px), a
// pagina rolava 8px na horizontal (scrollWidth 1288 numa tela de 1280).
//
// `node --test` nao tem DOM: o teste trava o CONTRATO DE CLASSE, lendo o fonte.
// Esconder o estouro com `overflow-x-hidden` nao conta como correcao.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = (rel: string) => readFileSync(new URL(`../${rel}`, import.meta.url), "utf8");

const CONTAINER = "src/components/layout/PageContainer.tsx";
const HEADER = "src/components/layout/PageHeader.tsx";

/** Breakpoints do Tailwind em ordem mobile-first; "" e' a base. */
const BREAKPOINTS = ["", "sm", "md", "lg", "xl", "2xl"] as const;

/** Escala padrao do Tailwind: 1 unidade = 4px. */
const PX_POR_UNIDADE = 4;

/** className da primeira tag que contem `marcador` entre as classes. */
function classesDe(fonte: string, marcador: string): string[] {
  const m = fonte.match(new RegExp(`className="([^"]*${marcador}[^"]*)"`));
  assert.ok(m, `className com "${marcador}" nao localizado`);
  return m[1].split(/\s+/);
}

/**
 * Valor em px de `prop` (`px` ou `mx`) por breakpoint, ja com a cascata
 * mobile-first aplicada: um breakpoint sem classe propria herda o anterior.
 * Margem negativa sai como numero negativo.
 */
function porBreakpoint(classes: string[], prop: "px" | "mx"): Map<string, number> {
  const declarado = new Map<string, number>();
  for (const c of classes) {
    const m = c.match(/^(?:([a-z0-9]+):)?(-?)(px|mx)-(\d+(?:\.\d+)?)$/);
    if (!m || m[3] !== prop) continue;
    const valor = Number(m[4]) * PX_POR_UNIDADE * (m[2] === "-" ? -1 : 1);
    declarado.set(m[1] ?? "", valor);
  }
  const efetivo = new Map<string, number>();
  let atual: number | undefined;
  for (const bp of BREAKPOINTS) {
    atual = declarado.get(bp) ?? atual;
    if (atual !== undefined) efetivo.set(bp, atual);
  }
  return efetivo;
}

const containerClasses = classesDe(src(CONTAINER), "flex-col");
const barraClasses = classesDe(src(HEADER), "sticky top-0");

test("a margem negativa da barra nunca passa do padding do PageContainer", () => {
  const padding = porBreakpoint(containerClasses, "px");
  const margem = porBreakpoint(barraClasses, "mx");
  assert.ok(padding.has(""), "PageContainer precisa declarar px-* na base");
  assert.ok(margem.has(""), "a barra precisa declarar -mx-* na base (sangria)");

  for (const bp of BREAKPOINTS) {
    const p = padding.get(bp)!;
    const mx = margem.get(bp)!;
    const nome = bp || "base";
    assert.ok(mx <= 0, `${nome}: a sangria da barra deve ser margem negativa (${mx}px)`);
    assert.ok(
      Math.abs(mx) <= p,
      `${nome}: margem negativa de ${Math.abs(mx)}px passa do padding de ${p}px do container`,
    );
  }
});

test("o padding da barra devolve exatamente a sangria, alinhando os filtros ao conteudo", () => {
  const margem = porBreakpoint(barraClasses, "mx");
  const padding = porBreakpoint(barraClasses, "px");
  for (const bp of BREAKPOINTS) {
    assert.equal(padding.get(bp), Math.abs(margem.get(bp)!), `${bp || "base"}: px-* da barra != |-mx-*|`);
  }
});

test("a barra nao esconde estouro com overflow-x-hidden", () => {
  assert.ok(
    !barraClasses.some((c) => /(^|:)overflow-(x-)?hidden$/.test(c)),
    "overflow hidden mascara a rolagem em vez de corrigi-la: " + barraClasses.join(" "),
  );
});
