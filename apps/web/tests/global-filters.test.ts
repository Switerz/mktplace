// Testes da lib de filtros globais (canal, marca, periodo, comparacao).
// Roda via `node --test` com type-stripping nativo do Node.
import { test } from "node:test";
import assert from "node:assert/strict";
import { presetRange, detectPreset, toISODate, previousEquivalentRange } from "../src/lib/filters/presets.ts";
import {
  parseFiltersFromSearchParams, filtersToSearchParams, validateDateRange,
  hasExplicitFilterParams, computeDefaultFilters, resolveFilters,
} from "../src/lib/filters/url-state.ts";
import { toggleMultiSelect } from "../src/lib/filters/multi-select.ts";
import { isFilterAwarePath, appendQuery } from "../src/lib/filters/nav-links.ts";
import { mockLimitationNote } from "../src/lib/filters/format.ts";

// Data fixa para tornar os testes de preset deterministas: 2026-06-15 (segunda).
const TODAY = new Date(2026, 5, 15);

test("presetRange: hoje retorna um unico dia", () => {
  const r = presetRange("hoje", TODAY);
  assert.equal(r.dateFrom, "2026-06-15");
  assert.equal(r.dateTo, "2026-06-15");
});

test("presetRange: 7d/30d/90d incluem hoje e contam para tras", () => {
  assert.deepEqual(presetRange("7d", TODAY), { dateFrom: "2026-06-09", dateTo: "2026-06-15" });
  assert.deepEqual(presetRange("30d", TODAY), { dateFrom: "2026-05-17", dateTo: "2026-06-15" });
  assert.deepEqual(presetRange("90d", TODAY), { dateFrom: "2026-03-18", dateTo: "2026-06-15" });
});

test("presetRange: mes_atual vai do dia 1 ate hoje", () => {
  assert.deepEqual(presetRange("mes_atual", TODAY), { dateFrom: "2026-06-01", dateTo: "2026-06-15" });
});

test("presetRange: mes_anterior cobre o mes calendario completo anterior", () => {
  assert.deepEqual(presetRange("mes_anterior", TODAY), { dateFrom: "2026-05-01", dateTo: "2026-05-31" });
});

test("presetRange: mes_anterior funciona corretamente em janeiro (vira dezembro do ano anterior)", () => {
  const jan = new Date(2026, 0, 10);
  assert.deepEqual(presetRange("mes_anterior", jan), { dateFrom: "2025-12-01", dateTo: "2025-12-31" });
});

test("toISODate nunca usa UTC (evita off-by-one em fuso negativo)", () => {
  // Meia-noite local de 15/06 nao pode virar 14/06 por causa de toISOString().
  assert.equal(toISODate(new Date(2026, 5, 15, 0, 0, 0)), "2026-06-15");
});

test("detectPreset reconhece um preset conhecido e cai para 'personalizado' fora deles", () => {
  assert.equal(detectPreset("2026-06-09", "2026-06-15", TODAY), "7d");
  assert.equal(detectPreset("2026-06-01", "2026-06-15", TODAY), "mes_atual");
  assert.equal(detectPreset("2026-01-05", "2026-01-20", TODAY), "personalizado");
});

test("previousEquivalentRange: espelha resolve_previous_period do backend (mesma duracao, imediatamente anterior)", () => {
  assert.deepEqual(previousEquivalentRange("2026-03-11", "2026-03-20"), { dateFrom: "2026-03-01", dateTo: "2026-03-10" });
  assert.deepEqual(previousEquivalentRange("2026-06-15", "2026-06-15"), { dateFrom: "2026-06-14", dateTo: "2026-06-14" });
});

test("validateDateRange: rejeita data de calendario inexistente (2026-02-31)", () => {
  // new Date("2026-02-31") nao lanca em JS, "rola" para 03/03 — o bug real
  // que esta correcao evita.
  const r = validateDateRange("2026-02-01", "2026-02-31", TODAY);
  assert.equal(r.valid, false);
});

test("validateDateRange: aceita datas validas dentro do calendario", () => {
  const r = validateDateRange("2026-02-01", "2026-02-28", TODAY);
  assert.equal(r.valid, true);
});

test("validateDateRange: 29/02 so e valido em ano bissexto", () => {
  assert.equal(validateDateRange("2024-02-01", "2024-02-29", TODAY).valid, true); // 2024 bissexto
  assert.equal(validateDateRange("2026-02-01", "2026-02-29", TODAY).valid, false); // 2026 nao e
});

test("validateDateRange: rejeita data futura (amanha)", () => {
  const amanha = new Date(2026, 5, 16);
  const r = validateDateRange("2026-06-10", toISODate(amanha), TODAY);
  assert.equal(r.valid, false);
});

test("validateDateRange: aceita date_to igual a hoje", () => {
  const r = validateDateRange("2026-06-10", toISODate(TODAY), TODAY);
  assert.equal(r.valid, true);
});

test("validateDateRange: rejeita intervalo invertido", () => {
  assert.equal(validateDateRange("2026-06-15", "2026-06-01", TODAY).valid, false);
});

test("validateDateRange: rejeita intervalo maior que 366 dias", () => {
  assert.equal(validateDateRange("2024-01-01", "2026-06-01", TODAY).valid, false);
});

test("validateDateRange: rejeita string malformada sem lancar excecao", () => {
  assert.doesNotThrow(() => {
    assert.equal(validateDateRange("not-a-date", "2026-06-10", TODAY).valid, false);
  });
});

test("parseFiltersFromSearchParams: data de calendario inexistente na URL cai para o default", () => {
  const filters = parseFiltersFromSearchParams(new URLSearchParams("date_from=2026-02-01&date_to=2026-02-31"));
  assert.notEqual(filters.dateTo, "2026-02-31");
});

test("parseFiltersFromSearchParams: data futura na URL cai para o default", () => {
  const filters = parseFiltersFromSearchParams(new URLSearchParams("date_from=2026-01-01&date_to=2099-01-01"));
  assert.notEqual(filters.dateTo, "2099-01-01");
});

test("parseFiltersFromSearchParams: intervalo valido e preservado", () => {
  const params = new URLSearchParams("channels=tiktok,ml&brands=barbours,kokeshi&date_from=2026-05-01&date_to=2026-05-10");
  const filters = parseFiltersFromSearchParams(params);
  assert.deepEqual(filters.channels, ["tiktok", "ml"]);
  assert.deepEqual(filters.brands, ["barbours", "kokeshi"]);
  assert.equal(filters.dateFrom, "2026-05-01");
  assert.equal(filters.dateTo, "2026-05-10");
  assert.equal(filters.compare, false);
});

test("parseFiltersFromSearchParams: datas invertidas caem para o default (30 dias)", () => {
  const params = new URLSearchParams("date_from=2026-05-10&date_to=2026-05-01");
  const filters = parseFiltersFromSearchParams(params);
  assert.notEqual(filters.dateFrom, "2026-05-10"); // nao usa o valor invalido
  assert.ok(filters.dateFrom <= filters.dateTo);
});

test("parseFiltersFromSearchParams: intervalo maior que 366 dias cai para o default", () => {
  const params = new URLSearchParams("date_from=2024-01-01&date_to=2026-06-01");
  const filters = parseFiltersFromSearchParams(params);
  assert.notEqual(filters.dateFrom, "2024-01-01");
});

test("parseFiltersFromSearchParams: data malformada cai para o default sem lancar excecao", () => {
  assert.doesNotThrow(() => {
    const filters = parseFiltersFromSearchParams(new URLSearchParams("date_from=not-a-date&date_to=2026-05-10"));
    assert.ok(filters.dateFrom);
  });
});

test("parseFiltersFromSearchParams: marca desconhecida e descartada silenciosamente (whitelist)", () => {
  const params = new URLSearchParams("brands=barbours,marca_inexistente");
  const filters = parseFiltersFromSearchParams(params);
  assert.deepEqual(filters.brands, ["barbours"]);
});

test("parseFiltersFromSearchParams: sem parametros usa 'todos os canais' e 'todas as marcas'", () => {
  const filters = parseFiltersFromSearchParams(new URLSearchParams());
  assert.deepEqual(filters.channels, ["tiktok", "ml", "shopee"]);
  assert.deepEqual(filters.brands, []);
});

test("parseFiltersFromSearchParams: compare=true e reconhecido", () => {
  assert.equal(parseFiltersFromSearchParams(new URLSearchParams("compare=true")).compare, true);
  assert.equal(parseFiltersFromSearchParams(new URLSearchParams("compare=1")).compare, true);
  assert.equal(parseFiltersFromSearchParams(new URLSearchParams("compare=false")).compare, false);
});

test("filtersToSearchParams: round-trip preserva os filtros", () => {
  const original = {
    channels: ["tiktok", "ml"] as const,
    brands: ["kokeshi", "barbours"],
    dateFrom: "2026-05-01",
    dateTo: "2026-05-10",
    compare: true,
  };
  const params = filtersToSearchParams(original as any);
  const roundTripped = parseFiltersFromSearchParams(params);
  assert.deepEqual(roundTripped.channels, ["tiktok", "ml"]);
  assert.deepEqual(roundTripped.brands, ["barbours", "kokeshi"]); // ordenado
  assert.equal(roundTripped.dateFrom, "2026-05-01");
  assert.equal(roundTripped.dateTo, "2026-05-10");
  assert.equal(roundTripped.compare, true);
});

test("filtersToSearchParams: brands vazio remove o parametro (nao serializa string vazia)", () => {
  const params = filtersToSearchParams({
    channels: ["tiktok", "ml", "shopee"],
    brands: [],
    dateFrom: "2026-05-01",
    dateTo: "2026-05-10",
    compare: false,
  } as any);
  assert.equal(params.has("brands"), false);
  assert.equal(params.get("channels"), "all");
});

test("filtersToSearchParams: preserva parametros nao relacionados a filtros (ex: paginacao)", () => {
  const base = new URLSearchParams("page=3");
  const params = filtersToSearchParams({
    channels: ["tiktok"], brands: [], dateFrom: "2026-01-01", dateTo: "2026-01-31", compare: false,
  } as any, base);
  assert.equal(params.get("page"), "3");
  assert.equal(params.get("channels"), "tiktok");
});

// ---------------------------------------------------------------------------
// hasExplicitFilterParams / computeDefaultFilters / resolveFilters — defaults
// por tela (useGlobalFilters({ defaultPreset, defaultCompare })). Regra:
// qualquer parametro de filtro presente na URL e "explicito" e vence sobre o
// default da tela; URL vazia aplica o default. Isso e o que preserva os
// numeros ja auditados (mes calendario anterior) sem exigir "ultimos 30 dias"
// como default universal — ver docs/filtros_globais_contrato.md.
// ---------------------------------------------------------------------------

test("hasExplicitFilterParams: URL totalmente vazia nao tem filtro explicito", () => {
  assert.equal(hasExplicitFilterParams(new URLSearchParams()), false);
});

test("hasExplicitFilterParams: qualquer parametro reconhecido conta como explicito", () => {
  assert.equal(hasExplicitFilterParams(new URLSearchParams("date_from=2026-06-01")), true);
  assert.equal(hasExplicitFilterParams(new URLSearchParams("date_to=2026-06-30")), true);
  assert.equal(hasExplicitFilterParams(new URLSearchParams("channels=tiktok")), true);
  assert.equal(hasExplicitFilterParams(new URLSearchParams("marketplace=tiktok")), true);
  assert.equal(hasExplicitFilterParams(new URLSearchParams("brands=barbours")), true);
  assert.equal(hasExplicitFilterParams(new URLSearchParams("compare=true")), true);
});

test("hasExplicitFilterParams: parametro nao relacionado a filtro (ex: paginacao) nao conta", () => {
  assert.equal(hasExplicitFilterParams(new URLSearchParams("page=3")), false);
});

test("computeDefaultFilters: URL vazia no Gerencial -> mes calendario anterior completo + compare=true", () => {
  const filters = computeDefaultFilters({ defaultPreset: "mes_anterior", defaultCompare: true, now: () => TODAY });
  assert.deepEqual(filters.channels, ["tiktok", "ml", "shopee"]);
  assert.deepEqual(filters.brands, []);
  assert.equal(filters.dateFrom, "2026-05-01");
  assert.equal(filters.dateTo, "2026-05-31");
  assert.equal(filters.compare, true);
});

test("computeDefaultFilters: URL vazia em Qualidade -> mes anterior + compare=true", () => {
  const filters = computeDefaultFilters({ defaultPreset: "mes_anterior", defaultCompare: true, now: () => TODAY });
  assert.equal(filters.dateFrom, "2026-05-01");
  assert.equal(filters.dateTo, "2026-05-31");
  assert.equal(filters.compare, true);
});

test("computeDefaultFilters: URL vazia em Pedidos -> ultimos 30 dias + compare=false", () => {
  const filters = computeDefaultFilters({ defaultPreset: "30d", now: () => TODAY });
  assert.equal(filters.dateFrom, "2026-05-17");
  assert.equal(filters.dateTo, "2026-06-15");
  assert.equal(filters.compare, false);
});

test("computeDefaultFilters: default sem opcoes cai em ultimos 30 dias + compare=false (fallback neutro)", () => {
  const filters = computeDefaultFilters({ now: () => TODAY });
  assert.equal(filters.compare, false);
  assert.deepEqual(filters.channels, ["tiktok", "ml", "shopee"]);
  assert.deepEqual({ dateFrom: filters.dateFrom, dateTo: filters.dateTo }, presetRange("30d", TODAY));
});

test("computeDefaultFilters: virada janeiro/fevereiro — mes anterior de fevereiro e janeiro (31 dias)", () => {
  const fev = new Date(2026, 1, 10); // 10/fev/2026
  const filters = computeDefaultFilters({ defaultPreset: "mes_anterior", now: () => fev });
  assert.equal(filters.dateFrom, "2026-01-01");
  assert.equal(filters.dateTo, "2026-01-31");
});

test("computeDefaultFilters: virada janeiro/dezembro — mes anterior de janeiro e dezembro do ano anterior", () => {
  const jan = new Date(2026, 0, 10); // 10/jan/2026
  const filters = computeDefaultFilters({ defaultPreset: "mes_anterior", now: () => jan });
  assert.equal(filters.dateFrom, "2025-12-01");
  assert.equal(filters.dateTo, "2025-12-31");
});

test("resolveFilters: URL vazia aplica o default da tela", () => {
  const filters = resolveFilters(new URLSearchParams(), { defaultPreset: "mes_anterior", defaultCompare: true, now: () => TODAY });
  assert.equal(filters.dateFrom, "2026-05-01");
  assert.equal(filters.compare, true);
});

test("resolveFilters: URL explicita prevalece sobre o default da tela (entrada direta em Pedidos nao usa mes_anterior de outra tela)", () => {
  const params = new URLSearchParams("channels=tiktok&date_from=2026-05-01&date_to=2026-05-31&compare=true");
  const filters = resolveFilters(params, { defaultPreset: "30d", defaultCompare: false, now: () => TODAY });
  assert.deepEqual(filters.channels, ["tiktok"]);
  assert.equal(filters.dateFrom, "2026-05-01");
  assert.equal(filters.dateTo, "2026-05-31");
  assert.equal(filters.compare, true);
});

test("resolveFilters: navegacao Gerencial -> Pedidos preserva o periodo carregado na querystring (nao aplica o default de 30d de Pedidos)", () => {
  // Simula o link de AppNav/BrandPerformanceTable propagando o periodo atual
  // do Gerencial (mes anterior) para Pedidos via querystring.
  const paramsVindoDoGerencial = new URLSearchParams("channels=all&date_from=2026-05-01&date_to=2026-05-31&compare=true");
  const filtrosEmPedidos = resolveFilters(paramsVindoDoGerencial, { defaultPreset: "30d", now: () => TODAY });
  assert.equal(filtrosEmPedidos.dateFrom, "2026-05-01");
  assert.equal(filtrosEmPedidos.dateTo, "2026-05-31");
});

test("resolveFilters: entrada direta em Pedidos (sem nenhum parametro) mantem 30 dias", () => {
  const filters = resolveFilters(new URLSearchParams(), { defaultPreset: "30d", now: () => TODAY });
  assert.deepEqual(filters, computeDefaultFilters({ defaultPreset: "30d", now: () => TODAY }));
});

test("resolveFilters: compare=false explicito (so date_from/date_to presentes, sem compare) sobrevive mesmo com defaultCompare=true — e o que faz 'desligar comparacao' sobreviver a reload", () => {
  // Cenario: usuario estava no Gerencial (defaultCompare=true), desligou o
  // toggle de comparacao (compare passa a ausente na URL, ver
  // filtersToSearchParams) e recarregou a pagina. hasExplicitFilterParams
  // ja e true (date_from/date_to presentes), entao o parser nunca consulta
  // defaultCompare — compare cai para false, nao para o default da tela.
  const params = new URLSearchParams("channels=all&date_from=2026-05-01&date_to=2026-05-31");
  const filters = resolveFilters(params, { defaultPreset: "mes_anterior", defaultCompare: true, now: () => TODAY });
  assert.equal(filters.compare, false);
});

test("resolveFilters: idempotente — resolver a mesma querystring materializada duas vezes da o mesmo resultado (sem drift, sem loop)", () => {
  // Garante a propriedade que sustenta 'materializacao ocorre uma unica vez'
  // no hook: uma vez que o default e escrito na URL como parametros
  // explicitos, resolver de novo a MESMA URL nunca produz um valor
  // diferente (o que dispararia outro router.replace).
  const first = computeDefaultFilters({ defaultPreset: "mes_anterior", defaultCompare: true, now: () => TODAY });
  const materialized = filtersToSearchParams(first);
  const second = resolveFilters(materialized, { defaultPreset: "mes_anterior", defaultCompare: true, now: () => TODAY });
  assert.deepEqual(first, second);
  // Resolver de novo a saida da segunda resolucao tambem tem que bater —
  // confirma que o ponto fixo e estavel (nao ha um terceiro valor diferente).
  const thirdParams = filtersToSearchParams(second);
  assert.equal(thirdParams.toString(), materialized.toString());
});

test("toggleMultiSelect: permite chegar a selecao vazia (semantica de 'todas' para marcas)", () => {
  assert.deepEqual(toggleMultiSelect(["barbours"], "barbours"), []);
});

test("toggleMultiSelect: adiciona e remove itens normalmente", () => {
  assert.deepEqual(toggleMultiSelect([], "barbours"), ["barbours"]);
  assert.deepEqual(toggleMultiSelect(["barbours"], "kokeshi"), ["barbours", "kokeshi"]);
  assert.deepEqual(toggleMultiSelect(["barbours", "kokeshi"], "barbours"), ["kokeshi"]);
});

// ---------------------------------------------------------------------------
// nav-links — usado por AppNav, BrandPerformanceTable e brand/[brand]/page
// para preservar filtros globais ao navegar entre telas compativeis.
// ---------------------------------------------------------------------------

test("isFilterAwarePath: reconhece as 5 telas com filtro global", () => {
  for (const p of ["/", "/canais", "/financeiro", "/qualidade", "/pedidos"]) {
    assert.equal(isFilterAwarePath(p), true, p);
  }
});

test("isFilterAwarePath: trata /brand/[brand] como rota compativel, sem hardcode de marca", () => {
  assert.equal(isFilterAwarePath("/brand/barbours"), true);
  assert.equal(isFilterAwarePath("/brand/kokeshi"), true);
  assert.equal(isFilterAwarePath("/brand/qualquer-marca-nova"), true); // generico, nao hardcoded
});

test("isFilterAwarePath: telas com semantica propria nao propagam filtro", () => {
  for (const p of ["/produtos", "/tempo-real", "/inteligencia", "/operacoes"]) {
    assert.equal(isFilterAwarePath(p), false, p);
  }
});

test("appendQuery: anexa a querystring quando presente", () => {
  assert.equal(appendQuery("/brand/barbours", "channels=tiktok&compare=true"), "/brand/barbours?channels=tiktok&compare=true");
});

test("appendQuery: nao deixa '?' pendurado quando a query esta vazia", () => {
  assert.equal(appendQuery("/brand/barbours", ""), "/brand/barbours");
  assert.equal(appendQuery("/", ""), "/");
});

// ---------------------------------------------------------------------------
// mockLimitationNote — avisa quando o fallback de demonstracao (API offline)
// nao filtra por marca, para nao passar a impressao de que o dado de exemplo
// reflete o filtro aplicado.
// ---------------------------------------------------------------------------

test("mockLimitationNote: null quando ao vivo, independente de marca ou periodo customizado", () => {
  assert.equal(mockLimitationNote(true, ["barbours"], false), null);
  assert.equal(mockLimitationNote(true, [], true), null);
});

test("mockLimitationNote: null offline sem marca e sem periodo customizado (mock generico e o esperado)", () => {
  assert.equal(mockLimitationNote(false, [], false), null);
});

test("mockLimitationNote: avisa quando offline E ha marca selecionada", () => {
  const note = mockLimitationNote(false, ["barbours"], false);
  assert.notEqual(note, null);
  assert.match(note ?? "", /demonstra[cç][aã]o/i);
});

test("mockLimitationNote: avisa quando offline e somente o periodo foi customizado (sem marca)", () => {
  // Achado da revisao de codigo: os mocks ignoram dateFrom/dateTo por
  // completo — trocar so o periodo (preset diferente do default da tela)
  // com a API offline precisa do mesmo aviso que trocar de marca ja tinha.
  const note = mockLimitationNote(false, [], true);
  assert.notEqual(note, null);
  assert.match(note ?? "", /per[ií]odo/i);
});

test("mockLimitationNote: menciona marca e periodo quando os dois estao customizados", () => {
  const note = mockLimitationNote(false, ["barbours"], true) ?? "";
  assert.match(note, /marca/i);
  assert.match(note, /per[ií]odo/i);
});
