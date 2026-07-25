// Identidade de requisicao por canal/proposito na aba Produtos (Gate U4,
// rodada de correcao consolidada — FINDING 1). Cada tabela e cada resumo
// Pareto tem a sua PROPRIA chave, contendo somente os parametros que o
// respectivo endpoint realmente recebe — nunca os 6 estados misturados numa
// unica chave global (tabela e resumo continuam podendo carregar de forma
// independente, ver FINDING 3).

export interface MlTableKeyInput {
  brand: string;
  bucket: string | null;
  signal: string;
  status: string;
  velocity: string;
  offset: number;
  sortColumn: string | null;
  sortDirection: string | null;
}

export function buildMlTableKey(input: MlTableKeyInput): string {
  return [
    input.brand, input.bucket ?? "", input.signal, input.status, input.velocity,
    input.offset, input.sortColumn ?? "", input.sortDirection ?? "",
  ].join("|");
}

export interface MlSummaryKeyInput {
  brand: string;
  signal: string;
  status: string;
  velocity: string;
}

export function buildMlSummaryKey(input: MlSummaryKeyInput): string {
  return [input.brand, input.signal, input.status, input.velocity].join("|");
}

/** TikTok e Shopee compartilham exatamente o mesmo contrato de parametros
 * de tabela (marca + periodo + bucket + offset + sort) — uma unica funcao,
 * mas cada canal guarda seu PROPRIO estado/chave (nunca comparados entre
 * si). */
export interface PeriodTableKeyInput {
  brand: string;
  period: string;
  bucket: string | null;
  offset: number;
  sortColumn: string | null;
  sortDirection: string | null;
}

export function buildPeriodTableKey(input: PeriodTableKeyInput): string {
  return [
    input.brand, input.period, input.bucket ?? "", input.offset,
    input.sortColumn ?? "", input.sortDirection ?? "",
  ].join("|");
}

export interface PeriodSummaryKeyInput {
  brand: string;
  period: string;
}

export function buildPeriodSummaryKey(input: PeriodSummaryKeyInput): string {
  return [input.brand, input.period].join("|");
}

/**
 * Estado de disponibilidade de uma tabela/resumo para a identidade ATUAL:
 * - "loading": requisicao em andamento OU a chave resolvida ainda nao bate
 *   com a chave atual (cobre o frame de render anterior ao efeito rodar —
 *   ex.: troca de aba, marca, periodo, bucket, pagina ou ordenacao);
 * - "unavailable": a chave atual ja foi resolvida (nao esta carregando),
 *   mas a requisicao correspondente falhou/nao retornou dado;
 * - "available": a chave atual foi resolvida com sucesso.
 */
export type ProdutosSectionState = "loading" | "unavailable" | "available";

export interface ChannelAvailabilityInput {
  resolvedKey: string | null;
  requestKey: string;
  loading: boolean;
  hasData: boolean;
}

export function resolveChannelAvailability(input: ChannelAvailabilityInput): ProdutosSectionState {
  if (input.loading || input.resolvedKey !== input.requestKey) return "loading";
  return input.hasData ? "available" : "unavailable";
}

/**
 * Mensagem de dados parciais (FINDING 3) — só existe quando exatamente UM
 * dos dois (tabela, resumo) está disponível e o outro está resolvido como
 * indisponível (nunca durante loading, nunca quando os dois falham — nesse
 * caso os estados de erro/offline já existentes de cada componente bastam).
 */
export function describeProdutosPartialWarning(table: ProdutosSectionState, summary: ProdutosSectionState): string | null {
  if (table === "loading" || summary === "loading") return null;
  if (table === "available" && summary === "unavailable") {
    return "Dados parciais: a tabela de produtos carregou, mas o resumo Pareto está indisponível para este filtro.";
  }
  if (table === "unavailable" && summary === "available") {
    return "Dados parciais: o resumo Pareto carregou, mas a tabela de produtos está indisponível para este filtro.";
  }
  return null;
}
