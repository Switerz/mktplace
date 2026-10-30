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
 *
 * EXPEDICAO-PERTO-DE-VENCER (30/10/2026, "fileira muito poluida"): a barra de
 * prazo e a legenda sairam do destaque (a particao esta' nos cartoes e na
 * tabela) e o "ver quais" virou o cartao inteiro clicavel, com "Ver pedidos →"
 * no hover, como os cartoes ao lado.
 */
import type { ReactNode } from "react";

import type { Kpi } from "@/lib/expedicao-contract";
import { formatarInteiro } from "@/lib/expedicao-formato";
import { percentualDaFila } from "@/lib/expedicao-travados";
import type { ItemAtencao } from "@/lib/expedicao-ux";

import { CoberturaClicavel, DicaVerPedidos } from "./CartaoDeRisco";

export default function RiscoEmDestaque({
  principal,
  backlog,
  marcasAlerta,
  ajuda,
  aoAbrir,
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
  /** Abre a lista dos pedidos da medida (o cartao inteiro e' o botao). */
  aoAbrir?: () => void;
  /** Os cartoes secundarios, ja' montados. */
  secundarios: ReactNode;
  /** Opcional: uma barra de prazo dentro do destaque. */
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
        className={`group relative flex flex-col gap-2 rounded-xl border p-4 ${fundo} ${
          aoAbrir ? "card-hover hover:border-accent/50" : ""
        }`}
      >
        {aoAbrir ? (
          <CoberturaClicavel rotulo={`Ver os ${formatarInteiro(valor)} pedidos: ${principal.rotulo}`} aoAbrir={aoAbrir} />
        ) : null}
        <div className="pointer-events-none relative z-10 flex items-center justify-between gap-2">
          <p className={`flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide ${tinta}`}>
            {principal.rotulo}
            {ajuda ? <span className="pointer-events-auto inline-flex">{ajuda}</span> : null}
          </p>
          {qtdAlerta > 0 ? (
            <span className="rounded-full border border-warn/40 bg-surface px-2 py-0.5 text-xs font-semibold text-warn-ink">
              {formatarInteiro(qtdAlerta)} {qtdAlerta === 1 ? "marca em alerta" : "marcas em alerta"}
            </span>
          ) : null}
        </div>
        <p className="pointer-events-none relative z-10 flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className={`text-4xl font-bold leading-none tabular-nums ${tinta}`}>
            {formatarInteiro(valor)}
          </span>
          {backlog?.valor != null ? (
            <span className="text-sm text-ink-muted">
              de {formatarInteiro(backlog.valor)} em fila{pct ? ` · ${pct} da fila` : ""}
            </span>
          ) : null}
        </p>
        {prazo ? <div className="relative z-10 mt-1">{prazo}</div> : null}
        {ondeAgir || aoAbrir ? (
          <p
            className="pointer-events-none relative z-10 mt-auto flex items-start justify-between gap-2 border-t border-line/70 pt-2 text-sm text-ink"
            data-testid="exp-onde-agir"
          >
            {ondeAgir ? (
              <span className="flex items-start gap-2">
                <span
                  aria-hidden="true"
                  className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${ondeAgir.tom === "critico" ? "bg-crit" : "bg-warn"}`}
                />
                <span>
                  <span className="font-semibold">Onde agir:</span> {ondeAgir.texto}
                </span>
              </span>
            ) : (
              <span />
            )}
            {aoAbrir ? <DicaVerPedidos /> : null}
          </p>
        ) : null}
      </section>
      {/* Os cartoes ESTICAM ate' a altura do destaque: sem faixa vazia embaixo.
          As colunas seguem a quantidade (3 na Shopee, 1 no ML). */}
      <section
        aria-label="Demais indicadores de risco"
        className="grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-2 lg:auto-rows-fr [&>*]:h-full"
      >
        {secundarios}
      </section>
    </div>
  );
}
