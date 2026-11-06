/**
 * VENDAS-POR-HORA-1 — o "Hoje" padronizado da Gerencial.
 *
 * Contrato de `GET /api/v1/performance/hoje-por-hora` e a leitura pura dele.
 * O corte e' UM so' para todos os canais do filtro (a menor hora fechada entre
 * eles), e ontem e a semana passada sao somados ate' o MESMO corte — por isso
 * a comparacao e' justa. Ver `apps/api/app/services/hoje_por_hora_service.py`.
 */
import type { Marketplace } from "../mock-data.ts";

export interface BlocoVendas {
  gmv: number;
  pedidos: number;
  ticket: number | null;
}

export interface CanalFonte {
  channel: Marketplace;
  fonte_ate: string | null;
  horas_fechadas_hoje: number;
}

export interface CanalVendas {
  channel: Marketplace;
  hoje: BlocoVendas;
  ontem: BlocoVendas;
  semana_passada: BlocoVendas;
}

/** Tendencia de fechamento do dia (ritmo de ontem ate' a mesma hora). */
export interface Tendencia {
  hoje: number;
  ontem_mesma_hora: number;
  ontem_dia: number;
  projecao: number | null;
}

export interface MarcaTendencia extends Tendencia {
  brand: string;
  /** A mesma conta por canal; ausente em respostas antigas da API. */
  por_canal?: (Tendencia & { channel: Marketplace })[];
}

/**
 * Tendencia de fechamento somando SO' os canais que o card mostra ao lado
 * (ex.: so' os que tem meta): hoje ate' HHh x (ontem inteiro / ontem ate' HHh).
 * Sem venda ontem ate' o corte nesses canais, `null`: sem ritmo, sem palpite.
 */
export function tendenciaNosCanais(
  m: MarcaTendencia,
  canais: readonly Marketplace[],
): { projecao: number; ontemDia: number } | null {
  const partes = (m.por_canal ?? []).filter((p) => canais.includes(p.channel));
  const soma = (k: keyof Tendencia) => partes.reduce((a, p) => a + (p[k] ?? 0), 0);
  const ate = soma("ontem_mesma_hora");
  const dia = soma("ontem_dia");
  if (ate <= 0 || dia <= 0) return null;
  return { projecao: (soma("hoje") * dia) / ate, ontemDia: dia };
}

export interface HojePorHora {
  disponivel: boolean;
  motivo?: string | null;
  dia: string;
  corte_hora: number | null;
  canais: CanalFonte[];
  limitacoes: string[];
  hoje?: BlocoVendas | null;
  ontem?: BlocoVendas | null;
  semana_passada?: BlocoVendas | null;
  por_canal: CanalVendas[];
  /** Ausente em respostas antigas da API. */
  por_marca?: MarcaTendencia[];
}

/** "até 13h"; `null` quando ainda nao fechou nenhuma hora. */
export function rotuloDoCorte(corte: number | null): string | null {
  if (corte == null || corte <= 0) return null;
  return `até ${corte}h`;
}

/** Variacao percentual contra a base; `null` sem base (nao inventa 100%). */
export function variacao(atual: number, base: number): number | null {
  if (!base) return null;
  return ((atual - base) / base) * 100;
}

/** A comparacao de "Hoje" que os cards de sempre mostram, no lugar do "vs anterior". */
export interface ComparacaoDeHoje {
  /** "vs ontem até 14h", com o aviso quando um recorte fica de fora. */
  rotulo: string;
  gmvPct: number | null;
  pedidosPct: number | null;
  ticketPct: number | null;
  ontem: BlocoVendas;
  porCanal: Partial<Record<Marketplace, number | null>>;
}

/**
 * Hoje x ontem ATE' A MESMA HORA, dos dois lados com o mesmo corte. `null`
 * sem a tabela por hora ou antes de fechar a primeira hora: sem comparacao,
 * nunca uma injusta (dia parcial contra dia inteiro).
 */
export function comparacaoDeHoje(d: HojePorHora | null): ComparacaoDeHoje | null {
  const rotulo = d ? rotuloDoCorte(d.corte_hora) : null;
  if (!d?.disponivel || !rotulo || !d.hoje || !d.ontem) return null;
  const fora = d.limitacoes.some((l) => l.startsWith("Kokeshi na Shopee")) ? " (sem Kokeshi Shopee)" : "";
  return {
    rotulo: `vs ontem ${rotulo}${fora}`,
    gmvPct: variacao(d.hoje.gmv, d.ontem.gmv),
    pedidosPct: variacao(d.hoje.pedidos, d.ontem.pedidos),
    ticketPct: d.hoje.ticket != null && d.ontem.ticket != null ? variacao(d.hoje.ticket, d.ontem.ticket) : null,
    ontem: d.ontem,
    porCanal: Object.fromEntries(d.por_canal.map((c) => [c.channel, variacao(c.hoje.gmv, c.ontem.gmv)])),
  };
}
