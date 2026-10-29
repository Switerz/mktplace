/**
 * METAS-1 — requisicoes da aba "Metas".
 *
 * Sem modo demonstracao, como a DRE: meta de exemplo seria lida como meta
 * real. Com a API fora, a tela mostra o erro.
 */
import {
  classificarFalha,
  hojeBrt,
  intervaloDoMes,
  problemaNaResposta,
  type CelulaMeta,
  type FalhaMetas,
  type MetasMes,
} from "./metas.ts";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export class MetasErro extends Error {
  readonly falha: FalhaMetas;
  constructor(falha: FalhaMetas) {
    super(falha.mensagem);
    this.name = "MetasErro";
    this.falha = falha;
  }
}

async function pedir(url: string, init?: RequestInit): Promise<unknown> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new MetasErro(classificarFalha(null, null));
  }
  let corpo: unknown = null;
  try {
    corpo = await res.json();
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
  }
  if (!res.ok) throw new MetasErro(classificarFalha(res.status, corpo));
  return corpo;
}

function validar(json: unknown): MetasMes {
  const problema = problemaNaResposta(json);
  if (problema) throw new MetasErro({ tipo: "desconhecido", mensagem: `Resposta inválida da API: ${problema}.` });
  return json as MetasMes;
}

export async function fetchMetas(mes: string, signal?: AbortSignal): Promise<MetasMes> {
  return validar(await pedir(`${API_URL}/api/v1/metas/${mes}`, { signal }));
}

export async function salvarMetas(
  mes: string,
  celulas: CelulaMeta[],
): Promise<MetasMes & { alteracoes: number }> {
  const json = await pedir(`${API_URL}/api/v1/metas/${mes}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ celulas }),
  });
  return validar(json) as MetasMes & { alteracoes: number };
}

export interface LinhaRealizado {
  brand: string;
  tiktok_gmv: number | null;
  ml_gmv: number | null;
  shopee_gmv: number | null;
}

/**
 * GMV realizado do mes por marca x canal, so' como referencia ao lado do
 * campo. Falha aqui NAO impede editar metas: devolve `null` e a tela segue.
 * Mes futuro devolve `"futuro"`, sem requisicao.
 */
export async function fetchRealizado(
  mes: string,
  signal?: AbortSignal,
): Promise<LinhaRealizado[] | null | "futuro"> {
  const janela = intervaloDoMes(mes, hojeBrt());
  if (!janela) return "futuro";
  const qs = new URLSearchParams({ channels: "all", date_from: janela.dateFrom, date_to: janela.dateTo });
  try {
    const res = await fetch(`${API_URL}/api/v1/performance/brands?${qs}`, { signal });
    if (!res.ok) return null;
    const json = (await res.json()) as { brands?: LinhaRealizado[] };
    return Array.isArray(json.brands) ? json.brands : null;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    return null;
  }
}
