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
