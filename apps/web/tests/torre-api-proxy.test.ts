// AUTH-GOOGLE-5 — rewrite same-origin do navegador para a API.
import { test } from "node:test";
import assert from "node:assert/strict";
import { TORRE_API_PREFIX, assertTorreApiConfig, torreApiRewrites } from "../src/lib/torre-api-proxy.ts";

test("prefixo fixo", () => {
  assert.equal(TORRE_API_PREFIX, "/torre-api");
});

test("vazio ou undefined nao gera rewrite", () => {
  assert.deepEqual(torreApiRewrites(undefined), []);
  assert.deepEqual(torreApiRewrites(""), []);
});

test("https e localhost geram o rewrite, sem barra final", () => {
  const regra = (d: string) => [{ source: "/torre-api/:path*", destination: d }];
  assert.deepEqual(torreApiRewrites("https://mktplace-api.onrender.com"), regra("https://mktplace-api.onrender.com/:path*"));
  assert.deepEqual(torreApiRewrites("https://mktplace-api.onrender.com/"), regra("https://mktplace-api.onrender.com/:path*"));
  assert.deepEqual(torreApiRewrites("http://localhost:8080"), regra("http://localhost:8080/:path*"));
  assert.deepEqual(torreApiRewrites("http://127.0.0.1:8080"), regra("http://127.0.0.1:8080/:path*"));
});

test("origens invalidas lancam erro", () => {
  for (const o of ["http://mktplace-api.onrender.com", "https://x.com/path", "ftp://x.com", "lixo", "http://localhost"]) {
    assert.throws(() => torreApiRewrites(o), /TORRE_API_ORIGIN invalida/, o);
  }
});

test("assertTorreApiConfig: base relativa sem origem lanca", () => {
  assert.throws(() => assertTorreApiConfig("/torre-api", undefined), /TORRE_API_ORIGIN obrigatoria quando NEXT_PUBLIC_API_URL e relativa/);
  assert.throws(() => assertTorreApiConfig("/torre-api", ""), /obrigatoria/);
});

test("assertTorreApiConfig: demais combinacoes passam", () => {
  assert.doesNotThrow(() => assertTorreApiConfig("/torre-api", "https://x.onrender.com"));
  assert.doesNotThrow(() => assertTorreApiConfig("https://api.exemplo.com", undefined));
  assert.doesNotThrow(() => assertTorreApiConfig(undefined, undefined));
});
