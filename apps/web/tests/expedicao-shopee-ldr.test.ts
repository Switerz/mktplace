/**
 * EXPEDICAO-SHOPEE-LDR-1 — LDR operacional da Shopee na tela.
 *
 * O que erra em silencio aqui: ausencia virando "0,00%"; a taxa aparecendo sem
 * o aviso de que nao e' a metrica de penalizacao da Shopee; a marca filtrada
 * sem chegar a' API; a flag desligada mudando a tela de producao; e a LDR do
 * TikTok sendo tocada por tabela.
 */
import { register } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  AVISO_LDR_SHOPEE,
  NOME_LDR_SHOPEE,
  estadoDaLdrShopee,
  formatarTaxaLdr,
  montarExclusoes,
  motivoDaTaxaAusente,
  queryDaLdrShopee,
  textoDaCobertura,
  textoDoEstado,
  type RespostaLdrShopee,
} from "../src/lib/expedicao-shopee-ldr.ts";

register("./helpers/tsx-loader.mjs", import.meta.url);

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8").replace(/\r\n/g, "\n");
const CLIENTE = fonte("app/expedicao/ExpedicaoClient.tsx");
const PAINEL = fonte("src/components/expedicao/LdrShopeePanel.tsx");
const TIKTOK = fonte("src/components/expedicao/TikTokDispatchPanel.tsx");
const PIPELINE = readFileSync(join(RAIZ, "..", "..", "pipelines", "expedicao", "shopee_ldr.py"), "utf8");

function resposta(over: Partial<RespostaLdrShopee> = {}): RespostaLdrShopee {
  return {
    availability: "available",
    unavailable_reason: null,
    channel: "shopee",
    metric_name: NOME_LDR_SHOPEE,
    official: false,
    disclaimer: AVISO_LDR_SHOPEE,
    event: "coleta",
    thresholds: { coverage_warning: 0.02, coverage_hide_rate: 0.1 },
    snapshot: {
      refresh_batch_id: "x", effective_at: "2026-10-21T17:00:00Z", source_watermark_at: null,
      today_brt: "2026-10-21", age_hours: 1, stale: false,
    },
    ldr: {
      from: "2026-10-14", to: "2026-10-21", days: 7, rate: 0.0335, rate_hidden_reason: null,
      base: 1819, late: 61, shipped_on_time: 1758, shipped_late: 61, pending_overdue: 0,
      pending_on_time: 300, pending_at_risk: 120, mature_due_days: 4, partial_due_days: 1,
      daily: [],
    },
    exclusions: {
      fbs: 4768, cancelled_without_pickup: 18, cancelled_seller_did_not_ship: 13,
      cancelled_unknown_reason: 0, shipped_without_pickup: 0, without_deadline: 0,
    },
    coverage: {
      state: "completa", missing_share: 0, missing: 0, eligible: 1819,
      without_deadline: 0, shipped_without_pickup: 0,
    },
    brands: [],
    alerts: [],
    ...over,
  };
}

test("nome e aviso batem com o pipeline, palavra por palavra", () => {
  assert.equal(NOME_LDR_SHOPEE, "LDR operacional (coleta × prazo Shopee)");
  assert.ok(PIPELINE.includes(`NOME_METRICA = "${NOME_LDR_SHOPEE}"`));
  assert.match(AVISO_LDR_SHOPEE, /não é a taxa de envio atrasado que a Shopee usa para penalização/);
});

test("ausencia nunca vira 0%", () => {
  assert.equal(formatarTaxaLdr(null), "—");
  assert.equal(formatarTaxaLdr(undefined), "—");
  assert.equal(formatarTaxaLdr(Number.NaN), "—");
  assert.equal(formatarTaxaLdr(0), "0,00%", "zero medido continua zero");
  assert.equal(formatarTaxaLdr(0.0335), "3,35%");
});

test("a marca vai para a API; sem marca, nenhum brands", () => {
  assert.equal(queryDaLdrShopee(7, "apice"), "/api/v1/expedicao/shopee/ldr?days=7&brands=apice");
  assert.equal(queryDaLdrShopee(7, null), "/api/v1/expedicao/shopee/ldr?days=7");
});

test("estados: desligada, nao migrada, vazia, erro, carregando e pronta", () => {
  const indis = (motivo: string) =>
    resposta({ availability: "unavailable", unavailable_reason: motivo, ldr: null });
  assert.equal(estadoDaLdrShopee({ carregando: false, erro: false, resposta: indis("disabled") }), "desligada");
  assert.equal(estadoDaLdrShopee({ carregando: false, erro: false, resposta: indis("table_not_migrated") }), "nao_migrada");
  assert.equal(estadoDaLdrShopee({ carregando: false, erro: false, resposta: indis("no_series_published") }), "vazia");
  assert.equal(estadoDaLdrShopee({ carregando: false, erro: true, resposta: null }), "erro");
  assert.equal(estadoDaLdrShopee({ carregando: true, erro: false, resposta: null }), "carregando");
  assert.equal(estadoDaLdrShopee({ carregando: false, erro: false, resposta: resposta() }), "pronta");
});

test("vazio diz ausencia de dado, nunca atraso zero", () => {
  assert.match(textoDoEstado("vazia", "apice"), /ausência de dado, não atraso zero/);
  assert.match(textoDoEstado("erro", null), /Nenhum número é exibido/);
});

test("taxa escondida explica o motivo", () => {
  const cob = resposta({
    ldr: { ...resposta().ldr!, rate: null, rate_hidden_reason: "coverage_insufficient" },
    coverage: { ...resposta().coverage!, state: "insuficiente", missing_share: 0.12 },
  });
  assert.match(motivoDaTaxaAusente(cob)!, /mais de 10%/);
  assert.match(textoDaCobertura(cob)!, /Cobertura insuficiente: 12,00%/);
  const semMadura = resposta({ ldr: { ...resposta().ldr!, rate: null, mature_due_days: 0 } });
  assert.match(motivoDaTaxaAusente(semMadura)!, /Nenhum vencimento fechado/);
  assert.equal(motivoDaTaxaAusente(resposta()), null);
});

test("cobertura: completa nao avisa, aviso acima de 2%", () => {
  assert.equal(textoDaCobertura(resposta()), null);
  const aviso = resposta({ coverage: { ...resposta().coverage!, state: "aviso", missing_share: 0.03 } });
  assert.match(textoDaCobertura(aviso)!, /incompleta: 3,00%.*acima de 2%/);
});

test("exclusoes: os quatro contadores, na ordem da precedencia, com o nao-enviado nomeado", () => {
  const ex = montarExclusoes(resposta());
  assert.deepEqual(ex.map((e) => e.chave), ["fbs", "cancelados", "sem_prazo", "sem_marco"]);
  assert.deepEqual(ex.map((e) => e.valor), [4768, 18, 0, 0]);
  assert.match(ex[1].nota!, /13 cancelado\(s\) pela Shopee por falta de envio/);
});

test("flag desligada: a secao Prazo fica como antes, inclusive o aviso de LDR ausente", () => {
  assert.match(CLIENTE, /const LDR_SHOPEE_LIGADA = process\.env\.NEXT_PUBLIC_EXPEDICAO_SHOPEE_LDR_ENABLED === "true";/);
  assert.match(CLIENTE, /\{LDR_SHOPEE_LIGADA \? null : \(\s*<Note[^>]*>\s*\{SEM_LDR_SHOPEE\}/);
  assert.match(CLIENTE, /\{LDR_SHOPEE_LIGADA \? <LdrShopeePanel api=\{API\} marca=\{marcaAtiva\} \/> : null\}/);
});

test("o painel sempre mostra o aviso de que nao e' oficial e nao pinta a taxa de severidade", () => {
  assert.match(PAINEL, /data-testid="exp-ldr-shopee-aviso"/);
  assert.match(PAINEL, /tom=\{l\.rate === null \? "ausente" : "neutro"\}/);
  assert.doesNotMatch(PAINEL, /meta|dentro da meta/i);
});

test("erro nao mantem o numero antigo", () => {
  assert.match(PAINEL, /setResposta\(null\);\s*setErro\(true\);/);
});

test("a LDR do TikTok nao foi tocada", () => {
  assert.doesNotMatch(TIKTOK, /shopee\/ldr|LdrShopee|LDR_SHOPEE/);
});
