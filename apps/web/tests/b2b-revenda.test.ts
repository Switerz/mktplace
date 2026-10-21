/**
 * Gate TORRE-B2B-OCTAPRICE — contrato da aba "B2B / Revendedores".
 *
 * Sem DOM: regras puras + inspecao do fonte, como as demais telas da Torre.
 *
 * O que estes testes existem para impedir:
 *  - indisponivel desenhado como zero ou como vazio;
 *  - a aba aparecer (ou dado ficticio aparecer como real) sem a flag certa;
 *  - o navegador falar com a Torre B2B, ou a aba exibir dado de cliente;
 *  - fixture incoerente validando uma tela impossivel.
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import {
  AVISO_FIXTURE,
  FILTROS_VAZIOS,
  SEM_DADO,
  TITULO_INDISPONIVEL,
  buildQuery,
  fmtData,
  fmtDesvio,
  fmtInstante,
  fmtMoeda,
  fmtPct,
  isIndisponivel,
  participacao,
  resumoVazio,
  situacaoLabel,
  temFiltro,
  type ResumoOk,
} from "../src/lib/b2b-revenda.ts";
import { B2B_REVENDA_FLAG, modoB2BRevenda, modoDoValor } from "../src/lib/b2b-revenda-flag.ts";
import {
  FIXTURE_ESCOPO,
  FIXTURE_FRESCOR,
  FIXTURE_INDISPONIVEL,
  fixtureCanaisMarcas,
  fixtureResumo,
  fixtureSkus,
  painelFicticio,
} from "../src/lib/b2b-revenda-fixtures.ts";

const RAIZ = join(import.meta.dirname, "..");
const ler = (rel: string) => readFileSync(join(RAIZ, rel), "utf-8");
const CLIENT = ler("app/monitoramento-preco/revendedores/RevendedoresClient.tsx");
const PAGE = ler("app/monitoramento-preco/revendedores/page.tsx");
const LAYOUT = ler("app/monitoramento-preco/layout.tsx");
const NAV = ler("app/monitoramento-preco/VisaoMonitoramentoNav.tsx");
const LIB = ler("src/lib/b2b-revenda.ts");
const FIXTURES = ler("src/lib/b2b-revenda-fixtures.ts");
const SCHEMA_PY = readFileSync(join(RAIZ, "..", "api", "app", "schemas", "b2b_revenda.py"), "utf-8");

// ---------------------------------------------------------------------------
// Flag
// ---------------------------------------------------------------------------
test("flag: so' os valores EXATOS ligam; o resto e' off", () => {
  assert.equal(B2B_REVENDA_FLAG, "NEXT_PUBLIC_B2B_REVENDA_MODE");
  assert.equal(modoDoValor(undefined), "off");
  for (const v of ["", "true", "1", "on", "FIXTURE", "Api", " api", "fixture "]) {
    assert.equal(modoDoValor(v), "off", `"${v}" nao pode ligar`);
  }
  assert.equal(modoDoValor("fixture"), "fixture");
  assert.equal(modoDoValor("api"), "api");
  assert.equal(modoB2BRevenda({ NEXT_PUBLIC_B2B_REVENDA_MODE: "api" } as NodeJS.ProcessEnv), "api");
  assert.equal(modoB2BRevenda({} as NodeJS.ProcessEnv), "off");
});

test("flag: acesso LITERAL a process.env (o Next substitui em build)", () => {
  const src = ler("src/lib/b2b-revenda-flag.ts");
  assert.match(src, /process\.env\.NEXT_PUBLIC_B2B_REVENDA_MODE\)/);
});

test("rota: 404 no servidor com a flag desligada", () => {
  assert.match(PAGE, /if \(modo === "off"\) notFound\(\);/);
});

test("layout: com a flag desligada devolve SO' children (lojas proprias intacta)", () => {
  assert.match(LAYOUT, /if \(modoB2BRevenda\(\) === "off"\) return <>\{children\}<\/>;/);
});

test("seletor: duas visoes, por link, com aria-current e alvo de 44px", () => {
  assert.match(NAV, /href: "\/monitoramento-preco", texto: "Lojas próprias"/);
  assert.match(NAV, /href: "\/monitoramento-preco\/revendedores", texto: "B2B \/ Revendedores"/);
  assert.match(NAV, /aria-current=\{ativa \? "page" : undefined\}/);
  assert.match(NAV, /min-h-11/);
});

// ---------------------------------------------------------------------------
// Fixture: nunca com cara de real
// ---------------------------------------------------------------------------
test("modo fixture: faixa de aviso e nenhuma requisicao", () => {
  assert.match(CLIENT, /modo === "fixture" && \(/);
  assert.match(CLIENT, /\{AVISO_FIXTURE\}/);
  assert.match(AVISO_FIXTURE, /fictícios/);
  // O ramo fixture sai antes de `carregarPainel`.
  const ramo = CLIENT.slice(CLIENT.indexOf('if (modo === "fixture") {'), CLIENT.indexOf("carregarPainel(f, ac.signal)"));
  assert.match(ramo, /painelFicticio\(f\.data\)/);
  assert.match(ramo, /return;/);
});

// ---------------------------------------------------------------------------
// Fronteira: navegador -> nossa API, nunca a Torre B2B
// ---------------------------------------------------------------------------
test("o navegador so' fala com a nossa API", () => {
  for (const src of [CLIENT, LIB, FIXTURES, PAGE, LAYOUT]) {
    assert.doesNotMatch(src.toLowerCase(), /supabase|service_role|eyjhbgcio|sb_publishable|sb_secret/);
  }
  assert.doesNotMatch(CLIENT, /fetch\(/, "a tela nao faz fetch direto: usa carregarPainel");
  assert.match(LIB, /const BASE = "\/api\/v1\/b2b-revenda";/);
  assert.match(LIB, /process\.env\.NEXT_PUBLIC_API_URL/);
});

/** Todo arquivo de codigo do frontend (app/, src/, configs na raiz do web). */
function arquivosDoFrontend(): string[] {
  const saida: string[] = [];
  const andar = (dir: string) => {
    for (const nome of readdirSync(dir)) {
      if (nome === "node_modules" || nome === ".next" || nome === "tests") continue;
      const p = join(dir, nome);
      if (statSync(p).isDirectory()) andar(p);
      else if (/\.(ts|tsx|js|jsx|mjs|cjs|json)$/.test(nome) && nome !== "package-lock.json") saida.push(p);
    }
  };
  andar(join(RAIZ, "app"));
  andar(join(RAIZ, "src"));
  for (const raiz of readdirSync(RAIZ)) {
    if (/^next\.config|^\.env/.test(raiz)) saida.push(join(RAIZ, raiz));
  }
  return saida;
}

test("nenhuma variavel secreta da integracao e' referenciada no frontend", () => {
  // So' `NEXT_PUBLIC_B2B_REVENDA_MODE` pode existir no web. URL e chave da
  // Torre B2B vivem SO' no backend; qualquer referencia aqui iria para o bundle.
  const proibido =
    /B2B_REVENDA_(ENABLED|SUPABASE|DATABASE|TIMEOUT|CACHE|CANAIS)|SUPABASE_(KEY|URL|SERVICE|ANON|SECRET|PUBLISHABLE)|service_role|sb_secret_|sb_publishable_/;
  const arquivos = arquivosDoFrontend();
  assert.ok(arquivos.length > 50, "varredura nao pode passar vazia");
  const achados = arquivos.filter((p) => proibido.test(readFileSync(p, "utf-8")));
  assert.deepEqual(achados, []);
});

test("sem dado de cliente nos tipos da aba", () => {
  const props = [...LIB.matchAll(/^\s+(\w+)\??:/gm)].map((m) => m[1]);
  const proibidos = props.filter((p) => /cliente|vendedor|cnpj|email|telefone|razao|seller_n/i.test(p));
  assert.deepEqual(proibidos, []);
});

// ---------------------------------------------------------------------------
// Contrato com a API (cruzado com o schema Python)
// ---------------------------------------------------------------------------
test("todo motivo de indisponibilidade da API tem titulo na tela", () => {
  const bloco = SCHEMA_PY.slice(SCHEMA_PY.indexOf("MotivoTecnico = Literal["), SCHEMA_PY.indexOf("]", SCHEMA_PY.indexOf("MotivoTecnico = Literal[")));
  const daApi = [...bloco.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]).sort();
  assert.ok(daApi.length >= 10);
  assert.deepEqual(Object.keys(TITULO_INDISPONIVEL).sort(), daApi);
});

test("buildQuery: vazio sem filtro; nomes dos parametros da API", () => {
  assert.equal(buildQuery(FILTROS_VAZIOS), "");
  const q = new URLSearchParams(buildQuery({ data: "2026-10-20", marca: "AP", canal: "Shopee", severidade: "grave" }, { limite: "10" }));
  assert.equal(q.get("data"), "2026-10-20");
  assert.equal(q.get("marcas"), "AP");
  assert.equal(q.get("canais"), "Shopee");
  assert.equal(q.get("severidades"), "grave");
  assert.equal(q.get("limite"), "10");
  assert.equal(temFiltro(FILTROS_VAZIOS), false);
  assert.equal(temFiltro({ ...FILTROS_VAZIOS, marca: "BB" }), true);
});

// ---------------------------------------------------------------------------
// Estados: indisponivel != vazio != zero
// ---------------------------------------------------------------------------
test("indisponivel nunca e' vazio", () => {
  assert.equal(isIndisponivel(FIXTURE_INDISPONIVEL), true);
  assert.equal(resumoVazio(FIXTURE_INDISPONIVEL), false);
  assert.equal(isIndisponivel(fixtureResumo()), false);
  assert.equal(resumoVazio(fixtureResumo()), false);
  const zerado: ResumoOk = { ...fixtureResumo(), kpis: { ...fixtureResumo().kpis, anuncios: 0 } };
  assert.equal(resumoVazio(zerado), true);
  assert.equal(isIndisponivel(null), false);
});

test("tela trata indisponivel ANTES de vazio e de numero", () => {
  const i = CLIENT.indexOf("if (isIndisponivel(resumo)) return <BlocoIndisponivel");
  const v = CLIENT.indexOf("if (resumoVazio(resumo))");
  const k = CLIENT.indexOf("<FaixaKpis");
  assert.ok(i > 0 && v > i && k > v);
});

test("dia fora das datas: o ficticio responde indisponivel, como a API", () => {
  const p = painelFicticio("2026-01-01");
  assert.equal(isIndisponivel(p.resumo) && p.resumo.motivo_tecnico, "sem_relatorio_na_data");
  assert.equal(isIndisponivel(p.frescor), false);
});

// ---------------------------------------------------------------------------
// Fixtures coerentes
// ---------------------------------------------------------------------------
test("fixtures: Amazon fora dos KPIs e listada como excluida", () => {
  assert.ok(!FIXTURE_ESCOPO.canais_nos_kpis.includes("Amazon"));
  assert.deepEqual(FIXTURE_ESCOPO.canais_excluidos.map((c) => c.canal), ["Amazon"]);
  for (const l of fixtureCanaisMarcas().linhas) {
    assert.ok(l.canal && FIXTURE_ESCOPO.canais_nos_kpis.includes(l.canal), `canal ${l.canal} fora do escopo`);
  }
});

test("fixtures: totais batem entre resumo, severidade, canal x marca e cobertura", () => {
  const r = fixtureResumo();
  const k = r.kpis;
  const sev = k.severidade.leve + k.severidade.media + k.severidade.grave;
  assert.equal(sev, k.anuncios_violando);
  assert.ok(k.anuncios_violando <= k.anuncios);
  assert.ok(k.buy_box.violando_com_buy_box <= k.buy_box.anuncios_com_buy_box);
  const linhas = fixtureCanaisMarcas().linhas;
  assert.equal(linhas.reduce((a, l) => a + l.anuncios, 0), k.anuncios);
  assert.equal(linhas.reduce((a, l) => a + l.anuncios_violando, 0), k.anuncios_violando);
  const c = r.cobertura_identificacao;
  assert.equal(c.sellers_violando, k.sellers_violando);
  assert.equal(c.anuncios_violando, k.anuncios_violando);
  assert.ok(c.sellers_violando_identificados <= c.sellers_violando);
  assert.ok(c.anuncios_violando_identificados <= c.anuncios_violando);
  for (const s of fixtureSkus().linhas) assert.ok(s.anuncios_violando <= s.anuncios && s.anuncios_violando > 0);
  assert.equal(FIXTURE_FRESCOR.datas_disponiveis[0], FIXTURE_FRESCOR.data_ref_mais_recente);
});

// ---------------------------------------------------------------------------
// Formatacao
// ---------------------------------------------------------------------------
test("desvio: fracao vira percentual com sinal tipografico", () => {
  assert.equal(fmtDesvio("-0.087"), "−8,7%");
  assert.equal(fmtDesvio("0.1"), "+10,0%");
  assert.equal(fmtDesvio("0"), "0,0%");
  assert.equal(fmtDesvio(null), SEM_DADO);
  assert.equal(fmtDesvio("abc"), SEM_DADO);
});

test("percentual, moeda e data", () => {
  assert.equal(fmtPct("21.7"), "21,7%");
  assert.equal(fmtPct(null), SEM_DADO);
  assert.equal(fmtMoeda("59.9").replace(/\s/g, " "), "R$ 59,90");
  assert.equal(fmtData("2026-10-21"), "21/10/2026");
  assert.equal(fmtData(null), SEM_DADO);
});

test("instante sempre em horario de Brasilia", () => {
  assert.equal(fmtInstante("2026-10-21T11:42:00+00:00"), "21/10/2026, 08:42");
  assert.equal(fmtInstante(null), SEM_DADO);
  assert.equal(fmtInstante("lixo"), SEM_DADO);
});

test("situacao do frescor e participacao", () => {
  assert.equal(situacaoLabel(FIXTURE_FRESCOR), "Em dia");
  assert.equal(situacaoLabel({ ...FIXTURE_FRESCOR, situacao: "atrasado", dias_de_atraso: 1 }), "Atrasado 1 dia");
  assert.equal(situacaoLabel({ ...FIXTURE_FRESCOR, situacao: "atrasado", dias_de_atraso: 3 }), "Atrasado 3 dias");
  assert.equal(participacao(30, 240), 12.5);
  assert.equal(participacao(1, 0), 0);
});

test("canal do escopo sem anuncio na carga e' sinalizado, nao some em silencio", () => {
  assert.match(CLIENT, /escopo\.canais_kpi_sem_dados\.length > 0 && \(/);
  assert.match(CLIENT, /Os KPIs abaixo não incluem esse canal/);
  assert.deepEqual(FIXTURE_ESCOPO.canais_kpi_sem_dados, []);
});

test("tela mostra coleta, importacao e consulta, e o aviso de piso", () => {
  for (const trecho of ["fmtInstante(frescor.coleta_fim)", "fmtInstante(frescor.importado_fim)", "fmtInstante(frescor.consultado_em)", "escopo.aviso_piso", "Piso: PMA do OctaPrice."]) {
    assert.ok(CLIENT.includes(trecho), `falta ${trecho}`);
  }
});
