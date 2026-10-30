/**
 * T-0010 — catalogo UNICO das definicoes exibidas no botao `?` da Expedicao.
 *
 * Cada metrica que aparece na tela (KPI, taxa, secao) tem uma entrada aqui,
 * com os MESMOS seis campos: definicao, formula, fonte, janela, cobertura e
 * limitacoes. Texto curto: quem abre o `?` quer a resposta em uma leitura.
 *
 * Regras que o texto respeita (e que os testes travam):
 *   - a LDR da Shopee e' indicador operacional INTERNO, nao o indice oficial
 *     de Account Health da Shopee;
 *   - o Mercado Livre nao publica prazo de despacho: nao ha SLA nem LDR, e o
 *     `?` diz isso em vez de inventar numero;
 *   - "acima de 48 h" e' limiar interno da Torre, nunca SLA do marketplace.
 *
 * Revisao T-0010: cada entrada declara tambem a ORIGEM do numero, em campo
 * proprio (`origem`), que o `?` mostra como rotulo curto antes da definicao:
 *   - "oficial": numero publicado pela plataforma, copiado dela — `fonte`
 *     diz de onde;
 *   - "torre": calculo operacional da Torre sobre dados brutos — a regra e' a
 *     `formula`;
 *   - "indisponivel": o canal nao publica o insumo; a tela nao mede.
 * Hoje NENHUMA metrica da Expedicao e' oficial: ate' as que comparam com um
 * numero da plataforma (a meta de 4% do TikTok) sao medicao da Torre, e o
 * numero da plataforma vai em `referencia`, nunca como fonte.
 *
 * SEM IMPORTS de outros contratos, para nao abrir ciclo.
 */

/** De onde vem o numero exibido. */
export type OrigemMetrica = "oficial" | "torre" | "indisponivel";

/** O rotulo curto que o `?` mostra antes da definicao. */
export const ROTULO_ORIGEM: Record<OrigemMetrica, string> = {
  oficial: "Oficial do marketplace",
  torre: "Cálculo da Torre",
  indisponivel: "Indisponível neste canal",
};

export interface DefinicaoMetrica {
  /** Nome curto, igual ao rotulo da tela. Vai no `aria-label` do `?`. */
  nome: string;
  /** Oficial do marketplace, calculo da Torre ou indisponivel no canal. */
  origem: OrigemMetrica;
  definicao: string;
  formula: string;
  fonte: string;
  janela: string;
  cobertura: string;
  limitacoes: string;
  /**
   * Numero externo citado como REFERENCIA (ex.: a meta publicada pela
   * plataforma). Nao e' a fonte do numero exibido.
   */
  referencia?: string;
}

/** Os seis campos, na ordem em que aparecem no `?`. */
export const CAMPOS_DEFINICAO = [
  ["definicao", "Definição"],
  ["formula", "Fórmula"],
  ["fonte", "Fonte"],
  ["janela", "Janela"],
  ["cobertura", "Cobertura"],
  ["limitacoes", "Limitações"],
] as const satisfies readonly (readonly [keyof DefinicaoMetrica, string])[];

const FILA_SHOPEE = "Fila publicada (marts.expedicao_fila_atual), lida do Data Mart da Shopee.";
const FILA_ML = "Fila publicada (marts.expedicao_fila_atual), lida do Data Mart do Mercado Livre.";
const FOTO = "Fotografia atual: pedidos em aberto no instante da publicação.";
const COB_SHOPEE = "Contas Shopee cadastradas; pedidos FBS incluídos quando a divisão não está publicada.";
const COB_ML = "Contas do Mercado Livre cadastradas; envios Full (fulfillment) ficam fora.";
const SEM_PRAZO_ML =
  "O Mercado Livre não publica prazo de despacho: não há SLA nem LDR a medir. A tela não inventa esse número.";
/** O 4% e' da plataforma; a medicao comparada com ele e' da Torre. */
const REF_META_TIKTOK =
  "Meta de 4% publicada pelo TikTok Shop: só a linha de comparação, não a fonte do número (a plataforma mede com o próprio relógio e denominador).";

/** Os KPIs da fila (`montarKpis`), um `?` por canal. */
export const KPIS_DA_FILA = [
  "backlog", "overdue", "due24h", "due_next_day", "sem_prazo", "over48h", "stalled", "slow", "zombie",
  "marcas_alerta",
] as const;
type KpiDaFila = (typeof KPIS_DA_FILA)[number];

/** Monta as entradas comuns da fila para um canal. */
function fila<C extends "shopee" | "mercadolivre">(canal: C): Record<`${C}.${KpiDaFila}`, DefinicaoMetrica> {
  const fonte = canal === "shopee" ? FILA_SHOPEE : FILA_ML;
  const cobertura = canal === "shopee" ? COB_SHOPEE : COB_ML;
  const grao = canal === "shopee" ? "pedido" : "envio (shipment)";
  // R21: o relogio da idade (`hours_open`) e' por canal, como em
  // `pipelines/expedicao/transform.py`: Shopee parte de `pay_time` (criacao se
  // faltar); o ML parte de `date_ready_to_ship` (criacao se faltar) porque a
  // fonte nao expoe pagamento (`paid_at` sai nulo de proposito).
  const marco =
    canal === "shopee"
      ? { curto: "desde o pagamento", regra: "pagamento (pay_time; a criação do pedido, se faltar)" }
      : {
          curto: "desde que o envio ficou pronto para despacho",
          regra: "envio pronto para despacho (date_ready_to_ship; a criação do pedido, se faltar)",
        };
  const prazo = (nome: string, definicao: string, formula: string): DefinicaoMetrica =>
    canal === "shopee"
      ? {
          nome,
          // O prazo (ship_by_date) e' da Shopee; a contagem e' da Torre.
          origem: "torre",
          definicao,
          formula,
          fonte,
          janela: FOTO,
          cobertura,
          limitacoes: "Prazo da Shopee (ship_by_date); pedido sem prazo publicado fica em “Sem prazo”.",
        }
      : {
          nome,
          origem: "indisponivel",
          definicao: "Não medido neste canal.",
          formula: "Não se aplica.",
          fonte,
          janela: FOTO,
          cobertura,
          limitacoes: SEM_PRAZO_ML,
        };
  return {
    [`${canal}.backlog`]: {
      nome: canal === "shopee" ? "Backlog total" : "Backlog total (envios)",
      origem: "torre",
      definicao: `Cada ${grao} pago que ainda não foi expedido.`,
      formula: `Contagem de ${grao}s em aberto na fotografia.`,
      fonte,
      janela: FOTO,
      cobertura,
      limitacoes:
        canal === "shopee"
          ? "Não separa a etapa interna (a preparar × envio organizado)."
          : "Não publica o substatus do envio.",
    },
    [`${canal}.overdue`]: prazo(
      "Vencidos",
      "Pedidos pagos, ainda não coletados, que já passaram do prazo de envio da Shopee.",
      "Contagem de pedidos com ship_by_date anterior ao instante da fotografia.",
    ),
    // EXPEDICAO-VENCE-AMANHA: o prazo da Shopee vence sempre as 23:59, entao
    // a janela de 24 h so' alcanca o dia de hoje — o "ⓘ" diz isso.
    [`${canal}.due24h`]: prazo(
      "Vence em 24 h",
      "Pedidos em aberto cujo prazo vence nas próximas 24 h. Todo prazo da Shopee vence às 23:59: na prática, são os que vencem hoje.",
      "Contagem de pedidos com ship_by_date entre agora e agora + 24 h.",
    ),
    [`${canal}.due_next_day`]: {
      ...prazo(
        "Vence no próximo dia de prazo",
        "Pedidos em aberto que vencem no próximo dia de prazo: amanhã, de segunda a quinta; na sexta ou antes de feriado, o próximo dia útil (a Shopee não vence prazo em fim de semana).",
        "Contagem de pedidos com ship_by_date (dia de Brasília) no primeiro dia de prazo depois do dia da fotografia.",
      ),
      ...(canal === "shopee"
        ? { limitacoes: "Já contados em “No prazo”. A lista vem da fila publicada, com pedidos FBS." }
        : {}),
    },
    [`${canal}.sem_prazo`]: {
      nome: "Sem prazo publicado",
      origem: "torre",
      definicao:
        canal === "shopee"
          ? "Pedidos sem prazo de envio (ship_by_date) publicado pela Shopee."
          : "Envios sem prazo de despacho na fonte.",
      formula: "Contagem de envios sem prazo publicado.",
      fonte,
      janela: FOTO,
      cobertura,
      limitacoes: canal === "shopee" ? "Pedido sem ship_by_date na fonte." : SEM_PRAZO_ML,
    },
    [`${canal}.over48h`]: {
      nome: "Acima de 48 h",
      origem: "torre",
      definicao: `${grao === "pedido" ? "Pedidos" : "Envios"} abertos há mais de 48 h ${marco.curto}, vencidos ou não: mede a idade, não o prazo.`,
      formula: `Contagem com horas em aberto > 48, contadas a partir do ${marco.regra}.`,
      fonte,
      janela: FOTO,
      cobertura,
      limitacoes: "Limiar interno da Torre, não é SLA do marketplace.",
    },
    [`${canal}.stalled`]: {
      nome: "Travados",
      origem: "torre",
      definicao: "Parados: abertos há mais tempo que o normal da própria conta (lentos) ou há mais de 30 dias (zumbis). Cada pedido conta uma vez.",
      formula: "União de lentos e zumbis (nunca a soma).",
      fonte,
      janela: FOTO,
      cobertura,
      limitacoes: "Classificação do pipeline; a tela não recalcula.",
    },
    [`${canal}.slow`]: {
      nome: "Lentos vs baseline",
      origem: "torre",
      definicao: "Abertos há mais tempo que o histórico da própria conta.",
      formula: "Horas em aberto acima da linha de base da conta.",
      fonte,
      janela: FOTO,
      cobertura,
      limitacoes: "A linha de base é do pipeline; conta nova tem histórico curto.",
    },
    [`${canal}.zombie`]: {
      nome: "Zumbis",
      origem: "torre",
      // R21: o pipeline so' compara a idade (`hours_open` > 30 dias); nao ha
      // historico de mudanca nem watermark. Nao afirmar que a fonte parou.
      definicao: `${grao === "pedido" ? "Pedidos" : "Envios"} abertos há mais de 30 dias na fonte, ${marco.curto}.`,
      formula: `Contagem com horas em aberto > 30 dias (720 h), contadas a partir do ${marco.regra}.`,
      fonte,
      janela: FOTO,
      cobertura,
      limitacoes: `Mede só a idade: não verifica se o ${grao} mudou na fonte. Pode ser item esquecido na operação ou status não atualizado na fonte; a tela não distingue.`,
    },
    [`${canal}.marcas_alerta`]: {
      nome: "Marcas com fonte em alerta",
      origem: "torre",
      definicao: "Marcas cuja fonte está desatualizada ou com frescor desconhecido.",
      formula: "Contagem de marcas com frescor crítico ou desconhecido.",
      fonte: "Frescor por marca publicado com a fila.",
      janela: FOTO,
      cobertura,
      limitacoes: "Mede a fonte, não a operação: fonte velha não é atraso.",
    },
  } as Record<`${C}.${KpiDaFila}`, DefinicaoMetrica>;
}

export const DEFINICOES = {
  ...fila("shopee"),
  ...fila("mercadolivre"),

  "shopee.prazo_situacao": {
    nome: "Pedidos em fila por situação de prazo",
    origem: "torre",
    definicao: "Divide a fila aberta em vencidos, vence em 24 h, no prazo e sem prazo.",
    formula: "Partição da fila: as quatro faixas somam o backlog.",
    fonte: FILA_SHOPEE,
    janela: FOTO,
    cobertura: COB_SHOPEE,
    limitacoes: "Não é LDR: a fotografia só tem pedidos em aberto, não os já enviados.",
  },
  "shopee.no_prazo": {
    nome: "No prazo",
    origem: "torre",
    definicao: "Pedidos em aberto cujo prazo de envio vence depois das próximas 24 h, de amanhã em diante.",
    formula: "Contagem de pedidos com ship_by_date depois de agora + 24 h.",
    fonte: FILA_SHOPEE,
    janela: FOTO,
    cobertura: COB_SHOPEE,
    limitacoes: "Inclui os que vencem no próximo dia de prazo.",
  },
  "shopee.fbs": {
    nome: "Pedidos FBS",
    origem: "torre",
    definicao: "Pedidos que a própria Shopee despacha do armazém dela.",
    formula: "Fila aberta com fluxo FBS, separada da fila do vendedor.",
    fonte: "Rota da LDR operacional (fila por fluxo).",
    janela: FOTO,
    cobertura: COB_SHOPEE,
    limitacoes: "Não é risco do vendedor. Só aparece com a LDR operacional ligada.",
  },
  "shopee.ldr": {
    nome: "LDR operacional da Shopee",
    origem: "torre",
    definicao: "Indicador operacional interno: parte dos pedidos com prazo vencido que foram coletados com atraso ou seguem sem coleta.",
    formula: "Atrasados ÷ pedidos com prazo vencido na janela.",
    fonte: "marts.expedicao_shopee_ldr_daily (por dia de vencimento × conta).",
    janela: "Últimos 7 dias de vencimento; só vencimentos fechados entram.",
    cobertura: "Pagos do vendedor, sem FBS e sem cancelados sem coleta.",
    limitacoes: "Indicador interno da Torre, não é o índice oficial de Account Health da Shopee. Com mais de 10% sem prazo ou sem coleta, a taxa não é exibida.",
  },
  "shopee.ldr_base": {
    nome: "Base (prazos vencidos)",
    origem: "torre",
    definicao: "Pedidos cujo prazo de envio venceu na janela.",
    formula: "Pagos − FBS − cancelados sem coleta − enviados sem coleta.",
    fonte: "marts.expedicao_shopee_ldr_daily.",
    janela: "Últimos 7 dias de vencimento.",
    cobertura: "Pagos do vendedor.",
    limitacoes: "Sem vencimento fechado, não há base: a tela mostra “—”.",
  },
  "shopee.ldr_atrasados": {
    nome: "Atrasados",
    origem: "torre",
    definicao: "Coletados depois do prazo mais os vencidos ainda sem coleta.",
    formula: "Coletados com atraso + vencidos sem coleta.",
    fonte: "marts.expedicao_shopee_ldr_daily.",
    janela: "Últimos 7 dias de vencimento.",
    cobertura: "Pagos do vendedor, sem FBS.",
    limitacoes: "Indicador interno, não o índice oficial de Account Health.",
  },
  "shopee.ldr_em_risco": {
    nome: "Vencem hoje ou amanhã",
    origem: "torre",
    definicao: "Pedidos ainda sem coleta e dentro do prazo, que vencem hoje ou amanhã.",
    formula: "Contagem de pendentes com vencimento em hoje ou amanhã.",
    fonte: "marts.expedicao_shopee_ldr_daily.",
    janela: "Hoje e amanhã.",
    cobertura: "Pagos do vendedor, sem FBS.",
    limitacoes: "Ainda dá para tratar: não entra na taxa.",
  },
  "mercadolivre.prazo": {
    nome: "Prazo e LDR no Mercado Livre",
    origem: "indisponivel",
    definicao: "Não medido: o canal não publica prazo de despacho.",
    formula: "Não se aplica.",
    fonte: FILA_ML,
    janela: FOTO,
    cobertura: COB_ML,
    limitacoes: SEM_PRAZO_ML + " O risco é lido pela idade da fila (acima de 48 h, limiar interno).",
  },
  "mercadolivre.mais_antigo": {
    nome: "Pedido mais antigo em fila",
    origem: "torre",
    definicao: "Idade do envio mais antigo ainda em aberto, por marca.",
    formula: "Maior idade de linha publicada no frescor por marca.",
    fonte: "Frescor por marca publicado com a fila.",
    janela: FOTO,
    cobertura: COB_ML,
    limitacoes: "Contexto, não alerta: um backlog legítimo sempre tem pedido velho.",
  },

  "tiktok.pendente_vencido": {
    nome: "Venceram e não saíram",
    origem: "torre",
    definicao: "Pedidos cujo prazo venceu e que ainda não tiveram o evento escolhido.",
    formula: "Contagem de pendentes com prazo vencido na janela.",
    fonte: "marts.expedicao_tiktok_dispatch_daily (dia × marca).",
    janela: "Vencimentos da janela consultada + hoje.",
    cobertura: "Pedidos pagos das lojas TikTok Shop cadastradas.",
    limitacoes: "Prazo reconstruído da política de dias úteis; não é o SLA oficial por pedido.",
  },
  "tiktok.vence_hoje_amanha": {
    nome: "Vencem hoje ou amanhã",
    origem: "torre",
    definicao: "Pedidos ainda sem o evento que vencem hoje ou amanhã.",
    formula: "Contagem de pendentes com vencimento em hoje ou amanhã.",
    fonte: "marts.expedicao_tiktok_dispatch_daily.",
    janela: "Hoje e amanhã.",
    cobertura: "Pedidos pagos das lojas TikTok Shop cadastradas.",
    limitacoes: "Prazo reconstruído da política de dias úteis.",
  },
  "tiktok.pendente_no_prazo": {
    nome: "Pendentes no prazo",
    origem: "torre",
    definicao: "Pedidos ainda sem o evento, dentro do prazo.",
    formula: "Contagem de pendentes com vencimento futuro.",
    fonte: "marts.expedicao_tiktok_dispatch_daily.",
    janela: "Vencimentos da janela consultada + hoje.",
    cobertura: "Pedidos pagos das lojas TikTok Shop cadastradas.",
    limitacoes: "Não entram na LDR enquanto o prazo não vence.",
  },
  "tiktok.lojas_acima_meta": {
    nome: "Lojas acima da meta",
    origem: "torre",
    definicao: "Lojas cuja LDR da janela passou da referência de 4% do TikTok.",
    formula: "Contagem de lojas com LDR > 4%, entre as com base suficiente.",
    fonte: "marts.expedicao_tiktok_dispatch_daily.",
    janela: "Vencimentos fechados da janela.",
    cobertura: "Só lojas com base suficiente de vencimentos fechados.",
    limitacoes: "Medição interna: não é o veredito de penalização da plataforma.",
    referencia: REF_META_TIKTOK,
  },
  "tiktok.ldr": {
    nome: "LDR do TikTok",
    origem: "torre",
    definicao: "Parte dos pedidos que venciam no período e saíram com atraso ou seguem sem o evento.",
    formula: "Atrasados ÷ pedidos que venciam (só vencimentos fechados).",
    fonte: "marts.expedicao_tiktok_dispatch_daily.",
    janela: "Vencimentos da janela consultada; o dia de hoje fica fora da taxa.",
    cobertura: "Pagos, sem amostras e sem cancelados antes do prazo.",
    limitacoes: "Prazo reconstruído (1 dia útil para etiqueta, 2 para coleta). Indicador interno, não a medição oficial da plataforma.",
    referencia: REF_META_TIKTOK,
  },
  "tiktok.venciam": {
    nome: "Venciam no período",
    origem: "torre",
    definicao: "Pedidos cujo prazo venceu num dia fechado da janela.",
    formula: "Soma dos vencimentos fechados (denominador da LDR).",
    fonte: "marts.expedicao_tiktok_dispatch_daily.",
    janela: "Vencimentos fechados da janela.",
    cobertura: "Pagos, sem amostras e sem cancelados antes do prazo.",
    limitacoes: "Sem vencimento fechado, não há base: a tela mostra “—”.",
  },
  "tiktok.atrasaram": {
    nome: "Já atrasaram",
    origem: "torre",
    definicao: "Pedidos dos vencimentos fechados que saíram depois do prazo ou ainda não saíram.",
    formula: "Enviados com atraso + vencidos sem o evento (numerador da LDR).",
    fonte: "marts.expedicao_tiktok_dispatch_daily.",
    janela: "Vencimentos fechados da janela.",
    cobertura: "Pagos, sem amostras e sem cancelados antes do prazo.",
    limitacoes: "Prazo reconstruído da política de dias úteis.",
  },
  "tiktok.fluxo_pagamento": {
    nome: "Taxa por data de pagamento",
    origem: "torre",
    definicao: "Visão auxiliar: atraso dos pedidos pagos em cada dia.",
    formula: "Atrasados ÷ pedidos pagos no dia (só dias fechados).",
    fonte: "marts.expedicao_tiktok_dispatch_daily.",
    janela: "Dias de pagamento da janela.",
    cobertura: "Pagos, sem amostras e sem cancelados antes do prazo.",
    limitacoes: "Não é a LDR: o denominador é outro. Um feriado empurra vários dias de pagamento para o mesmo vencimento.",
  },
  "jornada.fila_aberta": {
    nome: "Jornada — fila aberta",
    origem: "torre",
    definicao: "Pedidos pagos ainda não expedidos, divididos pela etapa em que estão agora.",
    formula: "Contagem por etapa ÷ total da fila aberta (as etapas somam o total).",
    fonte: "Rota /expedicao/jornada sobre a fila publicada (marts.expedicao_fila_atual).",
    janela: FOTO,
    cobertura: "Contas cadastradas do canal, com o filtro de marca aplicado no servidor.",
    limitacoes: "A etapa interna (a preparar × envio organizado; substatus no ML) ainda não é publicada. Sobreposições como vencidos são subconjuntos e não se somam.",
  },
  "jornada.coorte": {
    nome: "Jornada — coorte",
    origem: "torre",
    definicao: "Pedidos de uma janela de dias fechados, pela etapa em que terminaram.",
    formula: "Contagem por etapa ÷ base da coorte (as etapas somam a base).",
    fonte: "TikTok: marts.expedicao_tiktok_dispatch_daily. Shopee: tabelas da LDR operacional.",
    janela: "Dias fechados em horário de Brasília, até ontem (as rotas irmãs incluem hoje).",
    cobertura: "TikTok por dia de pagamento; Shopee por dia de vencimento. O Mercado Livre não tem coorte.",
    limitacoes: "Dias recentes ainda mudam. Não bate dia a dia com a LDR, que inclui hoje.",
  },
} satisfies Record<string, DefinicaoMetrica>;

/** As chaves do `fila()` sao montadas por canal; o teste confere o catalogo. */
export type ChaveMetrica = keyof typeof DEFINICOES;

export function ehChaveMetrica(v: string): v is ChaveMetrica {
  return Object.prototype.hasOwnProperty.call(DEFINICOES, v);
}

export function definicaoDe(chave: ChaveMetrica): DefinicaoMetrica {
  return DEFINICOES[chave];
}

/**
 * Chave do `?` de um KPI da fila (`montarKpis`). O canal entra na chave
 * porque a mesma medida tem limitacao diferente em cada um — no Mercado
 * Livre, "Vencidos" e' justamente o que nao existe.
 */
export function chaveDoKpiDaFila(canal: "shopee" | "mercadolivre", kpi: string): ChaveMetrica | null {
  const chave = `${canal}.${kpi}`;
  return ehChaveMetrica(chave) ? chave : null;
}

/** O `?` da secao de prazo do canal SEM prazo publicado (Mercado Livre). */
export const AJUDA_CANAL_SEM_PRAZO = {
  prazo: "mercadolivre.prazo",
  maisAntigo: "mercadolivre.mais_antigo",
} as const satisfies Record<string, ChaveMetrica>;

/** As metricas exibidas em cada canal — o teste cobre o catalogo com isto. */
export const METRICAS_POR_CANAL: Record<"shopee" | "mercadolivre" | "tiktokshop", readonly ChaveMetrica[]> = {
  shopee: [
    "shopee.backlog", "shopee.overdue", "shopee.due24h", "shopee.due_next_day", "shopee.no_prazo",
    "shopee.sem_prazo", "shopee.over48h", "shopee.stalled",
    "shopee.slow", "shopee.zombie", "shopee.marcas_alerta", "shopee.prazo_situacao",
    "shopee.fbs", "shopee.ldr", "shopee.ldr_base", "shopee.ldr_atrasados", "shopee.ldr_em_risco",
    "jornada.fila_aberta", "jornada.coorte",
  ],
  mercadolivre: [
    "mercadolivre.backlog", "mercadolivre.overdue", "mercadolivre.due24h", "mercadolivre.sem_prazo",
    "mercadolivre.over48h", "mercadolivre.stalled", "mercadolivre.slow", "mercadolivre.zombie",
    "mercadolivre.marcas_alerta", "mercadolivre.prazo", "mercadolivre.mais_antigo",
    "jornada.fila_aberta",
  ],
  tiktokshop: [
    "tiktok.pendente_vencido", "tiktok.vence_hoje_amanha", "tiktok.pendente_no_prazo",
    "tiktok.lojas_acima_meta", "tiktok.ldr", "tiktok.venciam", "tiktok.atrasaram",
    "tiktok.fluxo_pagamento", "jornada.coorte",
  ],
};
