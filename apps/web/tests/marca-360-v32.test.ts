// Gate V3-2 — Marca 360: competência real, dois regimes temporais, contexto
// quente da Inteligência e os dois drill-downs mensais.
//
// Testes de comportamento sobre os módulos puros, mais asserções estáticas
// sobre a página e o painel para os contratos que não têm superfície pura
// (ausência de mock, um único diálogo, piso tipográfico, linha não clicável).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  availabilityForBrand, brandDetailRequestKey, fmtCompetencia, isRefMonth,
  monthlyViewState, monthOptions, parseRefMonth, periodRegimeRelation,
  REF_MONTH_QUERY_KEY, resolveRefMonth,
  type MonthAvailability, type ParamReader,
} from "../src/lib/brand/ref-month.ts";
import {
  changesState, CHANGES_NOTE, channelTotals, decomposeByChannel, sortByImpact,
} from "../src/lib/brand/period-changes.ts";
import {
  channelCtaHref, CHANNEL_CTA_SCOPE_NOTE, metricValue, PRODUCT_CTA_SCOPE_NOTE, productCtaHref,
  readChannelDetail, readProductDetail,
} from "../src/lib/brand/monthly-drilldown.ts";
import {
  ARRIVAL_FOCUSES, buildInteligenciaArrivalParams, buildReturnHref, CTX_FROM_CANAIS,
  CTX_FROM_INTELIGENCIA, focusForEvidenceKind, focusForQuadrant, isFocusCompatibleWithChannel,
  parseBrandArrivalContext, returnCtaLabel, returnPreservesGlobalFilters,
  SECTION_MENSAL_PRODUTOS, SECTION_PERIOD,
} from "../src/lib/brand-arrival-context.ts";
import { FILTER_QUERY_KEYS, mergeFilteredHref } from "../src/lib/filters/nav-links.ts";
import { decBr, pctBr, roasBr } from "../src/lib/inteligencia/format.ts";

const WEB = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(WEB, p), "utf8");
const PAGINA = read("app/brand/[brand]/page.tsx");
const PAINEL = read("src/components/brand/TikTokMonthlyPanel.tsx");
const DRILL = read("src/lib/brand/monthly-drilldown.ts");
const CTX = read("src/lib/brand-arrival-context.ts");

/**
 * Fonte SEM comentarios.
 *
 * As asserções de AUSÊNCIA precisam olhar código e markup, não prosa. Um
 * comentário que diz "nada aqui é governado pelo intervalo global", ou que
 * explica por que `refreshed_at` não existe no contrato, casaria com a busca e
 * reprovaria justamente o arquivo que faz a coisa certa — foi o que aconteceu
 * na primeira execução destes testes.
 */
function semComentarios(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    // Agnóstico a EOL: `.` não casa `\r`, então um `split("\n")` cru deixaria o
    // `\r` no fim da linha e o `$` do regex de comentário jamais casaria. O repo
    // usa CRLF na árvore de trabalho (`core.autocrlf=true`), logo isto não é
    // hipótese remota.
    .split(/\r?\n/)
    .map((l) => l.replace(/(^|\s)\/\/.*$/, "$1"))
    .join("\n");
}
const PAGINA_CODIGO = semComentarios(PAGINA);
const PAINEL_CODIGO = semComentarios(PAINEL);
const DRILL_CODIGO = semComentarios(DRILL);

const params = (qs: string): ParamReader => new URLSearchParams(qs);
const ALL = ["tiktok", "ml", "shopee"];

// ═══════════════════════════════════════════════════════════════════════════
// 1. `available_months` vem do contrato REAL, não de mock
// ═══════════════════════════════════════════════════════════════════════════

test("V32 available_months existe no contrato TS e no schema da API", () => {
  const client = read("src/lib/api-client.ts");
  assert.match(client, /available_months: string\[\];/, "campo no contrato TypeScript");
  // o campo é do BE5 e vive DENTRO de BrandDetail, não num endpoint novo
  const brandDetail = client.slice(client.indexOf("export interface BrandDetail {"));
  assert.ok(brandDetail.slice(0, brandDetail.indexOf("\n}")).includes("available_months"));
  const schema = readFileSync(join(WEB, "..", "api", "app", "schemas", "performance.py"), "utf8");
  assert.match(schema, /available_months: list\[str\]/, "campo no schema real da API");
});

test("V32 a página de Marca não importa mock-daily nem AVAILABLE_MONTHS", () => {
  assert.doesNotMatch(PAGINA, /mock-daily/, "nenhum import direto do módulo de mock");
  assert.doesNotMatch(PAGINA, /AVAILABLE_MONTHS/, "a lista de competências não vem do mock");
  assert.doesNotMatch(PAINEL, /mock-daily|AVAILABLE_MONTHS/, "nem no painel mensal");
  // o fallback continua existindo, mas encapsulado e ROTULADO como exemplo
  assert.match(PAGINA, /buildDemoSeries\(brand, days\)/);
  assert.match(PAGINA, /DEMO_SERIES_WARNING/);
  const demo = read("src/lib/brand/demo-series.ts");
  assert.match(demo, /não usar para decisão/, "o rótulo diz explicitamente que não sustenta decisão");
});

test("V32 as competências do seletor vêm de available_months da resposta", () => {
  assert.match(PAGINA, /months: d\.available_months \?\? \[\]/);
  assert.match(PAGINA, /months=\{opcoesMes\}/);
  assert.match(PAGINA, /monthOptions\(mesesDaMarca\)/);
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. Parsing e resolução de `ref_month`
// ═══════════════════════════════════════════════════════════════════════════

test("V32 ref_month: formato canônico aceito, o resto recusado", () => {
  assert.ok(isRefMonth("2026-08"));
  assert.ok(isRefMonth("2026-01"));
  assert.ok(isRefMonth("2026-12"));
  for (const ruim of ["2026-13", "2026-00", "2026-8", "26-08", "2026/08", "2026-08-01", "", "agosto", "2026-1a"]) {
    assert.equal(isRefMonth(ruim), false, ruim);
  }
});

test("V32 ref_month: ausente, inválido e repetido resolvem sem erro", () => {
  assert.equal(parseRefMonth(params("")), null, "ausente");
  assert.equal(parseRefMonth(params("ref_month=2026-13")), null, "mês inexistente");
  assert.equal(parseRefMonth(params("ref_month=")), null, "vazio");
  assert.equal(parseRefMonth(params("ref_month=2026-08&ref_month=2026-07")), null, "repetido é ambíguo");
  assert.equal(parseRefMonth(params("ref_month=2026-08")), "2026-08", "válido");
  assert.equal(parseRefMonth(params("ref_month=%202026-08%20")), "2026-08", "espaços são tolerados");
});

test("V32 ref_month: a URL manda, e competência sem dado é PRESERVADA", () => {
  const disponiveis = ["2026-07", "2026-06", "2026-05"];
  const naLista = resolveRefMonth("2026-06", disponiveis);
  assert.deepEqual(naLista, { month: "2026-06", source: "url", available: true, hasAvailable: true });

  // o caso que importa: mês bem formado, mas a marca não tem dado nele
  const foraDaLista = resolveRefMonth("2026-01", disponiveis);
  assert.equal(foraDaLista.month, "2026-01", "a competência pedida NÃO é trocada em silêncio");
  assert.equal(foraDaLista.available, false, "e é marcada como sem dado, para o estado vazio");
  assert.equal(foraDaLista.hasAvailable, true);
});

test("V32 ref_month: ausência cai na competência mais recente disponível", () => {
  const r = resolveRefMonth(null, ["2026-07", "2026-06"]);
  assert.deepEqual(r, { month: "2026-07", source: "latest", available: true, hasAvailable: true });
});

test("V32 ref_month: marca sem nenhuma competência não inventa mês", () => {
  const r = resolveRefMonth(null, []);
  assert.deepEqual(r, { month: null, source: "default", available: false, hasAvailable: false });
});

test("V32 a adoção da competência mais recente é por DERIVAÇÃO, sem helper morto", () => {
  // Não existe `latestToAdopt`: a adoção acontece porque `available_months`
  // entra no estado e `resolveRefMonth` passa a devolver `available[0]`, o que
  // muda a identidade da requisição. Um helper testado isoladamente e sem
  // consumidor na página dava FALSA PROVA de wiring.
  const antes = resolveRefMonth(null, []);
  assert.equal(antes.month, null, "antes da resposta não há competência a pedir");
  const depois = resolveRefMonth(null, ["2026-07", "2026-06"]);
  assert.equal(depois.month, "2026-07", "com a lista, a mais recente é adotada");
  assert.notEqual(
    brandDetailRequestKey("barbours", antes.month),
    brandDetailRequestKey("barbours", depois.month),
    "a mudança de identidade é o que dispara a segunda leitura",
  );
  // e ela CONVERGE: com a competência já na lista, a resolução se repete igual
  const estavel = resolveRefMonth(null, ["2026-07", "2026-06"]);
  assert.equal(estavel.month, depois.month, "no máximo uma adoção — sem loop");
  const src = read("src/lib/brand/ref-month.ts");
  assert.doesNotMatch(src, /export function latestToAdopt/, "helper removido");
  assert.doesNotMatch(PAGINA, /latestToAdopt/);
});

test("V32 monthOptions: rótulo de competência, sem duplicata e sem lixo", () => {
  assert.deepEqual(monthOptions(["2026-08", "2026-08", "2026-13", "2026-07", ""]), [
    { value: "2026-08", label: "ago/2026" },
    { value: "2026-07", label: "jul/2026" },
  ]);
  assert.deepEqual(monthOptions([]), []);
});

test("V32 o rótulo de competência é visualmente distinto do de intervalo", () => {
  assert.equal(fmtCompetencia("2026-08"), "ago/2026");
  assert.equal(fmtCompetencia(null), "—");
  assert.equal(fmtCompetencia("2026-13"), "—");
  // intervalo usa dd/mm/aaaa – dd/mm/aaaa (fmtPeriodo); competência usa mmm/aaaa.
  assert.doesNotMatch(fmtCompetencia("2026-08"), /\d{2}\/\d{2}\/\d{4}/);
});

test("V32 a competência escolhida vira URL — seleção compartilhável", () => {
  assert.equal(REF_MONTH_QUERY_KEY, "ref_month");
  assert.match(PAGINA, /qs\.set\(REF_MONTH_QUERY_KEY, month\)/);
  // `push`, nao `replace`: a escolha da competencia e' navegacao do analista, e o
  // botao voltar precisa desfazer a escolha. Com `replace` o `back` pulava a
  // competencia inteira — achado do QA visual do V3-3.
  assert.match(PAGINA, /router\.push\(`\$\{pathname\}\?\$\{qs\.toString\(\)\}`, \{ scroll: false \}\)/);
  assert.doesNotMatch(PAGINA_CODIGO, /router\.replace/, "a pagina de Marca nao usa replace para a competencia");
  // e NÃO entra em FILTER_QUERY_KEYS: é estado da rota, não filtro global
  assert.ok(!FILTER_QUERY_KEYS.includes(REF_MONTH_QUERY_KEY));
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. Identidade e frescor da requisição mensal
// ═══════════════════════════════════════════════════════════════════════════

test("V32 chave mensal é marca + competência, e nada mais", () => {
  assert.equal(brandDetailRequestKey("barbours", "2026-08"), "barbours|2026-08");
  assert.notEqual(
    brandDetailRequestKey("barbours", "2026-08"),
    brandDetailRequestKey("barbours", "2026-07"),
    "trocar competência muda a identidade",
  );
  assert.notEqual(
    brandDetailRequestKey("barbours", "2026-08"),
    brandDetailRequestKey("kokeshi", "2026-08"),
    "trocar marca muda a identidade",
  );
  assert.equal(brandDetailRequestKey("barbours", null), "barbours|default");
});

test("V32 a identidade mensal NÃO carrega intervalo, canal nem compare", () => {
  const chave = brandDetailRequestKey("barbours", "2026-08");
  for (const ruido of ["tiktok", "2026-08-01", "true", "compare"]) {
    assert.ok(!chave.includes(ruido), ruido);
  }
  // e a chave global segue intocada, com os cinco componentes originais
  assert.match(PAGINA, /return `\$\{brand\}\|\$\{channels\.join\(","\)\}\|\$\{dateFrom\}\|\$\{dateTo\}\|\$\{compare\}`;/);
});

test("V32 dados mensais só são lidos quando frescos", () => {
  assert.match(PAGINA, /const displayDetail = detailStatus\.fresh \? brandDetail : null;/);
  assert.match(PAGINA, /detail=\{displayDetail\}/, "o painel recebe o valor protegido, nunca o estado cru");
  assert.match(PAGINA, /const semPayloadFresco = detailStatus\.fresh && brandDetail == null;/,
    "payload ausente numa resposta fresca nunca chega a ready");
});

test("V32 resposta antiga é descartada pela guarda ignore, nos dois efeitos", () => {
  assert.equal((PAGINA.match(/let ignore = false;/g) ?? []).length, 2, "um por efeito");
  assert.equal((PAGINA.match(/if \(ignore\) return;/g) ?? []).length >= 4, true);
  assert.equal((PAGINA.match(/return \(\) => \{ ignore = true; \};/g) ?? []).length, 2);
});

test("V32 os TRÊS desfechos encerram o loading da chave atual", () => {
  const efeito = PAGINA.slice(PAGINA.indexOf("fetchBrandDetail(brand, refMonth"));
  const corpo = efeito.slice(0, efeito.indexOf("}, [brand, refMonth, detailRetry])"));
  // sucesso/nulo no .then e rejeição no .catch — os dois registram a chave
  assert.equal((corpo.match(/setResolvedDetailKey\(key\)/g) ?? []).length, 2);
  assert.equal((corpo.match(/setDetailLoading\(false\)/g) ?? []).length, 2);
  assert.match(corpo, /\.catch\(\(\) => \{/, "rejeição não deixa o painel em skeleton para sempre");
});

test("V32 drill-down mensal fecha quando a identidade muda", () => {
  assert.match(PAGINA, /useEffect\(\(\) => \{ setDialog\(null\); \}, \[detailRequestKey\]\);/);
});

test("V32 os QUATRO estados do painel mensal são distintos e exclusivos", () => {
  const base = { loading: false, error: false, monthAvailable: true };
  assert.equal(monthlyViewState({ ...base, loading: true }), "loading");
  assert.equal(monthlyViewState({ ...base, error: true }), "error");
  assert.equal(monthlyViewState({ ...base, monthAvailable: false }), "empty");
  assert.equal(monthlyViewState(base), "ready");
  // precedência: loading vence erro (retry muda a chave antes de limpar o erro)
  assert.equal(monthlyViewState({ loading: true, error: true, monthAvailable: false }), "loading");
  // erro vence vazio: falha não afirma "não há dado"
  assert.equal(monthlyViewState({ loading: false, error: true, monthAvailable: false }), "error");
});

test("V32 `unavailable` não existe mais no contrato de estados", () => {
  // Existia com a copy "a consulta concluiu sem payload", que era falsa: no
  // contrato de `apiFetch`, `null` é falha de leitura. Distinção inventada, e
  // que ainda privava o usuário do retry.
  const src = read("src/lib/brand/ref-month.ts");
  assert.match(src, /MonthlyViewState = "loading" \| "error" \| "empty" \| "ready"/);
  assert.doesNotMatch(src, /"unavailable"/);
  assert.doesNotMatch(PAINEL, /unavailable/);
  assert.doesNotMatch(PAINEL, /consulta concluiu sem payload/);
  assert.doesNotMatch(PAGINA, /"unavailable"|hasData:/);
});

test("V32 os dois vazios e o erro têm textos distintos, e só o erro tem retry", () => {
  assert.match(PAINEL, /Esta marca não tem histórico no TikTok Shop/, "vazio sem histórico");
  assert.match(PAINEL, /nenhuma competência com dado<\/strong> para esta marca/);
  assert.match(PAINEL, /não há mês para escolher/, "não sugere escolher outro mês");
  assert.match(PAINEL, /Sem dado de TikTok Shop para/, "vazio de mês nomeia a competência");
  assert.match(PAINEL, /preservada/, "o vazio de mês declara que nada foi trocado");
  assert.match(PAINEL, /A leitura mensal falhou/, "erro fala de falha, não de ausência");
  assert.match(PAINEL, /Não sabemos ainda se a marca tem dado/, "erro não afirma vazio");
  // retry existe exatamente uma vez, e no ramo de erro
  assert.equal((PAINEL.match(/onClick=\{onRetry\}/g) ?? []).length, 1);
  const erro = PAINEL.slice(PAINEL.indexOf('state === "error"'), PAINEL.indexOf('state === "empty"'));
  assert.match(erro, /Tentar novamente/);
  // os dois vazios são mutuamente exclusivos por `hasHistory`
  assert.match(PAINEL, /state === "empty" && !hasHistory/);
  assert.match(PAINEL, /state === "empty" && hasHistory/);
});

// ═══════════════════════════════════════════════════════════════════════════
// 4. Os dois regimes temporais
// ═══════════════════════════════════════════════════════════════════════════

test("V32 regimes: sobreposição não gera nota", () => {
  const r = periodRegimeRelation("2026-08", "2026-08-01", "2026-08-31");
  assert.equal(r.overlaps, true);
  assert.equal(r.note, null);
});

test("V32 regimes: competência fora do intervalo gera nota NEUTRA e não bloqueia", () => {
  const r = periodRegimeRelation("2026-07", "2026-08-01", "2026-08-31");
  assert.equal(r.overlaps, false);
  assert.match(r.note ?? "", /fora do intervalo global/);
  assert.match(r.note ?? "", /períodos diferentes/);
  // não é erro: nenhuma palavra de falha
  assert.doesNotMatch(r.note ?? "", /erro|falha|inválid|indisponív/i);
});

test("V32 regimes: sobreposição PARCIAL conta como sobreposição", () => {
  // o intervalo termina dentro do mês
  assert.equal(periodRegimeRelation("2026-08", "2026-07-15", "2026-08-02").overlaps, true);
  // e começa dentro do mês
  assert.equal(periodRegimeRelation("2026-08", "2026-08-30", "2026-09-10").overlaps, true);
  // fevereiro de ano não bissexto termina em 28
  assert.equal(periodRegimeRelation("2026-02", "2026-02-28", "2026-02-28").overlaps, true);
  assert.equal(periodRegimeRelation("2026-02", "2026-03-01", "2026-03-31").overlaps, false);
});

test("V32 regimes: sem competência ou sem intervalo não afirma nada", () => {
  assert.deepEqual(periodRegimeRelation(null, "2026-08-01", "2026-08-31"), { overlaps: false, note: null });
  assert.deepEqual(periodRegimeRelation("2026-08", "", ""), { overlaps: false, note: null });
});

test("V32 todo bloco analítico declara o período que o governa", () => {
  // global: etiqueta de intervalo; mensal: etiqueta de competência
  assert.equal((PAGINA.match(/intervalo global \$\{periodLabel\}/g) ?? []).length >= 4, true);
  assert.match(PAINEL, /competência \{competencia\}/, "etiqueta por bloco dentro do contêiner");
  assert.match(PAINEL, /TikTok Shop · análise mensal/, "o contêiner é nomeado");
  // KPIs mensais NÃO ficam em cards que pareçam do intervalo global
  assert.doesNotMatch(PAINEL_CODIGO, /intervalo global/);
});

test("V32 o contêiner mensal declara competência, fonte e estado da leitura", () => {
  assert.match(PAINEL, /Competência <strong>\{competencia\}<\/strong>/);
  assert.match(PAINEL, /fonte: contrato mensal do TikTok Shop/);
  assert.match(PAINEL, /EstadoLeitura/);
  // e NÃO inventa timestamp: o contrato não traz refreshed_at
  assert.doesNotMatch(PAINEL_CODIGO, /new Date\(\)|Date\.now\(\)|refreshed_at/);
  assert.match(PAINEL, /nunca um 'atualizado em' que não foi entregue/);
});

test("V32 fallback global é rotulado como exemplo e diferenciado visualmente", () => {
  assert.match(PAGINA, /DEMO_SERIES_WARNING/);
  assert.match(PAGINA, /série de exemplo/);
  assert.match(PAGINA, /isLive \? "border-violet-100" : "border-amber-200"/, "borda distinta, sem perder legibilidade");
  const demo = read("src/lib/brand/demo-series.ts");
  assert.match(demo, /Dados de exemplo — não usar para decisão/);
});

// ═══════════════════════════════════════════════════════════════════════════
// 5. "O que mudou" — decomposição sem métrica nova
// ═══════════════════════════════════════════════════════════════════════════

const dia = (date: string, tk: number | null, ml: number | null, sh: number | null): {
  date: string; tiktok_gmv: number | null; ml_gmv: number | null; shopee_gmv: number | null;
  total_gmv: number; orders: number; avg_ticket: number | null; ad_spend: number | null;
} => ({
  date, tiktok_gmv: tk, ml_gmv: ml, shopee_gmv: sh,
  total_gmv: (tk ?? 0) + (ml ?? 0) + (sh ?? 0), orders: 1, avg_ticket: null, ad_spend: null,
});

test("V32 decomposição por canal: null é ausência, zero é medida", () => {
  const cur = [dia("2026-08-01", 100, 0, null), dia("2026-08-02", 50, 0, null)];
  const prev = [dia("2026-07-01", 60, 10, null)];
  const linhas = decomposeByChannel(cur, prev, ALL);
  const tk = linhas.find((l) => l.key === "tiktok")!;
  const ml = linhas.find((l) => l.key === "ml")!;
  const sh = linhas.find((l) => l.key === "shopee")!;

  assert.equal(tk.current, 150);
  assert.equal(tk.delta, 90);
  assert.equal(ml.current, 0, "zero medido continua zero");
  assert.equal(ml.delta, -10);
  assert.equal(sh.current, null, "canal sem nenhuma linha com valor é ausência");
  assert.equal(sh.delta, null, "não se subtrai ausência");
  assert.equal(sh.deltaPct, null);
});

test("V32 decomposição: sem base positiva não existe percentual", () => {
  const linhas = decomposeByChannel([dia("2026-08-01", 100, null, null)], [dia("2026-07-01", 0, null, null)], ["tiktok"]);
  assert.equal(linhas[0].delta, 100);
  assert.equal(linhas[0].deltaPct, null, "dividir por zero não produz leitura");
});

test("V32 decomposição respeita a seleção de canal", () => {
  const linhas = decomposeByChannel([dia("2026-08-01", 1, 2, 3)], [], ["ml"]);
  assert.deepEqual(linhas.map((l) => l.key), ["ml"]);
});

test("V32 ordenação por impacto: magnitude, com ausência no fim", () => {
  const linhas = decomposeByChannel(
    [dia("2026-08-01", 10, 500, null)],
    [dia("2026-07-01", 100, 100, null)],
    ALL,
  );
  assert.deepEqual(sortByImpact(linhas).map((l) => l.key), ["ml", "tiktok", "shopee"]);
});

test("V32 o bloco de mudanças só aparece quando o dado sustenta", () => {
  assert.equal(changesState(false, true, 10), "sem_comparacao");
  assert.equal(changesState(true, false, 10), "demonstracao");
  assert.equal(changesState(true, true, 0), "sem_periodo_anterior");
  assert.equal(changesState(true, true, 10), "ready");
  for (const k of ["sem_comparacao", "demonstracao", "sem_periodo_anterior"] as const) {
    assert.ok(CHANGES_NOTE[k].length > 20, k);
  }
});

test("V32 mix por marketplace: share sobre os canais com dado, nunca 0% fabricado", () => {
  const totais = channelTotals([dia("2026-08-01", 75, 25, null)], ALL);
  assert.equal(totais.find((c) => c.key === "tiktok")!.sharePct, 75);
  assert.equal(totais.find((c) => c.key === "ml")!.sharePct, 25);
  const sh = totais.find((c) => c.key === "shopee")!;
  assert.equal(sh.gmv, null);
  assert.equal(sh.sharePct, null, "canal sem cobertura não recebe 0%");
  // base zero não distribui percentual
  assert.equal(channelTotals([dia("2026-08-01", 0, null, null)], ["tiktok"])[0].sharePct, null);
});

// ═══════════════════════════════════════════════════════════════════════════
// 6. Contexto quente — Canais retrocompatível, Inteligência novo
// ═══════════════════════════════════════════════════════════════════════════

const CANAIS_OK = "ctx_from=canais&ctx_signal=custo_alto&ctx_channel=ml&ctx_brand=kokeshi";

test("V32 o contrato de Canais continua idêntico", () => {
  const ctx = parseBrandArrivalContext(params(CANAIS_OK), "kokeshi", ALL);
  assert.ok(ctx);
  assert.equal(ctx!.from, CTX_FROM_CANAIS);
  assert.equal((ctx as { signal: string }).signal, "custo_alto");
  assert.equal(ctx!.channel, "ml");
  assert.equal(ctx!.brand, "kokeshi");
  assert.equal(returnCtaLabel(ctx!), "Voltar à evidência em Canais");
  assert.equal(buildReturnHref(ctx!), "/canais?brands=kokeshi&channels=ml");
  assert.equal(returnPreservesGlobalFilters(ctx!), true);
});

test("V32 os SEIS focos da Inteligência são aceitos, cada um com o seu canal", () => {
  for (const focus of ARRIVAL_FOCUSES) {
    const canal = focus === "produto_tiktok" ? "tiktok" : "ml";
    const qs = `ctx_from=inteligencia&ctx_focus=${focus}&ctx_channel=${canal}&ctx_brand=barbours`;
    const ctx = parseBrandArrivalContext(params(qs), "barbours", ALL);
    assert.ok(ctx, focus);
    assert.equal(ctx!.from, CTX_FROM_INTELIGENCIA, focus);
    assert.equal((ctx as { focus: string }).focus, focus);
    assert.ok(ctx!.description.length > 0, focus);
    assert.doesNotMatch(ctx!.description, /\d/, `${focus}: descrição nunca traz número`);
    assert.ok((ctx!.unavailableNote ?? "").length > 0, `${focus}: sempre declara o escopo/limitação`);
    assert.equal(returnCtaLabel(ctx!), "Voltar à evidência em Inteligência");
    assert.equal(returnPreservesGlobalFilters(ctx!), false);
  }
});

test("V32 só produto_tiktok e os três focos de Ads têm evidência nesta página", () => {
  const ctxDe = (focus: string, canal: string) =>
    parseBrandArrivalContext(
      params(`ctx_from=inteligencia&ctx_focus=${focus}&ctx_channel=${canal}&ctx_brand=barbours`),
      "barbours", ALL,
    )!;
  assert.equal(ctxDe("produto_tiktok", "tiktok").section, SECTION_MENSAL_PRODUTOS);
  for (const f of ["desperdicio_ads", "escala_ads", "venda_organica"]) {
    assert.equal(ctxDe(f, "ml").section, SECTION_PERIOD, f);
    assert.equal(ctxDe(f, "ml").hasEvidence, true, f);
  }
  // concentração e LTV não existem na Marca: declaram limitação, sem âncora falsa
  for (const f of ["concentracao", "ltv"]) {
    assert.equal(ctxDe(f, "ml").section, null, f);
    assert.equal(ctxDe(f, "ml").hasEvidence, false, f);
    assert.match(ctxDe(f, "ml").unavailableNote ?? "", /não exist\w* nesta página/, f);
  }
});

test("V32 as duas âncoras de evidência existem de fato na página", () => {
  assert.match(PAGINA, /id=\{SECTION_PERIOD\}/);
  assert.match(PAGINA, /className="scroll-mt-24"/);
  assert.match(PAINEL, /id=\{SECTION_MENSAL_PRODUTOS\}/);
  assert.match(PAINEL, /scroll-mt-28/);
});

test("V32 contexto inválido, repetido, ambíguo ou incompatível é ignorado", () => {
  const q = (extra: string) => `ctx_from=inteligencia&ctx_channel=ml&ctx_brand=barbours&${extra}`;
  // foco desconhecido
  assert.equal(parseBrandArrivalContext(params(q("ctx_focus=margem")), "barbours", ALL), null);
  // foco ausente
  assert.equal(parseBrandArrivalContext(params("ctx_from=inteligencia&ctx_channel=ml&ctx_brand=barbours"), "barbours", ALL), null);
  // repetido é ambíguo
  assert.equal(parseBrandArrivalContext(params(q("ctx_focus=ltv&ctx_focus=escala_ads")), "barbours", ALL), null);
  // as DUAS chaves de motivo juntas: origem ambígua
  assert.equal(parseBrandArrivalContext(params(q("ctx_focus=ltv&ctx_signal=custo_alto")), "barbours", ALL), null);
  // marca divergente da rota
  assert.equal(parseBrandArrivalContext(params(q("ctx_focus=ltv")), "kokeshi", ALL), null);
  // canal fora do filtro global
  assert.equal(parseBrandArrivalContext(params(q("ctx_focus=ltv")), "barbours", ["tiktok"]), null);
  // canal incompatível com o foco: produto_tiktok NUNCA é de ML
  assert.equal(
    parseBrandArrivalContext(params(q("ctx_focus=produto_tiktok")), "barbours", ALL),
    null,
    "par foco × canal impossível",
  );
  // origem desconhecida
  assert.equal(
    parseBrandArrivalContext(params("ctx_from=gerencial&ctx_focus=ltv&ctx_channel=ml&ctx_brand=barbours"), "barbours", ALL),
    null,
  );
});

test("V32 compatibilidade foco × canal é fechada", () => {
  assert.equal(isFocusCompatibleWithChannel("produto_tiktok", "tiktok"), true);
  assert.equal(isFocusCompatibleWithChannel("produto_tiktok", "ml"), false);
  assert.equal(isFocusCompatibleWithChannel("escala_ads", "ml"), true);
  assert.equal(isFocusCompatibleWithChannel("escala_ads", "shopee"), false);
});

test("V32 a URL quente carrega SÓ identificadores — zero número, dinheiro ou texto", () => {
  for (const focus of ARRIVAL_FOCUSES) {
    const qs = buildInteligenciaArrivalParams(focus, "barbours");
    assert.match(qs, /^ctx_from=inteligencia&ctx_focus=[a-z_]+&ctx_channel=[a-z]+&ctx_brand=[a-z]+$/, focus);
    assert.doesNotMatch(qs, /\d/, `${focus}: nenhum dígito`);
    assert.doesNotMatch(qs, /R\$|%|\{|\}|"|item_id|product_id/, focus);
    // exatamente quatro chaves, nenhuma a mais
    assert.equal([...new URLSearchParams(qs).keys()].length, 4, focus);
  }
  // produtor recusa entrada fora do domínio
  assert.equal(buildInteligenciaArrivalParams(null, "barbours"), "");
  assert.equal(buildInteligenciaArrivalParams("escala_ads", "inexistente"), "");
});

test("V32 o retorno reconstrói marca + lente/âncora e NUNCA repropaga ctx_*", () => {
  const esperado: Record<string, string> = {
    desperdicio_ads: "/inteligencia?brands=barbours&lens=parar#fila-evidencias",
    escala_ads: "/inteligencia?brands=barbours&lens=escalar#fila-evidencias",
    venda_organica: "/inteligencia?brands=barbours&lens=testar#fila-evidencias",
    concentracao: "/inteligencia?brands=barbours#concentracao",
    ltv: "/inteligencia?brands=barbours#ltv",
    produto_tiktok: "/inteligencia?brands=barbours#produtos-tiktok",
  };
  for (const focus of ARRIVAL_FOCUSES) {
    const canal = focus === "produto_tiktok" ? "tiktok" : "ml";
    const ctx = parseBrandArrivalContext(
      params(`ctx_from=inteligencia&ctx_focus=${focus}&ctx_channel=${canal}&ctx_brand=barbours`),
      "barbours", ALL,
    )!;
    const href = buildReturnHref(ctx);
    assert.equal(href, esperado[focus], focus);
    assert.doesNotMatch(href, /ctx_/, `${focus}: voltar não é chegar quente`);
  }
});

test("V32 as âncoras de retorno existem de fato na Inteligência", () => {
  const lens = read("src/lib/inteligencia/lens.ts");
  const intel = read("app/inteligencia/page.tsx");
  for (const [chave, id] of [["concentracao", "concentracao"], ["ltv", "ltv"], ["produtosTiktok", "produtos-tiktok"], ["fila", "fila-evidencias"]]) {
    assert.match(lens, new RegExp(`${chave}: "${id}"`), id);
  }
  assert.match(intel, /id=\{INTELIGENCIA_ANCHORS\.produtosTiktok\}/, "a nova âncora está montada");
});

test("V32 o retorno da Inteligência não passa por mergeFilteredHref", () => {
  // `/inteligencia` não é filter-aware, e mergeFilteredHref destruiria a âncora
  const banner = read("src/components/BrandArrivalBanner.tsx");
  assert.match(banner, /returnPreservesGlobalFilters\(ctx\) \? buildHref\(buildReturnHref\(ctx\)\) : buildReturnHref\(ctx\)/);
  // prova do dano que isso evita
  const quebrado = mergeFilteredHref("/inteligencia?brands=barbours&lens=parar#fila-evidencias", new URLSearchParams(""));
  assert.doesNotMatch(quebrado, /#fila-evidencias$/, "a âncora seria perdida");
});

test("V32 trocar de marca ou navegar pela sidebar descarta o contexto", () => {
  // o parser recusa contexto de outra marca
  assert.equal(parseBrandArrivalContext(params(CANAIS_OK), "barbours", ALL), null);
  // ctx_* fora de FILTER_QUERY_KEYS ⇒ sidebar e pills não propagam
  for (const k of ["ctx_from", "ctx_signal", "ctx_focus", "ctx_channel", "ctx_brand"]) {
    assert.ok(!FILTER_QUERY_KEYS.includes(k), k);
  }
  // e o href dos pills não monta nenhum ctx_*
  const pills = PAGINA.slice(PAGINA.indexOf('aria-label="Selecionar marca"'), PAGINA.indexOf("</nav>"));
  assert.doesNotMatch(pills, /ctx_/);
  // a competência, sim, é preservada na troca de marca
  assert.match(pills, /\$\{REF_MONTH_QUERY_KEY\}=\$\{refMonthUrl\}/);
});

test("V32 os produtores emitem foco só quando o dado o demonstra", () => {
  // lente → foco: mapeamento exato das três listas do payload
  assert.equal(focusForEvidenceKind("parar"), "desperdicio_ads");
  assert.equal(focusForEvidenceKind("escalar"), "escala_ads");
  assert.equal(focusForEvidenceKind("testar"), "venda_organica");
  // quadrante → foco: só `escalar`, por identidade de população
  assert.equal(focusForQuadrant("escalar"), "escala_ads");
  for (const q of ["reduzir_parar", "monitorar", "testar_investimento", "qualquer_outro"]) {
    assert.equal(focusForQuadrant(q), null, `${q} não sustenta foco — CTA frio`);
  }
});

test("V32 Canais e Inteligência são os únicos produtores; a Marca, a única consumidora", () => {
  const canais = read("src/components/ChannelComparisonDialogContent.tsx");
  assert.match(canais, /buildArrivalParams\(row\.signals, row\.channel, row\.brand\)/);

  const intel = read("app/inteligencia/page.tsx");
  assert.match(intel, /buildInteligenciaArrivalParams\(focusForEvidenceKind\(dialog\.item\.kind\), dialog\.item\.brand\)/);
  assert.match(intel, /buildInteligenciaArrivalParams\(focusForQuadrant\(h\.quadrant\), h\.brand\)/);
  assert.match(intel, /buildInteligenciaArrivalParams\("ltv", r\.brand\)/);

  assert.match(PAGINA, /parseBrandArrivalContext\(searchParams, brand, filters\.channels\)/);
  assert.match(PAGINA, /<BrandArrivalBanner ctx=\{arrivalCtx\}/);

  // nenhuma outra tela produz nem consome ctx_*
  for (const f of ["app/page.tsx", "app/canais/page.tsx", "app/financeiro/page.tsx", "app/produtos/page.tsx", "src/lib/filters/nav-links.ts"]) {
    assert.doesNotMatch(read(f), /ctx_from|ctx_signal|ctx_focus|ctx_channel|ctx_brand/, f);
  }
});

test("V32 o módulo de contexto e o banner seguem sem fetch e sem shell próprio", () => {
  for (const src of [CTX, read("src/components/BrandArrivalBanner.tsx")]) {
    assert.doesNotMatch(src, /\bfetch\s*\(/);
    assert.doesNotMatch(src, /createPortal|role="dialog"/);
  }
});

// ═══════════════════════════════════════════════════════════════════════════
// 7. Drill-downs mensais
// ═══════════════════════════════════════════════════════════════════════════

const CANAL = {
  channel: "VIDEO", label: "Vídeo", impressions: 1000, page_views: 0,
  items_sold: 5, gmv: 250, ctr_pct: null, cvr_pct: 0,
};
const PRODUTO = {
  product_id: "p1", product_name: "Produto A", gmv: 0, orders: 0, videos: 3, gpm: null,
};

test("V32 detalhe de canal: só o próprio canal, e null ≠ zero", () => {
  const d = readChannelDetail(CANAL, "2026-08");
  assert.equal(d.label, "Vídeo");
  assert.equal(d.refMonthLabel, "ago/2026");
  const m = Object.fromEntries(d.metrics.map((x) => [x.key, x.value]));
  assert.deepEqual(m.impressions, { kind: "value", n: 1000 });
  assert.deepEqual(m.page_views, { kind: "value", n: 0 }, "zero medido é valor");
  assert.deepEqual(m.ctr_pct, { kind: "missing" }, "null é ausência");
  assert.deepEqual(m.cvr_pct, { kind: "value", n: 0 }, "CVR zero não é ausência");
  // ordem do funil, nada além do contrato do canal
  assert.deepEqual(d.metrics.map((x) => x.key), ["impressions", "ctr_pct", "page_views", "cvr_pct", "items_sold", "gmv"]);
});

test("V32 a copy do detalhe fala de SUPERFÍCIE, e o CTA não mente sobre o destino", () => {
  const d = readChannelDetail(CANAL, "2026-08");
  // vídeo/live/product card são superfícies do TikTok, não marketplaces
  assert.match(d.note, /superfície/);
  assert.doesNotMatch(d.note, /entre canais/, "não existe 'benchmark entre canais' aqui");
  assert.equal(d.ctaLabel, "Abrir TikTok Shop em Canais");
  assert.doesNotMatch(d.ctaLabel, /[Cc]omparar canais/, "o destino não compara superfícies");
  // e a ressalva declara o que NÃO viaja
  assert.match(CHANNEL_CTA_SCOPE_NOTE, /superfície específica não viaja/);
  assert.match(CHANNEL_CTA_SCOPE_NOTE, /vídeo × live × product card/);
  assert.match(PAGINA, /CHANNEL_CTA_SCOPE_NOTE/, "a ressalva é renderizada");
  // o destino real não carrega dimensão de superfície, e nenhum parâmetro novo nasceu
  assert.equal(channelCtaHref("barbours"), "/canais?brands=barbours&channels=tiktok");
  assert.doesNotMatch(DRILL, /surface=|superficie=|channel_surface/);
  // a tela também usa "superfície" nos rótulos do funil
  assert.match(PAINEL, /Funil por superfície/);
  assert.match(PAINEL, /label="Superfície"/);
});

test("V32 detalhe de canal não tem benchmark, mediana nem p75 calculados", () => {
  const d = readChannelDetail(CANAL, "2026-08");
  // nenhuma métrica de comparação entre canais no conteúdo
  for (const chave of d.metrics.map((x) => x.key)) {
    assert.doesNotMatch(chave, /median|mediana|p75|benchmark|share|avg|media/, chave);
  }
  // e a limitação explica POR QUE não há
  assert.match(d.note, /superfícies heterogêneas/);
  assert.match(d.note, /sem regra de negócio documentada/);
  assert.match(d.note, /benchmark entre superfícies/);
  // o módulo não faz aritmética de comparação
  const corpo = DRILL.slice(DRILL.indexOf("export function readChannelDetail"));
  assert.doesNotMatch(corpo.slice(0, corpo.indexOf("\n}")), /[+\-*/]\s*row\./);
});

test("V32 detalhe de produto: só o que o contrato entrega", () => {
  const d = readProductDetail(PRODUTO, "2026-08");
  assert.equal(d.productName, "Produto A");
  const m = Object.fromEntries(d.metrics.map((x) => [x.key, x.value]));
  assert.deepEqual(m.gmv, { kind: "value", n: 0 }, "GMV zero é medida");
  assert.deepEqual(m.orders, { kind: "value", n: 0 });
  assert.deepEqual(m.videos, { kind: "value", n: 3 });
  assert.deepEqual(m.gpm, { kind: "missing" }, "GPM null é ausência, não zero");
  assert.deepEqual(d.metrics.map((x) => x.key), ["gmv", "orders", "videos", "gpm"]);
  // nada de Ads, ROAS, margem ou CMV
  assert.match(d.note, /Sem Ads, ROAS, margem ou CMV/);
  // as METRICAS nao mencionam nada disso — a nota, sim, porque ela NEGA
  for (const x of d.metrics) {
    assert.doesNotMatch(`${x.key} ${x.label}`, /roas|acos|margem|cmv|ads/i, x.key);
  }
});

test("V32 metricValue: zero, negativo e NaN tratados corretamente", () => {
  assert.deepEqual(metricValue(0), { kind: "value", n: 0 });
  assert.deepEqual(metricValue(-5), { kind: "value", n: -5 });
  assert.deepEqual(metricValue(null), { kind: "missing" });
  assert.deepEqual(metricValue(undefined), { kind: "missing" });
  assert.deepEqual(metricValue(Number.NaN), { kind: "missing" }, "NaN não é número exibível");
});

test("V32 CTAs dos detalhes preservam marca/canal e não mentem sobre escopo", () => {
  assert.equal(channelCtaHref("barbours"), "/canais?brands=barbours&channels=tiktok");
  assert.equal(productCtaHref("barbours"), "/produtos?channels=tiktok&brands=barbours");
  // nenhum dos dois carrega competência...
  assert.doesNotMatch(channelCtaHref("barbours"), /ref_month/);
  assert.doesNotMatch(productCtaHref("barbours"), /ref_month/);
  // ...e o detalhe de produto DIZ isso, porque /produtos não consome ref_month
  assert.match(PRODUCT_CTA_SCOPE_NOTE, /não viaja/);
  assert.match(PAGINA, /PRODUCT_CTA_SCOPE_NOTE/);
  const produtosUrl = read("src/lib/produtos-url.ts");
  assert.doesNotMatch(produtosUrl, /ref_month/, "a premissa da ressalva segue verdadeira");
  // e os dois passam pelo buildHref da página (filtros preservados)
  assert.match(PAGINA, /buildHref\(channelCtaHref\(brand\)\)/);
  assert.match(PAGINA, /buildHref\(productCtaHref\(brand\)\)/);
});

test("V32 um único shell de diálogo, e um único diálogo por vez", () => {
  // só o shell canônico portaliza / declara role=dialog
  assert.match(read("src/components/KpiDrilldownDialog.tsx"), /createPortal/);
  for (const src of [PAGINA, PAINEL]) {
    assert.doesNotMatch(src, /createPortal|role="dialog"|aria-modal/);
  }
  assert.equal((PAGINA.match(/<KpiDrilldownDialog/g) ?? []).length, 1, "um shell só");
  // estado único e discriminado ⇒ nunca dois abertos
  assert.match(PAGINA, /const \[dialog, setDialog\] = useState<BrandDialog \| null>\(null\)/);
  assert.match(PAGINA, /open=\{dialog != null\}/);
  // e as primitives do G2 são reutilizadas, não recriadas
  for (const p of ["DrilldownContextLine", "DataQualityNote", "DrilldownCta", "EvidenceRow"]) {
    assert.match(PAGINA, new RegExp(p), p);
  }
});

test("V32 nenhuma linha inteira é clicável, e nenhuma linha sem ação tem hover", () => {
  for (const [nome, src] of [["page", PAGINA], ["panel", PAINEL]] as const) {
    assert.doesNotMatch(src, /<tr[^>]*onClick/, `${nome}: linha clicável`);
    assert.doesNotMatch(src, /<tr[^>]*hover:/, `${nome}: hover de linha sem ação`);
    assert.doesNotMatch(src, /<tr[^>]*cursor-pointer/, nome);
  }
  // o acionamento é sempre um botão com nome acessível e alvo >= 44px
  assert.match(PAINEL, /aria-label=\{`Detalhe do funil da superfície \$\{ch\.label\} em \$\{competencia\}`\}/);
  assert.match(PAINEL, /aria-label=\{`Detalhe do produto \$\{p\.product_name\} em \$\{competencia\}`\}/);
  assert.equal((PAINEL.match(/min-h-11 min-w-11/g) ?? []).length >= 3, true, "alvos de 44px nos acionadores");
});

// ═══════════════════════════════════════════════════════════════════════════
// 8. Remoção de conteúdo enganoso, acessibilidade, tipografia
// ═══════════════════════════════════════════════════════════════════════════

test("V32 Demographics foi removido e não deixou card vazio", () => {
  for (const [nome, src] of [["page", PAGINA_CODIGO], ["panel", PAINEL_CODIGO]] as const) {
    // o componente, os campos e o cabeçalho da seção não existem mais.
    // A PALAVRA continua existindo — dentro da nota que explica a remoção —, e
    // essa é a diferença entre apagar o card e apagar a informação.
    assert.doesNotMatch(src, /DemoBar|viewers_pct_|followers_pct_/, nome);
    assert.doesNotMatch(src, /<h\d[^>]*>Demographics/, `${nome}: nenhuma seção Demographics`);
    assert.doesNotMatch(src, /Sem dados de viewers|Sem dados de followers/, nome);
  }
  // no lugar, uma nota compacta DENTRO do contêiner mensal
  assert.match(PAINEL, /Limitações do dado nesta competência/);
  assert.match(PAINEL, /Audiência não é coberta/);
  assert.match(PAINEL, /100% nulas na fonte/);
});

test("V32 nada de Ads, margem, afiliado, benchmark, ranking novo ou previsão na Marca", () => {
  const proibido = /afiliad|margem_|share de atribui|benchmark competitiv|previs[ãa]o|forecast/i;
  for (const [nome, src] of [["page", PAGINA_CODIGO], ["panel", PAINEL_CODIGO], ["drilldown", DRILL_CODIGO]] as const) {
    assert.doesNotMatch(src, proibido, nome);
  }
  // o único "Ad Spend" é o KPI global preexistente, com N/D declarado no TikTok
  assert.match(PAGINA, /N\/D para TikTok Shop/);
  // o contêiner mensal não RENDERIZA Ads nem ROAS. A nota de limitação menciona
  // os dois para dizer que não existem — negar não é prometer.
  assert.doesNotMatch(PAINEL_CODIGO, /label="Ad Spend"|label="ROAS"/);
  assert.doesNotMatch(PAINEL_CODIGO, /detail\.(ad_spend|roas|ad_roas)/);
});

test("V32 nenhuma métrica comercial é recalculada no frontend", () => {
  // as taxas exibidas vêm prontas: nenhuma divisão sobre campos do contrato
  assert.doesNotMatch(PAINEL_CODIGO, /\/\s*detail\.(total_views|impressions|page_views)/);
  // `=(?!=)` é atribuição; `==` é comparação com null, que é justamente o que
  // se quer ver aqui. Sem a guarda, `detail.gpm == null` reprovaria.
  assert.doesNotMatch(PAINEL_CODIGO, /(ctr_pct|cvr_pct|gpm)\s*=(?!=)/);
  // a única divisão da página é ticket médio = gmv/orders, guardada por > 0
  assert.match(PAINEL, /detail\.orders > 0 \? fmtBrl\(detail\.gmv \/ detail\.orders\) : "N\/D"/);
});

test("V32 estados, aria e headings coerentes", () => {
  assert.match(PAGINA, /aria-busy=\{!dailyIsFresh\}/);
  assert.match(PAINEL, /aria-busy=\{state === "loading"\}/);
  assert.match(PAINEL, /aria-label=\{`TikTok Shop, análise mensal da competência \$\{competencia\}`\}/);
  // headings descem sem pular nível: h2 no contêiner, h3 nos blocos, h4 nos cards
  // o nível e o peso importam; o TOM é decisão de paleta e não deve travar o teste
  assert.match(PAINEL, /<h2 className="text-sm font-bold text-violet-\d00">/);
  assert.match(PAINEL, /<h3 className="text-sm font-semibold text-slate-700">\{titulo\}<\/h3>/);
  // toda tabela tem nome acessível e cabeçalho com scope
  assert.equal((PAINEL.match(/aria-label=\{`(Funil|Top 5)/g) ?? []).length >= 3, true);
  assert.doesNotMatch(PAINEL, /<th className=/, "todo th declara scope");
});

test("V32 piso tipográfico de 12px nas superfícies do gate", () => {
  for (const [nome, src] of [["page", PAGINA], ["panel", PAINEL]] as const) {
    for (const m of src.matchAll(/text-\[(\d+(?:\.\d+)?)px\]/g)) {
      assert.ok(Number(m[1]) >= 12, `${nome} renderiza texto a ${m[1]}px`);
    }
    assert.doesNotMatch(src, /text-\[10px\]|text-\[11px\]/, nome);
  }
});

test("V32 tabela rolável tem TableScrollHint e o layout não estoura", () => {
  assert.equal((PAINEL.match(/<TableScrollHint>/g) ?? []).length, 3, "funil, creators e produtos");
  assert.match(PAGINA, /<TableScrollHint>/, "últimos 7 dias");
  // nada de largura fixa que force scroll horizontal de página
  for (const src of [PAGINA, PAINEL]) {
    assert.doesNotMatch(src, /w-\[\d{4,}px\]|min-w-\[\d{4,}px\]/);
  }
});

test("V32 nenhum endpoint ou fetch novo, nenhuma dependência nova", () => {
  // a página mantém exatamente os dois fetches que já existiam
  assert.equal((PAGINA.match(/await fetch\(/g) ?? []).length, 1, "o fetch diário direto, um só");
  assert.equal((PAGINA.match(/fetchBrandDetail\(/g) ?? []).length, 1, "uma única chamada mensal");
  assert.doesNotMatch(PAINEL, /\bfetch\s*\(|fetchBrandDetail/, "o painel não busca nada");
  for (const src of [PAINEL, DRILL, read("src/lib/brand/ref-month.ts"), read("src/lib/brand/period-changes.ts")]) {
    assert.doesNotMatch(src, /from "(?!@\/|\.)/, "nenhum import de pacote externo novo");
  }
});

test("V32 package.json mudou apenas para registrar o teste novo", () => {
  const pkg = JSON.parse(read("package.json")) as {
    dependencies: Record<string, string>;
    devDependencies: Record<string, string>;
    scripts: Record<string, string>;
  };
  assert.match(pkg.scripts.test, /tests\/marca-360-v32\.test\.ts/);
  // as dependências continuam as sete + oito de desenvolvimento
  assert.equal(Object.keys(pkg.dependencies).length, 7);
  assert.equal(Object.keys(pkg.devDependencies).length, 8);
  assert.ok(!("@testing-library/react" in pkg.devDependencies));
});
// ═══════════════════════════════════════════════════════════════════════════
// 9. Rodada de correção terminal — os cinco findings corrigidos
// ═══════════════════════════════════════════════════════════════════════════

// ── FINDING 1: available_months vazio nunca fabrica `ready` com zeros ─────

/** Payload que o backend devolve para marca SEM histórico: 200, agregados
 * zerados, `available_months` vazio. É exatamente o que enganava o wiring. */
const PAYLOAD_ZERADO = {
  ref_month: "2026-08", gmv: 0, orders: 0, customers: 0,
  channel_funnel: [] as unknown[], daily: [] as unknown[],
  top_creators: [] as unknown[], top_produtos: [] as unknown[],
  available_months: [] as string[],
};

test("F1 resposta fresca com available_months vazio produz EMPTY, nunca ready", () => {
  const r = resolveRefMonth(null, PAYLOAD_ZERADO.available_months);
  assert.equal(r.hasAvailable, false);
  assert.equal(r.available, false, "sem lista, nenhuma competência 'consta'");
  const estado = monthlyViewState({ loading: false, error: false, monthAvailable: r.available });
  assert.equal(estado, "empty", "os zeros do payload NÃO viram indicadores");
});

test("F1 o furo `available || !hasAvailable` não existe mais em ponto algum", () => {
  assert.doesNotMatch(PAGINA, /!\s*resolucao\.hasAvailable/);
  assert.doesNotMatch(PAGINA, /resolucao\.available \|\|/);
  assert.match(PAGINA, /monthAvailable: resolucao\.available,/, "a pergunta é só uma");
});

test("F1 nenhuma métrica do payload zerado é renderizável no estado empty", () => {
  // os blocos de indicador, funil, conteúdo e produtos vivem TODOS dentro do
  // mesmo guarda `state === "ready" && detail`
  assert.match(PAINEL, /\{state === "ready" && detail && \(/);
  const readyIdx = PAINEL.indexOf('state === "ready" && detail');
  for (const marcador of [
    "Indicadores da competência", "Mix de superfície", "Funil por superfície",
    "Conteúdo e creators", "Produtos do TikTok Shop", "<ChannelMixChart",
  ]) {
    const at = PAINEL.indexOf(marcador);
    assert.ok(at > readyIdx, `${marcador} precisa estar dentro do ramo ready`);
  }
  // e o vazio sem histórico diz explicitamente por que não mostra zero
  assert.match(PAINEL, /zero agregado de um\s+histórico inexistente não é venda zero/);
});

test("F1 competência explícita fora da lista continua empty com o mês preservado", () => {
  const r = resolveRefMonth("2026-01", ["2026-07", "2026-06"]);
  assert.equal(r.month, "2026-01", "o mês pedido é preservado");
  assert.equal(r.hasAvailable, true, "a marca TEM histórico — outro vazio");
  assert.equal(monthlyViewState({ loading: false, error: false, monthAvailable: r.available }), "empty");
});

test("F1 competência na lista produz ready", () => {
  const r = resolveRefMonth("2026-07", ["2026-07", "2026-06"]);
  assert.equal(monthlyViewState({ loading: false, error: false, monthAvailable: r.available }), "ready");
});

test("F1 primeira carga pendente é LOADING, nunca empty prematuro", () => {
  // antes da resposta: lista vazia, mês null, mas a requisição está em curso
  const r = resolveRefMonth(null, []);
  assert.equal(
    monthlyViewState({ loading: true, error: false, monthAvailable: r.available }),
    "loading",
    "ausência não resolvida não é ausência de dado",
  );
  // e a chave ainda não resolvida também mantém loading (frame anterior ao efeito)
  assert.match(PAGINA, /loading: detailStatus\.loading,/);
});

test("F1 o vazio sem histórico tem copy própria e não oferece outro mês", () => {
  assert.match(PAGINA, /hasHistory=\{resolucao\.hasAvailable\}/);
  assert.match(PAINEL, /hasHistory: boolean/);
  // sem lista, o seletor não é renderizado
  assert.match(PAINEL, /\{months\.length > 0 && refMonth != null && \(/);
});

test("F1 sem competência na URL, o vazio é nomeado pelo ref_month ecoado", () => {
  assert.match(PAGINA, /servedMonth=\{disponibilidade\?\.servedMonth \?\? null\}/);
  assert.match(PAGINA, /servedMonth: d\.ref_month \?\? null,/);
  assert.match(PAINEL, /fmtCompetencia\(refMonth \?\? servedMonth\)/);
  // e o rótulo do mês servido é competência de verdade, não travessão
  assert.equal(fmtCompetencia(PAYLOAD_ZERADO.ref_month), "ago/2026");
});

// ── FINDING 2: falha de leitura é erro, com retry ────────────────────────

test("F2 `null` de apiFetch é FALHA e vira error com retry", () => {
  // o contrato real: apiFetch devolve null para não-2xx, rede, JSON inválido e
  // exceção capturada — nenhum desses é "concluiu sem payload"
  const client = read("src/lib/api-client.ts");
  const corpo = client.slice(client.indexOf("async function apiFetch"));
  const fn = corpo.slice(0, corpo.indexOf("\n}"));
  assert.match(fn, /if \(!res\.ok\) return null;/);
  assert.match(fn, /catch \{\s*return null;/);

  // a página trata isso como erro, no MESMO ramo `.then`
  const efeito = PAGINA.slice(PAGINA.indexOf("fetchBrandDetail(brand, refMonth"));
  const bloco = efeito.slice(0, efeito.indexOf("}, [brand, refMonth, detailRetry])"));
  assert.match(bloco, /\} else \{[\s\S]*?setDetailError\(true\);/, "d === null ⇒ error");
  // e a chave é registrada e o loading encerrado nos dois desfechos
  assert.equal((bloco.match(/setResolvedDetailKey\(key\)/g) ?? []).length, 2);
  assert.equal((bloco.match(/setDetailLoading\(false\)/g) ?? []).length, 2);
  // o estado de erro oferece retry, e o retry muda a chave de tentativa
  assert.match(PAGINA, /onRetry=\{\(\) => setDetailRetry\(\(n\) => n \+ 1\)\}/);
  assert.match(PAGINA, /\[brand, refMonth, detailRetry\]/, "o retry re-executa o efeito");
});

test("F2 o retry volta à rede porque o PF1 não cacheia null", () => {
  // premissa do retry: `withCache` não guarda falha. Se isso mudar, o retry
  // passaria a servir a mesma falha do cache — daí o teste de premissa.
  const client = read("src/lib/api-client.ts");
  assert.match(client, /export function isCacheableApiResult/);
  const pred = client.slice(client.indexOf("export function isCacheableApiResult"));
  assert.match(pred.slice(0, pred.indexOf("\n}")), /=== null|== null/);
  assert.match(client, /brand-detail:\$\{brand\}:\$\{month\}/, "a chave inclui a competência");
});

test("F2 nenhuma requisição automática extra e nenhum endpoint novo", () => {
  assert.equal((PAGINA.match(/fetchBrandDetail\(/g) ?? []).length, 1);
  assert.equal((PAGINA.match(/await fetch\(/g) ?? []).length, 1);
  assert.doesNotMatch(PAGINA, /setInterval|setTimeout\(/, "nenhum polling");
  // e a tela não afirma tratamento de timeout, que apiFetch não distingue
  assert.doesNotMatch(PAINEL, /timeout|tempo esgotado/i);
});

// ── FINDING 3: available_months com identidade de marca ──────────────────

const DISP: MonthAvailability = { brand: "barbours", months: ["2026-07", "2026-06"], servedMonth: "2026-07" };

test("F3 a lista da Barbours nunca aparece na Kokeshi", () => {
  assert.equal(availabilityForBrand(DISP, "barbours"), DISP);
  assert.equal(availabilityForBrand(DISP, "kokeshi"), null, "outra marca não vê a lista");
  assert.deepEqual(monthOptions(availabilityForBrand(DISP, "kokeshi")?.months ?? []), []);
});

test("F3 troca de marca durante loading não exibe o seletor antigo", () => {
  // sem disponibilidade aplicável, não há meses e o seletor não renderiza
  const meses = availabilityForBrand(DISP, "kokeshi")?.months ?? [];
  assert.equal(meses.length, 0);
  assert.equal(monthlyViewState({ loading: true, error: false, monthAvailable: false }), "loading");
  assert.match(PAINEL, /\{months\.length > 0 && refMonth != null && \(/);
});

test("F3 troca de marca com falha não exibe meses da marca anterior", () => {
  // a falha NÃO sobrescreve a disponibilidade; o portão de marca é que filtra
  const efeito = PAGINA.slice(PAGINA.indexOf("fetchBrandDetail(brand, refMonth"));
  const bloco = efeito.slice(0, efeito.indexOf("}, [brand, refMonth, detailRetry])"));
  assert.equal((bloco.match(/setAvailability\(/g) ?? []).length, 1, "só a resposta real grava");
  assert.match(bloco, /if \(d\) \{[\s\S]*?setAvailability\(\{/);
  // e o consumo é sempre pela marca da rota
  assert.match(PAGINA, /availabilityForBrand\(availability, brand\)/);
  assert.equal(availabilityForBrand(DISP, "kokeshi"), null);
});

test("F3 retry da MESMA marca continua enxergando a própria lista", () => {
  assert.equal(availabilityForBrand(DISP, "barbours")?.months.length, 2);
  // a lista sobrevive porque a falha não a apaga e a marca não mudou
  assert.match(PAGINA, /setAvailability\(\{\s*brand,/);
});

test("F3 ref_month escolhido explicitamente continua no href dos pills", () => {
  const pills = PAGINA.slice(PAGINA.indexOf('aria-label="Selecionar marca"'), PAGINA.indexOf("</nav>"));
  assert.match(pills, /\$\{REF_MONTH_QUERY_KEY\}=\$\{refMonthUrl\}/);
  assert.doesNotMatch(pills, /ctx_/, "ctx_* continua descartado");
});

test("F3 nenhuma lista de meses solta sobrou no estado da página", () => {
  assert.doesNotMatch(PAGINA, /useState<string\[\]>/);
  assert.doesNotMatch(PAGINA, /setAvailableMonths/);
});

// ── FINDING 4: chave estrangeira rejeitada por PRESENÇA ──────────────────

test("F4 canais rejeita qualquer ocorrência de ctx_focus", () => {
  const base = "ctx_from=canais&ctx_signal=custo_alto&ctx_channel=ml&ctx_brand=kokeshi";
  assert.ok(parseBrandArrivalContext(params(base), "kokeshi", ALL), "controle: válido isolado");
  for (const estrangeira of ["ctx_focus=ltv", "ctx_focus=ltv&ctx_focus=escala_ads", "ctx_focus=", "ctx_focus=xyz"]) {
    assert.equal(
      parseBrandArrivalContext(params(`${base}&${estrangeira}`), "kokeshi", ALL),
      null,
      estrangeira,
    );
  }
});

test("F4 inteligência rejeita qualquer ocorrência de ctx_signal", () => {
  const base = "ctx_from=inteligencia&ctx_focus=ltv&ctx_channel=ml&ctx_brand=barbours";
  assert.ok(parseBrandArrivalContext(params(base), "barbours", ALL), "controle: válido isolado");
  for (const estrangeira of ["ctx_signal=custo_alto", "ctx_signal=a&ctx_signal=b", "ctx_signal=", "ctx_signal=xyz"]) {
    assert.equal(
      parseBrandArrivalContext(params(`${base}&${estrangeira}`), "barbours", ALL),
      null,
      estrangeira,
    );
  }
});

test("F4 a guarda é de PRESENÇA, distinta de 'valor único válido'", () => {
  // o furo antigo: `readSingle(x) != null` devolve null para repetido, então a
  // chave estrangeira REPETIDA passava. `hasParam` conta ocorrências.
  assert.match(CTX, /function hasParam\(params: ParamReader, key: string\): boolean/);
  assert.match(CTX, /return params\.getAll\(key\)\.length > 0;/);
  assert.match(CTX, /if \(hasParam\(params, "ctx_focus"\)\) return null;/);
  assert.match(CTX, /if \(hasParam\(params, "ctx_signal"\)\) return null;/);
  assert.doesNotMatch(CTX, /readSingle\(params, "ctx_signal"\) != null/, "o teste furado saiu");
});

test("F4 parâmetro próprio repetido continua inválido, e os válidos continuam válidos", () => {
  assert.equal(
    parseBrandArrivalContext(params("ctx_from=canais&ctx_signal=custo_alto&ctx_signal=sem_dado&ctx_channel=ml&ctx_brand=kokeshi"), "kokeshi", ALL),
    null,
  );
  assert.equal(
    parseBrandArrivalContext(params("ctx_from=inteligencia&ctx_focus=ltv&ctx_focus=concentracao&ctx_channel=ml&ctx_brand=barbours"), "barbours", ALL),
    null,
  );
  // e os dois caminhos válidos seguem intactos
  const c = parseBrandArrivalContext(params("ctx_from=canais&ctx_signal=custo_alto&ctx_channel=ml&ctx_brand=kokeshi"), "kokeshi", ALL);
  assert.equal(c?.from, CTX_FROM_CANAIS);
  const i = parseBrandArrivalContext(params("ctx_from=inteligencia&ctx_focus=ltv&ctx_channel=ml&ctx_brand=barbours"), "barbours", ALL);
  assert.equal(i?.from, CTX_FROM_INTELIGENCIA);
});

test("F4 leitor sem getAll continua funcionando nas duas origens", () => {
  const simples = (qs: string): ParamReader => {
    const u = new URLSearchParams(qs);
    return { get: (k) => u.get(k) };
  };
  assert.ok(parseBrandArrivalContext(simples("ctx_from=canais&ctx_signal=custo_alto&ctx_channel=ml&ctx_brand=kokeshi"), "kokeshi", ALL));
  assert.ok(parseBrandArrivalContext(simples("ctx_from=inteligencia&ctx_focus=ltv&ctx_channel=ml&ctx_brand=barbours"), "barbours", ALL));
  // e a rejeição por presença também vale sem getAll
  assert.equal(
    parseBrandArrivalContext(simples("ctx_from=canais&ctx_signal=custo_alto&ctx_channel=ml&ctx_brand=kokeshi&ctx_focus=ltv"), "kokeshi", ALL),
    null,
  );
});
// ── FINDING V3-3: separador decimal pt-BR ────────────────────────────────

test("V33 nenhum numero da Marca 360 usa PONTO decimal", () => {
  // O QA visual do V3-3 mediu `CVR 0.00%` numa interface inteiramente pt-BR —
  // exatamente o defeito que o V3-1A ja havia fechado na Inteligencia com
  // `decBr`/`pctBr`. `toFixed` e insensivel a locale.
  for (const [nome, src] of [["page", PAGINA_CODIGO], ["panel", PAINEL_CODIGO]] as const) {
    assert.doesNotMatch(src, /\.toFixed\(/, `${nome}: toFixed reintroduz ponto decimal`);
  }
  assert.match(PAGINA, /import \{ decBr, pctBr, roasBr \} from "@\/lib\/inteligencia\/format"/);
  assert.match(PAINEL, /import \{ decBr, pctBr \} from "@\/lib\/inteligencia\/format"/);
  // os pontos exatos que o QA pegou
  assert.match(PAGINA, /if \(unit === "pct"\) return pctBr\(m\.n, 2\);/);
  assert.match(PAGINA, /if \(unit === "brl2"\) return `R\$ \$\{decBr\(m\.n, 2\)\}`;/);
  assert.match(PAINEL, /return v == null \? "Sem dado" : pctBr\(v, digits\);/);
});

test("V33 os formatadores pt-BR realmente produzem virgula", () => {
  assert.equal(pctBr(0, 2), "0,00%");
  assert.equal(pctBr(4, 2), "4,00%");
  assert.equal(decBr(2.35, 2), "2,35");
  assert.equal(roasBr(40.9), "40,9x");
  // e zero continua zero, nao travessao
  assert.doesNotMatch(pctBr(0, 2), /—|N\/D|Sem dado/);
});
