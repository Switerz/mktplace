// Gate EXP-3C2 — canal na tela da Expedicao.
//
// O que estes testes travam, em uma frase: com a flag do ML desligada a tela e'
// exatamente a de hoje e nenhuma requisicao de Mercado Livre pode partir dela;
// com a flag ligada, os dois canais nunca se misturam e o ML jamais aparece
// como "no prazo" so' porque a fonte nao publica prazo.
//
// Parte dos testes le' o FONTE, mesmo padrao do arquivo da EXP-2B: sao
// invariantes de fiacao que nao dependem de harness de componente React.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  CANAIS,
  CANAL_PADRAO,
  FILTROS_PADRAO,
  ROTULO_CANAL,
  SEM_PRAZO_ML,
  VALOR_SEM_PRAZO,
  SITUACOES_DE_PRAZO,
  aplicarCanal,
  aplicarFiltro,
  avisosDeCobertura,
  canalTemPrazo,
  chaveDaRequisicao,
  construirQuery,
  estadoDaTela,
  expedicaoMlHabilitado,
  montarKpis,
  mostrarColunaReferencia,
  queryDaTendencia,
  sanitizarCanal,
  type Canal,
  type Cobertura,
  type LinhaFila,
  type RespostaExpedicao,
  type Totais,
} from "../src/lib/expedicao-contract.ts";

const RAIZ = join(import.meta.dirname, "..");
const CLIENTE = readFileSync(
  join(RAIZ, "app/expedicao/ExpedicaoClient.tsx"),
  "utf8",
);
const CONTRATO = readFileSync(
  join(RAIZ, "src/lib/expedicao-contract.ts"),
  "utf8",
);

function totais(over: Partial<Totais> = {}): Totais {
  return {
    backlog_count: 714,
    overdue_count: 0,
    due_within_24h_count: 0,
    on_time_count: 0,
    deadline_unavailable_count: 714,
    over_48h_count: 179,
    slow_count: 0,
    zombie_count: 0,
    stalled_count: 0,
    ...over,
  };
}

function resposta(over: Partial<RespostaExpedicao> = {}): RespostaExpedicao {
  return {
    availability: "available",
    unavailable_reason: null,
    channel: "mercadolivre",
    snapshot: null,
    totals: totais(),
    accounts: [],
    freshness: [],
    coverage: null,
    queue: [],
    pagination: null,
    limitations: null,
    ...over,
  } as RespostaExpedicao;
}

// ---------------------------------------------------------------------------
// 1. A flag
// ---------------------------------------------------------------------------
test("flag do ML: somente a string exata 'true' liga", () => {
  assert.equal(expedicaoMlHabilitado("true"), true);
  for (const v of [undefined, null, "", "1", "TRUE", "True", "yes", "false", " true"]) {
    assert.equal(expedicaoMlHabilitado(v as string), false, String(v));
  }
});

test("a regra da flag e' UMA SO', escrita de duas formas equivalentes", () => {
  // O cliente compara inline para que o Next dobre a expressao e o minificador
  // elimine o seletor do bundle. O helper existe para o resto do codigo e para
  // os testes. Se as duas divergirem, a tela e o contrato passam a discordar.
  assert.match(CONTRATO, /return valor === "true";/);
  assert.match(
    CLIENTE,
    /const ML_LIGADO = process\.env\.NEXT_PUBLIC_EXPEDICAO_ML_ENABLED === "true";/,
  );
  for (const v of ["true", "false", "", "1", "TRUE"]) {
    assert.equal(expedicaoMlHabilitado(v), v === "true", v);
  }
});

test("a flag do ML e' SEPARADA da flag da rota", () => {
  assert.match(CLIENTE, /NEXT_PUBLIC_EXPEDICAO_ML_ENABLED/);
  // a rota continua sendo controlada pela flag original, no servidor
  const page = readFileSync(join(RAIZ, "app/expedicao/page.tsx"), "utf8");
  assert.match(page, /expedicaoHabilitado\(process\.env\.NEXT_PUBLIC_EXPEDICAO_ENABLED\)/);
  assert.doesNotMatch(page, /NEXT_PUBLIC_EXPEDICAO_ML_ENABLED/);
});

test("flag desligada: nenhum caminho leva ao canal do ML", () => {
  // o seletor so' existe atras da flag
  assert.match(CLIENTE, /ML_LIGADO && \(/);
  // e a leitura da URL sai cedo quando a flag esta desligada
  assert.match(CLIENTE, /if \(!ML_LIGADO\) return;/);
  // nenhum literal de canal do ML solto no componente
  assert.doesNotMatch(CLIENTE, /"mercadolivre"/);
});

test("sanitizarCanal e' fail-closed nas duas pontas", () => {
  assert.equal(sanitizarCanal("mercadolivre", false), "shopee");
  assert.equal(sanitizarCanal("mercadolivre", true), "mercadolivre");
  for (const v of [undefined, null, "", "tiktokshop", "SHOPEE", "shopee "]) {
    assert.equal(sanitizarCanal(v as string, true), "shopee", String(v));
  }
});

// ---------------------------------------------------------------------------
// 2. Shopee intacta
// ---------------------------------------------------------------------------
test("Shopee e' o padrao", () => {
  assert.equal(FILTROS_PADRAO.channel, "shopee");
  assert.equal(CANAL_PADRAO, "shopee");
});

test("a query da Shopee nao ganha parametro novo", () => {
  const q = construirQuery(FILTROS_PADRAO);
  assert.doesNotMatch(q, /channel/);
  // e e' exatamente a query de antes deste gate
  assert.equal(q, "order_by=criticidade&limit=100&offset=0");
});

test("a tendencia da Shopee tambem nao ganha parametro novo", () => {
  assert.equal(queryDaTendencia(48, "shopee"), "window_hours=48");
});

// ---------------------------------------------------------------------------
// 3. Propagacao do canal
// ---------------------------------------------------------------------------
test("o ML entra nas DUAS rotas, com o mesmo canal", () => {
  const f = aplicarFiltro(FILTROS_PADRAO, { channel: "mercadolivre" });
  const fila = new URLSearchParams(construirQuery(f));
  const tend = new URLSearchParams(queryDaTendencia(48, f.channel));
  assert.equal(fila.get("channel"), "mercadolivre");
  assert.equal(tend.get("channel"), "mercadolivre");
  assert.equal(fila.get("channel"), tend.get("channel"));
});

test("o cliente monta a tendencia pelo helper, nao a mao", () => {
  assert.match(CLIENTE, /queryDaTendencia\(janela, filtros\.channel\)/);
  assert.match(CLIENTE, /expedicao\/trend\?\$\{chaveTendencia\}/);
});

test("trocar de canal muda a chave da requisicao", () => {
  const sh = chaveDaRequisicao(FILTROS_PADRAO);
  const ml = chaveDaRequisicao(
    aplicarFiltro(FILTROS_PADRAO, { channel: "mercadolivre" }),
  );
  assert.notEqual(sh, ml);
});

test("resposta atrasada de um canal nao substitui a do outro", () => {
  // a fila ja' descartava por chave; a tendencia passou a fazer o mesmo
  assert.match(CLIENTE, /chaveTendVigente\.current !== minhaChave/);
  const ocorrencias = CLIENTE.match(/chaveTendVigente\.current !== minhaChave/g) ?? [];
  assert.equal(ocorrencias.length, 2, "sucesso E erro precisam descartar");
});

test("trocar de canal volta para a primeira pagina", () => {
  const f = { ...FILTROS_PADRAO, offset: 300 };
  const depois = aplicarFiltro(f, { channel: "mercadolivre" });
  assert.equal(depois.offset, 0);
});

test("filtros e paginacao preservam o canal", () => {
  const base = aplicarFiltro(FILTROS_PADRAO, { channel: "mercadolivre" });
  for (const mudanca of [
    { brands: ["kokeshi"] },
    { situacao: ["over_48h" as const] },
    { orderBy: "oldest" as const },
    { offset: 100 },
    { limit: 50 },
  ]) {
    const f = aplicarFiltro(base, mudanca);
    assert.equal(f.channel, "mercadolivre", JSON.stringify(mudanca));
    assert.match(construirQuery(f), /channel=mercadolivre/);
  }
});

// ---------------------------------------------------------------------------
// 4. Semantica do Mercado Livre
// ---------------------------------------------------------------------------
test("o ML nao tem prazo e a tela nao finge que tem", () => {
  assert.equal(canalTemPrazo("shopee"), true);
  assert.equal(canalTemPrazo("mercadolivre"), false);
});

test("zero vencidos do ML NAO vira desempenho perfeito", () => {
  const ml = montarKpis(totais(), [], "mercadolivre");
  for (const chave of ["overdue", "due24h"]) {
    const k = ml.find((x) => x.chave === chave)!;
    assert.equal(k.valor, null, chave);
    assert.equal(k.valorTexto, VALOR_SEM_PRAZO, chave);
    assert.equal(k.alerta, false, chave);
    assert.equal(k.nota, SEM_PRAZO_ML, chave);
  }
});

test("o ML ganha um cartao que NOMEIA a ausencia de prazo", () => {
  const ml = montarKpis(totais(), [], "mercadolivre");
  const k = ml.find((x) => x.chave === "sem_prazo")!;
  assert.ok(k, "falta o cartao de prazo indisponivel");
  assert.equal(k.valor, 714);
  assert.equal(k.alerta, false);
});

test("nenhum rotulo do ML menciona a Shopee", () => {
  for (const k of montarKpis(totais(), [], "mercadolivre")) {
    assert.doesNotMatch(k.rotulo, /Shopee/i, k.chave);
  }
});

test("os KPIs da Shopee ficam exatamente como eram", () => {
  const sh = montarKpis(totais({ overdue_count: 3, due_within_24h_count: 5 }), []);
  const overdue = sh.find((k) => k.chave === "overdue")!;
  assert.equal(overdue.rotulo, "Vencidos (prazo Shopee)");
  assert.equal(overdue.valor, 3);
  assert.equal(overdue.alerta, true);
  assert.equal(overdue.valorTexto, undefined);
  assert.equal(sh.find((k) => k.chave === "sem_prazo"), undefined);
});

test("over_48h continua sendo limiar interno, nos dois canais", () => {
  for (const canal of CANAIS) {
    const k = montarKpis(totais(), [], canal).find((x) => x.chave === "over48h")!;
    assert.match(k.rotulo, /limiar interno/i);
    assert.equal(k.valor, 179);
  }
});

// ---------------------------------------------------------------------------
// 5. Cobertura
// ---------------------------------------------------------------------------
function cobertura(over: Partial<Cobertura> = {}): Cobertura {
  return {
    expected_accounts: [],
    observed_accounts: [],
    missing_accounts: [],
    unexpected_accounts: [],
    accounts: [],
    brands_not_covered: [],
    ...over,
  };
}

test("Kokeshi coberta no ML: nenhum aviso de fora de escopo", () => {
  const avisos = avisosDeCobertura(
    cobertura({
      expected_accounts: ["1366932565", "2227056661"],
      observed_accounts: ["1366932565", "2227056661"],
      brands_not_covered: [],
    }),
  );
  assert.equal(avisos.filter((a) => a.tipo === "fora_do_escopo").length, 0);
  assert.equal(avisos.length, 0);
});

test("Kokeshi segue fora da cobertura na Shopee", () => {
  const avisos = avisosDeCobertura(cobertura({ brands_not_covered: ["kokeshi"] }));
  assert.equal(avisos.length, 1);
  assert.equal(avisos[0].tipo, "fora_do_escopo");
  assert.match(avisos[0].texto, /kokeshi/i);
});

test("conta ausente e' acusada pela identidade da conta", () => {
  const avisos = avisosDeCobertura(
    cobertura({
      expected_accounts: ["111", "222"],
      observed_accounts: ["111"],
      missing_accounts: ["222"],
    }),
  );
  assert.equal(avisos[0].tipo, "faltando");
  assert.match(avisos[0].texto, /222/);
});

// ---------------------------------------------------------------------------
// 6. Estados
// ---------------------------------------------------------------------------
test("channel_disabled tem estado proprio, diferente de sem fotografia", () => {
  const e = estadoDaTela({
    carregando: false,
    erroHttp: null,
    temFiltro: false,
    resposta: resposta({
      availability: "unavailable",
      unavailable_reason: "channel_disabled",
      totals: null,
    }),
  });
  assert.equal(e, "canal_desligado");
});

test("o estado de canal desligado e' pintado", () => {
  assert.match(CLIENTE, /estado === "canal_desligado"/);
});

test("os demais estados continuam distintos", () => {
  const casos: Array<[string, string]> = [
    ["feature_flag_disabled", "indisponivel_backend"],
    ["inconsistent_batch", "batch_inconsistente"],
    ["no_snapshot_published", "sem_snapshot"],
  ];
  for (const [motivo, esperado] of casos) {
    const e = estadoDaTela({
      carregando: false,
      erroHttp: null,
      temFiltro: false,
      resposta: resposta({
        availability: "unavailable",
        unavailable_reason: motivo,
        totals: null,
      }),
    });
    assert.equal(e, esperado, motivo);
  }
});

// ---------------------------------------------------------------------------
// 7. Higiene do payload
// ---------------------------------------------------------------------------
function linha(over: Partial<LinhaFila> = {}): LinhaFila {
  return {
    shop_account: "2227056661",
    brand: "kokeshi",
    created_at: null,
    paid_at: null,
    dispatch_deadline: null,
    deadline_source: "unavailable",
    deadline_status: "unavailable",
    operational_age_status: "within_48h",
    is_slow_vs_baseline: false,
    is_source_zombie: false,
    is_stalled: false,
    hours_open: 10,
    hours_overdue: null,
    logistic_type: "cross_docking",
    carrier: null,
    source_ingested_at: null,
    source_freshness_status: "fresh",
    timestamp_quality: "assumed",
    order_ref: null,
    ...over,
  } as LinhaFila;
}

test("sem order_ref a coluna de referencia nao existe", () => {
  assert.equal(mostrarColunaReferencia([linha(), linha()]), false);
  assert.equal(mostrarColunaReferencia([linha({ order_ref: "abc" })]), true);
});

test("nenhum identificador de pedido e' renderizado", () => {
  assert.doesNotMatch(CLIENTE, /marketplace_order_id/);
  assert.doesNotMatch(CLIENTE, /order_sn/);
  // e nenhum identificador vira link
  assert.doesNotMatch(CLIENTE, /href=\{[^}]*order/);
});

// ---------------------------------------------------------------------------
// 8. Acessibilidade do seletor
// ---------------------------------------------------------------------------
test("o seletor e' um radiogroup com nome acessivel", () => {
  assert.match(CLIENTE, /role="radiogroup"/);
  assert.match(CLIENTE, /aria-label="Canal da fotografia"/);
  assert.match(CLIENTE, /role="radio"/);
  assert.match(CLIENTE, /aria-checked=\{ativo\}/);
});

test("os alvos do seletor tem 44x44 e foco visivel", () => {
  assert.match(CLIENTE, /min-h-\[44px\] min-w-\[44px\]/);
  assert.match(CLIENTE, /focus-visible:outline-2/);
});

test("o seletor usa <button>, navegavel por teclado", () => {
  assert.match(CLIENTE, /type="button"\r?\n?\s*role="radio"/);
});

test("o titulo reflete o canal e a tela nao ganha um segundo h1", () => {
  assert.match(CLIENTE, /Expedição — \{ROTULO_CANAL\[filtros\.channel\]\}/);
  assert.doesNotMatch(CLIENTE, /<h1/);
  assert.equal(ROTULO_CANAL.mercadolivre, "Mercado Livre");
});

test("a URL e' lida DEPOIS da montagem, sem risco de hidratacao", () => {
  // `window.location` so' aparece dentro de efeito ou de callback
  const noRender = CLIENTE.split("useEffect")[0];
  assert.doesNotMatch(noRender, /window\./);
  assert.match(CLIENTE, /window\.history\.replaceState/);
});

// ---------------------------------------------------------------------------
// 9. Nada mais foi tocado
// ---------------------------------------------------------------------------
test("a rota /full-ml e as demais telas seguem intocadas", () => {
  for (const rota of ["app/full-ml/page.tsx", "app/pedidos/page.tsx"]) {
    const src = readFileSync(join(RAIZ, rota), "utf8");
    assert.doesNotMatch(src, /EXPEDICAO_ML_ENABLED/, rota);
  }
});

test("o menu continua atras da flag original", () => {
  const nav = readFileSync(join(RAIZ, "src/components/shell/NavList.tsx"), "utf8");
  assert.match(nav, /expedicaoHabilitado\(process\.env\.NEXT_PUBLIC_EXPEDICAO_ENABLED\)/);
  assert.doesNotMatch(nav, /EXPEDICAO_ML_ENABLED/);
});

test("o contrato documenta por que o ML nao tem prazo", () => {
  assert.match(CONTRATO, /0% preenchidos/);
  assert.match(SEM_PRAZO_ML, /N\/D/);
});


// ---------------------------------------------------------------------------
// 10. Troca de canal: o que atravessa e o que nao atravessa (EXP-3C2-R/V)
// ---------------------------------------------------------------------------
const CHEIO = aplicarFiltro(FILTROS_PADRAO, {
  brands: ["kokeshi", "apice"],
  accounts: ["apice"],
  situacao: ["overdue", "over_48h", "deadline_unavailable"],
  orderBy: "deadline",
  offset: 300,
});

test("conta NAO atravessa a troca de canal", () => {
  // `apice` e' nome de loja da Shopee; no ML a conta e' seller_id. Levar uma
  // para a outra devolve tela vazia que parece backlog zero.
  assert.deepEqual(aplicarCanal(CHEIO, "mercadolivre").accounts, []);
  const doMl = aplicarFiltro(FILTROS_PADRAO, {
    channel: "mercadolivre",
    accounts: ["2227056661"],
  });
  assert.deepEqual(aplicarCanal(doMl, "shopee").accounts, []);
});

test("marca ATRAVESSA: e' a mesma entidade nos dois canais", () => {
  assert.deepEqual(aplicarCanal(CHEIO, "mercadolivre").brands, ["kokeshi", "apice"]);
});

test("situacao de PRAZO cai ao entrar num canal sem prazo", () => {
  const ml = aplicarCanal(CHEIO, "mercadolivre");
  for (const s of SITUACOES_DE_PRAZO) {
    assert.ok(!ml.situacao.includes(s), s);
  }
  // as que continuam fazendo sentido ficam
  assert.ok(ml.situacao.includes("over_48h"));
  assert.ok(ml.situacao.includes("deadline_unavailable"));
});

test("voltando para a Shopee, as situacoes de prazo nao sao inventadas de volta", () => {
  const ml = aplicarCanal(CHEIO, "mercadolivre");
  const volta = aplicarCanal(ml, "shopee");
  assert.ok(!volta.situacao.includes("overdue"), "nao reaparece o que o usuario perdeu");
  assert.ok(volta.situacao.includes("over_48h"));
});

test("ordenar por prazo cai num canal sem prazo", () => {
  assert.equal(aplicarCanal(CHEIO, "mercadolivre").orderBy, "criticidade");
  assert.equal(aplicarCanal(CHEIO, "shopee").orderBy, "deadline");
});

test("troca de canal volta para a primeira pagina", () => {
  assert.equal(aplicarCanal(CHEIO, "mercadolivre").offset, 0);
});

test("trocar para o MESMO canal nao destroi o recorte", () => {
  // o cliente so' chama `aplicarCanal` quando o canal muda; esta e' a rede de
  // seguranca caso alguem remova essa guarda
  const igual = aplicarCanal(CHEIO, "shopee");
  assert.deepEqual(igual.brands, CHEIO.brands);
  assert.equal(igual.orderBy, "deadline");
});

// ---------------------------------------------------------------------------
// 11. Deep link: nenhuma requisicao antes de a URL ser resolvida
// ---------------------------------------------------------------------------
test("as duas buscas esperam a URL ser lida", () => {
  const guardas = CLIENTE.match(/if \(!urlPronta\) return;/g) ?? [];
  assert.equal(guardas.length, 2, "fila E tendencia precisam esperar");
});

test("com a flag desligada nao ha espera: comportamento identico ao de antes", () => {
  assert.match(CLIENTE, /useState\(!ML_LIGADO\)/);
});

test("urlPronta entra nas dependencias dos dois efeitos", () => {
  assert.match(CLIENTE, /\[chave, filtros, urlPronta\]/);
  assert.match(CLIENTE, /\[chaveTendencia, urlPronta\]/);
});

test("voltar/avancar do navegador ressincroniza o canal", () => {
  assert.match(CLIENTE, /addEventListener\("popstate"/);
  assert.match(CLIENTE, /removeEventListener\("popstate"/);
});

test("a troca de canal usa aplicarCanal, nao aplicarFiltro cru", () => {
  assert.doesNotMatch(CLIENTE, /aplicarFiltro\(f, \{ channel/);
  const usos = CLIENTE.match(/aplicarCanal\(f, canal\)/g) ?? [];
  assert.ok(usos.length >= 3, "montagem, popstate e seletor");
});
