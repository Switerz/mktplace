// Cartoes de prioridade da fotografia ML (Gate V3-1A, bloco 2).
//
// Derivados SOMENTE das listas que o payload atual entrega. Duas regras que
// mandam no modulo inteiro:
//
// 1. a contagem exibida e' de REGISTROS RECEBIDOS, nunca do universo — as
//    tres listas vem capadas no backend e `*_total_count` so' existe com BE3;
// 2. cartao sem registro DESAPARECE, em vez de virar "prioridade zero".
//
// `tk_products` NAO entra aqui: tem janela de 30 dias e a fotografia ML nao
// tem janela; misturar produziria um numero sem periodo definivel.
//
// Modulo puro, sem React.

import type { InteligenciaData } from "../api-client.ts";
import type { BrandSelection } from "./brands.ts";
import {
  buildQueue,
  KIND_ORDER,
  KIND_RULES,
  LIST_LIMITS,
  queueForLens,
  sampleNote,
  sortForLens,
  type EvidenceItem,
  type EvidenceKind,
} from "./queue.ts";

export interface Priority {
  /** Chave allowlisted — tambem a lente de destino na fila. */
  kind: EvidenceKind;
  /** Titulo humano do cartao. */
  title: string;
  /** Quantidade de REGISTROS RECEBIDOS nesta lista. */
  received: number;
  /** Soma da metrica relevante sobre os registros recebidos (nunca do universo). */
  amount: number;
  /** Qual metrica `amount` representa, para a UI formatar corretamente. */
  amountKind: "money" | "count";
  /** Rotulo da metrica ("Ad spend na amostra"). */
  amountLabel: string;
  /** Motivo curto — a regra real que formou a lista. */
  reason: string;
  /** Limitacao de cobertura, sempre presente. */
  limitation: string;
  /** Ate cinco maiores contribuintes, para o dialogo. */
  contributors: EvidenceItem[];
}

const TITLES: Record<EvidenceKind, string> = {
  parar: "Desperdício de Ads",
  escalar: "Oportunidade de escala",
  testar: "Oportunidade de teste orgânico",
};

const REASONS: Record<EvidenceKind, string> = {
  parar: "gasto em mídia sem nenhuma venda registrada",
  escalar: "ROAS alto com ads já ativos",
  testar: "vende bem sem nenhum investimento em mídia",
};

const AMOUNT_LABELS: Record<EvidenceKind, string> = {
  parar: "Ad spend na amostra",
  escalar: "GMV na amostra",
  testar: "GMV na amostra",
};

/** Soma que ignora `null` sem convertê-lo em zero de exibição. */
function sum(rows: readonly EvidenceItem[], pick: (r: EvidenceItem) => number | null | undefined): number {
  let total = 0;
  for (const r of rows) {
    const v = pick(r);
    if (v != null) total += v;
  }
  return total;
}

export const MAX_CONTRIBUTORS = 5;

/**
 * Constroi os cartoes de prioridade, na ordem deterministica de `KIND_ORDER`
 * (desperdicio primeiro, porque e' dinheiro saindo agora).
 *
 * Cartao com zero registros e' OMITIDO do resultado.
 */
export function buildPriorities(
  data: InteligenciaData | null | undefined,
  selection: BrandSelection,
): Priority[] {
  const queue = buildQueue(data, selection);
  const out: Priority[] = [];
  for (const kind of KIND_ORDER) {
    const rows = queueForLens(queue, kind);
    if (rows.length === 0) continue;
    const ordered = sortForLens(rows, kind);
    out.push({
      kind,
      title: TITLES[kind],
      received: rows.length,
      amount: kind === "parar" ? sum(rows, (r) => r.ad_spend) : sum(rows, (r) => r.gmv),
      amountKind: "money",
      amountLabel: AMOUNT_LABELS[kind],
      reason: REASONS[kind],
      limitation: sampleNote(kind, rows.length),
      contributors: ordered.slice(0, MAX_CONTRIBUTORS),
    });
  }
  return out;
}

/** Contagem por origem, para as abas da fila e a nota de amostra. */
export function receivedByKind(
  data: InteligenciaData | null | undefined,
  selection: BrandSelection,
): Record<EvidenceKind, number> {
  const queue = buildQueue(data, selection);
  return {
    parar: queueForLens(queue, "parar").length,
    escalar: queueForLens(queue, "escalar").length,
    testar: queueForLens(queue, "testar").length,
  };
}

/** Frase de regra para o dialogo da prioridade. */
export function priorityRule(kind: EvidenceKind): string {
  return KIND_RULES[kind];
}

/** Teto conhecido da lista, para o texto de limitacao do dialogo. */
export function priorityLimit(kind: EvidenceKind): number {
  return LIST_LIMITS[kind];
}
