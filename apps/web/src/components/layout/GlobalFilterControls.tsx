"use client";

/**
 * UX-TORRE-2 — os tres filtros globais (canal, marca, periodo) montados do
 * MESMO jeito em Gerencial, Canais e Financeiro.
 *
 * Antes cada tela montava os tres controles a mao, com wrappers e
 * espacamentos proprios. Aqui so' a COMPOSICAO e' compartilhada: a fonte de
 * verdade continua sendo a URL via `useGlobalFilters`, na pagina — este
 * componente recebe o valor e devolve a mudanca, sem estado de filtro proprio.
 *
 * MOBILE: a faixa com tres grupos de alvos de 44px ocupava ~40% de uma tela de
 * 844px, e por ser sticky ficava ali durante toda a rolagem. Abaixo de `sm` os
 * controles recolhem atras de um botao que mostra o RESUMO do escopo — o leitor
 * continua sabendo o que esta lendo sem abrir nada. A partir de `sm` os
 * controles ficam sempre visiveis e o botao some.
 *
 * O unico estado local e' "aberto/fechado" do recolhivel, que e' apresentacao
 * e nao filtro.
 */
import { useId, useState } from "react";

import BrandFilter from "@/components/BrandFilter";
import DateRangeFilter from "@/components/DateRangeFilter";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import { resumoDosFiltros } from "@/lib/filters/summary";
import type { GlobalFilters } from "@/lib/filters/types";

interface Props {
  filters: GlobalFilters;
  onChange: (partial: Partial<GlobalFilters>) => void;
  /** Tela cuja fonte nao calcula delta por periodo (ver DateRangeFilter). */
  hideCompare?: boolean;
  /** Indicador discreto de requisicao em voo, junto dos controles. */
  loading?: boolean;
}

export default function GlobalFilterControls({ filters, onChange, hideCompare = false, loading = false }: Props) {
  const [aberto, setAberto] = useState(false);
  const id = useId();
  const resumo = resumoDosFiltros(filters, { semComparacao: hideCompare });

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1.5">
      <button
        type="button"
        aria-expanded={aberto}
        aria-controls={id}
        onClick={() => setAberto((v) => !v)}
        className="inline-flex min-h-11 w-full items-center justify-between gap-2 rounded-lg border border-line bg-surface px-3 text-left text-sm sm:hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <span className="min-w-0">
          <span className="block text-xs font-semibold text-ink">Filtros</span>
          <span className="block truncate text-xs text-ink-muted">{resumo}</span>
        </span>
        <svg
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          aria-hidden="true"
          className={`h-4 w-4 shrink-0 text-ink-faint transition-transform ${aberto ? "rotate-180" : ""}`}
        >
          <path d="M3.5 6 8 10.5 12.5 6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {/* UX-TORRE-B2B-1: uma linha so' no desktop. Canal fica segmentado
          (quatro opcoes, com a cor de identidade); marca e periodo viram
          gatilhos que mostram o valor atual — os mesmos botoes, dentro de um
          painel. A barra passou de duas linhas (~100px) para uma (~52px). */}
      <div
        id={id}
        className={`${aberto ? "flex" : "hidden"} min-w-0 flex-wrap items-start gap-x-2 gap-y-1.5 pb-1 sm:flex sm:pb-0`}
      >
        <MarketplaceFilter value={filters.channels} onChange={(channels) => onChange({ channels })} />
        <BrandFilter value={filters.brands} onChange={(brands) => onChange({ brands })} variante="menu" />
        <DateRangeFilter
          dateFrom={filters.dateFrom}
          dateTo={filters.dateTo}
          compare={filters.compare}
          onChange={(v) => onChange(v)}
          onCompareChange={(compare) => onChange({ compare })}
          hideCompare={hideCompare}
          variante="menu"
        />
        {loading && (
          <span className="inline-flex min-h-11 shrink-0 items-center text-xs text-ink-faint animate-pulse">
            Atualizando…
          </span>
        )}
      </div>
    </div>
  );
}
