/**
 * Contrato e regras de exibicao da tela "Estoque Full Mercado Livre" —
 * ML-FULL-STOCK-API-1. Espelha `apps/api/app/schemas/ml_full_stock.py`.
 *
 * NAO E' A TELA "Full Mercado Livre" (`/full-ml`): aquela mede MODALIDADE DE
 * ENVIO do pedido. Esta mede ESTOQUE fisico no CD do ML, por INVENTARIO.
 *
 * AUSENCIA NAO E' ZERO
 * --------------------
 * `null` (inventario sem leitura, marca sem fotografia) vira travessao via
 * `fmtOpcional`; `0` medido continua "0". Quando ha' ausencia no escopo, o
 * total e' rotulado PARCIAL com os motivos que a API enviou.
 *
 * GRAO: INVENTARIO
 * ----------------
 * Estoque aparece uma vez por inventario. `anuncios` e' contagem de
 * associacao e nunca multiplica estoque. A tela nao soma nada por anuncio.
 *
 * A TELA NAO RECALCULA classificacao, cobertura, totais nem parcialidade.
 */
import {
  SEM_DADO,
  fmtCobertura,
  fmtData,
  fmtDecimal,
  fmtInt,
  fmtOpcional,
} from "./estoque-full.ts";

export { SEM_DADO, fmtCobertura, fmtData, fmtDecimal, fmtInt, fmtOpcional };

/** Allowlist do backend (CHECK da migration 029). */
export const MARCAS_ML = ["barbours", "kokeshi", "lescent", "rituaria"] as const;

/** Dominio FECHADO, espelhando o CHECK da migration 029. */
export const CLASSIFICACOES_ML = [
  "RUPTURA_CANDIDATA",
  "BAIXO_CANDIDATO",
  "EXCESSO_CANDIDATO",
  "SEM_GIRO_CANDIDATO",
  "SUFICIENTE",
  "SEM_DEMANDA_MEDIDA",
  "SEM_LEITURA",
  "FORA_DO_FULL",
] as const;

export type ClassificacaoML = (typeof CLASSIFICACOES_ML)[number];

export const CLASS_LABEL_ML: Record<ClassificacaoML, string> = {
  RUPTURA_CANDIDATA: "Ruptura",
  BAIXO_CANDIDATO: "Estoque baixo",
  EXCESSO_CANDIDATO: "Excesso",
  SEM_GIRO_CANDIDATO: "Sem giro",
  SUFICIENTE: "Suficiente",
  SEM_DEMANDA_MEDIDA: "Sem demanda medida",
  SEM_LEITURA: "Sem leitura",
  FORA_DO_FULL: "Fora do Full",
};

export const CLASS_TOM_ML: Record<ClassificacaoML, string> = {
  RUPTURA_CANDIDATA: "bg-red-50 text-red-700 border-red-200",
  BAIXO_CANDIDATO: "bg-amber-50 text-amber-700 border-amber-200",
  EXCESSO_CANDIDATO: "bg-sky-50 text-sky-700 border-sky-200",
  SEM_GIRO_CANDIDATO: "bg-slate-100 text-slate-600 border-slate-200",
  SUFICIENTE: "bg-emerald-50 text-emerald-700 border-emerald-200",
  SEM_DEMANDA_MEDIDA: "bg-slate-50 text-slate-500 border-slate-200",
  SEM_LEITURA: "bg-orange-50 text-orange-800 border-orange-300",
  FORA_DO_FULL: "bg-slate-50 text-slate-500 border-slate-200",
};

export interface InventarioML {
  /**
   * Ordinal da linha NESTA resposta (1..n, ordem operacional). A API e'
   * publica: o ID do inventario do ML nao viaja, nem derivado dele.
   */
  posicao: number;
  brand: string;
  read_status: string;
  eh_full: boolean;
  disponivel: number | null;
  nao_disponivel: number | null;
  transfer: number | null;
  total_coerente: boolean | null;
  detalhe_coerente: boolean | null;
  anuncios: number;
  anuncios_ativos: number;
  anuncios_full: number;
  compartilhado: boolean;
  unidades_28d: number;
  media_diaria_28d: number | string;
  cobertura_torre_dias: number | string | null;
  classificacao_torre: ClassificacaoML;
  vinculo_vendas: string;
  ref_date: string;
}

export interface StatusNaoDisponivel {
  status_raw: string;
  quantidade: number;
  inventarios: number;
}

export interface Leitura {
  inventarios: number;
  inventarios_lidos: number;
  inventarios_sem_leitura: number;
}

export interface MarcaML {
  brand: string;
  tem_fotografia_no_dia: boolean;
  ultima_fotografia: string | null;
  published_at: string | null;
  inventarios: number | null;
  inventarios_full_lidos: number | null;
  inventarios_compartilhados: number | null;
  leitura: Leitura | null;
  disponivel_full: number | null;
  nao_disponivel_full: number | null;
  transfer_full: number | null;
  unidades_28d_alocadas: number | null;
  unidades_28d_item_varios_inventarios: number | null;
  unidades_28d_sem_inventory_id: number | null;
  unidades_28d_fora_da_ponte: number | null;
  cobertura_da_alocacao: number | string | null;
  por_classificacao: Partial<Record<ClassificacaoML, number>>;
  nao_disponivel_por_status: StatusNaoDisponivel[];
}

export interface IndicadoresML {
  disponivel_full: number | null;
  nao_disponivel_full: number | null;
  transfer_full: number | null;
  total_parcial: boolean;
  motivos_total_parcial: string[];
  inventarios: number;
  inventarios_full: number;
  inventarios_full_lidos: number;
  inventarios_fora_do_full: number;
  inventarios_compartilhados: number;
  inventarios_lidos: number;
  inventarios_sem_leitura: number;
  inventarios_ruptura: number;
  inventarios_baixo: number;
  inventarios_excesso: number;
  inventarios_sem_giro: number;
  inventarios_suficientes: number;
  inventarios_sem_demanda_medida: number;
  inventarios_exigem_acao: number;
}

export interface FrescorML {
  ref_date: string;
  ultima_fotografia_disponivel: string;
  published_at: string | null;
  dias_desde_a_fotografia: number;
  horas_desde_a_publicacao: number | null;
  publicacao_agendada: boolean;
}

export interface CoberturaML {
  marcas_esperadas: string[];
  marcas_com_fotografia: string[];
  marcas_sem_fotografia: string[];
  leitura: Leitura;
}

export interface MLFullEstoqueOk {
  status: "ok";
  marketplace: "ml";
  scope_label: string;
  frescor: FrescorML;
  cobertura: CoberturaML;
  indicadores: IndicadoresML;
  marcas: MarcaML[];
  inventarios: InventarioML[];
  total_no_filtro: number;
  truncado: boolean;
  incluiu_fora_do_full: boolean;
  limites: {
    cobertura_baixa_dias: number;
    cobertura_excesso_dias: number;
    provisorio: boolean;
    observacao: string;
  };
  limitacoes: string[];
}

export type MotivoIndisponivelML =
  | "feature_flag_desligada"
  | "fato_inexistente"
  | "sem_fotografia_publicada"
  | "sem_fotografia_na_data";

export interface MLFullEstoqueIndisponivel {
  status: "unavailable";
  marketplace: "ml";
  unavailable_reason: string;
  motivo_tecnico: MotivoIndisponivelML;
  scope_label: string;
  limitacoes?: string[];
}

export type MLFullEstoqueResponse = MLFullEstoqueOk | MLFullEstoqueIndisponivel;

/** Discriminante EXPLICITO, pelo status -- nunca pela lista vazia. */
export function isIndisponivelML(
  p: MLFullEstoqueResponse,
): p is MLFullEstoqueIndisponivel {
  return p.status === "unavailable";
}

export const TITULO_INDISPONIVEL_ML: Record<MotivoIndisponivelML, string> = {
  feature_flag_desligada: "Tela ainda não habilitada",
  fato_inexistente: "Estoque Full do Mercado Livre ainda não existe",
  sem_fotografia_publicada: "Nenhuma fotografia publicada ainda",
  sem_fotografia_na_data: "Sem fotografia na data escolhida",
};

export const PROXIMO_PASSO_ML: Record<MotivoIndisponivelML, string> = {
  feature_flag_desligada:
    "A ativação é decisão de negócio. Fale com o time de dados quando quiser ligar.",
  fato_inexistente:
    "Depende da migration 029 ser aplicada no banco e de uma primeira publicação autorizada.",
  sem_fotografia_publicada:
    "As tabelas já existem; falta rodar o publicador `publish_ml_full_stock_daily`.",
  sem_fotografia_na_data:
    "Escolha outra data ou volte para a última fotografia publicada.",
};

// ---------------------------------------------------------------------------
// Frescor e cobertura
// ---------------------------------------------------------------------------

/** A partir de quantos dias a fotografia exibida merece aviso. */
export const DIAS_ATE_AVISAR_ML = 2;

export function frescorLabelML(f: FrescorML): string {
  const dias = f.dias_desde_a_fotografia;
  const base = `Fotografia de ${fmtData(f.ref_date)}`;
  const quando =
    dias <= 0 ? " (hoje)" : dias === 1 ? " (ontem)" : ` (há ${dias} dias)`;
  const pub =
    f.horas_desde_a_publicacao === null
      ? ""
      : ` · publicada há ${fmtDecimal(f.horas_desde_a_publicacao)} h`;
  return `${base}${quando}${pub}`;
}

export function fotografiaVelhaML(f: FrescorML): boolean {
  return f.dias_desde_a_fotografia >= DIAS_ATE_AVISAR_ML;
}

/** Exibe uma data anterior a' ultima publicada? A tela avisa. */
export function eDataHistorica(f: FrescorML): boolean {
  return f.ref_date !== f.ultima_fotografia_disponivel;
}

export function coberturaMarcasLabel(c: CoberturaML): string {
  return (
    `${c.marcas_com_fotografia.length} de ${c.marcas_esperadas.length} ` +
    "marcas com fotografia no dia"
  );
}

export function leituraLabel(l: Leitura): string {
  return (
    `${fmtInt(l.inventarios_lidos)} de ${fmtInt(l.inventarios)} inventários ` +
    `lidos · ${fmtInt(l.inventarios_sem_leitura)} sem leitura`
  );
}

/** Fracao 0..1 (string do NUMERIC ou numero) em %, ou travessao. */
export function fmtFracao(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === "") return SEM_DADO;
  const n = typeof v === "string" ? Number(v) : v;
  if (!Number.isFinite(n)) return SEM_DADO;
  return `${(n * 100).toLocaleString("pt-BR", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}%`;
}

export function tituloMarca(m: string): string {
  return m.charAt(0).toUpperCase() + m.slice(1);
}

/**
 * O motivo PARCIAL vem da API com o slug da marca ("kokeshi"). A tela exibe o
 * rotulo formatado; o texto e' so' apresentado, nunca reinterpretado.
 */
export function motivoParcialLegivel(texto: string): string {
  return texto.replace(
    new RegExp(`\\b(${MARCAS_ML.join("|")})\\b`, "g"),
    (slug) => tituloMarca(slug),
  );
}

/**
 * Aviso de "fotografia velha" so' quando a tela mostra a ULTIMA fotografia.
 * Data historica escolhida de proposito ja tem aviso proprio.
 */
export function mostrarAvisoVelhaML(f: FrescorML): boolean {
  return fotografiaVelhaML(f) && !eDataHistorica(f);
}

/** Subtitulo do card "Nao disponivel Full": ausencia nao vira travessao solto. */
export function subtituloTransferML(transfer: number | null): string {
  return transfer === null
    ? "Transferência interna sem medida"
    : `${fmtInt(transfer)} em transferência interna`;
}

/**
 * Cards de CONTAGEM num total PARCIAL: "0" nao pode ser lido como "nenhum"
 * quando parte do escopo nao foi medida. Mesmo prefixo do card de disponivel.
 */
export const PREFIXO_PARCIAL_ML = "Parcial: ver aviso acima";

export function subvalorContagemML(parcial: boolean, texto: string): string {
  return parcial ? `${PREFIXO_PARCIAL_ML} · ${texto}` : texto;
}

/** Texto do estado vazio: sem filtro ativo, nao fala em "filtros atuais". */
export function textoVazioML(args: {
  refDate: string;
  inventarios: number;
  filtroAtivo: boolean;
}): string {
  const dia = fmtData(args.refDate);
  if (args.filtroAtivo) {
    return (
      `A fotografia de ${dia} tem ${fmtInt(args.inventarios)} inventários; ` +
      "nenhum casa com os filtros atuais."
    );
  }
  if (args.inventarios === 0) {
    return `A fotografia de ${dia} não tem inventários.`;
  }
  return (
    `A fotografia de ${dia} tem ${fmtInt(args.inventarios)} inventários, ` +
    "nenhum no Full. Marque “Listar também inventários fora do Full” para vê-los."
  );
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

/** Hoje (YYYY-MM-DD) em America/Sao_Paulo -- o mesmo relogio da API. */
export function hojeSaoPauloML(agora: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Sao_Paulo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(agora);
}

export const ERRO_DATA_FUTURA_ML = "Data no futuro: escolha até hoje.";

/** Ano minimo plausivel para a fotografia: abaixo disso e' digitacao parcial. */
export const ANO_MINIMO_DATA_ML = 2020;

/**
 * O `<input type="date">` emite valores parciais enquanto se digita o ano
 * ("0002-12-01", "0020-12-01", "0202-12-01"). So' uma data AAAA-MM-DD que
 * existe no calendario e com ano >= ANO_MINIMO_DATA_ML vai para o filtro.
 */
export function dataCompletaML(valor: string): boolean {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(valor);
  if (!m) return false;
  const [ano, mes, dia] = [Number(m[1]), Number(m[2]), Number(m[3])];
  if (ano < ANO_MINIMO_DATA_ML) return false;
  const d = new Date(Date.UTC(ano, mes - 1, dia));
  return (
    d.getUTCFullYear() === ano && d.getUTCMonth() === mes - 1 && d.getUTCDate() === dia
  );
}

/**
 * Valor do campo -> valor do filtro. Vazio limpa (""); data completa aplica;
 * parcial/implausivel devolve `null` = nao mexe no filtro.
 */
export function dataParaFiltroML(valor: string): string | null {
  if (valor === "") return "";
  return dataCompletaML(valor) ? valor : null;
}

/**
 * A API devolve 422 para data futura (D+1 em BRT). Marca fora da allowlist
 * tambem e' 422, mas a tela so' envia marcas da allowlist: com data no
 * filtro, o 422 e' da data. Nao compara com o relogio local, que pode
 * divergir do servidor perto da meia-noite.
 */
export function eErroDataFuturaML(status: number | null, refDate: string): boolean {
  return status === 422 && /^\d{4}-\d{2}-\d{2}$/.test(refDate);
}

// ---------------------------------------------------------------------------
// Query
// ---------------------------------------------------------------------------

export interface FiltrosML {
  brands: string[];
  /** YYYY-MM-DD; vazio = ultima fotografia. */
  refDate: string;
  incluirForaDoFull: boolean;
}

export const FILTROS_ML_VAZIOS: FiltrosML = {
  brands: [],
  refDate: "",
  incluirForaDoFull: false,
};

/** Lista vazia NAO e' enviada: no backend, ausencia = allowlist inteira. */
export function buildQueryML(f: FiltrosML): string {
  const qs = new URLSearchParams();
  f.brands.forEach((b) => qs.append("brands", b));
  if (/^\d{4}-\d{2}-\d{2}$/.test(f.refDate)) qs.set("ref_date", f.refDate);
  if (f.incluirForaDoFull) qs.set("incluir_fora_do_full", "true");
  return qs.toString();
}

export function temFiltroAtivoML(f: FiltrosML): boolean {
  return f.brands.length > 0 || f.refDate !== "" || f.incluirForaDoFull;
}

// ---------------------------------------------------------------------------
// Avisos fixos
// ---------------------------------------------------------------------------

export const AVISO_GRAO_ML =
  "Estoque contado uma vez por inventário. Um inventário pode servir a vários " +
  "anúncios: a coluna “Anúncios” é só associação e nunca soma estoque.";

export const AVISO_NAO_E_MODALIDADE_ML =
  "Esta tela mede estoque físico no Full. A tela “Full Mercado Livre” mede " +
  "modalidade de envio dos pedidos — são números diferentes.";

export const AVISO_AUSENCIA_ML =
  "“—” significa sem leitura ou sem fotografia: não sabemos. Não é estoque zero.";

export const AVISO_TRANSFER_ML =
  "“Transferência” é movimento interno do Mercado Livre, não remessa ao Full.";

export const AVISO_ALERTAS_SO_NA_TORRE_ML =
  "As classificações desta tela existem apenas aqui, na Torre. Nada é enviado " +
  "ao Mercado Livre nem dispara reposição automática.";
