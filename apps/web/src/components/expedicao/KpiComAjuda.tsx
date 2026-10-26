"use client";

/**
 * T-0010 — o `KpiTile` da Expedicao com o `?` da metrica.
 *
 * O `KpiTile` (ui) nao e' editado: o `?` entra no fim da linha de comparacao,
 * que e' `ReactNode`. Assim ele acompanha o texto (nao cobre rotulo nem o
 * ponto de severidade) e funciona igual com ou sem comparacao.
 */
import type { ComponentProps } from "react";

import KpiTile from "@/components/ui/KpiTile";
import type { ChaveMetrica } from "@/lib/expedicao-definicoes";

import AjudaMetrica from "./AjudaMetrica";

/** Reexportado para quem so' precisa do `?` (titulo de painel, cabecalho). */
export { AjudaMetrica as Ajuda };

export function KpiComAjuda({
  ajuda,
  comparacao,
  ...props
}: ComponentProps<typeof KpiTile> & { ajuda: ChaveMetrica | null }) {
  return (
    <KpiTile
      {...props}
      comparacao={
        ajuda ? (
          <>
            {comparacao}
            {comparacao ? " " : null}
            <AjudaMetrica chave={ajuda} />
          </>
        ) : (
          comparacao
        )
      }
    />
  );
}

export default KpiComAjuda;
