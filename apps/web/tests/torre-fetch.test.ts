// AUTH-GOOGLE-5 — torreFetch sinaliza sessao expirada; guarda de fiacao.
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { EVENTO_SESSAO_EXPIRADA, torreFetch } from "../src/lib/torre-fetch.ts";

const g = globalThis as Record<string, unknown>;
const origFetch = g.fetch;
const origWindow = g.window;
afterEach(() => {
  g.fetch = origFetch;
  if (origWindow === undefined) delete g.window;
  else g.window = origWindow;
});

function instala(status: number, headers: Record<string, string>) {
  const chamadas: { input: unknown; init: RequestInit | undefined }[] = [];
  const eventos: Event[] = [];
  g.fetch = async (input: unknown, init?: RequestInit) => {
    chamadas.push({ input, init });
    return new Response("x", { status, headers });
  };
  g.window = { dispatchEvent: (e: Event) => (eventos.push(e), true) };
  return { chamadas, eventos };
}

test("nome do evento", () => {
  assert.equal(EVENTO_SESSAO_EXPIRADA, "torre:sessao-expirada");
});

test("401 com X-Torre-Auth dispara o evento uma vez e devolve a resposta", async () => {
  const { chamadas, eventos } = instala(401, { "X-Torre-Auth": "sessao-ausente" });
  const res = await torreFetch("/torre-api/x");
  assert.equal(res.status, 401);
  assert.equal(eventos.length, 1);
  assert.equal(eventos[0].type, EVENTO_SESSAO_EXPIRADA);
  assert.equal(chamadas[0].init?.credentials, "same-origin");
});

test("401 sem o header (B2B/Auth0) nao dispara", async () => {
  const { eventos } = instala(401, {});
  await torreFetch("/x");
  assert.equal(eventos.length, 0);
});

test("200 nao dispara", async () => {
  const { eventos } = instala(200, { "X-Torre-Auth": "sessao-ausente" });
  await torreFetch("/x");
  assert.equal(eventos.length, 0);
});

test("sem window nao lanca", async () => {
  instala(401, { "X-Torre-Auth": "sessao-ausente" });
  delete g.window;
  const res = await torreFetch("/x");
  assert.equal(res.status, 401);
});

test("init do chamador prevalece em signal e headers", async () => {
  const { chamadas } = instala(200, {});
  const ac = new AbortController();
  await torreFetch("/x", { signal: ac.signal, headers: { A: "1" } });
  assert.equal(chamadas[0].init?.signal, ac.signal);
  assert.deepEqual(chamadas[0].init?.headers, { A: "1" });
});

test("fiacao: nenhum ponto da API usa fetch cru", () => {
  const arquivos = [
    "src/lib/api-client.ts",
    "src/lib/b2b-revenda.ts",
    "src/lib/dre-api.ts",
    "src/lib/metas-api.ts",
    "src/lib/expedicao-demo.ts",
    "app/brand/[brand]/page.tsx",
  ];
  for (const a of arquivos) {
    const src = readFileSync(new URL(`../${a}`, import.meta.url), "utf8");
    assert.doesNotMatch(src, /(?<![.\w])fetch\(/, `${a} ainda chama fetch(`);
    assert.match(src, /torreFetch/, `${a} nao usa torreFetch`);
  }
});
