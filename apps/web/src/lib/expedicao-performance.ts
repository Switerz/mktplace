/**
 * CX-MKT-2 — seção "Performance" da Expedição: a nota OFICIAL de cada conta
 * (`GET /api/v1/expedicao/performance?canal=&brands=`).
 *
 * Também guarda a flag que liga a seção e a faixa de LDR do topo. Sem React:
 * validação e montagem testáveis com `node --test`.
 */

/** Liga a faixa de LDR do topo E a seção Performance. Só "true" exato. */
export function performanceHabilitada(env?: Readonly<Record<string, string | undefined>>): boolean {
  if (env) return env.NEXT_PUBLIC_EXPEDICAO_PERFORMANCE_ENABLED === "true";
  return process.env.NEXT_PUBLIC_EXPEDICAO_PERFORMANCE_ENABLED === "true";
}

export type CanalOficial = "mercadolivre" | "shopee" | "tiktokshop";
export type Tom = "bom" | "atencao" | "critico" | "neutro";
export type Situacao = "ok" | "fora" | "sem_meta";
export type Unidade = "pct" | "contagem" | "dias" | "horas" | "segundos" | "nota";

export interface MetricaOficial {
  codigo: string;
  rotulo: string;
  grupo: string | null;
  valor: number | null;
  unidade: Unidade;
  quantidade: number | null;
  anterior: number | null;
  meta: number | null;
  comparador: string | null;
  rotulo_meta: string | null;
  situacao: Situacao;
  ajuda: string | null;
}

export interface ContaOficial {
  marca: string;
  dia: string;
  capturado_em: string | null;
  nivel: string | null;
  tom: Tom;
  termometro: number | null;
  termometro_max: number | null;
  resumo: string | null;
  destaques: { rotulo: string; valor: string; tom: Tom }[];
  metricas: MetricaOficial[];
  avisos: string[];
}

export interface PerformanceOficialResponse {
  gerado_em: string;
  canal: CanalOficial;
  estado: "dado" | "sem_fonte" | "erro";
  motivo: string | null;
  fonte: string;
  cobertura: { marcas: string[]; fora: { marca: string; motivo: string }[] };
  contas: ContaOficial[];
  sem_foto: { marca: string; motivo: string }[];
}

// ---------------------------------------------------------------------------
// Validação (qualquer item quebrado recusa o payload inteiro)
// ---------------------------------------------------------------------------
function obj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
function num(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}
function numOuNulo(v: unknown): boolean {
  return v === undefined || v === null || num(v);
}
function txtOuNulo(v: unknown): boolean {
  return v === undefined || v === null || typeof v === "string";
}
const TONS = ["bom", "atencao", "critico", "neutro"];
const SITUACOES = ["ok", "fora", "sem_meta"];
const UNIDADES = ["pct", "contagem", "dias", "horas", "segundos", "nota"];

export function problemaNaPerformance(json: unknown): string | null {
  if (!obj(json)) return "corpo inválido";
  if (typeof json.gerado_em !== "string" || typeof json.fonte !== "string") return "cabeçalho inválido";
  if (!["mercadolivre", "shopee", "tiktokshop"].includes(json.canal as string)) return "canal desconhecido";
  if (!["dado", "sem_fonte", "erro"].includes(json.estado as string)) return "estado fora do domínio";
  if (!txtOuNulo(json.motivo)) return "motivo inválido";
  const c = json.cobertura;
  if (!obj(c) || !Array.isArray(c.marcas) || !Array.isArray(c.fora)) return "cobertura inválida";
  if (json.estado !== "dado" && Array.isArray(json.contas) && json.contas.length > 0) return "contas sem dado";
  for (const k of (json.contas as unknown[]) ?? []) {
    if (!obj(k) || typeof k.marca !== "string" || typeof k.dia !== "string") return "conta inválida";
    if (!TONS.includes(k.tom as string)) return "tom fora do domínio";
    if (!numOuNulo(k.termometro) || !numOuNulo(k.termometro_max)) return "termômetro inválido";
    if (!txtOuNulo(k.nivel) || !txtOuNulo(k.resumo)) return "texto da conta inválido";
    if (!Array.isArray(k.metricas) || !Array.isArray(k.destaques) || !Array.isArray(k.avisos)) return "listas da conta";
    for (const m of k.metricas) {
      if (!obj(m) || typeof m.codigo !== "string" || typeof m.rotulo !== "string") return "métrica inválida";
      if (!UNIDADES.includes(m.unidade as string) || !SITUACOES.includes(m.situacao as string)) return "métrica fora do domínio";
      for (const campo of ["valor", "quantidade", "anterior", "meta"]) if (!numOuNulo(m[campo])) return `métrica.${campo}`;
    }
    for (const d of k.destaques) {
      if (!obj(d) || typeof d.rotulo !== "string" || typeof d.valor !== "string" || !TONS.includes(d.tom as string)) {
        return "destaque inválido";
      }
    }
  }
  if (json.sem_foto !== undefined && !Array.isArray(json.sem_foto)) return "sem_foto inválido";
  return null;
}

// ---------------------------------------------------------------------------
// Formatação
// ---------------------------------------------------------------------------
const FMT_PCT = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
const FMT_NUM = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const FMT_INT = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });

/** Valor da métrica na unidade da plataforma. Ausente = "—" (nunca "0"). */
export function valorMetrica(m: Pick<MetricaOficial, "valor" | "unidade">): string {
  if (!num(m.valor)) return "—";
  switch (m.unidade) {
    case "pct": return `${FMT_PCT.format(m.valor)}%`;
    case "dias": return `${FMT_NUM.format(m.valor)} dia(s)`;
    case "horas": return `${FMT_NUM.format(m.valor)} h`;
    case "segundos": return m.valor >= 3600 ? `${FMT_NUM.format(m.valor / 3600)} h` : `${FMT_INT.format(Math.round(m.valor / 60))} min`;
    default: return FMT_NUM.format(m.valor);
  }
}

export function quantidadeMetrica(m: Pick<MetricaOficial, "quantidade">): string | null {
  return num(m.quantidade) ? `${FMT_INT.format(m.quantidade)} vendas` : null;
}

export const ROTULO_SITUACAO: Record<Situacao, string | null> = {
  ok: "dentro do limite",
  fora: "fora do limite",
  sem_meta: null,
};

export const ROTULO_OFICIAL: Record<CanalOficial, string> = {
  mercadolivre: "Oficial ML",
  shopee: "Oficial Shopee",
  tiktokshop: "Oficial TikTok",
};

export function urlPerformance(api: string, canal: CanalOficial, marca: string | null): string {
  const qs = new URLSearchParams({ canal });
  if (marca) qs.set("brands", marca);
  return `${api}/api/v1/expedicao/performance?${qs.toString()}`;
}

/** Data `YYYY-MM-DD` -> `DD/MM`. */
export function diaCurto(iso: string): string {
  const [, m, d] = iso.split("-");
  return d && m ? `${d}/${m}` : iso;
}
