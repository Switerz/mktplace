import type { Marketplace } from "../mock-data";

/** Estado completo dos filtros globais (canal, marca, periodo, comparacao). */
export interface GlobalFilters {
  channels: Marketplace[];
  /** Marcas selecionadas (brand_key). Vazio = todas (sem filtro). */
  brands: string[];
  /** YYYY-MM-DD, inclusive. */
  dateFrom: string;
  /** YYYY-MM-DD, inclusive. */
  dateTo: string;
  compare: boolean;
}

export type DatePreset =
  | "hoje"
  | "7d"
  | "30d"
  | "90d"
  | "mes_atual"
  | "mes_anterior"
  | "personalizado";
