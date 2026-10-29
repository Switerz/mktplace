/**
 * Gate UX-TORRE-2 — Gerencial, Canais e Financeiro no sistema visual, com
 * filtros de marca/periodo montados do mesmo jeito nas tres telas.
 *
 * Logica pura (resumo do escopo) testada direto; a montagem, onde a garantia
 * so' existe no codigo-fonte, e' travada lendo o arquivo. A medicao em
 * navegador (temas, overflow, console, numeros antes/depois) esta' no
 * relatorio do gate.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { resumoCanais, resumoDosFiltros, resumoMarcas } from "../src/lib/filters/summary.ts";
import type { GlobalFilters } from "../src/lib/filters/types.ts";

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8");
const semComentarios = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

const BASE: GlobalFilters = {
  channels: ["tiktok", "ml", "shopee"],
  brands: [],
  dateFrom: "2026-09-01",
  dateTo: "2026-09-30",
  compare: false,
};

// ===========================================================================
// 1. Resumo do escopo (botao "Filtros" do mobile)
// ===========================================================================
test("resumo: tudo selecionado le como 'todos/todas', com o periodo real", () => {
  assert.equal(resumoDosFiltros(BASE), "Todos os canais · Todas as marcas · 01/09/2026 – 30/09/2026");
});

test("resumo: canais parciais na ordem canonica, nunca na ordem do clique", () => {
  assert.equal(resumoCanais(["shopee", "tiktok"]), "TikTok Shop + Shopee");
  assert.equal(resumoCanais(["ml"]), "Mercado Livre");
});

test("resumo: marca vazia e' TODAS (sem filtro), nunca 'nenhuma'", () => {
  assert.equal(resumoMarcas([]), "Todas as marcas");
  // as cinco marcadas uma a uma tambem e' todas, para quem le
  assert.equal(resumoMarcas(["rituaria", "apice", "barbours", "kokeshi", "lescent"]), "Todas as marcas");
  assert.equal(resumoMarcas(["kokeshi", "apice"]), "ÁPICE, KOKESHI");
});

test("resumo: comparacao so' e' afirmada onde a tela calcula delta", () => {
  const comCompare = { ...BASE, compare: true };
  assert.match(resumoDosFiltros(comCompare), / · vs\. período anterior$/);
  // Canais/Financeiro escondem o toggle: `compare=true` herdado da URL da
  // Gerencial nao pode virar uma afirmacao falsa no resumo.
  assert.doesNotMatch(resumoDosFiltros(comCompare, { semComparacao: true }), /período anterior/);
});

test("resumo: um dia so' aparece como uma data, nao como intervalo", () => {
  assert.match(resumoDosFiltros({ ...BASE, dateFrom: "2026-10-09", dateTo: "2026-10-09" }), / · 09\/10\/2026$/);
});

// ===========================================================================
// 2. As tres telas montam os filtros pelo MESMO componente
// ===========================================================================
const PAGINAS = {
  gerencial: "app/page.tsx",
  canais: "app/canais/page.tsx",
  financeiro: "app/financeiro/page.tsx",
} as const;

test("as tres telas usam GlobalFilterControls e nenhuma remonta os filtros a mao", () => {
  for (const [nome, arquivo] of Object.entries(PAGINAS)) {
    const codigo = semComentarios(fonte(arquivo));
    assert.match(codigo, /<GlobalFilterControls filters=\{filters\} onChange=\{setFilters\}/, `${nome} sem o componente comum`);
    for (const avulso of ["MarketplaceFilter", "BrandFilter", "DateRangeFilter"]) {
      assert.doesNotMatch(codigo, new RegExp(`<${avulso}\\b`), `${nome} ainda monta ${avulso} a mao`);
    }
    // a fonte de verdade continua sendo a URL via useGlobalFilters, na pagina
    assert.match(codigo, /useGlobalFilters\(/, `${nome} perdeu a fonte de verdade dos filtros`);
  }
});

test("comparacao: so' a Gerencial oferece; Canais e Financeiro seguem sem delta", () => {
  // Nao e' escolha visual: as fontes de Canais e Financeiro nao calculam
  // deltas por periodo (docs/filtros_globais_contrato.md). O gate nao muda isso.
  assert.doesNotMatch(semComentarios(fonte(PAGINAS.gerencial)), /<GlobalFilterControls[^>]*hideCompare/);
  assert.match(semComentarios(fonte(PAGINAS.canais)), /<GlobalFilterControls[^>]*hideCompare/);
  assert.match(semComentarios(fonte(PAGINAS.financeiro)), /<GlobalFilterControls[^>]*hideCompare/);
});

test("defaults de periodo de cada tela nao mudaram", () => {
  // GERENCIAL-METAS-1: a Gerencial passou a abrir no mes corrente (decisao do
  // proprietario), para a meta mostrar ritmo e projecao. As demais seguem.
  assert.match(fonte(PAGINAS.gerencial), /useGlobalFilters\(\{ defaultPreset: "mes_atual", defaultCompare: true \}\)/);
  assert.match(fonte(PAGINAS.canais), /useGlobalFilters\(\{ defaultPreset: "mes_anterior" \}\)/);
  assert.match(fonte(PAGINAS.financeiro), /useGlobalFilters\(\{ defaultPreset: "mes_anterior" \}\)/);
});

// ===========================================================================
// 3. O recolhivel do mobile
// ===========================================================================
test("o recolhivel declara aria-expanded e aponta para um alvo que existe", () => {
  const c = semComentarios(fonte("src/components/layout/GlobalFilterControls.tsx"));
  assert.match(c, /aria-expanded=\{aberto\}/);
  assert.match(c, /aria-controls=\{id\}/);
  assert.match(c, /id=\{id\}/, "o alvo do aria-controls precisa existir no DOM");
  // fechado esconde SO' abaixo de `sm`; a partir dele os controles ficam sempre visiveis
  assert.match(c, /\$\{aberto \? "flex" : "hidden"\}[^`]*sm:flex/);
  assert.match(c, /sm:hidden/, "o botao some quando os controles ja cabem");
  // o resumo vem da MESMA GlobalFilters que vai para a API
  assert.match(c, /resumoDosFiltros\(filters/);
  // o botao tambem cumpre 44px
  assert.match(c, /min-h-11 w-full/);
  // nenhum estado de filtro paralelo: so' aberto/fechado
  assert.equal((c.match(/useState\(/g) ?? []).length, 1);
});

// ===========================================================================
// 4. Disciplina do violeta e alvo nos tres filtros compartilhados
// ===========================================================================
test("filtros: opcao ativa em relevo neutro, sem botao violeta cheio", () => {
  for (const arquivo of [
    "src/components/MarketplaceFilter.tsx",
    "src/components/BrandFilter.tsx",
    "src/components/DateRangeFilter.tsx",
  ]) {
    const c = semComentarios(fonte(arquivo));
    assert.doesNotMatch(c, /\bbg-accent\b(?![-/])/, `${arquivo}: botao ativo voltou a ser violeta cheio`);
    assert.match(c, /bg-surface font-semibold text-ink shadow-sm ring-1 ring-inset ring-line-strong/, arquivo);
    assert.match(c, /focus-visible:ring-accent/, `${arquivo}: foco visivel`);
  }
});

test("BrandFilter ganhou o piso de 44px que os outros dois ja tinham", () => {
  const c = fonte("src/components/BrandFilter.tsx");
  assert.match(c, /min-h-11 min-w-11/);
  assert.match(c, /shrink-0/);
  assert.match(c, /overflow-x-auto/);
  assert.equal((c.match(/aria-pressed=/g) ?? []).length, 2, "Todas + as cinco marcas");
});

test("SortHeader tematizado: coluna numerica tem o rotulo encostado nos numeros", () => {
  // Medido nas capturas do gate: com `flex-row-reverse justify-end` o rotulo
  // ia para a ESQUERDA da celula, enquanto os valores ficam a direita.
  const c = semComentarios(fonte("src/components/ui/SortHeader.tsx"));
  assert.match(c, /alinhamento === "right" \? "flex-row-reverse justify-start" : "justify-start"/);
  assert.doesNotMatch(c, /flex-row-reverse justify-end/);
});

// ===========================================================================
// 5. Cor so' com regra de negocio por tras
// ===========================================================================
test("ponto de KPI e' identidade (canal ou serie), nunca severidade decorativa", () => {
  // Ticket em ambar e Ads em verde liam como 'em alerta' e 'saudavel' sem
  // nenhuma regra de negocio por tras.
  const kpis = fonte("src/lib/gerencial/kpi-band.ts");
  assert.doesNotMatch(kpis, /accent: "bg-(ok|warn|crit)"/);
  for (const arquivo of [PAGINAS.canais, PAGINAS.financeiro]) {
    assert.doesNotMatch(fonte(arquivo), /accent="bg-(ok|warn|crit)/, arquivo);
  }
});

test("limiares com regra continuam pintando estado", () => {
  // O redesenho nao pode apagar o que TEM regra: ROAS, ACOS, taxas, recompra.
  const fin = fonte(PAGINAS.financeiro);
  assert.match(fin, /if \(v >= 10\) return "text-ok-ink";/, "ROAS >= 10x");
  assert.match(fin, /if \(v < 30\) return "text-warn-ink";/, "taxas 20-30%");
  const can = fonte(PAGINAS.canais);
  assert.match(can, /if \(v >= 15\) return "text-ok-ink bg-ok-soft font-semibold";/, "recompra >= 15%");
});

test("identidade de canal tem token proprio, com o hex de antes no tema claro", () => {
  const css = fonte("app/globals.css");
  // claro: exatamente os hex que CHANNEL_STROKE usava (#8b5cf6, #06b6d4, #f97316)
  assert.match(css, /--tc-canal-tiktok: 139 92 246;/);
  assert.match(css, /--tc-canal-ml: 6 182 212;/);
  assert.match(css, /--tc-canal-shopee: 249 115 22;/);
  const paleta = fonte("src/lib/chart-theme.ts");
  assert.match(paleta, /canais: \{ tiktok: "#8b5cf6", ml: "#06b6d4", shopee: "#f97316" \}/);
});
