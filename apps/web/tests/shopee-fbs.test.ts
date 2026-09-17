/**
 * Gate FULL-SH-1D — contrato da tela "Full Shopee".
 *
 * Sem DOM: os testes exercitam as regras puras e verificam o contrato do
 * componente por inspecao do fonte, como as demais telas da Torre.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import {
  AVISO_CARGA_MANUAL,
  AVISO_COBERTURA,
  AVISO_DIVERGENCIA,
  AVISO_GMV_BRUTO,
  AVISO_HANDLING,
  CLASS_LABEL,
  HANDLING_COBERTURA_MINIMA,
  MAX_RANGE_DAYS,
  SEM_DADO,
  alertas,
  buildQuery,
  byClass,
  fmtCount,
  fmtGmv,
  fmtHandling,
  fmtShare,
  handlingPoucaCobertura,
  isUnavailable,
  isVazioReal,
  lastClosedDate,
  parseGmv,
  presetRange,
  serieDiaria,
  validateRange,
  type ShopeeFbsResponse,
} from "../src/lib/shopee-fbs.ts";
import {
  MOTIVO_DESLIGADA,
  SHOPEE_FBS_FLAG,
  VALOR_QUE_LIGA,
  shopeeFbsEnabled,
} from "../src/lib/shopee-fbs-flag.ts";
import {
  SHOPEE_FBS_NAV,
  NAV_SECTIONS,
  getRouteTitle,
  navSections,
} from "../src/components/shell/nav-config.ts";

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8");

const CLIENT = fonte("app/full-shopee/FullShopeeClient.tsx");
const PAGE = fonte("app/full-shopee/page.tsx");
const LIB = fonte("src/lib/shopee-fbs.ts");
const NAVLIST = fonte("src/components/shell/NavList.tsx");
const API_CLIENT = fonte("src/lib/api-client.ts");

const HOJE = "2026-09-16";

function payload(over: Partial<ShopeeFbsResponse> = {}): ShopeeFbsResponse {
  return {
    meta: {
      marketplace: "shopee", scope_label: "Shopee — cobertura API",
      date_policy: "closed_day", d0_materialized: false,
      last_closed_date: "2026-09-15",
      requested_date_from: "2026-08-01", requested_date_to: "2026-08-31",
      effective_date_from: "2026-08-01", effective_date_to: "2026-08-31",
      expected_accounts: ["apice", "barbours", "lescent", "rituaria"],
      observed_accounts: ["apice", "barbours", "lescent", "rituaria"],
      missing_accounts: [], unexpected_accounts: [],
      covered_brands: ["apice", "barbours", "lescent", "rituaria"],
      brands_not_covered: ["kokeshi"],
      gmv_definition: "GMV bruto de pedidos nao cancelados...",
      freshness: {
        freshness_status: "fresh", source_watermark_at: "2026-09-16T18:00:00Z",
        refreshed_at: "2026-09-16T22:01:23Z", source_max_date: "2026-09-15",
        source_min_date: "2026-01-01", source_age_hours: 4.5,
        snapshot_age_hours: 0.6, closed_days_behind: 0,
        load_mode: "manual_snapshot", no_automation: true,
      },
      warnings: [], limitations: ["limitacao a"],
      ...(over.meta ?? {}),
    },
    totals: {
      gross_gmv: "1953272.70", gross_units: 26135, created_orders: 27215,
      eligible_orders: 22929, cancelled_orders: 4286, to_return_orders: 41,
      to_return_gmv: "3195.54", unpaid_orders: 0, unpaid_gmv: "0",
      handling: {
        seconds_avg: 95051.19, hours_avg: 26.4, days_avg: 1.1,
        seconds_sum: 2179428743, sample_count: 22929, coverage_ratio: 1,
      },
      ...(over.totals ?? {}),
    },
    rates: { cancellation_rate: 0.1574866801, ...(over.rates ?? {}) },
    shares: {
      share_fbs_gmv: 0.6096477671, share_fbs_orders: 0.6442060273,
      share_fbs_units: 0.6141572604, ...(over.shares ?? {}),
    },
    by_class: over.by_class ?? [
      {
        fbs_class: "fbs", gross_gmv: "1190808.34", gross_units: 16051,
        created_orders: 16000, eligible_orders: 14771, cancelled_orders: 1229,
        to_return_gmv: "0", unpaid_gmv: "0",
        handling: { seconds_avg: 1, hours_avg: 1, days_avg: 1,
                    seconds_sum: 10, sample_count: 10, coverage_ratio: 1 },
      },
      {
        fbs_class: "seller", gross_gmv: "762464.36", gross_units: 10084,
        created_orders: 11215, eligible_orders: 8158, cancelled_orders: 3057,
        to_return_gmv: "3195.54", unpaid_gmv: "0",
        handling: { seconds_avg: 1, hours_avg: 1, days_avg: 1,
                    seconds_sum: 10, sample_count: 10, coverage_ratio: 1 },
      },
    ],
    by_brand: over.by_brand ?? [
      { brand: "barbours", gross_gmv: "944358.30", gross_units: 1, eligible_orders: 1,
        cancelled_orders: 0, created_orders: 1, share_fbs_gmv: 0.807848917,
        share_fbs_orders: 0.8 },
      { brand: "apice", gross_gmv: "275234.00", gross_units: 1, eligible_orders: 1,
        cancelled_orders: 0, created_orders: 1, share_fbs_gmv: 0, share_fbs_orders: 0 },
    ],
    by_account: over.by_account ?? [
      { shop_account: "barbours", brand: "barbours", gross_gmv: "944358.30",
        eligible_orders: 1, created_orders: 1, share_fbs_gmv: 0.807848917,
        source_watermark_at: "2026-09-16T18:09:03Z" },
    ],
    daily: over.daily ?? [
      { ref_date: "2026-08-01", fbs_class: "fbs", gross_gmv: "700.00",
        gross_units: 7, eligible_orders: 7, created_orders: 8, cancelled_orders: 1 },
      { ref_date: "2026-08-01", fbs_class: "seller", gross_gmv: "300.00",
        gross_units: 3, eligible_orders: 3, created_orders: 4, cancelled_orders: 1 },
    ],
  };
}

// ---------------------------------------------------------------------------
// Flag
// ---------------------------------------------------------------------------

test("flag ausente, vazia ou diferente de 'true' mantem a tela desligada", () => {
  assert.equal(SHOPEE_FBS_FLAG, "NEXT_PUBLIC_SHOPEE_FBS_ENABLED");
  assert.equal(VALOR_QUE_LIGA, "true");
  for (const v of [undefined, "", "1", "TRUE", "True", "yes", "sim", "on", " true"]) {
    const env = v === undefined ? {} : { NEXT_PUBLIC_SHOPEE_FBS_ENABLED: v };
    assert.equal(shopeeFbsEnabled(env as NodeJS.ProcessEnv), false,
      `${JSON.stringify(v)} nao pode ligar a superficie`);
  }
  assert.equal(
    shopeeFbsEnabled({ NEXT_PUBLIC_SHOPEE_FBS_ENABLED: "true" } as NodeJS.ProcessEnv),
    true);
});

test("item de navegacao so' existe com a flag ligada", () => {
  const desligado = navSections({ shopeeFbs: false });
  assert.equal(JSON.stringify(desligado), JSON.stringify(NAV_SECTIONS),
    "com a flag off a navegacao e' identica a original");
  const ligado = navSections({ shopeeFbs: true });
  const hrefs = ligado.flatMap((s) => s.pages.map((p) => p.href));
  assert.ok(hrefs.includes("/full-shopee"));
  const ops = ligado.find((s) => s.label === "Operações");
  assert.ok(ops?.pages.some((p) => p.href === SHOPEE_FBS_NAV.href));
});

test("a rota e' de topo, para nao colidir com /operacoes por prefixo", () => {
  assert.equal(SHOPEE_FBS_NAV.href, "/full-shopee");
  assert.ok(!SHOPEE_FBS_NAV.href.startsWith("/operacoes/"));
});

test("NavList consulta a flag, nao a lista fixa", () => {
  assert.match(NAVLIST, /shopeeFbs: shopeeFbsEnabled\(\)/);
  assert.ok(!/\bNAV_SECTIONS\.map\b/.test(NAVLIST),
    "a lista fixa nao pode ser usada direto: ignoraria a flag");
});

test("a rota falha FECHADA no servidor, nao apenas no menu", () => {
  assert.match(PAGE, /import \{ notFound \} from "next\/navigation"/);
  assert.match(PAGE, /if \(!shopeeFbsEnabled\(\)\) notFound\(\);/);
  // O guard vem ANTES de montar o cliente -- nenhum fetch acontece.
  assert.ok(PAGE.indexOf("notFound()") < PAGE.indexOf("<FullShopeeClient"));
});

test("com a flag off nenhuma requisicao pode partir", () => {
  // O unico fetch vive no componente cliente, que so' e' montado depois do
  // guard. A page (servidor) nao chama a API.
  assert.ok(!PAGE.includes("fetchShopeeFbs"));
  assert.ok(CLIENT.includes("fetchShopeeFbs"));
});

test("a flag do backend e' independente e a ativacao e' coordenada", () => {
  const flagSrc = fonte("src/lib/shopee-fbs-flag.ts");
  assert.match(flagSrc, /SHOPEE_FBS_ENABLED/);
  assert.match(flagSrc, /coordenada|MESMA decisao/i);
  assert.ok(MOTIVO_DESLIGADA.length > 40);
});

// ---------------------------------------------------------------------------
// Dinheiro e tipos
// ---------------------------------------------------------------------------

test("dinheiro chega como STRING e a conversao tem um unico ponto", () => {
  assert.match(LIB, /gross_gmv: string;/);
  assert.match(LIB, /to_return_gmv: string;/);
  assert.ok(!/gross_gmv: number/.test(LIB), "tipar como number produz NaN");
  assert.equal(parseGmv("944358.30"), 944358.3);
});

test("parseGmv devolve null para ausencia, nunca zero", () => {
  for (const v of [null, undefined, "", "abc", "R$ 10"]) {
    assert.equal(parseGmv(v as string), null, `${JSON.stringify(v)}`);
  }
  assert.equal(parseGmv("0"), 0, "zero medido continua zero");
});

test("nenhum formatador devolve NaN ou Infinity", () => {
  for (const f of [fmtGmv("abc"), fmtGmv(null), fmtCount(NaN),
                   fmtShare(Infinity), fmtShare(null), fmtHandling(null)]) {
    assert.equal(f, SEM_DADO);
    assert.ok(!/NaN|Infinity/.test(f));
  }
});

// ---------------------------------------------------------------------------
// Zero x ausencia
// ---------------------------------------------------------------------------

test("share null vira travessao e share zero vira 0,00%", () => {
  assert.equal(fmtShare(null), SEM_DADO);
  assert.equal(fmtShare(0), "0,00%");
  assert.notEqual(fmtShare(0), SEM_DADO);
});

test("Apice: coberta, com share FBS legitimo de 0%", () => {
  const p = payload();
  const apice = p.by_brand.find((b) => b.brand === "apice")!;
  assert.equal(apice.share_fbs_gmv, 0);
  assert.equal(fmtShare(apice.share_fbs_gmv), "0,00%");
  // A tela rotula o caso para nao ser lido como falta de dado.
  assert.match(CLIENT, /coberta, sem FBS/);
  assert.match(CLIENT, /l\.share === 0/);
});

test("Kokeshi fica FORA da cobertura e nunca vira zero", () => {
  const p = payload();
  assert.ok(p.meta.brands_not_covered.includes("kokeshi"));
  assert.ok(!p.meta.covered_brands.includes("kokeshi"));
  assert.ok(!p.by_brand.some((b) => b.brand === "kokeshi"));
  assert.ok(!p.by_account.some((a) => a.shop_account === "kokeshi"));
  // A tela a exibe como nao coberta, com texto proprio.
  assert.match(CLIENT, /Fora da cobertura da API — sem medição\. Não é zero\./);
  assert.match(CLIENT, /naoCobertas/);
});

test("conta sem movimento aparece como fora da cobertura, nao 0%", () => {
  const p = payload({
    meta: { ...payload().meta, missing_accounts: ["lescent"],
            observed_accounts: ["apice", "barbours", "rituaria"] },
  } as Partial<ShopeeFbsResponse>);
  const a = alertas(p);
  assert.ok(a.some((x) => x.includes("lescent") && x.includes("não e 0%")
    || x.includes("lescent") && x.includes("nao e 0%")));
  assert.match(CLIENT, /ausentes/);
});

// ---------------------------------------------------------------------------
// Agregacao
// ---------------------------------------------------------------------------

test("share do PERIODO vem da API, nao da media dos shares diarios", () => {
  // Dois dias de pesos muito diferentes.
  const p = payload({
    shares: { share_fbs_gmv: 0.99, share_fbs_orders: null, share_fbs_units: null },
    daily: [
      { ref_date: "2026-08-01", fbs_class: "fbs", gross_gmv: "99000.00",
        gross_units: 1, eligible_orders: 1, created_orders: 1, cancelled_orders: 0 },
      { ref_date: "2026-08-01", fbs_class: "seller", gross_gmv: "1000.00",
        gross_units: 1, eligible_orders: 1, created_orders: 1, cancelled_orders: 0 },
      { ref_date: "2026-08-02", fbs_class: "fbs", gross_gmv: "1.00",
        gross_units: 1, eligible_orders: 1, created_orders: 1, cancelled_orders: 0 },
      { ref_date: "2026-08-02", fbs_class: "seller", gross_gmv: "1.00",
        gross_units: 1, eligible_orders: 1, created_orders: 1, cancelled_orders: 0 },
    ],
  });
  const serie = serieDiaria(p, "gmv");
  const mediaDiaria = serie.reduce((s, x) => s + (x.share ?? 0), 0) / serie.length;
  assert.ok(Math.abs(mediaDiaria - 0.745) < 0.01, `media diaria ${mediaDiaria}`);
  // O consolidado da API e' 0,99 -- DIFERENTE da media dos dias.
  assert.equal(p.shares.share_fbs_gmv, 0.99);
  assert.ok(Math.abs(p.shares.share_fbs_gmv! - mediaDiaria) > 0.2);
  // E a tela usa o consolidado nos cartoes.
  assert.match(CLIENT, /fmtShare\(dados\.shares\.share_fbs_gmv\)/);
});

test("share diario usa o denominador do proprio dia", () => {
  const serie = serieDiaria(payload(), "gmv");
  assert.equal(serie.length, 1);
  assert.equal(serie[0].share, 700 / 1000);
});

test("dia com denominador zero vira null, nunca zero", () => {
  const p = payload({
    daily: [
      { ref_date: "2026-08-03", fbs_class: "fbs", gross_gmv: "0",
        gross_units: 0, eligible_orders: 0, created_orders: 0, cancelled_orders: 0 },
      { ref_date: "2026-08-03", fbs_class: "seller", gross_gmv: "0",
        gross_units: 0, eligible_orders: 0, created_orders: 0, cancelled_orders: 0 },
    ],
  });
  assert.equal(serieDiaria(p, "gmv")[0].share, null);
});

test("FBS + seller fecha os totais", () => {
  const p = payload();
  const fbs = byClass(p, "fbs")!;
  const seller = byClass(p, "seller")!;
  // Tolerancia de meio centavo: `parseGmv` devolve `number`, e somar dois
  // valores binarios nao reproduz o Decimal exato do backend
  // (1190808.34 + 762464.36 = 1953272.6999999998). Por isso a API entrega o
  // total JA SOMADO em Decimal, e a tela nunca recompoe totais somando partes.
  assert.ok(
    Math.abs(parseGmv(fbs.gross_gmv)! + parseGmv(seller.gross_gmv)!
             - parseGmv(p.totals.gross_gmv)!) < 0.005,
    "FBS + seller precisa fechar o total (dentro do centavo)");
  assert.equal(fbs.eligible_orders + seller.eligible_orders, p.totals.eligible_orders);
  assert.equal(fbs.gross_units + seller.gross_units, p.totals.gross_units);
});

test("nao existe terceira classe", () => {
  assert.deepEqual(Object.keys(CLASS_LABEL).sort(), ["fbs", "seller"]);
  assert.ok(!/unknown/i.test(LIB.split("FbsClass =")[1].split("\n")[0]));
});

// ---------------------------------------------------------------------------
// Handling
// ---------------------------------------------------------------------------

test("handling e' ponderado e vem pronto da API", () => {
  const p = payload();
  // A tela NAO recalcula: usa `seconds_avg`/`hours_avg` do payload.
  assert.match(CLIENT, /fmtHandling\(dados\.totals\.handling\)/);
  assert.ok(!/seconds_sum\s*\/\s*sample_count/.test(CLIENT),
    "a media nao pode ser recalculada na tela");
  // Abaixo de 48 h a leitura util e' em horas; acima, em dias.
  assert.equal(fmtHandling(p.totals.handling), "26,4 h");
  assert.equal(
    fmtHandling({ ...p.totals.handling, hours_avg: 72, days_avg: 3 }), "3,0 dias");
});

test("handling sem amostra vira travessao, nao zero", () => {
  const h = { seconds_avg: null, hours_avg: null, days_avg: null,
              seconds_sum: 0, sample_count: 0, coverage_ratio: null };
  assert.equal(fmtHandling(h), SEM_DADO);
  assert.equal(handlingPoucaCobertura(h), false, "sem amostra nao e' 'pouca cobertura'");
});

test("baixa cobertura de handling e' LIMITACAO declarada", () => {
  const h = { seconds_avg: 10, hours_avg: 1, days_avg: 1, seconds_sum: 10,
              sample_count: 1, coverage_ratio: 0.2 };
  assert.ok(handlingPoucaCobertura(h));
  assert.ok(HANDLING_COBERTURA_MINIMA > 0 && HANDLING_COBERTURA_MINIMA <= 1);
  const p = payload({ totals: { ...payload().totals, handling: h } });
  assert.ok(alertas(p).some((a) => a.includes("Handling cobre apenas")));
});

test("handling nunca e' chamado de entrega", () => {
  assert.match(AVISO_HANDLING, /COLETA/);
  assert.match(AVISO_HANDLING, /nao e prazo de entrega/i);
  assert.ok(!/prazo de entrega\b(?! )/.test(CLIENT.replace(AVISO_HANDLING, "")));
  assert.match(CLIENT, /não é entrega|não é prazo de entrega/);
});

// ---------------------------------------------------------------------------
// GMV bruto e copy
// ---------------------------------------------------------------------------

test("a tela declara GMV bruto com to_return e unpaid incluidos", () => {
  assert.match(AVISO_GMV_BRUTO, /BRUTO/);
  assert.match(AVISO_GMV_BRUTO, /to_return/);
  assert.match(AVISO_GMV_BRUTO, /unpaid/);
  assert.match(AVISO_GMV_BRUTO, /exclui cancelados/);
  assert.match(AVISO_GMV_BRUTO, /nao e receita liquida/i);
  assert.match(CLIENT, /AVISO_GMV_BRUTO/);
});

test("to_return e exibido separadamente e nao somado de novo", () => {
  assert.match(CLIENT, /fmtGmv\(dados\.totals\.to_return_gmv\)/);
  assert.match(CLIENT, /já\s*\n?\s*incluídos|já incluídos/);
  assert.match(CLIENT, /Não some novamente/);
});

test("a divergencia com telas baseadas em is_sale e declarada", () => {
  assert.match(AVISO_DIVERGENCIA, /is_sale/);
  assert.match(AVISO_DIVERGENCIA, /DEFINICAO|definicao/i);
  assert.match(CLIENT, /AVISO_DIVERGENCIA/);
});

test("a tela NUNCA afirma tempo real ou atualizacao automatica", () => {
  assert.match(AVISO_CARGA_MANUAL, /manual/i);
  assert.match(AVISO_CARGA_MANUAL, /nao e tempo real/i);
  // Só o texto RENDERIZADO: os comentarios do fonte citam "tempo real"
  // justamente para proibi-lo, e procurar no arquivo cru reprovaria a
  // propria proibicao.
  const semComentarios = CLIENT
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .toLowerCase();
  assert.ok(!semComentarios.includes("tempo real"),
    "a tela nao pode dizer 'tempo real'");
  assert.ok(!/atualiza(ção|cao) autom/.test(semComentarios));
  assert.ok(!semComentarios.includes("ao vivo"));
  assert.match(CLIENT, /carga manual/);
});

test("a cobertura parcial esta declarada e nega 'Shopee total'", () => {
  assert.match(AVISO_COBERTURA, /PARCIAL/);
  assert.match(AVISO_COBERTURA, /Kokeshi/);
  assert.match(AVISO_COBERTURA, /nunca a\s+operacao Shopee inteira|nunca a operacao/i);
  assert.match(CLIENT, /AVISO_COBERTURA/);
});

// ---------------------------------------------------------------------------
// Janela
// ---------------------------------------------------------------------------

test("D-1 e' o teto e D0 e futuro sao recusados", () => {
  assert.equal(lastClosedDate(HOJE), "2026-09-15");
  assert.equal(validateRange("2026-09-01", "2026-09-15", HOJE), null);
  assert.match(String(validateRange("2026-09-01", HOJE, HOJE)), /dias fechados/);
  assert.match(String(validateRange("2026-09-01", "2026-09-20", HOJE)), /dias fechados/);
});

test("366 dias passa e 367 e' recusado", () => {
  const p366 = presetRange(366, HOJE);
  assert.equal(validateRange(p366.from, p366.to, HOJE), null);
  const d = new Date(`${p366.from}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  const msg = validateRange(d.toISOString().slice(0, 10), p366.to, HOJE);
  assert.match(String(msg), /367 dias excede o maximo de 366/);
  assert.equal(MAX_RANGE_DAYS, 366);
});

test("date_from posterior a date_to e recusado", () => {
  assert.match(String(validateRange("2026-08-31", "2026-08-01", HOJE)),
               /nao pode ser posterior/);
});

test("preset padrao termina em D-1", () => {
  const r = presetRange(30, HOJE);
  assert.equal(r.to, "2026-09-15");
  assert.equal(r.from, "2026-08-17");
});

test("limpar restaura o padrao de 30 dias e zera os filtros", () => {
  assert.match(CLIENT, /const limpar = useCallback/);
  const bloco = CLIENT.split("const limpar")[1].split("}, [hoje])")[0];
  for (const alvo of ["setFrom", "setTo", "setMarca", "setConta", "setMetrica"]) {
    assert.ok(bloco.includes(alvo), `limpar precisa restaurar ${alvo}`);
  }
  assert.ok(bloco.includes("presetRange(30"));
});

test("a validacao roda ANTES da requisicao", () => {
  const efeito = CLIENT.split("useEffect(() => {")[1];
  assert.ok(efeito.indexOf("if (erroJanela)") < efeito.indexOf("fetchShopeeFbs"));
});

// ---------------------------------------------------------------------------
// Requisicao
// ---------------------------------------------------------------------------

test("buildQuery so' envia parametros suportados pela API", () => {
  const q = buildQuery({ from: "2026-08-01", to: "2026-08-31",
                         brands: ["barbours"], accounts: ["lescent"] });
  const p = new URLSearchParams(q);
  assert.deepEqual([...new Set(p.keys())].sort(),
                   ["accounts", "brands", "date_from", "date_to"]);
  assert.equal(p.get("date_from"), "2026-08-01");
});

test("resposta atrasada nao sobrescreve a atual", () => {
  // AbortController cancela o request; a sequencia monotonica cobre a corrida
  // em que a resposta ja' estava em transito.
  assert.match(CLIENT, /new AbortController\(\)/);
  assert.match(CLIENT, /ctrl\.abort\(\)/);
  assert.match(CLIENT, /const seq = useRef\(0\)/);
  assert.match(CLIENT, /const meu = \+\+seq\.current/);
  assert.match(CLIENT, /if \(meu !== seq\.current\) return;/);
});

test("o 422 vira mensagem propria e nao ecoa a entrada", () => {
  const bloco = API_CLIENT.split("export async function fetchShopeeFbs")[1];
  assert.match(bloco, /res\.status === 422/);
  assert.match(bloco, /Periodo ou filtro invalido/);
  assert.ok(!/await res\.text\(\)/.test(bloco), "nao pode devolver o corpo do 422");
  assert.ok(!/detail/.test(bloco), "nao pode ecoar o detail do backend");
});

test("unavailable do backend nao e' tratado como erro", () => {
  const p = { marketplace: "shopee" as const, status: "unavailable" as const,
              scope_label: "x", unavailable_reason: "y",
              date_policy: "closed_day" as const };
  assert.ok(isUnavailable(p));
  assert.ok(!isUnavailable(payload()));
  assert.match(CLIENT, /setDesligadoBackend/);
});

// ---------------------------------------------------------------------------
// Estados
// ---------------------------------------------------------------------------

test("a tela cobre os estados obrigatorios", () => {
  for (const [rotulo, regex] of [
    ["loading", /role="status"[\s\S]{0,200}animate-pulse|carregando/i],
    ["erro", /role="alert"/],
    ["vazio real", /isVazioReal|vazio/],
    ["desligado no backend", /desligadoBackend/],
    ["janela invalida", /erroJanela/],
    ["stale", /freshness_status/],
  ] as const) {
    assert.match(CLIENT, regex as RegExp, `estado ausente: ${rotulo}`);
  }
});

test("janela vazia nao parece queda de vendas", () => {
  assert.match(CLIENT, /ausência de dado publicado/);
  // O JSX quebra a frase em duas linhas; normaliza o espaco em branco.
  const texto = CLIENT.replace(/\s+/g, " ");
  assert.match(texto, /não queda de vendas/);
  assert.ok(isVazioReal(payload({ by_class: [], daily: [] })));
  assert.ok(!isVazioReal(payload()));
});

test("resposta sem daily nao quebra a serie", () => {
  assert.deepEqual(serieDiaria(payload({ daily: [] }), "gmv"), []);
  assert.deepEqual(serieDiaria(null, "gmv"), []);
  assert.match(CLIENT, /Sem série diária publicada/);
});

test("snapshot stale gera alerta proprio", () => {
  const p = payload({
    meta: { ...payload().meta,
            freshness: { ...payload().meta.freshness,
                         freshness_status: "stale", closed_days_behind: 9 } },
  } as Partial<ShopeeFbsResponse>);
  const a = alertas(p);
  assert.ok(a.some((x) => x.includes("DESATUALIZADO") && x.includes("9 dias")));
});

// ---------------------------------------------------------------------------
// Seguranca e acessibilidade
// ---------------------------------------------------------------------------

test("zero PII no contrato e na tela", () => {
  for (const termo of ["order_sn", "buyer", "cpf", "recipient", "phone",
                       "email", "endereco", "external_seller"]) {
    assert.ok(!LIB.toLowerCase().includes(termo), `${termo} no contrato`);
    assert.ok(!CLIENT.toLowerCase().includes(termo), `${termo} na tela`);
  }
});

test("nenhuma mensagem tecnica ou SQL visivel", () => {
  for (const termo of ["SELECT ", "psycopg", "Traceback", "stack",
                       "postgresql://", "sqlalchemy"]) {
    assert.ok(!CLIENT.includes(termo), termo);
  }
});

test("controles tem alvo de 44px e foco visivel", () => {
  assert.match(CLIENT, /const CONTROLE_BASE =/);
  assert.match(CLIENT, /"min-h-11 px-3/);
  assert.match(CLIENT, /focus-visible:outline-2/);
  // A base NAO fixa cor: sobrepor cor a uma base colorida produziu
  // branco-sobre-branco no Gate FULL-1D-R/V.
  const base = CLIENT.split("const CONTROLE_BASE =")[1].split(";")[0];
  assert.ok(!/\bbg-/.test(base), "CONTROLE_BASE nao pode fixar cor de fundo");
  assert.ok(!/\btext-(white|slate-\d)/.test(base));
});

test("todo input e select tem label associado por id", () => {
  for (const id of ["sh-de", "sh-ate", "sh-marca", "sh-conta"]) {
    assert.ok(CLIENT.includes(`htmlFor="${id}"`), `label de ${id}`);
    assert.ok(CLIENT.includes(`id="${id}"`), `id ${id}`);
  }
});

test("grupos de botoes e grafico tem nome acessivel", () => {
  assert.match(CLIENT, /role="group" aria-label="Períodos predefinidos"/);
  assert.match(CLIENT, /role="group" aria-label="Métrica da série"/);
  assert.match(CLIENT, /role="img"/);
  assert.match(CLIENT, /aria-label=\{/);
  assert.match(CLIENT, /aria-pressed=\{metrica === m\.key\}/);
});

test("toda tabela tem caption e cabecalho com scope", () => {
  const captions = CLIENT.match(/<caption/g) ?? [];
  const tables = CLIENT.match(/<table/g) ?? [];
  assert.equal(captions.length, tables.length, "toda tabela precisa de caption");
  assert.ok((CLIENT.match(/scope="col"/g) ?? []).length >= 10);
  assert.ok((CLIENT.match(/scope="row"/g) ?? []).length >= 3);
});

test("tabelas largas rolam dentro do proprio container", () => {
  const blocos = CLIENT.split("<table").length - 1;
  assert.ok((CLIENT.match(/overflow-x-auto/g) ?? []).length >= blocos - 1);
});

test("o grafico tem alternativa textual navegavel", () => {
  assert.match(CLIENT, /<details/);
  assert.match(CLIENT, /Ver a série como tabela/);
});

test("ordenacao dos comparativos e deterministica", () => {
  assert.match(CLIENT, /\.sort\(\(a, b\) => \{/);
  assert.match(CLIENT, /localeCompare\(b\.nome, "pt-BR"\)/);
});

// ---------------------------------------------------------------------------
// Nao regressao do Full ML
// ---------------------------------------------------------------------------

test("/full-ml continua intacto e separado", () => {
  const ml = fonte("app/full-ml/page.tsx");
  assert.ok(ml.includes("Full Mercado Livre"));
  assert.ok(!ml.toLowerCase().includes("shopee"),
    "a tela do ML nao pode ter sido tocada");
  assert.ok(!CLIENT.includes("full-ml"));
  assert.ok(!CLIENT.includes("ml-fulfillment"));
  // As duas rotas coexistem na navegacao com a flag ligada.
  const hrefs = navSections({ shopeeFbs: true }).flatMap((s) => s.pages.map((p) => p.href));
  assert.ok(hrefs.includes("/full-ml") && hrefs.includes("/full-shopee"));
});

test("nenhuma rota existente foi removida ou renomeada", () => {
  const antes = NAV_SECTIONS.flatMap((s) => s.pages.map((p) => p.href));
  const depois = navSections({ shopeeFbs: true }).flatMap((s) => s.pages.map((p) => p.href));
  for (const h of antes) assert.ok(depois.includes(h), `rota sumiu: ${h}`);
  assert.equal(depois.length, antes.length + 1);
});


test("nenhum texto da tela nasce abaixo de 12px", () => {
  // Achado do QA em navegador real: os rotulos do eixo Y do grafico estavam
  // com fontSize 11, abaixo do piso de legibilidade da Torre.
  const fontes = [...CLIENT.matchAll(/fontSize="(\d+)"/g)].map((m) => Number(m[1]));
  for (const f of fontes) {
    assert.ok(f >= 12, `fontSize ${f} abaixo do piso de 12px`);
  }
  assert.ok(fontes.length > 0, "o grafico precisa declarar fontSize explicito");
  // Nenhuma classe utilitaria abaixo de text-xs (12px) na tela.
  assert.ok(!/text-\[(?:[0-9]|10|11)px\]/.test(CLIENT));
});

test("a flag e' lida por acesso LITERAL a process.env", () => {
  // O Next substitui `process.env.NEXT_PUBLIC_*` por analise estatica do
  // texto. Ler de uma variavel recebida por parametro nao e' substituido, e
  // no browser o valor vira `undefined` -- a flag ficava desligada no cliente
  // mesmo definida no build. Medido no Gate FULL-SH-1D.
  const flagSrc = fonte("src/lib/shopee-fbs-flag.ts");
  assert.match(flagSrc, /process\.env\.NEXT_PUBLIC_SHOPEE_FBS_ENABLED === VALOR_QUE_LIGA/);
  // O parametro existe SO' para teste e nao pode ser o caminho padrao.
  assert.ok(!/function shopeeFbsEnabled\(env: NodeJS\.ProcessEnv = process\.env\)/.test(flagSrc),
    "o default de parametro impede a substituicao estatica do Next");
});


// ---------------------------------------------------------------------------
// Flags independentes — Gate FULL-SH-1D-R/V
// ---------------------------------------------------------------------------

test("as duas flags sao independentes: a matriz completa", () => {
  // O risco que isto trava: duas funcoes separadas partindo de NAV_SECTIONS se
  // ignoram, e ligar uma frente esconderia a outra. Uma funcao so', com as
  // duas flags, faz elas COMPOREM.
  const href = (f: Parameters<typeof navSections>[0]) =>
    navSections(f).flatMap((s) => s.pages.map((p) => p.href));

  const nenhuma = href({ expedicao: false, shopeeFbs: false });
  assert.ok(!nenhuma.includes("/expedicao"));
  assert.ok(!nenhuma.includes("/full-shopee"));
  assert.ok(nenhuma.includes("/full-ml"), "Full ML nao depende de flag");

  const soExp = href({ expedicao: true, shopeeFbs: false });
  assert.ok(soExp.includes("/expedicao"), "Expedicao precisa aparecer");
  assert.ok(!soExp.includes("/full-shopee"),
    "ligar a Expedicao NAO pode revelar o Full Shopee");

  const soShopee = href({ expedicao: false, shopeeFbs: true });
  assert.ok(soShopee.includes("/full-shopee"));
  assert.ok(!soShopee.includes("/expedicao"),
    "ligar o Full Shopee NAO pode revelar a Expedicao");

  const ambas = href({ expedicao: true, shopeeFbs: true });
  assert.ok(ambas.includes("/expedicao") && ambas.includes("/full-shopee"));
  assert.equal(ambas.length, nenhuma.length + 2, "dois itens distintos");
  assert.equal(new Set(ambas).size, ambas.length, "nenhum item duplicado");
});

test("navSections aceita o booleano legado da Expedicao", () => {
  // O chamador original passava um unico booleano. Quebrar essa forma seria
  // remover funcionalidade da Expedicao para resolver o conflito.
  const legado = navSections(true).flatMap((s) => s.pages.map((p) => p.href));
  assert.ok(legado.includes("/expedicao"));
  assert.ok(!legado.includes("/full-shopee"));
  assert.deepEqual(navSections(false), NAV_SECTIONS);
  assert.deepEqual(navSections(), NAV_SECTIONS);
});

test("getRouteTitle reconhece as DUAS rotas novas", () => {
  const nav = fonte("src/components/shell/nav-config.ts");
  assert.match(nav, /navSections\(\{ expedicao: true, shopeeFbs: true \}\)/);
  // E o titulo resolve de fato.
  assert.equal(getRouteTitle("/full-shopee"), "Full Shopee");
  assert.equal(getRouteTitle("/expedicao"), "Expedição Shopee");
  assert.equal(getRouteTitle("/full-ml"), "Full Mercado Livre");
});

test("a Expedicao continua registrada e intacta", () => {
  // Prova de que a resolucao do conflito nao removeu funcionalidade alheia.
  const pkg = JSON.parse(fonte("package.json"));
  const testes = pkg.scripts.test.split(" ").filter((t: string) => t.startsWith("tests/"));
  assert.ok(testes.includes("tests/expedicao.test.ts"), "teste da Expedicao sumiu");
  assert.ok(testes.includes("tests/shopee-fbs.test.ts"));
  assert.equal(new Set(testes).size, testes.length, "teste duplicado no script");

  const nav = fonte("src/components/shell/nav-config.ts");
  assert.match(nav, /EXPEDICAO_NAV/);
  assert.match(nav, /href: "\/expedicao"/);
  const navlist = fonte("src/components/shell/NavList.tsx");
  assert.match(navlist, /expedicaoHabilitado\(process\.env\.NEXT_PUBLIC_EXPEDICAO_ENABLED\)/);
});
