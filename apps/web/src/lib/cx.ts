/**
 * Aba CX (menu principal, 04/11/2026) — primeira versao, so' Shopee.
 *
 * `GET /api/v1/cx/shopee?brands=&dias=` junta:
 *   - chat: recorte de atendimento da Saude da Conta (fotografia diaria
 *     oficial, com a meta da Shopee);
 *   - atendimento: cancelamentos com motivo e pedidos em devolucao.
 * Mercado Livre e TikTok entram depois (dependem de permissao nos apps).
 *
 * Funcoes puras testadas em node --test; a pagina so' desenha a visao.
 */
import {
  JANELA_PADRAO,
  JANELAS_DIAS,
  problemaNoPayloadAtendimento,
  type AtendimentoResponse,
  type BlocoMotivos,
  ROTULO_NATUREZA,
  type CanalAtendimento,
  type EstadoBloco,
  type JanelaDias,
  type Motivo,
} from "./performance-contas-atendimento.ts";
import { MOTIVO_TIMEOUT, TIMEOUT_API_MS } from "./performance-contas.ts";
import { valorMetrica, type ContaOficial } from "./expedicao-performance.ts";

export { JANELAS_DIAS, JANELA_PADRAO, type JanelaDias };

type ParamBruto = string | string[] | undefined;

export interface NaoLidas {
  marca: string;
  valor: number | null;
  capturado_em: string | null;
  /** Volume de conversas (get_conversation_list). Ausente/null = coleta ainda sem essa leitura. */
  conversas_7d?: number | null;
  conversas_30d?: number | null;
  aguardando_resposta?: number | null;
  /** true = a leitura bateu o teto de paginas: o numero e' um piso. */
  conversas_truncado?: boolean | null;
}

export interface ChatShopee {
  estado: "dado" | "sem_fonte" | "erro";
  motivo?: string | null;
  fonte: string;
  contas: ContaOficial[];
  sem_foto: { marca: string; motivo: string }[];
  /** Coleta propria (`shopee_cx_coleta`), a cada 2 h. Ausente na API antiga. */
  nao_lidas_estado?: EstadoBloco;
  nao_lidas?: NaoLidas[];
}

export interface DevolucoesShopee {
  estado: EstadoBloco;
  fonte: string;
  motivo_indisponivel?: string | null;
  total?: number | null;
  aceitas?: number | null;
  em_andamento?: number | null;
  canceladas?: number | null;
  /** So' das aceitas. */
  reembolsado?: number | null;
  motivos?: BlocoMotivos | null;
  marcas: {
    marca: string;
    devolucoes: number | null;
    aceitas?: number | null;
    em_andamento?: number | null;
    canceladas?: number | null;
    reembolsado: number | null;
  }[];
}

export interface CxShopeeResponse {
  gerado_em: string;
  janela: AtendimentoResponse["janela"];
  chat: ChatShopee;
  atendimento: CanalAtendimento;
  /** Ausente na API antiga: a pagina mostra "sem fonte". */
  devolucoes?: DevolucoesShopee;
}

// ---------------------------------------------------------------------------
// Filtros (URL e' a fonte de verdade)
// ---------------------------------------------------------------------------
export interface FiltrosCx {
  marca: string | null;
  dias: JanelaDias;
}

function primeiro(v: ParamBruto): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

export function lerFiltrosCx(
  params: Readonly<Record<string, ParamBruto>>,
  marcasPermitidas: readonly string[],
): FiltrosCx {
  const marca = primeiro(params.brands)?.trim().toLowerCase();
  const dias = Number(primeiro(params.dias));
  return {
    marca: marca && marcasPermitidas.includes(marca) ? marca : null,
    dias: (JANELAS_DIAS as readonly number[]).includes(dias) ? (dias as JanelaDias) : JANELA_PADRAO,
  };
}

export function hrefCx(f: FiltrosCx): string {
  const qs = new URLSearchParams();
  if (f.marca) qs.set("brands", f.marca);
  if (f.dias !== JANELA_PADRAO) qs.set("dias", String(f.dias));
  const s = qs.toString();
  return s ? `/cx?${s}` : "/cx";
}

// ---------------------------------------------------------------------------
// Payload e fetch no servidor
// ---------------------------------------------------------------------------
export function problemaNoPayloadCx(json: unknown): string | null {
  if (!json || typeof json !== "object") return "corpo vazio";
  const o = json as Record<string, unknown>;
  const chat = o.chat as Record<string, unknown> | undefined;
  if (!chat || typeof chat !== "object" || !Array.isArray(chat.contas) || !Array.isArray(chat.sem_foto)) {
    return "chat ausente";
  }
  if (!o.atendimento || typeof o.atendimento !== "object") return "atendimento ausente";
  return problemaNoPayloadAtendimento({
    gerado_em: o.gerado_em,
    janela: o.janela,
    canais: [o.atendimento],
  });
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export const ROTA_API_CX_SHOPEE = "/api/v1/cx/shopee";

export function urlCxShopee(base: string, f: FiltrosCx): string {
  const qs = new URLSearchParams();
  if (f.marca) qs.set("brands", f.marca);
  qs.set("dias", String(f.dias));
  return `${base}${ROTA_API_CX_SHOPEE}?${qs.toString()}`;
}

export type ResultadoCx = { ok: true; dados: CxShopeeResponse } | { ok: false; motivo: string };

/** Nunca lanca: qualquer falha volta como `{ ok: false }` e a pagina mostra erro. */
export async function carregarCxShopee(
  f: FiltrosCx,
  fetchImpl: typeof fetch = fetch,
  base: string = API_URL,
  timeoutMs: number = TIMEOUT_API_MS,
): Promise<ResultadoCx> {
  const controle = new AbortController();
  const timer = setTimeout(() => controle.abort(), timeoutMs);
  try {
    let res: Response;
    try {
      res = await fetchImpl(urlCxShopee(base, f), { cache: "no-store", signal: controle.signal });
    } catch {
      if (controle.signal.aborted) return { ok: false, motivo: MOTIVO_TIMEOUT };
      return { ok: false, motivo: "Não foi possível contatar a API." };
    }
    if (!res.ok) return { ok: false, motivo: `A API respondeu ${res.status}.` };
    let json: unknown;
    try {
      json = await res.json();
    } catch {
      if (controle.signal.aborted) return { ok: false, motivo: MOTIVO_TIMEOUT };
      return { ok: false, motivo: "Resposta inválida da API (o corpo não é JSON)." };
    }
    const problema = problemaNoPayloadCx(json);
    if (problema) return { ok: false, motivo: `Resposta inválida da API (${problema}).` };
    return { ok: true, dados: json as CxShopeeResponse };
  } finally {
    clearTimeout(timer);
  }
}

const FMT_INT = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const FMT_PCT = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const FMT_BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

function inteiro(v: number | null | undefined): string {
  return v == null || !Number.isFinite(v) ? "—" : FMT_INT.format(v);
}

function pct(v: number | null | undefined): string {
  return v == null || !Number.isFinite(v) ? "—" : `${FMT_PCT.format(v)}%`;
}

export function reais(v: number | null | undefined): string {
  return v == null || !Number.isFinite(v) ? "—" : FMT_BRL.format(v);
}

/** Soma ignorando ausentes; tudo ausente = null (nunca zero). */
function somar(vals: (number | null | undefined)[]): number | null {
  const ok = vals.filter((v): v is number => v != null && Number.isFinite(v));
  return ok.length ? ok.reduce((a, b) => a + b, 0) : null;
}

// ---------------------------------------------------------------------------
// Bloco 1 — Atendimento (planilha 1.2: nº de chats, em aberto, % respondido,
// tempo de resposta, contact rate). Uma linha por loja Shopee.
// ---------------------------------------------------------------------------
export const COD_RESPONDIDO = "shopee_11";
export const COD_NOTA = "shopee_22";

export interface CelulaMeta {
  valor: string;
  fora: boolean;
  meta: string | null;
}

export interface LinhaAtendimentoCx {
  chave: string;
  marca: string;
  conversas: string;
  aguardando: string;
  naoLidas: string;
  respondido: CelulaMeta | null;
  nota: CelulaMeta | null;
  contactRate: string;
}

export interface VisaoAtendimentoCx {
  estado: "dado" | "sem_fonte" | "erro";
  motivo: string | null;
  rotuloConversas: string;
  totais: { conversas: string; aguardando: string; naoLidas: string; foraDaMeta: string[] };
  linhas: LinhaAtendimentoCx[];
  /** Uma nota por coisa que falta, no rodape (ausencia nunca parece zero). */
  notas: string[];
}

function celulaMeta(conta: ContaOficial | undefined, codigo: string): CelulaMeta | null {
  const m = conta?.metricas.find((x) => x.codigo === codigo);
  if (!m) return null;
  return { valor: valorMetrica(m), fora: m.situacao === "fora", meta: m.rotulo_meta ?? null };
}

export function montarAtendimentoCx(
  dados: Pick<CxShopeeResponse, "chat" | "atendimento" | "janela">,
  rotuloMarca: (m: string) => string,
): VisaoAtendimentoCx {
  const { chat, atendimento } = dados;
  const dias = dados.janela.dias;
  const usa7 = dias === 7;
  const comContactRate = dias === 7 || dias === 30;
  const contas = new Map(chat.contas.map((c) => [c.marca, c]));
  const fotos = chat.nao_lidas_estado === "dado" ? chat.nao_lidas ?? [] : [];
  const conversas = new Map(fotos.map((n) => [n.marca, n]));
  const pedidos = new Map(atendimento.marcas.map((m) => [m.marca, m.pedidos]));
  const marcas = [...contas.keys(), ...[...conversas.keys()].filter((m) => !contas.has(m))];

  const brutas = marcas.map((m) => {
    const n = conversas.get(m);
    const conv = n ? ((usa7 ? n.conversas_7d : n.conversas_30d) ?? null) : null;
    return { m, n, conv, ped: pedidos.get(m) ?? null };
  });
  const linhas: LinhaAtendimentoCx[] = brutas.map(({ m, n, conv, ped }) => ({
    chave: m,
    marca: rotuloMarca(m),
    conversas: conv == null ? "—" : `${n?.conversas_truncado ? "≥ " : ""}${inteiro(conv)}`,
    aguardando: inteiro(n?.aguardando_resposta),
    naoLidas: inteiro(n?.valor),
    respondido: celulaMeta(contas.get(m), COD_RESPONDIDO),
    nota: celulaMeta(contas.get(m), COD_NOTA),
    contactRate: comContactRate && conv != null && ped ? pct((conv / ped) * 100) : "—",
  }));

  const notas: string[] = [];
  if (chat.nao_lidas_estado === "erro") {
    notas.push("Conversas indisponíveis nesta carga.");
  } else if (brutas.every((b) => b.conv == null)) {
    notas.push("Conversas e contact rate: aguardando a coleta com a lista de conversas (shopee_cx_coleta, a cada 2 h).");
  }
  if (!comContactRate) notas.push("Conversas cobrem os últimos 30 dias; contact rate só em 7 e 30 dias.");
  notas.push("Tempo de resposta: a Shopee não manda esse número pela API; ele será calculado pelas mensagens.");
  if (brutas.some((b) => b.n?.conversas_truncado)) {
    notas.push("“≥” = a loja tem mais conversas do que a coleta leu: o número é um piso.");
  }
  notas.push(...chat.sem_foto.map((s) => `${rotuloMarca(s.marca)}: ${s.motivo}`));

  return {
    estado: linhas.length ? "dado" : chat.estado === "erro" ? "erro" : "sem_fonte",
    motivo: linhas.length ? null : (chat.motivo ?? "Nenhuma loja Shopee no filtro."),
    rotuloConversas: usa7 ? "Conversas (7 dias)" : "Conversas (30 dias)",
    totais: {
      conversas: inteiro(somar(brutas.map((b) => b.conv))),
      aguardando: inteiro(somar(brutas.map((b) => b.n?.aguardando_resposta))),
      naoLidas: inteiro(somar(brutas.map((b) => b.n?.valor))),
      foraDaMeta: linhas.filter((l) => l.respondido?.fora).map((l) => l.marca),
    },
    linhas,
    notas,
  };
}

// ---------------------------------------------------------------------------
// Ajuda dos numeros (o "ⓘ" ao lado do rotulo; a tela fica so' com o numero)
// ---------------------------------------------------------------------------
export const AJUDA_CX = {
  conversas:
    "Conversas do chat da Shopee com mensagem no período (7 ou 30 dias, pela última mensagem). Cada comprador conta uma vez, mesmo que mande várias mensagens.",
  aguardando: "Conversas em que a última mensagem é do comprador: ele está esperando a loja responder.",
  naoLidas: "Conversas com mensagem ainda não aberta pela loja, na última leitura do chat (a cada 2 h).",
  contactRate: "Conversas ÷ pedidos do período: de cada 100 pedidos, quantos viraram conversa no chat.",
  cancelamentos:
    "Pedidos criados no período e cancelados, por qualquer autor (comprador, loja, Shopee ou logística). Os pedidos não pagos, que a Shopee cancela sozinha, ficam de fora.",
  devolucoes:
    "Pedidos de devolução ou reembolso abertos pelo comprador no período. Os que o próprio comprador cancelou ficam de fora.",
  status:
    "Classificação oficial da Saúde da Conta da Shopee (Excelente, Bom, Precisa melhorar ou Ruim). Em vermelho, as métricas fora da meta da Shopee.",
} as const;

// ---------------------------------------------------------------------------
// Status por marca (visao com todas as marcas): a nota oficial da Shopee e o
// que esta' fora da meta, para achar a loja com problema sem ler a tabela.
// ---------------------------------------------------------------------------
export type TomStatus = "bom" | "atencao" | "critico" | "neutro" | "ausente";

export interface StatusMarca {
  chave: string;
  marca: string;
  tom: TomStatus;
  /** Nivel oficial ("Bom") ou o motivo de nao haver foto. */
  rotulo: string;
  /** Metricas fora da meta: "Taxa de resposta do chat 45,3% (meta >= 60)". */
  alertas: string[];
  aguardando: string;
  cancelamento: string;
  devolucoes: string;
}

const ORDEM_TOM: Record<TomStatus, number> = { critico: 0, atencao: 1, neutro: 2, bom: 3, ausente: 4 };

export function montarStatusMarcas(
  dados: Pick<CxShopeeResponse, "chat" | "atendimento" | "devolucoes">,
  rotuloMarca: (m: string) => string,
): StatusMarca[] {
  const { chat, atendimento, devolucoes } = dados;
  const contas = new Map(chat.contas.map((c) => [c.marca, c]));
  const semFoto = new Map(chat.sem_foto.map((s) => [s.marca, s.motivo]));
  const fotos = new Map((chat.nao_lidas_estado === "dado" ? chat.nao_lidas ?? [] : []).map((n) => [n.marca, n]));
  const pedidos = new Map(atendimento.marcas.map((m) => [m.marca, m]));
  const dev = new Map((devolucoes?.estado === "dado" ? devolucoes.marcas : []).map((m) => [m.marca, m]));
  const marcas = [...new Set([...pedidos.keys(), ...contas.keys(), ...semFoto.keys()])];

  return marcas
    .map((m): StatusMarca => {
      const conta = contas.get(m);
      const d = dev.get(m);
      const base = {
        chave: m,
        marca: rotuloMarca(m),
        aguardando: inteiro(fotos.get(m)?.aguardando_resposta),
        cancelamento: pct(pedidos.get(m)?.taxa_cancelamento_pct),
        devolucoes: inteiro(d?.devolucoes == null ? null : d.devolucoes - (d.canceladas ?? 0)),
      };
      if (!conta) {
        return { ...base, tom: "ausente", rotulo: "Sem Saúde da Conta", alertas: semFoto.has(m) ? [semFoto.get(m)!] : [] };
      }
      const alertas = conta.metricas
        .filter((x) => x.situacao === "fora")
        .map((x) => `${x.rotulo} ${valorMetrica(x)}${x.rotulo_meta ? ` (${x.rotulo_meta.toLowerCase()})` : ""}`);
      // Tom e' o da propria Shopee (nivel); metrica fora da meta nunca fica verde.
      const tom: TomStatus = alertas.length && conta.tom === "bom" ? "atencao" : conta.tom;
      return { ...base, tom, rotulo: conta.nivel ?? "Sem nível", alertas };
    })
    .sort((a, b) => ORDEM_TOM[a.tom] - ORDEM_TOM[b.tom] || a.marca.localeCompare(b.marca));
}

// ---------------------------------------------------------------------------
// Bloco 2 — Pareto dos problemas (planilha 1.1: motivos + quanto
// reembolsamos, reenviamos e cancelamos). Devolucoes e cancelamentos juntos.
// ---------------------------------------------------------------------------
export const MAX_ITENS_PARETO = 10;

export interface ItemPareto {
  chave: string;
  /** Codigo do motivo na API (abre a lista de pedidos dele). */
  codigo: string;
  rotulo: string;
  tipo: "Devolução" | "Cancelamento";
  natureza: string;
  quantidade: string;
  pct: string;
  acumulado: string;
  /** 0-100, relativo ao maior motivo. */
  largura: number;
  mapeado: boolean;
}

export interface VisaoPareto {
  estado: EstadoBloco;
  motivo: string | null;
  totais: { reembolsado: string; devolucoes: string; cancelamentos: string; reenviados: string };
  itens: ItemPareto[];
  /** Motivos fora do top, somados numa linha. */
  resto: string | null;
  linhas: {
    chave: string;
    marca: string;
    pedidos: string;
    cancelamentos: string;
    devolucoes: string;
    reembolsado: string;
  }[];
  notas: string[];
}

export function montarPareto(
  dados: Pick<CxShopeeResponse, "atendimento" | "devolucoes">,
  rotuloMarca: (m: string) => string,
): VisaoPareto {
  const dev = dados.devolucoes;
  const can = dados.atendimento;
  const blocoDev = dev?.estado === "dado" && dev.motivos?.estado === "dado" ? dev.motivos : null;
  const blocoCan = can.motivos?.estado === "dado" ? can.motivos : null;

  const brutos: (Motivo & { tipo: ItemPareto["tipo"] })[] = [
    ...(blocoDev?.itens ?? []).map((m) => ({ ...m, tipo: "Devolução" as const })),
    ...(blocoCan?.itens ?? []).map((m) => ({ ...m, tipo: "Cancelamento" as const })),
  ]
    .filter((m) => m.quantidade > 0)
    .sort((a, b) => b.quantidade - a.quantidade || a.rotulo.localeCompare(b.rotulo));
  const total = brutos.reduce((a, m) => a + m.quantidade, 0);
  const maior = brutos[0]?.quantidade ?? 0;
  let acum = 0;
  const todos: ItemPareto[] = brutos.map((m) => {
    acum += m.quantidade;
    return {
      chave: `${m.tipo}:${m.codigo}`,
      codigo: m.codigo,
      rotulo: m.rotulo,
      tipo: m.tipo,
      natureza: ROTULO_NATUREZA[m.natureza] ?? m.natureza,
      quantidade: inteiro(m.quantidade),
      pct: pct((m.quantidade / total) * 100),
      acumulado: pct((acum / total) * 100),
      largura: maior ? Math.max(2, Math.round((m.quantidade / maior) * 100)) : 0,
      mapeado: m.mapeado,
    };
  });
  const fora = brutos.slice(MAX_ITENS_PARETO);
  const somaFora = fora.reduce((a, m) => a + m.quantidade, 0);

  const devMarca = new Map((dev?.estado === "dado" ? dev.marcas : []).map((m) => [m.marca, m]));
  const canMarca = new Map(can.marcas.map((m) => [m.marca, m]));
  const marcas = [...canMarca.keys(), ...[...devMarca.keys()].filter((m) => !canMarca.has(m))];
  /** Devolucoes sem as que o proprio comprador cancelou (a mesma base do Pareto). */
  const liquidas = (m: { devolucoes: number | null; canceladas?: number | null } | undefined) =>
    m?.devolucoes == null ? null : m.devolucoes - (m.canceladas ?? 0);

  const notas: string[] = [];
  if (!blocoDev) {
    notas.push(
      dev?.motivo_indisponivel ?? dev?.motivos?.motivo_indisponivel ??
        "Devoluções: aguardando a coleta da Shopee (shopee_cx_coleta).",
    );
  }
  if (!blocoCan && can.motivos?.motivo_indisponivel) notas.push(`Cancelamentos: ${can.motivos.motivo_indisponivel}`);
  for (const b of [blocoDev, blocoCan]) {
    if (b?.excluidos && b.rotulo_excluidos) notas.push(`${inteiro(b.excluidos)} ${b.rotulo_excluidos}.`);
  }
  notas.push("Reenvios: nenhuma API da Shopee marca o pedido reenviado; sem fonte ainda.");

  const erro = dev?.estado === "erro" || can.motivos?.estado === "erro";
  return {
    estado: todos.length ? "dado" : erro ? "erro" : blocoDev || blocoCan ? "vazio_legitimo" : "sem_fonte",
    motivo: todos.length ? null : erro ? "Fonte indisponível nesta carga." : "Nenhum problema registrado no período.",
    totais: {
      reembolsado: dev?.estado === "dado" ? reais(dev.reembolsado) : "—",
      devolucoes: inteiro(blocoDev?.total),
      cancelamentos: inteiro(somar(can.marcas.map((m) => m.cancelamentos))),
      reenviados: "—",
    },
    itens: todos.slice(0, MAX_ITENS_PARETO),
    resto: fora.length
      ? `+ ${fora.length} motivo(s) com ${inteiro(somaFora)} caso(s) (${pct((somaFora / total) * 100)})`
      : null,
    linhas: marcas.map((m) => ({
      chave: m,
      marca: rotuloMarca(m),
      pedidos: inteiro(canMarca.get(m)?.pedidos),
      cancelamentos: inteiro(canMarca.get(m)?.cancelamentos),
      devolucoes: inteiro(liquidas(devMarca.get(m))),
      reembolsado: devMarca.has(m) ? reais(devMarca.get(m)?.reembolsado) : "—",
    })),
    notas,
  };
}

// ---------------------------------------------------------------------------
// Pedidos de um motivo do Pareto (CX-PEDIDOS-MOTIVO, 05/11/2026)
// `GET /api/v1/cx/shopee/pedidos?tipo=&motivo=&brands=&dias=` (ate' 500).
// ---------------------------------------------------------------------------
export type TipoPedidos = "cancelamento" | "devolucao";

export interface PedidoMotivo {
  marca: string;
  pedido: string | null;
  devolucao: string | null;
  dia: string | null;
  situacao: string | null;
  valor: number | null;
}

export interface PedidosMotivoResponse {
  gerado_em: string;
  tipo: TipoPedidos;
  codigo: string;
  rotulo: string | null;
  estado: "dado" | "vazio_legitimo" | "erro";
  motivo_indisponivel: string | null;
  total: number | null;
  truncado: boolean;
  parcial: boolean;
  numero_publico: boolean;
  pedidos: PedidoMotivo[];
}

export const ROTA_API_CX_PEDIDOS = "/api/v1/cx/shopee/pedidos";

export function tipoDoItem(i: Pick<ItemPareto, "tipo">): TipoPedidos {
  return i.tipo === "Devolução" ? "devolucao" : "cancelamento";
}

export function urlPedidosMotivo(base: string, f: FiltrosCx, tipo: TipoPedidos, codigo: string): string {
  const qs = new URLSearchParams({ tipo, motivo: codigo, dias: String(f.dias) });
  if (f.marca) qs.set("brands", f.marca);
  return `${base}${ROTA_API_CX_PEDIDOS}?${qs.toString()}`;
}

export type ResultadoPedidos = { ok: true; dados: PedidosMotivoResponse } | { ok: false; motivo: string };

/** Nunca lanca: falha volta como `{ ok: false }`. */
export async function carregarPedidosMotivo(
  f: FiltrosCx,
  tipo: TipoPedidos,
  codigo: string,
  fetchImpl: typeof fetch = fetch,
  base: string = API_URL,
  timeoutMs: number = TIMEOUT_API_MS,
): Promise<ResultadoPedidos> {
  const controle = new AbortController();
  const timer = setTimeout(() => controle.abort(), timeoutMs);
  try {
    const res = await fetchImpl(urlPedidosMotivo(base, f, tipo, codigo), { cache: "no-store", signal: controle.signal });
    if (!res.ok) return { ok: false, motivo: `A API respondeu ${res.status}.` };
    const json = (await res.json()) as PedidosMotivoResponse;
    if (!json || !Array.isArray(json.pedidos)) return { ok: false, motivo: "Resposta inválida da API." };
    return { ok: true, dados: json };
  } catch {
    return { ok: false, motivo: controle.signal.aborted ? MOTIVO_TIMEOUT : "Não foi possível contatar a API." };
  } finally {
    clearTimeout(timer);
  }
}

export interface LinhaPedidoCx {
  chave: string;
  pedido: string;
  marca: string;
  dia: string;
  situacao: string;
  valor: string;
}

function diaBr(iso: string | null): string {
  if (!iso) return "—";
  const [a, m, d] = iso.slice(0, 10).split("-");
  return d && m && a ? `${d}/${m}/${a}` : "—";
}

export function montarPedidosMotivo(dados: PedidosMotivoResponse, rotuloMarca: (m: string) => string) {
  const linhas: LinhaPedidoCx[] = dados.pedidos.map((p, i) => ({
    chave: `${p.marca}:${p.devolucao ?? p.pedido ?? i}`,
    pedido: p.pedido ?? "—",
    marca: rotuloMarca(p.marca),
    dia: diaBr(p.dia),
    situacao: p.situacao ?? "—",
    valor: p.valor == null ? "—" : reais(p.valor),
  }));
  const qtd = inteiro(dados.total);
  const resumo = dados.truncado
    ? `${qtd} pedidos · mostrando os ${inteiro(linhas.length)} mais recentes`
    : `${qtd} pedido(s)`;
  const avisos: string[] = [];
  if (dados.parcial) avisos.push("Uma das fontes não respondeu: a lista pode estar incompleta.");
  if (!dados.numero_publico) avisos.push("O número do pedido está retido nesta configuração da Torre.");
  return { linhas, resumo, avisos, comValor: dados.tipo === "devolucao" };
}

function celulaCsv(v: string): string {
  const seguro = /^[=+\-@]/.test(v) ? `'${v}` : v; // nunca vira formula no Excel
  return /[";\n]/.test(seguro) ? `"${seguro.replace(/"/g, '""')}"` : seguro;
}

/** CSV com `;` (Excel pt-BR). Valor com virgula decimal. */
export function csvPedidosMotivo(dados: PedidosMotivoResponse, rotulo: string): string {
  const cab = ["pedido", "devolucao", "marca", "dia", "situacao", "valor_reembolsado", "motivo"];
  const linhas = dados.pedidos.map((p) =>
    [
      p.pedido ?? "",
      p.devolucao ?? "",
      p.marca,
      p.dia ?? "",
      p.situacao ?? "",
      p.valor == null ? "" : String(p.valor).replace(".", ","),
      rotulo,
    ]
      .map(celulaCsv)
      .join(";"),
  );
  return "﻿" + [cab.join(";"), ...linhas].join("\n");
}

export function nomeDoCsvPedidos(tipo: TipoPedidos, codigo: string, f: FiltrosCx, agora: Date): string {
  return `cx-${tipo}-${codigo}${f.marca ? `-${f.marca}` : ""}-${f.dias}d-${agora.toISOString().slice(0, 10)}.csv`;
}
