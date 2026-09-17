// Gate EXP-2C1-H1 — o landmark `main` e unico e pertence ao AppShell.
//
// Contexto: o smoke de producao da Expedicao (EXP-2C1-S) mediu DOIS `<main>`
// em /expedicao — o do AppShell e um segundo aberto pelo ExpedicaoClient. Toda
// outra rota tinha um. Dois landmarks `main` quebram a navegacao por landmarks
// no leitor de tela: "ir para o conteudo principal" passa a ser ambiguo.
//
// A inspecao e por AST do proprio TypeScript (ja e devDependency; nenhuma
// dependencia nova). Isso importa: um `<main>` escrito dentro de comentario
// — `{/* <main> */}` ou `// <main>` — NAO vira no da arvore, entao este teste
// nao reprova documentacao nem aprova defeito escondido em texto. Contagem
// textual cega faria as duas coisas erradas.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import ts from "typescript";

const RAIZ = join(import.meta.dirname, "..");
const APP_SHELL = "src/components/shell/AppShell.tsx";

/** Todos os componentes JSX do app, sem node_modules nem build. */
function componentesJsx(): string[] {
  const achados: string[] = [];
  const andar = (dir: string): void => {
    for (const entrada of readdirSync(dir, { withFileTypes: true })) {
      const caminho = join(dir, entrada.name);
      if (entrada.isDirectory()) {
        if (entrada.name === "node_modules" || entrada.name === ".next") continue;
        andar(caminho);
        continue;
      }
      if (/\.(tsx|jsx)$/.test(entrada.name)) achados.push(caminho);
    }
  };
  andar(join(RAIZ, "app"));
  andar(join(RAIZ, "src"));
  return achados.map((c) => relative(RAIZ, c).split("\\").join("/"));
}

function arvore(rel: string): ts.SourceFile {
  return ts.createSourceFile(
    rel,
    readFileSync(join(RAIZ, rel), "utf8"),
    ts.ScriptTarget.Latest,
    /* setParentNodes */ true,
    ts.ScriptKind.TSX,
  );
}

type Marca = { arquivo: string; linha: number; motivo: "elemento" | "role" };

/** Elementos `main` e atributos role="main" REAIS — comentarios nao contam. */
function landmarks(rel: string): Marca[] {
  const fonte = arvore(rel);
  const marcas: Marca[] = [];
  const linhaDe = (no: ts.Node) =>
    fonte.getLineAndCharacterOfPosition(no.getStart(fonte)).line + 1;

  const visitar = (no: ts.Node): void => {
    if (ts.isJsxOpeningElement(no) || ts.isJsxSelfClosingElement(no)) {
      if (no.tagName.getText(fonte) === "main") {
        marcas.push({ arquivo: rel, linha: linhaDe(no), motivo: "elemento" });
      }
      for (const atributo of no.attributes.properties) {
        if (!ts.isJsxAttribute(atributo)) continue;
        if (atributo.name.getText(fonte) !== "role") continue;
        const valor = atributo.initializer;
        const texto =
          valor && ts.isStringLiteral(valor)
            ? valor.text
            : valor &&
                ts.isJsxExpression(valor) &&
                valor.expression &&
                ts.isStringLiteralLike(valor.expression)
              ? valor.expression.text
              : null;
        if (texto === "main") {
          marcas.push({ arquivo: rel, linha: linhaDe(atributo), motivo: "role" });
        }
      }
    }
    ts.forEachChild(no, visitar);
  };
  visitar(fonte);
  return marcas;
}

const TODOS = componentesJsx().flatMap(landmarks);

test("o AppShell continua sendo dono de um landmark `main`", () => {
  const doShell = landmarks(APP_SHELL);
  assert.deepEqual(
    doShell.map((m) => m.motivo),
    ["elemento"],
    "o AppShell precisa declarar exatamente um <main> e nenhum role=\"main\"",
  );
});

test("o `main` do AppShell envolve o conteudo da rota", () => {
  // Se o landmark existir mas nao envolver `{children}`, o leitor de tela pula
  // justamente o conteudo — o defeito seria pior que o duplicado.
  const fonte = arvore(APP_SHELL);
  let envolve = false;
  const visitar = (no: ts.Node): void => {
    if (ts.isJsxElement(no) && no.openingElement.tagName.getText(fonte) === "main") {
      envolve = no.children.some(
        (filho) =>
          ts.isJsxExpression(filho) &&
          filho.expression !== undefined &&
          ts.isIdentifier(filho.expression) &&
          filho.expression.text === "children",
      );
    }
    ts.forEachChild(no, visitar);
  };
  visitar(fonte);
  assert.ok(envolve, "<main> do AppShell precisa renderizar {children}");
});

test("nenhum componente da Expedicao declara `main` nem role=\"main\"", () => {
  const daExpedicao = TODOS.filter((m) => m.arquivo.startsWith("app/expedicao/"));
  assert.deepEqual(
    daExpedicao,
    [],
    "a tela da Expedicao vive DENTRO do <main> do AppShell; abrir o seu proprio "
      + "cria um segundo landmark (regressao do EXP-2B, corrigida no EXP-2C1-H1)",
  );
});

test("o AppShell e o UNICO lugar do app que declara o landmark `main`", () => {
  const forasteiros = TODOS.filter((m) => m.arquivo !== APP_SHELL);
  assert.deepEqual(
    forasteiros,
    [],
    "todas as rotas sao embrulhadas pelo AppShell em app/layout.tsx: qualquer "
      + "outro <main> ou role=\"main\" duplica o landmark na rota inteira",
  );
});

test("toda rota compoe exatamente um landmark `main`", () => {
  // Invariante de composicao, nao de arquivo: RootLayout embrulha tudo com
  // AppShell, entao o total por rota e (1 do shell) + (0 das paginas).
  //
  // Tambem por AST: casar o texto `<AppShell>{children}</AppShell>` aprovaria
  // a mesma frase dentro de um comentario e reprovaria uma quebra de linha do
  // formatador — ruido nos dois sentidos.
  const layout = arvore("app/layout.tsx");
  let embrulha = false;
  const procurar = (no: ts.Node): void => {
    if (ts.isJsxElement(no) && no.openingElement.tagName.getText(layout) === "AppShell") {
      embrulha ||= no.children.some(
        (filho) =>
          ts.isJsxExpression(filho) &&
          filho.expression !== undefined &&
          ts.isIdentifier(filho.expression) &&
          filho.expression.text === "children",
      );
    }
    ts.forEachChild(no, procurar);
  };
  procurar(layout);
  assert.ok(embrulha, "o RootLayout precisa continuar embrulhando as rotas no AppShell");
  assert.equal(TODOS.length, 1, `landmarks declarados: ${JSON.stringify(TODOS)}`);
  assert.equal(TODOS[0]?.arquivo, APP_SHELL);
});

test("a raiz da tela da Expedicao continua sendo um contêiner com aria-busy", () => {
  // A correcao troca a TAG, nao a estrutura: se alguem remover o wrapper, o
  // layout (max-w, gap, padding) e o estado de carregamento vao junto.
  const rel = "app/expedicao/ExpedicaoClient.tsx";
  const fonte = arvore(rel);
  // Ancorar na FUNCAO do componente, nao no primeiro `return` do arquivo: os
  // auxiliares (Cartao etc.) tambem retornam JSX e vem antes na travessia.
  const componente = fonte.statements.find(
    (s): s is ts.FunctionDeclaration =>
      ts.isFunctionDeclaration(s) && s.name?.text === "ExpedicaoClient",
  );
  assert.ok(componente?.body, "ExpedicaoClient precisa ser uma funcao declarada");
  const retorno = componente.body.statements.find(ts.isReturnStatement);
  assert.ok(retorno?.expression, "ExpedicaoClient precisa retornar JSX");
  const alvo = ts.isParenthesizedExpression(retorno.expression)
    ? retorno.expression.expression
    : retorno.expression;
  assert.ok(ts.isJsxElement(alvo), "a raiz precisa ser um elemento JSX com filhos");
  const elemento = alvo.openingElement;
  assert.equal(elemento.tagName.getText(fonte), "div");
  const atributos = elemento.attributes.properties
    .filter(ts.isJsxAttribute)
    .map((a) => a.name.getText(fonte));
  assert.ok(atributos.includes("className"), "className preservado");
  assert.ok(atributos.includes("aria-busy"), "aria-busy preservado");
});
