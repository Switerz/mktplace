/**
 * Contrato e regras puras da tela "Full Shopee" — Gate FULL-SH-1D.
 *
 * A tela NAO recalcula nada que a API entrega pronto. Shares consolidados,
 * taxa de cancelamento e handling medio vem do backend, ja' agregados no
 * periodo inteiro. A UNICA divisao feita aqui e' o share DIARIO da serie,
 * porque a API entrega o share agregado do periodo e nao por dia -- e ela usa
 * exatamente a mesma regra: denominador `fbs + seller`.
 *
 * DINHEIRO CHEGA COMO STRING
 * ---------------------------
 * O backend serializa `Decimal` como STRING ("944358.30"), nao numero. Tipar
 * como `number` compilaria, passaria no lint e produziria `NaN` ou
 * concatenacao em producao. A conversao acontece num unico ponto (`parseGmv`).
 *
 * ZERO NAO E' AUSENCIA
 * ---------------------
 * `share` nulo significa denominador zero -- "nao ha o que dividir". Zero
 * significa "mediu e deu zero". Apice e' o caso real do segundo: vendeu
 * R$ 275.234,00 em agosto/2026 e nao teve um unico pedido FBS. Exibir "sem
 * dado" ali seria mentira.
 *
 * KOKESHI NAO E' ZERO
 * --------------------
 * Ela nao existe na esteira API. Nao aparece em nenhum denominador, em nenhuma
 * tabela e em nenhum grafico -- aparece como NAO COBERTA.
 */

/** Classes logisticas. Nao existe `unknown` nesta fonte. */
export type FbsClass = "fbs" | "seller";

export const CLASS_LABEL: Record<FbsClass, string> = {
  fbs: "FBS (Shopee)",
  seller: "Envio pelo vendedor",
};

export const SEM_DADO = "—";
export const MAX_RANGE_DAYS = 366;
export const PERIOD_PRESETS = [30, 90, 180, 366] as const;

export type FreshnessStatus = "fresh" | "stale" | "never_loaded" | "unknown";

export const FRESHNESS_LABEL: Record<FreshnessStatus, string> = {
  fresh: "Atualizado",
  stale: "Desatualizado",
  never_loaded: "Nunca carregado",
  unknown: "Frescor desconhecido",
};

export interface HandlingStat {
  seconds_avg: number | null;
  hours_avg: number | null;
  days_avg: number | null;
  seconds_sum: number;
  sample_count: number;
  coverage_ratio: number | null;
}

export interface Freshness {
  freshness_status: FreshnessStatus;
  source_watermark_at: string | null;
  refreshed_at: string | null;
  source_max_date: string | null;
  source_min_date: string | null;
  source_age_hours: number | null;
  snapshot_age_hours: number | null;
  closed_days_behind: number | null;
  load_mode: "manual_snapshot";
  no_automation: boolean;
}

export interface Meta {
  marketplace: "shopee";
  scope_label: string;
  date_policy: "closed_day";
  d0_materialized: boolean;
  last_closed_date: string;
  requested_date_from: string;
  requested_date_to: string;
  effective_date_from: string | null;
  effective_date_to: string | null;
  expected_accounts: string[];
  observed_accounts: string[];
  missing_accounts: string[];
  unexpected_accounts: string[];
  covered_brands: string[];
  brands_not_covered: string[];
  gmv_definition: string;
  freshness: Freshness;
  warnings: string[];
  limitations: string[];
}

/** Dinheiro e' STRING. Nao trocar por `number`: ha teste que falha. */
export interface Totals {
  gross_gmv: string;
  gross_units: number;
  created_orders: number;
  eligible_orders: number;
  cancelled_orders: number;
  to_return_orders: number;
  to_return_gmv: string;
  unpaid_orders: number;
  unpaid_gmv: string;
  handling: HandlingStat;
}

export interface Shares {
  share_fbs_gmv: number | null;
  share_fbs_orders: number | null;
  share_fbs_units: number | null;
}

export interface Rates {
  cancellation_rate: number | null;
}

export interface ClassBreakdown {
  fbs_class: FbsClass;
  gross_gmv: string;
  gross_units: number;
  created_orders: number;
  eligible_orders: number;
  cancelled_orders: number;
  to_return_gmv: string;
  unpaid_gmv: string;
  handling: HandlingStat;
}

export interface BrandBreakdown {
  brand: string;
  gross_gmv: string;
  gross_units: number;
  eligible_orders: number;
  cancelled_orders: number;
  created_orders: number;
  share_fbs_gmv: number | null;
  share_fbs_orders: number | null;
}

export interface AccountBreakdown {
  shop_account: string;
  brand: string;
  gross_gmv: string;
  eligible_orders: number;
  created_orders: number;
  share_fbs_gmv: number | null;
  source_watermark_at: string | null;
}

export interface DailyPoint {
  ref_date: string;
  fbs_class: FbsClass;
  gross_gmv: string;
  gross_units: number;
  eligible_orders: number;
  created_orders: number;
  cancelled_orders: number;
}

export interface ShopeeFbsResponse {
  meta: Meta;
  totals: Totals;
  rates: Rates;
  shares: Shares;
  by_class: ClassBreakdown[];
  by_brand: BrandBreakdown[];
  by_account: AccountBreakdown[];
  daily: DailyPoint[];
}

/** Resposta quando a flag do BACKEND esta desligada. */
export interface ShopeeFbsUnavailable {
  marketplace: "shopee";
  status: "unavailable";
  scope_label: string;
  unavailable_reason: string;
  date_policy: "closed_day";
}

export type ShopeeFbsPayload = ShopeeFbsResponse | ShopeeFbsUnavailable;

export function isUnavailable(p: ShopeeFbsPayload | null): p is ShopeeFbsUnavailable {
  return !!p && (p as ShopeeFbsUnavailable).status === "unavailable";
}

// ---------------------------------------------------------------------------
// Conversao e formatacao
// ---------------------------------------------------------------------------

/**
 * String Decimal -> number. Devolve `null` para ausencia ou lixo -- NUNCA 0,
 * que significaria "vendeu zero" em vez de "nao sei".
 */
export function parseGmv(valor: string | null | undefined): number | null {
  if (valor === null || valor === undefined || valor === "") return null;
  const n = Number(valor);
  return Number.isFinite(n) ? n : null;
}

export function fmtGmv(valor: string | null | undefined): string {
  const n = parseGmv(valor);
  if (n === null) return SEM_DADO;
  return n.toLocaleString("pt-BR", {
    style: "currency", currency: "BRL", minimumFractionDigits: 2,
  });
}

export function fmtCount(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return SEM_DADO;
  return n.toLocaleString("pt-BR");
}

/** `null` vira travessao, nunca "0%". */
export function fmtShare(v: number | null | undefined, casas = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return SEM_DADO;
  return `${(v * 100).toFixed(casas).replace(".", ",")}%`;
}

/** Handling em horas/dias. `null` quando a amostra e' zero. */
export function fmtHandling(h: HandlingStat | null | undefined): string {
  if (!h || h.sample_count === 0 || h.hours_avg === null) return SEM_DADO;
  if (h.hours_avg < 48) return `${h.hours_avg.toFixed(1).replace(".", ",")} h`;
  return `${(h.days_avg ?? 0).toFixed(1).replace(".", ",")} dias`;
}

// ---------------------------------------------------------------------------
// Janela
// ---------------------------------------------------------------------------

export function hojeIsoBrt(agora?: Date): string {
  const d = agora ?? new Date();
  return d.toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" });
}

/** Ultimo dia FECHADO: D-1. A fato nao materializa D0. */
export function lastClosedDate(hojeIso: string): string {
  const d = new Date(`${hojeIso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

/**
 * Valida a janela ANTES da requisicao. O 422 do backend nao ecoa a entrada,
 * mas barrar aqui evita ida perdida ao servidor e da' mensagem melhor.
 */
export function validateRange(
  from: string, to: string, hojeIso: string,
): string | null {
  if (!from || !to) return "Informe as duas datas do periodo.";
  if (from > to) return "A data inicial nao pode ser posterior a final.";
  const ultimo = lastClosedDate(hojeIso);
  if (to > ultimo) {
    return `Esta tela publica apenas dias fechados. A data final maxima e ${ultimo} (D-1).`;
  }
  const dias =
    Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`))
      / 86_400_000) + 1;
  if (dias > MAX_RANGE_DAYS) {
    return `Periodo de ${dias} dias excede o maximo de ${MAX_RANGE_DAYS}. Consulte em janelas menores.`;
  }
  return null;
}

/** Preset de N dias terminando em D-1. */
export function presetRange(dias: number, hojeIso: string): { from: string; to: string } {
  const to = lastClosedDate(hojeIso);
  const d = new Date(`${to}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() - (dias - 1));
  return { from: d.toISOString().slice(0, 10), to };
}

export function buildQuery(p: {
  from: string; to: string; brands?: string[]; accounts?: string[];
}): string {
  const q = new URLSearchParams();
  q.set("date_from", p.from);
  q.set("date_to", p.to);
  for (const b of p.brands ?? []) q.append("brands", b);
  for (const a of p.accounts ?? []) q.append("accounts", a);
  return q.toString();
}

// ---------------------------------------------------------------------------
// Derivacoes da serie
// ---------------------------------------------------------------------------

export type MetricaSerie = "gmv" | "orders" | "units";

export interface PontoSerie {
  data: string;
  fbs: number;
  seller: number;
  /** FBS / (FBS + seller) do DIA. `null` quando o denominador e' zero. */
  share: number | null;
}

function valorDaMetrica(p: DailyPoint, m: MetricaSerie): number {
  if (m === "gmv") return parseGmv(p.gross_gmv) ?? 0;
  if (m === "orders") return p.eligible_orders;
  return p.gross_units;
}

/**
 * Serie diaria com share POR DIA.
 *
 * IMPORTANTE: este share e' do DIA, para a linha do grafico. O share do
 * PERIODO vem de `shares.share_fbs_gmv`, calculado pelo backend sobre os
 * totais consolidados. Promediar os shares diarios daria peso igual a um
 * domingo de R$ 300 e a uma Black Friday de R$ 300.000 -- ha teste que trava
 * essa diferenca.
 */
export function serieDiaria(
  dados: ShopeeFbsResponse | null, metrica: MetricaSerie,
): PontoSerie[] {
  if (!dados?.daily?.length) return [];
  const porDia = new Map<string, { fbs: number; seller: number }>();
  for (const p of dados.daily) {
    const atual = porDia.get(p.ref_date) ?? { fbs: 0, seller: 0 };
    atual[p.fbs_class] += valorDaMetrica(p, metrica);
    porDia.set(p.ref_date, atual);
  }
  return [...porDia.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([data, v]) => {
      const den = v.fbs + v.seller;
      return { data, fbs: v.fbs, seller: v.seller, share: den > 0 ? v.fbs / den : null };
    });
}

export function byClass(
  dados: ShopeeFbsResponse | null, classe: FbsClass,
): ClassBreakdown | null {
  return dados?.by_class?.find((c) => c.fbs_class === classe) ?? null;
}

/** Janela sem NENHUMA linha publicada -- diferente de "vendeu zero". */
export function isVazioReal(dados: ShopeeFbsResponse | null): boolean {
  if (!dados) return false;
  return dados.by_class.length === 0 && dados.daily.length === 0;
}

/** Cobertura de handling abaixo deste piso e' LIMITACAO, nao precisao plena. */
export const HANDLING_COBERTURA_MINIMA = 0.5;

export function handlingPoucaCobertura(h: HandlingStat | null | undefined): boolean {
  if (!h || h.sample_count === 0) return false;
  return h.coverage_ratio !== null && h.coverage_ratio < HANDLING_COBERTURA_MINIMA;
}

// ---------------------------------------------------------------------------
// Avisos permanentes — verdades da fonte, nao estado da requisicao
// ---------------------------------------------------------------------------

export const AVISO_CARGA_MANUAL =
  "A carga ainda e manual (manual_snapshot): nao existe DAG nem agenda, e o " +
  "dado so avanca quando alguem executa o sync. Esta tela nao e tempo real.";

export const AVISO_COBERTURA =
  "Cobertura PARCIAL da Shopee. A esteira de API cobre quatro contas; " +
  "Kokeshi NAO esta na API e nao entra em nenhum total, share ou grafico " +
  "desta tela. Os numeros aqui sao 'Shopee — cobertura API', nunca a " +
  "operacao Shopee inteira.";

export const AVISO_GMV_BRUTO =
  "GMV BRUTO de pedidos nao cancelados: inclui pedidos em devolucao " +
  "(to_return) e nao pagos (unpaid), e exclui cancelados. Nao e receita " +
  "liquida nem realizada.";

export const AVISO_DIVERGENCIA =
  "Esta definicao difere das telas Shopee baseadas em `is_sale`, que excluem " +
  "to_return e unpaid. Diferenca entre telas pode ser de DEFINICAO, nao " +
  "necessariamente erro.";

export const AVISO_HANDLING =
  "Handling mede do pagamento ate a COLETA (pay_time -> pickup_done_time). " +
  "Nao e prazo de entrega e nao inclui devolucao: a API da Shopee nao expoe " +
  "data real de entrega.";

export const AVISO_JANELA =
  "Serie publicada a partir de 01/01/2026, sempre ate D-1. Cada consulta " +
  "cobre no maximo 366 dias.";

/** Avisos derivados do payload. Nunca inventa: so' reporta o que veio. */
export function alertas(dados: ShopeeFbsResponse | null): string[] {
  if (!dados) return [];
  const out: string[] = [];
  const m = dados.meta;

  if (m.missing_accounts.length) {
    out.push(
      `Conta(s) sem movimento na janela: ${m.missing_accounts.join(", ")}. ` +
      "Fora da cobertura medida — nao e 0%.",
    );
  }
  if (m.unexpected_accounts.length) {
    out.push(
      `Conta(s) fora da lista esperada: ${m.unexpected_accounts.join(", ")}. ` +
      "O contrato da fonte mudou; investigue antes de usar o total.",
    );
  }
  if (m.freshness.freshness_status === "stale") {
    const atraso = m.freshness.closed_days_behind;
    out.push(
      "Snapshot DESATUALIZADO" +
      (atraso && atraso > 1 ? `: a serie esta ${atraso} dias atras de D-1.` : ".") +
      " Verifique a idade antes de decidir com estes numeros.",
    );
  }
  if (m.freshness.freshness_status === "never_loaded") {
    out.push("Nenhuma linha publicada para esta janela.");
  }
  if (handlingPoucaCobertura(dados.totals.handling)) {
    const pct = fmtShare(dados.totals.handling.coverage_ratio, 0);
    out.push(
      `Handling cobre apenas ${pct} dos pedidos elegiveis. A media descreve ` +
      "essa amostra, nao o periodo inteiro.",
    );
  }
  return out;
}
