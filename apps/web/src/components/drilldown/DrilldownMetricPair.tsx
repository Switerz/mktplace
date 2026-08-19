"use client";

interface Props {
  /** Rótulo da métrica principal (ex: "Ad spend na amostra"). */
  label: string;
  /** Valor já formatado pelo chamador — a semântica null/zero é de quem
   * conhece o contrato da métrica, nunca deste componente. */
  value: string;
  /** Referência NOMEADA opcional (ex: "Média das 4 marcas ML"). Sem nome não
   * é referência, é número solto — por isso o rótulo vem junto do valor. */
  referenceLabel?: string | null;
  referenceValue?: string | null;
}

/**
 * Métrica principal × referência nomeada (Gate G2, contrato §3 de
 * docs/DRILLDOWN_ARCHITECTURE.md, itens 4 e 5). Era o único componente de
 * composição previsto em §3.1 que nunca havia sido construído; o Gate V3-1A
 * é o primeiro consumidor real.
 *
 * Não exibe delta: no escopo do V3-1A a fotografia ML não tem período
 * anterior com que comparar, e inventar um delta seria inventar dado.
 */
export default function DrilldownMetricPair({ label, value, referenceLabel, referenceValue }: Props) {
  const hasReference = referenceLabel != null && referenceValue != null;
  return (
    <div className="flex items-end justify-between gap-4">
      <div className="min-w-0">
        <p className="text-xs text-slate-500">{label}</p>
        <p className="text-2xl font-bold text-slate-900 tabular-nums leading-tight">{value}</p>
      </div>
      {hasReference && (
        <div className="text-right shrink-0">
          <p className="text-xs text-slate-400">{referenceLabel}</p>
          <p className="text-sm font-semibold text-slate-600 tabular-nums">{referenceValue}</p>
        </div>
      )}
    </div>
  );
}
