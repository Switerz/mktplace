/**
 * T-0014 — tela "Performance das contas" (contrato: docs/performance_contas_contrato.md).
 *
 * Tipos do contrato `GET /api/v1/performance-contas`, a flag da tela, o fetch
 * do servidor e as funcoes puras de apresentacao. Sem React aqui: tudo que
 * decide o que aparece na tela e' testavel com `node --test`.
 *
 * REGRAS QUE ESTE MODULO GARANTE (contrato §2 e §6.3)
 * ----------------------------------------------------
 *   - Oficial e Torre sao coisas distintas: o rotulo "Oficial" so' sai para
 *     `tipo === "oficial"`; qualquer outro valor vira "Cálculo Torre".
 *   - Ausencia nunca vira zero: `formatarValor` devolve `null` (e nao "0" nem
 *     "0%") para valor nulo ou estado diferente de `dado`.
 *   - Numero Torre nunca recebe faixa oficial: um limiar `oficial` ao lado de
 *     um indicador `torre` e' suprimido, e nenhum valor recebe cor de
 *     severidade nesta fatia.
 *   - Nenhum dado demonstrativo: falha de rede ou payload invalido vira erro
 *     da tela inteira, nunca numero.
 */

// ---------------------------------------------------------------------------
// Flag (fail-closed) — mesmo padrao de `estoque-full-flag.ts`.
// ---------------------------------------------------------------------------

/**
 * Somente a string EXATA "true" liga. Ausencia, "1", "TRUE" ou "yes" mantem
 * desligado.
 *
 * O acesso a `process.env` precisa ser LITERAL: o Next substitui a expressao
 * em BUILD por analise estatica do texto. Ler por indice com o nome numa
 * constante (`PERFORMANCE_CONTAS_FLAG`) nao e' substituido, e no
 * browser o valor viraria `undefined`.
 *
 * A rota devolve 404 com a flag desligada (guard no servidor, em `page.tsx`)
 * e o item de menu (`PERFORMANCE_CONTAS_NAV` em `nav-config.ts`) some.
 */
export const PERFORMANCE_CONTAS_FLAG = "NEXT_PUBLIC_PERFORMANCE_CONTAS_ENABLED";

/** Valor EXATO que liga. Qualquer outro mantem desligado. */
export const VALOR_QUE_LIGA = "true";

/** `env` existe SO' para teste. Sem ele, le `process.env` com acesso literal. */
export function habilitado(env?: Readonly<Record<string, string | undefined>>): boolean {
  if (env) {
    return env.NEXT_PUBLIC_PERFORMANCE_CONTAS_ENABLED === VALOR_QUE_LIGA;
  }
  return process.env.NEXT_PUBLIC_PERFORMANCE_CONTAS_ENABLED === VALOR_QUE_LIGA;
}

// ---------------------------------------------------------------------------
// Tipos do contrato da API
// ---------------------------------------------------------------------------

export type Canal = "mercado_livre" | "shopee" | "tiktok";
export type TipoIndicador = "oficial" | "torre";
export type EstadoIndicador = "dado" | "vazio_legitimo" | "sem_fonte" | "nao_comprovado" | "erro";
export type Unidade = "pct" | "contagem";
/** O limiar pode ter unidade propria (ex.: Expedicao ML conta envios, limite em horas). */
export type UnidadeLimiar = Unidade | "horas";
export type OrigemLimiar = "oficial" | "interno";

export interface Limiar {
  valor: number | null;
  origem: OrigemLimiar;
  rotulo: string;
  /** Opcional (aditivo): ausente ou `null` = mesma unidade do indicador. */
  unidade?: UnidadeLimiar | null;
}

export interface Indicador {
  codigo: string;
  rotulo: string;
  tipo: TipoIndicador;
  estado: EstadoIndicador;
  valor: number | null;
  unidade: Unidade | null;
  janela: string | null;
  limiar: Limiar | null;
  fonte: string;
  atualizado_em: string | null;
  aviso_frescor: string | null;
  cobertura: string | null;
  ajuda: string;
  motivo_indisponivel: string | null;
}

export interface CoberturaCanal {
  marcas: string[];
  fora: { marca: string; motivo: string }[];
}

export interface CanalPerformance {
  canal: Canal;
  cobertura: CoberturaCanal;
  indicadores: Indicador[];
}

export interface PerformanceContasResponse {
  gerado_em: string;
  canais: CanalPerformance[];
}

// ---------------------------------------------------------------------------
// Filtros (allowlist; valor fora da lista e' ignorado, nunca ecoado)
// ---------------------------------------------------------------------------

export const CANAIS: readonly { valor: Canal; texto: string }[] = [
  { valor: "mercado_livre", texto: "Mercado Livre" },
  { valor: "shopee", texto: "Shopee" },
  { valor: "tiktok", texto: "TikTok Shop" },
];

export function rotuloCanal(canal: string): string {
  return CANAIS.find((c) => c.valor === canal)?.texto ?? canal;
}

export interface FiltrosPerformanceContas {
  /** `null` = todos os canais. */
  canal: Canal | null;
  /** `null` = todas as marcas. */
  marca: string | null;
}

type ParamBruto = string | string[] | undefined;

function primeiro(v: ParamBruto): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/**
 * Le `?canal=` e `?brands=` da URL. Canal e marca so' entram se estiverem na
 * allowlist; qualquer outro valor cai em "todos".
 */
export function lerFiltros(
  params: Readonly<Record<string, ParamBruto>>,
  marcasPermitidas: readonly string[],
): FiltrosPerformanceContas {
  const canalBruto = primeiro(params.canal);
  const marcaBruta = primeiro(params.brands);
  const canal = CANAIS.some((c) => c.valor === canalBruto) ? (canalBruto as Canal) : null;
  const marca = marcaBruta && marcasPermitidas.includes(marcaBruta) ? marcaBruta : null;
  return { canal, marca };
}

/** O filtro de canal e' local: a API devolve os tres e a tela recorta. */
export function filtrarCanais(
  canais: readonly CanalPerformance[],
  canal: Canal | null,
): CanalPerformance[] {
  return canal === null ? [...canais] : canais.filter((c) => c.canal === canal);
}

// ---------------------------------------------------------------------------
// Funcoes puras de apresentacao
// ---------------------------------------------------------------------------

/** "Oficial" so' para `tipo === "oficial"`. Qualquer outra coisa e' Torre. */
export function rotuloTipo(tipo: string): "Oficial" | "Cálculo Torre" {
  return tipo === "oficial" ? "Oficial" : "Cálculo Torre";
}

function numeroValido(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

const FMT_PCT = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
const FMT_CONTAGEM = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const FMT_GENERICO = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });

/**
 * Formata um numero conforme a unidade. `pct` assume pontos percentuais
 * (4.5 -> "4,5%"), como os demais campos `*_pct` da API.
 */
export function formatarNumero(valor: number | null | undefined, unidade: UnidadeLimiar | null): string | null {
  if (!numeroValido(valor)) return null;
  if (unidade === "pct") return `${FMT_PCT.format(valor)}%`;
  if (unidade === "contagem") return FMT_CONTAGEM.format(valor);
  if (unidade === "horas") return `${FMT_GENERICO.format(valor)} h`;
  return FMT_GENERICO.format(valor);
}

/**
 * Valor exibivel do indicador. `null` quando o estado nao e' `dado` ou o valor
 * e' nulo/nao numerico — NUNCA "0" ou "0%" para ausencia.
 */
export function formatarValor(ind: Pick<Indicador, "estado" | "valor" | "unidade">): string | null {
  if (ind.estado !== "dado") return null;
  return formatarNumero(ind.valor, ind.unidade);
}

export type TipoStateBlock = "vazio" | "indisponivel" | "erro";

/**
 * Estado do contrato -> tipo do `StateBlock`. `null` so' para `dado`.
 * Estado desconhecido (contrato violado) vira `erro`, nunca dado.
 */
export function estadoParaStateBlock(estado: string): TipoStateBlock | null {
  switch (estado) {
    case "dado":
      return null;
    case "vazio_legitimo":
      return "vazio";
    case "sem_fonte":
    case "nao_comprovado":
      return "indisponivel";
    default:
      return "erro";
  }
}

/**
 * Estado que a tela efetivamente desenha. `dado` sem valor numerico e' um
 * contrato violado: vira `erro`, e nao zero.
 */
export function estadoEfetivo(ind: Pick<Indicador, "estado" | "valor">): EstadoIndicador | "invalido" {
  if (ind.estado === "dado") return numeroValido(ind.valor) ? "dado" : "invalido";
  if (estadoParaStateBlock(ind.estado) === "erro" && ind.estado !== "erro") return "invalido";
  return ind.estado;
}

/**
 * Titulo do bloco de estado (contrato §4, "texto minimo na tela"). Curto de
 * proposito: a janela inteira no titulo truncava e repetia a linha "Janela"
 * (QA). A janela vai so' na descricao (`descricaoEstado`).
 */
export function tituloEstado(estado: EstadoIndicador | "invalido", janela: string | null): string {
  switch (estado) {
    case "vazio_legitimo":
      return janela ? "Sem registros na janela" : "Sem registros no período";
    case "sem_fonte":
      return "Indisponível: sem fonte na Torre";
    case "nao_comprovado":
      return "Indisponível: autorização não comprovada";
    case "erro":
      return "Falha na fonte";
    case "invalido":
      return "Resposta inválida da API para este indicador";
    case "dado":
      return "";
  }
}

/**
 * Descricao do bloco de estado: o motivo da API; sem motivo, um `vazio_legitimo`
 * com janela mostra a janela aqui (e nao no titulo).
 */
export function descricaoEstado(
  estado: EstadoIndicador | "invalido",
  janela: string | null,
  motivo: string | null,
): string | null {
  if (motivo) return motivo;
  if (estado === "vazio_legitimo" && janela) return `Nenhum registro em ${janela}.`;
  return null;
}

/** Origem do limiar escrita por extenso. Interno nunca e' meta da plataforma. */
export function rotuloOrigemLimiar(origem: string): string {
  return origem === "oficial"
    ? "limite oficial da plataforma"
    : "referência interna, não é meta da plataforma";
}

/**
 * Limiar exibivel ao lado do indicador, ou `null`.
 *
 * Um limiar `oficial` ao lado de um numero `torre` e' suprimido (contrato
 * §2.1 e §6.3): ele mediria outra definicao. A API ja' nao deveria mandar
 * essa combinacao; aqui e' a segunda barreira.
 *
 * O valor usa `limiar.unidade` e, so' na ausencia dela, a do indicador: a
 * Expedicao ML conta envios mas o limite e' "48 h", nao 48 envios.
 * `texto` e `origem` sao desenhados em linhas separadas (o rotulo do limiar
 * nao se mistura com o texto da origem).
 */
export function limiarExibivel(
  ind: Pick<Indicador, "tipo" | "limiar" | "unidade">,
): { texto: string; origem: string } | null {
  const l = ind.limiar;
  if (!l) return null;
  if (l.origem === "oficial" && ind.tipo !== "oficial") return null;
  const valor = formatarNumero(l.valor, l.unidade ?? ind.unidade);
  return {
    texto: valor ? `${l.rotulo}: ${valor}` : l.rotulo,
    origem: rotuloOrigemLimiar(l.origem),
  };
}

/**
 * Tom do valor. Nesta fatia nenhum numero recebe cor de severidade: `neutro`
 * para dado, `ausente` para o resto. Em especial, numero Torre nunca e'
 * pintado com faixa oficial (contrato §6.3).
 */
export function tomDoValor(ind: Pick<Indicador, "estado" | "valor">): "neutro" | "ausente" {
  return estadoEfetivo(ind) === "dado" ? "neutro" : "ausente";
}

/** Data/hora em pt-BR (America/Sao_Paulo). Texto invalido volta como veio. */
export function formatarInstante(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("pt-BR", {
    timeZone: "America/Sao_Paulo",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// Validacao minima do payload e fetch do servidor
// ---------------------------------------------------------------------------

function ehObjeto(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

const TIPOS: readonly string[] = ["oficial", "torre"];
const ESTADOS: readonly string[] = ["dado", "vazio_legitimo", "sem_fonte", "nao_comprovado", "erro"];
const UNIDADES: readonly string[] = ["pct", "contagem"];
const UNIDADES_LIMIAR: readonly string[] = ["pct", "contagem", "horas"];
const ORIGENS_LIMIAR: readonly string[] = ["oficial", "interno"];

/** Campo opcional do contrato: string, `null` ou ausente. */
function textoOpcional(v: unknown): boolean {
  return v === undefined || v === null || typeof v === "string";
}

function numeroOuNulo(v: unknown): boolean {
  return v === undefined || v === null || numeroValido(v);
}

function problemaNoLimiar(l: unknown): string | null {
  if (l === undefined || l === null) return null;
  if (!ehObjeto(l)) return "limiar não é objeto";
  if (!numeroOuNulo(l.valor)) return "limiar.valor inválido";
  if (typeof l.origem !== "string" || !ORIGENS_LIMIAR.includes(l.origem)) return "limiar.origem fora do domínio";
  if (typeof l.rotulo !== "string") return "limiar.rotulo inválido";
  if (!(l.unidade === undefined || l.unidade === null || (typeof l.unidade === "string" && UNIDADES_LIMIAR.includes(l.unidade)))) {
    return "limiar.unidade fora do domínio";
  }
  return null;
}

function problemaNoIndicador(i: unknown): string | null {
  if (!ehObjeto(i)) return "indicador não é objeto";
  if (typeof i.codigo !== "string") return "indicador.codigo inválido";
  if (typeof i.rotulo !== "string") return "indicador.rotulo inválido";
  if (typeof i.tipo !== "string" || !TIPOS.includes(i.tipo)) return "indicador.tipo fora do domínio";
  if (typeof i.estado !== "string" || !ESTADOS.includes(i.estado)) return "indicador.estado fora do domínio";
  if (!numeroOuNulo(i.valor)) return "indicador.valor inválido";
  if (!(i.unidade === undefined || i.unidade === null || (typeof i.unidade === "string" && UNIDADES.includes(i.unidade)))) {
    return "indicador.unidade fora do domínio";
  }
  if (typeof i.fonte !== "string") return "indicador.fonte inválida";
  if (typeof i.ajuda !== "string") return "indicador.ajuda inválida";
  for (const campo of ["janela", "atualizado_em", "aviso_frescor", "cobertura", "motivo_indisponivel"] as const) {
    if (!textoOpcional(i[campo])) return `indicador.${campo} inválido`;
  }
  return problemaNoLimiar(i.limiar);
}

function problemaNaCobertura(c: unknown): string | null {
  if (!ehObjeto(c)) return "cobertura não é objeto";
  if (!Array.isArray(c.marcas) || !c.marcas.every((m) => typeof m === "string")) {
    return "cobertura.marcas inválida";
  }
  if (!Array.isArray(c.fora)) return "cobertura.fora inválida";
  for (const f of c.fora) {
    if (!ehObjeto(f) || typeof f.marca !== "string" || typeof f.motivo !== "string") {
      return "cobertura.fora com item inválido";
    }
  }
  return null;
}

/**
 * `null` se a forma bate com o contrato; senao, o problema encontrado.
 *
 * Valida em profundidade TODO campo que a tela desreferencia ou desenha
 * (parecer R20): um item quebrado em qualquer nivel recusa o payload inteiro
 * e a tela mostra erro — nunca um TypeError no render nem numero parcial.
 */
export function problemaNoPayload(json: unknown): string | null {
  if (!ehObjeto(json)) return "o corpo não é um objeto";
  if (typeof json.gerado_em !== "string") return "gerado_em ausente";
  if (!Array.isArray(json.canais)) return "canais ausente";
  for (const c of json.canais) {
    if (!ehObjeto(c)) return "canal não é objeto";
    if (!CANAIS.some((x) => x.valor === c.canal)) return "canal desconhecido";
    const cobertura = problemaNaCobertura(c.cobertura);
    if (cobertura) return cobertura;
    if (!Array.isArray(c.indicadores)) return "indicadores ausente";
    for (const i of c.indicadores) {
      const problema = problemaNoIndicador(i);
      if (problema) return problema;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Montagem da tela (o que `page.tsx` e `IndicadorItem.tsx` desenham)
// ---------------------------------------------------------------------------

export interface VisaoIndicador {
  chave: string;
  rotulo: string;
  tipo: "Oficial" | "Cálculo Torre";
  /** Texto do numero; `null` quando nao ha numero a desenhar. */
  valor: string | null;
  /** Bloco de estado no lugar do numero; `null` quando ha numero. */
  bloco: { tipo: TipoStateBlock; titulo: string; descricao: string | null } | null;
  idAjuda: string;
  ajuda: string;
  janela: string | null;
  atualizado: string | null;
  cobertura: string | null;
  limiar: { texto: string; origem: string } | null;
  fonte: string;
  avisoFrescor: string | null;
}

export interface VisaoCanal {
  canal: Canal;
  titulo: string;
  descricao: string;
  fora: { chave: string; texto: string }[];
  indicadores: VisaoIndicador[];
}

/** Tudo que `IndicadorItem` desenha, ja' resolvido. Puro e testavel. */
export function montarIndicador(ind: Indicador, canal: string): VisaoIndicador {
  const efetivo = estadoEfetivo(ind);
  const valor = efetivo === "dado" ? formatarValor(ind) : null;
  const tipoBloco = efetivo === "invalido" ? "erro" : estadoParaStateBlock(efetivo);
  return {
    chave: ind.codigo,
    rotulo: ind.rotulo,
    tipo: rotuloTipo(ind.tipo),
    valor,
    bloco:
      valor === null && tipoBloco
        ? {
            tipo: tipoBloco,
            titulo: tituloEstado(efetivo, ind.janela ?? null),
            descricao: descricaoEstado(efetivo, ind.janela ?? null, ind.motivo_indisponivel ?? null),
          }
        : null,
    // Id unico por canal + codigo, so' com caracteres seguros para atributo.
    idAjuda: `ajuda-${canal}-${ind.codigo}`.replace(/[^A-Za-z0-9_-]/g, "_"),
    ajuda: ind.ajuda,
    janela: ind.janela ?? null,
    atualizado: formatarInstante(ind.atualizado_em ?? null),
    cobertura: ind.cobertura ?? null,
    limiar: limiarExibivel(ind),
    fonte: ind.fonte,
    avisoFrescor: ind.aviso_frescor ?? null,
  };
}

/**
 * Tudo que a pagina desenha por canal, ja' resolvido. `rotuloMarca` e'
 * injetado (a pagina passa `brandLabel`) para manter este modulo sem imports.
 */
export function montarCanais(
  dados: PerformanceContasResponse,
  canal: Canal | null,
  rotuloMarca: (marca: string) => string,
): VisaoCanal[] {
  return filtrarCanais(dados.canais, canal).map((c) => {
    const marcas = c.cobertura.marcas.map(rotuloMarca).join(", ");
    return {
      canal: c.canal,
      titulo: rotuloCanal(c.canal),
      descricao: marcas ? `Cobertura: ${marcas}` : "Cobertura: nenhuma conta coberta",
      fora: c.cobertura.fora.map((f) => ({ chave: f.marca, texto: `${rotuloMarca(f.marca)}: ${f.motivo}` })),
      indicadores: c.indicadores.map((ind) => montarIndicador(ind, c.canal)),
    };
  });
}

/**
 * Mesma origem que o resto do app (`api-client.ts`, `dre-api.ts`,
 * `b2b-revenda.ts`): `NEXT_PUBLIC_API_URL`, com o mesmo fallback local.
 * `api-client.ts` nao exporta a constante, por isso ela e' repetida aqui.
 */
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export const ROTA_API = "/api/v1/performance-contas";

export function urlPerformanceContas(base: string, filtros: Pick<FiltrosPerformanceContas, "marca">): string {
  const qs = filtros.marca ? `?brands=${encodeURIComponent(filtros.marca)}` : "";
  return `${base}${ROTA_API}${qs}`;
}

export type ResultadoPerformanceContas =
  | { ok: true; dados: PerformanceContasResponse }
  | { ok: false; motivo: string };

/**
 * Fetch no SERVIDOR. Nunca lanca: falha de rede, status nao-2xx, JSON
 * quebrado ou forma invalida voltam como `{ ok: false }` e a tela inteira
 * mostra erro — nenhum numero.
 */
export async function carregarPerformanceContas(
  filtros: Pick<FiltrosPerformanceContas, "marca">,
  fetchImpl: typeof fetch = fetch,
  base: string = API_URL,
): Promise<ResultadoPerformanceContas> {
  let res: Response;
  try {
    res = await fetchImpl(urlPerformanceContas(base, filtros), { cache: "no-store" });
  } catch {
    return { ok: false, motivo: "Não foi possível contatar a API." };
  }
  if (!res.ok) {
    return { ok: false, motivo: `A API respondeu ${res.status}.` };
  }
  let json: unknown;
  try {
    json = await res.json();
  } catch {
    return { ok: false, motivo: "Resposta inválida da API (o corpo não é JSON)." };
  }
  const problema = problemaNoPayload(json);
  if (problema) return { ok: false, motivo: `Resposta inválida da API (${problema}).` };
  return { ok: true, dados: json as PerformanceContasResponse };
}
