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
// - Gate V3-2: `ctx_from` passa a ter DOIS valores, e o contrato vira uma
//   UNIÃO DISCRIMINADA POR ORIGEM. Canais continua idêntico, com `ctx_signal`;
//   Inteligência entra com `ctx_focus`, enum próprio. Reaproveitar
//   `ctx_signal` com outro significado seria mapeamento semanticamente falso:
//   os cinco sinais de Canais são sinais da matriz marca × canal, e as
//   categorias da Inteligência (desperdício, escala, orgânico, concentração,
//   LTV, produto TikTok) não são esses sinais. Propagação transitiva desde a
//   Gerencial segue sendo dívida futura do §8; não se cria enum sem wiring.

/** Origem da jornada — união discriminada a partir do Gate V3-2. */
export const CTX_FROM_CANAIS = "canais";
export const CTX_FROM_INTELIGENCIA = "inteligencia";
export type BrandArrivalFrom = typeof CTX_FROM_CANAIS | typeof CTX_FROM_INTELIGENCIA;

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

/** Âncora dos produtos TikTok DENTRO do contêiner mensal (Gate V3-2). Única
 * seção da Marca com evidência de produto TikTok; não foi criada para receber
 * âncora — ela já existia como "Top 5 Produtos". */
export const SECTION_MENSAL_PRODUTOS = "marca-produtos-tiktok";

// ---------------------------------------------------------------------------
// Gate V3-2 — origem `inteligencia`
// ---------------------------------------------------------------------------

/**
 * Focos allowlisted da Inteligência.
 *
 * Cada um espelha UM bloco real do payload de `/inteligencia` (§9.1):
 * `desperdicio_ads`←`urgent`, `escala_ads`←`scale`,
 * `venda_organica`←`organic`, `concentracao`←`pareto`, `ltv`←`ltv`,
 * `produto_tiktok`←`tk_products`. `signals` é agregado de suporte, sem produto
 * para navegar, e por isso não gera foco. Nenhum foco foi criado sem bloco que
 * o produza.
 */
export const ARRIVAL_FOCUSES = [
  "desperdicio_ads", "escala_ads", "venda_organica", "concentracao", "ltv", "produto_tiktok",
] as const;
export type ArrivalFocus = (typeof ARRIVAL_FOCUSES)[number];

/**
 * Canal semanticamente necessário de cada foco.
 *
 * Cada foco nasce de uma fonte de UM marketplace, então o canal é exigido nos
 * seis casos — o que preserva a validação já existente contra os canais
 * filtrados: chegar com foco de ML numa página sem ML no filtro descarta o
 * contexto, e isso é correto. Um `ctx_channel` que não seja o do foco também
 * descarta: a URL é entrada não confiável.
 */
const FOCUS_CHANNEL: Record<ArrivalFocus, ArrivalChannel> = {
  desperdicio_ads: "ml",
  escala_ads: "ml",
  venda_organica: "ml",
  concentracao: "ml",
  ltv: "ml",
  produto_tiktok: "tiktok",
};

export const isFocus = (v: string): v is ArrivalFocus =>
  (ARRIVAL_FOCUSES as readonly string[]).includes(v);

/** `true` quando o canal é o do foco no contrato vigente. */
export function isFocusCompatibleWithChannel(focus: ArrivalFocus, channel: ArrivalChannel): boolean {
  return FOCUS_CHANNEL[focus] === channel;
}

interface FocusMeta {
  description: string;
  section: string | null;
  sectionLabel: string | null;
  unavailableNote: string | null;
  /** Lente da fila de evidências no retorno, ou `null` quando o retorno é
   * apenas âncora (os focos que não têm lente correspondente). */
  returnLens: "parar" | "escalar" | "testar" | null;
  /** Âncora do bloco de origem na Inteligência. */
  returnAnchor: string;
}

/**
 * Mapa foco → o que a Marca REALMENTE evidencia, auditado bloco a bloco.
 *
 * Mesma disciplina do mapa de sinais de Canais: onde a Marca não tem a
 * evidência, ela declara a limitação em vez de prometer. Concentração (Pareto)
 * e LTV não existem nesta tela — dizer "veja abaixo" seria mentira de
 * navegação. Nenhuma descrição contém número.
 */
const FOCUS_META: Record<ArrivalFocus, FocusMeta> = {
  desperdicio_ads: {
    description: "desperdício de investimento em Ads no Mercado Livre",
    section: SECTION_PERIOD,
    sectionLabel: "Ver GMV e investimento do intervalo",
    unavailableNote:
      "Esta página mostra GMV e investimento da marca no intervalo global. A classificação " +
      "produto a produto (ad spend sem venda) fica na fila de evidências da Inteligência.",
    returnLens: "parar",
    returnAnchor: "fila-evidencias",
  },
  escala_ads: {
    description: "oportunidade de escalar Ads no Mercado Livre",
    section: SECTION_PERIOD,
    sectionLabel: "Ver GMV e investimento do intervalo",
    unavailableNote:
      "Esta página mostra GMV e investimento da marca no intervalo global. O ROAS por produto e a " +
      "referência do portfólio ficam na Inteligência.",
    returnLens: "escalar",
    returnAnchor: "fila-evidencias",
  },
  venda_organica: {
    description: "venda orgânica sem investimento em Ads no Mercado Livre",
    section: SECTION_PERIOD,
    sectionLabel: "Ver GMV e investimento do intervalo",
    unavailableNote:
      "Esta página mostra GMV e investimento da marca no intervalo global. Quais produtos vendem sem " +
      "Ads é classificação da fotografia ML, na Inteligência.",
    returnLens: "testar",
    returnAnchor: "fila-evidencias",
  },
  concentracao: {
    description: "concentração de receita no portfólio do Mercado Livre",
    section: null,
    sectionLabel: null,
    unavailableNote:
      "A concentração Pareto do portfólio não existe nesta página — ela vive na Inteligência. " +
      "Aqui a marca é lida por intervalo global e por competência mensal do TikTok.",
    returnLens: null,
    returnAnchor: "concentracao",
  },
  ltv: {
    description: "recorrência e valor do cliente da marca",
    section: null,
    sectionLabel: null,
    unavailableNote:
      "Recorrência e LTV não existem nesta página — são leitura cross-company e vivem na Inteligência. " +
      "Esta tela não tem dimensão de comprador.",
    returnLens: null,
    returnAnchor: "ltv",
  },
  produto_tiktok: {
    description: "produto em destaque no TikTok Shop",
    section: SECTION_MENSAL_PRODUTOS,
    sectionLabel: "Ver produtos do TikTok na competência",
    unavailableNote:
      "Os produtos aqui são da COMPETÊNCIA MENSAL selecionada; a lista da Inteligência é uma janela " +
      "de 30 dias. Períodos diferentes, portanto números diferentes — e nenhum dos dois traz Ads.",
    returnLens: null,
    returnAnchor: "produtos-tiktok",
  },
};

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

/** Parte comum das duas origens — tudo que o banner precisa sem saber de onde
 * o usuário veio. */
export interface BrandArrivalCommon {
  channel: ArrivalChannel;
  channelLabel: string;
  brand: string;
  /** Descrição humana do motivo (do código, allowlisted). Nunca contém número. */
  description: string;
  /** Âncora da seção com evidência real, ou `null`. */
  section: string | null;
  sectionLabel: string | null;
  /** Limitação quando não há evidência nesta página, ou `null`. */
  unavailableNote: string | null;
  /** `true` quando a Marca realmente evidencia o motivo. */
  hasEvidence: boolean;
}

/** Chegada por Canais — contrato original, inalterado. */
export interface CanaisArrivalContext extends BrandArrivalCommon {
  from: typeof CTX_FROM_CANAIS;
  signal: ArrivalSignal;
}

/** Chegada pela Inteligência (Gate V3-2) — enum próprio, nunca `ctx_signal`. */
export interface InteligenciaArrivalContext extends BrandArrivalCommon {
  from: typeof CTX_FROM_INTELIGENCIA;
  focus: ArrivalFocus;
}

/** União discriminada por `from`. */
export type BrandArrivalContext = CanaisArrivalContext | InteligenciaArrivalContext;

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
 * PRESENÇA do parâmetro — qualquer ocorrência, com qualquer valor.
 *
 * Deliberadamente distinta de `readSingle`, que responde "há exatamente um
 * valor?" e devolve `null` tanto para ausente quanto para repetido. Usar
 * `readSingle(x) != null` como teste de presença tem um furo exato: uma chave
 * ESTRANGEIRA repetida (`?ctx_signal=a&ctx_signal=b` numa URL de Inteligência)
 * devolveria `null` e passaria pela guarda, exatamente o caso mais suspeito.
 * Aqui, uma ocorrência, várias, valor vazio ou valor inválido contam todos como
 * presentes.
 */
function hasParam(params: ParamReader, key: string): boolean {
  if (typeof params.getAll === "function") return params.getAll(key).length > 0;
  return params.get(key) != null;
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
  const channel = readSingle(params, "ctx_channel");
  const brand = readSingle(params, "ctx_brand");

  // Marca, canal e origem são obrigatórios nas duas origens: contexto parcial
  // não é contexto. O motivo é lido pela chave da origem correspondente.
  if (!from || !channel || !brand) return null;
  if (!isChannel(channel) || !isBrand(brand)) return null;

  // Compatibilidade com a página atual: marca da rota e canal no filtro.
  // Troca de marca (pills) ou de canal (filtro) descarta o contexto.
  if (brand !== routeBrand) return null;
  if (!selectedChannels.includes(channel)) return null;

  // A união é discriminada, e isso vale nas DUAS direções: cada origem rejeita
  // a chave de motivo da outra. Uma URL com as duas famílias é ambígua sobre a
  // própria origem, e ambiguidade nunca vira escolha silenciosa.
  if (from === CTX_FROM_CANAIS) {
    if (hasParam(params, "ctx_focus")) return null;
    const signal = readSingle(params, "ctx_signal");
    if (!signal || !isSignal(signal)) return null;
    // Compatibilidade sinal × canal: a URL é entrada não confiável, então um
    // sinal impossível para o canal (ex.: `roas_forte` no TikTok, que não tem
    // Ads no contrato) nunca é anunciado — contexto ignorado em silêncio.
    if (!isSignalCompatibleWithChannel(signal, channel)) return null;

    const meta = SIGNAL_META[signal];
    return {
      from: CTX_FROM_CANAIS,
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

  if (from === CTX_FROM_INTELIGENCIA) {
    if (hasParam(params, "ctx_signal")) return null;
    const focus = readSingle(params, "ctx_focus");
    if (!focus || !isFocus(focus)) return null;
    // Mesma guarda da origem Canais: o canal do foco é fixo no contrato, e um
    // par foco × canal impossível (ex.: `produto_tiktok` com `ctx_channel=ml`)
    // nunca é anunciado.
    if (!isFocusCompatibleWithChannel(focus, channel)) return null;

    const meta = FOCUS_META[focus];
    return {
      from: CTX_FROM_INTELIGENCIA,
      focus,
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

  // Origem desconhecida (ex.: `gerencial`) ⇒ contexto ignorado, sem erro.
  return null;
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
 *
 * Para a Inteligência o retorno reconstrói **marca + lente/âncora** (§9.2):
 * os três focos que vêm da fila voltam para a lente correspondente; os outros
 * três voltam para a âncora do bloco que os produziu, porque não há lente
 * para eles.
 */
export function buildReturnHref(ctx: BrandArrivalContext): string {
  if (ctx.from === CTX_FROM_INTELIGENCIA) {
    const meta = FOCUS_META[ctx.focus];
    const lens = meta.returnLens ? `&lens=${meta.returnLens}` : "";
    return `/inteligencia?brands=${ctx.brand}${lens}#${meta.returnAnchor}`;
  }
  return `/canais?brands=${ctx.brand}&channels=${ctx.channel}`;
}

/**
 * `true` quando o href de retorno DEVE passar por `mergeFilteredHref`.
 *
 * Só Canais. Dois motivos independentes, e cada um sozinho já bastaria:
 *
 * 1. `/inteligencia` **não é filter-aware** (`FILTER_AWARE_PAGES`): injetar
 *    `channels`/`date_from`/`compare` nela seria propagar filtro para uma tela
 *    de semântica própria, exatamente o que o contrato de navegação proíbe;
 * 2. o retorno da Inteligência tem **âncora**, e `mergeFilteredHref` faz
 *    `split("?")` e joga o resto num `URLSearchParams` — o `#fila-evidencias`
 *    viraria parte do valor de `lens` (`lens=parar%23fila-evidencias`),
 *    quebrando a lente E perdendo a âncora. O href de retorno da Inteligência
 *    já está completo e vai direto.
 */
export function returnPreservesGlobalFilters(ctx: BrandArrivalContext): boolean {
  return ctx.from === CTX_FROM_CANAIS;
}

/** Rótulo do CTA de retorno para Canais (mantido para compatibilidade). */
export const RETURN_CTA_LABEL = "Voltar à evidência em Canais";
export const RETURN_CTA_LABEL_INTELIGENCIA = "Voltar à evidência em Inteligência";

/** Rótulo do CTA de retorno conforme a origem. */
export function returnCtaLabel(ctx: BrandArrivalContext): string {
  return ctx.from === CTX_FROM_INTELIGENCIA ? RETURN_CTA_LABEL_INTELIGENCIA : RETURN_CTA_LABEL;
}

/** Rótulo humano da origem, para a copy do banner. */
export function originLabel(ctx: BrandArrivalContext): string {
  return ctx.from === CTX_FROM_INTELIGENCIA ? "Inteligência" : "Canais";
}

/**
 * Sufixo de querystring do contexto quente da Inteligência.
 *
 * Espelha `buildArrivalParams` de Canais: só identificadores, e a mesma guarda
 * do lado do produtor — nunca emitir um contexto que o consumidor recusaria.
 * O canal não é parâmetro: ele é **derivado do foco**, porque no contrato cada
 * foco nasce de uma fonte de um único marketplace. Deixá-lo aberto permitiria
 * ao produtor montar um par impossível.
 */
export function buildInteligenciaArrivalParams(
  focus: ArrivalFocus | null,
  brand: string,
): string {
  if (!focus || !isFocus(focus) || !isBrand(brand)) return "";
  const qs = new URLSearchParams({
    ctx_from: CTX_FROM_INTELIGENCIA,
    ctx_focus: focus,
    ctx_channel: FOCUS_CHANNEL[focus],
    ctx_brand: brand,
  });
  return qs.toString();
}

/**
 * Foco a emitir a partir da lente da fila de evidências.
 *
 * Mapeamento EXATO, não aproximação: as três lentes são as três listas do
 * payload, e os três focos são exatamente essas listas (§9.1) —
 * `parar`←`urgent`, `escalar`←`scale`, `testar`←`organic`.
 */
export function focusForEvidenceKind(kind: "parar" | "escalar" | "testar"): ArrivalFocus {
  return kind === "parar" ? "desperdicio_ads" : kind === "escalar" ? "escala_ads" : "venda_organica";
}

/**
 * Foco a emitir a partir do quadrante da matriz de oportunidades — ou `null`.
 *
 * Só `escalar` produz foco, e por identidade de população demonstrável: o
 * quadrante exige GMV acima da mediana **e** ROAS ≥ referência, e a referência
 * de ROAS é a mesma da lista `scale`, logo todo ponto de `escalar` pertence à
 * população de `escala_ads`.
 *
 * `reduzir_parar` NÃO vira `desperdicio_ads`: aquele foco é
 * `product_status = 'ad_spend_no_sales'`, e um ponto do quadrante inferior tem
 * ROAS medido — pode ter venda. Mapeá-lo seria inventar classificação só para
 * produzir contexto. `monitorar` e `testar_investimento` também não têm foco
 * demonstrável. Nesses casos o CTA continua FRIO, e isso é a resposta certa.
 */
export function focusForQuadrant(quadrant: string): ArrivalFocus | null {
  return quadrant === "escalar" ? "escala_ads" : null;
}
