/**
 * EXPEDICAO-KOKESHI-EXPORT — a Kokeshi entra na Expedicao Shopee pelo export do
 * Seller Centre, carregado pelo operador em outra hora que a fotografia
 * agendada. A tela continua com UMA "Ultima atualizacao" no topo; so' a linha
 * da conta carregada em outra hora mostra a propria hora, na tabela de marcas.
 */
import { register } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { avisosDeCobertura, type Cobertura } from "../src/lib/expedicao-contract.ts";
import { horaPropriaDaConta } from "../src/lib/expedicao-evolucao.ts";

register("./helpers/tsx-loader.mjs", import.meta.url);

const RAIZ = join(import.meta.dirname, "..");
const CLIENTE = readFileSync(join(RAIZ, "app/expedicao/ExpedicaoClient.tsx"), "utf8");

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: EvolucaoPorMarca } = await import("../src/components/expedicao/EvolucaoPorMarca.tsx");

const TOPO = "2026-11-03T14:50:31Z"; // 11:50 de Brasilia

test("conta da mesma fotografia nao ganha hora propria", () => {
  assert.equal(horaPropriaDaConta(TOPO, TOPO), null);
  // Folga de 5 min: a mesma execucao pode carimbar contas com segundos de diferenca.
  assert.equal(horaPropriaDaConta("2026-11-03T14:54:00Z", TOPO), null);
  assert.equal(horaPropriaDaConta(null, TOPO), null);
  assert.equal(horaPropriaDaConta(undefined, TOPO), null);
});

test("conta carregada em outra hora mostra dia e hora de Brasilia, sem ano", () => {
  assert.equal(horaPropriaDaConta("2026-11-03T13:20:00Z", TOPO), "03/11 10:20");
  assert.equal(horaPropriaDaConta("2026-11-04T01:05:00Z", TOPO), "03/11 22:05");
});

test("a tabela de marcas escreve a hora so' na linha que a tem", () => {
  const html = renderToStaticMarkup(
    h(EvolucaoPorMarca, {
      resposta: null,
      fila: [
        { conta: "apice", titulo: "ÁPICE", apelido: null, backlog: 9, vencidos: 0, acima48h: 0,
          travados: 0, barra: null },
        { conta: "kokeshi", titulo: "KOKESHI", apelido: null, backlog: 3053, vencidos: 12,
          acima48h: 40, travados: 0, barra: null, atualizadaEm: "03/11 10:20" },
      ],
    }),
  );
  const horas = html.match(/data-testid="exp-hora-da-conta"[^>]*>([^<]*)</g) ?? [];
  assert.equal(horas.length, 1);
  assert.match(html, /atualizada 03\/11 10:20/);
});

test("a pagina passa a hora da conta contra o topo da tela", () => {
  assert.match(CLIENTE, /atualizadaEm: horaPropriaDaConta\(a\.observed_at, dados\?\.snapshot\?\.effective_at\)/);
});

test("Kokeshi coberta pelo export deixa de ser aviso de fora da cobertura", () => {
  const base: Cobertura = {
    expected_accounts: ["apice", "kokeshi"], observed_accounts: ["apice", "kokeshi"],
    missing_accounts: [], unexpected_accounts: [], accounts: [], brands_not_covered: [],
  } as unknown as Cobertura;
  assert.deepEqual(avisosDeCobertura(base), []);
  const sem = { ...base, brands_not_covered: ["kokeshi"] } as Cobertura;
  assert.equal(avisosDeCobertura(sem)[0].tipo, "fora_do_escopo");
});
