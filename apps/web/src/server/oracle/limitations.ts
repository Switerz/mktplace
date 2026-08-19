/**
 * Limitacoes de negocio conhecidas — PONTO UNICO de curadoria.
 *
 * Regra (Fase D do handoff OM1): nao manter lista rigida que contradiga o
 * backend. Por isso este arquivo so guarda o que NAO e' derivavel da resposta
 * (a semantica de uma ausencia), e cada item traz o gatilho que o torna
 * aplicavel. Tudo que o backend ja informa — `data_warning`, `coverage_level`,
 * `channels_sem_cobertura_regional` — e' lido de la, nunca duplicado aqui.
 */

export type Limitation = {
  readonly topic: string;
  readonly scope: string;
  readonly description: string;
};

/**
 * TikTok nao registra cancelamento no mart: a coluna existe e vem 0 em todos
 * os meses auditados. Zero aqui significa AUSENCIA DE MEDICAO, nao excelencia
 * operacional — e e' por isso que precisa de texto, nao de numero.
 */
export const TIKTOK_CANCEL_NOT_MEASURED: Limitation = {
  topic: "cancelamento_tiktok",
  scope: "Qualidade — TikTok Shop",
  description:
    "O TikTok Shop nao tem cancelamento medido nesta base: o valor chega como 0 em todos os meses auditados. Trate como NAO MENSURADO, nunca como 0% de cancelamento.",
};

/** Comissao do Mercado Livre nao existe no mart (total_fees nulo para ML). */
export const ML_COMMISSION_MISSING: Limitation = {
  topic: "comissao_ml",
  scope: "Financeiro/Canais — Mercado Livre",
  description:
    "A comissao do Mercado Livre nao esta disponivel no mart. O custo de marketplace vem nulo para o ML — isso e' ausencia de dado, nunca custo zero.",
};

/** Base de repasse do TikTok difere do GMV comercial. */
export const TIKTOK_SETTLEMENT_DIRECTIONAL: Limitation = {
  topic: "repasse_tiktok",
  scope: "Financeiro — TikTok Shop",
  description:
    "A base de repasse (settlement) do TikTok difere do GMV comercial em cerca de 5,5%. Use o custo do TikTok como referencia direcional, nao como valor exato do mes.",
};

/** Nao existe CMV: margem real e' impossivel nos tres marketplaces. */
export const NO_MARGIN: Limitation = {
  topic: "margem",
  scope: "Todas as superficies",
  description:
    "Nao existe CMV nesta base, portanto nao ha margem nem lucro reais. Qualquer campo de margem estimada e' inadequado para decisao e nao e' exposto por este conector.",
};

/** Cobertura regional e' menor que a de canais — nao reconciliar a forca. */
export const REGIONAL_COVERAGE: Limitation = {
  topic: "cobertura_regional",
  scope: "Regioes",
  description:
    "A cobertura regional e' materialmente menor que a de canais: nem todo pedido tem UF atribuida. O GMV por UF NAO reconcilia com o GMV gerencial, e a diferenca e' de cobertura, nao erro de calculo. Nenhum ajuste artificial e' aplicado para forcar igualdade.",
};

/** Base de produtos do ML e' cumulativa, sem competencia mensal. */
export const ML_PRODUCTS_CUMULATIVE: Limitation = {
  topic: "produtos_ml_cumulativo",
  scope: "Produtos — Mercado Livre",
  description:
    "O ranking de produtos do Mercado Livre e' cumulativo e nao tem competencia mensal. Nao e' possivel recortar por mes, e o valor nao corresponde a um periodo especifico.",
};

/** O dia corrente e' sempre parcial. */
export const CURRENT_DAY_PARTIAL: Limitation = {
  topic: "dia_corrente",
  scope: "Todas as superficies diarias",
  description:
    "A carga do dia corrente nao fecha em tempo real: o GMV do proprio dia aparece drasticamente menor que o de um dia normal. Nao interprete como queda.",
};

/** Sempre aplicaveis, independentemente da resposta do backend. */
export const ALWAYS_APPLICABLE: readonly Limitation[] = [NO_MARGIN, CURRENT_DAY_PARTIAL];
