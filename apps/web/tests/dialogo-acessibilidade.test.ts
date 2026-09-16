// Gate PMA-2C4B-H1 — contrato de acessibilidade do diálogo de drill-down.
//
// POR QUE ESTE ARQUIVO EXISTE
// ---------------------------
// O QA do PMA-2C4B mediu "sem focus trap e sem Escape" e contradisse a
// certificação do PMA-4F. A medição estava errada: `MobileDrawer` renderiza
// `role="dialog"` no shell MESMO FECHADO (só com `aria-hidden="true"`), e vem
// ANTES no DOM do portal do `KpiDrilldownDialog`. Um
// `document.querySelector('[role="dialog"]')` pega o drawer, não o diálogo —
// e então "o foco não está dentro" e "Escape não fechou" eram artefatos do
// seletor, não do produto.
//
// A garantia era real mas só verificável abrindo um navegador. Estes testes a
// tornam verificável no mesmo runner dos demais, sem dependência nova.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  alvoDoFocusTrap,
  FOCUSABLE_SELECTOR,
} from "../src/lib/focus-trap.ts";

const DIALOGO = new URL("../src/components/KpiDrilldownDialog.tsx", import.meta.url);
const DRAWER = new URL("../src/components/shell/MobileDrawer.tsx", import.meta.url);
const PAGINA = new URL("../app/monitoramento-preco/page.tsx", import.meta.url);

async function ler(url: URL): Promise<string> {
  const { readFile } = await import("node:fs/promises");
  return readFile(url, "utf8");
}

// ---------------------------------------------------------------------------
// Focus trap — a decisão, exaustivamente
// ---------------------------------------------------------------------------

const TRES = ["fechar", "link", "acao"];

test("Tab no ULTIMO elemento volta ao primeiro", () => {
  assert.equal(
    alvoDoFocusTrap({ focusables: TRES, active: "acao", dentroDoPainel: true, shiftKey: false }),
    "fechar",
  );
});

test("Tab no meio deixa o navegador seguir (sem interferir)", () => {
  assert.equal(
    alvoDoFocusTrap({ focusables: TRES, active: "link", dentroDoPainel: true, shiftKey: false }),
    null,
  );
  assert.equal(
    alvoDoFocusTrap({ focusables: TRES, active: "fechar", dentroDoPainel: true, shiftKey: false }),
    null,
  );
});

test("Shift+Tab no PRIMEIRO elemento volta ao ultimo", () => {
  assert.equal(
    alvoDoFocusTrap({ focusables: TRES, active: "fechar", dentroDoPainel: true, shiftKey: true }),
    "acao",
  );
});

test("Shift+Tab no meio deixa o navegador seguir", () => {
  assert.equal(
    alvoDoFocusTrap({ focusables: TRES, active: "link", dentroDoPainel: true, shiftKey: true }),
    null,
  );
  assert.equal(
    alvoDoFocusTrap({ focusables: TRES, active: "acao", dentroDoPainel: true, shiftKey: true }),
    null,
  );
});

test("foco PERDIDO fora do painel e' trazido de volta, nos dois sentidos", () => {
  // Acontece de verdade: o conteudo troca e o elemento focado e' desmontado,
  // jogando o foco no `document.body`. Sem isto, o proximo Tab sairia do
  // dialogo e percorreria a pagina de fundo.
  assert.equal(
    alvoDoFocusTrap({ focusables: TRES, active: null, dentroDoPainel: false, shiftKey: false }),
    "fechar",
  );
  assert.equal(
    alvoDoFocusTrap({ focusables: TRES, active: null, dentroDoPainel: false, shiftKey: true }),
    "acao",
  );
});

test("painel com UM unico focavel: o ciclo fecha nele mesmo", () => {
  // E' o caso real de Shopee e TikTok, onde a fato nao tem URL e o dialogo
  // fica so' com o botao de fechar.
  const um = ["fechar"];
  assert.equal(
    alvoDoFocusTrap({ focusables: um, active: "fechar", dentroDoPainel: true, shiftKey: false }),
    "fechar",
  );
  assert.equal(
    alvoDoFocusTrap({ focusables: um, active: "fechar", dentroDoPainel: true, shiftKey: true }),
    "fechar",
  );
});

test("painel SEM focavel nao prende o foco", () => {
  // Prender o foco num painel vazio deixaria o usuario de teclado sem saida.
  for (const shiftKey of [false, true]) {
    assert.equal(
      alvoDoFocusTrap({ focusables: [], active: null, dentroDoPainel: false, shiftKey }),
      null,
    );
  }
});

test("dois focaveis dao a volta nos dois sentidos", () => {
  // Caso do Mercado Livre: fechar + "Abrir anúncio".
  const dois = ["fechar", "link"];
  assert.equal(
    alvoDoFocusTrap({ focusables: dois, active: "link", dentroDoPainel: true, shiftKey: false }),
    "fechar",
  );
  assert.equal(
    alvoDoFocusTrap({ focusables: dois, active: "fechar", dentroDoPainel: true, shiftKey: true }),
    "link",
  );
});

// ---------------------------------------------------------------------------
// O contrato que o componente precisa manter
// ---------------------------------------------------------------------------

test("o seletor de focaveis cobre link, botao habilitado e tabindex proprio", () => {
  assert.ok(FOCUSABLE_SELECTOR.includes("a[href]"));
  assert.ok(FOCUSABLE_SELECTOR.includes("button:not([disabled])"));
  assert.ok(FOCUSABLE_SELECTOR.includes('[tabindex]:not([tabindex="-1"])'));
});

test("role, aria-modal e nome acessivel ligado ao titulo visivel", async () => {
  const src = await ler(DIALOGO);
  assert.ok(src.includes('role="dialog"'));
  assert.ok(src.includes('aria-modal="true"'));
  assert.ok(src.includes("aria-labelledby={titleId}"),
    "o nome acessivel precisa apontar para o titulo VISIVEL");
  assert.ok(src.includes('<h2 id={titleId}'),
    "o titulo visivel precisa carregar o mesmo id");
});

test("foco inicial vai para dentro, e volta ao disparador ao fechar", async () => {
  const src = await ler(DIALOGO);
  assert.ok(src.includes("previousFocusRef.current = document.activeElement"),
    "o disparador precisa ser guardado ANTES de mover o foco");
  assert.ok(src.includes("closeButtonRef.current?.focus()"),
    "o foco inicial vai para um controle do dialogo");
  assert.ok(src.includes("previousFocusRef.current?.focus()"),
    "ao desmontar, o foco volta para quem abriu");
});

test("Escape fecha e o listener e' removido no cleanup", async () => {
  const src = await ler(DIALOGO);
  assert.ok(/if \(e\.key === "Escape"\)[\s\S]{0,60}onClose\(\)/.test(src),
    "Escape precisa chamar onClose");
  assert.ok(src.includes('document.addEventListener("keydown", onKeyDown)'));
  assert.ok(src.includes('return () => document.removeEventListener("keydown", onKeyDown)'),
    "sem o cleanup ficariam listeners orfaos a cada abertura");
});

test("o fundo fica inerte enquanto aberto e volta ao normal ao fechar", async () => {
  const src = await ler(DIALOGO);
  assert.ok(src.includes('root?.setAttribute("inert", "")'));
  assert.ok(src.includes('root?.removeAttribute("inert")'),
    "deixar o shell inerte apos fechar travaria a pagina inteira");
  assert.ok(src.includes('document.body.style.overflow = originalOverflow'),
    "o overflow original precisa ser restaurado, nao chutado para ''");
});

test("o dialogo e' portalizado para fora do shell que recebe `inert`", async () => {
  const src = await ler(DIALOGO);
  assert.ok(src.includes("createPortal("));
  // O container e' o ULTIMO argumento do `createPortal`. Conferir so' a
  // PRESENCA de "document.body," nao basta: `getElementById("app-shell-root")
  // ?? document.body` contem a mesma substring e portalizaria o dialogo DENTRO
  // do no' que recebe `inert` — ai' o proprio dialogo ficaria inerte e nada
  // nele receberia foco. Por isso o container e' fixado por INTEIRO.
  const container = /<\/div>,\r?\n\s*(.+),\r?\n\s*\);/.exec(src)?.[1]?.trim();
  assert.equal(container, "document.body",
    "o container do portal precisa ser `document.body` e nada mais: qualquer no' " +
    "dentro de #app-shell-root herdaria o `inert` e travaria o proprio dialogo");
});

// ---------------------------------------------------------------------------
// A armadilha de medicao que gerou a contradicao
// ---------------------------------------------------------------------------

test("ha MAIS DE UM `role=dialog` no DOM: quem medir precisa desambiguar", async () => {
  const drawer = await ler(DRAWER);
  // O drawer do shell fica SEMPRE montado, apenas `aria-hidden` quando fechado.
  assert.ok(drawer.includes('role="dialog"'));
  assert.ok(drawer.includes("aria-hidden={!open}"),
    "e' este par que faz `querySelector('[role=dialog]')` devolver o drawer");
  // E o diálogo real NUNCA e' aria-hidden: e' esse o criterio de desambiguacao.
  const dialogo = await ler(DIALOGO);
  assert.ok(!dialogo.includes("aria-hidden={!open}"));
  assert.ok(!/aria-hidden="true"[\s\S]{0,40}role="dialog"/.test(dialogo));
});

test("a pagina de monitoramento usa UM unico dialogo, o compartilhado", async () => {
  const pagina = await ler(PAGINA);
  const ocorrencias = (pagina.match(/<KpiDrilldownDialog/g) ?? []).length;
  assert.equal(ocorrencias, 1, "mais de um shell de dialogo na mesma tela");
  assert.ok(!pagina.includes('role="dialog"'),
    "a pagina nao pode declarar um dialogo proprio ao lado do compartilhado");
});

test("o disparador some quando o filtro muda, e isso nao pode quebrar o retorno", async () => {
  const src = await ler(DIALOGO);
  // `?.focus()` — o disparador pode ter sido desmontado por troca de filtro ou
  // de canal. Sem o encadeamento opcional, fechar o dialogo levantaria.
  assert.ok(src.includes("previousFocusRef.current?.focus()"));
  assert.ok(!src.includes("previousFocusRef.current.focus()"),
    "chamada sem `?.` quebra quando o disparador ja' nao existe");
});
