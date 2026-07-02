// Testes da logica de estado assincrono por canal usada em produtos/page.tsx
// (guarda contra resposta obsoleta, reset em falha, sem loading infinito).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  initialChannelState,
  startFetch,
  resolveFetch,
  resolveFetchError,
} from "../src/lib/async-channel-state.ts";

test("estado inicial: sem dados, sem loading, nao ao vivo", () => {
  const s = initialChannelState<{ total: number }>();
  assert.deepEqual(s, { data: null, loading: false, live: false });
});

test("startFetch: liga loading e LIMPA os dados imediatamente (skeleton, nunca dados do filtro anterior)", () => {
  const s = startFetch({ data: { total: 3 }, loading: false, live: true });
  assert.deepEqual(s, { data: null, loading: true, live: true });
});

test("troca de marca/periodo/bucket/aba: startFetch nunca deixa a pagina anterior visivel durante o carregamento", () => {
  // Estado apos uma busca bem sucedida para o filtro ANTERIOR.
  const afterPreviousFilter = { data: { total: 42 }, loading: false, live: true };
  // Usuario troca marca/periodo/bucket/aba -> dispara nova busca.
  const s = startFetch(afterPreviousFilter);
  assert.equal(s.data, null, "dados do filtro anterior devem sumir imediatamente (skeleton), nao ficar visiveis com um selo de 'atualizando'");
  assert.equal(s.loading, true);
});

test("resolveFetch com sucesso: aplica dados, desliga loading, marca live", () => {
  const s = resolveFetch(startFetch(initialChannelState()), true, { total: 5 });
  assert.deepEqual(s, { data: { total: 5 }, loading: false, live: true });
});

test("resolveFetch com falha (result=null): nunca deixa dados antigos exibidos como se fossem do filtro atual", () => {
  const stale = { data: { total: 99 }, loading: true, live: true };
  const s = resolveFetch(stale, true, null);
  assert.deepEqual(s, { data: null, loading: false, live: false });
});

test("resolveFetchError: equivalente a falha, reseta dados e live", () => {
  const stale = { data: { total: 99 }, loading: true, live: true };
  const s = resolveFetchError(stale, true);
  assert.deepEqual(s, { data: null, loading: false, live: false });
});

test("resposta obsoleta (isCurrent=false) e ignorada: estado permanece intacto", () => {
  // Simula: usuario mudou de marca rapidamente; a requisicao ANTIGA (mais
  // lenta) chega depois da NOVA (mais rapida) ja ter atualizado o estado.
  const afterNewerRequestSettled = { data: { total: 7 }, loading: false, live: true };
  const s = resolveFetch(afterNewerRequestSettled, false, { total: 999 });
  assert.deepEqual(s, afterNewerRequestSettled); // a resposta obsoleta (999) nunca aparece
});

test("resposta obsoleta que falhou tambem e ignorada (nao apaga dados validos mais novos)", () => {
  const afterNewerRequestSettled = { data: { total: 7 }, loading: false, live: true };
  const s = resolveFetchError(afterNewerRequestSettled, false);
  assert.deepEqual(s, afterNewerRequestSettled);
});

test("sequencia completa nunca deixa loading travado: start -> resolve sempre desliga loading quando atual", () => {
  let s = initialChannelState<{ total: number }>();
  s = startFetch(s);
  assert.equal(s.loading, true);
  s = resolveFetch(s, true, { total: 1 });
  assert.equal(s.loading, false);
});

test("troca rapida de filtro: 2 requisicoes disparadas, so a mais recente (id maior) deve valer", () => {
  // Simula o guard usado em page.tsx: id crescente por canal, isCurrent = (id === ref atual)
  let currentId = 0;
  let s = initialChannelState<{ total: number }>();

  const idA = ++currentId; // requisicao A disparada primeiro
  s = startFetch(s);
  const idB = ++currentId; // requisicao B disparada em seguida (currentId agora = 2)
  s = startFetch(s);

  // B (mais rapida) resolve primeiro
  s = resolveFetch(s, idB === currentId, { total: 2 });
  assert.deepEqual(s, { data: { total: 2 }, loading: false, live: true });

  // A (mais lenta) resolve depois, mas esta obsoleta (idA !== currentId)
  s = resolveFetch(s, idA === currentId, { total: 1 });
  assert.deepEqual(s, { data: { total: 2 }, loading: false, live: true }); // resultado de B preservado
});
