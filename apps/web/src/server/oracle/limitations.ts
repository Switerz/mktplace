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
 * TikTok nao registra cancelamento no mart. Na auditoria original a coluna
 * vinha 0; medido em 2026-10-13 ela chega `null` em todos os meses. Os dois
 * significam AUSENCIA DE MEDICAO, nao excelencia operacional — e e' por isso
 * que precisa de texto, nao de numero.
 */
export const TIKTOK_CANCEL_NOT_MEASURED: Limitation = {
  topic: "cancelamento_tiktok",
  scope: "Qualidade — TikTok Shop",
  description:
    "O TikTok Shop nao tem cancelamento medido nesta base: a fonte devolve nulo (ou 0) em todos os meses auditados. Trate como NAO MENSURADO, nunca como 0% de cancelamento.",
};

/**
 * Comissao do Mercado Livre NAO CARREGADA para marcas especificas no periodo.
 *
 * MCP-COVERAGE-1: o texto anterior afirmava que a comissao "nao esta
 * disponivel no mart" — deixou de ser verdade com o MARGEM-REAL-2 (a carga
 * existe a partir do fim de jun/2026). A disponibilidade agora e' POR LINHA,
 * entao a limitacao nomeia as marcas afetadas em vez de afirmar ausencia geral.
 */
export function mlCommissionMissing(brands: readonly string[]): Limitation {
  return {
    topic: "comissao_ml",
    scope: `Financeiro/Canais — Mercado Livre (${[...brands].sort().join(", ")})`,
    description:
      "A comissao do Mercado Livre nao esta carregada para estas marcas no periodo consultado: o custo de marketplace vem nulo — ausencia de dado, nunca custo zero. Outras marcas ou periodos podem ter o valor; leia marketplace_cost_available de cada linha.",
  };
}

/**
 * `marketplace_cost_available` do backend e' "ha custo em PELO MENOS UM dia
 * do periodo" (`total_fees_n > 0`), nao "o periodo inteiro esta coberto".
 * Medido em 2026-10-13: jun/2026 inteiro sai `available=true` com 1,5-3,2%
 * contra 14-17% nos meses seguintes, porque so os ultimos dias de junho tem
 * comissao carregada — e o backend nao emite `data_warning` nesse caso.
 */
export const ML_COMMISSION_PARTIAL_WINDOW: Limitation = {
  topic: "cobertura_comissao_ml",
  scope: "Financeiro/Canais — Mercado Livre",
  description:
    "Comissao do Mercado Livre 'disponivel' significa que ha valor em pelo menos um dia do periodo, nao que o periodo inteiro esteja coberto. A carga e' recente: uma janela que comece antes dela traz custo SUBESTIMADO sem nenhum outro aviso. Para comparar meses, confirme que o percentual e' coerente com os meses seguintes antes de afirmar queda ou alta.",
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

/**
 * A fonte regional soma unidades com `COALESCE(SUM(units_sold), 0)`: canal ou
 * periodo sem unidade registrada entra como 0. Medido em 2026-10-13: o ML
 * devolve 0 unidades em todo mes com dezenas de milhares de pedidos, e numa
 * consulta "todos os canais" o total e' so o da Shopee. Como zero e ausencia
 * sao indistinguiveis no payload, a tool devolve `units_sold` sempre nulo.
 */
export const REGIONAL_UNITS_NOT_MEASURED: Limitation = {
  topic: "unidades_regioes",
  scope: "Regioes",
  description:
    "Unidades por UF nao sao confiaveis nesta fonte: canais sem unidade registrada entram como 0 na soma, e zero fica indistinguivel de ausencia. Por isso units_sold vem sempre nulo nesta tool. Para unidades, use torre_produtos_prioritarios.",
};

/** Base de produtos do ML e' cumulativa, sem competencia mensal. */
export const ML_PRODUCTS_CUMULATIVE: Limitation = {
  topic: "produtos_ml_cumulativo",
  scope: "Produtos — Mercado Livre",
  description:
    "O ranking de produtos do Mercado Livre e' cumulativo e nao tem competencia mensal. Nao e' possivel recortar por mes, e o valor nao corresponde a um periodo especifico.",
};

/**
 * Gate SH-API-2D — traduz um aviso do selo de escopo em Limitation.
 *
 * Deliberadamente uma FUNCAO e nao uma lista de constantes: o texto vem do
 * backend, que e' quem mede. Duplicar aqui as frases de imaturidade criaria
 * exatamente o que o cabecalho deste arquivo proibe — uma lista rigida que
 * pode contradizer o backend. O unico conhecimento local e o mapeamento de
 * codigo para escopo legivel, e ele degrada sozinho para um escopo generico
 * quando o backend inventar um codigo novo (nunca descarta o aviso).
 */
export function scopeWarningToLimitation(
  warning: { code: string; severity: string; description?: string; message?: string },
  channelLabel: string,
): Limitation {
  const escopos: Record<string, string> = {
    shopee_produtos_maturacao_insuficiente: "competencia em maturacao",
    shopee_produtos_nenhuma_linha_elegivel: "sem produto concluido",
    shopee_produtos_carga_ausente: "carga ausente",
    shopee_produtos_carga_atras_da_diaria: "mart atras da diaria",
    shopee_produtos_fonte_nunca_carregada: "competencia nunca carregada",
    shopee_produtos_cobertura_de_marcas: "cobertura de marcas incompleta",
    shopee_produtos_elegibilidade_parcial: "elegibilidade parcial",
    shopee_produtos_maturacao_nao_medida: "maturacao nao medida",
    shopee_produtos_historico_de_carga_ausente: "historico de carga ausente",
    shopee_produtos_indice_invalido: "indice de maturacao invalido",
  };
  const detalhe = escopos[warning.code] ?? "qualidade do escopo";
  return {
    topic: warning.code,
    scope: `Produtos — ${channelLabel} (${detalhe})`,
    description: warning.description ?? warning.message ?? warning.code,
  };
}

/** O dia corrente e' sempre parcial. */
export const CURRENT_DAY_PARTIAL: Limitation = {
  topic: "dia_corrente",
  scope: "Todas as superficies diarias",
  description:
    "A carga do dia corrente nao fecha em tempo real: o GMV do proprio dia aparece drasticamente menor que o de um dia normal. Nao interprete como queda.",
};

/** Sempre aplicaveis, independentemente da resposta do backend. */
export const ALWAYS_APPLICABLE: readonly Limitation[] = [NO_MARGIN, CURRENT_DAY_PARTIAL];
