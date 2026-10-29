/**
 * Gate EXPEDICAO-UX-2 — evolucao do backlog por marca, em small multiples.
 *
 * O que erra em silencio aqui: o identificador da conta virando titulo; duas
 * contas da mesma marca somadas; hora sem ponto virando zero ou linha
 * costurada; uma janela de 72 h apresentada como preenchida com 3 h de
 * historico; e a hora exibida no fuso da maquina em vez do de Brasilia.
 */
import { register } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import type { PontoTendencia, RespostaTendencia } from "../src/lib/expedicao-contract.ts";
import {
  FOLGA_COBERTURA_HORAS,
  ROTULO_SEM_MARCA,
  coberturaDaEvolucao,
  descreverCobertura,
  dominioHorizontal,
  formatarHoraBrt,
  faixaDaSerie,
  lerVariacao,
  periodoDaVariacao,
  BASE_MINIMA_PERCENTUAL,
  HORAS_MINIMAS_PERCENTUAL,
  montarEvolucaoPorMarca,
} from "../src/lib/expedicao-evolucao.ts";

register("./helpers/tsx-loader.mjs", import.meta.url);

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8");
const CLIENTE = fonte("app/expedicao/ExpedicaoClient.tsx");
const COMPONENTE = fonte("src/components/expedicao/EvolucaoPorMarca.tsx");

/** 21/10/2026 10:00 em Brasilia = 13:00 UTC. */
const H = (horaUtc: number) => `2026-10-21T${String(horaUtc).padStart(2, "0")}:00:00Z`;

function ponto(conta: string, marca: string | null, hora: string, backlog: number): PontoTendencia {
  return {
    snapshot_hour: hora,
    shop_account: conta,
    brand: marca as string,
    refresh_batch_id: "b",
    observed_at: hora,
    source_watermark_at: null,
    source_advanced: true,
    run_status: "success",
    backlog_count: backlog,
    overdue_count: 0,
    due_within_24h_count: 0,
    on_time_count: 0,
    deadline_unavailable_count: backlog,
    over_48h_count: 7,
    slow_count: 0,
    zombie_count: 0,
    stalled_count: 2,
  };
}

function resposta(points: PontoTendencia[], janela = 72): RespostaTendencia {
  const horas = points.map((p) => p.snapshot_hour).sort();
  return {
    availability: "available",
    unavailable_reason: null,
    channel: "mercadolivre",
    window_hours: janela,
    from_hour: horas[0] ?? null,
    to_hour: horas.at(-1) ?? null,
    points,
    truncated: false,
    limitations: {
      no_automation: true,
      load_mode: "manual_snapshot",
      snapshot_age_hours: 0.5,
      brands_not_covered: [],
      order_identifier_withheld: true,
      notes: [],
    },
  };
}

// ===========================================================================
// 1. Marca no titulo, conta fora dele
// ===========================================================================
test("o titulo e' a marca da propria serie, nunca o identificador da conta", () => {
  const [s] = montarEvolucaoPorMarca([ponto("2227056661", "kokeshi", H(13), 10)]);
  assert.equal(s.titulo, "KOKESHI");
  assert.equal(s.conta, "2227056661");
  assert.ok(!s.titulo.includes("2227056661"));
  assert.equal(s.apelido, null);
});

test("a relacao conta -> marca vem do ponto, nao de um mapa fixo", () => {
  // Mesma conta, outra marca no payload: o titulo segue o payload.
  const [s] = montarEvolucaoPorMarca([ponto("2227056661", "barbours", H(13), 10)]);
  assert.equal(s.titulo, "BARBOURS");
  // Marca desconhecida aparece pelo proprio slug, nunca some.
  const [n] = montarEvolucaoPorMarca([ponto("9", "marca-nova", H(13), 1)]);
  assert.equal(n.titulo, "MARCA-NOVA");
  assert.doesNotMatch(COMPONENTE + fonte("src/lib/expedicao-evolucao.ts"), /2227056661|1366932565/);
});

test("marca sem nome vira 'Marca não informada' e vai para o fim", () => {
  const series = montarEvolucaoPorMarca([
    ponto("111", "", H(13), 5),
    ponto("222", "  ", H(13), 6),
    ponto("333", "lescent", H(13), 7),
  ]);
  assert.equal(series[0].titulo, "LESCENT");
  assert.equal(series[1].titulo, ROTULO_SEM_MARCA);
  assert.equal(series[2].titulo, ROTULO_SEM_MARCA);
  assert.equal(series[1].marca, null);
  // as duas sem marca nao se fundem: cada conta segue sua serie
  assert.deepEqual(series.slice(1).map((s) => s.apelido), ["conta 1 de 2", "conta 2 de 2"]);
});

test("duas contas da mesma marca NAO sao somadas: dois cartoes com apelido", () => {
  const series = montarEvolucaoPorMarca([
    ponto("B-2", "barbours", H(13), 100),
    ponto("B-1", "barbours", H(13), 40),
    ponto("B-2", "barbours", H(16), 110),
    ponto("B-1", "barbours", H(16), 30),
  ]);
  assert.equal(series.length, 2);
  assert.deepEqual(series.map((s) => [s.conta, s.apelido, s.atual]), [
    ["B-1", "conta 1 de 2", 30],
    ["B-2", "conta 2 de 2", 110],
  ]);
  assert.ok(series.every((s) => s.titulo === "BARBOURS"));
  assert.ok(!series.some((s) => s.atual === 140), "nenhuma soma de contas");
});

// ===========================================================================
// 2. Estados da serie
// ===========================================================================
test("um ponto so': historico insuficiente, sem variacao inventada", () => {
  const [s] = montarEvolucaoPorMarca([ponto("a", "apice", H(13), 50)]);
  assert.equal(s.estado, "insuficiente");
  assert.equal(s.variacaoAbs, null);
  assert.equal(s.variacaoPct, null);
  assert.deepEqual(lerVariacao(s, 72), { principal: "—", secundario: "um ponto só" });
  assert.equal(s.trechos.length, 1);
  assert.equal(s.trechos[0].length, 1, "um ponto nao vira linha");
});

test("dois pontos: variacao absoluta e percentual sobre o primeiro", () => {
  const [s] = montarEvolucaoPorMarca([ponto("a", "apice", H(13), 200), ponto("a", "apice", H(16), 230)]);
  assert.equal(s.estado, "variou");
  assert.equal(s.variacaoAbs, 30);
  assert.equal(s.variacaoPct, 0.15);
  assert.deepEqual(lerVariacao(s, 48), { principal: "+30", secundario: "+15,0%" });
  const [q] = montarEvolucaoPorMarca([ponto("a", "apice", H(13), 200), ponto("a", "apice", H(16), 150)]);
  assert.deepEqual(lerVariacao(q, 48), { principal: "−50", secundario: "−25,0%" });
});

test("serie sem variacao e' dita como tal", () => {
  const [s] = montarEvolucaoPorMarca([
    ponto("a", "apice", H(13), 80),
    ponto("a", "apice", H(16), 80),
    ponto("a", "apice", H(17), 80),
  ]);
  assert.equal(s.estado, "sem_variacao");
  assert.equal(s.variacaoAbs, 0);
});

test("primeiro ponto em zero: variacao % nao existe (nunca infinito)", () => {
  const [s] = montarEvolucaoPorMarca([ponto("a", "apice", H(13), 0), ponto("a", "apice", H(16), 5)]);
  assert.equal(s.variacaoPct, null);
  assert.deepEqual(lerVariacao(s, 48), { principal: "+5", secundario: null });
});

test("hora sem ponto quebra a linha e nunca vira zero", () => {
  // A conta `a` nao tem a hora 16, que a conta `b` tem.
  const series = montarEvolucaoPorMarca([
    ponto("a", "apice", H(13), 10),
    ponto("b", "barbours", H(13), 20),
    ponto("b", "barbours", H(16), 21),
    ponto("a", "apice", H(17), 12),
    ponto("b", "barbours", H(17), 22),
  ]);
  const a = series.find((s) => s.conta === "a")!;
  assert.equal(a.pontos.length, 2, "nenhum ponto fabricado");
  assert.ok(!a.pontos.some((p) => p.backlog === 0));
  assert.equal(a.trechos.length, 2, "a lacuna separa dois trechos");
  assert.equal(a.lacunas, 1);
  const b = series.find((s) => s.conta === "b")!;
  assert.equal(b.trechos.length, 1);
  assert.equal(b.lacunas, 0);
});

test("antes do primeiro ponto nao existe linha nem valor replicado", () => {
  const series = montarEvolucaoPorMarca([
    ponto("velha", "apice", H(10), 5),
    ponto("velha", "apice", H(13), 6),
    ponto("nova", "kokeshi", H(13), 9),
  ]);
  const nova = series.find((s) => s.conta === "nova")!;
  assert.equal(nova.pontos.length, 1);
  assert.equal(nova.primeiro, H(13));
  assert.equal(nova.lacunas, 0, "hora antes do inicio da conta nao e' lacuna");
});

// ===========================================================================
// 3. Cobertura real x janela pedida
// ===========================================================================
test("72 h pedidas com 3 h de historico: a tela diz quanto ha", () => {
  const r = resposta([ponto("a", "apice", H(13), 1), ponto("a", "apice", H(16), 2)], 72);
  const c = coberturaDaEvolucao(r);
  assert.equal(c.horasCobertas, 3);
  assert.equal(c.parcial, true);
  const d = descreverCobertura(c);
  assert.equal(d.desde, "Histórico disponível desde 21/10/2026 10:00.");
  assert.equal(d.exibindo, "Exibindo 3 h disponíveis das 72 h selecionadas.");
});

test("um unico horario tambem e' dito, sem fingir intervalo", () => {
  const d = descreverCobertura(coberturaDaEvolucao(resposta([ponto("a", "apice", H(13), 1)], 24)));
  assert.equal(d.exibindo, "Exibindo um único horário com dados das 24 h selecionadas.");
});

test("janela coberta (com a folga da cadencia) nao ganha aviso", () => {
  // 24 h pedidas, pontos a cada 2 h de 00 a 22 UTC: 22 h cobertas.
  const pts = Array.from({ length: 12 }, (_, i) => ponto("a", "apice", H(i * 2), 10 + i));
  const c = coberturaDaEvolucao(resposta(pts, 24));
  assert.equal(c.horasCobertas, 22);
  assert.ok(24 - FOLGA_COBERTURA_HORAS <= 22);
  assert.equal(c.parcial, false);
  assert.deepEqual(descreverCobertura(c), { desde: null, exibindo: null });
});

test("resposta sem pontos nao vira cobertura nenhuma", () => {
  const c = coberturaDaEvolucao(resposta([], 72));
  assert.equal(c.horasCobertas, null);
  assert.deepEqual(descreverCobertura(c), { desde: null, exibindo: null });
  assert.deepEqual(montarEvolucaoPorMarca([]), []);
  assert.equal(dominioHorizontal([]), null);
});

test("a hora e' a de Brasilia, qualquer que seja o fuso da maquina", () => {
  assert.equal(formatarHoraBrt("2026-10-21T13:00:00Z"), "21/10/2026 10:00");
  assert.equal(formatarHoraBrt("2026-10-21T02:30:00Z"), "20/10/2026 23:30");
  assert.equal(formatarHoraBrt("2026-10-21T13:00:00Z", false), "21/10 10:00");
  assert.equal(formatarHoraBrt("lixo"), "—");
});

// ===========================================================================
// 4. Leitura da variacao (EXPEDICAO-UX-3) — sem escala, sem faixa artificial
// ===========================================================================
test("serie constante: 'Estável em N' e nenhuma faixa vertical", () => {
  const [s] = montarEvolucaoPorMarca([
    ponto("a", "apice", H(13), 102),
    ponto("a", "apice", H(16), 102),
  ]);
  assert.deepEqual(lerVariacao(s, 72), { principal: "Estável em 102", secundario: null });
  assert.equal(faixaDaSerie(s), null, "linha reta no meio, sem escala inventada");
});

test("serie que varia: faixa da PROPRIA serie, so' para desenhar a forma", () => {
  const series = montarEvolucaoPorMarca([
    ponto("grande", "apice", H(13), 600),
    ponto("grande", "apice", H(16), 610),
    ponto("pequena", "kokeshi", H(13), 10),
    ponto("pequena", "kokeshi", H(16), 14),
  ]);
  assert.deepEqual(faixaDaSerie(series.find((s) => s.conta === "pequena")!), [10, 14]);
  assert.deepEqual(faixaDaSerie(series.find((s) => s.conta === "grande")!), [600, 610]);
});

test("percentual so' com base e historico suficientes", () => {
  const [s] = montarEvolucaoPorMarca([ponto("a", "apice", H(13), 200), ponto("a", "apice", H(16), 230)]);
  // 3 h de historico: so' a diferenca absoluta.
  assert.deepEqual(lerVariacao(s, 3), { principal: "+30", secundario: null });
  // 24 h ou mais e base >= 20: o percentual aparece.
  assert.deepEqual(lerVariacao(s, HORAS_MINIMAS_PERCENTUAL), { principal: "+30", secundario: "+15,0%" });
  // base pequena: 4 -> 6 seria "+50%"; a tela mostra so' "+2".
  const [p] = montarEvolucaoPorMarca([ponto("b", "apice", H(13), 4), ponto("b", "apice", H(16), 6)]);
  assert.ok(4 < BASE_MINIMA_PERCENTUAL);
  assert.deepEqual(lerVariacao(p, 72), { principal: "+2", secundario: null });
});

test("historico curto e' dito como historico curto", () => {
  const curto = coberturaDaEvolucao(resposta([ponto("a", "apice", H(13), 1), ponto("a", "apice", H(17), 2)], 72));
  assert.equal(periodoDaVariacao(curto), "desde o início da série (4 h)");
  const um = coberturaDaEvolucao(resposta([ponto("a", "apice", H(13), 1)], 72));
  assert.equal(periodoDaVariacao(um), "desde o início da série (um único horário)");
  const pts = Array.from({ length: 12 }, (_, i) => ponto("a", "apice", H(i * 2), 10 + i));
  assert.equal(periodoDaVariacao(coberturaDaEvolucao(resposta(pts, 24))), "nas últimas 24 h");
});

// ===========================================================================
// 5. Fiacao
// ===========================================================================
test("o painel e' 'por marca' e a tabela de dados mostra marca e conta", () => {
  assert.match(CLIENTE, /titulo="Evolução do backlog por marca"/);
  assert.match(CLIENTE, /<th scope="col">Marca<\/th>/);
  assert.match(CLIENTE, /<th scope="col">Hora \(Brasília\)<\/th>/);
  assert.match(CLIENTE, /\{tendencia \? <EvolucaoPorMarca resposta=\{tendencia\} \/> : null\}/);
});

test("o tooltip le a resposta: marca, conta, hora de Brasilia, backlog, 48 h, travados", () => {
  for (const s of ["{serie.titulo}", "conta {serie.conta}", "(Brasília)", "Backlog", "Acima de 48 h", "Travados"]) {
    assert.ok(COMPONENTE.includes(s), s);
  }
  assert.doesNotMatch(COMPONENTE, /fetch\(/, "nenhuma chamada por hover");
});

test("sem escala escrita, sem toggle de escala, cor so' para risco", () => {
  assert.doesNotMatch(COMPONENTE, /Escala do cartão|Escala comum|Variação %|Volume absoluto/);
  assert.doesNotMatch(COMPONENTE, /stroke-accent|fill-accent/, "a linha nao e' decorada com cor");
  assert.doesNotMatch(COMPONENTE, /(stroke|fill)-(ok|warn|crit)\b/);
});

test("a evolucao e' tabela, nao cartoes dentro de painel", () => {
  assert.doesNotMatch(COMPONENTE, /import Panel/);
  assert.match(COMPONENTE, /<th scope="col">Marca<\/th>/);
  for (const c of ["Backlog atual", "Variação", "Acima de 48 h", "Travados", "Tendência"]) {
    assert.ok(COMPONENTE.includes(c), c);
  }
});

// ===========================================================================
// 6. Renderizacao
// ===========================================================================
const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: EvolucaoPorMarca } = await import("../src/components/expedicao/EvolucaoPorMarca.tsx");

test("render: tabela por marca, conta ausente do HTML, estados escritos", () => {
  const html = renderToStaticMarkup(
    h(EvolucaoPorMarca, {
      resposta: resposta(
        [
          ponto("2227056661", "kokeshi", H(13), 300),
          ponto("2227056661", "kokeshi", H(16), 320),
          ponto("1366932565", "rituaria", H(13), 102),
          ponto("1366932565", "rituaria", H(16), 102),
          ponto("1717171717", "lescent", H(16), 12),
        ],
        72,
      ),
    }),
  );
  assert.match(html, /<th scope="row"[^>]*>KOKESHI/);
  assert.match(html, /<th scope="row"[^>]*>RITUÁRIA/);
  assert.doesNotMatch(html, /2227056661|1366932565|1717171717/, "id so' no tooltip, que nasce fechado");
  assert.match(html, /Estável em 102/);
  assert.match(html, /um ponto só/);
  assert.match(html, />\+20</, "variacao absoluta");
  assert.doesNotMatch(html, /6,7%/, "3 h de historico: sem percentual");
  assert.match(html, /desde o início da série \(3 h\)/);
  assert.match(html, /Histórico disponível desde 21\/10\/2026 10:00\./);
  assert.match(html, /Exibindo 3 h disponíveis das 72 h selecionadas\./);
  assert.doesNotMatch(html, /Escala|radiogroup/);
  assert.equal((html.match(/<polyline/g) ?? []).length, 2, "um ponto so' nao desenha linha");
  assert.equal((html.match(/<circle/g) ?? []).length, 5, "um circulo por ponto real");
  assert.equal((html.match(/tabindex="0"/gi) ?? []).length, 3, "um ponto de tabulacao por linha");
});

// ===========================================================================
// T-0021 — variacao: absoluto e percentual com separador para o leitor de tela
// ===========================================================================
test("render: a variacao le '−12 · −12,0%' (separador em sr-only), visual igual", () => {
  const html = renderToStaticMarkup(
    h(EvolucaoPorMarca, {
      resposta: resposta(
        [
          ponto("2227056661", "kokeshi", "2026-10-20T13:00:00Z", 100),
          ponto("2227056661", "kokeshi", "2026-10-21T13:00:00Z", 88),
        ],
        72,
      ),
    }),
  );
  const celula = html.match(/<td class="whitespace-nowrap[^"]*">([\s\S]*?)<\/td>/);
  assert.ok(celula, html);
  assert.match(celula![1], /<span class="sr-only"> · <\/span><span class="ml-1 [^"]*">−12,0%<\/span>/);
  // O que o leitor de tela ouve: nada colado.
  assert.equal(celula![1].replace(/<[^>]+>/g, ""), "−12 · −12,0%");
  assert.doesNotMatch(html, /−12−12,0%/);
});

test("render: o motivo secundario ('um ponto só') tambem ganha o separador", () => {
  const html = renderToStaticMarkup(
    h(EvolucaoPorMarca, {
      resposta: resposta(
        [ponto("2227056661", "kokeshi", H(13), 300), ponto("2227056661", "kokeshi", H(16), 320), ponto("1717171717", "lescent", H(16), 12)],
        72,
      ),
    }),
  );
  assert.match(html, />—<span class="sr-only"> · <\/span><span class="ml-1 [^"]*">um ponto só<\/span>/);
  // sem secundario, sem separador solto
  assert.match(html, /<span class="relative">\+20<\/span><\/td>/);
});

test("QA F1: o `sr-only` da variacao fica dentro de um invólucro `relative` (nao ancora no BODY)", () => {
  const html = renderToStaticMarkup(
    h(EvolucaoPorMarca, {
      resposta: resposta(
        [
          ponto("2227056661", "kokeshi", "2026-10-20T13:00:00Z", 100),
          ponto("2227056661", "kokeshi", "2026-10-21T13:00:00Z", 88),
          ponto("1717171717", "lescent", H(16), 12),
        ],
        72,
      ),
    }),
  );
  const srOnly = html.match(/<span class="sr-only">/g) ?? [];
  assert.equal(srOnly.length, 2, "variacao com percentual + 'um ponto só'");
  // Todo `sr-only` tem como pai direto o invólucro `relative` da celula.
  const comPai = html.match(/<span class="relative">[^<]*<span class="sr-only">/g) ?? [];
  assert.equal(comPai.length, srOnly.length, html);
  assert.match(COMPONENTE, /<span className="relative">\s*\{v\.principal\}/);
});
