/**
 * EXPEDICAO-VENCE-AMANHA — pedido do usuario de 30/10/2026:
 *   - "ⓘ" ao lado do titulo de cada classificacao, com o que ela trata;
 *   - % de vencidos sobre a fila;
 *   - lista dos pedidos que vencem amanha (proximo dia de prazo);
 *   - onde estao os vencidos (lista por etapa, como a dos travados).
 */
import { register } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  SITUACOES,
  montarKpis,
  montarRiscoAtual,
  recortarPorMarca,
  rotuloDoProximoVencimento,
  type FrescorMarca,
  type RespostaExpedicao,
  type ResumoConta,
  type Totais,
} from "../src/lib/expedicao-contract.ts";
import {
  DEFINICOES,
  METRICAS_POR_CANAL,
  chaveDoKpiDaFila,
  definicaoDe,
} from "../src/lib/expedicao-definicoes.ts";
import { descricaoDaEtapa } from "../src/lib/expedicao-jornada.ts";

register("./helpers/tsx-loader.mjs", import.meta.url);

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8").replace(/\r\n/g, "\n");
const CLIENTE = fonte("app/expedicao/ExpedicaoClient.tsx");

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const {
  colunasDoCsvTravados,
  LIMITE_TRAVADOS,
  LISTAS,
  csvDaLista,
  nomeDoCsv,
  queryDaLista,
  queryDosTravados,
} = await import("../src/lib/expedicao-travados.ts");
const { KpiComAjuda } = await import("../src/components/expedicao/KpiComAjuda.tsx");
const { default: RiscoEmDestaque } = await import("../src/components/expedicao/RiscoEmDestaque.tsx");
const { default: EvolucaoPorMarca } = await import("../src/components/expedicao/EvolucaoPorMarca.tsx");

function totais(over: Partial<Totais> = {}): Totais {
  return {
    backlog_count: 722, overdue_count: 5, due_within_24h_count: 378, on_time_count: 339,
    deadline_unavailable_count: 0, over_48h_count: 13, slow_count: 27, zombie_count: 0,
    stalled_count: 27, due_next_day_count: 210, ...over,
  };
}
function frescor(brand: string): FrescorMarca {
  return {
    brand, freshness: "fresh", status: "pass", severity: "low", source_watermark_at: null,
    source_age_hours: 1, oldest_row_age_hours: 10, accounts: 1, observed_at: null,
    measures: null, derived_from_batch: false,
  };
}
function conta(shop: string, brand: string, over: Partial<Totais> = {}): ResumoConta {
  return {
    shop_account: shop, brand, run_status: "success", source_watermark_at: null,
    source_advanced: true, snapshot_hour: "2026-10-30T18:00:00Z", ...totais(over),
  };
}
function linha(extra: Record<string, unknown> = {}) {
  return {
    order_number: "261029ABC", order_ref: null, shop_account: "apice", brand: "apice",
    created_at: "2026-10-29T15:00:00Z", paid_at: null, dispatch_deadline: "2026-11-04T02:59:00Z",
    deadline_source: "native", deadline_status: "on_time", operational_age_status: "within_48h",
    is_slow_vs_baseline: false, is_source_zombie: false, is_stalled: false, hours_open: 24,
    hours_overdue: null, logistic_type: null, carrier: "Shopee Xpress", source_ingested_at: null,
    source_freshness_status: "fresh", timestamp_quality: "ok", etapa: "a_preparar",
    ...extra,
  } as never;
}

// ===========================================================================
// Proximo dia de prazo ("vence amanha")
// ===========================================================================
test("a API aceita a situacao do proximo vencimento", () => {
  assert.ok((SITUACOES as readonly string[]).includes("due_next_day"));
});

test("rotulo: 'Vence amanhã' no dia seguinte da fotografia, em Brasilia", () => {
  // quinta 29/10 15:00 BRT -> sexta 30/10
  assert.equal(rotuloDoProximoVencimento("2026-10-30", "2026-10-29T18:00:00Z"), "Vence amanhã");
  // 29/10 23:30 em Brasilia (30/10 em UTC): amanha ainda e' 30/10
  assert.equal(rotuloDoProximoVencimento("2026-10-30", "2026-10-30T02:30:00Z"), "Vence amanhã");
});

test("rotulo: na sexta, o proximo dia de prazo e' escrito com dia e data", () => {
  // sexta 30/10 15:12 BRT; segunda 02/11 e' feriado: o proximo prazo e' terca
  assert.equal(rotuloDoProximoVencimento("2026-11-03", "2026-10-30T18:12:23Z"), "Vence ter 03/11");
});

test("rotulo: sem proximo prazo na fila, continua 'Vence amanhã' (zero medido)", () => {
  assert.equal(rotuloDoProximoVencimento(null, "2026-10-30T18:12:23Z"), "Vence amanhã");
  assert.equal(rotuloDoProximoVencimento(undefined, null), "Vence amanhã");
});

test("KPI do proximo vencimento: so' na Shopee, logo depois de 'Vence em 24h'", () => {
  const snap = { effective_at: "2026-10-30T18:12:23Z", next_due_date: "2026-11-03" };
  const shopee = montarKpis(totais(), [frescor("apice")], "shopee", snap);
  const i24 = shopee.findIndex((k) => k.chave === "due24h");
  assert.equal(shopee[i24 + 1].chave, "due_next_day");
  assert.equal(shopee[i24 + 1].valor, 210);
  assert.equal(shopee[i24 + 1].rotulo, "Vence ter 03/11");

  const ml = montarKpis(totais(), [frescor("apice")], "mercadolivre", snap);
  assert.equal(ml.find((k) => k.chave === "due_next_day"), undefined);
});

test("KPI do proximo vencimento: API antiga (sem o campo) e' ausencia, nunca zero", () => {
  const sem = totais();
  delete sem.due_next_day_count;
  const k = montarKpis(sem, [], "shopee").find((x) => x.chave === "due_next_day");
  assert.equal(k?.valor, null);
});

test("Risco atual da Shopee: vence amanha entre 'vence em 24h' e 'acima de 48h'", () => {
  const chaves = montarRiscoAtual(totais(), [], "shopee").map((k) => k.chave);
  assert.deepEqual(chaves, ["backlog", "overdue", "due24h", "due_next_day", "over48h", "stalled", "marcas_alerta"]);
});

test("recorte por marca de uma conta leva a contagem do proximo vencimento", () => {
  const r = {
    availability: "available", unavailable_reason: null, channel: "shopee", snapshot: null,
    totals: totais(), accounts: [conta("apice", "apice", { due_next_day_count: 120 })], freshness: [],
    coverage: null, queue: [], pagination: null, limitations: null,
  } as unknown as RespostaExpedicao;
  assert.equal(recortarPorMarca(r, "apice").totais?.due_next_day_count, 120);
});

// ===========================================================================
// Listas por cartao (vencidos, vence em 24 h, vence amanha, +48 h, travados)
// ===========================================================================
test("cada lista usa a rota da fila com a sua situacao e ordem", () => {
  const q = (t: Parameters<typeof queryDaLista>[0]) =>
    new URLSearchParams(queryDaLista(t, "shopee", "apice", null));
  assert.equal(q("vencidos").get("situacao"), "overdue");
  assert.equal(q("vencidos").get("order_by"), "deadline"); // mais atrasado primeiro
  assert.equal(q("vence_24h").get("situacao"), "due_within_24h");
  assert.equal(q("vence_proximo").get("situacao"), "due_next_day");
  assert.equal(q("vence_proximo").get("order_by"), "oldest");
  assert.equal(q("acima_48h").get("situacao"), "over_48h");
  assert.equal(q("vence_proximo").get("brands"), "apice");
  // a lista de amanha e' para agir: cabe a fila inteira do dia (limite da API)
  assert.equal(q("vence_proximo").get("limit"), "500");
  // travados: o mesmo de antes
  assert.equal(queryDaLista("travados", "shopee", null, null), queryDosTravados("shopee", null, null));
  assert.equal(new URLSearchParams(queryDosTravados("shopee", null, null)).get("limit"), String(LIMITE_TRAVADOS));
});

test("CSV das listas de prazo: sem a coluna 'Por que travou'", () => {
  const csv = csvDaLista([linha()], "shopee", "vence_proximo");
  const [cab, l] = csv.slice(1).split("\r\n");
  assert.doesNotMatch(cab, /Por que travou/);
  assert.match(cab, /^Pedido;Canal;Marca;Etapa;/);
  assert.match(l, /^261029ABC;Shopee;ÁPICE;A preparar;/);
  // travados continua com as colunas de antes
  const t = csvDaLista([linha({ is_stalled: true, is_slow_vs_baseline: true })], "shopee", "travados");
  assert.equal(t.slice(1).split("\r\n")[0], colunasDoCsvTravados("shopee").join(";"));
  // no Mercado Livre a primeira coluna e' o envio (#138), tambem nas listas novas
  assert.match(csvDaLista([linha()], "mercadolivre", "acima_48h").slice(1), /^Envio;Canal;/);
});

test("nome do CSV pelo tipo da lista", () => {
  const d = new Date("2026-10-30T18:00:00Z");
  assert.equal(nomeDoCsv("vence_proximo", "shopee", null, d), "vence-proximo-dia-shopee-todas-2026-10-30.csv");
  assert.equal(nomeDoCsv("vencidos", "shopee", "barbours", d), "vencidos-shopee-barbours-2026-10-30.csv");
  assert.equal(nomeDoCsv("travados", "shopee", "barbours", d), "travados-shopee-barbours-2026-10-30.csv");
});

test("toda lista tem nome, texto de vazio e situacao", () => {
  for (const [tipo, l] of Object.entries(LISTAS)) {
    assert.ok(l.nome.length > 2, tipo);
    assert.ok(l.vazio.length > 2, tipo);
    assert.ok((SITUACOES as readonly string[]).includes(l.situacao), tipo);
  }
});

test("popup: 'Por que travou' so' na lista de travados", () => {
  const POPUP = fonte("src/components/expedicao/TravadosDialog.tsx");
  assert.match(POPUP, /ehTravados \? \(\s*<DrilldownSecao titulo="Por que travou"/);
  assert.match(POPUP, /\{ehTravados \? <th scope="col">Por que travou<\/th> : null\}/);
  // vencidos: quanto ja' passou do prazo
  assert.match(POPUP, /\{ehVencidos \? <th scope="col" className="text-right">Atraso<\/th> : null\}/);
});

// ===========================================================================
// "ⓘ" ao lado do titulo, com o que cada classificacao trata
// ===========================================================================
test("KpiComAjuda poe o ⓘ ao lado do rotulo, antes do numero", () => {
  const html = renderToStaticMarkup(
    h(KpiComAjuda, { ajuda: "shopee.due24h", rotulo: "Vence em 24h", valor: "378", comparacao: "nota" }),
  );
  const iInfo = html.indexOf('role="img"');
  const iValor = html.indexOf("378");
  assert.ok(iInfo > 0 && iInfo < iValor, "o ⓘ vem antes do numero");
  assert.ok(html.indexOf("Vence em 24h") < iInfo, "o ⓘ vem depois do rotulo");
});

test("o ⓘ de 'Vence em 24 h' diz que, na pratica, sao os que vencem hoje", () => {
  assert.match(definicaoDe("shopee.due24h").definicao, /23:59/);
  assert.match(definicaoDe("shopee.due24h").definicao, /hoje/);
});

test("catalogo: proximo vencimento e as faixas da barra de prazo tem ⓘ", () => {
  assert.equal(chaveDoKpiDaFila("shopee", "due_next_day"), "shopee.due_next_day");
  assert.match(definicaoDe("shopee.due_next_day").definicao, /fim de semana|sexta/);
  assert.equal(definicaoDe("mercadolivre.due_next_day").origem, "indisponivel");
  for (const c of ["shopee.due_next_day", "shopee.no_prazo", "shopee.sem_prazo"] as const) {
    assert.ok(c in DEFINICOES, c);
    assert.ok(METRICAS_POR_CANAL.shopee.includes(c), c);
  }
});

test("barra de prazo: cada faixa da legenda tem o seu ⓘ", () => {
  assert.match(CLIENTE, /AJUDA_DA_FAIXA\[f\.chave\]/);
  assert.match(CLIENTE, /overdue: "shopee\.overdue"/);
  assert.match(CLIENTE, /on_time: "shopee\.no_prazo"/);
});

test("Jornada: cada etapa diz o que significa, no canal certo", () => {
  assert.match(descricaoDaEtapa("a_preparar", "shopee") ?? "", /READY_TO_SHIP/);
  assert.match(descricaoDaEtapa("aguardando_coleta", "shopee") ?? "", /PROCESSED/);
  assert.match(descricaoDaEtapa("aguardando_coleta", "mercadolivre") ?? "", /ready_for_pickup/);
  for (const k of ["fora_fbs", "fora_amostra", "cancelado_antes", "enviado_sem_registro", "aguardando", "concluido"]) {
    assert.ok(descricaoDaEtapa(k, "shopee") || descricaoDaEtapa(k, "tiktok"), k);
  }
  assert.equal(descricaoDaEtapa("etapa_que_nao_existe", "shopee"), null);
  const JORNADA = fonte("src/components/expedicao/JornadaPedidos.tsx");
  assert.match(JORNADA, /descricaoDaEtapa\(e\.key, canal\)/);
});

// ===========================================================================
// % de vencidos sobre a fila
// ===========================================================================
test("destaque: vencidos com o % da fila ('5 de 722 em fila · 0,7%')", () => {
  const html = renderToStaticMarkup(
    h(RiscoEmDestaque, {
      principal: { chave: "overdue", rotulo: "Vencidos (prazo Shopee)", valor: 5, alerta: true },
      backlog: { chave: "backlog", rotulo: "Backlog total", valor: 722, alerta: false },
      secundarios: null,
    }),
  );
  assert.match(html, /de 722 em fila/);
  assert.match(html, /0,7% da fila/);
});

test("tabela por marca: vencidos com % da conta e ⓘ nos cabecalhos", () => {
  const html = renderToStaticMarkup(
    h(EvolucaoPorMarca, {
      resposta: null,
      fila: [{
        conta: "apice", titulo: "ÁPICE", apelido: null, backlog: 420, vencidos: 4, acima48h: 2,
        travados: 7, pctTravados: "1,7%", pctVencidos: "1,0%", barra: null,
      }],
      ajudas: { vencidos: h("span", { "data-ajuda": "vencidos" }), travados: h("span", { "data-ajuda": "travados" }) },
    }),
  );
  assert.match(html, /1,0%/);
  assert.match(html, /data-ajuda="vencidos"/);
  assert.match(html, /data-ajuda="travados"/);
});
