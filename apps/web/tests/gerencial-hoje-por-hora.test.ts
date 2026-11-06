/**
 * VENDAS-POR-HORA-1 — o "Hoje" padronizado da Gerencial
 * (`src/lib/gerencial/hoje-por-hora.ts` e a fiacao do bloco na pagina).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { canaisQueLimitam, nomeDoCanal, rotuloDoCorte, variacao } from "../src/lib/gerencial/hoje-por-hora.ts";

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

test("quem limita o corte: o canal parado na hora do corte quando outro ja' passou", () => {
  const canais = [
    { channel: "tiktok" as const, fonte_ate: null, horas_fechadas_hoje: 13 },
    { channel: "ml" as const, fonte_ate: null, horas_fechadas_hoje: 13 },
    { channel: "shopee" as const, fonte_ate: null, horas_fechadas_hoje: 11 },
  ];
  assert.deepEqual(canaisQueLimitam({ canais, corte_hora: 11 }), ["shopee"]);
  assert.deepEqual(canaisQueLimitam({ canais: canais.slice(0, 2), corte_hora: 13 }), []);
  assert.deepEqual(canaisQueLimitam({ canais: canais.slice(2), corte_hora: 11 }), []);
  assert.equal(nomeDoCanal("ml"), "Mercado Livre");
});

const pagina = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
const bloco = readFileSync(new URL("../src/components/gerencial/HojeAteAHora.tsx", import.meta.url), "utf8");
const cliente = readFileSync(new URL("../src/lib/api-client.ts", import.meta.url), "utf8");

test("o bloco so' aparece com o periodo 'Hoje' e recebe canal e marca do filtro", () => {
  assert.match(
    pagina,
    /\{diaEmAndamento && <HojeAteAHora channels=\{filters\.channels\} brands=\{filters\.brands\} \/>\}/,
  );
});

test("o bloco busca /hoje-por-hora sem o cache de 5 min", () => {
  assert.match(bloco, /fetchHojePorHora\(channels, brands\)/);
  const fn = cliente.slice(cliente.indexOf("export async function fetchHojePorHora"));
  assert.match(fn, /apiFetch<HojePorHora>\(`\/api\/v1\/performance\/hoje-por-hora\?\$\{qs\.toString\(\)\}`\)/);
  assert.doesNotMatch(fn.slice(0, fn.indexOf("\n}\n")), /withCache/);
});

test("limitacao (Kokeshi na Shopee) aparece no bloco, nao some", () => {
  assert.match(bloco, /d\.limitacoes\.map/);
});
