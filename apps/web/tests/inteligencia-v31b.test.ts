// Gate V3-1B — matriz definitiva de oportunidades (bloco 3).
//
// O alvo destes testes é uma regra só, e ela é negativa: o frontend NÃO decide
// nada do mapa. Mediana de GMV, referência de ROAS, classificação em quadrante,
// agregados, faixas e seleção de destaques vêm do `opportunity_map` (BE6).
// Qualquer recomputação aqui divergiria do contrato na primeira mudança de regra
// — por isso há teste estático caçando exatamente isso.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  axisPosition, BAND_KEYS, BAND_META, freshnessLabel, isSample, labelledPoints,
  matrixState, plotPoints, pointRadius, QUADRANT_KEYS, QUADRANT_META,
  contagemExata, readPoint, referenceOrigins, sampleDeclaration, trueSampleNote,
} from "../src/lib/inteligencia/opportunity.ts";
import type { OpportunityMap } from "../src/lib/api-client.ts";

const src = (rel: string) => readFileSync(new URL(`../${rel}`, import.meta.url), "utf8");
const PAGE = "app/inteligencia/page.tsx";
const MATRIZ = "src/components/inteligencia/OpportunityMatrix.tsx";
const OPP = "src/lib/inteligencia/opportunity.ts";
const CLIENT = "src/lib/api-client.ts";

const fmtMoeda = (v: number) => `R$ ${v.toFixed(2)}`;
const fmtRoas = (v: number) => `${v.toFixed(1)}x`;

function mapa(over: Partial<OpportunityMap> = {}): OpportunityMap {
  return {
    scope: "ml_snapshot",
    classification_status: "available",
    brands: ["barbours"],
    total_count: 1000,
    returned_count: 0,
    roas_reference: 8,
    gmv_reference: 2000,
    gmv_reference_basis_count: 800,
    reference_note: "Referências descritivas do portfólio no escopo atual; não são metas comerciais.",
    unclassified_count: 0,
    highlight_limit_per_quadrant: 10,
    highlight_order: "ad_spend_desc_gmv_desc_brand_item",
    quadrants: QUADRANT_KEYS.map((k) => ({ key: k, count: 100, gmv: 10, ad_spend: 1, returned_count: 0 })),
    bands: BAND_KEYS.map((k) => ({ key: k, count: 300, gmv: 5, ad_spend: 0 })),
    highlights: [],
    ...over,
  };
}

const destaque = (
  quadrant: (typeof QUADRANT_KEYS)[number], item_id: string,
  gmv = 5000, ad_spend = 100, ad_roas: number | null = 12,
) => ({ item_id, brand: "barbours", title: `Produto ${item_id}`, gmv, ad_spend, ad_roas, quadrant });

// ═══════════════════════════════════════════════════════════════════════════
// A regra negativa: nada é recalculado no cliente
// ═══════════════════════════════════════════════════════════════════════════

test("V31B: o frontend nao recalcula mediana, referencia nem classificacao", () => {
  for (const rel of [PAGE, MATRIZ, OPP]) {
    const s = src(rel);
    // Procura COMPUTAÇÃO de mediana, não a palavra: a prosa destes arquivos fala
    // de mediana justamente para dizer que ela vem do backend. Por isso o corpo é
    // limpo de comentários e de literais de texto antes da busca.
    const codigo = s
      .replace(/\/\/.*$/gm, "")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/`[^`]*`/g, "``")
      .replace(/"[^"]*"/g, '""');
    assert.ok(!/percentile/i.test(codigo), `${rel}: percentil calculado no cliente`);
    assert.ok(!/\.sort\([^)]*\)\s*\[[^\]]*(?:\/\s*2|length\s*>>\s*1)\]/.test(codigo),
      `${rel}: parece pegar o elemento do meio de um array ordenado`);
    // a classificação nunca é derivada de comparação local
    assert.ok(!/ad_roas\s*>=\s*8|roas\s*>=\s*roas_reference|gmv\s*>=\s*gmv_reference/.test(s),
      `${rel}: parece reclassificar quadrante no cliente`);
  }
});

test("V31B: o quadrante exibido vem SEMPRE do campo do payload", () => {
  const s = src(MATRIZ) + src(OPP);
  assert.ok(/h\.quadrant|p\.quadrant|highlight\.quadrant/.test(s), "o quadrante e lido do destaque");
  // e a metade do plano e derivada do quadrante, nao de comparacao numerica
  assert.ok(/QUADRANT_META\[h\.quadrant\]|QUADRANT_META\[p\.quadrant\]/.test(s));
});

test("V31B: agregados e faixas sao lidos, nunca somados no cliente", () => {
  const s = src(MATRIZ);
  assert.ok(!/reduce\(\s*\(\s*\w+\s*,\s*\w+\s*\)\s*=>\s*\w+\s*\+\s*\w+\.(count|gmv|ad_spend)/.test(s),
    "os agregados do quadrante nao podem ser recompostos no cliente");
});

// ═══════════════════════════════════════════════════════════════════════════
// Universo × agregados × destaques
// ═══════════════════════════════════════════════════════════════════════════

test("V31B: quando returned_count < total_count a UI declara que os pontos sao destaques", () => {
  const m = mapa({ total_count: 1650, returned_count: 40 });
  assert.equal(isSample(m), true);
  const t = sampleDeclaration(m);
  // `1.650`, nao `1650`: a asserção antiga codificava o defeito que o smoke
  // de produção pegou — contagem auditável em pt-BR (ver V3F)
  assert.match(t, /1\.650/, "cita o universo com separador de milhar");
  assert.match(t, /40/, "cita os destaques");
  assert.match(t, /destaques/i);
  assert.match(t, /nunca todos os produtos/i);
});

test("V31B: sem truncamento a UI nao promete amostra", () => {
  const m = mapa({ total_count: 12, returned_count: 12 });
  assert.equal(isSample(m), false);
  assert.match(sampleDeclaration(m), /Todos os 12/);
});

test("V31B: universo zero nao vira frase de amostra", () => {
  assert.match(sampleDeclaration(mapa({ total_count: 0, returned_count: 0 })), /Nenhum produto classificado/);
});

// ═══════════════════════════════════════════════════════════════════════════
// Estados
// ═══════════════════════════════════════════════════════════════════════════

test("V31B: os quatro estados do bloco sao derivados do contrato", () => {
  assert.equal(matrixState(mapa(), ["barbours"]), "available");
  assert.equal(matrixState(mapa({ classification_status: "empty", total_count: 0 }), ["barbours"]), "empty");
  assert.equal(matrixState(mapa({ classification_status: "unavailable_no_positive_gmv", gmv_reference: null }), ["barbours"]), "unavailable");
  assert.equal(matrixState(null, ["barbours"]), "empty");
});

test("V31B: escopo ML vazio (so apice) e estado PROPRIO, distinto de universo zero", () => {
  assert.equal(matrixState(mapa(), []), "out_of_scope");
  // e nao se confunde com o vazio
  assert.notEqual(matrixState(mapa(), []), matrixState(mapa({ total_count: 0 }), ["barbours"]));
});

test("V31B: sem gmv_reference nenhum ponto e plotado — zero quadrante falso", () => {
  const m = mapa({
    classification_status: "unavailable_no_positive_gmv", gmv_reference: null,
    unclassified_count: 20, highlights: [destaque("escalar", "A")], returned_count: 1,
  });
  assert.deepEqual(plotPoints(m), [], "sem eixo de volume nao existe posicao defensavel");
});

test("V31B: o componente nao renderiza quadrante quando a referencia e null", () => {
  const s = src(MATRIZ);
  assert.match(s, /const indisponivel = estado === "unavailable"/);
  assert.match(s, /const pontos = indisponivel \? \[\] : plotPoints\(map\)/);
  assert.match(s, /Matriz indisponível/);
});

// ═══════════════════════════════════════════════════════════════════════════
// As duas faixas nunca se fundem
// ═══════════════════════════════════════════════════════════════════════════

test("V31B: as duas faixas tem explicacoes DIFERENTES e cada uma diz o que nao e", () => {
  const a = BAND_META.sem_ads;
  const b = BAND_META.roas_indisponivel_com_investimento;
  assert.notEqual(a.explicacao, b.explicacao);
  assert.notEqual(a.label, b.label);
  // sem_ads: ausencia de investimento, e diz que NAO e retorno indisponivel
  assert.match(a.explicacao, /não têm investimento|ad_spend.*zero/i);
  assert.match(a.naoConfundir, /não é retorno indisponível/i);
  // a outra: investimento existe, medicao falhou, e NAO e roas baixo
  assert.match(b.explicacao, /TÊM investimento/);
  assert.match(b.explicacao, /falha de mensuração/i);
  assert.match(b.naoConfundir, /Não é ROAS baixo/i);
  assert.match(b.naoConfundir, /ROAS = 0.*retorno baixo medido/is);
});

test("V31B: as duas faixas seguem presentes e separadas, mesmo zeradas", () => {
  assert.deepEqual([...BAND_KEYS], ["sem_ads", "roas_indisponivel_com_investimento"]);
  const s = src(MATRIZ);
  assert.match(s, /duas faixas distintas/i);
  assert.match(s, /BAND_KEYS\.map/);
});

test("V31B: ROAS zero e retorno baixo medido e continua em quadrante", () => {
  const m = mapa({ highlights: [destaque("reduzir_parar", "Z", 100, 50, 0)], returned_count: 1 });
  const [p] = plotPoints(m);
  assert.equal(p.quadrant, "reduzir_parar", "zero fica no quadrante que o backend deu");
  const leitura = readPoint(m.highlights[0], m, fmtMoeda, fmtRoas);
  assert.match(leitura.roasComparacao, /ROAS 0\.0x/, "zero e' exibido como numero");
  assert.ok(!/indispon/i.test(leitura.roasComparacao), "zero nunca e' descrito como indisponivel");
});

test("V31B: ROAS null e indisponibilidade, e a leitura diz isso", () => {
  const m = mapa({ highlights: [destaque("monitorar", "N", 9000, 50, null)], returned_count: 1 });
  const leitura = readPoint(m.highlights[0], m, fmtMoeda, fmtRoas);
  assert.match(leitura.roasComparacao, /indisponível/i);
  assert.ok(!/0\.0x/.test(leitura.roasComparacao), "null nunca vira zero");
});

// ═══════════════════════════════════════════════════════════════════════════
// Fronteiras e posicionamento
// ═══════════════════════════════════════════════════════════════════════════

test("V31B: fronteira alta e inclusiva e o desenho respeita a metade do contrato", () => {
  // valor exatamente na referencia, classificado como ALTO pelo backend
  assert.ok(axisPosition(2000, 2000, true) >= 0.5, "na fronteira, metade alta");
  // o mesmo valor, se o backend disser BAIXO, nao pode vazar para a metade alta
  assert.ok(axisPosition(2000, 2000, false) <= 0.5, "confinado a metade baixa");
});

test("V31B: a posicao nunca contradiz o quadrante atribuido", () => {
  const m = mapa({
    returned_count: 4,
    highlights: [
      destaque("escalar", "E", 90000, 500, 30),
      destaque("testar_investimento", "T", 1, 10, 30),
      destaque("monitorar", "M", 90000, 10, 0.1),
      destaque("reduzir_parar", "R", 1, 1, 0.1),
    ],
  });
  for (const p of plotPoints(m)) {
    const meta = QUADRANT_META[p.quadrant];
    assert.equal(p.x >= 0.5, meta.roasHigh, `${p.quadrant}: metade horizontal errada`);
    assert.equal(p.y >= 0.5, meta.gmvHigh, `${p.quadrant}: metade vertical errada`);
  }
});

test("V31B: raio do ponto cresce com a raiz do investimento e tem piso clicavel", () => {
  assert.equal(pointRadius(0), 4, "piso");
  assert.equal(pointRadius(1), 14, "teto");
  assert.ok(pointRadius(0.25) < pointRadius(1) / 2 + 4.1, "escala por area, nao por raio");
  for (const w of [-1, 0, 0.5, 1, 2]) {
    const r = pointRadius(w);
    assert.ok(r >= 4 && r <= 14, `raio fora da faixa segura: ${r}`);
  }
});

test("V31B: rotulo apenas nos maiores contribuintes de cada quadrante", () => {
  const m = mapa({
    returned_count: 4,
    highlights: [
      destaque("escalar", "grande", 9000, 900), destaque("escalar", "pequeno", 9000, 1),
      destaque("monitorar", "unico", 9000, 5, 0.5),
    ],
  });
  const rot = labelledPoints(plotPoints(m));
  assert.ok(rot.has("grande") && !rot.has("pequeno"), "so o maior de cada quadrante");
  assert.ok(rot.has("unico"));
  assert.ok(rot.size <= QUADRANT_KEYS.length, "no maximo um rotulo por quadrante");
});

// ═══════════════════════════════════════════════════════════════════════════
// Referências: descritivas, com origem declarada
// ═══════════════════════════════════════════════════════════════════════════

test("V31B: a origem de cada referencia e escrita, e nenhuma e chamada de meta", () => {
  const [roas, gmv] = referenceOrigins(mapa(), fmtMoeda);
  assert.match(roas, /corte que a lista "Escalar" já usava/);
  assert.match(gmv, /mediana do GMV/);
  assert.match(gmv, /800 produtos/, "declara a base da mediana");
  assert.match(gmv, /Muda quando o escopo muda/);
  for (const t of [roas, gmv]) assert.ok(!/meta\b/i.test(t) || /não.*meta/i.test(t));
});

test("V31B: sem GMV positivo a origem declara a indisponibilidade em vez de inventar", () => {
  const [, gmv] = referenceOrigins(mapa({ gmv_reference: null, gmv_reference_basis_count: 0 }), fmtMoeda);
  assert.match(gmv, /indisponível/i);
  assert.ok(!/mediana do GMV estritamente/.test(gmv));
});

// ═══════════════════════════════════════════════════════════════════════════
// Frescor (BE4) — jamais inventado
// ═══════════════════════════════════════════════════════════════════════════

test("V31B: frescor null diz que nao esta disponivel, e nao usa o relogio local", () => {
  assert.match(freshnessLabel(null), /indisponível/i);
  assert.match(freshnessLabel("lixo"), /indisponível/i);
  const s = src(OPP);
  const corpo = s.slice(s.indexOf("export function freshnessLabel"));
  assert.ok(!/new Date\(\)/.test(corpo.slice(0, 600)), "nunca `new Date()` como substituto");
});

test("V31B: com timestamp o frescor e formatado a partir do valor recebido", () => {
  const t = freshnessLabel("2026-08-21T09:02:43Z");
  assert.match(t, /Fotografia ML sincronizada em/);
  assert.match(t, /21\/08\/2026/);
});

test("V31B: o frescor ML nao e aplicado a blocos TikTok", () => {
  const s = src(PAGE);
  const i = s.indexOf("frescorMl");
  const usos = [...s.matchAll(/frescorMl/g)].length;
  assert.ok(i > 0 && usos >= 2, "frescor calculado e usado");
  // a unica renderizacao fica no bloco ML do mapa, nao perto de tk_products
  const trecho = s.slice(s.indexOf("{frescorMl}") - 900, s.indexOf("{frescorMl}"));
  assert.ok(/ml_snapshot|Mercado Livre/i.test(trecho), "o frescor aparece no bloco ML");
  assert.ok(!/tk_products/.test(trecho), "nao encosta em tk_products");
});

// ═══════════════════════════════════════════════════════════════════════════
// Totais verdadeiros (BE3)
// ═══════════════════════════════════════════════════════════════════════════

test("V31B: a nota de amostra usa o total verdadeiro em vez de 'ao menos N'", () => {
  assert.equal(trueSampleNote(5, 674, "registro"), "5 de 674 registros no escopo.");
  assert.equal(trueSampleNote(3, 3, "registro"), "3 registros no escopo.");
  assert.equal(trueSampleNote(1, 1, "registro"), "1 registro no escopo.");
  assert.equal(trueSampleNote(0, 0, "registro"), "Nenhum registro neste escopo.");
  for (const t of [trueSampleNote(5, 674, "registro"), trueSampleNote(3, 3, "registro")]) {
    assert.ok(!/ao menos/.test(t), "o 'ao menos' nao sobrevive quando o total e conhecido");
  }
});

test("V31B: a pagina passa os totais do BE3 para os paineis do bloco 5", () => {
  const s = src(PAGE);
  assert.match(s, /total: displayData\?\.urgent_total_count/);
  assert.match(s, /total: displayData\?\.scale_total_count/);
  assert.match(s, /trueSampleNote\(panel\.rows\.length, panel\.total/);
});

// ═══════════════════════════════════════════════════════════════════════════
// Escopo de marca vai para a API
// ═══════════════════════════════════════════════════════════════════════════

test("V31B: fetchInteligencia envia o escopo e o inclui na chave do cache", () => {
  const s = src(CLIENT);
  const i = s.indexOf("export function fetchInteligencia");
  const corpo = s.slice(i, s.indexOf("\n}", i));
  assert.match(corpo, /brands\?: readonly string\[\] \| null/, "aceita o escopo");
  assert.match(corpo, /encodeURIComponent/, "o valor e codificado");
  assert.match(corpo, /`inteligencia:\$\{escopo/, "o escopo entra na chave do cache");
  assert.ok(!/withCache\("inteligencia"/.test(s), "a chave constante nao pode sobreviver");
});

test("V31B: a pagina inclui a marca na identidade da requisicao", () => {
  const s = src(PAGE);
  assert.match(s, /const requestKey = useMemo\(\s*\(\) => `\$\{retryKey\}\|\$\{escopoPedido/);
  assert.match(s, /fetchInteligencia\(escopoPedido\)/);
  assert.match(s, /\}, \[retryKey, escopoPedido\]\)/, "o efeito refaz o fetch quando a marca muda");
});

test("V31B: o universo de marcas so e aprendido de resposta SEM escopo", () => {
  const s = src(PAGE);
  assert.match(s, /if \(!escopoPedido && res\.data\?\.ml_scope_brands\?\.length\)/);
  assert.match(s, /setBrandUniverse\(res\.data\.ml_scope_brands\)/);
});

// ═══════════════════════════════════════════════════════════════════════════
// URLs e diálogo
// ═══════════════════════════════════════════════════════════════════════════

test("V31B: nenhum valor livre, dinheiro, percentual ou titulo viaja na URL", () => {
  const s = src(PAGE) + src(MATRIZ);
  const hrefs = [...s.matchAll(/href=\{`([^`]+)`\}/g)].map((m) => m[1]);
  assert.ok(hrefs.length > 0, "ha links a inspecionar");
  for (const h of hrefs) {
    assert.ok(!/\btitle\b|R\$|fmtBrl|fmtNumber|roasBr|decBr|pctBr|JSON/.test(h),
      `valor livre ou metrica na URL: ${h}`);
    assert.ok(!/gmv|ad_spend|ad_roas/.test(h), `metrica na URL: ${h}`);
  }
});

test("V31B: o CTA do ponto e FRIO — nenhum contexto quente e emitido", () => {
  // O plano reserva o contexto quente de chegada ao V3-2 e exige "wiring real".
  // A pagina de Marca nao tem consumidor de foco de Inteligencia hoje, entao o
  // link leva a marca preservando o filtro, e nada mais.
  const s = src(PAGE);
  const cta = [...s.matchAll(/href=\{`\/brand\/([^`]+)`\}/g)].map((m) => m[1]);
  assert.ok(cta.length > 0, "o ponto tem CTA para a marca");
  for (const h of cta) {
    assert.match(h, /^\$\{[\w.]+\}\?brands=/, `destino de marca fora do formato frio: ${h}`);
    assert.ok(!/ctx_/.test(h), `contexto quente emitido antes do V3-2: ${h}`);
    assert.ok(!/item_id=/.test(h), "identificador de produto nao viaja nesta fase");
  }
});
test("V31B: um unico shell de dialogo, e o bloco 3 nao cria outro", () => {
  const s = src(PAGE);
  assert.equal([...s.matchAll(/<KpiDrilldownDialog/g)].length, 1, "um unico shell");
  const m = src(MATRIZ);
  assert.ok(!/Dialog|createPortal|role="dialog"/.test(m), "a matriz nao cria dialogo proprio");
});

test("V31B: os quatro acionamentos novos existem e sao distintos", () => {
  const s = src(PAGE);
  for (const k of ['kind: "quadrant"', 'kind: "point"', 'kind: "band"', 'kind: "matrix"']) {
    assert.ok(s.includes(k), `estado ausente: ${k}`);
  }
  for (const k of ['dialog?.kind === "quadrant"', 'dialog?.kind === "point"',
                   'dialog?.kind === "band"', 'dialog?.kind === "matrix"']) {
    assert.ok(s.includes(k), `corpo ausente: ${k}`);
  }
});

test("V31B: nenhuma linha inteira e clicavel na matriz", () => {
  const m = src(MATRIZ);
  assert.ok(!/<tr[^>]*onClick/.test(m) && !/cursor-pointer/.test(m.replace(/cursor-pointer focus-visible/g, "")),
    "nenhuma linha/area difusa clicavel; os alvos sao botoes e pontos");
});

// ═══════════════════════════════════════════════════════════════════════════
// Acessibilidade estrutural e tipografia
// ═══════════════════════════════════════════════════════════════════════════

test("V31B: nenhum texto abaixo de 12px nos arquivos autorais do V3-1B", () => {
  for (const rel of [MATRIZ, OPP]) {
    for (const m of src(rel).matchAll(/text-\[(\d+)px\]/g)) {
      assert.ok(Number(m[1]) >= 12, `${rel} renderiza texto a ${m[1]}px`);
    }
    for (const m of src(rel).matchAll(/fontSize:\s*(\d+)/g)) {
      assert.ok(Number(m[1]) >= 12, `${rel} usa fontSize ${m[1]}`);
    }
  }
});

test("V31B: todo acionavel da matriz tem alvo de 44px e nome acessivel", () => {
  const m = src(MATRIZ);
  // A tag vai até o `>` que fecha em linha própria. Um `[\s\S]*?>` ingênuo pararia
  // no `>` de uma arrow function (`() =>`) dentro de `onClick`, cortando a tag
  // antes do `className` — e reprovaria botões que estão corretos.
  const botoes = [...m.matchAll(/<button[\s\S]*?\n\s*>/g)].map((x) => x[0]);
  assert.ok(botoes.length >= 3, `esperava varios botoes, achei ${botoes.length}`);
  for (const b of botoes) {
    assert.ok(/min-h-11/.test(b), `botao sem min-h-11: ${b.slice(0, 90)}`);
    assert.ok(/aria-label=/.test(b), `botao sem nome acessivel: ${b.slice(0, 90)}`);
    assert.ok(/focus-visible:ring/.test(b), `botao sem foco visivel: ${b.slice(0, 90)}`);
  }
  // o ponto do SVG tem alvo concentrico de 44px (r=22)
  assert.match(m, /r=\{22\}/, "alvo de toque de 44px no ponto");
  assert.match(m, /role="button" tabIndex/, "o ponto e alcancavel por teclado");
});

test("V31B: a matriz colapsa em dialogo abaixo de 640px (§13)", () => {
  const m = src(MATRIZ);
  assert.match(m, /hidden sm:block/, "o plano cartesiano some no mobile");
  assert.match(m, /sm:hidden/, "e o botao de abrir aparece so no mobile");
  assert.match(m, /Abrir a matriz/);
  const p = src(PAGE);
  assert.match(p, /onOpenMatrix=\{\(\) => setDialog\(\{ kind: "matrix" \}\)\}/);
  assert.match(p, /semBreakpoint/, "dentro do dialogo a matriz e sempre exibida");
});

test("V31B: o SVG tem nome acessivel e descricao textual do que plota", () => {
  const m = src(MATRIZ);
  assert.match(m, /role="img"/);
  assert.match(m, /aria-label=\{`Matriz de oportunidades com/);
  assert.match(m, /<figcaption className="sr-only">/, "descricao para leitor de tela");
  assert.match(m, /aria-hidden="true"/, "a decoracao do plano e escondida");
});

test("V31B: a cor nunca carrega a informacao sozinha", () => {
  const m = src(MATRIZ);
  // legenda textual dos quadrantes
  assert.match(m, /QUADRANT_META\[k\]\.label/);
  // e cada quadrante tem rotulo escrito no proprio plano
  assert.match(m, /\{m\.label\}/);
});


// ═══════════════════════════════════════════════════════════════════════════
// FINDING 1 (Task 2/2) — frescor real nos drill-downs da matriz
//
// O timestamp sai de `displayData`, não de `data`. Essa é a proteção inteira:
// `displayData = status.fresh ? data : null`, então loading, erro e resposta
// obsoleta já derrubam o timestamp para `null` junto com os dados. Nenhum
// diálogo pode exibir o frescor de uma requisição anterior.
// ═══════════════════════════════════════════════════════════════════════════

test("V31B frescor: o timestamp atual chega aos tres drill-downs da matriz", () => {
  const s = src(PAGE);
  assert.match(s, /const mlRefreshedAt = displayData\?\.ml_snapshot_refreshed_at \?\? null;/);
  assert.equal((s.match(/refreshedAt=\{mlRefreshedAt\}/g) ?? []).length, 3,
    "quadrante, ponto e faixa recebem o frescor do contrato");
});

test("V31B frescor: resposta obsoleta, loading e erro nunca exibem timestamp antigo", () => {
  const s = src(PAGE);
  // `displayData` e' a unica fonte do timestamp, e ela e' nula fora do estado fresh
  assert.match(s, /const displayData = status\.fresh \? data : null;/);
  // e nao existe nenhuma outra origem: nem `data.` direto, nem estado proprio
  assert.ok(!/refreshedAt=\{data\?\./.test(s), "o timestamp nao pode sair de `data` cru");
  assert.ok(!/useState[^;]*refreshedAt/i.test(s), "nenhum timestamp guardado em estado proprio");
});

test("V31B frescor: nenhum relogio local substitui a ausencia", () => {
  const s = src(PAGE) + src(MATRIZ) + src(OPP);
  assert.ok(!/refreshedAt=\{new Date/.test(s), "nunca o relogio do navegador");
  assert.ok(!/refreshedAt=\{Date\./.test(s), "nunca Date.now()");
  // o unico `new Date` da pagina esta em comentario, explicando por que nao se usa
  const codigo = src(PAGE).replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
  assert.ok(!/new Date\(\)/.test(codigo), "nenhum `new Date()` em codigo executavel da pagina");
});

test("V31B frescor: ausencia real permanece null e e' rotulada como indisponivel", () => {
  assert.match(freshnessLabel(null), /indisponível/i);
  // escopo ML vazio: o backend devolve null, e o rotulo diz indisponivel
  assert.match(freshnessLabel(null), /Frescor da fotografia indisponível/);
  // e nunca a palavra "agora"
  assert.ok(!/\bagora\b/i.test(freshnessLabel(null)));
});

// ═══════════════════════════════════════════════════════════════════════════
// FINDING 2 (Task 2/2) — CTA do ponto FRIO, decisão fechada
// ═══════════════════════════════════════════════════════════════════════════

test("V31B CTA: o ponto abre a Marca preservando filtro e sem nenhum ctx_*", () => {
  const s = src(PAGE);
  const cta = [...s.matchAll(/href=\{`\/brand\/([^`]+)`\}/g)].map((m) => m[1]);
  assert.ok(cta.length > 0, "o ponto tem CTA para a marca");
  for (const h of cta) {
    // preserva a marca como filtro do destino
    assert.match(h, /\?brands=/, `o destino perdeu o filtro de marca: ${h}`);
    // e nada de contexto quente, identificador de produto ou valor livre
    assert.ok(!/ctx_/.test(h), `contexto quente emitido antes do V3-2: ${h}`);
    assert.ok(!/item_id/.test(h), `identificador de produto na URL: ${h}`);
    assert.ok(!/gmv|ad_spend|ad_roas|title|R\$|%/.test(h), `metrica ou texto livre na URL: ${h}`);
  }
});
// ═══════════════════════════════════════════════════════════════════════════
const MATRIZ_SRC = src(MATRIZ);

// Patch terminal de formatacao (pos-smoke de producao)
//
// O smoke pegou tres incoerencias cosmeticas na mesma tela: o universo saia
// abreviado (`1.6K`), a declaracao de amostra saia crua (`1650`) e a nota do
// backend vinha sem acento. Estes contratos existem para que nenhuma volte.
// ═══════════════════════════════════════════════════════════════════════════

test("V3F contagemExata: inteiro pt-BR, sem K/M e sem casa decimal", () => {
  assert.equal(contagemExata(0), "0");
  assert.equal(contagemExata(40), "40");
  assert.equal(contagemExata(1650), "1.650");
  assert.equal(contagemExata(1_000_000), "1.000.000");
  // limites vizinhos do ponto em que `fmtNumber` abreviaria
  assert.equal(contagemExata(999), "999");
  assert.equal(contagemExata(1000), "1.000");
  for (const v of [0, 40, 999, 1000, 1650, 1_000_000]) {
    assert.doesNotMatch(contagemExata(v), /[KM]/, String(v));
    assert.doesNotMatch(contagemExata(v), /,/, `${v} nao pode ter casa decimal`);
  }
  // contagem fracionaria nao existe: arredonda em vez de exibir "1.650,4"
  assert.equal(contagemExata(1650.4), "1.650");
  assert.equal(contagemExata(1650.6), "1.651");
});

test("V3F sampleDeclaration usa contagem exata nos tres ramos", () => {
  const amostra = sampleDeclaration(mapa({ total_count: 1650, returned_count: 40 }));
  assert.match(amostra, /universo completo de 1\.650 produtos/);
  assert.match(amostra, /Os 40 pontos plotados/);
  assert.doesNotMatch(amostra, /1650/, "nunca cru");
  assert.doesNotMatch(amostra, /1\.6K/, "nunca abreviado");

  const todos = sampleDeclaration(mapa({ total_count: 1650, returned_count: 1650 }));
  assert.match(todos, /Todos os 1\.650 produtos classificados/);
  assert.doesNotMatch(todos, /1650|1\.6K/);

  assert.match(sampleDeclaration(mapa({ total_count: 0, returned_count: 0 })), /Nenhum produto classificado/);
});

test("V3F a matriz nao usa mais fmtNumber para contagem", () => {
  assert.doesNotMatch(MATRIZ_SRC, /fmtNumber\(/, "nenhuma contagem abreviada no componente");
  assert.match(MATRIZ_SRC, /import \{ fmtBrl \} from "@\/lib\/formatters"/, "dinheiro segue com fmtBrl");
  // as nove contagens do componente passaram ao helper
  // 11 usos: as nove contagens visuais mais as duas da descricao sr-only do
  // plano, que o QA local pegou ainda cruas
  assert.equal((MATRIZ_SRC.match(/contagemExata\(/g) ?? []).length, 11);
  assert.match(MATRIZ_SRC, /\{contagemExata\(map\.returned_count\)\} destaques de um universo de/);
  for (const alvo of [
    "contagemExata(map.total_count)", "contagemExata(map.returned_count)",
    "contagemExata(map.unclassified_count)", "contagemExata(q?.count ?? 0)",
    "contagemExata(q?.returned_count ?? 0)", "contagemExata(b?.count ?? 0)",
  ]) assert.ok(MATRIZ_SRC.includes(alvo), alvo);
  // e o dinheiro NAO foi tocado
  assert.match(MATRIZ_SRC, /fmtBrl\(q\?\.gmv \?\? 0\)/);
  assert.match(MATRIZ_SRC, /fmtBrl\(b\?\.gmv \?\? 0\)/);
});

test("V3F fmtNumber global permanece inalterado", () => {
  const f = src("src/lib/formatters.ts");
  assert.match(f, /if \(value >= 1_000_000\) return `\$\{\(value \/ 1_000_000\)\.toFixed\(1\)\}M`;/);
  assert.match(f, /if \(value >= 1_000\) return `\$\{\(value \/ 1_000\)\.toFixed\(1\)\}K`;/);
  // `fmtNumber(1650)` = "1.7K": a abreviacao de manchete segue deliberada, e e'
  // exatamente por isso que a contagem auditavel precisou de helper proprio.
  assert.match(f, /return value\.toLocaleString\("pt-BR"\);/);
});

test("V3F o patch nao mexeu em metrica, referencia, quadrante nem faixa", () => {
  const m = mapa({ total_count: 1650, returned_count: 40 });
  // referencias e limites intactos
  assert.equal(m.roas_reference, 8);
  assert.equal(m.gmv_reference, 2000, "default do fixture, intocado pelo patch");
  assert.equal(m.highlight_limit_per_quadrant, 10);
  assert.equal(m.quadrants.length, 4);
  assert.equal(m.bands.length, 2);
  // nada de recalculo no frontend
  const opp = src(OPP);
  assert.doesNotMatch(opp, /PERCENTILE|percentile|function median|calcMedian/);
  assert.doesNotMatch(opp, /(gmv_reference|roas_reference|total_count|returned_count)\s*=(?![=>])/);
  // o helper e' de contagem: nao formata dinheiro nem taxa
  const corpo = opp.slice(opp.indexOf("export function contagemExata"));
  assert.doesNotMatch(corpo.slice(0, 220), /currency|BRL|%/);
});

test("V3F a nota de referencia continua vindo do backend, acentuada", () => {
  // o frontend RENDERIZA o campo, sem normalizar nem reescrever
  assert.match(MATRIZ_SRC, /\{map\.reference_note\}/);
  assert.doesNotMatch(MATRIZ_SRC, /reference_note\s*=|normalize|replace\(/);
  const py = readFileSync(new URL("../../api/app/services/gold_service.py", import.meta.url), "utf8");
  assert.match(py, /"Referências descritivas do portfólio no escopo atual; não são metas comerciais\."/);
  assert.doesNotMatch(py, /"Referencias descritivas do portfolio no escopo atual; nao sao metas comerciais\."/);
});
test("V3F o dialogo do quadrante usa contagem exata, nao K/M", () => {
  const PAGE_SRC = src(PAGE);
  // as duas contagens do par universo x destaques
  assert.match(PAGE_SRC, /label="Produtos no quadrante \(universo\)"[\s\S]{0,80}value=\{contagemExata\(q\?\.count \?\? 0\)\}/);
  assert.match(PAGE_SRC, /referenceLabel="Destaques plotados"[\s\S]{0,90}referenceValue=\{contagemExata\(q\?\.returned_count \?\? 0\)\}/);
  assert.doesNotMatch(PAGE_SRC, /value=\{fmtNumber\(q\?\.count \?\? 0\)\}/);
  assert.doesNotMatch(PAGE_SRC, /referenceValue=\{fmtNumber\(q\?\.returned_count \?\? 0\)\}/);

  // comportamento: os numeros que o dialogo vai renderizar
  assert.equal(contagemExata(1650), "1.650");
  assert.equal(contagemExata(40), "40");
  assert.doesNotMatch(contagemExata(1650), /1[.,]6K/);
  assert.doesNotMatch(contagemExata(40), /[KM]/);

  // o dialogo continua no shell compartilhado
  assert.match(PAGE_SRC, /<KpiDrilldownDialog/);
  assert.equal((PAGE_SRC.match(/<KpiDrilldownDialog/g) ?? []).length, 1, "um unico shell");

  // e os OUTROS usos de fmtNumber na pagina seguem intactos
  for (const alvo of [
    "fmtNumber(r.orders)", "nullable(r.total_buyers, fmtNumber)",
    "nullable(r.at_risk_or_churned, fmtNumber)", "fmtNumber(b?.count ?? 0)",
    "fmtNumber(oppMap.total_count)", "fmtNumber(dialog.share.n_products)",
  ]) assert.ok(PAGE_SRC.includes(alvo), alvo);
});
