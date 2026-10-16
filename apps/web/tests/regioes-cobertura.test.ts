// REGIONAL-SOURCE-RECOVERY-2 — cobertura regional por canal na tela Regioes.
// Cenario real de 16/10/2026: ML carregado ate 15/10, Shopee congelada em
// 24/08 (fonte descontinuada, endereco mascarado na API), TikTok sem fonte.
// Roda via `node --test` (type-stripping nativo), sem JSDOM: a parte visual e'
// travada por leitura estatica de page.tsx, como os demais testes de fiacao.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {
  buildAvisoCobertura, linhaAtualizacao, marcaPontoAusente, fmtDataBr, nomeCanal, normalizarCobertura,
} from "../src/lib/regioes-cobertura.ts";
import type { CoberturaCanal } from "../src/lib/api-client.ts";

const DETALHE_SHOPEE =
  "A fonte regional da Shopee (export do Seller Center) foi descontinuada e a API " +
  "da Shopee entrega o endereço do comprador mascarado — não há UF para carregar.";

const ML_ATUAL: CoberturaCanal = {
  channel: "ml", marketplace_id: 2, status: "atualizada", dados_ate: "2026-10-15",
  esperado_ate: "2026-10-15", motivo: null, motivo_detalhe: null,
};
const SHOPEE_DEFASADA: CoberturaCanal = {
  channel: "shopee", marketplace_id: 3, status: "defasada", dados_ate: "2026-08-24",
  esperado_ate: "2026-10-15", motivo: "carga_defasada", motivo_detalhe: DETALHE_SHOPEE,
};
const TIKTOK: CoberturaCanal = {
  channel: "tiktok", marketplace_id: 1, status: "sem_cobertura", dados_ate: null,
  esperado_ate: "2026-10-15", motivo: "sem_fonte_regional",
  motivo_detalhe: "Nenhuma fonte mapeada expõe a UF do pedido TikTok.",
};

// ---------------------------------------------------------------------------
// Aviso
// ---------------------------------------------------------------------------
test("ML atual + Shopee defasada: aviso nomeia a Shopee, a data e o motivo", () => {
  const aviso = buildAvisoCobertura([TIKTOK, ML_ATUAL, SHOPEE_DEFASADA]);
  assert.ok(aviso);
  assert.equal(aviso.severidade, "alerta");
  assert.equal(aviso.titulo, "Shopee com dado regional só até 24/08/2026");
  assert.match(aviso.mensagem, /Shopee não entra nos números depois de 24\/08\/2026/);
  assert.match(aviso.mensagem, /ausência de dado regional não é venda zero/);
  assert.deepEqual(aviso.detalhes, [DETALHE_SHOPEE]);
});

test("a data recente do ML aparece como do ML, nunca como do total", () => {
  const aviso = buildAvisoCobertura([ML_ATUAL, SHOPEE_DEFASADA]);
  assert.ok(aviso);
  assert.match(aviso.mensagem, /Mercado Livre até 15\/10\/2026 está em dia, mas isso não torna o total atualizado/);
});

test("TikTok sozinho nao gera este aviso (tem aviso estrutural proprio)", () => {
  assert.equal(buildAvisoCobertura([TIKTOK]), null);
});

test("tudo atualizado: sem aviso", () => {
  assert.equal(buildAvisoCobertura([ML_ATUAL, { ...SHOPEE_DEFASADA, status: "atualizada", motivo: null, motivo_detalhe: null, dados_ate: "2026-10-15" }]), null);
});

test("canal sem nenhuma carga e' critico, nao alerta", () => {
  const aviso = buildAvisoCobertura([ML_ATUAL, { ...SHOPEE_DEFASADA, status: "sem_carga", dados_ate: null, motivo: "sem_carga" }]);
  assert.ok(aviso);
  assert.equal(aviso.severidade, "critico");
  assert.equal(aviso.titulo, "Shopee sem dado regional carregado");
});

test("dois canais parados: titulo no plural e os dois nomeados", () => {
  const mlParado: CoberturaCanal = { ...ML_ATUAL, status: "defasada", dados_ate: "2026-09-28", motivo: "carga_defasada" };
  const aviso = buildAvisoCobertura([mlParado, SHOPEE_DEFASADA]);
  assert.ok(aviso);
  assert.equal(aviso.titulo, "Canais com dado regional desatualizado");
  assert.match(aviso.mensagem, /Mercado Livre não entra nos números depois de 28\/09\/2026; Shopee não entra/);
});

// ---------------------------------------------------------------------------
// Linha temporal do cabecalho
// ---------------------------------------------------------------------------
test("com canal defasado, o cabecalho mostra 'Dados completos até' e nao 'Atualizado em'", () => {
  const linha = linhaAtualizacao("2026-08-24", ["shopee"], "16/10/2026 06:30");
  assert.equal(linha, "Dados completos até 24/08/2026 · carga mais recente (parcial) em 16/10/2026 06:30");
  assert.doesNotMatch(linha ?? "", /^Atualizado em/);
});

test("sem canal defasado, o cabecalho continua 'Atualizado em'", () => {
  assert.equal(linhaAtualizacao("2026-10-15", [], "16/10/2026 06:30"), "Atualizado em 16/10/2026 06:30");
});

test("canal sem carga (dados_completos_ate nulo) nunca vira data inventada", () => {
  assert.equal(linhaAtualizacao(null, ["shopee"], null), "Dados incompletos no período");
});

// ---------------------------------------------------------------------------
// Pontos da tendencia
// ---------------------------------------------------------------------------
test("ponto sem Shopee e' marcado; ponto completo nao", () => {
  assert.equal(marcaPontoAusente(["shopee"]), "sem Shopee");
  assert.equal(marcaPontoAusente(["ml", "shopee"]), "sem Mercado Livre e Shopee");
  assert.equal(marcaPontoAusente([]), null);
  assert.equal(marcaPontoAusente(undefined), null);
});

test("fmtDataBr nao sofre deslocamento de fuso", () => {
  assert.equal(fmtDataBr("2026-08-24"), "24/08/2026");
  assert.equal(fmtDataBr(null), null);
  assert.equal(nomeCanal("ml"), "Mercado Livre");
});

// ---------------------------------------------------------------------------
// Fiacao em page.tsx (estatica)
// ---------------------------------------------------------------------------
const PAGE = fs.readFileSync(path.join(import.meta.dirname, "..", "app", "regioes", "page.tsx"), "utf8");

test("page.tsx renderiza o aviso de cobertura ANTES dos KPI cards", () => {
  const aviso = PAGE.indexOf('data-testid="aviso-cobertura-canal"');
  const kpis = PAGE.indexOf("{/* KPI Cards */}");
  assert.ok(aviso > 0, "aviso-cobertura-canal ausente");
  assert.ok(kpis > 0 && aviso < kpis, "o aviso precisa vir antes dos KPIs");
  assert.match(PAGE, /buildAvisoCobertura\(cobertura\.canais\)/);
});

test("page.tsx nunca mais escreve 'Atualizado em' direto do refreshed_at", () => {
  assert.doesNotMatch(PAGE, /Atualizado em \{fmtRefreshedAt/);
  assert.match(PAGE, /linhaAtualizacao\(/);
  assert.match(PAGE, /cobertura\.dadosCompletosAte/);
});

test("page.tsx marca os pontos da tendencia sem algum canal", () => {
  assert.match(PAGE, /marcaPontoAusente\(p\.canais_ausentes\)/);
  assert.match(PAGE, /data-testid="tendencia-canal-ausente"/);
});

test("a cobertura so' e' lida com dado fresco e sempre via normalizarCobertura", () => {
  assert.match(PAGE, /normalizarCobertura\(dataIsFresh \? summary : null\)/);
  // nenhum acesso direto aos campos crus (que somem na API antiga)
  assert.doesNotMatch(PAGE, /summary\??\.(cobertura_por_canal|canais_defasados|canais_com_atraso|dados_completos_ate)/);
});

// ---------------------------------------------------------------------------
// RECOVERY-3 — D-2 e' atraso leve, nunca "atualizado"
// ---------------------------------------------------------------------------
const ML_D2: CoberturaCanal = {
  ...ML_ATUAL, status: "atraso_leve", dados_ate: "2026-10-14", motivo: "carga_atrasada",
  motivo_detalhe: "A carga do último dia fechado ainda não chegou para este canal.",
};

test("ML em D-2: aviso leve, titulo de 1 dia de atraso, e o total deixa de ser 'atualizado'", () => {
  const aviso = buildAvisoCobertura([ML_D2]);
  assert.ok(aviso);
  assert.equal(aviso.severidade, "leve");
  assert.equal(aviso.titulo, "Mercado Livre com 1 dia de atraso — dados até 14/10/2026");
  const n = normalizarCobertura({ cobertura_por_canal: [ML_D2], dados_completos_ate: "2026-10-14" });
  assert.deepEqual(n.canaisComAtraso, ["ml"]);
  assert.equal(linhaAtualizacao(n.dadosCompletosAte, n.canaisComAtraso, "16/10/2026 05:00"),
    "Dados completos até 14/10/2026 · carga mais recente (parcial) em 16/10/2026 05:00");
});

test("ML em D-2 + Shopee defasada: o grave manda na severidade e os dois aparecem", () => {
  const aviso = buildAvisoCobertura([ML_D2, SHOPEE_DEFASADA]);
  assert.ok(aviso);
  assert.equal(aviso.severidade, "alerta");
  assert.equal(aviso.titulo, "Canais com dado regional desatualizado");
  assert.match(aviso.mensagem, /Shopee não entra nos números depois de 24\/08\/2026; Mercado Livre não entra nos números depois de 14\/10\/2026/);
});

test("status desconhecido (API mais nova que o front) conta como atraso, nunca como atualizado", () => {
  const futuro = { ...ML_ATUAL, status: "status_novo_qualquer" } as CoberturaCanal;
  assert.deepEqual(normalizarCobertura({ cobertura_por_canal: [futuro] }).canaisComAtraso, ["ml"]);
  assert.ok(buildAvisoCobertura([futuro]));
});

// ---------------------------------------------------------------------------
// RECOVERY-3 — compatibilidade com a API antiga (Vercel publica antes do Render)
// ---------------------------------------------------------------------------
const SUMMARY_API_ANTIGA = {
  gmv: 1000, orders: 10, refreshed_at: "2026-09-29T14:15:18+00:00",
  channels_sem_cobertura_regional: ["tiktok"],
}; // sem nenhum campo de cobertura

test("API antiga: sem contrato, sem aviso, cabecalho continua 'Atualizado em' e nada quebra", () => {
  const n = normalizarCobertura(SUMMARY_API_ANTIGA);
  assert.equal(n.contratoPresente, false);
  assert.deepEqual(n.canais, []);
  assert.deepEqual(n.canaisComAtraso, []);
  assert.equal(buildAvisoCobertura(n.canais), null);
  assert.equal(linhaAtualizacao(n.dadosCompletosAte, n.canaisComAtraso, "29/09/2026 11:15"), "Atualizado em 29/09/2026 11:15");
});

test("API antiga: ponto de tendencia sem canais_ausentes nao ganha marca", () => {
  assert.equal(marcaPontoAusente((({ date: "2026-09-01" }) as { canais_ausentes?: string[] }).canais_ausentes), null);
});

test("payload nulo ou campo com tipo errado nunca derruba a tela", () => {
  assert.equal(normalizarCobertura(null).contratoPresente, false);
  assert.equal(normalizarCobertura(undefined).contratoPresente, false);
  assert.equal(normalizarCobertura({ cobertura_por_canal: null }).contratoPresente, false);
});

test("API da RECOVERY-2 (sem canais_com_atraso): atraso derivado da lista por canal", () => {
  const n = normalizarCobertura({
    cobertura_por_canal: [ML_ATUAL, SHOPEE_DEFASADA], canais_defasados: ["shopee"], dados_completos_ate: "2026-08-24",
  });
  assert.deepEqual(n.canaisComAtraso, ["shopee"]);
});
