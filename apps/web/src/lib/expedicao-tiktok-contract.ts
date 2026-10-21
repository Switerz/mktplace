/**
 * Gate EXP-TK-OPS-1/2 — contrato da tela de LDR do TikTok Shop.
 *
 * Separado de `expedicao-contract.ts` de proposito. Aquele descreve uma FILA
 * (uma linha por pedido parado agora); este descreve uma COORTE por prazo. Os
 * dois tem estados, rotulos e perguntas diferentes, e misturar os contratos
 * faria cada mudanca aqui arriscar os dois canais que ja estao em producao.
 */
import {
  consolidarQualidade,
  type BarraQualidade,
  type Fotografia,
  type ItemAtencao,
  type ProblemaQualidade,
  type TomAtencao,
} from "./expedicao-ux.ts";

// ---------------------------------------------------------------------------
// Eventos — a politica tem DOIS prazos
// ---------------------------------------------------------------------------
/**
 * Os dois marcos da politica. NAO tem o mesmo prazo, e confundi-los troca o
 * dono do problema:
 *
 *   coleta (TTS)   2 dias uteis — a transportadora retirou o pacote
 *   despacho (RTS) 1 dia util   — a operacao confirmou "pronto para envio"
 *
 * Medido na fonte, a coleta acontece em mediana 44,9 h depois do despacho.
 * Aplicar 2 dias uteis a etiqueta subestimava o atraso da operacao: a taxa de
 * RTS passa de 0,06% para 0,61% com o prazo certo.
 */
export const EVENTOS = ["coleta", "despacho"] as const;
export type Evento = (typeof EVENTOS)[number];

/** A coleta e' o padrao: e' o marco que a plataforma cobra. */
export const EVENTO_PADRAO: Evento = "coleta";

export const ROTULO_EVENTO: Record<Evento, string> = {
  coleta: "Coleta pela transportadora",
  despacho: "Despacho (etiqueta)",
};

export const EXPLICACAO_EVENTO: Record<Evento, string> = {
  coleta:
    "Quando a transportadora de fato retirou o pacote. Prazo de 2 dias úteis. " +
    "É o marco que a plataforma cobra.",
  despacho:
    "Quando a operação confirmou “pronto para envio” e a etiqueta nasceu. " +
    "Prazo de 1 dia útil. Está sob nosso controle direto.",
};

/**
 * O prazo desta tela NAO vem do TikTok: e' reconstruido da politica de dias
 * uteis. A API devolve `deadline_is_reconstructed` e a tela e' obrigada a
 * mostrar este aviso.
 *
 * A reconstrucao foi reconciliada contra a planilha da gestao (marca
 * `barbours`, 01-23/09): erro medio de 0,31 pp por dia, com os 23 dias dentro
 * da resolucao de 1 pp que a planilha permite (ela exibe a taxa arredondada).
 * O artefato e o script que refazem essa conta estao em
 * `docs/reconciliation/` e `pipelines/reconciliation/tiktok_ldr_planilha.py`.
 */
export const AVISO_PRAZO_RECONSTRUIDO =
  "Prazo reconstruído da política de dias úteis (1 dia para etiqueta, 2 para " +
  "coleta). O SLA oficial por pedido não é ingerido hoje, então este número é " +
  "um indicador interno reconciliado com a planilha da gestão, e não a " +
  "medição de penalização da plataforma.";

// ---------------------------------------------------------------------------
// Os DOIS limiares — nomes diferentes de proposito
// ---------------------------------------------------------------------------
/**
 * Referencia operacional do TikTok para a LDR.
 *
 * O numero (4%) e' da plataforma. A MEDICAO com que o comparamos e' nossa, e
 * usa prazo reconstruido — o SLA por pedido nao e' ingerido. Entao cruzar esta
 * linha significa "acima da referencia pela nossa conta", e NAO "penalizado
 * pelo TikTok": a plataforma calcula com o proprio relogio e o proprio
 * denominador, aos quais nao temos acesso.
 *
 * Todo texto derivado daqui precisa carregar essa distincao. Ver
 * `EXPLICACAO_META`.
 */
export const META_TIKTOK_LDR = 0.04;

/**
 * Limiar INTERNO de severidade. Serve para achar o dia em que a operacao
 * quebrou, e nao para dizer se estamos em conformidade. Confundir os dois faria
 * a tela dizer "dentro da meta" num dia de 9%.
 */
export const LIMIAR_CRITICO_INTERNO = 0.1;

export const ROTULO_META = "Referência do TikTok";
export const ROTULO_LIMIAR_INTERNO = "Limiar crítico interno";

/**
 * O selo exibido quando a LDR cruza a referencia. NAO diz "fora da meta":
 * quem decide isso e' o TikTok, com o proprio relogio e o proprio
 * denominador. Diz o que de fato sabemos — que a NOSSA medicao passou de 4%.
 */
export const SELO_ACIMA_DA_META = "Acima da meta — medição interna";
export const SELO_DENTRO_DA_META = "Dentro da meta — medição interna";

/**
 * Vai SEMPRE junto do selo, nunca escondido atras de um tooltip. As tres
 * coisas que o operador precisa saber para nao levar o numero a uma reuniao
 * como se fosse da plataforma.
 */
export const EXPLICACAO_META =
  "4% é a referência operacional do TikTok. Esta medição usa prazo " +
  "reconstruído da política de dias úteis e ainda não é a medição oficial de " +
  "penalização da plataforma.";

// ---------------------------------------------------------------------------
// As duas leituras
// ---------------------------------------------------------------------------
export const TITULO_LDR = "LDR operacional — vencimentos na janela";
export const TITULO_FLUXO = "Fluxo por data de pagamento";

export const EXPLICACAO_LDR =
  "Denominador: pedidos cujo envio vence no período. É a leitura que responde " +
  "se estamos dentro da régua do TikTok.";

export const EXPLICACAO_FLUXO =
  "Denominador: pedidos pagos em cada dia. É a visão usada para achar onde o " +
  "fluxo entupiu — não é a LDR. Um feriado empurra três dias de pagamento " +
  "para o mesmo vencimento e aparece aqui como três linhas de ~100%.";

export const JANELAS = [
  { dias: 7, rotulo: "7d + hoje" },
  { dias: 14, rotulo: "14d + hoje" },
  { dias: 30, rotulo: "30d + hoje" },
] as const;

export const JANELA_INICIAL_DIAS = 7;

/** Teto da API (`JANELA_MAX_DIAS` em `expedicao_tiktok_service.py`). */
export const JANELA_MAX_DIAS = 90;

// ---------------------------------------------------------------------------
// Payload
// ---------------------------------------------------------------------------
export type TikTokLdrDia = {
  due_date: string;
  base: number;
  late: number;
  pending_overdue: number;
  pending_on_time: number;
  rate: number | null;
  is_mature: boolean;
  above_target: boolean;
  is_critical: boolean;
};

export type TikTokFluxoDia = {
  paid_date: string;
  deadline_date: string;
  paid_orders: number;
  excluded_samples: number;
  excluded_cancelled: number;
  base: number;
  shipped_on_time: number;
  shipped_late: number;
  pending_overdue: number;
  pending_on_time: number;
  late: number;
  rate: number | null;
  is_mature: boolean;
};

export type TikTokLdr = {
  from: string;
  to: string;
  days: number;
  rate: number | null;
  base: number;
  late: number;
  pending_overdue: number;
  pending_on_time: number;
  pending_at_risk: number;
  mature_due_days: number;
  partial_due_days: number;
  above_target: boolean;
  internal_critical: boolean;
  incident_start: string | null;
  incident_end: string | null;
  daily: TikTokLdrDia[];
};

export type TikTokMarca = {
  brand: string;
  shop_name: string | null;
  base: number;
  late: number;
  rate: number | null;
  above_target: boolean;
};

export type TikTokAlerta = {
  severity: "critical" | "warning" | "info";
  code: string;
  message: string;
};

export type TikTokPayload = {
  availability: "available" | "unavailable";
  unavailable_reason: string | null;
  channel: "tiktokshop";
  event?: Evento;
  deadline_is_reconstructed: boolean;
  sla_business_days: Record<string, number>;
  targets: { tiktok_ldr: number; internal_critical: number };
  snapshot: {
    refresh_batch_id: string;
    effective_at: string;
    source_watermark_at: string | null;
    today_brt: string;
  } | null;
  ldr: TikTokLdr | null;
  payment_flow: TikTokFluxoDia[];
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

export function formatarTaxa(v: number | null | undefined, casas = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return VALOR_SEM_TAXA;
  return `${(v * 100).toFixed(casas).replace(".", ",")}%`;
}

export function formatarInteiro(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return VALOR_SEM_TAXA;
  return new Intl.NumberFormat("pt-BR").format(v);
}

/**
 * Data ISO -> `dd/mm`. Constroi a data com `Date.UTC` a partir das PARTES, e
 * nao com `new Date("2026-09-07")`, porque a segunda forma e' meia-noite UTC e
 * volta um dia atras em qualquer fuso negativo — que e' exatamente o nosso.
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
export type Severidade = "ok" | "fora_da_meta" | "critico" | "parcial";

/**
 * Estado de um dia de vencimento. `parcial` vence qualquer outra
 * classificacao: coorte que ainda nao venceu nao pode ser pintada de verde nem
 * de vermelho, porque a taxa dela ainda vai mudar.
 *
 * Le os selos que a API ja calculou em vez de recalcular o limiar. Se a tela
 * recalculasse, o alerta e o grafico poderiam discordar na mesma pagina.
 */
export function severidadeDoDia(dia: TikTokLdrDia): Severidade {
  if (!dia.is_mature) return "parcial";
  if (dia.is_critical) return "critico";
  if (dia.above_target) return "fora_da_meta";
  return "ok";
}

export const ROTULO_SEVERIDADE: Record<Severidade, string> = {
  ok: "Dentro da meta (medição interna)",
  fora_da_meta: "Acima da meta (medição interna)",
  critico: "Crítico (limiar interno)",
  parcial: "Ainda no prazo (parcial)",
};

/**
 * Texto do periodo do incidente. Devolve `null` quando nao houve dia critico —
 * a tela nao deve inventar um incidente para preencher espaco.
 */
export function descreverIncidente(j: TikTokLdr | null): string | null {
  if (!j || !j.incident_start) return null;
  const ini = formatarDiaLongo(j.incident_start);
  if (!j.incident_end || j.incident_end === j.incident_start) {
    return `Um vencimento acima do limiar interno: ${ini}.`;
  }
  return `De ${ini} a ${formatarDiaLongo(j.incident_end)}.`;
}

export function contarForaDaMeta(dias: TikTokLdrDia[]): number {
  return dias.filter((d) => d.above_target).length;
}

export function contarCriticos(dias: TikTokLdrDia[]): number {
  return dias.filter((d) => d.is_critical).length;
}

/** As marcas fora da meta, que e' o que responde "quem concentra o risco". */
export function marcasForaDaMeta(marcas: TikTokMarca[]): TikTokMarca[] {
  return marcas.filter((m) => m.above_target);
}

/**
 * Altura da barra, em porcentagem do maior valor da serie. Normaliza pelo
 * MAXIMO e nao por 100%: numa janela calma (taxa maxima de 3%) todas as barras
 * ficariam invisiveis e o operador nao veria a variacao que importa. Piso de
 * 2% para que um dia com taxa pequena, mas nao nula, continue visivel.
 */
export function alturaDaBarra(dia: TikTokLdrDia, maximo: number): number {
  if (dia.rate === null || maximo <= 0) return 0;
  return Math.max(2, (dia.rate / maximo) * 100);
}

export function taxaMaxima(dias: TikTokLdrDia[]): number {
  return dias.reduce((max, d) => (d.rate !== null && d.rate > max ? d.rate : max), 0);
}

/** Onde desenhar a linha da meta no grafico, em % da altura. */
export function alturaDaMeta(maximo: number, meta = META_TIKTOK_LDR): number | null {
  if (maximo <= 0 || meta > maximo) return null;
  return (meta / maximo) * 100;
}

// ---------------------------------------------------------------------------
// EXPEDICAO-UX-UNIFICADA-1 — o TikTok na mesma hierarquia dos outros canais
// ---------------------------------------------------------------------------

/**
 * Codigo do alerta de frescor que a API publica (`_alertas` em
 * `expedicao_tiktok_service.py`, acima de 12 h). E' a UNICA fonte do veredito
 * "serie desatualizada" nesta tela: o payload nao traz a idade, e a tela nao
 * subtrai relogios para inventar uma nem redigita o limiar da API.
 */
export const CODIGO_FOTOGRAFIA_VELHA = "fotografia_velha";

/**
 * O payload do TikTok NAO informa como a serie foi publicada (nao existe
 * `load_mode` nem `no_automation` nele). A tela diz isso, em vez de herdar o
 * "atualizada a mao" da fila ou de presumir agendamento.
 */
export const AUTOMACAO_NAO_INFORMADA = "Modo de publicação não informado";

export function serieDesatualizada(p: TikTokPayload | null): boolean {
  return Boolean(p?.alerts.some((a) => a.code === CODIGO_FOTOGRAFIA_VELHA));
}

export function fotografiaDoTikTok(p: TikTokPayload | null): Fotografia | null {
  if (!p?.snapshot) return null;
  return {
    rotulo: "Série",
    publicadaEm: p.snapshot.effective_at,
    idade: null,
    desatualizada: serieDesatualizada(p),
    automacao: AUTOMACAO_NAO_INFORMADA,
    fonteLidaAte: p.snapshot.source_watermark_at,
  };
}

const TOM_DO_ALERTA: Record<TikTokAlerta["severity"], TomAtencao> = {
  critical: "critico",
  warning: "atencao",
  // O unico `info` publicado hoje e' "dentro da meta — medicao interna".
  info: "bom",
};

/**
 * "O que exige atenção agora" do TikTok: os alertas QUE A API JA' ESCREVEU,
 * com o texto dela, na ordem dela. A tela nao reimplementa limiar nenhum —
 * senao o alerta e o destaque do grafico discordariam na mesma pagina.
 *
 * Sai daqui so' o alerta de frescor: ele descreve o DADO, nao a operacao, e
 * vai para a barra de qualidade e para o selo do cabecalho.
 */
export function montarAtencaoDoTikTok(p: TikTokPayload | null): ItemAtencao[] {
  if (!p) return [];
  return p.alerts
    .filter((a) => a.code !== CODIGO_FOTOGRAFIA_VELHA)
    .map((a) => ({ chave: a.code, tom: TOM_DO_ALERTA[a.severity] ?? "neutro", texto: a.message }));
}

/**
 * A barra de qualidade do TikTok — a mesma barra da fila, preenchida com o
 * que este contrato sabe. Substitui a pilha de banners (aviso de prazo +
 * um banner por alerta) que empurrava o primeiro numero para baixo da dobra.
 *
 * Ordem no empate de severidade, de proposito: serie desatualizada primeiro
 * (e' o que muda a leitura de TODOS os numeros abaixo), prazo reconstruido
 * depois. O aviso de prazo nunca some: fica na lista e, alem disso, colado ao
 * periodo consultado, ao lado da LDR.
 */
export function montarBarraDeQualidadeTikTok(p: TikTokPayload | null): BarraQualidade {
  if (!p) return consolidarQualidade([]);
  const itens: ProblemaQualidade[] = [];
  const velha = p.alerts.find((a) => a.code === CODIGO_FOTOGRAFIA_VELHA);
  if (velha) {
    itens.push({
      chave: "serie_desatualizada",
      severidade: "atencao",
      texto:
        `Série desatualizada. ${velha.message} Os números descrevem aquela publicação, ` +
        "não agora.",
    });
  }
  if (p.deadline_is_reconstructed) {
    itens.push({ chave: "prazo_reconstruido", severidade: "atencao", texto: AVISO_PRAZO_RECONSTRUIDO });
  }
  const parciais = p.ldr?.partial_due_days ?? 0;
  if (parciais > 0) {
    itens.push({
      chave: "vencimentos_parciais",
      severidade: "info",
      texto:
        `${parciais} vencimento(s) da janela ainda no prazo: a taxa desses dias ainda ` +
        "vai mudar e fica fora da LDR.",
    });
  }
  if (p.availability === "available") {
    itens.push({
      chave: "modo_publicacao",
      severidade: "info",
      texto:
        "A API do TikTok não informa se a série é publicada à mão ou por " +
        "agendamento. O instante da última publicação está no cabeçalho.",
    });
  }
  return consolidarQualidade(itens);
}

/**
 * A serie tem algum vencimento FECHADO com taxa? Sem nenhum, o grafico so'
 * desenharia barras hachuradas ou vazias — um retangulo enorme sem informacao
 * util, que era o que a tela mostrava com a fonte vencida. Nesse caso a tela
 * troca o grafico por uma linha que diz por que nao ha o que desenhar.
 */
export function serieTemDiaMedido(dias: TikTokLdrDia[]): boolean {
  return dias.some((d) => d.is_mature && d.rate !== null);
}

/** URL da API. Mantem a mesma forma de query usada pelos outros canais. */
export function queryDoTikTok(evento: Evento, dias: number): string {
  const p = new URLSearchParams();
  p.set("event", evento);
  p.set("days", String(dias));
  return `/api/v1/expedicao/tiktok?${p.toString()}`;
}
