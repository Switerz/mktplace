/**
 * VENDAS-POR-HORA-1 — o "Hoje" padronizado da Gerencial
 * (`src/lib/gerencial/hoje-por-hora.ts` e a fiacao nos cards da pagina).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { comparacaoDeHoje, rotuloDoCorte, variacao } from "../src/lib/gerencial/hoje-por-hora.ts";

test("rotulo do corte: 'até 13h'; sem hora fechada, nada", () => {
  assert.equal(rotuloDoCorte(13), "até 13h");
  assert.equal(rotuloDoCorte(0), null);
  assert.equal(rotuloDoCorte(null), null);
});

test("variacao contra a base; sem base nao inventa 100%", () => {
  assert.equal(variacao(110, 100), 10);
  assert.equal(variacao(90, 100), -10);
  assert.equal(variacao(10, 0), null);
});

const bloco = (gmv: number, pedidos: number) => ({ gmv, pedidos, ticket: pedidos ? gmv / pedidos : null });
const resposta = {
  disponivel: true, dia: "2026-11-06", corte_hora: 14, canais: [], limitacoes: [],
  hoje: bloco(1150, 23), ontem: bloco(1000, 25), semana_passada: bloco(900, 20),
  por_canal: [{ channel: "ml" as const, hoje: bloco(600, 10), ontem: bloco(500, 10), semana_passada: bloco(0, 0) }],
};

test("comparacao de hoje: ontem ate' a mesma hora, nos numeros dos cards", () => {
  const c = comparacaoDeHoje(resposta)!;
  assert.equal(c.rotulo, "vs ontem até 14h");
  assert.equal(c.gmvPct, 15);
  assert.equal(c.pedidosPct, -8);
  assert.equal(Math.round(c.ticketPct! * 10) / 10, 25);
  assert.equal(c.porCanal.ml, 20);
});

test("comparacao de hoje: sem tabela ou sem hora fechada, nada; Kokeshi fora vira aviso no rotulo", () => {
  assert.equal(comparacaoDeHoje(null), null);
  assert.equal(comparacaoDeHoje({ ...resposta, disponivel: false }), null);
  assert.equal(comparacaoDeHoje({ ...resposta, corte_hora: 0 }), null);
  const k = comparacaoDeHoje({ ...resposta, limitacoes: ["Kokeshi na Shopee fica fora do corte por hora: ..."] })!;
  assert.equal(k.rotulo, "vs ontem até 14h (sem Kokeshi Shopee)");
});

const pagina = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
const cliente = readFileSync(new URL("../src/lib/api-client.ts", import.meta.url), "utf8");

test("sem card proprio: a pagina usa a comparacao de hoje nos cards de sempre", () => {
  assert.doesNotMatch(pagina, /HojeAteAHora/);
  assert.match(pagina, /comparacaoDeHoje\(/);
});

test("busca /hoje-por-hora sem o cache de 5 min", () => {
  const fn = cliente.slice(cliente.indexOf("export async function fetchHojePorHora"));
  assert.match(fn, /apiFetch<HojePorHora>\(`\/api\/v1\/performance\/hoje-por-hora\?\$\{qs\.toString\(\)\}`\)/);
  assert.doesNotMatch(fn.slice(0, fn.indexOf("\n}\n")), /withCache/);
});

test("tendencia so' com os canais mostrados: com TikTok fora do valor, TikTok fica fora da conta", async () => {
  const { tendenciaNosCanais } = await import("../src/lib/gerencial/hoje-por-hora.ts");
  const t = (hoje: number, ate: number, dia: number) => ({ hoje, ontem_mesma_hora: ate, ontem_dia: dia, projecao: null });
  const m = {
    brand: "barbours", ...t(160, 100, 340),
    por_canal: [
      { channel: "tiktok" as const, ...t(100, 50, 200) },
      { channel: "ml" as const, ...t(40, 30, 90) },
      { channel: "shopee" as const, ...t(20, 20, 50) },
    ],
  };
  // ML + Shopee: 60 x 140 / 50 = 168, contra 140 de ontem.
  assert.deepEqual(tendenciaNosCanais(m, ["ml", "shopee"]), { projecao: 168, ontemDia: 140 });
  assert.equal(tendenciaNosCanais({ ...m, por_canal: [] }, ["ml"]), null);
  assert.equal(tendenciaNosCanais(m, ["ml", "shopee", "tiktok"])!.ontemDia, 340);
});
