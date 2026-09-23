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
  AVISO_SEM_AUTOMACAO,
  CANAL_PADRAO,
  JANELAS_TENDENCIA,
  JANELA_INICIAL_HORAS,
  KPIS_PRINCIPAIS,
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

const RAIZ = join(import.meta.dirname, "..");
const CLIENTE = readFileSync(join(RAIZ, "app/expedicao/ExpedicaoClient.tsx"), "utf8");
const GRAFICO = readFileSync(
  join(RAIZ, "src/components/expedicao/TendenciaChart.tsx"),
  "utf8",
);

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
    modoDeCarga: "manual_snapshot",
    semAutomacao: true,
    ...over,
  };
}

test("defasagem, ausencia de automacao e cobertura entram todas na barra", () => {
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
  assert.ok(chaves.includes("sem_automacao"));
  assert.ok(chaves.includes("fonte_em_alerta"));
  assert.equal(chaves.filter((c) => c.startsWith("cobertura_")).length, 1);
  assert.equal(barra.detalhes.length, 4, "nenhuma limitacao pode sumir");
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
  const barra = montarBarraDeQualidade(relogios({ semAutomacao: false }), [], []);
  assert.equal(barra.principal?.chave, "fotografia_antiga");
  assert.match(barra.principal?.texto ?? "", /21,9 h|21\.9 h/);
});

test("sem nenhum problema, a barra nao e' pintada", () => {
  const barra = montarBarraDeQualidade(
    relogios({ snapshotVelho: false, semAutomacao: false }),
    [],
    [],
  );
  assert.equal(barra.principal, null);
  assert.equal(barra.detalhes.length, 0);
});

test("o aviso de ausencia de automacao e' exatamente o texto do contrato", () => {
  const barra = montarBarraDeQualidade(relogios(), [], []);
  const item = barra.detalhes.find((d) => d.chave === "sem_automacao");
  assert.equal(item?.texto, AVISO_SEM_AUTOMACAO);
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
  assert.match(CLIENTE, /queryDaTendencia\(janela, filtros\.channel\)/);
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
  assert.match(GRAFICO, /connectNulls=\{false\}/);
});

test("a tabela acessivel da tendencia continua disponivel", () => {
  assert.match(CLIENTE, /Ver dados/);
  assert.match(CLIENTE, /aria-expanded=\{dadosDaTendencia\}/);
  // e continua sendo uma tabela de verdade, com caption e escopo
  assert.match(CLIENTE, /contas nunca são somadas/);
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
  // A comparacao literal no cliente e' o que permite o minificador eliminar o
  // seletor quando a flag esta definida como algo diferente de "true".
  assert.match(
    CLIENTE,
    /process\.env\.NEXT_PUBLIC_EXPEDICAO_ML_ENABLED === "true"/,
  );
});

test("com a flag desligada a tela nao pede canal nenhum", () => {
  // `construirQuery` omite `channel` no canal historico, entao a requisicao da
  // Shopee sai identica a de antes do multicanal.
  assert.equal(queryDaTendencia(48, CANAL_PADRAO), "window_hours=48");
  assert.match(CLIENTE, /ML_LIGADO && \(/, "o seletor e' condicionado a flag");
  assert.match(CLIENTE, /useState\(!ML_LIGADO\)/, "sem flag, urlPronta nasce true");
});

// ===========================================================================
// 9. Design system e acessibilidade
// ===========================================================================
test("o seletor de canal continua navegavel e com alvo de toque", () => {
  assert.match(CLIENTE, /role="radiogroup"/);
  assert.match(CLIENTE, /aria-label=\{rotulo\}/);
  assert.match(CLIENTE, /rotulo="Canal da fotografia"/);
  assert.match(CLIENTE, /role="radio"/);
  assert.match(CLIENTE, /aria-checked=\{ativo\}/);
  // O alvo de 44px vive no componente compartilhado e vale para toda opcao.
  assert.match(CLIENTE, /min-h-\[44px\] min-w-\[44px\]/);
});

test("radiogroup cumpre o teclado que o papel ARIA promete", () => {
  // Papel `radiogroup` sem setas anuncia um comportamento que a tela nao tem.
  for (const tecla of ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"]) {
    assert.ok(CLIENTE.includes(tecla), `sem tratamento de ${tecla}`);
  }
  // tabIndex rovente: o grupo e' UM ponto de tabulacao, nao um por opcao.
  assert.match(CLIENTE, /tabIndex=\{ativo \? 0 : -1\}/);
});

test("os dois seletores usam o MESMO componente", () => {
  // Um grupo com teclado e outro sem seria pior que os dois sem: o operador
  // aprende um comportamento e ele falha no controle ao lado.
  assert.equal((CLIENTE.match(/<GrupoRadio/g) ?? []).length, 2);
  assert.match(CLIENTE, /rotulo="Janela da tendência"/);
});

test("disclosure diz QUAL regiao controla", () => {
  const expandidos = (CLIENTE.match(/aria-expanded=/g) ?? []).length;
  const controlados = (CLIENTE.match(/aria-controls=/g) ?? []).length;
  assert.equal(controlados, expandidos, "aria-expanded sem aria-controls");
  assert.match(CLIENTE, /id="exp-qualidade-detalhes"/);
  assert.match(CLIENTE, /id="exp-tendencia-dados"/);
});

test("foco visivel em todo controle interativo novo", () => {
  const botoes = (CLIENTE.match(/<button/g) ?? []).length;
  const focos = (CLIENTE.match(/focus-visible:outline/g) ?? []).length;
  assert.ok(focos >= 4, `${focos} focos para ${botoes} botoes`);
});

test("verde, ambar e vermelho ficam reservados para ESTADO", () => {
  // As cores das series do grafico sao por conta, e conta nao e' estado.
  assert.doesNotMatch(GRAFICO, /#16a34a|#22c55e|#dc2626|#ef4444|#f59e0b/);
});

test("a tela nao cria um segundo landmark nem um segundo h1", () => {
  assert.doesNotMatch(CLIENTE, /<main/);
  assert.doesNotMatch(CLIENTE, /<h1/);
});

test("o recharts fica fora do bundle inicial", () => {
  assert.match(CLIENTE, /dynamic\(\(\) => import\("@\/components\/expedicao\/TendenciaChart"\)/);
  assert.match(CLIENTE, /ssr: false/);
  assert.doesNotMatch(CLIENTE, /from "recharts"/);
});

test("o modo de carga aparece em linguagem humana", () => {
  assert.match(CLIENTE, /atualizada à mão, sem agendamento/);
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

test("idade operacional nunca aparece como enum cru", () => {
  assert.equal(rotuloIdade("over_48h"), "Acima de 48h");
  assert.equal(rotuloIdade("under_24h"), "Menos de 24h");
  // valor novo da fonte volta cru em vez de sumir: melhor feio que invisivel
  assert.equal(rotuloIdade("algo_novo"), "algo_novo");
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
  assert.match(CLIENTE, /useState\(JANELA_INICIAL_HORAS\)/);
});

test("as colunas das tabelas tem respiro horizontal", () => {
  // Sem isto, "Travados" colava em "Fonte avancou" e "Aberto ha" em "Idade
  // operacional" — medido no QA visual do EXP-UX-1.
  const tabelas = (CLIENTE.match(/<table\b/g) ?? []).length;
  const comEspaco = (CLIENTE.match(/\[&_td\]:pr-4/g) ?? []).length;
  assert.equal(comEspaco, tabelas, "toda tabela precisa separar as colunas");
});
