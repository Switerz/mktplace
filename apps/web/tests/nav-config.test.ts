// Testes da estrutura de navegacao do shell (Sidebar/MobileDrawer) — Gate U1.
// Roda via `node --test` com type-stripping nativo do Node.
import { test } from "node:test";
import assert from "node:assert/strict";
import { NAV_SECTIONS, isNavItemActive, getRouteTitle } from "../src/components/shell/nav-config.ts";
import { buildPreservedQuery, hrefForPage } from "../src/lib/filters/nav-links.ts";

test("NAV_SECTIONS mantem os 4 grupos existentes, na ordem original", () => {
  assert.deepEqual(
    NAV_SECTIONS.map((s) => s.label),
    ["Cockpits", "Pedidos", "Inteligência", "Operações"],
  );
});

test("NAV_SECTIONS mantem exatamente as rotas atuais (nenhuma nova, nenhuma removida)", () => {
  const hrefs = NAV_SECTIONS.flatMap((s) => s.pages.map((p) => p.href));
  assert.deepEqual(hrefs, [
    "/",
    "/canais",
    "/produtos",
    "/qualidade",
    "/financeiro",
    "/regioes",
    "/tempo-real",
    "/pedidos",
    "/pedidos/tiktok",
    "/pedidos/ml",
    "/inteligencia",
    "/operacoes",
  ]);
});

test("TikTok Shop e Mercado Livre em Pedidos continuam desabilitados com badge 'Em breve'", () => {
  const pedidos = NAV_SECTIONS.find((s) => s.label === "Pedidos")!;
  const tiktok = pedidos.pages.find((p) => p.href === "/pedidos/tiktok")!;
  const ml = pedidos.pages.find((p) => p.href === "/pedidos/ml")!;
  assert.equal(tiktok.disabled, true);
  assert.equal(tiktok.badge, "Em breve");
  assert.equal(ml.disabled, true);
  assert.equal(ml.badge, "Em breve");
  const geral = pedidos.pages.find((p) => p.href === "/pedidos")!;
  assert.equal(geral.disabled, undefined);
});

test("isNavItemActive: Gerencial ativa em / e em qualquer /brand/[brand]", () => {
  assert.equal(isNavItemActive("/", "/"), true);
  assert.equal(isNavItemActive("/", "/brand/kokeshi"), true);
  assert.equal(isNavItemActive("/", "/brand/barbours"), true);
  assert.equal(isNavItemActive("/", "/canais"), false);
});

test("isNavItemActive: demais rotas ativam por prefixo exato do proprio href", () => {
  assert.equal(isNavItemActive("/canais", "/canais"), true);
  assert.equal(isNavItemActive("/canais", "/canais"), true);
  assert.equal(isNavItemActive("/produtos", "/canais"), false);
  assert.equal(isNavItemActive("/pedidos", "/pedidos"), true);
});

test("getRouteTitle: reconhece cada rota de Cockpits pelo label do item", () => {
  assert.equal(getRouteTitle("/"), "Gerencial");
  assert.equal(getRouteTitle("/canais"), "Canais");
  assert.equal(getRouteTitle("/produtos"), "Produtos");
  assert.equal(getRouteTitle("/qualidade"), "Qualidade");
  assert.equal(getRouteTitle("/financeiro"), "Financeiro");
  assert.equal(getRouteTitle("/regioes"), "Regiões");
  assert.equal(getRouteTitle("/tempo-real"), "Tempo Real");
});

test("getRouteTitle: /brand/[brand] usa a mesma associacao visual da Gerencial", () => {
  assert.equal(getRouteTitle("/brand/kokeshi"), "Gerencial");
  assert.equal(getRouteTitle("/brand/qualquer-marca-nova"), "Gerencial");
});

test("getRouteTitle: rotas fora do mapa caem no titulo padrao", () => {
  assert.equal(getRouteTitle("/rota-inexistente"), "Torre de Controle");
});

test("buildPreservedQuery + hrefForPage: preserva filtros apenas em paginas filter-aware", () => {
  const params = new Map([["channels", "tiktok,ml"], ["compare", "true"]]);
  const getParam = (key: string) => params.get(key) ?? null;

  const preserved = buildPreservedQuery("/", getParam);
  assert.equal(preserved, "channels=tiktok%2Cml&compare=true");
  assert.equal(hrefForPage("/canais", preserved), "/canais?channels=tiktok%2Cml&compare=true");

  // Produtos/Tempo Real/Inteligencia/Operacoes nao fazem parte do contrato —
  // nunca herdam a querystring, mesmo que ela exista.
  const notFilterAware = buildPreservedQuery("/produtos", getParam);
  assert.equal(notFilterAware, "");
  assert.equal(hrefForPage("/produtos", notFilterAware), "/produtos");
});

test("hrefForPage: paginas fora do contrato de filtros nunca recebem querystring anexada", () => {
  assert.equal(hrefForPage("/tempo-real", "channels=ml"), "/tempo-real");
  assert.equal(hrefForPage("/inteligencia", "channels=ml"), "/inteligencia");
  assert.equal(hrefForPage("/operacoes", "channels=ml"), "/operacoes");
});
