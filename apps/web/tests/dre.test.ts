/**
 * DRE-UX-1 — contrato da tela `/dre`.
 *
 * Testa a logica PURA (`src/lib/dre.ts`) contra fixtures sinteticas e, onde a
 * garantia so' existe na montagem, o CODIGO-FONTE — mesmo criterio de
 * `tema.test.ts`: `node --test` nao tem DOM.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  CLASSIFICACAO_CHIP,
  FILTROS_PADRAO,
  ROTULO_MARGEM,
  STATUS_CHIP,
  TEXTO_NAO_CALCULAVEL_AGREGADO,
  avisoFotografia,
  canaisBloqueadores,
  canaisCalculaveisOcultos,
  classificarFalha,
  completudeExibida,
  custosAusentes,
  dreApiUrl,
  dreQuery,
  elegivelParaRanking,
  fmtCusto,
  fmtPctDre,
  fmtReais,
  juntarNomes,
  linhasDre,
  mesCorrenteBrt,
  mesesSelecionaveis,
  parseDreFiltros,
  problemaNaResposta,
  queryCanonica,
  rankingDeMargem,
  rotuloAusentes,
  rotuloMes,
  rotuloPeriodo,
  selecaoVazia,
  shareDaReceita,
  somaDeControle,
  textoMargem,
  textoMargemPct,
  textoShare,
  type DreFiltros,
} from "../src/lib/dre.ts";
import { DreErro, fetchDre, lerCorpoDre } from "../src/lib/dre-api.ts";
import { NAV_SECTIONS, getRouteTitle } from "../src/components/shell/nav-config.ts";
import { ROTAS_TEMATIZADAS, rotaTematizada } from "../src/lib/theme.ts";
import {
  MARCA_A,
  MARCA_B,
  MARCA_C,
  MARCA_D,
  ML,
  ML_ADS_ZERO,
  SHOPEE,
  TIKTOK,
  TOTAL_MISTO,
  celula,
  comp,
  resposta,
} from "./dre-fixtures.ts";

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8");
const semComentarios = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
const params = (q: string) => new URLSearchParams(q);

// ===========================================================================
// 1. ML calculavel
// ===========================================================================
test("ML calculavel: todas as linhas na ordem do contrato, margem da API", () => {
  const linhas = linhasDre(ML);
  assert.deepEqual(
    linhas.map((l) => l.chave),
    ["receita", "devolucoes", "taxas", "afiliados", "ads", "frete", "cmv", "impostos"],
  );
  assert.deepEqual(
    linhas.filter((l) => l.naMargem).map((l) => l.chave),
    ["taxas", "ads", "frete", "cmv"],
  );
  assert.equal(somaDeControle(ML), ML.margem_contribuicao_conhecida.valor);
  assert.equal(ML.margem_contribuicao_conhecida.rotulo, ROTULO_MARGEM);
});

// ===========================================================================
// 2. TikTok: afiliado nao somavel, nunca subtraido duas vezes
// ===========================================================================
test("afiliado TikTok e' decomposicao: recuado, fora da margem, nao subtraido de novo", () => {
  const af = linhasDre(TIKTOK).find((l) => l.chave === "afiliados")!;
  assert.equal(af.recuo, true);
  assert.equal(af.naMargem, false);
  assert.equal(af.rotulo, "dos quais: afiliados");
  // 60k − 12k (taxas, que ja' contem o afiliado) − 4k − 20k = 24k. Somar o
  // afiliado de novo daria 21k.
  assert.equal(somaDeControle(TIKTOK), 24_000);
  assert.equal(TIKTOK.margem_contribuicao_conhecida.valor, 24_000);
  assert.notEqual(somaDeControle(TIKTOK), 24_000 - 3_000);
});

test("afiliado continua fora da margem MESMO se a API o marcasse como usado", () => {
  // Defesa em profundidade: um backend regredido nos DOIS pontos — afiliado
  // `somavel: true` E listado em componentes_usados — nao faz a tela soma-lo.
  const regredido = celula({
    canal: "tiktok",
    classificacao: "PARCIAL",
    comps: { ...TIKTOK.componentes, afiliados: { ...TIKTOK.componentes.afiliados, somavel: true } },
    margem: 24_000,
    usados: ["taxas", "afiliados", "frete", "cmv"],
    faltam: [],
  });
  assert.equal(linhasDre(regredido).find((l) => l.chave === "afiliados")!.naMargem, false);
  assert.equal(somaDeControle(regredido), 24_000);
});

test("a tela de DRE vertical pinta o afiliado sem sinal de subtracao", () => {
  const src = semComentarios(fonte("src/components/dre/DreVertical.tsx"));
  assert.match(src, /if \(l\.recuo\) \{[\s\S]*?fmtReais\(v\)/, "afiliado usa fmtReais, nao fmtCusto");
  assert.match(src, /Já incluído em Taxas — não somado de novo\./);
});

// ===========================================================================
// 3/4. Shopee nao calculavel e custo ausente: ausencia nunca vira zero
// ===========================================================================
test("ausencia nunca vira zero: valor null mostra status + motivo", () => {
  for (const c of [ML, TIKTOK, SHOPEE, TOTAL_MISTO]) {
    for (const l of linhasDre(c)) {
      if (l.componente.valor !== null) {
        assert.equal(l.ausencia, null);
        continue;
      }
      assert.ok(l.ausencia, `${l.chave} sem texto de ausencia`);
      assert.doesNotMatch(l.ausencia!, /R\$\s*0|^0$|^—$/, `${l.chave}: ausencia virou zero`);
      const chip = STATUS_CHIP[l.componente.status];
      assert.ok(chip, `${l.chave}: status ${l.componente.status} sem chip`);
      assert.ok(l.ausencia!.startsWith(chip!.rotulo));
    }
  }
  const imposto = linhasDre(ML).find((l) => l.chave === "impostos")!;
  assert.equal(imposto.ausencia, "Indisponível — fonte candidata nao ratificada (pergunta F8)");
});

test("o componente de valor devolve o chip de status quando o valor e' null, nunca um numero", () => {
  const src = semComentarios(fonte("src/components/dre/DreVertical.tsx"));
  assert.match(src, /if \(v === null\) \{\s*return <StatusChip/);
});

test("Shopee nao calculavel: custo com valor mas fora da margem fica marcado", () => {
  const linhas = linhasDre(SHOPEE);
  const taxas = linhas.find((l) => l.chave === "taxas")!;
  const cmv = linhas.find((l) => l.chave === "cmv")!;
  assert.equal(taxas.naMargem, false);
  assert.equal(taxas.chip?.rotulo, "Não confirmado");
  assert.equal(cmv.naMargem, false);
  assert.equal(cmv.chip?.rotulo, "Não reconcilia");
  assert.equal(cmv.chip?.tom, "critico");
  assert.equal(SHOPEE.margem_contribuicao_conhecida.valor, null);
  assert.deepEqual(custosAusentes(SHOPEE), ["devoluções", "taxas", "frete", "CMV", "impostos"]);
});

test("todos os status do contrato tem rotulo; so' 'disponivel' nao tem chip", () => {
  const status = Object.keys(STATUS_CHIP);
  assert.equal(status.length, 10);
  for (const s of status) {
    if (s === "disponivel") assert.equal(STATUS_CHIP[s as "disponivel"], null);
    else assert.ok(STATUS_CHIP[s as keyof typeof STATUS_CHIP]?.rotulo);
  }
  assert.equal(STATUS_CHIP.provisorio?.tom, "atencao", "provisorio sinalizado");
  assert.equal(STATUS_CHIP.snapshot_vencido?.rotulo, "Insumo desatualizado");
});

// ===========================================================================
// 5. Custo igual a zero medido
// ===========================================================================
test("zero MEDIDO aparece como R$ 0 e entra na margem; ausencia nao", () => {
  const ads = linhasDre(ML_ADS_ZERO).find((l) => l.chave === "ads")!;
  assert.equal(ads.componente.valor, 0);
  assert.equal(ads.naMargem, true);
  assert.equal(ads.ausencia, null);
  assert.equal(fmtCusto(0), fmtReais(0));
  assert.match(fmtCusto(0), /^R\$\s0$/);
  assert.match(fmtCusto(1234), /^− R\$\s1\.234$/);
  assert.equal(somaDeControle(ML_ADS_ZERO), 23_500);
});

// ===========================================================================
// 6. Fotografia vencida / ausente / tabela ausente
// ===========================================================================
test("fotografia: cada estado do lote vira uma faixa propria; ok nao mostra nada", () => {
  const fmt = (iso: string) => `<${iso}>`;
  assert.equal(avisoFotografia(resposta({}, "ok").fontes.insumos_dre, fmt), null);

  const vencido = avisoFotografia(resposta({}, "vencido").fontes.insumos_dre, fmt)!;
  assert.equal(vencido.curto, "Fotografia vencida");
  assert.match(vencido.detalhe, /48 h/);
  assert.match(vencido.detalhe, /<2026-10-10T06:00:00\+00:00>/, "mostra a publicacao mais antiga");

  assert.equal(avisoFotografia(resposta({}, "nao_publicado").fontes.insumos_dre, fmt)!.curto, "Fotografia ausente");
  const tabela = avisoFotografia(resposta({}, "tabela_ausente").fontes.insumos_dre, fmt)!;
  assert.equal(tabela.curto, "Insumos ausentes");
  assert.match(tabela.detalhe, /migration 025/);
  assert.equal(avisoFotografia(resposta({}, "parcialmente_vencido").fontes.insumos_dre, fmt)!.curto, "Fotografia parcial");
});

// ===========================================================================
// 7. Agregado com celulas de classificacoes diferentes
// ===========================================================================
test("total nao calculavel nao esconde os canais calculaveis", () => {
  const r = resposta();
  assert.equal(r.total!.classificacao, "NAO_CALCULAVEL");
  assert.deepEqual(canaisCalculaveisOcultos(r), ["tiktok", "ml"]);
  // Cada canal mantem a propria classificacao e margem.
  assert.equal(r.por_canal.find((c) => c.canal === "ml")!.margem_contribuicao_conhecida.valor, 42_000);
  assert.equal(r.por_canal.find((c) => c.canal === "tiktok")!.margem_contribuicao_conhecida.valor, 24_000);
});

test("sem canal calculavel, ou com total calculavel, nao ha oferta de refiltro", () => {
  assert.deepEqual(canaisCalculaveisOcultos(resposta({ por_canal: [SHOPEE] })), []);
  assert.deepEqual(canaisCalculaveisOcultos(resposta({ total: ML, por_canal: [ML] })), []);
});

test("o atalho de refiltro (resumo e ranking) passa pelo mesmo `aplicar` que escreve a URL", () => {
  const src = semComentarios(fonte("app/dre/DreClient.tsx"));
  assert.match(src, /<BotaoRecorteCalculavel canais=\{ocultos\} aoAplicar=\{\(canais\) => aplicar\(\{ canais \}\)\} \/>/);
  assert.match(src, /calculaveis=\{ocultos\}\s*aoFiltrar=\{\(canais\) => aplicar\(\{ canais \}\)\}/);
});

// ===========================================================================
// 8. Filtros por marca/canal/mes
// ===========================================================================
test("URL -> filtros: competencia, canais e marcas validos; o resto e' ignorado", () => {
  const f = parseDreFiltros(params("ref_month=2026-06&channels=ml,tiktok&brands=kokeshi,xyz,barbours"));
  assert.equal(f.mes, "2026-06");
  assert.deepEqual(f.canais, ["tiktok", "ml"], "ordem canonica");
  assert.deepEqual(f.marcas, ["barbours", "kokeshi"], "marca desconhecida descartada, ordem canonica");

  // date_from/date_to das outras telas NAO entram: 30 dias nao e' mes inteiro.
  const g = parseDreFiltros(params("date_from=2026-09-13&date_to=2026-10-12&compare=true"));
  assert.deepEqual(g, FILTROS_PADRAO);
  assert.equal(dreQuery(g), "");

  for (const ruim of ["2026-13", "2026-7", "26-07", "2026-07-01", "abc"]) {
    assert.equal(parseDreFiltros(params(`ref_month=${ruim}`)).mes, null, ruim);
  }
});

test("filtros -> query: so' o que difere do padrao, em ordem canonica", () => {
  assert.equal(dreQuery(FILTROS_PADRAO), "");
  assert.equal(
    dreQuery({ mes: "2026-07", canais: ["ml", "tiktok"], marcas: ["kokeshi", "apice"] }),
    "ref_month=2026-07&channels=tiktok%2Cml&brands=apice%2Ckokeshi",
  );
  assert.equal(dreQuery({ mes: null, canais: ["tiktok", "ml", "shopee"], marcas: [] }), "", "todos os canais = padrao");
});

test("seletor de competencia: 12 meses ate o corrente, e o mes da URL nunca some", () => {
  const meses = mesesSelecionaveis(new Date("2026-10-13T15:00:00Z"));
  assert.equal(meses.length, 12);
  assert.equal(meses[0], "2026-10");
  assert.equal(meses[11], "2025-11");
  const comExtra = mesesSelecionaveis(new Date("2026-10-13T15:00:00Z"), 12, "2025-01");
  assert.ok(comExtra.includes("2025-01"));
  assert.equal(comExtra[comExtra.length - 1], "2025-01");
});

test("mes corrente e' o de BRASILIA, nao o do relogio do navegador", () => {
  // 01/11 02:00 UTC = 31/10 23:00 em Brasilia: ainda outubro.
  assert.deepEqual(mesCorrenteBrt(new Date("2026-11-01T02:00:00Z")), { ano: 2026, mes: 10 });
  assert.equal(mesesSelecionaveis(new Date("2026-11-01T02:00:00Z"))[0], "2026-10");
  // 01/11 03:30 UTC = 01/11 00:30 em Brasilia: novembro.
  assert.equal(mesesSelecionaveis(new Date("2026-11-01T03:30:00Z"))[0], "2026-11");
  // Virada de ano.
  assert.equal(mesesSelecionaveis(new Date("2027-01-01T02:59:00Z"))[0], "2026-12");
});

test("competencia e' exibida como MM/AAAA", () => {
  assert.equal(rotuloMes("2026-07"), "07/2026");
  assert.equal(rotuloMes("2025-12"), "12/2025");
  assert.equal(rotuloPeriodo(["2026-07"]), "07/2026");
  assert.equal(rotuloPeriodo(["2026-06", "2026-07"]), "06/2026 – 07/2026");
  for (const m of mesesSelecionaveis(new Date("2026-10-13T15:00:00Z"))) assert.match(rotuloMes(m), /^\d{2}\/\d{4}$/);
});

// ===========================================================================
// URL canonica
// ===========================================================================
test("URL canonica: valida e canonica nao muda", () => {
  for (const q of ["", "ref_month=2026-07", "channels=tiktok%2Cml", "ref_month=2026-06&channels=ml&brands=apice%2Ckokeshi"]) {
    const r = queryCanonica(q);
    assert.equal(r.mudou, false, q);
    assert.equal(r.query, q);
  }
});

test("URL canonica: invalido, parcialmente invalido e fora de ordem sao reescritos para o recorte aplicado", () => {
  const casos: [string, string, Partial<DreFiltros>][] = [
    // marca invalida: aplica todas e a URL deixa de dizer xyz
    ["brands=xyz", "", { marcas: [] }],
    // canal invalido
    ["channels=foo", "", { canais: ["tiktok", "ml", "shopee"] }],
    // competencia invalida
    ["ref_month=2026-13", "", { mes: null }],
    // mistura de marca valida e invalida
    ["brands=kokeshi%2Cxyz", "brands=kokeshi", { marcas: ["kokeshi"] }],
    // canais fora da ordem canonica
    ["channels=ml%2Ctiktok", "channels=tiktok%2Cml", { canais: ["tiktok", "ml"] }],
    // marcas fora de ordem + parametro de outra tela
    ["brands=kokeshi%2Capice&date_from=2026-09-13&date_to=2026-10-12", "brands=apice%2Ckokeshi", { marcas: ["apice", "kokeshi"] }],
  ];
  for (const [entrada, canonica, esperado] of casos) {
    const r = queryCanonica(entrada);
    assert.equal(r.mudou, true, entrada);
    assert.equal(r.query, canonica, entrada);
    for (const [k, v] of Object.entries(esperado)) assert.deepEqual(r.filtros[k as keyof DreFiltros], v, `${entrada}: ${k}`);
    // URL reescrita, controles (filtros) e requisicao dizem o MESMO recorte.
    assert.equal(new URL(dreApiUrl("http://api", r.filtros)).search.replace(/^\?/, ""), r.query, entrada);
    assert.equal(queryCanonica(r.query).mudou, false, `${entrada}: reescrita estabiliza`);
  }
});

test("a pagina reescreve a URL nao canonica com router.replace", () => {
  const cliente = semComentarios(fonte("app/dre/DreClient.tsx"));
  assert.match(cliente, /queryCanonica\(searchParams\.toString\(\)\)/);
  assert.match(cliente, /if \(mudou\) router\.replace\(chave \? `\/dre\?\$\{chave\}` : "\/dre", \{ scroll: false \}\);/);
});

// ===========================================================================
// Payload invalido
// ===========================================================================
test("validacao estrutural: resposta real-like passa; forma incompativel nao", () => {
  assert.equal(problemaNaResposta(resposta()), null);
  assert.equal(problemaNaResposta(resposta({ total: null, por_canal: [], por_marca: [], celulas_base: [] })), null);
  assert.equal(problemaNaResposta(null), "corpo não é objeto");
  assert.equal(problemaNaResposta({ detail: "ok" }), "periodo inválido");
  const semComponente = structuredClone(resposta());
  delete (semComponente.por_canal[1].componentes as Partial<Record<string, unknown>>).cmv;
  assert.equal(problemaNaResposta(semComponente), "por_canal[1].componentes.cmv ausente");
  const statusNovo = structuredClone(resposta());
  (statusNovo.total!.componentes.taxas as { status: string }).status = "estimado";
  assert.equal(problemaNaResposta(statusNovo), "total.componentes.taxas.status inválido");
  const margemTexto = structuredClone(resposta());
  (margemTexto.por_marca[0].margem_contribuicao_conhecida as { valor: unknown }).valor = "24000";
  assert.equal(problemaNaResposta(margemTexto), "por_marca[0].margem inválida");
});

test("payload 200 invalido vira 'Resposta inválida da API', nunca 'Sem conexão'", async () => {
  const corpo = (json: () => Promise<unknown>) => ({ json });
  for (const [nome, res] of [
    ["JSON quebrado", corpo(() => Promise.reject(new SyntaxError("Unexpected token <")))],
    ["forma incompativel", corpo(() => Promise.resolve({ ok: true }))],
  ] as const) {
    await assert.rejects(lerCorpoDre(res), (err: unknown) => {
      assert.ok(err instanceof DreErro, nome);
      assert.equal((err as DreErro).falha.tipo, "payload_invalido", nome);
      assert.equal((err as DreErro).falha.titulo, "Resposta inválida da API", nome);
      assert.notEqual((err as DreErro).falha.tipo, classificarFalha(null, null).tipo);
      return true;
    });
  }
  const ok = await lerCorpoDre(corpo(() => Promise.resolve(resposta())));
  assert.equal(ok.total!.classificacao, "NAO_CALCULAVEL");
});

test("fetchDre: 200 invalido -> payload_invalido; fetch que falha -> rede", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = (async () => new Response("<html>proxy</html>", { status: 200 })) as typeof fetch;
    await assert.rejects(fetchDre(FILTROS_PADRAO), (e: unknown) => (e as DreErro).falha.tipo === "payload_invalido");
    globalThis.fetch = (async () => { throw new TypeError("Failed to fetch"); }) as typeof fetch;
    await assert.rejects(fetchDre(FILTROS_PADRAO), (e: unknown) => (e as DreErro).falha.tipo === "rede");
  } finally {
    globalThis.fetch = original;
  }
});

// ===========================================================================
// Leitura do agregado nao calculavel
// ===========================================================================
test("agregado bloqueado pela Shopee: bloqueador nomeado, sem '0 de 6', ausencias qualificadas", () => {
  const r = resposta();
  assert.deepEqual(canaisBloqueadores(r), ["Shopee"]);
  assert.equal(completudeExibida(r.total!), null, "agregado nao calculavel nao exibe completude");
  assert.equal(TEXTO_NAO_CALCULAVEL_AGREGADO, "Não calculável — veja os canais");
  assert.equal(rotuloAusentes(r.total!), "Ausente em ao menos um canal");
  assert.equal(rotuloAusentes(MARCA_A), "Ausente em ao menos um canal");
  assert.equal(rotuloAusentes(SHOPEE), "Ausente em ao menos uma marca");
  assert.equal(rotuloAusentes({ ...ML, marca: "kokeshi" }), "Faltam");
  // canal calculavel continua com a propria completude
  assert.equal(completudeExibida(ML), "4 de 6 custos");
  // total calculavel nao tem bloqueador
  assert.deepEqual(canaisBloqueadores(resposta({ total: ML, por_canal: [ML] })), []);
  assert.equal(juntarNomes(["Shopee", "TikTok Shop", "Mercado Livre"]), "Shopee, TikTok Shop e Mercado Livre");
});

test("margem, % e share nulos nunca viram R$ 0 nem travessao", () => {
  for (const c of [SHOPEE, TOTAL_MISTO, MARCA_C]) {
    assert.equal(textoMargem(c), "Não calculável");
    assert.equal(textoMargemPct(c), "Não calculável");
  }
  assert.equal(textoMargem(ML), fmtReais(42_000));
  assert.equal(textoMargem(TIKTOK), fmtReais(24_000));
  assert.equal(textoMargemPct(ML), "42,0%");
  assert.equal(textoShare(ML, TOTAL_MISTO), "50,0%");
  assert.equal(textoShare(ML, null), "Sem receita");
  // zero MEDIDO continua zero
  const zero = celula({ classificacao: "PARCIAL", comps: { receita: comp(1_000, "disponivel", { somavel: false }) }, margem: 0, usados: ["taxas", "cmv"], faltam: [] });
  assert.equal(textoMargem(zero), fmtReais(0));
});

// ===========================================================================
// Filtros enviados a API == filtros na URL
// ===========================================================================
test("a requisicao a API carrega EXATAMENTE a querystring da URL", () => {
  const casos: DreFiltros[] = [
    FILTROS_PADRAO,
    { mes: "2026-06", canais: ["ml"], marcas: [] },
    { mes: null, canais: ["tiktok", "shopee"], marcas: ["lescent"] },
    { mes: "2026-07", canais: ["tiktok", "ml"], marcas: ["apice", "barbours", "kokeshi"] },
  ];
  for (const f of casos) {
    const urlDaPagina = `/dre${dreQuery(f) ? `?${dreQuery(f)}` : ""}`;
    const api = new URL(dreApiUrl("http://api.local", f));
    assert.equal(api.pathname, "/api/v1/dre");
    assert.equal(api.search, new URL(urlDaPagina, "http://web.local").search);
    // E o que a API recebe, lido de volta, sao os mesmos filtros.
    assert.deepEqual(parseDreFiltros(api.searchParams), parseDreFiltros(new URL(urlDaPagina, "http://x").searchParams));
  }
});

test("a pagina usa a MESMA serializacao para a URL e para o fetch", () => {
  const cliente = semComentarios(fonte("app/dre/DreClient.tsx"));
  // `chave` = dreQuery(parse(URL)), via queryCanonica.
  assert.match(cliente, /const \{ filtros, query: chave, mudou \} = useMemo\(\(\) => queryCanonica\(searchParams\.toString\(\)\)/);
  assert.match(cliente, /router\.replace\(q \? `\/dre\?\$\{q\}` : "\/dre"/);
  assert.match(cliente, /const q = dreQuery\(\{ \.\.\.filtros, \.\.\.parcial \}\);/);
  assert.match(cliente, /fetchDre\(parseDreFiltros\(new URLSearchParams\(chave\)\)/);
  const api = semComentarios(fonte("src/lib/dre-api.ts"));
  assert.match(api, /fetch\(dreApiUrl\(API_URL, filtros\)/);
  // Sem modo demonstracao: nenhum dado de exemplo pode virar DRE.
  assert.doesNotMatch(api, /mock/i);
});

test("resultado de filtro anterior nunca aparece sob o filtro novo", () => {
  const cliente = semComentarios(fonte("app/dre/DreClient.tsx"));
  assert.match(cliente, /resultado && resultado\.chave === chave \? resultado : null/);
  assert.match(cliente, /return \(\) => ac\.abort\(\);/);
});

// ===========================================================================
// 9. Share ~ 100% com valores crus
// ===========================================================================
test("share usa a receita crua: tres terços somam 100%, nao 99,9%", () => {
  const terco = 100_000 / 3;
  const mk = (marca: string) =>
    celula({
      marca,
      classificacao: "PARCIAL",
      comps: { receita: comp(terco, "disponivel", { somavel: false }) },
      margem: 0,
      usados: [],
      faltam: [],
    });
  const total = celula({
    classificacao: "PARCIAL",
    comps: { receita: comp(100_000, "disponivel", { somavel: false }) },
    margem: 0,
    usados: [],
    faltam: [],
  });
  const shares = ["a", "b", "c"].map((m) => shareDaReceita(mk(m), total)!);
  assert.ok(Math.abs(shares.reduce((a, b) => a + b, 0) - 100) < 1e-9);
  // O texto arredondado somaria 99,9 — prova de que a conta nao parte dele.
  const somaDoTexto = shares.map((s) => Number(fmtPctDre(s)!.replace("%", "").replace(",", "."))).reduce((a, b) => a + b, 0);
  assert.ok(Math.abs(somaDoTexto - 99.9) < 1e-9);
});

test("share das marcas da fixture soma ~100% e o de canal tambem", () => {
  const r = resposta();
  const somaMarcas = r.por_marca.reduce((acc, c) => acc + shareDaReceita(c, r.total)!, 0);
  assert.ok(Math.abs(somaMarcas - 100) < 1e-9, String(somaMarcas));
  const somaCanais = r.por_canal.reduce((acc, c) => acc + shareDaReceita(c, r.total)!, 0);
  assert.ok(Math.abs(somaCanais - 100) < 1e-9, String(somaCanais));
});

test("sem receita total nao ha share: null, nunca 0%", () => {
  const semReceita = celula({
    classificacao: "NAO_CALCULAVEL",
    comps: { receita: comp(null, "indisponivel", { somavel: false }) },
    margem: null,
    usados: [],
    faltam: [],
  });
  assert.equal(shareDaReceita(ML, semReceita), null);
  assert.equal(shareDaReceita(ML, null), null);
  assert.equal(shareDaReceita(semReceita, TOTAL_MISTO), null);
});

// ===========================================================================
// Ranking: marca nao calculavel nunca entra
// ===========================================================================
test("marca NAO_CALCULAVEL nunca entra no ranking de margem", () => {
  const ranking = rankingDeMargem([MARCA_A, MARCA_B, MARCA_C, MARCA_D]);
  const c = ranking.find((i) => i.celula.marca === "apice")!;
  assert.equal(c.posicao, null);
  assert.equal(c.comparabilidade.comparavel, false);
  assert.equal(c.comparabilidade.motivo, "CMV nao reconcilia (razao 0,62)");
});

test("margem maior com MENOS custos nao vira 'melhor margem'", () => {
  const ranking = rankingDeMargem([MARCA_A, MARCA_B, MARCA_C, MARCA_D]);
  assert.deepEqual(
    ranking.map((i) => [i.celula.marca, i.posicao]),
    [
      ["kokeshi", 1], // 35%
      ["barbours", 2], // 30%
      ["apice", null], // nao calculavel (40k de receita)
      ["lescent", null], // 50%, mas sem Ads na conta (20k)
    ],
  );
  const d = ranking.find((i) => i.celula.marca === "lescent")!;
  assert.match(d.comparabilidade.motivo!, /custos diferentes dos demais \(sem Ads\)/);
});

test("sem taxas ou sem CMV na conta, a marca e' inelegivel", () => {
  const semCmv = celula({
    marca: "rituaria",
    classificacao: "PARCIAL",
    comps: { receita: comp(10_000, "disponivel", { somavel: false }), taxas: comp(1_000, "disponivel") },
    margem: 9_000,
    usados: ["taxas"],
    faltam: ["cmv"],
  });
  assert.deepEqual(elegivelParaRanking(semCmv), { comparavel: false, motivo: "sem CMV na conta" });
  assert.deepEqual(elegivelParaRanking(MARCA_A), { comparavel: true, motivo: null });
});

test("a tabela de marcas usa o ranking puro e mostra 'Não comparável' com motivo", () => {
  const src = semComentarios(fonte("src/components/dre/DreMarcas.tsx"));
  assert.match(src, /rankingDeMargem\(marcas\)/);
  assert.match(src, /textoShare\(c, total\)/);
  assert.match(src, /Não comparável/);
  assert.match(src, /item\.comparabilidade\.motivo/);
});

// ===========================================================================
// Estados da tela
// ===========================================================================
test("falhas HTTP viram estados distintos, com a mensagem humana do servidor quando houver", () => {
  const rede = classificarFalha(null, null);
  assert.equal(rede.tipo, "rede");

  const i1 = classificarFalha(422, { detail: "periodo precisa cobrir meses inteiros" });
  assert.equal(i1.tipo, "invalido");
  assert.equal(i1.mensagem, "periodo precisa cobrir meses inteiros");
  // 422 de validacao de tipo do FastAPI (lista) nao vaza para a tela.
  const i2 = classificarFalha(422, { detail: [{ loc: ["query", "date_from"], msg: "invalid date" }] });
  assert.equal(i2.tipo, "invalido");
  assert.doesNotMatch(i2.mensagem, /loc|query/);

  assert.equal(classificarFalha(503, { detail: "Banco indisponivel." }).mensagem, "Banco indisponivel.");
  assert.equal(classificarFalha(503, null).tipo, "indisponivel");
  assert.equal(classificarFalha(404, null).tipo, "sem_rota");
  assert.equal(classificarFalha(500, "<html>").tipo, "outro");
});

test("selecao sem dados e' um estado proprio, nao receita zero", () => {
  assert.equal(selecaoVazia(resposta({ total: null, por_canal: [], por_marca: [], celulas_base: [] })), true);
  assert.equal(selecaoVazia(resposta()), false);
  const cliente = fonte("app/dre/DreClient.tsx");
  assert.match(cliente, /Isto não é receita zero/);
});

test("a pagina implementa carregando, erro (com acao) e vazio", () => {
  const cliente = semComentarios(fonte("app/dre/DreClient.tsx"));
  assert.match(cliente, /tipo="carregando"/);
  assert.match(cliente, /tipo="erro"/);
  assert.match(cliente, /tipo="vazio"/);
  assert.match(cliente, /falha\.tipo === "invalido"/, "422 oferece limpar filtros");
  assert.match(cliente, /<RetryButton onClick=\{tentarDeNovo\}/, "demais falhas oferecem nova tentativa");
  assert.match(fonte("app/dre/page.tsx"), /<Suspense/);
});

// ===========================================================================
// Rotulos, navegacao e tema
// ===========================================================================
test("nada na tela se chama lucro ou margem liquida (exceto a negacao explicita)", () => {
  for (const rel of [
    "app/dre/DreClient.tsx",
    "src/components/dre/DreResumo.tsx",
    "src/components/dre/DreVertical.tsx",
    "src/components/dre/DreCanais.tsx",
    "src/components/dre/DreMarcas.tsx",
    "src/lib/dre.ts",
  ]) {
    const src = semComentarios(fonte(rel)).replace(/Não é lucro nem margem líquida/g, "");
    assert.doesNotMatch(src, /lucro|margem l[ií]quida|margem real/i, rel);
  }
  assert.equal(CLASSIFICACAO_CHIP.NAO_CALCULAVEL.rotulo, "Não calculável");
  assert.equal(CLASSIFICACAO_CHIP.PARCIAL.rotulo, "Parcial");
  assert.equal(CLASSIFICACAO_CHIP.COMPLETA.rotulo, "Completa");
});

test("/dre esta na navegacao, com titulo proprio, e e' rota tematizada", () => {
  const cockpits = NAV_SECTIONS.find((s) => s.label === "Cockpits")!;
  assert.ok(cockpits.pages.some((p) => p.href === "/dre" && p.label === "DRE" && !p.disabled));
  assert.equal(getRouteTitle("/dre"), "DRE");
  assert.ok((ROTAS_TEMATIZADAS as readonly string[]).includes("/dre"));
  assert.ok(rotaTematizada("/dre"));
  assert.match(fonte("src/components/shell/NavList.tsx"), /"\/dre": Calculator/);
});
