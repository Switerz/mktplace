// Gate SH-API-2D — o selo de qualidade de escopo atravessa MCP e tela.
//
// O que estes testes protegem: um consumidor NAO pode receber uma competencia
// em maturacao sem saber. Isso vale para os dois caminhos de saida — o modelo
// (torre_produtos_prioritarios / torre_qualidade_dados) e a pessoa (tela de
// Produtos e de Qualidade). Cobrir so um dos dois deixa o outro como porta
// lateral por onde dado imaturo sai apresentado como definitivo.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

import { Client } from "@modelcontextprotocol/client";
import { InMemoryTransport } from "@modelcontextprotocol/server";

import { buildOracleServer } from "../src/server/oracle/server.ts";
import { SlidingWindowRateLimiter } from "../src/server/oracle/rate-limit.ts";
import {
  BRANDS, CANAIS, FIXED_NOW, HEALTH, jsonFetch, OVERVIEW, PRODUTOS_ML,
  PRODUTOS_SHOPEE, QUALITY, REGIOES_BY_UF, REGIOES_SUMMARY, TREND,
} from "./oracle-fixtures.ts";

function readSource(...segments: string[]): string {
  return readFileSync(
    fileURLToPath(new URL(`../${segments.join("/")}`, import.meta.url)),
    "utf8",
  );
}

// ---------------------------------------------------------------------------
// Selos de referencia
// ---------------------------------------------------------------------------

/** Mes fechado: os seis eixos saudaveis. */
const SELO_MADURO = {
  channel: "shopee",
  brand: null,
  ref_month: "2024-02",
  source_status: "source_covered",
  load_status: "load_current",
  eligibility_status: "eligible",
  maturity_status: "mature",
  coverage_status: "coverage_ok",
  loaded_at: "2024-03-02T10:00:00+00:00",
  load_age_days: 3,
  definitive: true,
  measured: {
    rows_present: 472, eligible_rows: 472, excluded_zero_gmv: 0,
    completed_gmv: 1000, reference_gmv: 980, completed_share: 1.0204,
    maturity_floor: 0.99, brands_present: 5, brands_expected: 5,
    source_covered_from: "2024-01-01", source_covered_through: "2024-03-01",
    daily_max_date: "2024-02-29",
  },
  warnings: [],
};

/** Competencia em maturacao E com carga defasada — dois eixos ao mesmo tempo. */
const SELO_IMATURO = {
  ...SELO_MADURO,
  load_status: "load_stale",
  maturity_status: "materially_immature",
  definitive: false,
  measured: { ...SELO_MADURO.measured, completed_share: 0.754 },
  warnings: [
    {
      code: "shopee_produtos_carga_defasada",
      severity: "warning",
      message: "A diaria ja registrou vendas desta competencia em datas posteriores a ultima publicacao do mart de Produtos.",
    },
    {
      code: "shopee_produtos_fonte_imatura",
      severity: "critical",
      message: "Competencia materialmente imatura: o GMV concluido cobre 75.4% do GMV que a diaria ja registrou (piso 99%). Os numeros por produto vao SUBIR quando os pedidos em transito forem concluidos. Nao use como definitivo.",
    },
  ],
};

function routes(over: Record<string, unknown> = {}) {
  return {
    "/api/v1/performance/overview": OVERVIEW,
    "/api/v1/performance/brands": BRANDS,
    "/api/v1/performance/trend": TREND,
    "/api/v1/performance/canais": CANAIS,
    "/api/v1/performance/quality": QUALITY,
    "/api/v1/performance/produtos/ml": PRODUTOS_ML,
    "/api/v1/performance/produtos/shopee": PRODUTOS_SHOPEE,
    "/api/v1/performance/health-datasource": HEALTH,
    "/api/v1/regioes/summary": REGIOES_SUMMARY,
    "/api/v1/regioes/by-uf": REGIOES_BY_UF,
    ...over,
  };
}

async function connect(rs: Record<string, unknown> = routes()) {
  const server = buildOracleServer({
    backendBaseUrl: "https://mktplace-api.onrender.com",
    fetchImpl: jsonFetch(rs),
    now: () => FIXED_NOW,
    rateLimiter: new SlidingWindowRateLimiter({ limit: 1000, windowMs: 60_000 }),
  });
  const [ct, st] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "scope-quality", version: "0.0.0" });
  await Promise.all([client.connect(ct), server.connect(st)]);
  return client;
}

type Res = { structuredContent: any; content: Array<{ text?: string }>; isError?: boolean };

async function call(name: string, args: Record<string, unknown>, rs = routes()) {
  const client = await connect(rs);
  const res = (await client.callTool({ name, arguments: args })) as unknown as Res;
  assert.notEqual(res.isError, true, `${name} deveria ter sucesso`);
  return res;
}

const shopeeRoute = (quality: unknown) => routes({
  "/api/v1/performance/produtos/shopee": { ...PRODUTOS_SHOPEE, quality },
});

// ---------------------------------------------------------------------------
// torre_produtos_prioritarios
// ---------------------------------------------------------------------------

test("produtos: competencia imatura chega ao modelo como campo estruturado", async () => {
  // `limitations` sozinho nao basta: o modelo decide com CAMPOS. Sem
  // `definitive`/`maturity_status` estruturados, "vendeu R$ X" sai com uma nota
  // de rodape que o modelo pode nao repassar.
  const res = await call("torre_produtos_prioritarios",
    { canal: "shopee", mes: "2024-02", limite: 2 }, shopeeRoute(SELO_IMATURO));
  const sq = res.structuredContent.data.scope_quality;
  assert.equal(sq.definitive, false);
  assert.equal(sq.maturity_status, "materially_immature");
  assert.equal(sq.load_status, "load_stale");
  assert.equal(sq.completed_share, 0.754);
  assert.equal(sq.maturity_floor, 0.99);
  assert.equal(sq.ref_month, "2024-02");
});

test("produtos: o resumo textual avisa quando a competencia nao e definitiva", async () => {
  // O texto e a unica parte da resposta que sempre chega ao usuario final,
  // mesmo quando o modelo ignora o `data`.
  const res = await call("torre_produtos_prioritarios",
    { canal: "shopee", mes: "2024-02", limite: 2 }, shopeeRoute(SELO_IMATURO));
  const texto = res.content.map((c) => c.text ?? "").join(" ");
  assert.match(texto, /NAO definitiva/);
  assert.match(texto, /scope_quality/);
});

test("produtos: competencia madura nao poluí o resumo com aviso", async () => {
  const res = await call("torre_produtos_prioritarios",
    { canal: "shopee", mes: "2024-02", limite: 2 }, shopeeRoute(SELO_MADURO));
  const texto = res.content.map((c) => c.text ?? "").join(" ");
  assert.doesNotMatch(texto, /NAO definitiva/);
  assert.equal(res.structuredContent.data.scope_quality.definitive, true);
});

test("produtos: cada aviso do backend vira UMA limitacao, sem texto duplicado localmente", async () => {
  // limitations.ts proibe duplicar aqui o que a resposta ja informa — a
  // descricao TEM de ser a do backend, senao o conector contradiz o mart
  // quando a medicao mudar.
  const res = await call("torre_produtos_prioritarios",
    { canal: "shopee", mes: "2024-02", limite: 2 }, shopeeRoute(SELO_IMATURO));
  const lims = res.structuredContent.data.limitations as Array<{ topic: string; description: string; scope: string }>;
  for (const w of SELO_IMATURO.warnings) {
    const achado = lims.find((l) => l.topic === w.code);
    assert.ok(achado, `limitacao ausente para ${w.code}`);
    assert.equal(achado.description, w.message, "descricao nao veio do backend");
    assert.match(achado.scope, /Produtos — shopee/);
  }
});

test("produtos: canal sem selo devolve scope_quality null, nunca um 'ok' inventado", async () => {
  const res = await call("torre_produtos_prioritarios", { canal: "ml", limite: 2 });
  assert.equal(res.structuredContent.data.scope_quality, null);
});

test("produtos: upstream sem o campo quality continua valido e degrada para null", async () => {
  // Compatibilidade: um backend ainda nao atualizado nao pode derrubar a tool.
  const res = await call("torre_produtos_prioritarios",
    { canal: "shopee", mes: "2024-02", limite: 2 });
  assert.equal(res.structuredContent.data.scope_quality, null);
});

test("produtos: status desconhecido no upstream e RECUSADO em vez de repassado", async () => {
  // Um estado novo tem de quebrar aqui; repassar "provavelmente_ok" ao modelo
  // seria pior que falhar, porque ele trataria como aprovacao.
  const client = await connect(shopeeRoute({
    ...SELO_MADURO, maturity_status: "provavelmente_ok",
  }));
  const res = (await client.callTool({
    name: "torre_produtos_prioritarios",
    arguments: { canal: "shopee", mes: "2024-02", limite: 2 },
  })) as unknown as Res;
  assert.equal(res.isError, true, "status desconhecido deveria ser recusado");
});

// ---------------------------------------------------------------------------
// torre_qualidade_dados
// ---------------------------------------------------------------------------

const qualityRoute = (q: unknown) => routes({
  "/api/v1/performance/quality": { ...QUALITY, produtos_shopee_quality: q },
});

test("qualidade: o selo do mart de Produtos aparece na tool consultada ANTES de confiar", async () => {
  // Sem isto era possivel perguntar "posso confiar?", ver 2,25% de
  // cancelamento e concluir que sim sobre uma competencia cujo mart de
  // Produtos cobria ~75% do GMV.
  const res = await call("torre_qualidade_dados", {}, qualityRoute(SELO_IMATURO));
  const sq = res.structuredContent.data.produtos_shopee_scope;
  assert.equal(sq.definitive, false);
  assert.equal(sq.maturity_status, "materially_immature");
});

test("qualidade: o selo NAO se mistura aos indicadores operacionais da diaria", async () => {
  // Fontes diferentes, ciclos diferentes. Um cancelamento saudavel na diaria
  // nao autoriza conclusao sobre o mart de Produtos.
  const res = await call("torre_qualidade_dados", {}, qualityRoute(SELO_IMATURO));
  const ind = res.structuredContent.data.quality_indicators as Array<{ channel: string; metric: string }>;
  assert.ok(ind.some((i) => i.channel === "shopee" && i.metric === "taxa_cancelamento"));
  assert.ok(!ind.some((i) => i.metric.includes("maturidade")),
    "maturidade nao pode virar mais um indicador operacional");
});

test("qualidade: o resumo textual carrega a ressalva do mart de Produtos", async () => {
  const res = await call("torre_qualidade_dados", {}, qualityRoute(SELO_IMATURO));
  const texto = res.content.map((c) => c.text ?? "").join(" ");
  assert.match(texto, /NAO e definitivo em 2024-02/);
});

test("qualidade: sem medicao o campo vem null e o resumo fica limpo", async () => {
  const res = await call("torre_qualidade_dados", {});
  assert.equal(res.structuredContent.data.produtos_shopee_scope, null);
  const texto = res.content.map((c) => c.text ?? "").join(" ");
  assert.doesNotMatch(texto, /NAO e definitivo/);
});

test("qualidade: competencia madura nao gera limitacao de escopo", async () => {
  const res = await call("torre_qualidade_dados", {}, qualityRoute(SELO_MADURO));
  const lims = res.structuredContent.data.limitations as Array<{ topic: string }>;
  assert.ok(!lims.some((l) => l.topic.startsWith("shopee_produtos_")));
  assert.equal(res.structuredContent.data.produtos_shopee_scope.definitive, true);
});

// ---------------------------------------------------------------------------
// Superficie visual
// ---------------------------------------------------------------------------

test("faixa: ausencia de selo NAO renderiza aprovacao", () => {
  const src = readSource("src", "components", "ScopeQualityBanner.tsx");
  assert.match(src, /if \(!quality\) return null;/,
    "selo ausente tem de sumir da tela, nunca virar um 'ok' que ninguem apurou");
});

test("faixa: TODOS os avisos sao exibidos, nao apenas o mais grave", () => {
  // Mostrar so o pior esconderia, por exemplo, a carga defasada atras da
  // imaturidade — o defeito exato que este contrato veio corrigir.
  const src = readSource("src", "components", "ScopeQualityBanner.tsx");
  assert.match(src, /quality\.warnings\.map/);
});

test("faixa: a data de publicacao do mart aparece tambem no caso saudavel", () => {
  const src = readSource("src", "components", "ScopeQualityBanner.tsx");
  const okBlock = src.slice(src.indexOf("scope-quality-ok") - 400, src.indexOf("scope-quality-ok") + 300);
  assert.match(okBlock, /publicacao/, "o caso definitivo tambem precisa datar a publicacao");
});

test("tela de Produtos: a faixa vem ANTES dos cards de Pareto", () => {
  // Quem le de cima para baixo tem de saber do regime antes de olhar os
  // numeros, nao depois de ja ter concluido.
  const src = readSource("app", "produtos", "page.tsx");
  const faixa = src.indexOf("<ScopeQualityBanner");
  const pareto = src.indexOf("buckets={shDisplaySummary?.buckets");
  assert.ok(faixa > 0 && pareto > 0, "ambos os blocos devem existir na aba Shopee");
  assert.ok(faixa < pareto, "a faixa de qualidade tem de preceder os cards A/B/C/D");
});

test("tela de Qualidade: o selo nao depende de haver metrica operacional Shopee", () => {
  // A pergunta "posso confiar nos numeros por produto?" continua valendo mesmo
  // num mes sem nenhum cancelamento para exibir.
  const src = readSource("app", "qualidade", "page.tsx");
  const bloco = src.indexOf("{produtosShQuality && (");
  const blocoOperacional = src.indexOf("{hasShQuality && (");
  assert.ok(bloco > 0, "secao do selo ausente");
  assert.ok(bloco < blocoOperacional, "o selo nao pode estar aninhado no bloco operacional");
});

test("tela de Qualidade: falha de requisicao limpa o selo antigo", () => {
  const src = readSource("app", "qualidade", "page.tsx");
  const catchBlock = src.slice(src.indexOf(".catch(() => {"));
  assert.match(catchBlock.slice(0, 500), /setProdutosShQuality\(null\)/,
    "um selo antigo nao pode continuar descrevendo um escopo nao medido");
});

test("nenhuma superficie do cliente grava o piso ou os meses do incidente", () => {
  // O piso e a competencia sao MEDIDOS pela API. Fixar qualquer um deles no
  // cliente faria a regra parar de acompanhar o dado.
  for (const arquivo of [
    ["src", "components", "ScopeQualityBanner.tsx"],
    ["app", "produtos", "page.tsx"],
    ["app", "qualidade", "page.tsx"],
  ]) {
    const src = readSource(...arquivo);
    for (const proibido of ["0.99", "2026-07", "2026-08"]) {
      assert.ok(!src.includes(proibido),
        `${arquivo.join("/")} nao pode conter o literal ${proibido}`);
    }
  }
});
