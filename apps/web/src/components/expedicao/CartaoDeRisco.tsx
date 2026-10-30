"use client";

/**
 * EXPEDICAO-PERTO-DE-VENCER — o cartao do "Risco atual", limpo.
 *
 * Pedido de 30/10/2026: a fileira estava poluida ("% da fila · ver quais",
 * pontos de severidade, legenda). Aqui: rotulo com "ⓘ", o numero e UMA linha
 * de contexto. O cartao inteiro abre a lista; no hover ele sobe (`card-hover`)
 * e mostra "Ver pedidos →" — sem link escrito ocupando a tela.
 *
 * O botao cobre o cartao por baixo do conteudo (que e' `pointer-events-none`),
 * e so' o "ⓘ" volta a receber o ponteiro: elemento focavel dentro de `<button>`
 * seria invalido, e o balao do "ⓘ" continua funcionando.
 */
import type { ReactNode } from "react";

export type TomDoCartao = "critico" | "atencao" | "neutro";

const SUPERFICIE: Record<TomDoCartao, string> = {
  critico: "border-crit/30 bg-crit-soft",
  atencao: "border-warn/40 bg-warn-soft",
  neutro: "border-line bg-surface",
};
const TINTA: Record<TomDoCartao, string> = {
  critico: "text-crit-ink",
  atencao: "text-warn-ink",
  neutro: "text-ink",
};

/** "Ver pedidos →" que aparece no hover (e no foco do teclado). */
export function DicaVerPedidos() {
  return (
    <span
      aria-hidden="true"
      className="shrink-0 text-xs font-semibold text-accent-ink opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
    >
      Ver pedidos →
    </span>
  );
}

/** O botao que cobre o cartao. `rotulo` e' o nome acessivel ("Ver os 447 pedidos..."). */
export function CoberturaClicavel({ rotulo, aoAbrir }: { rotulo: string; aoAbrir: () => void }) {
  return (
    <button
      type="button"
      onClick={aoAbrir}
      aria-label={rotulo}
      className="absolute inset-0 z-0 cursor-pointer rounded-[inherit] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    />
  );
}

export default function CartaoDeRisco({
  rotulo,
  ajuda,
  valor,
  detalhe,
  tom = "neutro",
  aoAbrir,
  rotuloAbrir,
}: {
  rotulo: string;
  ajuda?: ReactNode;
  /** Ja' formatado ("447", "—"). */
  valor: string;
  /** Uma linha: "378 hoje · 69 ter 03/11", "1,8% da fila". */
  detalhe?: string;
  tom?: TomDoCartao;
  /** Abre a lista dos pedidos do cartao. Sem ela, o cartao nao e' clicavel. */
  aoAbrir?: () => void;
  rotuloAbrir?: string;
}) {
  return (
    <div
      className={`group relative flex min-w-0 flex-col justify-between rounded-lg border px-3.5 py-3 ${SUPERFICIE[tom]} ${
        aoAbrir ? "card-hover hover:border-accent/50" : ""
      }`}
      data-testid="exp-cartao-risco"
    >
      {aoAbrir ? <CoberturaClicavel rotulo={rotuloAbrir ?? `Ver os pedidos: ${rotulo}`} aoAbrir={aoAbrir} /> : null}
      <p className="pointer-events-none relative z-10 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-ink-faint">
        {rotulo}
        {ajuda ? <span className="pointer-events-auto inline-flex">{ajuda}</span> : null}
      </p>
      <p className={`pointer-events-none relative z-10 mt-2 text-[28px] font-semibold leading-none tabular-nums ${TINTA[tom]}`}>
        {valor}
      </p>
      <p className="pointer-events-none relative z-10 mt-2 flex min-h-4 items-center justify-between gap-2 text-xs text-ink-muted">
        <span className="min-w-0 truncate">{detalhe}</span>
        {aoAbrir ? <DicaVerPedidos /> : null}
      </p>
    </div>
  );
}
