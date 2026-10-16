/**
 * DRE-UX-1 — requisicao de `GET /api/v1/dre`.
 *
 * Modulo proprio, e nao mais uma funcao no `api-client.ts`: a DRE nao tem
 * modo demonstracao. Um numero de exemplo numa DRE seria lido como margem
 * real — com a API fora, a tela mostra o erro, nunca dado inventado.
 *
 * A URL da requisicao sai de `dreApiUrl`, a mesma funcao que escreve a barra
 * de endereco (ver `dreQuery`).
 */
import {
  classificarFalha,
  dreApiUrl,
  falhaDePayload,
  problemaNaResposta,
  type DreFiltros,
  type DreResponse,
  type FalhaDre,
} from "./dre.ts";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export class DreErro extends Error {
  readonly falha: FalhaDre;
  constructor(falha: FalhaDre) {
    super(falha.mensagem);
    this.name = "DreErro";
    this.falha = falha;
  }
}

export async function fetchDre(filtros: DreFiltros, signal?: AbortSignal): Promise<DreResponse> {
  let res: Response;
  try {
    res = await fetch(dreApiUrl(API_URL, filtros), { signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new DreErro(classificarFalha(null, null));
  }
  if (!res.ok) {
    let corpo: unknown = null;
    try {
      corpo = await res.json();
    } catch {
      // corpo nao-JSON (proxy, HTML de erro): a classificacao usa so' o status
    }
    throw new DreErro(classificarFalha(res.status, corpo));
  }
  return lerCorpoDre(res);
}

/**
 * 200 -> DRE validada. JSON quebrado ou forma incompativel vira
 * `payload_invalido` ("Resposta inválida da API"), e NAO "sem conexao": a API
 * respondeu. Abort durante a leitura continua sendo abort.
 */
export async function lerCorpoDre(res: { json(): Promise<unknown> }): Promise<DreResponse> {
  let json: unknown;
  try {
    json = await res.json();
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new DreErro(falhaDePayload("o corpo não é JSON"));
  }
  const problema = problemaNaResposta(json);
  if (problema) throw new DreErro(falhaDePayload(problema));
  return json as DreResponse;
}
