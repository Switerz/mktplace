// Gate DQ2 — verdade da interface (ver docs/MARKETPLACE_DATA_QUALITY_CHECKPOINT.md
// §10, achados 1 e 2). Cobre a lógica pura de indisponibilidade em Qualidade
// (TikTok) e de escopo em Regiões, mais regressões estáticas de wiring: nenhum
// zero fabricado, nenhuma leitura de "cobertura integral", nenhum texto que
// prometa métrica indisponível.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  UNAVAILABLE_VALUE,
  UNAVAILABLE_SUBVALUE,
  TIKTOK_UNAVAILABLE_QUALITY_METRICS,
  TIKTOK_QUALITY_UNAVAILABLE_NOTE,
  qualityLoadedAnnouncement,
} from "../src/lib/quality-availability.ts";
import {
  buildRegionalScope,
  channelLabel,
  REGIONAL_GMV_LABEL,
  UF_FILL_LABEL,
} from "../src/lib/regioes-scope.ts";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf-8");

// ---------------------------------------------------------------------------
// Qualidade — TikTok: cancelamento/devolução explicitamente indisponíveis
// ---------------------------------------------------------------------------

test("TikTok expõe cancelamento E devolução como indisponíveis, nunca como zero", () => {
  assert.equal(TIKTOK_UNAVAILABLE_QUALITY_METRICS.length, 2);
  const labels = TIKTOK_UNAVAILABLE_QUALITY_METRICS.map((m) => m.label);
  assert.ok(labels.some((l) => /cancelamento/i.test(l)), "precisa de card de cancelamento");
  assert.ok(labels.some((l) => /devolu/i.test(l)), "precisa de card de devolução");
  for (const m of TIKTOK_UNAVAILABLE_QUALITY_METRICS) {
    assert.equal(m.value, UNAVAILABLE_VALUE);
    assert.equal(m.subvalue, UNAVAILABLE_SUBVALUE);
    assert.doesNotMatch(m.value, /^0|%$/, "nunca 0%");
    assert.doesNotMatch(m.subvalue, /saudável|sem cancelamento|zero/i);
  }
});

test("valor de indisponibilidade é distinto de zero e de '—' (sem valor)", () => {
  assert.equal(UNAVAILABLE_VALUE, "N/D");
  assert.notEqual(UNAVAILABLE_VALUE, "—");
  assert.notEqual(UNAVAILABLE_VALUE, "0.0%");
  assert.match(UNAVAILABLE_SUBVALUE, /não disponível/i);
});

test("nota de limitação declara ausência sem prometer número nem afirmar taxa zero", () => {
  assert.match(TIKTOK_QUALITY_UNAVAILABLE_NOTE, /não estão disponíveis/i);
  assert.match(TIKTOK_QUALITY_UNAVAILABLE_NOTE, /não significa taxa zero/i);
  assert.match(TIKTOK_QUALITY_UNAVAILABLE_NOTE, /Mercado Livre e Shopee não são afetados/);
  assert.doesNotMatch(TIKTOK_QUALITY_UNAVAILABLE_NOTE, /\d+([.,]\d+)?%/, "não promete percentual algum");
});

test("aria-live comunica indisponibilidade quando TikTok está no escopo, não sucesso puro", () => {
  const withTk = qualityLoadedAnnouncement(true);
  assert.match(withTk, /não estão disponíveis nesta fonte/i);
  assert.doesNotMatch(withTk, /0%|zero/i);
  const withoutTk = qualityLoadedAnnouncement(false);
  assert.equal(withoutTk, "Dados de qualidade carregados.");
  assert.doesNotMatch(withoutTk, /TikTok/);
});

test("wiring: página de Qualidade renderiza os cards e a nota só com TikTok no escopo", () => {
  const src = read("app/qualidade/page.tsx");
  assert.match(src, /showTiktok && TIKTOK_UNAVAILABLE_QUALITY_METRICS\.map/);
  assert.match(src, /showTiktok && <DataQualityNote note=\{TIKTOK_QUALITY_UNAVAILABLE_NOTE\} \/>/);
  assert.match(src, /qualityLoadedAnnouncement\(showTiktok\)/);
  // ML e Shopee seguem lendo os valores reais da API (sem regressão)
  assert.match(src, /fmtRate\(displayKpis\?\.ml_cancel_rate_pct \?\? null\)/);
  assert.match(src, /fmtRate\(displayKpis\?\.shopee_cancel_rate_pct \?\? null\)/);
});

// ---------------------------------------------------------------------------
// Regiões — escopo de canal × elegibilidade × preenchimento de UF
// ---------------------------------------------------------------------------

test("TikTok ausente do regional nunca vira cobertura completa (seleção 'Todos')", () => {
  const s = buildRegionalScope(["tiktok", "ml", "shopee"], ["tiktok"], 100);
  assert.equal(s.status, "partial_channels");
  assert.deepEqual(s.channelsInScope, ["Mercado Livre", "Shopee"]);
  assert.deepEqual(s.channelsOutOfScope, ["TikTok Shop"]);
  assert.match(s.scopeNote, /TikTok Shop fora do escopo/);
  assert.match(s.scopeNote, /não são comparáveis ao GMV total/);
});

test("UF preenchida = 100% dentro do elegível NÃO significa cobertura total", () => {
  const s = buildRegionalScope(["tiktok", "ml", "shopee"], ["tiktok"], 100);
  assert.ok(s.ufFillCaveat, "com canal fora do escopo, 100% exige ressalva explícita");
  assert.match(s.ufFillCaveat!, /não cobertura de 100% do GMV do período/);
  // e o rótulo do KPI já carrega o denominador
  assert.match(UF_FILL_LABEL, /elegíveis/i);
  assert.match(REGIONAL_GMV_LABEL, /com cobertura regional/i);
});

test("seleção somente TikTok resulta em not_applicable, nunca em zero", () => {
  const s = buildRegionalScope(["tiktok"], ["tiktok"], null);
  assert.equal(s.status, "not_applicable");
  assert.deepEqual(s.channelsInScope, []);
  assert.match(s.scopeNote, /Nenhum canal selecionado tem cobertura regional/);
  assert.match(s.scopeNote, /não significa venda zero/i);
  assert.equal(s.ufFillCaveat, null, "sem elegíveis não há ressalva de preenchimento");
});

test("seleção ML/Shopee mostra o escopo correto e ainda declara a elegibilidade", () => {
  const ml = buildRegionalScope(["ml"], [], 97.5);
  assert.equal(ml.status, "all_selected_channels");
  assert.deepEqual(ml.channelsInScope, ["Mercado Livre"]);
  assert.match(ml.scopeNote, /apenas os pedidos elegíveis/);
  assert.equal(ml.ufFillCaveat, null);

  const both = buildRegionalScope(["ml", "shopee"], [], 100);
  assert.equal(both.status, "all_selected_channels");
  assert.deepEqual(both.channelsOutOfScope, []);
  assert.match(both.scopeNote, /Mercado Livre e Shopee/);
});

test("null e zero de uf_fill_pct continuam distintos no escopo", () => {
  const nulo = buildRegionalScope(["ml", "shopee"], [], null);
  const zero = buildRegionalScope(["ml", "shopee"], [], 0);
  assert.equal(nulo.ufFillCaveat, null);
  assert.equal(zero.ufFillCaveat, null, "0% não é 100%: nenhuma ressalva de 'cobertura completa'");
  // o status de canal não depende do preenchimento
  assert.equal(nulo.status, "all_selected_channels");
  assert.equal(zero.status, "all_selected_channels");
});

test("canal desconhecido no filtro nunca é assumido como coberto", () => {
  const s = buildRegionalScope(["canal_novo", "ml"], [], 100);
  assert.equal(s.status, "partial_channels");
  assert.deepEqual(s.channelsOutOfScope, ["canal_novo"]);
  assert.equal(channelLabel("ml"), "Mercado Livre");
  assert.equal(channelLabel("desconhecido"), "desconhecido");
});

test("wiring: Regiões usa os rótulos novos e o escopo só com dado fresco", () => {
  const src = read("app/regioes/page.tsx");
  assert.match(src, /label=\{REGIONAL_GMV_LABEL\}/);
  assert.match(src, /label=\{UF_FILL_LABEL\}/);
  assert.doesNotMatch(src, /label="GMV Regional"/, "rótulo antigo não pode voltar");
  assert.match(src, /dataIsFresh\s*\?\s*buildRegionalScope\(/, "escopo derivado só com dado fresco");
  assert.match(src, /dentro dos pedidos elegíveis/);
});

// ---------------------------------------------------------------------------
// Canais — o guardrail de `custo_alto` vive no produtor (API); aqui só se
// garante que a explicação do G2 não promete métrica indisponível.
// ---------------------------------------------------------------------------

test("explicação de sinais não promete custo quando a referência é indisponível", () => {
  const src = read("src/lib/channel-signal-reasons.ts");
  assert.match(src, /referência do canal indisponível/);
  assert.match(src, /não permitem detalhar a causa/);
  // e continua explicando apenas sinais efetivamente emitidos (row.signals)
  assert.match(src, /row\.signals\.map\(\(s\) => explainSignal\(s, row, median\)\)/);
});
