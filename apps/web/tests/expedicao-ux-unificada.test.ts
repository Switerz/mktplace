/**
 * Gate EXPEDICAO-UX-UNIFICADA-1 — um produto so' para Shopee, Mercado Livre e
 * TikTok Shop, com os contratos de cada canal intactos.
 *
 * O que erra em silencio aqui:
 *   - um link antigo (`?channel=tiktokshop&dias=45`) abrindo noutro canal ou
 *     noutra janela depois da troca de navegacao;
 *   - a narrativa do gestor reinterpretando metrica: "acima de 48h" virando
 *     prazo, o ML ganhando "vencidos", "nada a fazer" pintado de verde numa
 *     fotografia velha;
 *   - o TikTok voltando a empilhar banners, ou desenhando um grafico vazio
 *     quando a serie esta' vencida;
 *   - a tela calculando a idade da serie do TikTok, que a API nao publica.
 */
import { register } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  canaisHabilitados,
  lerCanalExpedicao,
  urlDoCanal,
} from "../src/lib/expedicao-canal.ts";
import {
  consolidarQualidade,
  lerVisao,
  urlComVisao,
  VISAO_PADRAO,
} from "../src/lib/expedicao-ux.ts";
import {
  fotografiaDaFila,
  montarAtencaoDaFila,
  type ResumoConta,
  type Totais,
} from "../src/lib/expedicao-contract.ts";
import {
  AVISO_PRAZO_RECONSTRUIDO,
  CODIGO_FOTOGRAFIA_VELHA,
  fotografiaDoTikTok,
  montarAtencaoDoTikTok,
  montarBarraDeQualidadeTikTok,
  serieTemDiaMedido,
  type TikTokLdrDia,
  type TikTokPayload,
} from "../src/lib/expedicao-tiktok-contract.ts";

register("./helpers/tsx-loader.mjs", import.meta.url);

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8");
const CASCA = fonte("app/expedicao/ExpedicaoShell.tsx");
const CLIENTE = fonte("app/expedicao/ExpedicaoClient.tsx");
const TIKTOK = fonte("src/components/expedicao/TikTokDispatchPanel.tsx");
const BASE = "https://torre.example/expedicao";

const TODAS = { ml: true, tiktok: true };

// ===========================================================================
// 1. Um seletor so', o mesmo `?channel=` de sempre
// ===========================================================================
test("o seletor tem uma ordem fixa e so' os canais com flag", () => {
  assert.deepEqual(canaisHabilitados(TODAS), ["shopee", "mercadolivre", "tiktokshop"]);
  assert.deepEqual(canaisHabilitados({ ml: true, tiktok: false }), ["shopee", "mercadolivre"]);
  assert.deepEqual(canaisHabilitados({ ml: false, tiktok: false }), ["shopee"]);
});

test("links antigos abrem no mesmo canal de antes", () => {
  assert.equal(lerCanalExpedicao("", TODAS), "shopee");
  assert.equal(lerCanalExpedicao("?channel=mercadolivre", TODAS), "mercadolivre");
  assert.equal(lerCanalExpedicao("?channel=tiktokshop&dias=45", TODAS), "tiktokshop");
});

test("canal desconhecido ou desligado cai na Shopee (fail-closed)", () => {
  for (const v of ["TIKTOKSHOP", "tiktok", "ml", "<script>", "shopee "]) {
    assert.equal(lerCanalExpedicao(`?channel=${encodeURIComponent(v)}`, TODAS), "shopee", v);
  }
  assert.equal(lerCanalExpedicao("?channel=tiktokshop", { ml: true, tiktok: false }), "shopee");
  assert.equal(lerCanalExpedicao("?channel=mercadolivre", { ml: false, tiktok: true }), "shopee");
});

test("a troca entre os dois canais da fila nao toca na janela nem na visao", () => {
  const u = new URL(urlDoCanal(`${BASE}?channel=mercadolivre&dias=10&visao=detalhe`, "mercadolivre", "shopee"));
  assert.equal(u.searchParams.has("channel"), false);
  assert.equal(u.searchParams.get("dias"), "10");
  assert.equal(u.searchParams.get("visao"), "detalhe");
});

// ===========================================================================
// 2. Profundidade
// ===========================================================================
test("a visao do gestor e' o padrao e nao suja a URL", () => {
  assert.equal(VISAO_PADRAO, "gestor");
  assert.equal(lerVisao(""), "gestor");
  assert.equal(lerVisao("?visao=detalhe"), "detalhe");
  for (const v of ["DETALHE", "operacional", "", "1"]) {
    assert.equal(lerVisao(`?visao=${v}`), "gestor", v);
  }
  const detalhe = new URL(urlComVisao(`${BASE}?channel=tiktokshop&dias=30`, "detalhe"));
  assert.equal(detalhe.searchParams.get("visao"), "detalhe");
  assert.equal(detalhe.searchParams.get("dias"), "30", "a janela do painel nao e' tocada");
  const volta = new URL(urlComVisao(detalhe.toString(), "gestor"));
  assert.equal(volta.searchParams.has("visao"), false);
  assert.equal(volta.searchParams.get("channel"), "tiktokshop");
});

test("a casca le canal E visao na montagem e no voltar/avancar", () => {
  const leitor = CASCA.slice(CASCA.indexOf("const lerDaUrl"), CASCA.indexOf("}, []);", CASCA.indexOf("const lerDaUrl")));
  assert.match(leitor, /lerCanalExpedicao\(window\.location\.search, FLAGS\)/);
  assert.match(leitor, /lerVisao\(window\.location\.search\)/);
  assert.match(CASCA, /const aoVoltar = \(\) => lerDaUrl\(\);/);
  assert.match(CASCA, /window\.history\.replaceState\(null, "", urlComVisao\(window\.location\.href, nova\)\)/);
  // Os paineis nao leem a profundidade sozinhos: uma fonte de verdade so'.
  for (const f of [CLIENTE, TIKTOK]) {
    assert.doesNotMatch(f, /lerVisao|PARAM_VISAO|get\("visao"\)/);
  }
});

test("trocar de canal pelo teclado nao derruba o foco na travessia fila <-> TikTok", () => {
  // Medido no QA: o corpo troca inteiro, o seletor renasce no outro painel e
  // o foco ia para o `body`. A casca lembra se o foco estava no seletor e o
  // devolve a opcao marcada depois da troca.
  assert.match(CASCA, /devolverFoco\.current = Boolean\(seletorRef\.current\?\.contains\(document\.activeElement\)\);/);
  assert.match(CASCA, /<span ref=\{seletorRef\} className="contents">/);
  assert.match(CASCA, /querySelector<HTMLButtonElement>\('\[role="radio"\]\[aria-checked="true"\]'\)\s*\?\.focus\(\);/);
});

test("a casca nao busca nada e so' monta o corpo do canal certo", () => {
  assert.ok(!CASCA.includes("fetch("));
  assert.match(CASCA, /ehCanalDaFila\(canal\) \? \(\s*<ExpedicaoClient/);
  assert.match(CASCA, /<TikTokDispatchPanel\s+seletorCanal=\{seletorCanal\}/);
  // um canal so' => sem seletor: cromo vazio nao ajuda ninguem
  assert.match(CASCA, /opcoes\.length > 1 \?/);
});

// ===========================================================================
// 3. "O que exige atenção agora" da fila — contagens da API em frase
// ===========================================================================
function totais(over: Partial<Totais> = {}): Totais {
  return {
    backlog_count: 100,
    overdue_count: 0,
    due_within_24h_count: 0,
    on_time_count: 100,
    deadline_unavailable_count: 0,
    over_48h_count: 0,
    slow_count: 0,
    zombie_count: 0,
    stalled_count: 0,
    ...over,
  };
}

function conta(over: Partial<ResumoConta>): ResumoConta {
  return {
    shop_account: "apice",
    brand: "apice",
    run_status: "success",
    source_watermark_at: null,
    source_advanced: true,
    snapshot_hour: "2026-10-21T10:00:00Z",
    ...totais(),
    ...over,
  };
}

test("Shopee: vencidos vem primeiro, com a conta que mais concentra", () => {
  const itens = montarAtencaoDaFila(
    totais({ overdue_count: 24, due_within_24h_count: 329, stalled_count: 3 }),
    [
      conta({ shop_account: "apice", brand: "apice", overdue_count: 5 }),
      conta({ shop_account: "barbours", brand: "barbours", overdue_count: 19 }),
    ],
    "shopee",
  );
  assert.deepEqual(itens.map((i) => i.chave), ["overdue", "due24h", "stalled"]);
  assert.equal(itens[0].tom, "critico");
  assert.match(itens[0].texto, /^24 pedido\(s\) já passaram do prazo da Shopee/);
  assert.match(itens[0].texto, /Maior concentração: BARBOURS, com 19\./);
  assert.match(itens[1].texto, /^329 pedido\(s\) vencem nas próximas 24 h/);
});

test("Mercado Livre: nunca pergunta por prazo, e 48h e' limiar interno", () => {
  const itens = montarAtencaoDaFila(
    totais({ overdue_count: 7, due_within_24h_count: 3, over_48h_count: 169 }),
    [conta({ shop_account: "2227056661", brand: "kokeshi", over_48h_count: 169 })],
    "mercadolivre",
  );
  // `overdue` do ML e' zero/lixo por AUSENCIA de prazo: nunca vira frase.
  assert.deepEqual(itens.map((i) => i.chave), ["over48h"]);
  for (const i of itens) {
    assert.doesNotMatch(i.texto, /vencid|prazo da Shopee|SLA/i, i.texto);
  }
  assert.match(itens[0].texto, /limiar interno da Torre, não prazo do marketplace/);
  // EXPEDICAO-UX-3: a marca e' a identidade da frase; o seller_id so'
  // aparece quando a marca tem mais de uma conta.
  assert.match(itens[0].texto, /Maior concentração: KOKESHI, com 169\./);
  assert.doesNotMatch(itens[0].texto, /2227056661/);
});

test("nada aceso nao vira 'tudo certo' em verde", () => {
  for (const canal of ["shopee", "mercadolivre"] as const) {
    const itens = montarAtencaoDaFila(totais(), [], canal);
    assert.equal(itens.length, 1);
    assert.equal(itens[0].tom, "neutro", "fotografia pode estar velha: sem verde");
    assert.match(itens[0].texto, /^Nesta fotografia/);
  }
  assert.doesNotMatch(montarAtencaoDaFila(totais(), [], "mercadolivre")[0].texto, /vencid/);
});

test("sem totais nao ha frase — ausencia nao vira zero", () => {
  assert.deepEqual(montarAtencaoDaFila(null, [], "shopee"), []);
});

test("concentracao e' selecao, nao soma: empate fica com a primeira da API", () => {
  const itens = montarAtencaoDaFila(
    totais({ stalled_count: 10 }),
    [
      conta({ shop_account: "a", brand: "a", stalled_count: 5 }),
      conta({ shop_account: "b", brand: "b", stalled_count: 5 }),
    ],
    "shopee",
  );
  assert.match(itens[0].texto, /Maior concentração: A, com 5\./);
  assert.doesNotMatch(itens[0].texto, /com 10\./, "nenhuma conta e' somada a outra");
});

test("a fotografia da fila repete a idade e o veredito que ja' existiam", () => {
  const f = fotografiaDaFila(
    {
      snapshotAgeHours: 30,
      snapshotVelho: true,
      efetivoEm: "2026-10-13T12:00:00Z",
    },
  );
  assert.equal(f.idade, "30.0 h");
  assert.equal(f.desatualizada, true);
  assert.ok(!("automacao" in f), "sem modo de publicacao: a API ainda nao o prova");
  const nada = fotografiaDaFila(null);
  assert.equal(nada.publicadaEm, null);
  assert.equal(nada.idade, null);
  assert.equal(nada.desatualizada, false);
});

// ===========================================================================
// 4. TikTok: alertas da API, barra de qualidade, serie vencida
// ===========================================================================
function dia(p: Partial<TikTokLdrDia> & { due_date: string }): TikTokLdrDia {
  return {
    base: 100,
    late: 0,
    pending_overdue: 0,
    pending_on_time: 0,
    rate: 0.01,
    is_mature: true,
    above_target: false,
    is_critical: false,
    ...p,
  };
}

function payload(over: Partial<TikTokPayload> = {}): TikTokPayload {
  return {
    availability: "available",
    unavailable_reason: null,
    channel: "tiktokshop",
    event: "coleta",
    deadline_is_reconstructed: true,
    sla_business_days: { coleta: 2, despacho: 1 },
    targets: { tiktok_ldr: 0.04, internal_critical: 0.1 },
    snapshot: {
      refresh_batch_id: "b",
      effective_at: "2026-10-13T12:00:00Z",
      source_watermark_at: "2026-10-13T11:00:00Z",
      today_brt: "2026-10-21",
    },
    ldr: {
      from: "2026-10-14",
      to: "2026-10-21",
      days: 7,
      rate: 0.05,
      base: 100,
      late: 5,
      pending_overdue: 1,
      pending_on_time: 3,
      pending_at_risk: 2,
      mature_due_days: 6,
      partial_due_days: 2,
      above_target: true,
      internal_critical: false,
      incident_start: null,
      incident_end: null,
      daily: [dia({ due_date: "2026-10-14" })],
    },
    payment_flow: [],
    brands: [],
    alerts: [
      { severity: "critical", code: "fora_da_meta_tiktok", message: "Acima da meta — medição interna: LDR de 5,00%." },
      { severity: "warning", code: "em_risco", message: "2 pedido(s) vencem hoje ou amanhã e ainda podem ser tratados." },
      { severity: "warning", code: CODIGO_FOTOGRAFIA_VELHA, message: "Última atualização há 192 h." },
    ],
    ...over,
  };
}

test("atencao do TikTok sao os alertas da API, com o texto e a ordem dela", () => {
  const p = payload();
  const itens = montarAtencaoDoTikTok(p);
  assert.deepEqual(itens.map((i) => i.chave), ["fora_da_meta_tiktok", "em_risco"]);
  assert.equal(itens[0].texto, p.alerts[0].message, "texto da API sem reescrita");
  assert.deepEqual(itens.map((i) => i.tom), ["critico", "atencao"]);
  // O alerta de frescor descreve o DADO, nao a operacao: vai para a barra.
  assert.ok(!itens.some((i) => i.chave === CODIGO_FOTOGRAFIA_VELHA));
  assert.deepEqual(montarAtencaoDoTikTok(null), []);
});

test("a barra do TikTok substitui a pilha: vencida primeiro, prazo nunca some", () => {
  const barra = montarBarraDeQualidadeTikTok(payload());
  assert.equal(barra.principal?.chave, "serie_desatualizada");
  assert.match(barra.principal?.texto ?? "", /Última atualização há 192 h\./);
  const chaves = barra.detalhes.map((d) => d.chave);
  assert.deepEqual(chaves, [
    "serie_desatualizada",
    "prazo_reconstruido",
    "vencimentos_parciais",
  ]);
  assert.equal(
    barra.detalhes.find((d) => d.chave === "prazo_reconstruido")?.texto,
    AVISO_PRAZO_RECONSTRUIDO,
  );
});

test("sem serie vencida, o aviso de prazo e' o que aparece fechado", () => {
  const barra = montarBarraDeQualidadeTikTok(
    payload({ alerts: payload().alerts.filter((a) => a.code !== CODIGO_FOTOGRAFIA_VELHA) }),
  );
  assert.equal(barra.principal?.chave, "prazo_reconstruido");
});

test("nenhuma barra afirma publicacao manual, agendada ou ausente", () => {
  // EXPEDICAO-UX-UNIFICADA-1: o modo de publicacao saiu da tela nos tres
  // canais ate' a API provar a origem do batch.
  for (const p of [payload(), payload({ availability: "unavailable", ldr: null, alerts: [] })]) {
    for (const d of montarBarraDeQualidadeTikTok(p).detalhes) {
      assert.doesNotMatch(d.texto, /à mão|a mao|agendad|agendamento|automa/i, d.texto);
    }
  }
  assert.equal(montarBarraDeQualidadeTikTok(null).principal, null);
});

test("a tela nao calcula a idade da serie do TikTok", () => {
  const f = fotografiaDoTikTok(payload());
  assert.equal(f?.idade, null, "a API nao publica idade; a tela nao subtrai relogios");
  assert.equal(f?.desatualizada, true, "o veredito e' o alerta da API");
  assert.ok(f && !("automacao" in f));
  assert.equal(f?.fonteLidaAte, "2026-10-13T11:00:00Z");
  const fresca = fotografiaDoTikTok(payload({ alerts: [] }));
  assert.equal(fresca?.desatualizada, false);
  assert.equal(fotografiaDoTikTok(payload({ snapshot: null })), null);
  // nada de relogio no painel nem no contrato
  for (const f2 of [TIKTOK, fonte("src/lib/expedicao-tiktok-contract.ts")]) {
    assert.doesNotMatch(f2, /Date\.now\(\)/);
  }
});

test("so' ha grafico quando algum vencimento esta' fechado com taxa", () => {
  assert.equal(serieTemDiaMedido([]), false);
  assert.equal(serieTemDiaMedido([dia({ due_date: "a", is_mature: false, rate: 0.5 })]), false);
  assert.equal(serieTemDiaMedido([dia({ due_date: "a", rate: null })]), false);
  assert.equal(serieTemDiaMedido([dia({ due_date: "a", rate: 0 })]), true, "0% fechado e' medicao");
});

test("a consolidacao mantem a ordem do contrato no empate", () => {
  const barra = consolidarQualidade([
    { chave: "a", severidade: "info", texto: "a" },
    { chave: "b", severidade: "atencao", texto: "b" },
    { chave: "c", severidade: "atencao", texto: "c" },
    { chave: "d", severidade: "alerta", texto: "d" },
  ]);
  assert.deepEqual(barra.detalhes.map((d) => d.chave), ["d", "b", "c", "a"]);
  assert.equal(barra.severidade, "alerta");
});

// ===========================================================================
// 5. Duas profundidades, corpos separados
// ===========================================================================
function ramos(src: string): { gestor: string; detalhe: string } {
  const ini = src.indexOf('visao === "gestor" ? (');
  assert.ok(ini > 0, "falta a bifurcacao por visao");
  const meio = src.indexOf(") : (", ini);
  const fim = src.indexOf("</div>\n  );", meio) > 0 ? src.indexOf("</div>\n  );", meio) : src.length;
  return { gestor: src.slice(ini, meio), detalhe: src.slice(meio, fim) };
}

test("fila: o gestor ve narrativa e concentracao; o detalhe ve a fila", () => {
  const { gestor, detalhe } = ramos(CLIENTE.replace(/\r\n/g, "\n"));
  // EXPEDICAO-UX-3: risco atual (com a lista de atencao dentro), prazo,
  // evolucao e concentracao — a mesma ordem do TikTok.
  for (const s of ["{secaoRisco}", "{secaoPrazo}", "{painelEvolucao}", "painelContas(", "<ConviteAoDetalhe"]) {
    assert.ok(gestor.includes(s), `gestor sem ${s}`);
  }
  assert.ok(
    gestor.indexOf("{secaoRisco}") < gestor.indexOf("{secaoPrazo}") &&
      gestor.indexOf("{secaoPrazo}") < gestor.indexOf("{painelEvolucao}") &&
      gestor.indexOf("{painelEvolucao}") < gestor.indexOf("painelContas("),
    "ordem: risco, prazo, evolucao, concentracao",
  );
  assert.match(CLIENTE, /const secaoRisco = \([\s\S]*?<AtencaoAgora\s+itens=\{atencao\}/);
  // marca de varias contas: a lista vazia diz POR QUE esta' vazia
  assert.match(CLIENTE, /a leitura de atenção fica por conta/);
  assert.ok(!gestor.includes("{painelFila}"), "a fila paginada nao e' visao do gestor");
  for (const s of ["{faixaPrincipal}", "Indicadores secundários", "{painelMaisCriticos}", "{painelFila}", "{painelFonte}", "painelContas("]) {
    assert.ok(detalhe.includes(s), `detalhe sem ${s}`);
  }
});

test("TikTok: LDR no gestor, fluxo por pagamento so' no detalhe", () => {
  const { gestor, detalhe } = ramos(TIKTOK.replace(/\r\n/g, "\n"));
  for (const s of ["{secaoRisco}", "{secaoLdr}", "{painelEvolucao}", "painelLojas(", "<ConviteAoDetalhe"]) {
    assert.ok(gestor.includes(s), `gestor sem ${s}`);
  }
  assert.ok(
    gestor.indexOf("{secaoRisco}") < gestor.indexOf("{secaoLdr}") &&
      gestor.indexOf("{secaoLdr}") < gestor.indexOf("{painelEvolucao}") &&
      gestor.indexOf("{painelEvolucao}") < gestor.indexOf("painelLojas("),
    "mesma ordem da fila: risco, prazo, evolucao, concentracao",
  );
  assert.match(TIKTOK, /const secaoRisco = ldr \? \([\s\S]*?<AtencaoAgora itens=\{atencao\} \/>/);
  // LDR e fluxo por pagamento nao dividem a primeira dobra.
  assert.ok(!gestor.includes("painelFluxo("));
  assert.ok(!gestor.includes("{painelCoortes}"));
  for (const s of ["{secaoLdr}", "{painelCoortes}", "painelLojas(", "painelFluxo("]) {
    assert.ok(detalhe.includes(s), `detalhe sem ${s}`);
  }
  // e o fluxo continua NOMEADO como visao auxiliar
  assert.match(TIKTOK, /visão auxiliar · não é a LDR/);
});

test("nenhuma fila do TikTok foi inventada", () => {
  for (const proibido of ["queue", "pagination", "Fila operacional", "montarTimeline"]) {
    assert.ok(!TIKTOK.includes(proibido), proibido);
  }
});

test("o TikTok nao empilha mais banners antes do primeiro numero", () => {
  const codigo = TIKTOK.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  assert.doesNotMatch(codigo, /alerts\.map/, "um banner por alerta");
  assert.doesNotMatch(codigo, /AVISO_PRAZO_RECONSTRUIDO/, "aviso solto fora da barra");
  assert.match(TIKTOK, /<BarraDeQualidade barra=\{barra\} \/>/);
  assert.match(TIKTOK, /<AtencaoAgora itens=\{atencao\}/);
});

test("serie sem vencimento fechado troca o grafico por uma linha", () => {
  assert.match(TIKTOK, /\{temDiaMedido \? \(\s*<Grafico dias=\{ldr\.daily\} \/>\s*\) : \(/);
  assert.match(TIKTOK, /Nenhum vencimento da janela tem taxa fechada para desenhar\./);
});

test("LDR sem taxa nao e' pintada de verde", () => {
  assert.match(TIKTOK, /tom=\{ldr\.rate === null \? "ausente" : ldr\.above_target \? "critico" : "bom"\}/);
});

test("sem vencimento fechado, base e atrasados sao ausencia, nao zero", () => {
  // `ldr.base` e `ldr.late` somam so' coortes maduras: sem nenhuma, valem 0
  // por construcao. A tela mostra o marcador de ausencia e o tom `ausente`.
  assert.match(TIKTOK, /const semVencimentoFechado = ldr \? ldr\.mature_due_days === 0 : false;/);
  assert.match(TIKTOK, /valor=\{semVencimentoFechado \? VALOR_SEM_TAXA : formatarInteiro\(ldr\.base\)\}/);
  assert.match(TIKTOK, /valor=\{semVencimentoFechado \? VALOR_SEM_TAXA : formatarInteiro\(ldr\.late\)\}/);
  assert.match(TIKTOK, /tom=\{semVencimentoFechado \? "ausente" : ldr\.late > 0 \? "critico" : "bom"\}/);
});

test("lojas sem base nao desenham tabela vazia", () => {
  assert.match(TIKTOK, /sortedRows\.length === 0 \? \(/);
  assert.match(TIKTOK, /Nenhuma loja com base suficiente de vencimentos fechados no período\./);
});

test("erro, ausencia e serie vencida tem estados diferentes no TikTok", () => {
  assert.match(TIKTOK, /<StateBlock\s+tipo="erro"/);
  assert.match(TIKTOK, /<StateBlock\s+tipo="indisponivel"/);
  assert.match(TIKTOK, /<RetryButton onClick=\{\(\) => void carregar\(\)\} \/>/);
  // vencida nao e' estado de bloqueio: e' selo + barra, com os numeros
  assert.match(TIKTOK, /fotografiaDoTikTok\(dados\)/);
});

test("a fila diz de quando e' o 'agora' e encolhe a tendencia vazia", () => {
  assert.match(CLIENTE, /Segundo a fotografia de \$\{dataCurta\(relogios\.efetivoEm\)\}/);
  assert.match(CLIENTE, /<p role="status" className="py-2 text-sm text-ink-muted">\s*Sem pontos na janela escolhida\./);
  assert.doesNotMatch(CLIENTE, /py-12 text-center text-sm text-ink-muted">\s*Sem pontos/);
});

// ===========================================================================
// 6. Renderizacao dos componentes compartilhados
// ===========================================================================
const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: CabecalhoExpedicao, ChipsFotografia } = await import(
  "../src/components/expedicao/CabecalhoExpedicao.tsx"
);
const { default: BarraDeQualidade } = await import("../src/components/expedicao/BarraDeQualidade.tsx");
const { AtencaoAgora, ConviteAoDetalhe } = await import(
  "../src/components/expedicao/SecoesExpedicao.tsx"
);

test("o cabecalho poe o canal no h2 e a profundidade num radiogroup", () => {
  const html = renderToStaticMarkup(
    h(CabecalhoExpedicao, {
      canal: "Mercado Livre",
      fotografia: null,
      visao: "detalhe",
      aoTrocarVisao: () => {},
    }),
  );
  assert.match(html, /<h2[^>]*>Expedição — Mercado Livre<\/h2>/);
  assert.doesNotMatch(html, /<h1/);
  assert.match(html, /role="radiogroup"/);
  assert.match(html, /aria-checked="true"[^>]*>.*Detalhe operacional/);
  assert.match(html, /id="exp-controles"/);
});

test("fotografia desatualizada e' ESCRITA, nao so' pintada", () => {
  const velha = renderToStaticMarkup(
    h(ChipsFotografia, {
      fotografia: {
        rotulo: "Série",
        publicadaEm: "2026-10-13T12:00:00Z",
        idade: null,
        desatualizada: true,
        fonteLidaAte: null,
      },
    }),
  );
  assert.match(velha, />Desatualizada</);
  assert.doesNotMatch(velha, /há /, "sem idade publicada, sem idade na tela");
  const fresca = renderToStaticMarkup(
    h(ChipsFotografia, {
      fotografia: {
        rotulo: "Fotografia",
        publicadaEm: "2026-10-21T12:00:00Z",
        idade: "1.2 h",
        desatualizada: false,
      },
    }),
  );
  assert.doesNotMatch(fresca, /Desatualizada|não agora/);
  assert.match(fresca, /há 1\.2 h/);
  // Selos so' do que a API mede: instante, idade, fonte. Nenhum modo.
  for (const html of [velha, fresca]) {
    assert.doesNotMatch(html, /à mão|agendamento|Modo de publicação|manual/i);
  }
});

test("a barra mostra o principal e anuncia quantas limitacoes ha", () => {
  const html = renderToStaticMarkup(
    h(BarraDeQualidade, { barra: montarBarraDeQualidadeTikTok(payload()) }),
  );
  assert.match(html, /<section aria-label="Qualidade do dado">/);
  assert.match(html, /Série desatualizada\./);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /aria-controls="[^"]+"/);
  assert.match(html, /Ver 3 limitações/);
  assert.match(html, /min-h-11/);
  assert.equal(
    renderToStaticMarkup(h(BarraDeQualidade, { barra: consolidarQualidade([]) })),
    "",
    "sem problema, sem barra",
  );
});

test("atencao: severidade escrita, uma linha por item, sem banner", () => {
  const html = renderToStaticMarkup(
    h(AtencaoAgora, {
      itens: montarAtencaoDoTikTok(payload()),
      descricao: "Segundo a série publicada em 13/10/2026.",
    }),
  );
  assert.match(html, /<h3[^>]*>O que exige atenção agora<\/h3>/);
  assert.match(html, /Crítico/);
  assert.match(html, /Atenção/);
  assert.equal((html.match(/<li\b/g) ?? []).length, 2);
  assert.doesNotMatch(html, /role="alert"/, "atencao nao interrompe o leitor de tela");
  const vazio = renderToStaticMarkup(h(AtencaoAgora, { itens: [] }));
  assert.match(vazio, /Nenhum alerta publicado/);
});

test("o convite ao detalhe e' um botao de 44px", () => {
  const html = renderToStaticMarkup(
    h(ConviteAoDetalhe, { conteudo: "Fila no detalhe.", aoAbrir: () => {} }),
  );
  assert.match(html, /<button[^>]*type="button"[^>]*class="[^"]*min-h-11[^"]*"[^>]*>Abrir detalhe operacional<\/button>/);
});

// ===========================================================================
// 7. Nenhuma afirmacao de publicacao manual (a DAG publica desde 21/10/2026)
// ===========================================================================
test("nenhum arquivo da Expedicao afirma atualizacao manual ou modo de publicacao", () => {
  // A API ainda devolve `load_mode="manual_snapshot"` e `no_automation=true`
  // como constantes. Ate' o contrato provar a origem do batch, a tela so'
  // mostra instante, idade, fonte e frescor. Comentarios ficam de fora: eles
  // EXPLICAM a remocao.
  const arquivos = [
    "app/expedicao/ExpedicaoShell.tsx",
    "app/expedicao/ExpedicaoClient.tsx",
    "src/components/expedicao/TikTokDispatchPanel.tsx",
    "src/components/expedicao/CabecalhoExpedicao.tsx",
    "src/components/expedicao/BarraDeQualidade.tsx",
    "src/components/expedicao/SecoesExpedicao.tsx",
    "src/lib/expedicao-contract.ts",
    "src/lib/expedicao-tiktok-contract.ts",
    "src/lib/expedicao-ux.ts",
  ];
  for (const rel of arquivos) {
    const codigo = fonte(rel).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    assert.doesNotMatch(
      codigo,
      // `load_mode` continua nos TIPOS que espelham o payload; o que nao pode
      // existir e' texto de tela ou campo derivado dele.
      /à mão|a mao|sem agendamento|Sem automa|manualmente|Modo de publicação|modoDeCarga|semAutomacao/,
      rel,
    );
  }
});

// ===========================================================================
// T-0010 — esqueleto comum (LayoutExpedicao) nos tres canais
// ===========================================================================
const { CorpoGestor, SecaoIndisponivel, EstadoSecao, CLASSE_RAIZ_EXPEDICAO } = await import(
  "../src/components/expedicao/LayoutExpedicao.tsx"
);

test("o corpo do gestor tem ordem fixa: risco, prazo, jornada, evolucao, concentracao, convite", () => {
  const html = renderToStaticMarkup(
    h(CorpoGestor, {
      // fora de ordem de proposito: quem manda e' o esqueleto
      convite: h("i", null, "6"),
      concentracao: h("i", null, "5"),
      evolucao: h("i", null, "4"),
      jornada: h("i", null, "3"),
      prazo: h("i", null, "2"),
      risco: h("i", null, "1"),
    }),
  );
  assert.equal(html, "<i>1</i><i>2</i><i>3</i><i>4</i><i>5</i><i>6</i>");
});

test("os dois corpos usam o mesmo esqueleto, a mesma raiz e a mesma tabela", () => {
  for (const f of [CLIENTE, TIKTOK]) {
    assert.match(f, /<div className=\{CLASSE_RAIZ_EXPEDICAO\} aria-busy=/);
    assert.match(f, /<LayoutExpedicao\b/);
    assert.match(f, /<CorpoGestor\b/);
    assert.doesNotMatch(f, /const CLASSE_TABELA\s*=/, "CLASSE_TABELA so' no esqueleto");
    assert.doesNotMatch(f, /const CLASSE_CABECALHO\s*=/);
  }
  assert.match(CLASSE_RAIZ_EXPEDICAO, /max-w-\[1400px\]/);
});

test("estados da fila sao os mesmos componentes do TikTok, com nova tentativa", () => {
  assert.match(CLIENTE, /<StateBlock tipo="carregando"/);
  assert.match(CLIENTE, /<RetryButton onClick=\{tentarDeNovo\} \/>/);
  assert.match(CLIENTE, /const tentarDeNovo = useCallback\(\(\) => setFiltros\(\(f\) => \(\{ \.\.\.f \}\)\), \[\]\);/);
  assert.match(TIKTOK, /<StateBlock tipo="carregando"/);
});

test("secao sem fonte no canal: bloco curto 'indisponível neste canal' com o ?", () => {
  const html = renderToStaticMarkup(
    h(SecaoIndisponivel, {
      titulo: "Prazo",
      motivo: "o Mercado Livre não publica prazo de despacho.",
      ajuda: "mercadolivre.prazo",
    }),
  );
  assert.match(html, /<h3[^>]*>Prazo<\/h3>/);
  assert.match(html, /Indisponível neste canal: o Mercado Livre não publica prazo de despacho\./);
  assert.match(html, /aria-label="O que é Prazo e LDR no Mercado Livre"/);
  assert.match(html, /role="status"/);
});

test("estado de secao: parcial e desatualizado sao ressalvas; erro tem nova tentativa", () => {
  assert.match(renderToStaticMarkup(h(EstadoSecao, { tipo: "parcial", titulo: "etapas não fecham." })), /Parcial:/);
  assert.match(renderToStaticMarkup(h(EstadoSecao, { tipo: "desatualizado", titulo: "x" })), /Desatualizada:/);
  const erro = renderToStaticMarkup(h(EstadoSecao, { tipo: "erro", titulo: "Falhou.", aoTentarDeNovo: () => {} }));
  assert.match(erro, /role="alert"/);
  assert.match(erro, /Tentar novamente/);
  assert.match(renderToStaticMarkup(h(EstadoSecao, { tipo: "carregando", titulo: "Carregando…" })), /aria-busy="true"/);
});
