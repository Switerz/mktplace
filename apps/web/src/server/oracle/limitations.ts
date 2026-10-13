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

// ---------------------------------------------------------------------------
// MCP-COVERAGE-2 — superficies operacionais. Cada item e' emitido pela tool
// SOMENTE quando o gatilho aparece na resposta; o texto descreve a semantica
// da ausencia, nao um numero.
// ---------------------------------------------------------------------------

export const EXP_NO_ORDER_LEVEL: Limitation = {
  topic: "expedicao_sem_pedido",
  scope: "Expedicao",
  description:
    "Somente agregados por canal, conta/loja e data. A fila por pedido nao e' servida por este conector: nenhum pedido, cliente ou endereco pode ser citado a partir desta resposta.",
};

export const EXP_MANUAL_SNAPSHOT: Limitation = {
  topic: "expedicao_fotografia_manual",
  scope: "Expedicao — Shopee/Mercado Livre",
  description:
    "A fotografia declara load_mode=manual_snapshot: ela envelhece ate a proxima execucao do refresh. Cite a idade (fotografia.idade_horas) junto do numero.",
};

export const EXP_DEADLINE_NOT_PUBLISHED: Limitation = {
  topic: "expedicao_prazo_nao_publicado",
  scope: "Expedicao — prazo de despacho",
  description:
    "Parte ou todo o backlog nao tem prazo publicado pelo canal (sem_prazo_publicado). Nesses casos vencidos/em risco/no prazo vem nulos ou parciais: ausencia de prazo nao e' atraso, e nao e' 'zero vencidos'.",
};

export const EXP_BRAND_FILTER_LOCAL: Limitation = {
  topic: "expedicao_filtro_marca",
  scope: "Expedicao — Shopee/Mercado Livre",
  description:
    "O resumo da fonte nao aplica filtro de marca; o conector soma as contas da marca no mesmo lote, que e' a mesma definicao do total da fonte. Marca sem conta no canal aparece em marcas_sem_conta.",
};

export const EXP_TIKTOK_RECONSTRUCTED: Limitation = {
  topic: "ldr_tiktok_interna",
  scope: "Expedicao — TikTok Shop",
  description:
    "A LDR desta resposta usa prazo RECONSTRUIDO pela Torre (regra de dias uteis por evento). E' medicao interna, nao a medicao oficial de penalizacao do TikTok: pode divergir do painel da plataforma.",
};

export const EXP_TIKTOK_OFFICIAL: Limitation = {
  topic: "ldr_tiktok_oficial",
  scope: "Expedicao — TikTok Shop",
  description:
    "A fonte declara que o prazo desta LDR e' o oficial do marketplace (deadline_is_reconstructed=false).",
};

export const STOCK_ML_NOT_STOCK: Limitation = {
  topic: "full_ml_nao_e_estoque",
  scope: "Full — Mercado Livre",
  description:
    "Para o Mercado Livre a Torre mede a MODALIDADE do envio (Full x nao-Full), nao estoque: nao ha disponibilidade, cobertura em dias nem ruptura do ML. Estoque ML e' 'nao aplicavel' nesta fonte, nunca zero.",
};

export const STOCK_TOWER_COVERAGE: Limitation = {
  topic: "estoque_cobertura_torre",
  scope: "Estoque Full — Shopee",
  description:
    "Cobertura em dias e classificacoes (ruptura, baixo, excesso) sao calculo da Torre com limiares PROVISORIOS sobre a media de 28 dias — nao sao numero oficial da Shopee. Kits ficam fora dos indicadores operacionais (nao conciliados).",
};

export const STOCK_PARTIAL_SCOPE: Limitation = {
  topic: "estoque_escopo_parcial",
  scope: "Full/Estoque — Shopee",
  description:
    "A esteira da Shopee nao cobre todas as marcas: as nao cobertas vem listadas e NAO estao nos totais. O total e' 'Shopee (cobertura API)', nunca 'Shopee total'.",
};

export const PRICE_ADVERTISED_ONLY: Limitation = {
  topic: "preco_somente_anunciado",
  scope: "Monitoramento de precos",
  description:
    "A comparacao usa o preco ANUNCIADO. Frete, cupom, subsidio e preco final de checkout nao sao medidos e vem nulos — nunca inventados. A diferenca pode mudar de valor e de sinal quando esses componentes forem considerados.",
};

export const PRICE_REFERENCE_PDV: Limitation = {
  topic: "preco_referencia_pdv",
  scope: "Monitoramento de precos",
  description:
    "A referencia e' o preco sugerido de revenda (PDV) da tabela B2B, sem vigencia declarada. Nao e' preco minimo anunciado. Anuncio sem referencia fica em no_reference com diferenca nula — nunca 0%.",
};

export const AFFILIATE_NO_RETURN: Limitation = {
  topic: "afiliados_sem_retorno",
  scope: "Canais — afiliados",
  description:
    "So existe CUSTO de afiliados (TikTok). Nao ha receita atribuida a afiliados na fonte, portanto receita atribuida e retorno/ROAS de afiliados vem nulos — indisponiveis, nao zero.",
};

export const AFFILIATE_NO_ROWS: Limitation = {
  topic: "afiliados_sem_linhas",
  scope: "Canais — afiliados",
  description:
    "O bloco de afiliados nao trouxe linhas para este periodo/escopo (ex.: periodo que nao fecha mes calendario, ou marca sem registro). Isso e' AUSENCIA de registro, nao custo de afiliado zero.",
};

export const DISCOUNTS_NO_ROWS: Limitation = {
  topic: "descontos_sem_linhas",
  scope: "Canais — descontos TikTok",
  description:
    "O bloco de descontos do TikTok nao trouxe linhas para este escopo. Ausencia de registro nao e' desconto zero nem 'nenhum pedido'.",
};
