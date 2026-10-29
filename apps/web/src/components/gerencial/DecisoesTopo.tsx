"use client";

/**
 * "O que pede decisao" (GERENCIAL-METAS-1) — UI-DENSIDADE-1: LISTA COMPACTA,
 * uma linha por sinal (escolha do proprietario). Cartoes altos e iguais davam
 * o mesmo peso a tudo; a linha mostra o sinal, onde (marca e canal, ja' no
 * titulo do contrato) e o numero que importa, e abre o detalhe no clique.
 *
 * O aviso de dado nao entra aqui: ele aparece colado no GMV, no bloco
 * principal, que e' onde muda a leitura.
 */
import { ChevronRight } from "lucide-react";

import type { CommercialAttentionItem } from "@/lib/gerencial/attention";
import { fmtPctBr } from "@/lib/formatters";

const PONTO: Record<CommercialAttentionItem["severity"], { cor: string; rotulo: string }> = {
  critical: { cor: "bg-crit", rotulo: "Crítico" },
  warning: { cor: "bg-warn", rotulo: "Atenção" },
  info: { cor: "bg-accent", rotulo: "Informativo" },
};

/**
 * O numero que importa no sinal: variacao para alta/queda; percentual medido
 * para sinal de custo/eficiencia. Sem numero confiavel, nada (nunca inventa).
 */
export function numeroDoSinal(item: Pick<CommercialAttentionItem, "type" | "deltaPct" | "metricValue">): string | null {
  if ((item.type === "growth" || item.type === "drop") && item.deltaPct != null) return fmtPctBr(item.deltaPct);
  if (item.metricValue != null && /cost|cust|frete|fee/i.test(item.type)) {
    return `${item.metricValue.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}% do GMV`;
  }
  return null;
}

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
    <section aria-labelledby="decisoes-titulo" className="rounded-xl border border-line bg-surface">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-1.5">
        <h2 id="decisoes-titulo" className="text-sm font-semibold text-ink">
          O que pede decisão
        </h2>
        {!loading && <span className="text-xs text-ink-faint tabular-nums">{itens.length} de {total}</span>}
        {total > itens.length && (
          <button
            type="button"
            onClick={onVerTodos}
            className="ml-auto inline-flex min-h-11 items-center text-xs font-semibold text-accent-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            Ver todos →
          </button>
        )}
      </div>
      {loading && itens.length === 0 ? (
        <div className="flex flex-col gap-2 p-3" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-9 animate-pulse rounded-lg bg-raised motion-reduce:animate-none" />
          ))}
        </div>
      ) : (
        <ul className="divide-y divide-line">
          {itens.map((item) => {
            const p = PONTO[item.severity];
            const numero = numeroDoSinal(item);
            return (
              <li key={item.key}>
                <button
                  type="button"
                  aria-haspopup="dialog"
                  onClick={() => onOpen(item)}
                  className="group flex min-h-11 w-full items-center gap-3 px-4 py-2 text-left transition-colors duration-200 hover:bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
                >
                  <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${p.cor}`} />
                  <span className="sr-only">{p.rotulo}:</span>
                  <span className="min-w-0 flex-1 truncate text-sm text-ink">{item.title}</span>
                  {numero && <span className="shrink-0 text-sm font-semibold text-ink tabular-nums">{numero}</span>}
                  <ChevronRight
                    aria-hidden="true"
                    className="h-4 w-4 shrink-0 text-ink-faint transition-transform duration-200 group-hover:translate-x-0.5 motion-reduce:transition-none"
                  />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
