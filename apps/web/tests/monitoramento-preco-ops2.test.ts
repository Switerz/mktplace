/**
 * Gate PMA-OPS-2 — o diagnostico vira superficie operacional.
 *
 * O que estes testes travam, em uma frase: o detalhamento das causas reconcilia
 * com o cartao ou DENUNCIA que nao reconcilia, os relogios da fotografia nunca
 * se passam por "agora", e a ausencia de link distingue limite da fonte de
 * defeito do dado.
 *
 * OS ESTADOS QUE NAO PODEM COLAPSAR
 * ----------------------------------
 * Quatro coisas parecem "sem numero" e sao diferentes: carregando, erro, zero
 * medido e campo ausente. Colapsa-las e' como a tela passa a mentir por
 * omissao — "0 kits sem composicao" quando a verdade e' "esta versao da API
 * ainda nao decompoe". Metade destes testes existe so' para isso.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import {
  MOTIVO_SEM_REFERENCIA_ROTULO,
  decomporSemReferencia,
  linkAnuncioView,
  painelDeFrescor,
} from "../src/lib/monitoramento-preco.ts";
import type { MonitoramentoPrecoMeta } from "../src/lib/monitoramento-preco-contract.ts";

const PAGE = new URL("../app/monitoramento-preco/page.tsx", import.meta.url);
const LIB = new URL("../src/lib/monitoramento-preco.ts", import.meta.url);
const ler = (u: URL) => readFile(u, "utf8");

/** A fotografia REAL de 2026-09-25, por canal. Numeros medidos, nao inventados. */
const PRODUCAO = {
  ml: {
    monitorados: 872, comparaveis: 144, semReferencia: 567,
    breakdown: {
      reference_missing_for_product: 434,
      brand_without_b2b_reference: 132,
      offer_without_match_key: 1,
    },
  },
  shopee: {
    monitorados: 695, comparaveis: 152, semReferencia: 440,
    breakdown: {
      kit_composition_missing: 264,
      reference_missing_for_product: 104,
      brand_without_b2b_reference: 72,
    },
  },
  tiktok: {
    monitorados: 1215, comparaveis: 103, semReferencia: 810,
    breakdown: {
      kit_composition_missing: 343,
      brand_without_b2b_reference: 273,
      reference_missing_for_product: 194,
    },
  },
} as const;

// ---------------------------------------------------------------------------
// 1. RECONCILIACAO com a fotografia publicada
// ---------------------------------------------------------------------------
for (const [canal, f] of Object.entries(PRODUCAO)) {
  test(`${canal}: o detalhamento fecha com o cartao sem referencia`, () => {
    const d = decomporSemReferencia(f.breakdown, f.semReferencia);
    assert.equal(d.somado, f.semReferencia);
    assert.equal(d.fecha, true);
    assert.equal(d.indisponivel, false);
    assert.ok(d.linhas && d.linhas.every((l) => !l.residual),
      "nenhuma linha residual quando fecha");
  });
}

test("os tres canais somam 1.817 ofertas sem referencia", () => {
  const total = Object.values(PRODUCAO)
    .reduce((s, f) => s + decomporSemReferencia(f.breakdown, f.semReferencia).somado, 0);
  assert.equal(total, 1817);
});

test("os baldes somados nos tres canais dao 477 / 607 / 1 / 732", () => {
  const soma: Record<string, number> = {};
  for (const f of Object.values(PRODUCAO)) {
    for (const [k, v] of Object.entries(f.breakdown)) soma[k] = (soma[k] ?? 0) + v;
  }
  assert.deepEqual(soma, {
    reference_missing_for_product: 732,
    brand_without_b2b_reference: 477,
    kit_composition_missing: 607,
    offer_without_match_key: 1,
  });
});

// ---------------------------------------------------------------------------
// 2. OS QUATRO ESTADOS, e eles nao colapsam
// ---------------------------------------------------------------------------
test("campo ausente e' INDISPONIVEL, nunca zero medido", () => {
  const d = decomporSemReferencia(undefined, 440);
  assert.equal(d.indisponivel, true);
  assert.equal(d.linhas, null, "sem campo nao ha lista — nem lista vazia");
  assert.equal(d.total, 440, "o total do cartao continua correto");
});

test("null tambem e' indisponivel: deploy antigo nao vira decomposicao vazia", () => {
  assert.equal(decomporSemReferencia(null, 10).indisponivel, true);
});

test("zero MEDIDO e' lista vazia que FECHA — e' uma boa noticia, nao ausencia", () => {
  const d = decomporSemReferencia({}, 0);
  assert.equal(d.indisponivel, false);
  assert.deepEqual(d.linhas, []);
  assert.equal(d.fecha, true);
});

test("baldes zerados nao viram linha: a tela lista causa, nao vocabulario", () => {
  const d = decomporSemReferencia(
    { kit_composition_missing: 0, brand_without_b2b_reference: 5 }, 5);
  assert.equal(d.linhas?.length, 1);
  assert.equal(d.linhas?.[0].chave, "brand_without_b2b_reference");
});

// ---------------------------------------------------------------------------
// 3. NAO FECHAR E' DEFEITO A MOSTRAR, nao a maquiar
// ---------------------------------------------------------------------------
test("soma menor que o total expoe o residual em vez de esconder", () => {
  const d = decomporSemReferencia({ brand_without_b2b_reference: 100 }, 440);
  assert.equal(d.fecha, false);
  const residual = d.linhas?.find((l) => l.residual);
  assert.ok(residual, "a diferenca tem de aparecer");
  assert.equal(residual?.valor, 340);
  assert.equal(d.somado, 100, "`somado` continua sendo a soma REAL dos baldes");
});

test("soma maior que o total produz residual NEGATIVO, nao truncado", () => {
  const d = decomporSemReferencia({ brand_without_b2b_reference: 500 }, 440);
  assert.equal(d.fecha, false);
  assert.equal(d.linhas?.find((l) => l.residual)?.valor, -60);
});

test("as fracoes usam o total do cartao, nao a soma dos baldes", () => {
  // Normalizar pela soma faria um detalhamento incompleto exibir 100%.
  const d = decomporSemReferencia({ brand_without_b2b_reference: 110 }, 440);
  assert.equal(d.linhas?.[0].fracao, 0.25);
});

test("total zero nao divide por zero: fracao e' nula", () => {
  const d = decomporSemReferencia({ brand_without_b2b_reference: 0 }, 0);
  assert.deepEqual(d.linhas, []);
});

// ---------------------------------------------------------------------------
// 4. ROTULOS COMPREENSIVEIS, e ordenacao estavel
// ---------------------------------------------------------------------------
test("os quatro baldes do backend tem rotulo em portugues", () => {
  for (const chave of [
    "reference_missing_for_product",
    "brand_without_b2b_reference",
    "kit_composition_missing",
    "offer_without_match_key",
  ]) {
    const rotulo = MOTIVO_SEM_REFERENCIA_ROTULO[chave];
    assert.ok(rotulo && rotulo !== chave, `${chave} sem rotulo`);
    assert.ok(!/_/.test(rotulo), `${chave} exibe o identificador cru`);
  }
});

test("chave desconhecida nao some da tela: cai no proprio nome", () => {
  // Um motivo novo no backend tem de APARECER, mesmo sem traducao. Silenciar
  // quebraria o fechamento sem dizer por que.
  const d = decomporSemReferencia({ motivo_inedito: 7 }, 7);
  assert.equal(d.linhas?.[0].rotulo, "motivo_inedito");
  assert.equal(d.fecha, true);
});

test("empate em contagem ordena por chave: a lista nao dança entre cargas", () => {
  const d = decomporSemReferencia(
    { zzz_motivo: 5, aaa_motivo: 5 }, 10);
  assert.deepEqual(d.linhas?.map((l) => l.chave), ["aaa_motivo", "zzz_motivo"]);
});

// ---------------------------------------------------------------------------
// 5. OS RELOGIOS — e o que a tela NAO consegue ver
// ---------------------------------------------------------------------------
const metaBase = {
  observed_date: "2026-09-23",
  observed_at: "2026-09-23T12:51:00+00:00",
  refreshed_at: "2026-09-23T12:51:42+00:00",
  eligible_ref_date: "2026-09-25",
  lag_days: 2,
  freshness_status: "stale",
} as unknown as MonitoramentoPrecoMeta;

test("o painel declara que a ingestao bruta NAO e' observavel aqui", () => {
  // Esta e' a linha que impede "a ingestao rodou hoje, logo a tela esta atual".
  const p = painelDeFrescor(metaBase);
  const bruta = p.relogios.find((r) => r.chave === "ingestao_bruta");
  assert.ok(bruta, "o quarto relogio tem de existir");
  assert.match(bruta!.valor, /não observável/i);
  assert.match(bruta!.explicacao, /Data Mart/);
});

test("fotografia atrasada nao e' chamada de atual, e o alerta diz o tamanho", () => {
  const p = painelDeFrescor(metaBase);
  assert.equal(p.situacao, "atrasada");
  assert.match(p.rotuloSituacao, /atrasada/i);
  assert.ok(p.alerta);
  assert.match(p.alerta!, /2 dia\(s\)/);
  assert.match(p.alerta!, /não descrevem o preço de hoje/i);
});

test("captura no canal e publicacao sao relogios SEPARADOS", () => {
  // Um so' relogio faria "a origem observou" e "nos publicamos" virarem a
  // mesma afirmacao — e e' a distancia entre os dois que revela o atraso.
  const p = painelDeFrescor(metaBase);
  const chaves = p.relogios.map((r) => r.chave);
  assert.ok(chaves.includes("captura"));
  assert.ok(chaves.includes("publicacao"));
  assert.notEqual(
    p.relogios.find((r) => r.chave === "captura")!.rotulo,
    p.relogios.find((r) => r.chave === "publicacao")!.rotulo,
  );
});

test("em dia: nenhum alerta, e a defasagem le-se 'em dia'", () => {
  const p = painelDeFrescor({
    ...metaBase, freshness_status: "fresh", lag_days: 0,
  } as unknown as MonitoramentoPrecoMeta);
  assert.equal(p.situacao, "em_dia");
  assert.equal(p.alerta, null);
  assert.equal(p.relogios.find((r) => r.chave === "defasagem")!.valor, "em dia");
});

test("retrospectiva NAO e' atraso, e nao gera alerta de pipeline", () => {
  const p = painelDeFrescor({
    ...metaBase, freshness_status: "historical",
  } as unknown as MonitoramentoPrecoMeta);
  assert.equal(p.situacao, "retrospectiva");
  assert.equal(p.alerta, null);
});

test("sem fotografia: ausencia declarada, e nunca zero", () => {
  const p = painelDeFrescor(null);
  assert.equal(p.situacao, "indisponivel");
  assert.match(p.alerta!, /Ausência não é zero/i);
  assert.equal(p.relogios.find((r) => r.chave === "defasagem")!.valor, "—");
});

test("meta nula nao quebra: os quatro relogios continuam existindo", () => {
  assert.equal(painelDeFrescor(null).relogios.length, 4);
});

// ---------------------------------------------------------------------------
// 6. LINK — tres estados, e nenhuma URL inventada
// ---------------------------------------------------------------------------
test("ML com permalink valido vira link clicavel", () => {
  const v = linkAnuncioView(
    "https://produto.mercadolivre.com.br/MLB-4861757283-serum-_JM", "ml");
  assert.equal(v.estado, "disponivel");
  assert.ok(v.url?.startsWith("https://produto.mercadolivre.com.br/"));
});

for (const canal of ["shopee", "tiktok"] as const) {
  test(`${canal} sem URL e' LIMITE DA FONTE, nao defeito`, () => {
    const v = linkAnuncioView(null, canal);
    assert.equal(v.estado, "ausente_na_fonte");
    assert.equal(v.url, null);
    assert.match(v.rotulo, /indisponível na fonte/i);
    assert.match(v.rotulo, /Nenhuma URL é montada/i);
  });
}

test("ML sem permalink e' DEFEITO, e o texto diz isso", () => {
  // O ML entrega 872/872. Uma linha sem link ali nao e' limite da fonte.
  const v = linkAnuncioView(null, "ml");
  assert.equal(v.estado, "anomalia");
  assert.match(v.rotulo, /defeito do dado/i);
});

test("permalink de outro canal nunca vira link", () => {
  assert.equal(linkAnuncioView("https://shopee.com.br/x", "ml").url, null);
  assert.equal(
    linkAnuncioView("https://produto.mercadolivre.com.br/x", "shopee").url, null);
});

test("http simples e' recusado em todos os canais", () => {
  assert.equal(linkAnuncioView("http://produto.mercadolivre.com.br/x", "ml").url, null);
  assert.equal(linkAnuncioView("http://shopee.com.br/x", "shopee").url, null);
});

test("nenhum estado devolve URL construida a partir de identificador", () => {
  // Shopee tem shop_id e item_id; TikTok tem product_id. A tentacao de montar
  // `shopee.com.br/product/{shop_id}/{item_id}` e' exatamente o que o gate veta.
  for (const canal of ["shopee", "tiktok"] as const) {
    for (const entrada of [null, undefined, "", "1609671923/123456"]) {
      assert.equal(linkAnuncioView(entrada, canal).url, null);
    }
  }
});

// ---------------------------------------------------------------------------
// 7. A PAGINA consome o que a lib produz
// ---------------------------------------------------------------------------
test("a pagina renderiza os quatro estados do detalhamento", async () => {
  const pagina = await ler(PAGE);
  assert.ok(/aria-busy=\{estado\.loading\}/.test(pagina), "carregando");
  assert.ok(pagina.includes("estado.error"), "erro");
  assert.ok(pagina.includes("causas.indisponivel"), "campo ausente");
  assert.ok(pagina.includes("causas.total === 0"), "zero medido");
});

test("a pagina mostra o veredito de fechamento, nos dois sentidos", async () => {
  const pagina = await ler(PAGE);
  assert.ok(pagina.includes("causas.fecha"));
  assert.ok(pagina.includes("Não fecha"), "nao fechar tem de ser visivel");
});

test("a pagina nao normaliza nem esconde o residual", async () => {
  const lib = await ler(LIB);
  assert.ok(lib.includes("__residual__"));
  assert.ok(!/linhas\.filter\(\(l\) => !l\.residual\)/.test(await ler(PAGE)),
    "a pagina nao pode filtrar a linha residual para fora");
});

test("o painel de frescor esta montado na pagina", async () => {
  const pagina = await ler(PAGE);
  assert.ok(pagina.includes("painelDeFrescor(meta)"));
  assert.ok(pagina.includes('aria-label="Frescor da fotografia"'));
});

test("a secao de causas tem rotulo acessivel proprio", async () => {
  const pagina = await ler(PAGE);
  assert.ok(pagina.includes('aria-label="Causas da ausência de referência"'));
});

test("o detalhamento declara que conta ATIVAS, kits e fora de escopo inclusive", async () => {
  // Sem esta frase, alguem somaria o detalhamento ao denominador de cobertura
  // — que exclui os dois de proposito — e acharia um erro que nao existe.
  const pagina = await ler(PAGE);
  assert.ok(/ofertas <strong>ativas<\/strong> sem referência/.test(pagina));
  assert.ok(pagina.includes("não o denominador de cobertura"));
});
