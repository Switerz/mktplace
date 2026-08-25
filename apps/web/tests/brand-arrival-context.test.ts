// Contrato de chegada "quente" da página de Marca (Gate G3, Task 2 — desenho
// em docs/DRILLDOWN_ARCHITECTURE.md §8). Cobre parse/validação, a seleção
// determinística de um único sinal, o mapa sinal → evidência real e as
// regressões estáticas de wiring (produtor único, consumidor único, zero
// fetch, zero número na URL, um só shell de diálogo).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  parseBrandArrivalContext,
  pickArrivalSignal,
  buildArrivalParams,
  buildReturnHref,
  isSignalCompatibleWithChannel,
  ARRIVAL_SIGNALS,
  CTX_FROM_CANAIS,
  SECTION_PERIOD,
  RETURN_CTA_LABEL,
  type ParamReader,
} from "../src/lib/brand-arrival-context.ts";
import { FILTER_QUERY_KEYS } from "../src/lib/filters/nav-links.ts";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf-8");

/** ParamReader real, com suporte a repetição (`getAll`). */
function params(qs: string): ParamReader {
  return new URLSearchParams(qs);
}
/** ParamReader legado, SEM `getAll` — garante o fallback de `readSingle`. */
function paramsNoGetAll(qs: string): ParamReader {
  const u = new URLSearchParams(qs);
  return { get: (k: string) => u.get(k) };
}

const VALID = "ctx_from=canais&ctx_signal=custo_alto&ctx_channel=shopee&ctx_brand=kokeshi";
const ALL = ["tiktok", "ml", "shopee"];

// ---------------------------------------------------------------------------
// Parse e validação
// ---------------------------------------------------------------------------

test("contexto válido é aceito e traz descrição allowlisted do código", () => {
  const ctx = parseBrandArrivalContext(params(VALID), "kokeshi", ALL);
  assert.ok(ctx);
  assert.equal(ctx!.from, CTX_FROM_CANAIS);
  assert.equal(ctx!.signal, "custo_alto");
  assert.equal(ctx!.channel, "shopee");
  assert.equal(ctx!.channelLabel, "Shopee");
  assert.equal(ctx!.brand, "kokeshi");
  assert.match(ctx!.description, /custo de marketplace no topo do canal/);
  // custo não tem evidência nesta página: limitação declarada, sem âncora
  assert.equal(ctx!.hasEvidence, false);
  assert.equal(ctx!.section, null);
  assert.ok(ctx!.unavailableNote && ctx!.unavailableNote.length > 0);
});

test("acesso direto sem contexto ⇒ null (página idêntica à atual)", () => {
  assert.equal(parseBrandArrivalContext(params(""), "kokeshi", ALL), null);
  assert.equal(parseBrandArrivalContext(params("brands=kokeshi&channels=shopee"), "kokeshi", ALL), null);
});

test("contexto parcial ⇒ null (não é contexto)", () => {
  assert.equal(parseBrandArrivalContext(params("ctx_from=canais&ctx_signal=custo_alto"), "kokeshi", ALL), null);
  assert.equal(
    parseBrandArrivalContext(params("ctx_from=canais&ctx_signal=custo_alto&ctx_channel=shopee"), "kokeshi", ALL),
    null,
  );
});

test("ctx_from inválido ⇒ null — gerencial NÃO é suportado nesta fase", () => {
  const gerencial = VALID.replace("ctx_from=canais", "ctx_from=gerencial");
  assert.equal(parseBrandArrivalContext(params(gerencial), "kokeshi", ALL), null);
  assert.equal(parseBrandArrivalContext(params(VALID.replace("canais", "xyz")), "kokeshi", ALL), null);
});

test("sinal desconhecido ⇒ null", () => {
  const q = VALID.replace("ctx_signal=custo_alto", "ctx_signal=sinal_novo_do_backend");
  assert.equal(parseBrandArrivalContext(params(q), "kokeshi", ALL), null);
});

test("marca incompatível com a rota ⇒ null (troca de marca descarta o contexto)", () => {
  assert.equal(parseBrandArrivalContext(params(VALID), "apice", ALL), null);
  const inexistente = VALID.replace("ctx_brand=kokeshi", "ctx_brand=marca_fantasma");
  assert.equal(parseBrandArrivalContext(params(inexistente), "marca_fantasma", ALL), null);
});

test("canal incompatível com o filtro ⇒ null (troca de canal descarta o contexto)", () => {
  assert.equal(parseBrandArrivalContext(params(VALID), "kokeshi", ["tiktok"]), null);
  assert.equal(parseBrandArrivalContext(params(VALID), "kokeshi", []), null);
  const canalInvalido = VALID.replace("ctx_channel=shopee", "ctx_channel=mercadolivre");
  assert.equal(parseBrandArrivalContext(params(canalInvalido), "kokeshi", ALL), null);
  // canal válido e presente no filtro parcial continua funcionando
  assert.ok(parseBrandArrivalContext(params(VALID), "kokeshi", ["shopee"]));
});

test("parâmetro repetido é ambíguo ⇒ null (nunca 'o primeiro ganha')", () => {
  const dup = VALID + "&ctx_signal=roas_forte";
  assert.equal(parseBrandArrivalContext(params(dup), "kokeshi", ALL), null);
  const dupBrand = VALID + "&ctx_brand=apice";
  assert.equal(parseBrandArrivalContext(params(dupBrand), "kokeshi", ALL), null);
});

test("reader sem getAll continua funcionando (fallback)", () => {
  assert.ok(parseBrandArrivalContext(paramsNoGetAll(VALID), "kokeshi", ALL));
  assert.equal(parseBrandArrivalContext(paramsNoGetAll(""), "kokeshi", ALL), null);
});

// ---------------------------------------------------------------------------
// Seleção determinística de UM sinal (prioridade do G2)
// ---------------------------------------------------------------------------

test("prioridade: atenção antes de destaque, na ordem do Gate G2", () => {
  assert.equal(pickArrivalSignal(["roas_forte", "custo_alto", "frete_alto"]), "custo_alto");
  assert.equal(pickArrivalSignal(["frete_alto", "roas_forte"]), "frete_alto");
  assert.equal(pickArrivalSignal(["roas_forte", "ads_subutilizado"]), "ads_subutilizado");
  assert.equal(pickArrivalSignal(["sem_dado", "roas_forte"]), "sem_dado");
  assert.equal(pickArrivalSignal(["roas_forte"]), "roas_forte");
  // determinístico: a ordem de entrada não altera a escolha
  assert.equal(pickArrivalSignal(["custo_alto", "frete_alto"]), pickArrivalSignal(["frete_alto", "custo_alto"]));
});

test("sem sinal conhecido ⇒ null e nenhum ctx_* é anexado ao CTA", () => {
  assert.equal(pickArrivalSignal([]), null);
  assert.equal(pickArrivalSignal(["sinal_desconhecido"]), null);
  assert.equal(buildArrivalParams([], "shopee", "kokeshi"), "");
  assert.equal(buildArrivalParams(["sinal_desconhecido"], "shopee", "kokeshi"), "");
});

test("buildArrivalParams emite só identificadores allowlisted", () => {
  const qs = buildArrivalParams(["roas_forte", "custo_alto"], "shopee", "kokeshi");
  const u = new URLSearchParams(qs);
  assert.deepEqual([...u.keys()].sort(), ["ctx_brand", "ctx_channel", "ctx_from", "ctx_signal"]);
  assert.equal(u.get("ctx_signal"), "custo_alto");
  assert.equal(u.get("ctx_from"), "canais");
  // nenhum número/valor monetário/percentual na querystring
  assert.doesNotMatch(qs, /\d/, "nenhum dígito deve aparecer no contexto");
});

test("canal ou marca fora do domínio não produz contexto", () => {
  assert.equal(buildArrivalParams(["custo_alto"], "canal_novo", "kokeshi"), "");
  assert.equal(buildArrivalParams(["custo_alto"], "shopee", "marca_nova"), "");
});

// ---------------------------------------------------------------------------
// Compatibilidade sinal × canal (rodada de correção da Task 2). A URL é
// entrada não confiável: um sinal impossível para o canal nunca é anunciado,
// mesmo que o produtor legítimo jamais o gere.
// ---------------------------------------------------------------------------

function q(signal: string, channel: string, brand = "kokeshi"): string {
  return `ctx_from=canais&ctx_signal=${signal}&ctx_channel=${channel}&ctx_brand=${brand}`;
}

test("TikTok NÃO aceita ads_subutilizado (canal sem Ads no contrato)", () => {
  assert.equal(parseBrandArrivalContext(params(q("ads_subutilizado", "tiktok")), "kokeshi", ALL), null);
  assert.equal(isSignalCompatibleWithChannel("ads_subutilizado", "tiktok"), false);
});

test("TikTok NÃO aceita frete_alto (canal sem frete de seller no contrato)", () => {
  assert.equal(parseBrandArrivalContext(params(q("frete_alto", "tiktok")), "kokeshi", ALL), null);
  assert.equal(isSignalCompatibleWithChannel("frete_alto", "tiktok"), false);
});

test("TikTok NÃO aceita roas_forte (sem Ads, sem ROAS)", () => {
  assert.equal(parseBrandArrivalContext(params(q("roas_forte", "tiktok")), "kokeshi", ALL), null);
  assert.equal(isSignalCompatibleWithChannel("roas_forte", "tiktok"), false);
});

test("TikTok aceita custo_alto e sem_dado", () => {
  const custo = parseBrandArrivalContext(params(q("custo_alto", "tiktok")), "kokeshi", ALL);
  assert.ok(custo);
  assert.equal(custo!.channel, "tiktok");
  assert.equal(custo!.signal, "custo_alto");
  const semDado = parseBrandArrivalContext(params(q("sem_dado", "tiktok")), "kokeshi", ALL);
  assert.ok(semDado);
  assert.equal(semDado!.signal, "sem_dado");
});

test("ML aceita ads_subutilizado; Shopee aceita frete_alto e roas_forte", () => {
  const mlAds = parseBrandArrivalContext(params(q("ads_subutilizado", "ml")), "kokeshi", ALL);
  assert.ok(mlAds);
  assert.equal(mlAds!.hasEvidence, true);

  for (const s of ["frete_alto", "roas_forte"]) {
    const ctx = parseBrandArrivalContext(params(q(s, "shopee")), "kokeshi", ALL);
    assert.ok(ctx, s);
    assert.equal(ctx!.channel, "shopee");
  }
});

test("os cinco sinais valem para ML e Shopee, e só dois para TikTok", () => {
  for (const ch of ["ml", "shopee"] as const) {
    for (const s of ARRIVAL_SIGNALS) {
      assert.equal(isSignalCompatibleWithChannel(s, ch), true, `${ch}/${s}`);
    }
  }
  const tk = ARRIVAL_SIGNALS.filter((s) => isSignalCompatibleWithChannel(s, "tiktok"));
  assert.deepEqual([...tk].sort(), ["custo_alto", "sem_dado"]);
});

test("buildArrivalParams não gera contexto incompatível com o canal", () => {
  assert.equal(buildArrivalParams(["roas_forte"], "tiktok", "kokeshi"), "");
  assert.equal(buildArrivalParams(["frete_alto"], "tiktok", "kokeshi"), "");
  assert.equal(buildArrivalParams(["ads_subutilizado"], "tiktok", "kokeshi"), "");
  // compatíveis seguem gerando normalmente
  assert.match(buildArrivalParams(["custo_alto"], "tiktok", "kokeshi"), /ctx_signal=custo_alto/);
  assert.match(buildArrivalParams(["sem_dado"], "tiktok", "kokeshi"), /ctx_signal=sem_dado/);
  assert.match(buildArrivalParams(["roas_forte"], "shopee", "kokeshi"), /ctx_signal=roas_forte/);
});

// ---------------------------------------------------------------------------
// Mapa sinal → evidência real na Marca
// ---------------------------------------------------------------------------

test("ads_subutilizado é o único sinal com evidência real (KPI de investimento)", () => {
  const qs = VALID.replace("ctx_signal=custo_alto", "ctx_signal=ads_subutilizado");
  const ctx = parseBrandArrivalContext(params(qs), "kokeshi", ALL);
  assert.ok(ctx);
  assert.equal(ctx!.hasEvidence, true);
  assert.equal(ctx!.section, SECTION_PERIOD);
  assert.ok(ctx!.sectionLabel);
  // mesmo com evidência, a ressalva de escopo continua declarada
  assert.match(ctx!.unavailableNote ?? "", /comparação com o canal/i);
  assert.match(ctx!.unavailableNote ?? "", /matriz por canal/i);
});

test("descrição de ads_subutilizado é neutra — não afirma 'abaixo da mediana'", () => {
  const qs = VALID.replace("ctx_signal=custo_alto", "ctx_signal=ads_subutilizado");
  const ctx = parseBrandArrivalContext(params(qs), "kokeshi", ALL)!;
  // a regra do canal também dispara com percentual ausente ou gasto zero,
  // então a descrição não pode afirmar uma comparação que talvez não ocorreu
  assert.doesNotMatch(ctx.description, /abaixo da mediana/i);
  assert.doesNotMatch(ctx.description, /mediana/i);
  assert.doesNotMatch(ctx.description, /percentual|%/i);
  assert.match(ctx.description, /Ads subutilizado/i);
});

test("custo, frete, sem_dado e roas_forte declaram limitação e não têm âncora", () => {
  for (const s of ["custo_alto", "frete_alto", "sem_dado", "roas_forte"]) {
    const ctx = parseBrandArrivalContext(params(VALID.replace("custo_alto", s)), "kokeshi", ALL);
    assert.ok(ctx, s);
    assert.equal(ctx!.hasEvidence, false, s);
    assert.equal(ctx!.section, null, s);
    assert.equal(ctx!.sectionLabel, null, s);
    assert.ok(ctx!.unavailableNote, s);
  }
});

test("todo sinal do enum tem descrição e nota — nenhum caso sem texto", () => {
  for (const s of ARRIVAL_SIGNALS) {
    const ctx = parseBrandArrivalContext(params(VALID.replace("custo_alto", s)), "kokeshi", ALL);
    assert.ok(ctx, s);
    assert.ok(ctx!.description.length > 0, s);
    assert.ok((ctx!.unavailableNote ?? "").length > 0, s);
    // nenhuma descrição promete número
    assert.doesNotMatch(ctx!.description, /\d/, s);
  }
});

// ---------------------------------------------------------------------------
// Retorno à evidência + isolamento dos ctx_*
// ---------------------------------------------------------------------------

test("retorno vai a Canais com marca/canal do contexto e sem repropagar ctx_*", () => {
  const ctx = parseBrandArrivalContext(params(VALID), "kokeshi", ALL)!;
  const href = buildReturnHref(ctx);
  assert.equal(href, "/canais?brands=kokeshi&channels=shopee");
  assert.doesNotMatch(href, /ctx_/, "voltar não é 'chegar quente'");
  assert.match(RETURN_CTA_LABEL, /Voltar à evidência em Canais/);
});

test("ctx_* NUNCA entram na preservação global de filtros", () => {
  for (const k of ["ctx_from", "ctx_signal", "ctx_channel", "ctx_brand"]) {
    assert.ok(!FILTER_QUERY_KEYS.includes(k), `${k} não pode estar em FILTER_QUERY_KEYS`);
  }
  assert.deepEqual(FILTER_QUERY_KEYS, ["channels", "brands", "date_from", "date_to", "compare"]);
});

test("null ≠ zero: ausência de contexto é null, nunca um objeto 'vazio'", () => {
  const ausente = parseBrandArrivalContext(params(""), "kokeshi", ALL);
  assert.equal(ausente, null);
  assert.notEqual(ausente, 0);
  assert.notDeepEqual(ausente, {});
});

// ---------------------------------------------------------------------------
// Regressões estáticas de wiring
// ---------------------------------------------------------------------------

test("Canais segue produzindo ctx_* e a Marca segue sendo a única consumidora", () => {
  // Atualizado no Gate V3-2: a Inteligência passou a ser um SEGUNDO produtor,
  // com `ctx_focus` próprio (nunca `ctx_signal`). O contrato de Canais continua
  // idêntico letra por letra — é isso que este teste protege.
  const canais = read("src/components/ChannelComparisonDialogContent.tsx");
  assert.match(canais, /buildArrivalParams\(row\.signals, row\.channel, row\.brand\)/);
  assert.match(canais, /DrilldownCta href=\{buildHref\(brandHref\)\}/);
  assert.doesNotMatch(canais, /ctx_focus|buildInteligenciaArrivalParams/, "Canais não emite a outra origem");

  const marca = read("app/brand/[brand]/page.tsx");
  assert.match(marca, /parseBrandArrivalContext\(searchParams, brand, filters\.channels\)/);
  assert.match(marca, /<BrandArrivalBanner ctx=\{arrivalCtx\}/);

  // o segundo produtor existe de fato — o enum não foi criado sem wiring
  const intel = read("app/inteligencia/page.tsx");
  assert.match(intel, /buildInteligenciaArrivalParams\(/);
  assert.doesNotMatch(intel, /ctx_signal/, "a Inteligência nunca reusa o sinal de Canais");

  // nenhuma tela ALÉM dessas duas produz ctx_*, e nenhuma além da Marca consome
  for (const f of ["app/page.tsx", "app/canais/page.tsx", "app/financeiro/page.tsx", "src/lib/filters/nav-links.ts"]) {
    assert.doesNotMatch(read(f), /ctx_from|ctx_signal|ctx_focus|ctx_channel|ctx_brand/, f);
  }
});

test("nenhum fetch/endpoint novo e nenhum modal novo no Gate G3", () => {
  for (const f of ["src/lib/brand-arrival-context.ts", "src/components/BrandArrivalBanner.tsx"]) {
    const src = read(f);
    assert.doesNotMatch(src, /\bfetch\s*\(/, f);
    assert.doesNotMatch(src, /useSWR|axios/, f);
    assert.doesNotMatch(src, /createPortal|role="dialog"/, f);
  }
  // o shell segue único
  const shell = read("src/components/KpiDrilldownDialog.tsx");
  assert.match(shell, /createPortal/);
});

test("banner reusa as primitives do G2 e não declara frescor", () => {
  const src = read("src/components/BrandArrivalBanner.tsx");
  assert.match(src, /DrilldownContextLine/);
  assert.match(src, /DataQualityNote/);
  assert.match(src, /DrilldownCta/);
  assert.match(src, /refreshedAt=\{null\}/, "o banner não afirma que os dados foram carregados");
  assert.match(src, /if \(!ctx\) return null;/, "sem contexto não renderiza nada");
});

test("seção-âncora existe de fato na página e tem scroll-mt", () => {
  const marca = read("app/brand/[brand]/page.tsx");
  assert.match(marca, /id=\{SECTION_PERIOD\}/);
  assert.match(marca, /className="scroll-mt-24"/);
});
