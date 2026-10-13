"use client";

/**
 * DRE-UX-1 — filtros da DRE: competencia, canal, marca.
 *
 * Canal e marca sao os MESMOS controles das telas filtradas (mesmo alvo de
 * 44px, mesma regra "vazio = todas"). O periodo NAO e' o seletor de datas
 * global: a DRE so' aceita mes inteiro, e um intervalo de 30 dias faria a API
 * responder 422. Por isso um seletor de competencia proprio.
 *
 * Sem estado de filtro aqui: o valor chega da pagina (que le a URL) e a
 * mudanca volta para ela. Mesmo recolhivel do mobile que o
 * `GlobalFilterControls` usa — o resumo continua visivel fechado.
 */
import { useId, useMemo, useState } from "react";

import BrandFilter from "@/components/BrandFilter";
import MarketplaceFilter from "@/components/MarketplaceFilter";
import { brandLabel } from "@/lib/filters/brands";
import { isAllSelected } from "@/lib/marketplace-filter";
import { ROTULO_CANAL, mesesSelecionaveis, rotuloMes, type DreFiltros } from "@/lib/dre";

interface Props {
  filtros: DreFiltros;
  /** Competencia que a API escolheu quando a URL nao tem `ref_month`. */
  mesPadrao: string | null;
  onChange: (parcial: Partial<DreFiltros>) => void;
  carregando?: boolean;
}

function resumo(f: DreFiltros, mesPadrao: string | null): string {
  const mes = f.mes ? rotuloMes(f.mes) : mesPadrao ? `${rotuloMes(mesPadrao)} (madura)` : "Competência madura";
  const canais = isAllSelected(f.canais) ? "Todos os canais" : f.canais.map((c) => ROTULO_CANAL[c]).join(" + ");
  const marcas =
    f.marcas.length === 0 ? "Todas as marcas" : f.marcas.length === 1 ? brandLabel(f.marcas[0]) : `${f.marcas.length} marcas`;
  return `${mes} · ${canais} · ${marcas}`;
}

export default function DreFiltros({ filtros, mesPadrao, onChange, carregando = false }: Props) {
  const [aberto, setAberto] = useState(false);
  const id = useId();
  const idMes = useId();
  const meses = useMemo(() => mesesSelecionaveis(new Date(), 12, filtros.mes), [filtros.mes]);

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
          <span className="block truncate text-xs text-ink-muted">{resumo(filtros, mesPadrao)}</span>
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

      <div
        id={id}
        className={`${aberto ? "flex" : "hidden"} min-w-0 flex-wrap items-center gap-x-2 gap-y-1.5 pb-1 sm:flex sm:pb-0`}
      >
        <div className="flex min-h-11 items-center gap-2 rounded-lg border border-line bg-raised py-0.5 pl-3 pr-0.5">
          <label htmlFor={idMes} className="text-xs font-semibold text-ink-muted">
            Competência
          </label>
          <select
            id={idMes}
            value={filtros.mes ?? ""}
            onChange={(e) => onChange({ mes: e.target.value || null })}
            className="min-h-10 rounded-md border-0 bg-surface px-2 text-sm font-semibold text-ink ring-1 ring-inset ring-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <option value="">{mesPadrao ? `Madura · ${rotuloMes(mesPadrao)}` : "Madura mais recente"}</option>
            {meses.map((m) => (
              <option key={m} value={m}>
                {rotuloMes(m)}
              </option>
            ))}
          </select>
        </div>
        <MarketplaceFilter value={filtros.canais} onChange={(canais) => onChange({ canais })} />
        <BrandFilter value={filtros.marcas} onChange={(marcas) => onChange({ marcas })} variante="menu" />
        {carregando && (
          <span className="inline-flex min-h-11 shrink-0 items-center text-xs text-ink-faint animate-pulse motion-reduce:animate-none">
            Atualizando…
          </span>
        )}
      </div>
    </div>
  );
}
