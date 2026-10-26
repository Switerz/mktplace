/**
 * ML-FULL-STOCK-API-1 — contrato da tela "Estoque Full Mercado Livre".
 *
 * Sem DOM, no padrao de `estoque-full.test.ts`: regras puras + inspecao do
 * fonte. Protege: flag fail-closed e propria, guard no servidor, ausencia
 * nunca desenhada como zero, e a tela nao confundida com `/full-ml`
 * (modalidade de envio).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import {
  CLASSIFICACOES_ML,
  CLASS_LABEL_ML,
  FILTROS_ML_VAZIOS,
  PROXIMO_PASSO_ML,
  SEM_DADO,
  TITULO_INDISPONIVEL_ML,
  buildQueryML,
  coberturaMarcasLabel,
  eDataHistorica,
  fmtFracao,
  fmtOpcional,
  fotografiaVelhaML,
  frescorLabelML,
  isIndisponivelML,
  leituraLabel,
  temFiltroAtivoML,
  type FrescorML,
  type MLFullEstoqueResponse,
  type MotivoIndisponivelML,
} from "../src/lib/ml-full-estoque.ts";
import {
  ML_FULL_ESTOQUE_FLAG,
  mlFullEstoqueEnabled,
} from "../src/lib/ml-full-estoque-flag.ts";
import {
  ESTOQUE_FULL_NAV,
  ML_FULL_ESTOQUE_NAV,
  getRouteTitle,
  isNavItemActive,
  navSections,
} from "../src/components/shell/nav-config.ts";

const RAIZ = join(import.meta.dirname, "..");
const CLIENT_SRC = readFileSync(
  join(RAIZ, "app", "estoque-ml-full", "MLFullEstoqueClient.tsx"),
  "utf8",
);
const PAGE_SRC = readFileSync(
  join(RAIZ, "app", "estoque-ml-full", "page.tsx"),
  "utf8",
);
const API_CLIENT_SRC = readFileSync(
  join(RAIZ, "src", "lib", "api-client.ts"),
  "utf8",
);

function frescor(over: Partial<FrescorML> = {}): FrescorML {
  return {
    ref_date: "2026-10-25",
    ultima_fotografia_disponivel: "2026-10-25",
    published_at: "2026-10-25T20:00:00Z",
    dias_desde_a_fotografia: 1,
    horas_desde_a_publicacao: 6,
    publicacao_agendada: false,
    ...over,
  };
}

// ---------------------------------------------------------------------------
// Flag e navegacao
// ---------------------------------------------------------------------------

test("flag e' fail-closed e propria", () => {
  const liga = (v: string | undefined) =>
    mlFullEstoqueEnabled({ NEXT_PUBLIC_ML_FULL_STOCK_ENABLED: v } as NodeJS.ProcessEnv);
  assert.equal(liga("true"), true);
  for (const v of ["", "1", "TRUE", "yes", "false", undefined]) {
    assert.equal(liga(v), false, `"${v}" nao pode ligar a tela`);
  }
  assert.equal(ML_FULL_ESTOQUE_FLAG, "NEXT_PUBLIC_ML_FULL_STOCK_ENABLED");
  // Ligar o estoque Shopee nao liga o estoque ML.
  assert.equal(
    mlFullEstoqueEnabled({
      NEXT_PUBLIC_SHOPEE_FBS_STOCK_ENABLED: "true",
    } as NodeJS.ProcessEnv),
    false,
  );
});

test("a rota tem guard no servidor", () => {
  assert.match(PAGE_SRC, /mlFullEstoqueEnabled\(\)/);
  assert.match(PAGE_SRC, /notFound\(\)/);
});

test("o menu responde so' a propria flag e nao colide com /full-ml nem /estoque-full", () => {
  const sem = navSections({ estoqueFull: true, shopeeFbs: true, expedicao: true })
    .flatMap((s) => s.pages.map((p) => p.href));
  assert.ok(!sem.includes(ML_FULL_ESTOQUE_NAV.href));

  const com = navSections({ mlFullEstoque: true, estoqueFull: true });
  const paginas = com.flatMap((s) => s.pages);
  assert.ok(paginas.some((p) => p.href === "/estoque-ml-full"));
  const rotulos = paginas.map((p) => p.label);
  assert.equal(new Set(rotulos).size, rotulos.length, "rotulo duplicado");

  // `isNavItemActive` casa por prefixo: nenhuma rota vizinha pode acender junto.
  for (const vizinha of ["/full-ml", ESTOQUE_FULL_NAV.href]) {
    assert.equal(isNavItemActive(vizinha, ML_FULL_ESTOQUE_NAV.href), false);
    assert.equal(isNavItemActive(ML_FULL_ESTOQUE_NAV.href, vizinha), false);
  }
  assert.equal(getRouteTitle("/estoque-ml-full"), "Estoque Full Mercado Livre");
});

test("o cliente chama a rota de estoque, nao a de modalidade de envio", () => {
  const trecho = API_CLIENT_SRC.split("export async function fetchMLFullEstoque")[1] ?? "";
  assert.ok(trecho.includes("/api/v1/performance/ml-full-estoque?"));
  assert.ok(!trecho.split("\n}\n")[0].includes("ml-fulfillment"));
});

// ---------------------------------------------------------------------------
// Ausencia nao e' zero
// ---------------------------------------------------------------------------

test("cada motivo de indisponibilidade tem titulo e proximo passo", () => {
  const motivos: MotivoIndisponivelML[] = [
    "feature_flag_desligada",
    "fato_inexistente",
    "sem_fotografia_publicada",
    "sem_fotografia_na_data",
  ];
  for (const m of motivos) {
    assert.ok(TITULO_INDISPONIVEL_ML[m]);
    assert.ok(PROXIMO_PASSO_ML[m]);
  }
  assert.match(PROXIMO_PASSO_ML.fato_inexistente, /029/);
});

test("isIndisponivelML decide pelo status", () => {
  const p: MLFullEstoqueResponse = {
    status: "unavailable",
    marketplace: "ml",
    unavailable_reason: "x",
    motivo_tecnico: "fato_inexistente",
    scope_label: "s",
  };
  assert.equal(isIndisponivelML(p), true);
});

test("null vira travessao e zero continua zero", () => {
  assert.equal(fmtOpcional(null), SEM_DADO);
  assert.equal(fmtOpcional(0), "0");
  assert.equal(fmtFracao(null), SEM_DADO);
  assert.equal(fmtFracao("0.9800"), "98,0%");
});

test("toda classificacao, inclusive SEM_LEITURA e FORA_DO_FULL, tem rotulo", () => {
  for (const c of CLASSIFICACOES_ML) assert.ok(CLASS_LABEL_ML[c]);
  assert.equal(CLASS_LABEL_ML.SEM_LEITURA, "Sem leitura");
});

test("o componente nao coalesce ausencia para zero e declara os quatro estados", () => {
  assert.ok(!CLIENT_SRC.includes("?? 0"));
  assert.ok(!CLIENT_SRC.includes("|| 0"));
  assert.ok(CLIENT_SRC.includes("fmtOpcional(i.disponivel)"));
  assert.ok(CLIENT_SRC.includes("fmtOpcional(m.disponivel_full)"));
  assert.ok(CLIENT_SRC.includes("Carregando estoque"));
  assert.ok(CLIENT_SRC.includes('role="alert"'));
  assert.ok(CLIENT_SRC.includes("Tentar novamente"));
  assert.ok(CLIENT_SRC.includes("PainelIndisponivel"));
  assert.ok(CLIENT_SRC.includes("Nenhum inventário neste filtro"));
  assert.match(CLIENT_SRC, /não significa estoque zero/);
  assert.ok((CLIENT_SRC.split("catch (e)")[1] ?? "").includes("setPayload(null)"));
});

test("frescor, cobertura e parcialidade ficam visiveis", () => {
  assert.ok(CLIENT_SRC.includes("frescorLabelML(payload.frescor)"));
  assert.ok(CLIENT_SRC.includes("coberturaMarcasLabel(payload.cobertura)"));
  assert.ok(CLIENT_SRC.includes("leituraLabel(payload.cobertura.leitura)"));
  assert.ok(CLIENT_SRC.includes("marcas_sem_fotografia"));
  assert.ok(CLIENT_SRC.includes("total_parcial"));
  assert.ok(CLIENT_SRC.includes("motivos_total_parcial"));
});

test("a tela nao soma estoque por anuncio", () => {
  assert.ok(!/disponivel\s*\*\s*i\.anuncios/.test(CLIENT_SRC));
  assert.ok(!/anuncios\s*\*/.test(CLIENT_SRC));
  assert.ok(!CLIENT_SRC.includes(".reduce("), "a tela nao recalcula totais");
});

test("rotulos de frescor e cobertura", () => {
  assert.equal(
    frescorLabelML(frescor()),
    "Fotografia de 25/10/2026 (ontem) · publicada há 6,0 h",
  );
  assert.equal(fotografiaVelhaML(frescor({ dias_desde_a_fotografia: 2 })), true);
  assert.equal(fotografiaVelhaML(frescor()), false);
  assert.equal(eDataHistorica(frescor({ ref_date: "2026-10-23" })), true);
  assert.equal(
    coberturaMarcasLabel({
      marcas_esperadas: ["barbours", "kokeshi", "lescent", "rituaria"],
      marcas_com_fotografia: ["barbours", "lescent", "rituaria"],
      marcas_sem_fotografia: ["kokeshi"],
      leitura: { inventarios: 7, inventarios_lidos: 5, inventarios_sem_leitura: 2 },
    }),
    "3 de 4 marcas com fotografia no dia",
  );
  assert.equal(
    leituraLabel({ inventarios: 7, inventarios_lidos: 5, inventarios_sem_leitura: 2 }),
    "5 de 7 inventários lidos · 2 sem leitura",
  );
});

test("buildQueryML: marca repetida, data valida, lista vazia omitida", () => {
  assert.equal(buildQueryML(FILTROS_ML_VAZIOS), "");
  const qs = new URLSearchParams(
    buildQueryML({ brands: ["barbours", "kokeshi"], refDate: "2026-10-23", incluirForaDoFull: true }),
  );
  assert.deepEqual(qs.getAll("brands"), ["barbours", "kokeshi"]);
  assert.equal(qs.get("ref_date"), "2026-10-23");
  assert.equal(qs.get("incluir_fora_do_full"), "true");
  assert.equal(buildQueryML({ ...FILTROS_ML_VAZIOS, refDate: "lixo" }), "");
  assert.equal(temFiltroAtivoML(FILTROS_ML_VAZIOS), false);
});
