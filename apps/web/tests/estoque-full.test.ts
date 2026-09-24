/**
 * Gate FULL-SOURCE-3 — contrato da tela "Estoque Full Shopee".
 *
 * Sem DOM: os testes exercitam as regras puras e verificam o contrato do
 * componente por inspecao do fonte, como as demais telas da Torre.
 *
 * O que estes testes existem para impedir, acima de tudo: que ausencia de
 * medicao seja desenhada como zero. Um "0" manda repor; "nao sabemos" nao
 * manda nada.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import {
  AVISO_ALERTAS_SO_NA_TORRE,
  AVISO_CARGA_MANUAL,
  AVISO_CONTEXTO,
  AVISO_COBERTURA_TORRE,
  AVISO_KITS,
  CLASSIFICACOES,
  CLASS_LABEL,
  DIAS_ATE_AVISAR,
  EXIGEM_ACAO,
  FILTROS_VAZIOS,
  PROXIMO_PASSO,
  SEM_DADO,
  TITULO_INDISPONIVEL,
  buildQuery,
  fmtCobertura,
  fmtData,
  fmtDecimal,
  fmtInt,
  fmtOpcional,
  fotografiaEstaVelha,
  frescorLabel,
  isIndisponivel,
  isVazioPorFiltro,
  nomeExibido,
  skuExibido,
  temFiltroAtivo,
  type EstoqueFullOk,
  type EstoqueFullResponse,
  type Frescor,
  type ProdutoEstoque,
} from "../src/lib/estoque-full.ts";
import {
  ESTOQUE_FULL_FLAG,
  MOTIVO_DESLIGADA,
  VALOR_QUE_LIGA,
  estoqueFullEnabled,
} from "../src/lib/estoque-full-flag.ts";
import {
  ESTOQUE_FULL_NAV,
  navSections,
} from "../src/components/shell/nav-config.ts";

const RAIZ = join(import.meta.dirname, "..");
const CLIENT_SRC = readFileSync(
  join(RAIZ, "app", "estoque-full", "EstoqueFullClient.tsx"),
  "utf8",
);
const PAGE_SRC = readFileSync(
  join(RAIZ, "app", "estoque-full", "page.tsx"),
  "utf8",
);

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function produto(over: Partial<ProdutoEstoque> = {}): ProdutoEstoque {
  return {
    shop_account: "barbours",
    brand: "barbours",
    item_id: "102",
    model_id: "0",
    item_sku: "SKU-102",
    item_name: "Capa Beta",
    item_status: "NORMAL",
    is_kit: false,
    full_stock_saleable: 10,
    full_stock_total: 12,
    location_count: 2,
    por_cd: [
      { location_id: "CD-SP", full_stock: 7, is_saleable: true },
      { location_id: "CD-MG", full_stock: 5, is_saleable: null },
    ],
    units_sold_28d: 56,
    days_with_sales_28d: 20,
    avg_daily_units_28d: 2,
    cobertura_torre_dias: 5,
    classificacao_torre: "BAIXO_CANDIDATO",
    vinculo_vendas: "COM_VENDA",
    reserved_stock: 3,
    seller_stock_total: 5,
    summary_available_stock: 11,
    units_sold_28d_legado_com_unpaid: 60,
    ref_date: "2026-09-23",
    ...over,
  };
}

function ok(over: Partial<EstoqueFullOk> = {}): EstoqueFullOk {
  return {
    status: "ok",
    marketplace: "shopee",
    scope_label: "Estoque Full Shopee — cobertura API",
    indicadores: {
      unidades_vendaveis_operacional: 610,
      unidades_vendaveis_kits_contexto: 25,
      unidades_vendaveis_total: 635,
      produtos_ruptura: 1,
      produtos_baixo: 1,
      produtos_excesso: 1,
      produtos_sem_giro: 1,
      produtos_suficientes: 1,
      produtos_sem_demanda_medida: 1,
      produtos_kit_nao_conciliado: 1,
      produtos_total: 7,
      produtos_exigem_acao: 2,
    },
    produtos: [produto()],
    total_no_filtro: 1,
    truncado: false,
    frescor: {
      ref_date: "2026-09-23",
      source_captured_at: "2026-09-23T08:00:00Z",
      ingested_at: "2026-09-23T09:00:00Z",
      dias_desde_a_fotografia: 1,
      load_mode: "manual_snapshot",
      no_automation: true,
    },
    limites: {
      cobertura_baixa_dias: 7,
      cobertura_excesso_dias: 90,
      provisorio: true,
      observacao: "Limiares PROVISORIOS, nao ratificados.",
    },
    contas_cobertas: ["apice", "barbours"],
    marcas_nao_cobertas: ["kokeshi"],
    limitacoes: ["Cobertura PARCIAL."],
    ...over,
  };
}

const frescor = (over: Partial<Frescor> = {}): Frescor => ({
  ...ok().frescor,
  ...over,
});

// ---------------------------------------------------------------------------
// 🔑 Ausencia nao e' zero
// ---------------------------------------------------------------------------

test("fmtOpcional: null vira travessao e zero continua zero", () => {
  assert.equal(fmtOpcional(null), SEM_DADO);
  assert.equal(fmtOpcional(undefined), SEM_DADO);
  assert.equal(fmtOpcional(0), "0");
  assert.equal(fmtOpcional(3), "3");
});

test("fmtOpcional nunca devolve '0' para ausencia", () => {
  for (const ausente of [null, undefined]) {
    assert.notEqual(fmtOpcional(ausente as null), "0");
  }
});

test("fmtCobertura: sem venda na janela nao vira 0 dias", () => {
  // 0 dia de cobertura e' RUPTURA; sem venda e' o oposto da urgencia.
  assert.equal(fmtCobertura(null), SEM_DADO);
  assert.equal(fmtCobertura(0), "0,0");
  assert.equal(fmtCobertura(5), "5,0");
});

test("fmtDecimal aceita numero em string, como o JSON de numeric entrega", () => {
  assert.equal(fmtDecimal("2.5"), "2,5");
  assert.equal(fmtDecimal(2.5), "2,5");
  assert.equal(fmtDecimal(null), SEM_DADO);
  assert.equal(fmtDecimal(""), SEM_DADO);
  // Lixo nao vira 0: vira ausencia.
  assert.equal(fmtDecimal("abc"), SEM_DADO);
});

test("fmtInt formata milhar em pt-BR", () => {
  assert.equal(fmtInt(0), "0");
  assert.ok(fmtInt(1234).includes("1"));
  assert.equal(fmtInt(1234).replace(/\D/g, ""), "1234");
});

// ---------------------------------------------------------------------------
// Estados: indisponivel x vazio por filtro
// ---------------------------------------------------------------------------

test("isIndisponivel distingue pelo status, nao pela lista vazia", () => {
  const vazio = ok({ produtos: [], total_no_filtro: 0 });
  assert.equal(isIndisponivel(vazio), false, "vazio por filtro NAO e' indisponivel");
  assert.equal(isVazioPorFiltro(vazio), true);

  const indisponivel: EstoqueFullResponse = {
    status: "unavailable",
    marketplace: "shopee",
    unavailable_reason: "x",
    motivo_tecnico: "fato_inexistente",
    scope_label: "y",
  };
  assert.equal(isIndisponivel(indisponivel), true);
  assert.equal(isVazioPorFiltro(indisponivel), false);
});

test("cada motivo tecnico tem titulo e proximo passo proprios", () => {
  const motivos = [
    "feature_flag_desligada",
    "fato_inexistente",
    "sem_fotografia_publicada",
  ] as const;
  const titulos = new Set<string>();
  const passos = new Set<string>();
  for (const m of motivos) {
    assert.ok(TITULO_INDISPONIVEL[m], `sem titulo para ${m}`);
    assert.ok(PROXIMO_PASSO[m], `sem proximo passo para ${m}`);
    titulos.add(TITULO_INDISPONIVEL[m]);
    passos.add(PROXIMO_PASSO[m]);
  }
  // Textos IGUAIS fariam as tres causas parecerem a mesma coisa.
  assert.equal(titulos.size, 3);
  assert.equal(passos.size, 3);
});

test("o passo do caso de producao cita as migrations pendentes", () => {
  assert.match(PROXIMO_PASSO.fato_inexistente, /020/);
  assert.match(PROXIMO_PASSO.fato_inexistente, /021/);
});

// ---------------------------------------------------------------------------
// Frescor
// ---------------------------------------------------------------------------

test("frescorLabel nomeia hoje, ontem e a distancia em dias", () => {
  assert.match(frescorLabel(frescor({ dias_desde_a_fotografia: 0 })), /hoje/);
  assert.match(frescorLabel(frescor({ dias_desde_a_fotografia: 1 })), /ontem/);
  assert.match(frescorLabel(frescor({ dias_desde_a_fotografia: 9 })), /9 dias/);
});

test("frescorLabel sem distancia ainda mostra a data", () => {
  const texto = frescorLabel(frescor({ dias_desde_a_fotografia: null }));
  assert.match(texto, /23\/09\/2026/);
});

test("fotografia velha dispara aviso a partir do limiar", () => {
  assert.equal(
    fotografiaEstaVelha(frescor({ dias_desde_a_fotografia: DIAS_ATE_AVISAR - 1 })),
    false,
  );
  assert.equal(
    fotografiaEstaVelha(frescor({ dias_desde_a_fotografia: DIAS_ATE_AVISAR })),
    true,
  );
  // Ausencia de distancia nao e' alarme falso.
  assert.equal(
    fotografiaEstaVelha(frescor({ dias_desde_a_fotografia: null })),
    false,
  );
});

test("fmtData nao passa por Date e nao desloca fuso", () => {
  assert.equal(fmtData("2026-09-23"), "23/09/2026");
  assert.equal(fmtData("2026-01-01T03:00:00Z"), "01/01/2026");
  assert.equal(fmtData(null), SEM_DADO);
  assert.equal(fmtData("lixo"), SEM_DADO);
});

// ---------------------------------------------------------------------------
// Query e filtros
// ---------------------------------------------------------------------------

test("buildQuery omite listas vazias", () => {
  // Ausencia significa "todas as contas da allowlist" no backend; mandar
  // `brands=` vazio seria outra coisa.
  assert.equal(buildQuery(FILTROS_VAZIOS), "");
});

test("buildQuery repete a chave por valor selecionado", () => {
  const qs = buildQuery({
    ...FILTROS_VAZIOS,
    brands: ["apice", "barbours"],
    classificacoes: ["RUPTURA_CANDIDATA"],
  });
  const params = new URLSearchParams(qs);
  assert.deepEqual(params.getAll("brands"), ["apice", "barbours"]);
  assert.deepEqual(params.getAll("classificacoes"), ["RUPTURA_CANDIDATA"]);
});

test("buildQuery codifica a busca sem vazar caractere especial", () => {
  const qs = buildQuery({ ...FILTROS_VAZIOS, busca: "100% & capa" });
  assert.ok(!qs.includes("100% &"), "o termo foi para a URL sem codificar");
  assert.equal(new URLSearchParams(qs).get("busca"), "100% & capa");
});

test("buildQuery ignora busca so' com espacos", () => {
  assert.equal(buildQuery({ ...FILTROS_VAZIOS, busca: "   " }), "");
});

test("somente_acao so' aparece quando ligado", () => {
  assert.equal(buildQuery({ ...FILTROS_VAZIOS, somenteAcao: false }), "");
  assert.equal(
    new URLSearchParams(
      buildQuery({ ...FILTROS_VAZIOS, somenteAcao: true }),
    ).get("somente_acao"),
    "true",
  );
});

test("temFiltroAtivo reconhece cada dimensao", () => {
  assert.equal(temFiltroAtivo(FILTROS_VAZIOS), false);
  assert.equal(temFiltroAtivo({ ...FILTROS_VAZIOS, brands: ["apice"] }), true);
  assert.equal(
    temFiltroAtivo({ ...FILTROS_VAZIOS, classificacoes: ["SUFICIENTE"] }),
    true,
  );
  assert.equal(temFiltroAtivo({ ...FILTROS_VAZIOS, busca: "abc" }), true);
  assert.equal(temFiltroAtivo({ ...FILTROS_VAZIOS, busca: "  " }), false);
  assert.equal(temFiltroAtivo({ ...FILTROS_VAZIOS, somenteAcao: true }), true);
});

// ---------------------------------------------------------------------------
// Dominio e rotulos
// ---------------------------------------------------------------------------

test("toda classificacao tem rotulo em portugues", () => {
  for (const c of CLASSIFICACOES) {
    assert.ok(CLASS_LABEL[c], `sem rotulo para ${c}`);
    // "CANDIDATA" e' vocabulario interno e nao vai para a tela.
    assert.ok(!CLASS_LABEL[c].includes("CANDIDAT"));
  }
});

test("rotulos sao distintos entre si", () => {
  const rotulos = CLASSIFICACOES.map((c) => CLASS_LABEL[c]);
  assert.equal(new Set(rotulos).size, rotulos.length);
});

test("exigem acao sao exatamente ruptura e baixo", () => {
  assert.deepEqual([...EXIGEM_ACAO], ["RUPTURA_CANDIDATA", "BAIXO_CANDIDATO"]);
});

test("nome exibido cai para SKU e depois para o item_id", () => {
  assert.equal(nomeExibido(produto()), "Capa Beta");
  assert.equal(nomeExibido(produto({ item_name: null })), "SKU-102");
  assert.equal(nomeExibido(produto({ item_name: "  " })), "SKU-102");
  assert.equal(
    nomeExibido(produto({ item_name: null, item_sku: null })),
    "Item 102",
  );
});

test("SKU ausente vira travessao, nao string vazia", () => {
  assert.equal(skuExibido(produto({ item_sku: null })), SEM_DADO);
  assert.equal(skuExibido(produto({ item_sku: "   " })), SEM_DADO);
});

// ---------------------------------------------------------------------------
// Flag
// ---------------------------------------------------------------------------

test("flag e' fail-closed: so' a string exata liga", () => {
  const liga = (v: string | undefined) =>
    estoqueFullEnabled({ NEXT_PUBLIC_SHOPEE_FBS_STOCK_ENABLED: v } as NodeJS.ProcessEnv);
  assert.equal(liga("true"), true);
  for (const v of ["", "1", "TRUE", "True", "yes", "false", undefined]) {
    assert.equal(liga(v), false, `"${v}" nao pode ligar a tela`);
  }
});

test("flag de estoque e' NOME diferente da flag de desempenho", () => {
  // A do desempenho ja' esta LIGADA em producao. Compartilhar faria esta tela
  // nascer visivel sobre fatos que nao existem no banco.
  assert.equal(ESTOQUE_FULL_FLAG, "NEXT_PUBLIC_SHOPEE_FBS_STOCK_ENABLED");
  assert.notEqual(ESTOQUE_FULL_FLAG, "NEXT_PUBLIC_SHOPEE_FBS_ENABLED");
  assert.equal(VALOR_QUE_LIGA, "true");
  // Ligar a de desempenho nao pode ligar esta.
  assert.equal(
    estoqueFullEnabled({
      NEXT_PUBLIC_SHOPEE_FBS_ENABLED: "true",
    } as NodeJS.ProcessEnv),
    false,
  );
});

test("a rota tem guard no servidor, nao so' no menu", () => {
  assert.match(PAGE_SRC, /estoqueFullEnabled\(\)/);
  assert.match(PAGE_SRC, /notFound\(\)/);
});

test("o item de menu responde so' a propria flag", () => {
  const semFlag = navSections({ shopeeFbs: true, expedicao: true });
  const hrefs = semFlag.flatMap((s) => s.pages.map((p) => p.href));
  assert.ok(!hrefs.includes(ESTOQUE_FULL_NAV.href));

  const comFlag = navSections({ estoqueFull: true });
  const comHrefs = comFlag.flatMap((s) => s.pages.map((p) => p.href));
  assert.ok(comHrefs.includes("/estoque-full"));
});

test("o rotulo do menu nao colide com as outras telas Full", () => {
  const todos = navSections({
    estoqueFull: true,
    shopeeFbs: true,
    expedicao: true,
  }).flatMap((s) => s.pages.map((p) => p.label));
  assert.equal(new Set(todos).size, todos.length, "rotulo duplicado no menu");
});

test("motivo de desligada diz que nada foi publicado", () => {
  // A mensagem precisa afirmar as DUAS coisas: a tela nao esta habilitada E a
  // fotografia nao foi publicada. So' a primeira deixaria o leitor supor que o
  // dado existe e esta' apenas escondido.
  assert.match(MOTIVO_DESLIGADA, /n[ãa]o habilitada/i);
  assert.match(MOTIVO_DESLIGADA, /n[ãa]o foi publicada/i);
});

// ---------------------------------------------------------------------------
// Contrato do componente
// ---------------------------------------------------------------------------

test("a tela nao recalcula classificacao nem cobertura", () => {
  // Uma segunda implementacao da regra divergiria da fato no primeiro ajuste.
  for (const proibido of [
    "cobertura_baixa_dias >",
    "units_sold_28d / ",
    "full_stock_saleable /",
    "RUPTURA_CANDIDATA =",
  ]) {
    assert.ok(
      !CLIENT_SRC.includes(proibido),
      `a tela esta recalculando: "${proibido}"`,
    );
  }
});

test("toda coluna anulavel passa por um formatador de ausencia", () => {
  // `reserved_stock` e' o representante: se ele for impresso cru, um null
  // viraria "null" ou, pior, um 0 depois de algum `?? 0`.
  assert.ok(CLIENT_SRC.includes("fmtOpcional(p.reserved_stock)"));
  assert.ok(!CLIENT_SRC.includes("?? 0"), "coalescencia para 0 apaga ausencia");
  assert.ok(!CLIENT_SRC.includes("|| 0"), "coalescencia para 0 apaga ausencia");
});

test("o painel de indisponibilidade nega estoque zero por escrito", () => {
  assert.match(CLIENT_SRC, /não significa estoque zero/);
});

test("indisponivel e vazio-por-filtro sao blocos diferentes", () => {
  assert.ok(CLIENT_SRC.includes("PainelIndisponivel"));
  assert.ok(CLIENT_SRC.includes("Nenhum produto neste filtro"));
});

test("a tela declara os quatro estados", () => {
  assert.ok(CLIENT_SRC.includes("Carregando estoque"), "falta loading");
  assert.ok(CLIENT_SRC.includes('role="alert"'), "falta estado de erro");
  assert.ok(CLIENT_SRC.includes("Tentar novamente"), "erro sem acao de saida");
  assert.ok(CLIENT_SRC.includes("PainelIndisponivel"), "falta indisponivel");
});

test("erro limpa o payload, para nao misturar numero velho com falha", () => {
  const trecho = CLIENT_SRC.split("catch (e)")[1] ?? "";
  assert.ok(trecho.includes("setPayload(null)"));
});

test("a tela exibe atualizacao e limitacoes", () => {
  for (const aviso of [
    AVISO_COBERTURA_TORRE,
    AVISO_CONTEXTO,
    AVISO_KITS,
    AVISO_CARGA_MANUAL,
    AVISO_ALERTAS_SO_NA_TORRE,
  ]) {
    const nome = Object.entries({
      AVISO_COBERTURA_TORRE,
      AVISO_CONTEXTO,
      AVISO_KITS,
      AVISO_CARGA_MANUAL,
      AVISO_ALERTAS_SO_NA_TORRE,
    }).find(([, v]) => v === aviso)?.[0];
    assert.ok(CLIENT_SRC.includes(`{${nome}}`), `${nome} nao aparece na tela`);
  }
  assert.ok(CLIENT_SRC.includes("frescorLabel(payload.frescor)"));
});

test("os alertas sao declarados como exclusivos da Torre", () => {
  assert.match(AVISO_ALERTAS_SO_NA_TORRE, /Torre/);
  assert.match(AVISO_ALERTAS_SO_NA_TORRE, /Nada é enviado ao/);
});

test("a cobertura e' rotulada como calculo nosso", () => {
  assert.match(AVISO_COBERTURA_TORRE, /cálculo nosso/);
  assert.match(AVISO_COBERTURA_TORRE, /Não reproduz nenhuma fórmula da Shopee/);
  assert.ok(CLIENT_SRC.includes("Cobertura da Torre"));
});

test("a marca nao coberta aparece como ausencia, nunca como zero", () => {
  assert.ok(CLIENT_SRC.includes("marcas_nao_cobertas"));
  assert.match(CLIENT_SRC, /como ausência, nunca como zero/);
});

test("o truncamento e' dito ao usuario", () => {
  assert.ok(CLIENT_SRC.includes("payload.truncado"));
  assert.ok(CLIENT_SRC.includes("total_no_filtro"));
});

test("o estoque Full exibido e' o vendavel, nao o total", () => {
  // `full_stock_total` aparece como contexto secundario; a medida principal da
  // coluna e' a vendavel, que e' o numero conciliado com o Seller Center.
  const coluna = CLIENT_SRC.split("Estoque Full vendável")[1] ?? "";
  const iVendavel = coluna.indexOf("full_stock_saleable");
  const iTotal = coluna.indexOf("full_stock_total");
  assert.ok(iVendavel >= 0 && iTotal > iVendavel);
});

test("a tabela cobre as colunas operacionais pedidas", () => {
  for (const cabecalho of [
    "Conta / marca",
    "SKU e produto",
    "Estoque Full vendável",
    "Demanda 28d",
    "Cobertura da Torre",
    "Classificação",
    "Reservado",
    "Por CD",
    "Fotografia",
  ]) {
    assert.ok(CLIENT_SRC.includes(cabecalho), `falta a coluna ${cabecalho}`);
  }
});

test("is_saleable ausente nao e' desenhado como nao-vendavel", () => {
  assert.ok(CLIENT_SRC.includes("cd.is_saleable === false"));
  assert.ok(CLIENT_SRC.includes("cd.is_saleable === null"));
  assert.ok(
    !CLIENT_SRC.includes("!cd.is_saleable"),
    "negacao truthy juntaria null com false",
  );
});

test("o payload de exemplo atravessa a formatacao sem virar zero", () => {
  const p = produto({
    reserved_stock: null,
    seller_stock_total: null,
    summary_available_stock: null,
    cobertura_torre_dias: null,
    full_stock_saleable: 0,
  });
  assert.equal(fmtOpcional(p.reserved_stock), SEM_DADO);
  assert.equal(fmtOpcional(p.seller_stock_total), SEM_DADO);
  assert.equal(fmtCobertura(p.cobertura_torre_dias), SEM_DADO);
  // ...e o zero MEDIDO continua zero.
  assert.equal(fmtInt(p.full_stock_saleable), "0");
});

test("fixture completa continua sendo aceita pelo discriminante", () => {
  const payload: EstoqueFullResponse = ok();
  assert.equal(isIndisponivel(payload), false);
  assert.equal(isVazioPorFiltro(payload), false);
});
