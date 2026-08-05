"use client";

import { fmtRefreshedAt } from "@/lib/filters/format";

interface Props {
  /** Texto opcional antes do período (ex: categoria do insight). */
  leading?: string | null;
  periodLabel: string;
  /** null = não exibe "Atualizado em" — nunca inventa timestamp. */
  refreshedAt: string | null;
}

/**
 * Linha de contexto canônica dos conteúdos de drill-down (Gate G2, contrato
 * §3 de docs/DRILLDOWN_ARCHITECTURE.md): período + "Atualizado em" quando o
 * timestamp da resposta fresca existir. Substitui o markup repetido em
 * KpiDrilldownContent/ChannelComparisonDialogContent e preenche a lacuna do
 * InsightDrilldownContent (que não exibia refreshed_at).
 */
export default function DrilldownContextLine({ leading, periodLabel, refreshedAt }: Props) {
  return (
    <p className="text-xs text-slate-400">
      {leading && <>{leading} · </>}
      {periodLabel}
      {refreshedAt && <> · Atualizado em {fmtRefreshedAt(refreshedAt)}</>}
    </p>
  );
}
