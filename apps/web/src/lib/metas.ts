/**
 * METAS-1 — logica PURA da aba "Metas" (`/metas`).
 *
 * A grade guarda TEXTO digitado (rascunho) e compara com o que esta salvo. Tudo
 * aqui e' testavel com `node --test`, sem React nem rede.
 *
 * "Sem meta" (campo vazio) e "meta zero" sao coisas diferentes, como no banco:
 * vazio vira `null`, nunca 0.
 */

export const MARCAS = [
  { id: "apice", label: "ÁPICE" },
  { id: "barbours", label: "BARBOURS" },
  { id: "kokeshi", label: "KOKESHI" },
  { id: "lescent", label: "LESCENT" },
  { id: "rituaria", label: "RITUÁRIA" },
] as const;

export const CANAIS = [
  { id: "tiktok", label: "TikTok Shop" },
  { id: "ml", label: "Mercado Livre" },
  { id: "shopee", label: "Shopee" },
] as const;

export type MarcaId = (typeof MARCAS)[number]["id"];
export type CanalId = (typeof CANAIS)[number]["id"];

/** Mesmo teto do backend (`META_MAXIMA`). */
export const META_MAXIMA = 9_999_999_999.99;

export interface CelulaMeta {
  brand: MarcaId;
  channel: CanalId;
  meta_gmv: number | null;
}

export interface MetasMes {
  mes: string;
  marcas: string[];
  canais: string[];
  celulas: { brand: string; channel: string; meta_gmv: number }[];
  ultima_alteracao: string | null;
}

export type Chave = `${MarcaId}:${CanalId}`;
export type Valores = Partial<Record<Chave, number | null>>;
export type Rascunho = Partial<Record<Chave, string>>;

export const chave = (brand: MarcaId, channel: CanalId): Chave => `${brand}:${channel}`;

export const TODAS_AS_CHAVES: Chave[] = MARCAS.flatMap((m) => CANAIS.map((c) => chave(m.id, c.id)));

// ---------------------------------------------------------------------------
// Mes
// ---------------------------------------------------------------------------

const RE_MES = /^(\d{4})-(\d{2})$/;
const MES_MINIMO = "2024-01";
const MES_MAXIMO = "2030-12";

/** `AAAA-MM` valido e dentro da faixa aceita pela API, ou `null`. */
export function parseMes(texto: string | null | undefined): string | null {
  const m = RE_MES.exec(texto ?? "");
  if (!m) return null;
  const mes = Number(m[2]);
  if (mes < 1 || mes > 12) return null;
  if (texto! < MES_MINIMO || texto! > MES_MAXIMO) return null;
  return texto!;
}

/** Dia corrente em Brasilia, `AAAA-MM-DD`. */
export function hojeBrt(agora: Date = new Date()): string {
  // en-CA formata como AAAA-MM-DD.
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Sao_Paulo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(agora);
}

/** Mes corrente em Brasilia, `AAAA-MM`. */
export function mesAtualBrt(agora: Date = new Date()): string {
  return hojeBrt(agora).slice(0, 7);
}

export function deslocarMes(mes: string, delta: number): string {
  const [a, m] = mes.split("-").map(Number);
  const total = a * 12 + (m - 1) + delta;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
}

export const podeIrPara = (mes: string): boolean => parseMes(mes) !== null;

const NOMES_MES = [
  "janeiro", "fevereiro", "março", "abril", "maio", "junho",
  "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
];

/** "outubro de 2026" */
export function rotuloMes(mes: string): string {
  const [a, m] = mes.split("-").map(Number);
  return `${NOMES_MES[m - 1]} de ${a}`;
}

/**
 * Janela do realizado do mes: do dia 1 ao ultimo dia, cortada em `hoje` — a
 * API recusa `date_to` futuro com 422. Mes que ainda nao comecou nao tem
 * realizado: `null`.
 */
export function intervaloDoMes(mes: string, hoje: string): { dateFrom: string; dateTo: string } | null {
  const [a, m] = mes.split("-").map(Number);
  const ultimo = new Date(Date.UTC(a, m, 0)).getUTCDate();
  const dateFrom = `${mes}-01`;
  if (dateFrom > hoje) return null;
  const fim = `${mes}-${String(ultimo).padStart(2, "0")}`;
  return { dateFrom, dateTo: fim < hoje ? fim : hoje };
}

// ---------------------------------------------------------------------------
// Valor em reais
// ---------------------------------------------------------------------------

export type Leitura = { ok: true; valor: number | null } | { ok: false; erro: string };

/**
 * Le o que foi digitado. Aceita "1.500.000", "1500000", "1.500.000,50",
 * "1500000,5", "R$ 1.500.000" e "1500000.50". Vazio = sem meta.
 *
 * Ponto sozinho so' e' separador de milhar quando os grupos tem 3 digitos
 * ("1.500" = mil e quinhentos); "1500.5" e' decimal.
 */
export function lerValor(texto: string): Leitura {
  const limpo = texto.replace(/R\$/gi, "").replace(/\s/g, "");
  if (!limpo) return { ok: true, valor: null };
  let normal: string;
  if (limpo.includes(",")) {
    if (!/^\d{1,3}(\.\d{3})*,\d{1,2}$|^\d+,\d{1,2}$/.test(limpo)) return { ok: false, erro: "Valor inválido" };
    normal = limpo.replace(/\./g, "").replace(",", ".");
  } else if (/^\d{1,3}(\.\d{3})+$/.test(limpo)) {
    normal = limpo.replace(/\./g, "");
  } else if (/^\d+(\.\d{1,2})?$/.test(limpo)) {
    normal = limpo;
  } else {
    return { ok: false, erro: "Valor inválido" };
  }
  const valor = Number(normal);
  if (!Number.isFinite(valor)) return { ok: false, erro: "Valor inválido" };
  if (valor > META_MAXIMA) return { ok: false, erro: "Valor alto demais" };
  return { ok: true, valor: Math.round(valor * 100) / 100 };
}

const FMT_CAMPO = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 0, maximumFractionDigits: 2 });

/** Texto do campo para um valor salvo: "1.500.000" ou "1.500.000,5". Vazio para sem meta. */
export function textoDoCampo(valor: number | null | undefined): string {
  return valor == null ? "" : FMT_CAMPO.format(valor);
}

/** "R$ 4,47 mi", "R$ 854 mil", "R$ 0" — leitura rapida de totais e do realizado. */
export function fmtCompacto(valor: number | null | undefined): string {
  if (valor == null) return "—";
  const abs = Math.abs(valor);
  if (abs >= 1_000_000) {
    return `R$ ${(valor / 1_000_000).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} mi`;
  }
  if (abs >= 1_000) return `R$ ${Math.round(valor / 1_000).toLocaleString("pt-BR")} mil`;
  return `R$ ${Math.round(valor).toLocaleString("pt-BR")}`;
}

// ---------------------------------------------------------------------------
// Grade
// ---------------------------------------------------------------------------

/** Resposta da API -> valores salvos por chave (celula ausente = sem meta). */
export function valoresDaResposta(resp: MetasMes): Valores {
  const valores: Valores = {};
  for (const c of resp.celulas) {
    const k = `${c.brand}:${c.channel}` as Chave;
    if (TODAS_AS_CHAVES.includes(k)) valores[k] = c.meta_gmv;
  }
  return valores;
}

export function rascunhoDe(valores: Valores): Rascunho {
  const r: Rascunho = {};
  for (const k of TODAS_AS_CHAVES) r[k] = textoDoCampo(valores[k]);
  return r;
}

export interface Analise {
  /** Celulas digitadas que nao se leem como valor. */
  invalidas: Partial<Record<Chave, string>>;
  /** Celulas validas diferentes do salvo, no formato do PUT. */
  alteradas: CelulaMeta[];
  /** Valor efetivo de cada celula: o digitado quando valido, senao o salvo. */
  efetivos: Valores;
}

export function analisar(salvo: Valores, rascunho: Rascunho): Analise {
  const invalidas: Analise["invalidas"] = {};
  const alteradas: CelulaMeta[] = [];
  const efetivos: Valores = {};
  for (const m of MARCAS) {
    for (const c of CANAIS) {
      const k = chave(m.id, c.id);
      const anterior = salvo[k] ?? null;
      const leitura = lerValor(rascunho[k] ?? "");
      if (!leitura.ok) {
        invalidas[k] = leitura.erro;
        efetivos[k] = anterior;
        continue;
      }
      efetivos[k] = leitura.valor;
      if (leitura.valor !== anterior) alteradas.push({ brand: m.id, channel: c.id, meta_gmv: leitura.valor });
    }
  }
  return { invalidas, alteradas, efetivos };
}

/** Soma que respeita ausencia: so' `null` quando NENHUMA parcela tem valor. */
function somar(valores: (number | null | undefined)[]): number | null {
  const presentes = valores.filter((v): v is number => v != null);
  return presentes.length ? Math.round(presentes.reduce((a, b) => a + b, 0) * 100) / 100 : null;
}

export interface Totais {
  porMarca: Record<MarcaId, number | null>;
  porCanal: Record<CanalId, number | null>;
  global: number | null;
}

export function totais(valores: Valores): Totais {
  const porMarca = Object.fromEntries(
    MARCAS.map((m) => [m.id, somar(CANAIS.map((c) => valores[chave(m.id, c.id)]))]),
  ) as Totais["porMarca"];
  const porCanal = Object.fromEntries(
    CANAIS.map((c) => [c.id, somar(MARCAS.map((m) => valores[chave(m.id, c.id)]))]),
  ) as Totais["porCanal"];
  return { porMarca, porCanal, global: somar(Object.values(porMarca)) };
}

/** Realizado por chave a partir das linhas de `/performance/brands`. */
export function realizadoDasMarcas(
  linhas: { brand: string; tiktok_gmv: number | null; ml_gmv: number | null; shopee_gmv: number | null }[],
): Valores {
  const r: Valores = {};
  for (const l of linhas) {
    if (!MARCAS.some((m) => m.id === l.brand)) continue;
    const b = l.brand as MarcaId;
    r[chave(b, "tiktok")] = l.tiktok_gmv;
    r[chave(b, "ml")] = l.ml_gmv;
    r[chave(b, "shopee")] = l.shopee_gmv;
  }
  return r;
}

/** Instante da ultima alteracao em Brasilia: "29/10/2026 às 10:16". */
export function fmtUltimaAlteracao(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const data = d.toLocaleDateString("pt-BR", { timeZone: "America/Sao_Paulo" });
  const hora = d.toLocaleTimeString("pt-BR", { timeZone: "America/Sao_Paulo", hour: "2-digit", minute: "2-digit" });
  return `${data} às ${hora}`;
}

// ---------------------------------------------------------------------------
// Falhas da API
// ---------------------------------------------------------------------------

export type FalhaMetas = { tipo: "rede" | "limite" | "invalido" | "banco" | "desconhecido"; mensagem: string };

export function classificarFalha(status: number | null, corpo: unknown): FalhaMetas {
  const detalhe =
    corpo && typeof corpo === "object" && typeof (corpo as { detail?: unknown }).detail === "string"
      ? (corpo as { detail: string }).detail
      : null;
  if (status === null) return { tipo: "rede", mensagem: "Sem conexão com a API da Torre." };
  if (status === 429) return { tipo: "limite", mensagem: "Muitas gravações em pouco tempo. Aguarde alguns segundos e tente de novo." };
  if (status === 422) return { tipo: "invalido", mensagem: detalhe ? `A API recusou os valores: ${detalhe}` : "A API recusou os valores enviados." };
  if (status === 503) return { tipo: "banco", mensagem: "O banco de metas está indisponível agora." };
  return { tipo: "desconhecido", mensagem: `A API respondeu com erro ${status}.` };
}

/** Forma minima da resposta de `/api/v1/metas/{mes}`; `null` quando valida. */
export function problemaNaResposta(json: unknown): string | null {
  if (!json || typeof json !== "object") return "a resposta não é um objeto";
  const r = json as Partial<MetasMes>;
  if (typeof r.mes !== "string") return "falta `mes`";
  if (!Array.isArray(r.celulas)) return "falta `celulas`";
  for (const c of r.celulas) {
    if (!c || typeof c.brand !== "string" || typeof c.channel !== "string" || typeof c.meta_gmv !== "number") {
      return "célula malformada";
    }
  }
  return null;
}
