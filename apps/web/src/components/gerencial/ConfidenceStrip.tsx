"use client";

/**
 * Faixa de confianca no dado (Gate V2-1, Task C; semantica corrigida na rodada
 * consolidada, Task F).
 *
 * Decisao do V2-0: confianca **NAO e' KPI**. Ela ocupa uma faixa horizontal
 * compacta entre os filtros e a faixa de KPIs, clicavel, que abre o drill-down.
 *
 * O que a faixa afirma agora e' **disponibilidade de serie**, derivada dos
 * estados das series por canal ja carregadas — nao "cobertura" inferida de
 * `gmv != null`. Um canal com serie de valores zero e' *disponivel*; um canal
 * sem nenhuma linha e' *sem registros*; um canal cuja fonte falhou e'
 * *indisponivel*. Os tres estados sao distintos e nomeados.
 *
 * Falha do executive-summary NAO transforma a faixa inteira em erro: a
 * disponibilidade das series continua valendo, e apenas defasagem/avisos passam
 * a constar como "não verificados".
 */
import type { ConfidenceStripData } from "@/lib/gerencial/attention";

interface Props {
  data: ConfidenceStripData;
  /** Nenhuma serie concluiu ainda — a faixa nao exibe numero anterior algum. */
  loading: boolean;
  onOpen: () => void;
}

export default function ConfidenceStrip({ data, loading, onOpen }: Props) {
  if (loading) {
    return (
      <div
        className="bg-white border border-violet-100 rounded-2xl px-4 py-2.5 flex items-center gap-2"
        role="status"
        aria-busy="true"
      >
        <span aria-hidden="true" className="w-1.5 h-1.5 rounded-full bg-slate-300 animate-pulse" />
        <span className="text-xs text-slate-500">Verificando disponibilidade das séries…</span>
      </div>
    );
  }

  const { selectedCount, availableCount, noRecordsCount, unavailableCount } = data;
  const allAvailable = availableCount === selectedCount && selectedCount > 0;
  const dotTone = unavailableCount > 0 ? "bg-amber-500" : allAvailable ? "bg-emerald-500" : "bg-slate-400";

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-haspopup="dialog"
      aria-label="Confiança no dado — abrir disponibilidade das séries, defasagem e avisos"
      className="w-full min-h-11 text-left bg-white border border-violet-100 rounded-2xl px-4 py-2.5 flex items-center gap-x-3 gap-y-1 flex-wrap hover:bg-violet-50/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 transition-colors"
    >
      <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        Confiança no dado
      </span>

      <span className="flex items-center gap-1.5 text-xs">
        <span aria-hidden="true" className={`w-1.5 h-1.5 rounded-full ${dotTone}`} />
        <span className="text-slate-600 tabular-nums">
          Série disponível em {availableCount} de {selectedCount}{" "}
          {selectedCount === 1 ? "canal" : "canais"}
        </span>
      </span>

      {noRecordsCount > 0 && (
        <span className="text-xs text-slate-600 tabular-nums">
          {noRecordsCount} sem registros no período
        </span>
      )}
      {unavailableCount > 0 && (
        <span className="text-xs text-amber-800 tabular-nums">
          {unavailableCount} {unavailableCount === 1 ? "indisponível" : "indisponíveis"}
        </span>
      )}

      {/* Defasagem e avisos vem do executive-summary. Se ele nao respondeu, o
          texto diz que NAO foi verificado — nunca "sem avisos". */}
      {data.warningsChecked ? (
        <>
          {data.maxStalenessDays != null && (
            <span className="text-xs text-slate-600 tabular-nums">
              defasagem máxima {data.maxStalenessDays}d
            </span>
          )}
          <span className={`text-xs tabular-nums ${data.warningCount > 0 ? "text-amber-800" : "text-slate-500"}`}>
            {data.warningCount > 0
              ? `${data.warningCount} aviso${data.warningCount > 1 ? "s" : ""} de dado`
              : "sem avisos de dado"}
          </span>
        </>
      ) : (
        <span className="text-xs text-slate-500">defasagem e avisos não verificados</span>
      )}

      <span aria-hidden="true" className="ml-auto text-xs font-semibold text-violet-700">
        Detalhar
      </span>
    </button>
  );
}
