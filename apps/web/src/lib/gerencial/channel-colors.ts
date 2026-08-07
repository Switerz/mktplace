/**
 * Cores de canal da Gerencial V2 (Gate V2-1).
 *
 * Modulo proprio, sem nenhuma dependencia, por um motivo concreto de bundle: os
 * cards de Canais, Saude do volume e Movimentos precisam dessas cores, e quando
 * elas moravam em `EvolutionChart.tsx` cada um desses imports estaticos
 * arrastava o recharts para o bundle inicial — anulando o `next/dynamic` do
 * grafico (First Load da rota `/` caiu de 252 kB para ~157 kB ao separar).
 */
import type { Marketplace } from "../mock-data.ts";

export const CHANNEL_STROKE: Record<Marketplace, string> = {
  tiktok: "#8b5cf6",
  ml: "#06b6d4",
  shopee: "#f97316",
};

/** Cor da linha de total, mais escura que qualquer canal para se destacar. */
export const TOTAL_STROKE = "#4c1d95";
