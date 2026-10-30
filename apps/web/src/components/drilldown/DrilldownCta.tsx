"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";

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
    // POPUPS-1 — a saida do detalhe e' um botao de linha inteira, com a seta
    // que avanca no hover; o "→" do texto do chamador vira o icone.
    <Link
      href={href}
      aria-label={ariaLabel}
      className="group mt-1 flex min-h-11 items-center justify-between gap-3 rounded-xl border border-line px-4 text-sm font-semibold text-accent transition-colors hover:border-accent/40 hover:bg-accent-soft/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <span>{typeof children === "string" ? children.replace(/\s*→\s*$/, "") : children}</span>
      <ArrowRight aria-hidden="true" className="h-4 w-4 shrink-0 transition-transform duration-200 group-hover:translate-x-0.5" />
    </Link>
  );
}
