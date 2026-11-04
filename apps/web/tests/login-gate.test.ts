import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { mensagemDeErroLogin, MENSAGEM_SESSAO_EXPIRADA } from "../src/lib/login-mensagens.ts";

const lerFonte = (rel: string) => readFileSync(join(import.meta.dirname, "..", rel), "utf8");

test("mensagemDeErroLogin: dominio nao permitido usa a message do corpo", () => {
  assert.equal(
    mensagemDeErroLogin(403, { error: "dominio_nao_permitido", message: "Use uma conta GoGroup." }),
    "Use uma conta GoGroup.",
  );
});

test("mensagemDeErroLogin: e-mail nao verificado", () => {
  assert.equal(
    mensagemDeErroLogin(403, { error: "email_nao_verificado" }),
    "Seu e-mail Google não está verificado.",
  );
});

test("mensagemDeErroLogin: 401 credencial invalida", () => {
  assert.equal(
    mensagemDeErroLogin(401, { error: "credencial_invalida" }),
    "Não foi possível validar sua conta Google. Tente de novo.",
  );
});

test("mensagemDeErroLogin: demais casos e falha de rede", () => {
  assert.equal(mensagemDeErroLogin(500, { error: "sessao_indisponivel" }), "Falha no login. Tente de novo.");
  assert.equal(mensagemDeErroLogin(0, null), "Falha no login. Tente de novo.");
  assert.equal(mensagemDeErroLogin(403, { error: "dominio_nao_permitido" }), "Falha no login. Tente de novo.");
});

test("MENSAGEM_SESSAO_EXPIRADA", () => {
  assert.equal(MENSAGEM_SESSAO_EXPIRADA, "Sua sessão expirou. Entre de novo para continuar.");
});

test("ligacao: layout envolve AppShell com LoginGate", () => {
  const src = lerFonte("app/layout.tsx");
  assert.match(src, /<LoginGate>\s*<AppShell>\{children\}<\/AppShell>\s*<\/LoginGate>/);
});

test("ligacao: Sidebar e Topbar renderizam ContaUsuario", () => {
  assert.match(lerFonte("src/components/shell/Sidebar.tsx"), /<ContaUsuario/);
  assert.match(lerFonte("src/components/shell/Topbar.tsx"), /<ContaUsuario/);
});

test("ligacao: LoginGate usa /api/auth/me e o evento de sessao expirada", () => {
  const src = lerFonte("src/components/auth/LoginGate.tsx");
  assert.match(src, /EVENTO_SESSAO_EXPIRADA/);
  assert.match(src, /\/api\/auth\/me/);
  assert.match(src, /gis-script/);
});

test("ligacao: ContaUsuario chama logout", () => {
  assert.match(lerFonte("src/components/auth/ContaUsuario.tsx"), /\/api\/auth\/logout/);
});

test("arquivos novos nao usam cor literal", () => {
  for (const f of [
    "src/components/auth/LoginGate.tsx",
    "src/components/auth/ContaUsuario.tsx",
    "src/lib/login-mensagens.ts",
  ]) {
    const src = lerFonte(f);
    assert.doesNotMatch(src, /#[0-9a-fA-F]{3,8}\b/, f);
    assert.doesNotMatch(src, /\bbg-white\b|\btext-gray-|\brgba?\(/, f);
  }
});
