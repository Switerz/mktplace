/**
 * Cores de canal da Gerencial V2 (Gate V2-1).
 *
 * Modulo proprio, sem nenhuma dependencia, por um motivo concreto de bundle: os
 * cards de Canais, Saude do volume e Movimentos precisam dessas cores, e quando
 * elas moravam em `EvolutionChart.tsx` cada um desses imports estaticos
 * arrastava o recharts para o bundle inicial — anulando o `next/dynamic` do
 * grafico (First Load da rota `/` caiu de 252 kB para ~157 kB ao separar).
 *
 * UX-TORRE-2: o hex fixo virou CLASSE de token (`bg-canal-*`), que muda com o
 * tema. O SVG do grafico nao le classe — ele pega a cor resolvida em
 * `temaDoGrafico(...).canais`, que espelha os mesmos tokens.
 */
import type { Marketplace } from "../mock-data.ts";

/** Fundo com a cor de identidade do canal (ponto, barra, legenda). */
export const CHANNEL_DOT: Record<Marketplace, string> = {
  tiktok: "bg-canal-tiktok",
  ml: "bg-canal-ml",
  shopee: "bg-canal-shopee",
};
