import type { ReactNode } from "react";

/**
 * UX-TORRE-B2B-1 — composicao conhecida de UM canal, em linhas.
 *
 * Cada linha e' um componente que a fonte do canal ENTREGA (valor + % do GMV),
 * ou declara que nao entrega (`ausente`). A barra e' o percentual sobre o GMV
 * do proprio canal, em escala real (100% = trilho inteiro): nada e' somado e
 * nada e' normalizado, porque os componentes vem de fontes e denominadores
 * diferentes e somar fecharia uma "margem" que esta tela nao tem.
 *
 * Cor da barra = identidade do canal. Nao ha verde/vermelho aqui: o fato de
 * um custo existir nao e' bom nem ruim; as faixas de severidade ficam nas
 * tabelas, onde tem regra de negocio por tras.
 */
export interface LinhaCusto {
  rotulo: string;
  /** Valor monetario ja' formatado. */
  valor?: string | null;
  /** % do GMV do canal, numero bruto (a barra usa este valor). */
  pct?: number | null;
  /** Texto do percentual ja' formatado (preserva o formato da tela). */
  pctTexto?: string | null;
  /** Uma linha curta de contexto (ROAS, ACOS, "competencias podem divergir"). */
  detalhe?: ReactNode;
  /** A fonte do canal nao entrega este componente. */
  ausente?: string;
}

export default function ChannelCostPanel({
  titulo,
  ponto,
  barra,
  receita,
  receitaRotulo = "Receita (GMV)",
  linhas,
  carregando = false,
  id,
}: {
  titulo: string;
  /** Classe `bg-canal-*` do ponto de identidade. */
  ponto: string;
  /** Classe `bg-canal-*` da barra (a mesma cor). */
  barra: string;
  receita: string;
  receitaRotulo?: string;
  linhas: LinhaCusto[];
  carregando?: boolean;
  id?: string;
}) {
  return (
    <section
      id={id}
      aria-label={`${titulo} — receita e componentes conhecidos`}
      className="flex min-w-0 flex-col rounded-xl border border-line bg-surface scroll-mt-24"
    >
      <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
            <span aria-hidden="true" className={`h-2.5 w-2.5 shrink-0 rounded-full ${ponto}`} />
            {titulo}
          </h3>
          <p className="mt-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">{receitaRotulo}</p>
        </div>
        {carregando ? (
          <span className="mt-1 h-7 w-28 animate-pulse rounded bg-raised" aria-hidden="true" />
        ) : (
          <p className="text-2xl font-semibold leading-tight tracking-tight text-ink tabular-nums">{receita}</p>
        )}
      </header>

      <ul className="flex flex-col divide-y divide-line" aria-busy={carregando}>
        {linhas.map((l) => (
          <li key={l.rotulo} className="flex flex-col gap-1.5 px-4 py-2.5">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm text-ink-muted">{l.rotulo}</span>
              {l.ausente ? (
                <span className="rounded border border-dashed border-line-strong px-1.5 py-0.5 text-xs text-ink-faint">
                  {l.ausente}
                </span>
              ) : (
                <span className="flex items-baseline gap-2 tabular-nums">
                  {l.valor && <span className="text-sm font-semibold text-ink">{l.valor}</span>}
                  {l.pctTexto && <span className="text-xs font-medium text-ink-muted">{l.pctTexto}</span>}
                </span>
              )}
            </div>
            {!l.ausente && l.pct != null && (
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised" aria-hidden="true">
                <div className={`h-full rounded-full ${barra}`} style={{ width: `${Math.min(100, Math.max(0, l.pct))}%` }} />
              </div>
            )}
            {l.detalhe && <p className="text-xs leading-snug text-ink-faint">{l.detalhe}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}
