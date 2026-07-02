import type { ParetoSummaryBucket } from "@/lib/api-client";
import { toggleBucketSelection } from "@/lib/produtos-tab-transition";

interface Props {
  buckets: ParetoSummaryBucket[] | null;
  loading: boolean;
  activeBucket: string | null;
  onSelectBucket: (bucket: string | null) => void;
  scopeNote?: string;
}

const BUCKET_STYLE: Record<string, { bg: string; border: string; text: string; bar: string; sub: string; ring: string }> = {
  A_top50: { bg: "bg-emerald-50", border: "border-emerald-200", text: "text-emerald-800", bar: "bg-emerald-500", sub: "text-emerald-600", ring: "ring-emerald-400" },
  B_next30: { bg: "bg-cyan-50", border: "border-cyan-200", text: "text-cyan-800", bar: "bg-cyan-500", sub: "text-cyan-600", ring: "ring-cyan-400" },
  C_next15: { bg: "bg-amber-50", border: "border-amber-200", text: "text-amber-800", bar: "bg-amber-500", sub: "text-amber-600", ring: "ring-amber-400" },
  D_tail: { bg: "bg-rose-50", border: "border-rose-200", text: "text-rose-800", bar: "bg-rose-500", sub: "text-rose-600", ring: "ring-rose-400" },
};

function fmtGmvShort(v: number): string {
  if (v >= 1_000_000) return `R$ ${(v / 1_000_000).toFixed(1)}M`;
  return `R$ ${(v / 1_000).toFixed(0)}K`;
}

/**
 * Cards A/B/C/D compartilhados pelas 3 abas de Produtos. Cada card e um
 * filtro clicavel (toggle): clicar de novo no bucket ja ativo remove o
 * filtro (equivalente a "Todos"). Os percentuais reais podem variar em
 * torno de 50/30/15/5 por causa do produto que cruza a fronteira — isso e
 * esperado, nao um bug.
 */
export default function ProductParetoSummary({ buckets, loading, activeBucket, onSelectBucket, scopeNote }: Props) {
  if (loading && !buckets) {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 px-6 py-4 border-b border-violet-50">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 h-[104px] animate-pulse" />
        ))}
      </div>
    );
  }

  if (!buckets || buckets.length === 0) return null;

  return (
    <div className="border-b border-violet-50">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 px-6 py-4">
        {buckets.map((b) => {
          const c = BUCKET_STYLE[b.bucket] ?? BUCKET_STYLE.D_tail;
          const active = activeBucket === b.bucket;
          return (
            <button
              key={b.bucket}
              type="button"
              aria-pressed={active}
              onClick={() => onSelectBucket(toggleBucketSelection(activeBucket, b.bucket))}
              className={`text-left rounded-xl border px-4 py-3 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 ${c.bg} ${c.border} ${
                active ? `ring-2 ${c.ring}` : ""
              }`}
            >
              <div className="flex items-baseline justify-between mb-2">
                <span className={`text-lg font-bold tabular-nums ${c.text}`}>{b.label}</span>
                <span className={`text-xs font-semibold tabular-nums ${c.sub}`}>{b.gmv_pct.toFixed(1)}%</span>
              </div>
              <div className="h-1 rounded-full bg-white/60 overflow-hidden mb-2">
                <div className={`h-1 rounded-full ${c.bar}`} style={{ width: `${Math.min(100, b.gmv_pct)}%` }} />
              </div>
              <p className={`text-xs font-semibold tabular-nums ${c.text}`}>{fmtGmvShort(b.gmv)}</p>
              <p className={`text-[11px] tabular-nums mt-0.5 ${c.sub}`}>{b.count} produtos · {b.description}</p>
            </button>
          );
        })}
      </div>
      {scopeNote && (
        <p className="px-6 pb-3 text-[11px] text-slate-400">{scopeNote}</p>
      )}
    </div>
  );
}
