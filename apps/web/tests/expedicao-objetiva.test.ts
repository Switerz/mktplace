/**
 * UI-EXPEDICAO-1 — Expedicao mais objetiva (escolhas 8 a 12 do usuario).
 *
 *  8. vencidos como numero principal (vermelho) + secundarios; backlog pequeno;
 *  9. cada numero uma vez: a lista vira "onde agir" e a barra de prazo sobe;
 * 10. texto explicativo vira "ⓘ"; fica o selo "inclui FBS";
 * 11. uma tabela so' de marcas (fila de agora + tendencia do periodo);
 * 12. o periodo no topo comanda a pagina; a fila de agora tem o selo "agora".
 */
import { register } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  ondeAgirNaFila,
  medidasDoDestaque,
  type Kpi,
  type PontoTendencia,
  type RespostaTendencia,
  type ResumoConta,
  type Totais,
} from "../src/lib/expedicao-contract.ts";

register("./helpers/tsx-loader.mjs", import.meta.url);

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8");
const CLIENTE = fonte("app/expedicao/ExpedicaoClient.tsx").replace(/\r\n/g, "\n");
const CABECALHO = fonte("src/components/expedicao/CabecalhoExpedicao.tsx");

function totais(over: Partial<Totais> = {}): Totais {
  return {
    backlog_count: 100, overdue_count: 10, due_within_24h_count: 20, on_time_count: 65,
    deadline_unavailable_count: 5, over_48h_count: 30, slow_count: 4, zombie_count: 1,
    stalled_count: 5, ...over,
  };
}
function conta(shop: string, brand: string, over: Partial<Totais> = {}): ResumoConta {
  return {
    shop_account: shop, brand, run_status: "success", source_watermark_at: null,
    source_advanced: true, snapshot_hour: "2026-10-21T17:00:00Z", ...totais(over),
  };
}

// ---------------------------------------------------------------------------
// 9. "onde agir"
// ---------------------------------------------------------------------------
test("onde agir: a marca que concentra a medida mais grave acesa", () => {
  const item = ondeAgirNaFila(
    totais({ overdue_count: 27 }),
    [conta("a", "apice", { overdue_count: 25 }), conta("b", "kokeshi", { overdue_count: 2 })],
    "shopee",
  );
  assert.equal(item?.texto, "ÁPICE concentra 25 dos 27 vencidos.");
  assert.equal(item?.tom, "critico");
});

test("onde agir: sem vencidos, desce para a proxima medida; todos numa conta", () => {
  const item = ondeAgirNaFila(
    totais({ overdue_count: 0, due_within_24h_count: 8 }),
    [conta("a", "apice", { due_within_24h_count: 0 }), conta("b", "kokeshi", { due_within_24h_count: 8 })],
    "shopee",
  );
  assert.equal(item?.texto, "KOKESHI concentra todos os 8 a vencer em 24 h.");
  assert.equal(item?.tom, "atencao");
});

test("onde agir: um pedido so' fica no singular", () => {
  const item = ondeAgirNaFila(
    totais({ overdue_count: 1 }),
    [conta("a", "apice", { overdue_count: 1 }), conta("b", "kokeshi", { overdue_count: 0 })],
    "shopee",
  );
  assert.equal(item?.texto, "ÁPICE tem o único pedido vencido.");
});

test("onde agir: Mercado Livre nao fala de vencidos; uma conta so' nao gera frase", () => {
  assert.deepEqual(medidasDoDestaque("mercadolivre"), ["over_48h_count", "stalled_count"]);
  const ml = ondeAgirNaFila(
    totais({ over_48h_count: 9 }),
    [conta("1", "kokeshi", { over_48h_count: 9 }), conta("2", "apice", { over_48h_count: 0 })],
    "mercadolivre",
  );
  assert.match(ml!.texto, /acima de 48 h/);
  assert.doesNotMatch(ml!.texto, /vencid/);
  assert.equal(ondeAgirNaFila(totais(), [conta("a", "apice")], "shopee"), null);
  assert.equal(ondeAgirNaFila(null, [conta("a", "apice"), conta("b", "kokeshi")], "shopee"), null);
});

// ---------------------------------------------------------------------------
// 8. destaque
// ---------------------------------------------------------------------------
const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: RiscoEmDestaque } = await import("../src/components/expedicao/RiscoEmDestaque.tsx");
const { SecaoGerencial } = await import("../src/components/expedicao/SecoesExpedicao.tsx");
const { default: EvolucaoPorMarca } = await import("../src/components/expedicao/EvolucaoPorMarca.tsx");

const kpi = (chave: string, rotulo: string, valor: number | null): Kpi => ({ chave, rotulo, valor, alerta: false });

test("destaque: vencidos grande em vermelho, backlog pequeno, onde agir dentro", () => {
  const html = renderToStaticMarkup(
    h(RiscoEmDestaque, {
      principal: kpi("overdue", "Vencidos", 27),
      backlog: kpi("backlog", "Backlog", 1234),
      marcasAlerta: kpi("marcas_alerta", "Marcas em alerta", 2),
      secundarios: h("i", null, "sec"),
      prazo: h("b", null, "barra"),
      ondeAgir: { chave: "overdue_count", tom: "critico", texto: "ÁPICE concentra 25 dos 27 vencidos." },
    }),
  );
  assert.match(html, /text-4xl[^"]*text-crit-ink[^"]*">27</);
  assert.match(html, /de 1\.234 em fila/);
  assert.match(html, /2 marcas em alerta/);
  assert.match(html, /<b>barra<\/b>/);
  assert.match(html, /Onde agir:<\/span> ÁPICE concentra 25 dos 27 vencidos\./);
  assert.match(html, /<i>sec<\/i>/);
});

test("destaque: zero nao pinta; +48 h (limiar interno) e' ambar, nao vermelho", () => {
  const zero = renderToStaticMarkup(
    h(RiscoEmDestaque, { principal: kpi("overdue", "Vencidos", 0), secundarios: null }),
  );
  assert.doesNotMatch(zero, /crit/);
  assert.doesNotMatch(zero, /marcas? em alerta/, "sem marca em alerta, sem selo");
  const ml = renderToStaticMarkup(
    h(RiscoEmDestaque, { principal: kpi("over48h", "Acima de 48 h", 9), secundarios: null }),
  );
  assert.match(ml, /text-warn-ink/);
  assert.doesNotMatch(ml, /text-crit-ink/);
});

test("secao: o ⓘ e o selo ficam fora do h3 (nome da secao = titulo)", () => {
  const html = renderToStaticMarkup(
    h(SecaoGerencial, { titulo: "Risco atual", dica: "Segundo a fotografia de ontem.", selo: h("em", null, "agora") }, "x"),
  );
  assert.match(html, /<h3[^>]*>Risco atual<\/h3>/);
  assert.match(html, /aria-label="Segundo a fotografia de ontem\."/);
  assert.match(html, /<em>agora<\/em>/);
});

// ---------------------------------------------------------------------------
// 11. tabela unica
// ---------------------------------------------------------------------------
const H = (hora: number) => `2026-10-21T${String(hora).padStart(2, "0")}:00:00Z`;
function ponto(c: string, marca: string, hora: string, backlog: number): PontoTendencia {
  return {
    snapshot_hour: hora, shop_account: c, brand: marca, refresh_batch_id: "b", observed_at: hora,
    source_watermark_at: null, source_advanced: true, run_status: "success", backlog_count: backlog,
    overdue_count: 0, due_within_24h_count: 0, on_time_count: 0, deadline_unavailable_count: backlog,
    over_48h_count: 7, slow_count: 0, zombie_count: 0, stalled_count: 2,
  };
}
function tendencia(points: PontoTendencia[]): RespostaTendencia {
  const horas = points.map((p) => p.snapshot_hour).sort();
  return {
    availability: "available", unavailable_reason: null, channel: "shopee", window_hours: 48,
    from_hour: horas[0] ?? null, to_hour: horas.at(-1) ?? null, points, truncated: false,
    limitations: {
      no_automation: true, load_mode: "manual_snapshot", snapshot_age_hours: 0.5,
      brands_not_covered: [], order_identifier_withheld: true, notes: [],
    },
  };
}
const linha = (c: string, titulo: string, backlog: number, vencidos: number | null) => ({
  conta: c, titulo, apelido: null, backlog, vencidos, acima48h: 3, travados: 1, barra: h("u", null, `barra-${c}`),
});

test("tabela unica: fotografia + tendencia; conta sem ponto continua na tabela", () => {
  const html = renderToStaticMarkup(
    h(EvolucaoPorMarca, {
      resposta: tendencia([ponto("a", "apice", H(13), 50), ponto("a", "apice", H(15), 60)]),
      fila: [linha("a", "ÁPICE", 61, 25), linha("b", "KOKESHI", 9, 0)],
    }),
  );
  for (const c of ["Backlog", "Vencidos", "Acima de 48 h", "Travados", "Prazo", "Tendência"]) {
    assert.match(html, new RegExp(`<th scope="col"[^>]*>${c}`), c);
  }
  // os numeros da linha sao os da FOTOGRAFIA (61), nao o ultimo ponto (60)
  assert.match(html, /ÁPICE<\/th><td[^>]*>61<\/td><td[^>]*text-crit-ink[^>]*>25<\/td>/);
  assert.match(html, /<u>barra-a<\/u>/);
  assert.match(html, /KOKESHI<\/th><td[^>]*>9<\/td>/);
  assert.match(html, /sem ponto no período/);
  assert.equal((html.match(/<polyline/g) ?? []).length, 1, "so' a conta com serie tem linha");
});

test("tabela unica: sem tendencia ainda mostra as marcas; canal sem prazo sem coluna de vencidos", () => {
  const html = renderToStaticMarkup(
    h(EvolucaoPorMarca, { resposta: null, fila: [linha("1", "KOKESHI", 9, null)], rotuloBarra: "Distribuição" }),
  );
  assert.match(html, /KOKESHI/);
  assert.doesNotMatch(html, />Vencidos</);
  assert.match(html, />Distribuição</);
  assert.equal(renderToStaticMarkup(h(EvolucaoPorMarca, { resposta: null, fila: [] })), "");
});

// ---------------------------------------------------------------------------
// Fiacao na tela
// ---------------------------------------------------------------------------
test("tela: destaque no lugar da faixa e da lista; barra de prazo nao repete", () => {
  assert.match(CLIENTE, /\{destaque \? \(\s*<RiscoEmDestaque/);
  assert.match(CLIENTE, /\{destaque \? null : \(\s*<AtencaoAgora/);
  assert.match(CLIENTE, /\{destaque && temBarraDePrazo \? null : \(/);
  assert.match(CLIENTE, /const chaveDestaque = temPrazo \? "overdue" : "over48h";/);
  // sem prazo publicado, a barra seria 100% "sem prazo": nao entra no destaque
  assert.match(CLIENTE, /prazo=\{temPrazo \? barraDePrazo\(true\) : undefined\}/);
});

test("tela: selo 'agora' (ou 'desatualizada') e 'inclui FBS' com ⓘ", () => {
  assert.match(CLIENTE, /<SeloLeitura tom="atencao">desatualizada<\/SeloLeitura>/);
  assert.match(CLIENTE, /<SeloLeitura>agora<\/SeloLeitura>/);
  assert.match(CLIENTE, /<SeloLeitura tom="atencao">inclui FBS<\/SeloLeitura>\s*<InfoDica texto=\{AVISO_FILA_COM_FBS\} \/>/);
  assert.match(CABECALHO, /<InfoDica texto=\{definicao\} \/>/);
});

test("tela: o periodo fica no topo, so' na visao do gestor", () => {
  assert.match(CLIENTE, /controles=\{visao === "gestor" \? controlePeriodo : undefined\}/);
  assert.match(CLIENTE, /const controlePeriodo = \([\s\S]*?<Segmented\s+rotulo="Período"/);
  assert.match(CLIENTE, /periodoConsultado \? <InfoDica texto=\{periodoConsultado\} \/> : null/);
});

test("LDR: trocar o periodo nao apaga a divisao da fila de agora; trocar a marca, sim", () => {
  const PAINEL = fonte("src/components/expedicao/LdrShopeePanel.tsx");
  assert.match(PAINEL, /if \(marcaDaResposta\.current !== marca\) setResposta\(null\);/);
  assert.match(PAINEL, /marcaDaResposta\.current = marca;\s*setResposta\(json\);/);
});

test("tabela unica: com fotografia, o apelido da conta e' o dela (uma numeracao so')", () => {
  const html = renderToStaticMarkup(
    h(EvolucaoPorMarca, {
      resposta: tendencia([ponto("a", "apice", H(13), 50), ponto("a", "apice", H(15), 60)]),
      fila: [
        { ...linha("a", "ÁPICE", 61, 25), apelido: "conta 1 de 2" },
        { ...linha("b", "ÁPICE", 9, 0), apelido: "conta 2 de 2" },
      ],
    }),
  );
  assert.match(html, /conta 1 de 2/);
  assert.match(html, /conta 2 de 2/);
});
