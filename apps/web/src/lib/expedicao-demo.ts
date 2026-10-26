/**
 * T-0010 — ponto UNICO de busca das rotas da Expedicao no navegador.
 *
 * Hoje so' repassa ao `fetch`. O modo de demonstracao (dados ficticios para
 * QA, so' em desenvolvimento) e' ligado aqui na entrega de estados.
 */
export function buscarExpedicao(url: string, init?: RequestInit): Promise<Response> {
  return fetch(url, init);
}
