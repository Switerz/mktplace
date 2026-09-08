/**
 * Gate AVH-4B-S Task 2/2 — contrato e apresentacao do snapshot manual da Avoe.
 *
 * SEM DEPENDENCIAS, de proposito: o type-stripping nativo do Node (usado por
 * `node --test`) nao resolve import sem extensao, e `api-client.ts` importa
 * `./mock-data` e outros helpers. Com o contrato isolado, tipos e formatadores
 * ficam testaveis sem carregar a arvore inteira do cliente. `api-client.ts`
 * reexporta tudo daqui.
 *
 * A UI SO APRESENTA
 * -----------------
 * Nenhuma regra de negocio do backend e' reimplementada aqui. Este modulo tem
 * tipos, textos, formatadores e filtro client-side — nada mais. Nao existe
 * funcao de soma, total, atingimento, margem, variacao ou comparacao, e ha
 * teste que falha se alguma aparecer.
 *
 * O QUE A TELA E' OBRIGADA A DIZER
 * --------------------------------
 * Fonte externa, carga manual, sem automacao, `reported_amount` com definicao
 * nao confirmada, BRL assumida, e ausencia de realizado/atingimento/margem.
 * Os textos ficam em constantes exportadas para que o teste possa exigi-los.
 */

// ---------------------------------------------------------------------------
// Tipos — FIEIS ao schema Python de `apps/api/app/schemas/avoe_snapshot.py`,
// conferidos contra o OpenAPI local em 2026-09-08.
// ---------------------------------------------------------------------------

export type AvoeSnapshotStatus = "available" | "unavailable";

export type AvoeSourceKind = "external_manual_snapshot";

/** Unico metodo hoje: a auditoria nao tem coluna de ligacao com a captura. */
export type AvoeSyncRunLinkMethod = "audit_time_window";

export type AvoeUnavailableReason =
  | "no_snapshot_published"
  | "targets_and_channels_capture_mismatch"
  | "capture_incomplete"
  | "capture_mixes_multiple_imports"
  | "duplicate_grain_in_capture"
  | "audit_run_not_conclusive"
  | "serving_inconsistent";

export type AvoeCurrencyStatus = "confirmed" | "assumed_unconfirmed";
export type AvoeDefinitionStatus = "unconfirmed";
export type AvoeCoverageStatus = "partial_month" | "full_month";

export interface AvoeSnapshotMeta {
  status: AvoeSnapshotStatus;
  source: "avoe_hub";
  source_kind: AvoeSourceKind;
  is_official_torre_source: false;
  captured_at: string | null;
  snapshot_id: string | null;
  sync_run_id: number | null;
  sync_run_status: string | null;
  sync_run_link_method: AvoeSyncRunLinkMethod | null;
  targets_count: number;
  channel_rows_count: number;
  target_ref_months: string[];
  channel_ref_months: string[];
  currency: string | null;
  currency_status: AvoeCurrencyStatus | null;
  refreshed_at: string;
  captured_age_days: number | null;
  unavailable_reason: AvoeUnavailableReason | null;
  warnings: string[];
}

export interface AvoeTargetRow {
  brand: string;
  brand_key: string | null;
  ref_month: string;
  target_amount: number;
  currency_code: string;
  currency_status: AvoeCurrencyStatus;
  currency_warning: string | null;
  source_recorded_at: string | null;
}

export interface AvoeExtraChannelRow {
  brand: string;
  brand_key: string | null;
  channel: string;
  channel_source_label: string;
  ref_month: string;
  /** `null` = a Avoe nao informou. `0` = a Avoe informou zero. Nunca se troca. */
  reported_amount: number | null;
  is_proxy: boolean;
  definition_status: AvoeDefinitionStatus;
  definition_warning: string;
  currency_code: string;
  currency_status: AvoeCurrencyStatus;
  days_covered: number;
  first_business_date: string;
  last_business_date: string;
  coverage_status: AvoeCoverageStatus;
  source_recorded_at: string | null;
}

export interface AvoeSnapshotLimitations {
  manual_snapshot: boolean;
  automated_refresh: boolean;
  channel_amount_definition_confirmed: boolean;
  currency_confirmed: boolean;
  provides_realized_amount: boolean;
  provides_attainment_or_margin: boolean;
  replaces_canonical_torre_kpi: boolean;
  notes: string[];
}

export interface AvoeSnapshotResponse {
  meta: AvoeSnapshotMeta;
  targets: AvoeTargetRow[];
  extra_channels: AvoeExtraChannelRow[];
  limitations: AvoeSnapshotLimitations;
}

export class AvoeSnapshotError extends Error {
  readonly httpStatus: number | null;

  constructor(message: string, httpStatus: number | null) {
    super(message);
    this.name = "AvoeSnapshotError";
    this.httpStatus = httpStatus;
  }
}

// ---------------------------------------------------------------------------
// Textos obrigatorios
// ---------------------------------------------------------------------------

export const PAGE_TITLE = "Referências externas — Avoe Hub";
export const NAV_LABEL = "Referências externas";
export const ROUTE = "/referencias-externas/avoe";

/** Selo permanente do topo. Nao e' dismissivel e nao entra em acordeao. */
export const SELO_FONTE_EXTERNA = "Fonte externa e manual";

export const COPY = {
  /** Subtitulo da pagina. */
  subtitulo:
    "Último snapshot do Avoe Hub, sistema de terceiro. Consulta somente leitura.",
  /** Aviso permanente nº 1 — natureza da fonte. */
  naoEhKpi:
    "Estes números NÃO são KPIs oficiais da Torre. Vêm de exportação manual do " +
    "Avoe Hub e não substituem, complementam nem alimentam nenhuma métrica " +
    "canônica. Não aparecem em Canais nem na Gerencial.",
  /** Aviso permanente nº 2 — ausencia de automacao. */
  semAutomacao:
    "Sem automação: nenhum agendamento atualiza esta fonte. A próxima captura " +
    "depende de alguém exportar o snapshot e rodar o importador manualmente.",
  /** Aviso permanente nº 3 — definicao do valor de canal. */
  definicaoNaoConfirmada:
    "O valor informado pela Avoe tem definição NÃO confirmada: a origem não " +
    "documentou se é bruto, líquido, com ou sem frete, nem se exclui " +
    "cancelados e devoluções.",
  /** Aviso permanente nº 4 — moeda. */
  moedaAssumida:
    "Moeda ASSUMIDA como BRL. A origem não declara moeda em campo, rótulo ou " +
    "tooltip; a inferência é do contrato, não da fonte.",
  /** Aviso permanente nº 5 — o que a fonte nao tem. */
  semRealizado:
    "Esta fonte não traz realizado. Não há atingimento, margem, variação nem " +
    "comparação com o oficial: cruzar meta com realizado exige um contrato " +
    "próprio, que não existe.",
  /** Nota do vinculo temporal com a auditoria. */
  vinculoTemporal:
    "O identificador da execução foi associado à captura por janela de tempo " +
    "da auditoria, não por chave: a tabela de auditoria não tem coluna de " +
    "captura. Proveniência, não métrica.",
  /** Nota de cobertura documentada no runbook (§6). */
  coberturaDenavitaGocase:
    "Denavita e GoCase têm meta no Avoe e não existem na fato oficial da " +
    "Torre. Permanecem aqui como referência externa, e o atingimento delas é " +
    "não calculável — nunca zero.",
  coberturaApice:
    "Ápice não tem Mercado Livre na fato oficial, então qualquer leitura do " +
    "realizado dela seria piso, não total. Nenhuma leitura de realizado é " +
    "feita nesta página.",
  /** Rotulo obrigatorio da coluna de valor de canal. */
  rotuloValorCanal: "Valor informado pela Avoe",
  /** Estado vazio depois de filtro. */
  filtroSemResultado: "Nenhuma linha para os filtros escolhidos.",
  /** Estado de carregamento. */
  carregando: "Carregando o último snapshot…",
  /** Erro de rede/servidor. */
  erro:
    "Não foi possível carregar o snapshot da Avoe. A consulta é somente " +
    "leitura e nada foi alterado. Tente novamente ou acione o time de dados.",
  /** Captura antiga. */
  capturaAntiga:
    "Captura antiga. Como não há automação, o snapshot só avança com uma " +
    "exportação manual nova.",
} as const;

/** Ausencia. NUNCA substituido por zero, em nenhuma coluna. */
export const NAO_INFORMADO = "Não informado";

/**
 * A partir de quantos dias a captura e' rotulada como antiga.
 *
 * E' um limiar de EXIBICAO, escolhido para a cadencia manual desta fonte — nao
 * e' regra de negocio nem vem do backend. Serve para que uma captura esquecida
 * apareca como esquecida em vez de passar por atual.
 */
export const DIAS_PARA_CAPTURA_ANTIGA = 30;

const MOTIVO_INDISPONIVEL: Record<AvoeUnavailableReason, string> = {
  no_snapshot_published:
    "Nenhuma captura publicada ainda. O importador não rodou, ou rodou e nada foi confirmado.",
  targets_and_channels_capture_mismatch:
    "Publicação incompleta: a captura mais recente existe em apenas uma das duas tabelas.",
  capture_incomplete: "Captura incompleta na origem do serving.",
  capture_mixes_multiple_imports:
    "A captura mistura mais de uma importação. Nada é servido enquanto a origem estiver ambígua.",
  duplicate_grain_in_capture:
    "Há duplicidade no grão da captura. Nada é servido enquanto a chave não for única.",
  audit_run_not_conclusive:
    "A execução que publicou a captura não está conclusiva na auditoria — pode estar em andamento, falhada ou indeterminada. Nada é servido nesse estado.",
  serving_inconsistent:
    "Inconsistência na camada de serving. Acione o time de dados.",
};

export function unavailableLabel(reason: AvoeUnavailableReason | null): string {
  if (reason === null) return "Motivo não informado pela API.";
  return MOTIVO_INDISPONIVEL[reason] ?? "Motivo não reconhecido por esta versão da tela.";
}

const ROTULO_CANAL: Record<string, string> = {
  magalu: "Magalu",
  shein: "SHEIN",
  kwai: "Kwai",
  beleza_na_web: "Beleza na Web",
  rd_marketplace: "RD Marketplace",
  amazon: "Amazon",
};

/** Canal desconhecido volta cru, para nao esconder dado novo da origem. */
export function channelLabel(channel: string): string {
  return ROTULO_CANAL[channel] ?? channel;
}

const ROTULO_COBERTURA: Record<AvoeCoverageStatus, string> = {
  partial_month: "Mês parcial",
  full_month: "Mês completo",
};

export function coverageLabel(status: AvoeCoverageStatus): string {
  return ROTULO_COBERTURA[status] ?? status;
}

export function currencyStatusLabel(status: AvoeCurrencyStatus | null): string {
  if (status === "confirmed") return "confirmada pela origem";
  if (status === "assumed_unconfirmed") return "assumida (BRL), não confirmada";
  return NAO_INFORMADO;
}

// ---------------------------------------------------------------------------
// Formatadores
// ---------------------------------------------------------------------------

/**
 * BRL com centavos, SEM abreviacao em K/M.
 *
 * `fmtBrl` do projeto abrevia acima de mil, e aqui isso apagaria o centavo que
 * a reconciliacao com o snapshot usa. Meta e valor informado sao exibidos
 * integrais.
 */
export function formatBrlExato(value: number): string {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

/**
 * O formatador que separa AUSENCIA de ZERO.
 *
 * `null` -> "Não informado". `0` -> "R$ 0,00". Os dois nunca se confundem, e
 * nenhum caminho converte um no outro.
 */
export function formatValorInformado(value: number | null): string {
  if (value === null || value === undefined) return NAO_INFORMADO;
  return formatBrlExato(value);
}

const MESES = [
  "jan", "fev", "mar", "abr", "mai", "jun",
  "jul", "ago", "set", "out", "nov", "dez",
];

/** `2026-08-01` -> `ago/2026`. Sem `Date`, para nao sofrer fuso. */
export function formatCompetencia(iso: string): string {
  const m = /^(\d{4})-(\d{2})-\d{2}$/.exec(iso);
  if (!m) return iso;
  const mes = MESES[Number(m[2]) - 1];
  return mes ? `${mes}/${m[1]}` : iso;
}

/** `2026-08-01` -> `01/08/2026`. Sem `Date`, para nao sofrer fuso. */
export function formatData(iso: string | null): string {
  if (!iso) return NAO_INFORMADO;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  return `${m[3]}/${m[2]}/${m[1]}`;
}

/** Timestamp ISO -> `01/09/2026 12:32` em America/Sao_Paulo. */
export function formatInstante(iso: string | null): string {
  if (!iso) return NAO_INFORMADO;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: "America/Sao_Paulo",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

export function formatIdade(dias: number | null): string {
  if (dias === null || dias === undefined) return NAO_INFORMADO;
  if (dias === 0) return "hoje";
  if (dias === 1) return "1 dia";
  return `${dias.toLocaleString("pt-BR")} dias`;
}

export function formatContagem(n: number): string {
  return n.toLocaleString("pt-BR");
}

export function isCapturaAntiga(dias: number | null): boolean {
  return dias !== null && dias !== undefined && dias >= DIAS_PARA_CAPTURA_ANTIGA;
}

// ---------------------------------------------------------------------------
// Fases da tela
// ---------------------------------------------------------------------------

export type AvoePhase = "loading" | "available" | "unavailable" | "error";

/**
 * A fase deriva do estado da requisicao, e `unavailable` vem do PROPRIO
 * contrato — nao de lista vazia. Uma resposta 200 com `available` e arrays
 * vazios seria dado real vazio, nao indisponibilidade, e continua `available`.
 */
export function resolvePhase(args: {
  loading: boolean;
  error: boolean;
  data: AvoeSnapshotResponse | null;
}): AvoePhase {
  if (args.error) return "error";
  if (args.loading || args.data === null) return "loading";
  return args.data.meta.status === "available" ? "available" : "unavailable";
}

// ---------------------------------------------------------------------------
// Proveniencia
// ---------------------------------------------------------------------------

export interface ProvenanceItem {
  label: string;
  value: string;
  /** Texto auxiliar exibido sob o valor, quando houver o que declarar. */
  note?: string;
}

/**
 * Os itens do bloco de proveniencia, na ordem de exibicao.
 *
 * `snapshot_id` e `sync_run_id` entram como PROVENIENCIA, nunca como metrica:
 * ficam neste bloco e nao viram cartao de numero.
 */
export function buildProvenance(meta: AvoeSnapshotMeta): ProvenanceItem[] {
  return [
    { label: "Fonte", value: "Avoe Hub", note: SELO_FONTE_EXTERNA },
    {
      label: "Captura em",
      value: formatInstante(meta.captured_at),
      note: `Idade: ${formatIdade(meta.captured_age_days)}`,
    },
    {
      label: "Execução da importação",
      value: meta.sync_run_id === null ? NAO_INFORMADO : `#${meta.sync_run_id}`,
      note:
        meta.sync_run_status === null
          ? undefined
          : `Auditoria: ${meta.sync_run_status}`,
    },
    {
      label: "Vínculo com a auditoria",
      value:
        meta.sync_run_link_method === "audit_time_window"
          ? "janela de tempo"
          : NAO_INFORMADO,
      note: COPY.vinculoTemporal,
    },
    {
      label: "Identificador do snapshot",
      value: meta.snapshot_id ?? NAO_INFORMADO,
      note: "Hash de conteúdo dos arquivos da captura.",
    },
    { label: "Metas na captura", value: formatContagem(meta.targets_count) },
    {
      label: "Linhas de canal na captura",
      value: formatContagem(meta.channel_rows_count),
    },
    {
      label: "Moeda",
      value: meta.currency ?? NAO_INFORMADO,
      note: currencyStatusLabel(meta.currency_status),
    },
    {
      label: "Consulta feita em",
      value: formatInstante(meta.refreshed_at),
      note: "Instante desta leitura, não da captura.",
    },
  ];
}

/**
 * Avisos a renderizar, na ordem, SEM colapsar.
 *
 * Comeca pelos textos fixos que a tela e' obrigada a dizer e depois acrescenta
 * os `warnings` da API que ainda nao estejam cobertos. Nada e' descartado: um
 * aviso novo do backend aparece mesmo que esta versao da tela nao o conheca.
 */
export function buildWarnings(data: AvoeSnapshotResponse): string[] {
  const fixos = [COPY.naoEhKpi, COPY.semAutomacao];
  if (data.meta.status === "available") {
    fixos.push(COPY.definicaoNaoConfirmada);
    if (data.meta.currency_status !== "confirmed") fixos.push(COPY.moedaAssumida);
    fixos.push(COPY.semRealizado);
  }
  if (isCapturaAntiga(data.meta.captured_age_days)) fixos.push(COPY.capturaAntiga);

  const vistos = new Set(fixos.map(normaliza));
  const daApi = [...data.meta.warnings, ...data.limitations.notes].filter((a) => {
    const chave = normaliza(a);
    if (vistos.has(chave)) return false;
    vistos.add(chave);
    return true;
  });
  return [...fixos, ...daApi];
}

function normaliza(texto: string): string {
  return texto
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** Notas de cobertura documentadas no runbook. Texto fixo, zero cálculo. */
export function coverageNotes(): string[] {
  return [COPY.coberturaDenavitaGocase, COPY.coberturaApice];
}

// ---------------------------------------------------------------------------
// Filtros client-side
// ---------------------------------------------------------------------------

export const TODOS = "__todos__";

export interface FilterOption {
  value: string;
  label: string;
}

function opcoes(valores: string[], rotulo: (v: string) => string): FilterOption[] {
  const unicos = [...new Set(valores)].sort();
  return [
    { value: TODOS, label: "Todas" },
    ...unicos.map((v) => ({ value: v, label: rotulo(v) })),
  ];
}

export function competenciaOptions(refMonths: string[]): FilterOption[] {
  const unicos = [...new Set(refMonths)].sort().reverse();
  return [
    { value: TODOS, label: "Todas" },
    ...unicos.map((v) => ({ value: v, label: formatCompetencia(v) })),
  ];
}

export function marcaOptions(brands: string[]): FilterOption[] {
  return opcoes(brands, (v) => v);
}

export function canalOptions(channels: string[]): FilterOption[] {
  return opcoes(channels, channelLabel);
}

export interface AvoeFilters {
  competencia: string;
  marca: string;
  /** Aplicado SOMENTE na tabela de canais. */
  canal: string;
}

export const FILTROS_VAZIOS: AvoeFilters = {
  competencia: TODOS,
  marca: TODOS,
  canal: TODOS,
};

/**
 * Filtra as metas. Puro: devolve um subconjunto das linhas recebidas, sem
 * agregar, somar nem reordenar por valor.
 */
export function filterTargets(
  rows: AvoeTargetRow[],
  f: AvoeFilters,
): AvoeTargetRow[] {
  return rows.filter(
    (r) =>
      (f.competencia === TODOS || r.ref_month === f.competencia) &&
      (f.marca === TODOS || r.brand === f.marca),
  );
}

/** Filtra os canais. `canal` só existe aqui. */
export function filterChannels(
  rows: AvoeExtraChannelRow[],
  f: AvoeFilters,
): AvoeExtraChannelRow[] {
  return rows.filter(
    (r) =>
      (f.competencia === TODOS || r.ref_month === f.competencia) &&
      (f.marca === TODOS || r.brand === f.marca) &&
      (f.canal === TODOS || r.channel === f.canal),
  );
}

/** Resumo textual do filtro ativo, para leitor de tela e para o cabecalho. */
export function describeFilters(f: AvoeFilters, comCanal: boolean): string {
  const partes: string[] = [];
  if (f.competencia !== TODOS) partes.push(`competência ${formatCompetencia(f.competencia)}`);
  if (f.marca !== TODOS) partes.push(`marca ${f.marca}`);
  if (comCanal && f.canal !== TODOS) partes.push(`canal ${channelLabel(f.canal)}`);
  return partes.length === 0 ? "sem filtro" : partes.join(", ");
}
