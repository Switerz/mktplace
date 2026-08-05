// Semantica de INDISPONIBILIDADE das metricas de qualidade por canal
// (Gate DQ2 — achado 1 do DQ1, ver docs/MARKETPLACE_DATA_QUALITY_CHECKPOINT.md
// §10). Modulo puro (sem React), mesmo padrao de regioes-format.ts.
//
// Problema corrigido: cancelamento e devolucao do TikTok NAO existem em
// nenhum ponto servido da cadeia (`gold.tiktok_brand_daily.canceled/returned/
// refunded` = 0 em todas as linhas de 2026, contra 436.814 pedidos CANCELLED
// na Raw). A API devolve `None` corretamente — nunca um zero falso —, mas a
// tela de Qualidade simplesmente NAO exibia essas metricas para o canal, de
// modo que a ausencia nunca era declarada ao usuario.
//
// Regras deste modulo:
// - a indisponibilidade e' SEMPRE textual e inequivoca ("Nao disponivel nesta
//   fonte"); nunca "0%", nunca "sem cancelamentos", nunca tom de sucesso;
// - nao inferimos taxa a partir de ausencia e nao calculamos nada da Raw
//   (fora do escopo deste gate);
// - ML e Shopee nao passam por aqui: seus valores continuam vindo da API.

/** Valor curto exibido no lugar do numero — deliberadamente diferente de "—"
 * (usado para "sem valor no periodo") e de "0%" (observacao real). */
export const UNAVAILABLE_VALUE = "N/D";

/** Legenda curta e inequivoca que acompanha o valor. */
export const UNAVAILABLE_SUBVALUE = "Não disponível nesta fonte";

export interface UnavailableMetric {
  /** Rótulo do card/coluna. */
  label: string;
  value: typeof UNAVAILABLE_VALUE;
  subvalue: string;
}

/** Métricas de qualidade que o TikTok não tem em nenhum ponto servido. */
export const TIKTOK_UNAVAILABLE_QUALITY_METRICS: UnavailableMetric[] = [
  { label: "Cancelamento TK", value: UNAVAILABLE_VALUE, subvalue: UNAVAILABLE_SUBVALUE },
  { label: "Devolução TK", value: UNAVAILABLE_VALUE, subvalue: UNAVAILABLE_SUBVALUE },
];

/**
 * Nota de limitação exibida quando o TikTok está no escopo selecionado.
 * Explica a causa (a fonte servida não traz o dado) sem prometer número nem
 * sugerir que a taxa seja zero.
 */
export const TIKTOK_QUALITY_UNAVAILABLE_NOTE =
  "Cancelamento e devolução/reembolso do TikTok Shop não estão disponíveis na fonte que alimenta a Torre — " +
  "os campos chegam sem valor. Ausência de dado não significa taxa zero: não há como afirmar o nível de " +
  "cancelamento do canal aqui. Mercado Livre e Shopee não são afetados.";

/** Texto do `aria-live` da tela de Qualidade. Quando o TikTok está no escopo,
 * comunica a indisponibilidade junto do sucesso do carregamento — nunca deixa
 * o leitor de tela concluir que o canal está saudável ou com taxa zero. */
export function qualityLoadedAnnouncement(tiktokSelected: boolean): string {
  const base = "Dados de qualidade carregados.";
  return tiktokSelected
    ? `${base} Cancelamento e devolução do TikTok Shop não estão disponíveis nesta fonte.`
    : base;
}
