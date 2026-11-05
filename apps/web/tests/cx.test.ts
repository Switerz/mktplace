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
  montarAtendimentoCx,
  montarPareto,
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

test("aba CX busca pelo navegador (torreFetch), nunca no servidor", () => {
  // 04/11/2026: com NEXT_PUBLIC_API_URL=/torre-api um fetch no servidor nao resolve.
  const pagina = readFileSync(new URL("../app/cx/page.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(pagina, /carregarCxShopee/);
  const conteudo = readFileSync(new URL("../app/cx/CxConteudo.tsx", import.meta.url), "utf8");
  assert.match(conteudo, /^"use client";/);
  assert.match(conteudo, /carregarCxShopee\(\{ marca, dias \}, torreFetch as typeof fetch\)/);
});

// ---------------------------------------------------------------------------
// Pagina enxuta (04/11/2026): Atendimento (planilha 1.2) e Pareto (1.1)
// ---------------------------------------------------------------------------
function comConversas(dias = 30) {
  const p = payload();
  return {
    ...p,
    janela: { ...p.janela, dias },
    chat: {
      ...p.chat,
      nao_lidas_estado: "dado" as const,
      nao_lidas: [
        { marca: "apice", valor: 4, capturado_em: null, conversas_7d: 40, conversas_30d: 200, aguardando_resposta: 3, conversas_truncado: false },
        { marca: "rituaria", valor: 6, capturado_em: null, conversas_7d: null, conversas_30d: null, aguardando_resposta: null, conversas_truncado: null },
      ],
    },
    atendimento: {
      ...p.atendimento,
      marcas: [
        { marca: "apice", pedidos: 1000, cancelamentos: 20, taxa_cancelamento_pct: 2, cancelamentos_comprador: 5, em_devolucao: 1 },
        { marca: "rituaria", pedidos: 300, cancelamentos: 3, taxa_cancelamento_pct: 1, cancelamentos_comprador: 1, em_devolucao: 0 },
      ],
      motivos: {
        titulo: "Motivos de cancelamento", estado: "dado" as const, total: 23, ajuda: "", excluidos: 9,
        rotulo_excluidos: "pedidos não pagos cancelados pela Shopee (fora da contagem)", motivo_indisponivel: null,
        itens: [{ codigo: "sem_estoque", rotulo: "Sem estoque", natureza: "vendedor" as const, quantidade: 23, pct: 100, mapeado: true }],
      },
    },
    devolucoes: {
      estado: "dado" as const, fonte: "x", total: 12, aceitas: 8, em_andamento: 2, canceladas: 2, reembolsado: 450.5,
      motivos: {
        titulo: "Motivos de devolução", estado: "dado" as const, total: 10, ajuda: "", excluidos: 2,
        rotulo_excluidos: "pedidos de devolução cancelados pelo próprio comprador (fora dos motivos)", motivo_indisponivel: null,
        itens: [
          { codigo: "not_receipt", rotulo: "Não recebeu o pedido", natureza: "logistica" as const, quantidade: 7, pct: 70, mapeado: true },
          { codigo: "change_mind", rotulo: "Mudou de ideia", natureza: "comprador" as const, quantidade: 3, pct: 30, mapeado: true },
        ],
      },
      marcas: [{ marca: "apice", devolucoes: 12, aceitas: 8, em_andamento: 2, canceladas: 2, reembolsado: 450.5 }],
    },
  };
}

test("atendimento: conversas, aguardando, % respondido e contact rate por loja", () => {
  const v = montarAtendimentoCx(comConversas(30), rotulo);
  assert.equal(v.estado, "dado");
  assert.equal(v.rotuloConversas, "Conversas (30 dias)");
  const apice = v.linhas.find((l) => l.chave === "apice")!;
  assert.deepEqual([apice.conversas, apice.aguardando, apice.naoLidas, apice.contactRate], ["200", "3", "4", "20,0%"]);
  assert.equal(apice.respondido?.valor, "82,65%");
  const rit = v.linhas.find((l) => l.chave === "rituaria")!;
  assert.equal(rit.conversas, "—", "sem a leitura nova: traco, nunca zero");
  assert.equal(rit.contactRate, "—");
  assert.equal(rit.respondido?.fora, true);
  assert.deepEqual(v.totais, { conversas: "200", aguardando: "3", naoLidas: "10", foraDaMeta: ["RITUARIA"] });
  assert.ok(v.notas.some((n) => /Tempo de resposta/.test(n)));
});

test("atendimento: 7 dias usa conversas_7d; 90 dias nao calcula contact rate", () => {
  assert.equal(montarAtendimentoCx(comConversas(7), rotulo).linhas[0].conversas, "40");
  assert.equal(montarAtendimentoCx(comConversas(7), rotulo).linhas[0].contactRate, "4,0%");
  const v90 = montarAtendimentoCx(comConversas(90), rotulo);
  assert.equal(v90.linhas[0].contactRate, "—");
  assert.ok(v90.notas.some((n) => /contact rate só/.test(n)));
});

test("atendimento: sem coleta de conversas avisa no rodape; truncado vira piso", () => {
  const p = comConversas(30);
  const sem = montarAtendimentoCx({ ...p, chat: { ...p.chat, nao_lidas_estado: "sem_fonte", nao_lidas: [] } }, rotulo);
  assert.ok(sem.notas.some((n) => /aguardando a coleta/.test(n)));
  assert.equal(sem.totais.conversas, "—");
  const trunc = comConversas(30);
  trunc.chat.nao_lidas[0].conversas_truncado = true;
  const v = montarAtendimentoCx(trunc, rotulo);
  assert.equal(v.linhas[0].conversas, "≥ 200");
  assert.ok(v.notas.some((n) => /piso/.test(n)));
});

test("pareto: devolucoes e cancelamentos juntos, do maior para o menor, com acumulado", () => {
  const v = montarPareto(comConversas(30), rotulo);
  assert.equal(v.estado, "dado");
  assert.deepEqual(v.itens.map((i) => [i.rotulo, i.tipo, i.quantidade, i.pct, i.acumulado]), [
    ["Sem estoque", "Cancelamento", "23", "69,7%", "69,7%"],
    ["Não recebeu o pedido", "Devolução", "7", "21,2%", "90,9%"],
    ["Mudou de ideia", "Devolução", "3", "9,1%", "100,0%"],
  ]);
  assert.equal(v.itens[0].largura, 100);
  assert.deepEqual(
    { ...v.totais, reembolsado: v.totais.reembolsado.replace(/\s/g, " ") },
    { reembolsado: "R$ 450,50", devolucoes: "10", cancelamentos: "23", reenviados: "—" },
  );
  assert.deepEqual(v.linhas.map((l) => [l.marca, l.pedidos, l.cancelamentos, l.devolucoes]), [
    ["APICE", "1.000", "20", "10"],
    ["RITUARIA", "300", "3", "—"],
  ]);
  assert.ok(v.notas.some((n) => /^9 pedidos não pagos/.test(n)));
  assert.ok(v.notas.some((n) => /Reenvios/.test(n)));
  assert.equal(v.resto, null);
});

test("pareto: top 10 e o resto somado numa linha; sem devolucoes = nota, nao zero", () => {
  const p = comConversas(30);
  const itens = Array.from({ length: 12 }, (_, i) => ({
    codigo: `m${i}`, rotulo: `Motivo ${String(i).padStart(2, "0")}`, natureza: "vendedor" as const, quantidade: 12 - i, pct: 0, mapeado: true,
  }));
  const v = montarPareto({ ...p, atendimento: { ...p.atendimento, motivos: { ...p.atendimento.motivos, itens } }, devolucoes: undefined }, rotulo);
  assert.equal(v.itens.length, 10);
  assert.match(v.resto ?? "", /^\+ 2 motivo\(s\) com 3 caso\(s\)/);
  assert.equal(v.totais.devolucoes, "—");
  assert.equal(v.totais.reembolsado, "—");
  assert.ok(v.notas.some((n) => /aguardando a coleta/.test(n)));
});
