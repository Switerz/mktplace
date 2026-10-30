"use client";

/**
 * UI-EXPEDICAO-1 — a hierarquia do "Risco atual".
 *
 * Seis cartoes do mesmo tamanho pediam ao gestor que decidisse sozinho por
 * onde comecar. Aqui UM numero manda: vencidos (ou, no canal sem prazo
 * publicado, acima de 48 h), grande e na cor do risco. Ao lado, os
 * secundarios; o backlog total fica pequeno, como base ("de N em fila").
 * A barra de prazo e a frase "onde agir" moram dentro do destaque, para cada
 * numero aparecer uma vez so' na tela.
 *
 * So' apresentacao: os numeros chegam prontos do contrato (`Kpi`), e quem
 * monta a barra e a frase e' o cliente do canal.
 */
import type { ReactNode } from "react";

import type { Kpi } from "@/lib/expedicao-contract";
import { formatarInteiro } from "@/lib/expedicao-formato";
import type { ItemAtencao } from "@/lib/expedicao-ux";

export default function RiscoEmDestaque({
  principal,
  backlog,
  marcasAlerta,
  ajuda,
  secundarios,
  prazo,
  ondeAgir,
}: {
  /** A medida em destaque, com valor medido (o cliente garante). */
  principal: Kpi;
  backlog?: Kpi;
  marcasAlerta?: Kpi;
  /** O "ⓘ" da medida em destaque. */
  ajuda?: ReactNode;
  /** Os cartoes secundarios, ja' montados. */
  secundarios: ReactNode;
  /** A barra de situacao de prazo (so' no canal que publica prazo). */
  prazo?: ReactNode;
  ondeAgir?: ItemAtencao | null;
}) {
  const valor = principal.valor ?? 0;
  // Vencido e' prazo do marketplace (vermelho); +48 h e' limiar interno (ambar).
  // Zero nao pinta: cor e' para risco, nao para decorar.
  const critico = principal.chave === "overdue";
  const tinta = valor === 0 ? "text-ink" : critico ? "text-crit-ink" : "text-warn-ink";
  const fundo =
    valor === 0
      ? "border-line bg-surface"
      : critico
        ? "border-crit/30 bg-crit-soft"
        : "border-warn/30 bg-warn-soft";
  const qtdAlerta = marcasAlerta?.valor ?? 0;

  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]" data-testid="exp-risco-destaque">
      <section
        aria-label={`${principal.rotulo}: ${formatarInteiro(valor)}`}
        className={`card-hover flex flex-col gap-2 rounded-xl border p-4 ${fundo}`}
      >
        <div className="flex items-center justify-between gap-2">
          <p className={`flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide ${tinta}`}>
            {principal.rotulo}
            {ajuda}
          </p>
          {qtdAlerta > 0 ? (
            <span className="rounded-full border border-warn/40 bg-surface px-2 py-0.5 text-xs font-semibold text-warn-ink">
              {formatarInteiro(qtdAlerta)} {qtdAlerta === 1 ? "marca em alerta" : "marcas em alerta"}
            </span>
          ) : null}
        </div>
        <p className="flex items-baseline gap-2">
          <span className={`text-4xl font-bold leading-none tabular-nums ${tinta}`}>
            {formatarInteiro(valor)}
          </span>
          {backlog?.valor != null ? (
            <span className="text-sm text-ink-muted">
              de {formatarInteiro(backlog.valor)} em fila
            </span>
          ) : null}
        </p>
        {prazo ? <div className="mt-1">{prazo}</div> : null}
        {ondeAgir ? (
          <p className="mt-1 flex items-start gap-2 border-t border-line/70 pt-2 text-sm text-ink" data-testid="exp-onde-agir">
            <span
              aria-hidden="true"
              className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${ondeAgir.tom === "critico" ? "bg-crit" : "bg-warn"}`}
            />
            <span>
              <span className="font-semibold">Onde agir:</span> {ondeAgir.texto}
            </span>
          </p>
        ) : null}
      </section>
      {/* Os cartoes ESTICAM ate' a altura do destaque: sem faixa vazia embaixo. */}
      <section
        aria-label="Demais indicadores de risco"
        className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:auto-rows-fr [&>*]:h-full"
      >
        {secundarios}
      </section>
    </div>
  );
}
