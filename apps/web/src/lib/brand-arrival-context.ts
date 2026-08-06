// Contexto de chegada da página de Marca — "chegando quente" (Gate G3, Task 2;
// desenho em docs/DRILLDOWN_ARCHITECTURE.md §8). Módulo puro (sem React),
// mesmo padrão de channel-signal-reasons.ts / regioes-scope.ts.
//
// Problema resolvido: o CTA "Abrir visão completa da marca" (detalhe marca ×
// canal, Gate G2) levava marca/canal/período mas NUNCA o motivo da navegação —
// o contexto morria no <Link>. Aqui ele passa a viajar como um punhado de
// IDENTIFICADORES allowlisted na querystring.
//
// Regras duras do contrato:
// - A URL transporta SOMENTE enums/slugs conhecidos. Nunca dinheiro,
//   percentual, mediana, p75, texto livre, mensagem pronta ou JSON. A URL
//   nunca é fonte de verdade de métrica: o banner não exibe número algum, e
//   todos os números da página continuam vindo dos fetches dela.
// - Parâmetro ausente, repetido, fora do enum ou incompatível com a página
//   atual (marca da rota / canal filtrado) ⇒ contexto IGNORADO, sem erro.
// - `ctx_*` NÃO entra em FILTER_QUERY_KEYS (nav-links.ts): é exatamente isso
//   que faz a navegação pela sidebar descartar o contexto e impede que ele
//   contamine outras telas.
// - Nesta primeira implementação, `ctx_from` aceita SÓ `canais` — o único
//   produtor real hoje. Propagação transitiva desde a Gerencial é dívida
//   futura registrada no §8; não se cria enum sem wiring.

/** Origem da jornada. Único valor suportado nesta fase (ver §8). */
export const CTX_FROM_CANAIS = "canais";
export type BrandArrivalFrom = typeof CTX_FROM_CANAIS;

/** Sinais reais da matriz marca × canal (contrato de `canais-channel-metrics`
 * / `performance_service`). Nenhum sinal novo é inventado aqui. */
export const ARRIVAL_SIGNALS = ["custo_alto", "frete_alto", "ads_subutilizado", "sem_dado", "roas_forte"] as const;
export type ArrivalSignal = (typeof ARRIVAL_SIGNALS)[number];

const CHANNELS = ["tiktok", "ml", "shopee"] as const;
export type ArrivalChannel = (typeof CHANNELS)[number];

/** Marcas canônicas do projeto (mesmo conjunto de BRAND_META/BRANDS_IN_SCOPE). */
const BRANDS = ["barbours", "kokeshi", "apice", "lescent", "rituaria"] as const;

const CHANNEL_LABEL: Record<ArrivalChannel, string> = {
  tiktok: "TikTok Shop",
  ml: "Mercado Livre",
  shopee: "Shopee",
};

/**
 * Prioridade determinística quando o detalhe tem mais de um sinal.
 *
 * Espelha a classificação JÁ existente no Gate G2
 * (`channel-signal-reasons.ts`: sinais de atenção antes do destaque
 * positivo, na ordem em que a headline os lista). Nenhum threshold novo,
 * nenhuma reclassificação de severidade — só a escolha de QUAL sinal viaja,
 * porque a URL nunca transporta array.
 */
const SIGNAL_PRIORITY: ArrivalSignal[] = ["custo_alto", "frete_alto", "ads_subutilizado", "sem_dado", "roas_forte"];

/**
 * Sinais que cada canal pode realmente emitir, espelhando a aplicabilidade do
 * contrato vigente (`performance_service`: `_ADS_APPLICABLE` e
 * `_SHIPPING_APPLICABLE` são `false` para TikTok, `_COST_APPLICABLE` é `true`
 * para os três). Sem Ads e sem frete de seller, o TikTok nunca produz
 * `ads_subutilizado`, `frete_alto` ou `roas_forte`.
 *
 * A validação existe porque **a URL é entrada não confiável**: não basta o
 * produtor legítimo não gerar a combinação — um link montado à mão ou
 * desatualizado não pode fazer a Marca anunciar um sinal impossível para o
 * canal. Nenhum threshold, sinal ou regra de negócio é criado aqui.
 */
const SIGNALS_BY_CHANNEL: Record<ArrivalChannel, readonly ArrivalSignal[]> = {
  tiktok: ["custo_alto", "sem_dado"],
  ml: ["custo_alto", "frete_alto", "ads_subutilizado", "sem_dado", "roas_forte"],
  shopee: ["custo_alto", "frete_alto", "ads_subutilizado", "sem_dado", "roas_forte"],
};

/** `true` quando o canal pode emitir esse sinal no contrato vigente. */
export function isSignalCompatibleWithChannel(signal: ArrivalSignal, channel: ArrivalChannel): boolean {
  return SIGNALS_BY_CHANNEL[channel].includes(signal);
}

/** Âncora da única seção da Marca que sustenta algum sinal de Canais: o bloco
 * "Período selecionado" (KPIs de GMV/Pedidos/Ticket/Ad Spend + gráfico diário).
 * Id adicionado no Gate G3; nenhuma seção foi criada para receber âncora. */
export const SECTION_PERIOD = "marca-periodo";

export interface ArrivalSignalMeta {
  /** Descrição humana allowlisted (texto fixo do código, nunca da URL). */
  description: string;
  /** Âncora da seção que sustenta o sinal — `null` quando a Marca não tem essa evidência. */
  section: string | null;
  /** Rótulo do CTA de seção; `null` quando não há seção. */
  sectionLabel: string | null;
  /** Limitação a declarar quando a Marca não evidencia o sinal. */
  unavailableNote: string | null;
}

/**
 * Mapa sinal → o que a página de Marca REALMENTE consegue evidenciar
 * (auditado no §8.2). Custo, frete e ROAS não têm seção nesta tela: em vez de
 * prometer, declaramos a limitação e devolvemos o usuário à evidência.
 */
const SIGNAL_META: Record<ArrivalSignal, ArrivalSignalMeta> = {
  custo_alto: {
    description: "custo de marketplace no topo do canal",
    section: null,
    sectionLabel: null,
    unavailableNote:
      "O detalhamento de custo de marketplace não existe nesta página — ele vive na matriz por canal e em Financeiro. " +
      "Aqui você vê GMV, pedidos e ticket da marca no período.",
  },
  frete_alto: {
    description: "frete pago pelo seller no topo do canal",
    section: null,
    sectionLabel: null,
    unavailableNote:
      "O detalhamento de frete do seller não existe nesta página — ele vive na matriz por canal. " +
      "Aqui você vê GMV, pedidos e ticket da marca no período.",
  },
  // Único sinal de Canais com evidência real nesta página: o KPI "Ad Spend" do
  // período existe aqui (ML/Shopee; "N/D para TikTok Shop"). A COMPARAÇÃO
  // contra a mediana do canal continua sendo de Canais — dito na nota.
  // Redação NEUTRA: a regra do canal também dispara quando o percentual de Ads
  // está ausente (a ausência conta como subutilização) e quando o gasto é
  // zero — logo "abaixo da mediana" não seria verdade em todos os ramos.
  ads_subutilizado: {
    description: "sinal de Ads subutilizado no canal",
    section: SECTION_PERIOD,
    sectionLabel: "Ver investimento do período",
    unavailableNote:
      "Esta página mostra apenas o investimento em Ads do período — a comparação com o canal e o " +
      "diagnóstico completo do sinal permanecem na matriz por canal.",
  },
  sem_dado: {
    description: "métricas do canal sem dado no período",
    section: null,
    sectionLabel: null,
    unavailableNote:
      "O sinal aponta ausência de dado na fonte do canal, não um resultado ruim. A cobertura por métrica fica na matriz por canal.",
  },
  roas_forte: {
    description: "ROAS na mediana do canal ou acima",
    section: null,
    sectionLabel: null,
    unavailableNote:
      "Esta página não compara ROAS contra a mediana do canal — essa referência fica na matriz por canal.",
  },
};

export interface BrandArrivalContext {
  from: BrandArrivalFrom;
  signal: ArrivalSignal;
  channel: ArrivalChannel;
  channelLabel: string;
  brand: string;
  /** Descrição humana do sinal (do código, allowlisted). */
  description: string;
  /** Âncora da seção com evidência real, ou `null`. */
  section: string | null;
  sectionLabel: string | null;
  /** Limitação quando não há evidência nesta página, ou `null`. */
  unavailableNote: string | null;
  /** `true` quando a Marca realmente evidencia o sinal. */
  hasEvidence: boolean;
}

/** Leitor mínimo de querystring — compatível com `URLSearchParams` e com o
 * `ReadonlyURLSearchParams` do Next, sem depender de nenhum tipo do Next. */
export interface ParamReader {
  get(key: string): string | null;
  getAll?(key: string): string[];
}

const isSignal = (v: string): v is ArrivalSignal => (ARRIVAL_SIGNALS as readonly string[]).includes(v);
const isChannel = (v: string): v is ArrivalChannel => (CHANNELS as readonly string[]).includes(v);
const isBrand = (v: string): boolean => (BRANDS as readonly string[]).includes(v);

/** Lê um parâmetro exigindo ocorrência ÚNICA: repetido (`?a=1&a=2`) é
 * ambíguo e portanto inválido — nunca "o primeiro ganha". */
function readSingle(params: ParamReader, key: string): string | null {
  if (typeof params.getAll === "function") {
    const all = params.getAll(key);
    if (all.length !== 1) return null;
    return all[0];
  }
  return params.get(key);
}

/**
 * Faz parse e valida o contexto de chegada. Devolve `null` (contexto
 * ignorado) em qualquer inconsistência — nunca lança, nunca erro na UI.
 *
 * @param params querystring atual
 * @param routeBrand marca da rota `/brand/[brand]`
 * @param selectedChannels canais no filtro global atual
 */
export function parseBrandArrivalContext(
  params: ParamReader,
  routeBrand: string,
  selectedChannels: readonly string[],
): BrandArrivalContext | null {
  const from = readSingle(params, "ctx_from");
  const signal = readSingle(params, "ctx_signal");
  const channel = readSingle(params, "ctx_channel");
  const brand = readSingle(params, "ctx_brand");

  // Todos obrigatórios: contexto parcial não é contexto.
  if (!from || !signal || !channel || !brand) return null;
  if (from !== CTX_FROM_CANAIS) return null;
  if (!isSignal(signal) || !isChannel(channel) || !isBrand(brand)) return null;

  // Compatibilidade com a página atual: marca da rota e canal no filtro.
  // Troca de marca (pills) ou de canal (filtro) descarta o contexto.
  if (brand !== routeBrand) return null;
  if (!selectedChannels.includes(channel)) return null;

  // Compatibilidade sinal × canal: a URL é entrada não confiável, então um
  // sinal impossível para o canal (ex.: `roas_forte` no TikTok, que não tem
  // Ads no contrato) nunca é anunciado — contexto ignorado em silêncio.
  if (!isSignalCompatibleWithChannel(signal, channel)) return null;

  const meta = SIGNAL_META[signal];
  return {
    from,
    signal,
    channel,
    channelLabel: CHANNEL_LABEL[channel],
    brand,
    description: meta.description,
    section: meta.section,
    sectionLabel: meta.sectionLabel,
    unavailableNote: meta.unavailableNote,
    hasEvidence: meta.section != null,
  };
}

/**
 * Escolhe o ÚNICO sinal que viaja na URL, pela prioridade do G2. Devolve
 * `null` quando a linha não tem sinal conhecido — nesse caso o CTA continua
 * funcionando, apenas sem `ctx_*`.
 */
export function pickArrivalSignal(signals: readonly string[]): ArrivalSignal | null {
  for (const candidate of SIGNAL_PRIORITY) {
    if (signals.includes(candidate)) return candidate;
  }
  return null;
}

/**
 * Monta o sufixo de querystring do contexto para anexar ao href de destino.
 * Devolve "" quando não há sinal válido. Só identificadores — nunca valores.
 */
export function buildArrivalParams(
  signals: readonly string[],
  channel: string,
  brand: string,
): string {
  const signal = pickArrivalSignal(signals);
  if (!signal || !isChannel(channel) || !isBrand(brand)) return "";
  // Mesma guarda do lado do produtor: nunca emitir um contexto que o
  // consumidor recusaria (sinal impossível para o canal).
  if (!isSignalCompatibleWithChannel(signal, channel)) return "";
  const qs = new URLSearchParams({
    ctx_from: CTX_FROM_CANAIS,
    ctx_signal: signal,
    ctx_channel: channel,
    ctx_brand: brand,
  });
  return qs.toString();
}

/**
 * Href de retorno à evidência de origem. Preserva os filtros globais atuais
 * (via `buildHref`/`mergeFilteredHref` do chamador) e fixa marca/canal do
 * contexto; NUNCA repropaga `ctx_*` — voltar não é "chegar quente".
 */
export function buildReturnHref(ctx: BrandArrivalContext): string {
  return `/canais?brands=${ctx.brand}&channels=${ctx.channel}`;
}

/** Rótulo do CTA de retorno. */
export const RETURN_CTA_LABEL = "Voltar à evidência em Canais";
