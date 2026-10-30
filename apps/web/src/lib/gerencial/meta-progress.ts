/**
 * Gerencial x Metas (GERENCIAL-METAS-1) — logica PURA do progresso contra a
 * meta mensal digitada na aba "Metas".
 *
 * QUANDO A META SE APLICA
 * -----------------------
 * A meta e' MENSAL. So' faz sentido comparar quando o periodo filtrado e' um
 * mes so', comecando no dia 1, e cobre o mes inteiro OU o mes corrente ate'
 * ontem (o preset "Mes atual" termina no ultimo dia fechado, D-1). "01 a 15/09" contra a meta cheia de setembro seria um atingimento
 * falso; nesses casos `mesDaMeta` devolve `null` e a tela cai na comparacao
 * com o periodo anterior.
 *
 * ESCOPO
 * ------
 * A meta somada respeita os MESMOS filtros do realizado: canais selecionados e
 * marcas selecionadas (nenhuma marca = todas). Celula sem meta nao entra na
 * soma; se nenhuma celula do escopo tiver meta, nao ha meta (`null`), e nunca
 * meta zero.
 *
 * PROJECAO
 * --------
 * Ritmo contra o plano: realizado / fatia do mes esperada ate' o ultimo dia com
 * dado. Com curva (METAS-DIARIAS-1, `metas-curva.ts`) a fatia segue os pesos
 * dos dias; sem curva e' linear (dias com dado / dias do mes). "Dias com dado"
 * e' o ultimo dia que tem venda na serie diaria — o Data Mart chega com D-1, e
 * contar hoje (ainda sem dado) puxaria a projecao para baixo.
 */
import type { Marketplace } from "../mock-data.ts";

export interface CelulaMetaGmv {
  brand: string;
  channel: string;
  meta_gmv: number;
}

export function diasNoMes(mes: string): number {
  const [a, m] = mes.split("-").map(Number);
  return new Date(Date.UTC(a, m, 0)).getUTCDate();
}

const fimDoMes = (mes: string) => `${mes}-${String(diasNoMes(mes)).padStart(2, "0")}`;

/** Dia anterior de um `AAAA-MM-DD`. */
function diaAnterior(dia: string): string {
  const [a, m, d] = dia.split("-").map(Number);
  return new Date(Date.UTC(a, m - 1, d - 1)).toISOString().slice(0, 10);
}

/**
 * `AAAA-MM` quando o periodo e' comparavel com a meta mensal: comeca no dia 1,
 * termina no mesmo mes, e vai ate' o fim do mes ou ate' ontem (mes corrente).
 */
export function mesDaMeta(dateFrom: string, dateTo: string, hoje: string): string | null {
  if (!/^\d{4}-\d{2}-01$/.test(dateFrom)) return null;
  const mes = dateFrom.slice(0, 7);
  if (dateTo.slice(0, 7) !== mes) return null;
  if (dateTo === fimDoMes(mes)) return mes;
  // Mes corrente ate' o ultimo dia fechado: o preset "Mes atual" termina em
  // D-1 (a fato diaria so' publica dias fechados).
  if (hoje.slice(0, 7) === mes && dateTo >= diaAnterior(hoje)) return mes;
  return null;
}

export interface MetaDoEscopo {
  total: number | null;
  porMarca: Record<string, number | null>;
  celulasComMeta: number;
}

/** Celulas com meta dentro dos canais e marcas filtrados (nenhuma marca = todas). */
export function celulasDoEscopo(
  celulas: CelulaMetaGmv[],
  channels: Marketplace[],
  brands: string[],
): CelulaMetaGmv[] {
  return celulas.filter(
    (c) => channels.includes(c.channel as Marketplace) && (brands.length === 0 || brands.includes(c.brand)),
  );
}

export function metaDoEscopo(
  celulas: CelulaMetaGmv[],
  channels: Marketplace[],
  brands: string[],
): MetaDoEscopo {
  const porMarca: Record<string, number | null> = {};
  let total: number | null = null;
  let n = 0;
  for (const c of celulasDoEscopo(celulas, channels, brands)) {
    porMarca[c.brand] = (porMarca[c.brand] ?? 0) + c.meta_gmv;
    total = (total ?? 0) + c.meta_gmv;
    n += 1;
  }
  return { total, porMarca, celulasComMeta: n };
}

/** Linha de `/performance/brands`: GMV de uma marca por canal. */
export interface GmvPorCanal {
  brand: string;
  tiktok_gmv: number | null;
  ml_gmv: number | null;
  shopee_gmv: number | null;
}

export const GMV_DO_CANAL: Record<Marketplace, "tiktok_gmv" | "ml_gmv" | "shopee_gmv"> = {
  tiktok: "tiktok_gmv",
  ml: "ml_gmv",
  shopee: "shopee_gmv",
};

export interface RealizadoDoEscopo {
  total: number;
  porMarca: Record<string, number>;
  /** Canais que entram na conta de cada marca (os que tem meta). */
  canaisPorMarca: Record<string, Marketplace[]>;
}

/**
 * Realizado COMPARAVEL com a meta: so' as marcas x canais que tem meta
 * (METAS-DIARIAS-1). Em out/26 o TikTok vendeu R$ 12 mi sem ter meta; somar
 * esse GMV contra a meta de ML + Shopee inflava o atingimento. Celula com meta
 * e sem venda entra com zero.
 */
export function realizadoDoEscopo(linhas: GmvPorCanal[], celulasEscopo: CelulaMetaGmv[]): RealizadoDoEscopo {
  const porMarca: Record<string, number> = {};
  const canaisPorMarca: Record<string, Marketplace[]> = {};
  let total = 0;
  for (const c of celulasEscopo) {
    const canal = c.channel as Marketplace;
    const linha = linhas.find((l) => l.brand === c.brand);
    const v = (linha ? linha[GMV_DO_CANAL[canal]] : null) ?? 0;
    porMarca[c.brand] = (porMarca[c.brand] ?? 0) + v;
    (canaisPorMarca[c.brand] ??= []).push(canal);
    total += v;
  }
  return { total, porMarca, canaisPorMarca };
}

/**
 * O dia, quando o periodo e' um dia so' (ex.: "Hoje"): ai' vale a meta do dia.
 *
 * Unica ambiguidade: o dia 1 sozinho tambem e' o "Mes atual" do dia 2 (o
 * preset termina em D-1). Ai' a meta continua mensal; so' o proprio dia de
 * hoje vira meta do dia.
 */
export function diaDaMeta(dateFrom: string, dateTo: string, hoje: string): string | null {
  if (dateFrom !== dateTo) return null;
  if (dateFrom !== hoje && mesDaMeta(dateFrom, dateTo, hoje) != null) return null;
  return dateFrom;
}

/**
 * Ultimo dia com venda na serie DIARIA (algum canal com numero > 0). Fora do
 * grao diario nao da para saber o dia: `null`, e a projecao usa ontem.
 */
export function ultimoDiaComDado(
  buckets: { date: string; values: Record<string, number | null> }[],
  granularity: string,
): string | null {
  if (granularity !== "day") return null;
  for (let i = buckets.length - 1; i >= 0; i--) {
    if (Object.values(buckets[i].values).some((v) => v != null && v > 0)) return buckets[i].date;
  }
  return null;
}

export type StatusMeta = "atingida" | "no_caminho" | "atencao" | "abaixo" | "nao_atingida";

export const ROTULO_STATUS: Record<StatusMeta, string> = {
  atingida: "Meta atingida",
  no_caminho: "No caminho",
  atencao: "Atenção",
  abaixo: "Abaixo do ritmo",
  nao_atingida: "Meta não atingida",
};

/** Projecao entre 95% e 100% da meta: perto o bastante para ser "atencao". */
export const LIMIAR_ATENCAO = 0.95;

export interface ProgressoMeta {
  meta: number;
  realizado: number;
  /** realizado / meta, em fracao (0,76 = 76%). */
  atingimento: number;
  diasNoMes: number;
  diasComDado: number;
  diasRestantes: number;
  encerrado: boolean;
  /** Quanto o plano esperava ate' o ultimo dia com dado (curva ou linear). */
  planoAteData: number;
  /** realizado / planoAteData: 1 = exatamente no plano. */
  atingimentoDoPlano: number;
  /** `null` quando ainda nao ha nenhum dia com dado no mes. */
  projecao: number | null;
  projecaoSobreMeta: number | null;
  falta: number;
  /** Quanto precisa vender por dia, nos dias restantes, para bater a meta. */
  porDiaNecessario: number | null;
  status: StatusMeta;
}

const diaDe = (iso: string) => Number(iso.slice(8, 10));

function ontem(hoje: string): string {
  const [a, m, d] = hoje.split("-").map(Number);
  const t = new Date(Date.UTC(a, m - 1, d - 1));
  return t.toISOString().slice(0, 10);
}

export function progressoMeta(args: {
  meta: number | null;
  realizado: number | null;
  mes: string;
  hoje: string;
  /** Ultimo dia com venda na serie diaria; `null` = desconhecido. */
  ultimoDia: string | null;
  /**
   * Fatia da meta esperada ate' o fim do dia `dia` (curva do escopo). Ausente
   * = linear (dia / dias do mes), a conta de antes das curvas.
   */
  fracaoAte?: (dia: number) => number;
}): ProgressoMeta | null {
  const { meta, realizado, mes, hoje } = args;
  if (meta == null || meta <= 0 || realizado == null) return null;
  const n = diasNoMes(mes);
  const encerrado = hoje > fimDoMes(mes);

  let diasComDado: number;
  if (encerrado) {
    diasComDado = n;
  } else {
    const referencia = args.ultimoDia && args.ultimoDia.slice(0, 7) === mes ? args.ultimoDia : ontem(hoje);
    diasComDado = referencia.slice(0, 7) === mes ? Math.min(diaDe(referencia), n) : 0;
  }
  const diasRestantes = encerrado ? 0 : n - diasComDado;
  // Ritmo contra o PLANO: quem vendeu o que a curva esperava ate' aqui termina
  // o mes na meta. No linear, fracao = dias/n e a projecao e' a de sempre.
  const fracao = args.fracaoAte ? args.fracaoAte(diasComDado) : diasComDado / n;
  const planoAteData = meta * fracao;
  const projecao = encerrado ? realizado : fracao > 0 ? realizado / fracao : null;
  const falta = Math.max(meta - realizado, 0);

  let status: StatusMeta;
  if (realizado >= meta) status = "atingida";
  else if (encerrado) status = "nao_atingida";
  else if (projecao == null) status = "atencao";
  else if (projecao >= meta) status = "no_caminho";
  else if (projecao >= meta * LIMIAR_ATENCAO) status = "atencao";
  else status = "abaixo";

  return {
    meta,
    realizado,
    atingimento: realizado / meta,
    diasNoMes: n,
    diasComDado,
    diasRestantes,
    encerrado,
    planoAteData,
    atingimentoDoPlano: planoAteData > 0 ? realizado / planoAteData : 0,
    projecao,
    projecaoSobreMeta: projecao == null ? null : projecao / meta,
    falta,
    porDiaNecessario: diasRestantes > 0 && falta > 0 ? falta / diasRestantes : null,
    status,
  };
}

/**
 * Os ate' `limite` sinais comerciais que pedem decisao, por severidade
 * (critico, atencao, informativo) e, no empate, na ordem da fila. O aviso de
 * dado nao entra: ele aparece colado no GMV, no bloco principal.
 */
export function topComerciais<C extends { severity: "critical" | "warning" | "info" }>(
  commercial: C[],
  limite = 3,
): C[] {
  const ordem = { critical: 0, warning: 1, info: 2 } as const;
  return commercial
    .map((item, i) => ({ item, i }))
    .sort((a, b) => ordem[a.item.severity] - ordem[b.item.severity] || a.i - b.i)
    .slice(0, limite)
    .map(({ item }) => item);
}

/**
 * O aviso de dado que muda a leitura do GMV: so' os da fonte do fato diario de
 * performance, que e' de onde sai o total. Aviso de regiao, por exemplo, nao
 * distorce o GMV e fica na confianca no dado, em "Mais detalhes".
 */
export function avisoQueAfetaOGmv<D extends { severity: "attention" | "note"; source: string | null }>(
  itens: D[],
): D | null {
  return itens.find((d) => d.severity === "attention" && (d.source ?? "").includes("daily_performance")) ?? null;
}
