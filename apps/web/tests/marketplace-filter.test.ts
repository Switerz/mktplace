// Testes da lib de selecao multicanal (apps/web/src/lib/marketplace-filter.ts).
// Roda via `node --test` com type-stripping nativo do Node (sem dependencias novas).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ALL_MARKETPLACES,
  DEFAULT_MARKETPLACE_SELECTION,
  canonicalizeSelection,
  isMarketplaceSelected,
  isAllSelected,
  toggleMarketplace,
  serializeMarketplaceSelection,
  parseMarketplaceParam,
} from "../src/lib/marketplace-filter.ts";

test("estado inicial (DEFAULT_MARKETPLACE_SELECTION) contem os tres canais na ordem canonica", () => {
  assert.deepEqual(DEFAULT_MARKETPLACE_SELECTION, ["tiktok", "ml", "shopee"]);
  assert.ok(isAllSelected(DEFAULT_MARKETPLACE_SELECTION));
});

test("canonicalizeSelection ordena e remove duplicados", () => {
  assert.deepEqual(canonicalizeSelection(["shopee", "tiktok", "tiktok"]), ["tiktok", "shopee"]);
  assert.deepEqual(canonicalizeSelection(["ml"]), ["ml"]);
  assert.deepEqual(canonicalizeSelection([...ALL_MARKETPLACES].reverse()), ["tiktok", "ml", "shopee"]);
});

test("toggleMarketplace: seleciona TikTok + ML a partir de um unico canal", () => {
  const onlyTk = ["tiktok"];
  const tkMl = toggleMarketplace(onlyTk, "ml");
  assert.deepEqual(tkMl, ["tiktok", "ml"]);
});

test("toggleMarketplace: seleciona ML + Shopee", () => {
  const onlyMl = ["ml"];
  const mlShopee = toggleMarketplace(onlyMl, "shopee");
  assert.deepEqual(mlShopee, ["ml", "shopee"]);
});

test("toggleMarketplace: nao permite desmarcar o ultimo canal selecionado", () => {
  const onlyTk = ["tiktok"];
  const result = toggleMarketplace(onlyTk, "tiktok");
  assert.deepEqual(result, ["tiktok"]);
});

test("toggleMarketplace: remove um canal quando ha mais de um selecionado", () => {
  const tkMl = ["tiktok", "ml"];
  assert.deepEqual(toggleMarketplace(tkMl, "ml"), ["tiktok"]);
});

test("isMarketplaceSelected reflete corretamente a selecao", () => {
  const sel = ["tiktok", "shopee"];
  assert.ok(isMarketplaceSelected(sel, "tiktok"));
  assert.ok(!isMarketplaceSelected(sel, "ml"));
  assert.ok(isMarketplaceSelected(sel, "shopee"));
});

test("isAllSelected: true apenas quando os tres canais estao presentes", () => {
  assert.ok(isAllSelected(["tiktok", "ml", "shopee"]));
  assert.ok(isAllSelected(["shopee", "tiktok", "ml"])); // ordem nao importa
  assert.ok(!isAllSelected(["tiktok", "ml"]));
});

test("serializeMarketplaceSelection: colapsa para 'all' quando os tres canais estao selecionados", () => {
  assert.equal(serializeMarketplaceSelection(["tiktok", "ml", "shopee"]), "all");
  assert.equal(serializeMarketplaceSelection(["shopee", "ml", "tiktok"]), "all");
});

test("serializeMarketplaceSelection: canal isolado serializa como o proprio nome (compat legado)", () => {
  assert.equal(serializeMarketplaceSelection(["tiktok"]), "tiktok");
  assert.equal(serializeMarketplaceSelection(["ml"]), "ml");
  assert.equal(serializeMarketplaceSelection(["shopee"]), "shopee");
});

test("serializeMarketplaceSelection: combinacao parcial serializa em ordem canonica separada por virgula", () => {
  assert.equal(serializeMarketplaceSelection(["ml", "tiktok"]), "tiktok,ml");
  assert.equal(serializeMarketplaceSelection(["shopee", "tiktok"]), "tiktok,shopee");
  assert.equal(serializeMarketplaceSelection(["shopee", "ml"]), "ml,shopee");
});

test("parseMarketplaceParam: compatibilidade com filtros antigos ('all'|'tiktok'|'ml'|'shopee')", () => {
  assert.deepEqual(parseMarketplaceParam("all"), ["tiktok", "ml", "shopee"]);
  assert.deepEqual(parseMarketplaceParam(""), ["tiktok", "ml", "shopee"]);
  assert.deepEqual(parseMarketplaceParam("tiktok"), ["tiktok"]);
  assert.deepEqual(parseMarketplaceParam("ml"), ["ml"]);
  assert.deepEqual(parseMarketplaceParam("shopee"), ["shopee"]);
});

test("parseMarketplaceParam: aceita combinacao separada por virgula e normaliza ordem", () => {
  assert.deepEqual(parseMarketplaceParam("ml,tiktok"), ["tiktok", "ml"]);
  assert.deepEqual(parseMarketplaceParam("shopee,tiktok,ml"), ["tiktok", "ml", "shopee"]);
});

test("parseMarketplaceParam: valor invalido cai para o padrao (todos os canais)", () => {
  assert.deepEqual(parseMarketplaceParam("invalido"), ["tiktok", "ml", "shopee"]);
});

test("round-trip: serializar uma selecao e re-parseá-la preserva a selecao (para combinacoes de 2 e 3 canais)", () => {
  for (const sel of [["tiktok", "ml"], ["ml", "shopee"], ["tiktok", "shopee"], ["tiktok", "ml", "shopee"]]) {
    const serialized = serializeMarketplaceSelection(sel);
    assert.deepEqual(parseMarketplaceParam(serialized), canonicalizeSelection(sel));
  }
});
