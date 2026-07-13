// Testes estaticos (grep no source, sem dependencias novas) que garantem que
// a aba Canais nao volte a expor desconto/afiliados nem misture N/A com
// "Sem dado" — contrato fechado no Gate 1/Gate 2
// (docs/sections/canais_audit.md, secao 14). Mesmo padrao de
// financeiro-labels.test.mjs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SOURCE_PATH = path.join(__dirname, "..", "app", "canais", "page.tsx");
const source = readFileSync(SOURCE_PATH, "utf-8");

test("Canais nao expoe desconto nem afiliados como metrica/coluna (bloqueado no Gate 1)", () => {
  // Nao proibe a palavra em si (o rodape explica textualmente o que ficou de
  // fora, o que e desejavel) — proibe especificamente rotulos de metrica/
  // coluna que reintroduziriam desconto ou afiliados como dado exibido.
  assert.doesNotMatch(source, /desconto\s*m[ée]dio/i);
  assert.doesNotMatch(source, /desconto\s*\/\s*gmv/i);
  assert.doesNotMatch(source, /afiliados?\s*\/\s*gmv/i);
});

test("Comparativo entre canais existe e nao inclui colunas de desconto/afiliados", () => {
  assert.match(source, /Comparativo entre Canais/);
  assert.match(source, /Ads\/GMV/);
  assert.match(source, /Custo marketplace\/GMV/);
  assert.match(source, /Frete seller\/GMV/);
});

test("legenda distingue N\\/A de Sem dado explicitamente", () => {
  assert.match(source, /N\/A = n[ãa]o se aplica/);
  assert.match(source, /Sem dado = deveria existir/);
});

test("legenda explica o terceiro estado — (denominador zero/nao calculavel)", () => {
  assert.match(source, /— = denominador zero ou m[ée]trica n[ãa]o calc/);
});

test("usa o modulo de metricas por canal em vez de logica ad-hoc duplicada", () => {
  assert.match(source, /from "@\/lib\/canais-channel-metrics"/);
});
