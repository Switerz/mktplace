"use client";

/**
 * "O que pede decisao" (GERENCIAL-METAS-1): no maximo tres sinais comerciais,
 * por severidade, logo abaixo dos numeros. A fila completa continua inteira em
 * "Mais detalhes"; aqui fica so' o que merece a primeira olhada.
 *
 * O aviso de dado nao entra aqui: ele ja' aparece colado no GMV, no bloco
 * principal, que e' onde muda a leitura.
 */
import type { CommercialAttentionItem } from "@/lib/gerencial/attention";

const PELE: Record<CommercialAttentionItem["severity"], { borda: string; tinta: string; rotulo: string }> = {
  critical: { borda: "border-t-crit", tinta: "text-crit-ink", rotulo: "Crítico" },
  warning: { borda: "border-t-warn", tinta: "text-warn-ink", rotulo: "Atenção" },
  info: { borda: "border-t-accent", tinta: "text-accent-ink", rotulo: "Informativo" },
};

interface Props {
  itens: CommercialAttentionItem[];
  total: number;
  loading: boolean;
  error: boolean;
  onOpen: (item: CommercialAttentionItem) => void;
  onVerTodos: () => void;
}

export default function DecisoesTopo({ itens, total, loading, error, onOpen, onVerTodos }: Props) {
  if (error || (!loading && itens.length === 0)) return null;
  return (
    <section aria-labelledby="decisoes-titulo" className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id="decisoes-titulo" className="text-sm font-semibold text-ink">
          O que pede decisão
        </h2>
        {!loading && (
          <span className="text-xs text-ink-faint tabular-nums">
            {itens.length} de {total} {total === 1 ? "sinal" : "sinais"}, por severidade
          </span>
        )}
        {total > itens.length && (
          <button
            type="button"
            onClick={onVerTodos}
            className="ml-auto inline-flex min-h-11 items-center text-xs font-semibold text-accent-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            Ver todos os sinais →
          </button>
        )}
      </div>
      {loading && itens.length === 0 ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-32 animate-pulse rounded-xl bg-raised motion-reduce:animate-none" />
          ))}
        </div>
      ) : (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {itens.map((item) => {
            const p = PELE[item.severity];
            return (
              <li key={item.key} className={`flex flex-col gap-1.5 rounded-xl border border-line border-t-[3px] bg-surface p-4 ${p.borda}`}>
                <span className={`text-xs font-bold uppercase tracking-wide ${p.tinta}`}>{p.rotulo}</span>
                <h3 className="text-sm font-semibold leading-snug text-ink">{item.title}</h3>
                {item.description && <p className="line-clamp-3 text-xs leading-relaxed text-ink-muted">{item.description}</p>}
                <button
                  type="button"
                  aria-haspopup="dialog"
                  onClick={() => onOpen(item)}
                  className="mt-auto inline-flex min-h-11 w-fit items-center text-xs font-semibold text-accent-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  Ver detalhe →
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
