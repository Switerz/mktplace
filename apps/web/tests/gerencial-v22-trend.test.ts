/**
 * Gate V2-2, Task 1/2 — granularidade selecionavel e serie do periodo anterior.
 *
 * Logica PURA (`lib/gerencial/trend-series.ts`) + invariantes de wiring lidos do
 * codigo-fonte, no mesmo padrao dos gates anteriores.
 *
 * Os contratos que estes testes existem para travar:
 *
 * - a granularidade entra na identidade das SERIES e em mais nada;
 * - o alinhamento do periodo anterior e' por POSICAO ORDINAL, preservando a data
 *   e o rotulo reais dos dois lados;
 * - ausencia nunca vira zero, e total comparativo parcial nunca se passa por
 *   total completo;
 * - `compare=false` nao produz nenhuma interface comparativa;
 * - um backend AINDA sem o campo `comparison` continua funcionando.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import {
  bucketRange,
  mergeChannelSeries,
  reconcileSeriesTotal,
  type ChannelSeries,
  type ComparisonStatus,
} from "../src/lib/gerencial/trend-series.ts";
import { buildChannelSeriesKey, buildGerencialRequestKey, type GerencialKeyInput } from "../src/lib/gerencial/request-key.ts";
import type { TrendPoint } from "../src/lib/api-client.ts";
import type { Marketplace } from "../src/lib/mock-data.ts";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf-8");

function codeOnly(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .map((line) => {
      const t = line.trim();
      if (t.startsWith("//") || t.startsWith("*")) return "";
      return line.replace(/\/\/.*$/, "");
    })
    .join("\n");
}

const KEY: GerencialKeyInput = {
  channels: ["tiktok", "ml", "shopee"],
  brands: [],
  dateFrom: "2026-07-01",
  dateTo: "2026-07-31",
  compare: true,
  retryKey: 0,
};

function pt(date: string, gmv: number, orders = 1): TrendPoint {
  return { date, label: date.slice(8), gmv, orders };
}

/**
 * Janela comparativa default dos testes. Vem do CONTRATO — o helper a declara
 * explicitamente porque uma serie comparativa concluida SEM janela e' o caso
 * `unknown` (backend anterior ao contrato), coberto em teste proprio.
 */
const CMP_WINDOW = { from: "2026-06-01", to: "2026-06-30" };

function series(
  channel: Marketplace,
  points: TrendPoint[],
  opts: {
    status?: ChannelSeries["status"];
    granularity?: ChannelSeries["granularity"];
    comparisonStatus?: ComparisonStatus;
    comparisonPoints?: TrendPoint[];
    /** `null` explicito = concluiu sem declarar janela (backend antigo). */
    comparisonWindow?: { from: string; to: string } | null;
  } = {},
): ChannelSeries {
  const settled = opts.comparisonStatus === "fresh" || opts.comparisonStatus === "empty";
  const win = opts.comparisonWindow === undefined ? (settled ? CMP_WINDOW : null) : opts.comparisonWindow;
  return {
    channel,
    status: opts.status ?? "fresh",
    granularity: opts.granularity ?? "day",
    points,
    comparisonStatus: opts.comparisonStatus,
    comparisonPoints: opts.comparisonPoints,
    comparisonDateFrom: win?.from ?? null,
    comparisonDateTo: win?.to ?? null,
  };
}

// ---------------------------------------------------------------------------
// Identidade, cache e frescor
// ---------------------------------------------------------------------------

test("V22-1. a granularidade entra na chave da SERIE e em mais nada", () => {
  const global = buildGerencialRequestKey(KEY);
  assert.ok(!global.includes("g:"), "a chave global não pode carregar granularidade");

  const auto = buildChannelSeriesKey(KEY, "ml", "auto");
  const week = buildChannelSeriesKey(KEY, "ml", "week");
  assert.notEqual(auto, week, "granularidades diferentes => chaves de série diferentes");
  assert.ok(auto.endsWith("|g:auto"));
  assert.ok(week.endsWith("|g:week"));
  // o default reproduz o comportamento anterior ao gate
  assert.equal(buildChannelSeriesKey(KEY, "ml"), auto);
  // e canais diferentes continuam nunca colidindo
  assert.notEqual(buildChannelSeriesKey(KEY, "ml", "week"), buildChannelSeriesKey(KEY, "shopee", "week"));
});

test("V22-2. a granularidade entra no cache key do fetchTrend", () => {
  const client = codeOnly(read("src/lib/api-client.ts"));
  assert.match(
    client,
    /withCache\(`trend:\$\{granularity\}:\$\{qs\.toString\(\)\}`/,
    "pedir week depois de day no mesmo filtro não pode servir cache da série diária",
  );
  // e `auto` nao envia o parametro, preservando a URL antiga
  assert.match(client, /if \(granularity !== "auto"\) qs\.set\("granularity", granularity\);/);
});

test("V22-3. trocar a granularidade refaz SO' as series de /trend", () => {
  const hook = codeOnly(read("src/hooks/useGerencialSources.ts"));

  // O efeito das series depende da granularidade...
  const seriesEffect = hook.slice(hook.indexOf("for (const channel of channels)"), hook.indexOf("const demoDecision"));
  assert.match(seriesEffect, /input\.granularity/, "o efeito das séries usa a granularidade");
  assert.match(hook, /\}, \[requestKey, channelsKey, filterOpts, keyInput, input\.granularity\]\)/);

  // ...e os efeitos das outras cinco fontes NAO.
  for (const fetcher of ["fetchOverview", "fetchBrands", "fetchExecutiveSummary", "fetchCanais", "fetchQuality"]) {
    const at = hook.indexOf(fetcher + "(");
    assert.ok(at > 0, `${fetcher} deve existir`);
    const effect = hook.slice(at, hook.indexOf("}, [requestKey", at) + 60);
    assert.doesNotMatch(effect, /input\.granularity/, `${fetcher} não pode depender da granularidade`);
  }
});

test("V22-4. trocar a metrica continua sem fetch e no maximo 3 chamadas", () => {
  const hook = codeOnly(read("src/hooks/useGerencialSources.ts"));
  assert.doesNotMatch(hook, /metric/i, "o hook das fontes não conhece a métrica");
  // uma chamada por canal SELECIONADO, nunca uma quarta agregada
  assert.match(hook, /for \(const channel of channels\) \{/);
  assert.match(hook, /fetchTrend\(\[channel\], filterOpts, input\.granularity\)/);
  const page = codeOnly(read("app/page.tsx"));
  assert.match(page, /granularity,/, "a página passa a granularidade ao hook");
  assert.match(page, /onMetricChange=\{setMetric\}/);
});

test("V22-5. resposta obsoleta e descartada pela chave da serie", () => {
  const hook = codeOnly(read("src/hooks/useGerencialSources.ts"));
  assert.match(hook, /const channelKey = buildChannelSeriesKey\(keyInput, channel, input\.granularity\)/);
  assert.match(hook, /resolvedKey: channelKey/);
  assert.match(hook, /requestKey: channelKey/);
});

// ---------------------------------------------------------------------------
// Alinhamento ordinal da comparacao
// ---------------------------------------------------------------------------

test("V22-6. 31 buckets atuais x 28 anteriores: os tres ultimos ficam sem comparacao", () => {
  const atuais = Array.from({ length: 31 }, (_, i) => pt(`2026-07-${String(i + 1).padStart(2, "0")}`, 100 + i));
  const anteriores = Array.from({ length: 28 }, (_, i) => pt(`2026-06-${String(i + 1).padStart(2, "0")}`, 50 + i));
  const merged = mergeChannelSeries(
    [series("ml", atuais, { comparisonStatus: "fresh", comparisonPoints: anteriores })],
    ["ml"],
    "gmv",
  );

  assert.equal(merged.buckets.length, 31);
  const comparados = merged.buckets.filter((b) => b.comparison != null);
  assert.equal(comparados.length, 28, "só 28 posições têm par");
  for (const b of merged.buckets.slice(28)) {
    assert.equal(b.comparison, null, "sem par ordinal => null, nunca zero");
  }
  // o alinhamento e' ORDINAL e preserva as datas REAIS dos dois lados
  assert.equal(merged.buckets[0].date, "2026-07-01");
  assert.equal(merged.buckets[0].comparison!.date, "2026-06-01");
  assert.equal(merged.buckets[27].date, "2026-07-28");
  assert.equal(merged.buckets[27].comparison!.date, "2026-06-28");
});

test("V22-7. lacuna no lado anterior permanece null, e zero explicito permanece zero", () => {
  const merged = mergeChannelSeries(
    [
      series("ml", [pt("2026-07-01", 10), pt("2026-07-02", 20)], {
        comparisonStatus: "fresh",
        // zero EXPLICITO no primeiro; o segundo bucket anterior existe
        comparisonPoints: [pt("2026-06-01", 0, 0), pt("2026-06-02", 5)],
      }),
      series("shopee", [pt("2026-07-01", 1), pt("2026-07-02", 2)], {
        comparisonStatus: "fresh",
        // só tem o primeiro: a segunda posição fica sem valor para este canal
        comparisonPoints: [pt("2026-06-01", 3)],
      }),
    ],
    ["ml", "shopee"],
    "gmv",
  );

  assert.equal(merged.buckets[0].comparison!.values.ml, 0, "zero explícito não pode virar null");
  assert.equal(merged.buckets[1].comparison!.values.shopee, null, "ausência não pode virar zero");
  assert.equal(merged.buckets[0].comparison!.total, 3, "0 + 3");
  assert.equal(merged.buckets[1].comparison!.total, null, "bucket comparativo incompleto não tem total");
});

test("V22-8. total anterior existe SO' com todos os canais comparativos completos", () => {
  const completo = mergeChannelSeries(
    [
      series("ml", [pt("2026-07-01", 10)], { comparisonStatus: "fresh", comparisonPoints: [pt("2026-06-01", 8)] }),
      series("shopee", [pt("2026-07-01", 2)], { comparisonStatus: "fresh", comparisonPoints: [pt("2026-06-01", 1)] }),
    ],
    ["ml", "shopee"],
    "gmv",
  );
  assert.equal(completo.comparison.everyBucketComplete, true);
  assert.equal(completo.comparison.seriesTotal, 9);

  // um canal comparativo em erro: a serie ATUAL continua, o total anterior nao
  const parcial = mergeChannelSeries(
    [
      series("ml", [pt("2026-07-01", 10)], { comparisonStatus: "fresh", comparisonPoints: [pt("2026-06-01", 8)] }),
      series("shopee", [pt("2026-07-01", 2)], { comparisonStatus: "error" }),
    ],
    ["ml", "shopee"],
    "gmv",
  );
  assert.deepEqual(parcial.comparison.failedChannels, ["shopee"]);
  assert.equal(parcial.comparison.everyBucketComplete, false);
  assert.equal(parcial.comparison.seriesTotal, null, "total anterior parcial nunca é apresentado");
  // a serie atual sobreviveu intacta
  assert.deepEqual(parcial.availableChannels, ["ml", "shopee"]);
  assert.equal(parcial.buckets[0].total, 12, "o total ATUAL continua");
});

test("V22-9. comparacao solicitada sem registros e 'sem registros', nao R$ 0", () => {
  const merged = mergeChannelSeries(
    [series("ml", [pt("2026-07-01", 10)], { comparisonStatus: "empty", comparisonPoints: [] })],
    ["ml"],
    "gmv",
  );
  assert.equal(merged.comparison.requested, true);
  assert.deepEqual(merged.comparison.emptyChannels, ["ml"]);
  assert.deepEqual(merged.comparison.availableChannels, []);
  assert.equal(merged.comparison.seriesTotal, null, "sem registros não vira zero");
  assert.equal(merged.buckets[0].comparison, null, "sem bucket anterior, não há par");
});

test("V22-10. comparacao NAO solicitada nao produz estado de erro nem aviso", () => {
  const merged = mergeChannelSeries([series("ml", [pt("2026-07-01", 10)])], ["ml"], "gmv");
  assert.equal(merged.comparison.requested, false);
  assert.deepEqual(merged.comparison.failedChannels, []);
  assert.deepEqual(merged.comparison.loadingChannels, []);
  assert.deepEqual(merged.comparison.emptyChannels, []);
  assert.equal(merged.comparison.seriesTotal, null);
  assert.equal(merged.buckets[0].comparison, null);
});

test("V22-11. comparacao carregando e um estado proprio, distinto de erro", () => {
  const merged = mergeChannelSeries(
    [
      series("ml", [pt("2026-07-01", 10)], { comparisonStatus: "fresh", comparisonPoints: [pt("2026-06-01", 8)] }),
      series("shopee", [pt("2026-07-01", 2)], { comparisonStatus: "loading" }),
    ],
    ["ml", "shopee"],
    "gmv",
  );
  assert.deepEqual(merged.comparison.loadingChannels, ["shopee"]);
  assert.deepEqual(merged.comparison.failedChannels, []);
  assert.equal(merged.comparison.allChannelsFresh, false);
});

// ---------------------------------------------------------------------------
// Granularidade divergente: NAO se mescla (substitui a regra "a mais grossa
// vence", que nao tornava as series compativeis — apenas escondia o problema)
// ---------------------------------------------------------------------------

test("V22-12. day + week: mismatch declarado, ZERO merge", () => {
  const merged = mergeChannelSeries(
    [
      series("ml", [pt("2026-07-01", 10)], { granularity: "day" }),
      series("shopee", [pt("2026-06-29", 20)], { granularity: "week" }),
    ],
    ["ml", "shopee"],
    "gmv",
  );
  assert.equal(merged.granularityIssue?.kind, "channel_mismatch");
  assert.deepEqual(merged.buckets, [], "nenhum bucket mesclado entre grãos distintos");
  assert.equal(merged.seriesTotal, null);
  assert.equal(merged.everyBucketComplete, false);
  // quais canais e quais graos divergiram
  assert.deepEqual(merged.granularityIssue?.grains, [
    { channel: "ml", granularity: "day" },
    { channel: "shopee", granularity: "week" },
  ]);
  // e a reconciliacao nao finge que houve serie
  assert.equal(reconcileSeriesTotal(merged, "gmv", 30).status, "skipped");
});

test("V22-12b. week + month: mismatch", () => {
  const merged = mergeChannelSeries(
    [
      series("ml", [pt("2026-06-29", 1)], { granularity: "week" }),
      series("shopee", [pt("2026-07-01", 1)], { granularity: "month" }),
    ],
    ["ml", "shopee"],
    "gmv",
  );
  assert.equal(merged.granularityIssue?.kind, "channel_mismatch");
  assert.deepEqual(merged.buckets, []);
});

test("V22-12c. todos week (e todos day em auto) sao validos", () => {
  const semanal = mergeChannelSeries(
    [
      series("ml", [pt("2026-06-29", 1)], { granularity: "week" }),
      series("shopee", [pt("2026-06-29", 2)], { granularity: "week" }),
    ],
    ["ml", "shopee"],
    "gmv",
    "week",
  );
  assert.equal(semanal.granularityIssue, null);
  assert.equal(semanal.granularity, "week");
  assert.equal(semanal.buckets.length, 1);

  const auto = mergeChannelSeries(
    [
      series("ml", [pt("2026-07-01", 1)], { granularity: "day" }),
      series("shopee", [pt("2026-07-01", 2)], { granularity: "day" }),
    ],
    ["ml", "shopee"],
    "gmv",
    "auto",
  );
  assert.equal(auto.granularityIssue, null);
  assert.equal(auto.buckets[0].total, 3);
});

test("V22-12d. pedido explicito 'week' recebendo 'day' e erro CONTRATUAL visivel", () => {
  const merged = mergeChannelSeries(
    [series("ml", [pt("2026-07-01", 10)], { granularity: "day" })],
    ["ml"],
    "gmv",
    "week",
  );
  assert.equal(merged.granularityIssue?.kind, "unsupported_request");
  assert.equal(merged.granularityIssue?.requested, "week");
  assert.deepEqual(merged.buckets, [], "o seletor não pode parecer funcional com a escolha ignorada");
  // `auto` recebendo day continua valido — a regra automática é do backend
  const autoOk = mergeChannelSeries(
    [series("ml", [pt("2026-07-01", 10)], { granularity: "day" })],
    ["ml"],
    "gmv",
    "auto",
  );
  assert.equal(autoOk.granularityIssue, null);
});

test("V22-12e. o mismatch e local ao bloco: nao ha estado global de pagina", () => {
  const card = codeOnly(read("src/components/gerencial/EvolutionCard.tsx"));
  const page = codeOnly(read("app/page.tsx"));
  // o card tem estado proprio para o mismatch...
  assert.match(card, /Granularidades incompatíveis/);
  assert.match(card, /const grainIssue = merged\.granularityIssue;/);
  // ...e o mismatch NAO gateia matriz, pulso, movimentos ou fila
  assert.doesNotMatch(page, /granularityIssue/, "a página não pode ramificar blocos pelo mismatch");
  // a legenda tambem nao promete linhas que o grafico nao desenha
  assert.match(card, /!loading && !grainIssue && merged\.availableChannels\.length > 0/);
  // e o merge recebe a granularidade PEDIDA
  assert.match(page, /mergeChannelSeries\(sources\.series, filters\.channels, metric, granularity\)/);
});

// ---------------------------------------------------------------------------
// Janela comparativa: vem do CONTRATO, nunca dos buckets
// ---------------------------------------------------------------------------

test("V22-25. janela real 01-31/07 com buckets semanais desde 29/06: a UI informa 01-31/07", () => {
  const semanas = ["2026-06-29", "2026-07-06", "2026-07-13", "2026-07-20", "2026-07-27"];
  const merged = mergeChannelSeries(
    [
      series("ml", semanas.map((d, i) => pt(d, 100 + i)), {
        granularity: "week",
        comparisonStatus: "fresh",
        // buckets semanais anteriores começam ANTES da janela real
        comparisonPoints: ["2026-05-25", "2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22"].map((d, i) =>
          pt(d, 50 + i),
        ),
        comparisonWindow: { from: "2026-06-01", to: "2026-06-30" },
      }),
    ],
    ["ml"],
    "gmv",
    "week",
  );
  assert.equal(merged.comparison.windowIssue, null);
  assert.equal(merged.comparison.dateFrom, "2026-06-01", "não é o primeiro bucket (25/05)");
  assert.equal(merged.comparison.dateTo, "2026-06-30", "não é o último bucket (22/06)");
  // o primeiro bucket comparativo continua sendo 25/05 no PAR ordinal — o par
  // preserva a data real do bucket, e a janela e' outra coisa
  assert.equal(merged.buckets[0].comparison?.date, "2026-05-25");

  const src = codeOnly(read("src/lib/gerencial/trend-series.ts"));
  assert.doesNotMatch(src, /dateFrom: cmpDates\[0\]/, "a janela não pode voltar a sair dos buckets");
  assert.match(src, /dateFrom: cmpDateFrom,/);
});

test("V22-26. comparacao VAZIA mantem a janela real declarada pela API", () => {
  const merged = mergeChannelSeries(
    [
      series("ml", [pt("2026-07-01", 10)], {
        comparisonStatus: "empty",
        comparisonPoints: [],
        comparisonWindow: { from: "2026-06-01", to: "2026-06-30" },
      }),
    ],
    ["ml"],
    "gmv",
  );
  assert.equal(merged.comparison.dateFrom, "2026-06-01");
  assert.equal(merged.comparison.dateTo, "2026-06-30");
  assert.equal(merged.comparison.seriesTotal, null, "janela conhecida ≠ valores conhecidos");
  assert.deepEqual(merged.comparison.emptyChannels, ["ml"]);

  // e o card exibe a janela mesmo sem comparação completa
  const card = codeOnly(read("src/components/gerencial/EvolutionCard.tsx"));
  assert.match(card, /\{cmp\.dateFrom && cmp\.dateTo && \(/);
  assert.doesNotMatch(card, /showComparison && cmp\.dateFrom && cmp\.dateTo/);
});

test("V22-27. janelas divergentes entre canais: total anterior bloqueado e aviso explicito", () => {
  const merged = mergeChannelSeries(
    [
      series("ml", [pt("2026-07-01", 10)], {
        comparisonStatus: "fresh",
        comparisonPoints: [pt("2026-06-01", 8)],
        comparisonWindow: { from: "2026-06-01", to: "2026-06-30" },
      }),
      series("shopee", [pt("2026-07-01", 2)], {
        comparisonStatus: "fresh",
        comparisonPoints: [pt("2026-06-02", 1)],
        comparisonWindow: { from: "2026-06-02", to: "2026-07-01" },
      }),
    ],
    ["ml", "shopee"],
    "gmv",
  );
  assert.equal(merged.comparison.windowIssue, "inconsistent");
  assert.equal(merged.comparison.seriesTotal, null);
  assert.equal(merged.comparison.everyBucketComplete, false);
  assert.equal(merged.comparison.dateFrom, null, "não se escolhe uma das janelas divergentes");
  assert.equal(merged.buckets[0].comparison, null, "sem pareamento entre janelas diferentes");
  // as janelas de cada canal seguem disponíveis para NOMEAR a inconsistência
  assert.deepEqual(merged.comparison.windowsByChannel, [
    { channel: "ml", dateFrom: "2026-06-01", dateTo: "2026-06-30" },
    { channel: "shopee", dateFrom: "2026-06-02", dateTo: "2026-07-01" },
  ]);
  // série ATUAL preservada
  assert.equal(merged.buckets[0].total, 12);

  const card = codeOnly(read("src/components/gerencial/EvolutionCard.tsx"));
  assert.match(card, /Janelas comparativas divergentes entre canais/);
});

test("V22-28. metadados ausentes (backend antigo) = indisponibilidade, nao janela fabricada", () => {
  const merged = mergeChannelSeries(
    [
      series("ml", [pt("2026-07-01", 10)], {
        comparisonStatus: "fresh",
        comparisonPoints: [pt("2026-06-01", 8)],
        comparisonWindow: null, // concluiu sem declarar a janela
      }),
    ],
    ["ml"],
    "gmv",
  );
  assert.equal(merged.comparison.windowIssue, "unknown");
  assert.equal(merged.comparison.dateFrom, null);
  assert.equal(merged.comparison.dateTo, null);
  assert.equal(merged.comparison.seriesTotal, null);
  assert.equal(merged.buckets[0].comparison, null);
  assert.equal(merged.buckets[0].total, 10, "a série atual continua");

  const card = codeOnly(read("src/components/gerencial/EvolutionCard.tsx"));
  assert.match(card, /A API não declarou as datas da janela anterior/);
});

test("V22-29. a janela e transportada do contrato ate o hook, sem derivacao", () => {
  const client = codeOnly(read("src/lib/api-client.ts"));
  assert.match(client, /dateFrom: raw\.comparison\.date_from \?\? null/);
  assert.match(client, /dateTo: raw\.comparison\.date_to \?\? null/);

  const hook = codeOnly(read("src/hooks/useGerencialSources.ts"));
  assert.match(hook, /comparisonDateFrom: settled \? \(cmpOk\?\.dateFrom \?\? null\) : null/);
  assert.match(hook, /comparisonDateTo: settled \? \(cmpOk\?\.dateTo \?\? null\) : null/);
  // "settled" inclui a comparação VAZIA — janela conhecida sem registros
  assert.match(hook, /const settled = comparisonStatus === "fresh" \|\| comparisonStatus === "empty";/);

  // e o drill-down do ponto tambem mostra a janela do contrato
  const drills = read("src/components/gerencial/GerencialDrilldowns.tsx");
  assert.match(drills, /Janela anterior completa: \{merged\.comparison\.dateFrom\} a \{merged\.comparison\.dateTo\}/);
});

// ---------------------------------------------------------------------------
// bucketRange semanal
// ---------------------------------------------------------------------------

test("V22-13. bucket semanal vai de segunda a domingo", () => {
  const r = bucketRange("2026-06-29", "week");
  assert.equal(r.dateFrom, "2026-06-29");
  assert.equal(r.dateTo, "2026-07-05");
  assert.equal(r.label, "esta semana");
  assert.equal(r.clamped, false);
});

test("V22-14. primeira e ultima semana parciais sao cortadas pelo periodo global", () => {
  const bounds = { dateFrom: "2026-07-01", dateTo: "2026-07-31" };

  // a semana comeca em 29/06, antes do periodo
  const primeira = bucketRange("2026-06-29", "week", bounds);
  assert.equal(primeira.dateFrom, "2026-07-01", "não aplica data anterior ao filtro");
  assert.equal(primeira.dateTo, "2026-07-05");
  assert.equal(primeira.clamped, true);

  // a semana termina em 02/08, depois do periodo
  const ultima = bucketRange("2026-07-27", "week", bounds);
  assert.equal(ultima.dateFrom, "2026-07-27");
  assert.equal(ultima.dateTo, "2026-07-31", "não aplica data posterior ao filtro");
  assert.equal(ultima.clamped, true);

  // uma semana inteiramente dentro do periodo nao e' cortada
  const inteira = bucketRange("2026-07-06", "week", bounds);
  assert.deepEqual(
    { from: inteira.dateFrom, to: inteira.dateTo, clamped: inteira.clamped },
    { from: "2026-07-06", to: "2026-07-12", clamped: false },
  );
});

test("V22-15. semana atravessando a virada de ano, sem erro de fuso", () => {
  const r = bucketRange("2026-12-28", "week");
  assert.equal(r.dateFrom, "2026-12-28");
  assert.equal(r.dateTo, "2027-01-03");
  // o helper nao usa Date em nenhum ponto
  const src = codeOnly(read("src/lib/gerencial/trend-series.ts"));
  const fn = src.slice(src.indexOf("export function bucketRange"), src.indexOf("export function gmvTolerance"));
  assert.doesNotMatch(fn, /\bnew Date\b|\bDate\./, "bucketRange não pode usar Date");
});

test("V22-16. os contratos diario e mensal seguem intactos", () => {
  assert.deepEqual(bucketRange("2026-07-15", "day"), {
    dateFrom: "2026-07-15",
    dateTo: "2026-07-15",
    label: "este dia",
    clamped: false,
  });
  assert.equal(bucketRange("2026-02-01", "month").dateTo, "2026-02-28");
  assert.equal(bucketRange("2024-02-01", "month").dateTo, "2024-02-29");
  assert.equal(bucketRange("2100-02-01", "month").dateTo, "2100-02-28");
  assert.equal(bucketRange("2026-12-01", "month").dateTo, "2026-12-31");
});

// ---------------------------------------------------------------------------
// Compatibilidade retroativa e interface
// ---------------------------------------------------------------------------

test("V22-17. backend antigo: compare=true sem o campo comparison e 'unsupported', nao 'nao solicitado'", () => {
  const client = codeOnly(read("src/lib/api-client.ts"));
  // o campo continua OPCIONAL no schema (extensao aditiva)...
  assert.match(client, /comparison\?: \{/, "o campo tem de ser opcional");
  // ...mas a ausencia dele NAO e' mais lida como "nao solicitada": a intencao
  // do usuario (`compare`) e' o que distingue os dois casos.
  assert.match(client, /const compareRequested = filters\?\.compare === true;/);
  assert.match(
    client,
    /: \{ status: compareRequested \? \("unsupported" as const\) : \("not_requested" as const\) \}/,
  );
  // o fallback de API offline aplica a mesma distincao
  assert.match(client, /comparison: \{ status: compareRequested \? "unsupported" : "not_requested" \}/);

  // e o hook traduz isso para o estado do canal
  const hook = codeOnly(read("src/hooks/useGerencialSources.ts"));
  assert.match(hook, /cmp\.status === "unsupported"\) comparisonStatus = "unsupported"/);
  assert.doesNotMatch(hook, /comparisonStatus = "not_requested";\s*\n\s*else if \(cmp == null\)/);
});

test("V22-17b. 'unsupported' bloqueia o total anterior e preserva a serie atual", () => {
  const merged = mergeChannelSeries(
    [
      series("ml", [pt("2026-07-01", 10)], { comparisonStatus: "unsupported" }),
      series("shopee", [pt("2026-07-01", 2)], { comparisonStatus: "unsupported" }),
    ],
    ["ml", "shopee"],
    "gmv",
  );
  assert.equal(merged.comparison.requested, true, "o usuário pediu: não é 'não solicitada'");
  assert.deepEqual(merged.comparison.unsupportedChannels, ["ml", "shopee"]);
  assert.equal(merged.comparison.seriesTotal, null);
  assert.equal(merged.comparison.everyBucketComplete, false);
  assert.equal(merged.comparison.dateFrom, null, "janela nunca fabricada");
  // serie ATUAL intacta
  assert.equal(merged.buckets[0].total, 12);
  assert.deepEqual(merged.availableChannels, ["ml", "shopee"]);

  const card = codeOnly(read("src/components/gerencial/EvolutionCard.tsx"));
  assert.match(card, /Comparação não suportada pela API para/);
});

test("V22-18. a mensagem antiga de indisponibilidade da serie anterior foi removida", () => {
  const card = read("src/components/gerencial/EvolutionCard.tsx");
  assert.doesNotMatch(
    card,
    /série do período anterior indisponível neste gráfico/,
    "a limitação deixou de ser verdadeira no V2-2",
  );
  assert.doesNotMatch(card, /COMPARISON_SERIES_NOTE/);
});

test("V22-19. o seletor de granularidade e acessivel e sem controle morto", () => {
  const card = codeOnly(read("src/components/gerencial/EvolutionCard.tsx"));
  const group = card.slice(card.indexOf('aria-label="Granularidade do gráfico"'), card.indexOf("</div>", card.indexOf('aria-label="Granularidade do gráfico"')) + 6);
  assert.match(group, /aria-pressed=\{granularity === opt\.value\}/);
  assert.match(group, /min-h-11/, "alvo de toque de 44px");
  assert.match(group, /onGranularityChange\(opt\.value\)/);
  // as quatro opcoes existem
  for (const v of ["auto", "day", "week", "month"]) {
    assert.match(card, new RegExp(`value: "${v}"`), `opção ${v} deve existir`);
  }
  // em Automatica, o card informa o grao RESOLVIDO
  assert.match(card, /resolvida automaticamente pelo intervalo/);
  // e o estado pedido e' separado do efetivo
  assert.match(card, /granularity: TrendGranularityRequest;/);
  assert.match(card, /effective: TrendGranularity/);
});

test("V22-20. a linha anterior e secundaria e nao depende de cor", () => {
  const chart = codeOnly(read("src/components/gerencial/EvolutionChart.tsx"));
  const cmpLine = chart.slice(chart.indexOf("showComparisonTotal && ("), chart.indexOf("{channels.map"));
  assert.match(cmpLine, /strokeDasharray="6 4"/, "tracejado distingue sem depender de cor");
  assert.match(cmpLine, /dataKey="comparison\.total"/, "UMA linha: o total anterior");
  assert.match(cmpLine, /stroke="#94a3b8"/, "neutra, não compete com as séries por canal");
  // nao viraram seis series equivalentes: a comparacao por canal nao e desenhada
  assert.doesNotMatch(chart, /dataKey=\{`comparison\.values\./);
});

test("V22-21. o tooltip distingue os dois periodos com datas reais", () => {
  const chart = read("src/components/gerencial/EvolutionChart.tsx");
  assert.match(chart, /Período atual/);
  assert.match(chart, /Período anterior/);
  assert.match(chart, /\{cmp\.label\} · \{cmp\.date\}/, "exibe o label e a data REAIS do anterior");
  assert.match(chart, /Sem ponto correspondente na janela anterior/);
});

test("V22-22. o CTA do bucket continua fixando o periodo ATUAL", () => {
  const drills = read("src/components/gerencial/GerencialDrilldowns.tsx");
  const bucket = drills.slice(
    drills.indexOf("export function TrendBucketDrilldownContent"),
    drills.indexOf("export function ChannelSeriesDrilldownContent"),
  );
  // o range vem do bucket ATUAL, cortado pelo periodo global
  assert.match(bucket, /bucketRange\(bucket\.date, merged\.granularity, bounds\)/);
  assert.match(bucket, /onPinRange\(range\.dateFrom, range\.dateTo\)/);
  // a comparacao aparece como EVIDENCIA, nunca como destino do CTA
  assert.match(bucket, /Mesma posição no período anterior/);
  assert.doesNotMatch(bucket, /onPinRange\(bucket\.comparison/);
  // e avisa quando o intervalo foi cortado
  assert.match(bucket, /Intervalo cortado pelo período selecionado/);
});

test("V22-23. zero tipografia abaixo de 12px nos arquivos tocados", () => {
  const targets = [
    ...readdirSync(join(ROOT, "src/components/gerencial")).map((f) => "src/components/gerencial/" + f),
    ...readdirSync(join(ROOT, "src/lib/gerencial")).map((f) => "src/lib/gerencial/" + f),
    "app/page.tsx",
    "src/hooks/useGerencialSources.ts",
    "src/lib/api-client.ts",
  ].filter((f) => /\.(tsx|ts)$/.test(f));

  const offenders: string[] = [];
  for (const f of targets) {
    const src = read(f);
    for (const [re, kind] of [
      [/text-\[(\d+)px\]/g, "Tailwind"],
      [/fontSize:\s*(\d+)\b/g, "inline/Recharts"],
      [/font-size:\s*(\d+)px/g, "CSS"],
    ] as [RegExp, string][]) {
      for (const m of src.matchAll(re)) {
        if (Number(m[1]) < 12) offenders.push(`${f}: ${kind} ${m[0]}`);
      }
    }
  }
  assert.deepEqual(offenders, [], "fontes < 12px:\n" + offenders.join("\n"));
});

test("V22-24. compare=false nao renderiza nada de comparacao", () => {
  const card = codeOnly(read("src/components/gerencial/EvolutionCard.tsx"));
  // TUDO que e' comparativo esta atras de `cmp.requested` ou `showComparison`
  assert.match(card, /const showComparison = cmp\.requested && cmp\.everyBucketComplete;/);
  assert.match(card, /\{cmp\.requested && \(/, "os avisos comparativos ficam atrás de requested");
  assert.match(card, /\{showComparison && \(/, "a legenda anterior fica atrás de showComparison");
  // e a linha do grafico so' e' pedida quando a comparacao esta completa
  assert.match(card, /showComparisonTotal=\{showComparison\}/);
});
