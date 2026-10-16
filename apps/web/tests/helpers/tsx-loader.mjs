/**
 * Loader de TESTE para renderizar componentes `.tsx` com `node --test`.
 *
 * `node` remove tipos de `.ts` sozinho, mas nao entende JSX nem o alias `@/`.
 * Este hook resolve `@/x` para `src/x` (com as extensoes que o bundler aceita)
 * e transpila `.tsx` com o `typescript` que ja' e' devDependency — nenhuma
 * dependencia nova. Usado so' pelos testes de renderizacao da DRE.
 */
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";
import ts from "typescript";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "src");
const EXTS = ["", ".tsx", ".ts", "/index.tsx", "/index.ts"];

function achar(base) {
  for (const ext of EXTS) if (existsSync(base + ext) && !base.endsWith("/") && (ext || /\.[cm]?[jt]sx?$/.test(base))) return base + ext;
  return null;
}

export async function resolve(spec, ctx, next) {
  if (spec.startsWith("@/")) {
    const alvo = achar(join(SRC, spec.slice(2)));
    if (alvo) return { url: pathToFileURL(alvo).href, shortCircuit: true };
  }
  if ((spec.startsWith("./") || spec.startsWith("../")) && ctx.parentURL?.startsWith("file:")) {
    const alvo = achar(join(dirname(fileURLToPath(ctx.parentURL)), spec));
    if (alvo) return { url: pathToFileURL(alvo).href, shortCircuit: true };
  }
  return next(spec, ctx);
}

export async function load(url, ctx, next) {
  if (url.endsWith(".tsx")) {
    const fonte = await readFile(fileURLToPath(url), "utf8");
    const { outputText } = ts.transpileModule(fonte, {
      compilerOptions: {
        jsx: ts.JsxEmit.ReactJSX,
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022,
        verbatimModuleSyntax: false,
      },
      fileName: fileURLToPath(url),
    });
    return { format: "module", source: outputText, shortCircuit: true };
  }
  return next(url, ctx);
}
