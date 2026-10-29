"use client";

/**
 * T-0010 — o `KpiTile` da Expedicao com o "ⓘ" da metrica.
 *
 * O `KpiTile` (ui) nao e' editado: o "ⓘ" entra no fim da linha de comparacao,
 * que e' `ReactNode`. Assim ele acompanha o texto (nao cobre rotulo nem o
 * ponto de severidade) e funciona igual com ou sem comparacao.
 */
import type { ComponentProps } from "react";

import KpiTile from "@/components/ui/KpiTile";
import type { ChaveMetrica } from "@/lib/expedicao-definicoes";
import { TEXTO_INDISPONIVEL, partesDoPercentualComBase } from "@/lib/expedicao-formato";

import AjudaMetrica from "./AjudaMetrica";

/** Reexportado para quem so' precisa do "ⓘ" (titulo de painel, cabecalho). */
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

/**
 * QA T-0010 (I2) — taxa no formato unico "3,35% · 199 de 5.940", com a taxa
 * PUBLICADA pela API (a tela nao divide de novo). `linha`: a base vai para
 * uma linha secundaria (tabelas); sem ela, fica ao lado, menor (KPI).
 */
export function TaxaComBase({
  numerador,
  base,
  taxa,
  casas = 2,
  linha = false,
}: {
  numerador: number | null | undefined;
  base: number | null | undefined;
  taxa: number | null | undefined;
  casas?: number;
  linha?: boolean;
}) {
  const partes = partesDoPercentualComBase(numerador, base, { taxa: taxa ?? null, casas });
  if (!partes) return <span className="text-ink-faint">{TEXTO_INDISPONIVEL}</span>;
  return (
    <>
      <span>{partes.percentual}</span>
      {linha ? (
        <span className="block text-xs font-normal text-ink-muted">{partes.base}</span>
      ) : (
        <span className="text-sm font-normal text-ink-muted"> · {partes.base}</span>
      )}
    </>
  );
}
