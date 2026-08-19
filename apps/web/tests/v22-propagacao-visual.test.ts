/**
 * Gate V2-2, Task 2/2 — propagacao visual as dez superficies.
 *
 * Testes ESTATICOS de contrato material. Nao testam aparencia: travam o que uma
 * regressao silenciosa quebraria — escopo do container, ausencia de fetch novo,
 * um unico shell de dialogo, N/D distinto de zero, `ctx_*` fora dos filtros,
 * limitacoes que precisam continuar visiveis e os contratos do checkpoint TikTok.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf-8");

/** Remove comentarios: uma assertiva nunca deve passar por causa de um comentario. */
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

/** As dez superficies em escopo desta task. A Gerencial NAO esta aqui. */
const ROTAS = [
  ["canais", "app/canais/page.tsx"],
  ["produtos", "app/produtos/page.tsx"],
  ["regioes", "app/regioes/page.tsx"],
  ["financeiro", "app/financeiro/page.tsx"],
  ["qualidade", "app/qualidade/page.tsx"],
  ["pedidos", "app/pedidos/page.tsx"],
  ["tempo-real", "app/tempo-real/page.tsx"],
  ["inteligencia", "app/inteligencia/page.tsx"],
  ["operacoes", "app/operacoes/page.tsx"],
  ["brand", "app/brand/[brand]/page.tsx"],
] as const;

/** Paginas que herdam filtros globais — as unicas que podem ter barra sticky. */
const COM_FILTROS_GLOBAIS = ["canais", "regioes", "financeiro", "qualidade", "pedidos", "brand"];

// ---------------------------------------------------------------------------
// Sistema visual transversal
// ---------------------------------------------------------------------------

test("P1. as dez rotas usam o container compartilhado, e nenhuma mantem max-w-7xl", () => {
  for (const [nome, arquivo] of ROTAS) {
    const src = codeOnly(read(arquivo));
    assert.match(src, /<PageContainer>/, `${nome} deve usar PageContainer`);
    assert.match(src, /<\/PageContainer>/, `${nome} deve fechar PageContainer`);
    assert.doesNotMatch(src, /max-w-7xl/, `${nome} nao pode mais fixar max-w-7xl`);
  }
});

test("P2. o container define largura, respiro e ritmo do V2 num unico lugar", () => {
  const src = codeOnly(read("src/components/layout/PageContainer.tsx"));
  assert.match(src, /max-w-\[1440px\]/);
  assert.match(src, /px-4 sm:px-6/, "mobile/tablet px-4, desktop px-6");
  assert.match(src, /gap-3 sm:gap-4/, "gap-3 abaixo de sm, gap-4 acima");
  assert.doesNotMatch(src, /gap-6/, "gap-6 era o espalhamento que reduzia densidade");
});

test("P3. o cabecalho compartilhado tem 10 consumidores reais? (>=2 justifica a extracao)", () => {
  const consumidores = ROTAS.filter(([, arquivo]) =>
    codeOnly(read(arquivo)).includes("<PageHeader"),
  ).map(([nome]) => nome);
  assert.ok(consumidores.length >= 2, "extracao exige ao menos 2 consumidores reais");
  // brand tem cabecalho proprio (voltar + avatar + pills) e deliberadamente
  // NAO usa PageHeader — seria o unico consumidor daquele contrato.
  assert.ok(!consumidores.includes("brand"), "brand mantem cabecalho proprio");
  assert.equal(consumidores.length, 9, consumidores.join(","));
});

test("P4. barra sticky SO' nas paginas que herdam filtros globais", () => {
  for (const [nome, arquivo] of ROTAS) {
    const src = codeOnly(read(arquivo));
    const passaFiltros = /<PageHeader[\s\S]*?filters=\{/.test(src);
    if (COM_FILTROS_GLOBAIS.includes(nome) && nome !== "brand") {
      assert.ok(passaFiltros, `${nome} herda filtros globais e deve passar filters=`);
    } else if (nome !== "brand") {
      assert.ok(!passaFiltros, `${nome} nao herda filtros globais: barra vazia seria afordancia falsa`);
    }
  }
  // e o sticky vive no componente, nao replicado por pagina
  const header = codeOnly(read("src/components/layout/PageHeader.tsx"));
  assert.match(header, /filters && \(/, "sem filtros, nenhuma faixa e' renderizada");
  assert.match(header, /sticky top-0 z-30/);
});

test("P4b. a barra sticky e IRMA do cabecalho, nao filha de um wrapper curto", () => {
  // Gate V2-3: a presenca da classe `sticky` NAO e' prova de nada. `position:
  // sticky` e' limitado pela caixa do elemento PAI; quando a barra vivia dentro
  // do `<div className="flex flex-col gap-3">` do proprio cabecalho, esse pai
  // terminava logo depois dela e a barra rolava para fora da tela (medido em
  // /canais: topo em -336px no desktop, -667px no mobile). O pai tem de ser o
  // container da pagina inteira — e para isso o componente devolve um Fragment.
  const src = codeOnly(read("src/components/layout/PageHeader.tsx"));

  // 1. Retorno e' um Fragment, nao um <div> unico envolvendo tudo.
  assert.match(src, /return \(\s*<>/, "PageHeader deve devolver um Fragment");
  assert.match(src, /<\/>\s*\);/, "Fragment fechado");

  // 2. O bloco sticky NAO esta dentro do wrapper do cabecalho: o wrapper fecha
  //    antes de a barra comecar.
  const wrapperStart = src.indexOf('<div className="flex flex-col gap-1">');
  const stickyStart = src.indexOf("sticky top-0 z-30");
  assert.ok(wrapperStart > -1 && stickyStart > wrapperStart, "ordem esperada: cabecalho, depois barra");
  // O slice termina onde COMECA a tag da barra, senao contaria o `<div` dela.
  const stickyTag = src.lastIndexOf("<div", stickyStart);
  const entre = src.slice(wrapperStart, stickyTag);
  const abre = (entre.match(/<div/g) || []).length;
  const fecha = (entre.match(/<\/div>/g) || []).length;
  assert.equal(abre, fecha, `o wrapper do cabecalho deve estar FECHADO antes da barra (abre=${abre} fecha=${fecha})`);

  // 3. Nenhuma rota reintroduz uma BARRA DE FILTROS sticky propria (filtros nao
  //    replicados). A assinatura da barra e' `sticky top-0 z-30`; `sticky top-0`
  //    em `<thead>` de tabela rolavel e' outro padrao, preexistente e legitimo,
  //    e continua permitido.
  for (const [nome, arquivo] of ROTAS) {
    assert.doesNotMatch(
      codeOnly(read(arquivo)),
      /sticky top-0 z-30/,
      `${nome}: a barra de filtros deve vir do PageHeader`,
    );
  }

  // 4. O container da pagina — o novo pai do sticky — nao pode ganhar overflow
  //    nem transform, que criariam um novo bloco de contencao e matariam o
  //    sticky outra vez.
  const cont = codeOnly(read("src/components/layout/PageContainer.tsx"));
  assert.doesNotMatch(cont, /overflow-(hidden|auto|scroll|clip)/, "overflow no container quebra o sticky");
  assert.doesNotMatch(cont, /transform|scale-|rotate-|translate-/, "transform cria bloco de contencao");
});

test("P4c. ticks do eixo X: minTickGap volta a valer e o ultimo rotulo nao e cortado", () => {
  const chart = codeOnly(read("src/components/gerencial/EvolutionChart.tsx"));
  // `interval` numerico faz o Recharts ignorar `minTickGap` — era a causa da
  // colisao dos rotulos semanais no mobile.
  assert.match(chart, /const interval = "preserveStartEnd" as const;/);
  assert.doesNotMatch(chart, /interval = buckets\.length > \d+ \?/, "intervalo numerico nao pode voltar");
  assert.match(chart, /minTickGap=\{20\}/, "folga suficiente para o rotulo semanal");
  // margem direita reserva a metade do ultimo rotulo
  assert.match(chart, /margin=\{\{ top: 6, right: 28, left: 0, bottom: 0 \}\}/);
  // e a fonte do tick continua em 12px: nada foi encolhido para caber
  assert.match(chart, /tick=\{\{ fontSize: 12, fill: "#64748b" \}\}/);
});

test("P5. o hack de margem negativa do periodo desapareceu das dez rotas", () => {
  for (const [nome, arquivo] of ROTAS) {
    assert.doesNotMatch(
      codeOnly(read(arquivo)),
      /-mt-3/,
      `${nome}: a linha de escopo pertence ao cabecalho, sem ajuste de pixel`,
    );
  }
});

test("P6. titulo de pagina em text-xl nas dez rotas", () => {
  const header = codeOnly(read("src/components/layout/PageHeader.tsx"));
  assert.match(header, /<h2 className="text-xl font-bold text-gray-900">\{title\}<\/h2>/);
  // brand tem titulo proprio, tambem em text-xl
  assert.match(codeOnly(read("app/brand/[brand]/page.tsx")), /text-xl font-bold text-gray-900 leading-none/);
  // e nenhuma rota mantem titulo de pagina em text-base/text-lg
  for (const [nome, arquivo] of ROTAS) {
    const src = codeOnly(read(arquivo));
    assert.doesNotMatch(src, /<h2 className="text-base font-bold/, `${nome}: titulo de pagina abaixo da escala`);
    assert.doesNotMatch(src, /<h2 className="text-lg font-bold/, `${nome}: titulo de pagina abaixo da escala`);
  }
});

test("P7. o cabecalho compartilhado NAO promove para <h1> (nao agrava a divida U6-04)", () => {
  const header = codeOnly(read("src/components/layout/PageHeader.tsx"));
  assert.doesNotMatch(header, /<h1/, "o <h1> da pagina e' o do shell");
});

test("P8. zero tipografia NOVA abaixo de 12px nos arquivos compartilhados criados", () => {
  for (const f of ["src/components/layout/PageContainer.tsx", "src/components/layout/PageHeader.tsx"]) {
    const src = read(f);
    for (const m of src.matchAll(/text-\[(\d+)px\]|fontSize:\s*(\d+)/g)) {
      const px = Number(m[1] ?? m[2]);
      assert.ok(px >= 12, `${f}: ${m[0]} abaixo de 12px`);
    }
  }
});

// ---------------------------------------------------------------------------
// Nenhum contrato de dados alterado
// ---------------------------------------------------------------------------

/**
 * Conjunto de fetchers de cada rota, REGISTRADO. Serve de guarda de regressao:
 * acrescentar uma fonte a qualquer uma destas telas quebra o teste, e e' isso
 * que se quer — a propagacao visual nao pode introduzir chamada nova.
 */
const FETCHERS_POR_ROTA: Record<string, string[]> = {
  canais: ["fetchCanais"],
  produtos: [
    "fetchProdutosML", "fetchProdutosMLSummary", "fetchProdutosShopee",
    "fetchProdutosShopeeSummary", "fetchProdutosTikTok", "fetchProdutosTikTokSummary",
  ],
  regioes: ["fetchRegioesSummary", "fetchRegioesByUf", "fetchRegioesByBrand", "fetchRegioesTrend"],
  financeiro: ["fetchFinanceiro"],
  qualidade: ["fetchQuality"],
  pedidos: ["fetchPedidos"],
  "tempo-real": ["fetchTempoReal"],
  inteligencia: ["fetchInteligencia"],
  operacoes: ["fetchOperacoes"],
  brand: ["fetchBrandDetail", "fetchDailyRange"],
};

test("P9. nenhum fetch/endpoint novo foi introduzido nas dez rotas", () => {
  for (const [nome, arquivo] of ROTAS) {
    const src = codeOnly(read(arquivo));
    const encontrados = [...new Set([...src.matchAll(/\b(fetch[A-Z][A-Za-z]*)\s*\(/g)].map((m) => m[1]))].sort();
    assert.deepEqual(
      encontrados,
      [...FETCHERS_POR_ROTA[nome]].sort(),
      `${nome}: o conjunto de fontes mudou`,
    );
    // Nenhuma chamada de rede crua fora do api-client. Excecao REGISTRADA:
    // `brand` tem um helper local (`fetchDailyRange`) que monta a URL a mao —
    // divida PRE-EXISTENTE, anterior a esta task, mantida porque corrigi-la
    // mexeria em codigo de dados, fora do escopo de uma propagacao visual.
    const cruasEsperadas = nome === "brand" ? 1 : 0;
    const cruas = (src.match(/\bfetch\(\s*[\s\S]{0,12}?["'`]/g) || []).length;
    assert.equal(cruas, cruasEsperadas, `${nome}: chamada de rede crua fora do api-client`);
    assert.doesNotMatch(src, /axios|XMLHttpRequest/, `${nome}: cliente HTTP alternativo`);
  }
});

test("P10. um unico shell de dialogo, e nenhuma pagina cria modal proprio", () => {
  let comDialogo = 0;
  for (const [nome, arquivo] of ROTAS) {
    const src = codeOnly(read(arquivo));
    const n = (src.match(/<KpiDrilldownDialog/g) || []).length;
    assert.ok(n <= 1, `${nome}: ${n} shells de dialogo (maximo 1)`);
    if (n === 1) comDialogo++;
    // nenhum modal artesanal
    assert.doesNotMatch(src, /role="dialog"/, `${nome}: dialogo deve vir do shell, nao do JSX da pagina`);
    assert.doesNotMatch(src, /createPortal/, `${nome}: portal proprio`);
  }
  assert.ok(comDialogo >= 1, "ao menos uma rota usa o shell (canais)");
});

test("P11. request identity preservada: nenhuma rota perdeu resolvedKey/requestKey", () => {
  const comIdentidade = [
    ["canais", "app/canais/page.tsx"],
    ["regioes", "app/regioes/page.tsx"],
    ["financeiro", "app/financeiro/page.tsx"],
    ["qualidade", "app/qualidade/page.tsx"],
    ["pedidos", "app/pedidos/page.tsx"],
    ["inteligencia", "app/inteligencia/page.tsx"],
    ["operacoes", "app/operacoes/page.tsx"],
  ] as const;
  for (const [nome, arquivo] of comIdentidade) {
    const src = codeOnly(read(arquivo));
    assert.match(src, /setResolvedKey\(/, `${nome}: conclusao da identidade`);
    assert.match(src, /let ignore = false/, `${nome}: guarda contra resposta obsoleta`);
    // O frescor sai da COMPARACAO entre identidade resolvida e atual. Canais
    // ainda faz isso inline (precede o helper `computeRequestStatus` do U5) —
    // a propagacao visual nao mexeu nisso, e este teste registra os dois
    // formatos em vez de forcar um refactor fora do escopo.
    const viaHelper = /computeRequestStatus\(/.test(src);
    const inline = /resolvedKey === requestKey/.test(src);
    assert.ok(viaHelper || inline, `${nome}: frescor precisa comparar resolvedKey com requestKey`);
  }
  // canais e' o unico no formato inline; se algum dia migrar, este teste avisa
  assert.ok(/resolvedKey === requestKey/.test(codeOnly(read("app/canais/page.tsx"))));
  for (const arquivo of [
    "app/regioes/page.tsx", "app/financeiro/page.tsx", "app/qualidade/page.tsx",
    "app/pedidos/page.tsx", "app/inteligencia/page.tsx", "app/operacoes/page.tsx",
  ]) {
    assert.match(codeOnly(read(arquivo)), /computeRequestStatus\(/, `${arquivo}: helper do U5`);
  }
});

test("P12. nenhum keepPreviousData visual foi introduzido", () => {
  for (const [nome, arquivo] of ROTAS) {
    const src = codeOnly(read(arquivo));
    assert.doesNotMatch(src, /keepPreviousData|placeholderData/, `${nome}`);
  }
});

test("P13. ctx_* continua fora de FILTER_QUERY_KEYS", () => {
  const filtros = codeOnly(read("src/lib/filters/nav-links.ts"));
  const keys = filtros.match(/FILTER_QUERY_KEYS[^\]]*\]/s)?.[0] ?? "";
  assert.ok(keys.length > 0, "FILTER_QUERY_KEYS deve existir");
  assert.doesNotMatch(keys, /ctx_/, "ctx_* nunca entra nos filtros preservados");
  for (const k of ["channels", "brands", "date_from", "date_to", "compare"]) {
    assert.ok(keys.includes(k), `FILTER_QUERY_KEYS deve conter ${k}`);
  }
});

test("P14. links de navegacao continuam preservando filtros", () => {
  for (const arquivo of ["app/financeiro/page.tsx", "app/canais/page.tsx", "app/brand/[brand]/page.tsx"]) {
    const src = codeOnly(read(arquivo));
    assert.match(src, /mergeFilteredHref|buildHref/, `${arquivo}: CTAs devem passar por buildHref`);
  }
});

// ---------------------------------------------------------------------------
// Limitacoes de dado que precisam continuar visiveis
// ---------------------------------------------------------------------------

test("P15. TikTok em Qualidade continua N/D, nunca 0%", () => {
  const src = read("app/qualidade/page.tsx");
  assert.match(src, /N\/D/, "N/D precisa aparecer na tela");
  assert.doesNotMatch(src, /tiktok_cancel_rate_pct\s*\?\?\s*0/, "N/D nunca vira zero");
  assert.doesNotMatch(src, /tiktok_return_rate_pct\s*\?\?\s*0/, "N/D nunca vira zero");
});

test("P16. Qualidade nao cria ranking competitivo de cancelamento entre canais", () => {
  const src = codeOnly(read("app/qualidade/page.tsx"));
  assert.doesNotMatch(src, /melhor canal|pior canal|ranking de cancel/i);
});

test("P17. Pedidos: Shopee isolada e' ausencia de cobertura, nao API offline", () => {
  const src = read("app/pedidos/page.tsx");
  assert.match(src, /Shopee sem cobertura nesta visão/);
  const statusBlock = src.slice(src.indexOf("        status={"), src.indexOf("        filters={"));
  assert.ok(statusBlock.indexOf("showShopeeOnly") < statusBlock.indexOf("<LiveStatusBadge"),
    "a cobertura ausente e' checada antes do badge de rede");
});

test("P18. Financeiro: ROAS por canal, sem consolidacao", () => {
  const src = codeOnly(read("app/financeiro/page.tsx"));
  assert.doesNotMatch(src, /roas_consolidado|roasTotal|totalRoas|avgRoas/i, "ROAS nunca e' somado nem mediado");
});

test("P19. Regioes: GMV com cobertura regional nunca se apresenta como GMV total", () => {
  const escopo = codeOnly(read("src/lib/regioes-scope.ts"));
  assert.match(escopo, /REGIONAL_GMV_LABEL/);
  assert.match(escopo, /UF_FILL_LABEL/);
  const src = codeOnly(read("app/regioes/page.tsx"));
  assert.match(src, /REGIONAL_GMV_LABEL/, "o rotulo declarado e' o que vai a tela");
  assert.match(src, /coverageLabel|semCoberturaAviso/, "cobertura do canal continua declarada");
});

test("P20. Tempo Real / Inteligencia / Operacoes: indisponibilidade explicada, sem mock", () => {
  for (const arquivo of ["app/tempo-real/page.tsx", "app/inteligencia/page.tsx", "app/operacoes/page.tsx"]) {
    const src = read(arquivo);
    assert.match(src, /indispon[íi]ve/i, `${arquivo}: precisa nomear a indisponibilidade`);
    assert.doesNotMatch(codeOnly(src), /MOCK_|mockData|fakeData/, `${arquivo}: nenhum mock local`);
  }
  // As duas telas de Data Mart declaram que NAO herdam filtros globais.
  //
  // A verificacao passou a ser da GARANTIA, nao da redacao (Gate V3-1A): a
  // Inteligencia foi reescrita e diz a mesma coisa com outras palavras. Amarrar
  // o teste a uma frase literal transformava a copia em contrato e NAO pegava o
  // defeito que importa — uma tela passar a herdar os filtros de fato. As duas
  // asserçoes abaixo pegam: ausencia do hook de filtros globais, e presenca de
  // uma declaracao explicita ao leitor.
  for (const arquivo of ["app/inteligencia/page.tsx", "app/operacoes/page.tsx"]) {
    const src = read(arquivo);
    assert.doesNotMatch(codeOnly(src), /useGlobalFilters/,
      `${arquivo}: nao pode passar a herdar os filtros globais`);
    assert.match(
      src,
      // tolerante a quebra de linha do JSX — a garantia importa, a formatacao nao
      /filtros globais de canal, marca e período não se aplicam|não\s+respondem ao período global/,
      `${arquivo}: contrato independente declarado ao leitor`,
    );
  }
});

test("P21. brand: banner de chegada e contexto preservados", () => {
  const src = codeOnly(read("app/brand/[brand]/page.tsx"));
  assert.match(src, /<BrandArrivalBanner/);
  assert.match(src, /ctx=\{arrivalCtx\}/);
  assert.match(src, /PageContainer/, "container do V2 aplicado");
  assert.doesNotMatch(src, /<PageHeader/, "cabecalho proprio: unico consumidor daquele contrato");
});

test("P22. TableScrollHint nas tabelas largas das rotas tocadas", () => {
  const comTabelaLarga = [
    "app/canais/page.tsx", "app/financeiro/page.tsx", "app/qualidade/page.tsx",
    "app/pedidos/page.tsx", "app/regioes/page.tsx",
  ];
  for (const arquivo of comTabelaLarga) {
    const src = codeOnly(read(arquivo));
    const tabelas = (src.match(/<table/g) || []).length;
    const hints = (src.match(/<TableScrollHint/g) || []).length;
    assert.ok(hints >= 1, `${arquivo}: nenhuma indicacao de rolagem`);
    assert.ok(hints >= tabelas, `${arquivo}: ${tabelas} tabelas x ${hints} hints`);
  }
});

// ---------------------------------------------------------------------------
// Checkpoint TikTok (frete) — nada implementado nesta task
// ---------------------------------------------------------------------------

test("P23. GMV do TikTok NAO passou a afirmar inclusao de frete", () => {
  const kpi = read("src/lib/kpi-drilldown.ts");
  assert.doesNotMatch(kpi, /inclui o frete|com frete|shipping_fee|total_amount/i,
    "KPI_META nao pode afirmar frete enquanto producao usa sub_total");
  for (const [nome, arquivo] of ROTAS) {
    assert.doesNotMatch(codeOnly(read(arquivo)), /total_amount|shipping_fee/,
      `${nome}: coluna de frete nao foi escolhida nesta task`);
  }
});

test("P24. gmv_video/live/card nao sao apresentados como decomposicao do GMV consolidado", () => {
  for (const [nome, arquivo] of ROTAS) {
    const src = codeOnly(read(arquivo));
    if (!/gmv_video|gmv_live|gmv_card/.test(src)) continue;
    assert.doesNotMatch(src, /gmv_video\s*\+\s*gmv_live|gmv_live\s*\+\s*gmv_card/,
      `${nome}: as colunas de conteudo nao somam o GMV calculado dos pedidos`);
  }
});

test("P25. Produtos TikTok continua no valor de produto, sem rateio de frete", () => {
  const src = codeOnly(read("app/produtos/page.tsx"));
  assert.doesNotMatch(src, /rateio|shipping|frete/i, "nenhuma regra de frete por produto");
  assert.doesNotMatch(src, /estimated_margin/, "campo fora das tabelas atuais");
  // e nenhum ranking consolidado entre marketplaces
  assert.match(src, /ProductMarketplaceTabs/, "cada marketplace na sua aba");
  assert.doesNotMatch(src, /rankingConsolidado|allChannelsRanking/i);
});
