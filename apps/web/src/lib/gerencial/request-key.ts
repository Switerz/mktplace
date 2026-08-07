/**
 * Identidade de requisicao da Gerencial V2 (Gate V2-1, Task B).
 *
 * A Gerencial passou a consumir SEIS superficies logicas (overview, brands,
 * executive-summary, canais, quality e trend por canal). Cada uma resolve no
 * seu proprio tempo, entao a identidade nao pode ser global-implicita: cada
 * fonte guarda a chave da ultima resposta que concluiu e so' e' considerada
 * fresca quando essa chave bate com a chave atual (mesmo contrato "Finding 2"
 * dos gates U2-U5, via `computeRequestStatus`).
 *
 * A METRICA do grafico (GMV | Pedidos) NAO entra na chave de fetch: `/trend`
 * devolve `gmv` e `orders` na mesma resposta, logo alternar a metrica e'
 * operacao puramente local e nunca dispara requisicao nova. Ela entra apenas
 * na identidade VISUAL (`buildGerencialViewKey`), usada para memoizacao e
 * para o QA verificar que a troca nao refez nenhum fetch.
 */
import type { Marketplace } from "../mock-data.ts";
import type { TrendGranularityRequest } from "../api-client.ts";

export type TrendMetric = "gmv" | "orders";

export interface GerencialKeyInput {
  channels: readonly Marketplace[];
  brands: readonly string[];
  dateFrom: string;
  dateTo: string;
  compare: boolean;
  retryKey: number;
}

/** Chave de FETCH: tudo que muda o resultado de qualquer uma das seis fontes. */
export function buildGerencialRequestKey(input: GerencialKeyInput): string {
  return [
    input.channels.join(","),
    input.brands.join(","),
    input.dateFrom,
    input.dateTo,
    String(input.compare),
    String(input.retryKey),
  ].join("|");
}

/**
 * Chave de FETCH de uma serie de canal unico. Deriva da chave global trocando
 * a selecao de canais pelo canal isolado — dois canais diferentes no mesmo
 * filtro nunca colidem, e trocar de periodo/marca invalida as tres series.
 *
 * A GRANULARIDADE entra aqui, e somente aqui (Gate V2-2): ela muda o resultado
 * de `/trend` e de mais nada. Trocar de granularidade refaz apenas as chamadas
 * de serie dos canais selecionados — `/overview`, `/brands`, `/quality`,
 * `/canais` e `/executive-summary` nao sao tocados, porque a chave deles
 * (`buildGerencialRequestKey`) nao a inclui.
 */
export function buildChannelSeriesKey(
  input: GerencialKeyInput,
  channel: Marketplace,
  granularity: TrendGranularityRequest = "auto",
): string {
  return `${buildGerencialRequestKey({ ...input, channels: [channel] })}|g:${granularity}`;
}

/** Chave VISUAL: inclui a metrica selecionada. Nao deve ser usada para fetch.
 * O teste do contrato compara as duas chaves diretamente: trocar a metrica
 * muda esta e mantem `buildGerencialRequestKey` byte a byte igual. */
export function buildGerencialViewKey(input: GerencialKeyInput, metric: TrendMetric): string {
  return `${buildGerencialRequestKey(input)}|${metric}`;
}
