"use client";

/**
 * Filtros da aba CX (05/11/2026): aplicam sozinhos ao trocar a marca ou o
 * periodo, sem o botao "Aplicar". A URL continua a fonte de verdade
 * (`hrefCx`), e sem JS o formulario GET + o botao do `<noscript>` seguem
 * funcionando.
 */
import { useRouter } from "next/navigation";
import { useTransition } from "react";

import { JANELAS_DIAS, hrefCx, lerFiltrosCx, type FiltrosCx as Filtros } from "@/lib/cx";
import { ALL_BRAND_KEYS, ALL_BRAND_OPTIONS } from "@/lib/filters/brands";

const CAMPO =
  "min-h-11 rounded-md border border-line bg-surface px-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-60";

export default function FiltrosCx({ f }: { f: Filtros }) {
  const router = useRouter();
  const [aplicando, iniciar] = useTransition();

  const aoMudar = (e: React.ChangeEvent<HTMLFormElement>) => {
    const dados = new FormData(e.currentTarget);
    const novo = lerFiltrosCx(
      { brands: String(dados.get("brands") ?? ""), dias: String(dados.get("dias") ?? "") },
      ALL_BRAND_KEYS,
    );
    iniciar(() => router.push(hrefCx(novo), { scroll: false }));
  };

  return (
    <form
      method="get"
      action="/cx"
      onChange={aoMudar}
      onSubmit={(e) => e.preventDefault()}
      aria-busy={aplicando}
      className="flex flex-wrap items-end gap-3"
      data-testid="cx-filtros"
    >
      <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
        Marca
        <select name="brands" defaultValue={f.marca ?? ""} className={CAMPO}>
          <option value="">Todas as marcas</option>
          {ALL_BRAND_OPTIONS.map((b) => (
            <option key={b.value} value={b.value}>
              {b.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
        Período
        <select name="dias" defaultValue={String(f.dias)} className={CAMPO}>
          {JANELAS_DIAS.map((d) => (
            <option key={d} value={d}>
              Últimos {d} dias
            </option>
          ))}
        </select>
      </label>
      {aplicando && <span className="pb-3 text-xs text-ink-faint" role="status">Atualizando…</span>}
      <noscript>
        <button
          type="submit"
          className="min-h-11 rounded-md border border-line-strong px-3 text-sm font-semibold text-ink hover:bg-raised"
        >
          Aplicar
        </button>
      </noscript>
    </form>
  );
}
