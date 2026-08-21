// Testes da regra de frescor de requisicao compartilhada entre Regioes e
// Financeiro (Gate U4) — mesmo padrao "Finding 2" do Gate U3 (Canais).
import { test } from "node:test";
import assert from "node:assert/strict";
import { isRequestFresh, computeRequestStatus } from "../src/lib/request-freshness.ts";

test("fresco: nao carregando, sem erro, chaves batem", () => {
  assert.equal(isRequestFresh({ loading: false, error: false, resolvedKey: "a", requestKey: "a" }), true);
});

test("carregando nunca e fresco, mesmo com chaves batendo (dado antigo nao e considerado fresco)", () => {
  assert.equal(isRequestFresh({ loading: true, error: false, resolvedKey: "a", requestKey: "a" }), false);
});

test("erro definitivo nunca e fresco, mesmo sem estar carregando (erro != loading)", () => {
  assert.equal(isRequestFresh({ loading: false, error: true, resolvedKey: "a", requestKey: "a" }), false);
});

test("chave resolvida desatualizada (filtro mudou, fetch ainda nao resolveu) nunca e fresco", () => {
  assert.equal(isRequestFresh({ loading: false, error: false, resolvedKey: "old", requestKey: "new" }), false);
});

test("resolvedKey nulo (nenhuma requisicao concluida ainda) nunca e fresco", () => {
  assert.equal(isRequestFresh({ loading: false, error: false, resolvedKey: null, requestKey: "a" }), false);
});

// ── computeRequestStatus — FINDING 2 (rodada de correcao consolidada) ────
// `!dataIsFresh` sozinho nao distinguia "carregando" de "erro definitivo".
// computeRequestStatus separa os 3 estados, sempre mutuamente exclusivos.

test("fresh: chaves batem, sem loading, sem erro", () => {
  const s = computeRequestStatus({ loading: false, error: false, resolvedKey: "a", requestKey: "a" });
  assert.deepEqual(s, { loading: false, error: false, fresh: true });
});

test("loading: requisicao em andamento, mesmo com chaves batendo e sem erro", () => {
  const s = computeRequestStatus({ loading: true, error: false, resolvedKey: "a", requestKey: "a" });
  assert.deepEqual(s, { loading: true, error: false, fresh: false });
});

test("loading: chave atual diferente da resolvida (frame de render anterior ao efeito) — mesmo com loading=false e error=false", () => {
  const s = computeRequestStatus({ loading: false, error: false, resolvedKey: "old", requestKey: "new" });
  assert.deepEqual(s, { loading: true, error: false, fresh: false });
});

test("error: erro definitivo SO conta quando a chave atual ja foi resolvida (nao esta em loading)", () => {
  const s = computeRequestStatus({ loading: false, error: true, resolvedKey: "a", requestKey: "a" });
  assert.deepEqual(s, { loading: false, error: true, fresh: false });
});

test("erro com chave desatualizada nunca vira 'error' — loading tem precedencia (retry: retryKey muda a chave ANTES do erro ser limpo)", () => {
  const s = computeRequestStatus({ loading: false, error: true, resolvedKey: "old", requestKey: "new" });
  assert.deepEqual(s, { loading: true, error: false, fresh: false });
});

test("loading e error sao sempre mutuamente exclusivos com fresh, para qualquer combinacao de entrada", () => {
  const combos = [
    { loading: false, error: false, resolvedKey: "a", requestKey: "a" },
    { loading: true, error: false, resolvedKey: "a", requestKey: "a" },
    { loading: false, error: true, resolvedKey: "a", requestKey: "a" },
    { loading: false, error: false, resolvedKey: "a", requestKey: "b" },
    { loading: true, error: true, resolvedKey: "a", requestKey: "b" },
  ];
  for (const input of combos) {
    const s = computeRequestStatus(input);
    const trueCount = [s.loading, s.error, s.fresh].filter(Boolean).length;
    assert.equal(trueCount, 1, `exatamente um estado deve ser verdadeiro para ${JSON.stringify(input)}`);
  }
});

// ═════════════════════════════════════════════════════════════════════════
// PF1 — cache de respostas e retry
// ═════════════════════════════════════════════════════════════════════════
//
// Defeito corrigido: `withCache` chamava `cacheSet` incondicionalmente, entao
// uma FALHA ficava memoizada por `CACHE_TTL` (5 min) e "Tentar novamente" nao
// tocava a rede — reencontrava a mesma falha no cache. `apiFetch` captura todo
// erro e devolve `null`, entao HTTP nao-2xx, rede, timeout e JSON invalido
// chegavam aqui como valor de retorno normal, nunca como excecao.
//
// LIMITACAO DESTE ARQUIVO, declarada e contornada — nao escondida.
//
// `api-client.ts` nao pode ser importado em RUNTIME por `node --test`: ele
// importa `./mock-data` e outros tres modulos SEM extensao, e o resolver ESM do
// Node nao completa extensao (verificado: `--experimental-specifier-resolution`
// nao existe mais no Node 24). E' a mesma restricao que `regioes.test.ts` L3-L5
// ja documenta, e a razao pela qual TODOS os testes deste projeto importam
// `api-client.ts` apenas como `import type`. Chamar `fetchProdutosML` com
// `global.fetch` controlado aqui e' estruturalmente impossivel nesta base.
//
// O contorno tem duas metades, e nenhuma delas e' "presenca textual da guarda":
//
//   1. GUARDA DE DERIVA — o teste le o codigo-fonte real das duas funcoes e
//      exige que ele seja EXATAMENTE o esperado. Se alguem alterar o predicado
//      ou `withCache`, estes testes falham alto, e nao silenciosamente.
//   2. HARNESS DE TRANSCRICAO — as duas funcoes sao transcritas aqui e os casos
//      comportamentais rodam contra elas, com relogio e contador de chamadas.
//      A guarda 1 e' o que torna a transcricao equivalente a producao.
//
// A prova ponta a ponta pelas funcoes publicas reais roda no QA em navegador,
// contra o modulo de verdade e o bundle de producao — nao aqui.

import { readFileSync } from "node:fs";

const CLIENT_SRC = readFileSync(new URL("../src/lib/api-client.ts", import.meta.url), "utf8").replace(/\r\n/g, "\n");

// ── 1. guarda de deriva: o codigo real e exatamente o esperado ─────────────

const PREDICADO_ESPERADO = `export function isCacheableApiResult(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "object" && "live" in value && (value as { live?: unknown }).live === false) {
    return false;
  }
  return true;
}`;

const WITHCACHE_ESPERADO = `async function withCache<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const hit = cacheGet<T>(key);
  // \`!== undefined\` e nao truthiness: e' o que permite cachear 0/false/""/[].
  if (hit !== undefined) return hit;
  const result = await fn();
  // Somente sucesso e' memoizado. O resultado e' devolvido ao chamador nos dois
  // casos, entao fallback e estado visual seguem exatamente como antes: o que
  // muda e' o que fica no cache, nunca o que a tela recebe.
  if (isCacheableApiResult(result)) cacheSet(key, result);
  return result;
}`;

function trecho(inicio: string): string {
  const i = CLIENT_SRC.indexOf(inicio);
  assert.ok(i >= 0, `nao encontrei no fonte: ${inicio.slice(0, 60)}`);
  const fim = CLIENT_SRC.indexOf("\n}", i);
  assert.ok(fim > i, "fim da funcao nao localizado");
  return CLIENT_SRC.slice(i, fim + 2);
}

test("PF1 deriva: o predicado real e exatamente o transcrito neste arquivo", () => {
  assert.equal(
    trecho("export function isCacheableApiResult"),
    PREDICADO_ESPERADO,
    "o predicado mudou no fonte: atualize a transcricao e revalide os casos abaixo",
  );
});

test("PF1 deriva: withCache real e exatamente o transcrito neste arquivo", () => {
  assert.equal(
    trecho("async function withCache<T>"),
    WITHCACHE_ESPERADO,
    "withCache mudou no fonte: atualize a transcricao e revalide os casos abaixo",
  );
});

// ── 2. harness de transcricao ─────────────────────────────────────────────
// Transcricao literal do que a guarda acima acabou de provar identico.

function isCacheableApiResult(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "object" && "live" in value && (value as { live?: unknown }).live === false) {
    return false;
  }
  return true;
}

const CACHE_TTL_TRANSCRITO = 5 * 60 * 1000;

/** Mesma maquinaria de cacheGet/cacheSet/withCache, com relogio injetavel. */
function novoCache() {
  const mapa = new Map<string, { data: unknown; at: number }>();
  let agora = 1_000_000;
  const cacheGet = <T,>(key: string): T | undefined => {
    const e = mapa.get(key);
    if (!e) return undefined;
    if (agora - e.at > CACHE_TTL_TRANSCRITO) { mapa.delete(key); return undefined; }
    return e.data as T;
  };
  const cacheSet = <T,>(key: string, data: T): T => { mapa.set(key, { data, at: agora }); return data; };
  const withCache = async <T,>(key: string, fn: () => Promise<T>): Promise<T> => {
    const hit = cacheGet<T>(key);
    if (hit !== undefined) return hit;
    const result = await fn();
    if (isCacheableApiResult(result)) cacheSet(key, result);
    return result;
  };
  return { withCache, mapa, avancar: (ms: number) => { agora += ms; } };
}

/** `apiFetch` transcrito: captura todo erro e devolve null. */
function novoApiFetch(respostas: Array<{ ok?: boolean; payload?: unknown; lancar?: boolean; jsonInvalido?: boolean }>) {
  const reg = { chamadas: 0 };
  const apiFetch = async <T,>(): Promise<T | null> => {
    const r = respostas[Math.min(reg.chamadas, respostas.length - 1)];
    reg.chamadas += 1;
    try {
      if (r.lancar) throw new Error("ECONNREFUSED (simulado)");
      if (r.ok === false) return null;
      if (r.jsonInvalido) throw new SyntaxError("corpo invalido");
      return r.payload as T;
    } catch {
      return null;
    }
  };
  return { apiFetch, reg };
}

// Os tres padroes reais de call site, transcritos.
const PAYLOAD = { total: 3, limit: 25, offset: 0, items: [{ brand: "x" }] };
const MOCK = { gmv: 999, __mock: true };

/** 12 funcoes: `withCache(key, () => apiFetch(...))` — falha = null. */
const chamarCru = (c: ReturnType<typeof novoCache>, f: ReturnType<typeof novoApiFetch>, key: string) =>
  c.withCache(key, () => f.apiFetch<typeof PAYLOAD>());

/** 7 funcoes: `if (raw) {live:true} else {live:false, mock}`. */
const chamarFallback = (c: ReturnType<typeof novoCache>, f: ReturnType<typeof novoApiFetch>, key: string) =>
  c.withCache(key, async () => {
    const raw = await f.apiFetch<typeof PAYLOAD>();
    if (raw) return { live: true, data: raw };
    return { live: false, data: MOCK };
  });

/** 2 funcoes (Inteligencia, Operacoes): `{ data: raw, live: raw != null }`. */
const chamarEnvelope = (c: ReturnType<typeof novoCache>, f: ReturnType<typeof novoApiFetch>, key: string) =>
  c.withCache(key, async () => {
    const raw = await f.apiFetch<typeof PAYLOAD>();
    return { data: raw, live: raw != null };
  });

// ── predicado: casos puros ────────────────────────────────────────────────

test("PF1 predicado: null nao e cacheavel (as 12 funcoes que devolvem apiFetch direto)", () => {
  assert.equal(isCacheableApiResult(null), false);
});

test("PF1 predicado: undefined nao e cacheavel", () => {
  assert.equal(isCacheableApiResult(undefined), false);
});

test("PF1 predicado: { live: false } nao e cacheavel (fallback de demonstracao)", () => {
  assert.equal(isCacheableApiResult({ live: false }), false);
});

test("PF1 predicado: { live: false, data: [] } nao e cacheavel (fetchTrend na falha: vazio, nao mock)", () => {
  assert.equal(isCacheableApiResult({ live: false, data: [] }), false);
});

test("PF1 predicado: { data: null, live: false } nao e cacheavel (envelope Inteligencia/Operacoes)", () => {
  assert.equal(isCacheableApiResult({ data: null, live: false }), false);
});

test("PF1 predicado: { live: true } e cacheavel", () => {
  assert.equal(isCacheableApiResult({ live: true }), true);
});

test("PF1 predicado: zero e cacheavel — truthiness quebraria isto", () => {
  assert.equal(isCacheableApiResult(0), true);
});

test("PF1 predicado: false e cacheavel", () => {
  assert.equal(isCacheableApiResult(false), true);
});

test("PF1 predicado: string vazia e cacheavel", () => {
  assert.equal(isCacheableApiResult(""), true);
});

test("PF1 predicado: array vazio e cacheavel", () => {
  assert.equal(isCacheableApiResult([]), true);
  assert.equal(isCacheableApiResult([0]), true);
});

test("PF1 predicado: objeto SEM propriedade live e cacheavel (payload cru da API)", () => {
  assert.equal(isCacheableApiResult({ total: 0, items: [] }), true);
  assert.equal(isCacheableApiResult({}), true);
});

test("PF1 predicado: live ausente/undefined nao reprova, e so o literal false reprova", () => {
  assert.equal(isCacheableApiResult({ live: undefined }), true, "undefined != false");
  assert.equal(isCacheableApiResult({ live: 0 }), true, "0 e falsy mas nao e o boolean false");
  assert.equal(isCacheableApiResult({ live: "false" }), true, "a string 'false' nao e o boolean false");
  assert.equal(isCacheableApiResult({ data: { x: 1 } }), true);
});

// ── comportamento: null cru ───────────────────────────────────────────────

test("PF1 null cru: a falha nao entra no cache e a chamada seguinte consulta a fonte", async () => {
  const c = novoCache();
  const f = novoApiFetch([{ ok: false }, { payload: PAYLOAD }]);

  const primeira = await chamarCru(c, f, "produtos-ml:limit=25&offset=0");
  assert.equal(primeira, null, "1a chamada falha e devolve null AO CHAMADOR");
  assert.equal(f.reg.chamadas, 1);
  assert.equal(c.mapa.size, 0, "nada foi memoizado");

  const segunda = await chamarCru(c, f, "produtos-ml:limit=25&offset=0");
  assert.equal(f.reg.chamadas, 2, "a 2a chamada TEM de consultar a fonte");
  assert.deepEqual(segunda, PAYLOAD, "e devolve o dado real, nao o null memoizado");
  assert.equal(c.mapa.size, 1, "agora sim: o sucesso foi memoizado");
});

test("PF1 null cru: erro de rede e JSON invalido tambem nao entram no cache", async () => {
  for (const falha of [{ lancar: true }, { jsonInvalido: true }] as const) {
    const c = novoCache();
    const f = novoApiFetch([falha, { payload: PAYLOAD }]);
    assert.equal(await chamarCru(c, f, "k"), null);
    assert.equal(c.mapa.size, 0, `nao memoizou: ${JSON.stringify(falha)}`);
    assert.deepEqual(await chamarCru(c, f, "k"), PAYLOAD);
    assert.equal(f.reg.chamadas, 2, "timeout/rede/JSON colapsam em null e nenhum e cacheavel");
  }
});

// ── comportamento: fallback com live:false ────────────────────────────────

test("PF1 fallback: live:false nao entra no cache, e o retry devolve live:true", async () => {
  const c = novoCache();
  const f = novoApiFetch([{ ok: false }, { payload: PAYLOAD }]);

  const primeira = await chamarFallback(c, f, "overview:channels=ml");
  assert.equal(primeira.live, false, "1a chamada devolve o fallback");
  assert.equal(primeira.data, MOCK, "o fallback CONTINUA sendo entregue: nada visual mudou");
  assert.equal(c.mapa.size, 0, "o mock nao foi memoizado");

  const segunda = await chamarFallback(c, f, "overview:channels=ml");
  assert.equal(f.reg.chamadas, 2, "o retry consulta a fonte");
  assert.equal(segunda.live, true);
  assert.deepEqual(segunda.data, PAYLOAD, "o valor real substituiu o fallback");
  assert.notEqual(segunda, primeira, "o fallback anterior nao foi reutilizado");
});

test("PF1 trend hibrido: {live:false, data:[]} nao e cacheado (indisponibilidade, nao serie vazia)", async () => {
  const c = novoCache();
  const f = novoApiFetch([{ ok: false }, { payload: PAYLOAD }]);
  const vazioPorFalha = async () => c.withCache("trend:day:qs", async () => {
    const raw = await f.apiFetch<typeof PAYLOAD>();
    if (raw) return { live: true, data: [raw] };
    return { live: false, data: [] as unknown[] };
  });

  const primeira = await vazioPorFalha();
  assert.equal(primeira.live, false);
  assert.deepEqual(primeira.data, [], "na falha o contrato entrega serie vazia, nao mock");
  assert.equal(c.mapa.size, 0, "vazio-por-falha nao pode ser confundido com serie vazia legitima");

  const segunda = await vazioPorFalha();
  assert.equal(f.reg.chamadas, 2);
  assert.equal(segunda.live, true);
  assert.equal(segunda.data.length, 1);
});

test("PF1 envelope: { data: null, live: false } de Inteligencia/Operacoes nao e cacheado", async () => {
  const c = novoCache();
  const f = novoApiFetch([{ ok: false }, { payload: PAYLOAD }]);

  const primeira = await chamarEnvelope(c, f, "inteligencia");
  assert.equal(primeira.live, false);
  assert.equal(primeira.data, null, "a tela recebe data null, como antes");
  assert.equal(c.mapa.size, 0);

  const segunda = await chamarEnvelope(c, f, "inteligencia");
  assert.equal(f.reg.chamadas, 2, "'Tentar novamente' de Inteligencia/Operacoes volta a funcionar");
  assert.equal(segunda.live, true);
  assert.deepEqual(segunda.data, PAYLOAD);
});

// ── comportamento: sucesso continua cacheado ──────────────────────────────

test("PF1 sucesso: duas chamadas identicas fazem UMA consulta a fonte", async () => {
  const c = novoCache();
  const f = novoApiFetch([{ payload: PAYLOAD }]);
  const a = await chamarCru(c, f, "produtos-ml:qs");
  const b = await chamarCru(c, f, "produtos-ml:qs");
  assert.equal(f.reg.chamadas, 1, "o cache de sucesso continua valendo: zero regressao de performance");
  assert.equal(a, b, "mesma referencia: veio do cache");
});

test("PF1 sucesso com fallback: live:true continua cacheado", async () => {
  const c = novoCache();
  const f = novoApiFetch([{ payload: PAYLOAD }]);
  const a = await chamarFallback(c, f, "overview:qs");
  const b = await chamarFallback(c, f, "overview:qs");
  assert.equal(f.reg.chamadas, 1);
  assert.equal(a.live, true);
  assert.equal(a, b);
});

test("PF1 envelope: live:true continua cacheado", async () => {
  const c = novoCache();
  const f = novoApiFetch([{ payload: PAYLOAD }]);
  const a = await chamarEnvelope(c, f, "operacoes");
  const b = await chamarEnvelope(c, f, "operacoes");
  assert.equal(f.reg.chamadas, 1);
  assert.equal(a, b);
});

// ── comportamento: zero e vazio REAIS, obtidos com sucesso ────────────────

test("PF1 zero e vazio: 200 com total:0 e items:[] continua cacheado, nao e tratado como falha", async () => {
  const c = novoCache();
  const vazioLegitimo = { total: 0, limit: 25, offset: 0, items: [] as unknown[] };
  const f = novoApiFetch([{ payload: vazioLegitimo }]);
  const a = await c.withCache("produtos-ml:vazio", () => f.apiFetch<typeof vazioLegitimo>());
  assert.equal(a?.total, 0, "zero legitimo preservado");
  assert.deepEqual(a?.items, [], "array vazio legitimo preservado");
  assert.equal(c.mapa.size, 1, "200 com zero/vazio e SUCESSO e continua cacheado");
  await c.withCache("produtos-ml:vazio", () => f.apiFetch<typeof vazioLegitimo>());
  assert.equal(f.reg.chamadas, 1, "a 2a chamada veio do cache");
});

test("PF1 zero, false, string vazia e array vazio sobrevivem como valores cacheados", async () => {
  for (const valor of [0, false, "", [], [0]] as unknown[]) {
    const c = novoCache();
    let chamadas = 0;
    const fonte = async () => { chamadas += 1; return valor; };
    const a = await c.withCache("k", fonte);
    const b = await c.withCache("k", fonte);
    assert.equal(chamadas, 1, `${JSON.stringify(valor)} deve ser HIT valido, nao cache miss`);
    assert.deepEqual(a, valor);
    assert.deepEqual(b, valor);
  }
});

test("PF1 null x zero: ausencia continua distinguivel de zero depois da correcao", async () => {
  const c = novoCache();
  const falha = novoApiFetch([{ ok: false }]);
  assert.equal(await chamarCru(c, falha, "ausencia"), null, "falha = null");
  const zero = novoApiFetch([{ payload: { total: 0, limit: 25, offset: 0, items: [] } }]);
  const r = await c.withCache("zero-real", () => zero.apiFetch<{ total: number }>());
  assert.notEqual(r, null, "zero real NAO e null");
  assert.equal(r?.total, 0);
});

// ── comportamento: TTL ────────────────────────────────────────────────────

test("PF1 TTL: sucesso serve do cache dentro de 5 min e reconsulta depois", async () => {
  const c = novoCache();
  const f = novoApiFetch([{ payload: PAYLOAD }]);
  await chamarCru(c, f, "ttl");
  assert.equal(f.reg.chamadas, 1);
  c.avancar(4 * 60 * 1000);
  await chamarCru(c, f, "ttl");
  assert.equal(f.reg.chamadas, 1, "dentro de 5 min o sucesso vem do cache");
  c.avancar(60 * 1000 + 1);
  await chamarCru(c, f, "ttl");
  assert.equal(f.reg.chamadas, 2, "apos o TTL a fonte e consultada de novo");
});

test("PF1 TTL: a falha nao depende mais do TTL para sair — o alivio e imediato", async () => {
  const c = novoCache();
  const f = novoApiFetch([{ ok: false }, { payload: PAYLOAD }]);
  await chamarCru(c, f, "ttl-falha");
  c.avancar(1000); // 1 segundo, nao 4min59s
  const segunda = await chamarCru(c, f, "ttl-falha");
  assert.equal(f.reg.chamadas, 2, "antes da correcao seriam 5 min de espera");
  assert.deepEqual(segunda, PAYLOAD);
});

test("PF1 TTL: o TTL declarado no fonte continua 5 min", () => {
  assert.match(CLIENT_SRC, /const CACHE_TTL = 5 \* 60 \* 1000;/);
  assert.equal(CACHE_TTL_TRANSCRITO, 5 * 60 * 1000, "a transcricao usa o mesmo TTL");
});

// ── chaves: nenhuma colisao entre identidades ─────────────────────────────

test("PF1 chaves: chaves distintas nao colidem no cache", async () => {
  const c = novoCache();
  const f = novoApiFetch([{ payload: PAYLOAD }]);
  const chaves = [
    "overview:channels=ml&date_from=2026-04-01&date_to=2026-04-30",
    "overview:channels=ml&date_from=2026-05-01&date_to=2026-05-31",
    "trend:day:channels=ml", "trend:week:channels=ml", "trend:month:channels=ml",
    "produtos-ml:limit=25&offset=0", "produtos-ml:limit=25&offset=25",
    "produtos-ml:limit=50&offset=0",
    "produtos-ml:limit=25&offset=0&sort_by=gmv&sort_dir=desc",
    "produtos-ml:limit=25&offset=0&sort_by=gmv&sort_dir=asc",
    "regioes-summary:channels=ml&uf=SP", "regioes-summary:channels=ml&uf=RJ",
  ];
  for (const k of chaves) await chamarCru(c, f, k);
  assert.equal(f.reg.chamadas, chaves.length, "cada identidade consultou a fonte uma vez");
  assert.equal(c.mapa.size, chaves.length, "cada identidade tem entrada propria");
  for (const k of chaves) await chamarCru(c, f, k);
  assert.equal(f.reg.chamadas, chaves.length, "repetir qualquer identidade volta do cache");
});

test("PF1 chaves: as 21 chaves reais seguem identicas e nenhuma perdeu filtro/paginacao/granularidade", () => {
  const chaves = [...CLIENT_SRC.matchAll(/withCache(?:<[^>]*>)?\(\s*(?:`([^`]*)`|"([^"]*)")/g)].map((m) => m[1] ?? m[2]);
  assert.equal(chaves.length, 21, "o numero de call sites nao mudou");
  assert.deepEqual(chaves, [
    "overview:${qs.toString()}", "brands:${qs.toString()}", "monthly:${marketplace}",
    "trend:${granularity}:${qs.toString()}", "produtos-ml:${qs}", "produtos-shopee:${qs}",
    "produtos-tk:${qs}", "produtos-ml-summary:${qs}", "produtos-tk-summary:${qs}",
    "produtos-sh-summary:${qs}", "canais:${qs.toString()}", "financeiro:${qs.toString()}",
    "quality:${qs.toString()}", "brand-detail:${brand}:${month}", "pedidos:${qs.toString()}",
    // Gate V3-1B: a chave ganhou o escopo de marca. Sem isso, `?brands=barbours`
    // e o escopo global colidiriam na mesma entrada do cache.
    "inteligencia:${escopo ? escopo.join(\",\") : \"all\"}", "operacoes", "regioes-summary:${qs.toString()}", "regioes-by-uf:${qs.toString()}",
    "regioes-by-brand:${qs.toString()}", "regioes-trend:${qs.toString()}",
  ]);
});

test("PF1 chaves: buildFilterQuery segue ordenando as marcas (chave deterministica)", () => {
  assert.match(CLIENT_SRC, /qs\.set\("brands", \[\.\.\.filters\.brands\]\.sort\(\)\.join\(","\)\)/);
});

// ── concorrencia: divida separada, registrada e nao corrigida aqui ────────

test("PF1 concorrencia: chamadas simultaneas iguais seguem SEM deduplicacao in-flight", async () => {
  // Registro factual, nao regressao. `withCache` memoiza o VALOR resolvido e nao
  // a promise, entao duas chamadas concorrentes com a mesma chave consultam a
  // fonte duas vezes. Isso e ANTERIOR ao PF1 e nao foi criado por ele; corrigir
  // exigiria armazenar promises, o que esta fora do escopo deste gate.
  const c = novoCache();
  const f = novoApiFetch([{ payload: PAYLOAD }]);
  const [a, b] = await Promise.all([chamarCru(c, f, "conc"), chamarCru(c, f, "conc")]);
  assert.equal(f.reg.chamadas, 2, "comportamento inalterado pelo PF1");
  assert.deepEqual(a, PAYLOAD);
  assert.deepEqual(b, PAYLOAD);
  await chamarCru(c, f, "conc");
  assert.equal(f.reg.chamadas, 2, "a chamada sequencial seguinte vem do cache");
});

// ── guardas de escopo: o PF1 nao pode ter introduzido nada disso ──────────

test("PF1 escopo: nenhum retry automatico, backoff, polling ou invalidacao global", () => {
  assert.ok(!/setTimeout|setInterval/.test(CLIENT_SRC), "nenhum temporizador foi introduzido");
  assert.ok(!/backoff|retryDelay|maxRetries/i.test(CLIENT_SRC), "nenhuma politica de retry automatico");
  assert.ok(!/_cache\.clear\(\)/.test(CLIENT_SRC), "nenhuma invalidacao global de cache");
  assert.ok(/if \(hit !== undefined\) return hit;/.test(CLIENT_SRC), "sentinela de miss intacta");
});

test("PF1 escopo: apiFetch nao foi alterado", () => {
  assert.ok(
    CLIENT_SRC.includes(`async function apiFetch<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(\`\${API_URL}\${path}\`);
    if (!res.ok) return null;`),
    "apiFetch deve continuar capturando todo erro e devolvendo null",
  );
  assert.ok(/\} catch \{\n    return null;\n  \}\n\}/.test(CLIENT_SRC), "o catch de apiFetch segue devolvendo null");
});

test("PF1 escopo: a maquinaria de cache continua privada — so o predicado e exportado", () => {
  for (const proibido of [
    "export const _cache", "export function cacheGet", "export function cacheSet",
    "export async function withCache", "export function withCache", "export { withCache",
  ]) {
    assert.ok(!CLIENT_SRC.includes(proibido), `nao pode exportar: ${proibido}`);
  }
  assert.ok(!/__test|resetCache|clearCache/.test(CLIENT_SRC), "nenhum helper existente apenas para teste");
  const exportsCache = [...CLIENT_SRC.matchAll(/^export (?:function|const|async function) (\w*[Cc]ache\w*)/gm)].map((m) => m[1]);
  assert.deepEqual(exportsCache, ["isCacheableApiResult"], "a unica exposicao nova e o predicado de contrato");
});

test("PF1 escopo: withCache chama cacheSet SOMENTE sob o predicado", () => {
  // todas as ocorrencias de `cacheSet` menos a propria declaracao
  const ocorrencias = [...CLIENT_SRC.matchAll(/\bcacheSet\s*[<(]/g)].length;
  const declaracao = [...CLIENT_SRC.matchAll(/function cacheSet\s*</g)].length;
  assert.equal(declaracao, 1, "cacheSet e declarada uma unica vez");
  assert.equal(ocorrencias - declaracao, 1, "cacheSet e CHAMADA em um unico lugar");
  assert.ok(
    /if \(isCacheableApiResult\(result\)\) cacheSet\(key, result\);/.test(CLIENT_SRC),
    "a unica chamada de cacheSet esta guardada pelo predicado",
  );
  assert.ok(
    !/return cacheSet\(key, result\);/.test(CLIENT_SRC),
    "o `return cacheSet(...)` incondicional — o defeito — nao pode voltar",
  );
});

test("PF1 escopo: as funcoes sem cache continuam sem cache e fetchMonthly segue intocada", () => {
  const L = CLIENT_SRC.split("\n");
  const corpoDe = (nome: string) => {
    const i = L.findIndex((l) => new RegExp(`^export (?:async )?function ${nome}\\b`).test(l));
    assert.ok(i >= 0, `${nome} nao encontrada`);
    let fim = L.length;
    for (let k = i + 1; k < L.length; k++) { if (/^export (?:async )?function /.test(L[k])) { fim = k; break; } }
    return L.slice(i, fim).join("\n");
  };
  assert.ok(!/withCache/.test(corpoDe("fetchExecutiveSummary")), "resumo executivo segue sem cache");
  assert.ok(!/withCache/.test(corpoDe("fetchTempoReal")), "Tempo Real segue sem cache: cachear sabotaria o polling");
  assert.ok(/withCache\(`monthly:\$\{marketplace\}`/.test(corpoDe("fetchMonthly")), "fetchMonthly intocada, mesmo sem consumidor");
});

test("PF1 escopo: as 23 assinaturas publicas de fetchX nao mudaram", () => {
  const assinaturas = [...CLIENT_SRC.matchAll(/^export (?:async )?function (fetch\w+)/gm)].map((m) => m[1]);
  assert.equal(assinaturas.length, 23, "nenhuma funcao publica foi adicionada ou removida");
  // nenhuma delas passou a receber parametro de cache/refresh
  for (const nome of assinaturas) {
    const i = CLIENT_SRC.indexOf(`export function ${nome}`) >= 0
      ? CLIENT_SRC.indexOf(`export function ${nome}`)
      : CLIENT_SRC.indexOf(`export async function ${nome}`);
    const cabecalho = CLIENT_SRC.slice(i, CLIENT_SRC.indexOf("{", CLIENT_SRC.indexOf(")", i)));
    assert.ok(
      !/forceRefresh|skipCache|noCache|shouldCache|invalidate/i.test(cabecalho),
      `${nome} nao pode ter ganhado parametro de cache`,
    );
  }
});
