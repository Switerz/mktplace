// Gate EXP-2B — contrato da tela de Expedicao.
//
// O que estes testes travam, em uma frase: a tela fica INVISIVEL e INERTE com
// a flag desligada, nao confunde os tres relogios, nao inventa agregacao que a
// API nao fez e nunca carrega identificador de pedido.
//
// Alguns sao estaticos (leem o fonte), mesmo padrao dos testes de wiring dos
// gates U4/U5: invariantes que nao dependem de harness de componente React.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  AVISO_SEM_AUTOMACAO,
  EXPLICACAO_LIMIAR_48H,
  FILTROS_PADRAO,
  JANELA_MAX_HORAS,
  LIMIAR_OPERACIONAL_HORAS,
  LIMIT_MAX,
  ORDENACOES,
  ROTULO_LIMIAR_48H,
  SITUACOES,
  acimaDoLimiarInterno,
  aplicarFiltro,
  avisosDeCobertura,
  chaveDaRequisicao,
  construirQuery,
  estadoDaTela,
  estadoDaTendencia,
  expedicaoHabilitado,
  montarBarraDeQualidade,
  montarFrescor,
  montarKpis,
  montarRelogios,
  montarSeries,
  mostrarColunaReferencia,
  paginaAtual,
  sanitizarJanela,
  sanitizarLimit,
  sanitizarOrdenacao,
  sanitizarSituacoes,
  type Cobertura,
  type FrescorMarca,
  type LinhaFila,
  type PontoTendencia,
  type RespostaExpedicao,
  type Totais,
} from "../src/lib/expedicao-contract.ts";
import {
  EXPEDICAO_NAV,
  NAV_SECTIONS,
  getRouteTitle,
  navSections,
} from "../src/components/shell/nav-config.ts";

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8");

const TOTAIS: Totais = {
  backlog_count: 985,
  overdue_count: 24,
  due_within_24h_count: 329,
  on_time_count: 486,
  deadline_unavailable_count: 146,
  over_48h_count: 272,
  slow_count: 287,
  zombie_count: 0,
  stalled_count: 287,
};

function frescor(over: Partial<FrescorMarca> = {}): FrescorMarca {
  return {
    brand: "apice",
    freshness: "fresh",
    status: "pass",
    severity: "low",
    source_watermark_at: "2026-09-16T18:07:25Z",
    source_age_hours: 3.47,
    oldest_row_age_hours: 243.4,
    accounts: 1,
    observed_at: "2026-09-16T21:36:01Z",
    measures: "source_watermark_only",
    derived_from_batch: false,
    ...over,
  };
}

function resposta(over: Partial<RespostaExpedicao> = {}): RespostaExpedicao {
  return {
    availability: "available",
    unavailable_reason: null,
    channel: "shopee",
    snapshot: {
      channel: "shopee",
      refresh_batch_id: "6ace44e2-208e-4724-a04a-d424317b2b2e",
      effective_at: "2026-09-16T21:35:53Z",
      snapshot_hour: "2026-09-16T21:00:00Z",
      load_mode: "manual_snapshot",
      source_health: "healthy",
      source_advanced: false,
      run_status: "success",
      rows_extracted: 985,
      rows_loaded: 985,
      audit_run_id: 315,
    },
    totals: TOTAIS,
    accounts: [],
    freshness: [frescor()],
    coverage: null,
    queue: [],
    pagination: { limit: 500, offset: 0, returned: 500, total: 985, has_more: true },
    limitations: {
      no_automation: true,
      load_mode: "manual_snapshot",
      snapshot_age_hours: 1.2,
      brands_not_covered: ["kokeshi"],
      order_identifier_withheld: true,
      notes: [],
    },
    ...over,
  };
}

// ===========================================================================
// Feature flag — fail-closed
// ===========================================================================
test("flag: somente a string exata 'true' liga", () => {
  assert.equal(expedicaoHabilitado("true"), true);
  for (const v of [undefined, null, "", "TRUE", "True", "1", "yes", "on", " true", "true "]) {
    assert.equal(expedicaoHabilitado(v as string | undefined), false, String(v));
  }
});

test("flag desligada: item de menu invisivel", () => {
  const off = navSections(false);
  const hrefs = off.flatMap((s) => s.pages.map((p) => p.href));
  assert.ok(!hrefs.includes("/expedicao"));
  assert.deepEqual(off, NAV_SECTIONS, "com a flag off a navegacao e a original");
});

test("flag ligada: item entra em Operacoes, sem remover nada", () => {
  const on = navSections(true);
  const operacoes = on.find((s) => s.label === "Operações");
  assert.ok(operacoes);
  assert.ok(operacoes.pages.some((p) => p.href === EXPEDICAO_NAV.href));
  const antes = NAV_SECTIONS.flatMap((s) => s.pages.map((p) => p.href));
  const depois = on.flatMap((s) => s.pages.map((p) => p.href));
  for (const h of antes) assert.ok(depois.includes(h), `sumiu: ${h}`);
  assert.equal(depois.length, antes.length + 1);
});

test("a rota e fail-closed no servidor, nao so no menu", () => {
  // Esconder o item deixaria a URL direta acessivel, e a API nao tem auth.
  const src = fonte("app/expedicao/page.tsx");
  assert.match(src, /notFound\(\)/);
  assert.match(src, /expedicaoHabilitado\(process\.env\.NEXT_PUBLIC_EXPEDICAO_ENABLED\)/);
  const guarda = src.indexOf("notFound()");
  const render = src.indexOf("<ExpedicaoClient");
  assert.ok(guarda > 0 && render > guarda, "o 404 precisa vir ANTES do render");
});

test("com a flag desligada nenhum fetch pode ser disparado", () => {
  // O componente que faz fetch e' filho do guard: se o guard nao passar, ele
  // nao e' renderizado nem chega ao navegador.
  const page = fonte("app/expedicao/page.tsx");
  assert.ok(!page.includes("fetch("), "a pagina-guarda nao pode buscar nada");
  const cliente = fonte("app/expedicao/ExpedicaoClient.tsx");
  assert.ok(cliente.includes("fetch("), "o fetch vive no filho, atras do guard");
  assert.ok(page.includes("import ExpedicaoClient"));
});

// ===========================================================================
// Allowlists
// ===========================================================================
test("situacao: oito valores, nada fora da allowlist passa", () => {
  assert.equal(SITUACOES.length, 8);
  assert.deepEqual(sanitizarSituacoes(["overdue", "hack", "stalled"]), ["overdue", "stalled"]);
  assert.deepEqual(sanitizarSituacoes(["'; DROP TABLE x; --"]), []);
});

test("order_by: tres valores; invalido cai no padrao", () => {
  assert.equal(ORDENACOES.length, 3);
  assert.equal(sanitizarOrdenacao("deadline"), "deadline");
  assert.equal(sanitizarOrdenacao("<script>"), "criticidade");
});

test("limit e janela respeitam o contrato da API", () => {
  assert.equal(sanitizarLimit(9999), LIMIT_MAX);
  assert.equal(sanitizarLimit(0), 1);
  assert.equal(sanitizarLimit(Number.NaN), 100);
  assert.equal(sanitizarJanela(99999), JANELA_MAX_HORAS);
  assert.equal(sanitizarJanela(0), 1);
});

test("query nunca carrega valor fora da allowlist", () => {
  const q = construirQuery({
    ...FILTROS_PADRAO,
    situacao: ["overdue", "xpto" as never],
    orderBy: "hack" as never,
    limit: 99999,
  });
  assert.ok(q.includes("situacao=overdue"));
  assert.ok(!q.includes("xpto"));
  assert.ok(q.includes("order_by=criticidade"));
  assert.ok(q.includes("limit=500"));
});

// ===========================================================================
// KPIs e o limiar de 48h
// ===========================================================================
test("KPIs saem do payload e medida ausente vira null, nunca zero", () => {
  const kpis = montarKpis(TOTAIS, [frescor()]);
  const porChave = Object.fromEntries(kpis.map((k) => [k.chave, k.valor]));
  assert.equal(porChave.backlog, 985);
  assert.equal(porChave.overdue, 24);
  assert.equal(porChave.over48h, 272);
  assert.equal(porChave.stalled, 287);
  assert.equal(porChave.slow, 287);
  assert.equal(porChave.zombie, 0);

  const vazios = montarKpis(null, []);
  for (const k of vazios) assert.equal(k.valor, null, `${k.chave} virou numero`);
});

test("stalled e servido como medida, nunca como slow + zombie", () => {
  const t = { ...TOTAIS, slow_count: 30, zombie_count: 20, stalled_count: 40 };
  const kpis = montarKpis(t, []);
  const stalled = kpis.find((k) => k.chave === "stalled");
  assert.equal(stalled?.valor, 40);
  assert.notEqual(stalled?.valor, t.slow_count + t.zombie_count);
});

test("48h e descrito como limiar INTERNO, nunca como SLA do marketplace", () => {
  assert.match(ROTULO_LIMIAR_48H, /limiar interno/i);
  assert.match(EXPLICACAO_LIMIAR_48H, /nao e' SLA do marketplace/i);
  const kpi = montarKpis(TOTAIS, []).find((k) => k.chave === "over48h");
  assert.match(String(kpi?.nota), /nao e' SLA/i);
  const tela = fonte("app/expedicao/ExpedicaoClient.tsx");
  assert.ok(tela.includes("EXPLICACAO_LIMIAR_48H"), "a tela precisa exibir a ressalva");
  assert.ok(!/SLA da Shopee|SLA oficial|garantia de prazo/i.test(tela));
});

test("fronteira: 48h exatas ainda NAO cruzaram o limiar", () => {
  assert.equal(acimaDoLimiarInterno(47.9), false);
  assert.equal(acimaDoLimiarInterno(LIMIAR_OPERACIONAL_HORAS), false);
  assert.equal(acimaDoLimiarInterno(48.01), true);
  assert.equal(acimaDoLimiarInterno(null), false);
});

test("nao existe faixa inventada entre 24h e 48h", () => {
  // Prazo e idade operacional sao dimensoes ORTOGONAIS: a interseccao nao
  // existe no contrato e calcula-la aqui seria agregacao que a API nao fez.
  const chaves = montarKpis(TOTAIS, []).map((k) => k.chave);
  assert.ok(!chaves.some((c) => /24.*48|entre/i.test(c)));
});

// ===========================================================================
// Os tres relogios
// ===========================================================================
test("as tres idades sao campos distintos e nao se substituem", () => {
  const r = resposta({
    freshness: [frescor({ source_age_hours: 3.47, oldest_row_age_hours: 243.4 })],
  });
  const rel = montarRelogios(r);
  const fr = montarFrescor(r.freshness);
  assert.equal(rel.snapshotAgeHours, 1.2);          // fotografia
  assert.equal(fr[0].sourceAgeHours, 3.47);         // fonte
  assert.equal(fr[0].oldestRowAgeHours, 243.4);     // pedido mais antigo
  assert.notEqual(rel.snapshotAgeHours, fr[0].sourceAgeHours);
  assert.notEqual(fr[0].sourceAgeHours, fr[0].oldestRowAgeHours);
});

test("pedido antigo com fonte recente NAO vira fonte desatualizada", () => {
  const fr = montarFrescor([
    frescor({ freshness: "fresh", source_age_hours: 0.55, oldest_row_age_hours: 243.4 }),
  ]);
  assert.equal(fr[0].freshness, "fresh");
  assert.equal(fr[0].alerta, false);
  assert.match(fr[0].rotulo, /atualizada/i);
});

test("fonte desatualizada e frescor desconhecido entram em alerta", () => {
  assert.equal(montarFrescor([frescor({ freshness: "critical" })])[0].alerta, true);
  assert.equal(montarFrescor([frescor({ freshness: "unknown" })])[0].alerta, true);
  assert.equal(montarFrescor([frescor({ freshness: "stale" })])[0].alerta, false);
});

test("estado derivado do lote e' rotulado como derivado", () => {
  const fr = montarFrescor([frescor({ derived_from_batch: true, measures: null })]);
  assert.equal(fr[0].derivado, true);
  assert.ok(fonte("app/expedicao/ExpedicaoClient.tsx").includes("derivado do lote"));
});

test("source_advanced nao altera veredito nenhum", () => {
  const comFalse = resposta({ snapshot: { ...resposta().snapshot!, source_advanced: false } });
  const est = estadoDaTela({
    carregando: false, erroHttp: null, resposta: comFalse, temFiltro: false,
  });
  assert.equal(est, "ok");
  const kpis = montarKpis(comFalse.totals, comFalse.freshness);
  assert.equal(kpis.find((k) => k.chave === "marcas_alerta")?.valor, 0);
  // e a tela o rotula como metadado
  assert.ok(fonte("app/expedicao/ExpedicaoClient.tsx").includes("(metadado)"));
});

test("snapshot antigo e sinalizado sem mudar os numeros", () => {
  const r = resposta({
    limitations: { ...resposta().limitations, snapshot_age_hours: 30 },
  });
  const rel = montarRelogios(r);
  assert.equal(rel.snapshotVelho, true);
  assert.equal(montarKpis(r.totals, []).find((k) => k.chave === "backlog")?.valor, 985);
});

test("aviso de carga manual e ausencia de automacao sempre presentes", () => {
  const rel = montarRelogios(resposta());
  assert.equal(rel.modoDeCarga, "manual_snapshot");
  assert.equal(rel.semAutomacao, true);
  // EXP-UX-1: o aviso deixou de ser um banner solto na tela e passou a ser um
  // item da barra consolidada de qualidade. A garantia que importa nao e' a
  // string estar num arquivo, e' o aviso SEMPRE existir quando nao ha
  // automacao — entao a assercao virou comportamental.
  const barra = montarBarraDeQualidade(rel, [], []);
  const semAutomacao = barra.detalhes.find((d) => d.chave === "sem_automacao");
  assert.ok(semAutomacao, "aviso de ausencia de automacao sumiu da barra");
  assert.equal(semAutomacao?.texto, AVISO_SEM_AUTOMACAO);

  const tela = fonte("app/expedicao/ExpedicaoClient.tsx");
  assert.ok(tela.includes("manual_snapshot"));
  assert.ok(!/automatizad|agendad/i.test(tela.replace(/Sem automa\w+/gi, "")));
});

// ===========================================================================
// Cobertura
// ===========================================================================
test("Kokeshi aparece como fora do escopo, nunca como zero", () => {
  const c: Cobertura = {
    expected_accounts: ["apice", "barbours", "lescent", "rituaria"],
    observed_accounts: ["apice", "barbours", "lescent", "rituaria"],
    missing_accounts: [],
    unexpected_accounts: [],
    accounts: [],
    brands_not_covered: ["kokeshi"],
  };
  const avisos = avisosDeCobertura(c);
  const kokeshi = avisos.find((a) => a.texto.includes("kokeshi"));
  assert.ok(kokeshi);
  assert.equal(kokeshi.tipo, "fora_do_escopo");
  assert.match(kokeshi.texto, /nao backlog zero/i);
  assert.ok(!avisos.some((a) => a.tipo === "faltando"));
});

test("conta faltando e conta inesperada sao avisos DIFERENTES", () => {
  const base: Cobertura = {
    expected_accounts: ["apice"], observed_accounts: [], missing_accounts: ["apice"],
    unexpected_accounts: ["nova"], accounts: [], brands_not_covered: [],
  };
  const tipos = avisosDeCobertura(base).map((a) => a.tipo);
  assert.deepEqual(tipos, ["faltando", "inesperada"]);
});

// ===========================================================================
// Estados da tela
// ===========================================================================
test("estados: carregando, ok, vazia, filtro vazio, 422, erro", () => {
  const base = { erroHttp: null, resposta: resposta(), temFiltro: false };
  assert.equal(estadoDaTela({ ...base, carregando: true }), "carregando");
  assert.equal(estadoDaTela({ ...base, carregando: false }), "ok");
  assert.equal(
    estadoDaTela({ ...base, carregando: false, erroHttp: 422 }), "erro_validacao");
  assert.equal(estadoDaTela({ ...base, carregando: false, erroHttp: 503 }), "erro");
  assert.equal(
    estadoDaTela({ ...base, carregando: false, resposta: null }), "erro");

  const vazia = resposta({ totals: { ...TOTAIS, backlog_count: 0 } });
  assert.equal(
    estadoDaTela({ carregando: false, erroHttp: null, resposta: vazia, temFiltro: false }),
    "fotografia_vazia");

  const semLinhas = resposta({
    pagination: { limit: 500, offset: 0, returned: 0, total: 0, has_more: false },
  });
  assert.equal(
    estadoDaTela({ carregando: false, erroHttp: null, resposta: semLinhas, temFiltro: true }),
    "fila_vazia_por_filtro");
});

test("backend desligado e batch inconsistente sao estados distintos", () => {
  const off = resposta({ availability: "unavailable", unavailable_reason: "feature_flag_disabled" });
  const inc = resposta({ availability: "unavailable", unavailable_reason: "inconsistent_batch" });
  const sem = resposta({ availability: "unavailable", unavailable_reason: "no_snapshot_published" });
  const e = (r: RespostaExpedicao) =>
    estadoDaTela({ carregando: false, erroHttp: null, resposta: r, temFiltro: false });
  assert.equal(e(off), "indisponivel_backend");
  assert.equal(e(inc), "batch_inconsistente");
  assert.equal(e(sem), "sem_snapshot");
});

test("batch inconsistente nao mostra numero parcial", () => {
  const tela = fonte("app/expedicao/ExpedicaoClient.tsx");
  const bloco = tela.slice(tela.indexOf('estado === "batch_inconsistente"'));
  assert.match(bloco.slice(0, 500), /inconsistente/i);
  // os KPIs so' entram nos estados ok / fila vazia por filtro
  assert.match(tela, /estado === "ok" \|\| estado === "fila_vazia_por_filtro"/);
});

// ===========================================================================
// Filtros, paginacao e corrida
// ===========================================================================
test("trocar filtro reinicia a pagina", () => {
  const f = { ...FILTROS_PADRAO, offset: 1000 };
  assert.equal(aplicarFiltro(f, { situacao: ["overdue"] }).offset, 0);
  assert.equal(aplicarFiltro(f, { brands: ["apice"] }).offset, 0);
  assert.equal(aplicarFiltro(f, { orderBy: "deadline" }).offset, 0);
  // paginar explicitamente preserva o recorte
  assert.equal(aplicarFiltro(f, { offset: 500 }).offset, 500);
});

test("resposta atrasada de filtro antigo nao sobrescreve a atual", () => {
  const a = chaveDaRequisicao({ ...FILTROS_PADRAO, situacao: ["overdue"] });
  const b = chaveDaRequisicao({ ...FILTROS_PADRAO, situacao: ["stalled"] });
  assert.notEqual(a, b);
  const tela = fonte("app/expedicao/ExpedicaoClient.tsx");
  assert.ok(tela.includes("chaveVigente"), "falta a guarda de chave vigente");
  assert.match(tela, /chaveVigente\.current !== minhaChave/);
});

test("paginacao: pagina atual derivada de limit e offset", () => {
  assert.deepEqual(
    paginaAtual({ limit: 500, offset: 0, returned: 500, total: 985, has_more: true }),
    { pagina: 1, total: 2 });
  assert.deepEqual(
    paginaAtual({ limit: 500, offset: 500, returned: 485, total: 985, has_more: false }),
    { pagina: 2, total: 2 });
  assert.deepEqual(paginaAtual(null), { pagina: 1, total: 1 });
});

// ===========================================================================
// Tendencia
// ===========================================================================
function ponto(conta: string, hora: string, backlog: number): PontoTendencia {
  return {
    ...TOTAIS, backlog_count: backlog, snapshot_hour: hora, shop_account: conta,
    brand: conta, refresh_batch_id: "b", observed_at: hora,
    source_watermark_at: null, source_advanced: false, run_status: "success",
  };
}

test("tendencia: uma serie por conta, sem somar contas nem horas", () => {
  const series = montarSeries([
    ponto("apice", "2026-09-16T18:00:00Z", 164),
    ponto("barbours", "2026-09-16T18:00:00Z", 609),
    ponto("apice", "2026-09-16T21:00:00Z", 164),
    ponto("barbours", "2026-09-16T21:00:00Z", 609),
  ]);
  assert.equal(series.length, 2);
  assert.deepEqual(series.map((s) => s.shopAccount), ["apice", "barbours"]);
  assert.deepEqual(series[0].pontos.map((p) => p.backlog), [164, 164]);
  // nenhuma serie agregada com o total das contas
  assert.ok(!series.some((s) => s.pontos.some((p) => p.backlog === 773)));
});

test("tendencia: pontos ordenados por hora", () => {
  const s = montarSeries([
    ponto("apice", "2026-09-16T21:00:00Z", 2),
    ponto("apice", "2026-09-16T18:00:00Z", 1),
  ]);
  assert.deepEqual(s[0].pontos.map((p) => p.backlog), [1, 2]);
});

test("tendencia: serie vazia, parcial e erro sao estados distintos", () => {
  assert.equal(estadoDaTendencia(true, false, [], 4), "carregando");
  assert.equal(estadoDaTendencia(false, true, [], 4), "erro");
  assert.equal(estadoDaTendencia(false, false, [], 4), "vazia");
  const completa = montarSeries(
    ["a", "b", "c", "d"].map((c) => ponto(c, "2026-09-16T21:00:00Z", 1)));
  assert.equal(estadoDaTendencia(false, false, completa, 4), "ok");
  const parcial = montarSeries(
    ["a", "b"].map((c) => ponto(c, "2026-09-16T21:00:00Z", 1)));
  assert.equal(estadoDaTendencia(false, false, parcial, 4), "parcial");
});

// ===========================================================================
// Identificadores
// ===========================================================================
function linha(over: Partial<LinhaFila> = {}): LinhaFila {
  return {
    order_ref: null, shop_account: "apice", brand: "apice",
    created_at: null, paid_at: null, dispatch_deadline: null,
    deadline_source: "marketplace_native", deadline_status: "on_time",
    operational_age_status: "within_48h", is_slow_vs_baseline: false,
    is_source_zombie: false, is_stalled: false, hours_open: 10,
    hours_overdue: null, logistic_type: null, carrier: "Shopee Xpress",
    source_ingested_at: null, source_freshness_status: "fresh",
    timestamp_quality: "verified", ...over,
  };
}

test("order_ref ausente: coluna nao aparece", () => {
  assert.equal(mostrarColunaReferencia([linha(), linha()]), false);
  assert.equal(mostrarColunaReferencia([]), false);
});

test("order_ref presente: coluna aparece", () => {
  assert.equal(mostrarColunaReferencia([linha({ order_ref: "AbC123" }), linha()]), true);
});

test("zero order_sn no codigo executavel, nos tipos e na tela", () => {
  const arquivos = [
    "src/lib/expedicao-contract.ts",
    "app/expedicao/page.tsx",
    "app/expedicao/ExpedicaoClient.tsx",
    "src/components/shell/nav-config.ts",
  ];
  for (const rel of arquivos) {
    const src = fonte(rel);
    // remove comentarios: eles EXPLICAM por que o identificador nao e servido,
    // e um teste que proibisse a explicacao obrigaria a documentar menos.
    const codigo = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    for (const proibido of ["order_sn", "marketplace_order_id"]) {
      assert.ok(!codigo.includes(proibido), `${rel} carrega ${proibido}`);
    }
  }
});

test("order_ref nunca vira link para o marketplace", () => {
  const tela = fonte("app/expedicao/ExpedicaoClient.tsx");
  const trecho = tela.slice(tela.indexOf("order_ref"));
  assert.ok(!/shopee\.com|href=\{.*order_ref/i.test(trecho));
  assert.ok(!tela.includes("https://shopee"));
});

test("zero PII nos tipos da tela", () => {
  const src = fonte("src/lib/expedicao-contract.ts");
  for (const t of ["buyer", "cpf", "telefone", "endereco", "recipient",
                   "email", "username", "password", "token"]) {
    assert.ok(!new RegExp(`\\b\\w*${t}\\w*\\s*[?:]`, "i").test(src), `campo com ${t}`);
  }
});

test("nenhum segredo chega ao navegador", () => {
  for (const rel of ["app/expedicao/page.tsx", "app/expedicao/ExpedicaoClient.tsx",
                     "src/lib/expedicao-contract.ts"]) {
    const src = fonte(rel);
    assert.ok(!/_SECRET|_secret|hmac|HMAC/i.test(src), `${rel} menciona segredo`);
    const publicos = [...src.matchAll(/process\.env\.(\w+)/g)].map((m) => m[1]);
    for (const v of publicos) {
      assert.ok(v.startsWith("NEXT_PUBLIC_"), `variavel nao publica no cliente: ${v}`);
    }
  }
});

test("mensagens de erro nao expoem detalhe tecnico", () => {
  const tela = fonte("app/expedicao/ExpedicaoClient.tsx");
  const mensagens = [...tela.matchAll(/role="alert"[\s\S]{0,400}?<\/p>/g)].join(" ");
  for (const t of ["stack", "Traceback", "psycopg", "sqlalchemy", "postgres",
                   "localhost:8080", "5432"]) {
    assert.ok(!mensagens.includes(t), `mensagem expoe ${t}`);
  }
});

// ===========================================================================
// Acessibilidade
// ===========================================================================
/**
 * Extrai a TAG DE ABERTURA de cada elemento interativo.
 *
 * Um regex ingenuo pararia no primeiro `>`, que em JSX aparece dentro de
 * `onChange={(e) => ...}`: o elemento sairia cortado e o teste passaria sem
 * olhar o `className`. Aqui o corte respeita a profundidade de chaves.
 */
function tagsDeAbertura(src: string, tags: readonly string[]): string[] {
  const achados: string[] = [];
  for (const tag of tags) {
    let i = src.indexOf(`<${tag}`);
    while (i !== -1) {
      let profundidade = 0;
      let j = i;
      for (; j < src.length; j++) {
        const c = src[j];
        if (c === "{") profundidade++;
        else if (c === "}") profundidade--;
        else if (c === ">" && profundidade === 0) break;
      }
      achados.push(src.slice(i, j + 1));
      i = src.indexOf(`<${tag}`, j);
    }
  }
  return achados;
}

test("controles interativos tem alvo minimo de 44px", () => {
  const tela = fonte("app/expedicao/ExpedicaoClient.tsx");
  const interativos = tagsDeAbertura(tela, ["button", "select", "input"]);
  assert.ok(interativos.length >= 4, `poucos controles: ${interativos.length}`);
  for (const el of interativos) {
    assert.ok(/min-h-\[44px\]/.test(el), `sem alvo de 44px: ${el.slice(0, 120)}`);
  }
});

test("nenhum texto abaixo de 12px", () => {
  const tela = fonte("app/expedicao/ExpedicaoClient.tsx");
  const tamanhos = [...tela.matchAll(/text-\[(\d+)px\]/g)].map((m) => Number(m[1]));
  for (const t of tamanhos) assert.ok(t >= 12, `texto de ${t}px`);
  assert.ok(!/text-\[1[01]px\]|\btext-\[\d\]px/.test(tela));
});

test("tabelas tem caption e cabecalho com escopo", () => {
  const tela = fonte("app/expedicao/ExpedicaoClient.tsx");
  const tabelas = (tela.match(/<table\b/g) ?? []).length;
  const captions = (tela.match(/<caption\b/g) ?? []).length;
  assert.equal(tabelas, captions, "toda tabela precisa de caption");
  assert.ok((tela.match(/scope="col"/g) ?? []).length >= tabelas);
  assert.ok((tela.match(/scope="row"/g) ?? []).length >= 1);
});

test("botoes de filtro anunciam estado por aria-pressed", () => {
  const tela = fonte("app/expedicao/ExpedicaoClient.tsx");
  assert.match(tela, /aria-pressed=\{filtros\.situacao\.includes\(s\)\}/);
});

test("estados de carga e erro sao anunciados por role", () => {
  const tela = fonte("app/expedicao/ExpedicaoClient.tsx");
  assert.ok((tela.match(/role="status"/g) ?? []).length >= 3);
  assert.ok((tela.match(/role="alert"/g) ?? []).length >= 3);
  assert.match(tela, /aria-busy=\{carregando\}/);
});

test("tabelas grandes ficam contidas por overflow", () => {
  const tela = fonte("app/expedicao/ExpedicaoClient.tsx");
  const tabelas = (tela.match(/<table\b/g) ?? []).length;
  // `overflow-auto` conta tambem: rola nos DOIS eixos, entao e' estritamente
  // mais forte que `overflow-x-auto`. O redesign usa a versao dos dois eixos
  // nas tabelas altas (fila e dados da tendencia), que ganharam altura maxima.
  const contidas = (tela.match(/overflow-(x-)?auto/g) ?? []).length;
  assert.ok(contidas >= tabelas, "tabela sem container rolavel transborda no mobile");
});


test("a topbar resolve o titulo da rota de Expedicao", () => {
  assert.equal(getRouteTitle("/expedicao"), EXPEDICAO_NAV.label);
  // e nao quebra as rotas existentes
  assert.equal(getRouteTitle("/regioes"), "Regiões");
  assert.equal(getRouteTitle("/"), "Gerencial");
  assert.equal(getRouteTitle("/rota-inexistente"), "Torre de Controle");
});

test("a pagina nao cria um segundo h1: o shell e dono dele", () => {
  const tela = fonte("app/expedicao/ExpedicaoClient.tsx");
  assert.equal((tela.match(/<h1\b/g) ?? []).length, 0, "dois h1 quebram a arvore de cabecalhos");
  assert.ok((tela.match(/<h2\b/g) ?? []).length >= 1);
});
