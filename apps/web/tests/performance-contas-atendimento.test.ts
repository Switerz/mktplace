// CX-MKT-1 — aba "Atendimento" da Performance das contas
// (contrato: docs/cx_atendimento_contrato.md). Roda via `node --test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  ATENDIMENTO_FLAG,
  atendimentoHabilitado,
  carregarAtendimento,
  celula,
  lerAba,
  lerDias,
  montarCanaisAtendimento,
  montarMotivos,
  problemaNoPayloadAtendimento,
  textoJanela,
  urlAtendimento,
  type AtendimentoResponse,
  type BlocoMotivos,
} from "../src/lib/performance-contas-atendimento.ts";

const AQUI = dirname(fileURLToPath(import.meta.url));

function indicador(over: Record<string, unknown> = {}) {
  return {
    codigo: "ml_taxa_cancelamento", rotulo: "Taxa de cancelamento", tipo: "torre",
    estado: "dado", valor: 3.2, unidade: "pct", janela: "30 dias", limiar: null,
    fonte: "Data Mart", atualizado_em: null, aviso_frescor: null, cobertura: null,
    ajuda: "ajuda", motivo_indisponivel: null, ...over,
  };
}

function motivos(over: Partial<BlocoMotivos> = {}): BlocoMotivos {
  return {
    titulo: "Motivos de cancelamento", estado: "dado", total: 100, ajuda: "ajuda",
    excluidos: null, rotulo_excluidos: null, motivo_indisponivel: null,
    itens: [
      { codigo: "a", rotulo: "Comprador cancelou", natureza: "comprador", quantidade: 80, pct: 80, mapeado: true },
      { codigo: "b", rotulo: "novo_codigo", natureza: "plataforma", quantidade: 20, pct: 20, mapeado: false },
    ],
    ...over,
  };
}

function payload(): AtendimentoResponse {
  return {
    gerado_em: "2026-11-04T12:00:00Z",
    janela: { inicio: "2026-10-05", fim: "2026-11-03", dias: 30 },
    canais: [
      {
        canal: "mercado_livre",
        cobertura: { marcas: ["kokeshi", "lescent"], fora: [{ marca: "apice", motivo: "Sem conta ML." }] },
        indicadores: [indicador() as never],
        motivos: motivos(),
        marcas: [
          { marca: "kokeshi", pedidos: 28095, cancelamentos: 1030, taxa_cancelamento_pct: 3.67,
            cancelamentos_comprador: null, em_devolucao: null },
          { marca: "lescent", pedidos: null, cancelamentos: null, taxa_cancelamento_pct: null,
            cancelamentos_comprador: null, em_devolucao: null },
        ],
        pendencias: [{ codigo: "ml_fila_acao", titulo: "Fila", motivo: "Depende da API." }],
        atualizado_em: null,
      },
      {
        canal: "tiktok",
        cobertura: { marcas: ["kokeshi"], fora: [] },
        indicadores: [indicador({ codigo: "tiktok_devolucoes", estado: "sem_fonte", valor: null,
                                  motivo_indisponivel: "Ainda não lido." }) as never],
        motivos: null, marcas: [], pendencias: [], atualizado_em: null,
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Flag, aba e janela
// ---------------------------------------------------------------------------
test("flag so' liga com 'true' exato", () => {
  assert.equal(ATENDIMENTO_FLAG, "NEXT_PUBLIC_PERFORMANCE_CONTAS_ATENDIMENTO_ENABLED");
  assert.equal(atendimentoHabilitado({ NEXT_PUBLIC_PERFORMANCE_CONTAS_ATENDIMENTO_ENABLED: "true" }), true);
  for (const v of [undefined, "1", "TRUE", "yes", ""]) {
    assert.equal(atendimentoHabilitado({ NEXT_PUBLIC_PERFORMANCE_CONTAS_ATENDIMENTO_ENABLED: v }), false);
  }
});

test("aba atendimento so' com a flag ligada", () => {
  assert.equal(lerAba({ aba: "atendimento" }, true), "atendimento");
  assert.equal(lerAba({ aba: "atendimento" }, false), "saude");
  assert.equal(lerAba({ aba: "<x>" }, true), "saude");
  assert.equal(lerAba({}, true), "saude");
});

test("dias fora da allowlist caem em 30", () => {
  assert.equal(lerDias({ dias: "7" }), 7);
  assert.equal(lerDias({ dias: "90" }), 90);
  for (const v of ["15", "-1", "abc", undefined]) assert.equal(lerDias({ dias: v }), 30);
});

test("url leva marca e janela", () => {
  assert.equal(
    urlAtendimento("http://api", { marca: "kokeshi", dias: 7 }),
    "http://api/api/v1/cx/atendimento?brands=kokeshi&dias=7",
  );
  assert.equal(urlAtendimento("http://api", { marca: null, dias: 30 }),
               "http://api/api/v1/cx/atendimento?dias=30");
});

test("texto da janela", () => {
  assert.equal(textoJanela(payload().janela), "30 dias fechados · 05/10 a 03/11");
});

// ---------------------------------------------------------------------------
// Ausencia nunca vira zero
// ---------------------------------------------------------------------------
test("celula ausente e' travessao, zero medido e' 0", () => {
  assert.equal(celula(null), "—");
  assert.equal(celula(undefined), "—");
  assert.equal(celula(0), "0");
  assert.equal(celula(28095), "28.095");
  assert.equal(celula(2.51, "pct"), "2,5%");
});

test("marca sem dado sai com travessao em todas as colunas", () => {
  const [ml] = montarCanaisAtendimento(payload(), null, (m) => m.toUpperCase());
  const lescent = ml.linhas.find((l) => l.chave === "lescent")!;
  assert.deepEqual(lescent.celulas, ["—", "—", "—"]);
  const kokeshi = ml.linhas.find((l) => l.chave === "kokeshi")!;
  assert.deepEqual(kokeshi.celulas, ["28.095", "1.030", "3,7%"]);
  assert.equal(ml.temAusente, true);
  const semAusencia = payload();
  semAusencia.canais[0].marcas = [semAusencia.canais[0].marcas[0]];
  assert.equal(montarCanaisAtendimento(semAusencia, null, (m) => m)[0].temAusente, false);
});

test("indicador sem fonte nao desenha numero", () => {
  const [, tt] = montarCanaisAtendimento(payload(), null, (m) => m);
  assert.equal(tt.indicadores[0].valor, null);
  assert.equal(tt.indicadores[0].bloco?.tipo, "indisponivel");
  assert.deepEqual(tt.linhas, []);
});

test("motivos sem dado viram bloco de estado, nunca barras", () => {
  const erro = montarMotivos(motivos({ estado: "erro", total: null, itens: [], motivo_indisponivel: "Fonte fora." }))!;
  assert.equal(erro.bloco?.tipo, "erro");
  assert.deepEqual(erro.itens, []);
  const vazio = montarMotivos(motivos({ estado: "vazio_legitimo", total: null, itens: [] }))!;
  assert.equal(vazio.bloco?.tipo, "vazio");
});

test("barras sao relativas ao maior motivo e codigo novo e' sinalizado", () => {
  const m = montarMotivos(motivos({ excluidos: 3799, rotulo_excluidos: "pedidos não pagos" }))!;
  assert.equal(m.itens[0].largura, 100);
  assert.equal(m.itens[1].largura, 25);
  assert.equal(m.itens[1].mapeado, false);
  assert.equal(m.naoMapeados, 1);
  assert.equal(m.excluidos, "3.799 pedidos não pagos");
  assert.equal(m.itens[0].pct, "80,0%");
});

test("filtro de canal e' local", () => {
  const so = montarCanaisAtendimento(payload(), "tiktok", (m) => m);
  assert.deepEqual(so.map((c) => c.canal), ["tiktok"]);
});

test("fora da cobertura usa o rotulo da marca", () => {
  const [ml] = montarCanaisAtendimento(payload(), null, (m) => (m === "apice" ? "ÁPICE" : m));
  assert.deepEqual(ml.fora, [{ chave: "apice", texto: "ÁPICE: Sem conta ML." }]);
});

// ---------------------------------------------------------------------------
// Validacao de forma
// ---------------------------------------------------------------------------
test("payload valido passa", () => {
  assert.equal(problemaNoPayloadAtendimento(payload()), null);
});

test("payload quebrado e' recusado inteiro", () => {
  const casos: [string, (p: any) => void][] = [
    ["janela", (p) => { p.janela = null; }],
    ["canal", (p) => { p.canais[0].canal = "amazon"; }],
    ["indicador", (p) => { p.canais[0].indicadores[0].estado = "talvez"; }],
    ["motivo natureza", (p) => { p.canais[0].motivos.itens[0].natureza = "x"; }],
    ["motivo numero", (p) => { p.canais[0].motivos.itens[0].quantidade = "80"; }],
    ["motivo sem dado com numero", (p) => { p.canais[0].motivos.estado = "erro"; }],
    ["marca numero", (p) => { p.canais[0].marcas[0].pedidos = "1"; }],
    ["pendencia", (p) => { p.canais[0].pendencias[0].motivo = 1; }],
  ];
  for (const [nome, estraga] of casos) {
    const p = payload();
    estraga(p);
    assert.notEqual(problemaNoPayloadAtendimento(p), null, nome);
  }
});

// ---------------------------------------------------------------------------
// Fetch
// ---------------------------------------------------------------------------
test("fetch: sucesso, status ruim, rede e json quebrado", async () => {
  const ok = await carregarAtendimento({ marca: null, dias: 30 },
    (async () => new Response(JSON.stringify(payload()))) as typeof fetch, "http://api");
  assert.equal(ok.ok, true);

  const ruim = await carregarAtendimento({ marca: null, dias: 30 },
    (async () => new Response("x", { status: 503 })) as typeof fetch, "http://api");
  assert.deepEqual(ruim, { ok: false, motivo: "A API respondeu 503." });

  const rede = await carregarAtendimento({ marca: null, dias: 30 },
    (async () => { throw new Error("ECONNREFUSED"); }) as typeof fetch, "http://api");
  assert.equal(rede.ok, false);

  const torto = await carregarAtendimento({ marca: null, dias: 30 },
    (async () => new Response(JSON.stringify({ gerado_em: "x" }))) as typeof fetch, "http://api");
  assert.equal(torto.ok, false);
});

// ---------------------------------------------------------------------------
// Fiacao da pagina (leitura do fonte, como os demais testes de contrato)
// ---------------------------------------------------------------------------
test("pagina: cancelamentos e devolucoes da Shopee vivem na aba CX (menu principal)", () => {
  // 04/11/2026: a aba Atendimento saiu da Performance das contas e virou a aba CX.
  const cx = readFileSync(join(AQUI, "..", "app", "cx", "CxConteudo.tsx"), "utf-8");
  assert.match(cx, /<AtendimentoCanal /);
  const perf = readFileSync(join(AQUI, "..", "app", "performance-contas", "page.tsx"), "utf-8");
  assert.doesNotMatch(perf, /atendimento/i);
});
