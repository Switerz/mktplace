// Testes de wiring do Gate V3-1A — contratos de JSX que o harness atual nao
// consegue renderizar (node:test sem DOM). Sao estaticos de proposito, e
// cobrem exatamente as regras que a revisao do V3-0 exigiu e que a pagina
// anterior violava.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p: string) => readFileSync(join(root, p), "utf8");

/**
 * Codigo sem comentarios — mesmo helper de `v22-propagacao-visual.test.ts`.
 *
 * Necessario porque os arquivos DOCUMENTAM as proibicoes ("BE6
 * (`opportunity_map`) nao existe, entao NAO ha matriz..."), e uma verificacao
 * sobre o texto bruto casaria com a explicacao em vez de com uma violacao.
 */
function codeOnly(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .map((line) => {
      const t = line.trim();
      if (t.startsWith("//") || t.startsWith("*")) return "";
      return line.replace(/\/\/.*$/, "");
    })
    .join("\n");
}

const INTEL = "app/inteligencia/page.tsx";
const PRODUTOS = "app/produtos/page.tsx";

/** Arquivos visuais ESCRITOS nesta task — alvo do piso tipografico estrito. */
const AUTHORED_FILES = [
  INTEL,
  "src/components/inteligencia/PriorityCards.tsx",
  "src/components/inteligencia/ConcentrationBars.tsx",
  "src/components/inteligencia/EvidenceQueue.tsx",
  "src/components/drilldown/DrilldownMetricPair.tsx",
];

// ---------------------------------------------------------------------------
// 24. reset de paginacao ao mudar filtro
// ---------------------------------------------------------------------------

test("24. mudanca de marca/bucket reseta a paginacao de cada aba", () => {
  const src = read(PRODUTOS);
  // os tres efeitos de reset preexistentes continuam cobrindo marca e bucket,
  // e a URL alimenta exatamente esse estado — nao ha caminho que mude o
  // filtro sem passar pelo reset
  assert.match(src, /setMlOffset\(0\);?\s*\}, \[brand, mlBucket/);
  assert.match(src, /setTkOffset\(0\)/);
  assert.match(src, /setShOffset\(0\)/);
});

test("24b. o estado inicial vem da URL e a URL e' sincronizada depois", () => {
  const src = read(PRODUTOS);
  assert.match(src, /parseProdutosUrl\(searchParams\)/, "parsing centralizado");
  assert.match(src, /useState<Tab>\(fromUrl\.tab \?\? "ml"\)/);
  assert.match(src, /useState\(fromUrl\.brand \?\? ""\)/);
  assert.match(src, /buildProdutosQuery\(/, "construcao centralizada");
  assert.match(src, /router\.replace\(`\/produtos\?\$\{next\}`, \{ scroll: false \}\)/,
    "sincroniza sem navegar e sem empilhar historico");
});

test("24c. nenhuma leitura ad hoc de searchParams fora do modulo allowlisted", () => {
  for (const f of [INTEL, PRODUTOS]) {
    const src = read(f);
    // `searchParams.get(...)` direto e' o que o contrato proibe: tudo passa
    // por `parseProdutosUrl`/`parseLens`/`readSingleParam`
    assert.equal(
      (src.match(/searchParams\.get\(/g) ?? []).length, 0,
      `${f} le searchParams diretamente`,
    );
  }
});

// ---------------------------------------------------------------------------
// 26. retorno frio: nenhum ctx_*
// ---------------------------------------------------------------------------

test("26. nenhum ctx_* e' produzido pela Inteligencia (contexto quente e' do V3-2)", () => {
  const src = read(INTEL);
  assert.equal((src.match(/ctx_from|ctx_signal|ctx_focus|ctx_brand|ctx_channel/g) ?? []).length, 0);
  assert.equal((src.match(/buildArrivalParams/g) ?? []).length, 0);
  // os destinos de marca sao frios: so' `brands=`
  assert.match(src, /\/brand\/\$\{[a-zA-Z.]+\}\?brands=/);
});

// ---------------------------------------------------------------------------
// 27. nenhuma matriz antes de BE6
// ---------------------------------------------------------------------------

// As tres verificacoes abaixo olham para IMPLEMENTACAO, nao para prosa.
//
// A primeira versao delas proibia as PALAVRAS "quadrante", "mediana" e
// "total do portfolio" no arquivo — e falhava justamente porque a pagina
// EXPLICA ao leitor que nao ha quadrante, nao ha mediana de subconjunto e que
// a contagem nao e' o total do portfolio. Proibir a palavra proibiria a
// explicacao. O que precisa nao existir e' o codigo.
test("27. a matriz do V3-1B le o contrato BE6 e nao deriva nada no cliente", () => {
  // Este teste era o congelamento do estado PRE-BE6: proibia matriz, quadrante,
  // eixo e scatter porque o payload nao os sustentava. BE6 chegou e o Gate V3-1B
  // implementou a matriz, entao o congelamento virou o seu oposto. O que
  // continua proibido e DERIVAR no cliente aquilo que e contrato.
  const src = codeOnly(read(INTEL));
  // a pagina consome o contrato e delega o desenho ao componente proprio
  assert.match(src, /opportunity_map/, "a matriz consome o contrato BE6");
  assert.match(src, /OpportunityMatrix/, "e delega o desenho ao componente proprio");
  // nada de biblioteca de grafico: o plano cartesiano e SVG autoral
  for (const p of [/ScatterChart|<Scatter\b/, /XAxis|YAxis|ZAxis|CartesianGrid/,
                   /from "recharts"/]) {
    assert.ok(!p.test(src), `${p} nao pode aparecer nesta pagina`);
  }
  // nenhuma referencia e recalculada aqui: elas sao LIDAS do mapa
  assert.ok(!/gmv_reference\s*=(?!=)/.test(src), "gmv_reference nao pode ser atribuida na pagina");
  assert.ok(!/roas_reference\s*=(?!=)/.test(src), "roas_reference nao pode ser atribuida na pagina");
  assert.ok(!/ad_roas\s*>=\s*\d/.test(src), "a pagina nao reclassifica por comparacao numerica");
});

test("27b. nenhuma mediana de subconjunto e' CALCULADA", () => {
  const src = read(INTEL);
  const libs = ["src/lib/inteligencia/queue.ts", "src/lib/inteligencia/priorities.ts", "src/lib/inteligencia/pareto.ts"];
  for (const f of [INTEL, ...libs]) {
    const s = read(f);
    assert.ok(!/function\s+\w*[Mm]edian|const\s+\w*[Mm]edian\s*=|\.sort\([^)]*\)\[Math\.floor/.test(s),
      `${f}: mediana calculada sem BE6 seria cobertura inventada`);
  }
});

test("27c. nenhuma contagem apresentada como total do universo", () => {
  // Frases que afirmariam cobertura. `de 30 dias` (janela do TikTok) e as
  // proprias negacoes ("nao e' o total do portfolio") sao legitimas e ficam
  // fora do padrao.
  const proibido = /\b\d+\s+produtos no total|\btodo o portf[oó]lio\b(?![^.]*n[aã]o)|\b\d+ de \d+ produtos\b/i;
  for (const f of [INTEL, "src/components/inteligencia/PriorityCards.tsx"]) {
    const src = read(f);
    assert.ok(!proibido.test(src), `${f} sugere total do universo`);
  }
  assert.match(read(INTEL), /registros recebidos|registros exibidos/);
});

// ---------------------------------------------------------------------------
// 28-29. falsas affordances removidas
// ---------------------------------------------------------------------------

test("28. os <span> que imitavam botao de acao nao existem mais", () => {
  const src = read(INTEL);
  // a pagina anterior tinha: <span className="... bg-rose-600 text-white ...">Pausar Ads</span>
  for (const rotulo of ["Pausar Ads", "Aumentar Budget", "Testar Ads"]) {
    assert.ok(!src.includes(rotulo), `rotulo de falso botao ainda presente: ${rotulo}`);
  }
  // nenhum span com preenchimento solido de botao
  assert.ok(!/<span[^>]*bg-(rose|emerald|amber)-600 text-white/.test(src),
    "span com aparencia de botao solido");
});

test("29. nenhuma linha de tabela com hover enganoso e nenhuma linha clicavel", () => {
  for (const f of [INTEL, "src/components/inteligencia/EvidenceQueue.tsx"]) {
    const src = read(f);
    assert.ok(!/<tr[^>]*hover:bg-/.test(src), `${f}: <tr> com hover sugere clique que nao existe`);
    assert.ok(!/<tr[^>]*onClick/.test(src), `${f}: linha inteira clicavel`);
  }
});

test("29b. todo acionamento e' button/link com nome acessivel", () => {
  const q = read("src/components/inteligencia/EvidenceQueue.tsx");
  assert.match(q, /aria-label=\{`Detalhe de \$\{/, "o botao identifica o produto");
  assert.match(q, /min-h-11/, "alvo de toque >= 44px");
  const c = read("src/components/inteligencia/ConcentrationBars.tsx");
  assert.match(c, /<button/);
  assert.match(c, /aria-label=\{`Detalhe do bucket/);
  const p = read("src/components/inteligencia/PriorityCards.tsx");
  assert.match(p, /<button/);
  assert.match(p, /aria-label=\{`Abrir detalhe da prioridade/);
  assert.match(p, /min-h-11/);
});

// ---------------------------------------------------------------------------
// 30. piso tipografico
// ---------------------------------------------------------------------------

test("30. piso de 12px nos arquivos escritos nesta task", () => {
  // Os cinco arquivos autorais do V3-1A: piso ESTRITO.
  for (const f of AUTHORED_FILES) {
    const src = read(f);
    const achados = src.match(/text-\[(?:[0-9]|10|11)px\]/g) ?? [];
    assert.deepEqual(achados, [], `${f} tem tipografia abaixo de 12px: ${achados.join(", ")}`);
  }
});

test("30b. /produtos: nenhuma tipografia sub-12px ACRESCENTADA por esta task", () => {
  // A pagina foi tocada apenas para o wiring de URL; seu redesenho visual nao
  // esta no escopo do V3-1A. As tres ocorrencias existentes sao anteriores e
  // ficam registradas como divida, mas o numero nao pode crescer.
  const achados = (read(PRODUTOS).match(/text-\[(?:[0-9]|10|11)px\]/g) ?? []).length;
  assert.equal(achados, 3, "o wiring de URL nao pode introduzir tipografia sub-12px");
});

// ---------------------------------------------------------------------------
// 31. um unico dialogo
// ---------------------------------------------------------------------------

test("31. existe UM KpiDrilldownDialog na pagina, e nenhum dialogo proprio", () => {
  const src = read(INTEL);
  assert.equal((src.match(/<KpiDrilldownDialog/g) ?? []).length, 1);
  assert.equal((src.match(/import KpiDrilldownDialog/g) ?? []).length, 1);
  // nenhum modal caseiro
  assert.ok(!/fixed inset-0.*z-\[?[5-9]\d/.test(src), "modal proprio fora do shell canonico");
  assert.ok(!/role="dialog"/.test(src), "o role vem do shell, nao da pagina");
});

test("31b. o detalhe usa as primitives do G2, incluindo a que faltava", () => {
  const src = read(INTEL);
  for (const p of ["DrilldownContextLine", "DrilldownMetricPair", "EvidenceRow", "DataQualityNote", "DrilldownCta"]) {
    assert.ok(src.includes(p), `primitive ausente: ${p}`);
  }
  // o dialogo entrega diagnostico, evidencia, limitacao e proximo passo
  assert.match(src, /DataQualityNote\s+note=/);
  assert.match(src, /<DrilldownCta/);
});

test("31c. o dialogo do bucket NAO lista produtos e diz por que", () => {
  const src = read(INTEL);
  assert.match(src, /ele n.o cont.m a lista de produtos/i);
  assert.ok(!/dialog\.share\.products|share\.items|bucketProducts/.test(src));
});

// ---------------------------------------------------------------------------
// 32. nenhuma dependencia ou fetch novo
// ---------------------------------------------------------------------------

test("32. nenhuma dependencia nova no package.json", () => {
  const pkg = JSON.parse(read("package.json")) as {
    dependencies: Record<string, string>; devDependencies: Record<string, string>;
  };
  const deps = Object.keys(pkg.dependencies ?? {}).sort();
  // A guarda continua sendo uma allowlist EXATA: qualquer pacote fora desta
  // lista faz o teste falhar. O V3-1A nao introduziu nenhuma dependencia de
  // runtime — as adicoes abaixo vieram de gates declarados, posteriores a ele:
  //   OM1  @modelcontextprotocol/server  SDK oficial do MCP (runtime da rota)
  //   OM1  zod                           validacao de input/output das tools
  //   OM2  jose                          verificacao de JWT/JWKS do Auth0
  // Todas sao de runtime por necessidade: a rota e' server-side e o build da
  // Vercel precisa delas em `dependencies`, nao em `devDependencies`.
  assert.deepEqual(
    deps,
    ["@modelcontextprotocol/server", "jose", "next", "react", "react-dom", "recharts", "zod"],
    "o conjunto de dependencias de runtime so muda por gate declarado (V3-1A: nenhuma; OM1: server+zod; OM2: jose)",
  );
  assert.ok(!("xlsx" in (pkg.dependencies ?? {})), "xlsx foi removido no U4 e nao volta");
});

test("32b. nenhum fetch novo: a Inteligencia continua com uma unica chamada", () => {
  const src = read(INTEL);
  assert.equal((src.match(/fetchInteligencia\(/g) ?? []).length, 1);
  assert.equal((src.match(/\bfetch\(/g) ?? []).length, 0, "nenhum fetch cru novo");
  assert.ok(!/useSWR|axios/.test(src), "nenhuma biblioteca de dados nova");
});

test("32c. a Inteligencia nao passou a herdar filtros globais", () => {
  const src = read(INTEL);
  assert.ok(!/useGlobalFilters/.test(src), "a tela nao herda o contrato de filtros globais (U5)");
  assert.match(src, /n[aã]o\s+respondem ao per[ií]odo global/i, "e diz isso ao leitor");
});

// ---------------------------------------------------------------------------
// regimes e honestidade temporal
// ---------------------------------------------------------------------------

test("regimes: ML sem janela e TikTok com 30 dias, nunca misturados", () => {
  const src = read(INTEL);
  assert.match(src, /Mercado Livre · fotografia do .ltimo carregamento/);
  assert.match(src, /TikTok Shop · .ltimos 30 dias/);
  // nenhum timestamp inventado
  // BE4 existe: o proibido deixou de ser "qualquer valor" e passou a ser
  // "valor inventado". O unico valor aceito vem do contrato, e ele so chega
  // quando a resposta e fresca (`displayData`, nao `data`).
  assert.ok(!/refreshedAt=\{new Date/.test(src), "nunca o relogio do navegador");
  assert.ok(!/refreshedAt=\{Date\./.test(src), "nunca Date.now()");
  assert.match(src, /const mlRefreshedAt = displayData\?\.ml_snapshot_refreshed_at \?\? null;/,
    "o timestamp sai de displayData, protegido por frescor de requisicao");
  // Gate V3-1B: os tres dialogos novos do bloco 3 tambem passam `null`, entao a
  // contagem subiu de 3 para 6. A contraprova que importa e a de cima: nenhum
  // `refreshedAt` recebe valor diferente de `null` nesta pagina. O frescor do
  // BE4 e exibido como TEXTO proprio do bloco ML, nao injetado nos dialogos.
  // Gate V3-1B Task 2/2 (FINDING 1): os tres dialogos do bloco 3 passaram a
  // receber o frescor REAL de `ml_snapshot_refreshed_at`, protegido por frescor
  // de requisicao. Congelar em `null` valia antes do BE4; agora protegeria a
  // AUSENCIA em vez do contrato. Os 3 que sobram sao blocos anteriores ao V3-1B.
  assert.equal((src.match(/refreshedAt=\{null\}/g) ?? []).length, 3);
  assert.equal((src.match(/refreshedAt=\{mlRefreshedAt\}/g) ?? []).length, 3,
    "os tres dialogos da matriz recebem o frescor do contrato");
});

test("estado: resolvedKey marcado tambem na falha (protecao do U4 preservada)", () => {
  const src = read(INTEL);
  assert.match(src, /computeRequestStatus\(/);
  // marcado nos dois caminhos: sucesso e catch
  assert.equal((src.match(/setResolvedKey\(key\)/g) ?? []).length, 2);
  assert.match(src, /displayData = status\.fresh \? data : null/);
});

test("estado: troca de recorte fecha dialogo incompativel", () => {
  const src = read(INTEL);
  assert.match(src, /useEffect\(\(\) => \{ setDialog\(null\); \}, \[brandSel, lens\]\)/);
});

test("marcas: nenhuma constante ML_BRANDS hardcoded na pagina", () => {
  const src = read(INTEL);
  assert.ok(!/const ML_BRANDS\s*=/.test(src), "a lista de marcas vem do payload");
  assert.match(src, /mlBrandsFromPayload\(/);
});

// ---------------------------------------------------------------------------
// Correcao consolidada pre-QA
// ---------------------------------------------------------------------------

test("33. precedencia loading -> error -> indisponivel -> fresh", () => {
  const src = read(INTEL);
  const iLoading = src.indexOf("{status.loading ? (");
  const iError = src.indexOf("status.error ? (");
  const iUnavail = src.indexOf("status.fresh && !hasData ? (");
  assert.ok(iLoading !== -1, "ramo explicito de loading precisa existir");
  assert.ok(iLoading < iError, "loading vem antes de error");
  assert.ok(iError < iUnavail, "error vem antes da indisponibilidade real");
  assert.match(src, /<InteligenciaSkeleton \/>/);
});

test("33b. o ramo de loading nao contem NENHUM texto de vazio", () => {
  const src = read(INTEL);
  const ini = src.indexOf("function InteligenciaSkeleton()");
  const fim = src.indexOf("type DialogState =");
  const skeleton = src.slice(ini, fim);
  for (const frase of [
    "Nenhuma prioridade", "Nenhum produto", "Sem dados", "indisponíveis",
    "não é modo demonstração", "Tentar novamente",
  ]) {
    assert.ok(!skeleton.includes(frase), `o skeleton nao pode dizer "${frase}"`);
  }
  // nem valor, nem contagem, nem controle acionavel
  assert.ok(!/fmtBrl|fmtNumber|tabular-nums/.test(skeleton), "nenhum valor no skeleton");
  assert.ok(!/<button|<Link|onClick/.test(skeleton), "nenhum controle acionavel no skeleton");
  assert.match(skeleton, /role="status"/);
  assert.match(skeleton, /aria-busy="true"/);
});

test("33c. controles ficam desabilitados durante loading", () => {
  const src = read(INTEL);
  assert.ok((src.match(/disabled=\{status\.loading\}/g) ?? []).length >= 4,
    "chips e cartoes precisam ficar inertes enquanto carrega");
});

test("34. as duas listas ML abrem o dialogo unico e tem CTA para a lente certa", () => {
  const src = read(INTEL);
  const ini = src.indexOf("Bloco 5 — produtos e mídia");
  const fim = src.indexOf("Bloco 6 — fila de evidências");
  const bloco = src.slice(ini, fim);
  // detalhe por produto, no shell unico
  assert.match(bloco, /setDialog\(\{ kind: "evidence", item: r \}\)/);
  assert.match(bloco, /aria-label=\{`Detalhe de \$\{r\.title/);
  assert.match(bloco, /min-h-11/);
  // CTA para a fila com a lente do painel e a ancora
  assert.match(bloco, /buildLensHref\("\/inteligencia", panel\.kind, searchParams, \{ anchor: INTELIGENCIA_ANCHORS\.fila \}\)/);
  assert.match(bloco, /Ver todos na fila/);
  // nenhum modal proprio, nenhum ctx_*
  assert.ok(!/fixed inset-0/.test(bloco));
  assert.ok(!/ctx_/.test(bloco));
  // truncamento honesto vem do helper, nao de string solta
  assert.match(bloco, /listSampleNote\(panel\.kind, panel\.rows\.length, received\)/);
  assert.ok(!/Até 5 de \$\{counts/.test(bloco), "a redacao antiga sugeria total");
});

test("34b. o card TikTok declara o teto proprio e nao inventa ROAS/Ads", () => {
  const src = read(INTEL);
  const ini = src.indexOf("Produtos TikTok em destaque");
  const fim = src.indexOf("Bloco 6 — fila de evidências");
  const bloco = src.slice(ini, fim);
  assert.match(bloco, /tkSampleNote\(tkRows\.length, tkReceived\)/);
  assert.match(bloco, /TK_PRODUCTS_LIMIT/);
  assert.match(bloco, /não traz investimento de mídia/);
  assert.ok(!/setDialog/.test(bloco), "sem drill-down sem evidencia");
  assert.match(bloco, /apresentação, não cobertura/);
});

test("35. a coluna de acao do LTV tem cabecalho proprio, nao 'Marca' duplicado", () => {
  const src = read(INTEL);
  const ini = src.indexOf("LTV e fidelização");
  const fim = src.indexOf("Próximos destinos");
  const bloco = src.slice(ini, fim);
  const marcaHeaders = (bloco.match(/>Marca</g) ?? []).length;
  assert.equal(marcaHeaders, 0, "nao pode haver um segundo <th> 'Marca'");
  assert.match(bloco, /<th scope="col"[^>]*>Detalhe<\/th>/);
  assert.match(bloco, /label="Marca" column="brand"/, "a primeira coluna continua sendo a marca");
});

test("36. a traducao brands -> brand esta centralizada de fato", () => {
  const src = read(PRODUTOS);
  assert.equal((src.match(/brandParamForEndpoint\(brand\)/g) ?? []).length, 6,
    "os seis pontos de request usam o helper");
  assert.equal((src.match(/brand: brand \|\| undefined/g) ?? []).length, 0,
    "nenhuma traducao inline sobrou");
});
