/**
 * DRE-UX-1 — contrato da tela `/dre`.
 *
 * Logica PURA, sem React e sem DOM, pelo mesmo motivo do contrato da
 * Expedicao e do tema: toda decisao que a tela toma (que filtro vai para a
 * API, qual share cada marca tem, quem entra no ranking, o que uma linha
 * ausente mostra) precisa ser testavel com `node --test`. Os componentes so'
 * pintam o que estas funcoes devolvem.
 *
 * A TELA NAO RECALCULA A DRE
 * --------------------------
 * Classificacao, completude, margem e percentuais chegam prontos de
 * `GET /api/v1/dre` (contrato: `docs/dre_contrato_0.md`, `docs/dre_mvp_1.md`).
 * O unico numero DERIVADO aqui e' o share de receita, que a API nao entrega:
 * receita do recorte / receita total da selecao, com os valores crus.
 *
 * AUSENCIA NUNCA VIRA ZERO
 * ------------------------
 * `valor === null` tem sempre um `status` que diz por que. A tela mostra o
 * status e o motivo — nunca "R$ 0" nem um travessao ambiguo. Zero so' aparece
 * quando a fonte mediu zero.
 *
 * NOME DO RESULTADO
 * -----------------
 * "Margem de contribuicao conhecida". Nada nesta tela se chama lucro nem
 * margem liquida: impostos e devolucoes nao tem fonte ratificada.
 */
import {
  ALL_MARKETPLACES,
  canonicalizeSelection,
  parseMarketplaceParam,
  serializeMarketplaceSelection,
} from "./marketplace-filter.ts";
import { ALL_BRAND_KEYS } from "./filters/brands.ts";
import type { Marketplace } from "./mock-data";
import type { Tom } from "../components/ui/tone";

// ---------------------------------------------------------------------------
// Tipos — espelham `apps/api/app/schemas/dre.py`
// ---------------------------------------------------------------------------

export type Classificacao = "COMPLETA" | "PARCIAL" | "NAO_CALCULAVEL";

export type StatusComponente =
  | "disponivel"
  | "parcial"
  | "indisponivel"
  | "snapshot_ausente"
  | "snapshot_vencido"
  | "nao_reconciliado"
  | "nao_confirmado"
  | "provisorio"
  | "sinal_inesperado"
  | "nao_aplicavel";

export const NOMES_COMPONENTE = [
  "receita",
  "devolucoes",
  "taxas",
  "afiliados",
  "ads",
  "frete",
  "cmv",
  "impostos",
] as const;
export type NomeComponente = (typeof NOMES_COMPONENTE)[number];

export interface Componente {
  valor: number | null;
  pct_receita: number | null;
  status: StatusComponente;
  motivo: string | null;
  fonte: string | null;
  somavel: boolean;
  partes?: Record<string, number | null> | null;
  data_as_of?: string | null;
  publicado_em?: string | null;
}

export interface Margem {
  valor: number | null;
  pct_receita: number | null;
  rotulo: string;
  formula: string | null;
  componentes_usados: string[];
  faltam: string[];
}

export interface CelulaDRE {
  escopo?: string | null;
  mes?: string | null;
  canal?: string | null;
  canal_label?: string | null;
  marca?: string | null;
  marca_label?: string | null;
  celulas?: number | null;
  classificacao: Classificacao;
  motivos_classificacao: string[];
  avisos_qualidade: string[];
  componentes: Record<NomeComponente, Componente>;
  componentes_disponiveis: string[];
  componentes_ausentes: { componente: string; status: StatusComponente; motivo: string | null }[];
  completude: { usados: number; aplicaveis: number; texto: string };
  margem_contribuicao_conhecida: Margem;
  data_maxima: {
    receita_ultimo_dia?: string | null;
    mart_carregado_em?: string | null;
    cmv_ultima_emissao?: string | null;
    repasse_tiktok_atualizado_em?: string | null;
    insumo_publicado_em?: string | null;
  };
}

export type EstadoInsumos = "ok" | "tabela_ausente" | "nao_publicado" | "vencido" | "parcialmente_vencido";

export interface DreResponse {
  periodo: {
    date_from: string;
    date_to: string;
    meses: string[];
    origem: "informado" | "padrao_competencia_madura";
  };
  filtros: Record<string, string[]>;
  fontes: {
    neon: "ok";
    insumos_dre: {
      estado: EstadoInsumos;
      publicado_em: string | null;
      publicado_em_mais_antigo?: string | null;
      publicado_em_mais_recente?: string | null;
      motivo: string | null;
    };
  };
  regras: Record<string, unknown>;
  total: CelulaDRE | null;
  por_canal: CelulaDRE[];
  por_marca: CelulaDRE[];
  por_canal_marca: CelulaDRE[];
  celulas_base: CelulaDRE[];
}

// ---------------------------------------------------------------------------
// Rotulos
// ---------------------------------------------------------------------------

export const ROTULO_MARGEM = "Margem de contribuição conhecida";

export const ROTULO_COMPONENTE: Record<NomeComponente, string> = {
  receita: "Receita",
  devolucoes: "Devoluções",
  taxas: "Taxas do marketplace",
  afiliados: "dos quais: afiliados",
  ads: "Ads",
  frete: "Frete",
  cmv: "CMV",
  impostos: "Impostos",
};

/** Nome curto para listas ("faltam: impostos, devoluções"). */
export const NOME_CURTO: Record<string, string> = {
  receita: "receita",
  devolucoes: "devoluções",
  taxas: "taxas",
  afiliados: "afiliados",
  ads: "Ads",
  frete: "frete",
  cmv: "CMV",
  impostos: "impostos",
};

export const ROTULO_CANAL: Record<string, string> = {
  tiktok: "TikTok Shop",
  ml: "Mercado Livre",
  shopee: "Shopee",
};

/**
 * Chip de status (`docs/dre_mvp_1.md` §6). `disponivel` nao tem chip: o valor
 * fala sozinho. Os demais sempre aparecem, com ou sem valor.
 */
export const STATUS_CHIP: Record<StatusComponente, { rotulo: string; tom: Tom } | null> = {
  disponivel: null,
  parcial: { rotulo: "Parcial", tom: "atencao" },
  nao_confirmado: { rotulo: "Não confirmado", tom: "atencao" },
  provisorio: { rotulo: "Provisório", tom: "atencao" },
  nao_reconciliado: { rotulo: "Não reconcilia", tom: "critico" },
  sinal_inesperado: { rotulo: "Sinal inesperado", tom: "critico" },
  indisponivel: { rotulo: "Indisponível", tom: "ausente" },
  snapshot_ausente: { rotulo: "Insumo não publicado", tom: "ausente" },
  snapshot_vencido: { rotulo: "Insumo desatualizado", tom: "atencao" },
  nao_aplicavel: { rotulo: "N/A", tom: "ausente" },
};

export const CLASSIFICACAO_CHIP: Record<Classificacao, { rotulo: string; tom: Tom }> = {
  COMPLETA: { rotulo: "Completa", tom: "bom" },
  PARCIAL: { rotulo: "Parcial", tom: "atencao" },
  NAO_CALCULAVEL: { rotulo: "Não calculável", tom: "critico" },
};

const ORDEM_CLASSIFICACAO: Record<Classificacao, number> = { NAO_CALCULAVEL: 0, PARCIAL: 1, COMPLETA: 2 };

// ---------------------------------------------------------------------------
// Filtros <-> URL <-> API
// ---------------------------------------------------------------------------

export interface DreFiltros {
  /** Competencia `YYYY-MM`. `null` = a API escolhe a competencia madura. */
  mes: string | null;
  canais: Marketplace[];
  /** brand_key. Vazio = todas as marcas ativas (decisao da API). */
  marcas: string[];
}

export const FILTROS_PADRAO: DreFiltros = { mes: null, canais: [...ALL_MARKETPLACES], marcas: [] };

const RE_MES = /^(\d{4})-(0[1-9]|1[0-2])$/;

export function mesValido(v: string | null | undefined): v is string {
  return typeof v === "string" && RE_MES.test(v);
}

/**
 * URL -> filtros. Nunca lanca: parametro estranho e' ignorado (cai no padrao).
 *
 * So' tres chaves valem aqui: `ref_month`, `channels`, `brands`. `date_from`
 * e `date_to` das outras telas sao ignorados de proposito — o intervalo de 30
 * dias delas nao e' um mes inteiro e faria a API responder 422.
 */
export function parseDreFiltros(params: { get(chave: string): string | null }): DreFiltros {
  const mesRaw = params.get("ref_month");
  const marcasRaw = params.get("brands");
  const marcas = marcasRaw
    ? ALL_BRAND_KEYS.filter((k) => marcasRaw.split(",").map((b) => b.trim()).includes(k))
    : [];
  return {
    mes: mesValido(mesRaw) ? mesRaw : null,
    canais: parseMarketplaceParam(params.get("channels") ?? "all"),
    marcas,
  };
}

/**
 * Filtros -> querystring. A MESMA funcao alimenta a URL da pagina e a
 * requisicao a API: o que o gestor le na barra de endereco e' exatamente o que
 * foi pedido ao servidor. Ordem das chaves e dos valores e' canonica, para a
 * string servir tambem de identidade da requisicao.
 */
export function dreQuery(f: DreFiltros): string {
  const q = new URLSearchParams();
  if (mesValido(f.mes)) q.set("ref_month", f.mes);
  const canais = serializeMarketplaceSelection(canonicalizeSelection(f.canais));
  if (canais !== "all") q.set("channels", canais);
  const marcas = ALL_BRAND_KEYS.filter((k) => f.marcas.includes(k));
  if (marcas.length) q.set("brands", marcas.join(","));
  return q.toString();
}

export const DRE_ENDPOINT = "/api/v1/dre";

export function dreApiUrl(base: string, f: DreFiltros): string {
  const q = dreQuery(f);
  return `${base}${DRE_ENDPOINT}${q ? `?${q}` : ""}`;
}

/**
 * Meses oferecidos no seletor: do mes corrente (BRT) para tras. Mes imaturo
 * continua selecionavel — a API responde `NAO_CALCULAVEL` com o motivo, que e'
 * informacao, nao erro. Um `mes` vindo da URL fora da janela entra na lista
 * para o seletor nao mentir sobre o que esta aplicado.
 */
export function mesesSelecionaveis(hoje: Date, quantidade = 12, extra: string | null = null): string[] {
  const out: string[] = [];
  let ano = hoje.getFullYear();
  let mes = hoje.getMonth() + 1;
  for (let i = 0; i < quantidade; i++) {
    out.push(`${ano}-${String(mes).padStart(2, "0")}`);
    mes -= 1;
    if (mes === 0) {
      mes = 12;
      ano -= 1;
    }
  }
  if (mesValido(extra) && !out.includes(extra)) {
    out.push(extra);
    out.sort().reverse();
  }
  return out;
}

const MESES_CURTOS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

export function rotuloMes(mes: string): string {
  const m = RE_MES.exec(mes);
  if (!m) return mes;
  return `${MESES_CURTOS[Number(m[2]) - 1]}/${m[1]}`;
}

/** Competencia do periodo respondido: "jul/2026" ou "jun/2026 – jul/2026". */
export function rotuloPeriodo(meses: string[]): string {
  if (!meses.length) return "—";
  if (meses.length === 1) return rotuloMes(meses[0]);
  return `${rotuloMes(meses[0])} – ${rotuloMes(meses[meses.length - 1])}`;
}

// ---------------------------------------------------------------------------
// Falhas da requisicao
// ---------------------------------------------------------------------------

export type TipoFalha = "rede" | "invalido" | "indisponivel" | "sem_rota" | "outro";

export interface FalhaDre {
  tipo: TipoFalha;
  status: number | null;
  titulo: string;
  mensagem: string;
}

/**
 * Status HTTP (ou `null` = sem resposta) -> estado de erro da tela.
 *
 * 422 e 503 da DRE carregam mensagem HUMANA do servidor (`HTTPException`
 * com texto em portugues): ela e' mostrada. O 422 de validacao de tipo do
 * FastAPI (lista de objetos) nao e' — vira texto proprio.
 */
export function classificarFalha(status: number | null, corpo: unknown): FalhaDre {
  const detalhe =
    corpo && typeof corpo === "object" && typeof (corpo as { detail?: unknown }).detail === "string"
      ? ((corpo as { detail: string }).detail)
      : null;
  if (status === null) {
    return {
      tipo: "rede",
      status,
      titulo: "Sem conexão com a API",
      mensagem: "Não foi possível contatar a API da Torre. Verifique a conexão e tente novamente.",
    };
  }
  if (status === 422) {
    return {
      tipo: "invalido",
      status,
      titulo: "Filtro inválido para a DRE",
      mensagem: detalhe ?? "A DRE aceita só meses inteiros, canais e marcas conhecidos. Revise os filtros.",
    };
  }
  if (status === 503) {
    return {
      tipo: "indisponivel",
      status,
      titulo: "DRE indisponível no momento",
      mensagem: detalhe ?? "O banco da Torre não respondeu. Tente novamente em instantes.",
    };
  }
  if (status === 404) {
    return {
      tipo: "sem_rota",
      status,
      titulo: "A API publicada ainda não serve a DRE",
      mensagem: "O endpoint /api/v1/dre não existe nesta versão da API — aguardando deploy.",
    };
  }
  return {
    tipo: "outro",
    status,
    titulo: "Não foi possível carregar a DRE",
    mensagem: `A API respondeu com erro ${status}.`,
  };
}

// ---------------------------------------------------------------------------
// Fotografia dos insumos (CMV + repasse TikTok publicados no Neon)
// ---------------------------------------------------------------------------

export interface AvisoFotografia {
  estado: Exclude<EstadoInsumos, "ok">;
  /** Selo do cabecalho, duas palavras. */
  curto: string;
  titulo: string;
  detalhe: string;
  tom: Tom;
}

/**
 * `fontes.insumos_dre` -> faixa no topo, ou `null` com fotografia ok.
 * `publicado_em` e' a publicacao MAIS ANTIGA do conjunto (referencia
 * conservadora do backend) — e' ela que aparece.
 */
export function avisoFotografia(
  ins: DreResponse["fontes"]["insumos_dre"],
  fmtInstante: (iso: string) => string,
): AvisoFotografia | null {
  const quando = ins.publicado_em ? ` Publicação mais antiga: ${fmtInstante(ins.publicado_em)}.` : "";
  switch (ins.estado) {
    case "ok":
      return null;
    case "tabela_ausente":
      return {
        estado: ins.estado,
        curto: "Insumos ausentes",
        titulo: "Insumos da DRE ainda não existem neste banco",
        detalhe:
          "A tabela de insumos não foi criada (migration 025). CMV, taxa e frete do TikTok saem indisponíveis e nenhuma margem é calculada.",
        tom: "atencao",
      };
    case "nao_publicado":
      return {
        estado: ins.estado,
        curto: "Fotografia ausente",
        titulo: "Fotografia ausente para o período",
        detalhe:
          "Nenhum insumo publicado para esta competência. CMV, taxa e frete do TikTok saem indisponíveis; o restante continua visível.",
        tom: "atencao",
      };
    case "vencido":
      return {
        estado: ins.estado,
        curto: "Fotografia vencida",
        titulo: "Fotografia vencida",
        detalhe: `${ins.motivo ?? "Insumos publicados há mais do que o limite."}${quando} Componentes que dependem dela saem como desatualizados.`,
        tom: "atencao",
      };
    case "parcialmente_vencido":
      return {
        estado: ins.estado,
        curto: "Fotografia parcial",
        titulo: "Parte da fotografia está vencida",
        detalhe: `${ins.motivo ?? "Parte dos insumos passou do limite."}${quando}`,
        tom: "atencao",
      };
  }
}

// ---------------------------------------------------------------------------
// DRE vertical
// ---------------------------------------------------------------------------

export interface LinhaDre {
  chave: NomeComponente;
  rotulo: string;
  /** "dos quais: afiliados" — recuada, nunca somada. */
  recuo: boolean;
  /** Entra na conta da margem desta celula (custo somavel e usado). */
  naMargem: boolean;
  /** Custo (exibido com sinal de subtracao) x receita. */
  custo: boolean;
  componente: Componente;
  chip: { rotulo: string; tom: Tom } | null;
  /** Texto para `valor === null`: status + motivo. Nunca "R$ 0". */
  ausencia: string | null;
}

/**
 * Celula -> linhas da DRE vertical, na ordem do contrato.
 *
 * `naMargem` vem de `margem.componentes_usados` (o backend decide o que
 * entrou). Afiliado NUNCA esta na margem: ja' esta dentro de taxas.
 */
export function linhasDre(c: CelulaDRE): LinhaDre[] {
  const usados = new Set(c.margem_contribuicao_conhecida.componentes_usados);
  return NOMES_COMPONENTE.map((nome) => {
    const comp = c.componentes[nome];
    const chip = STATUS_CHIP[comp.status];
    const afiliado = nome === "afiliados";
    return {
      chave: nome,
      rotulo: ROTULO_COMPONENTE[nome],
      recuo: afiliado,
      naMargem: !afiliado && nome !== "receita" && comp.somavel && usados.has(nome),
      custo: nome !== "receita",
      componente: comp,
      chip,
      ausencia:
        comp.valor === null
          ? `${chip?.rotulo ?? "Sem valor"}${comp.motivo ? ` — ${comp.motivo}` : ""}`
          : null,
    };
  });
}

/**
 * Soma de controle: receita − custos na margem. Usada SO' em teste, para
 * provar que a tela nunca soma o afiliado. A margem exibida e' sempre a da
 * API.
 */
export function somaDeControle(c: CelulaDRE): number | null {
  const linhas = linhasDre(c);
  const receita = c.componentes.receita.valor;
  if (receita === null) return null;
  return linhas.filter((l) => l.naMargem).reduce((acc, l) => acc - (l.componente.valor ?? 0), receita);
}

/** Custos ausentes, por nome curto, em ordem do contrato. Afiliado nao e' custo. */
export function custosAusentes(c: CelulaDRE): string[] {
  return c.margem_contribuicao_conhecida.faltam.map((n) => NOME_CURTO[n] ?? n);
}

// ---------------------------------------------------------------------------
// Share de receita
// ---------------------------------------------------------------------------

/**
 * Participacao de cada recorte na receita da SELECAO, em %.
 *
 * Denominador = receita do `total` da mesma resposta, com o valor CRU (nao o
 * texto formatado nem um valor arredondado). Sem receita total (null ou 0),
 * nao ha share — `null`, nunca 0%.
 */
export function shareDaReceita(recorte: CelulaDRE, total: CelulaDRE | null): number | null {
  const denom = total?.componentes.receita.valor ?? null;
  const num = recorte.componentes.receita.valor;
  if (denom === null || denom === 0 || num === null) return null;
  return (num / denom) * 100;
}

// ---------------------------------------------------------------------------
// Comparabilidade e ranking de margem
// ---------------------------------------------------------------------------

export interface Comparabilidade {
  comparavel: boolean;
  motivo: string | null;
}

function chaveDeCustos(c: CelulaDRE): string {
  return [...c.margem_contribuicao_conhecida.componentes_usados].sort().join("+");
}

/**
 * Elegibilidade de UMA celula para entrar num ranking de margem, isolada.
 *
 * Fora do ranking, sempre com motivo:
 *  - `NAO_CALCULAVEL` (o motivo do backend vem junto);
 *  - margem ou % ausentes;
 *  - sem taxas ou sem CMV na conta (custo essencial; contrato §8).
 */
export function elegivelParaRanking(c: CelulaDRE): Comparabilidade {
  const m = c.margem_contribuicao_conhecida;
  if (c.classificacao === "NAO_CALCULAVEL") {
    return { comparavel: false, motivo: c.motivos_classificacao[0] ?? "não calculável" };
  }
  if (m.valor === null || m.pct_receita === null) {
    return { comparavel: false, motivo: "margem não informada pela API" };
  }
  for (const essencial of ["taxas", "cmv"]) {
    if (!m.componentes_usados.includes(essencial)) {
      return { comparavel: false, motivo: `sem ${NOME_CURTO[essencial]} na conta` };
    }
  }
  return { comparavel: true, motivo: null };
}

export interface ItemRanking {
  celula: CelulaDRE;
  comparabilidade: Comparabilidade;
  /** 1 = maior margem % entre os comparaveis. `null` fora do ranking. */
  posicao: number | null;
}

/**
 * Ranking de margem % entre recortes COMPARAVEIS.
 *
 * Duas margens parciais so' se comparam se descontaram os MESMOS custos: uma
 * marca vendida so' no TikTok (sem Ads) pareceria melhor que outra com Ads do
 * ML so' por ter um custo a menos. A referencia e' o conjunto de custos mais
 * frequente entre os elegiveis (empate: o conjunto maior); quem difere sai com
 * "custos diferentes". Nao-comparaveis vao para o fim, na ordem de receita.
 */
export function rankingDeMargem(celulas: CelulaDRE[]): ItemRanking[] {
  const elegiveis = celulas.filter((c) => elegivelParaRanking(c).comparavel);
  const freq = new Map<string, number>();
  for (const c of elegiveis) freq.set(chaveDeCustos(c), (freq.get(chaveDeCustos(c)) ?? 0) + 1);
  const referencia = [...freq.entries()].sort(
    (a, b) => b[1] - a[1] || b[0].split("+").length - a[0].split("+").length || a[0].localeCompare(b[0]),
  )[0]?.[0];

  const itens: ItemRanking[] = celulas.map((celula) => {
    const base = elegivelParaRanking(celula);
    if (base.comparavel && chaveDeCustos(celula) !== referencia) {
      const ref = new Set(referencia?.split("+") ?? []);
      const proprios = new Set(celula.margem_contribuicao_conhecida.componentes_usados);
      const semEste = [...ref].filter((n) => !proprios.has(n)).map((n) => NOME_CURTO[n] ?? n);
      const aMais = [...proprios].filter((n) => !ref.has(n)).map((n) => NOME_CURTO[n] ?? n);
      const partes = [
        semEste.length ? `sem ${semEste.join(", ")}` : null,
        aMais.length ? `com ${aMais.join(", ")}` : null,
      ].filter(Boolean);
      return {
        celula,
        comparabilidade: { comparavel: false, motivo: `custos diferentes dos demais (${partes.join("; ")})` },
        posicao: null,
      };
    }
    return { celula, comparabilidade: base, posicao: null };
  });

  const comparaveis = itens
    .filter((i) => i.comparabilidade.comparavel)
    .sort(
      (a, b) =>
        (b.celula.margem_contribuicao_conhecida.pct_receita ?? 0) -
        (a.celula.margem_contribuicao_conhecida.pct_receita ?? 0),
    );
  comparaveis.forEach((i, idx) => (i.posicao = idx + 1));
  const fora = itens
    .filter((i) => !i.comparabilidade.comparavel)
    .sort((a, b) => (b.celula.componentes.receita.valor ?? -1) - (a.celula.componentes.receita.valor ?? -1));
  return [...comparaveis, ...fora];
}

// ---------------------------------------------------------------------------
// Agregado x celulas
// ---------------------------------------------------------------------------

/**
 * Canais calculaveis escondidos por um agregado NAO_CALCULAVEL.
 *
 * A classificacao do total e' a PIOR entre as celulas: com a Shopee no
 * filtro, o total sai nao calculavel mesmo com ML e TikTok calculaveis. A
 * tela nomeia esses canais e oferece filtrar so' por eles — o agregado deles
 * a API calcula de verdade, sem soma feita aqui.
 */
export function canaisCalculaveisOcultos(resp: DreResponse): Marketplace[] {
  if (!resp.total || resp.total.classificacao !== "NAO_CALCULAVEL") return [];
  const ok = resp.por_canal
    .filter((c) => c.classificacao !== "NAO_CALCULAVEL" && c.canal)
    .map((c) => c.canal as Marketplace);
  if (!ok.length || ok.length === resp.por_canal.length) return [];
  return canonicalizeSelection(ok);
}

export function piorClassificacao(cs: Classificacao[]): Classificacao | null {
  if (!cs.length) return null;
  return cs.reduce((a, b) => (ORDEM_CLASSIFICACAO[b] < ORDEM_CLASSIFICACAO[a] ? b : a));
}

/** Seleção sem nenhuma célula: a API respondeu e não há dado no recorte. */
export function selecaoVazia(resp: DreResponse): boolean {
  return resp.total === null || resp.celulas_base.length === 0;
}

// ---------------------------------------------------------------------------
// Formatacao
// ---------------------------------------------------------------------------

const BRL = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

/** R$ inteiro. So' recebe numero: `null` e' tratado antes, com o status. */
export function fmtReais(v: number): string {
  return BRL.format(v);
}

/** Custo exibido como subtracao: "− R$ 1.234". Zero medido: "R$ 0". */
export function fmtCusto(v: number): string {
  if (v === 0) return fmtReais(0);
  return `− ${fmtReais(Math.abs(v))}`;
}

export function fmtPctDre(v: number | null, casas = 1): string | null {
  if (v === null || !Number.isFinite(v)) return null;
  return `${v.toFixed(casas).replace(".", ",")}%`;
}

/** ISO (data ou instante) -> "13/10/2026" ou "13/10/2026 06:12" em BRT. */
export function fmtInstanteBrt(iso: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [a, m, d] = iso.split("-");
    return `${d}/${m}/${a}`;
  }
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: "America/Sao_Paulo",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(dt).replace(",", "");
}
