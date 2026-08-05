"use client";

interface Props {
  /** Texto da observação — null/vazio não renderiza nada (nunca uma caixa vazia). */
  note: string | null | undefined;
}

/**
 * Aviso âmbar canônico de qualidade/limitação do dado (Gate G2, contrato §3
 * de docs/DRILLDOWN_ARCHITECTURE.md). Unifica o markup repetido em
 * KpiDrilldownContent (caveat), ChannelComparisonDialogContent
 * (data_warning) e InsightDrilldownContent (confidence_note) — mesma cor,
 * mesma borda, mesma tipografia nos três gêneros de detalhe.
 */
export default function DataQualityNote({ note }: Props) {
  if (!note) return null;
  return (
    <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5">
      {note}
    </p>
  );
}
