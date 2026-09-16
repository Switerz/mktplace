/**
 * Contrato da tela "Full Mercado Livre" — Gate FULL-1D.
 *
 * `node --test` nao tem DOM: aqui nao se mede pixel. Estas assercoes travam o
 * CONTRATO DE DADO (tipos, formatacao, ausencia x zero, janela) e o CONTRATO DE
 * CLASSE (a regra explicita que produz alvo de 44px, scroll interno, foco
 * visivel). A medicao visual pertence ao QA em navegador; um teste estatico que
 * passasse enquanto a tela renderizasse errado seria pior que nenhum.
 *
 * O payload de referencia e' o de agosto/2026 SERVIDO DE VERDADE pela API no
 * gate FULL-1C-R2 -- inclusive o detalhe que quebraria a tela em silencio: o
 * backend serializa `Decimal` como STRING.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  AVISO_COBERTURA,
  AVISO_JANELA,
  AVISO_MANUAL_SNAPSHOT,
  AVISO_SEM_ESTOQUE,
  CLASS_DESCRICAO,
  CLASS_LABEL,
  FRESHNESS_LABEL,
  LOAD_MODE_LABEL,
  MAX_RANGE_DAYS,
  PERIOD_PRESETS,
  SEM_DADO,
  alertas,
  buildQuery,
  byClass,
  classesPresentes,
  fmtCount,
  fmtGmv,
  fmtShare,
  fmtTempo,
  isVazioReal,
  parseGmv,
  presetRange,
  rangeDays,
  serieDiaria,
  validateRange,
  type MLFulfillmentResponse,
} from "../src/lib/ml-fulfillment.ts";

const PAGE = readFileSync(new URL("../app/full-ml/page.tsx", import.meta.url), "utf8");
const TREND = readFileSync(
  new URL("../src/components/MLFulfillmentTrend.tsx", import.meta.url), "utf8");
const NAV = readFileSync(
  new URL("../src/components/shell/nav-config.ts", import.meta.url), "utf8");
const CLIENT = readFileSync(
  new URL("../src/lib/api-client.ts", import.meta.url), "utf8");

/** Agosto/2026, como a API realmente respondeu. Dinheiro como STRING. */
const AGOSTO: MLFulfillmentResponse = {
  date_from: "2026-08-01",
  date_to: "2026-08-31",
  brands: [],
  share_full_gmv: 0.8061401158192494,
  share_full_orders: 0.8188308410582583,
  share_full_units: 0.8205355202350719,
  paid_gmv_total: "4526767.38",
  paid_orders_total: 59116,
  paid_units_total: 60909,
  by_class: [
    {
      fulfillment_class: "full", paid_gmv: "3649208.78", paid_orders: 48406,
      paid_units: 49973, eligible_orders: 50492, cancelled_orders: 2065,
      other_orders: 21, cancellation_rate: 0.04089756793155351,
      handling: { seconds_avg: 103243.6, hours_avg: 28.678, days_avg: 1.195, sample_count: 49118 },
      delivery: { seconds_avg: 228960, hours_avg: 63.6, days_avg: 2.65, sample_count: 48728 },
    },
    {
      fulfillment_class: "non_full", paid_gmv: "877558.60", paid_orders: 10710,
      paid_units: 10936, eligible_orders: 11217, cancelled_orders: 491,
      other_orders: 16, cancellation_rate: 0.043773,
      handling: { seconds_avg: 258588, hours_avg: 71.83, days_avg: 2.99, sample_count: 10887 },
      delivery: { seconds_avg: 422496, hours_avg: 117.36, days_avg: 4.89, sample_count: 10772 },
    },
    {
      fulfillment_class: "unknown", paid_gmv: "0", paid_orders: 0,
      paid_units: 0, eligible_orders: 8, cancelled_orders: 8,
      other_orders: 0, cancellation_rate: 1.0,
      handling: { seconds_avg: null, hours_avg: null, days_avg: null, sample_count: 0 },
      delivery: { seconds_avg: null, hours_avg: null, days_avg: null, sample_count: 0 },
    },
  ],
  by_logistic_type: [
    { logistic_type_original: "fulfillment", fulfillment_class: "full", paid_gmv: "3649208.78", paid_orders: 48406, paid_units: 49973 },
    { logistic_type_original: "cross_docking", fulfillment_class: "non_full", paid_gmv: "877558.60", paid_orders: 10710, paid_units: 10936 },
  ],
  daily: [
    { ref_date: "2026-08-01", fulfillment_class: "full", paid_gmv: "147285.06", paid_orders: 1899, paid_units: 1968, eligible_orders: 2007, cancelled_orders: 107 },
    { ref_date: "2026-08-01", fulfillment_class: "non_full", paid_gmv: "30000.00", paid_orders: 400, paid_units: 410, eligible_orders: 420, cancelled_orders: 20 },
    { ref_date: "2026-08-02", fulfillment_class: "full", paid_gmv: "100000.00", paid_orders: 1000, paid_units: 1000, eligible_orders: 1050, cancelled_orders: 50 },
  ],
  listings: { full_only: 271, mixed: 222, non_full_only: 90, total: 583 },
  migration_opportunities: [
    { item_id: "MLB7237165078", brand: "kokeshi", non_full_gmv: "76898.70", non_full_units: 513, full_gmv: "16489.00", listing_class: "mixed" },
  ],
  quality: {
    unmatched_orders: 8, missing_shipping_items: 0,
    unknown_logistic_type_orders: 8, is_complete: true,
    limitations: ["a", "b", "c", "d", "e", "f"],
  },
  freshness: {
    freshness_status: "fresh", refreshed_at: "2026-09-16T16:59:20.671893Z",
    source_max_date: "2026-09-15", age_hours: 0.179, load_mode: "manual_snapshot",
  },
};

// ---------------------------------------------------------------------------
// Mapeamento integral do payload
// ---------------------------------------------------------------------------

test("todo campo do contrato da API tem tipo declarado e e' lido", () => {
  const raiz = Object.keys(AGOSTO).sort();
  assert.deepEqual(raiz, [
    "brands", "by_class", "by_logistic_type", "daily", "date_from", "date_to",
    "freshness", "listings", "migration_opportunities", "paid_gmv_total",
    "paid_orders_total", "paid_units_total", "quality", "share_full_gmv",
    "share_full_orders", "share_full_units",
  ]);
  assert.deepEqual(Object.keys(AGOSTO.quality).sort(), [
    "is_complete", "limitations", "missing_shipping_items",
    "unknown_logistic_type_orders", "unmatched_orders",
  ]);
  assert.deepEqual(Object.keys(AGOSTO.freshness).sort(), [
    "age_hours", "freshness_status", "load_mode", "refreshed_at", "source_max_date",
  ]);
  assert.deepEqual(Object.keys(AGOSTO.listings).sort(), [
    "full_only", "mixed", "non_full_only", "total",
  ]);
});

test("dinheiro chega como STRING, nao numero", () => {
  // Se algum dia virar numero, este teste avisa antes de a tela mostrar NaN.
  assert.equal(typeof AGOSTO.paid_gmv_total, "string");
  assert.equal(typeof AGOSTO.by_class[0].paid_gmv, "string");
  assert.equal(typeof AGOSTO.migration_opportunities[0].non_full_gmv, "string");
  // Shares e taxas continuam numeros.
  assert.equal(typeof AGOSTO.share_full_gmv, "number");
  assert.equal(typeof AGOSTO.by_class[0].cancellation_rate, "number");
});

test("shares oficiais de agosto sao os da API, sem recalculo", () => {
  assert.equal(fmtShare(AGOSTO.share_full_gmv), "80,61%");
  assert.equal(fmtShare(AGOSTO.share_full_orders), "81,88%");
  assert.equal(fmtShare(AGOSTO.share_full_units), "82,05%");
});

test("a tela nao recalcula share agregado a partir do GMV", () => {
  // Nenhuma divisao de paid_gmv por paid_gmv_total no codigo da pagina.
  assert.ok(!/paid_gmv_total\s*\)?\s*\//.test(PAGE));
  assert.ok(PAGE.includes("share_full_gmv"));
});

// ---------------------------------------------------------------------------
// Janela: 366 dias, D0 e futuro
// ---------------------------------------------------------------------------

test("MAX_RANGE_DAYS espelha o guardrail global da API", () => {
  assert.equal(MAX_RANGE_DAYS, 366);
});

test("rangeDays conta de forma inclusiva", () => {
  assert.equal(rangeDays("2026-08-01", "2026-08-31"), 31);
  assert.equal(rangeDays("2026-08-01", "2026-08-01"), 1);
});

test("janela de exatamente 366 dias passa", () => {
  assert.equal(validateRange("2025-09-15", "2026-09-15", "2026-09-16"), null);
  assert.equal(rangeDays("2025-09-15", "2026-09-15"), 366);
});

test("janela de 367 dias e' barrada ANTES da requisicao", () => {
  const erro = validateRange("2025-09-14", "2026-09-15", "2026-09-16");
  assert.ok(erro);
  assert.match(erro, /367 dias excede o maximo de 366/);
  assert.match(erro, /janelas/);   // explica que o historico e' maior
});

test("D0 e futuro sao bloqueados", () => {
  assert.match(validateRange("2026-09-01", "2026-09-16", "2026-09-16")!, /dia corrente/);
  assert.match(validateRange("2026-09-01", "2026-12-31", "2026-09-16")!, /dia corrente/);
});

test("periodo invertido e data invalida sao recusados", () => {
  assert.match(validateRange("2026-08-31", "2026-08-01", "2026-09-16")!, /antes da final/);
  assert.match(validateRange("nao-e-data", "2026-08-01", "2026-09-16")!, /invalida/);
  assert.match(validateRange("", "2026-08-01", "2026-09-16")!, /duas datas/);
});

test("presets terminam em D-1 e respeitam o teto", () => {
  for (const p of PERIOD_PRESETS) {
    const r = presetRange(p.days, "2026-09-16");
    assert.equal(r.to, "2026-09-15", `${p.days}d deveria terminar em D-1`);
    assert.equal(rangeDays(r.from, r.to), p.days);
    assert.equal(validateRange(r.from, r.to, "2026-09-16"), null);
  }
  assert.deepEqual(PERIOD_PRESETS.map((p) => p.days), [30, 90, 180, 366]);
});

// ---------------------------------------------------------------------------
// Query string
// ---------------------------------------------------------------------------

test("query usa os nomes que o filters_query da API le", () => {
  const q = buildQuery({ from: "2026-08-01", to: "2026-08-31" });
  assert.equal(q, "date_from=2026-08-01&date_to=2026-08-31");
});

test("marca vira brands separadas por virgula; ausente = todas", () => {
  assert.match(buildQuery({ from: "a", to: "b", brands: ["barbours"] }), /brands=barbours/);
  assert.match(buildQuery({ from: "a", to: "b", brands: ["a", "b"] }), /brands=a%2Cb/);
  assert.ok(!buildQuery({ from: "a", to: "b", brands: [] }).includes("brands"));
  assert.ok(!buildQuery({ from: "a", to: "b" }).includes("brands"));
});

// ---------------------------------------------------------------------------
// Formatacao — ausencia NUNCA vira zero
// ---------------------------------------------------------------------------

test("parseGmv converte string do backend e devolve null para ausencia", () => {
  assert.equal(parseGmv("4526767.38"), 4526767.38);
  assert.equal(parseGmv("0"), 0);            // zero e' medicao
  assert.equal(parseGmv(null), null);        // ausencia e' outra coisa
  assert.equal(parseGmv(""), null);
  assert.equal(parseGmv("abc"), null);
});

test("null exibe travessao, zero exibe zero", () => {
  assert.equal(fmtShare(null), SEM_DADO);
  assert.equal(fmtShare(0), "0,00%");
  assert.equal(fmtCount(null), SEM_DADO);
  assert.equal(fmtCount(0), "0");
  assert.equal(fmtGmv(null), SEM_DADO);
  assert.ok(fmtGmv("0").includes("0,00"));
  assert.notEqual(fmtShare(null), fmtShare(0));
});

test("moeda e' cheia, com centavo: esta tela e' de conferencia", () => {
  const s = fmtGmv("3649208.78");
  assert.ok(s.includes("3.649.208,78"), s);
  assert.ok(!s.includes("M") && !s.includes("K"), "nao abreviar");
});

test("percentual usa virgula e duas casas", () => {
  assert.equal(fmtShare(0.8061401158192494), "80,61%");
  assert.equal(fmtShare(1), "100,00%");
});

test("tempo mostra travessao quando a amostra e' zero", () => {
  assert.equal(fmtTempo({ seconds_avg: null, hours_avg: null, days_avg: null, sample_count: 0 }), SEM_DADO);
  assert.equal(fmtTempo(null), SEM_DADO);
  assert.equal(fmtTempo(AGOSTO.by_class[0].handling), "28,7 h");
  assert.equal(fmtTempo(AGOSTO.by_class[1].delivery), "4,89 d");   // >= 48h vira dias
});

// ---------------------------------------------------------------------------
// Classes
// ---------------------------------------------------------------------------

test("as tres classes tem rotulo proprio", () => {
  assert.deepEqual(Object.keys(CLASS_LABEL).sort(), ["full", "non_full", "unknown"]);
  assert.equal(CLASS_LABEL.full, "Full");
  assert.equal(CLASS_LABEL.non_full, "Não-Full");
  assert.equal(CLASS_LABEL.unknown, "Desconhecido");
});

test("unknown NUNCA e' chamado de cross-docking", () => {
  assert.ok(!/cross.?docking/i.test(CLASS_LABEL.unknown));
  assert.ok(!/cross.?docking/i.test(CLASS_DESCRICAO.unknown));
});

test("non_full nao e' apresentado como modalidade unica", () => {
  assert.match(CLASS_DESCRICAO.non_full, /Nao e' uma modalidade unica/);
  assert.match(CLASS_DESCRICAO.non_full, /extintos/);
});

test("a descricao de unknown explica a exclusao do denominador", () => {
  assert.match(CLASS_DESCRICAO.unknown, /denominador/);
  assert.match(CLASS_DESCRICAO.unknown, /sem envio/);
});

test("classe ausente nao vira linha de zeros", () => {
  const semUnknown: MLFulfillmentResponse = {
    ...AGOSTO, by_class: AGOSTO.by_class.filter((c) => c.fulfillment_class !== "unknown"),
  };
  assert.equal(byClass(semUnknown, "unknown"), null);
  assert.deepEqual(classesPresentes(semUnknown), ["full", "non_full"]);
});

// ---------------------------------------------------------------------------
// Serie diaria
// ---------------------------------------------------------------------------

test("serie pivota por data e exclui unknown do denominador", () => {
  const s = serieDiaria(AGOSTO, "orders");
  assert.equal(s.length, 2);
  assert.equal(s[0].ref_date, "2026-08-01");
  assert.equal(s[0].full, 1899);
  assert.equal(s[0].non_full, 400);
  assert.ok(Math.abs(s[0].share_full! - 1899 / 2299) < 1e-9);
});

test("dia sem venda classificada tem share null, nao zero", () => {
  const vazio: MLFulfillmentResponse = {
    ...AGOSTO,
    daily: [{ ref_date: "2026-08-05", fulfillment_class: "unknown", paid_gmv: "0", paid_orders: 0, paid_units: 0, eligible_orders: 3, cancelled_orders: 3 }],
  };
  const s = serieDiaria(vazio, "gmv");
  assert.equal(s[0].share_full, null);
  assert.notEqual(s[0].share_full, 0);
});

test("a serie muda com a metrica escolhida", () => {
  const gmv = serieDiaria(AGOSTO, "gmv");
  const un = serieDiaria(AGOSTO, "units");
  assert.notEqual(gmv[0].full, un[0].full);
});

// ---------------------------------------------------------------------------
// Frescor, manual_snapshot e alertas
// ---------------------------------------------------------------------------

test("os quatro estados de frescor tem rotulo", () => {
  assert.deepEqual(Object.keys(FRESHNESS_LABEL).sort(),
    ["fresh", "never_loaded", "stale", "unknown"]);
});

test("manual_snapshot tem rotulo e aviso permanente", () => {
  assert.equal(LOAD_MODE_LABEL.manual_snapshot, "Carga manual");
  assert.match(AVISO_MANUAL_SNAPSHOT, /manual_snapshot/);
  assert.match(AVISO_MANUAL_SNAPSHOT, /DAG|automacao/i);
  assert.ok(PAGE.includes("AVISO_MANUAL_SNAPSHOT"));
});

test("stale e never_loaded viram alerta", () => {
  const stale = { ...AGOSTO, freshness: { ...AGOSTO.freshness, freshness_status: "stale" as const } };
  assert.ok(alertas(stale).some((a) => /frescor/.test(a)));
  const nunca = { ...AGOSTO, freshness: { ...AGOSTO.freshness, freshness_status: "never_loaded" as const } };
  assert.ok(alertas(nunca).some((a) => /Nenhuma carga/.test(a)));
});

test("is_complete false vira alerta", () => {
  const inc = { ...AGOSTO, quality: { ...AGOSTO.quality, is_complete: false } };
  assert.ok(alertas(inc).some((a) => /incompletos|sem linha/.test(a)));
});

test("pedidos unknown geram alerta especifico", () => {
  const a = alertas(AGOSTO);
  assert.ok(a.some((x) => /sem envio/.test(x) && /Desconhecido/.test(x)));
});

test("sem unknown, sem alerta de unknown", () => {
  const limpo = { ...AGOSTO, quality: { ...AGOSTO.quality, unknown_logistic_type_orders: 0, unmatched_orders: 0 } };
  assert.ok(!alertas(limpo).some((x) => /sem envio/.test(x)));
});

// ---------------------------------------------------------------------------
// Avisos de contrato
// ---------------------------------------------------------------------------

test("a tela declara que nao mede estoque", () => {
  assert.match(AVISO_SEM_ESTOQUE, /nao.*estoque|nao e.*estoque/i);
  assert.match(AVISO_SEM_ESTOQUE, /ruptura/);
  assert.ok(PAGE.includes("AVISO_SEM_ESTOQUE"));
});

test("a tela avisa que o historico excede a janela consultavel", () => {
  assert.match(AVISO_JANELA, /366/);
  assert.ok(PAGE.includes("AVISO_JANELA"));
});

test("a tela nao apresenta maio-julho/2025 como cobertos", () => {
  assert.match(AVISO_COBERTURA, /01\/08\/2025/);
  assert.match(AVISO_COBERTURA, /Maio a julho de 2025 nao sao publicados/);
  assert.ok(PAGE.includes("AVISO_COBERTURA"));
});

test("nenhum aviso promete estoque, ruptura ou expedicao", () => {
  for (const a of [AVISO_MANUAL_SNAPSHOT, AVISO_JANELA, AVISO_COBERTURA]) {
    assert.ok(!/cobertura em dias|ruptura ativa|expedicao/i.test(a), a);
  }
});

// ---------------------------------------------------------------------------
// Estados da interface
// ---------------------------------------------------------------------------

test("vazio real e' distinguido de erro e de carregando", () => {
  assert.equal(isVazioReal(null), false);              // null = ainda nao chegou
  assert.equal(isVazioReal(AGOSTO), false);
  const vazio: MLFulfillmentResponse = { ...AGOSTO, by_class: [], daily: [] };
  assert.equal(isVazioReal(vazio), true);
});

test("a pagina implementa loading, erro, vazio e janela invalida", () => {
  assert.ok(PAGE.includes("carregando"), "estado de carregamento");
  assert.ok(PAGE.includes("animate-pulse"), "skeleton");
  assert.ok(PAGE.includes('role="alert"'), "erro anunciado");
  assert.ok(PAGE.includes("isVazioReal"), "vazio real");
  assert.ok(PAGE.includes("erroJanela"), "janela invalida");
  assert.ok(PAGE.includes('role="status"'), "carregando anunciado");
});

test("a tela nao fabrica dado para esconder vazio", () => {
  assert.ok(!/mock|placeholder|fakeData|dadosFicticios/i.test(PAGE));
  assert.ok(PAGE.includes("ausência de venda, não falha de carga"));
});

test("o corpo tecnico do 422 nao chega a tela", () => {
  // O cliente troca o 422 por mensagem propria; a pagina so' exibe `e.message`.
  assert.ok(CLIENT.includes("Periodo ou filtro invalido para esta consulta."));
  assert.ok(!PAGE.includes("res.text()") && !PAGE.includes("detail"));
});

test("a validacao de janela roda ANTES da requisicao", () => {
  const i = PAGE.indexOf("const erroJanela");
  // `fetchMLFulfillment` aparece primeiro no IMPORT: comparar contra ele
  // daria falso negativo. O que importa e a ordem da CHAMADA.
  const j = PAGE.indexOf("fetchMLFulfillment(query");
  assert.ok(i > 0 && j > 0 && i < j, "erroJanela precisa ser avaliado antes da chamada");
  assert.ok(PAGE.includes("if (erroJanela) {"), "curto-circuito no efeito");
});

// ---------------------------------------------------------------------------
// Acessibilidade e responsividade — contrato de classe
// ---------------------------------------------------------------------------

test("todo controle interativo declara alvo minimo de 44px", () => {
  // `min-h-11` = 44px no Tailwind. Regra EXPLICITA: padding + line-height
  // chegaria a 44 por acidente, e qualquer troca de fonte derrubaria o alvo.
  assert.ok(PAGE.includes('const CONTROLE ='));
  assert.ok(PAGE.includes('"min-h-11 px-3'), "a constante precisa fixar o alvo");

  // Fatiar por `<button` e inspecionar ate o fechamento e' mais robusto que um
  // regex de atributos: arrow functions contem `>`, que truncaria `[^>]*` no
  // meio da tag e daria falso positivo.
  const blocos = PAGE.split("<button").slice(1);
  // 3 literais `<button`: os presets e o seletor de metrica saem de
  // `.map()`, entao um limiar maior reprovaria codigo correto.
  assert.equal(blocos.length, 3, "a tela tem 3 literais de botao");
  for (const b of blocos) {
    const tag = b.split(">")[0] + b.split(">").slice(1).join(">").split("</button>")[0];
    assert.ok(tag.includes("className="), "todo button precisa de className");
    assert.ok(tag.includes("CONTROLE"), "todo button usa a constante de alvo 44px");
  }

  // Inputs e selects tambem.
  for (const marcador of ["<input", "<select"]) {
    for (const b of PAGE.split(marcador).slice(1)) {
      const tag = b.split("/>")[0].split(">")[0] + b;
      assert.ok(tag.slice(0, 400).includes("CONTROLE"),
        `${marcador} precisa da constante de alvo`);
    }
  }
});
test("foco visivel em todo controle", () => {
  assert.match(PAGE, /focus-visible:outline-2/);
  assert.match(TREND, /focus-visible:outline-2/);
});

test("todo input e select tem label associado por id", () => {
  const ids = [...PAGE.matchAll(/<(?:input|select)[^>]*\bid="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(ids.length >= 3, "de, ate e marca");
  for (const id of ids) {
    assert.ok(PAGE.includes(`htmlFor="${id}"`), `falta <label htmlFor="${id}">`);
  }
});

test("grupos de botoes sem label individual tem aria-label", () => {
  assert.ok(PAGE.includes('role="group" aria-label="Períodos predefinidos"'));
  assert.ok(PAGE.includes('role="group" aria-label="Métrica da série"'));
});

test("o seletor de metrica expoe o estado por aria-pressed", () => {
  assert.ok(PAGE.includes("aria-pressed={metrica === m.key}"));
});

test("o grafico tem alternativa textual e tabela navegavel", () => {
  assert.ok(TREND.includes('role="img"'));
  assert.ok(TREND.includes("aria-label="));
  assert.ok(TREND.includes("<details"), "tabela alternativa");
  assert.ok(TREND.includes("<caption"), "tabela descrita");
  assert.ok(TREND.includes("connectNulls={false}"),
    "ligar os pontos inventaria uma queda que nao houve");
});

test("toda tabela tem caption e cabecalho com scope", () => {
  const tabelas = (PAGE.match(/<table\b/g) ?? []).length;
  const captions = (PAGE.match(/<caption\b/g) ?? []).length;
  assert.ok(tabelas >= 3);
  assert.equal(captions, tabelas, "cada tabela precisa de caption");
  assert.ok((PAGE.match(/scope="col"/g) ?? []).length >= 10);
  assert.ok(PAGE.includes('scope="row"'), "primeira coluna como cabecalho de linha");
});

test("tabela larga rola dentro do proprio container, nao na pagina", () => {
  // Cada `min-w-[NNNpx]` (que e' o que pode estourar) precisa de um
  // `overflow-x-auto` ancestral no mesmo bloco.
  const larguras = (PAGE.match(/min-w-\[\d+px\]/g) ?? []).length;
  const scrolls = (PAGE.match(/overflow-x-auto/g) ?? []).length;
  assert.ok(larguras >= 3, "ha tabelas largas");
  assert.ok(scrolls >= larguras, "toda tabela larga precisa de scroll interno");
});

test("nenhum overflow horizontal de pagina", () => {
  // `min-w` so' aparece dentro de tabela; nada no layout externo trava largura.
  assert.ok(!/className="[^"]*\bw-\[\d{4,}px\]/.test(PAGE));
  assert.ok(!PAGE.includes("overflow-x-scroll"), "scroll da pagina, nao do bloco");
});

test("grade responde em mobile, tablet e desktop", () => {
  assert.match(PAGE, /grid-cols-1 sm:grid-cols-2 xl:grid-cols-4/);
  assert.match(PAGE, /grid-cols-1 sm:grid-cols-3/);
  assert.match(PAGE, /flex-wrap/, "filtros quebram linha em telas estreitas");
});

test("nao ha texto abaixo de 12px", () => {
  // text-xs = 12px e' o piso. text-[10px] e text-[11px] sao proibidos.
  assert.ok(!/text-\[\s*(?:9|10|11)px\s*\]/.test(PAGE));
  assert.ok(!/text-\[\s*(?:9|10|11)px\s*\]/.test(TREND));
});

// ---------------------------------------------------------------------------
// Navegacao
// ---------------------------------------------------------------------------

test("a rota entra na navegacao sem remover nem renomear nada", () => {
  assert.ok(NAV.includes('{ href: "/full-ml", label: "Full Mercado Livre" }'));
  for (const existente of [
    '"/", label: "Gerencial"', '"/canais"', '"/produtos"', '"/qualidade"',
    '"/financeiro"', '"/regioes"', '"/tempo-real"', '"/pedidos"',
    '"/inteligencia"', '"/monitoramento-preco"', '"/operacoes"',
    '"/referencias-externas/avoe"',
  ]) {
    assert.ok(NAV.includes(existente), `rota existente sumiu: ${existente}`);
  }
});

test("a rota e' de topo, para nao colidir com /operacoes por prefixo", () => {
  // `isNavItemActive` casa por `startsWith`: /operacoes/full-ml deixaria os
  // dois itens ativos ao mesmo tempo.
  assert.ok(!NAV.includes('"/operacoes/full-ml"'));
  assert.ok(NAV.includes('"/full-ml"'));
});

test("a tela nao e' apresentada como estoque nem expedicao", () => {
  assert.ok(!/Estoque|Expedi[çc][ãa]o/.test(
    NAV.split('"/full-ml"')[1].split("}")[0] ?? ""));
  assert.ok(PAGE.includes("modalidade logística"));
});
