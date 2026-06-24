export function SkeletonKpiCard() {
  return (
    <div className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5 flex flex-col gap-3 animate-pulse">
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-slate-200 shrink-0" />
        <div className="h-3 bg-slate-100 rounded w-24" />
      </div>
      <div>
        <div className="h-8 bg-slate-200 rounded w-28 mb-2" />
        <div className="h-3 bg-slate-100 rounded w-36" />
      </div>
    </div>
  );
}

export function SkeletonTableRows({ rows = 4, cols = 6 }: { rows?: number; cols?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, i) => (
        <tr key={i} className="border-t border-slate-50 animate-pulse">
          <td className="px-6 py-4">
            <div className="h-4 bg-slate-100 rounded w-20" />
          </td>
          {Array.from({ length: cols - 1 }).map((_, j) => (
            <td key={j} className="px-4 py-4">
              <div className="h-4 bg-slate-100 rounded w-full" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
