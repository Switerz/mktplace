// "O que mudou e por quê" da Marca 360 (Gate V3-2; bloco 5 do §8.1 do
// desenho): decomposição do GMV do intervalo global por canal, contra o
// intervalo equivalente anterior.
//
// Isto NÃO é métrica nova. É o mesmo GMV que a página já exibe, somado pela
// coluna de canal que o payload diário já traz, comparado com a janela
// anterior que a própria página já busca quando `compare` está ativo. Nenhum
// threshold, nenhuma classificação, nenhum diagnóstico: a linha diz quanto o
// canal somou antes, quanto somou agora e a diferença. A interpretação fica
// com quem lê.
//
// Semântica de ausência, que é o ponto delicado aqui:
// - canal **sem nenhuma linha com valor** na janela ⇒ `null` ("sem dado"),
//   nunca zero. Zero significaria "somou zero", e é afirmação diferente;
// - se um dos dois lados é `null`, o delta é `null`: não se subtrai ausência;
// - variação percentual só existe quando a base anterior é **maior que zero**.
//   Dividir por zero, ou por ausência, produziria ∞ ou NaN com cara de número.
//
// Módulo puro (sem React).

import type { DailyRow } from "../mock-daily.ts";

export type ChannelKey = "tiktok" | "ml" | "shopee";

const CHANNEL_LABEL: Record<ChannelKey, string> = {
  tiktok: "TikTok Shop",
  ml: "Mercado Livre",
  shopee: "Shopee",
};

const COLUMN: Record<ChannelKey, keyof Pick<DailyRow, "tiktok_gmv" | "ml_gmv" | "shopee_gmv">> = {
  tiktok: "tiktok_gmv",
  ml: "ml_gmv",
  shopee: "shopee_gmv",
};

export interface ChannelTotal {
  key: ChannelKey;
  label: string;
  /** GMV somado no intervalo; `null` quando o canal não tem dado. */
  gmv: number | null;
  /** Participação no total dos canais COM dado; `null` quando não há base. */
  sharePct: number | null;
}

/**
 * Mix por marketplace no intervalo global.
 *
 * Existe porque `ChannelMixChart` **não serve aqui**: aquele componente é o mix
 * de SUPERFÍCIE do TikTok (vídeo / live / product card) e consome a série
 * mensal de `/brand-detail`. Mix por marketplace é outra grandeza, com outra
 * fonte, e pertence ao regime global — confundir os dois foi um erro que o
 * typecheck pegou, e que na tela apareceria como um gráfico rotulado errado.
 *
 * O share é calculado sobre a soma dos canais **com dado**, e é `null` quando
 * essa soma não é positiva: distribuir 100% sobre base zero não é leitura.
 */
export function channelTotals(
  rows: readonly DailyRow[],
  selection: readonly string[],
): ChannelTotal[] {
  const ordem: ChannelKey[] = ["tiktok", "ml", "shopee"];
  const brutos = ordem
    .filter((k) => selection.includes(k))
    .map((key) => ({ key, label: CHANNEL_LABEL[key], gmv: somaCanal(rows, key) }));
  const base = brutos.reduce((acc, c) => acc + (c.gmv ?? 0), 0);
  return brutos.map((c) => ({
    ...c,
    sharePct: c.gmv == null || base <= 0 ? null : (c.gmv / base) * 100,
  }));
}

export interface ChannelChange {
  key: ChannelKey;
  label: string;
  /** GMV do canal na janela atual; `null` quando o canal não tem dado. */
  current: number | null;
  previous: number | null;
  /** `current - previous`; `null` quando qualquer lado é ausente. */
  delta: number | null;
  /** Variação percentual; `null` quando a base anterior não é positiva. */
  deltaPct: number | null;
}

/** Soma a coluna do canal. `null` quando NENHUMA linha tem valor — a distinção
 * entre "somou zero" e "não há dado" é preservada de propósito. */
function somaCanal(rows: readonly DailyRow[], key: ChannelKey): number | null {
  let total = 0;
  let algumValor = false;
  for (const r of rows) {
    const v = r[COLUMN[key]];
    if (v == null) continue;
    algumValor = true;
    total += v;
  }
  return algumValor ? total : null;
}

/**
 * Decomposição por canal, restrita aos canais selecionados no filtro global.
 *
 * A ordem de saída é a dos canais selecionados na ordem canônica
 * (TikTok, ML, Shopee) — determinística, para a leitura não mudar de posição
 * entre renderizações.
 */
export function decomposeByChannel(
  current: readonly DailyRow[],
  previous: readonly DailyRow[],
  selection: readonly string[],
): ChannelChange[] {
  const ordem: ChannelKey[] = ["tiktok", "ml", "shopee"];
  return ordem
    .filter((k) => selection.includes(k))
    .map((key) => {
      const cur = somaCanal(current, key);
      const prev = somaCanal(previous, key);
      const delta = cur == null || prev == null ? null : cur - prev;
      const deltaPct = delta == null || prev == null || prev <= 0 ? null : (delta / prev) * 100;
      return { key, label: CHANNEL_LABEL[key], current: cur, previous: prev, delta, deltaPct };
    });
}

/**
 * Ordena as linhas por **magnitude** do delta, para que o canal que mais
 * explica a variação apareça primeiro — subir ou cair conta igual, porque a
 * pergunta do bloco é "o que mudou", não "o que melhorou".
 *
 * Linhas sem delta (`null`) vão para o fim: ausência não compete por posição
 * com movimento medido.
 */
export function sortByImpact(rows: readonly ChannelChange[]): ChannelChange[] {
  return [...rows].sort((a, b) => {
    const va = a.delta == null ? -1 : Math.abs(a.delta);
    const vb = b.delta == null ? -1 : Math.abs(b.delta);
    return vb - va;
  });
}

export type ChangesState = "ready" | "sem_comparacao" | "sem_periodo_anterior" | "demonstracao";

/**
 * Por que o bloco não pode ser exibido — ou `ready`.
 *
 * O bloco só aparece quando é sustentado pelos dados que a página já tem:
 * `compare` ativo, janela anterior realmente carregada e leitura AO VIVO. Em
 * modo demonstração a comparação seria entre duas séries geradas, o que
 * pareceria análise e não seria.
 */
export function changesState(
  compare: boolean,
  isLive: boolean,
  previousRowCount: number,
): ChangesState {
  if (!compare) return "sem_comparacao";
  if (!isLive) return "demonstracao";
  if (previousRowCount === 0) return "sem_periodo_anterior";
  return "ready";
}

/** Explicação de UMA linha para cada estado não-pronto. Nunca uma caixa vazia
 * gigante: o bloco some e sobra uma frase. */
export const CHANGES_NOTE: Record<Exclude<ChangesState, "ready">, string> = {
  sem_comparacao:
    "A decomposição por canal aparece com a comparação de períodos ativada no filtro de datas.",
  sem_periodo_anterior:
    "Sem dado no intervalo equivalente anterior, não há o que comparar neste intervalo.",
  demonstracao:
    "A decomposição não é exibida em modo demonstração: comparar duas séries geradas pareceria análise sem ser.",
};
