// Competência mensal da Marca 360 (Gate V3-2; desenho em
// docs/INTELIGENCIA_BRAND_V3_PLAN.md §8.2 e §15.2/BE5).
//
// A página de Marca convive com DOIS regimes temporais, e o defeito M1 do
// desenho era exatamente eles coexistirem sem marcação:
//
// 1. o **intervalo global** da Torre (`date_from`/`date_to`), que governa a
//    série diária e o mix de canais;
// 2. a **competência mensal** do TikTok (`ref_month`), que governa tudo que
//    vem de `/brand-detail` — a fonte só suporta mês calendário fechado.
//
// Este módulo é a verdade sobre o segundo regime: parse, resolução, rótulo,
// identidade de requisição e a relação entre os dois. Módulo puro (sem React,
// sem tipos do Next), testável com `node:test`.
//
// Regras duras:
// - a lista de competências vem SEMPRE de `available_months` da resposta real
//   (BE5). Nenhum mês é derivado de módulo de mock (defeito M2);
// - `ref_month` na URL é entrada não confiável: formato inválido, parâmetro
//   repetido ou ausência resolvem sem erro;
// - competência bem formada mas **sem dado para a marca** é PRESERVADA, com
//   estado vazio explícito. Trocar silenciosamente de mês esconderia do
//   analista que o mês pedido não existe;
// - nenhuma métrica viaja na URL — só o identificador da competência.

import type { MonthOption } from "../produtos-tab-transition.ts";

/** Chave da competência na querystring. Deliberadamente FORA de
 * `FILTER_QUERY_KEYS`: é estado local reproduzível da rota `/brand/[brand]`,
 * não filtro global, então a sidebar nunca a propaga para outra tela. */
export const REF_MONTH_QUERY_KEY = "ref_month";

/** `YYYY-MM` canônico, com mês real (01–12). `2026-13` é inválido. */
const REF_MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;

const MES_ABBR = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

export function isRefMonth(v: unknown): v is string {
  return typeof v === "string" && REF_MONTH_RE.test(v);
}

/** Leitor mínimo de querystring — o mesmo contrato de `brand-arrival-context`
 * e `lens`, para não depender de nenhum tipo do Next. */
export interface ParamReader {
  get(key: string): string | null;
  getAll?(key: string): string[];
}

/** Lê exigindo ocorrência ÚNICA: repetido (`?ref_month=a&ref_month=b`) é
 * ambíguo, e ambiguidade nunca vira escolha silenciosa. */
function readSingle(params: ParamReader, key: string): string | null {
  if (typeof params.getAll === "function") {
    const all = params.getAll(key);
    if (all.length !== 1) return null;
    return all[0];
  }
  return params.get(key);
}

/** Competência pedida pela URL, ou `null` quando ausente/repetida/inválida. */
export function parseRefMonth(params: ParamReader): string | null {
  const raw = readSingle(params, REF_MONTH_QUERY_KEY);
  if (raw == null) return null;
  const trimmed = raw.trim();
  return isRefMonth(trimmed) ? trimmed : null;
}

/** De onde veio a competência efetivamente usada. */
export type RefMonthSource = "url" | "latest" | "default";

export interface RefMonthResolution {
  /** Competência a exibir/consultar. `null` só quando não há nenhuma. */
  month: string | null;
  source: RefMonthSource;
  /** `true` quando `month` consta em `available_months`. */
  available: boolean;
  /** `true` quando a marca tem ao menos uma competência com dado. */
  hasAvailable: boolean;
}

/**
 * Resolve a competência efetiva.
 *
 * A ordem importa e é deliberada: **a URL manda**. Uma competência bem
 * formada pedida na URL nunca é substituída, mesmo quando não há dado para
 * ela — nesse caso `available` vem `false` e a UI mostra o vazio explícito
 * nomeando o mês. Só a ausência de pedido cai na competência mais recente
 * realmente disponível.
 */
export function resolveRefMonth(
  requested: string | null,
  available: readonly string[],
): RefMonthResolution {
  const lista = available.filter(isRefMonth);
  const hasAvailable = lista.length > 0;
  if (requested != null && isRefMonth(requested)) {
    return { month: requested, source: "url", available: lista.includes(requested), hasAvailable };
  }
  if (hasAvailable) {
    return { month: lista[0], source: "latest", available: true, hasAvailable };
  }
  return { month: null, source: "default", available: false, hasAvailable };
}

// Não existe helper de "adoção" da competência mais recente, e isso é
// deliberado. A adoção acontece **por derivação**: `available_months` chega na
// resposta, entra no estado de disponibilidade, e `resolveRefMonth` passa a
// devolver `available[0]` em vez de `null`. Isso muda `brandDetailRequestKey`,
// e a mudança de identidade é o que dispara a segunda leitura — no máximo uma,
// porque na rodada seguinte a competência já consta na lista e a resolução
// estabiliza. Um helper `latestToAdopt` existiu numa versão anterior deste
// módulo, testado isoladamente e **sem consumidor na página**: dava falsa prova
// de wiring, e por isso foi removido em vez de mantido.

/**
 * Disponibilidade de competências **com a marca que a produziu**.
 *
 * A lista de competências é um fato POR MARCA, e guardá-la num `string[]` solto
 * era um furo na proteção de frescor: ao trocar de marca pelos pills, o seletor
 * seguia oferecendo os meses da marca anterior durante o loading, e se a nova
 * leitura falhasse a lista antiga permanecia — oferecendo escolhas que não
 * pertencem à marca da rota.
 *
 * `servedMonth` guarda o `ref_month` que a resposta ecoou. Serve para nomear a
 * competência no estado vazio quando a URL não pediu nenhuma e a lista veio
 * vazia: nesse caminho `resolveRefMonth` devolve `month: null`, e "sem dado
 * para —" não diz nada a ninguém.
 */
export interface MonthAvailability {
  brand: string;
  months: string[];
  servedMonth: string | null;
}

/**
 * Disponibilidade **da marca da rota**, ou `null`.
 *
 * É este portão que faz a lista de uma marca nunca aparecer em outra: enquanto
 * a resposta da marca nova não chega, não há disponibilidade aplicável, e um
 * erro depois da troca também não ressuscita a lista antiga. Um retry da MESMA
 * marca, por outro lado, continua enxergando a própria lista — é a mesma
 * identidade.
 */
export function availabilityForBrand(
  a: MonthAvailability | null,
  brand: string,
): MonthAvailability | null {
  return a != null && a.brand === brand ? a : null;
}

/** Rótulo de COMPETÊNCIA — `ago/2026`. Deliberadamente distinto do rótulo de
 * intervalo (`01/08/2026 – 31/08/2026`, `fmtPeriodo`): os dois regimes nunca
 * usam a mesma formatação, para que a etiqueta identifique o regime sozinha. */
export function fmtCompetencia(month: string | null): string {
  if (!isRefMonth(month)) return "—";
  const [y, m] = month.split("-");
  return `${MES_ABBR[Number(m) - 1]}/${y}`;
}

/** Opções do `PeriodSelector` a partir das competências REAIS da resposta. */
export function monthOptions(available: readonly string[]): MonthOption[] {
  const vistos = new Set<string>();
  const opts: MonthOption[] = [];
  for (const v of available) {
    // repetido é ignorado com segurança: a lista serve para escolher, e um
    // mês duas vezes seria dois botões idênticos.
    if (!isRefMonth(v) || vistos.has(v)) continue;
    vistos.add(v);
    opts.push({ value: v, label: fmtCompetencia(v) });
  }
  return opts;
}

/**
 * Identidade da requisição mensal — `brand` + competência, e nada mais.
 *
 * Separada de propósito da identidade diária/global (`buildDailyRequestKey`,
 * que carrega canais, intervalo e `compare`): os dois fetches têm gatilhos
 * diferentes, e misturá-los faria a troca de intervalo global invalidar dado
 * mensal que continua válido — e vice-versa.
 */
export function brandDetailRequestKey(brand: string, month: string | null): string {
  return `${brand}|${month ?? "default"}`;
}

/**
 * Estado exibível do contêiner mensal.
 *
 * Os quatro são mutuamente exclusivos e a ordem de decisão é o contrato:
 *
 * - `loading`: requisição em andamento, ou chave atual ainda não resolvida.
 *   Ausência **ainda não resolvida** nunca vira vazio;
 * - `error`: a leitura da chave atual falhou. **Nunca** volta a skeleton, que é
 *   o defeito clássico de `!isFresh`, e sempre oferece "Tentar novamente";
 * - `empty`: a leitura teve sucesso e a resposta é "não há dado" — ou a marca
 *   não tem competência alguma (`available_months = []`), ou a competência
 *   pedida não consta na lista;
 * - `ready`: payload da competência pedida, que consta na lista.
 *
 * **Não existe `unavailable`.** Existiu, com a copy "a consulta concluiu sem
 * payload", e era semanticamente falso: `apiFetch` devolve `null` para HTTP
 * não-2xx, falha de rede, JSON inválido e qualquer exceção capturada — ou seja,
 * `null` é **falha de leitura**, não conclusão sem conteúdo. Não há hoje nenhum
 * gatilho demonstrável para um terceiro estado entre erro e vazio, então o
 * estado foi removido em vez de mantido como distinção inventada, que também
 * privava o usuário do retry.
 */
export type MonthlyViewState = "loading" | "error" | "empty" | "ready";

export function monthlyViewState(input: {
  loading: boolean;
  error: boolean;
  /** `true` quando a competência resolvida consta em `available_months`. */
  monthAvailable: boolean;
}): MonthlyViewState {
  if (input.loading) return "loading";
  if (input.error) return "error";
  if (!input.monthAvailable) return "empty";
  return "ready";
}

export interface RegimeRelation {
  /** `true` quando a competência intersecta o intervalo global. */
  overlaps: boolean;
  /** Nota neutra quando NÃO há sobreposição; `null` quando há (ou quando
   * falta informação para afirmar qualquer coisa). */
  note: string | null;
}

/** Último dia do mês, em ISO. `2026-02` ⇒ `2026-02-28`. */
function fimDoMes(month: string): string {
  const [y, m] = month.split("-").map(Number);
  // dia 0 do mês seguinte = último dia deste mês; ano/mês são explícitos,
  // então a função continua pura (nenhum `new Date()` sem argumento).
  const dia = new Date(y, m, 0).getDate();
  return `${month}-${String(dia).padStart(2, "0")}`;
}

/**
 * Relação entre os dois regimes.
 *
 * Ausência de sobreposição **não é erro** e não bloqueia nada: é escopo. Os
 * dois blocos continuam corretos, cada um no seu período — o que faltava era
 * dizer isso na tela.
 */
export function periodRegimeRelation(
  month: string | null,
  dateFrom: string,
  dateTo: string,
): RegimeRelation {
  if (!isRefMonth(month) || !dateFrom || !dateTo) return { overlaps: false, note: null };
  const inicio = `${month}-01`;
  const fim = fimDoMes(month);
  // comparação lexicográfica de datas ISO é comparação cronológica
  const overlaps = inicio <= dateTo && fim >= dateFrom;
  if (overlaps) return { overlaps: true, note: null };
  return {
    overlaps: false,
    note:
      "A competência mensal do TikTok está fora do intervalo global selecionado. " +
      "Os blocos abaixo representam períodos diferentes.",
  };
}
