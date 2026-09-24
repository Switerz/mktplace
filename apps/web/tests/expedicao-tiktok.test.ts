// Gate EXP-TK-OPS-1 — contrato da tela de expedicao do TikTok Shop.
//
// O foco e' o que erra em silencio na tela: um `0%` mostrado numa coorte que
// ainda nao venceu, uma data que anda um dia para tras por causa do fuso, e
// uma barra que some porque a normalizacao do grafico esta errada. Nenhum
// desses casos quebra nada — todos apenas mentem.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  AVISO_PRAZO_RECONSTRUIDO,
  EVENTOS,
  EVENTO_PADRAO,
  JANELA_INICIAL_DIAS,
  LIMIAR_DIA_CRITICO,
  ROTULO_EVENTO,
  VALOR_SEM_TAXA,
  alturaDaBarra,
  contarDiasCriticos,
  descreverIncidente,
  divergenciaEmPp,
  explicarIndisponivel,
  formatarDiaCurto,
  formatarDiaLongo,
  formatarInteiro,
  formatarTaxa,
  marcasCriticas,
  queryDoTikTok,
  severidadeDoDia,
  taxaMaxima,
  type TikTokDia,
  type TikTokJanela,
} from "../src/lib/expedicao-tiktok-contract.ts";

function dia(p: Partial<TikTokDia> & { paid_date: string }): TikTokDia {
  return {
    deadline_date: "2026-09-25",
    pedidos_pagos: 100,
    cancelados: 0,
    atrasados: 0,
    pendentes_vencidos: 0,
    pendentes_no_prazo: 0,
    rate: 0,
    is_mature: true,
    is_critical: false,
    ...p,
  };
}

// ---------------------------------------------------------------------------
// Eventos
// ---------------------------------------------------------------------------
test("os dois eventos existem e a coleta e' o padrao", () => {
  // A coleta e' o evento em que o incidente aparece: na janela medida o
  // despacho ficou em ~0,05% e a coleta chegou a 88,9% num unico dia.
  assert.deepEqual([...EVENTOS], ["coleta", "despacho"]);
  assert.equal(EVENTO_PADRAO, "coleta");
});

test("cada evento tem rotulo em portugues, sem termo cru da fonte", () => {
  for (const e of EVENTOS) {
    const r = ROTULO_EVENTO[e];
    assert.ok(r && r.length > 3, e);
    assert.ok(!/IN_TRANSIT|rts_time|AWAITING/i.test(r), r);
  }
});

test("o aviso do prazo diz que o numero nao e' o do TikTok", () => {
  // Sem isto a tela afirma medir a penalizacao da plataforma, que ninguem mediu.
  assert.match(AVISO_PRAZO_RECONSTRUIDO, /reconstru/i);
  assert.match(AVISO_PRAZO_RECONSTRUIDO, /TikTok/);
});

// ---------------------------------------------------------------------------
// Formatacao
// ---------------------------------------------------------------------------
test("taxa ausente nunca vira 0%", () => {
  // `0%` numa coorte sem base seria lido como "dia perfeito".
  assert.equal(formatarTaxa(null), VALOR_SEM_TAXA);
  assert.equal(formatarTaxa(undefined), VALOR_SEM_TAXA);
  assert.equal(formatarTaxa(Number.NaN), VALOR_SEM_TAXA);
  assert.equal(formatarTaxa(0), "0,00%");
  assert.equal(formatarTaxa(0.889), "88,90%");
});

test("inteiro ausente tambem nao vira zero", () => {
  assert.equal(formatarInteiro(null), VALOR_SEM_TAXA);
  assert.equal(formatarInteiro(0), "0");
});

test("a data da coorte nao anda um dia para tras", () => {
  // `new Date("2026-09-07")` e' meia-noite UTC e volta 06/09 em qualquer fuso
  // negativo — o nosso. A tela inteira mostraria o dia errado.
  assert.equal(formatarDiaCurto("2026-09-07"), "07/09");
  assert.equal(formatarDiaLongo("2026-09-07"), "07/09/2026");
  assert.equal(formatarDiaCurto("2026-01-01"), "01/01");
  assert.equal(formatarDiaLongo("2026-12-31"), "31/12/2026");
});

// ---------------------------------------------------------------------------
// Severidade e maturacao
// ---------------------------------------------------------------------------
test("coorte imatura e' sempre parcial, por pior que pareca a taxa", () => {
  const d = dia({ paid_date: "2026-09-24", rate: 0.9, is_mature: false });
  assert.equal(severidadeDoDia(d), "parcial");
});

test("coorte imatura com taxa zero tambem e' parcial, nao verde", () => {
  // Este e' o caso de "hoje": quase sempre 0%, quase nunca termina em 0%.
  const d = dia({ paid_date: "2026-09-24", rate: 0, is_mature: false });
  assert.equal(severidadeDoDia(d), "parcial");
});

test("coorte madura classifica pelo limiar", () => {
  assert.equal(severidadeDoDia(dia({ paid_date: "a", rate: 0.0 })), "ok");
  assert.equal(severidadeDoDia(dia({ paid_date: "b", rate: 0.04 })), "ok");
  assert.equal(severidadeDoDia(dia({ paid_date: "c", rate: 0.05 })), "atencao");
  assert.equal(severidadeDoDia(dia({ paid_date: "d", rate: 0.1 })), "critico");
  assert.equal(severidadeDoDia(dia({ paid_date: "e", rate: 0.889 })), "critico");
});

test("coorte madura sem base e' ok e nao critica", () => {
  assert.equal(severidadeDoDia(dia({ paid_date: "f", rate: null, pedidos_pagos: 0 })), "ok");
});

test("o limiar da tela e' o mesmo da API", () => {
  // Se divergirem, o alerta e o destaque do grafico discordam na mesma tela.
  assert.equal(LIMIAR_DIA_CRITICO, 0.1);
});

// ---------------------------------------------------------------------------
// Incidente
// ---------------------------------------------------------------------------
test("sem incidente nao inventa periodo", () => {
  const j = { incident_start: null, incident_end: null } as unknown as TikTokJanela;
  assert.equal(descreverIncidente(j), null);
  assert.equal(descreverIncidente(null), null);
});

test("incidente de um dia so' nao vira intervalo", () => {
  const j = {
    incident_start: "2026-09-07",
    incident_end: "2026-09-07",
  } as unknown as TikTokJanela;
  assert.match(descreverIncidente(j)!, /Um dia/);
  assert.match(descreverIncidente(j)!, /07\/09\/2026/);
});

test("incidente com inicio e fim descreve o intervalo", () => {
  const j = {
    incident_start: "2026-09-06",
    incident_end: "2026-09-13",
  } as unknown as TikTokJanela;
  const t = descreverIncidente(j)!;
  assert.match(t, /06\/09\/2026/);
  assert.match(t, /13\/09\/2026/);
});

test("conta apenas os dias marcados como criticos pela API", () => {
  const dias = [
    dia({ paid_date: "2026-09-06", rate: 0.35, is_critical: true }),
    dia({ paid_date: "2026-09-07", rate: 0.889, is_critical: true }),
    dia({ paid_date: "2026-09-08", rate: 0.02 }),
    // imatura com taxa alta: a API nunca marca critica, e a tela nao recalcula
    dia({ paid_date: "2026-09-24", rate: 0.9, is_mature: false }),
  ];
  assert.equal(contarDiasCriticos(dias), 2);
});

// ---------------------------------------------------------------------------
// As duas leituras da janela
// ---------------------------------------------------------------------------
test("a divergencia entre as duas leituras e' exposta em pontos percentuais", () => {
  // Medido em 2026-09-24 na janela de 14 dias: 14,42% contra 10,46%.
  const j = {
    rate_ratio_of_totals: 0.1442,
    rate_mean_of_daily: 0.1046,
  } as unknown as TikTokJanela;
  const d = divergenciaEmPp(j)!;
  assert.ok(Math.abs(d - 3.96) < 0.01, String(d));
});

test("divergencia e' nula quando falta uma das leituras", () => {
  assert.equal(
    divergenciaEmPp({
      rate_ratio_of_totals: null,
      rate_mean_of_daily: 0.1,
    } as unknown as TikTokJanela),
    null,
  );
  assert.equal(divergenciaEmPp(null), null);
});

// ---------------------------------------------------------------------------
// Marcas
// ---------------------------------------------------------------------------
test("so' as marcas acima do limiar entram no destaque", () => {
  const marcas = [
    { brand: "kokeshi", shop_name: null, pedidos_pagos: 100, atrasados: 36, rate: 0.36 },
    { brand: "gocase", shop_name: null, pedidos_pagos: 100, atrasados: 0, rate: 0.002 },
    { brand: "novo", shop_name: null, pedidos_pagos: 0, atrasados: 0, rate: null },
  ];
  assert.deepEqual(
    marcasCriticas(marcas).map((m) => m.brand),
    ["kokeshi"],
  );
});

// ---------------------------------------------------------------------------
// Grafico
// ---------------------------------------------------------------------------
test("a barra normaliza pelo maximo da serie, nao por 100%", () => {
  // Numa janela calma (maximo 3%) normalizar por 100% deixaria todas as
  // barras invisiveis e esconderia justamente a variacao que importa.
  const dias = [
    dia({ paid_date: "a", rate: 0.03 }),
    dia({ paid_date: "b", rate: 0.015 }),
  ];
  const max = taxaMaxima(dias);
  assert.equal(max, 0.03);
  assert.equal(alturaDaBarra(dias[0], max), 100);
  assert.equal(alturaDaBarra(dias[1], max), 50);
});

test("taxa pequena mas nao nula continua visivel", () => {
  const dias = [dia({ paid_date: "a", rate: 0.889 }), dia({ paid_date: "b", rate: 0.0001 })];
  const max = taxaMaxima(dias);
  assert.ok(alturaDaBarra(dias[1], max) >= 2);
});

test("taxa ausente nao desenha barra", () => {
  assert.equal(alturaDaBarra(dia({ paid_date: "a", rate: null }), 0.5), 0);
});

test("serie toda sem taxa nao divide por zero", () => {
  const dias = [dia({ paid_date: "a", rate: null })];
  assert.equal(taxaMaxima(dias), 0);
  assert.equal(alturaDaBarra(dias[0], 0), 0);
});

// ---------------------------------------------------------------------------
// Indisponibilidade
// ---------------------------------------------------------------------------
test("cada motivo de indisponibilidade tem texto proprio", () => {
  assert.match(explicarIndisponivel("table_not_migrated"), /migration/i);
  assert.match(explicarIndisponivel("no_series_published"), /publicada|publicad/i);
});

test("motivo desconhecido nao ecoa o codigo cru na tela", () => {
  const t = explicarIndisponivel("<script>alert(1)</script>");
  assert.ok(!t.includes("<script>"));
  assert.equal(t, "Série indisponível.");
  assert.equal(explicarIndisponivel(null), "Série indisponível.");
});

// ---------------------------------------------------------------------------
// Query
// ---------------------------------------------------------------------------
test("a query carrega evento e janela", () => {
  assert.equal(
    queryDoTikTok("coleta", 7),
    "/api/v1/expedicao/tiktok?event=coleta&days=7",
  );
  assert.equal(
    queryDoTikTok("despacho", 30),
    "/api/v1/expedicao/tiktok?event=despacho&days=30",
  );
});

test("a janela inicial e' 7 dias + hoje", () => {
  assert.equal(JANELA_INICIAL_DIAS, 7);
});
