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
import {
  Activity,
  AlertTriangle,
  CalendarClock,
  Clock,
  Hourglass,
  Layers,
  PauseCircle,
  type LucideIcon,
} from "lucide-react";

export type TomDoCartao = "critico" | "atencao" | "neutro";

// UX-VIDRO-4: vidro com a borda e o brilho do canto no tom do cartao (a
// regra de cor nao mudou: so' ha' tom quando a regra de negocio diz).
const SUPERFICIE: Record<TomDoCartao, string> = {
  critico: "border-crit/45 bg-surface tc-brilho-crit",
  atencao: "border-warn/50 bg-surface tc-brilho-warn",
  neutro: "border-line bg-surface tc-brilho-neutro",
};
const LADRILHO: Record<TomDoCartao, string> = {
  critico: "bg-crit/10 text-crit-ink",
  atencao: "bg-warn/15 text-warn-ink",
  neutro: "bg-accent/10 text-accent",
};
/** Um icone por medida, o mesmo em todo canal. */
const ICONE_DA_MEDIDA: Record<string, LucideIcon> = {
  overdue: AlertTriangle,
  due_soon: Clock,
  due24h: Clock,
  due_next_day: CalendarClock,
  over48h: Hourglass,
  stalled: PauseCircle,
  backlog: Layers,
  marcas_alerta: Activity,
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
  chave,
}: {
  /** A medida (`overdue`, `stalled`...): escolhe o icone do ladrilho. */
  chave?: string;
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
  const Icone = chave ? ICONE_DA_MEDIDA[chave] : undefined;
  return (
    <div
      className={`group relative flex min-w-0 flex-col justify-between rounded-xl border px-4 py-3.5 ${SUPERFICIE[tom]} ${
        aoAbrir ? "card-hover hover:border-accent/50" : ""
      }`}
      data-testid="exp-cartao-risco"
    >
      {aoAbrir ? <CoberturaClicavel rotulo={rotuloAbrir ?? `Ver os pedidos: ${rotulo}`} aoAbrir={aoAbrir} /> : null}
      <p className="pointer-events-none relative z-10 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
        {Icone ? (
          <span aria-hidden="true" className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${LADRILHO[tom]}`}>
            <Icone className="h-4 w-4" strokeWidth={1.75} />
          </span>
        ) : null}
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
