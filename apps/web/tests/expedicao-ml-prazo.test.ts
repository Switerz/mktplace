/**
 * EXPEDICAO-ML-PRAZO — pedido do usuario de 03/11/2026: "faça o mesmo para o
 * Mercado Livre" (cartao/coluna/lista "Perto de vencer" e Vencidos em
 * vermelho, como na Shopee).
 *
 * O prazo do ML e' o SLA oficial do envio, gravado pelo extrator do Airflow.
 * Ele so' existe em parte das fotografias (antes do deploy, ou com o SLA
 * faltando), entao a tela decide por `snapshot.deadline_published`, nunca pelo
 * nome do canal. O que estes testes travam:
 *   - sem o campo (ou `false`), o ML fica EXATAMENTE como antes;
 *   - com `true`, o ML le' como a Shopee, com o NOME do prazo dele;
 *   - nenhum texto do ML com prazo cita a Shopee ou o `ship_by_date`.
 */
import { register } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  KPIS_PRINCIPAIS,
  KPIS_RISCO_ATUAL,
  canalTemPrazo,
  medidasDoDestaque,
  montarAtencaoDaFila,
  montarKpisPrincipais,
  montarMapaDeRisco,
  montarRiscoAtual,
  montarTimeline,
  ondeAgirNaFila,
  type FotografiaDoPrazo,
  type LinhaFila,
  type ResumoConta,
  type Totais,
} from "../src/lib/expedicao-contract.ts";

register("./helpers/tsx-loader.mjs", import.meta.url);

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8").replace(/\r\n/g, "\n");
const CLIENTE = fonte("app/expedicao/ExpedicaoClient.tsx");
const DIALOGO = fonte("src/components/expedicao/TravadosDialog.tsx");

const COM_SLA: FotografiaDoPrazo = {
  effective_at: "2026-11-03T15:00:00Z",
  next_due_date: "2026-11-04",
  deadline_published: true,
};
const SEM_SLA: FotografiaDoPrazo = { ...COM_SLA, next_due_date: null, deadline_published: false };

function totais(over: Partial<Totais> = {}): Totais {
  return {
    backlog_count: 757, overdue_count: 4, due_within_24h_count: 300, on_time_count: 400,
    deadline_unavailable_count: 53, over_48h_count: 12, slow_count: 0, zombie_count: 1,
    stalled_count: 1, due_today_count: 120, due_next_day_count: 90, due_soon_count: 210,
    ...over,
  };
}
function conta(shop: string, brand: string, over: Partial<Totais> = {}): ResumoConta {
  return {
    shop_account: shop, brand, run_status: "success", source_watermark_at: null,
    source_advanced: true, snapshot_hour: "2026-11-03T15:00:00Z", ...totais(over),
  };
}

test("canalTemPrazo: Shopee sempre; ML so' com a fotografia dizendo que tem", () => {
  assert.equal(canalTemPrazo("shopee"), true);
  assert.equal(canalTemPrazo("shopee", SEM_SLA), true, "a Shopee nao depende do campo");
  assert.equal(canalTemPrazo("mercadolivre"), false);
  assert.equal(canalTemPrazo("mercadolivre", null), false);
  assert.equal(canalTemPrazo("mercadolivre", SEM_SLA), false);
  assert.equal(canalTemPrazo("mercadolivre", { deadline_published: null }), false, "API antiga");
  assert.equal(canalTemPrazo("mercadolivre", COM_SLA), true);
});

test("ML sem SLA: Risco atual igual ao de antes (quatro cartoes, sem prazo)", () => {
  for (const f of [null, SEM_SLA]) {
    const chaves = montarRiscoAtual(totais(), [], "mercadolivre", f).map((k) => k.chave);
    assert.deepEqual(chaves, [...KPIS_RISCO_ATUAL.mercadolivre]);
  }
});

test("ML com SLA: Risco atual le' como a Shopee, com vencidos e perto de vencer", () => {
  const kpis = montarRiscoAtual(totais(), [], "mercadolivre", COM_SLA);
  assert.deepEqual(kpis.map((k) => k.chave), [...KPIS_RISCO_ATUAL.shopee]);
  const vencidos = kpis.find((k) => k.chave === "overdue")!;
  assert.equal(vencidos.rotulo, "Vencidos (prazo Mercado Livre)");
  assert.equal(vencidos.valor, 4);
  assert.equal(vencidos.alerta, true, "vencido acende o vermelho");
  const perto = kpis.find((k) => k.chave === "due_soon")!;
  assert.equal(perto.valor, 210, "o MESMO numero da tabela e da lista");
  const over48 = kpis.find((k) => k.chave === "over48h")!;
  assert.doesNotMatch(String(over48.nota), /Shopee|ship_by_date/);
  assert.match(String(over48.nota), /SLA de despacho do Mercado Livre/);
});

test("ML com SLA: os quatro cartoes principais sao os da Shopee", () => {
  const chaves = montarKpisPrincipais(totais(), [], "mercadolivre", COM_SLA).map((k) => k.chave);
  assert.deepEqual(chaves, [...KPIS_PRINCIPAIS.shopee]);
  const semSla = montarKpisPrincipais(totais(), [], "mercadolivre", SEM_SLA).map((k) => k.chave);
  assert.deepEqual(semSla, [...KPIS_PRINCIPAIS.mercadolivre]);
});

test("ML com SLA: a atencao fala do prazo do Mercado Livre, nunca da Shopee", () => {
  const itens = montarAtencaoDaFila(totais(), [conta("2227056661", "kokeshi")], "mercadolivre", COM_SLA);
  const vencidos = itens.find((i) => i.chave === "overdue")!;
  assert.equal(vencidos.tom, "critico");
  assert.match(vencidos.texto, /prazo de despacho do Mercado Livre/);
  assert.doesNotMatch(itens.map((i) => i.texto).join(" "), /Shopee/);
  // Sem SLA, a frase de vencidos nao existe (nao ha o que vencer).
  const sem = montarAtencaoDaFila(totais(), [conta("2227056661", "kokeshi")], "mercadolivre", SEM_SLA);
  assert.ok(!sem.some((i) => i.chave === "overdue"));
});

test("ML com SLA: destaque e onde agir comecam pelos vencidos", () => {
  assert.deepEqual(medidasDoDestaque("mercadolivre", SEM_SLA), ["over_48h_count", "stalled_count"]);
  assert.equal(medidasDoDestaque("mercadolivre", COM_SLA)[0], "overdue_count");
  const contas = [
    conta("2227056661", "kokeshi", { overdue_count: 3 }),
    conta("2532564723", "barbours", { overdue_count: 1 }),
  ];
  const onde = ondeAgirNaFila(totais(), contas, "mercadolivre", COM_SLA);
  assert.equal(onde?.chave, "overdue_count");
});

test("ML com SLA: a barra por marca e' a particao de prazo (empilhavel)", () => {
  const contas = [conta("2227056661", "kokeshi", {
    backlog_count: 10, overdue_count: 2, due_soon_count: 3, on_time_count: 4,
    deadline_unavailable_count: 1,
  })];
  const sem = montarMapaDeRisco(contas, "mercadolivre", SEM_SLA);
  assert.equal(sem.empilhavel, false);
  const com = montarMapaDeRisco(contas, "mercadolivre", COM_SLA);
  assert.equal(com.empilhavel, true);
  assert.deepEqual(com.linhas[0].segmentos.map((s) => s.chave), ["overdue", "due_soon", "on_time", "sem_prazo"]);
  assert.equal(com.linhas[0].segmentos.reduce((s, x) => s + x.valor, 0), 10, "fecha o backlog");
});

test("ML com SLA: a timeline marca o prazo e o vencido de cada envio", () => {
  const linha = {
    order_number: "45678901234", order_ref: null, shop_account: "2227056661", brand: "kokeshi",
    created_at: "2026-11-02T15:00:00Z", paid_at: null, dispatch_deadline: "2026-11-03T17:00:00Z",
    deadline_source: "marketplace_native", deadline_status: "overdue",
    operational_age_status: "within_48h", is_slow_vs_baseline: false, is_source_zombie: false,
    is_stalled: false, hours_open: 20, hours_overdue: 1, logistic_type: "cross_docking",
    carrier: null, source_ingested_at: null, source_freshness_status: "fresh",
    timestamp_quality: "assumed",
  } as unknown as LinhaFila;
  const [sem] = montarTimeline([linha], "mercadolivre", undefined, SEM_SLA);
  assert.equal(sem.prazo, null);
  assert.equal(sem.vencido, false);
  const [com] = montarTimeline([linha], "mercadolivre", undefined, COM_SLA);
  assert.equal(com.prazo, "2026-11-03T17:00:00Z");
  assert.equal(com.vencido, true);
});

test("cliente: o prazo vem da fotografia do MESMO canal, e a LDR fica so' na Shopee", () => {
  assert.match(
    CLIENTE,
    /const fotografiaDoPrazo = dados && dados\.channel === filtros\.channel \? dados\.snapshot : null;/,
  );
  assert.match(CLIENTE, /const temPrazo = canalTemPrazo\(filtros\.channel, fotografiaDoPrazo\);/);
  assert.match(CLIENTE, /const ldrNaSecao = LDR_SHOPEE_LIGADA && filtros\.channel === "shopee";/);
  assert.match(CLIENTE, /acao=\{<Ajuda chave=\{`\$\{filtros\.channel\}\.prazo_situacao`\} \/>\}/);
});

test("lista em popup: a coluna Prazo aparece no ML quando alguma linha traz o SLA", () => {
  assert.match(DIALOGO, /canalTemPrazo\(canal\) \|\|/);
  assert.match(DIALOGO, /estado\.linhas\.some\(\(l\) => l\.dispatch_deadline != null\)/);
});
