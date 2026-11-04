/**
 * CX-MKT-1 — aba "Atendimento" da Performance das contas
 * (contrato: docs/cx_atendimento_contrato.md).
 *
 * Tipos de `GET /api/v1/performance-contas/atendimento`, a flag da aba, o
 * fetch do servidor e as funcoes puras de apresentacao. Sem React: tudo que
 * decide o que aparece e' testavel com `node --test`.
 *
 * As mesmas regras da "Saude da conta" valem aqui: ausencia nunca vira zero,
 * numero Torre nunca recebe faixa oficial, payload invalido vira erro da aba
 * inteira (nenhum numero parcial).
 */
import {
  CANAIS,
  MOTIVO_TIMEOUT,
  TIMEOUT_API_MS,
  ehObjeto,
  filtrarCanais,
  montarIndicador,
  problemaNaCobertura,
  problemaNoIndicador,
  rotuloCanal,
  type Canal,
  type CoberturaCanal,
  type Indicador,
  type VisaoIndicador,
} from "./performance-contas.ts";

// ---------------------------------------------------------------------------
// Flag (fail-closed), aba e janela
// ---------------------------------------------------------------------------

export const ATENDIMENTO_FLAG = "NEXT_PUBLIC_PERFORMANCE_CONTAS_ATENDIMENTO_ENABLED";

/** So' a string EXATA "true" liga. Acesso LITERAL a `process.env` (Next). */
export function atendimentoHabilitado(env?: Readonly<Record<string, string | undefined>>): boolean {
  if (env) return env.NEXT_PUBLIC_PERFORMANCE_CONTAS_ATENDIMENTO_ENABLED === "true";
  return process.env.NEXT_PUBLIC_PERFORMANCE_CONTAS_ATENDIMENTO_ENABLED === "true";
}

export type Aba = "saude" | "atendimento";

export const ABAS: readonly { valor: Aba; texto: string }[] = [
  { valor: "saude", texto: "Saúde da conta" },
  { valor: "atendimento", texto: "Atendimento" },
];

type ParamBruto = string | string[] | undefined;

function primeiro(v: ParamBruto): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/** `?aba=atendimento` so' vale com a flag ligada; qualquer outro valor = saude. */
export function lerAba(params: Readonly<Record<string, ParamBruto>>, habilitada: boolean): Aba {
  return habilitada && primeiro(params.aba) === "atendimento" ? "atendimento" : "saude";
}

export const JANELAS_DIAS = [7, 30, 90] as const;
export type JanelaDias = (typeof JANELAS_DIAS)[number];
export const JANELA_PADRAO: JanelaDias = 30;

/** `?dias=` fora da allowlist cai na janela padrao. */
export function lerDias(params: Readonly<Record<string, ParamBruto>>): JanelaDias {
  const n = Number(primeiro(params.dias));
  return (JANELAS_DIAS as readonly number[]).includes(n) ? (n as JanelaDias) : JANELA_PADRAO;
}

// ---------------------------------------------------------------------------
// Tipos do contrato da API
// ---------------------------------------------------------------------------

export type Natureza = "comprador" | "reclamacao" | "logistica" | "vendedor" | "plataforma";
export type EstadoBloco = "dado" | "vazio_legitimo" | "sem_fonte" | "erro";

export interface Motivo {
  codigo: string;
  rotulo: string;
  natureza: Natureza;
  quantidade: number;
  pct: number;
  mapeado: boolean;
}

export interface BlocoMotivos {
  titulo: string;
  estado: EstadoBloco;
  total: number | null;
  itens: Motivo[];
  ajuda: string;
  excluidos: number | null;
  rotulo_excluidos: string | null;
  motivo_indisponivel: string | null;
}

export interface LinhaMarca {
  marca: string;
  pedidos: number | null;
  cancelamentos: number | null;
  taxa_cancelamento_pct: number | null;
  cancelamentos_comprador: number | null;
  em_devolucao: number | null;
}

export interface Pendencia {
  codigo: string;
  titulo: string;
  motivo: string;
}

export interface CanalAtendimento {
  canal: Canal;
  cobertura: CoberturaCanal;
  indicadores: Indicador[];
  motivos: BlocoMotivos | null;
  marcas: LinhaMarca[];
  pendencias: Pendencia[];
  atualizado_em: string | null;
}

export interface AtendimentoResponse {
  gerado_em: string;
  janela: { inicio: string; fim: string; dias: number };
  canais: CanalAtendimento[];
}

// ---------------------------------------------------------------------------
// Validacao de forma (recusa o payload inteiro em qualquer item quebrado)
// ---------------------------------------------------------------------------

const NATUREZAS: readonly string[] = ["comprador", "reclamacao", "logistica", "vendedor", "plataforma"];
const ESTADOS_BLOCO: readonly string[] = ["dado", "vazio_legitimo", "sem_fonte", "erro"];
const CAMPOS_MARCA = [
  "pedidos", "cancelamentos", "taxa_cancelamento_pct", "cancelamentos_comprador", "em_devolucao",
] as const;

function numero(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function numeroOuNulo(v: unknown): boolean {
  return v === undefined || v === null || numero(v);
}

function textoOuNulo(v: unknown): boolean {
  return v === undefined || v === null || typeof v === "string";
}

function problemaNosMotivos(b: unknown): string | null {
  if (b === undefined || b === null) return null;
  if (!ehObjeto(b)) return "motivos não é objeto";
  if (typeof b.titulo !== "string" || typeof b.ajuda !== "string") return "motivos sem título";
  if (typeof b.estado !== "string" || !ESTADOS_BLOCO.includes(b.estado)) return "motivos.estado fora do domínio";
  if (!numeroOuNulo(b.total) || !numeroOuNulo(b.excluidos)) return "motivos.total inválido";
  if (!textoOuNulo(b.rotulo_excluidos) || !textoOuNulo(b.motivo_indisponivel)) return "motivos com texto inválido";
  if (!Array.isArray(b.itens)) return "motivos.itens ausente";
  if (b.estado === "dado" && !numero(b.total)) return "motivos 'dado' sem total";
  if (b.estado !== "dado" && (numero(b.total) || b.itens.length > 0)) return "motivos sem dado carregando número";
  for (const m of b.itens) {
    if (!ehObjeto(m)) return "motivo não é objeto";
    if (typeof m.codigo !== "string" || typeof m.rotulo !== "string") return "motivo sem código";
    if (typeof m.natureza !== "string" || !NATUREZAS.includes(m.natureza)) return "motivo.natureza fora do domínio";
    if (!numero(m.quantidade) || !numero(m.pct)) return "motivo com número inválido";
    if (m.mapeado !== undefined && typeof m.mapeado !== "boolean") return "motivo.mapeado inválido";
  }
  return null;
}

export function problemaNoPayloadAtendimento(json: unknown): string | null {
  if (!ehObjeto(json)) return "o corpo não é um objeto";
  if (typeof json.gerado_em !== "string") return "gerado_em ausente";
  const j = json.janela;
  if (!ehObjeto(j) || typeof j.inicio !== "string" || typeof j.fim !== "string" || !numero(j.dias)) {
    return "janela inválida";
  }
  if (!Array.isArray(json.canais)) return "canais ausente";
  for (const c of json.canais) {
    if (!ehObjeto(c)) return "canal não é objeto";
    if (!CANAIS.some((x) => x.valor === c.canal)) return "canal desconhecido";
    const cob = problemaNaCobertura(c.cobertura);
    if (cob) return cob;
    if (!Array.isArray(c.indicadores)) return "indicadores ausente";
    for (const i of c.indicadores) {
      const p = problemaNoIndicador(i);
      if (p) return p;
    }
    const mot = problemaNosMotivos(c.motivos);
    if (mot) return mot;
    if (c.marcas !== undefined && !Array.isArray(c.marcas)) return "marcas inválida";
    for (const m of (c.marcas as unknown[] | undefined) ?? []) {
      if (!ehObjeto(m) || typeof m.marca !== "string") return "linha de marca inválida";
      for (const campo of CAMPOS_MARCA) if (!numeroOuNulo(m[campo])) return `marcas.${campo} inválido`;
    }
    if (c.pendencias !== undefined && !Array.isArray(c.pendencias)) return "pendencias inválida";
    for (const p of (c.pendencias as unknown[] | undefined) ?? []) {
      if (!ehObjeto(p) || typeof p.codigo !== "string" || typeof p.titulo !== "string" || typeof p.motivo !== "string") {
        return "pendência inválida";
      }
    }
    if (!textoOuNulo(c.atualizado_em)) return "atualizado_em inválido";
  }
  return null;
}

// ---------------------------------------------------------------------------
// Montagem da aba
// ---------------------------------------------------------------------------

const FMT_INT = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const FMT_PCT = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** Ausente nunca vira "0": devolve "—" (celula de tabela) ou o numero. */
export function celula(v: number | null | undefined, unidade: "contagem" | "pct" = "contagem"): string {
  if (!numero(v)) return "—";
  return unidade === "pct" ? `${FMT_PCT.format(v)}%` : FMT_INT.format(v);
}

export const ROTULO_NATUREZA: Record<Natureza, string> = {
  comprador: "Comprador",
  reclamacao: "Reclamação",
  logistica: "Logística",
  vendedor: "Vendedor",
  plataforma: "Plataforma",
};

export interface VisaoMotivo {
  chave: string;
  rotulo: string;
  natureza: string;
  quantidade: string;
  pct: string;
  /** Largura da barra, 0-100, relativa ao MAIOR motivo do bloco. */
  largura: number;
  mapeado: boolean;
}

export interface VisaoMotivos {
  titulo: string;
  ajuda: string;
  total: string | null;
  itens: VisaoMotivo[];
  /** Estado sem dado: titulo e descricao para o StateBlock. */
  bloco: { tipo: "vazio" | "indisponivel" | "erro"; titulo: string; descricao: string | null } | null;
  excluidos: string | null;
  naoMapeados: number;
}

export function montarMotivos(b: BlocoMotivos | null | undefined): VisaoMotivos | null {
  if (!b) return null;
  const excluidos = numero(b.excluidos) && b.excluidos > 0
    ? `${FMT_INT.format(b.excluidos)} ${b.rotulo_excluidos ?? "fora da contagem"}`
    : null;
  if (b.estado !== "dado") {
    const tipo = b.estado === "erro" ? "erro" : b.estado === "vazio_legitimo" ? "vazio" : "indisponivel";
    const titulo = b.estado === "erro" ? "Não foi possível carregar os motivos"
      : b.estado === "vazio_legitimo" ? "Nenhum cancelamento no recorte" : "Motivos ainda não publicados";
    return { titulo: b.titulo, ajuda: b.ajuda, total: null, itens: [], excluidos,
             bloco: { tipo, titulo, descricao: b.motivo_indisponivel }, naoMapeados: 0 };
  }
  const maior = Math.max(1, ...b.itens.map((m) => m.quantidade));
  return {
    titulo: b.titulo,
    ajuda: b.ajuda,
    total: numero(b.total) ? FMT_INT.format(b.total) : null,
    itens: b.itens.map((m) => ({
      chave: m.codigo,
      rotulo: m.rotulo,
      natureza: ROTULO_NATUREZA[m.natureza] ?? m.natureza,
      quantidade: FMT_INT.format(m.quantidade),
      pct: `${FMT_PCT.format(m.pct)}%`,
      largura: Math.round((m.quantidade / maior) * 1000) / 10,
      mapeado: m.mapeado !== false,
    })),
    bloco: null,
    excluidos,
    naoMapeados: b.itens.filter((m) => m.mapeado === false).length,
  };
}

export interface ColunaMarca {
  campo: (typeof CAMPOS_MARCA)[number];
  titulo: string;
  unidade: "contagem" | "pct";
}

/** Colunas da tabela por canal: so' o que aquele canal mede. */
export const COLUNAS_POR_CANAL: Record<Canal, ColunaMarca[]> = {
  mercado_livre: [
    { campo: "pedidos", titulo: "Pedidos", unidade: "contagem" },
    { campo: "cancelamentos", titulo: "Cancelamentos", unidade: "contagem" },
    { campo: "taxa_cancelamento_pct", titulo: "Taxa", unidade: "pct" },
  ],
  shopee: [
    { campo: "pedidos", titulo: "Pedidos", unidade: "contagem" },
    { campo: "cancelamentos", titulo: "Cancelamentos", unidade: "contagem" },
    { campo: "taxa_cancelamento_pct", titulo: "Taxa", unidade: "pct" },
    { campo: "cancelamentos_comprador", titulo: "Pelo comprador", unidade: "contagem" },
    { campo: "em_devolucao", titulo: "Em devolução agora", unidade: "contagem" },
  ],
  tiktok: [],
};

export interface VisaoCanalAtendimento {
  canal: Canal;
  titulo: string;
  descricao: string;
  fora: { chave: string; texto: string }[];
  indicadores: VisaoIndicador[];
  motivos: VisaoMotivos | null;
  colunas: ColunaMarca[];
  linhas: { chave: string; marca: string; celulas: string[] }[];
  /** Alguma celula saiu "—" (ausente): a tabela explica que nao e' zero. */
  temAusente: boolean;
  pendencias: Pendencia[];
}

export function montarCanaisAtendimento(
  dados: AtendimentoResponse,
  canal: Canal | null,
  rotuloMarca: (marca: string) => string,
): VisaoCanalAtendimento[] {
  return filtrarCanais(dados.canais as never, canal).map((bruto) => {
    const c = bruto as unknown as CanalAtendimento;
    const marcas = c.cobertura.marcas.map(rotuloMarca).join(", ");
    const colunas = COLUNAS_POR_CANAL[c.canal] ?? [];
    const linhas = colunas.length === 0 ? [] : (c.marcas ?? []).map((m) => ({
      chave: m.marca,
      marca: rotuloMarca(m.marca),
      celulas: colunas.map((col) => celula(m[col.campo], col.unidade)),
    }));
    return {
      canal: c.canal,
      titulo: rotuloCanal(c.canal),
      descricao: marcas ? `Cobertura: ${marcas}` : "Nenhuma marca do filtro tem conta neste canal",
      fora: c.cobertura.fora.map((f) => ({ chave: f.marca, texto: `${rotuloMarca(f.marca)}: ${f.motivo}` })),
      indicadores: c.indicadores.map((ind) => montarIndicador(ind, `cx-${c.canal}`)),
      motivos: montarMotivos(c.motivos),
      colunas,
      linhas,
      temAusente: linhas.some((l) => l.celulas.includes("—")),
      pendencias: c.pendencias ?? [],
    };
  });
}

export function textoJanela(j: AtendimentoResponse["janela"]): string {
  const dm = (iso: string) => {
    const [, mes, dia] = iso.split("-");
    return `${dia}/${mes}`;
  };
  return `${j.dias} dias fechados · ${dm(j.inicio)} a ${dm(j.fim)}`;
}

// ---------------------------------------------------------------------------
// Fetch no servidor
// ---------------------------------------------------------------------------

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export const ROTA_API_ATENDIMENTO = "/api/v1/performance-contas/atendimento";

export function urlAtendimento(base: string, filtros: { marca: string | null; dias: JanelaDias }): string {
  const qs = new URLSearchParams();
  if (filtros.marca) qs.set("brands", filtros.marca);
  qs.set("dias", String(filtros.dias));
  return `${base}${ROTA_API_ATENDIMENTO}?${qs.toString()}`;
}

export type ResultadoAtendimento =
  | { ok: true; dados: AtendimentoResponse }
  | { ok: false; motivo: string };

/** Nunca lanca: qualquer falha volta como `{ ok: false }` e a aba mostra erro. */
export async function carregarAtendimento(
  filtros: { marca: string | null; dias: JanelaDias },
  fetchImpl: typeof fetch = fetch,
  base: string = API_URL,
  timeoutMs: number = TIMEOUT_API_MS,
): Promise<ResultadoAtendimento> {
  const controle = new AbortController();
  const timer = setTimeout(() => controle.abort(), timeoutMs);
  try {
    let res: Response;
    try {
      res = await fetchImpl(urlAtendimento(base, filtros), { cache: "no-store", signal: controle.signal });
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
    const problema = problemaNoPayloadAtendimento(json);
    if (problema) return { ok: false, motivo: `Resposta inválida da API (${problema}).` };
    return { ok: true, dados: json as AtendimentoResponse };
  } finally {
    clearTimeout(timer);
  }
}
