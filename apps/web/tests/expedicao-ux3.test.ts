/**
 * Gate EXPEDICAO-UX-3 — a mesma hierarquia gerencial nos tres canais, e o
 * filtro de marca.
 *
 * O que erra em silencio aqui: o filtro de marca mostrando o TOTAL DO CANAL
 * sob o nome de uma marca (a API da fila nao recorta `totals`); uma marca de
 * duas contas somada no frontend; o Mercado Livre ganhando "vencidos" ou LDR;
 * a Shopee apresentando a particao por prazo como se fosse LDR; e "Fonte
 * avançou: não" lido como falha.
 */
import { register } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  DEFINICAO_FILA,
  KPIS_RISCO_ATUAL,
  SEM_LDR_SHOPEE,
  SEM_PRAZO_CANAL_ML,
  envelhecimentoDaFila,
  faixasDePrazo,
  montarAtencaoDaFila,
  montarRiscoAtual,
  recortarPorMarca,
  rotuloFonte,
  type FrescorMarca,
  type RespostaExpedicao,
  type ResumoConta,
  type Totais,
} from "../src/lib/expedicao-contract.ts";
import {
  DEFINICAO_TIKTOK,
  explicarIndisponivelComMarca,
  queryDoTikTok,
} from "../src/lib/expedicao-tiktok-contract.ts";
import { comMarcaNaQuery, lerMarca, urlComMarca } from "../src/lib/expedicao-ux.ts";
import { urlDoCanal } from "../src/lib/expedicao-canal.ts";

register("./helpers/tsx-loader.mjs", import.meta.url);

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8").replace(/\r\n/g, "\n");
const CLIENTE = fonte("app/expedicao/ExpedicaoClient.tsx");
const TIKTOK = fonte("src/components/expedicao/TikTokDispatchPanel.tsx");
const CASCA = fonte("app/expedicao/ExpedicaoShell.tsx");
const BASE = "https://torre.example/expedicao";

function totais(over: Partial<Totais> = {}): Totais {
  return {
    backlog_count: 100, overdue_count: 10, due_within_24h_count: 20, on_time_count: 65,
    deadline_unavailable_count: 5, over_48h_count: 30, slow_count: 4, zombie_count: 1,
    stalled_count: 5, ...over,
  };
}
function conta(shop: string, brand: string, over: Partial<Totais> = {}): ResumoConta {
  return {
    shop_account: shop, brand, run_status: "success", source_watermark_at: null,
    source_advanced: true, snapshot_hour: "2026-10-21T17:00:00Z", ...totais(over),
  };
}
function frescor(brand: string, oldest: number | null): FrescorMarca {
  return {
    brand, freshness: "fresh", status: "pass", severity: "low", source_watermark_at: null,
    source_age_hours: 1, oldest_row_age_hours: oldest, accounts: 1, observed_at: null,
    measures: null, derived_from_batch: false,
  };
}
function resposta(contas: ResumoConta[], fr: FrescorMarca[] = [], naoCobertas: string[] = []): RespostaExpedicao {
  return {
    availability: "available", unavailable_reason: null, channel: "mercadolivre", snapshot: null,
    totals: totais({ backlog_count: 999 }), accounts: contas, freshness: fr,
    coverage: { expected_accounts: [], observed_accounts: [], missing_accounts: [], unexpected_accounts: [], accounts: [], brands_not_covered: naoCobertas },
    queue: [], pagination: null,
    limitations: { no_automation: true, load_mode: "manual_snapshot", snapshot_age_hours: 1, brands_not_covered: naoCobertas, order_identifier_withheld: true, notes: [] },
  };
}

// ===========================================================================
// 1. Filtro de marca na URL e nas requisicoes
// ===========================================================================
test("`?marca=` aceita so' slug e atravessa a troca de canal", () => {
  assert.equal(lerMarca("?marca=barbours"), "barbours");
  for (const v of ["BARBOURS", "<script>", "", "a b", "x".repeat(41)]) {
    assert.equal(lerMarca(`?marca=${encodeURIComponent(v)}`), null, v);
  }
  const u = new URL(urlComMarca(`${BASE}?channel=mercadolivre`, "kokeshi"));
  assert.equal(u.searchParams.get("marca"), "kokeshi");
  assert.equal(new URL(urlComMarca(u.toString(), null)).searchParams.has("marca"), false);
  // a troca de canal mexe so' em channel/dias: a marca fica
  const tk = new URL(urlDoCanal(`${BASE}?marca=kokeshi&dias=10`, "shopee", "tiktokshop"));
  assert.equal(tk.searchParams.get("marca"), "kokeshi");
});

test("a marca vai para a API como `brands` — a tela nao filtra serie sozinha", () => {
  assert.equal(comMarcaNaQuery("window_hours=72", "lescent"), "window_hours=72&brands=lescent");
  assert.equal(comMarcaNaQuery("window_hours=72", null), "window_hours=72");
  assert.equal(queryDoTikTok("coleta", 7, "kokeshi"), "/api/v1/expedicao/tiktok?event=coleta&days=7&brands=kokeshi");
  assert.equal(queryDoTikTok("coleta", 7), "/api/v1/expedicao/tiktok?event=coleta&days=7");
  assert.match(CLIENTE, /comMarcaNaQuery\(queryDaTendenciaEmDias\(janela, filtros\.channel\), marcaAtiva\)/);
  assert.match(CLIENTE, /aplicarFiltro\(f, \{ brands: alvo \}\)/);
  assert.match(TIKTOK, /queryDoTikTok\(evento, dias, marca\)/);
});

test("a casca le a marca na montagem e no voltar/avancar e a repassa aos dois corpos", () => {
  assert.match(CASCA, /setMarca\(lerMarca\(window\.location\.search\)\)/);
  assert.match(CASCA, /urlComMarca\(window\.location\.href, nova\)/);
  assert.equal((CASCA.match(/marca=\{marca\}/g) ?? []).length, 2);
});

// ===========================================================================
// 2. Recorte por marca da fila: SELECAO, nunca soma
// ===========================================================================
test("sem marca: os totais do canal, como a API publicou", () => {
  const r = resposta([conta("1", "barbours")]);
  assert.equal(recortarPorMarca(r, null).totais?.backlog_count, 999);
});

test("marca de uma conta: a linha daquela conta, nao o total do canal", () => {
  const r = resposta([conta("1", "barbours", { backlog_count: 120 }), conta("2", "kokeshi", { backlog_count: 80 })]);
  const rec = recortarPorMarca(r, "kokeshi");
  assert.equal(rec.totais?.backlog_count, 80, "a API nao recorta totals: o canal tem 999");
  assert.deepEqual(rec.contas.map((c) => c.shop_account), ["2"]);
  assert.equal(rec.variasContas, false);
});

test("marca de duas contas: SEM total da marca (a tela nao soma contas)", () => {
  const r = resposta([conta("1", "barbours", { backlog_count: 120 }), conta("2", "barbours", { backlog_count: 30 })]);
  const rec = recortarPorMarca(r, "barbours");
  assert.equal(rec.totais, null);
  assert.equal(rec.variasContas, true);
  assert.equal(rec.contas.length, 2);
  assert.doesNotMatch(fonte("src/lib/expedicao-contract.ts").split("export function recortarPorMarca")[1].split("export ")[0], /reduce|\+=/);
  // e os KPIs viram ausencia, nunca zero
  for (const k of montarRiscoAtual(rec.totais, rec.frescor, "shopee")) {
    if (k.chave !== "marcas_alerta") assert.equal(k.valor, null, k.chave);
  }
});

test("marca fora da cobertura: ausencia, nao backlog zero", () => {
  const rec = recortarPorMarca(resposta([conta("1", "apice")], [], ["kokeshi"]), "kokeshi");
  assert.equal(rec.semConta, true);
  assert.equal(rec.foraDaCobertura, true);
  assert.equal(rec.totais, null);
  assert.match(CLIENTE, /não é coberta pela fonte deste canal — não é backlog zero\./);
});

// ===========================================================================
// 3. Risco atual: so' o que o canal sustenta
// ===========================================================================
// EXPEDICAO-VENCE-AMANHA: o proximo dia de prazo entrou depois de "vence em 24 h".
test("Shopee: sete indicadores, com vencidos, vence em 24 h e o proximo dia de prazo", () => {
  assert.deepEqual(
    [...KPIS_RISCO_ATUAL.shopee],
    ["backlog", "overdue", "due24h", "due_next_day", "over48h", "stalled", "marcas_alerta"],
  );
  assert.equal(montarRiscoAtual(totais(), [], "shopee").length, 7);
});

test("Mercado Livre: nenhum vencido, nenhum 'vence em 24 h'", () => {
  const ml = montarRiscoAtual(totais(), [], "mercadolivre").map((k) => k.chave);
  assert.deepEqual(ml, ["backlog", "over48h", "stalled", "marcas_alerta"]);
  assert.ok(!ml.includes("overdue") && !ml.includes("due24h"));
});

test("acima de 48 h e' limiar interno nos dois canais, nunca SLA", () => {
  for (const canal of ["shopee", "mercadolivre"] as const) {
    const k = montarRiscoAtual(totais(), [], canal).find((x) => x.chave === "over48h")!;
    assert.match(k.rotulo, /limiar interno/i);
    assert.match(String(k.nota), /não é SLA/i);
  }
});

test("a nota de 48 h do Mercado Livre nao fala de vencidos nem da Shopee", () => {
  // Achado do QA: o cartao do ML exibia "o prazo contratual aparece em
  // Vencidos / Vence em 24h, que vem do ship_by_date da Shopee".
  const ml = montarRiscoAtual(totais(), [], "mercadolivre").find((x) => x.chave === "over48h")!;
  assert.doesNotMatch(String(ml.nota), /Shopee|ship_by_date|Vencidos|Vence em 24h/);
  assert.match(CLIENTE, /temPrazo \? EXPLICACAO_LIMIAR_48H : EXPLICACAO_LIMIAR_48H_SEM_PRAZO/);
});

test("a frase de atencao nomeia a marca, nao o seller_id", () => {
  const um = montarAtencaoDaFila(totais({ over_48h_count: 9 }), [conta("2227056661", "kokeshi", { over_48h_count: 9 })], "mercadolivre");
  assert.match(um.find((i) => i.chave === "over48h")!.texto, /Maior concentração: KOKESHI, com 9\./);
  assert.doesNotMatch(um.map((i) => i.texto).join(" "), /2227056661/);
  // duas contas da mesma marca: a conta volta, para dizer QUAL delas
  const dois = montarAtencaoDaFila(
    totais({ over_48h_count: 9 }),
    [conta("111", "barbours", { over_48h_count: 2 }), conta("222", "barbours", { over_48h_count: 7 })],
    "mercadolivre",
  );
  assert.match(dois.find((i) => i.chave === "over48h")!.texto, /BARBOURS \(conta 222\), com 7\./);
});

// ===========================================================================
// 4. Prazo
// ===========================================================================
test("Shopee: particao do backlog por situacao de prazo, que fecha o total", () => {
  const f = faixasDePrazo(totais())!;
  assert.deepEqual(f.map((x) => x.chave), ["overdue", "due24h", "on_time", "sem_prazo"]);
  assert.equal(f.reduce((s, x) => s + x.valor, 0), 100);
  assert.equal(faixasDePrazo(null), null, "sem totais, sem faixa");
});

test("Shopee: a secao diz que NAO ha LDR e por que", () => {
  assert.match(SEM_LDR_SHOPEE, /LDR/);
  assert.match(SEM_LDR_SHOPEE, /só os\s+pedidos em aberto/);
  assert.match(CLIENTE, /dica=\{LDR_SHOPEE_LIGADA \? undefined : SEM_LDR_SHOPEE\}/);
});

test("Mercado Livre: prazo indisponivel pelo canal e envelhecimento da fila", () => {
  assert.match(
    SEM_PRAZO_CANAL_ML,
    /^Indisponível neste canal: o Mercado Livre não publica prazo de despacho na fonte atual\./,
  );
  assert.doesNotMatch(SEM_PRAZO_CANAL_ML, /Prazo indisponível pelo canal/, "frase antiga fora");
  assert.match(SEM_PRAZO_CANAL_ML, /Não há vencidos nem LDR/);
  const e = envelhecimentoDaFila(totais({ over_48h_count: 30 }), [frescor("a", 50), frescor("b", 200), frescor("c", null)])!;
  assert.deepEqual(e, { acima48h: 30, backlog: 100, maisAntigo: { marca: "b", horas: 200 } });
  assert.equal(envelhecimentoDaFila(totais(), [frescor("a", null)])!.maisAntigo, null, "idade ausente nao vira zero");
  assert.match(CLIENTE, /\{SEM_PRAZO_CANAL_ML\}/);
});

test("TikTok: a secao de prazo e' a LDR, com o evento (coleta/despacho) nomeado", () => {
  assert.match(TIKTOK, /const secaoLdr = ldr \? \(\s*<SecaoGerencial\s+titulo="Prazo"/);
  assert.match(TIKTOK, /evento: \$\{ROTULO_EVENTO\[evento\]\}/);
  assert.match(TIKTOK, /rotulo="Evento"/);
});

// ===========================================================================
// 5. Mesma hierarquia e mesma linguagem
// ===========================================================================
test("os tres canais tem uma definicao curta da medicao", () => {
  assert.match(DEFINICAO_FILA.shopee, /ship_by_date/);
  assert.match(DEFINICAO_FILA.mercadolivre, /não publica prazo/);
  assert.match(DEFINICAO_TIKTOK, /coleta e despacho/);
  assert.match(CLIENTE, /definicao=\{DEFINICAO_FILA\[filtros\.channel\]\}/);
  assert.match(TIKTOK, /definicao=\{DEFINICAO_TIKTOK\}/);
});

test("as secoes tem o mesmo titulo nos tres canais", () => {
  for (const f of [CLIENTE, TIKTOK]) {
    assert.match(f, /<SecaoGerencial titulo="Risco atual"/);
    assert.match(f, /titulo="Prazo"/);
    assert.match(f, /Concentração por marca/);
  }
});

test("'Fonte avançou' virou linguagem de gestor", () => {
  assert.equal(rotuloFonte(true), "Recebeu atualização");
  assert.equal(rotuloFonte(false), "Sem atualização nova");
  assert.doesNotMatch(CLIENTE, /Fonte avançou|\(metadado\)/);
});

test("na concentracao a marca e' a identidade; a conta e' detalhe discreto", () => {
  assert.match(CLIENTE, /\{brandLabel\(a\.brand\)\}\s*\{a\.shop_account !== a\.brand \? \(/);
  assert.match(CLIENTE, /text-xs font-normal text-ink-faint">\s*conta \{a\.shop_account\}/);
  assert.doesNotMatch(CLIENTE, /<th scope="col">Conta<\/th>\s*<th scope="col">Marca<\/th>/);
});

test("TikTok com marca sem serie: nao diz 'nenhuma fotografia publicada'", () => {
  assert.equal(
    explicarIndisponivelComMarca("no_series_published", "kokeshi"),
    "Sem série do TikTok para esta marca na janela consultada.",
  );
  assert.match(explicarIndisponivelComMarca("no_series_published", null), /nenhuma fotografia foi publicada/);
});

test("TikTok: lojas acima da meta sem base suficiente e' ausencia", () => {
  assert.match(TIKTOK, /valor=\{dados\?\.brands\.length \? formatarInteiro\(piores\.length\) : VALOR_SEM_TAXA\}/);
});

// ===========================================================================
// 6. Renderizacao do seletor de marca e do estado de frescor
// ===========================================================================
const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { SeletorMarca, ChipsFotografia } = await import("../src/components/expedicao/CabecalhoExpedicao.tsx");

test("seletor de marca: 'Todas', o mapa canonico e marcas novas do payload", () => {
  const html = renderToStaticMarkup(
    h(SeletorMarca, { marca: "kokeshi", marcasPresentes: ["marca-nova"], aoTrocar: () => {} }),
  );
  assert.match(html, /<label[^>]*for="([^"]+)"[^>]*>Marca<\/label>/);
  assert.match(html, /<option value="">Todas as marcas<\/option>/);
  for (const r of ["BARBOURS", "KOKESHI", "LESCENT", "RITUÁRIA", "ÁPICE", "MARCA-NOVA"]) {
    assert.ok(html.includes(`>${r}</option>`), r);
  }
  assert.match(html, /min-h-11/);
});

// EXPEDICAO-ULTIMA-ATUALIZACAO (30/10/2026): no topo, so' "Última atualização";
// "Atualizada"/"Fotografia"/"Fonte até" sairam. "Desatualizada" continua escrito.
test("topo: so' 'Última atualização'; 'Desatualizada' escrito quando velha", () => {
  const base = { rotulo: "Fotografia", publicadaEm: "2026-10-21T12:00:00Z", idade: "1.2 h", fonteLidaAte: "2026-10-21T11:00:00Z" };
  const fresca = renderToStaticMarkup(h(ChipsFotografia, { fotografia: { ...base, desatualizada: false } }));
  assert.match(fresca, /Última atualização <time dateTime="2026-10-21T12:00:00Z">\d{2}\/\d{2}, \d{2}:\d{2}<\/time>/);
  assert.doesNotMatch(fresca, />Atualizada|>Fotografia|Fonte até|Desatualizada/);
  // a idade e a fonte nao somem: vao para o hover do selo
  assert.match(fresca, /title="Publicada há 1\.2 h\. Fonte lida até/);
  const velha = renderToStaticMarkup(h(ChipsFotografia, { fotografia: { ...base, desatualizada: true } }));
  assert.match(velha, />Desatualizada</);
});

// ===========================================================================
// T-0010 — `?` das metricas e catalogo unico de definicoes
// ===========================================================================
import {
  AJUDA_CANAL_SEM_PRAZO,
  CAMPOS_DEFINICAO,
  DEFINICOES,
  KPIS_DA_FILA,
  METRICAS_POR_CANAL,
  chaveDoKpiDaFila,
  ehChaveMetrica,
} from "../src/lib/expedicao-definicoes.ts";
import { montarKpis } from "../src/lib/expedicao-contract.ts";

const { default: AjudaMetrica } = await import("../src/components/expedicao/AjudaMetrica.tsx");
const { KpiComAjuda } = await import("../src/components/expedicao/KpiComAjuda.tsx");

test("catalogo: toda metrica exibida tem os seis campos, curtos", () => {
  for (const [canal, chaves] of Object.entries(METRICAS_POR_CANAL)) {
    for (const c of chaves) {
      assert.ok(ehChaveMetrica(c), `${canal}: ${c} fora do catalogo`);
    }
  }
  for (const [chave, def] of Object.entries(DEFINICOES)) {
    assert.ok(def.nome.length > 2, chave);
    for (const [campo] of CAMPOS_DEFINICAO) {
      const t = def[campo];
      assert.ok(t.trim().length > 0, `${chave}.${campo} vazio`);
      assert.ok(t.length <= 260, `${chave}.${campo} longo demais (${t.length})`);
    }
  }
});

test("catalogo: todo KPI da fila, nos dois canais, tem `?`", () => {
  for (const canal of ["shopee", "mercadolivre"] as const) {
    const exibidos = montarKpis(totais(), [frescor("a", 1)], canal).map((k) => k.chave);
    for (const k of exibidos) {
      assert.ok(chaveDoKpiDaFila(canal, k), `${canal}.${k} sem definicao`);
      assert.ok((KPIS_DA_FILA as readonly string[]).includes(k), k);
    }
  }
});

test("catalogo: toda chave citada nas telas existe", () => {
  const arquivos = [
    "app/expedicao/ExpedicaoClient.tsx",
    "src/components/expedicao/TikTokDispatchPanel.tsx",
    "src/components/expedicao/LdrShopeePanel.tsx",
  ];
  let total = 0;
  for (const rel of arquivos) {
    for (const m of fonte(rel).matchAll(/(?:ajuda|chave)="([a-z_.]+)"/g)) {
      total += 1;
      assert.ok(ehChaveMetrica(m[1]), `${rel}: ${m[1]}`);
    }
  }
  assert.ok(total >= 12, `poucas chaves encontradas: ${total}`);
  // cada KPI do TikTok e da LDR Shopee passa pelo componente com `?`
  for (const rel of ["src/components/expedicao/TikTokDispatchPanel.tsx", "src/components/expedicao/LdrShopeePanel.tsx"]) {
    assert.doesNotMatch(fonte(rel), /<KpiTile\b/, `${rel} tem KPI sem ?`);
  }
  assert.match(CLIENTE, /ajuda=\{chaveDoKpiDaFila\(canal, kpi\.chave\)\}/);
});

test("LDR Shopee: indicador interno, nao o Account Health oficial", () => {
  const d = DEFINICOES["shopee.ldr"];
  assert.match(`${d.definicao} ${d.limitacoes}`, /interno/i);
  assert.match(d.limitacoes, /não é o índice oficial de Account Health/);
});

test("Mercado Livre: o `?` diz que nao ha SLA/LDR e nao inventa numero", () => {
  for (const c of ["mercadolivre.overdue", "mercadolivre.due24h", AJUDA_CANAL_SEM_PRAZO.prazo] as const) {
    const d = DEFINICOES[c];
    assert.match(d.limitacoes, /não publica prazo de despacho/);
    assert.match(d.limitacoes, /SLA nem LDR/);
    assert.doesNotMatch(`${d.definicao} ${d.formula}`, /\d+\s*%/, "nenhum numero inventado");
  }
  assert.match(DEFINICOES["mercadolivre.over48h"].limitacoes, /não é SLA/);
});

test("R21: +48 h conta do marco de cada canal (Shopee = pagamento; ML = pronto para despacho)", () => {
  const sh = DEFINICOES["shopee.over48h"];
  assert.match(sh.definicao, /48 h desde o pagamento/);
  assert.match(sh.formula, /pay_time/);
  const ml = DEFINICOES["mercadolivre.over48h"];
  // no ML `paid_at` e' nulo de proposito: o relogio e' `date_ready_to_ship`
  assert.match(ml.definicao, /48 h desde que o envio ficou pronto para despacho/);
  assert.match(ml.formula, /date_ready_to_ship/);
  assert.doesNotMatch(`${ml.definicao} ${ml.formula}`, /pagamento|pay_time/);
});

test("R21: zumbi declara a idade (> 30 dias), sem afirmar que a fonte parou", () => {
  for (const [c, marco] of [["shopee", /pay_time/], ["mercadolivre", /date_ready_to_ship/]] as const) {
    const d = DEFINICOES[`${c}.zombie`];
    assert.match(d.definicao, /abertos há mais de 30 dias na fonte/, c);
    assert.match(d.formula, /> 30 dias \(720 h\)/, c);
    assert.match(d.formula, marco, c);
    assert.match(d.limitacoes, /Mede só a idade/, c);
    assert.doesNotMatch(`${d.definicao} ${d.formula} ${d.limitacoes}`, /pararam|parou|parad/, c);
  }
});

test("o ⓘ da metrica: InfoDica focavel com definicao e limitacao, sem a metodologia", () => {
  // Objetividade: o popover de seis campos virou o "ⓘ" do resto da Torre.
  const d = DEFINICOES["tiktok.ldr"];
  const html = renderToStaticMarkup(h(AjudaMetrica, { chave: "tiktok.ldr" }));
  const rotulo = html.match(/<span tabindex="0" role="img" aria-label="([^"]+)"/)?.[1];
  assert.ok(rotulo, html);
  assert.equal(rotulo, `${d.definicao} ${d.limitacoes}`);
  assert.match(rotulo!, /Indicador interno/, "a ressalva que muda a leitura fica");
  for (const campo of ["formula", "fonte", "janela", "cobertura"] as const) {
    assert.ok(!html.includes(d[campo]), `${campo} fora da tela`);
  }
  assert.doesNotMatch(html, /role="dialog"|<button|Fechar|Definição: /);
  assert.doesNotMatch(html, /<div\b/, "so' span: o ⓘ mora dentro de <p>");
});

test("nenhum elemento da Expedicao combina hidden= com classe de display (B1)", () => {
  // No Tailwind 3.4 ".block"/".flex" vencem o "[hidden]" do preflight: o
  // elemento "escondido" aparece. Esconder = nao renderizar.
  for (const rel of [
    "src/components/expedicao/AjudaMetrica.tsx",
    "src/components/expedicao/JornadaPedidos.tsx",
    "src/components/expedicao/LayoutExpedicao.tsx",
    "src/components/expedicao/KpiComAjuda.tsx",
    "src/components/expedicao/TikTokDispatchPanel.tsx",
    "src/components/expedicao/LdrShopeePanel.tsx",
    "app/expedicao/ExpedicaoClient.tsx",
    "app/expedicao/ExpedicaoShell.tsx",
  ]) {
    // Cada tag de abertura JSX, inteira (atributos em varias linhas).
    for (const tag of fonte(rel).match(/<[a-zA-Z]+\b(?:[^<>{}]|\{(?:[^{}]|\{[^{}]*\})*\})*>/g) ?? []) {
      if (/\shidden=/.test(tag)) {
        assert.doesNotMatch(tag, /\b(block|flex|grid|inline-flex|inline-block)\b/, `${rel}: ${tag}`);
      }
    }
  }
});

// ===========================================================================
// Revisao T-0010 — origem do numero (oficial x Torre x indisponivel) e "ⓘ"
// nos cabecalhos ordenaveis da "LDR por loja"
// ===========================================================================
import { ROTULO_ORIGEM } from "../src/lib/expedicao-definicoes.ts";

const { default: SortHeaderComAjuda } = await import("../src/components/expedicao/SortHeaderComAjuda.tsx");

test("catalogo: toda definicao declara a origem do numero", () => {
  const validas = Object.keys(ROTULO_ORIGEM);
  for (const [chave, def] of Object.entries(DEFINICOES) as [string, { origem?: string; formula: string; fonte: string; limitacoes: string }][]) {
    assert.ok(def.origem && validas.includes(def.origem), `${chave} sem origem declarada`);
    if (def.origem === "torre") {
      assert.doesNotMatch(def.formula, /Não se aplica/, `${chave}: calculo da Torre sem regra`);
    }
    if (def.origem === "oficial") {
      assert.ok(def.fonte.trim().length > 0, `${chave}: oficial sem fonte`);
    }
    if (def.origem === "indisponivel") {
      assert.match(def.formula, /Não se aplica/, `${chave}: indisponivel com formula`);
      assert.match(def.limitacoes, /não publica/, `${chave}: indisponivel sem motivo`);
    }
  }
});

test("origem: classificacao pelos fatos do codigo", () => {
  assert.equal(DEFINICOES["shopee.ldr"].origem, "torre", "LDR Shopee nao e' Account Health");
  assert.equal(DEFINICOES["tiktok.ldr"].origem, "torre");
  // 4% e' REFERENCIA do TikTok, nao a fonte do numero
  for (const c of ["tiktok.ldr", "tiktok.lojas_acima_meta"] as const) {
    const d = DEFINICOES[c];
    assert.match(d.referencia ?? "", /4%.*TikTok/, c);
    assert.doesNotMatch(d.fonte, /TikTok Shop|4%/, `${c}: a meta nao e' fonte`);
  }
  for (const k of ["backlog", "over48h", "stalled", "slow", "zombie"]) {
    assert.equal(DEFINICOES[`shopee.${k}` as keyof typeof DEFINICOES].origem, "torre", `shopee.${k}`);
    assert.equal(DEFINICOES[`mercadolivre.${k}` as keyof typeof DEFINICOES].origem, "torre", `mercadolivre.${k}`);
  }
  assert.equal(DEFINICOES["shopee.overdue"].origem, "torre");
  for (const c of ["mercadolivre.overdue", "mercadolivre.due24h", "mercadolivre.prazo"] as const) {
    assert.equal(DEFINICOES[c].origem, "indisponivel", c);
  }
  // Hoje nenhuma metrica da Expedicao e' copiada da plataforma.
  assert.deepEqual(Object.entries(DEFINICOES).filter(([, d]) => d.origem === "oficial"), []);
});

test("LDR por loja: ⓘ nos cabecalhos ordenaveis, fora do botao de ordenar", () => {
  const props = {
    rotulo: "LDR",
    coluna: "rate",
    sort: { column: "rate", direction: "desc" as const },
    aoOrdenar: () => {},
    ajuda: "tiktok.ldr" as const,
  };
  const html = renderToStaticMarkup(h(SortHeaderComAjuda, props));
  assert.match(html, /^<th scope="col" aria-sort="descending"/);
  const i = html.indexOf('role="img"');
  assert.ok(i > 0, html);
  // Antes do ⓘ, o botao de ordenar ja' fechou: nada focavel dentro de botao.
  const antes = html.slice(0, i);
  assert.equal((antes.match(/<button\b/g) ?? []).length, 1, "so' o botao de ordenar");
  assert.equal((antes.match(/<\/button>/g) ?? []).length, 1, "o botao de ordenar fecha antes do ⓘ");
  assert.match(html, /min-h-11/, "alvo de 44 px do ordenar");

  // Arvore React: so' o botao de ordenar tem onClick, e o ⓘ nao mora nele.
  type No = { type: unknown; props: { onClick?: () => void; children?: unknown } };
  const ordenadas: string[] = [];
  const raiz = SortHeaderComAjuda({ ...props, aoOrdenar: (c: string) => ordenadas.push(c) }) as unknown as No;
  const filhos = (n: No): No[] =>
    ([] as unknown[]).concat(n.props.children ?? []).filter((x): x is No => typeof x === "object" && x !== null && "props" in x);
  const todos: { no: No; dentroDoOrdenar: boolean }[] = [];
  const andar = (n: No, dentro: boolean) => {
    todos.push({ no: n, dentroDoOrdenar: dentro });
    const ehOrdenar = n.type === "button";
    for (const f of filhos(n)) andar(f, dentro || ehOrdenar);
  };
  andar(raiz, false);
  const comClique = todos.filter((t) => typeof t.no.props.onClick === "function");
  assert.equal(comClique.length, 1, "um unico onClick: o de ordenar");
  assert.equal(comClique[0].no.type, "button");
  const ajuda = todos.find((t) => t.no.type === AjudaMetrica);
  assert.ok(ajuda, "o ⓘ e' o AjudaMetrica");
  assert.equal(ajuda!.dentroDoOrdenar, false, "o ⓘ nao esta' dentro do botao de ordenar");
  comClique[0].no.props.onClick!();
  assert.deepEqual(ordenadas, ["rate"]);
});

test("LDR por loja: Venciam, Atrasados e LDR com ⓘ; Loja segue no SortHeader", () => {
  const ini = TIKTOK.indexOf("LDR por loja no período");
  const tabela = TIKTOK.slice(ini, TIKTOK.indexOf("</thead>", ini));
  for (const [coluna, chave] of [["base", "tiktok.venciam"], ["late", "tiktok.atrasaram"], ["rate", "tiktok.ldr"]]) {
    assert.match(
      tabela,
      new RegExp(`<SortHeaderComAjuda\\s+rotulo="[^"]+"\\s+coluna="${coluna}"[^>]*ajuda="${chave.replace(".", "\\.")}"`),
      coluna,
    );
  }
  assert.match(tabela, /<SortHeader\s+rotulo="Loja"/);
  assert.doesNotMatch(tabela, /<SortHeader rotulo="(Venciam|Atrasados|LDR)"/);
});

// EXPEDICAO-VENCE-AMANHA: o ⓘ saiu da linha de comparacao para o lado do rotulo.
test("KpiComAjuda poe o ⓘ ao lado do rotulo do KPI", () => {
  const html = renderToStaticMarkup(
    h(KpiComAjuda, { ajuda: "shopee.backlog", rotulo: "Backlog total", valor: "1.820", comparacao: "Fila aberta." }),
  );
  assert.match(html, /Backlog total<span tabindex="0" role="img" aria-label="Cada pedido pago que ainda não foi expedido\./);
  assert.match(html, /<p class="mt-1\.5 text-xs leading-snug text-ink-muted">Fila aberta\.<\/p>/);
});

// ===========================================================================
// T-0021 — ML, secao Prazo: frase no padrao "Indisponível neste canal: …"
// ===========================================================================
test("ML Prazo: a secao le 'Indisponível neste canal: …' no ⓘ (a frase antiga sai)", async () => {
  const { SecaoGerencial } = await import("../src/components/expedicao/SecoesExpedicao.tsx");
  // #124: a frase saiu do corpo do painel e virou o ⓘ da secao Prazo.
  assert.match(CLIENTE, /<SecaoGerencial titulo="Prazo" dica=\{SEM_PRAZO_CANAL_ML\}>/);
  const html = renderToStaticMarkup(
    h(SecaoGerencial, { titulo: "Prazo", dica: SEM_PRAZO_CANAL_ML }, h("div", null)),
  );
  // O leitor de tela le' a frase inteira pelo `aria-label` do ⓘ.
  assert.match(
    html,
    /aria-label="Indisponível neste canal: o Mercado Livre não publica prazo de despacho na fonte atual\./,
  );
  assert.doesNotMatch(html, /Prazo indisponível pelo canal:/);
});

test("ML Prazo: titulo do painel e' 'Prazo de despacho', com o ⓘ em `acao`; a ausencia fica no ⓘ da secao", async () => {
  const { default: Panel } = await import("../src/components/ui/Panel.tsx");
  const { SecaoGerencial } = await import("../src/components/expedicao/SecoesExpedicao.tsx");
  // #130..#133: o ⓘ do painel saiu do titulo e foi para `acao`; so' o texto
  // do titulo e' da T-0021.
  assert.match(
    CLIENTE,
    /<Panel titulo="Prazo de despacho" acao=\{<Ajuda chave=\{AJUDA_CANAL_SEM_PRAZO\.prazo\} \/>\}>/,
  );
  assert.doesNotMatch(CLIENTE, /Prazo indisponível pelo canal/, "titulo antigo fora");
  // Mesma montagem do cliente: secao com o ⓘ da frase + painel com titulo e ⓘ.
  const html = renderToStaticMarkup(
    h(
      SecaoGerencial,
      { titulo: "Prazo", dica: SEM_PRAZO_CANAL_ML },
      h(Panel, {
        titulo: "Prazo de despacho",
        acao: h(AjudaMetrica, { chave: AJUDA_CANAL_SEM_PRAZO.prazo }),
      }),
    ),
  );
  assert.match(html, />Prazo de despacho</);
  assert.match(html, /aria-label="Não medido: o canal não publica prazo de despacho\./);
  assert.match(html, /Indisponível neste canal: o Mercado Livre não publica prazo de despacho na fonte atual\./);
  assert.doesNotMatch(html, /Prazo indisponível pelo canal/);
});
