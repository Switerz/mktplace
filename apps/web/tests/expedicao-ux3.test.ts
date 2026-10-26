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
  assert.match(CLIENTE, /não é coberta pela fonte deste canal — ausência de cobertura, não backlog zero\./);
});

// ===========================================================================
// 3. Risco atual: so' o que o canal sustenta
// ===========================================================================
test("Shopee: seis indicadores, com vencidos e vence em 24 h", () => {
  assert.deepEqual([...KPIS_RISCO_ATUAL.shopee], ["backlog", "overdue", "due24h", "over48h", "stalled", "marcas_alerta"]);
  assert.equal(montarRiscoAtual(totais(), [], "shopee").length, 6);
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
    assert.match(String(k.nota), /nao e' SLA/i);
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
  assert.match(CLIENTE, /\{SEM_LDR_SHOPEE\}/);
});

test("Mercado Livre: prazo indisponivel pelo canal e envelhecimento da fila", () => {
  assert.match(SEM_PRAZO_CANAL_ML, /Prazo indisponível pelo canal/);
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

test("frescor escrito nos dois sentidos: 'Atualizada' e 'Desatualizada'", () => {
  const base = { rotulo: "Fotografia", publicadaEm: "2026-10-21T12:00:00Z", idade: "1.2 h", fonteLidaAte: null };
  assert.match(renderToStaticMarkup(h(ChipsFotografia, { fotografia: { ...base, desatualizada: false } })), /Atualizada · há 1\.2 h/);
  assert.match(renderToStaticMarkup(h(ChipsFotografia, { fotografia: { ...base, desatualizada: true } })), /Desatualizada · há 1\.2 h/);
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

test("o `?` e' um botao real, de 44px, rotulado e ligado ao painel", () => {
  const html = renderToStaticMarkup(h(AjudaMetrica, { chave: "tiktok.ldr" }));
  const botao = html.match(/<button[^>]*aria-label="O que é LDR do TikTok"[^>]*>/)?.[0];
  assert.ok(botao, html);
  assert.match(botao!, /type="button"/);
  assert.match(botao!, /aria-expanded="false"/);
  assert.match(botao!, /h-11 w-11/);
  const alvo = botao!.match(/aria-controls="([^"]+)"/)![1];
  assert.match(html, new RegExp(`<span[^>]*id="${alvo.replace(/[:]/g, "\:")}"[^>]*role="dialog"[^>]*hidden=""`));
  for (const [, rotulo] of CAMPOS_DEFINICAO) assert.ok(html.includes(`${rotulo}: `), rotulo);
  assert.doesNotMatch(html, /<div\b/, "so' span: o ? mora dentro de <p>");
  assert.match(html, /min-h-11 min-w-11[^"]*"[^>]*>Fechar<\/button>/);
});

test("o `?` fecha com Esc, clique fora e devolve o foco; posicao fixa na viewport", () => {
  const src = fonte("src/components/expedicao/AjudaMetrica.tsx");
  assert.match(src, /e\.key === "Escape"\) fechar\(true\)/);
  assert.match(src, /addEventListener\("pointerdown", fora\)/);
  assert.match(src, /botao\.current\?\.focus\(\)/);
  assert.match(src, /Math\.min\(LARGURA_MAX, vw - 2 \* MARGEM\)/, "cabe em 390 px");
  assert.match(src, /className="fixed z-50/);
  assert.match(src, /ATRASO_HOVER_MS/);
});

test("KpiComAjuda poe o `?` na linha de comparacao do KPI", () => {
  const html = renderToStaticMarkup(
    h(KpiComAjuda, { ajuda: "shopee.backlog", rotulo: "Backlog total", valor: "1.820", comparacao: "Fila aberta." }),
  );
  assert.match(html, /Fila aberta\. <span[^>]*><button[^>]*aria-label="O que é Backlog total"/);
});
