/**
 * Gate EXPEDICAO-UX-UNIFICADA-1 — um seletor so' para os tres canais.
 *
 * Antes eram dois niveis: "Fila (Shopee / ML) | TikTok" e, dentro da fila,
 * "Shopee | Mercado Livre". Pareciam produtos diferentes. Agora e' um nivel
 * so' — Shopee | Mercado Livre | TikTok Shop — com o MESMO parametro
 * `?channel=` de sempre, para os links ja' compartilhados abrirem onde abriam.
 *
 * O canal da fila (`Canal`, em `expedicao-contract.ts`) NAO ganhou o TikTok:
 * aquele tipo indexa KPIs, mapa de risco e timeline que o TikTok nao publica.
 * O canal da EXPEDICAO e' a uniao, e so' a casca o conhece.
 */
import { CANAL_PADRAO, sanitizarCanal, type Canal } from "./expedicao-contract.ts";
import { PARAM_DIAS } from "./expedicao-janela.ts";

export const CANAL_TIKTOK = "tiktokshop" as const;
export type CanalExpedicao = Canal | typeof CANAL_TIKTOK;

/** Mesmo parametro de sempre: os links antigos continuam abrindo onde abriam. */
export const PARAM_CANAL = "channel";

export const ROTULO_CANAL_EXPEDICAO: Record<CanalExpedicao, string> = {
  shopee: "Shopee",
  mercadolivre: "Mercado Livre",
  tiktokshop: "TikTok Shop",
};

export interface FlagsDeCanal {
  ml: boolean;
  tiktok: boolean;
}

/** Ordem fixa do seletor. Canal com a flag desligada simplesmente nao existe. */
export function canaisHabilitados(flags: FlagsDeCanal): CanalExpedicao[] {
  const canais: CanalExpedicao[] = [CANAL_PADRAO];
  if (flags.ml) canais.push("mercadolivre");
  if (flags.tiktok) canais.push(CANAL_TIKTOK);
  return canais;
}

/**
 * Canal da URL -> canal servivel. FAIL-CLOSED: valor fora da allowlist, ou de
 * um canal com a flag desligada, cai na Shopee — a mesma regra que
 * `sanitizarCanal` ja' aplicava a fila.
 */
export function lerCanalExpedicao(search: string, flags: FlagsDeCanal): CanalExpedicao {
  const valor = new URLSearchParams(search).get(PARAM_CANAL);
  if (valor === CANAL_TIKTOK && flags.tiktok) return CANAL_TIKTOK;
  return sanitizarCanal(valor, flags.ml);
}

export function ehCanalDaFila(canal: CanalExpedicao): canal is Canal {
  return canal !== CANAL_TIKTOK;
}

/**
 * URL da troca de canal.
 *
 * - `channel` some no canal padrao (Shopee), como sempre.
 * - `dias` SO' cai quando a troca atravessa fila <-> TikTok: os limites sao
 *   diferentes (1-14 na fila, 1-90 no TikTok) e 30 dias levados para a fila
 *   seria um link invalido. Shopee <-> Mercado Livre mantem a janela, como ja'
 *   mantinha antes deste gate.
 * - `visao` atravessa: profundidade e' intencao de quem le, nao do canal.
 */
export function urlDoCanal(href: string, de: CanalExpedicao, para: CanalExpedicao): string {
  const url = new URL(href);
  if (ehCanalDaFila(de) !== ehCanalDaFila(para)) url.searchParams.delete(PARAM_DIAS);
  if (para === CANAL_PADRAO) url.searchParams.delete(PARAM_CANAL);
  else url.searchParams.set(PARAM_CANAL, para);
  return url.toString();
}
