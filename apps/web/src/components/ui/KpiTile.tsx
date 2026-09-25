import type { ReactNode } from "react";

import { TOM_PONTO, TOM_SUPERFICIE, TOM_TINTA, type Tom } from "./tone";

/**
 * UX-TORRE-1 — celula da faixa de KPIs.
 *
 * Quatro campos, sempre na mesma ordem: rotulo curto, valor, comparacao/meta,
 * estado. A explicacao longa vai para `contexto`, que vira `title` — o texto
 * continua existindo, mas para de ocupar a primeira dobra.
 *
 * `destaque` muda SO' a escala do numero. Duas escalas de destaque na mesma
 * faixa fariam o operador procurar a hierarquia em vez de ler o numero.
 */
export default function KpiTile({
  rotulo,
  valor,
  comparacao,
  contexto,
  tom = "neutro",
  destaque = false,
}: {
  rotulo: string;
  valor: ReactNode;
  /** Meta, delta ou denominador. Uma linha. */
  comparacao?: ReactNode;
  /** Metodologia/ressalva — vai para o tooltip, nao para a tela. */
  contexto?: string;
  tom?: Tom;
  destaque?: boolean;
}) {
  return (
    <div
      title={contexto}
      className={`flex min-w-0 flex-col justify-between rounded-lg border px-3.5 py-3 ${TOM_SUPERFICIE[tom]}`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-ink-faint">
          {rotulo}
        </p>
        {tom !== "neutro" && (
          <span
            aria-hidden="true"
            className={`mt-1 h-2 w-2 shrink-0 rounded-full ${TOM_PONTO[tom]}`}
          />
        )}
      </div>
      <p
        className={`mt-1.5 font-semibold leading-none tabular-nums ${
          destaque ? "text-[28px]" : "text-xl"
        } ${TOM_TINTA[tom]}`}
      >
        {valor}
      </p>
      {comparacao && (
        <p className="mt-1.5 text-xs leading-snug text-ink-muted">{comparacao}</p>
      )}
    </div>
  );
}
