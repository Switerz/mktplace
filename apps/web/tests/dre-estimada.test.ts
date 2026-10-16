/**
 * DRE-UX-3 — visao "Conhecida x Estimada com premissas" (contrato do PR #79).
 *
 * Logica pura (`src/lib/dre-estimada.ts`, URL em `src/lib/dre.ts`) e
 * renderizacao real dos componentes (`react-dom/server` via
 * `helpers/tsx-loader.mjs`). Fixtures sinteticas em `dre-fixtures-estimada.ts`.
 */
import { register } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

register("./helpers/tsx-loader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const dre = await import("../src/lib/dre.ts");
const est = await import("../src/lib/dre-estimada.ts");
const fx = await import("./dre-fixtures.ts");
const fe = await import("./dre-fixtures-estimada.ts");
const comp = await import("../src/components/dre/DreEstimada.tsx");

const texto = (html: string) =>
  html
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/ /g, " ")
    .replace(/\s+/g, " ")
    .trim();
const render = (el: unknown) => renderToStaticMarkup(el as Parameters<typeof renderToStaticMarkup>[0]);
const R_ZERO = /R\$\s?0(?![\d.,])/;
const RAIZ = join(import.meta.dirname, "..");

const NC = fe.cenarioFx({ estado: "nao_calculavel", previa: true, cmv: null });
const PARCIAL = fe.cenarioFx({ estado: "parcial", previa: true, margem: 18_000 });
const CALC = fe.cenarioFx({ estado: "calculavel", margem: 20_000 });

// ===========================================================================
// URL: cenario e' visao, nao recorte
// ===========================================================================
test("cenario vai para a URL e NUNCA para a requisicao", () => {
  const r = dre.queryCanonica("cenario=estimada");
  assert.equal(r.filtros.cenario, "estimada");
  assert.equal(r.mudou, false);
  assert.equal(r.url, "cenario=estimada");
  assert.equal(r.query, "", "a API nao recebe cenario");
  assert.equal(new URL(dre.dreApiUrl("http://api", r.filtros)).search, "");

  const c = dre.queryCanonica("ref_month=2026-10&channels=tiktok%2Cml&cenario=estimada");
  assert.equal(c.mudou, false);
  assert.equal(c.query, "ref_month=2026-10&channels=tiktok%2Cml");
  assert.doesNotMatch(new URL(dre.dreApiUrl("http://api", c.filtros)).search, /cenario/);
});

test("cenario invalido ou fora de ordem e' reescrito; 'conhecida' e' o padrao e nao vai para a URL", () => {
  const casos: [string, string, "conhecida" | "estimada"][] = [
    ["cenario=foo", "", "conhecida"],
    ["cenario=conhecida", "", "conhecida"],
    ["cenario=estimada&ref_month=2026-10", "ref_month=2026-10&cenario=estimada", "estimada"],
    ["cenario=estimada&brands=xyz", "cenario=estimada", "estimada"],
  ];
  for (const [entrada, url, cen] of casos) {
    const r = dre.queryCanonica(entrada);
    assert.equal(r.mudou, true, entrada);
    assert.equal(r.url, url, entrada);
    assert.equal(r.filtros.cenario, cen, entrada);
    assert.equal(dre.queryCanonica(r.url).mudou, false, `${entrada}: estabiliza`);
  }
});

test("trocar o cenario preserva o recorte e a URL sobrevive ao reload", () => {
  const atual = dre.queryCanonica("ref_month=2026-10&brands=apice%2Ckokeshi").filtros;
  const nova = dre.dreUrlQuery({ ...atual, cenario: "estimada" });
  assert.equal(nova, "ref_month=2026-10&brands=apice%2Ckokeshi&cenario=estimada");
  // "reload": a mesma URL produz os mesmos filtros e a mesma requisicao
  const recarregada = dre.queryCanonica(nova);
  assert.equal(recarregada.mudou, false);
  assert.deepEqual(recarregada.filtros, { ...atual, cenario: "estimada" });
  assert.equal(recarregada.query, dre.dreQuery(atual), "trocar cenario nao muda a requisicao");
});

test("a pagina grava o cenario pela mesma funcao de URL e oferece o seletor acessivel", () => {
  const src = readFileSync(join(RAIZ, "app/dre/DreClient.tsx"), "utf8");
  assert.match(src, /aoEscolher=\{\(c\) => aplicar\(\{ cenario: c \}\)\}/);
  assert.match(src, /rotuladoPor="dre-cenario-rotulo"/);
  assert.match(src, /texto: "Estimada com premissas"/);
  // a requisicao continua dependendo so' da chave do recorte
  assert.match(src, /\}, \[chave, tentativa\]\);/);
});

// ===========================================================================
// Disponibilidade: API antiga x sem premissa x cenario
// ===========================================================================
test("API antiga, sem premissa e cenario presente sao estados distintos", () => {
  const antiga = fx.resposta();
  assert.equal(antiga.fontes.premissas_dre, undefined);
  assert.deepEqual(est.disponibilidade(antiga.total, antiga), { tipo: "api_antiga" });

  const semPremissa = fe.respostaEstimada({ estado: "parcial" }, "nao_publicado");
  const d = est.disponibilidade(semPremissa.total, semPremissa);
  assert.equal(d.tipo, "sem_premissa");
  assert.match((d as { motivo: string }).motivo, /nenhuma competencia do periodo tem premissa publicada/);

  const tabela = fe.respostaEstimada({ estado: "parcial" }, "tabela_ausente");
  assert.match((est.disponibilidade(tabela.total, tabela) as { motivo: string }).motivo, /migration 026/);

  const ok = fe.respostaEstimada({ estado: "calculavel" });
  assert.equal(est.disponibilidade(ok.total, ok).tipo, "ok");
});

test("resposta antiga (sem cenario) continua valida; cenario malformado e' payload invalido", () => {
  assert.equal(dre.problemaNaResposta(fx.resposta()), null, "contrato antigo segue aceito");
  assert.equal(dre.problemaNaResposta(fe.respostaEstimada({ estado: "parcial" })), null);
  assert.equal(dre.problemaNaResposta(fe.respostaEstimada({ estado: "parcial" }, "nao_publicado")), null);

  const estadoNovo = structuredClone(fe.respostaEstimada({ estado: "parcial" }));
  (estadoNovo.total!.cenario_premissas as { estado: string }).estado = "estimado";
  assert.equal(dre.problemaNaResposta(estadoNovo), "total.cenario_premissas.estado inválido");

  const valorTexto = structuredClone(fe.respostaEstimada({ estado: "parcial" }));
  (valorTexto.por_canal[0].cenario_premissas!.linhas[3] as { valor: unknown }).valor = "100";
  assert.equal(dre.problemaNaResposta(valorTexto), "por_canal[0].cenario_premissas.linhas[3].valor inválido");

  const resumoRuim = structuredClone(fe.respostaEstimada({ estado: "parcial" }));
  (resumoRuim.fontes.premissas_dre as { estado: string }).estado = "talvez";
  assert.equal(dre.problemaNaResposta(resumoRuim), "fontes.premissas_dre inválido");
});

// ===========================================================================
// Linhas: CMV ausente, nao somaveis, ausencia nunca zero
// ===========================================================================
test("CMV ausente: 'CMV da competência ainda não publicado', nunca R$ 0; margem estimada nao calculavel", () => {
  const { principais } = est.linhasEstimadas(NC);
  const cmv = principais.find((x) => x.l.linha === "cmv")!;
  assert.equal(cmv.l.valor, null);
  assert.equal(cmv.ausencia, est.TEXTO_CMV_NAO_PUBLICADO);
  assert.equal(est.TEXTO_CMV_NAO_PUBLICADO, "CMV da competência ainda não publicado");
  assert.equal(est.textoValorLinha(cmv), null);
  assert.equal(est.textoMargemEstimada(NC), "Não calculável");
  assert.equal(est.textoMargemEstimadaPct(NC), "Não calculável");
});

test("Comissoes e Outras Receitas: visiveis com taxa e valor, fora da soma, sem sinal de subtracao", () => {
  const { principais, informativas } = est.linhasEstimadas(CALC);
  const com = principais.find((x) => x.l.linha === "comissoes")!;
  assert.equal(com.foraDaSoma, true);
  assert.equal(com.naSoma, false);
  assert.equal(est.textoValorLinha(com), dre.fmtReais(12_500), "sem '−'");
  assert.equal(est.fmtTaxaPremissa(com.l.percentual), "12,50%");
  const outras = informativas.find((x) => x.l.linha === "outras_receitas_operacionais")!;
  assert.equal(outras.foraDaSoma, true);
  assert.equal(outras.chip?.rotulo, "Sinal não ratificado");
  assert.equal(com.chip?.rotulo, "Sobreposição não ratificada");
  // itens somaveis continuam com sinal de custo
  const icms = principais.find((x) => x.l.linha === "icms")!;
  assert.equal(icms.naSoma, true);
  assert.equal(icms.recuo, true);
  assert.match(est.textoValorLinha(icms)!, /^− R\$/);
});

test("premissa ausente na matriz nao vira zero; 0% explicito continua 0%", () => {
  const { principais } = est.linhasEstimadas(CALC);
  const gw = principais.find((x) => x.l.linha === "despesas_gateway")!;
  assert.equal(gw.l.valor, null);
  assert.match(gw.ausencia!, /^Ausente na matriz/);
  const iss = principais.find((x) => x.l.linha === "iss_ipi_simples")!;
  assert.equal(est.fmtTaxaPremissa(iss.l.percentual), "0,00%");
  assert.equal(est.textoValorLinha(iss), dre.fmtReais(0), "zero explicito da matriz e' zero");
});

test("status desconhecido aparece por extenso, nunca escondido", () => {
  assert.deepEqual(est.chipDaLinha("status_novo_do_backend"), { rotulo: "status_novo_do_backend", tom: "neutro" });
  assert.equal(est.chipDaLinha("ok"), null);
  assert.equal(est.chipDaLinha("disponivel"), null);
  assert.equal(est.chipDaLinha("snapshot_ausente")?.rotulo, "Insumo não publicado");
});

test("premissas por marca vem das celulas base (so' elas tem taxa); agregado nao tem percentual", () => {
  const r = fe.respostaEstimada({ estado: "parcial" });
  const { marcas, componentes, fonte } = est.premissasPorMarca(r);
  assert.deepEqual(marcas.map((m) => m.marca), ["apice", "kokeshi"], "uma linha por marca, sem repetir canal");
  assert.equal(componentes.length, 18);
  assert.equal(marcas[0].premissas.icms.taxa, 0.1);
  assert.equal(marcas[0].premissas.despesas_gateway.taxa, null);
  assert.equal(marcas[0].premissas.comissoes.somavel, false);
  assert.equal(fonte?.rotulo, "Premissas de outubro/2026");
  assert.equal(r.total!.cenario_premissas!.linhas.find((l) => l.linha === "icms")!.percentual, null);
  assert.equal(est.hashCurto("a".repeat(64)), `${"a".repeat(12)}…`);
});

// ===========================================================================
// Renderizacao
// ===========================================================================
test("resumo estimado nao calculavel (CMV ausente): status e motivo, sem R$ 0, sem margem conhecida", () => {
  const t = texto(render(h(comp.ResumoEstimado, { cenario: NC })));
  assert.match(t, /Margem de contribuição estimada com premissas/);
  assert.match(t, /Não calculável/);
  assert.match(t, /cmv snapshot_ausente/);
  assert.match(t, /Prévia/);
  assert.match(t, /Premissas de outubro\/2026/);
  assert.doesNotMatch(t, R_ZERO);
  assert.doesNotMatch(t, /Margem de contribuição conhecida/, "nao mistura as duas margens no resumo estimado");
});

test("resumo estimado parcial e calculavel mostram o numero com o estado ao lado", () => {
  const p = texto(render(h(comp.ResumoEstimado, { cenario: PARCIAL })));
  assert.match(p, /R\$\s?18\.000/);
  assert.match(p, /Parcial/);
  assert.match(p, /Prévia/);
  const c = texto(render(h(comp.ResumoEstimado, { cenario: CALC })));
  assert.match(c, /R\$\s?20\.000/);
  assert.match(c, /Calculável/);
  assert.doesNotMatch(c, /Prévia/);
});

test("comparacao: conhecida e estimada lado a lado, rotuladas, mesma receita base, nunca somadas", () => {
  const celula = { ...fx.ML, cenario_premissas: CALC };
  const t = texto(render(h(comp.ComparacaoCenarios, { celula, cenario: CALC })));
  assert.match(t, /Margem de contribuição conhecida R\$\s?42\.000/);
  assert.match(t, /Margem de contribuição estimada com premissas R\$\s?20\.000/);
  assert.doesNotMatch(t, /62\.000/, "nenhuma soma das duas margens");
  assert.match(t, /Receita base das duas: R\$\s?100\.000/);
  // conhecida nao calculavel continua dizendo isso, mesmo com estimada calculavel
  const t2 = texto(render(h(comp.ComparacaoCenarios, { celula: { ...fx.SHOPEE, cenario_premissas: CALC }, cenario: CALC })));
  assert.match(t2, /Margem de contribuição conhecida Não calculável/);
});

test("DRE estimada: origem, taxa, CMV nao publicado, nao somaveis fora da soma, informativas separadas", () => {
  const r = fe.respostaEstimada({ estado: "nao_calculavel", previa: true, cmv: null });
  const opcoes = [{ valor: "canal:ml", rotulo: "Canal: Mercado Livre", celula: { ...fx.ML, cenario_premissas: fe.cenarioFx({ estado: "nao_calculavel", previa: true, cmv: null }) } }];
  const html = render(
    h(comp.DreVerticalEstimada, { opcoes, selecionado: "canal:ml", aoEscolher: () => {}, dispDe: (c: typeof fx.ML) => est.disponibilidade(c, r) }),
  );
  const t = texto(html);
  assert.match(t, /CMV da competência ainda não publicado/);
  assert.match(t, /Premissa/);
  assert.match(t, /12,50%/, "taxa da celula base");
  assert.match(t, /\(-\) Comissões.*fora da soma/);
  assert.match(t, /Fora da soma \(informativo\)/);
  assert.match(t, /Outras Receitas Operacionais/);
  assert.match(t, /Frete realizado \(comparação\)/);
  assert.match(t, /Ausente na matriz/);
  assert.match(t, /Margem de contribuição estimada com premissas.*Não calculável/);
  // O unico R$ 0 legitimo e' o ISS/IPI/Simples: 0% EXPLICITO na matriz (zero medido).
  assert.match(t, /ISS\/IPI\/Simples[^(]*?0,00%[^(]*?R\$\s?0 /);
  assert.doesNotMatch(t.replace(/ISS\/IPI\/Simples[^(]*?R\$\s?0 /, ""), R_ZERO, "nenhuma ausencia vira R$ 0");
  assert.match(t, /Sem valor: Custo das Mercadorias, Gateway/, "ausentes pelo rotulo, nao pelo id");
  assert.match(html, /aria-label="Linhas informativas, fora da soma"/);
});

test("DRE estimada sem cenario: API antiga e competencia sem premissa tem mensagens proprias", () => {
  const antiga = fx.resposta();
  const opc = [{ valor: "total", rotulo: "Total da seleção", celula: antiga.total! }];
  const t1 = texto(render(h(comp.DreVerticalEstimada, { opcoes: opc, selecionado: "total", aoEscolher: () => {}, dispDe: (c: typeof fx.ML) => est.disponibilidade(c, antiga) })));
  assert.match(t1, /A API publicada ainda não fornece o cenário estimado/);
  const sem = fe.respostaEstimada({ estado: "parcial" }, "nao_publicado");
  const opc2 = [{ valor: "total", rotulo: "Total da seleção", celula: sem.total! }];
  const t2 = texto(render(h(comp.DreVerticalEstimada, { opcoes: opc2, selecionado: "total", aoEscolher: () => {}, dispDe: (c: typeof fx.ML) => est.disponibilidade(c, sem) })));
  assert.match(t2, /Sem premissas para esta competência/);
  assert.match(t2, /nenhuma competencia do periodo tem premissa publicada/);
});

test("premissas por marca: taxa por marca, ausencia por escrito, nao somaveis marcados, hash no detalhe", () => {
  const html = render(h(comp.PremissasPorMarca, { resp: fe.respostaEstimada({ estado: "parcial" }) }));
  const t = texto(html);
  assert.match(t, /APICE.*KOKESHI/);
  assert.match(t, /ICMS 10,00% 10,00%/);
  assert.match(t, /Gateway Ausente na matriz Ausente na matriz/);
  assert.match(t, /Comissões fora da soma 12,50%/);
  assert.match(t, /Detalhes da fonte/);
  assert.doesNotMatch(t, new RegExp("a".repeat(64)), "hash inteiro so' dentro do detalhe (fechado)");
  assert.match(t, /Premissas de outubro\/2026/);
});

test("estado por recorte: cada recorte com o proprio estado e motivos expansiveis", () => {
  const r = fe.respostaEstimada({ estado: "parcial", previa: true });
  const linhas = [
    { rotulo: "Total da seleção", celula: r.total! },
    ...r.por_canal.map((c) => ({ rotulo: c.canal_label ?? "", celula: c })),
  ];
  const html = render(h(comp.EstimadaPorRecorte, { linhas, dispDe: (c: typeof fx.ML) => est.disponibilidade(c, r) }));
  const t = texto(html);
  assert.equal((t.match(/Parcial/g) ?? []).length, 4);
  assert.match(html, /aria-expanded="false"/, "motivos expansiveis");
  assert.doesNotMatch(t, R_ZERO);
});

test("a visao conhecida nao le o cenario estimado (componentes oficiais so')", () => {
  for (const rel of [
    "src/components/dre/DreResumo.tsx",
    "src/components/dre/DreCanais.tsx",
    "src/components/dre/DreVertical.tsx",
    "src/components/dre/DreMarcas.tsx",
  ]) {
    assert.doesNotMatch(readFileSync(join(RAIZ, rel), "utf8"), /cenario_premissas|dre-estimada/, rel);
  }
  // e a estimada nunca usa a margem conhecida fora do painel de comparacao rotulado
  const src = readFileSync(join(RAIZ, "src/components/dre/DreEstimada.tsx"), "utf8");
  const usos = src.split("\n").filter((l) => /textoMargem\(|margem_contribuicao_conhecida/.test(l));
  assert.ok(usos.length >= 1 && usos.length <= 3, `uso restrito a comparacao: ${usos.length}`);
});
