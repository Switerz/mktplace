// Testes da fundacao decisoria da Inteligencia (Gate V3-1A).
//
// Cobrem as regras que o desenho V3-0 fixou e que a pagina anterior violava:
// marcas derivadas do payload (incluindo Rituaria), contagem sempre rotulada
// como amostra, lente com allowlist, `null` distinto de zero, e o dialogo do
// Pareto restrito aos agregados que o payload realmente entrega.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  BRAND_ALL, brandLabel, brandScopeLabel, filterByBrand, mlBrandsFromPayload, parseBrandSelection,
} from "../src/lib/inteligencia/brands.ts";
import {
  buildLensHref, DEFAULT_LENS, isLens, LENSES, LENS_QUERY_KEY, parseLens, parseLensValue,
} from "../src/lib/inteligencia/lens.ts";
import {
  buildQueue, KIND_ORDER, lensSampleNote, LENS_COLUMNS, LIST_LIMITS, listSampleNote,
  queueForLens, sampleNote, sortForLens, TK_PRODUCTS_LIMIT, tkSampleNote,
} from "../src/lib/inteligencia/queue.ts";
import {
  buildPriorities, MAX_CONTRIBUTORS, receivedByKind,
} from "../src/lib/inteligencia/priorities.ts";
import {
  buildParetoProdutosHref, concentrationByBrand, isParetoBucket, PARETO_BUCKETS,
} from "../src/lib/inteligencia/pareto.ts";
import { decBr, fractionAsPctBr, pctBr, roasBr } from "../src/lib/inteligencia/format.ts";
import { FILTER_QUERY_KEYS } from "../src/lib/filters/nav-links.ts";
import type { InteligenciaData, ProductSignalRow } from "../src/lib/api-client.ts";

// ---------------------------------------------------------------------------
// Fixtures — espelham o contrato real de `get_inteligencia`
// ---------------------------------------------------------------------------

function sig(brand: string, title: string, over: Partial<ProductSignalRow> = {}): ProductSignalRow {
  return {
    brand, title,
    pareto_bucket: "A_top50", revenue_velocity: "high",
    gmv: 100, ad_spend: 10, ad_roas: 10, ad_acos_pct: 10,
    cancel_rate_pct: 1, revenue_share_pct: 0.1, units_sold: 5,
    days_advertised: 30, ad_efficiency: null,
    ...over,
  };
}

/** Payload com as QUATRO marcas ML, incluindo rituaria (o defeito B4). */
const DATA: InteligenciaData = {
  signals: [
    { product_status: "ad_spend_no_sales", n_products: 999, gmv: 0, ad_spend: 500, avg_roas: null },
    { product_status: "sells+advertised", n_products: 999, gmv: 9000, ad_spend: 900, avg_roas: 11 },
  ],
  urgent: [
    sig("barbours", "P1", { ad_spend: 300, gmv: 0, ad_roas: null }),
    sig("rituaria", "P2", { ad_spend: 100, gmv: 0, ad_roas: null }),
  ],
  scale: [
    sig("kokeshi", "P3", { gmv: 5000, ad_roas: 14 }),
    sig("lescent", "P4", { gmv: 3000, ad_roas: 9 }),
  ],
  organic: [sig("rituaria", "P5", { gmv: 800, ad_spend: 0, ad_roas: null, units_sold: 40 })],
  pareto: [
    { brand: "barbours", pareto_bucket: "A_top50", n_products: 10, gmv: 700, ad_spend: 70 },
    { brand: "barbours", pareto_bucket: "D_tail", n_products: 40, gmv: 300, ad_spend: 5 },
    { brand: "rituaria", pareto_bucket: "A_top50", n_products: 4, gmv: 200, ad_spend: 20 },
  ],
  ltv: [
    { brand: "barbours", total_buyers: 100, repeat_buyers: 20, repeat_rate_pct: 20, avg_customer_ltv: 300, vip_buyers: 5, one_and_done_buyers: 80, at_risk_or_churned: 10, overall_roas: 9 },
    { brand: "kokeshi", total_buyers: 50, repeat_buyers: 0, repeat_rate_pct: 0, avg_customer_ltv: null, vip_buyers: 0, one_and_done_buyers: 50, at_risk_or_churned: null, overall_roas: null },
    { brand: "lescent", total_buyers: 30, repeat_buyers: 3, repeat_rate_pct: 10, avg_customer_ltv: 100, vip_buyers: 1, one_and_done_buyers: 27, at_risk_or_churned: 2, overall_roas: 4 },
    { brand: "rituaria", total_buyers: 10, repeat_buyers: 1, repeat_rate_pct: 10, avg_customer_ltv: 50, vip_buyers: 0, one_and_done_buyers: 9, at_risk_or_churned: 1, overall_roas: 2 },
  ],
  tk_products: [
    { brand: "apice", product_name: "TK1", gmv: 400, orders: 10, avg_pct_video: 50, avg_pct_live: 30, avg_pct_card: 20, avg_rating: 4.5 },
  ],
};

function reader(pairs: [string, string][]) {
  const p = new URLSearchParams();
  for (const [k, v] of pairs) p.append(k, v);
  return p;
}

// ---------------------------------------------------------------------------
// 1-3. marcas derivadas do payload
// ---------------------------------------------------------------------------

test("1. marcas ML vem do payload e incluem rituaria", () => {
  const brands = mlBrandsFromPayload(DATA);
  assert.deepEqual(brands, ["barbours", "kokeshi", "lescent", "rituaria"]);
  assert.ok(brands.includes("rituaria"), "rituaria nao pode ficar invisivel (defeito B4)");
});

test("1b. tk_products NAO contribui para as marcas ML (apice fora)", () => {
  assert.ok(!mlBrandsFromPayload(DATA).includes("apice"));
});

test("1c. payload nulo/vazio devolve lista vazia, sem lancar", () => {
  assert.deepEqual(mlBrandsFromPayload(null), []);
  assert.deepEqual(mlBrandsFromPayload(undefined), []);
});

test("2. selecao de marca: all, valida, invalida, vazia, ausente", () => {
  const avail = mlBrandsFromPayload(DATA);
  assert.equal(parseBrandSelection(null, avail), BRAND_ALL);
  assert.equal(parseBrandSelection(undefined, avail), BRAND_ALL);
  assert.equal(parseBrandSelection("", avail), BRAND_ALL);
  assert.equal(parseBrandSelection("all", avail), BRAND_ALL);
  assert.equal(parseBrandSelection("rituaria", avail), "rituaria");
  assert.equal(parseBrandSelection("nao-existe", avail), BRAND_ALL, "invalida cai em all sem erro");
  assert.equal(parseBrandSelection("apice", avail), BRAND_ALL, "marca fora do ML cai em all");
});

test("2b. filtro por marca aplica-se a qualquer bloco com coluna brand", () => {
  assert.equal(filterByBrand(DATA.urgent, "rituaria").length, 1);
  assert.equal(filterByBrand(DATA.urgent, BRAND_ALL).length, 2);
  assert.equal(filterByBrand(DATA.pareto, "barbours").length, 2);
});

test("3. LTV respeita a marca selecionada (defeito B9)", () => {
  assert.equal(filterByBrand(DATA.ltv, BRAND_ALL).length, 4);
  const so = filterByBrand(DATA.ltv, "kokeshi");
  assert.equal(so.length, 1);
  assert.equal(so[0].brand, "kokeshi");
});

test("3b. rotulos: marca conhecida, desconhecida e escopo", () => {
  assert.equal(brandLabel("rituaria"), "RITUÁRIA");
  assert.equal(brandLabel("marca-nova"), "MARCA-NOVA", "marca sem rotulo nao desaparece");
  assert.equal(brandScopeLabel(BRAND_ALL), "Todas as marcas ML");
  assert.equal(brandScopeLabel("lescent"), "LESCENT");
});

// ---------------------------------------------------------------------------
// 4-7. contrato da lente
// ---------------------------------------------------------------------------

test("4. parsing de lens valido", () => {
  for (const l of LENSES) assert.equal(parseLensValue(l), l);
  assert.equal(parseLens(reader([["lens", "escalar"]])), "escalar");
});

test("5. lens ausente, vazio, invalido ou REPETIDO cai em todos", () => {
  assert.equal(parseLensValue(null), DEFAULT_LENS);
  assert.equal(parseLensValue(""), DEFAULT_LENS);
  assert.equal(parseLensValue("PARAR"), DEFAULT_LENS, "case-sensitive por contrato");
  assert.equal(parseLensValue("matriz"), DEFAULT_LENS);
  assert.equal(parseLens(reader([])), DEFAULT_LENS);
  assert.equal(parseLens(reader([["lens", "parar"], ["lens", "escalar"]])), DEFAULT_LENS,
    "repetido e ambiguidade, e ambiguidade nunca vira escolha");
  assert.equal(DEFAULT_LENS, "todos");
});

test("6. construcao da URL da lente preserva marca e omite o padrao", () => {
  const p = reader([["brands", "rituaria"]]);
  assert.equal(buildLensHref("/inteligencia", "parar", p), "/inteligencia?brands=rituaria&lens=parar");
  assert.equal(buildLensHref("/inteligencia", "todos", p), "/inteligencia?brands=rituaria",
    "lente padrao nao suja a URL");
  assert.equal(buildLensHref("/inteligencia", "escalar", reader([])), "/inteligencia?lens=escalar");
});

test("6b. construcao da URL descarta ctx_* (retorno nunca chega quente)", () => {
  const p = reader([["brands", "kokeshi"], ["ctx_from", "canais"], ["ctx_signal", "custo_alto"]]);
  const href = buildLensHref("/inteligencia", "parar", p);
  assert.ok(!href.includes("ctx_"), href);
  assert.ok(href.includes("brands=kokeshi"));
});

test("7. lens NAO entra em FILTER_QUERY_KEYS (sidebar nao propaga)", () => {
  assert.ok(!FILTER_QUERY_KEYS.includes(LENS_QUERY_KEY));
  assert.ok(!FILTER_QUERY_KEYS.includes("pareto_bucket"));
  assert.ok(isLens("parar") && !isLens("quadrante"));
});

// ---------------------------------------------------------------------------
// 8-10. prioridades
// ---------------------------------------------------------------------------

test("8. prioridade sem registro DESAPARECE (nunca prioridade zero)", () => {
  const semOrganic: InteligenciaData = { ...DATA, organic: [] };
  const kinds = buildPriorities(semOrganic, BRAND_ALL).map((p) => p.kind);
  assert.deepEqual(kinds, ["parar", "escalar"]);
  assert.ok(!kinds.includes("testar"));
  assert.deepEqual(buildPriorities({ ...DATA, urgent: [], scale: [], organic: [] }, BRAND_ALL), []);
});

test("9. contagem e' de registros recebidos, rotulada como amostra", () => {
  const [parar] = buildPriorities(DATA, BRAND_ALL);
  assert.equal(parar.received, 2);
  assert.match(parar.limitation, /registros exibidos/);
  assert.ok(!/no total|todo o portf|de 30/i.test(parar.limitation), parar.limitation);
  // amostra cheia declara o teto conhecido, sem afirmar cobertura
  const cheia = sampleNote("parar", LIST_LIMITS.parar);
  assert.match(cheia, /amostra limitada a até 30/);
  assert.ok(!/30 de 30/.test(cheia));
});

test("9b. soma da prioridade ignora null sem convertê-lo em zero de exibicao", () => {
  const comNull: InteligenciaData = {
    ...DATA,
    urgent: [sig("barbours", "A", { ad_spend: 100 }), sig("barbours", "B", { ad_spend: null as unknown as number })],
  };
  const [parar] = buildPriorities(comNull, BRAND_ALL);
  assert.equal(parar.amount, 100);
  assert.equal(parar.received, 2, "a linha com null continua contando como registro");
});

test("10. no maximo cinco contribuintes, ordenados pela metrica da lente", () => {
  const muitos: InteligenciaData = {
    ...DATA,
    urgent: Array.from({ length: 9 }, (_, i) => sig("barbours", `X${i}`, { ad_spend: i * 10 })),
  };
  const [parar] = buildPriorities(muitos, BRAND_ALL);
  assert.equal(parar.contributors.length, MAX_CONTRIBUTORS);
  assert.equal(parar.contributors[0].ad_spend, 80, "maior gasto primeiro");
  assert.ok(parar.contributors[0].ad_spend! >= parar.contributors[4].ad_spend!);
});

test("10b. prioridades respeitam a marca selecionada", () => {
  const so = buildPriorities(DATA, "rituaria");
  assert.deepEqual(so.map((p) => p.kind), ["parar", "testar"]);
  assert.equal(so[0].received, 1);
});

// ---------------------------------------------------------------------------
// 11-14. fila de evidencias
// ---------------------------------------------------------------------------

test("11. fila deriva das tres listas com origem discriminada", () => {
  const q = buildQueue(DATA, BRAND_ALL);
  assert.equal(q.length, 5);
  assert.deepEqual(queueForLens(q, "parar").map((r) => r.title), ["P1", "P2"]);
  assert.deepEqual(queueForLens(q, "escalar").map((r) => r.title), ["P3", "P4"]);
  assert.deepEqual(queueForLens(q, "testar").map((r) => r.title), ["P5"]);
  for (const r of q) assert.ok(KIND_ORDER.includes(r.kind));
});

test("11b. grao preservado: nenhum identificador inventado", () => {
  const q = buildQueue(DATA, BRAND_ALL);
  for (const r of q) {
    assert.ok(!("item_id" in r), "item_id nao pode ser inventado no frontend");
    assert.ok(!("product_id" in r));
    assert.ok(typeof r.brand === "string" && typeof r.title === "string");
  }
});

// A versao anterior deste teste exigia deduplicacao por `(brand, title)` e,
// com ela, o modulo APAGAVA registros legitimos. O payload nao entrega
// `item_id`, e o grao da tabela-fonte e' `(brand, item_id)`: duas linhas com o
// mesmo titulo podem ser dois produtos. O contrato correto e' preservar tudo.
test("12. Todos devolve a uniao COMPLETA, sem perder nenhuma linha", () => {
  const q = buildQueue(DATA, BRAND_ALL);
  const todos = queueForLens(q, "todos");
  assert.equal(todos.length, 5);
  const recebidas = DATA.urgent.length + DATA.scale.length + DATA.organic.length;
  assert.equal(todos.length, recebidas, "a fila tem exatamente as linhas recebidas");
});

test("12a. BLOQUEADOR: duas linhas da MESMA lista com mesmo brand/title permanecem", () => {
  const d: InteligenciaData = {
    ...DATA,
    urgent: [
      sig("barbours", "MESMO TITULO", { gmv: 100, ad_spend: 100 }),
      sig("barbours", "MESMO TITULO", { gmv: 200, ad_spend: 200 }),
    ],
    scale: [], organic: [],
  };
  const q = buildQueue(d, BRAND_ALL);
  assert.equal(q.length, 2, "as duas linhas recebidas tem de permanecer");
  assert.deepEqual(q.map((r) => r.ad_spend), [100, 200]);
  // e as derivacoes precisam refletir isso
  assert.equal(receivedByKind(d, BRAND_ALL).parar, 2);
  const [parar] = buildPriorities(d, BRAND_ALL);
  assert.equal(parar.received, 2);
  assert.equal(parar.amount, 300, "a soma nao pode perder R$ 200");
});

test("12b. linhas de origens DIFERENTES com mesmo brand/title tambem permanecem", () => {
  const d: InteligenciaData = {
    ...DATA,
    urgent: [sig("barbours", "COLIDE", { ad_spend: 50 })],
    scale: [sig("barbours", "COLIDE", { gmv: 900, ad_roas: 12 })],
    organic: [],
  };
  const q = buildQueue(d, BRAND_ALL);
  assert.equal(q.length, 2, "sem item_id o frontend nao pode provar identidade");
  assert.deepEqual(q.map((r) => r.kind), ["parar", "escalar"]);
  assert.equal(queueForLens(q, "todos").length, 2);
  assert.deepEqual(receivedByKind(d, BRAND_ALL), { parar: 1, escalar: 1, testar: 0 });
});

test("12c. nenhuma contagem ou soma perde registro, em qualquer lente", () => {
  const rep = (n: number, kind: "urgent" | "scale" | "organic") =>
    Array.from({ length: n }, () => sig("kokeshi", "IGUAL", { gmv: 10, ad_spend: 7 }));
  const d: InteligenciaData = {
    ...DATA, urgent: rep(4, "urgent"), scale: rep(3, "scale"), organic: rep(2, "organic"),
  };
  const q = buildQueue(d, BRAND_ALL);
  assert.equal(q.length, 9);
  assert.deepEqual(receivedByKind(d, BRAND_ALL), { parar: 4, escalar: 3, testar: 2 });
  assert.equal(queueForLens(q, "parar").length, 4);
  assert.equal(queueForLens(q, "escalar").length, 3);
  assert.equal(queueForLens(q, "testar").length, 2);
  assert.equal(queueForLens(q, "todos").length, 9);
  const ps = buildPriorities(d, BRAND_ALL);
  assert.equal(ps.find((p) => p.kind === "parar")!.amount, 28, "4 x 7");
  assert.equal(ps.find((p) => p.kind === "escalar")!.amount, 30, "3 x 10");
  assert.equal(ps.find((p) => p.kind === "testar")!.amount, 20, "2 x 10");
  // a ordenacao tambem nao pode encolher a lista
  assert.equal(sortForLens(queueForLens(q, "todos"), "todos").length, 9);
});

test("13. ordenacao por lente usa a metrica que define a lente", () => {
  const q = buildQueue(DATA, BRAND_ALL);
  assert.deepEqual(sortForLens(queueForLens(q, "parar"), "parar").map((r) => r.title), ["P1", "P2"]);
  assert.deepEqual(sortForLens(queueForLens(q, "escalar"), "escalar").map((r) => r.title), ["P3", "P4"]);
  const kinds = sortForLens(queueForLens(q, "todos"), "todos").map((r) => r.kind);
  assert.deepEqual(kinds, ["parar", "parar", "escalar", "escalar", "testar"], "todos agrupa por origem");
});

test("13b. null vai para o fim da ordenacao, sem virar zero", () => {
  const rows = [
    sig("a", "semRoas", { ad_roas: null }),
    sig("a", "comRoas", { ad_roas: 3 }),
  ].map((r) => ({ ...r, kind: "escalar" as const }));
  assert.deepEqual(sortForLens(rows, "escalar").map((r) => r.title), ["comRoas", "semRoas"]);
});

test("14. zero e' zero e null e' null — nunca colapsados", () => {
  const rows = [sig("a", "zero", { ad_spend: 0 }), sig("a", "nulo", { ad_spend: null as unknown as number })];
  assert.equal(rows[0].ad_spend, 0);
  assert.equal(rows[1].ad_spend, null);
  assert.notEqual(rows[0].ad_spend, rows[1].ad_spend);
  // a soma trata null como ausente, e zero como valor
  const d: InteligenciaData = { ...DATA, urgent: rows };
  assert.equal(buildPriorities(d, BRAND_ALL)[0].amount, 0);
  assert.equal(buildPriorities(d, BRAND_ALL)[0].received, 2);
});

test("14b. colunas por lente nao repetem as nove em todas", () => {
  assert.ok(LENS_COLUMNS.parar.includes("ad_spend") && !LENS_COLUMNS.parar.includes("ad_roas"));
  assert.ok(LENS_COLUMNS.escalar.includes("ad_roas") && !LENS_COLUMNS.escalar.includes("days_advertised"));
  assert.ok(LENS_COLUMNS.todos.includes("kind"));
  for (const l of LENSES) assert.ok(LENS_COLUMNS[l].length <= 7, `${l} tem ${LENS_COLUMNS[l].length} colunas`);
});

test("14c. nota de amostra de `todos` soma os tetos das tres listas", () => {
  const nota = lensSampleNote("todos", { parar: 30, escalar: 20, testar: 20 });
  assert.match(nota, /70 registros exibidos/);
  assert.match(nota, /até 70/);
  assert.ok(!/total|portf/i.test(nota));
  assert.deepEqual(receivedByKind(DATA, BRAND_ALL), { parar: 2, escalar: 2, testar: 1 });
});

// ---------------------------------------------------------------------------
// 15-18. Pareto
// ---------------------------------------------------------------------------

test("15. Pareto itera as marcas do payload, incluindo rituaria", () => {
  const c = concentrationByBrand(DATA, BRAND_ALL);
  assert.deepEqual(c.map((x) => x.brand), ["barbours", "rituaria"], "ordenado por GMV total desc");
  assert.equal(c[0].totalGmv, 1000);
  assert.equal(c[0].totalProducts, 50);
  assert.ok(c.some((x) => x.brand === "rituaria"));
});

test("15b. Pareto respeita o filtro local de marca", () => {
  const c = concentrationByBrand(DATA, "rituaria");
  assert.equal(c.length, 1);
  assert.equal(c[0].brand, "rituaria");
});

test("16. share do bucket vem dos agregados; total zero devolve null, nao 0%", () => {
  const c = concentrationByBrand(DATA, "barbours")[0];
  const a = c.buckets.find((b) => b.bucket === "A_top50")!;
  const d = c.buckets.find((b) => b.bucket === "D_tail")!;
  assert.equal(a.sharePct, 70);
  assert.equal(d.sharePct, 30);
  const zerado: InteligenciaData = {
    ...DATA,
    pareto: [{ brand: "kokeshi", pareto_bucket: "A_top50", n_products: 3, gmv: 0, ad_spend: 0 }],
  };
  assert.equal(concentrationByBrand(zerado, BRAND_ALL)[0].buckets[0].sharePct, null,
    "divisao indefinida nao vira 0%");
});

test("17. o bucket carrega SOMENTE agregados — nenhuma lista de produtos", () => {
  const c = concentrationByBrand(DATA, "barbours")[0];
  for (const b of c.buckets) {
    assert.deepEqual(Object.keys(b).sort(), ["ad_spend", "bucket", "gmv", "n_products", "sharePct"]);
    assert.ok(!("products" in b) && !("items" in b) && !("titles" in b),
      "o payload pareto nao contem os produtos do bucket");
  }
});

test("18. URL canonica do CTA Pareto -> Produtos", () => {
  assert.equal(
    buildParetoProdutosHref("rituaria", "A_top50"),
    "/produtos?channels=ml&brands=rituaria&pareto_bucket=A_top50",
  );
  const href = buildParetoProdutosHref("barbours", "D_tail");
  assert.ok(href.startsWith("/produtos?channels=ml&"), href);
  assert.ok(!/gmv|R\$|%|\{|n_products/.test(href), "nenhuma metrica viaja na URL");
  for (const b of PARETO_BUCKETS) assert.ok(isParetoBucket(b));
  assert.ok(!isParetoBucket("E_outro") && !isParetoBucket("") && !isParetoBucket(null));
});

// ---------------------------------------------------------------------------
// Truncamento honesto das listas compactas (bloco 5) e do card TikTok
// ---------------------------------------------------------------------------

test("19. na borda do LIMIT o texto diz 'de ao menos', nunca 'de N' como total", () => {
  // lista cheia: 30 recebidos com LIMIT 30 -> nao sabemos quantos existem
  const cheia = listSampleNote("parar", 5, LIST_LIMITS.parar);
  assert.equal(cheia, "5 de ao menos 30 registros nesta lista");
  assert.ok(!/de 30 registros nesta lista$/.test(cheia), "nao pode soar como total");
  // abaixo do LIMIT: o numero recebido E' tudo o que existe naquela lista
  assert.equal(listSampleNote("escalar", 5, 7), "5 de 7 registros recebidos nesta lista");
  assert.equal(listSampleNote("testar", 1, 1), "1 de 1 registro recebido nesta lista");
  // e nunca a palavra portfolio
  for (const n of [cheia, listSampleNote("escalar", 5, 7)]) {
    assert.ok(!/portf[oó]lio|no total/i.test(n), n);
  }
});

test("19b. o card TikTok declara o proprio teto (25), sem alegar total", () => {
  assert.equal(TK_PRODUCTS_LIMIT, 25);
  const cheio = tkSampleNote(5, TK_PRODUCTS_LIMIT);
  assert.equal(cheio, "5 de ao menos 25 produtos nesta janela");
  assert.equal(tkSampleNote(5, 9), "5 de 9 produtos recebidos nesta janela");
  assert.equal(tkSampleNote(1, 1), "1 de 1 produto recebido nesta janela");
  for (const n of [cheio, tkSampleNote(5, 9)]) {
    assert.ok(!/portf[oó]lio|no total|ROAS|ads/i.test(n), n);
  }
});

test("20. href da CTA para a fila: lente correta, marca preservada, ancora, sem ctx_*", () => {
  const p = reader([["brands", "rituaria"], ["ctx_from", "canais"]]);
  const parar = buildLensHref("/inteligencia", "parar", p, { anchor: "fila-evidencias" });
  assert.equal(parar, "/inteligencia?brands=rituaria&lens=parar#fila-evidencias");
  const escalar = buildLensHref("/inteligencia", "escalar", p, { anchor: "fila-evidencias" });
  assert.equal(escalar, "/inteligencia?brands=rituaria&lens=escalar#fila-evidencias");
  for (const h of [parar, escalar]) {
    assert.ok(!h.includes("ctx_"), h);
    assert.ok(!/gmv|R\$|%|\d+,\d/.test(h), "nenhuma metrica na URL");
  }
  // sem marca selecionada a URL fica limpa, e a ancora continua
  assert.equal(
    buildLensHref("/inteligencia", "parar", reader([]), { anchor: "fila-evidencias" }),
    "/inteligencia?lens=parar#fila-evidencias",
  );
});

// ---------------------------------------------------------------------------
// Regressao pt-BR (achado do QA visual do Gate V3-1A)
//
// A tela renderizava `12.0x`, `0.0%`, `8.0%` com PONTO decimal porque os
// valores vinham de `Number.toFixed()`, que e' insensivel a locale, enquanto
// `fmtBrl`/`fmtNumber` na mesma pagina usam pt-BR. Os testes abaixo travam a
// convencao e garantem que a correcao mexe SOMENTE na apresentacao.
// ---------------------------------------------------------------------------

test("V3-1A pt-BR: decBr/pctBr/roasBr usam virgula como separador decimal", () => {
  assert.equal(decBr(12), "12,0");
  assert.equal(decBr(0), "0,0");
  assert.equal(pctBr(8), "8,0%");
  assert.equal(pctBr(0), "0,0%", "zero real segue visivel, e nao vira vazio");
  assert.equal(roasBr(12), "12,0x");
  assert.equal(roasBr(2), "2,0x");
  // nenhuma saida pode conter ponto decimal
  for (const s of [decBr(1.5), pctBr(30.25), roasBr(9.04), decBr(4.5, 1)]) {
    assert.ok(!/\d\.\d/.test(s), `saida com ponto decimal: ${s}`);
  }
});

test("V3-1A pt-BR: casas decimais identicas ao toFixed anterior, sem mudar arredondamento", () => {
  // mesma quantidade de casas que `toFixed(1)` / `toFixed(0)` produziam
  assert.equal(decBr(4.5, 1), "4,5");
  assert.equal(decBr(37, 0), "37");
  assert.equal(decBr(2.449, 1), "2,4");
  assert.equal(pctBr(99.95, 1), "100,0%");
  // valor de negocio preservado: apenas o separador muda
  for (const v of [0, 0.1, 1.05, 12, 37.4, 1234.56]) {
    assert.equal(
      decBr(v, 1).replace(/\./g, "").replace(",", "."),
      v.toFixed(1),
      `decBr(${v}) deve representar o mesmo numero que toFixed(1)`,
    );
  }
});

test("V3-1A pt-BR: fractionAsPctBr converte a fracao de revenue_share_pct", () => {
  // o contrato entrega revenue_share_pct como FRACAO (0,1 = 10%)
  assert.equal(fractionAsPctBr(0.1), "10,0%");
  assert.equal(fractionAsPctBr(0.3), "30,0%");
  assert.equal(fractionAsPctBr(0), "0,0%");
  assert.ok(!/\d\.\d/.test(fractionAsPctBr(0.1234)));
});

test("V3-1A pt-BR: nenhum arquivo visual do V3-1A voltou a usar toFixed", () => {
  const arquivos = [
    "app/inteligencia/page.tsx",
    "src/components/inteligencia/EvidenceQueue.tsx",
    "src/components/inteligencia/ConcentrationBars.tsx",
    "src/components/inteligencia/PriorityCards.tsx",
  ];
  for (const rel of arquivos) {
    const src = readFileSync(new URL(`../${rel}`, import.meta.url), "utf8");
    assert.ok(
      !/\.toFixed\s*\(/.test(src),
      `${rel} usa toFixed — numero decimal renderizaria com ponto numa UI pt-BR`,
    );
  }
});

test("V3-1A pt-BR: format.ts nao acrescenta sinal de delta como fmtPct compartilhado", () => {
  // `fmtPct` de src/lib/formatters.ts e' formatador de DELTA e prefixa `+`;
  // os numeros da Inteligencia sao niveis, nao variacoes.
  assert.ok(!pctBr(8).startsWith("+"));
  assert.ok(!roasBr(12).startsWith("+"));
  assert.equal(pctBr(-3), "-3,0%", "negativo mantem o sinal proprio");
});
