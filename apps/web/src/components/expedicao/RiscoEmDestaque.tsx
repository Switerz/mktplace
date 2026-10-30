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
import { percentualDaFila } from "@/lib/expedicao-travados";
import type { ItemAtencao } from "@/lib/expedicao-ux";

export default function RiscoEmDestaque({
  principal,
  backlog,
  marcasAlerta,
  ajuda,
  acao,
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
  /** "ver quais" — abre a lista dos pedidos da medida. */
  acao?: ReactNode;
  /** Os cartoes secundarios, ja' montados. */
  secundarios: ReactNode;
  /** A barra de situacao de prazo (so' no canal que publica prazo). */
  prazo?: ReactNode;
  ondeAgir?: ItemAtencao | null;
}) {
  const valor = principal.valor ?? 0;
  // EXPEDICAO-VENCE-AMANHA: o % sobre a MESMA fila do "de N em fila", com a
  // regra dos travados ("3,7% da fila").
  const pct = backlog?.valor != null ? percentualDaFila(valor, backlog.valor) : null;
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
        <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className={`text-4xl font-bold leading-none tabular-nums ${tinta}`}>
            {formatarInteiro(valor)}
          </span>
          {backlog?.valor != null ? (
            <span className="text-sm text-ink-muted">
              de {formatarInteiro(backlog.valor)} em fila
            </span>
          ) : null}
          {pct ? (
            <span className={`rounded-md bg-surface/70 px-1.5 py-0.5 text-sm font-semibold tabular-nums ${tinta}`}>
              {pct} da fila
            </span>
          ) : null}
          {acao ? <span className="text-sm">{acao}</span> : null}
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
        // EXPEDICAO-VENCE-AMANHA: 4 cartoes na Shopee, 1 no ML — as colunas
        // seguem a quantidade (2 por linha em 390px, todos lado a lado no desktop).
        className="grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-2 lg:auto-rows-fr [&>*]:h-full"
      >
        {secundarios}
      </section>
    </div>
  );
}
