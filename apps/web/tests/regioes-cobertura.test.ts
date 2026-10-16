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
  buildAvisoCobertura, linhaAtualizacao, marcaPontoAusente, fmtDataBr, nomeCanal,
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
  assert.match(PAGE, /buildAvisoCobertura\(coberturaPorCanal\)/);
});

test("page.tsx nunca mais escreve 'Atualizado em' direto do refreshed_at", () => {
  assert.doesNotMatch(PAGE, /Atualizado em \{fmtRefreshedAt/);
  assert.match(PAGE, /linhaAtualizacao\(/);
  assert.match(PAGE, /summary\?\.dados_completos_ate/);
});

test("page.tsx marca os pontos da tendencia sem algum canal", () => {
  assert.match(PAGE, /marcaPontoAusente\(p\.canais_ausentes\)/);
  assert.match(PAGE, /data-testid="tendencia-canal-ausente"/);
});

test("a cobertura so' e' lida com dado fresco (nunca de uma requisicao antiga)", () => {
  assert.match(PAGE, /const coberturaPorCanal = dataIsFresh \?/);
  assert.match(PAGE, /const canaisDefasados = dataIsFresh \?/);
});
