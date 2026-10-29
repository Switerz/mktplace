"use client";

/**
 * T-0010 (revisao) — cabecalho ORDENAVEL de coluna numerica com o `?` da
 * metrica.
 *
 * No `SortHeader` (ui) o `rotulo` mora DENTRO do botao de ordenar: um `?` ali
 * seria botao dentro de botao (HTML invalido) e o clique nele subiria ate' o
 * `onClick` da ordenacao. Aqui o `?` e' IRMAO do botao de ordenar, no mesmo
 * `<th>` — o clique nele nunca chega a `aoOrdenar`. O `SortHeader` nao e'
 * tocado; este repete o visual dele (mesmo `aria-sort`, mesmas classes, alvo
 * de 44 px) e o `?` e' o mesmo `AjudaMetrica` dos cabecalhos da concentracao.
 *
 * So' alinhamento a' direita: e' para colunas de contagem e taxa.
 */
import type { ReactNode } from "react";

import type { ChaveMetrica } from "@/lib/expedicao-definicoes";
import type { SortState } from "@/lib/use-sortable-table";

import AjudaMetrica from "./AjudaMetrica";

export default function SortHeaderComAjuda({
  rotulo,
  coluna,
  sort,
  aoOrdenar,
  ajuda,
}: {
  rotulo: ReactNode;
  coluna: string;
  sort: SortState;
  aoOrdenar: (coluna: string) => void;
  ajuda: ChaveMetrica;
}) {
  const ativo = sort.column === coluna;
  return (
    <th
      scope="col"
      aria-sort={ativo ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
      className="p-0 text-right text-xs font-semibold uppercase tracking-wide"
    >
      <span className="flex items-center justify-end">
        {/* O botao ocupa o resto da celula (como no SortHeader); o rotulo
            fica encostado no `?` e nos numeros alinhados a' direita. */}
        <button
          type="button"
          onClick={() => aoOrdenar(coluna)}
          className={`flex min-h-11 flex-1 flex-row-reverse items-center justify-start gap-1 py-1.5 pl-3 transition-colors hover:text-ink ${
            ativo ? "text-accent" : "text-ink-faint"
          }`}
        >
          <span>{rotulo}</span>
          <span aria-hidden="true" className="text-xs leading-none">
            {ativo ? (sort.direction === "asc" ? "▲" : "▼") : "↕"}
          </span>
        </button>
        <AjudaMetrica chave={ajuda} />
      </span>
    </th>
  );
}
