/**
 * Contrato e regras puras da tela "Full Mercado Livre" — Gate FULL-1D.
 *
 * Esta superficie mede MODALIDADE LOGISTICA do envio, nao posicao de estoque.
 * "Full" e' `logistic_type = 'fulfillment'` no Mercado Livre. Nao ha aqui
 * disponibilidade, cobertura em dias nem ruptura, e a fato por tras
 * (`marts.fact_ml_fulfillment_daily`) tambem nao tem -- o gate FULL-0R mediu
 * que o campo de estoque anunciado do ML nao se comporta como estoque fisico.
 *
 * DINHEIRO CHEGA COMO STRING, E ISSO NAO E' DETALHE
 * --------------------------------------------------
 * O backend serializa `Decimal` como STRING (`"4526767.38"`), nao numero.
 * Tipar como `number` compilaria, passaria no lint e produziria `NaN` ou
 * concatenacao silenciosa em producao. Por isso os campos monetarios sao
 * `string` no tipo e so' viram numero por `parseGmv`, num unico lugar.
 *
 * NADA E' RECALCULADO AQUI
 * -------------------------
 * Shares, taxas de cancelamento e medias de tempo vem prontos da API. Este
 * modulo formata e rotula; nao deriva grandeza que o backend ja entrega. Se um
 * numero da tela divergir do SQL, a causa esta no backend, nao aqui.
 */

// ---------------------------------------------------------------------------
// Tipos — espelham exatamente apps/api/app/schemas/ml_fulfillment.py
// ---------------------------------------------------------------------------

export type FulfillmentClass = "full" | "non_full" | "unknown";
export type FreshnessStatus = "fresh" | "stale" | "never_loaded" | "unknown";
export type LoadMode = "manual_snapshot" | "scheduled";
export type ListingClass = "mixed" | "non_full_only";

export interface TimeStat {
  seconds_avg: number | null;
  hours_avg: number | null;
  days_avg: number | null;
  sample_count: number;
}

export interface ModalBreakdown {
  fulfillment_class: FulfillmentClass;
  /** Decimal serializado como string pelo backend. */
  paid_gmv: string;
  paid_orders: number;
  paid_units: number;
  eligible_orders: number;
  cancelled_orders: number;
  other_orders: number;
  cancellation_rate: number | null;
  handling: TimeStat;
  delivery: TimeStat;
}

export interface LogisticTypeBreakdown {
  logistic_type_original: string;
  fulfillment_class: FulfillmentClass;
  paid_gmv: string;
  paid_orders: number;
  paid_units: number;
}

export interface DailyPoint {
  ref_date: string;
  fulfillment_class: FulfillmentClass;
  paid_gmv: string;
  paid_orders: number;
  paid_units: number;
  eligible_orders: number;
  cancelled_orders: number;
}

export interface ListingClassification {
  full_only: number;
  mixed: number;
  non_full_only: number;
  total: number;
}

export interface MigrationOpportunity {
  item_id: string;
  brand: string;
  non_full_gmv: string;
  non_full_units: number;
  full_gmv: string;
  listing_class: ListingClass;
}

export interface MLFulfillmentQuality {
  unmatched_orders: number;
  missing_shipping_items: number;
  unknown_logistic_type_orders: number;
  is_complete: boolean;
  limitations: string[];
}

export interface MLFulfillmentFreshness {
  freshness_status: FreshnessStatus;
  refreshed_at: string | null;
  source_max_date: string | null;
  age_hours: number | null;
  load_mode: LoadMode;
}

export interface MLFulfillmentResponse {
  date_from: string;
  date_to: string;
  brands: string[];
  share_full_gmv: number | null;
  share_full_orders: number | null;
  share_full_units: number | null;
  paid_gmv_total: string;
  paid_orders_total: number;
  paid_units_total: number;
  by_class: ModalBreakdown[];
  by_logistic_type: LogisticTypeBreakdown[];
  daily: DailyPoint[];
  listings: ListingClassification;
  migration_opportunities: MigrationOpportunity[];
  quality: MLFulfillmentQuality;
  freshness: MLFulfillmentFreshness;
}

// ---------------------------------------------------------------------------
// Janela: o teto de 366 dias e' guardrail GLOBAL da Torre
// ---------------------------------------------------------------------------

/**
 * Espelha `MAX_RANGE_DAYS` de `apps/api/app/deps/period.py`. Excede-lo devolve
 * 422 com corpo tecnico; barrar antes da requisicao evita mostrar isso ao
 * usuario e poupa a ida.
 *
 * NAO e' limite da fato: a fato publica mais de 400 dias. O historico completo
 * existe e precisa ser consultado em janelas.
 */
export const MAX_RANGE_DAYS = 366;

/** Presets de periodo. 366 e' o teto exato, nao um valor redondo. */
export const PERIOD_PRESETS = [
  { days: 30, label: "30 dias" },
  { days: 90, label: "90 dias" },
  { days: 180, label: "180 dias" },
  { days: MAX_RANGE_DAYS, label: "366 dias" },
] as const;

/** Dias INCLUSIVOS entre duas datas ISO (`2026-08-01` a `2026-08-31` = 31). */
export function rangeDays(from: string, to: string): number {
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b)) return NaN;
  return Math.round((b - a) / 86_400_000) + 1;
}

/**
 * Valida a janela ANTES da requisicao. Devolve a mensagem em portugues, ou
 * `null` quando esta valida.
 *
 * `hojeIso` e' parametro em vez de `new Date()` interno: sem isso o teste
 * dependeria do relogio da maquina, e o bloqueio de D0 nunca seria verificavel.
 */
export function validateRange(
  from: string,
  to: string,
  hojeIso: string,
): string | null {
  if (!from || !to) return "Informe as duas datas do periodo.";
  const dias = rangeDays(from, to);
  if (Number.isNaN(dias)) return "Data invalida.";
  if (dias <= 0) return "A data inicial precisa vir antes da final.";
  if (dias > MAX_RANGE_DAYS) {
    return `Periodo de ${dias} dias excede o maximo de ${MAX_RANGE_DAYS}. ` +
      "O historico completo tem mais de 366 dias e deve ser consultado em janelas.";
  }
  // D0 e futuro: a fato so' publica ate' D-1, entao pedir o dia corrente
  // devolveria uma lacuna que parece queda.
  if (to >= hojeIso) {
    return "O dia corrente ainda esta aberto. Selecione ate' ontem (D-1).";
  }
  return null;
}

/** Janela do preset, terminando em D-1. */
export function presetRange(
  days: number,
  hojeIso: string,
): { from: string; to: string } {
  const hoje = Date.parse(`${hojeIso}T00:00:00Z`);
  const to = new Date(hoje - 86_400_000);
  const from = new Date(to.getTime() - (days - 1) * 86_400_000);
  return { from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10) };
}

// ---------------------------------------------------------------------------
// Formatacao — ausencia NUNCA vira zero
// ---------------------------------------------------------------------------

/** Texto de indisponibilidade. Um unico simbolo, para a tela nao inventar. */
export const SEM_DADO = "—";

/**
 * Converte o Decimal-string do backend em numero.
 *
 * Retorna `null` para `null`, string vazia ou texto nao numerico -- nunca `0`.
 * Zero e' medicao ("nao houve GMV"); ausencia e' outra coisa, e a tela precisa
 * poder distinguir as duas.
 */
export function parseGmv(valor: string | null | undefined): number | null {
  if (valor === null || valor === undefined || valor === "") return null;
  const n = Number(valor);
  return Number.isFinite(n) ? n : null;
}

export function fmtShare(valor: number | null | undefined): string {
  if (valor === null || valor === undefined || !Number.isFinite(valor)) return SEM_DADO;
  return `${(valor * 100).toFixed(2).replace(".", ",")}%`;
}

/** Moeda cheia, sem abreviar: esta tela e' de conferencia, nao de vitrine. */
export function fmtGmv(valor: string | number | null | undefined): string {
  const n = typeof valor === "string" ? parseGmv(valor) : valor;
  if (n === null || n === undefined || !Number.isFinite(n)) return SEM_DADO;
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

export function fmtCount(valor: number | null | undefined): string {
  if (valor === null || valor === undefined || !Number.isFinite(valor)) return SEM_DADO;
  return valor.toLocaleString("pt-BR");
}

/**
 * Media de tempo com a amostra ao lado.
 *
 * `sample_count` e' CENSURA, nao decoracao: pedido ainda sem despacho ou sem
 * entrega fica fora da amostra em vez de entrar como tempo zero. Esconder o
 * tamanho da amostra faria uma media de 3 pedidos parecer igual a uma de 300
 * mil.
 */
export function fmtTempo(stat: TimeStat | null | undefined): string {
  if (!stat || stat.sample_count <= 0 || stat.hours_avg === null) return SEM_DADO;
  if (stat.hours_avg >= 48 && stat.days_avg !== null) {
    return `${stat.days_avg.toFixed(2).replace(".", ",")} d`;
  }
  return `${stat.hours_avg.toFixed(1).replace(".", ",")} h`;
}

// ---------------------------------------------------------------------------
// Rotulos — o vocabulario da tela
// ---------------------------------------------------------------------------

/**
 * `non_full` NAO e' sinonimo de cross-docking.
 *
 * A fonte ja emitiu cinco modalidades (`cross_docking`, `xd_drop_off`,
 * `self_service`, `drop_off` e o proprio `fulfillment`), e tres delas foram
 * extintas em 2026. Chamar a classe inteira de "cross-docking" apagaria o
 * historico e mentiria sobre o presente. A composicao real fica no bloco
 * `by_logistic_type`.
 */
export const CLASS_LABEL: Record<FulfillmentClass, string> = {
  full: "Full",
  non_full: "Não-Full",
  unknown: "Desconhecido",
};

export const CLASS_DESCRICAO: Record<FulfillmentClass, string> = {
  full: "Envio pelo fulfillment do Mercado Livre (logistic_type = fulfillment).",
  non_full:
    "Qualquer outra modalidade logistica registrada no envio — inclui " +
    "cross-docking e os tipos historicos ja extintos. Nao e' uma modalidade unica.",
  unknown:
    "Pedido sem envio correspondente ou com modalidade nula. Nao entra no " +
    "denominador dos shares: nao e' Full nem nao-Full, e conta-lo faria uma " +
    "lacuna de dado parecer queda operacional.",
};

export const FRESHNESS_LABEL: Record<FreshnessStatus, string> = {
  fresh: "Atualizado",
  stale: "Desatualizado",
  never_loaded: "Nunca carregado",
  unknown: "Frescor desconhecido",
};

export const LOAD_MODE_LABEL: Record<LoadMode, string> = {
  manual_snapshot: "Carga manual",
  scheduled: "Carga agendada",
};

/** Aviso fixo sobre o modo de carga. Nao some enquanto a DAG nao existir. */
export const AVISO_MANUAL_SNAPSHOT =
  "A carga ainda e' manual (manual_snapshot): a automacao diaria e o rebuild " +
  "periodico pertencem a frente de DAG e ainda nao existem. O dado so' avanca " +
  "quando alguem executa o sync.";

export const AVISO_JANELA =
  "A fato publica mais de 366 dias, mas cada consulta cobre no maximo 366. " +
  "Para ver o historico inteiro, avance a janela.";

export const AVISO_COBERTURA =
  "A serie comeca em 01/08/2025. Maio a julho de 2025 nao sao publicados: a " +
  "fonte tem 1.330 pedidos pagos sem item nesse intervalo, e publicar isso " +
  "gravaria venda sem unidade.";

export const AVISO_SEM_ESTOQUE =
  "Esta tela mede modalidade logistica do envio, nao posicao de estoque. Nao " +
  "ha aqui disponibilidade, cobertura em dias nem ruptura.";

// ---------------------------------------------------------------------------
// Derivacoes de apresentacao
// ---------------------------------------------------------------------------

export function byClass(
  dados: MLFulfillmentResponse | null,
  classe: FulfillmentClass,
): ModalBreakdown | null {
  return dados?.by_class.find((c) => c.fulfillment_class === classe) ?? null;
}

/**
 * A classe ausente NAO vira linha de zeros: o backend a omite quando nao houve
 * nenhum pedido dela, e repetir essa omissao aqui preserva a diferenca entre
 * "nao houve" e "houve zero".
 */
export function classesPresentes(
  dados: MLFulfillmentResponse | null,
): FulfillmentClass[] {
  if (!dados) return [];
  return dados.by_class.map((c) => c.fulfillment_class);
}

/** Alertas derivados do payload. Texto fixo; nada e' interpolado de erro. */
export function alertas(dados: MLFulfillmentResponse | null): string[] {
  if (!dados) return [];
  const out: string[] = [];
  const q = dados.quality;

  if (q.unknown_logistic_type_orders > 0) {
    out.push(
      `${fmtCount(q.unknown_logistic_type_orders)} pedido(s) sem envio ` +
        "correspondente na janela. Ficam em Desconhecido e fora dos shares.",
    );
  }
  if (q.missing_shipping_items > 0) {
    out.push(
      `${fmtCount(q.missing_shipping_items)} envio(s) sem itens declarados. ` +
        "Nao afeta as unidades, que vem do item do pedido.",
    );
  }
  if (!q.is_complete) {
    out.push(
      "A janela pedida tem dias sem linha publicada, ou a carga esta " +
        "desatualizada. Os totais podem estar incompletos.",
    );
  }
  if (dados.freshness.freshness_status === "stale") {
    out.push("A ultima carga passou do limiar de frescor.");
  }
  if (dados.freshness.freshness_status === "never_loaded") {
    out.push("Nenhuma carga foi publicada ainda.");
  }
  return out;
}

/** True quando a resposta veio bem, mas sem nenhum pedido — vazio REAL. */
export function isVazioReal(dados: MLFulfillmentResponse | null): boolean {
  if (!dados) return false;
  return dados.by_class.length === 0 && dados.daily.length === 0;
}

/**
 * Serie diaria pivotada para o grafico: uma linha por data, com o share da
 * metrica escolhida por classe.
 *
 * O share do dia e' calculado sobre `full + non_full`, EXCLUINDO `unknown` --
 * a mesma regra do backend. Esta e' a unica divisao que a tela faz, e existe
 * porque a API entrega o share agregado do periodo, nao por dia.
 */
export type MetricaSerie = "gmv" | "orders" | "units";

export interface PontoSerie {
  ref_date: string;
  share_full: number | null;
  full: number;
  non_full: number;
  unknown: number;
}

export function serieDiaria(
  dados: MLFulfillmentResponse | null,
  metrica: MetricaSerie,
): PontoSerie[] {
  if (!dados) return [];
  const valor = (p: DailyPoint): number => {
    if (metrica === "gmv") return parseGmv(p.paid_gmv) ?? 0;
    if (metrica === "orders") return p.paid_orders;
    return p.paid_units;
  };
  const porData = new Map<string, PontoSerie>();
  for (const p of dados.daily) {
    const atual = porData.get(p.ref_date) ?? {
      ref_date: p.ref_date, share_full: null, full: 0, non_full: 0, unknown: 0,
    };
    atual[p.fulfillment_class] += valor(p);
    porData.set(p.ref_date, atual);
  }
  const pontos = [...porData.values()].sort((a, b) =>
    a.ref_date < b.ref_date ? -1 : 1,
  );
  for (const p of pontos) {
    const den = p.full + p.non_full;
    // Denominador zero vira `null`, nunca 0: um dia sem venda classificada nao
    // e' um dia de 0% de Full.
    p.share_full = den > 0 ? p.full / den : null;
  }
  return pontos;
}

/** Query string do endpoint. Sem marca = todas (a API trata `brands` ausente). */
export function buildQuery(params: {
  from: string;
  to: string;
  brands?: string[];
}): string {
  const q = new URLSearchParams();
  q.set("date_from", params.from);
  q.set("date_to", params.to);
  if (params.brands && params.brands.length > 0) {
    q.set("brands", params.brands.join(","));
  }
  return q.toString();
}
