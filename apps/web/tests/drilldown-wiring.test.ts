// Regressão estática do contrato transversal de drill-down (Gate G2, Task 2
// — ver docs/DRILLDOWN_ARCHITECTURE.md). Mesmo padrão dos testes estáticos
// de wiring dos gates U4/U5: lê o código-fonte e confere invariantes que não
// dependem de harness de componente React:
//   1. os conteúdos de drill-down NÃO fazem fetch próprio (dados sempre já
//      carregados pela página);
//   2. existe UM único shell de diálogo (KpiDrilldownDialog) — os conteúdos
//      não portalizam nem criam modal próprio;
//   3. o refreshed_at do detalhe de insight vem da MESMA resposta fresca do
//      executive-summary (gate execFresh), nunca do overview nem de uma
//      requisição anterior;
//   4. o diálogo de Canais continua abrindo somente com dados frescos.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf-8");

const CONTENT_FILES = [
  "src/components/KpiDrilldownContent.tsx",
  "src/components/InsightDrilldownContent.tsx",
  "src/components/ChannelComparisonDialogContent.tsx",
  "src/components/drilldown/DrilldownContextLine.tsx",
  "src/components/drilldown/EvidenceRow.tsx",
  "src/components/drilldown/DataQualityNote.tsx",
  "src/components/drilldown/DrilldownCta.tsx",
];

test("conteudos de drill-down nao fazem fetch proprio", () => {
  for (const f of CONTENT_FILES) {
    const src = read(f);
    assert.doesNotMatch(src, /\bfetch\s*\(/, `${f} não deve chamar fetch`);
    assert.doesNotMatch(src, /useSWR|axios/, `${f} não deve usar client de dados`);
  }
});

test("um unico shell de dialogo: so KpiDrilldownDialog portaliza/define role=dialog", () => {
  const shell = read("src/components/KpiDrilldownDialog.tsx");
  assert.match(shell, /createPortal/);
  assert.match(shell, /role="dialog"/);
  for (const f of CONTENT_FILES) {
    const src = read(f);
    assert.doesNotMatch(src, /createPortal/, `${f} não deve portalizar modal próprio`);
    assert.doesNotMatch(src, /role="dialog"/, `${f} não deve definir um segundo dialog`);
  }
  // as duas páginas usam o MESMO shell importado, nunca um modal local
  for (const page of ["app/page.tsx", "app/canais/page.tsx"]) {
    const src = read(page);
    assert.match(src, /import KpiDrilldownDialog from "@\/components\/KpiDrilldownDialog"/, `${page} importa o shell canônico`);
    assert.doesNotMatch(src, /createPortal/, `${page} não deve criar modal paralelo`);
  }
});

// Gate V2-1: o wiring mudou (as seis fontes passaram a ter estado proprio em
// `useGerencialSources`), mas o INVARIANTE e' o mesmo — o refreshed_at do
// detalhe de insight vem da resposta fresca do executive-summary, nunca do
// overview. Agora quem garante o frescor e' `toSource`, que zera o campo
// enquanto a chave resolvida nao bate com a chave atual.
test("refreshed_at do insight vem do executive-summary fresco, nunca do overview", () => {
  const src = read("app/page.tsx");
  const insightBlock = src.slice(
    src.indexOf("<InsightDrilldownContent"),
    src.indexOf("/>", src.indexOf("<InsightDrilldownContent")),
  );
  assert.match(
    insightBlock,
    /refreshedAt=\{sources\.executiveSummary\.refreshedAt\}/,
    "InsightDrilldownContent recebe o refreshedAt da fonte executive-summary",
  );
  assert.doesNotMatch(
    insightBlock,
    /refreshedAt=\{sources\.overview\.refreshedAt\}/,
    "não pode receber o refreshedAt do overview",
  );

  // O frescor e' aplicado na fonte: fora de `status.fresh`, refreshedAt e' null.
  const hook = read("src/hooks/useGerencialSources.ts");
  assert.match(
    hook,
    /refreshedAt:\s*status\.fresh \? state\.refreshedAt : null/,
    "toSource deve anular refreshedAt quando a fonte não está fresca",
  );
  assert.match(hook, /data:\s*status\.fresh \? state\.data : null/, "dado obsoleto nunca vaza");
});

// Gate V2-1: os conteudos novos da Gerencial seguem a regra do shell unico.
// A regra de "nao fazer fetch proprio" tem UMA excecao autorizada aqui: o
// detalhe de um ponto da serie carrega overview+brands sob demanda para aquele
// dia, com os endpoints existentes (Task E, item 3). A excecao e' pontual e
// vale so' para esse conteudo — nao ha endpoint novo nem fetch nos demais.
test("conteudos de drill-down da Gerencial V2 nao criam shell nem role=dialog proprio", () => {
  const src = read("src/components/gerencial/GerencialDrilldowns.tsx");
  assert.doesNotMatch(src, /createPortal/, "não deve portalizar modal próprio");
  assert.doesNotMatch(src, /role="dialog"/, "não deve definir um segundo dialog");
  // o fetch sob demanda usa apenas os fetchers existentes, nunca uma rota nova
  assert.doesNotMatch(src, /\bfetch\s*\(\s*[`"']/, "não deve chamar fetch() cru com URL");
  assert.match(src, /fetchOverview|fetchBrands/, "usa os fetchers existentes");
});

test("dialogo de Canais so abre com dados frescos (dataIsFresh) e conteudo condicionado", () => {
  const src = read("app/canais/page.tsx");
  assert.match(src, /open=\{dataIsFresh && detailRow != null\}/, "abertura gated por frescor");
  assert.match(src, /\{dataIsFresh && detailRow && \(/, "conteúdo também gated por frescor");
});

test("CTAs dos conteudos passam pelo buildHref da pagina (filtros preservados)", () => {
  const canal = read("src/components/ChannelComparisonDialogContent.tsx");
  assert.match(canal, /DrilldownCta href=\{buildHref\(/);
  const kpi = read("src/components/KpiDrilldownContent.tsx");
  assert.match(kpi, /DrilldownCta href=\{buildHref\(/);
  const insight = read("src/components/InsightDrilldownContent.tsx");
  assert.match(insight, /DrilldownCta href=\{buildHref\(/);
});
// ---- Gate V3, correção terminal de foco na transição mobile ----
// Abaixo de 640 a matriz mora DENTRO do diálogo, e clicar num quadrante, faixa
// ou ponto troca `dialog.kind` com `open` ainda true. O botão clicado é
// desmontado e o foco cai no `document.body`. A correção é a prop opcional
// `focusResetKey`: um segundo efeito devolve o foco ao "Fechar" quando o
// CONTEÚDO muda, sem tocar em previousFocusRef, inert ou overflow.

const SHELL = "src/components/KpiDrilldownDialog.tsx";
const INTEL = "app/inteligencia/page.tsx";
/** Consumidores que NÃO passam a prop: contraprova de compatibilidade. */
const SEM_PROP = ["app/page.tsx", "app/canais/page.tsx", "app/brand/[brand]/page.tsx"];

/** Corpo do efeito cuja lista de dependências é exatamente `deps`. */
function corpoDoEfeito(src: string, deps: string): string {
  const alvo = "}, [" + deps + "]);";
  const fim = src.indexOf(alvo);
  assert.ok(fim > 0, "efeito com deps [" + deps + "] não encontrado");
  const inicio = src.lastIndexOf("useEffect(() => {", fim);
  assert.ok(inicio > 0, "abertura do efeito [" + deps + "] não encontrada");
  return src.slice(inicio, fim);
}

/** Avalia a expressão REAL de `dialogFocusKey` da página, não uma cópia. */
function chaveDeFoco(dialog: unknown): string {
  const src = read(INTEL);
  const inicio = src.indexOf("const dialogFocusKey =");
  assert.ok(inicio > 0, "dialogFocusKey não encontrada na página");
  const fim = src.indexOf(";", src.indexOf("evidence:", inicio));
  const expr = src.slice(inicio + "const dialogFocusKey =".length, fim);
  return new Function("dialog", "return (" + expr + ");")(dialog) as string;
}

test("V3G focusResetKey e' prop opcional e nao muda os consumidores existentes", () => {
  const shell = read(SHELL);
  assert.match(shell, /focusResetKey\?: string;/, "prop declarada como opcional");
  assert.match(shell, /\{ open, onClose, title, children, focusResetKey \}/, "prop desestruturada");
  // sem a prop o efeito novo nem roda: o comportamento anterior fica intacto
  assert.match(shell, /if \(!open \|\| focusResetKey === undefined\) return;/);
  for (const p of SEM_PROP) {
    assert.match(read(p), /<KpiDrilldownDialog/, p + " usa o shell canônico");
    assert.doesNotMatch(read(p), /focusResetKey/, p + " não passa a prop nova");
  }
  assert.ok(read(INTEL).includes("title={dialogTitle} focusResetKey={dialogFocusKey}>"),
    "só a Inteligência passa a chave");
});

test("V3G transicao matrix -> quadrant altera focusResetKey", () => {
  const matriz = chaveDeFoco({ kind: "matrix" });
  const quadrante = chaveDeFoco({ kind: "quadrant", key: "escalar" });
  assert.equal(matriz, "matrix");
  assert.equal(quadrante, "quadrant:escalar");
  assert.notEqual(matriz, quadrante);
});

test("V3G matrix -> band e matrix -> point tambem alteram focusResetKey", () => {
  const matriz = chaveDeFoco({ kind: "matrix" });
  const faixa = chaveDeFoco({ kind: "band", key: "alto_roas" });
  const ponto = chaveDeFoco({ kind: "point", highlight: { item_id: "MLB123" } });
  assert.equal(faixa, "band:alto_roas");
  assert.equal(ponto, "point:MLB123");
  assert.notEqual(matriz, faixa);
  assert.notEqual(matriz, ponto);
  // e os quatro conteúdos são distintos entre si: cada navegação refoca
  const chaves = [matriz, faixa, ponto, chaveDeFoco({ kind: "quadrant", key: "monitorar" })];
  assert.equal(new Set(chaves).size, 4, "chaves colidiriam e o foco não se moveria");
  // quadrantes diferentes também são conteúdos diferentes
  assert.notEqual(chaveDeFoco({ kind: "quadrant", key: "escalar" }), chaveDeFoco({ kind: "quadrant", key: "monitorar" }));
  // diálogo fechado tem chave própria, sem depender de conteúdo nenhum
  assert.equal(chaveDeFoco(null), "fechado");
});

test("V3G previousFocusRef so e' capturado na abertura", () => {
  const shell = read(SHELL);
  // uma única atribuição, e ela vive no efeito de abertura/fechamento
  assert.equal((shell.match(/previousFocusRef\.current =/g) ?? []).length, 1);
  const abertura = corpoDoEfeito(shell, "open");
  assert.match(abertura, /previousFocusRef\.current = document\.activeElement/);
  assert.match(abertura, /previousFocusRef\.current\?\.focus\(\)/, "devolução final do foco segue aqui");
  const reset = corpoDoEfeito(shell, "open, focusResetKey");
  assert.doesNotMatch(reset, /previousFocusRef/, "o efeito de reset não pode sobrescrever o acionador original");
});

test("V3G transicao interna nao executa cleanup de inert/overflow", () => {
  const shell = read(SHELL);
  const reset = corpoDoEfeito(shell, "open, focusResetKey");
  assert.match(reset, /closeButtonRef\.current\?\.focus\(\);/, "o efeito de reset foca o Fechar");
  for (const proibido of [/return \(\) =>/, /inert/, /style\.overflow/, /getElementById/]) {
    assert.doesNotMatch(reset, proibido, "o efeito de reset não pode ter cleanup nem tocar inert/overflow");
  }
  // inert e overflow continuam sendo exclusividade do efeito de abertura
  const abertura = corpoDoEfeito(shell, "open");
  assert.match(abertura, /setAttribute\("inert", ""\)/);
  assert.match(abertura, /removeAttribute\("inert"\)/);
  assert.match(abertura, /document\.body\.style\.overflow = "hidden"/);
  assert.equal((shell.match(/setAttribute\("inert"/g) ?? []).length, 1);
  assert.equal((shell.match(/style\.overflow =/g) ?? []).length, 2, "define e restaura, uma vez cada");
  // sem reabrir o diálogo para mover o foco
  assert.doesNotMatch(shell, /setTimeout|requestAnimationFrame/);
});

test("V3G continua existindo um unico role=dialog no fluxo da Inteligencia", () => {
  const shell = read(SHELL);
  assert.equal((shell.match(/role="dialog"/g) ?? []).length, 1);
  assert.equal((shell.match(/createPortal\(/g) ?? []).length, 1, "um unico portal");
  const intel = read(INTEL);
  assert.equal((intel.match(/<KpiDrilldownDialog/g) ?? []).length, 1, "um shell na página");
  assert.doesNotMatch(intel, /createPortal/, "a página não cria modal paralelo");
  assert.doesNotMatch(intel, /role="dialog"/, "a página não define um segundo dialog");
  const matriz = read("src/components/inteligencia/OpportunityMatrix.tsx");
  assert.doesNotMatch(matriz, /createPortal|role="dialog"/, "a matriz não abre modal próprio");
});
