/**
 * Adapter read-only para a API FastAPI da Torre — SERVER-ONLY.
 *
 * Deliberadamente NAO reutiliza `src/lib/api-client.ts`: aquele modulo e' de
 * browser e tem fallback para dados mock. Um MCP que devolve mock e' pior do
 * que um MCP que falha, porque o numero inventado e' indistinguivel do real.
 *
 * Contrato (docs/ORACLE_MCP_PLAN.md secao 15):
 * - somente GET, somente caminhos LITERAIS deste arquivo;
 * - nenhuma URL, host ou path vem do modelo;
 * - UM timeout cobre a operacao inteira: resposta + leitura do corpo + parse;
 * - parametro interno desconhecido FALHA (typo nunca remove filtro em silencio);
 * - teto de tamanho medido em BYTES;
 * - exige `application/json`: corpo HTML (ex: 403 do WAF, que inclui o IP do
 *   solicitante) e' descartado sem nunca chegar ao modelo;
 * - zero retry; falha vira categoria sanitizada, nunca lista vazia silenciosa.
 */
import { isUsableBackendUrl } from "./access.ts";
import { toolError } from "./errors.ts";

/** Caminhos allowlisted. A chave e' o unico "endereco" que o resto do codigo conhece. */
export const ENDPOINTS = {
  overview: "/api/v1/performance/overview",
  brands: "/api/v1/performance/brands",
  trend: "/api/v1/performance/trend",
  canais: "/api/v1/performance/canais",
  quality: "/api/v1/performance/quality",
  produtosMl: "/api/v1/performance/produtos/ml",
  produtosTiktok: "/api/v1/performance/produtos/tiktok",
  produtosShopee: "/api/v1/performance/produtos/shopee",
  healthDatasource: "/api/v1/performance/health-datasource",
  // ATENCAO: regioes NAO vive sob /performance — verificado em producao no OM0.
  regioesSummary: "/api/v1/regioes/summary",
  regioesByUf: "/api/v1/regioes/by-uf",
} as const;

export type EndpointKey = keyof typeof ENDPOINTS;

/**
 * Parametros aceitos. Uma chave fora daqui e' ERRO, nao descarte silencioso:
 * um typo como `brand` no lugar de `brands` removeria o filtro e ampliaria a
 * consulta sem ninguem perceber.
 */
const ALLOWED_PARAMS = new Set([
  "channels",
  "brands",
  "date_from",
  "date_to",
  "ref_month",
  "compare",
  "granularity",
  "limit",
  "offset",
  "pareto_bucket",
  "brand",
  "uf",
]);

export type QueryParams = Record<string, string | number | boolean | undefined>;

export type TorreClientOptions = {
  baseUrl: string;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  maxBytes?: number;
};

const DEFAULT_TIMEOUT_MS = 8_000;
const DEFAULT_MAX_BYTES = 256 * 1024;

const encoder = new TextEncoder();

/** Tamanho em BYTES UTF-8 — nao em caracteres. */
function utf8Bytes(s: string): number {
  return encoder.encode(s).byteLength;
}

function isAbortError(err: unknown): boolean {
  return err instanceof Error && err.name === "AbortError";
}

function abortError(): Error {
  const e = new Error("aborted");
  e.name = "AbortError";
  return e;
}

export class TorreClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly timeoutMs: number;
  private readonly maxBytes: number;

  constructor(opts: TorreClientOptions) {
    // Validacao estrita e unica da base: https, sem credencial, sem query,
    // sem fragment e sem path. Um path configurado aqui seria ignorado adiante,
    // entao recusamos em vez de descartar em silencio.
    if (!isUsableBackendUrl(opts.baseUrl)) {
      throw toolError("MISSING_CONFIGURATION");
    }
    this.baseUrl = new URL(opts.baseUrl).origin;
    this.fetchImpl = opts.fetchImpl ?? fetch;
    this.timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.maxBytes = opts.maxBytes ?? DEFAULT_MAX_BYTES;
  }

  /**
   * GET em um endpoint allowlisted. Devolve JSON parseado; a validacao do
   * contrato de negocio e' responsabilidade da tool (ver `upstream.ts`).
   *
   * O timeout cobre TODA a operacao — nao apenas os headers.
   */
  async get(endpoint: EndpointKey, params: QueryParams = {}): Promise<unknown> {
    const path = ENDPOINTS[endpoint];
    if (!path) throw toolError("MISSING_CONFIGURATION");

    const url = new URL(this.baseUrl + path);
    for (const [k, v] of Object.entries(params)) {
      // A allowlist e' checada ANTES do descarte de `undefined`: um typo cujo
      // valor por acaso seja `undefined` tambem precisa falhar, senao o erro
      // fica latente ate o dia em que aquele parametro passa a ter valor.
      if (!ALLOWED_PARAMS.has(k)) {
        // Falha ANTES do fetch: zero chamada upstream.
        throw toolError("MISSING_CONFIGURATION");
      }
      if (v === undefined) continue;
      url.searchParams.set(k, String(v));
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      let res: Response;
      try {
        res = await this.fetchImpl(url.toString(), {
          method: "GET",
          signal: controller.signal,
          headers: { accept: "application/json" },
          // Sem credenciais: a API e' publica hoje e o MCP nao guarda segredo.
          cache: "no-store",
        });
      } catch (err) {
        // Nem a URL nem a mensagem original sao propagadas.
        throw toolError(
          isAbortError(err) || controller.signal.aborted ? "SOURCE_TIMEOUT" : "SOURCE_UNAVAILABLE",
        );
      }

      if (!res.ok) {
        // 4xx/5xx do backend OU pagina HTML do WAF: em nenhum caso o corpo e'
        // lido para o modelo, e o status nao e' exposto.
        throw toolError("SOURCE_UNAVAILABLE");
      }

      // Content-Type precisa ser JSON. Um 200 com HTML e' resposta invalida.
      const contentType = res.headers.get("content-type") ?? "";
      if (!contentType.toLowerCase().includes("application/json")) {
        throw toolError("INVALID_UPSTREAM_RESPONSE");
      }

      const declared = Number(res.headers.get("content-length") ?? "");
      if (Number.isFinite(declared) && declared > this.maxBytes) {
        throw toolError("INVALID_UPSTREAM_RESPONSE");
      }

      // A leitura do corpo continua sob o MESMO deadline.
      const body = await this.readCapped(res, controller.signal);

      try {
        return JSON.parse(body) as unknown;
      } catch {
        throw toolError("INVALID_UPSTREAM_RESPONSE");
      }
    } finally {
      // Limpo exatamente uma vez, ao final da operacao COMPLETA (resposta,
      // corpo e parse) — nao logo apos os headers.
      clearTimeout(timer);
    }
  }

  /**
   * Le o corpo respeitando o teto em bytes E o deadline. Um corpo que fica
   * pendurado apos os headers termina como `SOURCE_TIMEOUT`, nao pendura a
   * invocacao.
   */
  private async readCapped(res: Response, signal: AbortSignal): Promise<string> {
    const reader = res.body?.getReader();

    if (!reader) {
      // Fallback sem stream: ainda assim sob o deadline, e medindo BYTES.
      let text: string;
      try {
        text = await this.withDeadline(res.text(), signal);
      } catch (err) {
        throw toolError(
          isAbortError(err) || signal.aborted ? "SOURCE_TIMEOUT" : "INVALID_UPSTREAM_RESPONSE",
        );
      }
      if (utf8Bytes(text) > this.maxBytes) throw toolError("INVALID_UPSTREAM_RESPONSE");
      return text;
    }

    const decoder = new TextDecoder();
    let total = 0;
    let out = "";

    try {
      for (;;) {
        const chunk = await this.withDeadline(reader.read(), signal);
        if (chunk.done) break;
        total += chunk.value?.byteLength ?? 0;
        if (total > this.maxBytes) throw toolError("INVALID_UPSTREAM_RESPONSE");
        out += decoder.decode(chunk.value, { stream: true });
      }
    } catch (err) {
      if (isAbortError(err) || signal.aborted) throw toolError("SOURCE_TIMEOUT");
      throw err;
    } finally {
      // Libera a conexao mesmo em erro/abort — mas SEM aguardar.
      //
      // `cancel()` de um stream/adaptador hostil pode nunca resolver; se
      // esperassemos por ele aqui, a tool ficaria pendurada DEPOIS de o
      // deadline ja ter expirado — anulando o proprio timeout. O cancelamento
      // e' best-effort e nao participa do deadline.
      void reader.cancel().catch(() => {
        /* best-effort: o objetivo e' apenas nao vazar o stream */
      });
    }

    out += decoder.decode();
    return out;
  }

  /**
   * Corre a promessa contra o abort do deadline. Sem isso, um corpo pendente
   * ficaria esperando indefinidamente mesmo com o timer disparado.
   */
  private withDeadline<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
    if (signal.aborted) return Promise.reject(abortError());

    let onAbort: (() => void) | undefined;
    const aborted = new Promise<never>((_resolve, reject) => {
      onAbort = () => reject(abortError());
      signal.addEventListener("abort", onAbort, { once: true });
    });
    // Evita "unhandled rejection" se o abort disparar depois do fim da leitura.
    aborted.catch(() => {});

    return Promise.race([promise, aborted]).finally(() => {
      if (onAbort) signal.removeEventListener("abort", onAbort);
    }) as Promise<T>;
  }
}

// ---------------------------------------------------------------------------
// Helpers de leitura do payload ja validado
// ---------------------------------------------------------------------------

/**
 * Numero ou `null`. Preserva zero e preserva ausencia: o que nao e' numero
 * finito vira `null`, JAMAIS 0.
 */
export function numOrNull(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  return null;
}

/** Inteiro ou `null`, com a mesma regra de preservacao. */
export function intOrNull(v: unknown): number | null {
  const n = numOrNull(v);
  return n === null ? null : Math.trunc(n);
}

export function strOrNull(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}
