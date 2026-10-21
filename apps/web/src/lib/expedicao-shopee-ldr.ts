/**
 * EXPEDICAO-SHOPEE-LDR-1 — contrato da LDR operacional da Shopee na tela.
 *
 * Consome `GET /api/v1/expedicao/shopee/ldr`. Nao recalcula taxa, cobertura
 * nem maturidade: a API ja' devolve `rate = null` quando nao ha base madura ou
 * quando a cobertura passa de 10%. Aqui so' se decide o ESTADO e o texto.
 *
 * NAO E' A METRICA DE PENALIZACAO DA SHOPEE. O nome e o aviso vem da propria
 * resposta (`metric_name`, `disclaimer`) e sao exibidos sempre que o numero
 * aparece.
 */

export const NOME_LDR_SHOPEE = "LDR operacional (coleta × prazo Shopee)";
export const AVISO_LDR_SHOPEE =
  "Indicador interno da Torre: não é a taxa de envio atrasado que a Shopee usa para penalização.";
export const VALOR_AUSENTE = "—";

/** Janela padrao, em dias de vencimento antes de hoje. */
export const DIAS_LDR_SHOPEE = 7;

export type EstadoCobertura = "completa" | "aviso" | "insuficiente" | "sem_base";

export interface DiaLdrShopee {
  due_date: string;
  base: number;
  late: number;
  shipped_on_time: number;
  shipped_late: number;
  pending_overdue: number;
  pending_on_time: number;
  excluded_fbs: number;
  excluded_cancelled: number;
  shipped_without_pickup: number;
  rate: number | null;
  is_mature: boolean;
}

export interface RespostaLdrShopee {
  availability: "available" | "unavailable";
  unavailable_reason: string | null;
  channel: "shopee";
  metric_name: string;
  official: boolean;
  disclaimer: string;
  event: "coleta";
  thresholds: { coverage_warning: number; coverage_hide_rate: number };
  snapshot: {
    refresh_batch_id: string;
    effective_at: string;
    source_watermark_at: string | null;
    today_brt: string;
    age_hours: number;
    source_age_hours?: number | null;
    stale: boolean;
  } | null;
  ldr: {
    from: string;
    to: string;
    days: number;
    rate: number | null;
    rate_hidden_reason: string | null;
    base: number;
    late: number;
    shipped_on_time: number;
    shipped_late: number;
    pending_overdue: number;
    pending_on_time: number;
    pending_at_risk: number;
    mature_due_days: number;
    partial_due_days: number;
    daily: DiaLdrShopee[];
  } | null;
  exclusions: {
    fbs: number;
    cancelled_without_pickup: number;
    cancelled_seller_did_not_ship: number;
    cancelled_unknown_reason: number;
    shipped_without_pickup: number;
    without_deadline: number;
  } | null;
  coverage: {
    state: EstadoCobertura;
    missing_share: number | null;
    missing: number;
    eligible: number;
    without_deadline: number;
    shipped_without_pickup: number;
  } | null;
  brands: {
    brand: string;
    base: number;
    late: number;
    rate: number | null;
    coverage_state: EstadoCobertura;
    excluded_fbs: number;
    excluded_cancelled: number;
    source_watermark_at: string | null;
  }[];
  alerts: { severity: "critical" | "warning" | "info"; code: string; message: string }[];
}

export function queryDaLdrShopee(dias: number, marca: string | null): string {
  const p = new URLSearchParams();
  p.set("days", String(dias));
  // A API recorta pela marca no SERVIDOR: taxa, exclusoes e cobertura ja'
  // chegam da marca. A tela nao filtra nem soma nada.
  if (marca) p.set("brands", marca);
  return `/api/v1/expedicao/shopee/ldr?${p.toString()}`;
}

export type EstadoLdrShopee =
  | "carregando"
  | "erro"
  | "desligada"
  | "nao_migrada"
  | "vazia"
  | "pronta";

export function estadoDaLdrShopee(args: {
  carregando: boolean;
  erro: boolean;
  resposta: RespostaLdrShopee | null;
}): EstadoLdrShopee {
  // Carregando vence a resposta guardada: ao trocar de marca, a resposta
  // antiga e' de OUTRA marca e nao pode aparecer como se fosse a nova.
  if (args.carregando) return "carregando";
  if (args.erro) return "erro";
  const r = args.resposta;
  if (!r) return "carregando";
  if (r.availability === "unavailable") {
    if (r.unavailable_reason === "disabled") return "desligada";
    if (r.unavailable_reason === "table_not_migrated") return "nao_migrada";
    return "vazia";
  }
  return "pronta";
}

/** Texto do estado que NAO e' "pronta". Nenhum deles mostra 0%. */
export function textoDoEstado(estado: EstadoLdrShopee, marca: string | null): string {
  switch (estado) {
    case "carregando":
      return "Carregando a LDR operacional…";
    case "erro":
      return "Não foi possível carregar a LDR operacional agora. Nenhum número é exibido no lugar.";
    case "desligada":
      return "A LDR operacional da Shopee ainda não está liberada nesta API.";
    case "nao_migrada":
      return "A LDR operacional da Shopee ainda não foi publicada: a tabela não existe nesta base.";
    case "vazia":
      return marca
        ? "Sem LDR publicada para esta marca na janela consultada — ausência de dado, não atraso zero."
        : "Sem LDR publicada na janela consultada — ausência de dado, não atraso zero.";
    default:
      return "";
  }
}

/** Taxa em pt-BR com 2 casas; `null` vira "—", nunca "0,00%". */
export function formatarTaxaLdr(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return VALOR_AUSENTE;
  return `${(v * 100).toFixed(2).replace(".", ",")}%`;
}

/** Por que a taxa aparece como "—" quando a resposta esta' pronta. */
export function motivoDaTaxaAusente(r: RespostaLdrShopee): string | null {
  if (!r.ldr || r.ldr.rate !== null) return null;
  if (r.ldr.rate_hidden_reason === "coverage_insufficient") {
    return "Taxa não exibida: mais de 10% da base elegível está sem prazo ou sem marco de coleta.";
  }
  if (r.ldr.mature_due_days === 0) {
    return "Nenhum vencimento fechado na janela: a taxa só existe depois que o prazo vence.";
  }
  return "Sem base madura na janela.";
}

export interface LinhaExclusao {
  chave: string;
  rotulo: string;
  valor: number;
  nota?: string;
}

/**
 * Exclusoes da taxa, cada uma com o seu contador. A ordem e' a da precedencia
 * do pipeline. Zero aqui e' uma contagem medida, nao ausencia.
 */
export function montarExclusoes(r: RespostaLdrShopee): LinhaExclusao[] {
  const e = r.exclusions;
  if (!e) return [];
  return [
    {
      chave: "fbs",
      rotulo: "FBS (envio pela Shopee)",
      valor: e.fbs,
      nota: "Fora da taxa de coleta do vendedor.",
    },
    {
      chave: "cancelados",
      rotulo: "Cancelados sem coleta",
      valor: e.cancelled_without_pickup,
      nota:
        e.cancelled_seller_did_not_ship > 0
          ? `${e.cancelled_seller_did_not_ship} cancelado(s) pela Shopee por falta de envio ("Seller did not Ship").`
          : undefined,
    },
    {
      chave: "sem_prazo",
      rotulo: "Sem prazo publicado",
      valor: e.without_deadline,
      nota: "Reduz a cobertura.",
    },
    {
      chave: "sem_marco",
      rotulo: "Enviados sem marco de coleta",
      valor: e.shipped_without_pickup,
      nota: "Reduz a cobertura.",
    },
  ];
}

/** Frase da cobertura. `completa` nao precisa de aviso. */
export function textoDaCobertura(r: RespostaLdrShopee): string | null {
  const c = r.coverage;
  if (!c || c.state === "completa" || c.state === "sem_base") return null;
  const pct = c.missing_share === null ? VALOR_AUSENTE : formatarTaxaLdr(c.missing_share);
  return c.state === "insuficiente"
    ? `Cobertura insuficiente: ${pct} da base elegível sem prazo ou sem marco de coleta (acima de 10%).`
    : `Cobertura incompleta: ${pct} da base elegível sem prazo ou sem marco de coleta (acima de 2%).`;
}
