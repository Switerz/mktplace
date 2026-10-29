// Testes da estrutura de navegacao do shell (Sidebar/MobileDrawer) — Gate U1.
// Roda via `node --test` com type-stripping nativo do Node.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  EXPEDICAO_NAV,
  NAV_SECTIONS,
  PERFORMANCE_CONTAS_NAV,
  SHOPEE_FBS_NAV,
  getRouteTitle,
  isNavItemActive,
  navSections,
} from "../src/components/shell/nav-config.ts";
import { habilitado as performanceContasHabilitado } from "../src/lib/performance-contas.ts";
import { buildPreservedQuery, hrefForPage } from "../src/lib/filters/nav-links.ts";

test("NAV_SECTIONS mantem os grupos existentes, na ordem original", () => {
  assert.deepEqual(
    NAV_SECTIONS.map((s) => s.label),
    [
      "Cockpits",
      "Pedidos",
      "Inteligência",
      "Operações",
      // Gate AVH-4B-S — unico grupo acrescentado desde o Gate U1. Fica no fim
      // e isolado de proposito: dado de terceiro, carga manual, nao e' KPI.
      // METAS-1 — grupo de entrada de dado do time, antes das referencias externas.
      "Planejamento",
      "Referências externas",
    ],
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
    // DRE-UX-1 — rota de topo ao lado do Financeiro, nao filha dele
    // (`isNavItemActive` casa por prefixo).
    "/dre",
    "/regioes",
    "/tempo-real",
    "/pedidos",
    // Gate EXP-UX-2C — `/pedidos/tiktok` e `/pedidos/ml` sairam. Eram itens
    // `disabled` com selo "Em breve" desde o Gate U1: promessa parada ocupa a
    // altura de um item real e ensina o operador a ignorar a regiao da lista.
    // Voltam como itens de verdade quando as rotas existirem.
    "/inteligencia",
    // Gate PMA-3 — primeira rota acrescentada desde o Gate U1. Nenhuma
    // removida nem renomeada; o pino literal continua exigindo revisao
    // consciente.
    "/monitoramento-preco",
    "/operacoes",
    // Gate FULL-1D — superficie de modalidade logistica do Mercado Livre.
    // Rota de TOPO, e nao filha de /operacoes: `isNavItemActive` casa por
    // prefixo, e um filho deixaria os dois itens ativos ao mesmo tempo.
    // Nao e estoque nem expedicao; mede por onde o pedido foi enviado.
    "/full-ml",
    // METAS-1 — metas mensais de GMV por marca x canal (grupo Planejamento).
    "/metas",
    // Gate AVH-4B-S Task 2/2 — referencias externas da Avoe.
    "/referencias-externas/avoe",
  ]);
});

test("Gate PMA-3: /monitoramento-preco fica em Inteligencia, habilitada, sem badge", () => {
  const inteligencia = NAV_SECTIONS.find((s) => s.label === "Inteligência")!;
  const rotulos = inteligencia.pages.map((p) => p.label);
  assert.deepEqual(rotulos, ["Ações ML + TikTok", "Monitoramento de preços"]);
  const pma = inteligencia.pages.find((p) => p.href === "/monitoramento-preco")!;
  assert.equal(pma.disabled, undefined);
  assert.equal(pma.badge, undefined);
  // O rotulo NAO usa "PMA": a referencia e' preco sugerido de revenda.
  assert.ok(!/\bPMA\b/.test(pma.label));
});

test("Gate PMA-3: rota ativa e titulo da topbar", () => {
  assert.equal(isNavItemActive("/monitoramento-preco", "/monitoramento-preco"), true);
  assert.equal(getRouteTitle("/monitoramento-preco"), "Monitoramento de preços");
  // Nao rouba a rota de /inteligencia.
  assert.equal(isNavItemActive("/inteligencia", "/monitoramento-preco"), false);
  assert.equal(getRouteTitle("/inteligencia"), "Ações ML + TikTok");
});

test("Gate EXP-UX-2C: nenhum item do menu e' uma promessa desabilitada", () => {
  // Antes deste gate, "TikTok Shop" e "Mercado Livre" viviam em Pedidos como
  // itens `disabled` com selo "Em breve". A assercao virou o INVERSO, e mais
  // forte: nenhuma secao pode ter item desabilitado. Item que nao leva a lugar
  // nenhum ocupa a altura de um real, some da navegacao por teclado e ensina o
  // operador a ignorar aquela regiao da lista.
  const pedidos = NAV_SECTIONS.find((s) => s.label === "Pedidos")!;
  assert.deepEqual(
    pedidos.pages.map((p) => p.href),
    ["/pedidos"],
    "Pedidos ficou so' com a rota que existe",
  );
  for (const s of NAV_SECTIONS) {
    for (const p of s.pages) {
      assert.notEqual(p.disabled, true, `${s.label} > ${p.label} desabilitado`);
      assert.equal(p.badge, undefined, `${s.label} > ${p.label} com selo`);
    }
  }
});

test("Gate EXP-UX-2C: a Expedicao no menu nao diz o canal", () => {
  // A tela e' multicanal: o menu dizer "Shopee" contradiz a tela aberta no ML.
  assert.equal(EXPEDICAO_NAV.label, "Expedição");
  assert.equal(EXPEDICAO_NAV.href, "/expedicao", "a ROTA nao pode mudar");
  assert.ok(!/Shopee|Mercado Livre/.test(EXPEDICAO_NAV.label));
  // e as duas telas Full, que nomeiam canal de proposito, seguem intactas
  assert.equal(SHOPEE_FBS_NAV.label, "Full Shopee");
  assert.equal(SHOPEE_FBS_NAV.href, "/full-shopee");
  const ops = NAV_SECTIONS.find((s) => s.label === "Operações")!;
  assert.ok(ops.pages.some((p) => p.href === "/full-ml" && p.label === "Full Mercado Livre"));
});

test("Gate EXP-UX-2C: o estado ativo do menu nao mudou", () => {
  // Mesma funcao serve Sidebar (desktop) e MobileDrawer: um so' teste cobre os
  // dois, e e' por isso que a regra vive no config e nao em cada componente.
  assert.equal(isNavItemActive("/expedicao", "/expedicao"), true);
  assert.equal(isNavItemActive("/expedicao", "/expedicao?channel=mercadolivre"), true);
  assert.equal(isNavItemActive("/expedicao", "/pedidos"), false);
  assert.equal(isNavItemActive("/pedidos", "/pedidos"), true);
  // as rotas removidas nao podem reacender o item pai
  assert.equal(isNavItemActive("/pedidos", "/pedidos/tiktok"), true);
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

test("T-0014: Performance das contas ausente do menu com a flag desligada (padrao)", () => {
  const hrefs = (secoes: ReturnType<typeof navSections>) =>
    secoes.flatMap((s) => s.pages.map((p) => p.href));
  assert.ok(!hrefs(NAV_SECTIONS).includes("/performance-contas"));
  assert.ok(!hrefs(navSections()).includes("/performance-contas"));
  assert.ok(!hrefs(navSections({ performanceContas: false })).includes("/performance-contas"));
  // Flag derivada do mesmo helper fail-closed da tela: so' "true" exato liga.
  for (const v of [undefined, "", "1", "TRUE", "yes"]) {
    const flag = performanceContasHabilitado({ NEXT_PUBLIC_PERFORMANCE_CONTAS_ENABLED: v });
    assert.ok(!hrefs(navSections({ performanceContas: flag })).includes("/performance-contas"));
  }
  // Ligar as OUTRAS flags nao revela este item.
  const outras = navSections({ expedicao: true, shopeeFbs: true, estoqueFull: true, mlFullEstoque: true });
  assert.ok(!hrefs(outras).includes("/performance-contas"));
});

test("T-0014: Performance das contas presente em Operações com a flag ligada", () => {
  const flag = performanceContasHabilitado({ NEXT_PUBLIC_PERFORMANCE_CONTAS_ENABLED: "true" });
  assert.equal(flag, true);
  const secoes = navSections({ performanceContas: flag });
  // Nenhum grupo novo.
  assert.deepEqual(secoes.map((s) => s.label), NAV_SECTIONS.map((s) => s.label));
  const comItem = secoes.filter((s) => s.pages.some((p) => p.href === "/performance-contas"));
  assert.deepEqual(comItem.map((s) => s.label), ["Operações"]);
  const item = comItem[0].pages.find((p) => p.href === "/performance-contas")!;
  assert.deepEqual(item, { href: "/performance-contas", label: "Performance das contas" });
  assert.equal(PERFORMANCE_CONTAS_NAV.label, "Performance das contas");
  // Nao rouba o estado ativo de outra rota (casamento por prefixo).
  for (const s of secoes) {
    for (const p of s.pages) {
      if (p.href === "/performance-contas" || p.href === "/") continue;
      assert.equal(isNavItemActive(p.href, "/performance-contas"), false, p.href);
    }
  }
  assert.equal(isNavItemActive("/performance-contas", "/performance-contas"), true);
  assert.equal(getRouteTitle("/performance-contas"), "Performance das contas");
});
