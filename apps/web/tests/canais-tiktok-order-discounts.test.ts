// Testes do bloco "Descontos e subsidios do pedido — TikTok Shop" da aba
// Canais (contrato §28, UE8-I3). Cobrem: normalizacao snake_case -> camelCase,
// sinais monetarios preservados, taxa negativa da marca e positiva da
// plataforma, ausencia de qualquer total, estados completos, guarda de
// frescor de requisicao, dialogo/foco, acessibilidade, textos obrigatorios e
// ausencia de calculo de margem ou retorno.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  buildCancelledDrilldown,
  buildDiscountTable,
  CANCELLED_COMPONENT_LABELS,
  CANCELLED_COMPONENT_ORDER,
  CANCELLED_COPY,
  CANCELLED_SECTION_TITLE,
  COVERAGE_LIMIT_COPY,
  coverageNote,
  deriveDiscountBlockView,
  DISCOUNT_COMPONENT_LABELS,
  DISCOUNT_COMPONENT_ORDER,
  DISCOUNTS_BLOCK_TITLE,
  describeFreshness,
  formatCount,
  formatSignedBrl,
  formatSignedPct,
  formatWindow,
  MANDATORY_COPY,
  RECENT_LOAD_COPY,
  RETROACTIVE_COPY,
  SELLER_COPY,
  STALE_LOAD_COPY,
  SUBSIDY_COPY,
} from "../src/lib/canais-tiktok-order-discounts.ts";
import { resolveBlockPhase } from "../src/lib/canais-affiliate-costs.ts";
import type {
  TikTokOrderDiscountRow,
  TikTokOrderDiscountsBlock,
} from "../src/lib/api-client.ts";

/**
 * Le um arquivo do repo SEM comentarios.
 *
 * Varrer o texto cru acusaria as proprias proibicoes escritas em comentario
 * ("nao soma os dois", "nenhum <tfoot> de total") como se fossem codigo. O que
 * precisa ser verificado e' o codigo executavel.
 */
function codeOf(relPath: string): string {
  return readFileSync(new URL(relPath, import.meta.url), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "");
}

const PANEL = "../src/components/TiktokOrderDiscountsPanel.tsx";
const LIB = "../src/lib/canais-tiktok-order-discounts.ts";
const CLIENT = "../src/lib/api-client.ts";
const PAGE = "../app/canais/page.tsx";

function row(over: Partial<TikTokOrderDiscountRow> = {}): TikTokOrderDiscountRow {
  return {
    brand: "apice",
    commercial_orders: 20992,
    official_gmv: 1478967.6,
    full_product_value: 2579318.67,
    seller_discount_signed: -1007437.24,
    platform_subsidy_amount: 141271.95,
    cancelled_orders: 4221,
    cancelled_seller_discount_signed: -229131.29,
    cancelled_platform_subsidy_amount: 31432.25,
    seller_discount_rate: -39.05826960109586,
    platform_subsidy_rate: 5.477103377846678,
    ...over,
  };
}

function block(
  over: Partial<TikTokOrderDiscountsBlock> = {},
): TikTokOrderDiscountsBlock {
  return {
    availability_status: "available",
    period_status: "complete_month",
    coverage_status: "complete",
    coverage_basis: "observed_grid",
    coverage_expected_keys: 155,
    coverage_present_keys: 155,
    coverage_missing_keys: 0,
    freshness_status: "recent_load",
    rows: [row()],
    date_from: "2026-08-01",
    date_to: "2026-08-31",
    date_count: 31,
    discounts_refreshed_at: "2026-09-08T15:35:17.868612+00:00",
    source_max_date: "2026-09-07",
    source_max_updated_at: "2026-09-08T12:25:52",
    seller_note: "nota da marca",
    subsidy_note: "nota do subsidio",
    limitation_note: "nota de limitacao",
    warnings: [],
    ...over,
  };
}

// ===========================================================================
// 1-6 — normalizacao snake_case -> camelCase no api-client
// ===========================================================================

test("01. o api-client normaliza tiktok_order_discounts para camelCase", () => {
  const src = codeOf(CLIENT);
  assert.match(src, /tiktokOrderDiscounts:\s*raw\.tiktok_order_discounts \?\? null/);
});

test("02. bloco ausente vira null, nunca um bloco vazio fabricado", () => {
  const src = codeOf(CLIENT);
  assert.match(src, /raw\.tiktok_order_discounts \?\? null/);
  assert.doesNotMatch(src, /tiktok_order_discounts \?\? \{/);
});

test("03. o modo demonstracao nao inventa descontos", () => {
  const src = codeOf(CLIENT);
  assert.match(src, /tiktokOrderDiscounts:\s*null/);
});

test("04. o tipo da linha tem exatamente os onze campos do contrato", () => {
  const r = row();
  assert.deepEqual(Object.keys(r).sort(), [
    "brand",
    "cancelled_orders",
    "cancelled_platform_subsidy_amount",
    "cancelled_seller_discount_signed",
    "commercial_orders",
    "full_product_value",
    "official_gmv",
    "platform_subsidy_amount",
    "platform_subsidy_rate",
    "seller_discount_rate",
    "seller_discount_signed",
  ]);
});

test("05. o tipo do bloco nao tem nenhum campo de total", () => {
  const chaves = [...Object.keys(block()), ...Object.keys(row())];
  for (const proibido of ["total_discount", "discount_total", "net_discount",
                          "margin", "margem", "roi", "roas", "net_revenue"]) {
    assert.ok(!chaves.some((c) => c.includes(proibido)), proibido);
  }
});

test("06. o api-client declara os tres valores de frescor", () => {
  const src = codeOf(CLIENT);
  assert.match(src, /\| "recent_load"/);
  assert.match(src, /\| "stale_load"/);
  assert.match(src, /\| "unknown"/);
  // `current_snapshot` foi descartado antes de virar contrato publico.
  assert.doesNotMatch(src, /current_snapshot/);
});

test("06b. o api-client declara a base da grade de cobertura", () => {
  const src = codeOf(CLIENT);
  assert.match(src, /DiscountCoverageBasis = "observed_grid"/);
  assert.match(src, /coverage_expected_keys: number/);
  assert.match(src, /coverage_present_keys: number/);
  assert.match(src, /coverage_missing_keys: number/);
});

// ===========================================================================
// 7-14 — sinais monetarios e taxas
// ===========================================================================

test("07. valor negativo sai com o sinal preservado", () => {
  const c = formatSignedBrl(-1007437.24);
  assert.ok(c.text.includes("-"));
  assert.equal(c.tone, "value");
});

test("08. valor positivo nao ganha sinal negativo", () => {
  assert.ok(!formatSignedBrl(141271.95).text.includes("-"));
});

test("09. o valor sai integral, com centavos, nunca abreviado", () => {
  const t = formatSignedBrl(1478967.6).text;
  assert.ok(t.includes(",60"));
  assert.ok(!/[KM]\b/.test(t));
});

test("10. null vira travessao e zero vira R$ 0,00", () => {
  assert.equal(formatSignedBrl(null).text, "—");
  assert.equal(formatSignedBrl(null).tone, "muted");
  const zero = formatSignedBrl(0);
  assert.ok(zero.text.includes("0,00"));
  assert.equal(zero.tone, "value");
  assert.notEqual(formatSignedBrl(null).text, zero.text);
});

test("11. a taxa da marca sai NEGATIVA", () => {
  const c = formatSignedPct(-39.058);
  assert.ok(c.text.startsWith("-"));
  assert.ok(c.text.endsWith("%"));
});

test("12. a taxa da plataforma sai POSITIVA e com sinal explicito", () => {
  const c = formatSignedPct(5.477);
  assert.ok(c.text.startsWith("+"));
  assert.ok(c.text.endsWith("%"));
});

test("13. taxa null vira travessao, taxa zero vira 0,00%", () => {
  assert.equal(formatSignedPct(null).text, "—");
  assert.equal(formatSignedPct(0).text, "0,00%");
});

test("14. nem o modulo nem o painel aplicam Math.abs ou invertem sinal", () => {
  for (const arquivo of [LIB, PANEL]) {
    const src = codeOf(arquivo);
    assert.ok(!src.includes("Math.abs"), arquivo);
    assert.ok(!/-\s*(row|linha)\./.test(src), arquivo);
  }
});

// ===========================================================================
// 15-20 — ausencia de total e de calculo derivado
// ===========================================================================

test("15. buildDiscountTable nao produz nenhum total", () => {
  const [linha] = buildDiscountTable([row()]);
  assert.equal(linha.cells.length, DISCOUNT_COMPONENT_ORDER.length);
  const rotulos = linha.cells.map((c) => c.label.toLowerCase());
  assert.ok(!rotulos.some((r) => r.includes("total")));
});

test("16. os dois descontos nunca sao somados", () => {
  const r = row({ seller_discount_signed: -3000, platform_subsidy_amount: 500 });
  const [linha] = buildDiscountTable([r]);
  const textos = linha.cells.map((c) => c.cell.text).join(" ");
  for (const proibido of ["2.500,00", "3.500,00"]) {
    assert.ok(!textos.includes(proibido), proibido);
  }
});

test("17. o painel nao tem tfoot de total", () => {
  const src = codeOf(PANEL);
  assert.ok(!src.includes("<tfoot"));
});

test("18. nem o modulo nem o painel calculam margem, lucro ou retorno", () => {
  for (const arquivo of [LIB, PANEL]) {
    const src = codeOf(arquivo).toLowerCase();
    for (const proibido of ["margem", "margin", "lucro", "profit",
                            "receita liquida", "roi", "roas", "recuperado"]) {
      assert.ok(!src.includes(proibido), `${arquivo}: ${proibido}`);
    }
  }
});

test("19. o painel nao recalcula nenhuma taxa — usa a do backend", () => {
  const src = codeOf(PANEL);
  assert.ok(!src.includes("* 100"));
  assert.ok(!src.includes("/ full_product_value"));
});

test("20. as taxas vem prontas do payload, sem derivacao no cliente", () => {
  const [linha] = buildDiscountTable([
    row({ seller_discount_rate: -12.5, platform_subsidy_rate: 3.25 }),
  ]);
  assert.equal(linha.sellerRate.text, "-12,50%");
  assert.equal(linha.platformRate.text, "+3,25%");
});

// ===========================================================================
// 21-30 — estados
// ===========================================================================

test("21. estado available com linhas", () => {
  const v = deriveDiscountBlockView(block());
  assert.equal(v.hasRows, true);
  assert.equal(v.emptyMessage, null);
});

test("22. estado unavailable_no_source", () => {
  const v = deriveDiscountBlockView(
    block({ availability_status: "unavailable_no_source", rows: [] }));
  assert.match(v.emptyMessage ?? "", /TikTok Shop/);
  assert.equal(v.hasRows, false);
});

test("23. estado no_eligible_brand", () => {
  const v = deriveDiscountBlockView(
    block({ availability_status: "no_eligible_brand", rows: [] }));
  assert.match(v.emptyMessage ?? "", /marca elegível/);
});

test("24. estado error usa a nota FIXA do backend", () => {
  const v = deriveDiscountBlockView(block({
    availability_status: "error", rows: [], limitation_note: "nota fixa",
  }));
  assert.equal(v.emptyMessage, "nota fixa");
  assert.equal(v.emptyTone, "warning");
});

test("25. estado empty com available", () => {
  const v = deriveDiscountBlockView(block({ rows: [] }));
  assert.match(v.emptyMessage ?? "", /Sem pedidos/);
  assert.equal(v.emptyTone, "muted");
});

test("26. periodo parcial NAO suprime as linhas — o grao aqui e diario", () => {
  const v = deriveDiscountBlockView(block({ period_status: "partial_month" }));
  assert.equal(v.hasRows, true);
  assert.equal(v.emptyMessage, null);
});

test("27. periodo desalinhado tambem nao suprime", () => {
  const v = deriveDiscountBlockView(block({ period_status: "not_month_aligned" }));
  assert.equal(v.emptyMessage, null);
});

test("28. cobertura incompleta traz os NUMEROS da grade observada", () => {
  const b = block({
    coverage_status: "incomplete_brand_coverage",
    coverage_expected_keys: 2280, coverage_present_keys: 2081,
    coverage_missing_keys: 199,
  });
  assert.equal(deriveDiscountBlockView(b).coverageIsIncomplete, true);
  const nota = coverageNote(b) ?? "";
  assert.match(nota, /199 de 2\.280 chaves/);
  assert.match(nota, /grade observada/);
  assert.match(nota, /indistinguíveis/);
  assert.match(nota, /NÃO foram preenchidas com zero/);
});

test("29. cobertura completa nao gera nota de incompletude", () => {
  assert.equal(coverageNote(block({ coverage_status: "complete" })), null);
  assert.equal(coverageNote(block({ coverage_status: "unknown" })), null);
});

test("29b. complete vem acompanhado do LIMITE, nunca sozinho", () => {
  // Sem esta linha o leitor concluiria "ingestao comprovada".
  assert.match(COVERAGE_LIMIT_COPY, /grade observada/);
  assert.match(COVERAGE_LIMIT_COPY, /não é prova de ingestão completa/);
  const src = codeOf(PANEL);
  assert.ok(src.includes("{COVERAGE_LIMIT_COPY}"));
});

test("29c. a base da grade e observed_grid, nunca dias de calendario", () => {
  assert.equal(block().coverage_basis, "observed_grid");
  const src = codeOf(LIB);
  assert.doesNotMatch(src, /dias de calend[áa]rio/i);
});

test("30. os warnings do backend chegam na ordem em que vieram", () => {
  const avisos = ["primeiro", "segundo"];
  const v = deriveDiscountBlockView(block({ warnings: avisos }));
  assert.deepEqual(v.warnings, avisos);
});

// ===========================================================================
// 31-36 — frescor, janela e timestamps
// ===========================================================================

test("31. recent_load nao afirma estabilidade do DADO", () => {
  // Procura a AFIRMACAO, nao a palavra: a frase correta e' justamente
  // "nao significa dado estavel", e proibir o termo reprovaria o texto que
  // protege o leitor.
  const texto = describeFreshness(block()) ?? "";
  const t = texto.toLowerCase();
  for (const afirmacao of ["é estável", "esta estável", "está estável",
                           "dado atual", "já fechado", "maduro",
                           "consolidado", "definitivo"]) {
    assert.ok(!t.includes(afirmacao), afirmacao);
  }
  assert.equal(RECENT_LOAD_COPY,
    "Carga recente — isso não significa dado estável; a fonte pode ser revisada.");
  assert.ok(texto.includes(RECENT_LOAD_COPY));
});

test("32. stale_load diz que a carga e antiga", () => {
  const texto = describeFreshness(block({ freshness_status: "stale_load" })) ?? "";
  assert.match(texto, /antiga/);
  assert.ok(texto.includes(STALE_LOAD_COPY));
});

test("32b. a frase fala da CARGA, e o maximo e da SELECAO", () => {
  const texto = describeFreshness(block()) ?? "";
  assert.match(texto, /Carga registrada em/);
  // `source_max_date` e' do escopo filtrado — a frase nao pode sugerir que a
  // FONTE inteira chegou ate ali.
  assert.match(texto, /dados desta seleção até/);
  assert.doesNotMatch(texto, /fonte carregada até/);
});

test("33. frescor unknown nao produz frase", () => {
  assert.equal(describeFreshness(block({ freshness_status: "unknown" })), null);
});

test("34. timestamp COM offset e convertido e rotulado BRT", () => {
  const texto = describeFreshness(block()) ?? "";
  assert.match(texto, /BRT/);
});

test("35. timestamp SEM offset nao recebe rotulo BRT", () => {
  // `source_max_updated_at` e' naive. Se um dia for exibido, nao pode ganhar
  // rotulo de fuso que a fonte nao declarou.
  const src = codeOf(LIB);
  assert.ok(!/source_max_updated_at[\s\S]{0,120}BRT/.test(src));
});

test("36. a janela efetiva e formatada como intervalo pt-BR", () => {
  assert.equal(formatWindow("2026-08-01", "2026-08-31"),
               "01/08/2026 a 31/08/2026");
  assert.equal(formatWindow(null, "2026-08-31"), null);
  assert.equal(formatWindow("2026-08-01", null), null);
});

test("37. date_count e apresentado como dias COM DADO", () => {
  const v = deriveDiscountBlockView(block({ date_count: 28 }));
  assert.match(v.dayCountLabel ?? "", /28 dia\(s\) com dado/);
  assert.equal(deriveDiscountBlockView(block({ date_count: 0 })).dayCountLabel,
               null);
});

// ===========================================================================
// 38-43 — cancelados separados
// ===========================================================================

test("38. cancelados nao entram na tabela principal", () => {
  const [linha] = buildDiscountTable([row()]);
  const chaves = linha.cells.map((c) => c.key as string);
  assert.ok(!chaves.some((c) => c.startsWith("cancelled")));
});

test("39. cancelados tem seu proprio construtor de drill-down", () => {
  const [linha] = buildCancelledDrilldown([row()]);
  assert.equal(linha.cancelledOrders, formatCount(4221));
  assert.equal(linha.components.length, CANCELLED_COMPONENT_ORDER.length);
});

test("40. o desconto cancelado preserva o sinal negativo", () => {
  const [linha] = buildCancelledDrilldown([row()]);
  const sd = linha.components.find((c) =>
    c.label === CANCELLED_COMPONENT_LABELS.cancelled_seller_discount_signed);
  assert.ok(sd!.cell.text.includes("-"));
});

test("41. o subsidio cancelado permanece positivo", () => {
  const [linha] = buildCancelledDrilldown([row()]);
  const ps = linha.components.find((c) =>
    c.label === CANCELLED_COMPONENT_LABELS.cancelled_platform_subsidy_amount);
  assert.ok(!ps!.cell.text.includes("-"));
});

test("42. os rotulos de cancelado dizem 'cancelados' no proprio texto", () => {
  for (const rotulo of Object.values(CANCELLED_COMPONENT_LABELS)) {
    assert.match(rotulo, /cancelados/);
  }
});

test("43. a secao de cancelados e visualmente separada no painel", () => {
  const src = codeOf(PANEL);
  assert.ok(src.includes(CANCELLED_SECTION_TITLE.slice(0, 20))
            || src.includes("CANCELLED_SECTION_TITLE"));
  assert.match(src, /border-t-2/);
});

// ===========================================================================
// 44-49 — textos obrigatorios
// ===========================================================================

test("44. a copy obrigatoria tem os quatro textos do contrato", () => {
  assert.equal(MANDATORY_COPY.length, 4);
  assert.ok(MANDATORY_COPY.includes(SELLER_COPY));
  assert.ok(MANDATORY_COPY.includes(SUBSIDY_COPY));
  assert.ok(MANDATORY_COPY.includes(RETROACTIVE_COPY));
  assert.ok(MANDATORY_COPY.includes(CANCELLED_COPY));
});

test("45. a copy da marca diz que reduz a receita da marca", () => {
  assert.match(SELLER_COPY, /reduz a receita/);
  assert.match(SELLER_COPY, /marca/);
});

test("46. a copy do subsidio proibe a soma explicitamente", () => {
  assert.match(SUBSIDY_COPY, /TikTok/);
  assert.match(SUBSIDY_COPY, /não deve ser somado/);
});

test("47. a copy declara a revisao retroativa da fonte", () => {
  assert.match(RETROACTIVE_COPY, /revisada retroativamente/);
});

test("48. a copy separa os cancelados das vendas comerciais", () => {
  assert.match(CANCELLED_COPY, /não integram as vendas comerciais/);
});

test("49. as quatro frases estao no painel renderizado", () => {
  const src = codeOf(PANEL);
  for (const nome of ["SELLER_COPY", "SUBSIDY_COPY", "RETROACTIVE_COPY",
                      "CANCELLED_COPY"]) {
    assert.ok(src.includes(`{${nome}}`), nome);
  }
});

// ===========================================================================
// 50-56 — guarda de frescor, dialogo e acessibilidade
// ===========================================================================

test("50. a pagina aplica a guarda dataIsFresh ao bloco novo", () => {
  const src = codeOf(PAGE);
  assert.match(src,
    /displayTiktokOrderDiscounts = dataIsFresh \? tiktokOrderDiscounts : null/);
});

test("51. o painel recebe o valor PROTEGIDO, nunca o estado bruto", () => {
  const src = codeOf(PAGE);
  assert.match(src, /block=\{displayTiktokOrderDiscounts\}/);
  assert.ok(!/block=\{tiktokOrderDiscounts\}/.test(src));
});

test("52. troca de filtro remonta o painel e fecha o dialogo", () => {
  const src = codeOf(PAGE);
  const trecho = src.slice(src.indexOf("TiktokOrderDiscountsPanel"));
  assert.match(trecho, /key=\{requestKey\}/);
});

test("53. as quatro fases sao tratadas explicitamente", () => {
  const src = codeOf(PANEL);
  for (const fase of ["loading", "neutral", "unavailable"]) {
    assert.ok(src.includes(`"${fase}"`), fase);
  }
  assert.match(src, /if \(!block\)/);
});

test("54. estado terminal nao pulsa; so loading/neutral tem animate-pulse", () => {
  const src = codeOf(PANEL);
  const antes = src.indexOf("animate-pulse");
  const terminal = src.indexOf('phase === "unavailable"');
  assert.ok(antes > 0 && terminal > antes,
            "o skeleton deve vir antes do estado terminal");
  assert.equal(src.split("animate-pulse").length - 1, 1);
});

test("55. o skeleton tem aria-busy E heading acessivel", () => {
  const src = codeOf(PANEL);
  const bloco = src.slice(src.indexOf("aria-busy"),
                          src.indexOf("animate-pulse"));
  assert.match(bloco, /<h2/);
  assert.match(bloco, /DISCOUNTS_BLOCK_TITLE/);
});

test("56. o dialogo reusa KpiDrilldownDialog e nao dispara fetch ao abrir", () => {
  const src = codeOf(PANEL);
  assert.match(src, /<KpiDrilldownDialog/);
  assert.ok(!src.includes("fetch("));
  assert.ok(!src.includes("useEffect"));
});

test("57. a tabela rola dentro do proprio container", () => {
  const src = codeOf(PANEL);
  assert.match(src, /overflow-x-auto/);
});

test("58. o botao de drill-down tem alvo de 44px", () => {
  const src = codeOf(PANEL);
  const botao = src.slice(src.indexOf("<button"), src.indexOf("</button>"));
  assert.match(botao, /min-h-\[44px\]/);
  assert.match(botao, /min-w-\[44px\]/);
});

test("59. a tabela tem caption acessivel que nega a soma", () => {
  const src = codeOf(PANEL);
  assert.match(src, /<caption className="sr-only">/);
  const cap = src.slice(src.indexOf("<caption"), src.indexOf("</caption>"));
  assert.match(cap, /separadamente e não somados/);
});

test("60. o titulo do bloco nomeia os dois conceitos", () => {
  assert.match(DISCOUNTS_BLOCK_TITLE, /Descontos e subsídios/);
  assert.match(DISCOUNTS_BLOCK_TITLE, /TikTok Shop/);
});

// ===========================================================================
// 61-64 — rotulos e ordem
// ===========================================================================

test("61. os rotulos nomeiam o FINANCIADOR de cada componente", () => {
  assert.match(DISCOUNT_COMPONENT_LABELS.seller_discount_signed,
               /financiado pela marca/);
  assert.match(DISCOUNT_COMPONENT_LABELS.platform_subsidy_amount,
               /financiado pelo TikTok/);
});

test("62. a ordem dos componentes e FIXA, nao ranking", () => {
  assert.deepEqual(DISCOUNT_COMPONENT_ORDER, [
    "full_product_value", "official_gmv",
    "seller_discount_signed", "platform_subsidy_amount",
  ]);
});

test("63. resolveBlockPhase e compartilhado com o bloco de afiliados", () => {
  assert.equal(resolveBlockPhase({
    loading: true, error: false, requestKey: "a", resolvedKey: null,
  }), "loading");
  assert.equal(resolveBlockPhase({
    loading: false, error: true, requestKey: "a", resolvedKey: "a",
  }), "unavailable");
  assert.equal(resolveBlockPhase({
    loading: false, error: false, requestKey: "a", resolvedKey: "b",
  }), "neutral");
  assert.equal(resolveBlockPhase({
    loading: false, error: false, requestKey: "a", resolvedKey: "a",
  }), "fresh");
});

test("64. formatCount usa separador pt-BR", () => {
  assert.equal(formatCount(20992), "20.992");
  assert.equal(formatCount(0), "0");
});
