"use client";

/**
 * Cabecalho compacto + barra de filtros sticky da Gerencial V2 (Gate V2-1,
 * Task C).
 *
 * Os controles de filtro chegam como `children`: a fonte de verdade continua
 * sendo a URL via `useGlobalFilters`, na pagina — este componente nao guarda
 * nenhum estado paralelo de filtro.
 *
 * `scroll-margin-top` nas ancoras da pagina resolve o unico risco real da barra
 * sticky: navegar por ancora e o titulo ficar escondido atras dela.
 */
import LiveStatusBadge from "@/components/LiveStatusBadge";
import { fmtRefreshedAt } from "@/lib/filters/format";

interface Props {
  periodLabel: string;
  refreshedAt: string | null;
  /** `null` enquanto nenhuma fonte principal esta fresca — nunca afirma live. */
  live: boolean | null;
  loading: boolean;
  children: React.ReactNode;
}

export default function GerencialHeader({ periodLabel, refreshedAt, live, loading, children }: Props) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-xl font-bold text-gray-900">Visão Gerencial</h1>
          <p className="text-sm text-slate-500">Como estamos, onde investigar e para onde ir a seguir.</p>
          <p className="text-xs text-slate-500 mt-0.5">
            Período: {periodLabel}
            {/* refreshed_at e badge saem SO' da resposta compativel com o filtro
                atual — nunca da requisicao anterior. */}
            {live != null && refreshedAt && <> · Atualizado em {fmtRefreshedAt(refreshedAt)}</>}
          </p>
        </div>
        {live != null ? (
          <LiveStatusBadge live={live} />
        ) : loading ? (
          <span className="text-xs text-slate-500 bg-slate-100 border border-slate-200 rounded-lg px-3 py-1.5 font-medium">
            Atualizando dados…
          </span>
        ) : null}
      </div>

      <div className="sticky top-0 z-30 -mx-6 px-6 py-2 bg-[#f8f7ff]/95 backdrop-blur supports-[backdrop-filter]:bg-[#f8f7ff]/80 border-b border-violet-100/70">
        <div className="flex items-start justify-between gap-3 flex-wrap">{children}</div>
      </div>
    </div>
  );
}
