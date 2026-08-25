// Contrato de alvo mínimo (44x44px) e piso tipográfico (12px) — patch terminal
// de acessibilidade do Gate V3-1A, autorizado pelo proprietário depois do
// stop-loss do QA visual.
//
// Fecha os quatro achados A1-A4:
//   A1 navegação interna da Inteligência renderizava alvo de ~24px;
//   A2 glifo de ordenação renderizava a 10px;
//   A3 segmentos interativos do Pareto renderizavam ~30px;
//   A4 cabeçalhos ordenáveis renderizavam ~40px.
//
// IMPORTANTE — o que estes testes são e o que NÃO são.
//
// `node --test` não tem DOM: aqui não se mede pixel. Estas asserções travam o
// CONTRATO DE CLASSE (a regra explícita que produz o alvo) e o CONTRATO
// FUNCIONAL (ordenação, largura proporcional, handler do Pareto, API pública do
// componente compartilhado). A MEDIÇÃO de verdade é comportamental e foi feita
// em navegador real, com `getBoundingClientRect()` e `getComputedStyle()`, nos
// três viewports — está registrada no §20.10 do plano. Um teste estático que
// passasse enquanto a tela renderizasse 24px seria inútil; por isso ele
// complementa a medição, e não a substitui.
//
// A regra de altura é EXPLÍCITA de propósito. `py-*` mais `line-height` também
// chegaria a 44px por acidente aritmético, e qualquer troca futura de fonte ou
// de padding derrubaria o alvo sem nenhum sinal. `min-h-11` falha alto.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { concentrationByBrand, PARETO_BUCKETS } from "../src/lib/inteligencia/pareto.ts";
import { BRAND_ALL } from "../src/lib/inteligencia/brands.ts";

const src = (rel: string) => readFileSync(new URL(`../${rel}`, import.meta.url), "utf8");

const PAGE = "app/inteligencia/page.tsx";
const SORTABLE = "src/components/SortableHeader.tsx";
const PARETO = "src/components/inteligencia/ConcentrationBars.tsx";

/** Classe utilitária de altura mínima que resolve para 44px. */
const MIN_44 = /\bmin-h-11\b/;

// ---------------------------------------------------------------------------
// A1 — navegação interna da Inteligência
// ---------------------------------------------------------------------------

test("A1: cada link da navegação interna declara alvo de 44px explicitamente", () => {
  const s = src(PAGE);
  const nav = s.slice(s.indexOf('aria-label="Navegação interna da página"'));
  const fim = nav.indexOf("</nav>");
  assert.ok(fim > 0, "bloco <nav> não localizado");
  const bloco = nav.slice(0, fim);

  // compara TOKENS de classe, e nao substrings: `min-w-11` como substring
  // casaria com `min-w-110` e a assercao passaria por acidente.
  const classe = bloco.match(/className="(inline-flex[^"]*)"/)?.[1] ?? "";
  const tokens = new Set(classe.split(/\s+/));

  assert.ok(tokens.has("min-h-11"), "o link interno precisa de min-h-11 (44px)");
  assert.ok(
    tokens.has("inline-flex") && tokens.has("items-center") && tokens.has("justify-center"),
    "min-h-11 so rende alvo real com o conteudo centrado: " + classe,
  );
  assert.ok(tokens.has("min-w-11"), 'rotulo curto ("LTV") mediu 39,8px de largura sem min-w-11');
  assert.ok(!tokens.has("py-1"), "py-1 era a regra implicita que produzia os 24px medidos no QA");
  // a altura e obrigatoria; a largura minima e piso, nao trava
  assert.ok(tokens.has("px-2.5"), "o padding horizontal do rotulo deve ser preservado");
  assert.ok(
    !tokens.has("w-11") && !tokens.has("h-11"),
    "largura/altura fixas cortariam os rotulos longos - o contrato pede piso, nao trava",
  );
});

test("A1: rótulos, destinos e ordem da navegação interna não mudaram", () => {
  const s = src(PAGE);
  const esperado = ["Prioridades", "Oportunidades", "Concentração", "Produtos e mídia", "Fila de evidências", "LTV"];
  const bloco = s.slice(s.indexOf('aria-label="Navegação interna da página"'));
  let cursor = 0;
  for (const label of esperado) {
    const i = bloco.indexOf(`"${label}"`, cursor);
    assert.ok(i > 0, `rótulo ausente ou fora de ordem: ${label}`);
    cursor = i;
  }
  assert.match(bloco, /href=\{`#\$\{anchor\}`\}/, "o destino continua sendo a âncora da seção");
  assert.match(bloco, /focus-visible:ring-violet-500/, "foco visível preservado");
});

// ---------------------------------------------------------------------------
// A2 + A4 — SortableHeader, componente COMPARTILHADO
// ---------------------------------------------------------------------------

test("A2: nenhum glifo do SortableHeader renderiza abaixo de 12px", () => {
  const s = src(SORTABLE);
  assert.ok(!s.includes("text-[10px]"), "text-[10px] no glifo de ordenação");
  const arbitrarios = [...s.matchAll(/text-\[(\d+)px\]/g)].map((m) => Number(m[1]));
  for (const px of arbitrarios) {
    assert.ok(px >= 12, `tamanho arbitrário abaixo do piso: ${px}px`);
  }
  // os dois ramos do ícone (inativo e ativo) precisam do tamanho explícito
  const ramos = [...s.matchAll(/className="text-(?:slate-300|violet-600) (text-\w+) leading-none"/g)].map((m) => m[1]);
  assert.equal(ramos.length, 2, "esperava dois ramos de SortIcon, achei " + ramos.length);
  for (const c of ramos) assert.equal(c, "text-xs", "o glifo deve declarar text-xs (12px)");
});

test("A2: o tamanho do glifo é explícito, não herdado — /pedidos rebaixa o <th> a 10px", () => {
  // `/pedidos` passa `!text-[10px]` no className do <th>. Se o glifo herdasse o
  // tamanho, ele voltaria a 10px naquela tela sem tocar este componente.
  const ped = src("app/pedidos/page.tsx");
  assert.ok(
    ped.includes("!text-[10px]"),
    "premissa do teste mudou: /pedidos não sobrescreve mais o tamanho do <th>",
  );
  const s = src(SORTABLE);
  assert.match(s, /text-slate-300 text-xs leading-none/, "glifo inativo com tamanho próprio");
  assert.match(s, /text-violet-600 text-xs leading-none/, "glifo ativo com tamanho próprio");
});

test("A4: o botão do cabeçalho ordenável declara 44px de altura", () => {
  const s = src(SORTABLE);
  const i = s.indexOf("<button");
  const bloco = s.slice(i, s.indexOf("</button>", i));
  assert.match(bloco, MIN_44, "o botão do cabeçalho precisa de min-h-11 (44px)");
  assert.match(bloco, /w-full h-full/, "a célula inteira continua clicável");
  assert.match(bloco, /px-4 py-3/, "padding original preservado");
  assert.match(bloco, /focus-visible:ring-violet-500/, "foco visível preservado");
});

test("A4: a API pública e a semântica do SortableHeader estão intactas", () => {
  const s = src(SORTABLE);
  // assinatura: nenhuma prop nova, nenhuma removida, mesmo default
  for (const prop of ["label", "column", "sort", "onSort", "align", "className"]) {
    assert.ok(new RegExp(`\\b${prop}\\b`).test(s), `prop ausente da API: ${prop}`);
  }
  assert.match(s, /align = "right"/, "default de align alterado");
  assert.match(s, /onSort\(column\)/, "a lógica de ordenação não pode mudar");
  assert.match(s, /aria-sort=\{ariaSort\}/, "aria-sort perdido");
  assert.match(s, /sort\.direction === "asc" \? "ascending" : "descending"/, "mapeamento de direção alterado");
  assert.match(s, /scope="col"/, "scope=col perdido");
  assert.match(s, /uppercase tracking-wider p-0/, "layout do <th> alterado");
  assert.match(s, /align === "right" \? "justify-end flex-row-reverse" : "justify-start"/, "alinhamento alterado");
});

// ---------------------------------------------------------------------------
// A3 — segmentos interativos do Pareto
// ---------------------------------------------------------------------------

test("A3: o contêiner e cada segmento do Pareto declaram 44px", () => {
  const s = src(PARETO);
  assert.ok(!/\bh-8\b/.test(s), "o contêiner interativo ainda usa h-8 (32px)");
  // min-h no contêiner e min-h no próprio botão: com `h-11` + `border`, o
  // border-box deixava o filho `h-full` em 42px — medido no navegador.
  assert.match(s, /flex min-h-11 rounded-lg/, "o contêiner deve declarar min-h-11 (44px)");
  assert.match(s, /self-stretch min-h-11 min-w-11/, "o segmento precisa garantir 44x44 por conta própria");
  assert.ok(!/h-full min-w/.test(s), "h-full herda a altura do content-box e perde 2px para a borda");
  assert.ok(!s.includes("min-w-[2.5rem]"), "min-w-[2.5rem] são 40px, abaixo do alvo");
});

test("A3: cores, legenda, foco e contrato de clique do Pareto preservados", () => {
  const s = src(PARETO);
  assert.match(s, /width: `\$\{width\}%`/, "largura proporcional ao GMV do bucket");
  assert.match(s, /b\.sharePct == null \? 100 \/ c\.buckets\.length : b\.sharePct/, "regra de largura alterada");
  assert.match(s, /onOpenBucket\(c\.brand, b, c\.totalGmv\)/, "argumentos do drill-down alterados");
  assert.match(s, /aria-label=\{`Detalhe do bucket \$\{BUCKET_LABEL\[b\.bucket\]\} de/, "nome acessível do segmento alterado");
  assert.match(s, /focus-visible:ring-inset focus-visible:ring-white/, "foco visível do segmento perdido");
  assert.match(s, /type="button"/, "o segmento continua sendo um <button> real");
  // legenda textual: a cor nunca carrega a informação sozinha
  assert.match(s, /BUCKET_LETTER\[b\.bucket\]/, "letra do bucket na legenda");
  assert.match(s, /do GMV/, "share textual na legenda");
  for (const b of ["A_top50", "B_next30", "C_next15", "D_tail"]) {
    assert.ok(s.includes(b), `cor do bucket ${b} removida do mapa de preenchimento`);
  }
});

// ---------------------------------------------------------------------------
// Contrato funcional — comportamento puro, não texto de classe
// ---------------------------------------------------------------------------

test("contrato funcional: o patch de altura não tocou o cálculo de concentração", () => {
  const rows = [
    { brand: "barbours", pareto_bucket: "A_top50", n_products: 10, revenue_share_pct: 0.5, gmv: 5000, ad_spend: 100 },
    { brand: "barbours", pareto_bucket: "B_next30", n_products: 20, revenue_share_pct: 0.3, gmv: 3000, ad_spend: 50 },
    { brand: "barbours", pareto_bucket: "C_next15", n_products: 30, revenue_share_pct: 0.15, gmv: 1500, ad_spend: 10 },
    { brand: "barbours", pareto_bucket: "D_tail", n_products: 40, revenue_share_pct: 0.05, gmv: 500, ad_spend: 0 },
  ];
  const [c] = concentrationByBrand({ pareto: rows } as never, BRAND_ALL);
  assert.equal(c.brand, "barbours");
  assert.equal(c.totalProducts, 100);
  assert.equal(c.totalGmv, 10000);
  assert.equal(c.buckets.length, PARETO_BUCKETS.length);
  // as larguras renderizadas vêm daqui: 50/30/15/5
  assert.deepEqual(c.buckets.map((b) => b.sharePct), [50, 30, 15, 5]);
  assert.equal(c.buckets.reduce((s, b) => s + (b.sharePct ?? 0), 0), 100);
});

test("contrato funcional: sem GMV total, sharePct é null e não zero", () => {
  const rows = [
    { brand: "kokeshi", pareto_bucket: "A_top50", n_products: 5, gmv: 0, ad_spend: 0 },
    { brand: "kokeshi", pareto_bucket: "B_next30", n_products: 5, gmv: 0, ad_spend: 0 },
  ];
  const [c] = concentrationByBrand({ pareto: rows } as never, BRAND_ALL);
  assert.equal(c.totalGmv, 0);
  assert.equal(c.buckets.length, 2);
  for (const b of c.buckets) {
    assert.equal(b.sharePct, null, "sem GMV total o share é null, nunca 0 — a barra cai no fallback de largura igual");
  }
});

// ---------------------------------------------------------------------------
// Regressão: os arquivos visuais do V3-1A não podem reintroduzir os defeitos
// ---------------------------------------------------------------------------

test("regressão: nenhum arquivo visual do V3-1A volta a ficar abaixo de 12px", () => {
  for (const rel of [PAGE, PARETO, SORTABLE, "src/components/inteligencia/PriorityCards.tsx", "src/components/inteligencia/EvidenceQueue.tsx"]) {
    for (const m of src(rel).matchAll(/text-\[(\d+)px\]/g)) {
      assert.ok(Number(m[1]) >= 12, `${rel} renderiza texto a ${m[1]}px`);
    }
  }
});

test("regressão: nenhum acionável do V3-1A declara altura fixa abaixo de 44px", () => {
  // h-8/h-9/h-10 num contêiner de <button> foi exatamente o defeito A3.
  for (const rel of [PARETO, "src/components/inteligencia/PriorityCards.tsx", "src/components/inteligencia/EvidenceQueue.tsx"]) {
    const s = src(rel);
    for (const m of s.matchAll(/\bh-(\d+)\b/g)) {
      const px = Number(m[1]) * 4;
      if (px >= 44) continue;
      // só reprova se a altura estiver no mesmo elemento de um acionável
      const ctx = s.slice(Math.max(0, m.index - 400), m.index + 400);
      assert.ok(
        !/<button|onClick/.test(ctx),
        `${rel}: altura fixa de ${px}px perto de um acionável (${m[0]})`,
      );
    }
  }
});
// ═══════════════════════════════════════════════════════════════════════════
// Patch terminal do Gate V3-3 — acessibilidade dos COMPARTILHADOS
//
// O QA integrado do V3-3 fechou funcionalmente, mas com 12 reprovacoes de
// acessibilidade em componentes usados por sete rotas. Estes contratos existem
// para que o proximo gate nao os reintroduza. A medicao final e' no navegador;
// isto aqui e' a rede de seguranca estatica.
// ═══════════════════════════════════════════════════════════════════════════

const CHART = src("src/components/DailyChart.tsx");
const MIX = src("src/components/ChannelMixChart.tsx");
const MKT = src("src/components/MarketplaceFilter.tsx");
const DRF = src("src/components/DateRangeFilter.tsx");
const PER = src("src/components/PeriodSelector.tsx");

/** Fonte sem comentarios: as asserções de contagem e de AUSENCIA precisam
 * olhar markup, nao a prosa que explica a decisao. Um comentario que cita
 * `role="img"` ou `<Legend>` reprovaria justamente o arquivo correto. */
const semCom = (t: string) => t
  .replace(/\/\*[\s\S]*?\*\//g, " ")
  .split(/\r?\n/)
  .map((l) => l.replace(/(^|\s)\/\/.*$/, "$1"))
  .join("\n");
const CHART_CODIGO = semCom(CHART);

test("V33 nenhum grafico renderiza tick abaixo de 12px", () => {
  for (const [nome, src] of [["DailyChart", CHART], ["ChannelMixChart", MIX]] as const) {
    for (const m of src.matchAll(/fontSize:\s*(\d+)/g)) {
      assert.ok(Number(m[1]) >= 12, `${nome} renderiza texto a ${m[1]}px`);
    }
    assert.doesNotMatch(src, /fontSize:\s*1[01]/, nome);
  }
  // o piso vive numa constante nomeada, para nao voltar por descuido
  assert.match(CHART, /const TICK = \{ fontSize: 12/);
});

test("V33 o eixo Y ganhou largura junto com a fonte, para nao cortar o valor", () => {
  // a 12px o rotulo monetario e' mais largo; largura antiga cortava
  assert.match(CHART, /width=\{80\}/);
  assert.match(MIX, /width=\{60\}/);
});

test("V33 o grafico tem UMA representacao acessivel, com nome e descricao", () => {
  assert.match(CHART, /role="img"/);
  assert.match(CHART, /aria-label=\{nomeAcessivel\}/);
  assert.match(CHART, /aria-describedby=\{descId\}/);
  assert.equal((CHART_CODIGO.match(/role="img"/g) ?? []).length, 1, "exatamente uma representacao");
  // o nome e a descricao sao derivados do dado, nao fixos
  assert.match(CHART, /Gráfico de GMV diário/);
  assert.match(CHART, /Séries exibidas/);
  // sem gambiarra de hidratacao
  assert.doesNotMatch(CHART_CODIGO, /suppressHydrationWarning/);
});

test("V33 a descricao distingue ausencia de zero e nao anuncia serie inativa", () => {
  assert.match(CHART, /sem ponto na série, e não é desenhado como zero/);
  assert.match(CHART, /zero medido é desenhado na linha de base/);
  // a legenda sai de `legenda`, que sai de `activeSeries` — nunca de uma lista fixa
  assert.match(CHART, /const legenda = activeSeries\.length > 0/);
  assert.match(CHART, /legenda\.map\(\(l\) => l\.nome\)\.join\(", "\)/);
});

test("V33 a legenda e textual e o marcador colorido e decorativo", () => {
  assert.match(CHART, /aria-label="Séries do gráfico"/);
  assert.match(CHART, /aria-hidden="true"[\s\S]{0,120}backgroundColor: l\.cor/);
  // o nome da serie esta em TEXTO, ao lado do marcador
  assert.match(CHART, /\{l\.nome\}/);
  // o Legend automatico saiu: ele nao garantia nem o texto fora do role=img
  // nem o aria-hidden no icone
  assert.doesNotMatch(CHART_CODIGO, /<Legend/);
  assert.doesNotMatch(CHART_CODIGO, /Legend,/, "Legend saiu tambem do import");
});

test("V33 MarketplaceFilter cumpre 44x44px sem comprimir", () => {
  assert.match(MKT, /min-h-11 min-w-11/);
  assert.match(MKT, /shrink-0/, "sem shrink-0 o flex comprime o alvo e recorta o rotulo");
  assert.match(MKT, /overflow-x-auto/, "a faixa rola em vez de encolher o alvo");
  assert.doesNotMatch(MKT, /py-2 rounded-lg/, "o padding vertical antigo saiu");
});

test("V33 DateRangeFilter cumpre 44px em preset, comparacao e datas", () => {
  // o slice tem de comecar no grupo de presets e terminar no toggle de
  // comparacao — `indexOf("hideCompare")` casava primeiro na interface Props,
  // acima, e devolvia uma faixa vazia
  const presets = DRF.slice(DRF.indexOf('aria-label="Presets de período"'),
    DRF.indexOf("O alvo aqui e' o LABEL"));
  assert.match(presets, /min-h-11 min-w-11/);
  assert.match(presets, /shrink-0/);
  // o alvo do checkbox e o LABEL, que e' o que recebe o clique
  assert.match(DRF, /<label className="inline-flex items-center gap-2 min-h-11 px-2/);
  // e os campos de data crescem no proprio input
  assert.equal((DRF.match(/className="min-h-11 border border-violet-200/g) ?? []).length, 2);
});

test("V33 aria-pressed, labels e validacao de data seguem intactos", () => {
  assert.equal((MKT.match(/aria-pressed=/g) ?? []).length, 2, "Todos + os tres canais");
  assert.match(DRF, /aria-pressed=\{active\}/);
  assert.match(DRF, /role="group"[\s\S]{0,80}aria-label="Presets de período"/);
  assert.match(MKT, /aria-label="Filtro de marketplaces"/);
  // labels dos inputs preservados
  assert.match(DRF, />\s*De\s*</);
  assert.match(DRF, />\s*Até\s*</);
  assert.match(DRF, /Comparar com período anterior/);
  // limites e validacao
  assert.match(DRF, /max=\{dateTo < todayIso \? dateTo : todayIso\}/);
  assert.match(DRF, /min=\{dateFrom\}/);
  assert.match(DRF, /validateDateRange\(next\.dateFrom, next\.dateTo\)/);
});

test("V33 PeriodSelector segue no contrato de 44px do V3-2", () => {
  assert.match(PER, /min-h-11 min-w-11/);
  assert.match(PER, /shrink-0/);
});

test("V33 nenhum onClick em elemento sem semantica de controle", () => {
  for (const [nome, src] of [["DailyChart", CHART], ["ChannelMixChart", MIX], ["MarketplaceFilter", MKT],
                             ["DateRangeFilter", DRF], ["PeriodSelector", PER]] as const) {
    for (const m of src.matchAll(/<(\w+)[^>]*\sonClick=/g)) {
      assert.ok(["button", "a", "input", "label", "select"].includes(m[1]),
        `${nome}: onClick em <${m[1]}>`);
    }
  }
});

test("V33 o sticky da Gerencial cede espacamento, nunca o alvo", () => {
  const hdr = src("src/components/gerencial/GerencialHeader.tsx");
  assert.match(hdr, /sticky top-0 z-30/, "comportamento do V2-4 preservado");
  assert.match(hdr, /py-1\.5/, "padding vertical reduzido");
  assert.match(hdr, /gap-x-3 gap-y-1\.5/, "gap entre linhas reduzido no wrap");
  // e nenhuma altura fixa que pudesse encolher os controles
  assert.doesNotMatch(hdr, /max-h-|h-\[\d+px\]/);
});

test("V33 o patch nao trouxe dependencia nova", () => {
  const pkg = JSON.parse(src("package.json")) as {
    dependencies: Record<string, string>; devDependencies: Record<string, string>;
  };
  assert.equal(Object.keys(pkg.dependencies).length, 7);
  assert.equal(Object.keys(pkg.devDependencies).length, 8);
  assert.ok("recharts" in pkg.dependencies, "o grafico continua no Recharts ja instalado");
  // nada de biblioteca de acessibilidade ou de grafico nova
  for (const proibida of ["@axe-core/react", "victory", "chart.js", "d3", "@visx/visx"]) {
    assert.ok(!(proibida in pkg.dependencies) && !(proibida in pkg.devDependencies), proibida);
  }
});
