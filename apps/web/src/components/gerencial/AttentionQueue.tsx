"use client";

/**
 * Fila de atencao (Gate V2-1, Task J).
 *
 * Substitui integralmente o alerta hard-coded por marca que vivia no JSX da
 * Gerencial (`if (brand === "lescent")`) — regra de negocio embutida em markup.
 * Fonte unica: `/executive-summary`.
 *
 * Duas listas SEPARADAS por decisao estrutural do G1: risco comercial e aviso
 * de confianca no dado nunca compartilham lista, e um aviso de dado nunca
 * recebe severidade comercial "Critico" (o tipo `DataSeverity` nao tem esse
 * valor). Clicar abre a explicacao antes de navegar.
 */
import type {
  CommercialAttentionItem,
  CommercialSeverity,
  DataConfidenceItem,
  DataSeverity,
} from "@/lib/gerencial/attention";

interface Props {
  commercial: CommercialAttentionItem[];
  dataConfidence: DataConfidenceItem[];
  commercialLoading: boolean;
  commercialError: boolean;
  onOpenCommercial: (item: CommercialAttentionItem) => void;
  onOpenDataWarning: (item: DataConfidenceItem) => void;
  onRetry: () => void;
}

const SEVERITY_LABEL: Record<CommercialSeverity, string> = {
  critical: "Crítico",
  warning: "Atenção",
  info: "Informativo",
};

const SEVERITY_TONE: Record<CommercialSeverity, string> = {
  critical: "text-rose-700 bg-rose-50 border-rose-200",
  warning: "text-amber-700 bg-amber-50 border-amber-200",
  info: "text-slate-600 bg-slate-50 border-slate-200",
};

/** Escala PROPRIA dos avisos de dado — deliberadamente sem "Critico". */
const DATA_LABEL: Record<DataSeverity, string> = {
  attention: "Verificar",
  note: "Nota",
};

const DATA_TONE: Record<DataSeverity, string> = {
  attention: "text-amber-700 bg-amber-50 border-amber-200",
  note: "text-slate-600 bg-slate-50 border-slate-200",
};

export default function AttentionQueue({
  commercial,
  dataConfidence,
  commercialLoading,
  commercialError,
  onOpenCommercial,
  onOpenDataWarning,
  onRetry,
}: Props) {
  return (
    <section
      aria-labelledby="fila-heading"
      className="bg-white border border-violet-100 rounded-2xl shadow-sm p-4 flex flex-col gap-4"
    >
      <h2 id="fila-heading" className="text-sm font-semibold text-slate-700">
        Fila de atenção
      </h2>

      {/* Lista 1 — atencoes comerciais */}
      <div className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Atenções comerciais
          </h3>
          {!commercialLoading && !commercialError && (
            <span className="text-xs text-slate-500 tabular-nums">{commercial.length}</span>
          )}
        </div>

        {commercialLoading ? (
          <div className="flex flex-col gap-2 animate-pulse" role="status" aria-busy="true">
            <span className="sr-only">Carregando atenções comerciais…</span>
            <div className="h-10 w-full bg-violet-50 rounded-xl" />
            <div className="h-10 w-full bg-violet-50 rounded-xl" />
          </div>
        ) : commercialError ? (
          <div className="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 flex items-center justify-between gap-3 flex-wrap">
            <p className="text-xs text-slate-600">
              Resumo executivo indisponível — não é possível listar as atenções comerciais do período.
            </p>
            <button
              type="button"
              onClick={onRetry}
              className="text-xs font-semibold text-slate-700 border border-slate-300 rounded-lg px-3 min-h-11 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
            >
              Tentar novamente
            </button>
          </div>
        ) : commercial.length === 0 ? (
          <p className="text-xs text-slate-500">Sem atenções comerciais no período e filtros selecionados.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {commercial.map((item) => (
              <li key={item.key}>
                <button
                  type="button"
                  onClick={() => onOpenCommercial(item)}
                  aria-haspopup="dialog"
                  aria-label={`${item.title} — abrir explicação`}
                  className="w-full min-h-11 text-left flex items-start gap-2.5 rounded-xl px-2 -mx-2 py-2 hover:bg-violet-50/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 transition-colors"
                >
                  <span
                    className={`text-xs font-semibold uppercase rounded border px-1.5 py-0.5 shrink-0 mt-0.5 ${SEVERITY_TONE[item.severity]}`}
                  >
                    {SEVERITY_LABEL[item.severity]}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-xs font-semibold text-slate-800 leading-snug">{item.title}</span>
                    <span className="block text-xs text-slate-500 leading-snug">{item.description}</span>
                  </span>
                  <span aria-hidden="true" className="text-slate-300 shrink-0 mt-0.5">
                    →
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Lista 2 — confianca no dado, com estado proprio e escala propria */}
      <div className="flex flex-col gap-2 pt-3 border-t border-violet-100">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Confiança no dado
          </h3>
          {!commercialLoading && (
            <span className="text-xs text-slate-500 tabular-nums">{dataConfidence.length}</span>
          )}
        </div>

        {commercialLoading ? (
          <div className="h-10 w-full bg-violet-50 rounded-xl animate-pulse" role="status" aria-busy="true">
            <span className="sr-only">Carregando avisos de confiança…</span>
          </div>
        ) : dataConfidence.length === 0 ? (
          <p className="text-xs text-slate-500">
            {commercialError
              ? "Não foi possível verificar avisos de confiança nesta carga."
              : "Nenhum aviso de confiança no dado neste período."}
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {dataConfidence.map((item) => (
              <li key={item.key}>
                <button
                  type="button"
                  onClick={() => onOpenDataWarning(item)}
                  aria-haspopup="dialog"
                  aria-label={`${item.message} — abrir explicação`}
                  className="w-full min-h-11 text-left flex items-start gap-2.5 rounded-xl px-2 -mx-2 py-2 hover:bg-violet-50/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 transition-colors"
                >
                  <span
                    className={`text-xs font-semibold uppercase rounded border px-1.5 py-0.5 shrink-0 mt-0.5 ${DATA_TONE[item.severity]}`}
                  >
                    {DATA_LABEL[item.severity]}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-xs text-slate-700 leading-snug">{item.message}</span>
                    {(item.source || item.lastDate || item.stalenessDays != null) && (
                      <span className="block text-xs text-slate-500 tabular-nums">
                        {item.source && <>fonte {item.source}</>}
                        {item.lastDate && <> · até {item.lastDate}</>}
                        {item.stalenessDays != null && <> · defasagem {item.stalenessDays}d</>}
                      </span>
                    )}
                  </span>
                  <span aria-hidden="true" className="text-slate-300 shrink-0 mt-0.5">
                    →
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
