/**
 * Mix do GMV de conteudo do TikTok — logica pura compartilhada.
 *
 * CONTRATO (espelha o backend, Gate UE-F1A, commit 417be72):
 *
 *   content_base  = gmv_video + gmv_live + gmv_card
 *   pct_video     = gmv_video / content_base * 100
 *   pct_live      = gmv_live  / content_base * 100
 *   pct_card      = gmv_card  / content_base * 100
 *   divergence_pct = (content_base - commercial_gmv) / commercial_gmv * 100
 *
 * FONTE DIRETA E LINHAGENS — tres coisas distintas, nao confundir:
 *
 * - **Fonte direta servida pela API** de `/canais`: todos os campos acima saem
 *   de `marts.fact_marketplace_daily_performance` (Neon). E' de la' que a
 *   `get_canais` le, e e' o unico ponto que o frontend consome.
 * - **Linhagem do GMV comercial** (`tiktok_gmv`): GMV canonico de pedidos,
 *   calculado da Raw com `SUM(sub_total)` e allowlist de status.
 * - **Linhagem dos componentes** (`gmv_video`/`gmv_live`/`gmv_card`): quebra de
 *   conteudo do TikTok, reconciliada com `gold.tiktok_brand_daily` — calculada
 *   sobre o valor antigo (~`total_amount`), nao sobre o GMV canonico.
 *
 * As duas linhagens convivem na MESMA tabela do mart. E' por isso que a soma
 * dos componentes nao fecha com `tiktok_gmv`: o dado esta correto, o que difere
 * e' a definicao a montante de cada coluna.
 *
 * SEMANTICA — o que estes numeros sao e o que NAO sao:
 *
 * - Os tres percentuais sao a composicao INTERNA da base de conteudo. Nao sao
 *   participacao nas vendas totais. O denominador e' a propria base, nunca o
 *   GMV comercial.
 * - A divergencia e' diagnostico de reconciliacao entre as duas linhagens.
 *   **Nao e' cobertura, participacao, margem nem severidade.**
 * - Soma arredondada de 99,9% ou 100,1% e' valida. Nao existe ajuste artificial
 *   para fechar exatamente 100%, e NUNCA existe categoria residual "Outros":
 *   as tres categorias particionam a base por construcao.
 *
 * Este modulo existe para que o fallback de demonstracao produza exatamente o
 * mesmo contrato do backend, e para que a regra de dominancia, a formatacao
 * pt-BR e os estados de indisponibilidade sejam testaveis sem renderizar a
 * pagina. Para dados live a pagina consome os campos JA calculados pelo
 * backend — nao recalcula nada.
 */

/** Uma casa decimal, igual ao `round(x, 1)` do backend. */
function round1(v: number): number {
  return parseFloat(v.toFixed(1));
}

export interface ContentMix {
  /** Soma monetaria de video+live+card. `null` quando nao ha base positiva. */
  base: number | null;
  videoPct: number | null;
  livePct: number | null;
  cardPct: number | null;
  /** Diagnostico de reconciliacao. `null` quando falta base ou GMV comercial. */
  divergencePct: number | null;
}

export const UNAVAILABLE_MIX: ContentMix = {
  base: null,
  videoPct: null,
  livePct: null,
  cardPct: null,
  divergencePct: null,
};

/**
 * Calcula o mix a partir dos componentes monetarios.
 *
 * Regras de nulo/zero, identicas as do backend:
 * - `base <= 0` -> base e os tres percentuais indisponiveis (`null`), e
 *   divergencia `null`. Nunca 0%, que leria como "medido e deu zero".
 * - `base > 0` e `commercialGmv <= 0` -> percentuais validos (o mix nao depende
 *   do GMV comercial), divergencia `null` (sem denominador nao ha o que
 *   reconciliar).
 * - `base === commercialGmv` -> divergencia `0.0`, que e' zero MEDIDO e deve
 *   ser exibido.
 * - componente zero com base valida -> percentual `0.0`, nao `null`.
 */
export function computeContentMix(
  video: number | null | undefined,
  live: number | null | undefined,
  card: number | null | undefined,
  commercialGmv: number | null | undefined,
): ContentMix {
  const v = video ?? 0;
  const l = live ?? 0;
  const c = card ?? 0;
  const base = v + l + c;

  if (!(base > 0)) return UNAVAILABLE_MIX;

  const gmv = commercialGmv ?? 0;
  return {
    base,
    videoPct: round1((v / base) * 100),
    livePct: round1((l / base) * 100),
    cardPct: round1((c / base) * 100),
    divergencePct: gmv > 0 ? round1(((base - gmv) / gmv) * 100) : null,
  };
}

export type ContentOrigin = "video" | "live" | "card";

/**
 * Componente dominante do mix, ou `null` quando nao ha mix valido.
 *
 * Diferente da `dominantChannel` anterior, que devolvia "video" mesmo com os
 * tres percentuais nulos (porque `0 >= 0 && 0 >= 0`) e assim rotulava como
 * dominante um canal sem dado algum.
 *
 * Desempate DETERMINISTICO e documentado: video > live > card. Escolhido para
 * ser estavel entre renders e reproduzivel em teste, nao por preferencia de
 * negocio. Zero real continua zero: se os tres forem 0.0 com base valida,
 * o desempate devolve "video" — isso e' um mix medido, nao ausencia de dado.
 */
export function dominantContentOrigin(mix: ContentMix): ContentOrigin | null {
  if (mix.base == null) return null;
  const { videoPct, livePct, cardPct } = mix;
  if (videoPct == null || livePct == null || cardPct == null) return null;
  if (videoPct >= livePct && videoPct >= cardPct) return "video";
  if (livePct >= cardPct) return "live";
  return "card";
}

/**
 * Le o mix ja calculado pelo backend. Para dados live a pagina NUNCA recalcula
 * o contrato — so reempacota os campos que o endpoint entrega, para que a
 * renderizacao e a regra de dominancia trabalhem com uma forma unica.
 */
export function contentMixFromApi(source: {
  tiktok_gmv_video?: number | null;
  tiktok_gmv_live?: number | null;
  tiktok_gmv_card?: number | null;
  tiktok_video_pct?: number | null;
  tiktok_live_pct?: number | null;
  tiktok_card_pct?: number | null;
  tiktok_content_gmv_base?: number | null;
  tiktok_content_gmv_divergence_pct?: number | null;
} | null | undefined): ContentMix {
  if (!source) return UNAVAILABLE_MIX;
  const base = source.tiktok_content_gmv_base ?? null;
  if (base == null || !(base > 0)) return UNAVAILABLE_MIX;
  return {
    base,
    videoPct: source.tiktok_video_pct ?? null,
    livePct: source.tiktok_live_pct ?? null,
    cardPct: source.tiktok_card_pct ?? null,
    divergencePct: source.tiktok_content_gmv_divergence_pct ?? null,
  };
}

/**
 * Pesos da barra de composicao: os valores MONETARIOS nao arredondados.
 *
 * Usar os percentuais arredondados como largura deixaria um vao de ate 0,3pp,
 * que a implementacao anterior preenchia com um segmento cinza "Outros" —
 * residuo fabricado que nao existe na fonte. Com `flexGrow` proporcional ao
 * valor monetario a barra preenche 100% por construcao, sem alterar nenhum
 * percentual exibido e sem inventar categoria.
 */
export function contentMixWeights(
  video: number | null | undefined,
  live: number | null | undefined,
  card: number | null | undefined,
): { video: number; live: number; card: number } {
  return { video: video ?? 0, live: live ?? 0, card: card ?? 0 };
}

// ---------------------------------------------------------------------------
// Formatacao pt-BR do bloco TikTok
//
// Formatadores PROPRIOS deste bloco, de proposito. O `fmtPct` local da
// `canais/page.tsx` e' compartilhado pelas tres secoes (TikTok, ML, Shopee) e
// usa `toFixed`, insensivel a locale; troca-lo mudaria ML e Shopee por arrasto,
// fora do escopo deste gate. Aqui a decimal e' virgula, como o resto da UI
// pt-BR ja faz para dinheiro e contagem.
//
// O sinal negativo e' o hifen-menos que o proprio `Intl.NumberFormat("pt-BR")`
// emite — mesma convencao dos outros formatadores do repositorio, em vez do
// U+2212 tipografico.
// ---------------------------------------------------------------------------

const PCT_BR = new Intl.NumberFormat("pt-BR", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

/** `+`/`-` explicito, exceto no zero: zero medido nao ganha sinal. */
const PCT_BR_SIGNED = new Intl.NumberFormat("pt-BR", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
  signDisplay: "exceptZero",
});

/** Ausencia de valor no bloco TikTok — mesma convencao de travessao da pagina. */
export const PCT_UNAVAILABLE_LABEL = "—";
/** Divergencia sem denominador: nunca 0%, que leria como "linhagens batem". */
export const DIVERGENCE_UNAVAILABLE_LABEL = "Comparação indisponível";
/** Base de conteudo ausente: nunca uma barra que pareca 0%. */
export const MIX_UNAVAILABLE_LABEL = "Mix de conteúdo indisponível";

/** Percentual pt-BR com uma decimal: `54,5%`. `null` -> travessao. */
export function formatContentPctBr(v: number | null | undefined): string {
  if (v == null) return PCT_UNAVAILABLE_LABEL;
  return `${PCT_BR.format(v)}%`;
}

/**
 * Divergencia pt-BR com sinal: `+10,0%`, `0,0%`, `-50,0%`.
 * `null` -> "Comparação indisponível", nunca `0,0%`.
 */
export function formatDivergenceBr(v: number | null | undefined): string {
  if (v == null) return DIVERGENCE_UNAVAILABLE_LABEL;
  return `${PCT_BR_SIGNED.format(v)}%`;
}
