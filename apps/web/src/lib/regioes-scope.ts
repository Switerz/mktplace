// Escopo declarado da tela Regioes (Gate DQ2 — achado 2 do DQ1, ver
// docs/MARKETPLACE_DATA_QUALITY_CHECKPOINT.md §7/§10). Modulo puro, mesmo
// padrao de regioes-format.ts.
//
// Problema corrigido: `uf_fill_pct = 100%` + `coverage_level = "ok"` eram
// lidos como "cobertura integral", quando na verdade descrevem apenas o
// PREENCHIMENTO DE UF dentro de um universo ja reduzido — em julho/2026 o
// total regional media 43,8% menos que Gerencial/Canais no mesmo periodo.
//
// As TRES dimensoes sao distintas e nunca podem ser colapsadas num numero:
//   1. cobertura de CANAL      — ML e Shopee tem regional; TikTok nao tem
//      (nenhuma fonte mapeada expoe UF do pedido) => `not_applicable`;
//   2. ELEGIBILIDADE regional  — parte dos pedidos do canal coberto nao entra
//      no fato regional (sem remessa/UF resolvivel);
//   3. PREENCHIMENTO de UF     — % com UF conhecida DENTRO do elegivel.
//
// Regra dura: nao fabricamos um percentual "geral" de cobertura, porque o
// denominador real (GMV/pedidos totais do periodo) nao existe neste endpoint.
// A limitacao e' declarada em texto, nunca estimada.

const CHANNEL_LABELS: Record<string, string> = {
  tiktok: "TikTok Shop",
  ml: "Mercado Livre",
  shopee: "Shopee",
};

/** Canais que possuem fato regional. TikTok nunca tem linha na tabela
 * (fato de dominio fixado no Gate 6A/6B e reconfirmado no DQ1). */
const CHANNELS_WITH_REGIONAL = new Set(["ml", "shopee"]);

export type RegionalScopeStatus =
  /** Nenhum canal selecionado tem fato regional (ex.: TikTok isolado). */
  | "not_applicable"
  /** Parte dos canais selecionados nao tem regional (ex.: "Todos"). */
  | "partial_channels"
  /** Todos os canais selecionados tem regional (ex.: ML e/ou Shopee). */
  | "all_selected_channels";

export interface RegionalScope {
  status: RegionalScopeStatus;
  /** Labels dos canais selecionados COM fato regional. */
  channelsInScope: string[];
  /** Labels dos canais selecionados SEM fato regional. */
  channelsOutOfScope: string[];
  /** Texto do escopo — sempre presente, para o total nunca ser lido como
   * "GMV dos tres canais". */
  scopeNote: string;
  /** Aviso adicional quando o preenchimento de UF esta completo mas o escopo
   * de canal NAO — o caso que originou o achado. `null` quando nao se aplica. */
  ufFillCaveat: string | null;
}

export function channelLabel(channel: string): string {
  return CHANNEL_LABELS[channel] ?? channel;
}

/**
 * Deriva o escopo regional a partir do que a API ja informa: os canais
 * pedidos e os canais estruturalmente sem cobertura regional
 * (`channels_sem_cobertura_regional`). Nunca infere GMV, nunca estima
 * percentual de cobertura total.
 *
 * @param selectedChannels canais no filtro atual (ex.: ["tiktok","ml","shopee"])
 * @param channelsSemCobertura eco da API (hoje: ["tiktok"] quando pedido)
 * @param ufFillPct `uf_fill_pct` do summary — `null` quando nao ha elegiveis
 */
export function buildRegionalScope(
  selectedChannels: readonly string[],
  channelsSemCobertura: readonly string[],
  ufFillPct: number | null,
): RegionalScope {
  const semCobertura = new Set(channelsSemCobertura);
  // Um canal fica fora do escopo se a API o declarou sem cobertura OU se ele
  // nao esta na lista de canais com regional (defesa em profundidade: os dois
  // criterios concordam hoje, e discordar nunca pode virar "cobertura total").
  const outOfScope = selectedChannels.filter((c) => semCobertura.has(c) || !CHANNELS_WITH_REGIONAL.has(c));
  const inScope = selectedChannels.filter((c) => !outOfScope.includes(c));

  const inLabels = inScope.map(channelLabel);
  const outLabels = outOfScope.map(channelLabel);

  const status: RegionalScopeStatus =
    inScope.length === 0 ? "not_applicable" : outOfScope.length > 0 ? "partial_channels" : "all_selected_channels";

  let scopeNote: string;
  if (status === "not_applicable") {
    scopeNote =
      `Nenhum canal selecionado tem cobertura regional (${outLabels.join(", ") || "seleção atual"}) — ` +
      "não há dado de UF na fonte. Ausência de dado regional não significa venda zero.";
  } else if (status === "partial_channels") {
    scopeNote =
      `Escopo regional: ${inLabels.join(" e ")}. ${outLabels.join(", ")} fora do escopo — ` +
      "os totais desta tela cobrem apenas os canais com dado de UF e não são comparáveis ao GMV total da Gerencial/Canais.";
  } else {
    scopeNote =
      `Escopo regional: ${inLabels.join(" e ")}. Os totais consideram apenas os pedidos elegíveis ao fato ` +
      "regional (com UF resolvível) — não são comparáveis ao GMV total da Gerencial/Canais.";
  }

  // O caso exato do achado: UF 100% preenchida dentro do elegivel, com canal
  // fora do escopo — "100%" jamais pode ser lido como cobertura completa.
  const ufFillCaveat =
    ufFillPct != null && ufFillPct >= 100 && status !== "all_selected_channels"
      ? "UF preenchida em 100% dos pedidos elegíveis — isso descreve só o preenchimento dentro do escopo regional, " +
        "não cobertura de 100% do GMV do período."
      : null;

  return { status, channelsInScope: inLabels, channelsOutOfScope: outLabels, scopeNote, ufFillCaveat };
}

/** Rótulo do total regional — nunca "GMV Regional" puro, que era lido como o
 * GMV do período. */
export const REGIONAL_GMV_LABEL = "GMV com cobertura regional";

/** Rótulo do preenchimento de UF, explicitando o denominador. */
export const UF_FILL_LABEL = "UF preenchida (elegíveis)";
