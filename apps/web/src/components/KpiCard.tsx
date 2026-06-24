interface KpiCardProps {
  label: string;
  value: string;
  subvalue?: string;
  mom?: number | null;
  accent?: string;
}

export default function KpiCard({ label, value, subvalue, mom, accent = "bg-violet-600" }: KpiCardProps) {
  const momColor = mom == null ? "" : mom >= 0 ? "text-emerald-600" : "text-red-500";
  const momArrow = mom == null ? "" : mom >= 0 ? "▲" : "▼";
  const momAbs = mom == null ? null : Math.abs(mom).toFixed(1);

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full ${accent} shrink-0`} />
        <span className="text-xs font-semibold text-slate-600 uppercase tracking-wide">{label}</span>
      </div>
      <div>
        <p className="text-3xl font-bold text-gray-900 leading-none tabular-nums">{value}</p>
        {subvalue && <p className="text-xs text-slate-500 mt-1">{subvalue}</p>}
      </div>
      {mom != null && (
        <p className={`text-xs font-semibold ${momColor}`}>
          {momArrow} {momAbs}% vs mês anterior
        </p>
      )}
    </div>
  );
}
