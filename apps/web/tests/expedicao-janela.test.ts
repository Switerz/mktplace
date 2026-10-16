/**
 * Gate EXPEDICAO-UX-JANELA-CUSTOMIZAVEL-1 — "ultimos N dias" na Expedicao.
 *
 * O que erra em silencio aqui: uma entrada "quase numerica" (`1,5`, `1e1`,
 * ` 7 `) que o `Number()` aceita e vira uma janela que o operador nao
 * escreveu; um valor fora do limite que vira 422 e aparece como "fonte
 * indisponivel"; dias que viram horas em dois lugares e divergem; e um link
 * com janela que, ao recarregar, volta para o padrao sem avisar.
 */
import { register } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  OPCAO_PERSONALIZADA,
  PARAM_DIAS,
  REGRA_JANELA,
  descreverJanelaEmHoras,
  ehAtalho,
  lerDiasDaUrl,
  opcoesDoSeletor,
  queryDaTendenciaEmDias,
  urlComDias,
  urlSemDias,
  validarDias,
  valorDoSeletor,
} from "../src/lib/expedicao-janela.ts";
import { JANELA_MAX_HORAS } from "../src/lib/expedicao-contract.ts";
import { queryDoTikTok } from "../src/lib/expedicao-tiktok-contract.ts";

register("./helpers/tsx-loader.mjs", import.meta.url);

const RAIZ = join(import.meta.dirname, "..");
const fonte = (rel: string) => readFileSync(join(RAIZ, rel), "utf8");
const CLIENTE = fonte("app/expedicao/ExpedicaoClient.tsx");
const TIKTOK = fonte("src/components/expedicao/TikTokDispatchPanel.tsx");
const CASCA = fonte("app/expedicao/ExpedicaoShell.tsx");
// Sem comentarios: o cabecalho do campo EXPLICA por que nao usa `type="number"`.
const CAMPO = fonte("src/components/expedicao/JanelaPersonalizada.tsx")
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/^\s*\/\/.*$/gm, "");

const FILA = REGRA_JANELA.fila;
const TK = REGRA_JANELA.tiktok;
const BASE = "https://torre.example/expedicao";

// ===========================================================================
// 1. Limites: os mesmos da API, derivados e nao redigitados
// ===========================================================================
test("a fila vai de 1 a 14 dias (336 h) e o TikTok de 1 a 90", () => {
  assert.equal(FILA.max, 14);
  assert.equal(FILA.max * 24, JANELA_MAX_HORAS);
  assert.equal(TK.max, 90);
});

test("os atalhos atuais continuam os mesmos, agora em dias", () => {
  assert.deepEqual(FILA.atalhos.map((a) => [a.dias, a.rotulo]), [
    [1, "24h"],
    [3, "72h"],
    [7, "7d"],
  ]);
  assert.deepEqual(TK.atalhos.map((a) => a.dias), [7, 14, 30]);
});

test("o padrao de cada superficie e' um atalho", () => {
  assert.equal(FILA.padrao, 3);
  assert.equal(TK.padrao, 7);
  assert.ok(ehAtalho(FILA.padrao, FILA));
  assert.ok(ehAtalho(TK.padrao, TK));
});

// ===========================================================================
// 2. Validacao do texto digitado
// ===========================================================================
test("aceita so' inteiros positivos dentro do limite", () => {
  assert.deepEqual(validarDias("10", FILA.max), { ok: true, dias: 10 });
  assert.deepEqual(validarDias("14", FILA.max), { ok: true, dias: 14 });
  assert.deepEqual(validarDias("1", FILA.max), { ok: true, dias: 1 });
  assert.deepEqual(validarDias(" 7 ", FILA.max), { ok: true, dias: 7 }, "espaco nas pontas");
  assert.deepEqual(validarDias("007", FILA.max), { ok: true, dias: 7 }, "zero a esquerda");
  assert.deepEqual(validarDias("90", TK.max), { ok: true, dias: 90 });
});

test("vazio, decimal, negativo, zero e acima do limite viram mensagem", () => {
  const casos: [string, number, RegExp][] = [
    ["", FILA.max, /Informe quantos dias/],
    ["   ", FILA.max, /Informe quantos dias/],
    ["1.5", FILA.max, /inteiro/],
    ["1,5", FILA.max, /inteiro/],
    [".5", FILA.max, /inteiro/],
    ["2.", FILA.max, /inteiro/],
    ["-1", FILA.max, /positivo/],
    ["-0", FILA.max, /positivo/],
    ["0", FILA.max, /pelo menos 1 dia/],
    ["00", FILA.max, /pelo menos 1 dia/],
    ["15", FILA.max, /máximo neste canal é 14/],
    ["91", TK.max, /máximo neste canal é 90/],
    ["99999999999999999999999", TK.max, /máximo/],
  ];
  for (const [texto, max, msg] of casos) {
    const r = validarDias(texto, max);
    assert.equal(r.ok, false, `"${texto}" nao pode passar`);
    if (!r.ok) assert.match(r.erro, msg, `"${texto}"`);
  }
});

test("formas que o Number() aceita mas o operador nao escreveu sao recusadas", () => {
  // Number("1e1") = 10, Number("0x10") = 16, Number("+5") = 5.
  for (const texto of ["1e1", "0x10", "+5", "abc", "7 dias", "1 0", "٣"]) {
    const r = validarDias(texto, TK.max);
    assert.equal(r.ok, false, `"${texto}" nao pode passar`);
  }
});

test("todo valor aceito gera uma requisicao dentro do contrato da API", () => {
  for (let d = -5; d <= 400; d++) {
    const f = validarDias(String(d), FILA.max);
    if (f.ok) {
      const horas = Number(new URLSearchParams(queryDaTendenciaEmDias(f.dias, "shopee")).get("window_hours"));
      assert.ok(Number.isInteger(horas) && horas >= 1 && horas <= 336, `fila ${d}`);
    }
    const t = validarDias(String(d), TK.max);
    if (t.ok) assert.ok(t.dias >= 1 && t.dias <= 90, `tiktok ${d}`);
  }
});

// ===========================================================================
// 3. Conversao dias -> horas, so' na fila e so' na query
// ===========================================================================
test("a fila converte dias em horas ao montar a requisicao", () => {
  assert.equal(queryDaTendenciaEmDias(1, "shopee"), "window_hours=24");
  assert.equal(queryDaTendenciaEmDias(3, "shopee"), "window_hours=72");
  assert.equal(queryDaTendenciaEmDias(10, "shopee"), "window_hours=240");
  assert.equal(queryDaTendenciaEmDias(14, "mercadolivre"), "channel=mercadolivre&window_hours=336");
});

test("o TikTok manda dias, sem conversao", () => {
  assert.equal(queryDoTikTok("coleta", 45), "/api/v1/expedicao/tiktok?event=coleta&days=45");
});

test("os atalhos antigos saem com as MESMAS horas de antes", () => {
  // 24h, 72h e 7d continuam pedindo 24, 72 e 168 — nenhuma requisicao muda
  // para quem so' usa os atalhos.
  assert.deepEqual(
    FILA.atalhos.map((a) => queryDaTendenciaEmDias(a.dias, "shopee")),
    ["window_hours=24", "window_hours=72", "window_hours=168"],
  );
});

// ===========================================================================
// 4. URL
// ===========================================================================
test("sem `dias` na URL, cada superficie abre no seu padrao", () => {
  assert.deepEqual(lerDiasDaUrl("", FILA), { dias: 3, descartado: false });
  assert.deepEqual(lerDiasDaUrl("?channel=tiktokshop", TK), { dias: 7, descartado: false });
});

test("`dias` valido na URL e' restaurado", () => {
  assert.deepEqual(lerDiasDaUrl("?dias=10", FILA), { dias: 10, descartado: false });
  assert.deepEqual(lerDiasDaUrl("?channel=tiktokshop&dias=45", TK), { dias: 45, descartado: false });
});

test("`dias` invalido na URL cai no padrao E e' marcado como descartado", () => {
  for (const v of ["0", "-3", "1.5", "abc", "", "15"]) {
    assert.deepEqual(lerDiasDaUrl(`?dias=${encodeURIComponent(v)}`, FILA), { dias: 3, descartado: true }, v);
  }
  // 45 vale no TikTok e nao na fila.
  assert.deepEqual(lerDiasDaUrl("?dias=45", FILA), { dias: 3, descartado: true });
});

test("escrever a janela preserva o canal e tira o padrao da URL", () => {
  const ml = `${BASE}?channel=mercadolivre`;
  assert.equal(new URL(urlComDias(ml, 10, FILA)).searchParams.get(PARAM_DIAS), "10");
  assert.equal(new URL(urlComDias(ml, 10, FILA)).searchParams.get("channel"), "mercadolivre");
  const volta = new URL(urlComDias(`${ml}&dias=10`, FILA.padrao, FILA));
  assert.equal(volta.searchParams.has(PARAM_DIAS), false, "padrao nao suja a URL");
  assert.equal(volta.searchParams.get("channel"), "mercadolivre");
});

test("ida e volta pela URL devolve a mesma janela", () => {
  for (const d of [1, 2, 5, 10, 14]) {
    const u = new URL(urlComDias(BASE, d, FILA));
    assert.equal(lerDiasDaUrl(u.search, FILA).dias, d);
  }
  for (const d of [1, 7, 21, 60, 90]) {
    const u = new URL(urlComDias(`${BASE}?channel=tiktokshop`, d, TK));
    assert.equal(lerDiasDaUrl(u.search, TK).dias, d);
  }
});

test("trocar de superficie descarta a janela e mantem o resto", () => {
  const u = new URL(urlSemDias(`${BASE}?channel=tiktokshop&dias=45`));
  assert.equal(u.searchParams.has(PARAM_DIAS), false);
  assert.equal(u.searchParams.get("channel"), "tiktokshop");
});

// ===========================================================================
// 5. Seletor: sempre exatamente uma opcao acesa
// ===========================================================================
test("atalho acende o atalho; janela livre acende Personalizado", () => {
  assert.equal(valorDoSeletor(3, FILA, false), "3");
  assert.equal(valorDoSeletor(10, FILA, false), OPCAO_PERSONALIZADA);
  assert.equal(valorDoSeletor(3, FILA, true), OPCAO_PERSONALIZADA, "campo aberto pelo operador");
});

test("o valor aceso sempre existe entre as opcoes", () => {
  for (const regra of [FILA, TK]) {
    const valores = opcoesDoSeletor(regra).map((o) => o.valor);
    assert.equal(valores.at(-1), OPCAO_PERSONALIZADA);
    for (let d = 1; d <= regra.max; d++) {
      for (const aberto of [false, true]) {
        assert.ok(valores.includes(valorDoSeletor(d, regra, aberto)), `${d}/${aberto}`);
      }
    }
  }
});

// ===========================================================================
// 6. Periodo consultado: vem da RESPOSTA
// ===========================================================================
test("descreve o `window_hours` devolvido pela API", () => {
  assert.equal(descreverJanelaEmHoras(24), "últimos 1 dia (24 h)");
  assert.equal(descreverJanelaEmHoras(240), "últimos 10 dias (240 h)");
  assert.equal(descreverJanelaEmHoras(48), "últimos 2 dias (48 h)", "o default da API");
  assert.equal(descreverJanelaEmHoras(5), "últimas 5 h");
});

test("janela ausente na resposta nao vira zero", () => {
  for (const v of [null, undefined, 0, -1, Number.NaN]) {
    assert.equal(descreverJanelaEmHoras(v as number), null, String(v));
  }
});

test("as duas telas rotulam o periodo com o que a API devolveu", () => {
  assert.match(CLIENTE, /descreverJanelaEmHoras\(tendencia\.window_hours\)/);
  assert.match(CLIENTE, /tendencia\.from_hour && tendencia\.to_hour/);
  assert.match(TIKTOK, /formatarDiaLongo\(ldr\.from\)/);
  assert.match(TIKTOK, /formatarDiaLongo\(ldr\.to\)/);
  assert.match(TIKTOK, /\{ldr\.days\}/);
});

// ===========================================================================
// 7. Fiacao
// ===========================================================================
test("a fila so' converte para horas no helper", () => {
  assert.match(CLIENTE, /queryDaTendenciaEmDias\(janela, filtros\.channel\)/);
  assert.doesNotMatch(CLIENTE, /janela\s*\*\s*24|\*\s*24\b/, "nenhuma conversao solta na tela");
});

test("as duas superficies leem a janela da URL e acompanham voltar/avancar", () => {
  assert.match(CLIENTE, /lerDiasDaUrl\(window\.location\.search, REGRA\)/);
  assert.match(TIKTOK, /lerDiasDaUrl\(window\.location\.search, REGRA\)/);
  for (const f of [CLIENTE, TIKTOK]) {
    assert.match(f, /addEventListener\("popstate"/);
    assert.match(f, /removeEventListener\("popstate"/);
    assert.match(f, /urlComDias\(window\.location\.href/);
  }
});

test("o popstate da fila relê a janela mesmo sem a flag do ML", () => {
  const efeito = CLIENTE.slice(CLIENTE.indexOf("const aoVoltar = () => {"));
  const corpo = efeito.slice(0, efeito.indexOf("};"));
  assert.match(corpo, /lerJanelaDaUrl\(\)/);
  // Antes, os dois efeitos de URL saiam cedo sem a flag; a janela nao pode.
  assert.doesNotMatch(CLIENTE, /if \(!ML_LIGADO\) return;/);
});

test("a casca descarta a janela ao trocar de superficie", () => {
  assert.match(CASCA, /new URL\(urlSemDias\(window\.location\.href\)\)/);
});

test("a casca nao monta painel nenhum antes de ler a URL", () => {
  // Defeito medido no QA: a fila montava antes de a aba ser lida, julgava
  // `?dias=45` (link do TikTok) invalido pela regra dela e o apagava da URL.
  const corpo = CASCA.slice(CASCA.indexOf("function ComAbas"));
  const guarda = corpo.indexOf("if (!pronta) {");
  const fila = corpo.indexOf("<ExpedicaoClient");
  assert.ok(guarda > 0, "precisa existir a guarda de `pronta`");
  assert.ok(fila > guarda, "a fila so' pode aparecer depois da guarda");
  assert.doesNotMatch(corpo, /pronta && aba === ABA_TIKTOK/);
});

test("o TikTok descarta resposta de janela antiga", () => {
  assert.equal((TIKTOK.match(/urlVigente\.current !== url/g) ?? []).length, 2);
});

test("o campo e' texto numerico, nao type=number, e aplica so' no envio", () => {
  assert.match(CAMPO, /type="text"/);
  assert.match(CAMPO, /inputMode="numeric"/);
  assert.doesNotMatch(CAMPO, /type="number"/);
  assert.match(CAMPO, /onSubmit=\{enviar\}/);
  // A unica chamada ao pai acontece depois da validacao.
  assert.equal((CAMPO.match(/aoAplicar\(/g) ?? []).length, 1);
  assert.match(CAMPO, /if \(!r\.ok\) \{[\s\S]*?return;[\s\S]*?aoAplicar\(r\.dias\)/);
});

// ===========================================================================
// 8. Renderizacao do campo
// ===========================================================================
const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: JanelaPersonalizada } = await import(
  "../src/components/expedicao/JanelaPersonalizada.tsx"
);

test("o campo renderiza rotulo, dica de limite e botao, sem erro inicial", () => {
  const html = renderToStaticMarkup(
    h(JanelaPersonalizada, { max: 14, diasAplicados: 10, aoAplicar: () => {} }),
  );
  assert.match(html, /<form[^>]*aria-label="Janela personalizada"/);
  assert.match(html, /<label[^>]*for="([^"]+)"[^>]*>Últimos/);
  const idCampo = html.match(/<label[^>]*for="([^"]+)"/)![1];
  assert.match(html, new RegExp(`<input[^>]*id="${idCampo}"`), "rotulo aponta para o campo");
  assert.match(html, /value="10"/);
  assert.match(html, /inputMode="numeric"|inputmode="numeric"/);
  const descr = html.match(/aria-describedby="([^"]+)"/)![1];
  assert.match(html, new RegExp(`id="${descr}"[^>]*>Número inteiro de 1 a 14 dias`));
  assert.doesNotMatch(html, /aria-invalid/);
  assert.doesNotMatch(html, /role="alert"/);
  assert.match(html, /<button[^>]*type="submit"[^>]*>Aplicar<\/button>/);
});

test("o limite exibido acompanha a superficie", () => {
  const html = renderToStaticMarkup(
    h(JanelaPersonalizada, { max: 90, diasAplicados: 7, aoAplicar: () => {} }),
  );
  assert.match(html, /de 1 a 90 dias/);
});
