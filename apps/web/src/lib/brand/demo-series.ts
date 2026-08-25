// Série diária de DEMONSTRAÇÃO da Marca 360 (Gate V3-2).
//
// Encapsula, pelo menor caminho possível, o fallback sintético que já existia
// na página (defeito M3 do desenho): quando `/performance/daily` não responde,
// a série exibida é gerada, não medida.
//
// Por que este módulo existe em vez de a página importar `mock-daily`
// diretamente: o nome do símbolo importado é a primeira coisa que alguém lê ao
// auditar a página. `generateDailyData` de um módulo chamado `mock-daily`
// parecia um detalhe de implementação; `buildDemoSeries` com
// `DEMO_SERIES_WARNING` ao lado deixa explícito que aquilo é exemplo e não
// resposta ao vivo. Nenhum comportamento muda — a mesma função é chamada.
//
// A página NÃO importa `mock-daily`; importa daqui.

import { generateDailyData, type DailyRow } from "../mock-daily.ts";

export type { DailyRow };

/** Rótulo curto do estado, para etiqueta/badge. */
export const DEMO_SERIES_LABEL = "dados de exemplo";

/** Aviso obrigatório sempre que a série exibida for gerada. Explícito quanto
 * ao uso: número de exemplo não sustenta decisão. */
export const DEMO_SERIES_WARNING =
  "Dados de exemplo — não usar para decisão. A leitura ao vivo de GMV diário não respondeu, " +
  "então a série e o mix abaixo são gerados para preservar o layout, não medidos.";

/**
 * Série sintética de `days` dias para a marca.
 *
 * Determinística por marca (mesma entrada, mesma saída), como sempre foi — o
 * que importa aqui é que quem chama sabe, pelo nome, que o resultado é
 * demonstração.
 */
export function buildDemoSeries(brand: string, days: number): DailyRow[] {
  return generateDailyData(brand, Math.max(1, days));
}
