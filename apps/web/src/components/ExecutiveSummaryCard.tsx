import Link from "next/link";
import type { ExecutiveInsight, ExecutiveSummaryData } from "@/lib/api-client";
import { HEALTH_STATUS_LABEL, HEALTH_STATUS_TONE, SEVERITY_LABEL, SEVERITY_TONE, sortBySeverity } from "@/lib/executive-summary";

interface ExecutiveSummaryCardProps {
  data: ExecutiveSummaryData | null;
  loading: boolean;
}

// Bloco de sintese acima da tabela/trend da Gerencial (Gate 2 Fase 1 — ver
// docs/sections/gerencial_audit.md secao 11). Falha isolada: se o endpoint
// executive-summary falhar, `data` chega null e so este bloco mostra um
// aviso discreto — cards/tabela/trend tem fetch proprio e continuam
// funcionando normalmente.
export default function ExecutiveSummaryCard({ data, loading }: ExecutiveSummaryCardProps) {
  if (loading) {
    return (
      <div className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5 animate-pulse" aria-busy="true">
        <div className="h-3 w-40 bg-violet-100 rounded mb-3" />
        <div className="h-4 w-full bg-violet-50 rounded mb-2" />
        <div className="h-4 w-2/3 bg-violet-50 rounded" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="bg-slate-50 border border-slate-200 rounded-2xl p-4">
        <p className="text-xs text-slate-500">
          Resumo executivo indisponível no momento — os cards, a tabela e a tendência abaixo continuam com os dados do período.
        </p>
      </div>
    );
  }

  const { health, changes, risks, data_warnings } = data;
  const sortedRisks = sortBySeverity(risks);

  return (
    <section className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5 flex flex-col gap-5">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Resumo executivo</p>
          <p className="text-sm text-slate-700">{health.summary}</p>
        </div>
        <span className={`text-xs font-semibold rounded-lg px-3 py-1.5 shrink-0 ${HEALTH_STATUS_TONE[health.status]}`}>
          {HEALTH_STATUS_LABEL[health.status]}
        </span>
      </div>

      {data_warnings.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {data_warnings.map((w, i) => (
            <p key={i} className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5">
              {w.message}
            </p>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <InsightColumn title="O que mudou" items={changes} emptyText="Sem mudanças relevantes de marca no período." />
        <InsightColumn title="Atenções" items={sortedRisks} emptyText="Sem riscos identificados no período." />
      </div>
    </section>
  );
}

function InsightColumn({ title, items, emptyText }: { title: string; items: ExecutiveInsight[]; emptyText: string }) {
  return (
    <div className="flex flex-col gap-2 min-w-0">
      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">{title}</p>
      {items.length === 0 ? (
        <p className="text-xs text-slate-400">{emptyText}</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {items.map((item, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className={`text-[10px] font-semibold uppercase rounded px-1.5 py-0.5 shrink-0 mt-0.5 ${SEVERITY_TONE[item.severity]}`}>
                {SEVERITY_LABEL[item.severity]}
              </span>
              <div className="min-w-0">
                <Link href={item.href} className="text-sm font-medium text-violet-700 hover:underline">
                  {item.title}
                </Link>
                <p className="text-xs text-slate-500">{item.description}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
