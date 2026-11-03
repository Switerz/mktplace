/**
 * EXPEDICAO-TRAVADOS-1 — a parte pura do popup de travados (testavel sem o
 * dialogo, que depende do roteador do Next).
 */
import { formatarDecimal } from "./expedicao-formato.ts";
import {
  FILTROS_PADRAO,
  LIMIT_MAX,
  construirQuery,
  numeroDoPedido,
  rotuloDeadline,
  rotuloDoNumero,
  type Canal,
  type LinhaFila,
  type Ordenacao,
  type Situacao,
} from "./expedicao-contract.ts";
import { brandLabel } from "./filters/brands.ts";
import { buildProdutosCsvFile, type ExportRecord } from "./produtos-export.ts";

/**
 * Etapa do pedido (T-0015) — o MESMO dominio e rotulo da API
 * (`ETAPAS_FILA` em `expedicao_service.py`), na ordem do fluxo.
 */
export const ETAPAS_DO_PEDIDO: readonly (readonly [string, string])[] = [
  ["a_faturar", "A faturar"],
  ["a_imprimir", "A imprimir"],
  ["em_separacao", "Em separação"],
  ["a_preparar", "A preparar"],
  ["aguardando_coleta", "Aguardando coleta"],
  ["entregue_transportadora", "Entregue à transportadora"],
  ["sinalizado_ml", "Sinalizado pelo Mercado Livre"],
  ["outro", "Outro status da fonte"],
  ["nao_publicada", "Etapa ainda não publicada"],
];
const ROTULO_ETAPA = new Map(ETAPAS_DO_PEDIDO);
const ORDEM_ETAPA = new Map(ETAPAS_DO_PEDIDO.map(([k], i) => [k, i]));

/** Rotulo da etapa; valor desconhecido aparece cru (nunca some). */
export function rotuloDaEtapa(etapa: string | null | undefined): string {
  if (!etapa) return "—";
  return ROTULO_ETAPA.get(etapa) ?? etapa;
}

/** A etapa REAL foi publicada para alguma linha (nao so' o default da 033). */
export function temEtapaPublicada(linhas: Pick<LinhaFila, "etapa">[]): boolean {
  return linhas.some((l) => l.etapa && l.etapa !== "nao_publicada");
}

/** Contagem por etapa das linhas, na ordem do fluxo: "onde estao travados". */
export function travadosPorEtapa(linhas: Pick<LinhaFila, "etapa">[]): { etapa: string; rotulo: string; n: number }[] {
  const n = new Map<string, number>();
  for (const l of linhas) {
    const k = l.etapa || "nao_publicada";
    n.set(k, (n.get(k) ?? 0) + 1);
  }
  return [...n.entries()]
    .sort((a, b) => (ORDEM_ETAPA.get(a[0]) ?? 99) - (ORDEM_ETAPA.get(b[0]) ?? 99))
    .map(([etapa, qtd]) => ({ etapa, rotulo: rotuloDaEtapa(etapa), n: qtd }));
}

/** Mais que isso vira "mostrando N de M": o popup e' para olhar, nao exportar. */
export const LIMITE_TRAVADOS = 200;

/**
 * EXPEDICAO-VENCE-AMANHA — o mesmo popup lista os pedidos de cada cartao do
 * "Risco atual". `travados` e' o de sempre; os outros sao recortes da MESMA
 * rota da fila por outra situacao.
 */
export type TipoLista =
  | "travados"
  | "vencidos"
  | "perto_de_vencer"
  | "vence_24h"
  | "vence_proximo"
  | "acima_48h";

export interface ConfigLista {
  situacao: Situacao;
  ordem: Ordenacao;
  limite: number;
  /** "travados de 722 na fila", "vencem no próximo dia de prazo de 722 na fila". */
  nome: string;
  vazio: string;
  /** Prefixo do arquivo CSV. */
  arquivo: string;
  /** Cor do numero: vencido e' prazo do marketplace; o resto, atencao. */
  tom: "critico" | "atencao";
  /** Como a lista vem ordenada, em lingua de gestor ("mais antigos"). */
  ordemTexto?: string;
}

export const LISTAS: Record<TipoLista, ConfigLista> = {
  travados: {
    situacao: "stalled", ordem: "oldest", limite: LIMITE_TRAVADOS, nome: "travados",
    vazio: "Nenhum pedido travado nesta fotografia.", arquivo: "travados", tom: "atencao",
  },
  // Mais atrasado primeiro: o prazo mais antigo e' o que mais doi.
  vencidos: {
    situacao: "overdue", ordem: "deadline", limite: LIMIT_MAX, nome: "vencidos",
    vazio: "Nenhum pedido vencido nesta fotografia.", arquivo: "vencidos", tom: "critico",
    ordemTexto: "mais atrasados",
  },
  // EXPEDICAO-PERTO-DE-VENCER: hoje primeiro, depois o proximo dia de prazo.
  perto_de_vencer: {
    situacao: "due_soon", ordem: "deadline", limite: LIMIT_MAX, nome: "perto de vencer",
    vazio: "Nenhum pedido vence hoje nem no próximo dia de prazo.", arquivo: "perto-de-vencer",
    tom: "atencao", ordemTexto: "mais urgentes",
  },
  // Mesmo prazo para todos (23:59): pago ha' mais tempo vem antes.
  vence_24h: {
    situacao: "due_within_24h", ordem: "oldest", limite: LIMIT_MAX, nome: "vencem em 24 h",
    vazio: "Nenhum pedido vence nas próximas 24 h.", arquivo: "vence-24h", tom: "atencao",
  },
  // A lista de amanha e' para agir: cabe o dia inteiro (limite da API).
  vence_proximo: {
    situacao: "due_next_day", ordem: "oldest", limite: LIMIT_MAX, nome: "vencem no próximo dia de prazo",
    vazio: "Nenhum pedido vence no próximo dia de prazo.", arquivo: "vence-proximo-dia", tom: "atencao",
  },
  acima_48h: {
    situacao: "over_48h", ordem: "oldest", limite: LIMIT_MAX, nome: "acima de 48 h",
    vazio: "Nenhum pedido aberto há mais de 48 h.", arquivo: "acima-48h", tom: "atencao",
  },
};

export interface AlvoTravados {
  /** Titulo curto: "Travados · ÁPICE". */
  titulo: string;
  marca: string | null;
  conta: string | null;
  /** Quantos o cartao mostra (travados, vencidos...), ate' a lista chegar. */
  travados: number;
  backlog: number;
  /**
   * A lista vem da fila PUBLICADA. Com a divisao vendedor x FBS na tela, o
   * cartao e' so' do vendedor: o popup diz que a lista inclui FBS.
   */
  incluiFbs?: boolean;
  /** Qual lista abrir; sem ela, travados. */
  lista?: TipoLista;
}

/** Por que o pipeline marcou o pedido como travado, em linguagem de gestor. */
export function motivoDoTravado(l: Pick<LinhaFila, "is_slow_vs_baseline" | "is_source_zombie">): string {
  if (l.is_source_zombie) return "Aberto há mais de 30 dias";
  if (l.is_slow_vs_baseline) return "Mais lento que o normal da conta";
  return "Travado";
}

/** "6,7% da fila" — so' com base positiva; sem base, nada. */
export function percentualDaFila(parte: number, base: number): string | null {
  if (!(base > 0)) return null;
  return `${formatarDecimal((parte / base) * 100, 1)}%`;
}

export function queryDaLista(tipo: TipoLista, canal: Canal, marca: string | null, conta: string | null): string {
  const l = LISTAS[tipo];
  return construirQuery({
    ...FILTROS_PADRAO,
    channel: canal,
    brands: marca ? [marca] : [],
    accounts: conta ? [conta] : [],
    situacao: [l.situacao],
    orderBy: l.ordem,
    limit: l.limite,
  });
}

export function queryDosTravados(canal: Canal, marca: string | null, conta: string | null): string {
  return queryDaLista("travados", canal, marca, conta);
}

// ---------------------------------------------------------------------------
// EXPEDICAO-NUMERO-PEDIDO — exportar a lista do popup em CSV
// ---------------------------------------------------------------------------
const ROTULO_CANAL: Record<Canal, string> = { shopee: "Shopee", mercadolivre: "Mercado Livre" };

/**
 * Colunas do CSV, na ordem do arquivo: so' o que o popup ja' mostra, mais a
 * transportadora. A primeira e' "Pedido" na Shopee e "Envio" no ML
 * (`rotuloDoNumero`): no ML a fila e' por envio, nao por venda.
 */
export function colunasDoCsvTravados(canal: Canal): string[] {
  return [
    rotuloDoNumero(canal), "Canal", "Marca", "Etapa", "Dias em aberto", "Por que travou",
    "Prazo de envio", "Situação do prazo", "Transportadora",
  ];
}

/** Data e hora de Brasilia ("dd/mm/aaaa hh:mm"), ou vazio. */
function dataBrt(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: "America/Sao_Paulo", day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  }).format(d).replace(",", "");
}

/** "03/11 23:59" em Brasilia, para a coluna Prazo do popup; sem data, nulo. */
export function prazoCurtoBrt(iso: string | null): string | null {
  const t = dataBrt(iso);
  return t ? `${t.slice(0, 5)} ${t.slice(-5)}` : null;
}

export function linhaDoCsvTravados(l: LinhaFila, canal: Canal): ExportRecord {
  return {
    [rotuloDoNumero(canal)]: numeroDoPedido(l),
    "Canal": ROTULO_CANAL[canal],
    "Marca": brandLabel(l.brand),
    "Etapa": l.etapa && l.etapa !== "nao_publicada" ? rotuloDaEtapa(l.etapa) : null,
    // Texto pt-BR ("53,3"): o Excel em portugues le como numero.
    "Dias em aberto": l.hours_open === null ? null : formatarDecimal(l.hours_open / 24, 1),
    "Por que travou": motivoDoTravado(l),
    "Prazo de envio": dataBrt(l.dispatch_deadline),
    "Situação do prazo": rotuloDeadline(l.deadline_status),
    "Transportadora": l.carrier,
  };
}

/** Conteudo do arquivo (com BOM, `;`), pelo MESMO montador do CSV de Produtos. */
export function csvDosTravados(linhas: LinhaFila[], canal: Canal): string {
  return buildProdutosCsvFile(colunasDoCsvTravados(canal), linhas.map((l) => linhaDoCsvTravados(l, canal)));
}

/** As listas de prazo e idade nao tem motivo de travamento: a coluna sai. */
export function csvDaLista(linhas: LinhaFila[], canal: Canal, tipo: TipoLista): string {
  if (tipo === "travados") return csvDosTravados(linhas, canal);
  const colunas = colunasDoCsvTravados(canal).filter((c) => c !== "Por que travou");
  return buildProdutosCsvFile(colunas, linhas.map((l) => linhaDoCsvTravados(l, canal)));
}

/** `vencidos-shopee-barbours-2026-10-30.csv`; sem marca, `todas`. */
export function nomeDoCsv(tipo: TipoLista, canal: Canal, marca: string | null, hoje: Date): string {
  const dia = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Sao_Paulo" }).format(hoje);
  const slug = (marca ?? "todas").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return `${LISTAS[tipo].arquivo}-${canal}-${slug || "todas"}-${dia}.csv`;
}

/** `travados-shopee-barbours-2026-10-30.csv`; sem marca, `todas`. */
export function nomeDoCsvTravados(canal: Canal, marca: string | null, hoje: Date): string {
  return nomeDoCsv("travados", canal, marca, hoje);
}

