// T-0014 — tela "Performance das contas" (contrato: docs/performance_contas_contrato.md).
// Roda via `node --test` com type-stripping nativo do Node.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  PERFORMANCE_CONTAS_FLAG,
  carregarPerformanceContas,
  estadoEfetivo,
  estadoParaStateBlock,
  filtrarCanais,
  formatarValor,
  habilitado,
  lerFiltros,
  limiarExibivel,
  montarCanais,
  montarIndicador,
  problemaNoPayload,
  rotuloOrigemLimiar,
  rotuloTipo,
  tituloEstado,
  tomDoValor,
  urlPerformanceContas,
  type CanalPerformance,
  type EstadoIndicador,
  type Indicador,
  type PerformanceContasResponse,
} from "../src/lib/performance-contas.ts";

const AQUI = dirname(fileURLToPath(import.meta.url));
const ESTADOS: EstadoIndicador[] = ["dado", "vazio_legitimo", "sem_fonte", "nao_comprovado", "erro"];

function indicador(parcial: Partial<Indicador> = {}): Indicador {
  return {
    codigo: "T1",
    rotulo: "Taxa de cancelamento",
    tipo: "torre",
    estado: "dado",
    valor: 4.5,
    unidade: "pct",
    janela: "mês corrente",
    limiar: null,
    fonte: "marts.fact_marketplace_daily_performance",
    atualizado_em: "2026-10-28T09:00:00Z",
    aviso_frescor: null,
    cobertura: "4 marcas",
    ajuda: "Todos os cancelamentos, qualquer autor.",
    motivo_indisponivel: null,
    ...parcial,
  };
}

// ---------------------------------------------------------------------------
// Flag
// ---------------------------------------------------------------------------

test("flag: nome exato da variavel publica", () => {
  assert.equal(PERFORMANCE_CONTAS_FLAG, "NEXT_PUBLIC_PERFORMANCE_CONTAS_ENABLED");
});

test("flag: so' liga com a string exata 'true'", () => {
  assert.equal(habilitado({ NEXT_PUBLIC_PERFORMANCE_CONTAS_ENABLED: "true" }), true);
  for (const v of [undefined, "", "1", "TRUE", "True", "yes", "on", " true", "true "]) {
    assert.equal(habilitado({ NEXT_PUBLIC_PERFORMANCE_CONTAS_ENABLED: v }), false, `valor ${JSON.stringify(v)}`);
  }
  assert.equal(habilitado({}), false);
});

test("flag: acesso literal a process.env (substituicao em build do Next)", () => {
  const src = readFileSync(join(AQUI, "../src/lib/performance-contas.ts"), "utf8");
  assert.match(src, /process\.env\.NEXT_PUBLIC_PERFORMANCE_CONTAS_ENABLED === VALOR_QUE_LIGA/);
  assert.doesNotMatch(src, /process\.env\[/);
});

test("pagina: guard no servidor com notFound e force-dynamic, sem fixture", () => {
  const src = readFileSync(join(AQUI, "../app/performance-contas/page.tsx"), "utf8");
  assert.match(src, /if \(!habilitado\(\)\) notFound\(\);/);
  assert.match(src, /export const dynamic = "force-dynamic";/);
  // Nenhum import de dado de exemplo ("demonstrativo" aparece so' no comentario).
  assert.doesNotMatch(src, /mock-data|fixture/i);
});

// ---------------------------------------------------------------------------
// formatarValor: ausencia nunca vira zero
// ---------------------------------------------------------------------------

test("formatarValor: pct com virgula pt-BR so' quando estado=dado", () => {
  assert.equal(formatarValor(indicador({ valor: 4.5 })), "4,5%");
  assert.equal(formatarValor(indicador({ valor: 0.25 })), "0,25%");
  assert.equal(formatarValor(indicador({ unidade: "contagem", valor: 1234 })), "1.234");
});

test("formatarValor: zero REAL em estado dado continua zero (nao e' ausencia)", () => {
  assert.equal(formatarValor(indicador({ valor: 0 })), "0,0%");
});

test("formatarValor: null para valor nulo ou estado != dado, nunca '0' / '0%'", () => {
  const proibidos = new Set(["0", "0%", "0,0%", "0,00%"]);
  for (const estado of ESTADOS) {
    for (const valor of [null, 0, 3.2]) {
      for (const unidade of ["pct", "contagem", null] as const) {
        const r = formatarValor({ estado, valor, unidade });
        if (estado !== "dado" || valor === null) {
          assert.equal(r, null, `estado=${estado} valor=${valor} unidade=${unidade}`);
        }
        if (valor === null || estado !== "dado") assert.ok(r === null || !proibidos.has(r));
      }
    }
  }
});

test("formatarValor: NaN/Infinity/string nao viram numero", () => {
  for (const valor of [Number.NaN, Number.POSITIVE_INFINITY, "4.5" as unknown as number]) {
    assert.equal(formatarValor(indicador({ valor })), null);
  }
});

// ---------------------------------------------------------------------------
// Estados -> StateBlock
// ---------------------------------------------------------------------------

test("estadoParaStateBlock: mapeamento do contrato §4", () => {
  assert.equal(estadoParaStateBlock("dado"), null);
  assert.equal(estadoParaStateBlock("vazio_legitimo"), "vazio");
  assert.equal(estadoParaStateBlock("sem_fonte"), "indisponivel");
  assert.equal(estadoParaStateBlock("nao_comprovado"), "indisponivel");
  assert.equal(estadoParaStateBlock("erro"), "erro");
});

test("estadoParaStateBlock: estado desconhecido vira erro, nunca dado nem vazio", () => {
  assert.equal(estadoParaStateBlock("no_series_published"), "erro");
  assert.equal(estadoParaStateBlock(""), "erro");
});

test("estadoEfetivo: dado sem numero e estado desconhecido sao invalidos", () => {
  assert.equal(estadoEfetivo({ estado: "dado", valor: 1 }), "dado");
  assert.equal(estadoEfetivo({ estado: "dado", valor: null }), "invalido");
  assert.equal(estadoEfetivo({ estado: "xyz" as EstadoIndicador, valor: null }), "invalido");
  assert.equal(estadoEfetivo({ estado: "sem_fonte", valor: null }), "sem_fonte");
});

test("tituloEstado: textos minimos do contrato", () => {
  assert.equal(tituloEstado("vazio_legitimo", "últimos 60 dias"), "Nenhum registro em últimos 60 dias");
  assert.match(tituloEstado("nao_comprovado", null), /autorização não comprovada/);
  assert.match(tituloEstado("sem_fonte", null), /Indisponível/);
});

// ---------------------------------------------------------------------------
// Oficial x Torre
// ---------------------------------------------------------------------------

test("rotuloTipo: Torre e Oficial sao rotulos diferentes", () => {
  assert.equal(rotuloTipo("oficial"), "Oficial");
  assert.equal(rotuloTipo("torre"), "Cálculo Torre");
  assert.notEqual(rotuloTipo("torre"), rotuloTipo("oficial"));
});

test("rotuloTipo: valor inesperado nunca vira Oficial", () => {
  for (const t of ["", "OFICIAL", "Oficial", "official", "x"]) {
    assert.equal(rotuloTipo(t), "Cálculo Torre");
  }
});

test("limiar interno: origem escrita como referencia interna", () => {
  const l = limiarExibivel(
    indicador({ codigo: "LDR_TT", limiar: { valor: 4, origem: "interno", rotulo: "Referência de LDR" } }),
  );
  assert.ok(l);
  assert.equal(l.texto, "Referência de LDR: 4,0%");
  assert.match(l.origem, /referência interna/);
  assert.match(l.origem, /não é meta da plataforma/);
  assert.match(rotuloOrigemLimiar("interno"), /interna/);
  assert.doesNotMatch(rotuloOrigemLimiar("interno"), /oficial/);
});

test("limiar oficial ao lado de numero Torre e' suprimido", () => {
  const ind = indicador({ tipo: "torre", limiar: { valor: 0.5, origem: "oficial", rotulo: "Líder MLB" } });
  assert.equal(limiarExibivel(ind), null);
  const oficial = indicador({ tipo: "oficial", limiar: { valor: 0.5, origem: "oficial", rotulo: "Líder MLB" } });
  assert.match(limiarExibivel(oficial)?.origem ?? "", /oficial/);
});

test("numero Torre nunca recebe cor de severidade", () => {
  for (const estado of ESTADOS) {
    const tom = tomDoValor(indicador({ estado, valor: 99 }));
    assert.ok(tom === "neutro" || tom === "ausente");
  }
  assert.equal(tomDoValor(indicador({ estado: "sem_fonte", valor: null })), "ausente");
});

// ---------------------------------------------------------------------------
// Filtros, payload e fetch
// ---------------------------------------------------------------------------

test("lerFiltros: allowlist de canal e marca; resto vira 'todos'", () => {
  const marcas = ["apice", "barbours"];
  assert.deepEqual(lerFiltros({ canal: "shopee", brands: "apice" }, marcas), { canal: "shopee", marca: "apice" });
  assert.deepEqual(lerFiltros({ canal: "<script>", brands: "drop" }, marcas), { canal: null, marca: null });
  assert.deepEqual(lerFiltros({ canal: ["tiktok", "shopee"] }, marcas), { canal: "tiktok", marca: null });
});

test("filtrarCanais: null devolve todos; canal recorta", () => {
  const canais = (["mercado_livre", "shopee", "tiktok"] as const).map(
    (canal): CanalPerformance => ({ canal, cobertura: { marcas: [], fora: [] }, indicadores: [] }),
  );
  assert.equal(filtrarCanais(canais, null).length, 3);
  assert.deepEqual(filtrarCanais(canais, "shopee").map((c) => c.canal), ["shopee"]);
});

test("urlPerformanceContas: rota do contrato e brands codificado", () => {
  assert.equal(urlPerformanceContas("http://x", { marca: null }), "http://x/api/v1/performance-contas");
  assert.equal(urlPerformanceContas("http://x", { marca: "apice" }), "http://x/api/v1/performance-contas?brands=apice");
});

test("problemaNoPayload: aceita o contrato e recusa formas quebradas", () => {
  const ok = {
    gerado_em: "2026-10-29T10:00:00Z",
    canais: [{ canal: "shopee", cobertura: { marcas: ["apice"], fora: [] }, indicadores: [indicador()] }],
  };
  assert.equal(problemaNoPayload(ok), null);
  assert.ok(problemaNoPayload(null));
  assert.ok(problemaNoPayload({ gerado_em: "x" }));
  assert.ok(problemaNoPayload({ ...ok, canais: [{ ...ok.canais[0], canal: "amazon" }] }));
});

// Parecer R20: o payload precisa ser validado em profundidade. `fora: [null]`
// passava e a pagina quebrava com TypeError em `f.marca`.
function payloadValido() {
  return {
    gerado_em: "2026-10-29T10:00:00Z",
    canais: [
      {
        canal: "shopee",
        cobertura: { marcas: ["apice"], fora: [{ marca: "barbours", motivo: "sem conta conectada" }] },
        indicadores: [
          indicador(),
          indicador({ codigo: "SPS", tipo: "oficial", estado: "sem_fonte", valor: null, unidade: null }),
          indicador({
            codigo: "LDR",
            limiar: { valor: 4, origem: "interno", rotulo: "Referência de LDR" },
          }),
        ],
      },
    ],
  };
}

// Cada mutacao recebe uma copia nova e a deixa quebrada (via `unknown`, de
// proposito: o tipo do contrato nao permite essas formas).
type Bruto = { canais: { cobertura: Record<string, unknown>; indicadores: Record<string, unknown>[] }[] } & Record<
  string,
  unknown
>;
const QUEBRADOS: Record<string, (p: Bruto) => void> = {
  "fora=[null]": (p) => void (p.canais[0].cobertura.fora = [null]),
  "fora=[{}]": (p) => void (p.canais[0].cobertura.fora = [{}]),
  "fora sem motivo": (p) => void (p.canais[0].cobertura.fora = [{ marca: "apice" }]),
  "fora nao-array": (p) => void (p.canais[0].cobertura.fora = { marca: "apice", motivo: "x" }),
  "marcas=[1]": (p) => void (p.canais[0].cobertura.marcas = [1]),
  "marcas=[null]": (p) => void (p.canais[0].cobertura.marcas = [null]),
  "cobertura null": (p) => void ((p.canais[0] as Record<string, unknown>).cobertura = null),
  "canais nao-array": (p) => void ((p as Record<string, unknown>).canais = { shopee: {} }),
  "canais=[null]": (p) => void ((p as Record<string, unknown>).canais = [null]),
  "indicador null": (p) => void ((p.canais[0] as Record<string, unknown>).indicadores = [null]),
  "indicador sem estado": (p) => void delete p.canais[0].indicadores[0].estado,
  "estado fora do dominio": (p) => void (p.canais[0].indicadores[0].estado = "no_series_published"),
  "tipo fora do dominio": (p) => void (p.canais[0].indicadores[0].tipo = "OFICIAL"),
  "sem codigo": (p) => void delete p.canais[0].indicadores[0].codigo,
  "valor string": (p) => void (p.canais[0].indicadores[0].valor = "4.5"),
  "unidade fora do dominio": (p) => void (p.canais[0].indicadores[0].unidade = "brl"),
  "fonte ausente": (p) => void delete p.canais[0].indicadores[0].fonte,
  "ajuda objeto": (p) => void (p.canais[0].indicadores[0].ajuda = { texto: "x" }),
  "janela numero": (p) => void (p.canais[0].indicadores[0].janela = 60),
  "aviso_frescor objeto": (p) => void (p.canais[0].indicadores[0].aviso_frescor = {}),
  "limiar string": (p) => void (p.canais[0].indicadores[2].limiar = "4%"),
  "limiar sem origem": (p) => void (p.canais[0].indicadores[2].limiar = { valor: 4, rotulo: "x" }),
  "limiar origem fora do dominio": (p) =>
    void (p.canais[0].indicadores[2].limiar = { valor: 4, origem: "meta", rotulo: "x" }),
  "limiar valor string": (p) =>
    void (p.canais[0].indicadores[2].limiar = { valor: "4", origem: "interno", rotulo: "x" }),
  "limiar sem rotulo": (p) => void (p.canais[0].indicadores[2].limiar = { valor: 4, origem: "interno" }),
};

test("problemaNoPayload: payload valido completo passa", () => {
  assert.equal(problemaNoPayload(payloadValido()), null);
});

test("problemaNoPayload: toda forma quebrada que a tela desreferencia e' recusada", () => {
  for (const [nome, mutar] of Object.entries(QUEBRADOS)) {
    const p = payloadValido() as unknown as Bruto;
    mutar(p);
    assert.ok(problemaNoPayload(p), `deveria recusar: ${nome}`);
  }
});

test("carregarPerformanceContas: fora=[null] vira erro da tela, sem dados", async () => {
  const p = payloadValido() as unknown as Bruto;
  p.canais[0].cobertura.fora = [null];
  const fake = (async () => Response.json(p)) as typeof fetch;
  const r = await carregarPerformanceContas({ marca: null }, fake, "http://x");
  assert.equal(r.ok, false);
  assert.ok(!r.ok && /Resposta inválida/.test(r.motivo));
  assert.equal("dados" in r, false);
});

test("montarCanais: payload valido percorre a arvore inteira sem TypeError", () => {
  const p = payloadValido() as PerformanceContasResponse;
  assert.equal(problemaNoPayload(p), null);
  const visao = montarCanais(p, null, (m) => m.toUpperCase());
  assert.equal(visao.length, 1);
  const [shopee] = visao;
  assert.equal(shopee.titulo, "Shopee");
  assert.equal(shopee.descricao, "Cobertura: APICE");
  assert.deepEqual(shopee.fora, [{ chave: "barbours", texto: "BARBOURS: sem conta conectada" }]);
  const [taxa, sps, ldr] = shopee.indicadores;
  assert.equal(taxa.valor, "4,5%");
  assert.equal(taxa.bloco, null);
  assert.equal(taxa.tipo, "Cálculo Torre");
  assert.equal(sps.valor, null);
  assert.equal(sps.tipo, "Oficial");
  assert.deepEqual(sps.bloco, { tipo: "indisponivel", titulo: "Indisponível: sem fonte na Torre", descricao: null });
  assert.equal(ldr.limiar?.texto, "Referência de LDR: 4,0%");
  assert.equal(ldr.idAjuda, "ajuda-shopee-LDR");
});

test("montarCanais: payload minimo (canal sem indicador, sem cobertura) e filtro de canal", () => {
  const minimo: PerformanceContasResponse = {
    gerado_em: "2026-10-29T10:00:00Z",
    canais: [
      { canal: "mercado_livre", cobertura: { marcas: [], fora: [] }, indicadores: [] },
      { canal: "tiktok", cobertura: { marcas: [], fora: [] }, indicadores: [] },
    ],
  };
  assert.equal(problemaNoPayload(minimo), null);
  assert.deepEqual(
    montarCanais(minimo, null, (m) => m).map((c) => [c.titulo, c.descricao, c.fora.length, c.indicadores.length]),
    [
      ["Mercado Livre", "Cobertura: nenhuma conta coberta", 0, 0],
      ["TikTok Shop", "Cobertura: nenhuma conta coberta", 0, 0],
    ],
  );
  assert.deepEqual(montarCanais(minimo, "tiktok", (m) => m).map((c) => c.canal), ["tiktok"]);
  assert.deepEqual(montarCanais({ ...minimo, canais: [] }, null, (m) => m), []);
});

test("montarIndicador: opcionais ausentes viram null, nunca undefined nem zero", () => {
  const ind: Partial<Indicador> = indicador({ estado: "vazio_legitimo", valor: null });
  for (const k of ["janela", "atualizado_em", "aviso_frescor", "cobertura", "motivo_indisponivel", "limiar"] as const) {
    delete ind[k];
  }
  const corpo = { gerado_em: "x", canais: [{ canal: "tiktok", cobertura: { marcas: [], fora: [] }, indicadores: [ind] }] };
  assert.equal(problemaNoPayload(corpo), null);
  const v = montarIndicador(ind as Indicador, "tiktok");
  assert.equal(v.valor, null);
  assert.deepEqual(v.bloco, { tipo: "vazio", titulo: "Nenhum registro no período", descricao: null });
  for (const campo of [v.janela, v.atualizado, v.cobertura, v.limiar, v.avisoFrescor]) {
    assert.equal(campo, null);
  }
});

test("pagina e item desenham so' a visao montada (sem desreferenciar o payload cru)", () => {
  const pagina = readFileSync(join(AQUI, "../app/performance-contas/page.tsx"), "utf8");
  assert.match(pagina, /montarCanais\(resultado\.dados, filtros\.canal, brandLabel\)/);
  // (`f.marca` em `hrefAtual` e' o filtro da URL, nao a cobertura.)
  assert.doesNotMatch(pagina, /cobertura\.(fora|marcas)|brandLabel\(f\.marca\)|f\.motivo|ind\.codigo/);
  const item = readFileSync(join(AQUI, "../app/performance-contas/IndicadorItem.tsx"), "utf8");
  assert.match(item, /\{ v \}: \{ v: VisaoIndicador \}/);
  assert.doesNotMatch(item, /\bind\./);
});

test("carregarPerformanceContas: falha de rede vira erro, nunca dado", async () => {
  const falha = (async () => {
    throw new TypeError("fetch failed");
  }) as unknown as typeof fetch;
  const r = await carregarPerformanceContas({ marca: null }, falha, "http://x");
  assert.equal(r.ok, false);
});

test("carregarPerformanceContas: status nao-2xx e payload invalido viram erro", async () => {
  const status500 = (async () => new Response("boom", { status: 500 })) as typeof fetch;
  assert.equal((await carregarPerformanceContas({ marca: null }, status500, "http://x")).ok, false);
  const naoJson = (async () => new Response("<html>", { status: 200 })) as typeof fetch;
  assert.equal((await carregarPerformanceContas({ marca: null }, naoJson, "http://x")).ok, false);
  const forma = (async () => Response.json({ canais: "x" })) as typeof fetch;
  assert.equal((await carregarPerformanceContas({ marca: null }, forma, "http://x")).ok, false);
});

test("carregarPerformanceContas: 200 valido devolve os dados e usa no-store", async () => {
  let pedido: { url: string; init?: RequestInit } | null = null;
  const corpo = { gerado_em: "2026-10-29T10:00:00Z", canais: [] };
  const fake = (async (url: string, init?: RequestInit) => {
    pedido = { url, init };
    return Response.json(corpo);
  }) as unknown as typeof fetch;
  const r = await carregarPerformanceContas({ marca: "barbours" }, fake, "http://x");
  assert.equal(r.ok, true);
  assert.deepEqual(r.ok && r.dados, corpo);
  assert.equal(pedido!.url, "http://x/api/v1/performance-contas?brands=barbours");
  assert.equal(pedido!.init?.cache, "no-store");
});
