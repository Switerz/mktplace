/**
 * Contrato e regras de exibicao da tela "Estoque Full" — Gate FULL-SOURCE-3.
 *
 * A REGRA QUE ORGANIZA O ARQUIVO INTEIRO: AUSENCIA NAO E' ZERO
 * -------------------------------------------------------------
 * Um "0" na tela diz "medimos e nao ha estoque" e manda a operacao repor.
 * Um travessao diz "nao sabemos". Sao decisoes opostas, entao os dois nunca
 * podem ser desenhados do mesmo jeito. `fmtOpcional` existe so' para isso, e
 * e' por onde passa TODA coluna anulavel.
 *
 * A TELA NAO RECALCULA O QUE A API ENTREGA
 * -----------------------------------------
 * Classificacao, cobertura e media diaria vem prontas da fato. Aqui so' se
 * formata e rotula. Reclassificar no cliente criaria uma segunda implementacao
 * da mesma regra, e a que o gestor veria seria a errada.
 */

/** Dominio FECHADO, espelhando o CHECK da migration 021. */
export const CLASSIFICACOES = [
  "RUPTURA_CANDIDATA",
  "BAIXO_CANDIDATO",
  "EXCESSO_CANDIDATO",
  "SEM_GIRO_CANDIDATO",
  "SUFICIENTE",
  "SEM_DEMANDA_MEDIDA",
  "KIT_NAO_CONCILIADO",
] as const;

export type Classificacao = (typeof CLASSIFICACOES)[number];

/** O que a operacao precisa olhar HOJE. */
export const EXIGEM_ACAO: readonly Classificacao[] = [
  "RUPTURA_CANDIDATA",
  "BAIXO_CANDIDATO",
];

/**
 * Rotulos em portugues de gente.
 *
 * "CANDIDATA" nao aparece na tela, mas o sufixo `?` e a nota de rodape
 * preservam o que ele significa: e' a NOSSA leitura, com limiar provisorio,
 * nao um alerta oficial da Shopee.
 */
export const CLASS_LABEL: Record<Classificacao, string> = {
  RUPTURA_CANDIDATA: "Ruptura",
  BAIXO_CANDIDATO: "Estoque baixo",
  EXCESSO_CANDIDATO: "Excesso",
  SEM_GIRO_CANDIDATO: "Sem giro",
  SUFICIENTE: "Suficiente",
  SEM_DEMANDA_MEDIDA: "Sem demanda medida",
  KIT_NAO_CONCILIADO: "Kit (não conciliado)",
};

export const CLASS_TOM: Record<Classificacao, string> = {
  RUPTURA_CANDIDATA: "bg-red-50 text-red-700 border-red-200",
  BAIXO_CANDIDATO: "bg-amber-50 text-amber-700 border-amber-200",
  EXCESSO_CANDIDATO: "bg-sky-50 text-sky-700 border-sky-200",
  SEM_GIRO_CANDIDATO: "bg-slate-100 text-slate-600 border-slate-200",
  SUFICIENTE: "bg-emerald-50 text-emerald-700 border-emerald-200",
  SEM_DEMANDA_MEDIDA: "bg-slate-50 text-slate-500 border-slate-200",
  KIT_NAO_CONCILIADO: "bg-violet-50 text-violet-700 border-violet-200",
};

/** O travessao. Uma constante para que nunca vire "0" por descuido. */
export const SEM_DADO = "—";

export interface EstoquePorCD {
  location_id: string;
  full_stock: number;
  /** `null` = a API nao disse. Diferente de `false` = medido nao vendavel. */
  is_saleable: boolean | null;
}

export interface ProdutoEstoque {
  shop_account: string;
  brand: string;
  item_id: string;
  model_id: string;
  item_sku: string | null;
  item_name: string | null;
  item_status: string | null;
  is_kit: boolean;
  full_stock_saleable: number;
  full_stock_total: number;
  location_count: number;
  por_cd: EstoquePorCD[];
  units_sold_28d: number;
  days_with_sales_28d: number;
  avg_daily_units_28d: number | string;
  cobertura_torre_dias: number | string | null;
  classificacao_torre: Classificacao;
  vinculo_vendas: string;
  reserved_stock: number | null;
  seller_stock_total: number | null;
  summary_available_stock: number | null;
  units_sold_28d_legado_com_unpaid: number | null;
  ref_date: string;
}

export interface Indicadores {
  unidades_vendaveis_operacional: number;
  unidades_vendaveis_kits_contexto: number;
  unidades_vendaveis_total: number;
  produtos_ruptura: number;
  produtos_baixo: number;
  produtos_excesso: number;
  produtos_sem_giro: number;
  produtos_suficientes: number;
  produtos_sem_demanda_medida: number;
  produtos_kit_nao_conciliado: number;
  produtos_total: number;
  produtos_exigem_acao: number;
}

export interface Frescor {
  ref_date: string;
  source_captured_at: string | null;
  ingested_at: string | null;
  dias_desde_a_fotografia: number | null;
  load_mode: string;
  no_automation: boolean;
}

export interface LimitesProvisorios {
  cobertura_baixa_dias: number;
  cobertura_excesso_dias: number;
  provisorio: boolean;
  observacao: string;
}

export interface EstoqueFullOk {
  status: "ok";
  marketplace: "shopee";
  scope_label: string;
  indicadores: Indicadores;
  produtos: ProdutoEstoque[];
  total_no_filtro: number;
  truncado: boolean;
  frescor: Frescor;
  limites: LimitesProvisorios;
  contas_cobertas: string[];
  marcas_nao_cobertas: string[];
  limitacoes: string[];
}

export interface EstoqueFullIndisponivel {
  status: "unavailable";
  marketplace: "shopee";
  unavailable_reason: string;
  motivo_tecnico:
    | "feature_flag_desligada"
    | "fato_inexistente"
    | "sem_fotografia_publicada";
  scope_label: string;
  limitacoes?: string[];
}

export type EstoqueFullResponse = EstoqueFullOk | EstoqueFullIndisponivel;

/**
 * Discriminante EXPLICITO.
 *
 * Testar `!payload.produtos?.length` confundiria indisponivel com filtro sem
 * resultado -- e sao telas diferentes: uma diz "nao sabemos", a outra diz
 * "sabemos, e nada casa com o seu filtro".
 */
export function isIndisponivel(
  payload: EstoqueFullResponse,
): payload is EstoqueFullIndisponivel {
  return payload.status === "unavailable";
}

/** Vazio POR FILTRO: dado existe, o filtro e' que nao casou. */
export function isVazioPorFiltro(payload: EstoqueFullResponse): boolean {
  return !isIndisponivel(payload) && payload.total_no_filtro === 0;
}

/** Titulo do painel de indisponibilidade, por causa. */
export const TITULO_INDISPONIVEL: Record<
  EstoqueFullIndisponivel["motivo_tecnico"],
  string
> = {
  feature_flag_desligada: "Tela ainda não habilitada",
  fato_inexistente: "Fotografia de estoque ainda não existe",
  sem_fotografia_publicada: "Nenhuma fotografia publicada ainda",
};

/**
 * O que fazer a seguir, por causa. Sem isto o painel diz "nao ha dado" e
 * deixa o gestor sem acao -- que e' a forma educada de nao informar nada.
 */
export const PROXIMO_PASSO: Record<
  EstoqueFullIndisponivel["motivo_tecnico"],
  string
> = {
  feature_flag_desligada:
    "A ativação é decisão de negócio. Fale com o time de dados quando quiser ligar.",
  fato_inexistente:
    "Depende das migrations 020 e 021 serem aplicadas no banco e de uma primeira carga autorizada.",
  sem_fotografia_publicada:
    "As tabelas já existem; falta executar a carga. Ela é manual: não há agendamento.",
};

// ---------------------------------------------------------------------------
// Formatacao
// ---------------------------------------------------------------------------

/** Inteiro em pt-BR. Zero MEDIDO continua "0", e deve mesmo. */
export function fmtInt(valor: number): string {
  return valor.toLocaleString("pt-BR");
}

/**
 * 🔑 A funcao que impede a confusao central desta tela.
 *
 * `null`/`undefined` viram travessao; `0` continua `0`. Todo campo anulavel
 * da resposta passa por aqui.
 */
export function fmtOpcional(valor: number | null | undefined): string {
  if (valor === null || valor === undefined) return SEM_DADO;
  return fmtInt(valor);
}

/** Uma casa decimal; usada em cobertura e media diaria. */
export function fmtDecimal(valor: number | string | null | undefined): string {
  if (valor === null || valor === undefined || valor === "") return SEM_DADO;
  const n = typeof valor === "string" ? Number(valor) : valor;
  if (!Number.isFinite(n)) return SEM_DADO;
  return n.toLocaleString("pt-BR", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
}

/**
 * Cobertura: `null` significa SEM VENDA NA JANELA, nao zero dia de estoque.
 *
 * Mostrar "0 dias" aqui inverteria completamente a leitura -- 0 dia e'
 * ruptura, e sem venda e' o oposto da urgencia.
 */
export function fmtCobertura(
  dias: number | string | null | undefined,
): string {
  if (dias === null || dias === undefined) return SEM_DADO;
  return fmtDecimal(dias);
}

/** Data ISO (YYYY-MM-DD) em pt-BR, sem passar por `Date` e sem fuso. */
export function fmtData(iso: string | null | undefined): string {
  if (!iso) return SEM_DADO;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return SEM_DADO;
  return `${m[3]}/${m[2]}/${m[1]}`;
}

/**
 * Frase de frescor.
 *
 * A carga e' MANUAL: a frase precisa dizer isso, senao o gestor le a data
 * como "atualiza sozinho todo dia" e confia num numero parado.
 */
export function frescorLabel(f: Frescor): string {
  const dias = f.dias_desde_a_fotografia;
  const base = `Fotografia de ${fmtData(f.ref_date)}`;
  if (dias === null || dias === undefined) return base;
  if (dias <= 0) return `${base} (hoje)`;
  if (dias === 1) return `${base} (ontem)`;
  return `${base} (há ${dias} dias)`;
}

/**
 * A fotografia esta velha o bastante para avisar?
 *
 * Nao existe DAG: 2 dias ja' significa que ninguem rodou a carga, nao que o
 * agendador atrasou.
 */
export const DIAS_ATE_AVISAR = 2;

export function fotografiaEstaVelha(f: Frescor): boolean {
  const dias = f.dias_desde_a_fotografia;
  return dias !== null && dias !== undefined && dias >= DIAS_ATE_AVISAR;
}

/** Nome exibido do produto. SKU e nome podem faltar; o item_id nunca. */
export function nomeExibido(p: ProdutoEstoque): string {
  return p.item_name?.trim() || p.item_sku?.trim() || `Item ${p.item_id}`;
}

export function skuExibido(p: ProdutoEstoque): string {
  return p.item_sku?.trim() || SEM_DADO;
}

// ---------------------------------------------------------------------------
// Query
// ---------------------------------------------------------------------------

export interface FiltrosEstoque {
  brands: string[];
  classificacoes: Classificacao[];
  busca: string;
  somenteAcao: boolean;
}

export const FILTROS_VAZIOS: FiltrosEstoque = {
  brands: [],
  classificacoes: [],
  busca: "",
  somenteAcao: false,
};

/**
 * Monta a query. Lista vazia NAO e' enviada: no backend, ausencia significa
 * "todas as contas da allowlist", e mandar `brands=` vazio seria outra coisa.
 */
export function buildQuery(f: FiltrosEstoque): string {
  const qs = new URLSearchParams();
  f.brands.forEach((b) => qs.append("brands", b));
  f.classificacoes.forEach((c) => qs.append("classificacoes", c));
  const termo = f.busca.trim();
  if (termo) qs.set("busca", termo);
  if (f.somenteAcao) qs.set("somente_acao", "true");
  return qs.toString();
}

export function temFiltroAtivo(f: FiltrosEstoque): boolean {
  return (
    f.brands.length > 0 ||
    f.classificacoes.length > 0 ||
    f.busca.trim() !== "" ||
    f.somenteAcao
  );
}

// ---------------------------------------------------------------------------
// Avisos fixos
// ---------------------------------------------------------------------------

export const AVISO_COBERTURA_TORRE =
  "“Cobertura da Torre” é cálculo nosso: estoque vendável dividido pela média " +
  "diária de vendas pagas de 28 dias. Não reproduz nenhuma fórmula da Shopee.";

export const AVISO_CARGA_MANUAL =
  "A carga desta fotografia é manual: não existe agendamento. A data mostrada " +
  "é a da última execução autorizada.";

export const AVISO_KITS =
  "Kits ficam fora dos indicadores operacionais: a semântica de estoque de kit " +
  "não foi conciliada com o Seller Center.";

export const AVISO_CONTEXTO =
  "“Reservado” e “Disponível (Shopee)” são contexto: não são estoque Full e não " +
  "entram no cálculo de cobertura.";

export const AVISO_ALERTAS_SO_NA_TORRE =
  "Os alertas desta tela existem apenas aqui, na Torre. Nada é enviado ao " +
  "marketplace nem dispara reposição automática.";
