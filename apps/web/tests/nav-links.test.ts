// Testes de combinacao de querystring entre os filtros globais atuais e o
// href de um destino (Gate U2 — padrao de drill-down/navegacao da nova
// Gerencial). Cobre a regra de precedencia (destino explicito vence),
// preservacao dos demais filtros, ausencia de query, warning sem href e
// encoding correto — ver docs/UI_REVAMP_PLAN.md Task 7.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mergeFilteredHref,
  mergeOptionalFilteredHref,
  FILTER_QUERY_KEYS,
} from "../src/lib/filters/nav-links.ts";
import { NAV_SECTIONS } from "../src/components/shell/nav-config.ts";

function currentSearch(query: string): URLSearchParams {
  return new URLSearchParams(query);
}

test("preserva os filtros globais atuais quando o destino nao tem query propria", () => {
  const current = currentSearch("channels=ml&brands=kokeshi&date_from=2026-06-01&date_to=2026-06-30&compare=true");
  const href = mergeFilteredHref("/canais", current);
  const [path, query] = href.split("?");
  assert.equal(path, "/canais");
  const params = new URLSearchParams(query);
  assert.equal(params.get("channels"), "ml");
  assert.equal(params.get("brands"), "kokeshi");
  assert.equal(params.get("date_from"), "2026-06-01");
  assert.equal(params.get("date_to"), "2026-06-30");
  assert.equal(params.get("compare"), "true");
});

test("destino sobrescreve o filtro atual na mesma chave (precedencia explicita)", () => {
  const current = currentSearch("brands=barbours&channels=all");
  const href = mergeFilteredHref("/canais?brands=kokeshi", current);
  const params = new URLSearchParams(href.split("?")[1]);
  assert.equal(params.get("brands"), "kokeshi");
  // demais filtros globais nao mencionados pelo destino continuam preservados
  assert.equal(params.get("channels"), "all");
});

test("href sem query e sem filtros atuais retorna apenas o path, sem '?' pendurado", () => {
  const href = mergeFilteredHref("/regioes", new URLSearchParams());
  assert.equal(href, "/regioes");
});

test("warning sem href (null) nunca vira link falso", () => {
  const current = currentSearch("channels=ml");
  assert.equal(mergeOptionalFilteredHref(null, current), null);
  // com href real, continua resolvendo normalmente
  assert.equal(mergeOptionalFilteredHref("/regioes", current), "/regioes?channels=ml");
});

test("encoding correto — nao duplica nem corrompe caracteres ja codificados pelo destino", () => {
  const destQuery = new URLSearchParams({ brands: "ana maria", note: "a&b" }).toString();
  const current = currentSearch("channels=tiktok,ml");
  const href = mergeFilteredHref(`/canais?${destQuery}`, current);
  const params = new URLSearchParams(href.split("?")[1]);
  assert.equal(params.get("brands"), "ana maria");
  assert.equal(params.get("note"), "a&b");
  assert.equal(params.get("channels"), "tiktok,ml");
});

test("nao herda parametros fora do contrato de filtros globais do estado atual", () => {
  const current = currentSearch("channels=ml&sort=desc&page=2");
  const href = mergeFilteredHref("/canais", current);
  const params = new URLSearchParams(href.split("?")[1]);
  assert.equal(params.get("channels"), "ml");
  assert.equal(params.get("sort"), null);
  assert.equal(params.get("page"), null);
});

test("FILTER_QUERY_KEYS cobre exatamente as chaves do contrato de filtros globais", () => {
  assert.deepEqual([...FILTER_QUERY_KEYS].sort(), ["brands", "channels", "compare", "date_from", "date_to"]);
});

// Regressao — nenhuma rota existente foi removida da navegacao neste gate.
test("regressao: NAV_SECTIONS continua cobrindo todas as rotas do inventario do U0", () => {
  const hrefs = NAV_SECTIONS.flatMap((s) => s.pages.map((p) => p.href));
  const expected = [
    "/", "/canais", "/produtos", "/qualidade", "/financeiro", "/regioes", "/tempo-real",
    "/pedidos", "/inteligencia", "/operacoes",
  ];
  for (const href of expected) {
    assert.ok(hrefs.some((h) => h === href), `rota ausente: ${href}`);
  }
});
