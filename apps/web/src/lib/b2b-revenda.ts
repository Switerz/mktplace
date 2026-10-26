/**
 * Aba "B2B / Revendedores" do Monitoramento de Precos — Gate TORRE-B2B-OCTAPRICE.
 *
 * Contrato espelhado de `apps/api/app/schemas/b2b_revenda.py`. Fonte: Torre
 * B2B (relatorio diario do OctaPrice), lida pela NOSSA API — o navegador nunca
 * fala com a Torre B2B.
 *
 * NADA E' CALCULADO AQUI. PMA, violacao, desvio, severidade e cobertura vem
 * prontos da API. Este modulo so' formata e classifica estados.
 *
 * O PISO DESTA ABA E' O PMA DO OCTAPRICE — nao o PDV da visao "Lojas
 * proprias". Os dois nao se misturam nem se comparam.
 *
 * INDISPONIVEL NAO E' ZERO. `status: "unavailable"` vira um bloco
 * "indisponivel" com o motivo; um zero so' aparece se a fonte mediu zero.
 */

// ---------------------------------------------------------------------------
// Tipos (espelho da API)
// ---------------------------------------------------------------------------
export type SituacaoFrescor = "em_dia" | "atrasado";

export type MotivoTecnico =
  | "feature_flag_desligada"
  | "credencial_ausente"
  | "credencial_invalida"
  | "configuracao_invalida"
  | "fonte_inacessivel"
  | "permissao_negada"
  | "contrato_da_fonte_quebrado"
  | "fonte_lenta"
  | "falha_desconhecida"
  | "fonte_sem_dados"
  | "sem_relatorio_na_data";

export interface Frescor {
  data_ref_mais_recente: string;
  coleta_inicio: string | null;
  coleta_fim: string | null;
  importado_inicio: string | null;
  importado_fim: string | null;
  linhas_na_carga: number;
  situacao: SituacaoFrescor;
  data_ref_esperada: string;
  dias_de_atraso: number;
  consultado_em: string;
  datas_disponiveis: string[];
}

export interface CanalExcluido {
  canal: string;
  motivo: string;
}

export interface Escopo {
  piso: "pma_octaprice";
  aviso_piso: string;
  canais_nos_kpis: string[];
  canais_excluidos: CanalExcluido[];
  /** Canais da allowlist ausentes da carga mais recente (grafia ou coleta). */
  canais_kpi_sem_dados: string[];
}

export interface FiltrosAplicados {
  data_ref: string;
  marcas: string[];
  canais: string[];
  severidades: string[];
  skus: string[];
  ufs: string[];
}

interface Base {
  status: "ok";
  data_ref: string;
  consulta_historica: boolean;
  frescor: Frescor;
  escopo: Escopo;
  filtros: FiltrosAplicados;
  limitacoes: string[];
}

export interface Indisponivel {
  status: "unavailable";
  motivo_tecnico: MotivoTecnico;
  unavailable_reason: string;
  frescor: Frescor | null;
  limitacoes: string[];
}

export interface FrescorOk {
  status: "ok";
  frescor: Frescor;
  escopo: Escopo;
  limitacoes: string[];
}

/** Decimais chegam como STRING (Decimal do Pydantic): nada de float na fonte. */
export type Dec = string | null;

export interface Kpis {
  anuncios: number;
  anuncios_violando: number;
  pct_violacao: Dec;
  sellers_violando: number;
  skus_monitorados: number;
  /** FRACAO: "-0.087" = 8,7% abaixo do PMA. */
  desvio_medio_pma: Dec;
  buy_box: { anuncios_com_buy_box: number; violando_com_buy_box: number };
  severidade: { leve: number; media: number; grave: number };
}

export interface CoberturaIdentificacao {
  sellers_violando: number;
  sellers_violando_identificados: number;
  pct_sellers: Dec;
  anuncios_violando: number;
  anuncios_violando_identificados: number;
  pct_anuncios: Dec;
  consistente: boolean;
}

export interface ResumoOk extends Base {
  kpis: Kpis;
  cobertura_identificacao: CoberturaIdentificacao;
}

export interface LinhaMarcaCanal {
  marca_id: string | null;
  marca: string;
  canal: string | null;
  anuncios: number;
  anuncios_violando: number;
  pct_violacao: Dec;
  desvio_medio_pma: Dec;
  preco_medio: Dec;
}

export interface CanaisMarcasOk extends Base {
  linhas: LinhaMarcaCanal[];
}

export interface LinhaSku {
  sku: string;
  marca_id: string | null;
  marca: string;
  exemplo_anuncio: string | null;
  anuncios: number;
  anuncios_violando: number;
  pct_violacao: Dec;
  desvio_medio_pma: Dec;
}

export interface SkusOk extends Base {
  linhas: LinhaSku[];
  limite: number;
}

export type Resposta<T> = T | Indisponivel;

export interface PainelB2B {
  frescor: Resposta<FrescorOk>;
  resumo: Resposta<ResumoOk>;
  canaisMarcas: Resposta<CanaisMarcasOk>;
  skus: Resposta<SkusOk>;
}

// ---------------------------------------------------------------------------
// Filtros
// ---------------------------------------------------------------------------
export const MARCAS: readonly { id: string; nome: string }[] = [
  { id: "AP", nome: "Ápice" },
  { id: "BB", nome: "Barbours" },
  { id: "KS", nome: "Kokeshi" },
];

export const SEVERIDADES: readonly { id: "grave" | "media" | "leve"; nome: string; faixa: string }[] = [
  { id: "grave", nome: "Grave", faixa: "mais de 15% abaixo do PMA" },
  { id: "media", nome: "Média", faixa: "de 5% a 15% abaixo" },
  { id: "leve", nome: "Leve", faixa: "até 5% abaixo" },
];

export interface Filtros {
  /** "" = relatorio mais recente. */
  data: string;
  marca: string;
  canal: string;
  severidade: string;
}

export const FILTROS_VAZIOS: Filtros = { data: "", marca: "", canal: "", severidade: "" };

export function temFiltro(f: Filtros): boolean {
  return Boolean(f.data || f.marca || f.canal || f.severidade);
}

/** Query string das rotas de dado. Vazio = parametro omitido. */
export function buildQuery(f: Filtros, extra: Record<string, string> = {}): string {
  const p = new URLSearchParams();
  if (f.data) p.set("data", f.data);
  if (f.marca) p.append("marcas", f.marca);
  if (f.canal) p.append("canais", f.canal);
  if (f.severidade) p.append("severidades", f.severidade);
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  return p.toString();
}

// ---------------------------------------------------------------------------
// Estados
// ---------------------------------------------------------------------------
export function isIndisponivel<T>(r: Resposta<T> | null | undefined): r is Indisponivel {
  return Boolean(r) && (r as { status?: string }).status === "unavailable";
}

/**
 * "Vazio" so' quando a fonte RESPONDEU e o recorte nao tem anuncio. Nunca
 * para indisponivel — esse e' outro bloco, com outro texto.
 */
export function resumoVazio(r: Resposta<ResumoOk> | null | undefined): boolean {
  return Boolean(r) && !isIndisponivel(r) && (r as ResumoOk).kpis.anuncios === 0;
}

export const TITULO_INDISPONIVEL: Record<MotivoTecnico, string> = {
  feature_flag_desligada: "Aba ainda não habilitada",
  credencial_ausente: "Aguardando a credencial da Torre B2B",
  credencial_invalida: "Credencial da Torre B2B inválida",
  configuracao_invalida: "Configuração da aba inválida",
  fonte_inacessivel: "Torre B2B fora do ar",
  permissao_negada: "Sem permissão na Torre B2B",
  contrato_da_fonte_quebrado: "A Torre B2B mudou o contrato de leitura",
  fonte_lenta: "Torre B2B lenta demais",
  falha_desconhecida: "Falha ao ler a Torre B2B",
  fonte_sem_dados: "Nenhum relatório do OctaPrice carregado",
  sem_relatorio_na_data: "Sem relatório do OctaPrice nesse dia",
};

export function tituloIndisponivel(r: Indisponivel): string {
  return TITULO_INDISPONIVEL[r.motivo_tecnico] ?? TITULO_INDISPONIVEL.falha_desconhecida;
}

export const AVISO_FIXTURE =
  "Dados fictícios. Esta aba está em modo de demonstração e não lê a Torre B2B — nenhum número aqui é real.";

/** A fonte expoe o DIA do relatorio, nao os horarios de coleta e importacao. */
export const SEM_COLETA_IMPORTACAO = "Horários de coleta e importação não expostos pela fonte";

export const AVISO_SEM_CLIENTE =
  "Nomes de cliente, vendedor e seller ficam na Torre B2B até a autenticação da Torre de Marketplaces ser definida. Aqui aparecem só contagens.";

// ---------------------------------------------------------------------------
// Formatacao (pt-BR, sem regra de negocio)
// ---------------------------------------------------------------------------
export const SEM_DADO = "—";
const TZ = "America/Sao_Paulo";

export function fmtInt(n: number | null | undefined): string {
  return n == null ? SEM_DADO : n.toLocaleString("pt-BR");
}

/** "21.7" -> "21,7%". */
export function fmtPct(v: Dec | undefined): string {
  if (v == null) return SEM_DADO;
  const n = Number(v);
  if (!Number.isFinite(n)) return SEM_DADO;
  return `${n.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
}

/**
 * Desvio do PMA vem como FRACAO. "-0.087" -> "−8,7%". O sinal e' o da fonte:
 * negativo = abaixo do PMA. Usa o sinal de menos tipografico.
 */
export function fmtDesvio(v: Dec | undefined): string {
  if (v == null) return SEM_DADO;
  const n = Number(v);
  if (!Number.isFinite(n)) return SEM_DADO;
  const pct = Math.abs(n * 100).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  if (n < 0) return `−${pct}%`;
  if (n > 0) return `+${pct}%`;
  return `${pct}%`;
}

export function fmtMoeda(v: Dec | undefined): string {
  if (v == null) return SEM_DADO;
  const n = Number(v);
  if (!Number.isFinite(n)) return SEM_DADO;
  return n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

/** "2026-10-21" -> "21/10/2026". Data de calendario: sem fuso. */
export function fmtData(iso: string | null | undefined): string {
  if (!iso) return SEM_DADO;
  const [y, m, d] = iso.slice(0, 10).split("-");
  return y && m && d ? `${d}/${m}/${y}` : SEM_DADO;
}

/** Instante em horario de Brasilia, explicito. */
export function fmtInstante(iso: string | null | undefined): string {
  if (!iso) return SEM_DADO;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return SEM_DADO;
  return d.toLocaleString("pt-BR", { timeZone: TZ, dateStyle: "short", timeStyle: "short" });
}

export function situacaoLabel(f: Frescor): string {
  if (f.situacao === "em_dia") return "Em dia";
  return f.dias_de_atraso === 1 ? "Atrasado 1 dia" : `Atrasado ${f.dias_de_atraso} dias`;
}

// ---------------------------------------------------------------------------
// Cartoes que dependem de denominador (nunca "0 de 0")
// ---------------------------------------------------------------------------
/**
 * O cartao de buy box so' existe se ha anuncio COM informacao de buy box no
 * recorte. Medido em 21/10/2026: o OctaPrice so' marca buy box na Amazon, que
 * esta fora dos KPIs — o cartao diria "0 de 0", e isso se le como "nenhum
 * problema". Sem denominador, nao ha medida: o cartao some e volta sozinho
 * quando houver dado aplicavel (ex.: Amazon de volta ao escopo).
 */
export function temBuyBox(k: Kpis): boolean {
  return k.buy_box.anuncios_com_buy_box > 0;
}

/**
 * Texto da cobertura de identificacao. Sem seller violando no recorte nao ha
 * fracao: diz isso em vez de "0 de 0" (e o valor ja' sai "—").
 */
export function textoCobertura(c: CoberturaIdentificacao): { contexto: string; nota: string | undefined } {
  if (!c.consistente) {
    return {
      contexto: `${fmtInt(c.sellers_violando_identificados)} de ${fmtInt(c.sellers_violando)} sellers violando têm cliente B2B`,
      nota: "Contagem diverge do KPI de sellers — confira a fonte",
    };
  }
  if (c.sellers_violando === 0) return { contexto: "Nenhum seller violando neste recorte", nota: undefined };
  return {
    contexto: `${fmtInt(c.sellers_violando_identificados)} de ${fmtInt(c.sellers_violando)} sellers violando têm cliente B2B`,
    nota: c.anuncios_violando > 0 ? `${fmtPct(c.pct_anuncios)} dos anúncios violando` : undefined,
  };
}

/**
 * Grade dos KPIs sem buraco. Com 6 cartoes: 2 / 3 / 6 colunas. Com 5: 2
 * colunas (o ultimo ocupa a linha inteira) e 5 colunas a partir de xl.
 */
export function classesGradeKpis(n: number): string {
  const base = "grid grid-cols-1 gap-3 sm:grid-cols-2";
  if (n === 6) return `${base} xl:grid-cols-3 2xl:grid-cols-6`;
  if (n === 5) return `${base} xl:grid-cols-5 sm:[&>*:last-child]:col-span-2 xl:[&>*:last-child]:col-span-1`;
  return `${base} xl:grid-cols-3`;
}

/** Participacao de uma severidade no total violando, para a barra. */
export function participacao(parte: number, total: number): number {
  if (total <= 0) return 0;
  return Math.round((parte / total) * 1000) / 10;
}

// ---------------------------------------------------------------------------
// Carga
// ---------------------------------------------------------------------------
export class B2BRevendaError extends Error {
  readonly status: number | null;
  constructor(message: string, status: number | null) {
    super(message);
    this.name = "B2BRevendaError";
    this.status = status;
  }
}

export const MSG_SEM_ACESSO = "Acesso restrito: esta aba exige login corporativo.";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";
const BASE = "/api/v1/b2b-revenda";

async function obter<T>(caminho: string, query: string, signal?: AbortSignal): Promise<Resposta<T>> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${BASE}${caminho}${query ? `?${query}` : ""}`, { signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new B2BRevendaError("Não foi possível contatar a API.", null);
  }
  if (res.status === 422) {
    throw new B2BRevendaError("Filtro recusado pela API.", 422);
  }
  if (res.status === 401 || res.status === 403) {
    // A API exige login corporativo (Auth0) nesta rota. Sem sessao, o erro e'
    // de ACESSO, nao de dado — e nunca vira zero nem "indisponivel".
    throw new B2BRevendaError(MSG_SEM_ACESSO, res.status);
  }
  if (!res.ok) {
    throw new B2BRevendaError(`A API respondeu ${res.status}.`, res.status);
  }
  return (await res.json()) as Resposta<T>;
}

/**
 * As quatro leituras da aba. Erro de transporte REJEITA (vira bloco "erro"
 * com nova tentativa); `unavailable` RESOLVE (vira bloco "indisponivel").
 */
export async function carregarPainel(f: Filtros, signal?: AbortSignal): Promise<PainelB2B> {
  const q = buildQuery(f);
  const [frescor, resumo, canaisMarcas, skus] = await Promise.all([
    obter<FrescorOk>("/frescor", "", signal),
    obter<ResumoOk>("/resumo", q, signal),
    obter<CanaisMarcasOk>("/canais-marcas", q, signal),
    obter<SkusOk>("/skus", buildQuery(f, { limite: "10" }), signal),
  ]);
  return { frescor, resumo, canaisMarcas, skus };
}
