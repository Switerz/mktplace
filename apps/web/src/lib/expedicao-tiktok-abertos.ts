/**
 * EXPEDICAO-TIKTOK-ABERTOS — contrato de `/api/v1/expedicao/tiktok/abertos`.
 *
 * O MESMO modelo da fila da Shopee e do Mercado Livre (vencidos, vence hoje,
 * vence no proximo dia util, acima de 48 h, com a lista dos pedidos), para o
 * TikTok Shop. O prazo e' RECONSTRUIDO da politica (despacho 1 dia util,
 * coleta 2 dias uteis), como na LDR do TikTok: todo texto diz isso.
 */

export type ListaAbertos =
  | "overdue"
  | "due_today"
  | "due_next_day"
  | "over_48h"
  | "awaiting_shipment"
  | "awaiting_collection"
  | "all";

export type ContagemAbertos = {
  open: number;
  awaiting_shipment: number;
  awaiting_collection: number;
  on_hold: number;
  overdue: number;
  overdue_shipment: number;
  overdue_collection: number;
  due_today: number;
  due_next_day: number;
  on_time: number;
  over_48h: number;
};

export type MarcaAbertos = ContagemAbertos & { brand: string; oldest_paid_at: string | null };

export type PedidoAberto = {
  brand: string;
  order_id: string;
  shop_name: string | null;
  stage: "despacho" | "coleta" | "retido";
  /** Brasilia, sem fuso. */
  paid_at: string | null;
  /** Dia do prazo (fim do dia), AAAA-MM-DD. */
  deadline: string | null;
  situation: "overdue" | "due_today" | "due_next_day" | "on_time" | "held";
  over_48h: boolean;
  hours_open: number | null;
};

export type RespostaAbertos = {
  channel: "tiktokshop";
  availability: "available" | "unavailable";
  unavailable_reason?: string;
  effective_at: string;
  today: string;
  next_business_day: string;
  cohort_days: number;
  excluded_brands: string[];
  deadline_reconstructed: boolean;
  totals: ContagemAbertos;
  brands: MarcaAbertos[];
  oldest_paid_at?: string | null;
  list: ListaAbertos | null;
  orders: PedidoAberto[];
  /** Paginacao da lista (1-based). */
  page: number;
  page_size: number;
  list_total: number;
  has_more: boolean;
  orders_unavailable?: boolean;
};

export function queryAbertos(marca: string | null, lista?: ListaAbertos, pagina = 1): string {
  const p = new URLSearchParams();
  if (marca) p.set("brands", marca);
  if (lista) p.set("lista", lista);
  if (lista && pagina > 1) p.set("page", String(pagina));
  const q = p.toString();
  return `/api/v1/expedicao/tiktok/abertos${q ? `?${q}` : ""}`;
}

/** "qui 06/11" a partir de AAAA-MM-DD (sem fuso: e' um dia de calendario). */
export function diaCurto(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [a, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!a || !m || !d) return "—";
  const dia = new Date(Date.UTC(a, m - 1, d));
  const semana = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"][dia.getUTCDay()];
  return `${semana} ${String(d).padStart(2, "0")}/${String(m).padStart(2, "0")}`;
}

/** "05/11 09:12" de um carimbo de Brasilia sem fuso. */
export function instanteCurto(iso: string | null | undefined): string {
  if (!iso || iso.length < 16) return "—";
  return `${iso.slice(8, 10)}/${iso.slice(5, 7)} ${iso.slice(11, 16)}`;
}

export const ROTULO_ETAPA: Record<PedidoAberto["stage"], string> = {
  despacho: "Aguardando despacho",
  coleta: "Aguardando coleta",
  retido: "Retido pelo TikTok",
};

export const CONFIG_LISTA: Record<ListaAbertos, { titulo: string; vazio: string }> = {
  overdue: { titulo: "Pedidos vencidos (TikTok)", vazio: "Nenhum pedido vencido agora." },
  due_today: { titulo: "Pedidos que vencem hoje (TikTok)", vazio: "Nenhum pedido vence hoje." },
  due_next_day: {
    titulo: "Pedidos que vencem no próximo dia útil (TikTok)",
    vazio: "Nenhum pedido vence no próximo dia útil.",
  },
  over_48h: { titulo: "Pedidos abertos há mais de 48 h (TikTok)", vazio: "Nenhum pedido acima de 48 h." },
  awaiting_shipment: {
    titulo: "Pedidos aguardando despacho (TikTok)",
    vazio: "Nenhum pedido aguardando despacho.",
  },
  awaiting_collection: {
    titulo: "Pedidos aguardando coleta (TikTok)",
    vazio: "Nenhum pedido aguardando coleta.",
  },
  all: { titulo: "Pedidos em aberto (TikTok)", vazio: "Nenhum pedido em aberto." },
};

function celula(v: string): string {
  return /[",;\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

/** CSV com `;` (Excel pt-BR), mesma ideia do "Exportar CSV" da fila. */
export function csvDosAbertos(pedidos: PedidoAberto[]): string {
  const cab = ["pedido", "marca", "loja", "etapa", "pago_em", "prazo", "horas_aberto", "situacao"];
  const linhas = pedidos.map((p) =>
    [
      p.order_id,
      p.brand,
      p.shop_name ?? "",
      ROTULO_ETAPA[p.stage] ?? p.stage,
      p.paid_at ?? "",
      p.deadline ?? "",
      p.hours_open === null ? "" : String(p.hours_open).replace(".", ","),
      p.situation,
    ]
      .map(celula)
      .join(";"),
  );
  return "﻿" + [cab.join(";"), ...linhas].join("\n");
}

export function nomeDoCsvAbertos(lista: ListaAbertos, marca: string | null, agora: Date): string {
  const dia = agora.toISOString().slice(0, 10);
  return `tiktok-${lista}${marca ? `-${marca}` : ""}-${dia}.csv`;
}

/** Campo do resumo que da' o total de cada lista. */
export const CAMPO_DA_LISTA: Record<ListaAbertos, keyof ContagemAbertos> = {
  overdue: "overdue",
  due_today: "due_today",
  due_next_day: "due_next_day",
  over_48h: "over_48h",
  awaiting_shipment: "awaiting_shipment",
  awaiting_collection: "awaiting_collection",
  all: "open",
};

/**
 * Quebra por etapa EXATA (do resumo), so' onde o resumo a tem. Nas demais
 * listas a etapa aparece na tabela, linha a linha.
 */
export function etapasDaLista(lista: ListaAbertos, t: ContagemAbertos): { rotulo: string; n: number }[] {
  const par =
    lista === "overdue"
      ? [t.overdue_collection, t.overdue_shipment]
      : lista === "all"
        ? [t.awaiting_collection, t.awaiting_shipment]
        : null;
  if (!par) return [];
  return [
    { rotulo: ROTULO_ETAPA.coleta, n: par[0] },
    { rotulo: ROTULO_ETAPA.despacho, n: par[1] },
  ].filter((e) => e.n > 0);
}

/** "201–400 de 3.776" */
export function faixaDaPagina(pagina: number, tamanho: number, naPagina: number, total: number): string {
  const fmt = (n: number) => n.toLocaleString("pt-BR");
  if (naPagina === 0) return `0 de ${fmt(total)}`;
  const ini = (pagina - 1) * tamanho + 1;
  return `${fmt(ini)}–${fmt(ini + naPagina - 1)} de ${fmt(Math.max(total, ini + naPagina - 1))}`;
}

// ---------------------------------------------------------------------------
// EXPEDICAO-TIKTOK-COLETA-PERDIDA — nao coletados no prazo, por dia de prazo
// ---------------------------------------------------------------------------
export type PedidoColetaPerdida = {
  brand: string;
  order_id: string;
  shop_name: string | null;
  status: string | null;
  paid_at: string | null;
  /** Etiqueta pronta (todos os itens com RTS), Brasilia sem fuso. */
  ready_at: string | null;
  /** Coleta (IN_TRANSIT); null = ainda sem coleta. */
  collected_at: string | null;
  hours_late: number | null;
};

export type RespostaColetaPerdida = {
  channel: "tiktokshop";
  availability: "available" | "unavailable";
  unavailable_reason?: string;
  deadline: string;
  paid_from: string | null;
  paid_to: string | null;
  deadline_days: string[];
  brands: { brand: string; missed: number; still_waiting: number }[];
  /** Todas as marcas do dia, sem filtro (opcoes do filtro de marca). */
  all_brands?: string[];
  /** Pedidos por status ATUAL, no recorte de marca (opcoes do filtro de status). */
  status_counts?: Record<string, number>;
  list_total: number;
  still_waiting: number;
  orders: PedidoColetaPerdida[];
  page: number;
  page_size: number;
  has_more: boolean;
};

/**
 * `etiqueta_pronta`: so' os com etiqueta pronta ate' o fim do prazo (contestar
 * a coleta). `todos`: todos os que venceram sem coleta (a leitura da LDR; e' o
 * que o clique na barra da evolucao abre).
 */
export type EscopoColeta = "etiqueta_pronta" | "todos";

/** Status atual do TikTok, na ordem do fluxo, com o nome em portugues. */
export const ROTULO_STATUS_TIKTOK: Record<string, string> = {
  ON_HOLD: "Retido pelo TikTok",
  AWAITING_SHIPMENT: "Aguardando despacho",
  AWAITING_COLLECTION: "Aguardando coleta",
  IN_TRANSIT: "Em trânsito",
  DELIVERED: "Entregue",
  COMPLETED: "Concluído",
  CANCELLED: "Cancelado",
};

export function rotuloStatusTikTok(st: string | null | undefined): string {
  return st ? (ROTULO_STATUS_TIKTOK[st] ?? st) : "—";
}

export function queryColetaPerdida(
  vencimento: string | null,
  marca: string | null,
  pagina = 1,
  status: string | null = null,
  escopo: EscopoColeta = "etiqueta_pronta",
): string {
  const p = new URLSearchParams();
  if (vencimento) p.set("vencimento", vencimento);
  if (marca) p.set("brands", marca);
  if (status) p.set("status", status);
  if (escopo !== "etiqueta_pronta") p.set("escopo", escopo);
  if (pagina > 1) p.set("page", String(pagina));
  const q = p.toString();
  return `/api/v1/expedicao/tiktok/coleta-perdida${q ? `?${q}` : ""}`;
}

export function csvDaColetaPerdida(pedidos: PedidoColetaPerdida[], vencimento: string): string {
  const cab = ["pedido", "marca", "loja", "status_tiktok", "prazo_coleta", "pago_em", "etiqueta_pronta_em", "coletado_em", "situacao", "horas_de_atraso"];
  const linhas = pedidos.map((p) =>
    [
      p.order_id,
      p.brand,
      p.shop_name ?? "",
      rotuloStatusTikTok(p.status),
      vencimento,
      (p.paid_at ?? "").replace("T", " "),
      (p.ready_at ?? "").replace("T", " "),
      (p.collected_at ?? "").replace("T", " "),
      p.collected_at ? "coletado depois do prazo" : "ainda sem coleta",
      p.hours_late === null ? "" : String(p.hours_late).replace(".", ","),
    ]
      .map(celula)
      .join(";"),
  );
  return "﻿" + [cab.join(";"), ...linhas].join("\n");
}

/** Teto de paginas num download (200 x 100 = 20 mil pedidos). */
export const MAX_PAGINAS_CSV = 100;

/**
 * Busca TODAS as paginas de uma lista, uma de cada vez (cada pagina cabe na
 * reserva pelo Metabase). `aoAvancar` recebe a pagina corrente para a tela
 * mostrar o progresso. Levanta se uma pagina falhar: CSV pela metade nunca.
 */
export async function buscarTodasAsPaginas<T>(
  buscar: (pagina: number) => Promise<{ orders: T[]; has_more: boolean }>,
  aoAvancar?: (pagina: number) => void,
): Promise<T[]> {
  const todos: T[] = [];
  for (let pagina = 1; pagina <= MAX_PAGINAS_CSV; pagina++) {
    aoAvancar?.(pagina);
    const r = await buscar(pagina);
    todos.push(...r.orders);
    if (!r.has_more) return todos;
  }
  throw new Error("lista grande demais para um download");
}

/** Ate' quantos dias para tras a lista por pedido alcanca (limite da API). */
export const JANELA_LISTA_DIAS = 30;

/**
 * O dia de prazo cabe na lista por pedido? (dos ultimos `JANELA_LISTA_DIAS`
 * dias ate' hoje, em Brasilia). Fora disso a barra do grafico nao e' clicavel.
 */
export function diaDentroDaJanelaDaLista(dia: string, agora: Date = new Date()): boolean {
  const brt = agora.getTime() - 3 * 3600000;
  const hoje = new Date(brt).toISOString().slice(0, 10);
  const limite = new Date(brt - JANELA_LISTA_DIAS * 86400000).toISOString().slice(0, 10);
  return dia >= limite && dia <= hoje;
}
