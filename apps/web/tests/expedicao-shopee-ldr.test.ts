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
  AVISO_FILA_COM_FBS,
  AVISO_LDR_SHOPEE,
  NOME_LDR_SHOPEE,
  divisaoDaFila,
  parcelasFecham,
  textoDaReconciliacao,
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

test("trocando de marca, a resposta antiga nunca aparece como pronta", () => {
  // Revisao do PR #107: com a resposta da marca anterior guardada, o estado
  // virava "pronta" e a tela mostrava a LDR da marca errada ate' a nova chegar.
  assert.equal(estadoDaLdrShopee({ carregando: true, erro: false, resposta: resposta() }), "carregando");
  assert.match(PAINEL, /let vivo = true;[\s\S]*?setResposta\(null\);\s*setCarregando\(true\);/);
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
  // O aviso de LDR ausente e' o "ⓘ" do titulo da secao.
  assert.match(CLIENTE, /<SecaoGerencial titulo="Prazo" dica=\{LDR_SHOPEE_LIGADA \? undefined : SEM_LDR_SHOPEE\}>/);
  assert.match(CLIENTE, /\{LDR_SHOPEE_LIGADA \? <LdrShopeePanel ldr=\{ldr\} marca=\{marcaAtiva\} \/> : null\}/);
  // Flag off = nenhuma requisicao: o hook so' liga com a flag E na Shopee.
  // UI-EXPEDICAO-1: a LDR le o periodo do topo da pagina.
  assert.match(CLIENTE, /useLdrShopee\(API, marcaAtiva, LDR_SHOPEE_LIGADA && filtros\.channel === "shopee", janela\)/);
  assert.match(PAINEL, /if \(!ligada\) \{\s*setResposta\(null\);/);
});

// ---------------------------------------------------------------------------
// Fila do vendedor x FBS
// ---------------------------------------------------------------------------
const T = (backlog: number, overdue: number, over48: number, stalled = 0) => ({
  backlog_count: backlog, overdue_count: overdue, due_within_24h_count: 0,
  on_time_count: backlog - overdue, deadline_unavailable_count: 0,
  over_48h_count: over48, slow_count: stalled, zombie_count: 0, stalled_count: stalled,
});
function comFila(over: Partial<NonNullable<RespostaLdrShopee["queue_by_flow"]>> = {}) {
  const seller = T(300, 2, 0);
  const fbs = T(258, 11, 43, 20);
  const total = T(558, 13, 43, 20);
  return resposta({
    queue_by_flow: {
      effective_at: "2026-10-21T21:40:00Z", source_watermark_at: null, stale: false,
      seller, fbs, total,
      accounts: [{ shop_account: "apice", brand: "apice", seller, fbs }],
      ...over,
    },
  });
}

test("FBS sai do risco do vendedor: os 43 acima de 48 h ficam na parcela FBS", () => {
  const d = divisaoDaFila(comFila())!;
  assert.equal(d.vendedor.over_48h_count, 0);
  assert.equal(d.fbs.over_48h_count, 43);
  assert.equal(d.vendedor.overdue_count, 2);
  assert.equal(d.contasVendedor[0].backlog_count, 300, "concentracao usa a fila do vendedor");
});

test("as parcelas fecham o total; se nao fecharem, nao ha divisao (volta o aviso)", () => {
  assert.ok(parcelasFecham(comFila().queue_by_flow!));
  const torta = comFila({ total: T(559, 13, 43, 20) });
  assert.equal(divisaoDaFila(torta), null);
});

test("sem fila publicada, a divisao e' ausencia (null), nunca zeros", () => {
  assert.equal(divisaoDaFila(resposta({ queue_by_flow: null })), null);
  assert.equal(divisaoDaFila(null), null);
});

test("conferencia mostra parcelas e total, sem chamar o total de risco", () => {
  assert.equal(
    textoDaReconciliacao(divisaoDaFila(comFila())!),
    "Conferência: fila do vendedor 300 + FBS 258 = 558 pedidos em aberto na mesma leitura. " +
      "O total serve só para conferir as parcelas.",
  );
});

test("tela: risco do vendedor com a divisao; aviso de FBS sem ela; FBS em bloco proprio", () => {
  assert.match(CLIENTE, /const totaisRisco = divisao \? divisao\.vendedor : recorte\.totais;/);
  assert.match(CLIENTE, /montarRiscoAtual\(totaisRisco,/);
  assert.match(CLIENTE, /montarAtencaoDaFila\(totaisRisco, contasRisco,/);
  assert.match(CLIENTE, /faixasDePrazo\(totaisRisco\)/);
  assert.match(CLIENTE, /filtros\.channel === "shopee" && !divisao && recorte\.totais \?/);
  assert.match(CLIENTE, /\{divisao \? <BlocoFbs divisao=\{divisao\} \/> : null\}/);
  assert.match(AVISO_FILA_COM_FBS, /Não leia estes números como atraso do vendedor/);
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
