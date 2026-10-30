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
  faixasDePrazo,
  montarKpis,
  montarMapaDeRisco,
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

// EXPEDICAO-PERTO-DE-VENCER: "vence em 24 h" + proximo dia viraram UM cartao.
test("Risco atual da Shopee: 'perto de vencer' logo depois de vencidos", () => {
  const chaves = montarRiscoAtual(totais(), [], "shopee").map((k) => k.chave);
  assert.deepEqual(chaves, ["backlog", "overdue", "due_soon", "over48h", "stalled", "marcas_alerta"]);
});

test("perto de vencer: o numero da API e a linha 'hoje · proximo dia'", () => {
  const snap = { effective_at: "2026-10-30T18:12:23Z", next_due_date: "2026-11-03" };
  const t = totais({ due_today_count: 378, due_next_day_count: 69, due_soon_count: 447 });
  const k = montarRiscoAtual(t, [], "shopee", snap).find((x) => x.chave === "due_soon")!;
  assert.equal(k.rotulo, "Perto de vencer");
  assert.equal(k.valor, 447);
  assert.equal(k.notaCurta, "378 hoje · 69 ter 03/11");
  // API antiga: ausencia, nunca zero
  const antiga = montarRiscoAtual(totais(), [], "shopee").find((x) => x.chave === "due_soon")!;
  assert.equal(antiga.valor, null);
  assert.equal(antiga.notaCurta, undefined);
});

test("particao de prazo: vencidos / perto de vencer / no prazo / sem prazo fecha o backlog", () => {
  const t = totais({ due_today_count: 378, due_next_day_count: 69, due_soon_count: 447 });
  const f = faixasDePrazo(t)!;
  assert.deepEqual(f.map((x) => x.chave), ["overdue", "due_soon", "on_time", "sem_prazo"]);
  assert.deepEqual(f.map((x) => x.valor), [5, 447, 270, 0]);
  assert.equal(f.reduce((s, x) => s + x.valor, 0), t.backlog_count);
  // a barra da tabela usa a MESMA particao, por conta
  const mapa = montarMapaDeRisco([conta("apice", "apice", { due_today_count: 378, due_next_day_count: 69, due_soon_count: 447 })], "shopee");
  assert.deepEqual(mapa.linhas[0].segmentos.map((x) => x.valor), [5, 447, 270, 0]);
});

test("lista 'perto de vencer': situacao due_soon, mais urgentes primeiro", () => {
  const q = new URLSearchParams(queryDaLista("perto_de_vencer", "shopee", null, null));
  assert.equal(q.get("situacao"), "due_soon");
  assert.equal(q.get("order_by"), "deadline");
  assert.equal(q.get("limit"), "500");
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

// ===========================================================================
// EXPEDICAO-TABELA-AMANHA — coluna "Vencem amanhã" e vencidos em vermelho
// ===========================================================================
// EXPEDICAO-PERTO-DE-VENCER: a coluna virou "Perto de vencer", o MESMO numero do cartao.
test("tabela por marca: coluna 'Perto de vencer' logo depois de Vencidos, que abre a lista", () => {
  const html = renderToStaticMarkup(
    h(EvolucaoPorMarca, {
      resposta: null,
      fila: [{
        conta: "apice", titulo: "ÁPICE", apelido: null, backlog: 420, vencidos: 4, acima48h: 2,
        travados: 7, pertoDeVencer: 296, pctPertoDeVencer: "70,5%", aoVerPertoDeVencer: () => {}, barra: null,
      }],
      ajudas: { pertoDeVencer: h("span", { "data-ajuda": "perto" }) },
    }),
  );
  const cab = html.slice(html.indexOf("<thead"), html.indexOf("</thead>"));
  assert.ok(cab.indexOf(">Vencidos") < cab.indexOf(">Perto de vencer"), "logo depois de Vencidos");
  assert.ok(cab.indexOf(">Perto de vencer") < cab.indexOf(">Acima de 48 h"));
  assert.match(cab, /data-ajuda="perto"/);
  assert.match(html, /aria-label="Ver os 296 pedidos de ÁPICE perto de vencer"[^>]*>296<\/button>/);
  assert.match(html, />70,5%</);
});

test("tabela por marca: sem a medida (ML ou API antiga), a coluna nao existe", () => {
  const html = renderToStaticMarkup(
    h(EvolucaoPorMarca, {
      resposta: null,
      fila: [{ conta: "1", titulo: "KOKESHI", apelido: null, backlog: 9, vencidos: null, acima48h: 1, travados: 0, barra: null }],
    }),
  );
  assert.doesNotMatch(html, /Perto de vencer/);
});

test("vencidos em destaque vermelho: titulo da coluna e numero em etiqueta vermelha", () => {
  const html = renderToStaticMarkup(
    h(EvolucaoPorMarca, {
      resposta: null,
      fila: [
        { conta: "a", titulo: "ÁPICE", apelido: null, backlog: 420, vencidos: 4, acima48h: 2, travados: 7, barra: null },
        { conta: "b", titulo: "LESCENT", apelido: null, backlog: 89, vencidos: 0, acima48h: 1, travados: 7, barra: null },
      ],
    }),
  );
  assert.match(html, /<th scope="col" class="[^"]*text-crit-ink[^"]*">Vencidos/);
  assert.match(html, /<span class="[^"]*bg-crit-soft[^"]*text-crit-ink[^"]*">4<\/span>/);
  // zero nao pinta: cor e' para risco
  assert.doesNotMatch(html, /bg-crit-soft[^"]*">0</);
});

test("tela: tabela e cartao usam a MESMA medida (due_soon_count) e a mesma lista", () => {
  assert.match(CLIENTE, /pertoDeVencer: temPrazo \? \(a\.due_soon_count \?\? null\) : null/);
  assert.match(CLIENTE, /lista: "perto_de_vencer"/);
  assert.match(CLIENTE, /due_soon: "perto_de_vencer"/);
  assert.match(CLIENTE, /perto_de_vencer: "due_soon_count"/);
  assert.equal(
    rotuloDoProximoVencimento("2026-11-03", "2026-10-30T18:12:23Z", "Vencem"),
    "Vencem ter 03/11",
  );
  assert.equal(rotuloDoProximoVencimento("2026-10-30", "2026-10-29T18:00:00Z", "Vencem"), "Vencem amanhã");
});

// ===========================================================================
// EXPEDICAO-DICA-LEGIVEL — balao maior, em tres partes, e texto objetivo
// ===========================================================================
const { ConteudoDaDica, LARGURA_DA_DICA } = await import("../src/components/ui/InfoDica.tsx");
const { default: AjudaMetrica } = await import("../src/components/expedicao/AjudaMetrica.tsx");

test("balao: nome em negrito, frase em tamanho de leitura e ressalva separada", () => {
  const html = renderToStaticMarkup(
    h(ConteudoDaDica, { titulo: "Acima de 48 h", texto: "Frase.", nota: "Ressalva." }),
  );
  assert.match(html, /<span class="[^"]*font-semibold[^"]*text-ink[^"]*">Acima de 48 h<\/span>/);
  assert.match(html, /<span class="[^"]*text-sm[^"]*text-ink[^"]*">Frase\.<\/span>/);
  assert.match(html, /<span class="[^"]*border-t[^"]*text-ink-muted[^"]*">Ressalva\.<\/span>/);
  // sem titulo nem nota (o resto da Torre): so' a frase
  assert.equal(
    renderToStaticMarkup(h(ConteudoDaDica, { texto: "So' isto." })).match(/<span/g)?.length,
    1,
  );
  assert.ok(LARGURA_DA_DICA >= 320, "balao maior que os 256 px de antes");
});

test("ⓘ da metrica: nome no balao, mas o leitor de tela le' so' definicao + ressalva", () => {
  const FONTE = fonte("src/components/expedicao/AjudaMetrica.tsx");
  assert.match(FONTE, /titulo=\{def\.nome\}/);
  assert.match(FONTE, /nota=\{def\.limitacoes\}/);
  const def = definicaoDe("shopee.over48h");
  const html = renderToStaticMarkup(h(AjudaMetrica, { chave: "shopee.over48h" }));
  assert.match(html, new RegExp(`aria-label="${def.definicao} ${def.limitacoes}"`));
});

test("textos objetivos: as classificacoes do Risco atual cabem numa leitura", () => {
  for (const c of [
    "shopee.backlog", "shopee.overdue", "shopee.due24h", "shopee.due_next_day", "shopee.no_prazo",
    "shopee.sem_prazo", "shopee.over48h", "shopee.stalled", "shopee.slow", "shopee.zombie",
  ] as const) {
    const d = definicaoDe(c);
    assert.ok(d.definicao.length <= 110, `${c}.definicao: ${d.definicao.length}`);
    assert.ok(d.limitacoes.length <= 90, `${c}.limitacoes: ${d.limitacoes.length}`);
  }
});
