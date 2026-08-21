// Apresentação do mapa de oportunidades (Gate V3-1B, bloco 3 — §7.3 do plano).
//
// REGRA CENTRAL DESTE MÓDULO: ele não decide nada de negócio.
//
// A mediana de GMV, a referência de ROAS, a classificação em quadrante, os
// agregados, as faixas e a seleção dos destaques vêm TODOS do `opportunity_map`
// do backend (BE6). Aqui só existe posicionamento visual, escala de tamanho,
// cor, rótulo e redação. Recalcular qualquer uma dessas decisões no cliente
// seria refazer no frontend o que é contrato — e divergir dele silenciosamente
// na primeira mudança de regra.
//
// A consequência prática: `plotPoint` posiciona o ponto DENTRO da metade que o
// backend já atribuiu, nunca escolhe a metade. Se um dia a fronteira do
// contrato mudar, o desenho acompanha sem uma linha de código aqui.

import type {
  OpportunityBand, OpportunityHighlight, OpportunityMap, OpportunityQuadrant,
} from "@/lib/api-client";

export type QuadrantKey = OpportunityQuadrant["key"];
export type BandKey = OpportunityBand["key"];

/** Ordem canônica, igual à do payload. */
export const QUADRANT_KEYS: readonly QuadrantKey[] = [
  "escalar", "testar_investimento", "monitorar", "reduzir_parar",
];
export const BAND_KEYS: readonly BandKey[] = [
  "sem_ads", "roas_indisponivel_com_investimento",
];

interface QuadrantMeta {
  label: string;
  /** Onde o quadrante fica no plano: retorno alto/baixo × volume alto/baixo. */
  roasHigh: boolean;
  gmvHigh: boolean;
  /** Cor consistente por quadrante, usada no ponto e na legenda. */
  dot: string;
  chip: string;
  /** Leitura da regra, escrita com as fronteiras. */
  regra: (roasRef: string, gmvRef: string) => string;
  /** O que a decisão significa, sem prometer o que o payload não sustenta. */
  leitura: string;
}

export const QUADRANT_META: Record<QuadrantKey, QuadrantMeta> = {
  escalar: {
    label: "Escalar",
    roasHigh: true, gmvHigh: true,
    dot: "bg-emerald-500", chip: "bg-emerald-50 text-emerald-700 border-emerald-200",
    regra: (r, g) => `ROAS ≥ ${r} e GMV ≥ ${g}`,
    leitura: "Retorno acima da referência com volume acima da referência.",
  },
  testar_investimento: {
    label: "Testar investimento",
    roasHigh: true, gmvHigh: false,
    dot: "bg-sky-500", chip: "bg-sky-50 text-sky-700 border-sky-200",
    regra: (r, g) => `ROAS ≥ ${r} e GMV < ${g}`,
    leitura: "Retorno acima da referência, mas volume ainda abaixo dela.",
  },
  monitorar: {
    label: "Monitorar",
    roasHigh: false, gmvHigh: true,
    dot: "bg-amber-500", chip: "bg-amber-50 text-amber-700 border-amber-200",
    regra: (r, g) => `ROAS < ${r} e GMV ≥ ${g}`,
    leitura: "Volume acima da referência sustentado por retorno abaixo dela.",
  },
  reduzir_parar: {
    label: "Reduzir ou parar",
    roasHigh: false, gmvHigh: false,
    dot: "bg-rose-500", chip: "bg-rose-50 text-rose-700 border-rose-200",
    regra: (r, g) => `ROAS < ${r} e GMV < ${g}`,
    leitura: "Retorno e volume ambos abaixo da referência.",
  },
};

interface BandMeta {
  label: string;
  /** A explicação de cada faixa é DIFERENTE de propósito: são fatos distintos. */
  explicacao: string;
  /** O que a faixa NÃO é — a confusão que o plano manda evitar. */
  naoConfundir: string;
  chip: string;
}

export const BAND_META: Record<BandKey, BandMeta> = {
  sem_ads: {
    label: "Sem investimento em Ads",
    explicacao:
      "Estes produtos não têm investimento de mídia no snapshot: `ad_spend` é zero "
      + "ou ausente. Sem investimento não existe retorno a medir, então eles ficam "
      + "fora dos quadrantes em vez de entrar como retorno baixo.",
    naoConfundir:
      "Não é retorno indisponível com gasto — isso é a outra faixa. Aqui não houve gasto.",
    chip: "bg-slate-50 text-slate-600 border-slate-200",
  },
  roas_indisponivel_com_investimento: {
    label: "Investimento sem ROAS medido",
    explicacao:
      "Estes produtos TÊM investimento de mídia, mas o snapshot não traz ROAS para "
      + "eles: é falha de mensuração, não retorno ruim. É aqui que o desperdício "
      + "não-mensurável fica visível, em vez de desaparecer dentro de \"sem Ads\".",
    naoConfundir:
      "Não é ROAS baixo. ROAS baixo é número medido e ocupa quadrante — inclusive "
      + "`ROAS = 0`, que é retorno baixo medido, nunca indisponibilidade.",
    chip: "bg-violet-50 text-violet-700 border-violet-200",
  },
};

// ---------------------------------------------------------------------------
// Estado do bloco
// ---------------------------------------------------------------------------

/**
 * Moeda EXATA em pt-BR, para as referências.
 *
 * O QA visual pegou isto: `fmtBrl` abrevia, e renderizava a mediana global
 * (2.207,05) e a de barbours (1.816,73) as duas como "R$ 2K". As duas
 * referências ficavam visualmente idênticas, enquanto o próprio diálogo afirma
 * que a mediana "muda quando o escopo muda".
 *
 * Referência é LIMIAR, não manchete: perder as casas decimais apaga justamente
 * a informação que ela existe para dar. Manchete continua abreviada.
 */
export function moedaExata(v: number): string {
  return v.toLocaleString("pt-BR", {
    style: "currency", currency: "BRL",
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
}

export type MatrixState = "available" | "empty" | "unavailable" | "out_of_scope";

/**
 * O estado do bloco, derivado do contrato — nunca inferido de contagem.
 *
 * `out_of_scope` é o caso "só Ápice": o escopo ML ficou vazio, e isso é
 * diferente de "o universo é zero". A tela precisa dizer coisas diferentes.
 */
export function matrixState(map: OpportunityMap | null | undefined, mlScope: readonly string[]): MatrixState {
  if (!map) return "empty";
  if (mlScope.length === 0) return "out_of_scope";
  if (map.classification_status === "unavailable_no_positive_gmv") return "unavailable";
  if (map.classification_status === "empty" || map.total_count === 0) return "empty";
  return "available";
}

/** `true` quando os pontos exibidos são só uma amostra do universo classificado. */
export function isSample(map: OpportunityMap): boolean {
  return map.returned_count < map.total_count;
}

/**
 * A frase que separa universo de amostra. O plano exige que a UI declare, quando
 * `returned_count < total_count`, que os agregados cobrem o universo e os pontos
 * são apenas destaques.
 */
export function sampleDeclaration(map: OpportunityMap): string {
  if (map.total_count === 0) return "Nenhum produto classificado neste escopo.";
  if (!isSample(map)) {
    return `Todos os ${map.total_count} produtos classificados estão plotados.`;
  }
  return (
    `Os agregados de cada quadrante cobrem o universo completo de ${map.total_count} `
    + `produtos. Os ${map.returned_count} pontos plotados são destaques — no máximo `
    + `${map.highlight_limit_per_quadrant} por quadrante —, nunca todos os produtos.`
  );
}

// ---------------------------------------------------------------------------
// Posicionamento — apresentação, jamais classificação
// ---------------------------------------------------------------------------

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);

/**
 * Posição de um valor no eixo, em `0..1`, com a referência sempre em `0.5`.
 *
 * Escala logarítmica de razão: uma década acima da referência vai a `1`, uma
 * década abaixo vai a `0`. É o que permite ver ao mesmo tempo um produto com
 * 10× a mediana e um com um décimo dela sem que todos colem numa borda.
 *
 * `metadeAlta` vem do QUADRANTE atribuído pelo backend. A posição é confinada à
 * metade correta, então o desenho nunca contradiz a classificação — nem por erro
 * de ponto flutuante na fronteira inclusiva, nem se a regra do contrato mudar.
 */
export function axisPosition(valor: number | null, referencia: number, metadeAlta: boolean): number {
  const meia = metadeAlta ? [0.5, 1] : [0, 0.5];
  if (valor == null || valor <= 0 || referencia <= 0) return metadeAlta ? 0.5 : 0.02;
  const razao = Math.log10(valor / referencia);
  const cru = clamp01(0.5 + (razao / 2) * 0.5);
  // confina à metade que o contrato determinou
  return Math.min(Math.max(cru, meia[0]), meia[1]);
}

export interface PlottedPoint {
  highlight: OpportunityHighlight;
  /** `0..1`, esquerda → direita (retorno). */
  x: number;
  /** `0..1`, baixo → cima (volume). */
  y: number;
  /** `0..1`, para a escala de raio. */
  weight: number;
  quadrant: QuadrantKey;
}

/**
 * Converte os destaques em pontos. A ordem de saída preserva a do payload, que
 * já é determinística (`highlight_order`).
 */
export function plotPoints(map: OpportunityMap): PlottedPoint[] {
  const roasRef = map.roas_reference;
  const gmvRef = map.gmv_reference;
  if (gmvRef == null) return [];
  const maiorAds = map.highlights.reduce((m, h) => Math.max(m, h.ad_spend), 0);
  return map.highlights.map((h) => {
    const meta = QUADRANT_META[h.quadrant];
    return {
      highlight: h,
      x: axisPosition(h.ad_roas, roasRef, meta.roasHigh),
      y: axisPosition(h.gmv, gmvRef, meta.gmvHigh),
      weight: maiorAds > 0 ? clamp01(h.ad_spend / maiorAds) : 0,
      quadrant: h.quadrant,
    };
  });
}

/**
 * Raio do ponto a partir do investimento. Raiz quadrada porque a percepção de
 * quantidade num círculo é de ÁREA, não de raio: escalar o raio linearmente
 * exageraria os maiores. Piso de 4px para o ponto continuar clicável e visível
 * mesmo com investimento zero.
 */
export function pointRadius(weight: number, min = 4, max = 14): number {
  const w = clamp01(weight);
  return Math.round((min + (max - min) * Math.sqrt(w)) * 10) / 10;
}

/**
 * Quais pontos recebem rótulo: só os maiores contribuintes de cada quadrante
 * (§7.3, "rótulo apenas nos maiores contribuintes"). Rotular todos ilegibiliza.
 */
export function labelledPoints(pontos: readonly PlottedPoint[], porQuadrante = 1): Set<string> {
  const porQ = new Map<QuadrantKey, PlottedPoint[]>();
  for (const p of pontos) {
    const lista = porQ.get(p.quadrant) ?? [];
    lista.push(p);
    porQ.set(p.quadrant, lista);
  }
  const out = new Set<string>();
  for (const lista of porQ.values()) {
    [...lista]
      .sort((a, b) => b.weight - a.weight || a.highlight.item_id.localeCompare(b.highlight.item_id))
      .slice(0, porQuadrante)
      .forEach((p) => out.add(p.highlight.item_id));
  }
  return out;
}

// ---------------------------------------------------------------------------
// Leitura de um ponto — por que ele caiu ali
// ---------------------------------------------------------------------------

export interface PointReading {
  roasComparacao: string;
  gmvComparacao: string;
  porque: string;
}

/**
 * Compara o produto com as DUAS referências e explica o quadrante. Não
 * reclassifica: parte do quadrante que o backend atribuiu.
 */
export function readPoint(
  h: OpportunityHighlight, map: OpportunityMap,
  fmtMoeda: (v: number) => string, fmtRoas: (v: number) => string,
): PointReading {
  const meta = QUADRANT_META[h.quadrant];
  const roasRef = fmtRoas(map.roas_reference);
  const gmvRef = map.gmv_reference == null ? "sem referência" : fmtMoeda(map.gmv_reference);
  const roasComparacao = h.ad_roas == null
    ? `ROAS indisponível no snapshot (referência ${roasRef})`
    : `ROAS ${fmtRoas(h.ad_roas)} ${meta.roasHigh ? "≥" : "<"} referência ${roasRef}`;
  const gmvComparacao = `GMV ${fmtMoeda(h.gmv)} ${meta.gmvHigh ? "≥" : "<"} referência ${gmvRef}`;
  return {
    roasComparacao,
    gmvComparacao,
    porque:
      `Caiu em "${meta.label}" porque ${meta.regra(roasRef, gmvRef)}. `
      + `As duas referências são descritivas do portfólio no escopo atual, não metas.`,
  };
}

/** Origem declarada de cada referência — o plano exige dizer de onde vem cada uma. */
export function referenceOrigins(map: OpportunityMap, fmtMoeda: (v: number) => string): string[] {
  return [
    `Referência de ROAS = ${map.roas_reference}: é o corte que a lista "Escalar" já `
    + `usava antes deste mapa, não um número novo.`,
    map.gmv_reference == null
      ? "Referência de GMV: indisponível — não existe GMV positivo neste escopo."
      : `Referência de GMV = ${fmtMoeda(map.gmv_reference)}: mediana do GMV `
        + `estritamente positivo dos ${map.gmv_reference_basis_count} produtos com venda `
        + `no escopo atual. Muda quando o escopo muda.`,
  ];
}

// ---------------------------------------------------------------------------
// Frescor da fotografia ML (BE4)
// ---------------------------------------------------------------------------

/**
 * Rótulo do frescor. Sem timestamp, diz que não está disponível — nunca
 * substitui por `new Date()`, que mostraria a hora do navegador como se fosse a
 * da carga.
 */
export function freshnessLabel(iso: string | null): string {
  if (!iso) return "Frescor da fotografia indisponível nesta resposta";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Frescor da fotografia indisponível nesta resposta";
  return `Fotografia ML sincronizada em ${d.toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", timeZone: "America/Sao_Paulo",
  })}`;
}

/**
 * Redação de amostra das listas, agora com o total VERDADEIRO do BE3. O "ao
 * menos N" do V3-1A existia porque o total real não era conhecido; com BE3 ele
 * deixa de ser necessário.
 */
export function trueSampleNote(exibidos: number, total: number, rotulo: string): string {
  if (total === 0) return `Nenhum ${rotulo} neste escopo.`;
  if (exibidos >= total) return `${total} ${rotulo}${total === 1 ? "" : "s"} no escopo.`;
  return `${exibidos} de ${total} ${rotulo}${total === 1 ? "" : "s"} no escopo.`;
}
