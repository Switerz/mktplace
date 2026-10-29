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
import { canaisHabilitados, lerCanalExpedicao } from "../src/lib/expedicao-canal.ts";

const RAIZ = join(import.meta.dirname, "..");
const CLIENTE = readFileSync(
  join(RAIZ, "app/expedicao/ExpedicaoClient.tsx"),
  "utf8",
);
const CONTRATO = readFileSync(
  join(RAIZ, "src/lib/expedicao-contract.ts"),
  "utf8",
);
// EXPEDICAO-UX-UNIFICADA-1: a casca passou a ser dona do seletor unico
// (Shopee | Mercado Livre | TikTok Shop) e da flag do ML; o titulo vive no
// cabecalho compartilhado. As garantias seguem o codigo ate' la'.
const CASCA = readFileSync(join(RAIZ, "app/expedicao/ExpedicaoShell.tsx"), "utf8");
const CABECALHO = readFileSync(
  join(RAIZ, "src/components/expedicao/CabecalhoExpedicao.tsx"),
  "utf8",
);
const SEGMENTED = readFileSync(join(RAIZ, "src/components/ui/Segmented.tsx"), "utf8");

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
    CASCA,
    /const ML_LIGADO = process\.env\.NEXT_PUBLIC_EXPEDICAO_ML_ENABLED === "true";/,
  );
  for (const v of ["true", "false", "", "1", "TRUE"]) {
    assert.equal(expedicaoMlHabilitado(v), v === "true", v);
  }
});

test("a flag do ML e' SEPARADA da flag da rota", () => {
  assert.match(CASCA, /NEXT_PUBLIC_EXPEDICAO_ML_ENABLED/);
  // a rota continua sendo controlada pela flag original, no servidor
  const page = readFileSync(join(RAIZ, "app/expedicao/page.tsx"), "utf8");
  assert.match(page, /expedicaoHabilitado\(process\.env\.NEXT_PUBLIC_EXPEDICAO_ENABLED\)/);
  assert.doesNotMatch(page, /NEXT_PUBLIC_EXPEDICAO_ML_ENABLED/);
});

test("flag desligada: nenhum caminho leva ao canal do ML", () => {
  // EXPEDICAO-UX-UNIFICADA-1: o seletor e a leitura do canal moram na casca.
  // As opcoes do seletor e a leitura da URL passam pelas MESMAS flags.
  assert.match(CASCA, /canaisHabilitados\(FLAGS\)/);
  assert.match(CASCA, /lerCanalExpedicao\(window\.location\.search, FLAGS\)/);
  assert.deepEqual(canaisHabilitados({ ml: false, tiktok: false }), ["shopee"]);
  assert.deepEqual(canaisHabilitados({ ml: false, tiktok: true }), ["shopee", "tiktokshop"]);
  // montagem E voltar/avancar passam pelo mesmo leitor, com a flag aplicada
  assert.match(CASCA, /const aoVoltar = \(\) => lerDaUrl\(\);/);
  assert.equal(lerCanalExpedicao("?channel=mercadolivre", { ml: false, tiktok: false }), "shopee");
  // A fila nao le mais o canal da URL, e nenhum literal do ML solto nela
  // nem na casca: o canal so' existe atras das flags.
  assert.doesNotMatch(CLIENTE, /lerCanalDaUrl|get\("channel"\)/);
  assert.doesNotMatch(CLIENTE, /"mercadolivre"/);
  assert.doesNotMatch(CASCA, /"mercadolivre"/);
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
  // EXPEDICAO-UX-JANELA-CUSTOMIZAVEL-1: a janela e' guardada em dias e o
  // helper converte para horas — ainda pelo MESMO canal da fila.
  assert.match(CLIENTE, /queryDaTendenciaEmDias\(janela, filtros\.channel\)/);
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
  // EXPEDICAO-UX-UNIFICADA-1: o seletor unico mora na casca e e' o
  // `Segmented` compartilhado. A garantia e' a mesma e o teste segue a
  // garantia, nao o lugar dela.
  assert.match(CASCA, /<Segmented\s+rotulo="Canal da Expedição"/);
  assert.match(SEGMENTED, /role="radiogroup"/);
  assert.match(SEGMENTED, /aria-label=\{rotuladoPor \? undefined : rotulo\}/);
  assert.match(SEGMENTED, /role="radio"/);
  assert.match(SEGMENTED, /aria-checked=\{ativo\}/);
});

test("os alvos do seletor tem 44x44 e foco visivel", () => {
  assert.match(CLIENTE, /min-h-\[44px\] min-w-\[44px\]/);
  assert.match(CLIENTE, /focus-visible:outline-2/);
});

test("o seletor usa <button>, navegavel por teclado", () => {
  assert.match(SEGMENTED, /type="button"\r?\n?\s*role="radio"/);
});

test("o titulo reflete o canal e a tela nao ganha um segundo h1", () => {
  // O titulo e' do cabecalho compartilhado; a fila passa o canal DOS DADOS
  // (`filtros.channel`), nao o do seletor.
  assert.match(CLIENTE, /canal=\{ROTULO_CANAL\[filtros\.channel\]\}/);
  assert.match(CABECALHO, /Expedição — \{canal\}/);
  assert.doesNotMatch(CLIENTE, /<h1/);
  assert.doesNotMatch(CABECALHO, /<h1/);
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
  // QA T-0010 (M2): a ausencia e' escrita "indisponível", nunca "N/D" nem 0.
  assert.match(SEM_PRAZO_ML, /indisponível/);
  assert.doesNotMatch(SEM_PRAZO_ML, /N\/D/);
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

test("a URL e' lida antes da primeira busca, com ou sem a flag do ML", () => {
  // Antes deste gate, sem a flag nao havia o que ler e `urlPronta` nascia
  // `true`. Agora `?dias=` vale nos dois casos: nascer `true` faria um link
  // de 10 dias pedir primeiro a janela padrao.
  assert.match(CLIENTE, /const \[urlPronta, setUrlPronta\] = useState\(false\);/);
  assert.doesNotMatch(CLIENTE, /useState\(!ML_LIGADO\)/);
  // E o canal ja' chega da casca, que so' monta a fila depois de ler a URL:
  // a primeira requisicao sai com o canal do link.
  assert.match(CLIENTE, /useState<Filtros>\(\(\) => aplicarCanal\(FILTROS_PADRAO, canal\)\)/);
});

test("urlPronta entra nas dependencias dos dois efeitos", () => {
  assert.match(CLIENTE, /\[chave, filtros, urlPronta\]/);
  // QA T-0010: a nova tentativa da tendencia entra nas dependencias.
  assert.match(CLIENTE, /\[chaveTendencia, urlPronta, tentativaTendencia\]/);
});

test("voltar/avancar do navegador ressincroniza o canal", () => {
  assert.match(CLIENTE, /addEventListener\("popstate"/);
  assert.match(CLIENTE, /removeEventListener\("popstate"/);
});

test("a troca de canal usa aplicarCanal, nao aplicarFiltro cru", () => {
  assert.doesNotMatch(CLIENTE, /aplicarFiltro\(f, \{ channel/);
  // EXPEDICAO-UX-UNIFICADA-1: montagem, popstate e seletor convergem na casca,
  // que repassa o canal como prop. Na fila restam dois pontos: o estado
  // inicial e o efeito da troca — os dois por `aplicarCanal`.
  assert.match(CLIENTE, /aplicarCanal\(FILTROS_PADRAO, canal\)/);
  assert.match(
    CLIENTE,
    /useEffect\(\(\) => \{\s*setFiltros\(\(f\) => \(f\.channel === canal \? f : aplicarCanal\(f, canal\)\)\);\s*\}, \[canal\]\);/,
  );
});

// ===========================================================================
// T-0010 — Jornada (contrato v1 + v1.1 + v1.2)
// ===========================================================================
import { register as registrarLoader } from "node:module";
import {
  canalDaJornada,
  estadoDaJornada,
  explicarMotivo,
  lerJornada,
  queryDaJornada,
  rotuloDaCoorte,
  textoDaSobreposicao,
  type RespostaJornada,
} from "../src/lib/expedicao-jornada.ts";

registrarLoader("./helpers/tsx-loader.mjs", import.meta.url);

/** Exemplo REAL do ML enviado pelo backend (v1.1). */
const JORNADA_ML = {
  channel: "mercadolivre",
  status: "ok",
  fila_aberta: {
    grain: "shipment",
    base: 10,
    etapas: [{ key: "aguardando_expedicao", label: "Aguardando expedição", count: 10 }],
    etapas_fecham: true,
    sobreposicoes: [
      { key: "vencido", count: null, reason: "o Mercado Livre não publica prazo de despacho" },
      { key: "sem_prazo", count: 10 },
    ],
    limitacoes: ["substatus do shipment não publicado na fila"],
  },
  coorte: null,
  limitacoes: ["o Mercado Livre não publica prazo de despacho"],
};

function jornadaTikTok(over: Record<string, unknown> = {}) {
  return {
    channel: "tiktok",
    status: "ok",
    reason: null,
    brands_applied: [],
    generated_at: "2026-10-26T21:00:00Z",
    fila_aberta: null,
    coorte: {
      eixo: "paid_date",
      evento: "coleta",
      janela: { inicio: "2026-10-19", fim: "2026-10-25", dias: 7 },
      base: 4310,
      etapas: [
        { key: "fora_amostra", label: "Amostras", count: 12 },
        { key: "cancelado_antes", label: "Cancelados antes do prazo", count: 80 },
        { key: "aguardando", label: "Aguardando", count: 300 },
        { key: "concluido", label: "Coletados", count: 3918 },
      ],
      etapas_fecham: true,
      sobreposicoes: [
        { key: "aguardando_vencido", count: 41, subconjunto_de: "aguardando" },
        { key: "concluido_atrasado", count: 150, subconjunto_de: "concluido" },
      ],
      fora_da_coorte: [],
      maturidade: { dias_maduros: 6, dias_imaturos: 1 },
      limitacoes: [],
      ...over,
    },
    limitacoes: ["o TikTok não publica fila aberta em grão de pedido"],
  };
}

type Ok = { ok: true; resposta: RespostaJornada };

test("jornada: payload real do ML passa, com sobreposicao nula como 'indisponível'", () => {
  const r = lerJornada(JORNADA_ML, "mercadolivre");
  assert.ok(r.ok, r.ok ? "" : r.erro);
  const j = (r as Ok).resposta;
  assert.equal(j.coorte, null);
  assert.deepEqual(j.limitacoes, ["o Mercado Livre não publica prazo de despacho"]);
  const f = j.fila_aberta!;
  const vencido = f.sobreposicoes.find((s) => s.key === "vencido")!;
  assert.equal(vencido.count, null, "nulo nao vira zero");
  const t = textoDaSobreposicao(vencido, f.etapas, f.base);
  assert.equal(t, "Vencidos: indisponível — o Mercado Livre não publica prazo de despacho");
  assert.equal(
    textoDaSobreposicao(f.sobreposicoes[1], f.etapas, f.base),
    "10 sem prazo publicado dentro de Aguardando expedição (100,0% · 10 de 10)",
  );
});

test("jornada: TikTok valido, coorte rotulada pela janela que termina ontem", () => {
  const r = lerJornada(jornadaTikTok(), "tiktok");
  assert.ok(r.ok);
  const c = (r as Ok).resposta.coorte!;
  assert.equal(rotuloDaCoorte(c), "pedidos pagos de 19/10 a 25/10");
  assert.equal(c.publicado_em, null, "v1.2 opcional: ausente vira null");
  assert.equal(c.desatualizada, null);
  assert.ok(lerJornada({ ...jornadaTikTok(), channel: "tiktokshop" }, "tiktok").ok);
});

test("jornada: v1.2 — publicado_em e desatualizada", () => {
  const r = lerJornada(jornadaTikTok({ publicado_em: "2026-10-26T09:00:00Z", desatualizada: true }), "tiktok");
  assert.ok(r.ok);
  const c = (r as Ok).resposta.coorte!;
  assert.equal(c.publicado_em, "2026-10-26T09:00:00Z");
  assert.equal(c.desatualizada, true);
  assert.equal(lerJornada(jornadaTikTok({ desatualizada: "sim" }), "tiktok").ok, false);
});

test("jornada: sobreposicao nunca e' somada e e' mostrada como subconjunto da etapa", () => {
  const c = (lerJornada(jornadaTikTok(), "tiktok") as Ok).resposta.coorte!;
  assert.equal(c.etapas.reduce((s, e) => s + e.count, 0), c.base, "as etapas fecham sem as sobreposicoes");
  assert.equal(
    textoDaSobreposicao(c.sobreposicoes[0], c.etapas, c.base, "coleta"),
    "41 vencidos ainda sem envio dentro de Aguardando (13,7% · 41 de 300)",
  );
  const comp = readFileSync(join(RAIZ, "src/components/expedicao/JornadaPedidos.tsx"), "utf8");
  assert.doesNotMatch(comp, /\.reduce\(/, "o componente nao soma nada");
});

test("jornada: payload invalido e' REJEITADO", () => {
  const casos: [string, unknown][] = [
    ["nao objeto", "x"],
    ["canal fora da allowlist", { ...jornadaTikTok(), channel: "amazon" }],
    ["canal trocado", JORNADA_ML],
    ["status desconhecido", { ...jornadaTikTok(), status: "great" }],
    ["contagem negativa", jornadaTikTok({ base: -1 })],
    ["contagem fracionada", jornadaTikTok({ base: 4310.5 })],
    ["fecham=true sem fechar", jornadaTikTok({ base: 9999 })],
    ["sobreposicao maior que a etapa", jornadaTikTok({ sobreposicoes: [{ key: "aguardando_vencido", count: 301, subconjunto_de: "aguardando" }] })],
    ["subconjunto de etapa inexistente", jornadaTikTok({ sobreposicoes: [{ key: "x", count: 1, subconjunto_de: "nada" }] })],
    ["janela invertida", jornadaTikTok({ janela: { inicio: "2026-10-25", fim: "2026-10-19", dias: 7 } })],
    ["eixo desconhecido", jornadaTikTok({ eixo: "created_at" })],
  ];
  for (const [nome, payload] of casos) {
    assert.equal(lerJornada(payload, "tiktok").ok, false, nome);
  }
});

test("jornada: etapas_fecham=false com status partial e' aceito; 'ok' assim e' rejeitado", () => {
  const p = { ...jornadaTikTok({ base: 9999, etapas_fecham: false }), status: "partial", reason: "coorte: dias_sem_publicacao (2)" };
  const r = lerJornada(p, "tiktok");
  assert.ok(r.ok, r.ok ? "" : r.erro);
  assert.equal(lerJornada({ ...p, status: "ok" }, "tiktok").ok, false);
  assert.equal(explicarMotivo(p.reason), "Coorte: 2 dia(s) sem publicação na janela");
  assert.equal(explicarMotivo("coorte: table_not_migrated"), "Coorte: a tabela ainda não foi publicada nesta base");
  assert.equal(explicarMotivo("coorte: codigo_novo"), "Coorte: motivo não detalhado pela API");
});

test("jornada: 404, rede e disabled viram 'indisponível'; 5xx e invalido viram erro", () => {
  const base = { carregando: false, http: 200, erroDeRede: false, resultado: null };
  assert.equal(estadoDaJornada({ ...base, http: 404 }), "indisponivel");
  assert.equal(estadoDaJornada({ ...base, http: null, erroDeRede: true }), "indisponivel");
  assert.equal(
    estadoDaJornada({ ...base, resultado: lerJornada({ channel: "shopee", status: "disabled" }, "shopee") }),
    "indisponivel",
  );
  assert.equal(estadoDaJornada({ ...base, http: 500 }), "erro");
  assert.equal(estadoDaJornada({ ...base, resultado: lerJornada({ channel: "shopee" }, "shopee") }), "erro");
  assert.equal(
    estadoDaJornada({
      ...base,
      resultado: lerJornada({ channel: "shopee", status: "unavailable", reason: "coorte: table_not_migrated" }, "shopee"),
    }),
    "fora_do_ar",
  );
  assert.equal(estadoDaJornada({ ...base, carregando: true }), "carregando");
});

test("jornada: a marca da URL vai como `brands`; evento so' no TikTok; dias nao no ML", () => {
  assert.equal(canalDaJornada("tiktokshop"), "tiktok");
  assert.equal(queryDaJornada("shopee", "apice"), "/api/v1/expedicao/jornada?channel=shopee&brands=apice");
  assert.equal(
    queryDaJornada("tiktok", "kokeshi", { dias: 14, evento: "despacho" }),
    "/api/v1/expedicao/jornada?channel=tiktok&brands=kokeshi&days=14&event=despacho",
  );
  assert.equal(queryDaJornada("mercadolivre", null, { dias: 7, evento: "coleta" }), "/api/v1/expedicao/jornada?channel=mercadolivre");
  assert.equal(queryDaJornada("shopee", null, { evento: "despacho" }), "/api/v1/expedicao/jornada?channel=shopee");
  // UI-EXPEDICAO-1 (decisao do usuario): o periodo do topo comanda a coorte.
  assert.match(CLIENTE, /useJornada\(API, filtros\.channel, marcaAtiva, \{ dias: janela \}\)/);
});

const { createElement: hj } = await import("react");
const { renderToStaticMarkup: htmlDe } = await import("react-dom/server");
const { default: JornadaPedidos } = await import("../src/components/expedicao/JornadaPedidos.tsx");

function render(http: number | null, json: unknown, canal: "shopee" | "mercadolivre" | "tiktok", erroDeRede = false) {
  return htmlDe(
    hj(JornadaPedidos, {
      carga: { carregando: false, http, erroDeRede, resultado: json === undefined ? null : lerJornada(json, canal) },
      aoTentarDeNovo: () => {},
    }),
  );
}

test("jornada 404 e rede: secao discreta, sem alerta vermelho", () => {
  for (const html of [render(404, undefined, "shopee"), render(null, undefined, "shopee", true)]) {
    assert.match(html, /Jornada indisponível\./);
    assert.doesNotMatch(html, /role="alert"|crit/);
  }
});

test("jornada ML: fila aberta com etapas e coorte indisponivel com a limitacao", () => {
  const html = render(200, JORNADA_ML, "mercadolivre");
  assert.match(html, /Agora — fila aberta/);
  assert.match(html, /Aguardando expedição/);
  assert.match(html, /100,0% · 10 de 10/);
  assert.match(html, /Vencidos: indisponível — o Mercado Livre não publica prazo de despacho/);
  assert.match(html, /Coorte<\/h3>[\s\S]*Indisponível neste canal: o Mercado Livre não publica prazo de despacho/);
  assert.match(html, /substatus do shipment não publicado na fila/);
});

test("jornada TikTok: coorte rotulada, publicado em e selo de frescor; parcial avisado", () => {
  const html = render(
    200,
    {
      ...jornadaTikTok({ publicado_em: "2026-10-26T09:00:00Z", desatualizada: true, base: 9999, etapas_fecham: false }),
      status: "partial",
      reason: "coorte: dias_sem_publicacao (1)",
    },
    "tiktok",
  );
  assert.match(html, /Coorte — pedidos pagos de 19\/10 a 25\/10/);
  assert.match(html, /publicado em <time dateTime="2026-10-26T09:00:00Z">\d{2}\/\d{2} \d{2}:\d{2}<\/time>/);
  assert.match(html, />Desatualizada</);
  assert.match(html, /Parcial:/);
  assert.match(html, /1 dia\(s\) sem publicação na janela/);
  assert.match(html, /Indisponível neste canal: o TikTok não publica fila aberta/);
});

// ===========================================================================
// R21 (HIGH) — secao indisponivel nao apaga a secao valida
// ===========================================================================
/** Shopee com a fila valida (base 10) e a coorte nula por migration ausente. */
function shopeeFilaSemCoorte() {
  return {
    channel: "shopee",
    status: "unavailable",
    reason: "coorte: table_not_migrated",
    brands_applied: [],
    generated_at: "2026-10-26T21:00:00Z",
    fila_aberta: {
      grain: "pedido",
      snapshot_at: "2026-10-26T20:00:00Z",
      freshness: "fresh",
      base: 10,
      etapas: [{ key: "aguardando_expedicao", label: "Aguardando expedição", count: 10 }],
      etapas_fecham: true,
      sobreposicoes: [{ key: "vencido", count: 2 }],
      limitacoes: [],
    },
    coorte: null,
    limitacoes: [],
  };
}

test("R21: unavailable com UMA secao presente e' 'pronta'; so' as duas nulas viram fora_do_ar", () => {
  const base = { carregando: false, http: 200, erroDeRede: false };
  assert.equal(estadoDaJornada({ ...base, resultado: lerJornada(shopeeFilaSemCoorte(), "shopee") }), "pronta");
  const tk = { ...jornadaTikTok(), status: "unavailable", reason: "fila_aberta: no_snapshot_published" };
  assert.equal(estadoDaJornada({ ...base, resultado: lerJornada(tk, "tiktok") }), "pronta");
  const nenhuma = { ...shopeeFilaSemCoorte(), fila_aberta: null, reason: "fila_aberta: no_snapshot_published; coorte: table_not_migrated" };
  assert.equal(estadoDaJornada({ ...base, resultado: lerJornada(nenhuma, "shopee") }), "fora_do_ar");
});

test("R21 render: fila valida + coorte nula (table_not_migrated) -> fila visivel, coorte indisponivel com o motivo", () => {
  const html = render(200, shopeeFilaSemCoorte(), "shopee");
  assert.match(html, /data-testid="exp-jornada"/, "os blocos aparecem");
  assert.match(html, /Agora — fila aberta/);
  assert.match(html, /10 pedidos pagos aguardando expedição\./);
  assert.match(html, /100,0% · 10 de 10/);
  assert.match(html, /exp-jornada-coorte-indisponivel/);
  assert.match(html, /Coorte<\/h3>[\s\S]*Indisponível agora: a tabela ainda não foi publicada nesta base\./);
  assert.doesNotMatch(html, /Jornada indisponível/, "a secao inteira nao some");
  assert.doesNotMatch(html, /table_not_migrated/, "o codigo cru nao aparece");
});

test("R21 render: fila nula + coorte valida (TikTok) -> coorte visivel", () => {
  const html = render(
    200,
    { ...jornadaTikTok(), status: "unavailable", reason: "fila_aberta: no_snapshot_published" },
    "tiktok",
  );
  assert.match(html, /data-testid="exp-jornada"/);
  assert.match(html, /Coorte — pedidos pagos de 19\/10 a 25\/10/);
  assert.match(html, /4\.310 pedidos/);
  assert.match(html, /exp-jornada-fila_aberta-indisponivel/);
  assert.match(html, /Agora — fila aberta<\/h3>[\s\S]*Indisponível agora: nenhuma fotografia publicada ainda\./);
  assert.doesNotMatch(html, /Jornada indisponível/);
});

test("R21 render: as duas secoes nulas -> 'Jornada indisponível' geral, com o motivo", () => {
  const html = render(
    200,
    { ...shopeeFilaSemCoorte(), fila_aberta: null, reason: "fila_aberta: no_snapshot_published; coorte: table_not_migrated" },
    "shopee",
  );
  assert.match(html, /Jornada indisponível agora\./);
  assert.match(html, /Coorte: a tabela ainda não foi publicada nesta base/);
  assert.doesNotMatch(html, /data-testid="exp-jornada"/, "nenhum bloco desenhado");
});

test("R21 render: partial por fonte_desatualizada -> secao visivel com selo de desatualizada", () => {
  const p = {
    ...jornadaTikTok({ publicado_em: "2026-10-26T09:00:00Z", desatualizada: true }),
    status: "partial",
    reason: "coorte: fonte_desatualizada",
  };
  const html = render(200, p, "tiktok");
  assert.match(html, /Coorte — pedidos pagos de 19\/10 a 25\/10/);
  assert.match(html, /4\.310 pedidos/);
  assert.match(html, />Desatualizada</, "chip de frescor");
  assert.match(html, /exp-jornada-coorte-desatualizada/, "aviso no bloco");
  assert.match(html, /Parcial:/);
  assert.match(html, /Coorte: a fonte não recebe dado novo/);
  // sem o booleano (opcional no parser), o `reason` da secao mantem o selo
  const semBool = render(200, { ...p, coorte: { ...p.coorte, desatualizada: undefined } }, "tiktok");
  assert.match(semBool, />Desatualizada</);
  assert.match(semBool, /exp-jornada-coorte-desatualizada/);
});

test("R21: motivo com detalhe entre parenteses vira frase; zumbi nao afirma fonte parada", () => {
  assert.match(explicarMotivo("fila_aberta: etapas_nao_fecham (soma 9 != base 10)")!, /^Fila aberta: as etapas não fecham/);
  assert.equal(explicarMotivo("coorte: dias_sem_publicacao (2)"), "Coorte: 2 dia(s) sem publicação na janela");
  const z = textoDaSobreposicao({ key: "zumbi_fonte", count: 1, reason: null, subconjunto_de: null }, [], 10);
  assert.match(z, /abertos há mais de 30 dias/);
  assert.doesNotMatch(z, /parad/);
});

// ===========================================================================
// QA T-0010 — menores (M2–M6) e lacunas (retry da tendencia, ? nas tabelas)
// ===========================================================================
import { formatarDecimal, formatarPercentualComBase as pctComBase } from "../src/lib/expedicao-formato.ts";
import { formatarHoras as horasFmt, VALOR_SEM_PRAZO as SEM_PRAZO_VALOR, avisosDeCobertura as avisosCob } from "../src/lib/expedicao-contract.ts";

test("M2: horas com virgula, 'indisponível' no lugar de N/D, '<0,1%' para contagem > 0", () => {
  assert.equal(horasFmt(33), "33,0 h");
  assert.equal(horasFmt(20), "20,0 h");
  assert.equal(formatarDecimal(1.25, 1), "1,3");
  assert.equal(SEM_PRAZO_VALOR, "indisponível");
  assert.equal(pctComBase(1, 5000), "<0,1% · 1 de 5.000");
  assert.equal(pctComBase(1, 50000, { casas: 2 }), "<0,01% · 1 de 50.000");
  assert.equal(pctComBase(0, 5000), "0,0% · 0 de 5.000", "zero medido continua zero");
});

test("M3: os reason com prefixo de secao viram frase", () => {
  for (const [r, esperado] of [
    ["fila_aberta: etapas_nao_fecham", /^Fila aberta: as etapas não fecham com a base/],
    ["coorte: desatualizada", /^Coorte: publicação desatualizada/],
    ["coorte: publicacao_desatualizada", /^Coorte: publicação desatualizada/],
    ["coorte: dias_sem_publicacao (3)", /^Coorte: 3 dia\(s\) sem publicação na janela$/],
    ["coorte: table_not_migrated", /^Coorte: a tabela ainda não foi publicada nesta base$/],
  ] as const) {
    const t = explicarMotivo(r)!;
    assert.match(t, esperado, r);
    assert.doesNotMatch(t, /não detalhado/, r);
  }
});

test("M5: textos de cobertura com acento e marca com o rotulo de exibicao", () => {
  const [aviso] = avisosCob({
    expected_accounts: [], observed_accounts: [], missing_accounts: [], unexpected_accounts: [], accounts: [],
    brands_not_covered: ["apice"],
  });
  assert.match(aviso.texto, /^ÁPICE não é coberta por esta fonte e por isso não aparece nos números — ausência de cobertura, não backlog zero\.$/);
  const contrato = readFileSync(join(RAIZ, "src/lib/expedicao-contract.ts"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  for (const literal of contrato.match(/"[^"\n]*"/g) ?? []) {
    assert.doesNotMatch(literal, /\bnao e'|\bnao ha\b|\bentao\b|\bUniao\b|\bnumeros\b/, literal);
  }
  assert.match(CLIENTE, /<td className="text-ink-muted">\{brandLabel\(l\.brand\)\}<\/td>/);
  assert.match(CLIENTE, /\{brandLabel\(f\.brand\)\}/);
});

test("M4/M6: textos de estado comuns e fluxo vazio com estado proprio", () => {
  const tk = readFileSync(join(RAIZ, "src/components/expedicao/TikTokDispatchPanel.tsx"), "utf8");
  for (const f of [CLIENTE, tk]) {
    assert.match(f, /titulo=\{TEXTO_ESTADO\.carregando\}/);
    assert.match(f, /titulo=\{TEXTO_ESTADO\.erro\}/);
  }
  assert.match(CLIENTE, /<StateBlock tipo="vazio" titulo=\{TEXTO_ESTADO\.vazio\} compacto \/>/);
  assert.match(tk, /\{fluxo\.length === 0 \? \(\s*<p role="status"[^>]*>\s*Nenhum dia de pagamento na janela consultada\./);
});

test("lacunas: tendencia com nova tentativa e ? nos cabecalhos de contagem", () => {
  assert.match(CLIENTE, /aoTentarDeNovo=\{\(\) => setTentativaTendencia\(\(n\) => n \+ 1\)\}/);
  for (const k of ["overdue", "over48h", "stalled"]) {
    assert.ok(CLIENTE.includes(`{ajudaDaFila("${k}")}`), k);
  }
});
