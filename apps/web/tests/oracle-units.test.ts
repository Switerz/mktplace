// Unidades puras: resolucao de periodo, validacao de entrada, envelope e
// rate limit. Sem rede, sem SDK, sem relogio real.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  desempenhoInput, inclusiveDays, MAX_RANGE_DAYS, produtosInput, regioesInput,
  resolvePeriod, todayInAppTimezone,
} from "../src/server/oracle/schemas.ts";
import { buildEnvelope, fmtMoney, textFallback } from "../src/server/oracle/envelope.ts";
import { SlidingWindowRateLimiter } from "../src/server/oracle/rate-limit.ts";

const NOW = new Date("2026-08-18T12:00:00.000Z");

// ---------------------------------------------------------------------------
// Fuso e periodo
// ---------------------------------------------------------------------------

test("hoje e' resolvido em America/Sao_Paulo, nao em UTC", () => {
  assert.equal(todayInAppTimezone(NOW), "2026-08-18");
  // 02:00 UTC de 19/08 ainda e' 18/08 em Sao Paulo (UTC-3).
  assert.equal(todayInAppTimezone(new Date("2026-08-19T02:00:00.000Z")), "2026-08-18");
});

test("mes_anterior resolve o mes calendario completo", () => {
  const p = resolvePeriod({ periodo: "mes_anterior" }, NOW);
  assert.deepEqual([p.start, p.end], ["2026-07-01", "2026-07-31"]);
  assert.equal(p.refMonth, "2026-07");
  assert.equal(p.includesCurrentDay, false);
});

test("mes_atual termina HOJE, nunca no fim do mes (sem inventar futuro)", () => {
  const p = resolvePeriod({ periodo: "mes_atual" }, NOW);
  assert.deepEqual([p.start, p.end], ["2026-08-01", "2026-08-18"]);
  assert.equal(p.includesCurrentDay, true);
});

test("ultimos_7_dias e' inclusivo nas duas pontas", () => {
  const p = resolvePeriod({ periodo: "ultimos_7_dias" }, NOW);
  assert.deepEqual([p.start, p.end], ["2026-08-12", "2026-08-18"]);
  assert.equal(inclusiveDays(p.start, p.end), 7);
});

test("virada de ano: janeiro -> dezembro do ano anterior", () => {
  const p = resolvePeriod({ periodo: "mes_anterior" }, new Date("2026-01-10T12:00:00.000Z"));
  assert.deepEqual([p.start, p.end], ["2025-12-01", "2025-12-31"]);
});

test("intervalo personalizado de exatamente 366 dias e' aceito", () => {
  const p = resolvePeriod(
    { periodo: "personalizado", data_inicio: "2025-08-18", data_fim: "2026-08-18" },
    NOW,
  );
  assert.equal(inclusiveDays(p.start, p.end), MAX_RANGE_DAYS);
});

test("367 dias e' rejeitado", () => {
  assert.throws(
    () =>
      resolvePeriod(
        { periodo: "personalizado", data_inicio: "2025-08-17", data_fim: "2026-08-18" },
        NOW,
      ),
    /intervalo maximo/i,
  );
});

test("data_inicio > data_fim e' rejeitado", () => {
  assert.throws(
    () =>
      resolvePeriod(
        { periodo: "personalizado", data_inicio: "2026-08-10", data_fim: "2026-08-01" },
        NOW,
      ),
    /posterior/i,
  );
});

test("data futura e' rejeitada", () => {
  assert.throws(
    () =>
      resolvePeriod(
        { periodo: "personalizado", data_inicio: "2026-08-01", data_fim: "2026-12-31" },
        NOW,
      ),
    /futura/i,
  );
});

test("personalizado sem datas e' rejeitado", () => {
  assert.throws(() => resolvePeriod({ periodo: "personalizado" }, NOW), /exige/i);
});

test("datas junto de preset e' rejeitado (nunca mistura fontes de periodo)", () => {
  assert.throws(
    () => resolvePeriod({ periodo: "mes_anterior", data_inicio: "2026-07-01" }, NOW),
    /personalizado/i,
  );
});

// ---------------------------------------------------------------------------
// Validacao de entrada
// ---------------------------------------------------------------------------

test("data inexistente no calendario e' rejeitada", () => {
  const r = desempenhoInput.safeParse({
    periodo: "personalizado",
    data_inicio: "2026-02-31",
    data_fim: "2026-03-01",
  });
  assert.equal(r.success, false);
});

test("formato de data invalido e' rejeitado", () => {
  for (const d of ["18/08/2026", "2026-8-1", "", "hoje", "2026-08-18T00:00:00Z"]) {
    const r = desempenhoInput.safeParse({
      periodo: "personalizado",
      data_inicio: d,
      data_fim: "2026-08-18",
    });
    assert.equal(r.success, false, `"${d}" deveria ser rejeitada`);
  }
});

test("enum fechado: canal e marca fora da lista sao rejeitados", () => {
  assert.equal(desempenhoInput.safeParse({ canais: ["magalu"] }).success, false);
  assert.equal(desempenhoInput.safeParse({ marcas: ["outra"] }).success, false);
  assert.equal(produtosInput.safeParse({ canal: "amazon" }).success, false);
});

test("parametro desconhecido e' rejeitado (strict)", () => {
  assert.equal(desempenhoInput.safeParse({ periodo: "mes_anterior", extra: 1 }).success, false);
});

test("defaults do contrato sao aplicados", () => {
  const r = desempenhoInput.parse({});
  assert.equal(r.periodo, "mes_anterior");
  assert.equal(r.granularidade, "none");
  assert.equal(r.comparar, false);

  assert.equal(produtosInput.parse({ canal: "tiktok" }).limite, 20);
  assert.equal(regioesInput.parse({}).limite, 27);
});

test("limites numericos sao respeitados", () => {
  assert.equal(produtosInput.safeParse({ canal: "ml", limite: 0 }).success, false);
  assert.equal(produtosInput.safeParse({ canal: "ml", limite: 51 }).success, false);
  assert.equal(produtosInput.safeParse({ canal: "ml", limite: 1.5 }).success, false);
  assert.equal(produtosInput.safeParse({ canal: "ml", limite: 50 }).success, true);
});

test("UF fora da lista oficial e' rejeitada", () => {
  assert.equal(regioesInput.safeParse({ ufs: ["SP", "RJ"] }).success, true);
  assert.equal(regioesInput.safeParse({ ufs: ["ZZ"] }).success, false);
  assert.equal(regioesInput.safeParse({ ufs: ["sp"] }).success, false);
});

test("mes no formato invalido e' rejeitado", () => {
  assert.equal(produtosInput.safeParse({ canal: "tiktok", mes: "2026-13" }).success, false);
  assert.equal(produtosInput.safeParse({ canal: "tiktok", mes: "2026-07" }).success, true);
});

// ---------------------------------------------------------------------------
// Envelope
// ---------------------------------------------------------------------------

const baseArgs = {
  period: { start: "2026-07-01", end: "2026-07-31", inclusive: true as const },
  filtersApplied: { channels: "all" },
  metricDefinition: "GMV bruto",
  refreshedAt: null,
  coverage: "universo completo",
  returnedCount: 3,
  data: { rows: [] },
};

test("total_count ausente permanece null e nao marca truncamento", () => {
  const e = buildEnvelope(baseArgs);
  assert.equal(e.meta.total_count, null);
  assert.equal(e.meta.truncated, false);
});

test("truncated so e' true quando o total verdadeiro supera o devolvido", () => {
  assert.equal(buildEnvelope({ ...baseArgs, totalCount: 3 }).meta.truncated, false);
  assert.equal(buildEnvelope({ ...baseArgs, totalCount: 100 }).meta.truncated, true);
});

test("refreshed_at nulo nao e' fabricado", () => {
  assert.equal(buildEnvelope(baseArgs).meta.refreshed_at, null);
});

test("moeda e unidade monetaria sao sempre declaradas", () => {
  const e = buildEnvelope(baseArgs);
  assert.equal(e.meta.currency, "BRL");
  assert.equal(e.meta.monetary_unit, "reais");
});

test("dinheiro e' numerico no structured e formatado so no texto", () => {
  const e = buildEnvelope({ ...baseArgs, data: { gmv: 1234.5 } });
  // Structured: numero cru, sem simbolo.
  assert.equal((e.data as { gmv: number }).gmv, 1234.5);
  // Texto: formatado.
  assert.match(fmtMoney(1234.5), /R\$/);
  // Ausencia NAO vira "R$ 0,00".
  assert.equal(fmtMoney(null), "sem dado");
  assert.equal(fmtMoney(0), fmtMoney(0));
  assert.match(fmtMoney(0), /R\$/);
});

test("fallback textual carrega o JSON completo e os avisos", () => {
  const e = buildEnvelope({ ...baseArgs, warnings: ["aviso importante"] });
  const t = textFallback("Resumo", e);
  assert.ok(t.includes("Resumo"));
  assert.ok(t.includes("aviso importante"));
  assert.ok(t.includes("2026-07-01"));
  // O JSON serializado precisa estar embutido para interoperabilidade.
  const jsonStart = t.indexOf("{");
  assert.deepEqual(JSON.parse(t.slice(jsonStart)), e);
});

// ---------------------------------------------------------------------------
// Rate limit
// ---------------------------------------------------------------------------

test("rate limit permite ate o teto e nega o excedente", () => {
  const rl = new SlidingWindowRateLimiter({ limit: 3, windowMs: 1000 });
  const t0 = 1_000_000;
  assert.equal(rl.check("k", t0).allowed, true);
  assert.equal(rl.check("k", t0 + 1).allowed, true);
  assert.equal(rl.check("k", t0 + 2).allowed, true);

  const denied = rl.check("k", t0 + 3);
  assert.equal(denied.allowed, false);
  assert.ok(denied.allowed === false && denied.retryAfterSeconds >= 0);
});

test("a janela desliza: apos expirar, libera de novo", () => {
  const rl = new SlidingWindowRateLimiter({ limit: 1, windowMs: 1000 });
  const t0 = 1_000_000;
  assert.equal(rl.check("k", t0).allowed, true);
  assert.equal(rl.check("k", t0 + 500).allowed, false);
  assert.equal(rl.check("k", t0 + 1500).allowed, true, "fora da janela deve liberar");
});

test("chaves distintas nao compartilham contador", () => {
  const rl = new SlidingWindowRateLimiter({ limit: 1, windowMs: 1000 });
  assert.equal(rl.check("a", 1).allowed, true);
  assert.equal(rl.check("b", 1).allowed, true);
  assert.equal(rl.check("a", 2).allowed, false);
});
