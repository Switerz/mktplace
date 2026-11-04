/**
 * AUTH-GOOGLE-5 — o navegador chama a API pela mesma origem (`/torre-api/*`),
 * e o Next repassa ao Render. Assim o cookie de sessao chega a API.
 * Sem imports de browser/Next: e' carregado pelo `next.config.ts`.
 */
export const TORRE_API_PREFIX = "/torre-api";

const ORIGEM_VALIDA = /^(https:\/\/[^\s/?#]+|http:\/\/(localhost|127\.0\.0\.1):\d+)\/?$/;

export function torreApiRewrites(
  origin: string | undefined,
): { source: string; destination: string }[] {
  const o = (origin ?? "").trim();
  if (!o) return [];
  if (!ORIGEM_VALIDA.test(o)) throw new Error("TORRE_API_ORIGIN invalida");
  return [{ source: `${TORRE_API_PREFIX}/:path*`, destination: `${o.replace(/\/$/, "")}/:path*` }];
}

/** Base relativa sem origem do proxy = toda chamada 404 (e a tela cai em demo). Falha alto no build. */
export function assertTorreApiConfig(publicApiUrl: string | undefined, origin: string | undefined): void {
  if ((publicApiUrl ?? "").startsWith("/") && !(origin ?? "").trim()) {
    throw new Error("TORRE_API_ORIGIN obrigatoria quando NEXT_PUBLIC_API_URL e relativa");
  }
}
