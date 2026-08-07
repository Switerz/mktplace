/**
 * Matriz Marca x Canal, Movimentos e Concentracao por marca da Gerencial V2
 * (Gate V2-1, Tasks H e I). Tudo derivado de `/brands`, no mesmo periodo
 * global dos outros blocos — nenhum endpoint novo.
 *
 * Regras que este modulo existe para garantir:
 *
 * - **Intensidade dentro do canal.** A escala de cor de uma celula compara a
 *   marca com o maior valor DAQUELE canal. Nunca entre canais, cujas ordens de
 *   grandeza sao diferentes.
 * - **Piso de base nos movimentos.** Variacao percentual sobre base minuscula
 *   engana. Bases anteriores abaixo de R$ 1 mil ficam fora da lista, o piso e'
 *   constante nomeada, e a ordenacao usa o valor ABSOLUTO.
 * - **Sem Infinity.** Base anterior zero nunca produz percentual infinito: o
 *   percentual fica `null` e o absoluto continua valendo.
 * - **Empate deterministico.** Mesma variacao absoluta => ordem estavel por
 *   marca e depois por canal, para a lista nao dancar entre renders.
 * - **Concentracao sem aproximacao.** Top 3 so' existe com pelo menos tres
 *   marcas de GMV positivo; nao se aproxima quando a base e' insuficiente.
 *
 * Ranking de PRODUTOS nao existe aqui, por decisao do V2-0: produtos nao tem
 * escopo temporal uniforme entre canais (ML e' acumulado atual; TikTok e
 * Shopee sao competencia mensal).
 */
import type { BrandRow } from "../api-client.ts";
import { isMarketplaceSelected } from "../marketplace-filter.ts";
import type { Marketplace } from "../mock-data.ts";

/** Piso de base anterior para um movimento entrar na lista. */
export const MOVEMENT_MIN_PREV_BASE = 1000;
export const MOVEMENT_FLOOR_NOTE = "Bases anteriores inferiores a R$ 1 mil ficam fora da lista";
export const MOVEMENT_MAX_ITEMS = 5;
export const PRODUCTS_SCOPE_NOTE = "Produtos possui contratos de período próprios por canal";

/** Ordem canonica dos canais — usada como desempate estavel. */
const CHANNEL_ORDER: readonly Marketplace[] = ["tiktok", "ml", "shopee"];

interface ChannelFields {
  gmv: (r: BrandRow) => number | null;
  prev: (r: BrandRow) => number | null;
}

const CHANNEL_FIELDS: Record<Marketplace, ChannelFields> = {
  tiktok: { gmv: (r) => r.tiktok_gmv, prev: (r) => r.tiktok_gmv_prev },
  ml: { gmv: (r) => r.ml_gmv, prev: (r) => r.ml_gmv_prev },
  shopee: { gmv: (r) => r.shopee_gmv, prev: (r) => r.shopee_gmv_prev },
};

// ---------------------------------------------------------------------------
// Matriz Marca x Canal
// ---------------------------------------------------------------------------

export interface MatrixCell {
  channel: Marketplace;
  gmv: number | null;
  /** Participacao da marca DENTRO daquele canal. */
  sharePctInChannel: number | null;
  momPct: number | null;
  /** 0..1, relativo ao maior GMV do proprio canal. */
  intensity: number;
  available: boolean;
  unavailableReason: string | null;
}

export interface MatrixRow {
  brand: string;
  label: string;
  cells: MatrixCell[];
  brandTotal: number;
}

export interface BrandChannelMatrix {
  channels: Marketplace[];
  rows: MatrixRow[];
  channelTotals: Record<string, number>;
  grandTotal: number;
}

export function buildBrandChannelMatrix(
  brands: readonly BrandRow[],
  channels: readonly Marketplace[],
): BrandChannelMatrix {
  const activeChannels = CHANNEL_ORDER.filter((c) => isMarketplaceSelected(channels, c));

  // Maximo e total por canal, calculados ANTES das celulas: a intensidade e a
  // participacao de cada celula sao relativas ao proprio canal.
  const maxByChannel: Record<string, number> = {};
  const channelTotals: Record<string, number> = {};
  for (const channel of activeChannels) {
    const pick = CHANNEL_FIELDS[channel].gmv;
    let max = 0;
    let total = 0;
    for (const row of brands) {
      const v = pick(row);
      if (v == null) continue;
      if (v > max) max = v;
      total += v;
    }
    maxByChannel[channel] = max;
    channelTotals[channel] = total;
  }

  const rows: MatrixRow[] = brands.map((row) => {
    const cells: MatrixCell[] = activeChannels.map((channel) => {
      const { gmv: pickGmv, prev: pickPrev } = CHANNEL_FIELDS[channel];
      const gmv = pickGmv(row);
      const prev = pickPrev(row);
      if (gmv == null) {
        return {
          channel,
          gmv: null,
          sharePctInChannel: null,
          momPct: null,
          intensity: 0,
          available: false,
          unavailableReason: "Sem dado desta marca neste canal no período.",
        };
      }
      const channelTotal = channelTotals[channel];
      const max = maxByChannel[channel];
      return {
        channel,
        gmv,
        sharePctInChannel: channelTotal > 0 ? (gmv / channelTotal) * 100 : null,
        momPct: prev != null && prev > 0 ? ((gmv - prev) / prev) * 100 : null,
        intensity: max > 0 ? gmv / max : 0,
        available: true,
        unavailableReason: null,
      };
    });
    const brandTotal = cells.reduce((sum, c) => sum + (c.gmv ?? 0), 0);
    return { brand: row.brand, label: row.label, cells, brandTotal };
  });

  rows.sort((a, b) => b.brandTotal - a.brandTotal || a.brand.localeCompare(b.brand));

  const grandTotal = activeChannels.reduce((sum, c) => sum + (channelTotals[c] ?? 0), 0);
  return { channels: activeChannels, rows, channelTotals, grandTotal };
}

// ---------------------------------------------------------------------------
// Movimentos (maiores altas / maiores quedas)
// ---------------------------------------------------------------------------

export interface Movement {
  brand: string;
  brandLabel: string;
  channel: Marketplace;
  current: number;
  previous: number;
  deltaAbs: number;
  /** `null` quando a base anterior e' zero — nunca Infinity. */
  deltaPct: number | null;
}

export interface Movements {
  gains: Movement[];
  drops: Movement[];
  /** Quantos pares marca x canal ficaram fora pelo piso de base. */
  excludedByFloor: number;
}

export function buildMovements(
  brands: readonly BrandRow[],
  channels: readonly Marketplace[],
): Movements {
  const activeChannels = CHANNEL_ORDER.filter((c) => isMarketplaceSelected(channels, c));
  const all: Movement[] = [];
  let excludedByFloor = 0;

  for (const row of brands) {
    for (const channel of activeChannels) {
      const { gmv: pickGmv, prev: pickPrev } = CHANNEL_FIELDS[channel];
      const current = pickGmv(row);
      const previous = pickPrev(row);
      if (current == null || previous == null) continue;
      if (previous < MOVEMENT_MIN_PREV_BASE) {
        // Base insuficiente para uma variacao significar algo.
        if (current !== previous) excludedByFloor += 1;
        continue;
      }
      const deltaAbs = current - previous;
      if (deltaAbs === 0) continue;
      all.push({
        brand: row.brand,
        brandLabel: row.label,
        channel,
        current,
        previous,
        // previous >= 1000 aqui, entao a divisao e' sempre finita; o guard de
        // `previous > 0` fica explicito para o caso do piso mudar.
        deltaPct: previous > 0 ? (deltaAbs / previous) * 100 : null,
        deltaAbs,
      });
    }
  }

  // Ordenacao por |delta| desc, com desempate deterministico: marca (alfabetica)
  // e depois a ordem canonica de canal. Sem isso, duas variacoes iguais podiam
  // trocar de posicao entre renders.
  function compare(a: Movement, b: Movement): number {
    const byMagnitude = Math.abs(b.deltaAbs) - Math.abs(a.deltaAbs);
    if (byMagnitude !== 0) return byMagnitude;
    const byBrand = a.brand.localeCompare(b.brand);
    if (byBrand !== 0) return byBrand;
    return CHANNEL_ORDER.indexOf(a.channel) - CHANNEL_ORDER.indexOf(b.channel);
  }

  const gains = all.filter((m) => m.deltaAbs > 0).sort(compare).slice(0, MOVEMENT_MAX_ITEMS);
  const drops = all.filter((m) => m.deltaAbs < 0).sort(compare).slice(0, MOVEMENT_MAX_ITEMS);
  return { gains, drops, excludedByFloor };
}

// ---------------------------------------------------------------------------
// Concentracao por marca
// ---------------------------------------------------------------------------

export interface ConcentrationEntry {
  brand: string;
  label: string;
  gmv: number;
  sharePct: number;
}

export interface Concentration {
  entries: ConcentrationEntry[];
  total: number;
  /** Participacao da maior marca — exige >= 1 marca com GMV positivo. */
  top1Pct: number | null;
  /** Participacao das tres maiores — exige >= 3 marcas com GMV positivo. */
  top3Pct: number | null;
  positiveBrands: number;
}

export function buildConcentration(brands: readonly BrandRow[]): Concentration {
  const positive = brands
    .filter((b) => b.total_gmv > 0)
    .map((b) => ({ brand: b.brand, label: b.label, gmv: b.total_gmv }));
  const total = positive.reduce((sum, b) => sum + b.gmv, 0);
  const entries: ConcentrationEntry[] = positive
    .map((b) => ({ ...b, sharePct: total > 0 ? (b.gmv / total) * 100 : 0 }))
    .sort((a, b) => b.gmv - a.gmv || a.brand.localeCompare(b.brand));

  const positiveBrands = entries.length;
  const top1Pct = positiveBrands >= 1 ? entries[0].sharePct : null;
  // Nao aproximar: com menos de tres marcas positivas, Top 3 nao existe.
  const top3Pct =
    positiveBrands >= 3
      ? entries.slice(0, 3).reduce((sum, e) => sum + e.sharePct, 0)
      : null;

  return { entries, total, top1Pct, top3Pct, positiveBrands };
}
