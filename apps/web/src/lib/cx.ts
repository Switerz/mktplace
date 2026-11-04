/**
 * Aba CX (menu principal, 04/11/2026) — primeira versao, so' Shopee.
 *
 * `GET /api/v1/cx/shopee?brands=&dias=` junta:
 *   - chat: recorte de atendimento da Saude da Conta (fotografia diaria
 *     oficial, com a meta da Shopee);
 *   - atendimento: cancelamentos com motivo e pedidos em devolucao.
 * Mercado Livre e TikTok entram depois (dependem de permissao nos apps).
 *
 * Funcoes puras testadas em node --test; a pagina so' desenha a visao.
 */
import {
  JANELA_PADRAO,
  JANELAS_DIAS,
  problemaNoPayloadAtendimento,
  type AtendimentoResponse,
  type CanalAtendimento,
  type JanelaDias,
} from "./performance-contas-atendimento.ts";
import { MOTIVO_TIMEOUT, TIMEOUT_API_MS } from "./performance-contas.ts";
import {
  diaCurto,
  valorMetrica,
  type ContaOficial,
  type Situacao,
  type Tom,
} from "./expedicao-performance.ts";

export { JANELAS_DIAS, JANELA_PADRAO, type JanelaDias };

type ParamBruto = string | string[] | undefined;

export interface ChatShopee {
  estado: "dado" | "sem_fonte" | "erro";
  motivo?: string | null;
  fonte: string;
  contas: ContaOficial[];
  sem_foto: { marca: string; motivo: string }[];
}

export interface CxShopeeResponse {
  gerado_em: string;
  janela: AtendimentoResponse["janela"];
  chat: ChatShopee;
  atendimento: CanalAtendimento;
}

// ---------------------------------------------------------------------------
// Filtros (URL e' a fonte de verdade)
// ---------------------------------------------------------------------------
export interface FiltrosCx {
  marca: string | null;
  dias: JanelaDias;
}

function primeiro(v: ParamBruto): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

export function lerFiltrosCx(
  params: Readonly<Record<string, ParamBruto>>,
  marcasPermitidas: readonly string[],
): FiltrosCx {
  const marca = primeiro(params.brands)?.trim().toLowerCase();
  const dias = Number(primeiro(params.dias));
  return {
    marca: marca && marcasPermitidas.includes(marca) ? marca : null,
    dias: (JANELAS_DIAS as readonly number[]).includes(dias) ? (dias as JanelaDias) : JANELA_PADRAO,
  };
}

export function hrefCx(f: FiltrosCx): string {
  const qs = new URLSearchParams();
  if (f.marca) qs.set("brands", f.marca);
  if (f.dias !== JANELA_PADRAO) qs.set("dias", String(f.dias));
  const s = qs.toString();
  return s ? `/cx?${s}` : "/cx";
}

// ---------------------------------------------------------------------------
// Chat: uma linha por loja, uma coluna por metrica de atendimento
// ---------------------------------------------------------------------------
export interface CelulaChat {
  valor: string;
  situacao: Situacao | null;
  meta: string | null;
  anterior: string | null;
}

export interface VisaoChat {
  estado: ChatShopee["estado"];
  motivo: string | null;
  colunas: { codigo: string; rotulo: string }[];
  linhas: { marca: string; dia: string; tom: Tom; celulas: (CelulaChat | null)[] }[];
  semFoto: string[];
  /** Lojas com alguma metrica fora da meta da Shopee. */
  foraDaMeta: string[];
}

export function montarChat(chat: ChatShopee, rotuloMarca: (m: string) => string): VisaoChat {
  const colunas: { codigo: string; rotulo: string }[] = [];
  for (const conta of chat.contas) {
    for (const m of conta.metricas) {
      if (!colunas.some((c) => c.codigo === m.codigo)) colunas.push({ codigo: m.codigo, rotulo: m.rotulo });
    }
  }
  const linhas = chat.contas.map((conta) => ({
    marca: rotuloMarca(conta.marca),
    dia: diaCurto(conta.dia),
    tom: conta.tom,
    celulas: colunas.map((col) => {
      const m = conta.metricas.find((x) => x.codigo === col.codigo);
      if (!m) return null;
      return {
        valor: valorMetrica(m),
        situacao: m.situacao ?? null,
        meta: m.rotulo_meta ?? null,
        anterior: m.anterior == null ? null : valorMetrica({ valor: m.anterior, unidade: m.unidade }),
      };
    }),
  }));
  return {
    estado: chat.estado,
    motivo: chat.motivo ?? null,
    colunas,
    linhas,
    semFoto: chat.sem_foto.map((s) => `${rotuloMarca(s.marca)}: ${s.motivo}`),
    foraDaMeta: chat.contas
      .filter((c) => c.metricas.some((m) => m.situacao === "fora"))
      .map((c) => rotuloMarca(c.marca)),
  };
}

// ---------------------------------------------------------------------------
// Payload e fetch no servidor
// ---------------------------------------------------------------------------
export function problemaNoPayloadCx(json: unknown): string | null {
  if (!json || typeof json !== "object") return "corpo vazio";
  const o = json as Record<string, unknown>;
  const chat = o.chat as Record<string, unknown> | undefined;
  if (!chat || typeof chat !== "object" || !Array.isArray(chat.contas) || !Array.isArray(chat.sem_foto)) {
    return "chat ausente";
  }
  if (!o.atendimento || typeof o.atendimento !== "object") return "atendimento ausente";
  return problemaNoPayloadAtendimento({
    gerado_em: o.gerado_em,
    janela: o.janela,
    canais: [o.atendimento],
  });
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export const ROTA_API_CX_SHOPEE = "/api/v1/cx/shopee";

export function urlCxShopee(base: string, f: FiltrosCx): string {
  const qs = new URLSearchParams();
  if (f.marca) qs.set("brands", f.marca);
  qs.set("dias", String(f.dias));
  return `${base}${ROTA_API_CX_SHOPEE}?${qs.toString()}`;
}

export type ResultadoCx = { ok: true; dados: CxShopeeResponse } | { ok: false; motivo: string };

/** Nunca lanca: qualquer falha volta como `{ ok: false }` e a pagina mostra erro. */
export async function carregarCxShopee(
  f: FiltrosCx,
  fetchImpl: typeof fetch = fetch,
  base: string = API_URL,
  timeoutMs: number = TIMEOUT_API_MS,
): Promise<ResultadoCx> {
  const controle = new AbortController();
  const timer = setTimeout(() => controle.abort(), timeoutMs);
  try {
    let res: Response;
    try {
      res = await fetchImpl(urlCxShopee(base, f), { cache: "no-store", signal: controle.signal });
    } catch {
      if (controle.signal.aborted) return { ok: false, motivo: MOTIVO_TIMEOUT };
      return { ok: false, motivo: "Não foi possível contatar a API." };
    }
    if (!res.ok) return { ok: false, motivo: `A API respondeu ${res.status}.` };
    let json: unknown;
    try {
      json = await res.json();
    } catch {
      if (controle.signal.aborted) return { ok: false, motivo: MOTIVO_TIMEOUT };
      return { ok: false, motivo: "Resposta inválida da API (o corpo não é JSON)." };
    }
    const problema = problemaNoPayloadCx(json);
    if (problema) return { ok: false, motivo: `Resposta inválida da API (${problema}).` };
    return { ok: true, dados: json as CxShopeeResponse };
  } finally {
    clearTimeout(timer);
  }
}

/** Fontes que ainda faltam na aba CX, ditas na tela (ausencia nunca vira zero). */
export const PROXIMAS_FONTES: readonly { titulo: string; motivo: string }[] = [
  {
    titulo: "Conversas não respondidas (Shopee)",
    motivo: "A API do chat da Shopee responde (testado em 04/11); falta a coleta diária no Airflow.",
  },
  {
    titulo: "Devoluções com motivo e valor reembolsado (Shopee)",
    motivo: "A API de devoluções responde (testado em 04/11); falta a coleta diária no Airflow.",
  },
  {
    titulo: "Funil do chat (visitantes, CSAT, conversão)",
    motivo: "Só existe no painel da Shopee (Informações Gerenciais → Chat); depende de exportação.",
  },
  {
    titulo: "Mercado Livre e TikTok Shop",
    motivo: "ML: falta a permissão “Comunicação pré e pós-venda” no app. TikTok: falta o escopo Customer Service.",
  },
];
