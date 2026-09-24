/**
 * Gate EXP-TK-OPS-1 — contrato da tela de expedicao do TikTok Shop.
 *
 * Separado de `expedicao-contract.ts` de proposito. Aquele descreve uma FILA
 * (uma linha por pedido parado agora); este descreve uma SERIE por COORTE (uma
 * linha por data de pagamento). Os dois tem estados, rotulos e perguntas
 * diferentes, e misturar os contratos faria cada mudanca aqui arriscar os dois
 * canais que ja estao em producao.
 */

// ---------------------------------------------------------------------------
// Eventos
// ---------------------------------------------------------------------------
/**
 * Os dois instantes que competem pelo nome "postado". Medidos na fonte em
 * 2026-09-24, e NAO sao a mesma coisa: a coleta acontece em mediana 44,9 h
 * depois do despacho.
 *
 * Na janela medida o despacho ficou em ~0,05% e a coleta chegou a 88,9% num
 * unico dia — a operacao etiquetou no prazo e a transportadora nao coletou.
 * A tela precisa deixar escolher qual esta sendo olhado, senao o operador
 * conclui a coisa errada sobre de quem e' o problema.
 */
export const EVENTOS = ["coleta", "despacho"] as const;
export type Evento = (typeof EVENTOS)[number];

/** A coleta e' o padrao: e' o evento em que o incidente aparece. */
export const EVENTO_PADRAO: Evento = "coleta";

export const ROTULO_EVENTO: Record<Evento, string> = {
  coleta: "Coleta pela transportadora",
  despacho: "Despacho (etiqueta)",
};

export const EXPLICACAO_EVENTO: Record<Evento, string> = {
  coleta:
    "Quando a transportadora de fato retirou o pacote. É o evento em que o " +
    "atraso recente aparece.",
  despacho:
    "Quando a operação confirmou “pronto para envio” e a etiqueta nasceu. " +
    "Está sob nosso controle direto.",
};

/**
 * O prazo desta tela NAO vem do TikTok: e' reconstruido da regra de 2 dias
 * uteis. A API devolve `deadline_is_reconstructed` e a tela e' obrigada a
 * mostrar este aviso — exibir a taxa como se fosse a medicao da plataforma
 * seria afirmar o que ninguem mediu.
 */
export const AVISO_PRAZO_RECONSTRUIDO =
  "Prazo reconstruído da regra de 2 dias úteis. O SLA oficial do TikTok não " +
  "é ingerido hoje, então este número é um indicador interno e não a " +
  "medição de penalização da plataforma.";

/** Acima disto o dia entra em destaque. Tem de casar com o limiar da API. */
export const LIMIAR_DIA_CRITICO = 0.1;

export const JANELAS = [
  { dias: 7, rotulo: "7d + hoje" },
  { dias: 14, rotulo: "14d + hoje" },
  { dias: 30, rotulo: "30d + hoje" },
] as const;

export const JANELA_INICIAL_DIAS = 7;

// ---------------------------------------------------------------------------
// Payload
// ---------------------------------------------------------------------------
export type TikTokDia = {
  paid_date: string;
  deadline_date: string;
  pedidos_pagos: number;
  cancelados: number;
  atrasados: number;
  pendentes_vencidos: number;
  pendentes_no_prazo: number;
  rate: number | null;
  is_mature: boolean;
  is_critical: boolean;
};

export type TikTokMarca = {
  brand: string;
  shop_name: string | null;
  pedidos_pagos: number;
  atrasados: number;
  rate: number | null;
};

export type TikTokJanela = {
  days: number;
  from: string;
  to: string;
  rate_ratio_of_totals: number | null;
  rate_mean_of_daily: number | null;
  mature_cohorts: number;
  partial_cohorts: number;
  paid_orders: number;
  late_orders: number;
  pending_overdue: number;
  pending_on_time: number;
  pending_at_risk: number;
  incident_start: string | null;
  incident_end: string | null;
};

export type TikTokAlerta = {
  severity: "critical" | "warning";
  code: string;
  message: string;
};

export type TikTokPayload = {
  availability: "available" | "unavailable";
  unavailable_reason: string | null;
  channel: "tiktokshop";
  event?: Evento;
  deadline_is_reconstructed: boolean;
  snapshot: {
    refresh_batch_id: string;
    effective_at: string;
    source_watermark_at: string | null;
    today_brt: string;
  } | null;
  window: TikTokJanela | null;
  daily: TikTokDia[];
  brands: TikTokMarca[];
  alerts: TikTokAlerta[];
};

export const MOTIVO_INDISPONIVEL: Record<string, string> = {
  table_not_migrated:
    "A série do TikTok ainda não foi criada no banco. Falta aplicar a " +
    "migration 020.",
  no_series_published:
    "A tabela existe mas nenhuma fotografia foi publicada ainda.",
};

export function explicarIndisponivel(motivo: string | null): string {
  if (!motivo) return "Série indisponível.";
  return MOTIVO_INDISPONIVEL[motivo] ?? "Série indisponível.";
}

// ---------------------------------------------------------------------------
// Formatacao
// ---------------------------------------------------------------------------
/** Marcador para taxa que nao existe. NUNCA renderizar `0%` no lugar dele. */
export const VALOR_SEM_TAXA = "—";

export function formatarTaxa(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return VALOR_SEM_TAXA;
  return `${(v * 100).toFixed(2).replace(".", ",")}%`;
}

export function formatarInteiro(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return VALOR_SEM_TAXA;
  return new Intl.NumberFormat("pt-BR").format(v);
}

/**
 * Data ISO -> `dd/mm`. Constroi a data com `Date.UTC` a partir das PARTES, e
 * nao com `new Date("2026-09-07")`, porque a segunda forma e' interpretada
 * como meia-noite UTC e volta um dia atras em qualquer fuso negativo — que e'
 * exatamente o nosso. O dia da coorte apareceria errado na tela inteira.
 */
export function formatarDiaCurto(iso: string): string {
  const [a, m, d] = iso.split("-").map(Number);
  if (!a || !m || !d) return iso;
  const dt = new Date(Date.UTC(a, m - 1, d));
  return `${String(dt.getUTCDate()).padStart(2, "0")}/${String(
    dt.getUTCMonth() + 1,
  ).padStart(2, "0")}`;
}

export function formatarDiaLongo(iso: string): string {
  const [a, m, d] = iso.split("-").map(Number);
  if (!a || !m || !d) return iso;
  return `${String(d).padStart(2, "0")}/${String(m).padStart(2, "0")}/${a}`;
}

// ---------------------------------------------------------------------------
// Derivacoes da tela
// ---------------------------------------------------------------------------
export type Severidade = "ok" | "atencao" | "critico" | "parcial";

/**
 * Cor/estado de um dia. `parcial` vence qualquer outra classificacao: coorte
 * que ainda nao venceu nao pode ser pintada de verde nem de vermelho, porque
 * a taxa dela ainda vai mudar.
 */
export function severidadeDoDia(dia: TikTokDia): Severidade {
  if (!dia.is_mature) return "parcial";
  if (dia.rate === null) return "ok";
  if (dia.rate >= LIMIAR_DIA_CRITICO) return "critico";
  if (dia.rate >= LIMIAR_DIA_CRITICO / 2) return "atencao";
  return "ok";
}

export const ROTULO_SEVERIDADE: Record<Severidade, string> = {
  ok: "Dentro do esperado",
  atencao: "Atenção",
  critico: "Crítico",
  parcial: "Ainda no prazo (parcial)",
};

/**
 * Texto do periodo do incidente. Devolve `null` quando nao houve nenhum dia
 * critico — a tela nao deve inventar um incidente para preencher espaco.
 */
export function descreverIncidente(j: TikTokJanela | null): string | null {
  if (!j || !j.incident_start) return null;
  const ini = formatarDiaLongo(j.incident_start);
  if (!j.incident_end || j.incident_end === j.incident_start) {
    return `Um dia acima do limiar: ${ini}.`;
  }
  return `De ${ini} a ${formatarDiaLongo(j.incident_end)}.`;
}

/**
 * Quantos dias criticos houve. Serve para o cartao de resumo nao depender de
 * o consumidor reimplementar o limiar.
 */
export function contarDiasCriticos(dias: TikTokDia[]): number {
  return dias.filter((d) => d.is_critical).length;
}

/**
 * As duas leituras da janela divergem quando o volume diario varia muito.
 * Medido em 2026-09-24: 14,42% pela razao dos totais contra 10,46% pela media
 * das diarias, ou seja 3,96 pp. A tela mostra a razao como numero principal e
 * a media ao lado, rotulada — nao da' para escolher uma sem saber qual a
 * plataforma usa, e esconder a outra impediria a reconciliacao com a planilha.
 */
export function divergenciaEmPp(j: TikTokJanela | null): number | null {
  if (!j || j.rate_ratio_of_totals === null || j.rate_mean_of_daily === null) {
    return null;
  }
  return (j.rate_ratio_of_totals - j.rate_mean_of_daily) * 100;
}

/** As marcas acima do limiar, que e' o que responde "quem concentra o risco". */
export function marcasCriticas(marcas: TikTokMarca[]): TikTokMarca[] {
  return marcas.filter((m) => m.rate !== null && m.rate >= LIMIAR_DIA_CRITICO);
}

/**
 * Altura da barra no grafico, em porcentagem do maior valor da serie.
 * Normaliza pelo MAXIMO e nao por 100%: numa janela calma (taxa maxima de 3%)
 * todas as barras ficariam invisiveis e o operador nao veria a variacao que
 * importa. Piso de 2% para que um dia com taxa muito pequena, mas nao nula,
 * continue clicavel e visivel.
 */
export function alturaDaBarra(dia: TikTokDia, maximo: number): number {
  if (dia.rate === null || maximo <= 0) return 0;
  return Math.max(2, (dia.rate / maximo) * 100);
}

export function taxaMaxima(dias: TikTokDia[]): number {
  return dias.reduce((max, d) => (d.rate !== null && d.rate > max ? d.rate : max), 0);
}

/** URL da API. Mantem a mesma forma de query usada pelos outros canais. */
export function queryDoTikTok(evento: Evento, dias: number): string {
  const p = new URLSearchParams();
  p.set("event", evento);
  p.set("days", String(dias));
  return `/api/v1/expedicao/tiktok?${p.toString()}`;
}
