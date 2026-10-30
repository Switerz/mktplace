/**
 * METAS-DIARIAS-1 — curva de pesos por dia e meta do dia. Logica PURA.
 *
 * A meta do dia nao e' digitada: sai da meta MENSAL da aba Metas, distribuida
 * pelos pesos do canal, como na planilha "Meta diaria Marketplace — nov/26":
 *
 *   meta do dia = meta do mes x peso do dia / soma dos pesos do mes
 *
 * Peso 1,00 = dia normal medio. A mesma curva vale para todas as marcas de um
 * canal. Mes ou canal SEM curva pesa todos os dias igual — e' a conta linear
 * que a Gerencial ja' usava, entao nada muda fora dos meses com curva.
 *
 * Por enquanto as curvas ficam aqui, fixas no codigo (copiadas da aba Curva);
 * editar pelo app fica para depois.
 */
import type { Marketplace } from "./mock-data.ts";
import { CANAIS, MARCAS } from "./metas.ts";
import { GMV_DO_CANAL, diasNoMes, type CelulaMetaGmv, type GmvPorCanal } from "./gerencial/meta-progress.ts";

type CanalComCurva = "ml" | "shopee";

interface DiaDaCurva {
  evento: string | null;
  ml: number;
  shopee: number;
}

/**
 * Pesos por mes (`AAAA-MM`), um item por dia a partir do dia 1.
 *
 * nov/26: dia normal = fator do dia da semana do canal (ago–out/26); dias de
 * evento = multiplicador sobre um dia normal. ML pelo nov/25 real; Shopee sem
 * historico de Black, pico pelas datas duplas de 2026 (3,5x).
 */
const CURVAS: Record<string, DiaDaCurva[]> = {
  "2026-11": [
    { evento: null, ml: 0.98, shopee: 0.89 },
    { evento: "Finados (feriado)", ml: 1, shopee: 0.88 },
    { evento: null, ml: 1.02, shopee: 1.1 },
    { evento: null, ml: 1.02, shopee: 1.12 },
    { evento: null, ml: 1.04, shopee: 1 },
    { evento: null, ml: 0.94, shopee: 0.91 },
    { evento: null, ml: 0.9, shopee: 0.86 },
    { evento: null, ml: 0.98, shopee: 0.89 },
    { evento: "Esquenta 11/11", ml: 1, shopee: 1.2 },
    { evento: "Véspera 11/11", ml: 1.05, shopee: 1.4 },
    { evento: "11/11", ml: 2.45, shopee: 3.5 },
    { evento: "Pós 11/11", ml: 1.2, shopee: 1.5 },
    { evento: "Pós 11/11", ml: 1, shopee: 1.1 },
    { evento: null, ml: 0.9, shopee: 0.86 },
    { evento: null, ml: 0.98, shopee: 0.89 },
    { evento: null, ml: 1, shopee: 0.88 },
    { evento: null, ml: 1.02, shopee: 1.1 },
    { evento: null, ml: 1.02, shopee: 1.12 },
    { evento: null, ml: 1.04, shopee: 1 },
    { evento: "Consciência Negra (feriado)", ml: 0.94, shopee: 0.91 },
    { evento: null, ml: 0.9, shopee: 0.86 },
    { evento: null, ml: 0.98, shopee: 0.89 },
    { evento: "Semana Black", ml: 1.2, shopee: 1.2 },
    { evento: "Semana Black", ml: 1.5, shopee: 1.5 },
    { evento: "Semana Black", ml: 1.8, shopee: 1.8 },
    { evento: "Véspera Black", ml: 1.75, shopee: 1.8 },
    { evento: "Black Friday", ml: 2.45, shopee: 3.5 },
    { evento: "Fim de semana Black", ml: 1.6, shopee: 1.6 },
    { evento: "Fim de semana Black", ml: 1.55, shopee: 1.55 },
    { evento: "Cyber Monday", ml: 1.45, shopee: 1.45 },
  ],
};

const comCurva = (canal: string): canal is CanalComCurva => canal === "ml" || canal === "shopee";

export function temCurva(mes: string, canal: string): boolean {
  return comCurva(canal) && CURVAS[mes] != null;
}

/** Peso de cada dia do mes (indice 0 = dia 1). Sem curva: tudo 1. */
export function pesosDoMes(mes: string, canal: string): number[] {
  const curva = CURVAS[mes];
  if (!curva || !comCurva(canal)) return Array(diasNoMes(mes)).fill(1);
  return curva.map((d) => d[canal]);
}

const soma = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

/** Fatia do mes que cabe ao dia `dia` (1 = primeiro dia). */
function fracaoDoDia(mes: string, canal: string, dia: number): number {
  const pesos = pesosDoMes(mes, canal);
  return (pesos[dia - 1] ?? 0) / soma(pesos);
}

/** Fatia do mes acumulada ate' o FIM do dia `dia` (0 antes do dia 1, 1 no ultimo). */
export function fracaoAte(mes: string, canal: string, dia: number): number {
  const pesos = pesosDoMes(mes, canal);
  if (dia <= 0) return 0;
  if (dia >= pesos.length) return 1;
  return soma(pesos.slice(0, dia)) / soma(pesos);
}

export function metaDoDia(metaMes: number, mes: string, canal: string, dia: number): number {
  return metaMes * fracaoDoDia(mes, canal, dia);
}

/** Nome do evento do dia (`AAAA-MM-DD`) na curva do mes, ou `null`. */
export function eventoDoDia(dia: string): string | null {
  return CURVAS[dia.slice(0, 7)]?.[Number(dia.slice(8, 10)) - 1]?.evento ?? null;
}

// ---------------------------------------------------------------------------
// Escopo filtrado (Gerencial)
// ---------------------------------------------------------------------------

/** Algum canal do escopo tem curva no mes (senao a conta e' a linear de sempre). */
export function escopoTemCurva(celulasEscopo: CelulaMetaGmv[], mes: string): boolean {
  return celulasEscopo.some((c) => temCurva(mes, c.channel));
}

/**
 * Fatia da meta do escopo esperada ate' o fim do dia `dia`: a curva de cada
 * canal ponderada pela meta da celula. Sem meta nenhuma, linear.
 */
export function fracaoDoEscopo(celulasEscopo: CelulaMetaGmv[], mes: string): (dia: number) => number {
  const total = soma(celulasEscopo.map((c) => c.meta_gmv));
  if (total <= 0) {
    const n = diasNoMes(mes);
    return (dia) => Math.min(Math.max(dia, 0), n) / n;
  }
  return (dia) => soma(celulasEscopo.map((c) => c.meta_gmv * fracaoAte(mes, c.channel, dia))) / total;
}

export type StatusDia = "em_andamento" | "atingida" | "nao_atingida";

export interface MetaDoDiaDaMarca {
  meta: number;
  realizado: number;
  atingimento: number;
  status: StatusDia;
  canais: Marketplace[];
}

export interface ProgressoDia {
  dia: string;
  evento: string | null;
  /** Hoje: o dia ainda nao fechou, entao nao ha "atingida" nem "nao atingida". */
  emAndamento: boolean;
  /** Meta do dia de TODO o escopo. */
  metaDoDia: number;
  /** Meta do dia so' das celulas que ja' tem venda — a base da comparacao. */
  metaComDado: number;
  realizado: number;
  /** realizado / metaComDado; `null` quando nenhuma celula tem venda ainda. */
  atingimento: number | null;
  falta: number;
  /** Celulas fora da conta: a venda delas ainda nao chegou. */
  semDado: CelulaMetaGmv[];
  status: StatusDia;
  porMarca: Record<string, MetaDoDiaDaMarca>;
}

/**
 * Progresso contra a meta do dia (periodo de um dia so', como "Hoje").
 *
 * O dado de cada canal chega em hora diferente: hoje a Shopee da Kokeshi entra
 * durante o dia e o resto so' no dia seguinte. Comparar a meta cheia com o que
 * ainda nao chegou daria um atingimento falso, entao:
 *  - dia em andamento: celula sem venda fica FORA da conta (`semDado`);
 *  - dia fechado: se o canal chegou para alguma marca, marca sem venda conta
 *    zero; se o canal nao chegou para ninguem, as celulas dele ficam fora.
 */
export function progressoDoDia(args: {
  celulas: CelulaMetaGmv[];
  linhas: GmvPorCanal[];
  dia: string;
  hoje: string;
}): ProgressoDia | null {
  const { celulas, linhas, dia, hoje } = args;
  if (celulas.length === 0) return null;
  const mes = dia.slice(0, 7);
  const d = Number(dia.slice(8, 10));
  const emAndamento = dia >= hoje;
  const statusDe = (realizado: number, meta: number): StatusDia =>
    emAndamento ? "em_andamento" : realizado >= meta ? "atingida" : "nao_atingida";
  const chegou = (canal: Marketplace) => linhas.some((l) => l[GMV_DO_CANAL[canal]] != null);

  let metaTotal = 0;
  let metaComDado = 0;
  let realizado = 0;
  const semDado: CelulaMetaGmv[] = [];
  const marcas: Record<string, { meta: number; realizado: number; canais: Marketplace[] }> = {};
  for (const c of celulas) {
    const canal = c.channel as Marketplace;
    const meta = metaDoDia(c.meta_gmv, mes, canal, d);
    metaTotal += meta;
    let valor = linhas.find((l) => l.brand === c.brand)?.[GMV_DO_CANAL[canal]] ?? null;
    if (valor == null && !emAndamento && chegou(canal)) valor = 0;
    if (valor == null) {
      semDado.push(c);
      continue;
    }
    metaComDado += meta;
    realizado += valor;
    const m = (marcas[c.brand] ??= { meta: 0, realizado: 0, canais: [] });
    m.meta += meta;
    m.realizado += valor;
    m.canais.push(canal);
  }

  const porMarca: Record<string, MetaDoDiaDaMarca> = {};
  for (const [brand, m] of Object.entries(marcas)) {
    porMarca[brand] = {
      ...m,
      atingimento: m.meta > 0 ? m.realizado / m.meta : 0,
      status: statusDe(m.realizado, m.meta),
    };
  }
  return {
    dia,
    evento: eventoDoDia(dia),
    emAndamento,
    metaDoDia: metaTotal,
    metaComDado,
    realizado,
    atingimento: metaComDado > 0 ? realizado / metaComDado : null,
    falta: Math.max(metaComDado - realizado, 0),
    semDado,
    status: statusDe(realizado, metaComDado),
    porMarca,
  };
}

const rotuloCanal = (c: string) => CANAIS.find((x) => x.id === c)?.label ?? c;
const rotuloMarca = (b: string) => MARCAS.find((x) => x.id === b)?.label ?? b.toUpperCase();

function juntar(itens: string[]): string {
  return itens.length <= 1 ? (itens[0] ?? "") : `${itens.slice(0, -1).join(", ")} e ${itens[itens.length - 1]}`;
}

/**
 * O que ficou fora da conta do dia, em linhas curtas: o canal pelo nome quando
 * nenhuma marca dele chegou ("Mercado Livre"), senao as marcas que faltam
 * ("ÁPICE e LESCENT na Shopee").
 */
export function resumoSemDado(semDado: CelulaMetaGmv[], celulasEscopo: CelulaMetaGmv[]): string[] {
  const linhas: string[] = [];
  for (const canal of CANAIS.map((c) => c.id)) {
    const faltam = semDado.filter((c) => c.channel === canal).map((c) => c.brand);
    if (!faltam.length) continue;
    const doCanal = celulasEscopo.filter((c) => c.channel === canal).length;
    if (faltam.length === doCanal && doCanal > 1) {
      linhas.push(rotuloCanal(canal));
    } else {
      const ordenadas = [...faltam].sort((a, b) => ordemMarca(a) - ordemMarca(b)).map(rotuloMarca);
      linhas.push(`${juntar(ordenadas)} ${canal === "shopee" ? "na" : "no"} ${rotuloCanal(canal)}`);
    }
  }
  return linhas;
}

// ---------------------------------------------------------------------------
// Dia da semana
// ---------------------------------------------------------------------------

const DIAS_SEMANA = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];

export function diaDaSemana(dia: string): string {
  const [a, m, d] = dia.split("-").map(Number);
  return DIAS_SEMANA[new Date(Date.UTC(a, m - 1, d)).getUTCDay()];
}

// ---------------------------------------------------------------------------
// Tabela "Meta por dia" (aba Metas)
// ---------------------------------------------------------------------------

export interface LinhaMetaPorDia {
  dia: string;
  diaSemana: string;
  evento: string | null;
  /** Meta do dia de cada coluna, na ordem de `colunas`. */
  valores: number[];
  /** Soma por canal, so' dos canais com alguma coluna. */
  porCanal: Partial<Record<string, number>>;
  total: number;
  acumulado: number;
  /** acumulado / meta do mes (0,35 = 35%). */
  pctAcumulado: number;
}

export interface TabelaMetaPorDia {
  colunas: CelulaMetaGmv[];
  linhas: LinhaMetaPorDia[];
  total: number;
}

const ordemCanal = (c: string) => CANAIS.findIndex((x) => x.id === c);
const ordemMarca = (b: string) => MARCAS.findIndex((x) => x.id === b);

/** As metas do mes abertas dia a dia, como a aba "Meta diaria" da planilha. */
export function tabelaMetaPorDia(celulas: CelulaMetaGmv[], mes: string): TabelaMetaPorDia {
  const colunas = celulas
    .filter((c) => c.meta_gmv > 0)
    .sort((a, b) => ordemCanal(a.channel) - ordemCanal(b.channel) || ordemMarca(a.brand) - ordemMarca(b.brand));
  const total = soma(colunas.map((c) => c.meta_gmv));
  const linhas: LinhaMetaPorDia[] = [];
  let acumulado = 0;
  for (let d = 1; d <= diasNoMes(mes); d++) {
    const dia = `${mes}-${String(d).padStart(2, "0")}`;
    const valores = colunas.map((c) => metaDoDia(c.meta_gmv, mes, c.channel, d));
    const porCanal: Partial<Record<string, number>> = {};
    colunas.forEach((c, i) => {
      porCanal[c.channel] = (porCanal[c.channel] ?? 0) + valores[i];
    });
    const totalDia = soma(valores);
    acumulado += totalDia;
    linhas.push({
      dia,
      diaSemana: diaDaSemana(dia),
      evento: eventoDoDia(dia),
      valores,
      porCanal,
      total: totalDia,
      acumulado,
      pctAcumulado: total > 0 ? acumulado / total : 0,
    });
  }
  return { colunas, linhas, total };
}
