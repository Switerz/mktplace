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
  canalLabel,
  coberturaPorConta,
  filtrosDoCanal,
  fmtPrecoObservado,
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
