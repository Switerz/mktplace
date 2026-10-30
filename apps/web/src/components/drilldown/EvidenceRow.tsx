"use client";

import { ArrowDownRight, ArrowUpRight } from "lucide-react";

/**
 * Linha de evidencia de um detalhe: rotulo a esquerda, valor a direita.
 *
 * POPUPS-1 — a linha passa a carregar o que da' valor a leitura, nao texto:
 *  - `pct` desenha a barra de participacao (0–100) sob a linha;
 *  - `ponto` e' a cor de identidade (canal) antes do rotulo;
 *  - `delta` e' a variacao contra o periodo anterior, em pilula;
 *  - `barra` pinta a barra com a cor do canal em vez do destaque.
 * Sem esses campos, a linha e' a mesma de antes.
 */
export type EvidenceTone = "value" | "muted" | "warning";

const TONE_CLASS: Record<EvidenceTone, string> = {
  value: "text-ink font-semibold",
  muted: "text-ink-faint",
  warning: "text-warn-ink",
};

interface Props {
  label: string;
  value: string;
  tone?: EvidenceTone;
  reference?: string | null;
  title?: string;
  mutedLabel?: boolean;
  /** Participacao 0–100 — desenha a barra sob a linha. */
  pct?: number | null;
  /** Classe de cor do ponto de identidade (ex.: `bg-canal-ml`). */
  ponto?: string;
  /** Classe de cor da barra; padrao e' o destaque. */
  barra?: string;
  /** Variacao % contra o periodo anterior. `neutro` = sem cor de juizo. */
  delta?: { pct: number; neutro?: boolean } | null;
}

function fmtDelta(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
}

export default function EvidenceRow({
  label,
  value,
  tone = "value",
  reference,
  title,
  mutedLabel,
  pct,
  ponto,
  barra = "bg-accent",
  delta,
}: Props) {
  const sobe = delta != null && delta.pct >= 0;
  const Seta = sobe ? ArrowUpRight : ArrowDownRight;
  const peleDelta = delta?.neutro ? "bg-raised text-ink-muted" : sobe ? "bg-ok-soft text-ok-ink" : "bg-crit-soft text-crit-ink";
  return (
    <li className="flex flex-col gap-1 py-1">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className={`flex min-w-0 items-center gap-2 ${mutedLabel ? "text-ink-faint" : "text-ink-muted"}`}>
          {ponto && <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${ponto}`} />}
          <span className="truncate" title={label}>{label}</span>
        </span>
        <span className="flex shrink-0 items-center gap-2 text-right tabular-nums">
          {delta != null && (
            <span className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-xs font-semibold ${peleDelta}`}>
              <Seta aria-hidden="true" className="h-3 w-3" strokeWidth={2.25} />
              {fmtDelta(delta.pct)}
            </span>
          )}
          <span className={TONE_CLASS[tone]} title={title}>{value}</span>
        </span>
      </div>
      {pct != null && (
        <span aria-hidden="true" className="block h-1.5 w-full overflow-hidden rounded-full bg-raised">
          <span className={`tc-barra block h-full rounded-full ${barra}`} style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
        </span>
      )}
      {reference && <span className="text-xs text-ink-faint">{reference}</span>}
    </li>
  );
}
