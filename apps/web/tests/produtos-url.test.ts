// Testes do contrato canonico de querystring da pagina Produtos (Gate V3-1A).
//
// Motivo: a pagina guardava aba/marca/bucket so' em estado local e nao lia
// `searchParams` — o filtro nao era reproduzivel por URL, e o CTA do bucket
// Pareto (Inteligencia) nao tinha destino honesto.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  brandParamForEndpoint, buildProdutosHref, buildProdutosQuery, CHANNEL_TO_TAB,
  EMPTY_PRODUTOS_URL_STATE, isParetoBucket, PARETO_BUCKETS, PARETO_BUCKET_QUERY_KEY,
  parseProdutosUrl, PRODUTOS_URL_KEYS, readSingle, TAB_TO_CHANNEL,
} from "../src/lib/produtos-url.ts";
import { FILTER_QUERY_KEYS, isFilterAwarePath } from "../src/lib/filters/nav-links.ts";
import { brandsForTab } from "../src/lib/produtos-tab-transition.ts";

function reader(pairs: [string, string][]) {
  const p = new URLSearchParams();
  for (const [k, v] of pairs) p.append(k, v);
  return p;
}

// ---------------------------------------------------------------------------
// 19. channels -> aba
// ---------------------------------------------------------------------------

test("19. channels=ml seleciona a aba Mercado Livre", () => {
  assert.equal(parseProdutosUrl(reader([["channels", "ml"]])).tab, "ml");
  assert.equal(parseProdutosUrl(reader([["channels", "tiktok"]])).tab, "tiktok");
  assert.equal(parseProdutosUrl(reader([["channels", "shopee"]])).tab, "shopee");
  assert.deepEqual(Object.keys(CHANNEL_TO_TAB).sort(), ["ml", "shopee", "tiktok"]);
});

test("19b. channels ausente, desconhecido, vazio ou MULTIPLO nao seleciona aba", () => {
  assert.equal(parseProdutosUrl(reader([])).tab, null);
  assert.equal(parseProdutosUrl(reader([["channels", "amazon"]])).tab, null);
  assert.equal(parseProdutosUrl(reader([["channels", ""]])).tab, null);
  assert.equal(parseProdutosUrl(reader([["channels", "ml"], ["channels", "tiktok"]])).tab, null,
    "multiplo e ambiguo — ignorado com seguranca");
});

// ---------------------------------------------------------------------------
// 20. brands
// ---------------------------------------------------------------------------

test("20. brands inicializa UMA marca valida para a aba resolvida", () => {
  assert.equal(parseProdutosUrl(reader([["channels", "ml"], ["brands", "rituaria"]])).brand, "rituaria");
  assert.equal(parseProdutosUrl(reader([["channels", "tiktok"], ["brands", "apice"]])).brand, "apice");
});

test("20b. brands incompativel com o canal e' ignorado", () => {
  // apice nao vende no ML — a marca e' validada contra as marcas DA ABA
  assert.ok(!brandsForTab("ml").includes("apice"));
  assert.equal(parseProdutosUrl(reader([["channels", "ml"], ["brands", "apice"]])).brand, null);
});

test("20c. brands invalido, vazio, repetido ou sem aba resolvida e' ignorado", () => {
  assert.equal(parseProdutosUrl(reader([["channels", "ml"], ["brands", "nao-existe"]])).brand, null);
  assert.equal(parseProdutosUrl(reader([["channels", "ml"], ["brands", ""]])).brand, null);
  assert.equal(parseProdutosUrl(reader([["channels", "ml"], ["brands", "a"], ["brands", "b"]])).brand, null);
  assert.equal(parseProdutosUrl(reader([["brands", "rituaria"]])).brand, null,
    "sem aba nao ha como validar compatibilidade");
});

test("20d. um campo ruim nao derruba um campo bom", () => {
  const s = parseProdutosUrl(reader([["channels", "ml"], ["brands", "xxx"], ["pareto_bucket", "A_top50"]]));
  assert.equal(s.tab, "ml");
  assert.equal(s.brand, null);
  assert.equal(s.bucket, "A_top50");
});

// ---------------------------------------------------------------------------
// 21-22. pareto_bucket
// ---------------------------------------------------------------------------

test("21. os quatro buckets allowlisted sao aceitos", () => {
  for (const b of PARETO_BUCKETS) {
    assert.equal(parseProdutosUrl(reader([["channels", "ml"], ["pareto_bucket", b]])).bucket, b);
  }
  assert.deepEqual([...PARETO_BUCKETS], ["A_top50", "B_next30", "C_next15", "D_tail"]);
});

test("22. bucket ausente, invalido, vazio ou repetido e' ignorado com seguranca", () => {
  assert.equal(parseProdutosUrl(reader([["channels", "ml"]])).bucket, null);
  assert.equal(parseProdutosUrl(reader([["channels", "ml"], ["pareto_bucket", "E_outro"]])).bucket, null);
  assert.equal(parseProdutosUrl(reader([["channels", "ml"], ["pareto_bucket", ""]])).bucket, null);
  assert.equal(
    parseProdutosUrl(reader([["channels", "ml"], ["pareto_bucket", "A_top50"], ["pareto_bucket", "D_tail"]])).bucket,
    null, "repetido e ambiguo");
  assert.ok(!isParetoBucket("a_top50"), "case-sensitive por contrato");
  assert.deepEqual(EMPTY_PRODUTOS_URL_STATE, { tab: null, brand: null, bucket: null });
});

test("22b. readSingle rejeita repeticao e aceita leitor sem getAll", () => {
  assert.equal(readSingle(reader([["k", "v"]]), "k"), "v");
  assert.equal(readSingle(reader([["k", "a"], ["k", "b"]]), "k"), null);
  const simples = { get: (k: string) => (k === "k" ? "v" : null) };
  assert.equal(readSingle(simples, "k"), "v");
});

// ---------------------------------------------------------------------------
// 23. brands (pagina) -> brand (endpoint)
// ---------------------------------------------------------------------------

test("23. a pagina fala brands (plural) e o endpoint recebe brand (singular)", () => {
  assert.ok(PRODUTOS_URL_KEYS.includes("brands"));
  assert.ok(!(PRODUTOS_URL_KEYS as readonly string[]).includes("brand"),
    "a URL da pagina nunca usa o nome do parametro do endpoint");
  assert.equal(brandParamForEndpoint("rituaria"), "rituaria");
  assert.equal(brandParamForEndpoint(""), undefined, "todas as marcas = sem filtro no endpoint");
});

// ---------------------------------------------------------------------------
// construcao da URL
// ---------------------------------------------------------------------------

test("build: querystring canonica, omitindo o que esta vazio", () => {
  assert.equal(buildProdutosHref({ tab: "ml", brand: "rituaria", bucket: "A_top50" }),
    "/produtos?channels=ml&brands=rituaria&pareto_bucket=A_top50");
  assert.equal(buildProdutosHref({ tab: "ml" }), "/produtos?channels=ml");
  assert.equal(buildProdutosHref({ tab: "tiktok", brand: "", bucket: null }), "/produtos?channels=tiktok");
  assert.equal(buildProdutosQuery({ tab: "ml", brand: "kokeshi" }), "channels=ml&brands=kokeshi");
  assert.deepEqual(TAB_TO_CHANNEL, { ml: "ml", tiktok: "tiktok", shopee: "shopee" });
});

test("build: nenhuma metrica na URL, ida e volta estavel", () => {
  const href = buildProdutosHref({ tab: "ml", brand: "barbours", bucket: "D_tail" });
  assert.ok(!/gmv|R\$|%|\{|\}|offset|sort/.test(href), href);
  const volta = parseProdutosUrl(new URLSearchParams(href.split("?")[1]));
  assert.deepEqual(volta, { tab: "ml", brand: "barbours", bucket: "D_tail" });
});

// ---------------------------------------------------------------------------
// 25. nao propagacao pela sidebar
// ---------------------------------------------------------------------------

test("25. pareto_bucket e lens nao sao propagados pela sidebar", () => {
  assert.ok(!FILTER_QUERY_KEYS.includes(PARETO_BUCKET_QUERY_KEY));
  assert.ok(!FILTER_QUERY_KEYS.includes("lens"));
  // reforco estrutural: as duas paginas nao participam do contrato de filtros
  assert.ok(!isFilterAwarePath("/produtos"));
  assert.ok(!isFilterAwarePath("/inteligencia"));
});
