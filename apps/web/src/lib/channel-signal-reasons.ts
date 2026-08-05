// Explicação humana dos sinais da matriz marca × canal (Gate G2 — ver
// docs/DRILLDOWN_ARCHITECTURE.md §3/§5). Módulo puro (sem React), mesmo
// padrão de canais-channel-metrics.ts/executive-pulse.ts.
//
// Regras do contrato (Task 2, aprovadas na Task 1):
// - NÃO cria threshold novo e NÃO reclassifica severidade: o sinal já veio
//   decidido do backend; aqui só se DESCREVE a evidência já carregada.
// - Mediana/p75 usados são sempre do MESMO canal (o chamador passa o
//   CanaisChannelMedian resolvido por findChannelMedian, que usa igualdade
//   estrita de canal — nunca se mistura referência de outro canal).
// - Valor exatamente igual ao p75 é descrito como "no p75 do canal ou
//   acima" (o corte do backend é inclusivo) — nunca "acima do p75".
// - Ausência de dado é dita explicitamente ("referência do canal
//   indisponível", "sem evidência suficiente…") — nunca convertida em zero;
//   um zero real continua sendo formatado como número.
// - Nenhuma causa comercial é inventada: quando os dados carregados não
//   explicam a causa, a explicação diz isso claramente.

import type { CanaisChannelMedian, CanaisChannelRow } from "./api-client";
// Extensão explícita: módulo também roda direto no node:test (type
// stripping), mesma convenção de kpi-drilldown.ts -> marketplace-filter.ts.
import { signalLabel } from "./canais-channel-metrics.ts";

export interface SignalExplanation {
  signal: string;
  /** Rótulo humano do chip (reusa signalLabel — fallback seguro p/ sinal desconhecido). */
  label: string;
  /** Evidência em linguagem humana, construída só com dados já carregados. */
  reason: string;
}

export interface ChannelDiagnosis {
  /** 1–2 frases: o que o período mostra para esta combinação marca × canal. */
  headline: string;
  /** Uma explicação por sinal presente na linha (ordem original preservada). */
  explanations: SignalExplanation[];
  /** Próximo passo sugerido quando os dados já o sustentam — null quando não há
   * nada além do CTA padrão a dizer. */
  nextAction: string | null;
}

const fmtPct1 = (v: number) => `${v.toFixed(1).replace(".", ",")}%`;
const fmtRoas = (v: number) => `${v.toFixed(2).replace(".", ",")}x`;
// BRL local (módulo puro, sem dependência dos formatters compactos da UI):
// valor inteiro em reais, suficiente para a evidência textual do diagnóstico.
const fmtBrl = (v: number) =>
  new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 }).format(v);

/** Compara valor vs p75 com igualdade inclusiva — "no p75 ou acima" quando
 * value >= p75 (o corte do sinal no backend é inclusivo). Quando o valor
 * carregado fica ABAIXO do p75 carregado, o sinal "alto" recebido não é
 * confirmado pela referência exibida: dizemos a inconsistência explicitamente
 * (Task 3, Finding 2) — nunca reclassificamos o sinal nem fingimos que o
 * corte foi atendido. */
function p75Phrase(value: number, p75: number, fmt: (v: number) => string): string {
  return value >= p75
    ? `no p75 do canal (${fmt(p75)}) ou acima`
    : `abaixo do p75 do canal carregado (${fmt(p75)}) — inconsistência entre o sinal recebido e a referência exibida`;
}

const INSUFFICIENT = "os dados carregados não permitem detalhar a causa.";

function explainSignal(
  signal: string,
  row: CanaisChannelRow,
  median: CanaisChannelMedian | null,
): SignalExplanation {
  const label = signalLabel(signal);

  switch (signal) {
    case "roas_forte": {
      if (row.roas == null) {
        return { signal, label, reason: `Sinal do canal indica ROAS forte, mas ${INSUFFICIENT}` };
      }
      const med = median?.roas_median;
      const ref = med != null ? ` — mediana do canal: ${fmtRoas(med)}` : " — mediana do canal indisponível";
      return { signal, label, reason: `ROAS de ${fmtRoas(row.roas)} no período${ref}.` };
    }
    case "ads_subutilizado": {
      // Espelha a regra REAL do backend (performance_service): GMV na mediana
      // do canal ou acima + Ads/GMV abaixo da mediana (ou ausente) + ROAS na
      // mediana ou acima / ausente / gasto zero. Só evidências disponíveis
      // são afirmadas; nenhuma causalidade comercial é inventada.
      const parts: string[] = [];

      // 1) GMV vs mediana de GMV do MESMO canal
      const gmvMed = median?.gmv_median;
      if (row.gmv != null && gmvMed != null) {
        parts.push(
          row.gmv >= gmvMed
            ? `GMV de ${fmtBrl(row.gmv)} na mediana do canal (${fmtBrl(gmvMed)}) ou acima`
            : `GMV de ${fmtBrl(row.gmv)}, abaixo da mediana do canal carregada (${fmtBrl(gmvMed)})`,
        );
      } else {
        parts.push("mediana de GMV do canal indisponível para comparar");
      }

      // 2) Ads/GMV vs mediana — ausência de percentual é parte da própria
      //    regra do sinal (conta como subutilização), dita explicitamente.
      const adsMed = median?.ads_gmv_pct_median;
      if (row.ads_gmv_pct == null) {
        parts.push("percentual de Ads indisponível (a regra do canal trata a ausência como subutilização)");
      } else if (adsMed != null) {
        parts.push(
          row.ads_gmv_pct < adsMed
            ? `investimento em Ads de ${fmtPct1(row.ads_gmv_pct)} do GMV, abaixo da mediana do canal (${fmtPct1(adsMed)})`
            : `investimento em Ads de ${fmtPct1(row.ads_gmv_pct)} do GMV — mediana do canal: ${fmtPct1(adsMed)}`,
        );
      } else {
        parts.push(`investimento em Ads de ${fmtPct1(row.ads_gmv_pct)} do GMV — mediana do canal indisponível`);
      }

      // 3) ROAS/gasto — o dado que sustenta o "vale investir mais": gasto
      //    zero, ou ROAS na mediana ou acima; ausências ditas explicitamente.
      const roasMed = median?.roas_median;
      if (row.ad_spend === 0) {
        parts.push("sem gasto de Ads no período");
      } else if (row.roas != null && roasMed != null) {
        parts.push(
          row.roas >= roasMed
            ? `ROAS de ${fmtRoas(row.roas)} na mediana do canal (${fmtRoas(roasMed)}) ou acima`
            : `ROAS de ${fmtRoas(row.roas)} — mediana do canal: ${fmtRoas(roasMed)}`,
        );
      } else if (row.roas != null) {
        parts.push(`ROAS de ${fmtRoas(row.roas)} — mediana do canal indisponível`);
      } else {
        parts.push("ROAS indisponível");
      }

      return { signal, label, reason: `${parts.join("; ")}.` };
    }
    case "custo_alto": {
      if (row.marketplace_cost_pct == null) {
        return { signal, label, reason: `Sinal do canal indica custo alto, mas ${INSUFFICIENT}` };
      }
      const v = row.marketplace_cost_pct;
      const p75 = median?.marketplace_cost_pct_p75;
      const med = median?.marketplace_cost_pct_median;
      if (p75 != null) {
        const medTxt = med != null ? `; mediana: ${fmtPct1(med)}` : "";
        return { signal, label, reason: `Custo marketplace/GMV de ${fmtPct1(v)} — ${p75Phrase(v, p75, fmtPct1)}${medTxt}.` };
      }
      if (med != null) {
        return { signal, label, reason: `Custo marketplace/GMV de ${fmtPct1(v)} — mediana do canal: ${fmtPct1(med)}.` };
      }
      return { signal, label, reason: `Custo marketplace/GMV de ${fmtPct1(v)} — referência do canal indisponível.` };
    }
    case "frete_alto": {
      if (row.seller_shipping_pct == null) {
        return { signal, label, reason: `Sinal do canal indica frete alto, mas ${INSUFFICIENT}` };
      }
      const v = row.seller_shipping_pct;
      const p75 = median?.seller_shipping_pct_p75;
      const med = median?.seller_shipping_pct_median;
      if (p75 != null) {
        const medTxt = med != null ? `; mediana: ${fmtPct1(med)}` : "";
        return { signal, label, reason: `Frete seller/GMV de ${fmtPct1(v)} — ${p75Phrase(v, p75, fmtPct1)}${medTxt}.` };
      }
      if (med != null) {
        return { signal, label, reason: `Frete seller/GMV de ${fmtPct1(v)} — mediana do canal: ${fmtPct1(med)}.` };
      }
      return { signal, label, reason: `Frete seller/GMV de ${fmtPct1(v)} — referência do canal indisponível.` };
    }
    case "sem_dado":
      return {
        signal, label,
        reason: row.data_warning ?? "Há métricas aplicáveis a este canal sem dado no período — a combinação não pode ser avaliada por completo.",
      };
    default:
      return { signal, label, reason: `Sinal sem explicação mapeada — ${INSUFFICIENT}` };
  }
}

/** Sinais que pedem atenção (na ordem de exibição) vs destaque positivo. */
const ATTENTION_SIGNALS = new Set(["custo_alto", "frete_alto", "ads_subutilizado", "sem_dado"]);
const POSITIVE_SIGNALS = new Set(["roas_forte"]);

/**
 * Constrói o diagnóstico humano de uma linha marca × canal a partir SOMENTE
 * dos sinais e valores já carregados pela página Canais. Nunca busca dado,
 * nunca recalcula sinal.
 */
export function buildChannelDiagnosis(
  row: CanaisChannelRow,
  median: CanaisChannelMedian | null,
): ChannelDiagnosis {
  const explanations = row.signals.map((s) => explainSignal(s, row, median));

  if (row.signals.length === 0) {
    return {
      headline: "Nenhum sinal de atenção do canal para esta combinação no período.",
      explanations: [],
      nextAction: null,
    };
  }

  const attention = row.signals.filter((s) => ATTENTION_SIGNALS.has(s)).map(signalLabel);
  const positive = row.signals.filter((s) => POSITIVE_SIGNALS.has(s)).map(signalLabel);
  const unknown = row.signals
    .filter((s) => !ATTENTION_SIGNALS.has(s) && !POSITIVE_SIGNALS.has(s))
    .map(signalLabel);

  const parts: string[] = [];
  if (attention.length > 0) parts.push(`Atenção no período: ${attention.join(", ").toLowerCase()}.`);
  if (positive.length > 0) parts.push(`Destaque: ${positive.join(", ")}.`);
  if (unknown.length > 0) parts.push(`Outros sinais: ${unknown.join(", ")}.`);
  const headline = parts.join(" ");

  // Próximo passo só quando há sinal de atenção a investigar — o destino já
  // existe (visão completa da marca); nada de rota ou métrica nova.
  const attentionSignals = row.signals.filter((s) => ATTENTION_SIGNALS.has(s));
  let nextAction: string | null = null;
  if (attentionSignals.length > 0) {
    nextAction = attentionSignals.every((s) => s === "sem_dado")
      ? "Antes de investigar a marca, confira a observação de cobertura — parte das métricas não tem dado no período."
      : "Continue na visão completa da marca para ver a evolução diária e os indicadores disponíveis da marca no período.";
  }

  return { headline, explanations, nextAction };
}
