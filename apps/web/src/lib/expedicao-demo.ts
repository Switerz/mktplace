/**
 * T-0010 — ponto UNICO de busca das rotas da Expedicao no navegador, e o
 * modo de DEMONSTRACAO para QA.
 *
 * `?demo=1` (ou `?demo=<cenario>`, ver `expedicao-fixtures.ts`) troca as
 * respostas da API por dados FICTICIOS. So' existe fora de producao:
 *
 *   - a checagem e' `process.env.NODE_ENV !== "production"`, escrita INLINE:
 *     o Next troca a expressao por literal no build de producao, a condicao
 *     dobra para `false` e o import das fixtures sai do bundle;
 *   - em producao `?demo=` e' ignorado e a busca vai direto ao `fetch`.
 *
 * Rota que a demonstracao nao conhece cai no `fetch` real.
 */
export const PARAM_DEMO = "demo";

/** Cenario pedido na URL, ou `null` sem `?demo=`. Puro, para teste. */
export function cenarioDaUrl(search: string): string | null {
  const v = new URLSearchParams(search).get(PARAM_DEMO);
  return v === null || v === "" || v === "0" ? null : v;
}

/** Demonstracao ligada? Sempre `false` em producao. */
export function demoAtivo(search: string, nodeEnv: string | undefined = process.env.NODE_ENV): boolean {
  return nodeEnv !== "production" && cenarioDaUrl(search) !== null;
}

export async function buscarExpedicao(url: string, init?: RequestInit): Promise<Response> {
  if (process.env.NODE_ENV !== "production" && typeof window !== "undefined") {
    const cenario = cenarioDaUrl(window.location.search);
    if (cenario !== null) {
      const { lerCenario, respostaFicticia } = await import("./expedicao-fixtures.ts");
      const c = lerCenario(cenario === "1" ? "ok" : cenario);
      // "carregando": a resposta nunca chega.
      if (c === "carregando") return new Promise<Response>(() => {});
      const r = respostaFicticia(url, c);
      if (r) {
        return new Response(JSON.stringify(r.corpo), {
          status: r.status,
          headers: { "Content-Type": "application/json" },
        });
      }
    }
  }
  return fetch(url, init);
}
