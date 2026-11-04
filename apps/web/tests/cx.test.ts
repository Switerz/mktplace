/**
 * Aba CX (04/11/2026) — filtros, montagem do chat, payload e menu.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  carregarCxShopee,
  hrefCx,
  lerFiltrosCx,
  montarChat,
  problemaNoPayloadCx,
  urlCxShopee,
  type ChatShopee,
} from "../src/lib/cx.ts";
import { CX_NAV, getRouteTitle, navSections } from "../src/components/shell/nav-config.ts";

const MARCAS = ["apice", "barbours", "kokeshi", "lescent", "rituaria"];
const rotulo = (m: string) => m.toUpperCase();

function metrica(codigo: string, rotuloM: string, valor: number, situacao: "ok" | "fora", meta: string) {
  return {
    codigo, rotulo: rotuloM, grupo: "Atendimento", valor, unidade: "pct" as const, quantidade: null,
    anterior: valor - 1, meta: 60, comparador: ">=", rotulo_meta: meta, situacao, ajuda: null,
  };
}

function chat(): ChatShopee {
  return {
    estado: "dado",
    fonte: "Shopee",
    sem_foto: [{ marca: "lescent", motivo: "Sem fotografia capturada ainda." }],
    contas: [
      {
        marca: "apice", dia: "2026-11-04", capturado_em: null, nivel: "Bom", tom: "bom", termometro: 3,
        termometro_max: 4, resumo: null, destaques: [], avisos: [],
        metricas: [metrica("shopee_11", "Taxa de resposta do chat", 82.65, "ok", "Meta >= 60")],
      },
      {
        marca: "rituaria", dia: "2026-11-04", capturado_em: null, nivel: "Bom", tom: "atencao", termometro: 3,
        termometro_max: 4, resumo: null, destaques: [], avisos: [],
        metricas: [
          metrica("shopee_11", "Taxa de resposta do chat", 45.31, "fora", "Meta >= 60"),
          { ...metrica("shopee_22", "Nota da loja", 4.93, "ok", "Meta >= 4.5"), unidade: "nota" as const },
        ],
      },
    ],
  };
}

test("filtros: marca fora da allowlist e periodo invalido caem no padrao", () => {
  assert.deepEqual(lerFiltrosCx({ brands: "Apice", dias: "7" }, MARCAS), { marca: "apice", dias: 7 });
  assert.deepEqual(lerFiltrosCx({ brands: "<x>", dias: "15" }, MARCAS), { marca: null, dias: 30 });
  assert.equal(hrefCx({ marca: null, dias: 30 }), "/cx");
  assert.equal(hrefCx({ marca: "apice", dias: 90 }), "/cx?brands=apice&dias=90");
  assert.equal(urlCxShopee("http://api", { marca: null, dias: 30 }), "http://api/api/v1/cx/shopee?dias=30");
});

test("chat: uma coluna por metrica, celula ausente vira null e quem esta fora da meta e listado", () => {
  const v = montarChat(chat(), rotulo);
  assert.deepEqual(v.colunas.map((c) => c.codigo), ["shopee_11", "shopee_22"]);
  assert.equal(v.linhas[0].marca, "APICE");
  assert.equal(v.linhas[0].celulas[1], null, "Ápice sem nota: ausente, nunca zero");
  assert.equal(v.linhas[1].celulas[0]?.situacao, "fora");
  assert.equal(v.linhas[1].celulas[0]?.meta, "Meta >= 60");
  assert.deepEqual(v.foraDaMeta, ["RITUARIA"]);
  assert.deepEqual(v.semFoto, ["LESCENT: Sem fotografia capturada ainda."]);
  assert.equal(v.linhas[0].dia, "04/11");
});

function payload() {
  return {
    gerado_em: "2026-11-04T19:00:00Z",
    janela: { inicio: "2026-10-05", fim: "2026-11-03", dias: 30 },
    chat: chat(),
    atendimento: {
      canal: "shopee",
      cobertura: { marcas: ["apice"], fora: [] },
      indicadores: [],
      marcas: [],
      pendencias: [],
      atualizado_em: null,
    },
  };
}

test("payload: valida chat e atendimento", () => {
  assert.equal(problemaNoPayloadCx(payload()), null);
  assert.equal(problemaNoPayloadCx({ ...payload(), chat: undefined }), "chat ausente");
  assert.equal(problemaNoPayloadCx({ ...payload(), atendimento: null }), "atendimento ausente");
});

test("carregar: nunca lanca; erro HTTP vira motivo", async () => {
  const ok = await carregarCxShopee({ marca: null, dias: 30 },
    (async () => new Response(JSON.stringify(payload()), { status: 200 })) as typeof fetch, "http://api");
  assert.equal(ok.ok, true);
  const falha = await carregarCxShopee({ marca: null, dias: 30 },
    (async () => new Response("x", { status: 503 })) as typeof fetch, "http://api");
  assert.deepEqual(falha, { ok: false, motivo: "A API respondeu 503." });
});

test("menu: CX fica em Operações logo depois da Expedição e tem titulo proprio", () => {
  const ops = navSections({ expedicao: true, cx: true }).find((s) => s.label === "Operações")!;
  const hrefs = ops.pages.map((p) => p.href);
  assert.equal(hrefs.indexOf("/cx"), hrefs.indexOf("/expedicao") + 1);
  assert.deepEqual(CX_NAV, { href: "/cx", label: "CX" });
  assert.equal(getRouteTitle("/cx"), "CX");
  const navList = readFileSync(new URL("../src/components/shell/NavList.tsx", import.meta.url), "utf8");
  assert.match(navList, /cx: true,/);
});

// ---------------------------------------------------------------------------
// Coleta de CX (devolucoes + conversas nao lidas), 04/11/2026
// ---------------------------------------------------------------------------
import { montarDevolucoes, reais } from "../src/lib/cx.ts";

test("chat: coluna de nao lidas so' com a coleta publicada; nulo vira traco", () => {
  const sem = montarChat(chat(), rotulo);
  assert.equal(sem.comNaoLidas, false);
  assert.match(sem.motivoNaoLidas ?? "", /shopee_cx_coleta/);
  const com = montarChat({
    ...chat(),
    nao_lidas_estado: "dado",
    nao_lidas: [{ marca: "apice", valor: 1234, capturado_em: null }, { marca: "rituaria", valor: null, capturado_em: null }],
  }, rotulo);
  assert.equal(com.comNaoLidas, true);
  assert.equal(com.motivoNaoLidas, null);
  assert.deepEqual(com.linhas.map((l) => l.naoLidas), ["1.234", "—"]);
});

test("devolucoes: sem a chave na API = sem fonte; com dado formata reais e motivos", () => {
  assert.equal(montarDevolucoes(undefined, rotulo).estado, "sem_fonte");
  const v = montarDevolucoes({
    estado: "dado", fonte: "x", total: 4, reembolsado: 120.5,
    motivos: {
      titulo: "Motivos de devolução", estado: "dado", total: 4, ajuda: "a",
      itens: [{ codigo: "change_mind", rotulo: "Mudou de ideia", natureza: "comprador", quantidade: 4, pct: 100, mapeado: true }],
    },
    aceitas: 3, em_andamento: 1, canceladas: 0,
    marcas: [
      { marca: "apice", devolucoes: 4, aceitas: 3, em_andamento: 1, canceladas: 0, reembolsado: 120.5 },
      { marca: "lescent", devolucoes: 0, aceitas: 0, em_andamento: 0, canceladas: 0, reembolsado: null },
    ],
  }, rotulo);
  assert.equal(v.total, "4");
  assert.equal(v.aceitas, "3");
  assert.equal(v.emAndamento, "1");
  assert.equal(v.reembolsado, reais(120.5));
  assert.equal(v.motivos?.itens[0].rotulo, "Mudou de ideia");
  assert.deepEqual(v.linhas.map((l) => [l.marca, l.devolucoes, l.reembolsado]),
    [["APICE", "4", reais(120.5)], ["LESCENT", "0", "—"]]);
});

test("aba CX busca pelo navegador (torreFetch), nunca no servidor", () => {
  // 04/11/2026: com NEXT_PUBLIC_API_URL=/torre-api um fetch no servidor nao resolve.
  const pagina = readFileSync(new URL("../app/cx/page.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(pagina, /carregarCxShopee/);
  const conteudo = readFileSync(new URL("../app/cx/CxConteudo.tsx", import.meta.url), "utf8");
  assert.match(conteudo, /^"use client";/);
  assert.match(conteudo, /carregarCxShopee\(\{ marca, dias \}, torreFetch as typeof fetch\)/);
});
