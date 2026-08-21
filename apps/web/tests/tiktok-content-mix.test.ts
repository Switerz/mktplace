// UE-F1B — contrato do mix do GMV de conteudo do TikTok no frontend.
//
// O que estes testes travam:
//
// 1. Os tres percentuais se fecham sobre a base de conteudo
//    (video+live+card), NUNCA sobre o GMV comercial.
// 2. A divergencia e' diagnostico de reconciliacao, com null/zero distintos.
// 3. Nao existe categoria residual "Outros" — nem na logica, nem na barra.
// 4. Dominancia nao rotula "Video" quando nao ha base valida.
// 5. O fallback de demonstracao produz o MESMO contrato do backend.
//
// Referencia: docs/UNIT_ECONOMICS_ATTRIBUTION_AUDIT.md secao 11 e o backend do
// Gate UE-F1A (commit 417be72).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  computeContentMix,
  contentMixFromApi,
  contentMixWeights,
  dominantContentOrigin,
  formatContentPctBr,
  formatDivergenceBr,
  DIVERGENCE_UNAVAILABLE_LABEL,
  MIX_UNAVAILABLE_LABEL,
  PCT_UNAVAILABLE_LABEL,
  UNAVAILABLE_MIX,
} from "../src/lib/tiktok-content-mix.ts";

const PAGE = readFileSync(new URL("../app/canais/page.tsx", import.meta.url), "utf8");
const API_CLIENT = readFileSync(new URL("../src/lib/api-client.ts", import.meta.url), "utf8");
const MODULO = readFileSync(new URL("../src/lib/tiktok-content-mix.ts", import.meta.url), "utf8");

// ---------------------------------------------------------------------------
// 1. Caso canonico: base maior que o GMV comercial
// ---------------------------------------------------------------------------

test("base 110 sobre GMV comercial 100: mix sobre a base e divergencia +10%", () => {
  const mix = computeContentMix(60, 30, 20, 100);
  assert.equal(mix.base, 110);
  assert.equal(mix.videoPct, 54.5); // 60/110
  assert.equal(mix.livePct, 27.3); // 30/110
  assert.equal(mix.cardPct, 18.2); // 20/110
  assert.equal(mix.divergencePct, 10);

  // o denominador antigo daria 60/30/20 e somaria 110% — nunca mais
  assert.notEqual(mix.videoPct, 60);
  const soma = mix.videoPct! + mix.livePct! + mix.cardPct!;
  assert.ok(soma <= 100.1, `soma ${soma} nao pode exceder 100,1%`);
});

// ---------------------------------------------------------------------------
// 2. Invariancia ao GMV comercial
// ---------------------------------------------------------------------------

test("mesmos componentes com GMV comercial diferente: mix igual, so a divergencia muda", () => {
  const a = computeContentMix(60, 30, 20, 100);
  const b = computeContentMix(60, 30, 20, 99_999);

  assert.equal(a.base, b.base);
  assert.equal(a.videoPct, b.videoPct);
  assert.equal(a.livePct, b.livePct);
  assert.equal(a.cardPct, b.cardPct);
  // so o diagnostico reage ao GMV comercial — e' esse o papel dele
  assert.notEqual(a.divergencePct, b.divergencePct);
  assert.ok(b.divergencePct! < 0, "base menor que o GMV comercial -> divergencia negativa");
});

test("base menor que o GMV comercial: divergencia negativa, mix ainda fecha 100%", () => {
  const mix = computeContentMix(50, 30, 20, 200);
  assert.equal(mix.base, 100);
  assert.equal(mix.videoPct, 50);
  assert.equal(mix.livePct, 30);
  assert.equal(mix.cardPct, 20);
  assert.equal(mix.videoPct! + mix.livePct! + mix.cardPct!, 100);
  assert.equal(mix.divergencePct, -50);
});

// ---------------------------------------------------------------------------
// 3. Base zero
// ---------------------------------------------------------------------------

test("base zero: base, percentuais e divergencia indisponiveis (nunca 0%)", () => {
  const mix = computeContentMix(0, 0, 0, 5000);
  assert.deepEqual(mix, UNAVAILABLE_MIX);
  assert.equal(mix.base, null);
  assert.equal(mix.videoPct, null);
  assert.equal(mix.livePct, null);
  assert.equal(mix.cardPct, null);
  assert.equal(mix.divergencePct, null);
});

test("componentes nulos equivalem a base zero", () => {
  assert.deepEqual(computeContentMix(null, null, null, 100), UNAVAILABLE_MIX);
  assert.deepEqual(computeContentMix(undefined, undefined, undefined, undefined), UNAVAILABLE_MIX);
});

// ---------------------------------------------------------------------------
// 4. GMV comercial zero com base valida
// ---------------------------------------------------------------------------

test("GMV comercial zero com base valida: percentuais validos, divergencia null", () => {
  const mix = computeContentMix(60, 30, 10, 0);
  assert.equal(mix.base, 100);
  assert.equal(mix.videoPct, 60);
  assert.equal(mix.livePct, 30);
  assert.equal(mix.cardPct, 10);
  // sem denominador nao ha o que reconciliar — null, nunca 0%
  assert.equal(mix.divergencePct, null);
});

// ---------------------------------------------------------------------------
// 5. Base igual ao GMV comercial
// ---------------------------------------------------------------------------

test("base igual ao GMV comercial: divergencia 0.0 explicita (zero medido)", () => {
  const mix = computeContentMix(50, 30, 20, 100);
  assert.equal(mix.divergencePct, 0);
  assert.notEqual(mix.divergencePct, null); // zero medido != ausencia
});

// ---------------------------------------------------------------------------
// 6. Componente individual zero
// ---------------------------------------------------------------------------

test("componente zero com base valida: percentual 0.0, nao null", () => {
  const mix = computeContentMix(75, 25, 0, 100);
  assert.equal(mix.cardPct, 0);
  assert.notEqual(mix.cardPct, null);
  assert.equal(mix.videoPct, 75);
  assert.equal(mix.livePct, 25);
  assert.equal(mix.videoPct! + mix.livePct! + mix.cardPct!, 100);
});

// ---------------------------------------------------------------------------
// 7. Soma arredondada 99,9 / 100,1 — sem ajuste artificial, sem "Outros"
// ---------------------------------------------------------------------------

test("soma arredondada pode dar 99,9% ou 100,1% e nao e' corrigida", () => {
  // 1/3 cada -> 33,3 + 33,3 + 33,3 = 99,9
  const a = computeContentMix(1, 1, 1, 3);
  assert.equal(a.videoPct, 33.3);
  const somaA = a.videoPct! + a.livePct! + a.cardPct!;
  assert.ok(Math.abs(somaA - 99.9) < 1e-9, `esperado 99,9 e veio ${somaA}`);

  // 2/6, 2/6, 2/6 com arredondamento para cima em dois -> 100,1
  const b = computeContentMix(100, 100, 101, 301);
  const somaB = b.videoPct! + b.livePct! + b.cardPct!;
  assert.ok(somaB >= 99.9 - 1e-9 && somaB <= 100.1 + 1e-9, `soma ${somaB} fora da tolerancia`);

  // nenhum campo residual em nenhum dos dois
  for (const mix of [a, b]) {
    assert.deepEqual(
      Object.keys(mix).sort(),
      ["base", "cardPct", "divergencePct", "livePct", "videoPct"],
      "o contrato nao pode ganhar campo residual",
    );
  }
});

// ---------------------------------------------------------------------------
// 8. Dominancia
// ---------------------------------------------------------------------------

test("marca sem TikTok nao declara video dominante", () => {
  assert.equal(dominantContentOrigin(UNAVAILABLE_MIX), null);
  assert.equal(dominantContentOrigin(computeContentMix(0, 0, 0, 0)), null);
  assert.equal(dominantContentOrigin(contentMixFromApi(null)), null);
  assert.equal(dominantContentOrigin(contentMixFromApi({})), null);
});

test("dominancia devolve o maior componente quando a base e' valida", () => {
  assert.equal(dominantContentOrigin(computeContentMix(60, 30, 10, 100)), "video");
  assert.equal(dominantContentOrigin(computeContentMix(10, 60, 30, 100)), "live");
  assert.equal(dominantContentOrigin(computeContentMix(10, 30, 60, 100)), "card");
});

test("empate segue a ordem deterministica documentada video > live > card", () => {
  assert.equal(dominantContentOrigin(computeContentMix(50, 50, 0, 100)), "video");
  assert.equal(dominantContentOrigin(computeContentMix(0, 50, 50, 100)), "live");
  // tres iguais -> video, por desempate; e' mix medido, nao ausencia
  assert.equal(dominantContentOrigin(computeContentMix(1, 1, 1, 3)), "video");
});

test("base valida com percentual faltando na resposta nao inventa dominante", () => {
  const parcial = contentMixFromApi({
    tiktok_content_gmv_base: 100,
    tiktok_video_pct: null,
    tiktok_live_pct: 30,
    tiktok_card_pct: 10,
  });
  assert.equal(dominantContentOrigin(parcial), null);
});

// ---------------------------------------------------------------------------
// 9. Leitura do contrato do backend (live nao recalcula)
// ---------------------------------------------------------------------------

test("contentMixFromApi reempacota os campos do backend sem recalcular", () => {
  const mix = contentMixFromApi({
    tiktok_gmv: 100,
    tiktok_content_gmv_base: 110,
    tiktok_video_pct: 54.5,
    tiktok_live_pct: 27.3,
    tiktok_card_pct: 18.2,
    tiktok_content_gmv_divergence_pct: 10,
  });
  assert.equal(mix.base, 110);
  assert.equal(mix.videoPct, 54.5);
  assert.equal(mix.divergencePct, 10);
});

test("base ausente ou nao positiva na resposta vira mix indisponivel", () => {
  assert.deepEqual(contentMixFromApi({ tiktok_content_gmv_base: null }), UNAVAILABLE_MIX);
  assert.deepEqual(contentMixFromApi({ tiktok_content_gmv_base: 0 }), UNAVAILABLE_MIX);
});

// ---------------------------------------------------------------------------
// 10. Pesos da barra: monetarios, sem residuo
// ---------------------------------------------------------------------------

test("pesos da barra usam valores monetarios, nao percentuais arredondados", () => {
  const w = contentMixWeights(4_996_333, 2_201_169, 2_512_285);
  assert.equal(w.video, 4_996_333);
  assert.equal(w.live, 2_201_169);
  assert.equal(w.card, 2_512_285);
  // exatamente tres pesos — nenhum "others"
  assert.deepEqual(Object.keys(w).sort(), ["card", "live", "video"]);
});

test("pesos tratam nulo como zero sem quebrar", () => {
  assert.deepEqual(contentMixWeights(null, undefined, 10), { video: 0, live: 0, card: 10 });
});

// ---------------------------------------------------------------------------
// 11. Wiring estatico
// ---------------------------------------------------------------------------

test("tipos do api-client contem os dois campos novos", () => {
  for (const campo of ["tiktok_content_gmv_base", "tiktok_content_gmv_divergence_pct"]) {
    // uma vez em CanaisKpis, uma em CanaisBrandRow
    const ocorrencias = API_CLIENT.split(campo).length - 1;
    assert.ok(ocorrencias >= 2, `${campo} deveria aparecer nos dois tipos, apareceu ${ocorrencias}x`);
  }
});

/** Remove comentarios de linha e de bloco: o invariante e' sobre CODIGO.
 * Citar o padrao antigo num comentario que explica a remocao e' documentacao
 * legitima — foi o que o backend fez com a medicao de 6,85% no UE-F1A. */
function semComentarios(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .map((l) => {
      const t = l.trimStart();
      if (t.startsWith("//") || t.startsWith("*")) return "";
      return l.replace(/\s\/\/.*$/, "");
    })
    .join("\n");
}

test("o frontend nao divide componentes de conteudo pelo GMV comercial", () => {
  // padroes do defeito antigo, na pagina e no mock do api-client
  const proibidos = [
    "tkVidTotal / tkGmvTotal",
    "tkLiveTotal / tkGmvTotal",
    "tkCardTotal / tkGmvTotal",
    "tkVid / tkGmv",
    "tkLive / tkGmv",
    "tkCard / tkGmv",
  ];
  const pageCode = semComentarios(PAGE);
  const apiCode = semComentarios(API_CLIENT);
  for (const p of proibidos) {
    assert.ok(!pageCode.includes(p), `page.tsx voltou a usar o GMV comercial como denominador: ${p}`);
    assert.ok(!apiCode.includes(p), `api-client.ts voltou a usar o GMV comercial como denominador: ${p}`);
  }
  // e as variaveis do calculo antigo nao existem mais em codigo
  for (const v of ["tkVidPctTotal", "tkLivePctTotal", "tkCardPctTotal"]) {
    assert.ok(!pageCode.includes(v), `variavel do calculo antigo reintroduzida: ${v}`);
  }
});

test("o texto 'Atribuicao de GMV por origem' saiu da pagina", () => {
  assert.ok(!PAGE.includes("Atribuição de GMV por origem"));
  assert.ok(!PAGE.includes("Atribuicao TikTok por Marca"));
  // e o rotulo correto entrou
  assert.ok(PAGE.includes("Mix do GMV de conteúdo do TikTok"));
  assert.ok(PAGE.includes("Não representa participação nas vendas totais."));
  assert.ok(PAGE.includes("Mix do GMV de conteúdo por marca"));
});

test("nao existe categoria residual 'Outros' na barra", () => {
  // o calculo do residuo nao pode voltar como CODIGO (mencao em comentario
  // explicando a remocao e' documentacao, nao defeito)
  const semComentarios = PAGE.split("\n")
    .filter((l) => !l.trim().startsWith("*") && !l.trim().startsWith("//") && !l.trim().startsWith("/*"))
    .join("\n");
  assert.ok(!semComentarios.includes("Math.max(0, 100 -"), "residuo 'others' reintroduzido");
  assert.ok(!semComentarios.includes("bg-slate-300"), "segmento cinza de residuo reintroduzido");
  assert.ok(!/\bothers\b/.test(semComentarios), "variavel others reintroduzida em codigo");
});

test("a pagina consome os campos novos e a barra semantica", () => {
  assert.ok(PAGE.includes("contentMixFromApi"), "a pagina deve ler o mix do backend");
  assert.ok(PAGE.includes("ContentMixBar"), "a barra renomeada deve estar em uso");
  assert.ok(!PAGE.includes("<AttributionBar"), "a barra antiga nao pode mais ser renderizada");
  assert.ok(PAGE.includes("Dif. base × GMV comercial"), "o diagnostico por marca deve aparecer");
  // os rotulos de indisponibilidade vivem no modulo (constantes), nao em
  // literal solto na pagina — a pagina apenas os consome
  assert.ok(PAGE.includes("MIX_UNAVAILABLE_LABEL"), "base ausente deve usar a constante do modulo");
  assert.ok(PAGE.includes("formatDivergenceBr"), "divergencia deve passar pelo formatador pt-BR");
  assert.ok(PAGE.includes("formatContentPctBr"), "percentuais do mix devem usar o formatador pt-BR");
});

// ---------------------------------------------------------------------------
// FINDING 1 — piso tipografico de 12px nos arquivos tocados
// ---------------------------------------------------------------------------

test("nenhum arquivo tocado usa fonte abaixo de 12px", () => {
  const tocados: Array<[string, string]> = [
    ["app/canais/page.tsx", PAGE],
    ["src/lib/api-client.ts", API_CLIENT],
    ["src/lib/tiktok-content-mix.ts", MODULO],
  ];
  const padroes: Array<[RegExp, string]> = [
    [/text-\[10px\]/g, "text-[10px]"],
    [/text-\[11px\]/g, "text-[11px]"],
    [/fontSize:\s*['"]?1[01](px)?['"]?/g, "fontSize 10/11"],
    [/font-size:\s*1[01]px/g, "font-size 10/11"],
  ];
  for (const [nome, src] of tocados) {
    for (const [rx, rotulo] of padroes) {
      const achados = src.match(rx) ?? [];
      assert.equal(
        achados.length, 0,
        `${nome} tem ${achados.length} ocorrencia(s) de ${rotulo}; o piso do gate e' 12px`,
      );
    }
  }
});

// ---------------------------------------------------------------------------
// FINDING 2 — localizacao pt-BR (logica pura, sem asserção textual fragil)
// ---------------------------------------------------------------------------

test("percentual comum sai em pt-BR com virgula decimal", () => {
  assert.equal(formatContentPctBr(54.5), "54,5%");
  assert.equal(formatContentPctBr(27.3), "27,3%");
  assert.equal(formatContentPctBr(100), "100,0%");
  // sempre uma decimal, mesmo em inteiro
  assert.equal(formatContentPctBr(60), "60,0%");
  // nunca ponto decimal
  assert.ok(!formatContentPctBr(54.5).includes("."), "decimal deve ser virgula");
});

test("zero medido sai como 0,0% e null como travessao", () => {
  assert.equal(formatContentPctBr(0), "0,0%");
  assert.equal(formatContentPctBr(null), PCT_UNAVAILABLE_LABEL);
  assert.equal(formatContentPctBr(undefined), PCT_UNAVAILABLE_LABEL);
  // zero medido nunca vira travessao
  assert.notEqual(formatContentPctBr(0), PCT_UNAVAILABLE_LABEL);
});

test("divergencia: zero medido sem sinal, positivo com + e negativo com -", () => {
  assert.equal(formatDivergenceBr(0), "0,0%");
  assert.equal(formatDivergenceBr(10), "+10,0%");
  assert.equal(formatDivergenceBr(-50), "-50,0%");
  assert.equal(formatDivergenceBr(6.85), "+6,9%");
  assert.equal(formatDivergenceBr(-0.4), "-0,4%");
});

test("divergencia null vira 'Comparacao indisponivel', nunca 0,0%", () => {
  assert.equal(formatDivergenceBr(null), DIVERGENCE_UNAVAILABLE_LABEL);
  assert.equal(formatDivergenceBr(undefined), DIVERGENCE_UNAVAILABLE_LABEL);
  assert.equal(DIVERGENCE_UNAVAILABLE_LABEL, "Comparação indisponível");
  assert.notEqual(formatDivergenceBr(null), "0,0%");
});

test("formatadores encadeiam com o mix calculado", () => {
  const mix = computeContentMix(60, 30, 20, 100);
  assert.equal(formatContentPctBr(mix.videoPct), "54,5%");
  assert.equal(formatContentPctBr(mix.livePct), "27,3%");
  assert.equal(formatContentPctBr(mix.cardPct), "18,2%");
  assert.equal(formatDivergenceBr(mix.divergencePct), "+10,0%");

  const vazio = computeContentMix(0, 0, 0, 100);
  assert.equal(formatContentPctBr(vazio.videoPct), PCT_UNAVAILABLE_LABEL);
  assert.equal(formatDivergenceBr(vazio.divergencePct), DIVERGENCE_UNAVAILABLE_LABEL);
  assert.equal(MIX_UNAVAILABLE_LABEL, "Mix de conteúdo indisponível");
});

test("milhar tambem segue pt-BR (ponto como separador de milhar)", () => {
  // divergencia grande e' improvavel, mas nao pode sair com formato en-US
  assert.equal(formatDivergenceBr(1234.5), "+1.234,5%");
});

test("o bloco TikTok nao usa mais o fmtPct compartilhado nem toFixed cru", () => {
  // fmtPct segue existindo para ML/Shopee — o gate nao os altera
  assert.ok(PAGE.includes("function fmtPct("), "fmtPct deve continuar para ML/Shopee");
  // ...mas nenhum campo do mix/divergencia/tiktok passa por ele
  const proibidos = [
    "fmtPct(mix.", "fmtPct(tkMixTotal.", "fmtPct(b.tiktok_", "fmtPct(displayKpis?.tiktok_",
    "fmtPct(partPct)", "tiktok_conversion_rate.toFixed",
  ];
  for (const p of proibidos) {
    assert.ok(!PAGE.includes(p), `bloco TikTok ainda usa formatador nao-localizado: ${p}`);
  }
});

// ---------------------------------------------------------------------------
// FINDING 3 — redacao factual da linhagem
// ---------------------------------------------------------------------------

test("o modulo distingue fonte direta de linhagem upstream", () => {
  assert.ok(
    MODULO.includes("marts.fact_marketplace_daily_performance"),
    "a fonte direta servida pela API deve estar nomeada",
  );
  assert.ok(MODULO.includes("GMV canonico de pedidos"), "linhagem do GMV comercial");
  assert.ok(
    MODULO.includes("reconciliada com `gold.tiktok_brand_daily`"),
    "linhagem dos componentes deve ser descrita como reconciliada, nao como fonte direta",
  );
  // e nao pode mais afirmar que a base "vem da" gold como se fosse a fonte lida
  assert.ok(
    !MODULO.includes("a base\n *   de conteudo vem da `gold.tiktok_brand_daily`"),
    "redacao antiga de linhagem reintroduzida",
  );
});

test("a UI fala de linhagens diferentes, nao de fontes diferentes", () => {
  const norm = PAGE.replace(/\s+/g, " ");
  assert.ok(norm.includes("As medidas têm linhagens diferentes"), "redacao de linhagem exigida");
  assert.ok(!norm.includes("As duas medidas vêm de fontes diferentes"), "redacao antiga ainda presente");
});

test("a divergencia continua negada como cobertura, participacao, margem e severidade", () => {
  const norm = PAGE.replace(/\s+/g, " ");
  assert.ok(
    norm.includes("não é cobertura, participação, margem nem severidade"),
    "as quatro negacoes devem estar explicitas na UI",
  );
  const doc = MODULO.replace(/\s+/g, " ");
  assert.ok(
    doc.includes("Nao e' cobertura, participacao, margem nem severidade"),
    "as quatro negacoes devem estar no contrato do modulo",
  );
});

test("a divergencia nao e' rotulada como cobertura, share ou margem", () => {
  // procura os termos proibidos na vizinhanca do rotulo de divergencia
  const idx = PAGE.indexOf("Dif. base × GMV comercial");
  assert.ok(idx > 0);
  const vizinhanca = PAGE.slice(Math.max(0, idx - 400), idx + 400).toLowerCase();
  for (const termo of ["cobertura", "margem"]) {
    assert.ok(!vizinhanca.includes(termo), `divergencia rotulada como ${termo}`);
  }
});

test("colunas ambiguas da tabela TikTok foram renomeadas", () => {
  assert.ok(PAGE.includes("GMV comercial"), "coluna de GMV deve dizer comercial");
  assert.ok(PAGE.includes("Share da marca no GMV comercial"));
  assert.ok(PAGE.includes("Mix de conteúdo"));
  assert.ok(PAGE.includes("Vídeo % da base"));
});

// ---------------------------------------------------------------------------
// 12. Mock reconcilia — por marca e no total
// ---------------------------------------------------------------------------

test("o mock deriva o mix por construcao, sem percentual escrito a mao", () => {
  assert.ok(
    API_CLIENT.includes("CANAIS_MOCK_SEED.map(withContentMix)"),
    "o mock deve derivar os campos de mix via computeContentMix",
  );
  // nenhum percentual de conteudo hardcoded sobrou na semente
  assert.ok(
    !/tiktok_video_pct:\s*\d/.test(API_CLIENT),
    "percentual de video hardcoded voltou ao mock",
  );
});

/**
 * `api-client.ts` importa `./mock-data` sem extensao, o que o loader ESM do
 * `node --test` nao resolve — por isso os testes deste repositorio nunca o
 * importam em runtime (so tipos, que sao apagados). Para verificar o mock
 * NUMERICAMENTE, extraimos a semente monetaria do proprio fonte e a passamos
 * pela mesma funcao pura que o mock usa. Se a semente mudar, o teste
 * acompanha; se a derivacao mudar, o teste quebra.
 */
function seedMonetarioDoMock(): Array<{
  brand: string; gmv: number; video: number; live: number; card: number;
}> {
  const inicio = API_CLIENT.indexOf("const CANAIS_MOCK_SEED");
  const fim = API_CLIENT.indexOf("function withContentMix");
  assert.ok(inicio > 0 && fim > inicio, "semente do mock nao localizada no fonte");
  const bloco = API_CLIENT.slice(inicio, fim);

  const num = (s: string) => parseFloat(s.replace(/_/g, ""));
  const linhas = [...bloco.matchAll(
    /brand:\s*"([a-z]+)",[\s\S]{0,120}?tiktok_gmv:\s*([\d_]+),\s*tiktok_gmv_video:\s*([\d_]+),\s*tiktok_gmv_live:\s*([\d_]+),\s*tiktok_gmv_card:\s*([\d_]+)/g,
  )];
  return linhas.map((m) => ({
    brand: m[1], gmv: num(m[2]), video: num(m[3]), live: num(m[4]), card: num(m[5]),
  }));
}

test("mock: base e divergencia reconciliam para cada marca e para o total", () => {
  const seed = seedMonetarioDoMock();
  assert.ok(seed.length >= 2, `o mock precisa de >=2 marcas TikTok, achou ${seed.length}`);

  let vidSoma = 0, liveSoma = 0, cardSoma = 0, gmvSoma = 0, baseSoma = 0;

  for (const s of seed) {
    const mix = computeContentMix(s.video, s.live, s.card, s.gmv);
    // base == soma dos tres componentes, ao centavo
    assert.equal(mix.base, s.video + s.live + s.card, `base de ${s.brand}`);
    // os tres percentuais fecham 100% dentro do arredondamento
    const soma = mix.videoPct! + mix.livePct! + mix.cardPct!;
    const EPS = 1e-9;
    assert.ok(
      soma >= 99.9 - EPS && soma <= 100.1 + EPS,
      `mix de ${s.brand} soma ${soma}, fora da tolerancia de arredondamento`,
    );
    // divergencia reconcilia com a definicao
    const esperada = parseFloat((((mix.base! - s.gmv) / s.gmv) * 100).toFixed(1));
    assert.equal(mix.divergencePct, esperada, `divergencia de ${s.brand}`);

    vidSoma += s.video; liveSoma += s.live; cardSoma += s.card;
    gmvSoma += s.gmv; baseSoma += mix.base!;
  }

  // total: soma os componentes primeiro, percentual depois
  const total = computeContentMix(vidSoma, liveSoma, cardSoma, gmvSoma);
  assert.equal(total.base, baseSoma, "base do total = soma das bases das marcas");
  assert.equal(total.videoPct, parseFloat(((vidSoma / baseSoma) * 100).toFixed(1)));

  // e NAO e' media simples dos percentuais das marcas
  const mediaSimples =
    seed.reduce((acc, s) => acc + computeContentMix(s.video, s.live, s.card, s.gmv).videoPct!, 0) /
    seed.length;
  assert.notEqual(
    total.videoPct, parseFloat(mediaSimples.toFixed(1)),
    "o total do mock nao pode coincidir com a media simples dos percentuais",
  );
});

test("mock: o total do api-client soma componentes antes do percentual", () => {
  // contraprova estatica: o KPI do mock le `tkMixTotal`, derivado de
  // computeContentMix(tkVid, tkLive, tkCard, tkGmv) — somas entre marcas.
  assert.ok(
    API_CLIENT.includes("const tkMixTotal = computeContentMix(tkVid, tkLive, tkCard, tkGmv)"),
    "o total do mock deve sair da soma dos componentes",
  );
  for (const campo of ["tiktok_video_pct", "tiktok_live_pct", "tiktok_card_pct"]) {
    const re = new RegExp(`${campo}:\\s*showTk \\? tkMixTotal\\.`);
    assert.ok(re.test(API_CLIENT), `${campo} do mock deve vir de tkMixTotal`);
  }
  // e o canal desligado devolve null, nunca 0
  assert.ok(API_CLIENT.includes("tiktok_content_gmv_base: showTk ? tkMixTotal.base : null"));
  assert.ok(
    API_CLIENT.includes("tiktok_content_gmv_divergence_pct: showTk ? tkMixTotal.divergencePct : null"),
  );
});
