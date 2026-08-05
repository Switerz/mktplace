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

test("refreshed_at do insight vem do executive-summary fresco (execFresh), nunca do overview", () => {
  const src = read("app/page.tsx");
  // a constante existe e é gated por execFresh sobre o period da MESMA resposta
  assert.match(
    src,
    /const pulseRefreshedAt = execFresh \? execSummary!\.period\.refreshed_at : null;/,
    "pulseRefreshedAt deve ser derivado de execSummary.period.refreshed_at com gate execFresh",
  );
  // e é exatamente ela que alimenta o conteúdo do insight
  const insightBlock = src.slice(src.indexOf("<InsightDrilldownContent"), src.indexOf("</KpiDrilldownDialog>", src.indexOf("<InsightDrilldownContent")));
  assert.match(insightBlock, /refreshedAt=\{pulseRefreshedAt\}/, "InsightDrilldownContent recebe pulseRefreshedAt");
  assert.doesNotMatch(insightBlock, /refreshedAt=\{refreshedAt\}/, "não pode receber o refreshedAt do overview");
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
