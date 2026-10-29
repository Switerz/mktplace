/**
 * Gate EXP-UX-1 — redesign operacional da Expedicao.
 *
 * Testa a LOGICA do redesign (funcoes puras do contrato) e, onde a garantia so'
 * existe na montagem, o CODIGO-FONTE da tela. Nao ha harness de React aqui: a
 * decisao do EXP-2B foi manter a logica fora do componente exatamente para
 * poder testa-la sem DOM.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  CANAL_PADRAO,
  JANELAS_TENDENCIA,
  JANELA_INICIAL_HORAS,
  IDADES_OPERACIONAIS,
  KPIS_PRINCIPAIS,
  ROTULO_IDADE_OPERACIONAL,
  ROTULO_SEM_PRAZO,
  SITUACOES,
  TIMELINE_MAX_LINHAS,
  VALOR_SEM_PRAZO,
  expedicaoMlHabilitado,
  montarBarraDeQualidade,
  montarKpisPrincipais,
  montarKpisSecundarios,
  montarKpis,
  montarMapaDeRisco,
  montarPontosDoGrafico,
  montarSeries,
  montarTimeline,
  queryDaTendencia,
  rotuloDeadline,
  rotuloIdade,
  segmentosFecham,
  type AvisoCobertura,
  type LinhaFila,
  type LinhaFrescor,
  type PontoTendencia,
  type Relogios,
  type ResumoConta,
  type Totais,
} from "../src/lib/expedicao-contract.ts";
import { REGRA_JANELA } from "../src/lib/expedicao-janela.ts";
import { lerCanalExpedicao } from "../src/lib/expedicao-canal.ts";

const RAIZ = join(import.meta.dirname, "..");
const CLIENTE = readFileSync(join(RAIZ, "app/expedicao/ExpedicaoClient.tsx"), "utf8");
// EXPEDICAO-UX-2: o grafico unico (recharts) virou small multiples em SVG
// proprio. As garantias de grafico seguem para o componente novo.
const GRAFICO = readFileSync(
  join(RAIZ, "src/components/expedicao/EvolucaoPorMarca.tsx"),
  "utf8",
);
// EXPEDICAO-UX-UNIFICADA-1: o seletor de canal e a flag do ML foram para a
// casca, e a barra de qualidade e o cabecalho viraram componentes dos tres
// canais. As garantias abaixo seguem o codigo ate' o lugar novo.
const CASCA = readFileSync(join(RAIZ, "app/expedicao/ExpedicaoShell.tsx"), "utf8");
const TIKTOK = readFileSync(join(RAIZ, "src/components/expedicao/TikTokDispatchPanel.tsx"), "utf8");
const CABECALHO = readFileSync(
  join(RAIZ, "src/components/expedicao/CabecalhoExpedicao.tsx"),
  "utf8",
);
const BARRA = readFileSync(join(RAIZ, "src/components/expedicao/BarraDeQualidade.tsx"), "utf8");
const SEGMENTED = readFileSync(join(RAIZ, "src/components/ui/Segmented.tsx"), "utf8");

function totais(over: Partial<Totais> = {}): Totais {
  return {
    backlog_count: 714,
    overdue_count: 0,
    due_within_24h_count: 0,
    on_time_count: 0,
    deadline_unavailable_count: 714,
    over_48h_count: 179,
    slow_count: 0,
    zombie_count: 0,
    stalled_count: 0,
    ...over,
  };
}

function conta(over: Partial<ResumoConta> = {}): ResumoConta {
  return {
    shop_account: "kokeshi",
    brand: "kokeshi",
    run_status: "success",
    source_watermark_at: null,
    source_advanced: true,
    snapshot_hour: "2026-09-22T19:00:00Z",
    ...totais(),
    ...over,
  };
}

// ===========================================================================
// 1. Quatro KPIs, com SIGNIFICADO diferente por canal
// ===========================================================================
test("cada canal destaca exatamente quatro KPIs", () => {
  for (const canal of ["shopee", "mercadolivre"] as const) {
    assert.equal(KPIS_PRINCIPAIS[canal].length, 4, canal);
    assert.equal(montarKpisPrincipais(totais(), [], canal).length, 4, canal);
  }
});

test("a Shopee destaca prazo; o Mercado Livre destaca idade e fonte", () => {
  assert.deepEqual([...KPIS_PRINCIPAIS.shopee], [
    "backlog",
    "overdue",
    "due24h",
    "stalled",
  ]);
  assert.deepEqual([...KPIS_PRINCIPAIS.mercadolivre], [
    "backlog",
    "over48h",
    "stalled",
    "marcas_alerta",
  ]);
  // Os conjuntos NAO podem ser iguais: se fossem, o ML abriria a tela com
  // quatro cartoes N/D e a area mais nobre nao responderia nada.
  assert.notDeepEqual([...KPIS_PRINCIPAIS.shopee], [...KPIS_PRINCIPAIS.mercadolivre]);
});

test("o ML nunca destaca 'vencidos', e se destacasse viria N/D e nao zero", () => {
  assert.ok(!KPIS_PRINCIPAIS.mercadolivre.includes("overdue"));
  const overdue = montarKpis(totais(), [], "mercadolivre").find(
    (k) => k.chave === "overdue",
  );
  assert.equal(overdue?.valor, null, "ausencia de prazo nao pode virar zero");
  assert.equal(overdue?.valorTexto, VALOR_SEM_PRAZO);
  assert.equal(overdue?.alerta, false, "sem medicao nao ha alarme");
});

test("nenhum KPI e' descartado: principais + secundarios = todos", () => {
  for (const canal of ["shopee", "mercadolivre"] as const) {
    const todos = montarKpis(totais(), [], canal).map((k) => k.chave).sort();
    const juntos = [
      ...montarKpisPrincipais(totais(), [], canal),
      ...montarKpisSecundarios(totais(), [], canal),
    ]
      .map((k) => k.chave)
      .sort();
    assert.deepEqual(juntos, todos, canal);
  }
});

// ===========================================================================
// 2. Mapa de risco: exclusivo na Shopee, sobreposto no ML
// ===========================================================================
test("as barras da Shopee sao mutuamente exclusivas e fecham o backlog", () => {
  const mapa = montarMapaDeRisco(
    [
      conta({
        shop_account: "apice",
        backlog_count: 100,
        overdue_count: 30,
        due_within_24h_count: 20,
        on_time_count: 45,
        deadline_unavailable_count: 5,
      }),
    ],
    "shopee",
  );
  assert.equal(mapa.empilhavel, true);
  const linha = mapa.linhas[0];
  assert.equal(linha.segmentos.length, 4);
  assert.ok(segmentosFecham(linha), "30+20+45+5 tem de fechar os 100");
  assert.equal(
    linha.segmentos.reduce((s, f) => s + f.valor, 0),
    linha.backlog,
  );
  assert.equal(linha.sobreposicoes.length, 0, "na Shopee nada e' sobreposto aqui");
});

test("o ML NAO empilha: backlog, 48h e travados se sobrepoem", () => {
  const mapa = montarMapaDeRisco(
    [conta({ backlog_count: 421, over_48h_count: 169, stalled_count: 12 })],
    "mercadolivre",
  );
  assert.equal(mapa.empilhavel, false, "empilhar inventaria um total");
  const linha = mapa.linhas[0];
  assert.equal(linha.segmentos.length, 0, "sem particao, sem segmentos");
  assert.equal(linha.sobreposicoes.length, 2);
  // A soma das sobreposicoes NAO e' o backlog, e nada no contrato afirma isso.
  const soma = linha.sobreposicoes.reduce((s, f) => s + f.valor, 0);
  assert.notEqual(soma, linha.backlog);
  assert.ok(soma < linha.backlog + linha.backlog, "nunca somadas como exclusivas");
  assert.match(mapa.nota, /sobrep/i);
  assert.match(mapa.nota, /limiar/i, "48h precisa ser declarado como limiar interno");
});

test("a tela pinta empilhado SOMENTE quando o mapa autoriza", () => {
  // O componente decide pelo campo do contrato, nao por um `if (canal ===)`
  // espalhado na marcacao.
  assert.match(CLIENTE, /empilhavel/);
  assert.match(CLIENTE, /mapa\.empilhavel/);
});

// ===========================================================================
// 3. Barra consolidada de qualidade — nada escondido
// ===========================================================================
function relogios(over: Partial<Relogios> = {}): Relogios {
  return {
    snapshotAgeHours: 21.9,
    snapshotVelho: true,
    efetivoEm: "2026-09-22T19:04:10Z",
    ...over,
  };
}

test("defasagem e cobertura entram todas na barra", () => {
  const avisos: AvisoCobertura[] = [
    { tipo: "fora_do_escopo", texto: "kokeshi nao e' coberta por esta fonte." },
  ];
  const frescor: LinhaFrescor[] = [
    {
      brand: "lescent",
      freshness: "critical",
      rotulo: "Fonte desatualizada",
      sourceAgeHours: 40,
      oldestRowAgeHours: 300,
      watermark: null,
      derivado: false,
      alerta: true,
    },
  ];
  const barra = montarBarraDeQualidade(relogios(), avisos, frescor);
  const chaves = barra.detalhes.map((d) => d.chave);
  assert.ok(chaves.includes("fotografia_antiga"));
  assert.ok(chaves.includes("fonte_em_alerta"));
  assert.equal(chaves.filter((c) => c.startsWith("cobertura_")).length, 1);
  assert.equal(barra.detalhes.length, 3, "nenhuma limitacao pode sumir");
  // EXPEDICAO-UX-UNIFICADA-1: o "sem automacao" saiu — repetia o
  // `no_automation` constante da API, falso com a DAG publicando.
  assert.ok(!chaves.includes("sem_automacao"));
});

test("o item mais grave e' o que aparece fechado", () => {
  const barra = montarBarraDeQualidade(
    relogios(),
    [{ tipo: "faltando", texto: "Conta esperada ausente da fotografia: apice." }],
    [],
  );
  assert.equal(barra.severidade, "alerta");
  assert.match(barra.principal?.texto ?? "", /ausente/);
});

test("fotografia velha aparece mesmo quando nada mais esta errado", () => {
  const barra = montarBarraDeQualidade(relogios(), [], []);
  assert.equal(barra.principal?.chave, "fotografia_antiga");
  assert.match(barra.principal?.texto ?? "", /21,9 h|21\.9 h/);
});

test("sem nenhum problema, a barra nao e' pintada", () => {
  const barra = montarBarraDeQualidade(
    relogios({ snapshotVelho: false }),
    [],
    [],
  );
  assert.equal(barra.principal, null);
  assert.equal(barra.detalhes.length, 0);
});

test("a barra nunca afirma publicacao manual nem ausencia de agendamento", () => {
  // A API devolve `load_mode`/`no_automation` como CONSTANTES; desde
  // 21/10/2026 quem publica e' a DAG. A barra nao repete essa declaracao.
  const barra = montarBarraDeQualidade(relogios(), [], []);
  for (const d of barra.detalhes) {
    assert.doesNotMatch(d.texto, /autom|manual|à mão|a mao|agend/i, d.texto);
  }
});

// ===========================================================================
// 4. Tendencia: mesmo canal, janelas, lacuna e tabela acessivel
// ===========================================================================
test("as tres janelas sao 24h, 72h e 7d", () => {
  assert.deepEqual(
    JANELAS_TENDENCIA.map((j) => j.horas),
    [24, 72, 168],
  );
});

test("a tendencia usa o MESMO canal da fila", () => {
  assert.equal(queryDaTendencia(24, "mercadolivre"), "channel=mercadolivre&window_hours=24");
  // Shopee e' o canal historico: omitir `channel` significa exatamente ela.
  assert.equal(queryDaTendencia(24, CANAL_PADRAO), "window_hours=24");
  assert.match(CLIENTE, /queryDaTendenciaEmDias\(janela, filtros\.channel\)/);
});

test("hora sem ponto vira lacuna, nunca zero", () => {
  const pontos: PontoTendencia[] = [
    { ...totais(), snapshot_hour: "h1", shop_account: "a", brand: "a", refresh_batch_id: "b", observed_at: "o", source_watermark_at: null, source_advanced: true, run_status: "success" },
    { ...totais(), snapshot_hour: "h2", shop_account: "a", brand: "a", refresh_batch_id: "b", observed_at: "o", source_watermark_at: null, source_advanced: true, run_status: "success" },
    { ...totais(), snapshot_hour: "h1", shop_account: "b", brand: "b", refresh_batch_id: "b", observed_at: "o", source_watermark_at: null, source_advanced: true, run_status: "success" },
  ];
  const grafico = montarPontosDoGrafico(montarSeries(pontos));
  const h2 = grafico.find((p) => p.hora === "h2");
  assert.equal(h2?.b, null, "conta sem ponto na hora e' lacuna");
  assert.notEqual(h2?.b, 0, "zero desenharia uma queda que nao houve");
});

test("o grafico nao costura lacunas", () => {
  // Antes: `connectNulls={false}` no recharts. Agora a linha e' desenhada por
  // TRECHO continuo; hora sem ponto separa dois trechos (ver
  // `montarEvolucaoPorMarca` em expedicao-ux2.test.ts).
  assert.match(GRAFICO, /serie\.trechos\.map\(/);
  assert.equal((GRAFICO.match(/<polyline/g) ?? []).length, 1, "uma linha, por trecho");
});

test("a tabela acessivel da tendencia continua disponivel", () => {
  assert.match(CLIENTE, /Ver dados/);
  assert.match(CLIENTE, /aria-expanded=\{dadosDaTendencia\}/);
  // e continua sendo uma tabela de verdade, com caption e escopo
  assert.match(CLIENTE, /contas nunca são somadas/);
});

test("'Ver dados' so' existe quando ha serie", () => {
  // Com a tendencia vazia o bloco do grafico e da tabela nao e' renderizado.
  // Um botao que promete revelar esse bloco seria um controle que nao faz nada
  // e um `aria-controls` apontando para um alvo inexistente — foi o que
  // aconteceu na Shopee em producao, cuja fotografia cai fora da janela.
  assert.match(CLIENTE, /\{temSerieNaTendencia && \(\s*<button/);
  assert.match(CLIENTE, /const temSerieNaTendencia =/);
});

test("botao e bloco usam UMA condicao, nao duas que podem divergir", () => {
  const usos = (CLIENTE.match(/temSerieNaTendencia/g) ?? []).length;
  assert.ok(usos >= 3, `esperava definicao + botao + bloco, achei ${usos}`);
  // A condicao antiga, duplicada, nao pode ter sobrado em lugar nenhum.
  assert.doesNotMatch(
    CLIENTE,
    /\(estadoTend === "ok" \|\| estadoTend === "parcial"\)/,
  );
});

test("o estado vazio da tendencia continua explicado", () => {
  // Esconder o botao nao pode esconder a explicacao: sem serie, a tela diz por
  // que, em vez de mostrar um cartao mudo.
  assert.match(CLIENTE, /estadoTend === "vazia"/);
  assert.match(CLIENTE, /Sem pontos na janela escolhida/);
});

test("serie parcial segue visivel e explicada", () => {
  assert.match(CLIENTE, /Série parcial/);
  assert.match(CLIENTE, /falta dado, não porque o backlog caiu/);
});

// ===========================================================================
// 5. Timeline: recorte explicito, e o ML sem prazo fabricado
// ===========================================================================
function linha(over: Partial<LinhaFila> = {}): LinhaFila {
  return {
    order_ref: null,
    shop_account: "kokeshi",
    brand: "kokeshi",
    created_at: null,
    paid_at: null,
    dispatch_deadline: null,
    deadline_source: "unavailable",
    deadline_status: "unavailable",
    operational_age_status: "over_48h",
    is_slow_vs_baseline: false,
    is_source_zombie: false,
    is_stalled: false,
    hours_open: 100,
    hours_overdue: null,
    logistic_type: "cross_docking",
    carrier: null,
    source_ingested_at: null,
    source_freshness_status: "critical",
    timestamp_quality: "assumed",
    ...over,
  };
}

test("a timeline nunca desenha a fila inteira", () => {
  const fila = Array.from({ length: 200 }, (_, i) => linha({ hours_open: i }));
  const barras = montarTimeline(fila, "shopee");
  assert.equal(barras.length, TIMELINE_MAX_LINHAS);
  assert.ok(TIMELINE_MAX_LINHAS < 100, "recorte tem de ser bem menor que a pagina");
});

test("a timeline mostra os mais antigos primeiro", () => {
  const barras = montarTimeline(
    [linha({ hours_open: 10 }), linha({ hours_open: 300 }), linha({ hours_open: 50 })],
    "shopee",
  );
  assert.deepEqual(
    barras.map((b) => b.horasAbertas),
    [300, 50, 10],
  );
});

test("o ML nunca ganha prazo nem 'vencido' na timeline", () => {
  const barras = montarTimeline(
    [linha({ dispatch_deadline: "2026-09-20T00:00:00Z", deadline_status: "overdue" })],
    "mercadolivre",
  );
  assert.equal(barras[0].prazo, null, "prazo fabricado seria SLA inventado");
  assert.equal(barras[0].vencido, false);
});

test("a Shopee mantem o prazo contratual na timeline", () => {
  const barras = montarTimeline(
    [linha({ dispatch_deadline: "2026-09-20T00:00:00Z", deadline_status: "overdue" })],
    "shopee",
  );
  assert.equal(barras[0].prazo, "2026-09-20T00:00:00Z");
  assert.equal(barras[0].vencido, true);
});

test("a tela diz que a timeline e' um recorte da pagina", () => {
  assert.match(CLIENTE, /não é o\s+backlog inteiro|nao e' o backlog inteiro/);
});

// ===========================================================================
// 6. Nenhuma agregacao sobre a fila paginada
// ===========================================================================
test("a tela nunca soma, conta ou reduz a fila paginada", () => {
  // `dados.queue` so' pode ser lido para renderizar linha a linha e para a
  // timeline (que e' explicitamente um recorte da pagina). Qualquer reduce,
  // filter-count ou soma sobre ele seria total global calculado em cima de 100
  // pedidos.
  const proibidos = [
    /queue\s*\.\s*reduce/,
    /queue\s*\.\s*filter\([^)]*\)\s*\.\s*length/,
    /queue\.length\s*[><=]/,
  ];
  for (const p of proibidos) {
    assert.doesNotMatch(CLIENTE, p, `agregacao sobre a fila paginada: ${p}`);
  }
  // os totais vem SEMPRE do payload
  assert.match(CLIENTE, /dados\?\.totals\?\.backlog_count/);
});

test("a paginacao e os filtros continuam existindo", () => {
  for (const marca of ["Anterior", "Próxima", "Ordenar", "aria-pressed"]) {
    assert.ok(CLIENTE.includes(marca), marca);
  }
});

// ===========================================================================
// 7. Canal: deep link, popstate e descarte de resposta obsoleta
// ===========================================================================
test("deep link, popstate e troca de canal continuam ligados", () => {
  assert.match(CLIENTE, /urlPronta/);
  assert.match(CLIENTE, /popstate/);
  assert.match(CLIENTE, /window\.history\.replaceState/);
  assert.match(CLIENTE, /aplicarCanal/);
});

test("as duas buscas esperam a URL antes de disparar", () => {
  const guardas = CLIENTE.match(/if \(!urlPronta\) return;/g) ?? [];
  assert.equal(guardas.length, 2, "fila E tendencia precisam esperar");
});

test("resposta obsoleta e' descartada nos dois fluxos", () => {
  assert.ok((CLIENTE.match(/chaveVigente\.current !== minhaChave/g) ?? []).length >= 2);
  assert.equal((CLIENTE.match(/chaveTendVigente\.current !== minhaChave/g) ?? []).length, 2);
});

// ===========================================================================
// 8. Flag desligada preserva o comportamento historico
// ===========================================================================
test("a flag continua fail-closed e dobravel em build", () => {
  assert.equal(expedicaoMlHabilitado("true"), true);
  for (const v of ["TRUE", "1", "yes", "", undefined, null]) {
    assert.equal(expedicaoMlHabilitado(v as string), false, String(v));
  }
  // A comparacao literal e' o que permite o minificador eliminar a opcao
  // quando a flag esta definida como algo diferente de "true". Desde o
  // EXPEDICAO-UX-UNIFICADA-1 ela vive na casca, dona do seletor unico.
  assert.match(
    CASCA,
    /process\.env\.NEXT_PUBLIC_EXPEDICAO_ML_ENABLED === "true"/,
  );
});

test("com a flag desligada a tela nao pede canal nenhum", () => {
  // `construirQuery` omite `channel` no canal historico, entao a requisicao da
  // Shopee sai identica a de antes do multicanal.
  assert.equal(queryDaTendencia(48, CANAL_PADRAO), "window_hours=48");
  // O canal e' lido UMA vez, na casca, pelas flags; a fila so' recebe um
  // canal ja' sanitizado e nao le mais `channel` da URL.
  assert.match(CASCA, /const FLAGS = \{ ml: ML_LIGADO, tiktok: TIKTOK_LIGADO \};/);
  assert.match(CASCA, /lerCanalExpedicao\(window\.location\.search, FLAGS\)/);
  assert.doesNotMatch(CLIENTE, /get\("channel"\)/);
  // e o comportamento, nao so' a fiacao: sem a flag, um link do ML abre a Shopee
  assert.equal(lerCanalExpedicao("?channel=mercadolivre", { ml: false, tiktok: true }), "shopee");
});

// ===========================================================================
// 9. Design system e acessibilidade
// ===========================================================================
test("o seletor de canal continua navegavel e com alvo de toque", () => {
  // O seletor unico mora na casca e e' o `Segmented` compartilhado.
  assert.match(CASCA, /<Segmented\s+rotulo="Canal da Expedição"/);
  assert.match(SEGMENTED, /role="radiogroup"/);
  assert.match(SEGMENTED, /aria-label=\{rotuladoPor \? undefined : rotulo\}/);
  assert.match(SEGMENTED, /role="radio"/);
  assert.match(SEGMENTED, /aria-checked=\{ativo\}/);
  // O alvo de 44px vive no componente compartilhado e vale para toda opcao.
  assert.match(SEGMENTED, /min-h-11 min-w-11/);
});

test("radiogroup cumpre o teclado que o papel ARIA promete", () => {
  // Papel `radiogroup` sem setas anuncia um comportamento que a tela nao tem.
  for (const tecla of ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"]) {
    assert.ok(SEGMENTED.includes(tecla), `sem tratamento de ${tecla}`);
  }
  // tabIndex rovente: o grupo e' UM ponto de tabulacao, nao um por opcao.
  assert.match(SEGMENTED, /tabIndex=\{ativo \? 0 : -1\}/);
});

test("todo seletor da Expedicao usa o MESMO componente", () => {
  // Um grupo com teclado e outro sem seria pior que os dois sem: o operador
  // aprende um comportamento e ele falha no controle ao lado. Antes eram dois
  // componentes (`GrupoRadio` na fila, `Segmented` no TikTok); agora e' um.
  for (const [nome, fonte] of [
    ["fila", CLIENTE],
    ["casca", CASCA],
    ["tiktok", TIKTOK],
    ["cabecalho", CABECALHO],
  ] as const) {
    // Sem comentarios: o cliente EXPLICA por que o `GrupoRadio` saiu.
    const codigo = fonte.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    assert.doesNotMatch(codigo, /role="radiogroup"/, `${nome} reimplementa o radiogroup`);
    assert.doesNotMatch(codigo, /GrupoRadio/, `${nome} ainda usa GrupoRadio`);
  }
  assert.match(CLIENTE, /<Segmented\s+rotulo="Janela da tendência"/);
  assert.match(CABECALHO, /<Segmented\s+rotulo="Profundidade"/);
  assert.equal((TIKTOK.match(/<Segmented\b/g) ?? []).length, 2, "evento e janela");
});

test("disclosure diz QUAL regiao controla", () => {
  const expandidos = (CLIENTE.match(/aria-expanded=/g) ?? []).length;
  const controlados = (CLIENTE.match(/aria-controls=/g) ?? []).length;
  assert.equal(controlados, expandidos, "aria-expanded sem aria-controls");
  assert.match(CLIENTE, /id="exp-tendencia-dados"/);
  // A barra de qualidade virou componente dos tres canais: o par
  // aria-expanded/aria-controls e o alvo com o MESMO id vivem nela.
  assert.match(CLIENTE, /<BarraDeQualidade barra=\{qualidade\} \/>/);
  assert.match(BARRA, /const id = useId\(\);/);
  assert.match(BARRA, /aria-expanded=\{aberta\}/);
  assert.match(BARRA, /aria-controls=\{id\}/);
  assert.match(BARRA, /id=\{id\}/);
});

test("foco visivel em todo controle interativo novo", () => {
  const botoes = (CLIENTE.match(/<button/g) ?? []).length;
  const focos = (CLIENTE.match(/focus-visible:outline/g) ?? []).length;
  assert.ok(focos >= 4, `${focos} focos para ${botoes} botoes`);
});

test("verde, ambar e vermelho ficam reservados para ESTADO", () => {
  // As cores das series do grafico sao por conta, e conta nao e' estado.
  assert.doesNotMatch(GRAFICO, /#16a34a|#22c55e|#dc2626|#ef4444|#f59e0b/);
  assert.doesNotMatch(GRAFICO, /(stroke|fill)-(ok|warn|crit)\b/);
});

test("a tela nao cria um segundo landmark nem um segundo h1", () => {
  assert.doesNotMatch(CLIENTE, /<main/);
  assert.doesNotMatch(CLIENTE, /<h1/);
});

test("a evolucao nao carrega o recharts", () => {
  // EXPEDICAO-UX-2: poucos pontos por cartao, SVG proprio. O import dinamico
  // existia so' para tirar o recharts do bundle inicial; sem recharts, sai.
  assert.doesNotMatch(CLIENTE, /from "recharts"/);
  assert.doesNotMatch(GRAFICO, /from "recharts"/);
  assert.match(CLIENTE, /import EvolucaoPorMarca from "@\/components\/expedicao\/EvolucaoPorMarca";/);
});

test("a fila nao exibe o modo de carga constante da API", () => {
  // Antes: "atualizada à mão, sem agendamento", traducao de um
  // `load_mode` que a API fixa em `manual_snapshot`. Com a DAG publicando,
  // isso era falso. Sem comentarios: eles EXPLICAM a remocao.
  const codigo = CLIENTE.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  assert.doesNotMatch(codigo, /à mão|sem agendamento|manual_snapshot|load_mode|no_automation/);
});

// ===========================================================================
// 10. Achados do QA visual — enum cru e janela sem opcao acesa
// ===========================================================================
test("`deadline_status` tem espaco de valores PROPRIO, nao o de Situacao", () => {
  // A API devolve `unavailable`; `Situacao` usa `deadline_unavailable`. Trocar
  // um pelo outro imprimia o enum cru na coluna de situacao do ML, que e'
  // justamente o estado de todo o backlog daquele canal.
  assert.equal(rotuloDeadline("unavailable"), ROTULO_SEM_PRAZO);
  assert.equal(rotuloDeadline("overdue"), "Vencido");
  assert.equal(rotuloDeadline("due_within_24h"), "Vence em 24h");
  assert.equal(rotuloDeadline("on_time"), "No prazo");
  assert.ok(!(SITUACOES as readonly string[]).includes("unavailable"));
});

test("o dominio de operational_age_status e' exatamente o do contrato", () => {
  // Fonte da verdade: `OperationalAgeStatus` em pipelines/expedicao/contract.py
  // e o Literal["within_48h","over_48h","unknown"] do schema da API. O mapa foi
  // escrito de memoria uma vez, inventou `under_24h` e `between_24h_48h` e
  // esqueceu `within_48h` — metade do dominio, presente nos DOIS canais. O enum
  // cru vazou para a coluna "Idade operacional" em producao.
  assert.deepEqual([...IDADES_OPERACIONAIS], ["within_48h", "over_48h", "unknown"]);
  assert.deepEqual(
    Object.keys(ROTULO_IDADE_OPERACIONAL).sort(),
    [...IDADES_OPERACIONAIS].sort(),
    "o mapa nao pode ter chave a mais nem a menos que o dominio",
  );
});

test("idade operacional nunca aparece como enum cru", () => {
  assert.equal(rotuloIdade("within_48h"), "Dentro de 48h");
  assert.equal(rotuloIdade("over_48h"), "Acima de 48h");
  assert.equal(rotuloIdade("unknown"), "Idade desconhecida");
  for (const v of IDADES_OPERACIONAIS) {
    assert.notEqual(rotuloIdade(v), v, `${v} vazou cru para a tela`);
  }
});

test("nao existe faixa 'entre 24h e 48h' no contrato", () => {
  // A idade operacional tem DUAS faixas medidas mais o desconhecido. Prometer
  // um terceiro recorte seria exibir um numero que o pipeline nao calcula.
  for (const inventado of ["under_24h", "between_24h_48h", "over_24h"]) {
    assert.ok(!(inventado in ROTULO_IDADE_OPERACIONAL), inventado);
    assert.equal(rotuloIdade(inventado), inventado, "fallback fail-visible");
  }
});

test("valor futuro desconhecido volta cru, nunca em branco", () => {
  // Melhor feio que invisivel: um valor novo da fonte precisa aparecer para
  // alguem notar, em vez de sumir da coluna.
  assert.equal(rotuloIdade("algo_novo"), "algo_novo");
  assert.equal(rotuloDeadline("algo_novo"), "algo_novo");
  assert.notEqual(rotuloIdade("algo_novo"), "");
});

test("a tela traduz os dois enums na fila", () => {
  assert.match(CLIENTE, /rotuloDeadline\(l\.deadline_status\)/);
  assert.match(CLIENTE, /rotuloIdade\(l\.operational_age_status\)/);
  assert.doesNotMatch(CLIENTE, /\{l\.operational_age_status\}/);
});

test("a janela inicial e' uma das tres do seletor", () => {
  assert.ok(
    JANELAS_TENDENCIA.some((j) => j.horas === JANELA_INICIAL_HORAS),
    "abrir numa janela fora do seletor deixa nenhum botao aceso",
  );
  // A janela agora e' guardada em dias; o padrao continua sendo 72h.
  assert.equal(REGRA_JANELA.fila.padrao * 24, JANELA_INICIAL_HORAS);
  assert.match(CLIENTE, /useState\(REGRA\.padrao\)/);
});

test("as colunas das tabelas tem respiro horizontal", () => {
  // Sem isto, "Travados" colava em "Fonte avancou" e "Aberto ha" em "Idade
  // operacional" — medido no QA visual do EXP-UX-1.
  //
  // UX-TORRE-1: a regra passou a viver numa CONSTANTE compartilhada pelas
  // quatro tabelas da tela, entao contar ocorrencias do literal reprovaria
  // justamente a versao sem duplicacao. A assercao virou estrutural: cada
  // `<table>` e' RESOLVIDA ate' a string de classe que ela realmente aplica,
  // e essa string precisa conter a regra. E' estritamente mais forte que a
  // contagem — uma quinta tabela com classe propria e sem `pr-4` reprova.
  const constantes = new Map<string, string>();
  // T-0010: as constantes de tabela moram no esqueleto comum (LayoutExpedicao).
  const LAYOUT = readFileSync(join(RAIZ, "src/components/expedicao/LayoutExpedicao.tsx"), "utf8");
  for (const m of (CLIENTE + LAYOUT).matchAll(/const (CLASSE_\w+)\s*=\s*\r?\n?\s*"([^"]*)";/g)) {
    constantes.set(m[1], m[2]);
  }

  const classes = [...CLIENTE.matchAll(/<table\s+className=(?:\{(\w+)\}|"([^"]*)")/g)].map(
    (m) => (m[1] ? (constantes.get(m[1]) ?? `<<${m[1]} nao resolvida>>`) : m[2]),
  );

  const tabelas = (CLIENTE.match(/<table\b/g) ?? []).length;
  assert.equal(classes.length, tabelas, "toda tabela precisa declarar className");
  assert.ok(tabelas >= 4, `esperava as quatro tabelas da tela, achei ${tabelas}`);
  for (const c of classes) {
    assert.ok(/\[&_td\]:(pr-4|px-3)/.test(c), `tabela sem respiro horizontal: ${c}`);
  }
});

// ===========================================================================
// T-0010 — modo de demonstracao (?demo=) e fixtures coerentes
// ===========================================================================
import { cenarioDaUrl, demoAtivo } from "../src/lib/expedicao-demo.ts";
import { CENARIOS_DEMO, lerCenario, respostaFicticia } from "../src/lib/expedicao-fixtures.ts";
import { lerJornada } from "../src/lib/expedicao-jornada.ts";
import { faixasDePrazo as faixas } from "../src/lib/expedicao-contract.ts";

test("demo: so' fora de producao, e so' com ?demo=", () => {
  assert.equal(demoAtivo("?demo=1", "development"), true);
  assert.equal(demoAtivo("?demo=parcial", "development"), true);
  assert.equal(demoAtivo("?demo=1", "production"), false, "producao ignora ?demo=");
  assert.equal(demoAtivo("", "development"), false);
  assert.equal(demoAtivo("?demo=0", "development"), false);
  assert.equal(cenarioDaUrl("?channel=tiktokshop&demo=erro"), "erro");
  assert.equal(lerCenario("1"), "ok");
  assert.equal(lerCenario("<script>"), "ok");
  const demo = readFileSync(join(RAIZ, "src/lib/expedicao-demo.ts"), "utf8");
  // inline, para o Next dobrar a condicao e tirar as fixtures do bundle
  assert.match(demo, /if \(process\.env\.NODE_ENV !== "production" && typeof window !== "undefined"\) \{/);
  assert.match(demo, /await import\("\.\/expedicao-fixtures\.ts"\)/);
  for (const rel of ["src/components/expedicao/TikTokDispatchPanel.tsx", "src/components/expedicao/LdrShopeePanel.tsx", "src/components/expedicao/JornadaPedidos.tsx"]) {
    const src = readFileSync(join(RAIZ, rel), "utf8");
    assert.doesNotMatch(src, /[^.\w]fetch\(/, `${rel} busca sem o ponto unico`);
    assert.match(src, /buscarExpedicao\(/, rel);
  }
  assert.match(readFileSync(join(RAIZ, "app/expedicao/ExpedicaoShell.tsx"), "utf8"), /Dados fictícios — modo de demonstração/);
});

test("fixtures: 3 canais e todos os estados, coerentes com os contratos", () => {
  for (const c of CENARIOS_DEMO) {
    if (c === "carregando") continue;
    for (const url of [
      "/api/v1/expedicao?channel=shopee",
      "/api/v1/expedicao?channel=mercadolivre",
      "/api/v1/expedicao/trend?window_hours=72&channel=shopee",
      "/api/v1/expedicao/tiktok?event=coleta&days=7",
      "/api/v1/expedicao/shopee/ldr?days=7",
    ]) {
      const r = respostaFicticia(url, c);
      assert.ok(r, `${c} ${url}`);
      assert.equal(r!.status, c === "erro" ? 500 : 200, `${c} ${url}`);
    }
    // a particao de prazo da Shopee fecha o backlog em todo cenario
    const fila = respostaFicticia("/api/v1/expedicao?channel=shopee", c)!.corpo as { totals: import("../src/lib/expedicao-contract.ts").Totais | null; accounts: import("../src/lib/expedicao-contract.ts").Totais[] };
    for (const t of [fila.totals, ...(fila.accounts ?? [])]) {
      if (!t) continue;
      assert.equal(faixas(t)!.reduce((s, x) => s + x.valor, 0), t.backlog_count, c);
    }
    // jornada: o parser aceita a fixture de cada canal (exceto 404/500)
    for (const canal of ["shopee", "mercadolivre", "tiktok"] as const) {
      const j = respostaFicticia(`/api/v1/expedicao/jornada?channel=${canal}`, c)!;
      if (c === "indisponivel" || c === "jornada404") { assert.equal(j.status, 404); continue; }
      if (c === "erro" || c === "jornadaErro") { assert.equal(j.status, 500); continue; }
      const p = lerJornada(j.corpo, canal);
      assert.ok(p.ok, `${c}/${canal}: ${p.ok ? "" : p.erro}`);
    }
  }
  assert.equal(respostaFicticia("/api/v1/outra-coisa", "ok"), null, "rota desconhecida cai no fetch real");
});

test("fixtures da jornada cobrem count nulo, dias_sem_publicacao e desatualizada (v1.2)", () => {
  const ml = respostaFicticia("/api/v1/expedicao/jornada?channel=mercadolivre", "ok")!.corpo as { fila_aberta: { sobreposicoes: { count: number | null }[] } };
  assert.ok(ml.fila_aberta.sobreposicoes.some((s) => s.count === null));
  const parcial = respostaFicticia("/api/v1/expedicao/jornada?channel=tiktok", "parcial")!.corpo as { status: string; reason: string };
  assert.equal(parcial.status, "partial");
  assert.match(parcial.reason, /dias_sem_publicacao/);
  const velha = respostaFicticia("/api/v1/expedicao/jornada?channel=tiktok", "desatualizado")!.corpo as { coorte: { desatualizada: boolean; publicado_em: string } };
  assert.equal(velha.coorte.desatualizada, true);
  assert.ok(velha.coorte.publicado_em);
});

// ===========================================================================
// QA T-0010 (I3, I4, M4) — cenarios so' da Jornada e recorte por marca
// ===========================================================================
import { register as registrarLoaderI3 } from "node:module";
import { avisoDeMarcaNaoAplicada } from "../src/lib/expedicao-jornada.ts";

registrarLoaderI3("./helpers/tsx-loader.mjs", import.meta.url);

test("demo: jornada404 / jornadaErro / jornadaVazia mexem SO' na Jornada", () => {
  for (const c of ["jornada404", "jornadaErro", "jornadaVazia"] as const) {
    assert.equal(lerCenario(c), c);
    // a pagina segue no cenario ok
    const fila = respostaFicticia("/api/v1/expedicao?channel=shopee", c)!;
    assert.equal(fila.status, 200);
    assert.ok((fila.corpo as { totals: { backlog_count: number } }).totals.backlog_count > 0, c);
    assert.equal(respostaFicticia("/api/v1/expedicao/tiktok?event=coleta&days=7", c)!.status, 200);
  }
  assert.equal(respostaFicticia("/api/v1/expedicao/jornada?channel=shopee", "jornada404")!.status, 404);
  assert.equal(respostaFicticia("/api/v1/expedicao/jornada?channel=tiktok", "jornadaErro")!.status, 500);
  for (const canal of ["shopee", "mercadolivre"] as const) {
    const j = lerJornada(respostaFicticia(`/api/v1/expedicao/jornada?channel=${canal}`, "jornadaVazia")!.corpo, canal);
    assert.ok(j.ok);
    assert.equal((j as { ok: true; resposta: { fila_aberta: { base: number } } }).resposta.fila_aberta.base, 0);
  }
});

test("demo: a Jornada recorta pela marca como a API (brands_applied e totais)", () => {
  const todas = lerJornada(respostaFicticia("/api/v1/expedicao/jornada?channel=shopee", "ok")!.corpo, "shopee");
  const apice = lerJornada(respostaFicticia("/api/v1/expedicao/jornada?channel=shopee&brands=apice", "ok")!.corpo, "shopee");
  assert.ok(todas.ok && apice.ok);
  const t = (todas as { ok: true; resposta: import("../src/lib/expedicao-jornada.ts").RespostaJornada }).resposta;
  const a = (apice as { ok: true; resposta: import("../src/lib/expedicao-jornada.ts").RespostaJornada }).resposta;
  assert.deepEqual(a.brands_applied, ["apice"]);
  assert.ok(a.fila_aberta!.base < t.fila_aberta!.base, "o total e' da marca, nao do canal");
  assert.equal(avisoDeMarcaNaoAplicada("apice", a), null);
  assert.match(avisoDeMarcaNaoAplicada("apice", t)!, /não foi aplicado pela API: os números da jornada são do canal inteiro/);
  assert.equal(avisoDeMarcaNaoAplicada(null, t), null);
});

test("jornada: marca pedida e nao aplicada aparece ESCRITA na secao", async () => {
  const { createElement: h } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const { default: JornadaPedidos } = await import("../src/components/expedicao/JornadaPedidos.tsx");
  const corpo = respostaFicticia("/api/v1/expedicao/jornada?channel=shopee", "ok")!.corpo;
  const html = renderToStaticMarkup(
    h(JornadaPedidos, {
      carga: { carregando: false, http: 200, erroDeRede: false, resultado: lerJornada(corpo, "shopee") },
      aoTentarDeNovo: () => {},
      marca: "apice",
    }),
  );
  assert.match(html, /exp-jornada-marca-nao-aplicada/);
  assert.match(html, /O filtro de marca \(APICE\) não foi aplicado pela API/);
  for (const rel of ["app/expedicao/ExpedicaoClient.tsx", "src/components/expedicao/TikTokDispatchPanel.tsx"]) {
    assert.match(readFileSync(join(RAIZ, rel), "utf8"), /<JornadaPedidos carga=\{jornada\.carga\} aoTentarDeNovo=\{jornada\.tentarDeNovo\} marca=\{/);
  }
});

test("fila zerada nao esconde a pagina (M4): o corpo aparece sob o aviso de vazio", () => {
  assert.match(CLIENTE, /const mostraCorpo =\s*estado === "ok" \|\| estado === "fila_vazia_por_filtro" \|\| estado === "fotografia_vazia";/);
  assert.match(CLIENTE, /\{mostraCorpo && !recorte\.semConta &&/);
});

// ===========================================================================
// QA T-0010 (reteste) — aviso "parcial" uma vez so'
// ===========================================================================
import { motivoDoTopo } from "../src/lib/expedicao-jornada.ts";

const { createElement: hF } = await import("react");
const { renderToStaticMarkup: htmlF } = await import("react-dom/server");
const { default: JornadaF } = await import("../src/components/expedicao/JornadaPedidos.tsx");

test("parcial por etapas que nao fecham: o aviso aparece UMA vez (no bloco)", () => {
  const corpo = respostaFicticia("/api/v1/expedicao/jornada?channel=shopee", "parcial")!.corpo;
  const r = lerJornada(corpo, "shopee");
  assert.ok(r.ok);
  const resposta = (r as { ok: true; resposta: import("../src/lib/expedicao-jornada.ts").RespostaJornada }).resposta;
  assert.equal(motivoDoTopo(resposta), null, "o topo nao repete o bloco");
  const html = htmlF(
    hF(JornadaF, {
      carga: { carregando: false, http: 200, erroDeRede: false, resultado: r },
      aoTentarDeNovo: () => {},
    }),
  );
  assert.equal((html.match(/não fecham/g) ?? []).length, 1, html);
  // motivo que o bloco NAO diz continua no topo
  assert.equal(
    motivoDoTopo({ ...resposta, reason: "fila_aberta: etapas_nao_fecham; coorte: dias_sem_publicacao (2)" }),
    "Coorte: 2 dia(s) sem publicação na janela",
  );
  // parcial sem motivo e sem aviso de bloco: frase generica
  assert.match(
    motivoDoTopo({
      ...resposta,
      reason: null,
      fila_aberta: { ...resposta.fila_aberta!, etapas_fecham: true },
    })!,
    /parte da jornada/,
  );
});

// ===========================================================================
// R21 — cenario de demo: fila valida + coorte nula (table_not_migrated)
// ===========================================================================
test("demo jornadaCoorteAusente: a pagina fica ok e a fila Shopee continua com a coorte indisponivel", () => {
  assert.equal(lerCenario("jornadaCoorteAusente"), "jornadaCoorteAusente");
  assert.equal(respostaFicticia("/api/v1/expedicao?channel=shopee", "jornadaCoorteAusente")!.status, 200);
  const corpo = respostaFicticia("/api/v1/expedicao/jornada?channel=shopee", "jornadaCoorteAusente")!.corpo as {
    status: string;
    reason: string;
    coorte: unknown;
  };
  assert.equal(corpo.status, "unavailable");
  assert.equal(corpo.reason, "coorte: table_not_migrated");
  assert.equal(corpo.coorte, null);
  const r = lerJornada(corpo, "shopee");
  assert.ok(r.ok);
  const html = htmlF(
    hF(JornadaF, { carga: { carregando: false, http: 200, erroDeRede: false, resultado: r }, aoTentarDeNovo: () => {} }),
  );
  assert.match(html, /1\.820 pedidos pagos aguardando expedição\./, "a fila nao some");
  assert.match(html, /Indisponível agora: a tabela ainda não foi publicada nesta base\./);
  assert.doesNotMatch(html, /Jornada indisponível/);
});
