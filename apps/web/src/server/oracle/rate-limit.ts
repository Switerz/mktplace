/**
 * Rate limit de invocacao de tools.
 *
 * Requisito normativo da spec 2026-07-28 (Tools / Security Considerations):
 * "Servers MUST: ... Rate limit tool invocations ...".
 *
 * Janela deslizante em memoria do processo. Em serverless isso e' APROXIMADO
 * — varias invocacoes podem cair em instancias diferentes, e cada uma tem seu
 * proprio contador. Isso esta declarado no plano e no runbook em vez de ser
 * apresentado como limite exato.
 *
 * Modulo puro: o relogio e' injetado, entao o teste nao precisa de timers.
 */

export type RateLimitConfig = {
  /** Chamadas permitidas dentro da janela. */
  readonly limit: number;
  /** Tamanho da janela em milissegundos. */
  readonly windowMs: number;
};

export const DEFAULT_RATE_LIMIT: RateLimitConfig = {
  limit: 60,
  windowMs: 5 * 60 * 1000,
};

export type RateLimitResult =
  | { allowed: true; remaining: number }
  | { allowed: false; retryAfterSeconds: number };

export class SlidingWindowRateLimiter {
  private readonly hits = new Map<string, number[]>();
  private readonly config: RateLimitConfig;

  constructor(config: RateLimitConfig = DEFAULT_RATE_LIMIT) {
    this.config = config;
  }

  /**
   * Registra uma tentativa e diz se ela pode prosseguir.
   * `key` identifica o chamador; hoje o piloto e' single-tenant, entao a
   * chave e' constante — mas a assinatura ja aceita segmentacao para quando
   * existir identidade real.
   */
  check(key: string, now: number): RateLimitResult {
    const windowStart = now - this.config.windowMs;
    const previous = this.hits.get(key) ?? [];
    // Descarta o que saiu da janela antes de decidir.
    const current = previous.filter((t) => t > windowStart);

    if (current.length >= this.config.limit) {
      const oldest = current[0] ?? now;
      const retryAfterMs = Math.max(0, oldest + this.config.windowMs - now);
      // Persiste a janela podada mesmo negando, para nao crescer sem limite.
      this.hits.set(key, current);
      return { allowed: false, retryAfterSeconds: Math.ceil(retryAfterMs / 1000) };
    }

    current.push(now);
    this.hits.set(key, current);
    return { allowed: true, remaining: this.config.limit - current.length };
  }

  /** Usado apenas por teste, para isolar casos. */
  reset(): void {
    this.hits.clear();
  }
}
