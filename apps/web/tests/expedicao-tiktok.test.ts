// Gate EXP-TK-OPS-1/2 — contrato da tela de LDR do TikTok Shop.
//
// O foco e' o que erra em silencio na tela: um `0%` mostrado numa coorte que
// ainda nao venceu, os dois limiares confundidos (a tela diria "dentro da meta"
// num dia de 9%), uma data que anda um dia para tras por causa do fuso, e uma
// barra que some porque a normalizacao do grafico esta errada.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  AVISO_PRAZO_RECONSTRUIDO,
  EVENTOS,
  EVENTO_PADRAO,
  EXPLICACAO_FLUXO,
  EXPLICACAO_LDR,
  EXPLICACAO_META,
  JANELA_INICIAL_DIAS,
  LIMIAR_CRITICO_INTERNO,
  META_TIKTOK_LDR,
  ROTULO_EVENTO,
  ROTULO_LIMIAR_INTERNO,
  ROTULO_META,
  ROTULO_SEVERIDADE,
  SELO_ACIMA_DA_META,
  SELO_DENTRO_DA_META,
  TITULO_FLUXO,
  TITULO_LDR,
  VALOR_SEM_TAXA,
  alturaDaBarra,
  alturaDaMeta,
  contarCriticos,
  contarForaDaMeta,
  descreverIncidente,
  explicarIndisponivel,
  formatarDiaCurto,
  formatarDiaLongo,
  formatarInteiro,
  formatarTaxa,
  marcasForaDaMeta,
  queryDoTikTok,
  severidadeDoDia,
  taxaMaxima,
  type TikTokLdr,
  type TikTokLdrDia,
} from "../src/lib/expedicao-tiktok-contract.ts";

function dia(p: Partial<TikTokLdrDia> & { due_date: string }): TikTokLdrDia {
  return {
    base: 100,
    late: 0,
    pending_overdue: 0,
    pending_on_time: 0,
    rate: 0,
    is_mature: true,
    above_target: false,
    is_critical: false,
    ...p,
  };
}

// ---------------------------------------------------------------------------
// Eventos e prazos
// ---------------------------------------------------------------------------
test("os dois eventos existem e a coleta e' o padrao", () => {
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

test("o aviso do prazo diz que o numero nao e' o do TikTok e cita os dois prazos", () => {
  assert.match(AVISO_PRAZO_RECONSTRUIDO, /reconstru/i);
  assert.match(AVISO_PRAZO_RECONSTRUIDO, /1 dia/);
  assert.match(AVISO_PRAZO_RECONSTRUIDO, /2 para/);
});

// ---------------------------------------------------------------------------
// Os DOIS limiares — nomes e valores distintos
// ---------------------------------------------------------------------------
test("meta do TikTok e limiar interno sao numeros e nomes diferentes", () => {
  // Confundi-los faria a tela dizer "dentro da meta" num dia de 9%.
  assert.equal(META_TIKTOK_LDR, 0.04);
  assert.equal(LIMIAR_CRITICO_INTERNO, 0.1);
  assert.ok(META_TIKTOK_LDR < LIMIAR_CRITICO_INTERNO);
  assert.notEqual(ROTULO_META, ROTULO_LIMIAR_INTERNO);
  assert.match(ROTULO_META, /TikTok/);
  assert.match(ROTULO_LIMIAR_INTERNO, /interno/i);
});

test("as duas leituras tem titulos e explicacoes distintos", () => {
  assert.notEqual(TITULO_LDR, TITULO_FLUXO);
  assert.match(TITULO_LDR, /vencimento/i);
  assert.match(TITULO_FLUXO, /pagamento/i);
  // a explicacao do fluxo precisa dizer que ele NAO e' a LDR
  assert.match(EXPLICACAO_FLUXO, /não é a LDR/i);
  assert.match(EXPLICACAO_LDR, /vence/i);
});

// ---------------------------------------------------------------------------
// Severidade — le os selos da API, nao recalcula
// ---------------------------------------------------------------------------
test("coorte imatura e' sempre parcial, por pior que pareca a taxa", () => {
  const d = dia({ due_date: "2026-09-24", rate: 0.9, is_mature: false, is_critical: true });
  assert.equal(severidadeDoDia(d), "parcial");
});

test("coorte imatura com taxa zero tambem e' parcial, nao verde", () => {
  // Este e' o caso de hoje: quase sempre 0%, quase nunca termina em 0%.
  assert.equal(severidadeDoDia(dia({ due_date: "x", rate: 0, is_mature: false })), "parcial");
});

test("os selos da API mandam na cor, e a tela nao recalcula o limiar", () => {
  assert.equal(severidadeDoDia(dia({ due_date: "a", rate: 0.02 })), "ok");
  assert.equal(
    severidadeDoDia(dia({ due_date: "b", rate: 0.05, above_target: true })),
    "fora_da_meta",
  );
  assert.equal(
    severidadeDoDia(dia({ due_date: "c", rate: 0.2, above_target: true, is_critical: true })),
    "critico",
  );
});

test("cada severidade tem rotulo distinto, e nenhum atribui o veredito ao TikTok", () => {
  const rotulos = Object.values(ROTULO_SEVERIDADE);
  assert.equal(new Set(rotulos).size, rotulos.length);
  // O rotulo diz que a MEDICAO e' nossa. Quem declara penalizacao e' o TikTok,
  // com o proprio relogio e o proprio denominador — nao esta tela.
  assert.match(ROTULO_SEVERIDADE.fora_da_meta, /medição interna/i);
  assert.match(ROTULO_SEVERIDADE.ok, /medição interna/i);
  assert.match(ROTULO_SEVERIDADE.critico, /interno/i);
  for (const r of rotulos) {
    assert.ok(!/penaliz/i.test(r), r);
  }
});

test("o selo diz 'acima da meta', e nunca 'fora da meta'", () => {
  // "Fora da meta" soa a veredito da plataforma. O que sabemos e' que a NOSSA
  // conta passou de 4%.
  assert.match(SELO_ACIMA_DA_META, /acima da meta/i);
  assert.match(SELO_ACIMA_DA_META, /medição interna/i);
  assert.ok(!/fora da meta/i.test(SELO_ACIMA_DA_META));
  assert.match(SELO_DENTRO_DA_META, /medição interna/i);
});

test("a ressalva da meta carrega as tres coisas que o operador precisa saber", () => {
  // 1. de quem e' o 4%; 2. que o prazo e' reconstruido; 3. que nao e' a
  // medicao oficial de penalizacao. Sem qualquer uma delas o numero sai desta
  // tela para uma reuniao como se fosse da plataforma.
  assert.match(EXPLICACAO_META, /4%/);
  assert.match(EXPLICACAO_META, /TikTok/);
  assert.match(EXPLICACAO_META, /reconstru/i);
  assert.match(EXPLICACAO_META, /penaliza/i);
  assert.match(EXPLICACAO_META, /ainda não é/i);
});

test("o rotulo da referencia nao a chama de meta oficial", () => {
  assert.match(ROTULO_META, /refer/i);
  assert.ok(!/oficial/i.test(ROTULO_META));
});

// ---------------------------------------------------------------------------
// Formatacao
// ---------------------------------------------------------------------------
test("taxa ausente nunca vira 0%", () => {
  assert.equal(formatarTaxa(null), VALOR_SEM_TAXA);
  assert.equal(formatarTaxa(undefined), VALOR_SEM_TAXA);
  assert.equal(formatarTaxa(Number.NaN), VALOR_SEM_TAXA);
  assert.equal(formatarTaxa(0), "0,00%");
  assert.equal(formatarTaxa(0.0518), "5,18%");
  assert.equal(formatarTaxa(0.04, 0), "4%");
});

test("inteiro ausente tambem nao vira zero", () => {
  assert.equal(formatarInteiro(null), VALOR_SEM_TAXA);
  assert.equal(formatarInteiro(0), "0");
  assert.equal(formatarInteiro(36495), "36.495");
});

test("a data da coorte nao anda um dia para tras", () => {
  // `new Date("2026-09-07")` e' meia-noite UTC e volta 06/09 em fuso negativo.
  assert.equal(formatarDiaCurto("2026-09-07"), "07/09");
  assert.equal(formatarDiaLongo("2026-09-07"), "07/09/2026");
  assert.equal(formatarDiaCurto("2026-01-01"), "01/01");
});

// ---------------------------------------------------------------------------
// Incidente e contagens
// ---------------------------------------------------------------------------
test("sem incidente nao inventa periodo", () => {
  const j = { incident_start: null, incident_end: null } as unknown as TikTokLdr;
  assert.equal(descreverIncidente(j), null);
  assert.equal(descreverIncidente(null), null);
});

test("incidente de um vencimento so' nao vira intervalo", () => {
  const j = {
    incident_start: "2026-09-17",
    incident_end: "2026-09-17",
  } as unknown as TikTokLdr;
  assert.match(descreverIncidente(j)!, /Um vencimento/);
});

test("incidente com inicio e fim descreve o intervalo", () => {
  const j = {
    incident_start: "2026-09-10",
    incident_end: "2026-09-17",
  } as unknown as TikTokLdr;
  const t = descreverIncidente(j)!;
  assert.match(t, /10\/09\/2026/);
  assert.match(t, /17\/09\/2026/);
});

test("conta fora-da-meta e critico separadamente", () => {
  const dias = [
    dia({ due_date: "a", rate: 0.05, above_target: true }),
    dia({ due_date: "b", rate: 0.2, above_target: true, is_critical: true }),
    dia({ due_date: "c", rate: 0.01 }),
    dia({ due_date: "d", rate: 0.9, is_mature: false }),
  ];
  assert.equal(contarForaDaMeta(dias), 2);
  assert.equal(contarCriticos(dias), 1);
});

// ---------------------------------------------------------------------------
// Marcas
// ---------------------------------------------------------------------------
test("so' as marcas fora da meta entram no destaque", () => {
  const marcas = [
    { brand: "kokeshi", shop_name: null, base: 100, late: 36, rate: 0.36, above_target: true },
    { brand: "gocase", shop_name: null, base: 100, late: 0, rate: 0.002, above_target: false },
  ];
  assert.deepEqual(marcasForaDaMeta(marcas).map((m) => m.brand), ["kokeshi"]);
});

// ---------------------------------------------------------------------------
// Grafico
// ---------------------------------------------------------------------------
test("a barra normaliza pelo maximo da serie, nao por 100%", () => {
  const dias = [dia({ due_date: "a", rate: 0.03 }), dia({ due_date: "b", rate: 0.015 })];
  const max = taxaMaxima(dias);
  assert.equal(max, 0.03);
  assert.equal(alturaDaBarra(dias[0], max), 100);
  assert.equal(alturaDaBarra(dias[1], max), 50);
});

test("taxa pequena mas nao nula continua visivel", () => {
  const dias = [dia({ due_date: "a", rate: 0.889 }), dia({ due_date: "b", rate: 0.0001 })];
  assert.ok(alturaDaBarra(dias[1], taxaMaxima(dias)) >= 2);
});

test("taxa ausente nao desenha barra e serie vazia nao divide por zero", () => {
  assert.equal(alturaDaBarra(dia({ due_date: "a", rate: null }), 0.5), 0);
  assert.equal(taxaMaxima([dia({ due_date: "a", rate: null })]), 0);
  assert.equal(alturaDaBarra(dia({ due_date: "a", rate: null }), 0), 0);
});

test("a linha da meta so' aparece quando cabe na escala", () => {
  // Numa janela calma (maximo 2%) a meta de 4% ficaria fora do grafico; desenhar
  // uma linha acima do topo sugeriria que o dia encostou nela.
  assert.equal(alturaDaMeta(0.02), null);
  assert.equal(alturaDaMeta(0.08), 50);
  assert.equal(alturaDaMeta(0), null);
});

// ---------------------------------------------------------------------------
// Indisponibilidade
// ---------------------------------------------------------------------------
test("cada motivo de indisponibilidade tem texto proprio", () => {
  assert.match(explicarIndisponivel("table_not_migrated"), /migration/i);
  assert.match(explicarIndisponivel("no_series_published"), /publicad/i);
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
  assert.equal(queryDoTikTok("coleta", 7), "/api/v1/expedicao/tiktok?event=coleta&days=7");
  assert.equal(
    queryDoTikTok("despacho", 30),
    "/api/v1/expedicao/tiktok?event=despacho&days=30",
  );
});

test("a janela inicial e' 7 dias + hoje", () => {
  assert.equal(JANELA_INICIAL_DIAS, 7);
});

// ===========================================================================
// T-0010 — formato unico de percentual da Expedicao
// ===========================================================================
import {
  TEXTO_INDISPONIVEL,
  formatarBase,
  formatarDeltaPp,
  formatarPercentual,
  formatarPercentualComBase,
  rotuloCoorte,
  rotuloJanela,
} from "../src/lib/expedicao-formato.ts";
import { formatarTaxaLdr } from "../src/lib/expedicao-shopee-ldr.ts";
import { readFileSync as lerFonte } from "node:fs";
import { join as juntar } from "node:path";

test("percentual com base: pt-BR, 1 casa por padrao, base ao lado", () => {
  assert.equal(formatarPercentualComBase(62, 1820), "3,4% · 62 de 1.820");
  assert.equal(formatarPercentualComBase(62, 1820, { casas: 2 }), "3,41% · 62 de 1.820");
  assert.equal(formatarPercentualComBase(0, 50), "0,0% · 0 de 50", "zero medido continua zero");
  assert.equal(formatarPercentualComBase(1820, 1820), "100,0% · 1.820 de 1.820");
});

test("base 0, null, ausente ou negativa e' 'indisponível', nunca 0%", () => {
  for (const base of [0, null, undefined, -3, Number.NaN]) {
    assert.equal(formatarPercentualComBase(0, base as number), TEXTO_INDISPONIVEL, String(base));
    assert.equal(formatarBase(0, base as number), TEXTO_INDISPONIVEL, String(base));
  }
  assert.equal(TEXTO_INDISPONIVEL, "indisponível");
  assert.equal(formatarPercentualComBase(null, 10), TEXTO_INDISPONIVEL, "numerador ausente");
});

test("arredondamento: meia unidade para longe do zero, sem '-0'", () => {
  // o valor exato do double decide, como no toFixed de antes
  assert.equal(formatarPercentual(0.0345), `${(0.0345 * 100).toFixed(1).replace(".", ",")}%`);
  assert.equal(formatarPercentual(0.125), "12,5%");
  assert.equal(formatarPercentual(0.12345, { casas: 2 }), "12,35%");
  assert.equal(formatarPercentual(1 / 3), "33,3%");
  assert.equal(formatarPercentual(2 / 3), "66,7%");
  assert.equal(formatarPercentual(-0), "0,0%");
  assert.equal(formatarPercentual(null), VALOR_SEM_TAXA);
});

test("a taxa publicada pela API e' exibida sem ser recalculada", () => {
  // 5 de 100 daria 5,0%; a API publicou 5,18% (outro recorte): vale a da API.
  assert.equal(formatarPercentualComBase(5, 100, { taxa: 0.0518, casas: 2 }), "5,18% · 5 de 100");
  assert.equal(formatarPercentualComBase(5, 100, { taxa: null }), TEXTO_INDISPONIVEL);
});

test("delta em pontos percentuais com sinal tipografico", () => {
  assert.equal(formatarDeltaPp(0.042, 0.034), "+0,8 p.p.");
  assert.equal(formatarDeltaPp(0.022, 0.034), "\u22121,2 p.p.");
  assert.equal(formatarDeltaPp(0.034, 0.034), "0,0 p.p.");
  assert.equal(formatarDeltaPp(0.0340001, 0.034), "0,0 p.p.", "arredondado a zero nao ganha sinal");
  assert.equal(formatarDeltaPp(null, 0.03), TEXTO_INDISPONIVEL);
  assert.equal(formatarDeltaPp(0.03, undefined), TEXTO_INDISPONIVEL);
});

test("rotulos de janela e de coorte", () => {
  assert.equal(rotuloJanela(7, "pagamento"), "últimos 7 dias · por dia de pagamento");
  assert.equal(rotuloJanela(1), "último dia");
  assert.equal(rotuloJanela(0), "janela não informada");
  assert.equal(rotuloCoorte("2026-10-19", "2026-10-25", "pagamento"), "pedidos pagos de 19/10 a 25/10");
  assert.equal(rotuloCoorte("2026-10-25", "2026-10-25", "vencimento"), "pedidos com vencimento em 25/10");
});

test("regressao TikTok/Shopee: os digitos exibidos nao mudam com o formato unico", () => {
  // Implementacao ANTERIOR, copiada aqui como oraculo.
  const antiga = (v: number | null, casas = 2) =>
    v === null || !Number.isFinite(v) ? "—" : `${(v * 100).toFixed(casas).replace(".", ",")}%`;
  const amostra = [0, 0.0001, 0.0049, 0.005, 0.0399, 0.04, 0.0401, 0.0518, 0.0335, 0.1, 0.12345, 0.889, 1, null];
  for (const v of amostra) {
    assert.equal(formatarTaxa(v), antiga(v), `TikTok ${v}`);
    assert.equal(formatarTaxa(v, 0), antiga(v, 0), `TikTok 0 casas ${v}`);
    assert.equal(formatarTaxaLdr(v), antiga(v), `Shopee ${v}`);
  }
  const inteiroAntigo = (n: number) => new Intl.NumberFormat("pt-BR").format(n);
  for (const n of [0, 7, 1820, 36495, 1234567]) assert.equal(formatarInteiro(n), inteiroAntigo(n));
});

test("nenhum formato de numero divergente sobrou na Expedicao", () => {
  const raiz = juntar(import.meta.dirname, "..");
  for (const rel of [
    "app/expedicao/ExpedicaoClient.tsx",
    "src/components/expedicao/TikTokDispatchPanel.tsx",
    "src/components/expedicao/LdrShopeePanel.tsx",
    "src/components/expedicao/EvolucaoPorMarca.tsx",
    "src/lib/expedicao-tiktok-contract.ts",
    "src/lib/expedicao-shopee-ldr.ts",
  ]) {
    const codigo = lerFonte(juntar(raiz, rel), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    assert.doesNotMatch(codigo, /NumberFormat\("pt-BR"\)\.format|toFixed\([^)]*\)\.replace\("\."/, rel);
  }
});
