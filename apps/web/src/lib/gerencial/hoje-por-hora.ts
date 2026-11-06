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

/** Tendencia de fechamento do dia por marca (ritmo de ontem ate' a mesma hora). */
export interface MarcaTendencia {
  brand: string;
  hoje: number;
  ontem_mesma_hora: number;
  ontem_dia: number;
  projecao: number | null;
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

const NOME_DO_CANAL: Record<Marketplace, string> = { tiktok: "TikTok", ml: "Mercado Livre", shopee: "Shopee" };

export function nomeDoCanal(canal: Marketplace): string {
  return NOME_DO_CANAL[canal];
}

/**
 * Quem segura o corte: os canais que pararam na hora do corte enquanto outro
 * ja' foi alem. Vazio quando todos estao na mesma hora (nada a explicar).
 */
export function canaisQueLimitam(r: Pick<HojePorHora, "canais" | "corte_hora">): Marketplace[] {
  if (r.corte_hora == null || r.canais.length < 2) return [];
  const max = Math.max(...r.canais.map((c) => c.horas_fechadas_hoje));
  if (max <= r.corte_hora) return [];
  return r.canais.filter((c) => c.horas_fechadas_hoje <= r.corte_hora!).map((c) => c.channel);
}
