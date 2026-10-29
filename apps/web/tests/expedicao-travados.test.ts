/**
 * EXPEDICAO-TRAVADOS-1 — % de travados, popup com os pedidos travados e a
 * Expedicao com menos texto (pedido do usuario).
 */
import { register } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { avisosDeCobertura, montarBarraDeQualidade } from "../src/lib/expedicao-contract.ts";

register("./helpers/tsx-loader.mjs", import.meta.url);

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8").replace(/\r\n/g, "\n");
const CLIENTE = fonte("app/expedicao/ExpedicaoClient.tsx");

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { motivoDoTravado, percentualDaFila, queryDosTravados, LIMITE_TRAVADOS } = await import(
  "../src/lib/expedicao-travados.ts"
);
const { default: BarraDeQualidade } = await import("../src/components/expedicao/BarraDeQualidade.tsx");
const { default: EvolucaoPorMarca } = await import("../src/components/expedicao/EvolucaoPorMarca.tsx");

test("% da fila: uma casa decimal; sem base, nada", () => {
  assert.equal(percentualDaFila(17, 320), "5,3%");
  assert.equal(percentualDaFila(0, 100), "0,0%");
  assert.equal(percentualDaFila(3, 0), null);
});

test("motivo do travado em linguagem de gestor, sem inventar", () => {
  assert.equal(motivoDoTravado({ is_slow_vs_baseline: true, is_source_zombie: false }), "Mais lento que o normal da conta");
  assert.equal(motivoDoTravado({ is_slow_vs_baseline: true, is_source_zombie: true }), "Aberto há mais de 30 dias");
  assert.equal(motivoDoTravado({ is_slow_vs_baseline: false, is_source_zombie: false }), "Travado");
});

test("a lista usa a rota da fila: so' travados, mais antigos primeiro, da marca e da conta", () => {
  const q = new URLSearchParams(queryDosTravados("mercadolivre", "kokeshi", "123"));
  assert.equal(q.get("channel"), "mercadolivre");
  assert.equal(q.get("brands"), "kokeshi");
  assert.equal(q.get("accounts"), "123");
  assert.equal(q.get("situacao"), "stalled");
  assert.equal(q.get("order_by"), "oldest");
  assert.equal(q.get("limit"), String(LIMITE_TRAVADOS));
  const todas = new URLSearchParams(queryDosTravados("shopee", null, null));
  assert.equal(todas.get("brands"), null);
  assert.equal(todas.get("accounts"), null);
});

test("tela: cartao de travados com % e 'ver quais'; popup montado uma vez", () => {
  assert.match(CLIENTE, /k\.chave === "stalled" && k\.valor !== null \? \(\s*cartaoTravados\(k\)/);
  assert.match(CLIENTE, /\{pct \? `\$\{pct\} da fila` : null\}/);
  assert.match(CLIENTE, />\s*ver quais\s*</);
  assert.equal((CLIENTE.match(/<TravadosDialog\b/g) ?? []).length, 1);
  assert.match(CLIENTE, /pctTravados: percentualDaFila\(a\.stalled_count, a\.backlog_count\)/);
});

test("tabela: o numero de travados abre a lista e mostra o %", () => {
  let aberto = 0;
  const html = renderToStaticMarkup(
    h(EvolucaoPorMarca, {
      resposta: null,
      fila: [
        {
          conta: "a", titulo: "ÁPICE", apelido: null, backlog: 320, vencidos: 25, acima48h: 16, travados: 17,
          pctTravados: "5,3%", aoVerTravados: () => { aberto += 1; }, barra: null,
        },
        {
          conta: "b", titulo: "BARBOURS", apelido: null, backlog: 208, vencidos: 0, acima48h: 3, travados: 0,
          pctTravados: "0,0%", barra: null,
        },
      ],
    }),
  );
  assert.match(html, /aria-label="Ver os 17 pedidos travados de ÁPICE"[^>]*>17<\/button>/);
  assert.match(html, />5,3%</);
  assert.doesNotMatch(html, />0,0%</, "zero travado nao mostra %");
  assert.equal(aberto, 0);
});

test("menos texto: Prazo some sem LDR; jornada so' com coorte; convite curto", () => {
  assert.match(CLIENTE, /const secaoPrazo = temPrazo && !LDR_SHOPEE_LIGADA && destaque && temBarraDePrazo \? null/);
  assert.match(CLIENTE, /<JornadaPedidos [^>]*soCoorte \/>/);
  assert.match(CLIENTE, /conteudo="Lista de pedidos, mais críticos e fonte por conta\."/);
  assert.match(CLIENTE, /<SecaoGerencial titulo="Prazo" dica=\{SEM_PRAZO_CANAL_ML\}>/);
  const JORNADA = fonte("src/components/expedicao/JornadaPedidos.tsx");
  assert.match(JORNADA, /if \(estado !== "pronta" \|\| !resposta\?\.coorte\) return null;/);
});

test("marca fora da cobertura vira linha curta com ⓘ, sem caixa", () => {
  const avisos = avisosDeCobertura({
    expected_accounts: [], observed_accounts: [], missing_accounts: [], unexpected_accounts: [],
    accounts: [], brands_not_covered: ["kokeshi"],
  });
  const barra = montarBarraDeQualidade(null, avisos, []);
  const html = renderToStaticMarkup(h(BarraDeQualidade, { barra }));
  assert.match(html, /KOKESHI fora da cobertura/);
  assert.match(html, /aria-label="KOKESHI não é coberta por esta fonte/);
  assert.doesNotMatch(html, /role="status"/, "informacao nao e' alerta");
});

test("revisao #124: popup sem coluna inutil, remonta por alvo e diz quando inclui FBS", () => {
  const POPUP = fonte("src/components/expedicao/TravadosDialog.tsx");
  assert.match(POPUP, /\{temPrazo \? <th scope="col">Prazo<\/th> : null\}/);
  assert.match(POPUP, /alvo\.incluiFbs \? "Lista da fila publicada, com pedidos FBS/);
  assert.match(POPUP, /estado\.tipo === "ok" && !temCodigo \? " Por segurança/);
  assert.match(CLIENTE, /<TravadosDialog\s+key=\{alvoTravados \?/);
  assert.match(CLIENTE, /incluiFbs: divisao !== null/);
  const JORNADA = fonte("src/components/expedicao/JornadaPedidos.tsx");
  assert.match(JORNADA, /if \(!ligada\) \{/);
});

