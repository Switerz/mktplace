// Gate PMA-2C4B — contraprovas do frontend multicanal do monitoramento.
//
// O que estes testes travam: o Mercado Livre nao muda, Shopee e TikTok nao
// aparecem enquanto a capacidade estiver desligada, e nenhum texto de um canal
// vaza para o outro — em especial a frase de D-1, que e' verdadeira so' no ML.
//
// As varreduras estruturais leem o CODIGO da pagina sem comentario: as proprias
// docstrings citam o que e' proibido, e uma varredura ingenua reprovaria a
// documentacao da garantia.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  buildMonitoramentoPrecoQuery,
  isMarketplace,
  mensagemDeFalha,
  MARKETPLACES,
  type MonitoramentoPrecoRow,
} from "../src/lib/monitoramento-preco-contract.ts";
import {
  CANAIS,
  INDISPONIVEL,
  PRECO_NAO_OBSERVADO,
  buildMonitoramentoRequestKey,
  canaisDisponiveis,
  canalHabilitado,
  canalComPreposicao,
  brandLabel,
  canalLabel,
  coberturaPorConta,
  filtrosDoCanal,
  fmtPrecoObservado,
  foraDoEscopo,
  marcaParaEscopo,
  PRODUCT_TYPE_ORDER,
  ROTULO_FORA_DE_ESCOPO,
  politicaDataView,
  productTypeLabel,
  resumoCobertura,
  situacaoFrescor,
  temPrecoObservado,
  urlAnuncioSegura,
} from "../src/lib/monitoramento-preco.ts";

const PAGE = new URL("../app/monitoramento-preco/page.tsx", import.meta.url);
const LIB = new URL("../src/lib/monitoramento-preco.ts", import.meta.url);
const CONTRATO = new URL("../src/lib/monitoramento-preco-contract.ts", import.meta.url);

async function ler(url: URL): Promise<string> {
  const { readFile } = await import("node:fs/promises");
  return readFile(url, "utf8");
}

async function lerCodigo(url: URL): Promise<string> {
  const bruto = await ler(url);
  return bruto
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1 ");
}

/** Restaura as variaveis de capacidade ao estado de producao (ausentes). */
function semFlags<T>(fn: () => T): T {
  const antes = {
    s: process.env.NEXT_PUBLIC_PMA_SHOPEE_ENABLED,
    t: process.env.NEXT_PUBLIC_PMA_TIKTOK_ENABLED,
  };
  delete process.env.NEXT_PUBLIC_PMA_SHOPEE_ENABLED;
  delete process.env.NEXT_PUBLIC_PMA_TIKTOK_ENABLED;
  try {
    return fn();
  } finally {
    if (antes.s === undefined) delete process.env.NEXT_PUBLIC_PMA_SHOPEE_ENABLED;
    else process.env.NEXT_PUBLIC_PMA_SHOPEE_ENABLED = antes.s;
    if (antes.t === undefined) delete process.env.NEXT_PUBLIC_PMA_TIKTOK_ENABLED;
    else process.env.NEXT_PUBLIC_PMA_TIKTOK_ENABLED = antes.t;
  }
}

function comFlags<T>(valores: Record<string, string>, fn: () => T): T {
  return semFlags(() => {
    Object.assign(process.env, valores);
    return fn();
  });
}

function linha(over: Partial<MonitoramentoPrecoRow> = {}): MonitoramentoPrecoRow {
  return {
    product_name: null, brand: "barbours", marketplace: "shopee",
    item_id: "IT-1", seller_sku: "SKU-1", gtin: null,
    listing_title: "Shampoo", permalink: null, listing_status: "active",
    currency: "BRL", ref_date: "2026-09-16", observed_at: null,
    listing_metadata_updated_at: null, advertised_price: 50, original_price: null,
    observed_effective_amount: 50, shipping_amount: null,
    seller_coupon_amount: null, platform_subsidy_amount: null,
    checkout_price: null, coverage_status: "advertised_only",
    suggested_retail_amount: null, reference_type: "suggested_retail_pdv",
    validity_status: "missing",
    policy_status: "not_applicable_to_own_store_monitoring",
    reference_captured_at: null, reference_row_id: null,
    difference_amount: null, difference_pct: null, match_method: null,
    match_quality: "unmatched", reference_candidate_count: 0,
    comparison_status: "no_reference", non_comparable_reason: null,
    freshness_status: "fresh", limitations: [],
    offer_key: "IT-1", shop_account: "barbours", parent_item_id: "IT-1",
    model_id: null, observed_date: "2026-09-16", date_policy: "snapshot_current",
    product_type: "no_kit_signal", product_type_source: "channel_flag",
    snapshot_status: "current", account_watermark_at: null,
    observed_price_source: "current_price", list_price: null,
    promo_context: "available", promo_id: null, promo_discount_pct: null,
    business_scope: "in_scope", batch_id: null, source_run_id: null,
    ...over,
  };
}

// ---------------------------------------------------------------------------
// 1 a 4 — capacidade fail-closed
// ---------------------------------------------------------------------------

test("1. flags ausentes resolvem false e so' o ML fica disponivel", () => {
  semFlags(() => {
    assert.equal(canalHabilitado("ml"), true);
    assert.equal(canalHabilitado("shopee"), false);
    assert.equal(canalHabilitado("tiktok"), false);
    assert.deepEqual(canaisDisponiveis().map((c) => c.id), ["ml"]);
  });
});

test("2. canal desativado nao aparece no seletor", () => {
  comFlags({ NEXT_PUBLIC_PMA_SHOPEE_ENABLED: "true" }, () => {
    assert.deepEqual(canaisDisponiveis().map((c) => c.id), ["ml", "shopee"]);
  });
});

test("3. so' a string exata `true` liga o canal", () => {
  for (const valor of ["1", "yes", "True", "TRUE", "", "false"]) {
    comFlags({ NEXT_PUBLIC_PMA_SHOPEE_ENABLED: valor }, () => {
      assert.equal(canalHabilitado("shopee"), false, `ligou com "${valor}"`);
    });
  }
});

test("4. o seletor so' e' renderizado com mais de um canal disponivel", async () => {
  const codigo = await lerCodigo(PAGE);
  assert.ok(codigo.includes("canais.length > 1"),
    "o seletor precisa estar atras da contagem de canais disponiveis");
  assert.ok(codigo.includes("canaisDisponiveis()"),
    "a lista tem de vir da funcao de capacidade, nunca de uma constante");
});

// ---------------------------------------------------------------------------
// 5 a 6 — troca de canal
// ---------------------------------------------------------------------------

test("5. o canal faz parte da chave de requisicao", () => {
  const base = {
    brand: "", status: "", productQuery: "", limit: 500, offset: 0,
  } as const;
  const a = buildMonitoramentoRequestKey({ ...base, marketplace: "ml" });
  const b = buildMonitoramentoRequestKey({ ...base, marketplace: "shopee" });
  assert.notEqual(a, b, "sem isto a resposta do canal anterior seria reusada");
  assert.ok(a.startsWith("ml"));
  assert.ok(b.startsWith("shopee"));
});

test("6. conta e tipo tambem entram na chave", () => {
  const base = {
    marketplace: "shopee", brand: "", status: "", productQuery: "",
    limit: 500, offset: 0,
  } as const;
  assert.notEqual(
    buildMonitoramentoRequestKey(base),
    buildMonitoramentoRequestKey({ ...base, shopAccount: "apice" }),
  );
  assert.notEqual(
    buildMonitoramentoRequestKey(base),
    buildMonitoramentoRequestKey({ ...base, productType: "kit_confirmed" }),
  );
});

test("7. trocar de canal limpa filtros, pagina, data e o dado anterior", async () => {
  const codigo = await lerCodigo(PAGE);
  const corpo = codigo.slice(codigo.indexOf("const trocaCanal"));
  const bloco = corpo.slice(0, corpo.indexOf("const submeterBusca"));
  for (const limpeza of [
    "setOffset(0)", "setBrand(\"\")", "setStatus(\"\")", "setProductQuery(\"\")",
    "setShopAccount(\"\")", "setProductType(\"\")", "setObservedDate(\"\")",
    // Sem isto, KPIs e tabela do canal anterior ficariam sob o titulo do novo.
    "setDados(null)",
  ]) {
    assert.ok(bloco.includes(limpeza), `trocaCanal precisa de ${limpeza}`);
  }
});

test("8. a guarda de resposta atrasada continua de pe'", async () => {
  const codigo = await lerCodigo(PAGE);
  assert.ok(codigo.includes("latestKey.current !== requestKey"),
    "a resposta antiga nao pode sobrescrever a atual");
  assert.ok(codigo.includes("new AbortController()"));
});

// ---------------------------------------------------------------------------
// 9 a 11 — politica de data
// ---------------------------------------------------------------------------

test("9. o ML mantem o teto D-1 e a frase publicada", () => {
  const v = politicaDataView({
    date_policy: "closed_day",
    eligible_ref_date: "2026-09-15",
    snapshot_mutability: null,
  });
  assert.ok(v.tetoRotulo.includes("D−1"));
  assert.ok(v.explicacao.includes("D−1"));
  assert.equal(v.avisoMutavel, null, "o ML nunca serve fotografia mutavel");
});

test("10. D0 dos canais e' rotulado como fotografia mutavel", () => {
  const v = politicaDataView({
    date_policy: "snapshot_current",
    eligible_ref_date: "2026-09-16",
    snapshot_mutability: "mutable_operational_snapshot",
  });
  assert.ok(v.avisoMutavel, "D0 precisa de aviso de mutabilidade");
  const aviso = v.avisoMutavel!;
  assert.ok(/ainda pode mudar/i.test(aviso));
  // O aviso PRECISA negar explicitamente o fechamento. Dizer "não é período
  // fechado" e' o texto correto; o que nao pode e' AFIRMAR que e'.
  assert.ok(/não é período fechado/i.test(aviso), aviso);
  assert.ok(/nem contagem definitiva/i.test(aviso), aviso);
  // Nenhuma afirmacao positiva de fechamento sobrevive.
  assert.ok(!/(?<!não )(?:é|e') (?:um )?período fechado/i.test(aviso), aviso);
  assert.ok(!/atualização completa/i.test(aviso), aviso);
});

test("11. nenhum texto de D-1 aparece nos canais novos", () => {
  for (const mut of ["mutable_operational_snapshot", "settled_snapshot"] as const) {
    const v = politicaDataView({
      date_policy: "snapshot_current",
      eligible_ref_date: "2026-09-16",
      snapshot_mutability: mut,
    });
    const texto = `${v.tetoRotulo} ${v.explicacao} ${v.avisoMutavel ?? ""}`;
    assert.ok(!texto.includes("D−1"), texto);
    assert.ok(!texto.includes("D-1"), texto);
  }
});

// ---------------------------------------------------------------------------
// 12 a 14 — preco ausente
// ---------------------------------------------------------------------------

test("12. advertised_price nulo NUNCA vira zero", () => {
  assert.equal(fmtPrecoObservado(null), PRECO_NAO_OBSERVADO);
  assert.equal(fmtPrecoObservado(undefined), PRECO_NAO_OBSERVADO);
  assert.equal(fmtPrecoObservado(Number.NaN), PRECO_NAO_OBSERVADO);
  assert.ok(!fmtPrecoObservado(null).includes("0,00"));
  assert.notEqual(fmtPrecoObservado(null), INDISPONIVEL);
  assert.ok(fmtPrecoObservado(50).includes("50,00"));
});

test("13. sem preco nao ha diferenca a apresentar", () => {
  const semPreco = linha({ advertised_price: null, observed_effective_amount: null });
  assert.equal(temPrecoObservado(semPreco), false);
  // A API ja' devolve a diferenca nula; a tela nao a reconstroi.
  assert.equal(semPreco.difference_amount, null);
  assert.equal(semPreco.difference_pct, null);
});

test("14. a pagina usa o formatador de preco observado, nao o de moeda cru", async () => {
  const codigo = await lerCodigo(PAGE);
  assert.ok(codigo.includes("fmtPrecoObservado(row.advertised_price)"));
  assert.ok(!codigo.includes("fmtMoeda(row.advertised_price)"),
    "o preco anunciado nao pode cair no formatador que devolve travessao");
});

// ---------------------------------------------------------------------------
// 15 a 17 — frescor e tipo de produto
// ---------------------------------------------------------------------------

test("15. fotografia stale domina o frescor da linha", () => {
  // Linha `stale` com fotografia `stale` = atraso de pipeline.
  assert.equal(situacaoFrescor({ freshness_status: "stale" }, "stale"),
    "fotografia_atrasada");
  // Linha `stale` com fotografia em dia = a OFERTA nao foi revista.
  assert.equal(situacaoFrescor({ freshness_status: "stale" }, "fresh"),
    "oferta_nao_revista");
  assert.equal(situacaoFrescor({ freshness_status: "fresh" }, "fresh"),
    "fotografia_em_dia");
  assert.equal(situacaoFrescor({ freshness_status: "historical" }, "historical"),
    "consulta_historica");
});

test("16. oferta nao revista nao manda verificar o sync", async () => {
  const lib = await ler(LIB);
  const rotulos = lib.slice(lib.indexOf("SITUACAO_FRESCOR_LABELS"));
  const bloco = rotulos.slice(0, rotulos.indexOf("}"));
  assert.ok(bloco.includes("Não revista nesta fotografia"));
  assert.ok(!/sync/i.test(bloco),
    "o rotulo da oferta nao revista nao pode apontar para o sync");
});

test("17. product_type e' traduzido, nunca recalculado", async () => {
  assert.equal(productTypeLabel("kit_confirmed"), "Kit confirmado");
  assert.equal(productTypeLabel("kit_suspected"), "Possível kit");
  assert.equal(productTypeLabel("product_type_unknown"), "Sinal de kit desconhecido");
  assert.equal(productTypeLabel(null), INDISPONIVEL);
  // `product_type_unknown` NAO pode virar "produto simples".
  assert.ok(!/simples/i.test(productTypeLabel("product_type_unknown")));

  const codigo = await lerCodigo(PAGE);
  for (const proibido of ["KIT", "kit_suspected =", "classificaKit", "inferirKit"]) {
    assert.ok(!codigo.includes(proibido),
      `a pagina nao pode reclassificar kit (${proibido})`);
  }
  assert.ok(codigo.includes("productTypeLabel(row.product_type)"));
});

// ---------------------------------------------------------------------------
// 18 a 21 — KPIs, filtros, links e contrato
// ---------------------------------------------------------------------------

test("18. KPIs continuam vindo da API, sem recalculo no navegador", async () => {
  const codigo = await lerCodigo(PAGE);
  assert.ok(codigo.includes("dados?.kpis ?? null"));
  assert.ok(codigo.includes("buildKpiViews(kpis, marketplace)"),
    "o cartao de KPI precisa saber de que canal fala");
  // Nenhuma soma de linha alimentando cartao.
  assert.ok(!/linhas\.(?:reduce|filter)\([^)]*\)\.length/.test(codigo),
    "KPI nao pode ser derivado das linhas da pagina");
});

test("19. filtros de conta e tipo so' existem nos canais que os tem", () => {
  assert.deepEqual(filtrosDoCanal("ml"), { conta: false, tipoDeProduto: false });
  assert.deepEqual(filtrosDoCanal("shopee"), { conta: true, tipoDeProduto: true });
  assert.deepEqual(filtrosDoCanal("tiktok"), { conta: true, tipoDeProduto: true });
});

test("20. a query so' envia conta e tipo quando foram pedidos", () => {
  const limpa = buildMonitoramentoPrecoQuery({ marketplace: "ml" });
  assert.equal(limpa.get("shop_account"), null);
  assert.equal(limpa.get("product_type"), null);
  const cheia = buildMonitoramentoPrecoQuery({
    marketplace: "shopee", shopAccount: "apice", productType: "kit_confirmed",
  });
  assert.equal(cheia.get("marketplace"), "shopee");
  assert.equal(cheia.get("shop_account"), "apice");
  assert.equal(cheia.get("product_type"), "kit_confirmed");
});

test("21. marketplace invalido e' rejeitado e nunca vira requisicao", () => {
  assert.equal(isMarketplace("ml"), true);
  assert.equal(isMarketplace("shopee"), true);
  assert.equal(isMarketplace("tiktok"), true);
  for (const ruim of ["amazon", "ML", "", null, undefined, 1, {}]) {
    assert.equal(isMarketplace(ruim), false, String(ruim));
  }
  // Valor fora do contrato cai para o canal publicado, nao viaja como lixo.
  const qs = buildMonitoramentoPrecoQuery({
    marketplace: "amazon" as never,
  });
  assert.equal(qs.get("marketplace"), "ml");
  assert.deepEqual([...MARKETPLACES], ["ml", "shopee", "tiktok"]);
  assert.deepEqual(CANAIS.map((c) => c.id), ["ml", "shopee", "tiktok"]);
});

test("22. link so' com HTTPS e dominio DO CANAL", () => {
  assert.equal(
    urlAnuncioSegura("https://produto.mercadolivre.com.br/x", "ml"),
    "https://produto.mercadolivre.com.br/x",
  );
  // Dominio de outro canal nao vira link, mesmo com HTTPS.
  assert.equal(urlAnuncioSegura("https://shopee.com.br/x", "ml"), null);
  assert.equal(urlAnuncioSegura("https://produto.mercadolivre.com.br/x", "shopee"), null);
  assert.equal(urlAnuncioSegura("https://shopee.com.br/x", "shopee"), "https://shopee.com.br/x");
  assert.equal(urlAnuncioSegura("https://shop.tiktok.com/x", "tiktok"), "https://shop.tiktok.com/x");
  // HTTP nunca.
  assert.equal(urlAnuncioSegura("http://shopee.com.br/x", "shopee"), null);
  assert.equal(urlAnuncioSegura(null, "shopee"), null);
});

test("23. a pagina nunca constroi URL a partir de offer_key", async () => {
  const codigo = await lerCodigo(PAGE);
  assert.ok(!/https?:\/\/[^"'`]*\$\{/.test(codigo),
    "nenhuma URL montada por interpolacao na pagina");
  assert.ok(codigo.includes("urlAnuncioSegura(linhaAberta.permalink, marketplace)"),
    "o link tem de passar pela allowlist do canal");
  const alvo = codigo.slice(codigo.indexOf("linkAnuncio"));
  assert.ok(/noopener/.test(codigo) && /noreferrer/.test(codigo), alvo.slice(0, 80));
});

// ---------------------------------------------------------------------------
// Gate PMA-2C4B-R — dimensao visivel tem de ser filtravel
// ---------------------------------------------------------------------------

test("R1. marca fora do escopo e' ROTULADA, nunca exibida em silencio", async () => {
  assert.equal(foraDoEscopo(linha({ business_scope: "out_of_business_scope" })), true);
  assert.equal(foraDoEscopo(linha({ business_scope: "in_scope" })), false);
  // O ML nao modela escopo de negocio: nulo nao vira "fora".
  assert.equal(foraDoEscopo(linha({ business_scope: null })), false);

  const codigo = await lerCodigo(PAGE);
  assert.ok(codigo.includes("foraDoEscopo(row)"),
    "a linha da tabela precisa do rotulo");
  assert.ok(codigo.includes("foraDoEscopo(linhaAberta)"),
    "o dialogo precisa do rotulo");
  assert.ok(codigo.includes("ROTULO_FORA_DE_ESCOPO"));
});

test("R2. o escopo vira filtro do SERVIDOR, com total e paginacao corretos", () => {
  const monitoradas = ["apice", "barbours", "kokeshi", "lescent", "rituaria"];
  assert.equal(marcaParaEscopo("todos", monitoradas), "",
    "sem filtro, a fotografia inteira — inclusive fora do escopo");
  assert.equal(marcaParaEscopo("monitoradas", monitoradas),
    "apice,barbours,kokeshi,lescent,rituaria",
    "a API ja' aceita lista de marcas: o filtro e' do servidor, nao da pagina");
  // Lista vazia nao pode virar um `brand=` que o backend recusaria.
  assert.equal(marcaParaEscopo("monitoradas", []), "");
});

test("R3. marca escolhida a mao vence o escopo", async () => {
  const codigo = await lerCodigo(PAGE);
  assert.ok(codigo.includes("brand || marcaParaEscopo(escopo"),
    "o pedido explicito do operador e' mais especifico que o escopo");
});

test("R4. o filtro de tipo oferece os QUATRO estados, nao so' os ativos", async () => {
  const codigo = await lerCodigo(PAGE);
  assert.ok(codigo.includes("const tiposDoCanal = PRODUCT_TYPE_ORDER;"),
    "filtrar as opcoes por `product_type_counts` esconderia do filtro um tipo "
    + "que existe apenas entre as INATIVAS — visivel na coluna e inalcancavel");
  assert.ok(!codigo.includes("product_type_counts?.[tp] ?? 0) > 0"));
  assert.equal(PRODUCT_TYPE_ORDER.length, 4);
});

test("R5. toda dimensao visivel tem filtro correspondente", async () => {
  const codigo = await lerCodigo(PAGE);
  // Marca -> select de marca; conta -> select de conta; tipo -> select de tipo;
  // situacao -> select de situacao; escopo -> select de escopo.
  for (const controle of ["marcaId", "contaId", "tipoId", "situacaoId", "escopoId"]) {
    assert.ok(codigo.includes(`id={${controle}}`), `falta o controle ${controle}`);
  }
  // As opcoes de conta saem da API, nunca de lista fixa no frontend.
  assert.ok(codigo.includes("contasDoFiltro.map"));
  // Gate PMA-2C4D3-H2: as de marca tambem saem da API, mas agora da COBERTURA
  // (`observed_brands`) e nao do escopo de monitoramento. A intencao do teste
  // — nada de lista fixa no frontend — e' a mesma; mudou a fonte.
  assert.ok(codigo.includes("marcasDoFiltro.map"));
  assert.ok(codigo.includes("meta?.observed_brands ?? meta?.monitored_brands"),
    "a derivacao precisa vir da API, com queda para o campo antigo enquanto o "
    + "backend novo nao estiver publicado");
});

test("R7. o que e' novo fica ESCOPADO aos canais; o ML nao ganha nada", async () => {
  const codigo = await lerCodigo(PAGE);
  // Subtitulo publicado do ML, palavra por palavra.
  assert.ok(codigo.includes(
    "Compara os preços anunciados das lojas próprias no Mercado Livre com o "
    + "preço sugerido de revenda (PDV) das tabelas B2B."),
    "o subtitulo do ML e' contrato publicado");
  // O chip de politica de data nao aparece no ML.
  assert.ok(codigo.includes('meta && marketplace !== "ml" &&'),
    "o chip de teto de data e' so' dos canais novos");
  // O sublabel de frescor por linha so' existe onde o frescor VARIA por linha.
  assert.ok(codigo.includes('meta?.date_policy === "snapshot_current" &&'),
    "no ML todas as linhas compartilham o frescor da resposta");
  // Preposicao: "do Mercado Livre", nunca "de Mercado Livre".
  assert.equal(canalComPreposicao("ml"), "do Mercado Livre");
  assert.equal(canalComPreposicao("shopee"), "da Shopee");
  assert.equal(canalComPreposicao("tiktok"), "da TikTok Shop");
  assert.ok(!codigo.includes("de {canalLabel(marketplace)}"),
    "concordancia: 'de Mercado Livre' e' agramatical");
});


test("R6. trocar de canal e limpar filtros tambem zeram o escopo", async () => {
  const codigo = await lerCodigo(PAGE);
  const trechos = codigo.split("setEscopo(\"todos\")");
  assert.ok(trechos.length >= 3,
    "o escopo precisa ser zerado na troca de canal E em limpar filtros");
});

// ---------------------------------------------------------------------------
// 24 a 26 — cobertura, PII e compatibilidade do ML
// ---------------------------------------------------------------------------

test("24. cobertura por conta sai de account_clocks, sem soma inventada", () => {
  const relogios = [
    { account: "apice", observed_at: null, refreshed_at: null,
      account_watermark_at: "2026-09-16T09:04:12+00:00",
      offers: 225, current_offers: 100, stale_offers: 125 },
    { account: "barbours", observed_at: null, refreshed_at: null,
      account_watermark_at: null, offers: 294, current_offers: 294,
      stale_offers: 0 },
  ];
  const v = coberturaPorConta(relogios);
  assert.equal(v.length, 2);
  assert.equal(v[0].conta, "apice");
  assert.equal(v[0].ofertas, 225);
  assert.equal(v[0].naoRevistas, 125);
  // O ML publica um relogio sem contagem: ausente vira nulo, nunca zero.
  const semContagem = coberturaPorConta([
    { account: "ml", observed_at: null, refreshed_at: null },
  ]);
  assert.equal(semContagem[0].ofertas, null);
  assert.equal(semContagem[0].revistas, null);
});

test("25. resumo de cobertura nao inventa contagem quando o canal nao a tem", () => {
  const ml = resumoCobertura({
    account_clocks: [{ account: "ml", observed_at: null, refreshed_at: null }],
    out_of_scope_offer_count: 0,
    snapshot_status_counts: {},
  });
  assert.equal(ml.contasObservadas, 1);
  assert.equal(ml.revistas, null, "sem snapshot_status o ML nao afirma frescor por oferta");
  const canal = resumoCobertura({
    account_clocks: [],
    out_of_scope_offer_count: 223,
    snapshot_status_counts: { current: 238, stale: 454 },
  });
  assert.equal(canal.revistas, 238);
  assert.equal(canal.naoRevistas, 454);
  assert.equal(canal.ofertasForaDeEscopo, 223);
});

test("26. nenhum campo de PII ou seller_id e' renderizado", async () => {
  const codigo = await lerCodigo(PAGE);
  for (const proibido of ["seller_id", "cpf", "cnpj", "buyer", "destinatario",
                          "telefone", "endereco"]) {
    assert.ok(!codigo.toLowerCase().includes(proibido),
      `a pagina nao pode renderizar ${proibido}`);
  }
});

test("27. o Mercado Livre continua o padrao e sem controles de canal", async () => {
  const codigo = await lerCodigo(PAGE);
  assert.ok(codigo.includes('useState<Marketplace>("ml")'),
    "o ML tem de continuar selecionado por padrao");
  assert.equal(canalLabel("ml"), "Mercado Livre");
  // Com a capacidade desligada, o seletor nao existe e a tela e' a de antes.
  semFlags(() => {
    assert.equal(canaisDisponiveis().length, 1);
  });
  // Os controles exclusivos ficam atras da capacidade do canal.
  assert.ok(codigo.includes("filtros.conta &&"));
  assert.ok(codigo.includes("filtros.tipoDeProduto &&"));
});

test("28. o contrato tipa os tres canais e o preco anulavel", async () => {
  const contrato = await ler(CONTRATO);
  assert.ok(contrato.includes('export type Marketplace = "ml" | "shopee" | "tiktok"'));
  assert.ok(contrato.includes("marketplace: Marketplace;"));
  assert.ok(contrato.includes("advertised_price: number | null;"));
  assert.ok(contrato.includes("observed_effective_amount: number | null;"));
  assert.ok(contrato.includes("metrics: MonitoramentoPrecoMetrics;"));
  // Sem `any` nem cast que silencie incompatibilidade.
  const codigo = await lerCodigo(CONTRATO);
  assert.ok(!/:\s*any\b/.test(codigo), "o contrato nao pode usar `any`");
  assert.ok(!/as\s+unknown\s+as/.test(codigo), "cast duplo silencia incompatibilidade");
});

// ---------------------------------------------------------------------------
// Gate PMA-2C4D3-H1 — estado do seletor e copy por canal
// ---------------------------------------------------------------------------

test("H1-1. cada botao do seletor declara `aria-pressed`, nao so' o ativo", async () => {
  const codigo = await ler(PAGE);
  const bloco = codigo.split("seletor de canal")[1].split("</section>")[0];
  assert.ok(bloco.includes("aria-pressed={ativo}"),
    "os tres botoes precisam expor o estado; `aria-current` so' marcava o ativo "
    + "e deixava os inativos sem atributo nenhum");
  // Procura o ATRIBUTO, nao a palavra: o comentario do codigo explica por que
  // `aria-current` saiu, e citar o nome ali nao pode reprovar o teste.
  assert.ok(!/aria-current=/.test(bloco),
    "um estado so': `aria-current` junto com `aria-pressed` e' redundante");
});

test("H1-2. exatamente um canal fica pressionado, e ele vem de `marketplace`", async () => {
  const codigo = await ler(PAGE);
  const bloco = codigo.split("seletor de canal")[1].split("</section>")[0];
  // `ativo` e' a unica fonte do estado, e compara o id do canal com o ativo.
  assert.ok(/const ativo = c\.id === marketplace/.test(bloco),
    "o pressionado precisa ser derivado do canal ativo, nao de indice ou ordem");
  assert.equal((bloco.match(/aria-pressed=/g) ?? []).length, 1,
    "um unico ponto de verdade para o estado, dentro do map");
});

test("H1-3. o estado nao depende so' de cor", async () => {
  const codigo = await ler(PAGE);
  const bloco = codigo.split("seletor de canal")[1].split("</section>")[0];
  const temCor = /bg-violet-600/.test(bloco);
  assert.ok(temCor, "a cor continua existindo — ela e' reforco, nao substituto");
  assert.ok(bloco.includes("aria-pressed"),
    "mas precisa haver estado programatico ALEM da cor");
});

test("H1-4. o nome acessivel e o alvo de 44px do seletor sao preservados", async () => {
  const codigo = await ler(PAGE);
  const bloco = codigo.split("seletor de canal")[1].split("</section>")[0];
  assert.ok(bloco.includes("aria-labelledby={canalId}"),
    "o grupo continua rotulado");
  assert.ok(bloco.includes("min-h-[44px]") && bloco.includes("min-w-[44px]"),
    "o alvo de toque nao pode encolher");
  assert.ok(bloco.includes("focus-visible:ring-2"), "o foco continua visivel");
});

test("H1-5. a frase de escopo do Mercado Livre NAO aparece nos outros canais", async () => {
  const codigo = await ler(PAGE);
  const i = codigo.indexOf("Fora do escopo por não terem catálogo próprio");
  assert.ok(i > 0, "a frase existe");
  const antes = codigo.slice(Math.max(0, i - 400), i);
  assert.ok(/marketplace === "ml" &&/.test(antes),
    "a clausula precisa estar condicionada ao ML: `out_of_scope_brands` volta "
    + "igual nos tres canais e cita o catalogo do Mercado Livre");
});

test("H1-6. a frase de escopo contradiz o dado fora do ML, e por isso e' escondida", async () => {
  // Apice esta em `out_of_scope_brands` (sem catalogo no ML) E TEM 225 anuncios
  // na Shopee, onde e' uma das quatro contas. Mostrar "fora do escopo" ali
  // seria afirmar o contrario do que a propria tabela mostra.
  const codigo = await ler(PAGE);
  const bloco = codigo.split("Marcas monitoradas:")[1].split("</p>")[0];
  assert.ok(bloco.includes('marketplace === "ml"'),
    "a condicao precisa estar no mesmo paragrafo da lista de marcas");
});

test("H1-7. as demais frases de cobertura continuam em todos os canais", async () => {
  const codigo = await ler(PAGE);
  const bloco = codigo.split("Marcas monitoradas:")[1].split("</p>")[0];
  assert.ok(!bloco.includes('marketplace === "ml" && meta.no_reference_brands'),
    "'sem tabela de referencia' vale para todos os canais e nao pode ser "
    + "condicionada ao ML");
  assert.ok(codigo.includes("comparison_basis_text"),
    "a frase das duas datas continua vindo do servidor, por canal");
});


// ---------------------------------------------------------------------------
// Gate PMA-2C4D3-H2 — cobertura observada por canal
// ---------------------------------------------------------------------------

test("H2-1. o filtro de marca sai de observed_brands, nao de monitored_brands", async () => {
  const codigo = await ler(PAGE);
  assert.ok(codigo.includes("marcasDoFiltro.map"),
    "o select precisa consumir a cobertura");
  assert.ok(/const marcasDaCobertura = meta\?\.observed_brands \?\? meta\?\.monitored_brands/
    .test(codigo), "a cobertura vem primeiro; o campo antigo e' so' a queda");
  // Gate PMA-2C4D3-H3 — `marcasDoFiltro` passou a ser a cobertura MAIS a marca
  // escolhida, para o controle nao se esvaziar quando a carga falha. A origem
  // continua sendo a cobertura, e e' isso que este teste trava.
  assert.ok(/const marcasDoFiltro =\s*\n?\s*brand && !marcasDaCobertura\.includes\(brand\)/
    .test(codigo), "o filtro deriva da cobertura, nunca de lista fixa");
  const bloco = codigo.split("htmlFor={marcaId}")[1].split("</select>")[0];
  assert.ok(!bloco.includes("monitored_brands"),
    "o select nao pode voltar a ler o escopo de monitoramento direto");
});

test("H2-2. a cobertura NAO e' derivada das linhas da pagina", async () => {
  const codigo = await ler(PAGE);
  const bloco = codigo.split("const marcasDoFiltro")[1].split(";")[0];
  for (const proibido of ["rows", "linhas", "dados.rows", "map((r)"]) {
    assert.ok(!bloco.includes(proibido),
      `a cobertura nao pode sair das linhas (${proibido}) — a resposta e' paginada`);
  }
});

test("H2-3. nenhuma marca e' fixada no codigo do frontend", async () => {
  const codigo = await ler(PAGE);
  // O texto de ausencia usa a lista da API. Kokeshi nao pode virar constante.
  const bloco = codigo.split("Sem observação nesta fotografia")[1].split("</p>")[0];
  assert.ok(bloco.includes("naoObservadas.map"),
    "a frase precisa enumerar o que a API devolveu");
  for (const marca of ["kokeshi", "Kokeshi"]) {
    assert.ok(!bloco.includes(marca), "marca fixa no codigo vira mentira amanha");
  }
});

test("H2-4. a ausencia e' apresentada como ausencia, nunca como zero", async () => {
  const codigo = await ler(PAGE);
  const i = codigo.indexOf("Sem observação nesta fotografia");
  assert.ok(i > 0, "a frase existe");
  const frase = codigo.slice(i, i + 320);
  assert.ok(/não significa zero anúncios/.test(frase),
    "precisa dizer explicitamente que nao e' zero");
  assert.ok(/não devolveu essas marcas/.test(frase),
    "precisa atribuir a ausencia a FONTE, nao ao desempenho da marca");
});

test("H2-5. marca que saiu da cobertura e' limpa", async () => {
  const codigo = await ler(PAGE);
  assert.ok(/if \(!meta\.observed_brands\.includes\(brand\)\)/.test(codigo),
    "trocar data pode invalidar a marca escolhida; manter exibiria 0 pela "
    + "razao errada");
  const efeito = codigo.split("if (!meta.observed_brands.includes(brand))")[1]
    .split("}")[0];
  assert.ok(efeito.includes("setBrand(\"\")") && efeito.includes("setOffset(0)"),
    "limpar a marca tambem reinicia a paginacao");
});

test("H2-6. trocar de canal continua limpando marca e data", async () => {
  const codigo = await ler(PAGE);
  const bloco = codigo.split("const trocaCanal")[1].split("},")[0];
  for (const limpeza of ['setBrand("")', 'setObservedDate("")', "setOffset(0)"]) {
    assert.ok(bloco.includes(limpeza), `a troca de canal precisa fazer ${limpeza}`);
  }
});

test("H2-7. o frontend tolera payload ANTIGO, sem os campos novos", async () => {
  const contrato = await ler(CONTRATO);
  // Ancorado no INICIO da linha: sem isso, `observed_brands?:` casaria dentro
  // de `monitored_unobserved_brands?:` e o teste passaria mesmo com o campo
  // declarado obrigatorio — foi o que a mutacao M8 revelou.
  assert.ok(/^\s*observed_brands\?: string\[\];$/m.test(contrato),
    "opcional no tipo: o backend e' publicado antes e o frontend depois");
  assert.ok(/^\s*monitored_unobserved_brands\?: string\[\];$/m.test(contrato));
  const codigo = await ler(PAGE);
  assert.ok(codigo.includes("meta?.observed_brands ?? meta?.monitored_brands ?? []"),
    "sem a queda, o filtro ficaria VAZIO na janela entre os dois deploys");
  assert.ok(codigo.includes("meta?.monitored_unobserved_brands ?? []"),
    "a frase de ausencia nao pode quebrar quando o campo nao existe");
});

test("H2-8. marcas fora do escopo comercial seguem selecionaveis se observadas", async () => {
  // Gocase e Denavita TEM linhas no TikTok. Some-las do filtro deixaria linhas
  // visiveis sem dimensao de filtragem — o que o gate R5 ja proibia.
  const codigo = await ler(PAGE);
  const bloco = codigo.split("const marcasDaCobertura")[1].split("\n\n")[0];
  for (const proibido of ["business_scope", "in_scope", "out_of_business_scope",
                          "escopo ===", "filter("]) {
    assert.ok(!bloco.includes(proibido),
      `a cobertura nao filtra por escopo comercial (${proibido})`);
  }
  assert.ok(codigo.includes("escopoId"), "o escopo continua sendo filtro proprio");
});

test("H2-9. toda marca observavel tem rotulo proprio", () => {
  // Gocase e Denavita entraram no filtro do TikTok ao virarem selecionaveis.
  // Sem rotulo, o `?? brand` as exibia em minusculas ao lado das capitalizadas,
  // e o fallback escondia a falta em vez de denuncia-la.
  for (const marca of ["apice", "barbours", "kokeshi", "lescent", "rituaria",
                       "gocase", "denavita"]) {
    assert.notEqual(brandLabel(marca), marca,
      `a marca ${marca} aparece no filtro e precisa de rotulo proprio`);
  }
  assert.equal(brandLabel("gocase"), "Gocase");
  assert.equal(brandLabel("denavita"), "Denavita");
});


// ---------------------------------------------------------------------------
// Gate PMA-2C4D3-H3 — falha de carga nao pode virar tela muda nem detalhe
// tecnico, e nao pode descartar o filtro que a pessoa escolheu.
// ---------------------------------------------------------------------------

test("H3-1. o alerta de falha nao expoe status HTTP nem jargao", () => {
  for (const status of [400, 404, 422, 500, 502, 503, null]) {
    const texto = mensagemDeFalha(status);
    assert.ok(texto.length > 0, `status ${status} sem mensagem`);
    for (const vazamento of ["422", "400", "404", "500", "502", "503",
                             "HTTP", "status", "API", "endpoint", "payload",
                             "Traceback", "null", "undefined"]) {
      assert.ok(!texto.includes(vazamento),
        `a mensagem de ${status} expoe "${vazamento}": ${texto}`);
    }
  }
});

test("H3-2. cada familia de falha tem redacao propria e acionavel", () => {
  assert.notEqual(mensagemDeFalha(422), mensagemDeFalha(500));
  assert.notEqual(mensagemDeFalha(null), mensagemDeFalha(500));
  assert.match(mensagemDeFalha(422), /filtros/i);
  assert.match(mensagemDeFalha(null), /conex/i);
  assert.match(mensagemDeFalha(503), /indispon/i);
});

test("H3-3. a pagina renderiza a mensagem amigavel, nao o erro cru", async () => {
  const codigo = await lerCodigo(PAGE);
  const alerta = codigo.split('role="alert"')[1].split("</div>")[0];
  assert.ok(alerta.includes("mensagemDeFalha(erroStatus)"),
    "o alerta precisa usar a traducao amigavel");
  assert.ok(!/\{erro\}/.test(alerta),
    "o alerta nao pode imprimir a mensagem de diagnostico");
});

test("H3-4. falha de carga NAO limpa a marca escolhida", async () => {
  const codigo = await lerCodigo(PAGE);
  const captura = codigo.split(".catch((err: unknown)")[1].split("});")[0];
  assert.ok(!captura.includes("setBrand"),
    "erro de rede/API nao prova que a marca deixou de ser observavel");
  assert.ok(!captura.includes("setObservedDate"),
    "erro tambem nao pode descartar a data escolhida");
});

test("H3-5. a marca escolhida continua listada quando a cobertura nao veio", async () => {
  const codigo = await lerCodigo(PAGE);
  assert.ok(codigo.includes("[...marcasDaCobertura, brand]"),
    "sem isto o <select> cai sozinho para 'Todas' e parece descartar o filtro");
});

test("H3-6. a limpeza automatica so' ocorre com COBERTURA nova", async () => {
  const codigo = await lerCodigo(PAGE);
  const efeito = codigo.split("if (!brand || !meta?.observed_brands) return;")[1];
  assert.ok(efeito !== undefined,
    "a guarda que exige cobertura recebida precisa continuar existindo");
  assert.ok(efeito.split("}, [")[0].includes("setBrand(\"\")"),
    "a limpeza continua acontecendo quando a cobertura prova a saida");
});

test("H3-7. o fallback de compatibilidade segue intacto", async () => {
  const codigo = await lerCodigo(PAGE);
  assert.ok(codigo.includes("meta?.observed_brands ?? meta?.monitored_brands ?? []"),
    "a janela entre os dois deploys continua coberta");
});

test("H3-8. exatamente um canal pressionado continua garantido", async () => {
  const codigo = await lerCodigo(PAGE);
  assert.ok(codigo.includes("aria-pressed={ativo}"),
    "o estado dos tres botoes continua explicito");
  assert.ok(!codigo.includes("aria-pressed={true}")
    && !codigo.includes("aria-pressed={false}"),
    "nenhum botao pode ter estado fixo");
});
