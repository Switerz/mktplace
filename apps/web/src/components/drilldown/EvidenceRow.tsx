"use client";

export type EvidenceTone = "value" | "muted" | "warning";

const TONE_CLASS: Record<EvidenceTone, string> = {
  value: "text-slate-800 font-semibold",
  muted: "text-slate-400",
  warning: "text-amber-700",
};

interface Props {
  label: string;
  /** Texto do valor já formatado pelo chamador ("R$ 1.234,56 · 12,3%",
   * "Sem dado", "—") — a semântica null/zero/indisponível é decidida por
   * quem conhece o contrato da métrica, nunca aqui. */
  value: string;
  tone?: EvidenceTone;
  /** Sub-linha de referência opcional (ex: "Mediana do canal: 4,1%"). */
  reference?: string | null;
  /** Tooltip opcional (ex: data_warning da métrica). */
  title?: string;
  /** Rebaixa o rótulo para o tom de referência (linhas informativas, ex: p75). */
  mutedLabel?: boolean;
}

/**
 * Linha de evidência canônica dos conteúdos de drill-down (Gate G2, contrato
 * §3 de docs/DRILLDOWN_ARCHITECTURE.md): rótulo à esquerda, valor tabular à
 * direita e referência nomeada opcional na sub-linha. Unifica o MetricRow de
 * ChannelComparisonDialogContent e as listas de decomposição de
 * KpiDrilldownContent.
 */
export default function EvidenceRow({ label, value, tone = "value", reference, title, mutedLabel }: Props) {
  return (
    <li className="flex items-center justify-between gap-3 text-xs">
      <span className={mutedLabel ? "text-slate-400" : "text-slate-500"}>{label}</span>
      <span className="text-right tabular-nums">
        <span className={TONE_CLASS[tone]} title={title}>{value}</span>
        {reference && <span className="block text-[10px] text-slate-400">{reference}</span>}
      </span>
    </li>
  );
}
