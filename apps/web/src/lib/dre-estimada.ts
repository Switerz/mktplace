/**
 * DRE-UX-3 — visao "Estimada com premissas" da tela `/dre`.
 *
 * Consome `cenario_premissas` e `fontes.premissas_dre` do contrato do PR #79
 * (`docs/dre_premissas_1.md`). Logica PURA, como `dre.ts`.
 *
 * A TELA NAO CALCULA O CENARIO
 * ----------------------------
 * Linhas, subtotais, margem estimada, estado e motivos chegam prontos. O unico
 * ajuste aqui e' de EXIBICAO: a taxa da matriz (0..1) vira percentual (x100).
 * Nenhuma premissa e' aplicada, somada ou reaplicada no navegador.
 *
 * DUAS MARGENS, NUNCA UM NUMERO SO'
 * ---------------------------------
 * "Margem de contribuicao conhecida" (realizado) e "Margem de contribuicao
 * estimada com premissas" tem rotulos, campos e componentes proprios. Nenhuma
 * funcao daqui le `margem_contribuicao_conhecida`.
 *
 * AUSENCIA NUNCA VIRA ZERO
 * ------------------------
 * `valor = null` mostra o status e o motivo. CMV nao publicado diz isso por
 * escrito ("CMV da competência ainda não publicado"), nunca "R$ 0".
 */
import type { Tom } from "../components/ui/tone";
import { STATUS_CHIP, fmtPctDre, fmtReais, type CelulaDRE, type DreResponse, type StatusComponente } from "./dre.ts";

// ---------------------------------------------------------------------------
// Tipos — espelham `apps/api/app/schemas/dre.py` (CenarioPremissas)
// ---------------------------------------------------------------------------

export type EstadoCenario = "calculavel" | "parcial" | "nao_calculavel";
export type OrigemLinha = "realizado" | "premissa" | "decomposicao" | "indisponivel" | "calculado" | "misto";

export interface LinhaCenario {
  linha: string;
  rotulo: string;
  /** `item` entra na soma (se `somavel`); `subtotal` e' calculado; `informativo` nunca soma. */
  tipo: "item" | "subtotal" | "informativo";
  valor: number | null;
  pct_receita?: number | null;
  origem: OrigemLinha;
  /** Taxa da matriz (0..1). So' na celula base; `null` em agregados. */
  percentual?: number | null;
  status: string;
  motivo?: string | null;
  somavel: boolean;
}

export interface FontePremissas {
  rotulo: string;
  competencia: string;
  data_fonte?: string | null;
  source_sha256?: string | null;
  carregado_em?: string | null;
}

export interface CenarioPremissas {
  competencia_premissas: string;
  natureza: "estimada";
  rotulo: string;
  estado: EstadoCenario;
  previa: boolean;
  competencia_madura: boolean;
  receita_base: number | null;
  fonte_cmv: "dre_realizada" | "cmp_sku";
  linhas: LinhaCenario[];
  componentes_ausentes: { linha: string; motivo: string | null }[];
  motivos: string[];
  avisos: string[];
  margem_contribuicao_estimada: number | null;
  margem_contribuicao_estimada_pct: number | null;
  fonte_premissas: FontePremissas;
  celulas: number;
}

export interface ResumoPremissas {
  estado: "ok" | "parcial" | "tabela_ausente" | "nao_publicado" | "lote_invalido" | "nao_consultado";
  competencias_publicadas: string[];
  source_sha256?: string | null;
  rotulo?: string | null;
  data_fonte?: string | null;
  carregado_em?: string | null;
  motivo?: string | null;
}

// ---------------------------------------------------------------------------
// Rotulos
// ---------------------------------------------------------------------------

export const ROTULO_MARGEM_ESTIMADA = "Margem de contribuição estimada com premissas";
export const TEXTO_CMV_NAO_PUBLICADO = "CMV da competência ainda não publicado";

export const ESTADO_CENARIO_CHIP: Record<EstadoCenario, { rotulo: string; tom: Tom }> = {
  calculavel: { rotulo: "Calculável", tom: "bom" },
  parcial: { rotulo: "Parcial", tom: "atencao" },
  nao_calculavel: { rotulo: "Não calculável", tom: "critico" },
};

export const ORIGEM_ROTULO: Record<OrigemLinha, string> = {
  realizado: "Realizado",
  premissa: "Premissa",
  decomposicao: "Decomposição",
  indisponivel: "Indisponível",
  calculado: "Calculado",
  misto: "Misto",
};

/** Status proprios das linhas do cenario. Os da DRE realizada vem de `STATUS_CHIP`. */
const STATUS_CENARIO: Record<string, { rotulo: string; tom: Tom } | null> = {
  ok: null,
  ausente_na_origem: { rotulo: "Ausente na matriz", tom: "ausente" },
  sinal_nao_ratificado: { rotulo: "Sinal não ratificado", tom: "atencao" },
  sobreposicao_nao_ratificada: { rotulo: "Sobreposição não ratificada", tom: "atencao" },
  nao_calculavel: { rotulo: "Não calculável", tom: "ausente" },
};

/** Selo do status de uma linha. `null` = status que dispensa selo (ok/disponivel). */
export function chipDaLinha(status: string): { rotulo: string; tom: Tom } | null {
  if (status in STATUS_CENARIO) return STATUS_CENARIO[status];
  if (status in STATUS_CHIP) return STATUS_CHIP[status as StatusComponente];
  // Status desconhecido: mostrado por extenso, nunca escondido.
  return { rotulo: status, tom: "neutro" };
}

/** As 18 premissas da matriz (contrato §2), na ordem da DRE estimada. */
export const COMPONENTES_PREMISSA = [
  "devolucoes_reembolsos", "icms", "difal", "pis", "cofins", "iss_ipi_simples",
  "frete_primario", "bonificacoes", "embalagens", "provisao_perda_estoque",
  "publicidade_propaganda", "comissoes", "agencia_growth", "despesas_gateway",
  "brindes", "logistica_fretes", "logistica_operador", "outras_receitas_operacionais",
] as const;

/** Itens do subtotal "Impostos sobre Receita": recuados na tabela. So' exibicao. */
const GRUPO_IMPOSTOS = new Set(["icms", "difal", "pis", "cofins", "iss_ipi_simples"]);

// ---------------------------------------------------------------------------
// Disponibilidade do cenario
// ---------------------------------------------------------------------------

export type Disponibilidade =
  | { tipo: "api_antiga" }
  | { tipo: "sem_premissa"; motivo: string }
  | { tipo: "ok"; cenario: CenarioPremissas };

/**
 * Tres situacoes DIFERENTES, que a tela nao pode confundir:
 *  - `api_antiga`: a API publicada nao conhece o contrato (sem
 *    `fontes.premissas_dre` nem `cenario_premissas`);
 *  - `sem_premissa`: o contrato existe, mas a competencia/recorte nao tem
 *    premissa publicada (`cenario_premissas = null`) — o motivo vem de
 *    `fontes.premissas_dre.motivo`;
 *  - `ok`: ha cenario (que pode ainda ser `nao_calculavel`).
 */
export function disponibilidade(c: CelulaDRE | null | undefined, resp: DreResponse): Disponibilidade {
  const resumo = resp.fontes.premissas_dre;
  if (resumo === undefined && (c?.cenario_premissas === undefined)) return { tipo: "api_antiga" };
  if (!c || !c.cenario_premissas) {
    return { tipo: "sem_premissa", motivo: resumo?.motivo ?? "nenhuma premissa publicada para esta competência" };
  }
  return { tipo: "ok", cenario: c.cenario_premissas };
}

// ---------------------------------------------------------------------------
// Linhas da DRE estimada
// ---------------------------------------------------------------------------

export interface LinhaEstimadaExibida {
  l: LinhaCenario;
  /** Item subtraido da margem estimada (somavel, com valor). */
  naSoma: boolean;
  /** Tem valor, mas `somavel=false`: exibido e NUNCA somado (Comissoes, Outras Receitas, decomposicao insegura). */
  foraDaSoma: boolean;
  subtotal: boolean;
  recuo: boolean;
  chip: { rotulo: string; tom: Tom } | null;
  /** Texto para `valor = null`. Nunca "R$ 0". */
  ausencia: string | null;
}

function exibir(l: LinhaCenario): LinhaEstimadaExibida {
  const chip = chipDaLinha(l.status);
  let ausencia: string | null = null;
  if (l.valor === null) {
    ausencia =
      l.linha === "cmv" && l.status === "snapshot_ausente"
        ? TEXTO_CMV_NAO_PUBLICADO
        : `${chip?.rotulo ?? "Sem valor"}${l.motivo ? ` — ${l.motivo}` : ""}`;
  }
  return {
    l,
    naSoma: l.tipo === "item" && l.somavel && l.valor !== null,
    foraDaSoma: l.tipo !== "subtotal" && !l.somavel && l.valor !== null && l.linha !== "receita_bruta",
    subtotal: l.tipo === "subtotal",
    recuo: GRUPO_IMPOSTOS.has(l.linha),
    chip,
    ausencia,
  };
}

/**
 * `principais`: a DRE estimada (itens e subtotais, na ordem da API).
 * `informativas`: linhas `tipo = informativo`, que NUNCA entram em soma.
 * A linha da margem estimada sai das principais: ela e' o rodape da tabela.
 */
export function linhasEstimadas(c: CenarioPremissas): {
  principais: LinhaEstimadaExibida[];
  informativas: LinhaEstimadaExibida[];
  margem: LinhaEstimadaExibida | null;
} {
  const todas = c.linhas.map(exibir);
  return {
    principais: todas.filter((x) => x.l.tipo !== "informativo" && x.l.linha !== "margem_contribuicao_estimada"),
    informativas: todas.filter((x) => x.l.tipo === "informativo"),
    margem: todas.find((x) => x.l.linha === "margem_contribuicao_estimada") ?? null,
  };
}

/** Margem estimada em texto. Sem margem: o status, nunca "R$ 0". */
export function textoMargemEstimada(c: CenarioPremissas): string {
  const v = c.margem_contribuicao_estimada;
  return c.estado === "nao_calculavel" || v === null ? "Não calculável" : fmtReais(v);
}

export function textoMargemEstimadaPct(c: CenarioPremissas): string {
  if (c.estado === "nao_calculavel" || c.margem_contribuicao_estimada === null) return "Não calculável";
  return fmtPctDre(c.margem_contribuicao_estimada_pct) ?? "Não calculável";
}

/** Taxa da matriz (0..1) -> "12,50%". Conversao de exibicao, nao calculo. */
export function fmtTaxaPremissa(taxa: number | null | undefined): string | null {
  if (taxa === null || taxa === undefined || !Number.isFinite(taxa)) return null;
  return fmtPctDre(taxa * 100, 2);
}

/** Valor de uma linha: custo com "−", subtotal/receita sem sinal; fora da soma, sem sinal. */
export function textoValorLinha(x: LinhaEstimadaExibida): string | null {
  const v = x.l.valor;
  if (v === null) return null;
  if (x.subtotal || x.l.linha === "receita_bruta" || x.foraDaSoma || x.l.tipo === "informativo") return fmtReais(v);
  return v === 0 ? fmtReais(0) : `− ${fmtReais(Math.abs(v))}`;
}

// ---------------------------------------------------------------------------
// Premissas por marca
// ---------------------------------------------------------------------------

export interface PremissaDaMarca {
  linha: string;
  rotulo: string;
  /** Taxa 0..1 ou `null` (ausente na matriz — nunca zero). */
  taxa: number | null;
  status: string;
  somavel: boolean;
}

export interface PremissasMarca {
  marca: string;
  marca_label: string;
  premissas: Record<string, PremissaDaMarca>;
}

/**
 * Taxa de cada premissa por marca. Vem das CELULAS BASE: so' nelas a API
 * entrega `percentual` (o agregado soma valores e nao tem taxa). A taxa e' da
 * marca, igual em todos os canais dela; usa-se a primeira celula da marca.
 */
export function premissasPorMarca(resp: DreResponse): {
  marcas: PremissasMarca[];
  componentes: { linha: string; rotulo: string }[];
  fonte: FontePremissas | null;
} {
  const porMarca = new Map<string, PremissasMarca>();
  const rotulos = new Map<string, string>();
  let fonte: FontePremissas | null = null;
  for (const c of resp.celulas_base) {
    const cen = c.cenario_premissas;
    if (!cen || !c.marca || porMarca.has(c.marca)) continue;
    fonte = fonte ?? cen.fonte_premissas;
    const premissas: Record<string, PremissaDaMarca> = {};
    for (const l of cen.linhas) {
      if (!(COMPONENTES_PREMISSA as readonly string[]).includes(l.linha)) continue;
      rotulos.set(l.linha, l.rotulo);
      premissas[l.linha] = {
        linha: l.linha,
        rotulo: l.rotulo,
        taxa: l.percentual ?? null,
        status: l.status,
        somavel: l.somavel,
      };
    }
    porMarca.set(c.marca, { marca: c.marca, marca_label: c.marca_label ?? c.marca.toUpperCase(), premissas });
  }
  const componentes = COMPONENTES_PREMISSA.filter((k) => rotulos.has(k)).map((k) => ({ linha: k, rotulo: rotulos.get(k)! }));
  const marcas = [...porMarca.values()].sort((a, b) => a.marca_label.localeCompare(b.marca_label));
  return { marcas, componentes, fonte };
}

/** "abcdef123456…" — o hash inteiro fica no detalhe, nao na primeira dobra. */
export function hashCurto(sha: string | null | undefined): string | null {
  if (!sha) return null;
  return sha.length > 12 ? `${sha.slice(0, 12)}…` : sha;
}
