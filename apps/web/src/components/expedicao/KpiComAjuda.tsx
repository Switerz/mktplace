"use client";

/**
 * T-0010 — o `KpiTile` da Expedicao com o "ⓘ" da metrica.
 *
 * EXPEDICAO-VENCE-AMANHA: o "ⓘ" sai do fim da linha de comparacao e vai para
 * o lado do rotulo, como no destaque de vencidos. No rodape ele ficava solto
 * (cartao sem comparacao) e longe do nome que explica.
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
  ...props
}: Omit<ComponentProps<typeof KpiTile>, "ajuda"> & { ajuda: ChaveMetrica | null }) {
  return <KpiTile {...props} ajuda={ajuda ? <AjudaMetrica chave={ajuda} /> : undefined} />;
}

export default KpiComAjuda;

/**
 * QA T-0010 (I2) — taxa no formato unico "3,35% · 199 de 5.940", com a taxa
 * PUBLICADA pela API (a tela nao divide de novo). `linha`: a base vai para
 * uma linha secundaria (tabelas); sem ela, fica ao lado, menor (KPI).
 *
 * T-0021: empilhada, a base perdia o separador e o leitor de tela lia
 * "3,35% 199 de 5.940". O " · " fica no texto acessivel (`sr-only`), sem
 * mudar o visual: o texto lido e' sempre o de `formatarPercentualComBase`.
 * O `sr-only` e' `position: absolute`: o invólucro `relative` o prende a'
 * celula; sem ele, o span ancorava no BODY e criava rolagem horizontal na
 * pagina (QA T-0021 F1). Invólucro inline: o layout nao muda.
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
  if (linha) {
    return (
      <span className="relative">
        <span>{partes.percentual}</span>
        <span className="sr-only"> · </span>
        <span className="block text-xs font-normal text-ink-muted">{partes.base}</span>
      </span>
    );
  }
  return (
    <>
      <span>{partes.percentual}</span>
      <span className="text-sm font-normal text-ink-muted"> · {partes.base}</span>
    </>
  );
}
