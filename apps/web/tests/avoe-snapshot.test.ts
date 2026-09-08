// Gate AVH-4B-S Task 2/2 — testes da pagina de referencias externas da Avoe.
//
// Cobrem: fases da tela, null x zero, filtros client-side, avisos obrigatorios
// sem colapso, proveniencia, ausencia de total/atingimento/margem, rotulo do
// valor de canal, captura antiga, navegacao isolada e a garantia de que nenhum
// campo Avoe encosta em contrato oficial.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  COPY,
  DIAS_PARA_CAPTURA_ANTIGA,
  FILTROS_VAZIOS,
  NAO_INFORMADO,
  NAV_LABEL,
  PAGE_TITLE,
  ROUTE,
  SELO_FONTE_EXTERNA,
  TODOS,
  buildProvenance,
  buildWarnings,
  canalOptions,
  channelLabel,
  competenciaOptions,
  coverageLabel,
  coverageNotes,
  currencyStatusLabel,
  describeFilters,
  filterChannels,
  filterTargets,
  formatBrlExato,
  formatCompetencia,
  formatContagem,
  formatData,
  formatIdade,
  formatInstante,
  formatValorInformado,
  isCapturaAntiga,
  marcaOptions,
  resolvePhase,
  unavailableLabel,
  type AvoeExtraChannelRow,
  type AvoeSnapshotResponse,
  type AvoeTargetRow,
} from "../src/lib/avoe-snapshot-contract.ts";
import { NAV_SECTIONS, getRouteTitle, isNavItemActive } from "../src/components/shell/nav-config.ts";

const PAGE_PATH = "app/referencias-externas/avoe/page.tsx";
const CONTRACT_PATH = "src/lib/avoe-snapshot-contract.ts";
const CLIENT_PATH = "src/lib/api-client.ts";

function fonte(caminho: string): string {
  return readFileSync(new URL(`../${caminho}`, import.meta.url), "utf8");
}

// ---------------------------------------------------------------------------
// Fixtures — espelham o snapshot 285 real (7 metas, 24 canais)
// ---------------------------------------------------------------------------

const CAPTURA = "2026-09-01T15:32:34.320000+00:00";

function meta(over: Partial<AvoeSnapshotResponse["meta"]> = {}): AvoeSnapshotResponse["meta"] {
  return {
    status: "available",
    source: "avoe_hub",
    source_kind: "external_manual_snapshot",
    is_official_torre_source: false,
    captured_at: CAPTURA,
    snapshot_id: "c8f6be83e576000000000000000000",
    sync_run_id: 285,
    sync_run_status: "success",
    sync_run_link_method: "audit_time_window",
    targets_count: 7,
    channel_rows_count: 24,
    target_ref_months: ["2026-08-01"],
    channel_ref_months: ["2026-06-01", "2026-07-01", "2026-08-01"],
    currency: "BRL",
    currency_status: "assumed_unconfirmed",
    refreshed_at: "2026-09-08T18:00:00+00:00",
    captured_age_days: 7,
    unavailable_reason: null,
    warnings: ["Fonte externa e manual: aviso vindo da API."],
    ...over,
  };
}

function alvo(brand: string, valor: number, ref = "2026-08-01"): AvoeTargetRow {
  return {
    brand,
    brand_key: brand.toLowerCase(),
    ref_month: ref,
    target_amount: valor,
    currency_code: "BRL",
    currency_status: "assumed_unconfirmed",
    currency_warning: "moeda assumida",
    source_recorded_at: "2026-08-31T12:00:00+00:00",
  };
}

function canal(
  brand: string,
  channel: string,
  valor: number | null,
  ref = "2026-08-01",
): AvoeExtraChannelRow {
  return {
    brand,
    brand_key: brand.toLowerCase(),
    channel,
    channel_source_label: channel.toUpperCase(),
    ref_month: ref,
    reported_amount: valor,
    is_proxy: true,
    definition_status: "unconfirmed",
    definition_warning: "definicao nao confirmada",
    currency_code: "BRL",
    currency_status: "assumed_unconfirmed",
    days_covered: 20,
    first_business_date: ref,
    last_business_date: "2026-08-28",
    coverage_status: "partial_month",
    source_recorded_at: null,
  };
}

function limites(): AvoeSnapshotResponse["limitations"] {
  return {
    manual_snapshot: true,
    automated_refresh: false,
    channel_amount_definition_confirmed: false,
    currency_confirmed: false,
    provides_realized_amount: false,
    provides_attainment_or_margin: false,
    replaces_canonical_torre_kpi: false,
    notes: ["Sem automacao: nota vinda da API."],
  };
}

function resposta(over: Partial<AvoeSnapshotResponse> = {}): AvoeSnapshotResponse {
  return {
    meta: meta(),
    targets: [
      alvo("Apice", 5000000),
      alvo("Barbours", 10000000),
      alvo("Denavita", 5000000),
      alvo("GoCase", 5000000),
      alvo("Kokeshi", 9557070.49),
      alvo("Lescent", 5000000),
      alvo("Yenzah", 5000000),
    ],
    extra_channels: [
      canal("Kokeshi", "shein", 1234.56),
      canal("Barbours", "magalu", 0),
      canal("Apice", "kwai", null),
      canal("Lescent", "amazon", 999.99, "2026-07-01"),
      canal("Yenzah", "beleza_na_web", 500.5, "2026-06-01"),
      canal("Denavita", "rd_marketplace", 42, "2026-06-01"),
    ],
    limitations: limites(),
    ...over,
  };
}

// ---------------------------------------------------------------------------
// 1. Rota e navegacao
// ---------------------------------------------------------------------------

test("a rota e o rotulo de navegacao sao os do contrato", () => {
  assert.equal(ROUTE, "/referencias-externas/avoe");
  assert.equal(NAV_LABEL, "Referências externas");
  assert.equal(PAGE_TITLE, "Referências externas — Avoe Hub");
});

test("a navegacao tem grupo proprio, isolado dos cockpits", () => {
  const grupo = NAV_SECTIONS.find((s) => s.label === NAV_LABEL);
  assert.ok(grupo, "grupo Referências externas ausente");
  assert.deepEqual(
    grupo!.pages.map((p) => p.href),
    [ROUTE],
  );
  // E a rota NAO esta em nenhum outro grupo.
  const outros = NAV_SECTIONS.filter((s) => s.label !== NAV_LABEL);
  for (const s of outros) {
    assert.ok(
      !s.pages.some((p) => p.href.startsWith("/referencias-externas")),
      `${s.label} nao pode conter a rota da Avoe`,
    );
  }
});

test("a rota fica ativa em si mesma e nao ativa Canais nem Gerencial", () => {
  assert.equal(isNavItemActive(ROUTE, ROUTE), true);
  assert.equal(isNavItemActive("/canais", ROUTE), false);
  assert.equal(isNavItemActive("/", ROUTE), false);
  assert.equal(getRouteTitle(ROUTE), "Avoe Hub");
});

// ---------------------------------------------------------------------------
// 2. Fases
// ---------------------------------------------------------------------------

test("loading enquanto nao ha dado", () => {
  assert.equal(resolvePhase({ loading: true, error: false, data: null }), "loading");
  assert.equal(resolvePhase({ loading: false, error: false, data: null }), "loading");
});

test("error vence tudo", () => {
  assert.equal(resolvePhase({ loading: true, error: true, data: null }), "error");
  assert.equal(
    resolvePhase({ loading: false, error: true, data: resposta() }),
    "error",
  );
});

test("available e unavailable vem do contrato, nao de lista vazia", () => {
  assert.equal(
    resolvePhase({ loading: false, error: false, data: resposta() }),
    "available",
  );
  const vazio = resposta({
    meta: meta({ status: "unavailable", unavailable_reason: "no_snapshot_published" }),
    targets: [],
    extra_channels: [],
  });
  assert.equal(resolvePhase({ loading: false, error: false, data: vazio }), "unavailable");
  // 200 com available e arrays vazios continua available: e' dado real vazio.
  const semLinhas = resposta({ targets: [], extra_channels: [] });
  assert.equal(
    resolvePhase({ loading: false, error: false, data: semLinhas }),
    "available",
  );
});

test("todos os motivos de indisponibilidade tem texto proprio", () => {
  const motivos = [
    "no_snapshot_published",
    "targets_and_channels_capture_mismatch",
    "capture_incomplete",
    "capture_mixes_multiple_imports",
    "duplicate_grain_in_capture",
    "audit_run_not_conclusive",
    "serving_inconsistent",
  ] as const;
  const vistos = new Set<string>();
  for (const m of motivos) {
    const texto = unavailableLabel(m);
    assert.ok(texto.length > 20, `${m} sem texto`);
    assert.ok(!vistos.has(texto), `${m} repete texto de outro motivo`);
    vistos.add(texto);
  }
  assert.ok(unavailableLabel(null).length > 5);
  // O motivo de run nao conclusivo precisa citar os tres estados.
  const run = unavailableLabel("audit_run_not_conclusive");
  for (const palavra of ["andamento", "falhada", "indeterminada"]) {
    assert.ok(run.includes(palavra), `faltou "${palavra}"`);
  }
});

// ---------------------------------------------------------------------------
// 3. null x zero — a regra central
// ---------------------------------------------------------------------------

test("null vira Nao informado e zero vira moeda zero", () => {
  assert.equal(formatValorInformado(null), NAO_INFORMADO);
  assert.equal(formatValorInformado(null), "Não informado");
  const zero = formatValorInformado(0);
  assert.ok(zero.includes("0,00"), zero);
  assert.notEqual(zero, NAO_INFORMADO);
});

test("ausencia nunca e convertida em zero em nenhum caminho", () => {
  // undefined tambem cai em ausencia, nao em zero.
  assert.equal(formatValorInformado(undefined as unknown as null), NAO_INFORMADO);
  // E zero nunca cai em ausencia.
  assert.notEqual(formatValorInformado(0), NAO_INFORMADO);
  assert.notEqual(formatValorInformado(-0), NAO_INFORMADO);
});

test("valor monetario sai integral, com centavo, sem abreviar em K ou M", () => {
  const grande = formatBrlExato(9557070.49);
  assert.ok(grande.includes("9.557.070,49"), grande);
  assert.ok(!grande.includes("M"), "nao pode abreviar");
  assert.ok(!grande.includes("K"), "nao pode abreviar");
  assert.ok(formatBrlExato(1234.56).includes("1.234,56"));
});

test("a pagina distingue visualmente ausencia de valor", () => {
  const src = fonte(PAGE_PATH);
  assert.ok(src.includes("valor-nao-informado"), "falta marcador de ausencia");
  assert.ok(src.includes("c.reported_amount === null"));
  // E nao existe fallback para zero em lugar nenhum.
  assert.ok(!/reported_amount\s*\?\?\s*0/.test(src), "fallback para zero proibido");
  assert.ok(!/reported_amount\s*\|\|\s*0/.test(src), "fallback para zero proibido");
});

// ---------------------------------------------------------------------------
// 4. Filtros client-side
// ---------------------------------------------------------------------------

test("sem filtro, tudo passa", () => {
  const r = resposta();
  assert.equal(filterTargets(r.targets, FILTROS_VAZIOS).length, 7);
  assert.equal(filterChannels(r.extra_channels, FILTROS_VAZIOS).length, 6);
});

test("filtro de competencia atinge as duas tabelas", () => {
  const r = resposta();
  const f = { ...FILTROS_VAZIOS, competencia: "2026-06-01" };
  assert.equal(filterTargets(r.targets, f).length, 0);
  assert.equal(filterChannels(r.extra_channels, f).length, 2);
});

test("filtro de marca atinge as duas tabelas", () => {
  const r = resposta();
  const f = { ...FILTROS_VAZIOS, marca: "Kokeshi" };
  assert.equal(filterTargets(r.targets, f).length, 1);
  assert.equal(filterChannels(r.extra_channels, f).length, 1);
});

test("filtro de canal SO afeta a tabela de canais", () => {
  const r = resposta();
  const f = { ...FILTROS_VAZIOS, canal: "shein" };
  // Metas nao tem canal: o filtro nao pode reduzir a lista de metas.
  assert.equal(filterTargets(r.targets, f).length, 7);
  assert.equal(filterChannels(r.extra_channels, f).length, 1);
});

test("filtros combinam por interseccao", () => {
  const r = resposta();
  const f = { competencia: "2026-06-01", marca: "Yenzah", canal: "beleza_na_web" };
  assert.equal(filterChannels(r.extra_channels, f).length, 1);
  const semCasar = { ...f, canal: "shein" };
  assert.equal(filterChannels(r.extra_channels, semCasar).length, 0);
});

test("filtrar nao agrega, nao soma e nao reordena por valor", () => {
  const r = resposta();
  const antes = r.extra_channels.map((c) => `${c.brand}|${c.channel}`);
  const depois = filterChannels(r.extra_channels, FILTROS_VAZIOS).map(
    (c) => `${c.brand}|${c.channel}`,
  );
  assert.deepEqual(depois, antes, "a ordem recebida e' preservada");
  // O resultado e' subconjunto das linhas originais, com os mesmos objetos.
  const f = { ...FILTROS_VAZIOS, marca: "Apice" };
  const sub = filterChannels(r.extra_channels, f);
  assert.equal(sub.length, 1);
  assert.equal(sub[0], r.extra_channels[2], "mesma referencia, nada reconstruido");
});

test("opcoes de filtro sao unicas, ordenadas e comecam em Todas", () => {
  const r = resposta();
  const comp = competenciaOptions([
    ...r.targets.map((t) => t.ref_month),
    ...r.extra_channels.map((c) => c.ref_month),
  ]);
  assert.equal(comp[0].value, TODOS);
  assert.deepEqual(
    comp.slice(1).map((o) => o.value),
    ["2026-08-01", "2026-07-01", "2026-06-01"],
  );
  assert.equal(comp[1].label, "ago/2026");

  const marcas = marcaOptions(r.targets.map((t) => t.brand));
  assert.equal(marcas[0].value, TODOS);
  assert.deepEqual(marcas.slice(1).map((o) => o.value), [
    "Apice", "Barbours", "Denavita", "GoCase", "Kokeshi", "Lescent", "Yenzah",
  ]);

  const canais = canalOptions(r.extra_channels.map((c) => c.channel));
  assert.equal(canais[0].value, TODOS);
  assert.ok(canais.some((o) => o.label === "Beleza na Web"));
  assert.ok(canais.some((o) => o.label === "SHEIN"));
});

test("o filtro nao dispara nova chamada a API", () => {
  const src = fonte(PAGE_PATH);
  // A busca acontece uma vez, no efeito de montagem.
  assert.equal((src.match(/fetchAvoeSnapshot\(/g) ?? []).length, 1);
  const efeito = src.slice(src.indexOf("useEffect("), src.indexOf("const fase"));
  assert.ok(efeito.includes("carregar(ctrl.signal)"));
  assert.ok(!efeito.includes("filtros"), "o efeito nao pode depender dos filtros");
});

test("descricao do filtro ativo", () => {
  assert.equal(describeFilters(FILTROS_VAZIOS, true), "sem filtro");
  assert.equal(
    describeFilters({ competencia: "2026-08-01", marca: "Kokeshi", canal: "shein" }, true),
    "competência ago/2026, marca Kokeshi, canal SHEIN",
  );
  // Sem canal, a descricao da tabela de metas nao menciona canal.
  assert.equal(
    describeFilters({ competencia: TODOS, marca: TODOS, canal: "shein" }, false),
    "sem filtro",
  );
});

// ---------------------------------------------------------------------------
// 5. Avisos e proveniencia
// ---------------------------------------------------------------------------

test("os cinco avisos obrigatorios aparecem no estado available", () => {
  const avisos = buildWarnings(resposta()).join(" | ");
  assert.ok(avisos.includes("NÃO são KPIs oficiais"));
  assert.ok(avisos.includes("Sem automação"));
  assert.ok(avisos.includes("definição NÃO confirmada"));
  assert.ok(avisos.includes("Moeda ASSUMIDA"));
  assert.ok(avisos.includes("não traz realizado"));
});

test("os avisos da API sao preservados, nao descartados", () => {
  const r = resposta();
  const avisos = buildWarnings(r);
  assert.ok(
    avisos.some((a) => a.includes("aviso vindo da API")),
    "aviso do backend precisa aparecer",
  );
  assert.ok(
    avisos.some((a) => a.includes("nota vinda da API")),
    "nota de limitacao do backend precisa aparecer",
  );
});

test("aviso duplicado entre fixo e API nao aparece duas vezes", () => {
  const r = resposta({
    meta: meta({
      warnings: [
        // Mesma frase do texto fixo, com acento e caixa diferentes.
        "SEM AUTOMACAO: nenhum agendamento atualiza esta fonte. A proxima captura depende de alguem exportar o snapshot e rodar o importador manualmente.",
      ],
    }),
    // A nota da API sai deste cenario: aqui o alvo e' o par fixo x warning.
    limitations: { ...limites(), notes: [] },
  });
  const avisos = buildWarnings(r);
  const iguais = avisos.filter((a) =>
    /nenhum agendamento atualiza esta fonte/i.test(
      a.normalize("NFD").replace(/[̀-ͯ]/g, ""),
    ),
  );
  assert.equal(iguais.length, 1, avisos.join(" | "));
  // E o texto que sobrevive e' o da tela, nao o da API.
  assert.equal(iguais[0], COPY.semAutomacao);
});

test("no estado indisponivel os avisos essenciais continuam", () => {
  const r = resposta({
    meta: meta({ status: "unavailable", unavailable_reason: "no_snapshot_published" }),
    targets: [],
    extra_channels: [],
  });
  const avisos = buildWarnings(r).join(" | ");
  assert.ok(avisos.includes("NÃO são KPIs oficiais"));
  assert.ok(avisos.includes("Sem automação"));
});

test("moeda confirmada remove o aviso de moeda assumida", () => {
  const r = resposta({ meta: meta({ currency_status: "confirmed" }) });
  const avisos = buildWarnings(r).join(" | ");
  assert.ok(!avisos.includes("Moeda ASSUMIDA"));
});

test("captura antiga acrescenta aviso proprio", () => {
  assert.equal(isCapturaAntiga(null), false);
  assert.equal(isCapturaAntiga(DIAS_PARA_CAPTURA_ANTIGA - 1), false);
  assert.equal(isCapturaAntiga(DIAS_PARA_CAPTURA_ANTIGA), true);
  const r = resposta({ meta: meta({ captured_age_days: 90 }) });
  assert.ok(buildWarnings(r).some((a) => a.includes("Captura antiga")));
  assert.ok(!buildWarnings(resposta()).some((a) => a.includes("Captura antiga")));
});

test("os avisos nao ficam em acordeao nem em tooltip", () => {
  const src = fonte(PAGE_PATH);
  const bloco = src.slice(src.indexOf("avoe-avisos"), src.indexOf("avoe-indisponivel"));
  assert.ok(!bloco.includes("<details"), "aviso nao pode ficar em details");
  assert.ok(!bloco.includes("summary"), "aviso nao pode ficar em summary");
  assert.ok(bloco.includes("lista-avisos"));
});

test("proveniencia traz os nove itens exigidos, com o vinculo temporal", () => {
  const itens = buildProvenance(meta());
  const rotulos = itens.map((i) => i.label);
  for (const esperado of [
    "Fonte",
    "Captura em",
    "Execução da importação",
    "Vínculo com a auditoria",
    "Identificador do snapshot",
    "Metas na captura",
    "Linhas de canal na captura",
    "Moeda",
    "Consulta feita em",
  ]) {
    assert.ok(rotulos.includes(esperado), `falta ${esperado}`);
  }
  const fonteItem = itens.find((i) => i.label === "Fonte")!;
  assert.equal(fonteItem.value, "Avoe Hub");
  assert.equal(fonteItem.note, SELO_FONTE_EXTERNA);

  const run = itens.find((i) => i.label === "Execução da importação")!;
  assert.equal(run.value, "#285");
  assert.ok(run.note!.includes("success"));

  const vinculo = itens.find((i) => i.label === "Vínculo com a auditoria")!;
  assert.equal(vinculo.value, "janela de tempo");
  assert.ok(vinculo.note!.includes("não por chave"));

  const moeda = itens.find((i) => i.label === "Moeda")!;
  assert.equal(moeda.value, "BRL");
  assert.ok(moeda.note!.includes("assumida"));
});

test("proveniencia sobrevive a campos nulos sem inventar valor", () => {
  const itens = buildProvenance(
    meta({
      captured_at: null,
      snapshot_id: null,
      sync_run_id: null,
      sync_run_status: null,
      sync_run_link_method: null,
      currency: null,
      currency_status: null,
      captured_age_days: null,
    }),
  );
  const porRotulo = new Map(itens.map((i) => [i.label, i]));
  assert.equal(porRotulo.get("Captura em")!.value, NAO_INFORMADO);
  assert.equal(porRotulo.get("Execução da importação")!.value, NAO_INFORMADO);
  assert.equal(porRotulo.get("Vínculo com a auditoria")!.value, NAO_INFORMADO);
  assert.equal(porRotulo.get("Identificador do snapshot")!.value, NAO_INFORMADO);
  assert.equal(porRotulo.get("Moeda")!.value, NAO_INFORMADO);
  // Nenhum zero inventado nas contagens: elas vem do contrato como numero.
  assert.equal(porRotulo.get("Metas na captura")!.value, "7");
});

test("notas de cobertura citam Denavita, GoCase e Apice", () => {
  const notas = coverageNotes().join(" | ");
  assert.ok(notas.includes("Denavita"));
  assert.ok(notas.includes("GoCase"));
  assert.ok(notas.includes("Ápice"));
  assert.ok(notas.includes("não calculável"));
  assert.ok(notas.includes("piso"));
});

// ---------------------------------------------------------------------------
// 6. Nenhum total, atingimento, margem ou comparacao
// ---------------------------------------------------------------------------

test("o contrato do frontend nao exporta funcao de agregacao", () => {
  const src = fonte(CONTRACT_PATH);
  for (const proibido of [
    "function total", "function soma", "function sum",
    "function atingimento", "function attainment",
    "function margem", "function margin",
    "function variacao", "function delta",
    ".reduce(", "calcMoM",
  ]) {
    assert.ok(!src.includes(proibido), `contrato nao pode ter ${proibido}`);
  }
});

test("a pagina nao soma, nao acumula e nao compara", () => {
  const src = fonte(PAGE_PATH);
  for (const proibido of [".reduce(", "calcMoM", "fmtPct", "totalGmv"]) {
    assert.ok(!src.includes(proibido), `pagina nao pode usar ${proibido}`);
  }
  // Nenhuma palavra de conclusao comercial na tela.
  for (const palavra of ["Atingimento", "atingimento de", "Margem", "Variação", "Total geral", "Subtotal"]) {
    assert.ok(!src.includes(palavra), `pagina nao pode exibir "${palavra}"`);
  }
  // E o texto que explica a ausencia de total esta presente.
  assert.ok(src.includes("Sem total consolidado"));
});

test("o valor de canal e chamado de valor informado pela Avoe", () => {
  assert.equal(COPY.rotuloValorCanal, "Valor informado pela Avoe");
  const src = fonte(PAGE_PATH);
  assert.ok(src.includes("COPY.rotuloValorCanal"));
  // E nunca de GMV, receita, venda, resultado ou realizado.
  const cabecalhos = src.slice(src.indexOf("avoe-canais"));
  for (const proibido of ["GMV", "Receita", "Vendas", "Resultado", "Realizado", "Faturamento"]) {
    assert.ok(
      !new RegExp(`>\\s*${proibido}`).test(cabecalhos),
      `coluna nao pode se chamar ${proibido}`,
    );
  }
});

// ---------------------------------------------------------------------------
// 7. Formatadores
// ---------------------------------------------------------------------------

test("competencia e data nao sofrem fuso", () => {
  assert.equal(formatCompetencia("2026-08-01"), "ago/2026");
  assert.equal(formatCompetencia("2026-01-01"), "jan/2026");
  assert.equal(formatCompetencia("2026-12-01"), "dez/2026");
  assert.equal(formatCompetencia("nao-iso"), "nao-iso");
  assert.equal(formatData("2026-06-10"), "10/06/2026");
  assert.equal(formatData(null), NAO_INFORMADO);
});

test("instante sai em horario de Sao Paulo", () => {
  // 2026-09-01T15:32Z = 12:32 em America/Sao_Paulo (UTC-3).
  const texto = formatInstante(CAPTURA);
  assert.ok(texto.includes("01/09/2026"), texto);
  assert.ok(texto.includes("12:32"), texto);
  assert.equal(formatInstante(null), NAO_INFORMADO);
});

test("idade, contagem e rotulos", () => {
  assert.equal(formatIdade(null), NAO_INFORMADO);
  assert.equal(formatIdade(0), "hoje");
  assert.equal(formatIdade(1), "1 dia");
  assert.equal(formatIdade(7), "7 dias");
  assert.equal(formatContagem(1234), "1.234");
  assert.equal(channelLabel("beleza_na_web"), "Beleza na Web");
  assert.equal(channelLabel("rd_marketplace"), "RD Marketplace");
  // Canal novo da origem volta cru, em vez de virar "desconhecido".
  assert.equal(channelLabel("canal_novo"), "canal_novo");
  assert.equal(coverageLabel("partial_month"), "Mês parcial");
  assert.equal(coverageLabel("full_month"), "Mês completo");
  assert.ok(currencyStatusLabel("assumed_unconfirmed").includes("assumida"));
  assert.equal(currencyStatusLabel(null), NAO_INFORMADO);
});

// ---------------------------------------------------------------------------
// 8. Acessibilidade e estados na pagina
// ---------------------------------------------------------------------------

test("os quatro blocos existem, nesta ordem", () => {
  const src = fonte(PAGE_PATH);
  const ordem = ["avoe-proveniencia", "avoe-avisos", "avoe-metas", "avoe-canais"];
  let anterior = -1;
  for (const id of ordem) {
    const pos = src.indexOf(id);
    assert.ok(pos > anterior, `${id} fora de ordem`);
    anterior = pos;
  }
});

test("todo controle interativo tem alvo de 44px", () => {
  const src = fonte(PAGE_PATH);
  const controles = src.match(/<(button|select)\b/g) ?? [];
  assert.ok(controles.length >= 4, "esperados pelo menos 4 controles");
  assert.equal((src.match(/min-h-\[44px\]/g) ?? []).length >= 3, true);
  // Todo `select` usa a constante que carrega o min-h.
  assert.ok(src.includes("const CONTROLE"));
  assert.ok(src.includes("min-h-[44px] rounded-lg border border-slate-300"));
  assert.equal((src.match(/className=\{CONTROLE\}/g) ?? []).length, 3);
});

test("todo select tem label associado por htmlFor", () => {
  const src = fonte(PAGE_PATH);
  const ids = src.match(/htmlFor=\{id[A-Za-z]+\}/g) ?? [];
  assert.equal(ids.length, 3, "tres filtros, tres labels");
  for (const nome of ["idCompetencia", "idMarca", "idCanal"]) {
    assert.ok(src.includes(`id={${nome}}`), `select sem id ${nome}`);
    assert.ok(src.includes(`htmlFor={${nome}}`), `label sem htmlFor ${nome}`);
  }
});

test("estados de loading, erro e indisponivel sao anunciados", () => {
  const src = fonte(PAGE_PATH);
  assert.ok(src.includes('aria-busy="true"'));
  assert.ok(src.includes('aria-live="polite"'));
  assert.ok(src.includes('role="alert"'));
  assert.ok(src.includes("estado-indisponivel"));
  assert.ok(src.includes("Tentar novamente"));
  // A tabela nao e' renderizada no estado indisponivel.
  const indisp = src.slice(src.indexOf("estado-indisponivel"), src.indexOf("avoe-filtros"));
  assert.ok(!indisp.includes("<table"));
  assert.ok(indisp.includes("não é o mesmo que valores iguais a zero"));
});

test("as tabelas rolam por dentro e nao estouram a pagina", () => {
  const src = fonte(PAGE_PATH);
  assert.equal((src.match(/<TableScrollHint\b/g) ?? []).length, 2, "duas tabelas");
  assert.equal((src.match(/<\/TableScrollHint>/g) ?? []).length, 2);
  assert.ok(src.includes("max-h-[26rem] overflow-y-auto"));
  assert.ok(src.includes("max-h-[32rem] overflow-y-auto"));
  // Sem overflow horizontal na pagina: nenhuma LARGURA FIXA solta. `min-w-` e
  // `max-w-` sao permitidos e necessarios — e o `min-w-[44rem]` da tabela que
  // faz a rolagem acontecer DENTRO do container, em vez de esticar a pagina.
  assert.ok(!/(?<!min-)(?<!max-)\bw-\[\d/.test(src), "largura fixa proibida");
  assert.ok(src.includes("min-w-[44rem]"), "tabela de metas precisa de min-w");
  assert.ok(src.includes("min-w-[56rem]"), "tabela de canais precisa de min-w");
  assert.ok(src.includes("min-w-0"), "quebra de texto preservada");
});

test("as tabelas tem caption e cabecalho com scope", () => {
  const src = fonte(PAGE_PATH);
  assert.equal((src.match(/<caption/g) ?? []).length, 2);
  const scopes = src.match(/scope="col"/g) ?? [];
  assert.equal(scopes.length, 12, "5 colunas de metas + 7 de canais");
});

test("mensagem de erro nao ecoa detalhe tecnico", () => {
  assert.ok(COPY.erro.includes("somente"));
  assert.ok(!/[0-9]{3}/.test(COPY.erro), "sem codigo HTTP no texto");
  const src = fonte(PAGE_PATH);
  const captura = src.slice(src.indexOf(".catch("), src.indexOf("useEffect("));
  assert.ok(!captura.includes("err.message"), "nao pode exibir a mensagem crua");
  assert.ok(!captura.includes("setErroTexto"), "nao existe estado de texto de erro");
});

// ---------------------------------------------------------------------------
// 9. A UI so apresenta: nenhuma regra do backend duplicada
// ---------------------------------------------------------------------------

test("a pagina nao reimplementa a selecao de captura nem a validacao", () => {
  const src = fonte(PAGE_PATH) + fonte(CONTRACT_PATH);
  for (const proibido of [
    "captured_at >", "max(captured_at)", "started_at", "finished_at",
    "snapshot_id ===", "import_run_id", "source_file", "source_file_hash",
  ]) {
    assert.ok(!src.includes(proibido), `regra do backend duplicada: ${proibido}`);
  }
});

test("o cliente busca a rota certa, sem parametro, e levanta em falha", () => {
  const src = fonte(CLIENT_PATH);
  assert.ok(src.includes("/api/v1/performance/avoe-snapshot"));
  assert.ok(src.includes("export async function fetchAvoeSnapshot"));
  assert.ok(src.includes("throw new AvoeSnapshotError"));
  const inicio = src.indexOf("export async function fetchAvoeSnapshot");
  const fn = src.slice(inicio, src.indexOf("\n}", inicio));
  assert.ok(!fn.includes("avoe-snapshot?"), "a rota nao aceita querystring");
  assert.ok(!fn.includes("URLSearchParams"), "nao monta query");
  assert.ok(!fn.includes("mock"), "nunca ha fallback em mock");
  // A assinatura recebe apenas o sinal de aborto.
  assert.ok(fn.includes("signal?: AbortSignal"));
});

test("nenhum campo Avoe entra em contrato oficial do cliente", () => {
  const src = fonte(CLIENT_PATH);
  // As interfaces oficiais (Overview, BrandRow, Canais) nao ganharam campo Avoe.
  const oficial = src.slice(0, src.indexOf("// Snapshot manual da Avoe"));
  for (const proibido of ["avoe", "reported_amount", "target_amount", "captured_age_days"]) {
    assert.ok(
      !oficial.toLowerCase().includes(proibido.toLowerCase()),
      `contrato oficial mencionou ${proibido}`,
    );
  }
});
