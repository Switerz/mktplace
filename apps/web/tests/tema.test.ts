/**
 * Gate UX-TORRE-1 — contrato do tema da Torre.
 *
 * Testa a LOGICA pura (preferencia -> tema pintado) e, onde a garantia so'
 * existe na montagem, o CODIGO-FONTE. `node --test` nao tem DOM: o que se
 * trava aqui e' o contrato; a medicao de contraste e de ausencia de flash foi
 * feita em navegador real e esta' no relatorio do gate.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  CLASSE_TEMA_CLARO_FIXO,
  CONSULTA_ESCURO,
  ROTAS_TEMATIZADAS,
  ROTULO_TEMA,
  TEMAS,
  TEMA_PADRAO,
  THEME_STORAGE_KEY,
  aplicarTemaNoDocumento,
  resolverTema,
  rotaTematizada,
  sanitizarTema,
  scriptDeTema,
} from "../src/lib/theme.ts";

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8");

/**
 * Remove comentarios antes de procurar um padrao proibido.
 *
 * Os comentarios deste gate CITAM o que foi banido (`darkMode: "media"`, a
 * variante `dark:`) para explicar por que. Um teste que proibisse a citacao
 * obrigaria a documentar menos — mesmo criterio ja usado no teste de PII da
 * Expedicao.
 */
const semComentarios = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

const CSS = fonte("app/globals.css");
const TAILWIND = fonte("tailwind.config.ts");
const LAYOUT = fonte("app/layout.tsx");
const SHELL = fonte("src/components/shell/AppShell.tsx");

// ===========================================================================
// 1. Tres estados, nao dois
// ===========================================================================
test("a preferencia tem tres estados e o padrao segue o sistema", () => {
  assert.deepEqual([...TEMAS], ["light", "dark", "system"]);
  assert.equal(TEMA_PADRAO, "system");
  // Um interruptor de dois estados obrigaria a primeira visita a escolher um
  // lado e perderia o acompanhamento de quem troca o tema do SO ao anoitecer.
  assert.equal(Object.keys(ROTULO_TEMA).length, 3);
});

test("resolverTema e' a UNICA fonte da verdade do que sera pintado", () => {
  assert.equal(resolverTema("light", true), "light", "manual sobrepoe o sistema");
  assert.equal(resolverTema("dark", false), "dark", "manual sobrepoe o sistema");
  assert.equal(resolverTema("system", true), "dark");
  assert.equal(resolverTema("system", false), "light");
});

test("valor estranho no armazenamento cai no padrao, nao quebra a tela", () => {
  for (const v of ["", null, undefined, "DARK", "escuro", "1", "<script>"]) {
    assert.equal(sanitizarTema(v as string), TEMA_PADRAO, String(v));
  }
  for (const v of TEMAS) assert.equal(sanitizarTema(v), v);
});

// ===========================================================================
// 2. Nada de flash: o script roda antes da primeira pintura
// ===========================================================================
test("o script inicial e' sincrono, no <head>, e protegido por try/catch", () => {
  const s = scriptDeTema();
  // `localStorage` LEVANTA (nao devolve null) em janela anonima com
  // armazenamento bloqueado — sem o catch a pagina inteira ficaria sem tema.
  assert.match(s, /try\{/);
  assert.match(s, /catch/);
  assert.ok(s.includes(JSON.stringify(THEME_STORAGE_KEY)), "le a MESMA chave do provedor");
  assert.match(s, /classList\.toggle\("dark"/);
  assert.match(s, /colorScheme/, "color-scheme precisa acompanhar o tema");
  assert.ok(s.includes(JSON.stringify(CONSULTA_ESCURO)));
  // Injetado direto no <head>: `defer`/`async` o fariam rodar DEPOIS da
  // primeira pintura, que e' exatamente o flash que ele existe para evitar.
  assert.match(LAYOUT, /<head>/);
  assert.match(LAYOUT, /dangerouslySetInnerHTML=\{\{ __html: scriptDeTema\(\) \}\}/);
  assert.doesNotMatch(LAYOUT, /<script[^>]*\b(defer|async)\b/);
});

test("o <html> aceita a divergencia que o proprio script cria", () => {
  // O script altera `class` e `style` de <html> antes da hidratacao, DE
  // PROPOSITO. Sem `suppressHydrationWarning` o React reclamaria da unica
  // divergencia que ele precisa aceitar.
  assert.match(LAYOUT, /<html lang="pt-BR" suppressHydrationWarning>/);
});

test("aplicarTemaNoDocumento grava resultado e preferencia separadamente", () => {
  const registros: Record<string, unknown> = {};
  const raiz = {
    classList: { toggle: (c: string, on: boolean) => (registros[`classe:${c}`] = on) },
    style: { colorScheme: "" },
    setAttribute: (n: string, v: string) => (registros[n] = v),
  };
  assert.equal(aplicarTemaNoDocumento(raiz, "system", true), "dark");
  assert.equal(registros["classe:dark"], true);
  assert.equal(raiz.style.colorScheme, "dark");
  // `data-tema` guarda a PREFERENCIA ("system"), a classe guarda o RESULTADO
  // ("dark"). Sao coisas diferentes e o seletor precisa das duas.
  assert.equal(registros["data-tema"], "system");

  assert.equal(aplicarTemaNoDocumento(raiz, "light", true), "light");
  assert.equal(registros["classe:dark"], false);
  assert.equal(registros["data-tema"], "light");
});

// ===========================================================================
// 3. Tema por CLASSE, nunca por media query
// ===========================================================================
test("o Tailwind alterna por classe: media query impediria escolha manual", () => {
  const tw = semComentarios(TAILWIND);
  assert.match(tw, /darkMode:\s*"class"/);
  assert.doesNotMatch(tw, /darkMode:\s*"media"/);
});

test("todo token semantico existe nos DOIS temas", () => {
  // Um token definido so' no claro faria o escuro herdar a cor clara — o
  // pedaco claro perdido dentro do escuro, que e' o defeito que este gate
  // existe para eliminar.
  const bloco = (seletor: string) => {
    const i = CSS.indexOf(seletor);
    assert.ok(i > 0, `bloco ${seletor} ausente`);
    const fim = CSS.indexOf("\n  }", i);
    return CSS.slice(i, fim);
  };
  const nomes = (texto: string) =>
    new Set([...texto.matchAll(/--tc-([\w-]+):/g)].map((m) => m[1]));

  const claro = nomes(bloco(":root,"));
  const escuro = nomes(bloco(".dark {"));
  assert.ok(claro.size >= 20, `poucos tokens no claro: ${claro.size}`);
  assert.deepEqual(
    [...claro].filter((t) => !escuro.has(t)),
    [],
    "token sem valor no tema escuro",
  );
  assert.deepEqual(
    [...escuro].filter((t) => !claro.has(t)),
    [],
    "token sem valor no tema claro",
  );
});

test("os tokens sao canais RGB, para o alfa do Tailwind funcionar", () => {
  // Com `#rrggbb` toda utilidade com opacidade (`bg-surface/60`) quebraria em
  // silencio: o Tailwind injeta o alfa dentro de `rgb(... / <alpha-value>)`.
  assert.match(TAILWIND, /rgb\(var\(--tc-\$\{nome\}\) \/ <alpha-value>\)/);
  const valores = [...CSS.matchAll(/--tc-(?!chart-grid|chart-axis)[\w-]+:\s*([^;]+);/g)].map(
    (m) => m[1].trim(),
  );
  const cores = valores.filter((v) => /^\d/.test(v));
  assert.ok(cores.length >= 20);
  for (const v of cores) {
    assert.match(v, /^\d{1,3} \d{1,3} \d{1,3}$/, `token fora do formato RGB: ${v}`);
  }
});

// ===========================================================================
// 4. A barreira da migracao
// ===========================================================================
test("rota nao migrada volta ao tema claro em vez de ficar meio escura", () => {
  assert.ok(rotaTematizada("/expedicao"));
  assert.ok(rotaTematizada("/expedicao/qualquer-coisa"));
  for (const r of ["/", "/canais", "/financeiro", "/produtos", null, undefined, ""]) {
    assert.equal(rotaTematizada(r as string), false, String(r));
  }
  // A barreira precisa estar LIGADA no shell, nao so' existir na biblioteca.
  assert.match(SHELL, /rotaTematizada\(pathname\)/);
  assert.match(SHELL, /tematizada \? "" : CLASSE_TEMA_CLARO_FIXO/);
  assert.ok(CSS.includes(`.${CLASSE_TEMA_CLARO_FIXO}`), "a classe precisa existir no CSS");
});

test("a lista de rotas migradas e' explicita e comeca pela Expedicao", () => {
  // Cada PR da sequencia remove a propria rota daqui ao migrar. Sem lista, a
  // migracao vira "quem lembrar".
  assert.deepEqual([...ROTAS_TEMATIZADAS], ["/expedicao"]);
});

// ===========================================================================
// 5. O `dark:` orfao nao pode voltar
// ===========================================================================
test("nenhum arquivo da fatia migrada usa a variante `dark:` solta", () => {
  // O `TikTokDispatchPanel` era o UNICO arquivo da Torre com classes `dark:*`,
  // numa aplicacao sem tema escuro global: com o SO em escuro, so' aquele
  // painel ficava preto dentro de uma tela clara. Com tokens semanticos a
  // variante deixa de ser necessaria — o papel ja' muda de valor sozinho.
  const fatia = [
    "app/expedicao/ExpedicaoClient.tsx",
    "app/expedicao/ExpedicaoShell.tsx",
    "src/components/expedicao/TikTokDispatchPanel.tsx",
    "src/components/expedicao/TendenciaChart.tsx",
    "src/components/shell/AppShell.tsx",
    "src/components/shell/Topbar.tsx",
    "src/components/shell/Sidebar.tsx",
    "src/components/shell/NavList.tsx",
    "src/components/shell/MobileDrawer.tsx",
    "src/components/ui/Panel.tsx",
    "src/components/ui/KpiTile.tsx",
    "src/components/ui/Chip.tsx",
    "src/components/ui/Note.tsx",
    "src/components/ui/Segmented.tsx",
    "src/components/ui/PageBar.tsx",
    "src/components/ui/Disclosure.tsx",
    "src/components/ui/SortHeader.tsx",
  ];
  for (const rel of fatia) {
    assert.doesNotMatch(
      semComentarios(fonte(rel)),
      /\bdark:[a-z[]/,
      `${rel} ainda tem variante dark: solta`,
    );
  }
});

test("a fatia migrada nao reintroduz cor literal de paleta", () => {
  // Cor literal (`bg-white`, `text-slate-600`, `border-violet-100`) e' clara
  // por definicao e nao acompanha o tema. O token acompanha.
  const fatia = [
    "app/expedicao/ExpedicaoClient.tsx",
    "src/components/expedicao/TikTokDispatchPanel.tsx",
    "src/components/shell/Topbar.tsx",
    "src/components/shell/Sidebar.tsx",
    "src/components/shell/NavList.tsx",
    "src/components/shell/MobileDrawer.tsx",
  ];
  const literais =
    /\b(?:bg|text|border|ring|divide)-(?:white|black|slate|gray|zinc|neutral|violet|purple|emerald|amber|rose|red|green|sky|cyan)-?\d*\b/;
  for (const rel of fatia) {
    const codigo = fonte(rel)
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    const achado = codigo.match(literais);
    assert.equal(achado, null, `${rel} usa cor literal: ${achado?.[0]}`);
  }
});

// ===========================================================================
// 6. O seletor
// ===========================================================================
test("o seletor de tema e' um radiogroup com as tres opcoes", () => {
  const toggle = fonte("src/components/theme/ThemeToggle.tsx");
  const seg = fonte("src/components/ui/Segmented.tsx");
  assert.match(toggle, /valor: t,\s*\n?\s*texto: ROTULO_TEMA\[t\]/);
  assert.match(toggle, /descricao: DESCRICAO_TEMA\[t\]/, "rotulo acessivel por opcao");
  assert.match(seg, /role="radiogroup"/);
  assert.match(seg, /role="radio"/);
  assert.match(seg, /aria-checked=\{ativo\}/);
  assert.match(seg, /tabIndex=\{ativo \? 0 : -1\}/);
  for (const tecla of ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"]) {
    assert.ok(seg.includes(tecla), `sem tratamento de ${tecla}`);
  }
  // Alvo de 44px explicito: `py-*` mais `line-height` chegaria la' por acidente
  // aritmetico e qualquer troca de fonte derrubaria o alvo sem sinal.
  assert.match(seg, /min-h-11 min-w-11/);
});

test("'sistema' continua seguindo o sistema com a tela aberta", () => {
  const prov = fonte("src/components/theme/ThemeProvider.tsx");
  assert.match(prov, /addEventListener\("change"/);
  assert.match(prov, /removeEventListener\("change"/);
  assert.match(prov, /preferencia !== "system"/);
});

test("a escolha manual e' persistida, e falha de armazenamento nao derruba", () => {
  const prov = fonte("src/components/theme/ThemeProvider.tsx");
  assert.match(prov, /localStorage\.setItem\(THEME_STORAGE_KEY, p\)/);
  assert.equal((prov.match(/try \{/g) ?? []).length >= 2, true, "leitura E escrita protegidas");
});
