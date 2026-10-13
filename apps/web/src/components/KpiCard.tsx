interface KpiCardProps {
  label: string;
  value: string;
  subvalue?: string;
  mom?: number | null;
  accent?: string;
  /** Quando presente, o card vira um `button` nativo acessivel (clique,
   * Enter, Espaco funcionam sem handler manual algum) que abre o detalhe do
   * indicador. Ausente (uso atual em outras telas) mantem o card como um
   * `div` nao interativo, exatamente como antes (Gate U2, Task 3). */
  onOpenDetail?: () => void;
  detailLabel?: string;
}

export default function KpiCard({
  label, value, subvalue, mom, accent = "bg-accent", onOpenDetail, detailLabel = "Ver detalhes",
}: KpiCardProps) {
  const momColor = mom == null ? "" : mom >= 0 ? "text-ok-ink" : "text-crit-ink";
  const momArrow = mom == null ? "" : mom >= 0 ? "▲" : "▼";
  const momAbs = mom == null ? null : Math.abs(mom).toFixed(1);

  const content = (
    <>
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full ${accent} shrink-0`} />
        <span className="text-xs font-semibold text-ink-muted uppercase tracking-wide">{label}</span>
      </div>
      <div>
        <p className="text-3xl font-bold text-ink leading-none tabular-nums">{value}</p>
        {subvalue && <p className="text-xs text-ink-faint mt-1">{subvalue}</p>}
      </div>
      {mom != null && (
        <p className={`text-xs font-semibold ${momColor}`}>
          {momArrow} {momAbs}% vs mês anterior
        </p>
      )}
      {onOpenDetail && (
        <p className="text-xs font-semibold text-accent">{detailLabel}</p>
      )}
    </>
  );

  if (!onOpenDetail) {
    return (
      <div className="bg-surface rounded-2xl shadow-sm border border-line p-5 flex flex-col gap-3">
        {content}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={onOpenDetail}
      aria-haspopup="dialog"
      className="bg-surface rounded-2xl shadow-sm border border-line p-5 flex flex-col gap-3 w-full text-left hover:shadow-md hover:border-line transition-shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1"
    >
      {content}
    </button>
  );
}
