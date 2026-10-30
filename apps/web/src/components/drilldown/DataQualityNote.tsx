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
    // POPUPS-1 — uma linha com marcador, sem caixa: a ressalva fica visivel
    // sem competir com o numero.
    <p className="flex items-start gap-2 text-xs leading-snug text-warn-ink">
      <span aria-hidden="true" className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-warn" />
      <span>{note}</span>
    </p>
  );
}
