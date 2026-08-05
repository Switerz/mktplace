"use client";

import Link from "next/link";

interface Props {
  /** Href final — o chamador já deve ter aplicado buildHref/mergeFilteredHref
   * (preservação de filtros é responsabilidade de quem conhece a página). */
  href: string;
  children: React.ReactNode;
  /** Nome acessível opcional quando o texto visível não identifica o destino. */
  ariaLabel?: string;
}

/**
 * CTA final canônico dos conteúdos de drill-down (Gate G2, contrato §3 de
 * docs/DRILLDOWN_ARCHITECTURE.md) — "próximo passo" com filtros preservados.
 * Unifica o link repetido em KpiDrilldownContent, InsightDrilldownContent e
 * ChannelComparisonDialogContent, e padroniza o alvo de toque ≥44px
 * (min-h-11) que antes só o Insight tinha.
 */
export default function DrilldownCta({ href, children, ariaLabel }: Props) {
  return (
    <Link
      href={href}
      aria-label={ariaLabel}
      className="inline-flex items-center min-h-11 text-sm font-semibold text-violet-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded self-start"
    >
      {children}
    </Link>
  );
}
