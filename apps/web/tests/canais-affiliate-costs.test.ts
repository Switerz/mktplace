// Testes do bloco "Impacto de afiliados no resultado" da aba Canais
// (contrato §23). Cobrem as quatro dimensoes ORTOGONAIS de estado, o sinal
// contabil preservado, ausencia distinta de zero, e a inexistencia de
// qualquer total/razao/retorno numerico.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  AFFILIATE_BLOCK_TITLE,
  AFFILIATE_COMPONENT_LABELS,
  AFFILIATE_COMPONENT_ORDER,
  brandLabel,
  buildAffiliateDrilldown,
  channelLabel,
  coverageNote,
  deriveAffiliateBlockView,
  describeChannelStatus,
  describeFreshness,
  formatRefMonth,
  formatSignedBrl,
  formatTimestamp,
  resolveBlockPhase,
  rowCoverageNote,
} from "../src/lib/canais-affiliate-costs.ts";
import type {
  AffiliateCostRow,
  AffiliateCostsBlock,
} from "../src/lib/api-client.ts";

/**
 * Le um arquivo do repo SEM comentarios.
 *
 * Varrer o texto cru acusaria as proprias proibicoes escritas em comentario
 * ("nao aplica Math.abs()", "nenhum <tfoot> de total") como se fossem codigo.
 * O que precisa ser verificado e' o codigo executavel.
 */
function codeOf(relPath: string): string {
  return readFileSync(new URL(relPath, import.meta.url), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")   // blocos /* ... */
    .replace(/^\s*\/\/.*$/gm, "")       // linhas // ...
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "");  // comentarios JSX {/* ... */}
}

function row(over: Partial<AffiliateCostRow> = {}): AffiliateCostRow {
  return {
    channel: "tiktok",
    brand: "apice",
    ref_month: "2026-03",
    creator_commission_signed: -1234.56,
    partner_commission_signed: -78.9,
    affiliate_ads_commission_signed: -10,
    coverage_status: "complete",
    brands_present_in_month: 5,
    ...over,
  };
}

function block(over: Partial<AffiliateCostsBlock> = {}): AffiliateCostsBlock {
  return {
    availability_status: "available",
    period_status: "complete_month",
    coverage_status: "complete",
    freshness_status: "manual_snapshot",
    rows: [row()],
    channels: [
      { channel: "tiktok", availability_status: "available", reason_note: "Dados disponíveis." },
      { channel: "mercadolivre", availability_status: "unavailable_no_source", reason_note: "Fonte equivalente não confirmada para este canal." },
      { channel: "shopee", availability_status: "unavailable_no_source", reason_note: "Fonte equivalente não confirmada para este canal." },
    ],
    months_included: ["2026-03"],
    affiliate_refreshed_at: "2026-08-25T19:33:26",
    source_watermark: "2026-08-25T00:11:55",
    return_availability: "unavailable_no_attributed_revenue",
    return_note: "Retorno de afiliados indisponível: não há receita atribuída.",
    source_note: "Competência mensal do pedido.",
    limitation_note: "Os três lançamentos são exibidos separadamente e não somados.",
    ...over,
  };
}

// ---------------------------------------------------------------------------
// Sinal contabil e ausencia
// ---------------------------------------------------------------------------

test("valor negativo mantem o sinal da fonte, sem abs()", () => {
  const c = formatSignedBrl(-1234.56);
  assert.equal(c.tone, "value");
  assert.match(c.text, /^-\s?R\$|R\$\s?-/);
  assert.ok(c.text.includes("1.234,56"), c.text);
});

test("valor positivo nao ganha sinal artificial", () => {
  assert.ok(formatSignedBrl(1234.56).text.includes("1.234,56"));
  assert.ok(!formatSignedBrl(1234.56).text.includes("-"));
});

test("zero medido e exibido como R$ 0,00, nunca como ausencia", () => {
  const c = formatSignedBrl(0);
  assert.equal(c.tone, "value");
  assert.ok(c.text.includes("0,00"), c.text);
  assert.notEqual(c.text, "—");
});

test("ausencia (null) vira travessao, nunca R$ 0,00", () => {
  assert.deepEqual(formatSignedBrl(null), { text: "—", tone: "muted" });
});

test("valor grande NAO e abreviado — custo contabil precisa reconciliar", () => {
  const c = formatSignedBrl(-5504405.93);
  assert.ok(!/[MK]/.test(c.text), c.text);
  assert.ok(c.text.includes("5.504.405,93"), c.text);
});

test("centavos nunca sao truncados", () => {
  assert.ok(formatSignedBrl(-0.01).text.includes("0,01"));
});

// ---------------------------------------------------------------------------
// Nenhum total, razao ou retorno numerico
// ---------------------------------------------------------------------------

test("o modulo nao exporta nada que agregue, some ou razoe", () => {
  const proibidos = ["total", "sum", "soma", "ratio", "razao", "roi", "roas",
                     "gmv", "pct", "amount", "revenue"];
  const fonte = codeOf("../src/lib/canais-affiliate-costs.ts");
  // Verificacao ESTRUTURAL: nomes exportados, nao prosa dos comentarios.
  const exportados = [...fonte.matchAll(
    /^export (?:function|const|type|interface) (\w+)/gm)].map((m) => m[1]);
  for (const nome of exportados) {
    const baixo = nome.toLowerCase();
    for (const p of proibidos) {
      assert.ok(!baixo.includes(p), `export proibido: ${nome} (${p})`);
    }
  }
  assert.ok(exportados.length > 5, "os exports deveriam ter sido detectados");
});

test("o modulo nao chama Math.abs em codigo executavel", () => {
  assert.ok(!/Math\.abs\s*\(/.test(codeOf("../src/lib/canais-affiliate-costs.ts")));
});

test("o drilldown lista os tres componentes separados, sem linha de total", () => {
  const linhas = buildAffiliateDrilldown([row()]);
  assert.equal(linhas.length, 1);
  assert.equal(linhas[0].components.length, 3);
  const rotulos = linhas[0].components.map((c) => c.label);
  assert.deepEqual(rotulos, AFFILIATE_COMPONENT_ORDER.map(
    (k) => AFFILIATE_COMPONENT_LABELS[k]));
  assert.ok(!rotulos.some((r) => /total|soma/i.test(r)));
});

// ---------------------------------------------------------------------------
// Titulo e rotulos
// ---------------------------------------------------------------------------

test("o titulo e o provisorio do contrato, nao 'Custo de afiliados'", () => {
  assert.equal(AFFILIATE_BLOCK_TITLE, "Impacto de afiliados no resultado");
  assert.notEqual(AFFILIATE_BLOCK_TITLE, "Custo de afiliados");
});

test("componentes tem rotulo proprio e ordem fixa, nao ordem por valor", () => {
  assert.deepEqual(AFFILIATE_COMPONENT_ORDER, [
    "creator_commission_signed",
    "partner_commission_signed",
    "affiliate_ads_commission_signed",
  ]);
  for (const k of AFFILIATE_COMPONENT_ORDER) {
    assert.ok(AFFILIATE_COMPONENT_LABELS[k].length > 0);
  }
});

test("competencia YYYY-MM vira mm/aaaa; entrada malformada volta como veio", () => {
  assert.equal(formatRefMonth("2026-03"), "03/2026");
  assert.equal(formatRefMonth("2026-12"), "12/2026");
  assert.equal(formatRefMonth("lixo"), "lixo");
});

test("timestamp ISO vira dd/mm/aaaa hh:mm, sem inventar fuso", () => {
  assert.equal(formatTimestamp("2026-08-25T19:33:26"), "25/08/2026 19:33");
  assert.equal(formatTimestamp("2026-08-25"), "25/08/2026");
  assert.equal(formatTimestamp("lixo"), "lixo");
});

test("rotulos de canal e marca degradam para a chave crua, nunca vazio", () => {
  assert.equal(channelLabel("tiktok"), "TikTok Shop");
  assert.equal(channelLabel("desconhecido"), "desconhecido");
  assert.equal(brandLabel("apice"), "Apice");
  assert.equal(brandLabel("nova"), "nova");
});

// ---------------------------------------------------------------------------
// Periodo: parcial e desalinhado nao mostram numero
// ---------------------------------------------------------------------------

test("mes completo mostra linhas e nenhuma mensagem de estado", () => {
  const v = deriveAffiliateBlockView(block());
  assert.equal(v.hasRows, true);
  assert.equal(v.emptyMessage, null);
  assert.equal(v.monthsLabel, "03/2026");
});

test("varios meses completos listam as competencias, sem agregar", () => {
  const v = deriveAffiliateBlockView(block({
    period_status: "complete_months",
    months_included: ["2026-01", "2026-02"],
    rows: [row({ ref_month: "2026-01" }), row({ ref_month: "2026-02" })],
  }));
  assert.equal(v.monthsLabel, "01/2026, 02/2026");
  assert.equal(v.hasRows, true);
});

test("mes parcial nao mostra numero e explica por que", () => {
  const v = deriveAffiliateBlockView(block({
    period_status: "partial_month", rows: [], months_included: [],
  }));
  assert.equal(v.hasRows, false);
  assert.match(v.emptyMessage ?? "", /aberto/i);
  assert.ok(!/R\$/.test(v.emptyMessage ?? ""));
});

test("periodo desalinhado orienta a selecionar mes completo", () => {
  const v = deriveAffiliateBlockView(block({
    period_status: "not_month_aligned", rows: [], months_included: [],
  }));
  assert.equal(v.hasRows, false);
  assert.match(v.emptyMessage ?? "", /mês\(es\) completo/i);
});

// ---------------------------------------------------------------------------
// As quatro dimensoes sao independentes
// ---------------------------------------------------------------------------

test("available + cobertura incompleta coexistem", () => {
  const v = deriveAffiliateBlockView(block({
    coverage_status: "incomplete_brand_coverage",
    rows: [row({ coverage_status: "incomplete_brand_coverage", brands_present_in_month: 1 })],
  }));
  assert.equal(v.hasRows, true);              // segue disponivel
  assert.equal(v.emptyMessage, null);
  assert.match(v.coverageWarning ?? "", /incompleta/i);
  assert.match(v.coverageWarning ?? "", /zero/i);   // diz que NAO preencheu
});

test("cobertura completa nao gera aviso", () => {
  assert.equal(coverageNote("complete"), null);
  assert.equal(coverageNote("unknown"), null);
  assert.equal(deriveAffiliateBlockView(block()).coverageWarning, null);
});

test("nota de cobertura por linha traz a contagem medida", () => {
  assert.equal(rowCoverageNote(row()), null);
  assert.match(
    rowCoverageNote(row({ coverage_status: "incomplete_brand_coverage", brands_present_in_month: 4 })) ?? "",
    /4 marca/);
  assert.equal(
    rowCoverageNote(row({ coverage_status: "incomplete_brand_coverage", brands_present_in_month: null })),
    "Competência incompleta");
});

test("erro do bloco usa a nota fixa do backend, sem SQL/DSN/driver", () => {
  const b = block({
    availability_status: "error", rows: [],
    limitation_note: "Não foi possível ler os custos de afiliados nesta consulta.",
  });
  const v = deriveAffiliateBlockView(b);
  assert.equal(v.hasRows, false);
  assert.equal(v.emptyTone, "warning");
  assert.equal(v.emptyMessage, b.limitation_note);
  for (const p of ["SELECT", "postgres", "sqlalchemy", "password", "5432"]) {
    assert.ok(!(v.emptyMessage ?? "").includes(p), p);
  }
});

test("sem marca elegivel e distinto de erro e de ausencia de fonte", () => {
  const msgs = new Set([
    deriveAffiliateBlockView(block({ availability_status: "no_eligible_brand", rows: [] })).emptyMessage,
    deriveAffiliateBlockView(block({ availability_status: "unavailable_no_source", rows: [] })).emptyMessage,
    deriveAffiliateBlockView(block({ availability_status: "error", rows: [] })).emptyMessage,
  ]);
  assert.equal(msgs.size, 3, "os tres estados devem ter mensagens distintas");
});

// ---------------------------------------------------------------------------
// Canais: ML e Shopee sao "Dados indisponiveis", nunca "Nao aplicavel"
// ---------------------------------------------------------------------------

test("ML e Shopee sao 'Dados indisponiveis', nunca 'Nao aplicavel'", () => {
  for (const canal of ["mercadolivre", "shopee"]) {
    const d = describeChannelStatus({
      channel: canal, availability_status: "unavailable_no_source",
      reason_note: "x",
    });
    assert.equal(d.text, "Dados indisponíveis");
    assert.ok(!/aplic/i.test(d.text), d.text);
    assert.ok(!/N\/A/.test(d.text), d.text);
  }
});

test("status por canal e autoritativo: TikTok disponivel nao contamina os outros", () => {
  const b = block();
  const ditos = b.channels.map(describeChannelStatus);
  assert.equal(ditos[0].text, "Dados disponíveis");
  assert.equal(ditos[1].text, "Dados indisponíveis");
  assert.equal(ditos[2].text, "Dados indisponíveis");
});

test("erro em um canal nao vira 'nao aplicavel' nem valor", () => {
  const d = describeChannelStatus({
    channel: "tiktok", availability_status: "error", reason_note: "x" });
  assert.equal(d.text, "Leitura indisponível");
  assert.equal(d.tone, "warning");
});

// ---------------------------------------------------------------------------
// Frescor proprio do bloco
// ---------------------------------------------------------------------------

test("frescor cita carga manual e a leitura da fonte, como grandezas distintas", () => {
  const t = describeFreshness(block());
  assert.match(t, /25\/08\/2026 19:33/);   // synced_at
  assert.match(t, /25\/08\/2026 00:11/);   // watermark
  assert.match(t, /Sem atualização automática/);
});

test("frescor nunca afirma 'fresco' nem inventa limiar de stale", () => {
  const t = describeFreshness(block());
  assert.ok(!/\bfresco\b/i.test(t), t);
  assert.ok(!/\bstale\b|desatualizado/i.test(t), t);
});

test("sem synced_at o frescor diz que nao ha registro, nao 'agora'", () => {
  const t = describeFreshness(block({
    affiliate_refreshed_at: null, source_watermark: null }));
  assert.match(t, /sem registro de data/i);
  assert.ok(!/agora|hoje/i.test(t), t);
});

// ---------------------------------------------------------------------------
// Wiring: guarda de frescor e ausencia de fetch extra
// ---------------------------------------------------------------------------

test("a pagina protege o bloco com dataIsFresh, como os outros estados", () => {
  const pagina = readFileSync(
    new URL("../app/canais/page.tsx", import.meta.url), "utf8");
  assert.match(pagina,
    /const displayAffiliateCosts = dataIsFresh \? affiliateCosts : null/);
  assert.match(pagina, /block=\{displayAffiliateCosts\}/);
  // NUNCA le o estado bruto direto no render
  assert.ok(!/block=\{affiliateCosts\}/.test(pagina));
});

test("o painel reusa KpiDrilldownDialog e nao faz fetch ao abrir", () => {
  const painel = readFileSync(
    new URL("../src/components/AffiliateCostsPanel.tsx", import.meta.url), "utf8");
  assert.match(painel, /import KpiDrilldownDialog from "\.\/KpiDrilldownDialog"/);
  assert.match(painel, /<KpiDrilldownDialog/);
  // zero fetch/efeito de rede no componente
  for (const p of ["fetch(", "fetchCanais", "useEffect", "apiFetch"]) {
    assert.ok(!painel.includes(p), `o dialogo nao deve usar ${p}`);
  }
});

test("o painel nao renderiza tfoot de total nem soma componentes", () => {
  const painel = codeOf("../src/components/AffiliateCostsPanel.tsx");
  assert.ok(!/<tfoot/.test(painel));
  assert.ok(!/reduce\(/.test(painel), "nenhuma agregacao no componente");
  assert.ok(!/Math\.abs/.test(painel));
});

test("o api-client trata bloco ausente como null nas duas rotas", () => {
  const cliente = readFileSync(
    new URL("../src/lib/api-client.ts", import.meta.url), "utf8");
  assert.match(cliente, /affiliateCosts: raw\.affiliate_costs \?\? null/);
  assert.match(cliente, /affiliateCosts: null/);       // rota mock
});


// ===========================================================================
// F5 — frescor so' quando ha fotografia consultada
// ===========================================================================

test("frescor e' null quando nenhuma fotografia foi consultada", () => {
  for (const status of ["unknown", "fresh", "stale"] as const) {
    assert.equal(describeFreshness(block({ freshness_status: status })), null,
      status);
  }
});

test("ML/Shopee sem fonte nao mostram frase de carga manual", () => {
  const b = block({
    availability_status: "unavailable_no_source", rows: [],
    freshness_status: "unknown",
    affiliate_refreshed_at: null, source_watermark: null,
  });
  const v = deriveAffiliateBlockView(b);
  assert.equal(v.freshnessLabel, null);
  assert.ok(!/carga manual/i.test(v.emptyMessage ?? ""));
});

test("erro mostra so' o erro sanitizado, sem frase de carga", () => {
  const b = block({
    availability_status: "error", rows: [], freshness_status: "unknown",
    limitation_note: "Nao foi possivel ler os custos de afiliados.",
    affiliate_refreshed_at: null, source_watermark: null,
  });
  const v = deriveAffiliateBlockView(b);
  assert.equal(v.freshnessLabel, null);
  assert.equal(v.emptyMessage, b.limitation_note);
  assert.equal(v.emptyTone, "warning");
});

test("periodo parcial explica o periodo e nao afirma carga sem data", () => {
  const v = deriveAffiliateBlockView(block({
    period_status: "partial_month", rows: [], months_included: [],
    freshness_status: "unknown",
    affiliate_refreshed_at: null, source_watermark: null,
  }));
  assert.equal(v.freshnessLabel, null);
  assert.match(v.emptyMessage ?? "", /aberto/i);
  assert.ok(!/sem registro de data/i.test(v.emptyMessage ?? ""));
});

test("available com linhas mostra as duas grandezas de frescor", () => {
  const v = deriveAffiliateBlockView(block());
  assert.ok(v.freshnessLabel);
  assert.match(v.freshnessLabel!, /Carga manual gravada em/);
  assert.match(v.freshnessLabel!, /fonte lida até/i);
});

test("o painel so' renderiza frescor quando ha rotulo", () => {
  const painel = codeOf("../src/components/AffiliateCostsPanel.tsx");
  const usos = painel.match(/view\.freshnessLabel/g) ?? [];
  const guardas = painel.match(/view\.freshnessLabel &&/g) ?? [];
  assert.ok(usos.length >= 2);
  // toda renderizacao passa por uma guarda: nunca imprime `null` cru
  assert.equal(guardas.length * 2, usos.length);
});

// ===========================================================================
// F6 — erro nao pode virar skeleton eterno
// ===========================================================================

test("fase: requisicao em voo e' loading", () => {
  assert.equal(resolveBlockPhase({
    loading: true, error: false, requestKey: "a", resolvedKey: null }),
    "loading");
});

test("fase: frame de troca de requestKey e' neutral, nao erro", () => {
  assert.equal(resolveBlockPhase({
    loading: false, error: false, requestKey: "b", resolvedKey: "a" }),
    "neutral");
});

test("fase: requisicao TERMINADA em erro e' unavailable, nunca loading", () => {
  const fase = resolveBlockPhase({
    loading: false, error: true, requestKey: "a", resolvedKey: null });
  assert.equal(fase, "unavailable");
  assert.notEqual(fase, "loading");
});

test("fase: erro com chave ja resolvida ainda e' unavailable", () => {
  assert.equal(resolveBlockPhase({
    loading: false, error: true, requestKey: "a", resolvedKey: "a" }),
    "unavailable");
});

test("fase: retry em voo vence o erro anterior", () => {
  assert.equal(resolveBlockPhase({
    loading: true, error: true, requestKey: "a", resolvedKey: null }),
    "loading");
});

test("fase: resolvedKey igual a requestKey e' fresh", () => {
  assert.equal(resolveBlockPhase({
    loading: false, error: false, requestKey: "a", resolvedKey: "a" }),
    "fresh");
});

test("o painel pulsa em loading/neutral e NUNCA em unavailable", () => {
  const painel = codeOf("../src/components/AffiliateCostsPanel.tsx");
  // o unico animate-pulse esta no ramo de loading/neutral
  const pulsos = painel.match(/animate-pulse/g) ?? [];
  assert.equal(pulsos.length, 1);
  const ramoPulso = painel.slice(
    painel.indexOf('phase === "loading"'), painel.indexOf('phase === "unavailable"'));
  assert.match(ramoPulso, /animate-pulse/);
  const ramoErro = painel.slice(painel.indexOf('phase === "unavailable"'));
  const ateProximo = ramoErro.slice(0, ramoErro.indexOf("if (!block)"));
  assert.ok(!/animate-pulse/.test(ateProximo));
  assert.ok(!/aria-busy/.test(ateProximo));
});

test("a pagina deriva a fase em vez de passar loading=!dataIsFresh", () => {
  const pagina = codeOf("../app/canais/page.tsx");
  assert.match(pagina, /const affiliatePhase = resolveBlockPhase\(/);
  assert.match(pagina, /phase=\{affiliatePhase\}/);
  assert.ok(!/loading=\{!dataIsFresh\}/.test(pagina),
    "loading=!dataIsFresh colapsava erro e carregamento");
});

test("a fase le error e loading reais da pagina", () => {
  const pagina = codeOf("../app/canais/page.tsx");
  const bloco = pagina.slice(pagina.indexOf("resolveBlockPhase({"));
  const chamada = bloco.slice(0, bloco.indexOf("})") + 2);
  for (const campo of ["loading", "error", "requestKey", "resolvedKey"]) {
    assert.ok(chamada.includes(campo), campo);
  }
});

// ===========================================================================
// F7 — dialogo fecha na troca de filtro
// ===========================================================================

test("o painel remonta na troca de requestKey, zerando o dialogo", () => {
  const pagina = codeOf("../app/canais/page.tsx");
  const trecho = pagina.slice(pagina.indexOf("<AffiliateCostsPanel"));
  const tag = trecho.slice(0, trecho.indexOf("/>") + 2);
  assert.match(tag, /key=\{requestKey\}/);
});

test("o estado do dialogo e' local e inicia fechado", () => {
  const painel = codeOf("../src/components/AffiliateCostsPanel.tsx");
  assert.match(painel, /useState\(false\)/);
  // nenhuma prop/efeito reabre o dialogo por conta propria
  assert.ok(!/setAberto\(true\)/.test(
    painel.replace(/onClick=\{\(\) => setAberto\(true\)\}/g, "")));
});

test("o shell KpiDrilldownDialog nao foi alterado", () => {
  const shell = codeOf("../src/components/KpiDrilldownDialog.tsx");
  assert.match(shell, /open,\s*onClose,\s*title,\s*children,\s*focusResetKey/);
  assert.ok(!/affiliate/i.test(shell), "o shell deve seguir generico");
});

// ===========================================================================
// F9 — timestamp com fuso explicito
// ===========================================================================

test("UTC com Z e' convertido para BRT e rotulado", () => {
  // 2026-08-25T19:33:26Z == 16:33 BRT (UTC-3)
  assert.equal(formatTimestamp("2026-08-25T19:33:26Z"), "25/08/2026 16:33 BRT");
});

test("offset explicito +00:00 tambem converte", () => {
  assert.equal(formatTimestamp("2026-08-25T19:33:26+00:00"),
               "25/08/2026 16:33 BRT");
});

test("offset negativo e' respeitado, nao recortado", () => {
  // 2026-08-25T19:33-05:00 == 21:33 BRT
  assert.equal(formatTimestamp("2026-08-25T19:33:00-05:00"),
               "25/08/2026 21:33 BRT");
});

test("fronteira de dia: UTC de madrugada e' o dia ANTERIOR em BRT", () => {
  // 2026-09-01T02:30:00Z == 31/08 23:30 BRT — recortar mostraria 01/09
  const t = formatTimestamp("2026-09-01T02:30:00Z");
  assert.equal(t, "31/08/2026 23:30 BRT");
  assert.ok(!t.startsWith("01/09"));
});

test("data pura nao sofre deslocamento de fuso", () => {
  assert.equal(formatTimestamp("2026-08-25"), "25/08/2026");
  assert.ok(!formatTimestamp("2026-08-25").includes("BRT"));
});

test("timestamp sem offset nao ganha rotulo de fuso inventado", () => {
  const t = formatTimestamp("2026-08-25T19:33:26");
  assert.equal(t, "25/08/2026 19:33");
  assert.ok(!t.includes("BRT"), "o fuso da fonte e desconhecido");
});

test("entrada invalida volta como veio, sem horario inventado", () => {
  for (const lixo of ["lixo", "", "2026-13-45T99:99Z", "25/08/2026"]) {
    assert.equal(formatTimestamp(lixo), lixo);
  }
});

// ===========================================================================
// F10 — alvo de interacao
// ===========================================================================

test("o botao de detalhe mantem 44px em TODOS os viewports", () => {
  const painel = codeOf("../src/components/AffiliateCostsPanel.tsx");
  const trecho = painel.slice(painel.indexOf("Ver detalhe por marca") - 900,
                              painel.indexOf("Ver detalhe por marca"));
  assert.match(trecho, /min-h-\[44px\]/);
  assert.match(trecho, /min-w-\[44px\]/);
  // nenhum breakpoint desfaz o alvo
  assert.ok(!/sm:min-h-0/.test(trecho));
  assert.ok(!/sm:min-h-\[/.test(trecho));
});

test("nenhum sm:min-h-0 sobrou no painel", () => {
  assert.ok(!/sm:min-h-0/.test(
    codeOf("../src/components/AffiliateCostsPanel.tsx")));
});

test("as duas formas REAIS medidas em producao sao tratadas distintamente", () => {
  // Medido no preflight de 27/08: `synced_at` e' `timestamp with time zone`
  // (serializa com `+00:00`) e `last_successful_upper_bound` e' `timestamp
  // without time zone` (sem offset). Sao tipos diferentes no banco, entao
  // exibi-los com a mesma regra carimbaria um fuso que uma delas nao declara.
  const b = block({
    affiliate_refreshed_at: "2026-08-25T19:33:26.287029+00:00",
    source_watermark: "2026-08-25T00:11:55.377962",
  });
  const rotulo = describeFreshness(b)!;
  assert.match(rotulo, /16:33 BRT/);      // convertido: 19:33 UTC -> 16:33 BRT
  assert.match(rotulo, /00:11(?! BRT)/);  // sem offset: exibido sem rotulo
  assert.equal((rotulo.match(/BRT/g) ?? []).length, 1);
});

test("o skeleton anuncia o titulo — regiao aria-busy precisa de nome", () => {
  // Achado do QA da Task 3/3: o skeleton renderizava uma regiao `aria-busy`
  // VAZIA. Um leitor de tela anunciava "ocupado" sem dizer do que. Todas as
  // outras secoes de /canais mantem o heading durante o load.
  const painel = codeOf("../src/components/AffiliateCostsPanel.tsx");
  const inicio = painel.indexOf('phase === "loading"');
  const fim = painel.indexOf('phase === "unavailable"');
  const ramo = painel.slice(inicio, fim);
  assert.ok(ramo.includes("AFFILIATE_BLOCK_TITLE"),
    "o ramo de loading deve renderizar o titulo");
  assert.ok(ramo.includes("<h2"), "o titulo deve ser um heading");
  assert.ok(ramo.includes('aria-busy="true"'), "a regiao segue marcada ocupada");
  // o pulso fica no invólucro das barras, nao na secao inteira: o titulo
  // nao deve piscar junto
  const secao = ramo.slice(ramo.indexOf("<section"), ramo.indexOf(">") + 1);
  assert.ok(!secao.includes("animate-pulse"),
    "a <section> nao deve pulsar; so' as barras de placeholder");
});
