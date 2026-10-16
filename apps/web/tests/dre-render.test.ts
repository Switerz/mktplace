/**
 * DRE-UX-2 — testes de RENDERIZACAO dos componentes da DRE.
 *
 * Os testes de `dre.test.ts` provam a logica pura; estes provam o que o
 * gestor LE: renderizam os componentes reais com `react-dom/server` (sem DOM,
 * sem dependencia nova — o `.tsx` e' transpilado pelo `typescript` ja'
 * instalado, ver `helpers/tsx-loader.mjs`) e inspecionam o HTML.
 *
 * Fixtures sinteticas; o caso "agregado bloqueado pela Shopee" reproduz a
 * FORMA medida em 07/2026 (TikTok e ML parciais, Shopee nao calculavel, total
 * nao calculavel), nao os valores.
 */
import { register } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";

register("./helpers/tsx-loader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: DreResumo } = await import("../src/components/dre/DreResumo.tsx");
const { default: DreCanais } = await import("../src/components/dre/DreCanais.tsx");
const { default: DreVertical } = await import("../src/components/dre/DreVertical.tsx");
const { default: Motivos } = await import("../src/components/dre/Motivos.tsx");
const { default: DreFiltros } = await import("../src/components/dre/DreFiltros.tsx");
const { DreMarcasTabela, DreMarcaCanalMatriz, CelulaNaoCalculavel, explicacaoRanking } = await import(
  "../src/components/dre/DreMarcas.tsx"
);
const { canaisBloqueadores, canaisCalculaveisOcultos, FILTROS_PADRAO } = await import("../src/lib/dre.ts");
const fx = await import("./dre-fixtures.ts");

/** HTML -> texto legivel (tags fora, entidades basicas resolvidas). */
const texto = (html: string) =>
  html
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/ /g, " ")
    .replace(/\s+/g, " ")
    .trim();
const render = (el: unknown) => renderToStaticMarkup(el as Parameters<typeof renderToStaticMarkup>[0]);

/** Corta o HTML do painel de canais na secao de um canal. */
function secao(html: string, rotulo: string): string {
  const i = html.indexOf(`aria-label="${rotulo}"`);
  assert.ok(i > 0, `secao ${rotulo} ausente`);
  const fim = html.indexOf("<section", i + 10);
  return texto(html.slice(i, fim > 0 ? fim : undefined));
}

const R_ZERO = /R\$\s?0(?![\d.,])/;

// Total calculavel (TikTok + ML): parcial, Ads ausente em ao menos um canal.
const TOTAL_PARCIAL = fx.celula({
  classificacao: "PARCIAL",
  comps: {
    receita: fx.comp(160_000, "disponivel", { somavel: false }),
    taxas: fx.comp(27_000, "disponivel"),
    ads: fx.comp(5_000, "parcial"),
    frete: fx.comp(12_000, "disponivel"),
    cmv: fx.comp(50_000, "disponivel"),
  },
  margem: 66_000,
  usados: ["taxas", "ads", "frete", "cmv"],
  faltam: ["devolucoes", "ads", "impostos"],
});

const NOVE_MOTIVOS = Array.from({ length: 9 }, (_, i) => `2026-07 shopee marca${i}: taxas nao confirmadas (pergunta C4)`);
const TOTAL_9 = { ...fx.TOTAL_MISTO, motivos_classificacao: NOVE_MOTIVOS };

// ===========================================================================
// Caso real: agregado bloqueado pela Shopee
// ===========================================================================
test("resumo do agregado bloqueado pela Shopee: sem '0 de 6', bloqueador nomeado", () => {
  const r = fx.resposta();
  const t = texto(render(h(DreResumo, { total: r.total, competencia: "07/2026", bloqueadores: canaisBloqueadores(r) })));
  assert.doesNotMatch(t, /0 de 6 custos/);
  assert.doesNotMatch(t, /Faltam: devoluções, taxas/, "nao lista tudo como faltante");
  assert.match(t, /Não calculável — veja os canais/);
  assert.match(t, /Bloqueado por Shopee/);
  assert.doesNotMatch(t, R_ZERO, "margem nula nao vira R$ 0");
});

test("canais: TikTok e ML com margem real; Shopee sem R$ 0; ausencias qualificadas", () => {
  const r = fx.resposta();
  const html = render(h(DreCanais, { canais: r.por_canal, total: r.total }));
  const ml = secao(html, "Mercado Livre");
  const tk = secao(html, "TikTok Shop");
  const sh = secao(html, "Shopee");
  // canal calculavel nunca mostrado como zero
  assert.match(ml, /R\$\s?42\.000/);
  assert.match(ml, /42,0%/);
  assert.match(tk, /R\$\s?24\.000/);
  assert.doesNotMatch(ml + tk, R_ZERO);
  // canal nao calculavel: status, nunca R$ 0, nunca "0 de 6"
  assert.doesNotMatch(sh, R_ZERO, "C6: margem nula da Shopee nao pode virar R$ 0");
  assert.match(sh, /MARGEM CONHECIDA Não calculável|Margem conhecida Não calculável/i);
  assert.doesNotMatch(sh, /0 de 6 custos/);
  assert.match(sh, /Não calculável — veja os motivos/);
  assert.match(sh, /Ausente em ao menos uma marca:/);
  assert.match(sh, /Por que não é calculável/);
});

test("marcas: ranking vazio explicado, mesmo botao do resumo, status no lugar do travessao", () => {
  const r = fx.resposta();
  const naoCalc = [
    fx.MARCA_C,
    { ...fx.MARCA_C, marca: "kokeshi", marca_label: "KOKESHI", motivos_classificacao: ["2026-07 shopee kokeshi: taxas nao confirmadas (pergunta C4)"] },
  ];
  const html = render(
    h(DreMarcasTabela, {
      marcas: naoCalc,
      total: r.total,
      bloqueadores: canaisBloqueadores(r),
      calculaveis: canaisCalculaveisOcultos(r),
      aoFiltrar: () => {},
    }),
  );
  const t = texto(html);
  assert.match(t, /Ausente em ao menos um canal/);
  assert.doesNotMatch(t, /Custos ausentes/);
  assert.match(t, /Ranking vazio: nenhuma marca é comparável neste recorte porque Shopee não é calculável/);
  assert.match(t, /Receita e share continuam válidos/);
  assert.match(t, /Ver só TikTok Shop \+ Mercado Livre/);
  assert.match(t, /Não comparável/);
  assert.doesNotMatch(t, R_ZERO);
  // nenhuma celula de margem/share com travessao solto
  assert.doesNotMatch(html, />—</, "travessao no lugar de status");
  assert.equal((t.match(/Não calculável/g) ?? []).length >= 4, true, "margem e margem % de cada marca dizem o status");
});

test("explicacao do ranking: 0, 1 e >=2 comparaveis", () => {
  assert.match(explicacaoRanking(0, ["Shopee"]), /nenhuma marca é comparável.*Shopee não é calculável/);
  assert.match(explicacaoRanking(1, ["Shopee", "TikTok Shop"]), /só uma marca é comparável.*Shopee e TikTok Shop não são calculáveis/);
  assert.match(explicacaoRanking(0, []), /O motivo de cada marca está na coluna Posição/);
  // Concordancia: um bloqueador "entra", varios "entram".
  assert.match(explicacaoRanking(0, ["Shopee"]), /não é calculável e entra no agregado/);
  assert.match(
    explicacaoRanking(0, ["TikTok Shop", "Mercado Livre", "Shopee"]),
    /TikTok Shop, Mercado Livre e Shopee não são calculáveis e entram no agregado/,
  );
  assert.match(explicacaoRanking(2, ["Shopee"]), /^Posição = ordem de margem/);
});

test("fotografia vencida (medido em 16/10): todos os canais bloqueiam, sem canal calculavel, sem botao", () => {
  // Forma medida: insumo publicado ha mais de 48 h deixa TikTok e ML
  // `snapshot_vencido` -> nao calculaveis, junto com a Shopee.
  const vencido = (c: typeof fx.ML) => ({ ...c, classificacao: "NAO_CALCULAVEL" as const, motivos_classificacao: ["taxas snapshot_vencido"], margem_contribuicao_conhecida: { ...c.margem_contribuicao_conhecida, valor: null, pct_receita: null, componentes_usados: [] } });
  const r = fx.resposta({ por_canal: [vencido(fx.TIKTOK), vencido(fx.ML), fx.SHOPEE] }, "vencido");
  assert.deepEqual(canaisBloqueadores(r), ["TikTok Shop", "Mercado Livre", "Shopee"]);
  assert.deepEqual(canaisCalculaveisOcultos(r), [], "nenhum canal para oferecer");
  const t = texto(
    render(h(DreMarcasTabela, { marcas: [fx.MARCA_C], total: r.total, bloqueadores: canaisBloqueadores(r), calculaveis: canaisCalculaveisOcultos(r), aoFiltrar: () => {} })),
  );
  assert.match(t, /TikTok Shop, Mercado Livre e Shopee não são calculáveis e entram/);
  assert.doesNotMatch(t, /Ver só/, "sem canal calculavel, sem botao vazio");
  const html = render(h(DreCanais, { canais: r.por_canal, total: r.total }));
  assert.doesNotMatch(texto(html), R_ZERO, "canal vencido nunca vira R$ 0");
  assert.equal((texto(html).match(/Não calculável — veja os motivos/g) ?? []).length, 3);
});

test("com ranking preenchido, a tabela nao oferece o botao de recorte", () => {
  const t = texto(
    render(h(DreMarcasTabela, { marcas: [fx.MARCA_A, fx.MARCA_B], total: TOTAL_PARCIAL, calculaveis: ["tiktok", "ml"], aoFiltrar: () => {} })),
  );
  assert.match(t, /1º/);
  assert.doesNotMatch(t, /Ver só/);
});

// ===========================================================================
// Margem parcial no proprio tile
// ===========================================================================
test("tile de margem calculavel diz 'Parcial' e o que falta, no proprio tile", () => {
  const html = render(h(DreResumo, { total: TOTAL_PARCIAL, competencia: "07/2026", bloqueadores: [] }));
  // o tile da margem e' o segundo KpiTile; corta do rotulo ate o proximo tile
  const i = html.indexOf("Margem de contribuição conhecida");
  const tile = texto(html.slice(i, html.indexOf("% da receita", i)));
  assert.match(tile, /R\$\s?66\.000/);
  assert.match(tile, /Parcial/);
  assert.match(tile, /Ausente em ao menos um canal: devoluções, Ads, impostos/);
  const completa = texto(render(h(DreResumo, { total: { ...fx.ML, classificacao: "COMPLETA", margem_contribuicao_conhecida: { ...fx.ML.margem_contribuicao_conhecida, faltam: [] } }, competencia: "07/2026", bloqueadores: [] })));
  assert.match(completa, /Completa/);
  assert.match(completa, /Todos os custos aplicáveis na conta/);
});

// ===========================================================================
// Motivos acessiveis
// ===========================================================================
test("motivos: 'ver mais' e' botao com aria-expanded/aria-controls; aberto mostra todos", () => {
  const fechado = render(h(Motivos, { motivos: NOVE_MOTIVOS, visiveis: 4 }));
  assert.equal((fechado.match(/<li>/g) ?? []).length, 4);
  assert.match(fechado, /<button[^>]*aria-expanded="false"[^>]*aria-controls="[^"]+"/);
  const idLista = fechado.match(/<ul id="([^"]+)"/)![1];
  assert.match(fechado, new RegExp(`aria-controls="${idLista}"`), "o botao controla a lista");
  assert.match(texto(fechado), /Ver mais 5 motivos/);
  const aberto = render(h(Motivos, { motivos: NOVE_MOTIVOS, visiveis: 4, abertoInicial: true }));
  assert.equal((aberto.match(/<li>/g) ?? []).length, 9);
  assert.match(aberto, /aria-expanded="true"/);
  assert.match(texto(aberto), /Mostrar menos/);
});

test("DRE vertical do total com 9 motivos: nenhum fica inalcancavel", () => {
  const html = render(
    h(DreVertical, { opcoes: [{ valor: "total", rotulo: "Total da seleção", celula: TOTAL_9 }], selecionado: "total", aoEscolher: () => {} }),
  );
  const t = texto(html);
  assert.doesNotMatch(t, /e mais \d+ motivo/, "o texto morto sumiu");
  assert.match(t, /Ver mais 5 motivos/);
  assert.match(html, /<button[^>]*aria-expanded="false"/);
  assert.match(t, /— veja os canais/);
  assert.doesNotMatch(t, /0 de 6 custos/);
  assert.match(t, /Ausente em ao menos um canal:/);
});

test("matriz: celula nao calculavel e' botao que abre o motivo por clique/teclado", () => {
  const fechada = render(h(CelulaNaoCalculavel, { c: fx.SHOPEE }));
  assert.match(fechada, /<button type="button" aria-expanded="false" aria-controls="[^"]+"/);
  assert.match(texto(fechada), /por quê\?/);
  const aberta = texto(render(h(CelulaNaoCalculavel, { c: fx.SHOPEE, abertoInicial: true })));
  assert.match(aberta, /taxas nao confirmadas \(pergunta C4\)/);
  assert.match(aberta, /CMV nao reconcilia/);

  const matriz = render(
    h(DreMarcaCanalMatriz, {
      celulas: [{ ...fx.ML, marca: "apice" }, { ...fx.SHOPEE, marca: "apice" }],
      marcas: [fx.MARCA_C],
      canais: ["tiktok", "ml", "shopee"],
    }),
  );
  const tm = texto(matriz);
  assert.match(tm, /42,0%/, "celula calculavel continua visivel");
  assert.match(tm, /Sem venda/, "celula inexistente nao vira zero");
  assert.equal((matriz.match(/aria-expanded="false"/g) ?? []).length, 2, "Shopee e marca (todos) abrem motivo");
});

test("nenhum componente da DRE depende de title nativo", async () => {
  const { readFileSync, readdirSync } = await import("node:fs");
  const { join } = await import("node:path");
  const raiz = join(import.meta.dirname, "..");
  const arquivos = [
    ...readdirSync(join(raiz, "src/components/dre")).map((f) => `src/components/dre/${f}`),
    "app/dre/DreClient.tsx",
  ];
  for (const rel of arquivos) {
    const src = readFileSync(join(raiz, rel), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    // `title="DRE"` do PageHeader e' o titulo da pagina, nao tooltip.
    const achados = [...src.matchAll(/\stitle=\{?["'`]?([^"'`}\s]*)/g)].filter((m) => m[1] !== "DRE");
    assert.deepEqual(achados.map((m) => m[0]), [], `${rel} usa title`);
    assert.doesNotMatch(src, /\scontexto=/, `${rel}: contexto do KpiTile vira title`);
  }
});

// ===========================================================================
// Competencia MM/AAAA no seletor
// ===========================================================================
test("seletor de competencia exibe MM/AAAA, inclusive a madura padrao", () => {
  const html = render(h(DreFiltros, { filtros: FILTROS_PADRAO, mesPadrao: "2026-07", onChange: () => {} }));
  const opcoes = [...html.matchAll(/<option[^>]*>([^<]+)<\/option>/g)].map((m) => m[1]);
  assert.equal(opcoes[0], "Madura · 07/2026");
  assert.equal(opcoes.length, 13);
  for (const o of opcoes.slice(1)) assert.match(o, /^\d{2}\/\d{4}$/, o);
  assert.match(texto(html), /07\/2026 \(madura\)/);
  assert.doesNotMatch(html, /\b(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\/\d{4}/);
});
