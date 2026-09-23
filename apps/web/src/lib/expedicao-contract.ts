/**
 * Gate EXP-2B — contrato da tela de Expedicao.
 *
 * Toda a logica que a tela usa mora aqui, em funcoes puras, para ser testavel
 * sem harness de React. O componente so' pinta o que estas funcoes devolvem.
 *
 * TRES RELOGIOS QUE NAO PODEM SER O MESMO
 * ----------------------------------------
 *   source_age_hours      ha quanto tempo a FONTE (Data Mart) foi lida.
 *                         E' o unico insumo do veredito de frescor.
 *   snapshot_age_hours    ha quanto tempo a FOTOGRAFIA foi publicada. Como nao
 *                         existe agendamento, isto cresce ate alguem rodar o
 *                         refresh a mao.
 *   oldest_row_age_hours  idade da linha mais antiga do backlog. CONTEXTO: um
 *                         backlog legitimo sempre tem pedido velho, e isso NAO
 *                         significa fonte desatualizada.
 *
 * Confundir os dois primeiros fez as quatro marcas nascerem vermelhas no
 * primeiro piloto (EXP-1E). A tela repete a separacao em rotulos distintos.
 *
 * SEM IDENTIFICADOR DE PEDIDO
 * ---------------------------
 * A API nao serve `order_sn` porque nao tem autenticacao. `order_ref` e' opaco,
 * opcional e NUNCA vira link para o marketplace. A tela nao tenta reconstruir
 * o identificador real nem exibe coluna vazia quando ele nao vem.
 */

/** Os oito valores de `situacao` aceitos pela API. Allowlist fechada. */
export const SITUACOES = [
  "overdue",
  "due_within_24h",
  "on_time",
  "deadline_unavailable",
  "over_48h",
  "stalled",
  "slow",
  "zombie",
] as const;
export type Situacao = (typeof SITUACOES)[number];

/** As tres ordenacoes aceitas pela API. */
export const ORDENACOES = ["criticidade", "deadline", "oldest"] as const;
export type Ordenacao = (typeof ORDENACOES)[number];

export const LIMIT_MAX = 500;
export const LIMIT_PADRAO = 100;
export const JANELA_MAX_HORAS = 336;
export const JANELA_PADRAO_HORAS = 48;

/**
 * Limiar OPERACIONAL INTERNO da Torre. Nao e' SLA do marketplace nem promessa
 * ao cliente: o prazo contratual vive em `deadline_status`, que vem do
 * `ship_by_date` da Shopee. Sao coisas independentes — medimos 206 pedidos com
 * mais de 48h ainda dentro do prazo nativo.
 */
export const LIMIAR_OPERACIONAL_HORAS = 48;
export const ROTULO_LIMIAR_48H = "Acima de 48h (limiar interno da Torre)";
export const EXPLICACAO_LIMIAR_48H =
  "Limiar operacional interno da Torre, nao e' SLA do marketplace: o prazo " +
  "contratual aparece em Vencidos / Vence em 24h, que vem do ship_by_date da " +
  "Shopee.";

export const AVISO_SEM_AUTOMACAO =
  "Sem automacao: a fotografia so' avanca quando alguem executa o refresh " +
  "manualmente.";

/** Acima disto a fotografia e' considerada velha e a tela avisa. */
export const SNAPSHOT_VELHO_HORAS = 6;

// ---------------------------------------------------------------------------
// Feature flag
// ---------------------------------------------------------------------------
/**
 * FAIL-CLOSED: so' a string exata "true" liga. Ausente, vazio, "1", "TRUE",
 * "yes" ou qualquer outra coisa resolve `false`.
 *
 * Um parser permissivo aqui transformaria erro de digitacao em exposicao de
 * dado operacional numa API que nao tem autenticacao.
 */
export function expedicaoHabilitado(valor: string | undefined | null): boolean {
  return valor === "true";
}

/**
 * Flag SEPARADA para o Mercado Livre, com o mesmo parser fail-closed.
 *
 * Nao substitui `NEXT_PUBLIC_EXPEDICAO_ENABLED`, que continua controlando a
 * rota inteira: sao duas decisoes distintas, e amarrar as duas na mesma flag
 * obrigaria a derrubar a Shopee para adiar o ML.
 */
export function expedicaoMlHabilitado(valor: string | undefined | null): boolean {
  return valor === "true";
}

// ---------------------------------------------------------------------------
// Canais
// ---------------------------------------------------------------------------
export const CANAIS = ["shopee", "mercadolivre"] as const;
export type Canal = (typeof CANAIS)[number];

/** Canal historico da tela. Omitir `channel` na API significa exatamente isto. */
export const CANAL_PADRAO: Canal = "shopee";

export const ROTULO_CANAL: Record<Canal, string> = {
  shopee: "Shopee",
  mercadolivre: "Mercado Livre",
};

/**
 * Canal vindo da URL -> canal servivel. Fora da allowlist, ou ML com a flag
 * desligada, cai no padrao. A tela nunca pede um canal que nao pode mostrar.
 */
export function sanitizarCanal(
  valor: string | undefined | null,
  mlHabilitado: boolean,
): Canal {
  if (valor === "mercadolivre" && mlHabilitado) return "mercadolivre";
  return CANAL_PADRAO;
}

/**
 * O Mercado Livre NAO publica prazo de despacho — medido no EXP-3A, os campos
 * candidatos estao 0% preenchidos na fonte. Entao `overdue`, `due_within_24h` e
 * `on_time` sao ZERO por ausencia de dado, nao por desempenho. Exibir "0
 * vencidos" para o ML seria uma afirmacao que ninguem mediu.
 */
export const SEM_PRAZO_ML =
  "O Mercado Livre nao publica prazo de despacho: nao ha vencidos nem 'no " +
  "prazo' a medir. Zero aqui seria engano, entao a tela mostra N/D.";

export const ROTULO_SEM_PRAZO = "Prazo indisponível";
export const VALOR_SEM_PRAZO = "N/D";

/** O canal tem prazo contratual publicado pela fonte? */
export function canalTemPrazo(canal: Canal): boolean {
  return canal === "shopee";
}

// ---------------------------------------------------------------------------
// Tipos do payload (espelham os schemas da API)
// ---------------------------------------------------------------------------
export type Availability = "available" | "unavailable";
export type Freshness = "fresh" | "stale" | "critical" | "unknown";

export interface Snapshot {
  channel: string;
  refresh_batch_id: string;
  effective_at: string;
  snapshot_hour: string;
  load_mode: string;
  source_health: string;
  source_advanced: boolean;
  run_status: string | null;
  rows_extracted: number | null;
  rows_loaded: number | null;
  audit_run_id: number | null;
}

export interface Totais {
  backlog_count: number;
  overdue_count: number;
  due_within_24h_count: number;
  on_time_count: number;
  deadline_unavailable_count: number;
  over_48h_count: number;
  slow_count: number;
  zombie_count: number;
  stalled_count: number;
}

export interface ResumoConta extends Totais {
  shop_account: string;
  brand: string;
  run_status: string;
  source_watermark_at: string | null;
  source_advanced: boolean;
  snapshot_hour: string;
}

export interface FrescorMarca {
  brand: string;
  freshness: Freshness;
  status: string | null;
  severity: string | null;
  source_watermark_at: string | null;
  source_age_hours: number | null;
  oldest_row_age_hours: number | null;
  accounts: number | null;
  observed_at: string | null;
  measures: string | null;
  derived_from_batch: boolean;
}

export interface CoberturaConta {
  shop_account: string;
  brand: string;
  observed: boolean;
  backlog_count: number | null;
  source_watermark_at: string | null;
  source_age_hours: number | null;
  source_advanced: boolean | null;
}

export interface Cobertura {
  expected_accounts: string[];
  observed_accounts: string[];
  missing_accounts: string[];
  unexpected_accounts: string[];
  accounts: CoberturaConta[];
  brands_not_covered: string[];
}

export interface LinhaFila {
  order_ref: string | null;
  shop_account: string;
  brand: string;
  created_at: string | null;
  paid_at: string | null;
  dispatch_deadline: string | null;
  deadline_source: string;
  deadline_status: string;
  operational_age_status: string;
  is_slow_vs_baseline: boolean;
  is_source_zombie: boolean;
  is_stalled: boolean;
  hours_open: number | null;
  hours_overdue: number | null;
  logistic_type: string | null;
  carrier: string | null;
  source_ingested_at: string | null;
  source_freshness_status: string;
  timestamp_quality: string;
}

export interface Paginacao {
  limit: number;
  offset: number;
  returned: number;
  total: number;
  has_more: boolean;
}

export interface Limitacoes {
  no_automation: boolean;
  load_mode: string;
  snapshot_age_hours: number | null;
  brands_not_covered: string[];
  order_identifier_withheld: boolean;
  notes: string[];
}

export interface RespostaExpedicao {
  availability: Availability;
  unavailable_reason: string | null;
  channel: string;
  snapshot: Snapshot | null;
  totals: Totais | null;
  accounts: ResumoConta[];
  freshness: FrescorMarca[];
  coverage: Cobertura | null;
  queue: LinhaFila[];
  pagination: Paginacao | null;
  limitations: Limitacoes;
}

export interface PontoTendencia extends Totais {
  snapshot_hour: string;
  shop_account: string;
  brand: string;
  refresh_batch_id: string;
  observed_at: string;
  source_watermark_at: string | null;
  source_advanced: boolean;
  run_status: string;
}

export interface RespostaTendencia {
  availability: Availability;
  unavailable_reason: string | null;
  channel: string;
  window_hours: number;
  from_hour: string | null;
  to_hour: string | null;
  points: PontoTendencia[];
  truncated: boolean;
  limitations: Limitacoes;
}

// ---------------------------------------------------------------------------
// Filtros e URL
// ---------------------------------------------------------------------------
export interface Filtros {
  channel: Canal;
  brands: string[];
  accounts: string[];
  situacao: Situacao[];
  orderBy: Ordenacao;
  limit: number;
  offset: number;
}

export const FILTROS_PADRAO: Filtros = {
  channel: CANAL_PADRAO,
  brands: [],
  accounts: [],
  situacao: [],
  orderBy: "criticidade",
  limit: LIMIT_PADRAO,
  offset: 0,
};

/** Descarta valor fora da allowlist em vez de mandar para a API e tomar 422. */
export function sanitizarSituacoes(valores: readonly string[]): Situacao[] {
  return valores.filter((v): v is Situacao =>
    (SITUACOES as readonly string[]).includes(v),
  );
}

export function sanitizarOrdenacao(valor: string): Ordenacao {
  return (ORDENACOES as readonly string[]).includes(valor)
    ? (valor as Ordenacao)
    : "criticidade";
}

export function sanitizarLimit(valor: number): number {
  if (!Number.isFinite(valor)) return LIMIT_PADRAO;
  return Math.min(LIMIT_MAX, Math.max(1, Math.trunc(valor)));
}

export function sanitizarJanela(valor: number): number {
  if (!Number.isFinite(valor)) return JANELA_PADRAO_HORAS;
  return Math.min(JANELA_MAX_HORAS, Math.max(1, Math.trunc(valor)));
}

/**
 * `channel` so' entra quando NAO e' o canal historico.
 *
 * Omitir `channel` significa `shopee` na API, entao a requisicao da Shopee sai
 * byte a byte igual a de antes deste gate — o que mantem a tela atual
 * verificavelmente intacta enquanto a flag do ML estiver desligada.
 */
export function construirQuery(f: Filtros): string {
  const p = new URLSearchParams();
  if (f.channel !== CANAL_PADRAO) p.set("channel", f.channel);
  if (f.brands.length) p.set("brands", f.brands.join(","));
  if (f.accounts.length) p.set("accounts", f.accounts.join(","));
  const sit = sanitizarSituacoes(f.situacao);
  if (sit.length) p.set("situacao", sit.join(","));
  p.set("order_by", sanitizarOrdenacao(f.orderBy));
  p.set("limit", String(sanitizarLimit(f.limit)));
  p.set("offset", String(Math.max(0, Math.trunc(f.offset))));
  return p.toString();
}

/**
 * Chave da requisicao — mesmo padrao de `request-freshness` usado nas outras
 * telas. Duas trocas rapidas de filtro geram chaves diferentes, e a resposta
 * que chega com chave antiga e' DESCARTADA em vez de sobrescrever a atual.
 */
export function chaveDaRequisicao(f: Filtros): string {
  return construirQuery(f);
}

/**
 * Query da tendencia, com o MESMO canal da fila.
 *
 * Existe para que as duas requisicoes nao possam divergir de canal: o grafico
 * de uma loja embaixo da fila de outra e' pior que grafico nenhum.
 */
export function queryDaTendencia(janela: number, canal: Canal): string {
  const p = new URLSearchParams();
  if (canal !== CANAL_PADRAO) p.set("channel", canal);
  p.set("window_hours", String(sanitizarJanela(janela)));
  return p.toString();
}

/**
 * Situacoes que dependem de PRAZO CONTRATUAL. Num canal sem prazo elas voltam
 * vazias por construcao — filtrar por "vencidos" no Mercado Livre nao devolve
 * zero pedidos vencidos, devolve zero porque a pergunta nao existe la'.
 *
 * `deadline_unavailable` NAO entra: no ML ela e' o backlog inteiro, e e' uma
 * pergunta legitima.
 */
export const SITUACOES_DE_PRAZO: readonly Situacao[] = [
  "overdue",
  "due_within_24h",
  "on_time",
];

/**
 * Troca de canal. O que atravessa e o que NAO atravessa, decidido campo a
 * campo em vez de por descuido:
 *
 * - `accounts` NAO atravessa. A identidade da conta e' do canal: nome da loja
 *   na Shopee, `seller_id` no Mercado Livre. Levar `apice` para o ML, ou
 *   `2227056661` para a Shopee, produz uma tela vazia que parece backlog zero
 *   e e' filtro incompativel.
 * - `brands` ATRAVESSA. Marca e' a mesma entidade nos dois canais, e o recorte
 *   por marca e' uma intencao do operador que sobrevive a troca. Marca que so'
 *   existe num canal devolve vazio — e' resultado, nao incoerencia.
 * - situacoes de PRAZO e ordenacao por prazo caem quando o canal de destino
 *   nao publica prazo: manter uma pergunta que a fonte nao responde exibiria
 *   vazio como se fosse resposta.
 * - `offset` volta a zero, como em qualquer troca de recorte.
 */
export function aplicarCanal(atual: Filtros, canal: Canal): Filtros {
  const perdePrazo = !canalTemPrazo(canal);
  return aplicarFiltro(atual, {
    channel: canal,
    accounts: [],
    situacao: perdePrazo
      ? atual.situacao.filter((s) => !SITUACOES_DE_PRAZO.includes(s))
      : atual.situacao,
    orderBy: perdePrazo && atual.orderBy === "deadline" ? "criticidade" : atual.orderBy,
  });
}

/** Trocar filtro reinicia a pagina: manter o offset mostraria pagina vazia. */
export function aplicarFiltro(atual: Filtros, mudanca: Partial<Filtros>): Filtros {
  const mudouRecorte =
    mudanca.channel !== undefined ||
    mudanca.brands !== undefined ||
    mudanca.accounts !== undefined ||
    mudanca.situacao !== undefined ||
    mudanca.orderBy !== undefined ||
    mudanca.limit !== undefined;
  return {
    ...atual,
    ...mudanca,
    offset: mudanca.offset !== undefined ? mudanca.offset : mudouRecorte ? 0 : atual.offset,
  };
}

// ---------------------------------------------------------------------------
// Estado da tela
// ---------------------------------------------------------------------------
export type EstadoTela =
  | "carregando"
  | "canal_desligado"
  | "ok"
  | "fotografia_vazia"
  | "fila_vazia_por_filtro"
  | "indisponivel_backend"
  | "batch_inconsistente"
  | "sem_snapshot"
  | "erro_validacao"
  | "erro";

export interface EntradaEstado {
  carregando: boolean;
  erroHttp: number | null;
  resposta: RespostaExpedicao | null;
  temFiltro: boolean;
}

export function estadoDaTela(e: EntradaEstado): EstadoTela {
  if (e.carregando) return "carregando";
  if (e.erroHttp === 422) return "erro_validacao";
  if (e.erroHttp !== null) return "erro";
  if (!e.resposta) return "erro";
  if (e.resposta.availability === "unavailable") {
    switch (e.resposta.unavailable_reason) {
      case "feature_flag_disabled":
        return "indisponivel_backend";
      case "channel_disabled":
        // O BACKEND ainda nao expoe este canal. E' diferente de "sem
        // fotografia": nao ha o que republicar, ha uma flag a ligar.
        return "canal_desligado";
      case "inconsistent_batch":
        return "batch_inconsistente";
      default:
        return "sem_snapshot";
    }
  }
  const total = e.resposta.totals?.backlog_count ?? 0;
  if (total === 0) return "fotografia_vazia";
  if ((e.resposta.pagination?.total ?? 0) === 0 && e.temFiltro) {
    return "fila_vazia_por_filtro";
  }
  return "ok";
}

// ---------------------------------------------------------------------------
// KPIs
// ---------------------------------------------------------------------------
export interface Kpi {
  chave: string;
  rotulo: string;
  valor: number | null;
  /** `true` pinta o cartao como alerta. Nunca inventa severidade. */
  alerta: boolean;
  nota?: string;
  /**
   * Texto que substitui o numero quando a medida NAO EXISTE na fonte —
   * "N/D", nunca "0". Zero e' uma medicao; ausencia nao e'.
   */
  valorTexto?: string;
}

/**
 * Cartoes derivados SOMENTE do payload. Medida ausente vira `null`, nunca 0:
 * zero afirmaria uma medicao que nao foi feita.
 *
 * Nao existe faixa "entre 24h e 48h" no contrato: as faixas de PRAZO
 * (`overdue`, `due_within_24h`, `on_time`, `unavailable`) e a faixa de IDADE
 * OPERACIONAL (`over_48h`) sao dimensoes ortogonais e nao se recortam. Inventar
 * a interseccao aqui seria calculo que a API nao fez.
 */
export function montarKpis(
  t: Totais | null,
  frescor: FrescorMarca[],
  canal: Canal = CANAL_PADRAO,
): Kpi[] {
  const v = (n: number | undefined | null) => (typeof n === "number" ? n : null);
  const emAlerta = frescor.filter(
    (f) => f.freshness === "critical" || f.freshness === "unknown",
  ).length;
  const temPrazo = canalTemPrazo(canal);
  // Sem prazo na fonte, `overdue` e `due_within_24h` valem zero por AUSENCIA de
  // dado. Publicar "0 vencidos" leria como operacao impecavel; o cartao mostra
  // N/D e diz por que. Nunca alerta: nao ha medicao para alarmar.
  const prazo = (chave: string, rotulo: string, valor: number | null, alerta: boolean): Kpi =>
    temPrazo
      ? { chave, rotulo, valor, alerta }
      : {
          chave,
          rotulo,
          valor: null,
          alerta: false,
          valorTexto: VALOR_SEM_PRAZO,
          nota: SEM_PRAZO_ML,
        };
  return [
    { chave: "backlog", rotulo: "Backlog total", valor: v(t?.backlog_count), alerta: false },
    prazo(
      "overdue",
      temPrazo ? "Vencidos (prazo Shopee)" : "Vencidos",
      v(t?.overdue_count),
      (t?.overdue_count ?? 0) > 0,
    ),
    prazo("due24h", "Vence em 24h", v(t?.due_within_24h_count), false),
    ...(temPrazo
      ? []
      : [
          {
            chave: "sem_prazo",
            rotulo: ROTULO_SEM_PRAZO,
            valor: v(t?.deadline_unavailable_count),
            alerta: false,
            nota:
              "Pedidos sem prazo de despacho publicado pela fonte. E' o backlog" +
              " inteiro do canal, nao uma anomalia.",
          } as Kpi,
        ]),
    {
      chave: "over48h",
      rotulo: ROTULO_LIMIAR_48H,
      valor: v(t?.over_48h_count),
      alerta: (t?.over_48h_count ?? 0) > 0,
      nota: EXPLICACAO_LIMIAR_48H,
    },
    {
      chave: "stalled",
      rotulo: "Travados",
      valor: v(t?.stalled_count),
      alerta: (t?.stalled_count ?? 0) > 0,
      nota: "Uniao de lentos e zumbis — nunca a soma dos dois.",
    },
    { chave: "slow", rotulo: "Lentos vs baseline", valor: v(t?.slow_count), alerta: false },
    { chave: "zombie", rotulo: "Zumbis", valor: v(t?.zombie_count), alerta: (t?.zombie_count ?? 0) > 0 },
    {
      chave: "marcas_alerta",
      rotulo: "Marcas com fonte em alerta",
      valor: frescor.length ? emAlerta : null,
      alerta: emAlerta > 0,
    },
  ];
}

/** O pedido cruzou o limiar interno? Fronteira: 48h EXATAS ainda nao cruzou. */
export function acimaDoLimiarInterno(horasAbertas: number | null): boolean {
  if (horasAbertas === null) return false;
  return horasAbertas > LIMIAR_OPERACIONAL_HORAS;
}

// ---------------------------------------------------------------------------
// Frescor e cobertura
// ---------------------------------------------------------------------------
export interface LinhaFrescor {
  brand: string;
  freshness: Freshness;
  rotulo: string;
  /** Idade da FONTE. Nunca a idade do pedido. */
  sourceAgeHours: number | null;
  /** CONTEXTO: idade do pedido mais antigo. Nao reprova a fonte. */
  oldestRowAgeHours: number | null;
  watermark: string | null;
  derivado: boolean;
  alerta: boolean;
}

const ROTULO_FRESCOR: Record<Freshness, string> = {
  fresh: "Fonte atualizada",
  stale: "Fonte atrasando",
  critical: "Fonte desatualizada",
  unknown: "Frescor desconhecido",
};

export function montarFrescor(itens: FrescorMarca[]): LinhaFrescor[] {
  return itens.map((f) => ({
    brand: f.brand,
    freshness: f.freshness,
    rotulo: ROTULO_FRESCOR[f.freshness] ?? ROTULO_FRESCOR.unknown,
    sourceAgeHours: f.source_age_hours,
    oldestRowAgeHours: f.oldest_row_age_hours,
    watermark: f.source_watermark_at,
    derivado: f.derived_from_batch,
    alerta: f.freshness === "critical" || f.freshness === "unknown",
  }));
}

export interface AvisoCobertura {
  tipo: "faltando" | "inesperada" | "fora_do_escopo";
  texto: string;
}

/**
 * Marca fora do escopo NAO e' conta faltando e NAO entra em denominador: ela
 * simplesmente nao existe nesta fonte. Mostra-la como zero afirmaria backlog
 * zero, que e' medicao — e nao foi feita.
 */
export function avisosDeCobertura(c: Cobertura | null): AvisoCobertura[] {
  if (!c) return [];
  const avisos: AvisoCobertura[] = [];
  if (c.missing_accounts.length) {
    avisos.push({
      tipo: "faltando",
      texto: `Conta esperada ausente da fotografia: ${c.missing_accounts.join(", ")}.`,
    });
  }
  if (c.unexpected_accounts.length) {
    avisos.push({
      tipo: "inesperada",
      texto: `Conta observada sem cadastro: ${c.unexpected_accounts.join(", ")}.`,
    });
  }
  for (const marca of c.brands_not_covered) {
    avisos.push({
      tipo: "fora_do_escopo",
      texto:
        `${marca} nao e' coberta por esta fonte e por isso nao aparece nos ` +
        "numeros — ausencia de cobertura, nao backlog zero.",
    });
  }
  return avisos;
}

// ---------------------------------------------------------------------------
// Relogios
// ---------------------------------------------------------------------------
export interface Relogios {
  snapshotAgeHours: number | null;
  snapshotVelho: boolean;
  efetivoEm: string | null;
  modoDeCarga: string;
  semAutomacao: boolean;
}

export function montarRelogios(r: RespostaExpedicao): Relogios {
  const idade = r.limitations?.snapshot_age_hours ?? null;
  return {
    snapshotAgeHours: idade,
    snapshotVelho: idade !== null && idade > SNAPSHOT_VELHO_HORAS,
    efetivoEm: r.snapshot?.effective_at ?? null,
    modoDeCarga: r.limitations?.load_mode ?? "manual_snapshot",
    semAutomacao: r.limitations?.no_automation !== false,
  };
}

// ---------------------------------------------------------------------------
// Tendencia
// ---------------------------------------------------------------------------
export interface SerieConta {
  shopAccount: string;
  brand: string;
  pontos: { hora: string; backlog: number; over48h: number; stalled: number }[];
}

/**
 * Uma serie POR CONTA. Nao soma contas nem horas: o mesmo pedido continua no
 * backlog de uma hora para a outra, e somar dois pontos o contaria duas vezes.
 */
export function montarSeries(pontos: PontoTendencia[]): SerieConta[] {
  const mapa = new Map<string, SerieConta>();
  for (const p of pontos) {
    const s = mapa.get(p.shop_account) ?? {
      shopAccount: p.shop_account,
      brand: p.brand,
      pontos: [],
    };
    s.pontos.push({
      hora: p.snapshot_hour,
      backlog: p.backlog_count,
      over48h: p.over_48h_count,
      stalled: p.stalled_count,
    });
    mapa.set(p.shop_account, s);
  }
  for (const s of mapa.values()) {
    s.pontos.sort((a, b) => a.hora.localeCompare(b.hora));
  }
  return [...mapa.values()].sort((a, b) => a.shopAccount.localeCompare(b.shopAccount));
}

export type EstadoTendencia = "carregando" | "ok" | "vazia" | "parcial" | "erro";

/** Uma hora com menos contas que o esperado e' PARCIAL, nao um vale real. */
export function estadoDaTendencia(
  carregando: boolean,
  erro: boolean,
  series: SerieConta[],
  contasEsperadas: number,
): EstadoTendencia {
  if (carregando) return "carregando";
  if (erro) return "erro";
  if (!series.length) return "vazia";
  const porHora = new Map<string, number>();
  for (const s of series) {
    for (const p of s.pontos) porHora.set(p.hora, (porHora.get(p.hora) ?? 0) + 1);
  }
  for (const n of porHora.values()) if (n < contasEsperadas) return "parcial";
  return "ok";
}

// ---------------------------------------------------------------------------
// Fila
// ---------------------------------------------------------------------------
/**
 * A coluna de referencia so' existe quando a API devolve `order_ref`. Sem ela,
 * a tela nao mostra coluna vazia nem texto sugerindo que o identificador foi
 * omitido por erro — ele foi retido por decisao de acesso.
 */
export function mostrarColunaReferencia(linhas: LinhaFila[]): boolean {
  return linhas.some((l) => typeof l.order_ref === "string" && l.order_ref.length > 0);
}

export function paginaAtual(p: Paginacao | null): { pagina: number; total: number } {
  if (!p || p.limit <= 0) return { pagina: 1, total: 1 };
  return {
    pagina: Math.floor(p.offset / p.limit) + 1,
    total: Math.max(1, Math.ceil(p.total / p.limit)),
  };
}

export function formatarHoras(h: number | null): string {
  if (h === null || !Number.isFinite(h)) return "—";
  if (h < 1) return `${Math.round(h * 60)} min`;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${Math.floor(h / 24)} d`;
}

// ---------------------------------------------------------------------------
// EXP-UX-1 — hierarquia "resumo primeiro, profundidade sob demanda"
// ---------------------------------------------------------------------------

/**
 * As QUATRO perguntas que a tela responde primeiro, por canal.
 *
 * Nao sao as mesmas nos dois: a Shopee tem prazo contratual e a pergunta certa
 * e' "o que venceu". O Mercado Livre nao tem prazo nenhum, entao perguntar
 * "quantos venceram" devolveria N/D quatro vezes e desperdicaria a area mais
 * nobre da tela. La' a pergunta e' idade operacional e saude da fonte.
 */
export const KPIS_PRINCIPAIS: Record<Canal, readonly string[]> = {
  shopee: ["backlog", "overdue", "due24h", "stalled"],
  mercadolivre: ["backlog", "over48h", "stalled", "marcas_alerta"],
};

/** Os quatro cartoes de destaque, na ordem declarada para o canal. */
export function montarKpisPrincipais(
  t: Totais | null,
  frescor: FrescorMarca[],
  canal: Canal = CANAL_PADRAO,
): Kpi[] {
  const todos = montarKpis(t, frescor, canal);
  const ordem = KPIS_PRINCIPAIS[canal];
  return ordem
    .map((chave) => todos.find((k) => k.chave === chave))
    .filter((k): k is Kpi => k !== undefined);
}

/** O resto, na faixa secundaria compacta. Nada e' descartado da tela. */
export function montarKpisSecundarios(
  t: Totais | null,
  frescor: FrescorMarca[],
  canal: Canal = CANAL_PADRAO,
): Kpi[] {
  const principais = new Set(KPIS_PRINCIPAIS[canal]);
  return montarKpis(t, frescor, canal).filter((k) => !principais.has(k.chave));
}

// ---------------------------------------------------------------------------
// Barra consolidada de qualidade do dado
// ---------------------------------------------------------------------------
export type SeveridadeQualidade = "alerta" | "atencao" | "info";

export interface ProblemaQualidade {
  chave: string;
  severidade: SeveridadeQualidade;
  texto: string;
}

export interface BarraQualidade {
  /** O problema que o operador precisa ler primeiro. `null` = tudo em ordem. */
  principal: ProblemaQualidade | null;
  /** Todos, inclusive o principal, para o painel expansivel. */
  detalhes: ProblemaQualidade[];
  severidade: SeveridadeQualidade | null;
}

const PESO_SEVERIDADE: Record<SeveridadeQualidade, number> = {
  alerta: 0,
  atencao: 1,
  info: 2,
};

/**
 * Consolida a pilha de banners numa barra so'.
 *
 * NADA e' escondido: tudo que hoje vira banner continua na lista de detalhes,
 * e o item mais grave aparece fechado. Defasagem, ausencia de automacao e
 * lacuna de cobertura entram sempre — sao exatamente as limitacoes que o
 * operador precisa ver antes de confiar no numero.
 */
export function montarBarraDeQualidade(
  relogios: Relogios | null,
  avisos: AvisoCobertura[],
  frescor: LinhaFrescor[],
): BarraQualidade {
  const itens: ProblemaQualidade[] = [];

  if (relogios?.snapshotVelho) {
    itens.push({
      chave: "fotografia_antiga",
      severidade: "atencao",
      texto:
        `Fotografia publicada há ${formatarHoras(relogios.snapshotAgeHours)}: os ` +
        "números descrevem aquele instante, não agora.",
    });
  }
  if (relogios?.semAutomacao !== false) {
    itens.push({
      chave: "sem_automacao",
      severidade: "info",
      texto: AVISO_SEM_AUTOMACAO,
    });
  }
  for (const a of avisos) {
    itens.push({
      chave: `cobertura_${a.tipo}_${a.texto.slice(0, 24)}`,
      // Conta esperada ausente e conta sem cadastro sao falhas de cobertura.
      // Marca fora do escopo e' informacao de contrato, nao defeito.
      severidade: a.tipo === "fora_do_escopo" ? "info" : "alerta",
      texto: a.texto,
    });
  }
  const emAlerta = frescor.filter((f) => f.alerta);
  if (emAlerta.length) {
    itens.push({
      chave: "fonte_em_alerta",
      severidade: "alerta",
      texto:
        `${emAlerta.length} marca(s) com a fonte em alerta: ` +
        `${emAlerta.map((f) => f.brand).join(", ")}.`,
    });
  }

  const ordenados = [...itens].sort(
    (a, b) => PESO_SEVERIDADE[a.severidade] - PESO_SEVERIDADE[b.severidade],
  );
  return {
    principal: ordenados[0] ?? null,
    detalhes: ordenados,
    severidade: ordenados[0]?.severidade ?? null,
  };
}

// ---------------------------------------------------------------------------
// Mapa de risco por conta
// ---------------------------------------------------------------------------
export interface FaixaRisco {
  chave: string;
  rotulo: string;
  valor: number;
}

export interface LinhaRisco {
  conta: string;
  marca: string;
  backlog: number;
  /** Categorias MUTUAMENTE EXCLUSIVAS. Vazio quando o canal nao as tem. */
  segmentos: FaixaRisco[];
  /** Medidas que SE SOBREPOEM ao backlog e entre si. Nunca empilhadas. */
  sobreposicoes: FaixaRisco[];
}

export interface MapaRisco {
  /**
   * `true` so' quando os segmentos particionam o backlog. Empilhar medidas
   * sobrepostas desenharia uma barra maior que o proprio backlog e sugeriria
   * um total que nao existe.
   */
  empilhavel: boolean;
  linhas: LinhaRisco[];
  /** Maior backlog da lista: escala comum das barras. */
  maximo: number;
  nota: string;
}

/**
 * Shopee: `overdue + due_within_24h + on_time + deadline_unavailable` e' uma
 * PARTICAO do backlog — o pipeline garante a soma exata, e por isso a barra
 * pode ser empilhada.
 *
 * Mercado Livre: `backlog`, `over_48h` e `stalled` se SOBREPOEM. Um pedido
 * acima de 48h tambem esta no backlog, e pode estar travado ao mesmo tempo.
 * Empilhar as tres inventaria um total. La' a barra mostra o backlog e as
 * outras duas entram como marcadores sobre ela.
 */
export function montarMapaDeRisco(
  contas: ResumoConta[],
  canal: Canal = CANAL_PADRAO,
): MapaRisco {
  const temPrazo = canalTemPrazo(canal);
  const linhas: LinhaRisco[] = contas.map((a) => ({
    conta: a.shop_account,
    marca: a.brand,
    backlog: a.backlog_count,
    segmentos: temPrazo
      ? [
          { chave: "overdue", rotulo: "Vencidos", valor: a.overdue_count },
          { chave: "due24h", rotulo: "Vence em 24h", valor: a.due_within_24h_count },
          { chave: "on_time", rotulo: "No prazo", valor: a.on_time_count },
          {
            chave: "sem_prazo",
            rotulo: ROTULO_SEM_PRAZO,
            valor: a.deadline_unavailable_count,
          },
        ]
      : [],
    sobreposicoes: temPrazo
      ? []
      : [
          { chave: "over48h", rotulo: "Acima de 48h", valor: a.over_48h_count },
          { chave: "stalled", rotulo: "Travados", valor: a.stalled_count },
        ],
  }));
  return {
    empilhavel: temPrazo,
    linhas,
    maximo: linhas.reduce((m, l) => Math.max(m, l.backlog), 0),
    nota: temPrazo
      ? "Categorias mutuamente exclusivas: somadas, fecham o backlog da conta."
      : "Backlog, acima de 48h e travados se sobrepõem e NÃO são somados. " +
        EXPLICACAO_LIMIAR_48H,
  };
}

/** Os segmentos particionam mesmo o backlog? Falso aqui e' defeito de dado. */
export function segmentosFecham(linha: LinhaRisco): boolean {
  if (!linha.segmentos.length) return true;
  return linha.segmentos.reduce((s, f) => s + f.valor, 0) === linha.backlog;
}

// ---------------------------------------------------------------------------
// Tendencia: janelas e serie para grafico
// ---------------------------------------------------------------------------
export const JANELAS_TENDENCIA = [
  { horas: 24, rotulo: "24h" },
  { horas: 72, rotulo: "72h" },
  { horas: 168, rotulo: "7d" },
] as const;

/**
 * Janela com que a tela ABRE. Tem de ser uma das tres acima, senao o seletor
 * nasce com nenhum botao aceso e o operador nao sabe o que esta' vendo —
 * defeito visto no QA do EXP-UX-1, quando o padrao era 48h.
 *
 * `JANELA_PADRAO_HORAS` continua sendo o default da API e nao muda.
 */
export const JANELA_INICIAL_HORAS = 72;

/**
 * Rotulos de `deadline_status`, que e' OUTRO espaco de valores que `Situacao`:
 * a API devolve `unavailable`, nao `deadline_unavailable`. Reaproveitar o mapa
 * de situacao fazia a tela imprimir o enum cru "unavailable" na coluna de
 * situacao do Mercado Livre — onde e' justamente o estado de TODO o backlog.
 */
export const ROTULO_DEADLINE_STATUS: Record<string, string> = {
  overdue: "Vencido",
  due_within_24h: "Vence em 24h",
  on_time: "No prazo",
  unavailable: ROTULO_SEM_PRAZO,
};

/** Rotulos de `operational_age_status`. Enum cru nao e' texto de tela. */
/**
 * Dominio REAL de `operational_age_status`, copiado da fonte da verdade:
 * `OperationalAgeStatus` em `pipelines/expedicao/contract.py` e o
 * `Literal["within_48h", "over_48h", "unknown"]` do schema da API.
 *
 * O mapa anterior foi escrito de memoria e inventou `under_24h` e
 * `between_24h_48h`, que nao existem em lugar nenhum — e deixou de fora
 * `within_48h`, que e' metade do dominio e aparece nos DOIS canais. O efeito
 * em producao foi a coluna "Idade operacional" exibindo o enum cru.
 *
 * Nao existe faixa "entre 24h e 48h" no contrato: a idade operacional tem
 * DUAS faixas medidas (dentro e acima de 48h) mais o desconhecido. Inventar
 * uma terceira faixa era prometer um recorte que o pipeline nao calcula.
 */
export const ROTULO_IDADE_OPERACIONAL: Record<string, string> = {
  within_48h: "Dentro de 48h",
  over_48h: "Acima de 48h",
  unknown: "Idade desconhecida",
};

/** O dominio fechado, para o teste travar contra a fonte da verdade. */
export const IDADES_OPERACIONAIS = ["within_48h", "over_48h", "unknown"] as const;

/** Traduz, e devolve o valor cru quando a fonte trouxer algo novo. */
export function rotuloDeadline(valor: string): string {
  return ROTULO_DEADLINE_STATUS[valor] ?? valor;
}

export function rotuloIdade(valor: string): string {
  return ROTULO_IDADE_OPERACIONAL[valor] ?? valor;
}

export interface PontoGrafico {
  hora: string;
  [conta: string]: string | number | null;
}

/**
 * Series por conta -> linhas do grafico, uma coluna por conta.
 *
 * Hora sem ponto para uma conta fica `null`, NUNCA zero: o grafico interrompe
 * a linha (`connectNulls={false}`) em vez de desenhar uma queda que nao houve.
 * Foi esse o erro que a tabela de tendencia evitava mostrando linha ausente.
 */
export function montarPontosDoGrafico(series: SerieConta[]): PontoGrafico[] {
  const horas = new Set<string>();
  for (const s of series) for (const p of s.pontos) horas.add(p.hora);
  return [...horas]
    .sort((a, b) => a.localeCompare(b))
    .map((hora) => {
      const ponto: PontoGrafico = { hora };
      for (const s of series) {
        const achado = s.pontos.find((p) => p.hora === hora);
        ponto[s.shopAccount] = achado ? achado.backlog : null;
      }
      return ponto;
    });
}

// ---------------------------------------------------------------------------
// Timeline dos pedidos mais criticos
// ---------------------------------------------------------------------------
export const TIMELINE_MAX_LINHAS = 12;

export interface BarraTimeline {
  chave: string;
  conta: string;
  marca: string;
  horasAbertas: number;
  /** Shopee: instante do prazo contratual. ML: sempre `null`. */
  prazo: string | null;
  /** Só `true` quando o canal publica prazo E o pedido o ultrapassou. */
  vencido: boolean;
  acimaDoLimiar: boolean;
}

/**
 * Os pedidos mais criticos da PAGINA ATUAL — nunca a fila inteira.
 *
 * Desenhar 714 ou 821 barras nao e' leitura operacional, e' ruido: o operador
 * age sobre os primeiros. O recorte e' explicito na tela para que ninguem leia
 * a timeline como se fosse o backlog inteiro.
 *
 * Para o Mercado Livre `prazo` sai SEMPRE nulo. A barra mede idade operacional
 * ate' agora; desenhar um fim fabricado seria inventar SLA que a fonte nao tem.
 */
export function montarTimeline(
  fila: LinhaFila[],
  canal: Canal = CANAL_PADRAO,
  limite: number = TIMELINE_MAX_LINHAS,
): BarraTimeline[] {
  const temPrazo = canalTemPrazo(canal);
  return [...fila]
    .sort((a, b) => (b.hours_open ?? 0) - (a.hours_open ?? 0))
    .slice(0, Math.max(0, limite))
    .map((l, i) => ({
      chave: `${l.shop_account}-${l.order_ref ?? i}`,
      conta: l.shop_account,
      marca: l.brand,
      horasAbertas: l.hours_open ?? 0,
      prazo: temPrazo ? l.dispatch_deadline : null,
      vencido: temPrazo && l.deadline_status === "overdue",
      acimaDoLimiar: l.operational_age_status === "over_48h",
    }));
}
