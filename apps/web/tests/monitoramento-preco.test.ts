// Gate PMA-3 — contraprovas da tela de monitoramento de precos proprios.
// Roda via `node --test` com type-stripping nativo do Node, como os demais.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  buildMonitoramentoPrecoQuery,
  MONITORAMENTO_PRECO_MAX_LIMIT,
  type MonitoramentoPrecoKpis,
  type MonitoramentoPrecoRow,
} from "../src/lib/monitoramento-preco-contract.ts";
import {
  AVISO_OBSERVACIONAL,
  COMPOSICAO_INDISPONIVEL,
  INDISPONIVEL,
  NOTA_DENOMINADOR,
  STATUS_LABELS,
  STATUS_ORDER,
  avisoTruncamento,
  brandLabel,
  buildKpiViews,
  buildMonitoramentoRequestKey,
  buildQualidadeViews,
  calcPaginacao,
  fmtCapturaPreco,
  fmtContagem,
  fmtData,
  fmtDiferenca,
  fmtMoeda,
  fmtPercentual,
  listingStatusLabel,
  matchLabel,
  matchQualityLabel,
  statusFecha,
  statusLabel,
  urlAnuncioSegura,
} from "../src/lib/monitoramento-preco.ts";

const PAGE = new URL("../app/monitoramento-preco/page.tsx", import.meta.url);
const LIB = new URL("../src/lib/monitoramento-preco.ts", import.meta.url);

async function ler(url: URL): Promise<string> {
  const { readFile } = await import("node:fs/promises");
  return readFile(url, "utf8");
}

/**
 * Codigo SEM comentario. As varreduras de palavra proibida ("severidade",
 * "mock", "denuncia") tem de olhar o que o modulo FAZ, nao o que ele explica:
 * as proprias docstrings declaram essas proibicoes, e uma varredura ingenua
 * reprovaria justamente a documentacao da garantia.
 */
async function lerCodigo(url: URL): Promise<string> {
  const bruto = await ler(url);
  return bruto
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1 ");
}

/**
 * Tags de abertura de elementos interativos, com seus atributos.
 *
 * A seta `=>` das arrow functions contem `>`, e um match lazy ate o primeiro
 * `>` cortaria a tag antes do `className`. Por isso a seta e' neutralizada
 * antes de fatiar — sem isso o teste media 4 elementos em vez de todos.
 */
function tagsInterativas(codigo: string): string[] {
  const semSeta = codigo.replace(/=>/g, "@@");
  const achadas = semSeta.match(/<(?:button|select|input|a)\s[\s\S]*?\/?>/g) ?? [];
  return achadas.filter((el) => el.includes("className="));
}

// ---------------------------------------------------------------------------
// 1. Cliente HTTP e URL
// ---------------------------------------------------------------------------

test("query envia marketplace=ml por padrao", () => {
  const qs = buildMonitoramentoPrecoQuery({});
  assert.equal(qs.get("marketplace"), "ml");
});

test("query monta todos os filtros suportados", () => {
  const qs = buildMonitoramentoPrecoQuery({
    brand: "rituaria",
    status: "below_reference",
    productQuery: "RT01016",
    limit: 500,
    offset: 500,
  });
  assert.equal(qs.get("brand"), "rituaria");
  assert.equal(qs.get("status"), "below_reference");
  assert.equal(qs.get("product_query"), "RT01016");
  assert.equal(qs.get("limit"), "500");
  assert.equal(qs.get("offset"), "500");
});

test("query NUNCA envia ref_date, mesmo se alguem passar a chave", () => {
  const qs = buildMonitoramentoPrecoQuery({
    // @ts-expect-error — nao existe no tipo, de proposito
    ref_date: "2026-08-01",
    refDate: "2026-08-01",
  });
  assert.equal(qs.get("ref_date"), null);
  assert.equal(qs.get("refDate"), null);
  assert.ok(!qs.toString().includes("ref_date"));
});

test("o codigo da pagina e do cliente nao menciona filtro de data", async () => {
  const [pagina, lib] = await Promise.all([ler(PAGE), ler(LIB)]);
  for (const [nome, texto] of [["page", pagina], ["lib", lib]] as const) {
    assert.ok(!/\bref_date\s*[:=]\s*["'`]/.test(texto), nome);
    assert.ok(!/dateFrom|dateTo|DateRangeFilter/.test(texto), nome);
  }
});

test("teto de limite e o do backend", () => {
  assert.equal(MONITORAMENTO_PRECO_MAX_LIMIT, 500);
});

// ---------------------------------------------------------------------------
// 2. NULL nunca vira zero
// ---------------------------------------------------------------------------

test("moeda ausente vira marcador, nunca R$ 0,00", () => {
  for (const v of [null, undefined, NaN]) {
    assert.equal(fmtMoeda(v as number | null), INDISPONIVEL);
  }
  assert.ok(!fmtMoeda(null).includes("0,00"));
  // Zero MEDIDO continua sendo zero — o que nao pode e' ausencia virar zero.
  assert.ok(fmtMoeda(0).includes("0,00"));
});

test("diferenca ausente vira marcador; percentual ausente nao vira 0%", () => {
  assert.equal(fmtDiferenca(null), INDISPONIVEL);
  assert.equal(fmtPercentual(null), INDISPONIVEL);
  assert.ok(!fmtPercentual(null).includes("0"));
});

test("contagem e data ausentes viram marcador", () => {
  assert.equal(fmtContagem(null), INDISPONIVEL);
  assert.equal(fmtData(null), INDISPONIVEL);
  assert.equal(fmtCapturaPreco(null), INDISPONIVEL);
});

// ---------------------------------------------------------------------------
// 3. Formatacao — sinal preservado, sem abreviacao
// ---------------------------------------------------------------------------

test("diferenca negativa preserva o sinal", () => {
  const s = fmtDiferenca(-90.1);
  assert.ok(s.startsWith("-"), s);
  assert.ok(s.includes("90,10"), s);
});

test("diferenca positiva ganha sinal explicito", () => {
  const s = fmtDiferenca(17.1);
  assert.ok(s.startsWith("+"), s);
  assert.ok(s.includes("17,10"), s);
});

test("percentual negativo e positivo com duas casas e sinal", () => {
  assert.equal(fmtPercentual(-56.3125), "-56,31%");
  assert.equal(fmtPercentual(25), "+25,00%");
  assert.equal(fmtPercentual(0), "0,00%");
});

test("moeda em pt-BR com duas casas e sem abreviacao", () => {
  const s = fmtMoeda(1234.5);
  assert.ok(s.includes("1.234,50"), s);
  assert.ok(!/[KM]\b/.test(s), s);
});

test("contagem inteira em pt-BR, sem K/M", () => {
  assert.equal(fmtContagem(855), "855");
  assert.equal(fmtContagem(25559), "25.559");
  assert.ok(!/[KM]/.test(fmtContagem(25559)));
});

test("nenhum Math.abs aplicado a diferenca em reais", async () => {
  const lib = await ler(LIB);
  const corpo = lib.slice(lib.indexOf("export function fmtDiferenca"));
  const fim = corpo.indexOf("export function fmtPercentual");
  assert.ok(!corpo.slice(0, fim).includes("Math.abs"),
    "fmtDiferenca nao pode usar Math.abs — o sinal e' a informacao");
});

test("ref_date formatada sem conversao de fuso", () => {
  assert.equal(fmtData("2026-09-01"), "01/09/2026");
});

test("captura do preco NAO e' rotulada como BRT", async () => {
  assert.equal(fmtCapturaPreco("2026-09-01T06:02:17"), "01/09/2026 06:02");
  const lib = await lerCodigo(LIB);
  const corpo = lib.slice(lib.indexOf("export function fmtCapturaPreco"));
  const fim = corpo.indexOf("export function fmtInstanteBrt");
  const trecho = corpo.slice(0, fim);
  assert.ok(!trecho.includes("America/Sao_Paulo"),
    "price_captured_at nao tem fuso declarado pela origem");
  assert.ok(!/BRT/.test(trecho));
});

// ---------------------------------------------------------------------------
// 4. Os seis status
// ---------------------------------------------------------------------------

test("os seis status tem rotulo em portugues", () => {
  assert.equal(STATUS_ORDER.length, 6);
  assert.deepEqual(STATUS_ORDER, [
    "below_reference",
    "at_or_above_reference",
    "no_reference",
    "non_comparable_reference_ambiguous",
    "inactive_listing",
    "stale_observation",
  ]);
  assert.equal(statusLabel("below_reference"), "Abaixo da referência");
  assert.equal(statusLabel("at_or_above_reference"), "Na ou acima da referência");
  assert.equal(statusLabel("no_reference"), "Sem referência B2B");
  assert.equal(statusLabel("non_comparable_reference_ambiguous"), "Referência ambígua");
  assert.equal(statusLabel("inactive_listing"), "Anúncio inativo");
  assert.equal(statusLabel("stale_observation"), "Observação desatualizada");
  for (const s of STATUS_ORDER) assert.ok(STATUS_LABELS[s]);
});

test("rotulos de match traduzidos, com fallback para qualidade", () => {
  assert.equal(matchLabel("brand_gtin_exact", "primary_gtin_exact"), "EAN exato na marca");
  assert.equal(matchLabel("brand_sku_exact_unique", "secondary_sku_unique_in_brand"), "SKU único na marca");
  assert.equal(matchLabel(null, "unmatched"), "Sem correspondência");
  assert.equal(matchLabel(null, "ambiguous_multiple_candidates"), "Ambígua — vários candidatos");
  assert.equal(matchQualityLabel("primary_gtin_exact"), "Chave primária (EAN)");
});

test("marca e situacao do anuncio traduzidas", () => {
  assert.equal(brandLabel("rituaria"), "Rituária");
  assert.equal(brandLabel("apice"), "Ápice");
  assert.equal(brandLabel("desconhecida"), "desconhecida");
  assert.equal(listingStatusLabel("active"), "Ativo");
  assert.equal(listingStatusLabel("under_review"), "Em revisão");
  assert.equal(listingStatusLabel(null), INDISPONIVEL);
});

// ---------------------------------------------------------------------------
// 5. KPIs — sem recalculo, sem severidade
// ---------------------------------------------------------------------------

const KPIS_REAIS: MonitoramentoPrecoKpis = {
  monitored_count: 855,
  comparable_count: 138,
  below_reference_count: 18,
  at_or_above_reference_count: 120,
  no_reference_count: 523,
  ambiguous_reference_count: 0,
  stale_count: 0,
  inactive_count: 194,
};

test("KPIs saem do backend sem recalculo", () => {
  const views = buildKpiViews(KPIS_REAIS);
  const porChave = Object.fromEntries(views.map((v) => [v.chave, v.valor]));
  assert.equal(porChave.monitored, 855);
  assert.equal(porChave.comparable, 138);
  assert.equal(porChave.below, 18);
  assert.equal(porChave.at_or_above, 120);
  assert.equal(porChave.no_reference, 523);
  assert.equal(porChave.inactive, 194);
});

test("todo KPI expoe denominador", () => {
  for (const v of [...buildKpiViews(KPIS_REAIS), ...buildQualidadeViews(KPIS_REAIS)]) {
    assert.ok(v.detalhe && v.detalhe.length > 0, v.chave);
  }
});

test("ambiguos e desatualizados sao indicadores de QUALIDADE, separados", () => {
  const q = buildQualidadeViews(KPIS_REAIS);
  assert.deepEqual(q.map((v) => v.chave), ["ambiguous", "stale"]);
  assert.ok(q.every((v) => v.qualidade === true));
  // E nao entram na lista de KPIs de negocio.
  const negocio = buildKpiViews(KPIS_REAIS).map((v) => v.chave);
  assert.ok(!negocio.includes("ambiguous"));
  assert.ok(!negocio.includes("stale"));
});

test("a soma dos seis status fecha com monitored_count", () => {
  assert.equal(statusFecha(KPIS_REAIS), true);
  assert.equal(statusFecha({ ...KPIS_REAIS, inactive_count: 193 }), false);
});

test("nenhuma severidade comercial e' criada", async () => {
  const [pagina, lib] = await Promise.all([lerCodigo(PAGE), lerCodigo(LIB)]);
  for (const texto of [pagina, lib]) {
    const baixo = texto.toLowerCase();
    for (const t of ["severidade", "severity", "gravidade", "criticidade", "prioridade", "threshold", "limiar comercial"]) {
      assert.ok(!baixo.includes(t), t);
    }
  }
});

test("frontend nao recalcula diferenca nem classifica anuncio", async () => {
  const [pagina, lib] = await Promise.all([ler(PAGE), ler(LIB)]);
  for (const texto of [pagina, lib]) {
    // Nenhuma subtracao entre preco anunciado e sugerido.
    assert.ok(!/advertised_price\s*-\s*suggested/.test(texto));
    assert.ok(!/suggested_retail_amount\s*-\s*advertised/.test(texto));
    // Nenhuma atribuicao de comparison_status no cliente.
    assert.ok(!/comparison_status\s*=\s*["']/.test(texto));
    assert.ok(!/match_method\s*=\s*["']/.test(texto));
  }
});

// ---------------------------------------------------------------------------
// 6. Paginacao 500/855 e truncamento
// ---------------------------------------------------------------------------

test("primeira pagina de 855 com limite 500", () => {
  const p = calcPaginacao(855, 500, 500, 0);
  assert.equal(p.pagina, 1);
  assert.equal(p.totalPaginas, 2);
  assert.equal(p.primeiraLinha, 1);
  assert.equal(p.ultimaLinha, 500);
  assert.equal(p.temAnterior, false);
  assert.equal(p.temProxima, true);
  assert.equal(p.rotulo, "1–500 de 855");
});

test("segunda pagina fecha as 855", () => {
  const p = calcPaginacao(855, 355, 500, 500);
  assert.equal(p.pagina, 2);
  assert.equal(p.primeiraLinha, 501);
  assert.equal(p.ultimaLinha, 855);
  assert.equal(p.temAnterior, true);
  assert.equal(p.temProxima, false);
  assert.equal(p.rotulo, "501–855 de 855");
});

test("truncated=true nunca e' escondido", () => {
  const aviso = avisoTruncamento(true, 500, 855);
  assert.ok(aviso);
  assert.ok(aviso!.includes("500"));
  assert.ok(aviso!.includes("855"));
  assert.equal(avisoTruncamento(false, 355, 855), null);
});

test("pagina vazia nao mente sobre o total", () => {
  const p = calcPaginacao(855, 0, 500, 1000);
  assert.equal(p.primeiraLinha, 0);
  assert.equal(p.rotulo, "0 de 855");
});

test("total zero nao divide por zero", () => {
  const p = calcPaginacao(0, 0, 500, 0);
  assert.equal(p.totalPaginas, 1);
  assert.equal(p.pagina, 1);
});

test("troca de filtro volta ao offset zero", async () => {
  const pagina = await ler(PAGE);
  // `trocaFiltro` zera o offset, e TODO onChange de filtro passa por ela.
  assert.ok(/const trocaFiltro[\s\S]{0,200}setOffset\(0\)/.test(pagina));
  const trechoFiltros = pagina.slice(pagina.indexOf('aria-label="Filtros"'));
  const onChanges = trechoFiltros.match(/onChange=\{\(e\) =>[^}]+\}/g) ?? [];
  assert.ok(onChanges.length >= 2, "filtros de marca e situacao");
  for (const oc of onChanges) {
    if (oc.includes("setBuscaInput")) continue; // digitar nao dispara requisicao
    assert.ok(oc.includes("trocaFiltro"), oc);
  }
  assert.ok(/limparFiltros[\s\S]{0,200}setOffset\(0\)/.test(pagina));
});

// ---------------------------------------------------------------------------
// 7. Frescor — resposta antiga nao sobrescreve a nova
// ---------------------------------------------------------------------------

test("chave de requisicao cobre todos os parametros", () => {
  const base = { brand: "", status: "", productQuery: "", limit: 500, offset: 0 };
  const k = buildMonitoramentoRequestKey(base);
  assert.notEqual(k, buildMonitoramentoRequestKey({ ...base, brand: "rituaria" }));
  assert.notEqual(k, buildMonitoramentoRequestKey({ ...base, status: "below_reference" }));
  assert.notEqual(k, buildMonitoramentoRequestKey({ ...base, productQuery: "x" }));
  assert.notEqual(k, buildMonitoramentoRequestKey({ ...base, offset: 500 }));
  // Estavel para a mesma entrada.
  assert.equal(k, buildMonitoramentoRequestKey({ ...base }));
});

test("a pagina compara a chave no retorno e aborta a requisicao anterior", async () => {
  const pagina = await ler(PAGE);
  assert.ok(pagina.includes("AbortController"));
  assert.ok(pagina.includes("controller.abort()"));
  assert.ok(pagina.includes("latestKey"));
  // Guarda no `then` E no `catch`: resposta antiga nao entra por nenhum caminho.
  const ocorrencias = pagina.match(/latestKey\.current !== requestKey/g) ?? [];
  assert.ok(ocorrencias.length >= 2, `esperava >=2 guardas, achei ${ocorrencias.length}`);
  assert.ok(pagina.includes("computeRequestStatus"));
});

test("linhas so aparecem quando o estado esta fresco", async () => {
  const pagina = await ler(PAGE);
  assert.ok(/const linhas = estado\.fresh && dados \? dados\.rows : \[\]/.test(pagina),
    "dado de requisicao antiga nao pode permanecer visivel");
});

// ---------------------------------------------------------------------------
// 8. Estados e ausencia de mock
// ---------------------------------------------------------------------------

test("loading, error e empty existem, com nome acessivel", async () => {
  const pagina = await ler(PAGE);
  assert.ok(pagina.includes("Carregando monitoramento de preços"));
  assert.ok(pagina.includes('role="alert"'));
  assert.ok(pagina.includes("Monitoramento de preços indisponível"));
  assert.ok(pagina.includes("Nenhum anúncio para os filtros selecionados"));
  assert.ok(pagina.includes("aria-busy"));
  assert.ok(pagina.includes("Tentar novamente"));
});

test("erro NAO cai em mock nem em dado fabricado", async () => {
  const pagina = await lerCodigo(PAGE);
  assert.ok(!/mock/i.test(pagina), "nenhuma referencia a mock no codigo");
  assert.ok(!/fallback/i.test(pagina));
  // No catch, os dados sao limpos — nunca mantidos como se fossem novos.
  assert.ok(/\.catch\([\s\S]{0,400}setDados\(null\)/.test(pagina));
});

test("o cliente levanta em vez de devolver null silencioso", async () => {
  const cliente = await ler(new URL("../src/lib/api-client.ts", import.meta.url));
  const trecho = cliente.slice(cliente.indexOf("export async function fetchMonitoramentoPreco"));
  assert.ok(trecho.includes("throw new MonitoramentoPrecoError"));
  assert.ok(!/return null/.test(trecho.slice(0, trecho.indexOf("res.json"))));
});

// ---------------------------------------------------------------------------
// 9. Link externo seguro
// ---------------------------------------------------------------------------

test("aceita URL HTTPS de dominio do Mercado Livre", () => {
  for (const u of [
    "https://produto.mercadolivre.com.br/MLB-123",
    "https://www.mercadolivre.com.br/p/MLB123",
    "https://articulo.mercadolibre.com.br/MLB-1",
  ]) {
    assert.ok(urlAnuncioSegura(u), u);
  }
});

test("recusa http, dominio estranho e esquema perigoso", () => {
  for (const u of [
    "http://produto.mercadolivre.com.br/MLB-123",
    "https://mercadolivre.com.br.evil.example/MLB-1",
    "https://evil.example/MLB-1",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "",
    "nao-e-url",
  ]) {
    assert.equal(urlAnuncioSegura(u), null, u);
  }
  assert.equal(urlAnuncioSegura(null), null);
});

test("o link usa target e rel seguros, e ausencia vira texto", async () => {
  const pagina = await ler(PAGE);
  assert.ok(pagina.includes('target="_blank"'));
  assert.ok(pagina.includes('rel="noopener noreferrer"'));
  assert.ok(pagina.includes("Link do anúncio indisponível"));
  assert.ok(pagina.includes("urlAnuncioSegura"));
});

test("nenhum HTML da API e' renderizado", async () => {
  const pagina = await ler(PAGE);
  assert.ok(!/dangerouslySetInnerHTML/.test(pagina));
});

// ---------------------------------------------------------------------------
// 10. Vocabulario — observacional, nunca sancao
// ---------------------------------------------------------------------------

test("o aviso observacional cobre os pontos obrigatorios", () => {
  const texto = AVISO_OBSERVACIONAL.join(" ").toLowerCase();
  assert.ok(texto.includes("observacional"));
  assert.ok(texto.includes("não é fiscalização de revendedores"));
  assert.ok(texto.includes("não altera preços automaticamente"));
  assert.ok(texto.includes("sem vigência declarada"));
  assert.ok(texto.includes("frete"));
  assert.ok(texto.includes("cupom"));
  assert.ok(texto.includes("subsídio"));
  assert.ok(texto.includes("checkout"));
  assert.ok(texto.includes("indeterminada"));
});

test("PDV nunca e' chamado de PMA", async () => {
  const [pagina, lib] = await Promise.all([ler(PAGE), ler(LIB)]);
  for (const texto of [pagina, lib]) {
    assert.ok(!/\bPMA\b(?!-)/.test(texto.replace(/Gate PMA-\d\w*/g, "")),
      "PMA so aparece como nome de gate, nunca como sinonimo de PDV");
    assert.ok(!/pre[çc]o m[íi]nimo anunciado/i.test(
      texto.replace(/n[ãa]o é política jurídica de preço mínimo anunciado/gi, ""),
    ));
  }
});

test("zero vocabulario de infracao ou sancao na UI", async () => {
  const [pagina, lib] = await Promise.all([lerCodigo(PAGE), lerCodigo(LIB)]);
  const proibidas = [
    "infração", "infracao", "violação", "violacao", "sanção",
    "penalidade", "multa", "denúncia", "denuncia", "descadastr",
    "punição", "punicao", "notificar", "bloquear", "advertência",
  ];
  for (const texto of [pagina, lib]) {
    const baixo = texto.toLowerCase();
    for (const t of proibidas) assert.ok(!baixo.includes(t), t);
  }
  // E o texto VISIVEL ao operador tambem nao usa essas palavras.
  const visivel = [...AVISO_OBSERVACIONAL, NOTA_DENOMINADOR].join(" ").toLowerCase();
  for (const t of proibidas) assert.ok(!visivel.includes(t), `visivel: ${t}`);
});

test("nenhuma acao de escrita ou automacao de preco na tela", async () => {
  const pagina = await ler(PAGE);
  assert.ok(!/method="post"|fetch\([^)]*POST|\.post\(/i.test(pagina));
  assert.ok(!/alterar pre[çc]o|ajustar pre[çc]o|aplicar pre[çc]o/i.test(pagina));
  // A unica acao por linha e' abrir a analise.
  assert.ok(pagina.includes("Analisar"));
});

test("a composicao indisponivel lista os quatro componentes como indisponiveis", () => {
  assert.equal(COMPOSICAO_INDISPONIVEL.length, 4);
  assert.deepEqual(
    COMPOSICAO_INDISPONIVEL.map((c) => c.rotulo),
    ["Frete", "Cupom de vitrine", "Subsídio da plataforma", "Preço de checkout"],
  );
  assert.ok(COMPOSICAO_INDISPONIVEL.every((c) => c.valor === INDISPONIVEL));
});

test("a nota de denominador explica o filtro de situacao", () => {
  assert.ok(NOTA_DENOMINADOR.includes("denominador"));
  assert.ok(NOTA_DENOMINADOR.toLowerCase().includes("situação"));
});

// ---------------------------------------------------------------------------
// 11. Dialogo, acessibilidade e scroll
// ---------------------------------------------------------------------------

test("o drill-down reutiliza o shell acessivel existente", async () => {
  const pagina = await ler(PAGE);
  assert.ok(pagina.includes("KpiDrilldownDialog"));
  assert.ok(pagina.includes("focusResetKey"));
  assert.ok(pagina.includes("onClose"));
});

test("o drill-down mostra referencia, match e limitacoes", async () => {
  const pagina = await ler(PAGE);
  for (const t of [
    "Preço sugerido de revenda (PDV)",
    "Vigência da referência",
    "Não informada",
    "Correspondência com a referência",
    "Composição indisponível",
    "Limitações",
    "Referência ambígua —",
  ]) {
    assert.ok(pagina.includes(t), t);
  }
});

test("alvos interativos tem altura minima de 44px", async () => {
  const pagina = await lerCodigo(PAGE);
  // JSX quebra atributos em varias linhas: casar a tag `<tag ... >` inteira.
  const interativos = tagsInterativas(pagina);
  assert.ok(interativos.length >= 8, `poucos interativos: ${interativos.length}`);
  for (const el of interativos) {
    assert.ok(/min-h-\[44px\]/.test(el), el.replace(/\s+/g, " ").slice(0, 100));
  }
});

test("nenhum texto abaixo de 12px", async () => {
  const [pagina, lib] = await Promise.all([ler(PAGE), ler(LIB)]);
  for (const texto of [pagina, lib]) {
    const achados = texto.match(/text-\[(\d+)px\]/g) ?? [];
    for (const a of achados) {
      const px = Number(/(\d+)/.exec(a)![1]);
      assert.ok(px >= 12, `${a} abaixo do piso de 12px`);
    }
  }
});

test("foco visivel em todo alvo interativo", async () => {
  const pagina = await lerCodigo(PAGE);
  const interativos = tagsInterativas(pagina);
  const semFoco = interativos.filter(
    (el) => !/focus-visible:ring|focus:ring/.test(el) && !/type="search"/.test(el),
  );
  assert.equal(semFoco.length, 0,
    semFoco.map((e) => e.replace(/\s+/g, " ").slice(0, 80)).join(" | "));
});

test("a tabela usa o hint de scroll do projeto", async () => {
  const pagina = await ler(PAGE);
  assert.ok(pagina.includes("TableScrollHint"));
  assert.ok(pagina.includes("<caption"));
  assert.ok(pagina.includes('scope="col"'));
});

test("colunas obrigatorias da tabela estao presentes", async () => {
  const pagina = await ler(PAGE);
  for (const c of [
    "Produto / anúncio", "Marca", "Item", "SKU", "Preço anunciado",
    "Preço sugerido", "Diferença", "Diferença %", "Situação", "Match",
    "Captura do preço",
  ]) {
    assert.ok(pagina.includes(c), c);
  }
});

test("cabecalho declara canal, data observada e frescor", async () => {
  const pagina = await ler(PAGE);
  for (const t of ["Mercado Livre", "Data observada", "Dados atualizados em",
                   "Referência capturada em", "Modo observacional"]) {
    assert.ok(pagina.includes(t), t);
  }
});

test("linha do payload tipa os quatro campos de checkout como anulaveis", () => {
  const row: MonitoramentoPrecoRow = {
    product_name: null, brand: "rituaria", marketplace: "ml",
    item_id: "MLB1", seller_sku: null, gtin: null, listing_title: null,
    permalink: null, listing_status: "active", currency: "BRL",
    ref_date: "2026-09-01", observed_at: null,
    listing_metadata_updated_at: null,
    advertised_price: 69.9, original_price: null,
    observed_effective_amount: 69.9,
    shipping_amount: null, seller_coupon_amount: null,
    platform_subsidy_amount: null, checkout_price: null,
    coverage_status: "advertised_only",
    suggested_retail_amount: null, reference_type: "suggested_retail_pdv",
    validity_status: "missing",
    policy_status: "not_applicable_to_own_store_monitoring",
    reference_captured_at: null, reference_row_id: null,
    difference_amount: null, difference_pct: null,
    match_method: null, match_quality: "unmatched",
    reference_candidate_count: 0, comparison_status: "no_reference",
    limitations: [],
  };
  assert.equal(row.shipping_amount, null);
  assert.equal(fmtMoeda(row.shipping_amount), INDISPONIVEL);
  assert.equal(fmtMoeda(row.checkout_price), INDISPONIVEL);
  assert.equal(row.observed_effective_amount, row.advertised_price);
});


test("o wrapper da tabela corta o overflow horizontal da pagina", async () => {
  // Regressao medida em producao no Gate PMA-4F: as 12 colunas em
  // `whitespace-nowrap` dao a tabela um min-content de ~1587px. Sem
  // `overflow-hidden` neste wrapper o overflow escapa do `overflow-x-auto` do
  // TableScrollHint e a pagina inteira rola na lateral nos tres viewports
  // (1440 -> scrollWidth 1766; 390 -> 1518). O recorte tem que ficar no
  // wrapper, nao no TableScrollHint, que precisa seguir rolavel.
  const pagina = await lerCodigo(PAGE);
  const wrapper = pagina.match(
    /<div className="bg-white border border-violet-100 rounded-2xl shadow-sm[^"]*">\s*\{truncamento/,
  );
  assert.ok(wrapper, "o wrapper da tabela mudou de forma; revise o recorte");
  assert.ok(
    wrapper[0].includes("overflow-hidden"),
    "o wrapper da tabela precisa de overflow-hidden para nao vazar rolagem lateral",
  );
  // O TableScrollHint segue sendo quem rola: o recorte nao o substitui.
  assert.ok(pagina.includes("<TableScrollHint>"), "a tabela continua no TableScrollHint");
});
