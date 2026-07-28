// Regressao estatica (Gate U5, Task 4) — confere as guardas de
// confiabilidade do polling de Tempo Real diretamente no codigo-fonte. Nao
// ha harness de componente React neste projeto (node:test puro): a logica
// de estado em si (computeTempoRealStatus) e' testada isoladamente em
// tempo-real-status.test.ts; aqui confere-se que o componente FIA o
// resultado dessa logica corretamente (guarda de concorrencia, guarda de
// unmount, e que uma falha nunca sobrescreve o ultimo dado valido).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const SRC = fs.readFileSync(
  path.join(import.meta.dirname, "..", "app", "tempo-real", "page.tsx"),
  "utf8",
);

test("doFetch guarda contra requests sobrepostas (timer automatico x botao manual) via inFlightRef", () => {
  assert.match(SRC, /if \(inFlightRef\.current\) return;/, "doFetch deve retornar cedo se ja houver um fetch em voo");
  assert.match(SRC, /inFlightRef\.current = true;/, "doFetch deve marcar inFlightRef como ocupado antes do fetch");
});

test("doFetch nunca atualiza estado apos unmount (mountedRef)", () => {
  const doFetchStart = SRC.indexOf("const doFetch = ");
  const doFetchEnd = SRC.indexOf("// carga inicial");
  assert.ok(doFetchStart > -1 && doFetchEnd > doFetchStart, "doFetch nao encontrado no arquivo");
  const doFetchBody = SRC.slice(doFetchStart, doFetchEnd);
  const mountedGuards = doFetchBody.match(/if \(!mountedRef\.current\) return;/g) ?? [];
  assert.ok(mountedGuards.length >= 2, "doFetch deve checar mountedRef tanto no .then quanto no .catch/.finally");
  assert.match(SRC, /mountedRef\.current = false;/, "cleanup do efeito de carga inicial deve marcar mountedRef=false no unmount");
});

test("falha de fetch NUNCA chama setData — o ultimo dado valido e' sempre preservado", () => {
  const doFetchStart = SRC.indexOf("const doFetch = ");
  const doFetchEnd = SRC.indexOf("// carga inicial");
  const doFetchBody = SRC.slice(doFetchStart, doFetchEnd);
  // O unico setData(...) dentro de doFetch deve estar no ramo de sucesso
  // (dentro do `if (res?.data)`), nunca no ramo de falha (`else`/`.catch`).
  const setDataCalls = doFetchBody.match(/setData\(/g) ?? [];
  assert.equal(setDataCalls.length, 1, "doFetch deve chamar setData exatamente uma vez (so no sucesso)");
  const successBranchIdx = doFetchBody.indexOf("if (res?.data)");
  const setDataIdx = doFetchBody.indexOf("setData(");
  assert.ok(successBranchIdx > -1 && setDataIdx > successBranchIdx, "setData deve estar dentro do ramo de sucesso (if (res?.data))");
});

test("falha de fetch NUNCA atualiza lastUpdated — so o sucesso atualiza", () => {
  const setLastUpdatedCalls = SRC.match(/setLastUpdated\(/g) ?? [];
  assert.equal(setLastUpdatedCalls.length, 1, "setLastUpdated deve ser chamado exatamente uma vez em todo o arquivo (so no sucesso do fetch)");
});

test("falha de fetch marca lastFetchFailed=true tanto no branch else quanto no catch", () => {
  const setFailedCalls = SRC.match(/setLastFetchFailed\(true\)/g) ?? [];
  assert.ok(setFailedCalls.length >= 2, "setLastFetchFailed(true) deve aparecer no branch de falha (res sem dado) e no catch");
});

test("timer (countdown) e' sempre limpo no cleanup do useEffect", () => {
  const clearIntervalCalls = SRC.match(/clearInterval\(/g) ?? [];
  assert.equal(clearIntervalCalls.length, 1, "esperado exatamente 1 clearInterval — so resta o tick do countdown (Finding 1: o segundo relogio, de auto-refresh, foi removido)");
});

// --- Finding 1 (rodada de correcao) — fonte unica de verdade do agendamento ---
//
// Antes, dois `setInterval` independentes coexistiam: o tick do countdown
// (1s) e um `setInterval(() => doFetch(true), REFRESH_INTERVAL_S * 1000)`
// criado uma unica vez na montagem. Um refresh manual (ou uma tentativa
// demorada) reiniciava o countdown para 300s no `finally` de `doFetch`, mas
// o segundo relogio continuava disparando no horario ORIGINAL — o texto
// podia mostrar "4:00" enquanto o proximo fetch real ja estava a 2 minutos
// de distancia. Os testes abaixo provam, estaticamente, que esse segundo
// relogio foi removido e que o countdown passou a ser a UNICA fonte que
// decide "quando" o proximo refresh automatico acontece.

test("nao existe mais um setInterval independente chamando doFetch diretamente", () => {
  // Fora de comentarios/strings, nenhuma chamada de setInterval deve ter
  // `doFetch` como callback — o unico setInterval real do arquivo decrementa
  // `countdown` via `setCountdown`, nunca chama `doFetch`.
  const codeLines = SRC.split("\n").filter((line) => !line.trim().startsWith("//"));
  const code = codeLines.join("\n");
  assert.doesNotMatch(code, /setInterval\(\s*\(\)\s*=>\s*doFetch/, "nao pode existir um setInterval(() => doFetch(...)) fora de comentario — essa era a segunda fonte de tempo (Finding 1)");
});

test("existe exatamente 1 chamada real de setInterval no codigo (fora de comentarios) — o tick do countdown", () => {
  const codeLines = SRC.split("\n").filter((line) => !line.trim().startsWith("//"));
  const code = codeLines.join("\n");
  const setIntervalCalls = code.match(/setInterval\(/g) ?? [];
  assert.equal(setIntervalCalls.length, 1, `esperado exatamente 1 setInterval real (tick do countdown), encontrado ${setIntervalCalls.length}`);
});

test("o countdown chegar a zero e' o unico gatilho do refresh automatico (efeito dependente de [countdown])", () => {
  assert.match(SRC, /useEffect\(\(\) => \{\s*if \(countdown === 0\) doFetch\(true\);/, "deve existir um useEffect que chama doFetch(true) quando countdown chega a 0");
  const effectIdx = SRC.indexOf("if (countdown === 0) doFetch(true);");
  assert.notEqual(effectIdx, -1);
  const after = SRC.slice(effectIdx, effectIdx + 200);
  assert.match(after, /\[countdown\]/, "o efeito que dispara o refresh automatico deve depender de [countdown]");
});

test("doFetch reseta countdown para REFRESH_INTERVAL_S no finally — unico ponto de reagendamento, cobre sucesso/falha/manual/automatico", () => {
  const doFetchStart = SRC.indexOf("const doFetch = ");
  const doFetchEnd = SRC.indexOf("// carga inicial");
  const doFetchBody = SRC.slice(doFetchStart, doFetchEnd);
  const setCountdownCalls = doFetchBody.match(/setCountdown\(REFRESH_INTERVAL_S\)/g) ?? [];
  assert.equal(setCountdownCalls.length, 1, "setCountdown(REFRESH_INTERVAL_S) deve aparecer exatamente 1 vez dentro de doFetch");
  const finallyIdx = doFetchBody.indexOf(".finally(");
  const setCountdownIdx = doFetchBody.indexOf("setCountdown(REFRESH_INTERVAL_S)");
  assert.ok(finallyIdx > -1 && setCountdownIdx > finallyIdx, "setCountdown(REFRESH_INTERVAL_S) deve estar dentro do .finally() — roda em qualquer desfecho (sucesso/falha), disparado por qualquer chamador (manual ou automatico)");
  // So existe UMA definicao de doFetch no arquivo — logo, qualquer chamador
  // (clique manual, carga inicial, ou o efeito de countdown===0) reagenda
  // o MESMO ciclo real ao concluir, nunca dois ciclos divergentes.
  const doFetchDefinitions = SRC.match(/const doFetch = /g) ?? [];
  assert.equal(doFetchDefinitions.length, 1, "deve existir exatamente 1 definicao de doFetch, compartilhada por carga inicial, refresh manual e refresh automatico");
});

// --- U6-02 — guarda de hidratacao (React #418) da data/hora ---
//
// A data/hora do cabecalho derivam do relogio (new Date()), que difere entre
// o SSR e o primeiro render do cliente — gerando hydration mismatch #418. A
// correcao usa um estado `clientReady` (false no SSR e no 1o render do
// cliente, ambos exibindo o mesmo placeholder deterministico) ativado em
// useEffect. Nao pode voltar a ler o relogio no render sem guarda, nem
// esconder o erro so com suppressHydrationWarning, nem criar novo agendamento.

test("existe estado client-ready inicialmente false", () => {
  assert.match(SRC, /const \[clientReady, setClientReady\] = useState\(false\);/, "deve existir clientReady = useState(false)");
});

test("clientReady e ativado dentro de um useEffect", () => {
  assert.match(SRC, /useEffect\(\(\) => \{\s*setClientReady\(true\);\s*\}, \[\]\);/, "setClientReady(true) deve rodar num useEffect de montagem");
});

test("data/hora NAO sao lidas de new Date() no render sem guarda de clientReady", () => {
  // O unico new Date() do escopo de render (fora do doFetch, que usa em
  // setLastUpdated) deve estar guardado por clientReady.
  assert.match(SRC, /const now = clientReady \? new Date\(\) : null;/, "now deve ser derivado condicionalmente de clientReady");
  // dateLabel/hourLabel derivam de `now` (que ja depende de clientReady),
  // nunca chamando new Date() diretamente na sua atribuicao.
  const dateLabelIdx = SRC.indexOf("const dateLabel =");
  const hourLabelIdx = SRC.indexOf("const hourLabel =");
  assert.ok(dateLabelIdx > -1 && hourLabelIdx > -1, "dateLabel e hourLabel devem existir");
  const labelsBlock = SRC.slice(dateLabelIdx, hourLabelIdx + 200);
  assert.doesNotMatch(labelsBlock, /new Date\(\)/, "dateLabel/hourLabel nao podem chamar new Date() diretamente — devem derivar de `now`");
});

test("placeholder pre-mount e deterministico (nao vem do relogio)", () => {
  assert.match(SRC, /"--:--"/, "hourLabel deve cair num placeholder estavel '--:--' antes de clientReady");
});

test("a correcao de hidratacao nao usa suppressHydrationWarning nem cria novo agendamento", () => {
  // Fora de comentarios (o codigo real), suppressHydrationWarning nao pode
  // aparecer — o fix corrige a causa, nao esconde o sintoma.
  const codeLines = SRC.split("\n").filter((line) => !line.trim().startsWith("//"));
  const code = codeLines.join("\n");
  assert.doesNotMatch(code, /suppressHydrationWarning/, "o fix nao pode apenas esconder o erro com suppressHydrationWarning");
  // continua havendo exatamente 1 setInterval real (o tick do countdown) —
  // o effect de clientReady nao introduz um segundo relogio.
  const setIntervalCalls = code.match(/setInterval\(/g) ?? [];
  assert.equal(setIntervalCalls.length, 1, "o fix de hidratacao nao pode introduzir um novo setInterval");
});
