import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

/**
 * UX-TORRE-B2B-1 — cartao de indicador NAO interativo (Canais, Financeiro).
 *
 * Mesma anatomia do cartao da Gerencial (`KpiBand`): rotulo + icone no topo,
 * numero de 24px, uma linha de contexto, nota curta na base. Existe separado
 * do `KpiCard` legado porque aquele e' consumido por sete rotas ainda nao
 * redesenhadas, e este gate nao pode mudar a aparencia delas.
 *
 * `canal` pinta so' o PONTO de identidade (TikTok violeta, ML ciano, Shopee
 * laranja) — nunca o numero. `tom` (bom/atencao/critico) so' com regra de
 * negocio por tras, e entao colore o valor.
 */
export default function StatCard({
  rotulo,
  valor,
  icone: Icone,
  canal,
  contexto,
  nota,
  tom = "neutro",
  carregando = false,
}: {
  rotulo: string;
  valor: ReactNode;
  icone?: LucideIcon;
  /** Classe `bg-canal-*` do ponto de identidade, quando o KPI e' de um canal. */
  canal?: string;
  /** Uma linha logo abaixo do numero: base, total, denominador. */
  contexto?: ReactNode;
  /** Ressalva curta, na base do cartao. Nunca removida — so' encurtada. */
  nota?: ReactNode;
  tom?: "neutro" | "bom" | "atencao" | "critico";
  carregando?: boolean;
}) {
  if (carregando) {
    return (
      <div
        role="status"
        aria-busy="true"
        className="flex min-h-[7.5rem] flex-col gap-3 rounded-xl border border-line bg-surface p-4 animate-pulse"
      >
        <span className="sr-only">Carregando indicador…</span>
        <span className="h-3 w-24 rounded bg-raised" />
        <span className="h-6 w-28 rounded bg-raised" />
        <span className="mt-auto h-3 w-20 rounded bg-raised" />
      </div>
    );
  }

  const tinta =
    tom === "bom" ? "text-ok-ink" : tom === "atencao" ? "text-warn-ink" : tom === "critico" ? "text-crit-ink" : "text-ink";

  return (
    <div className="flex min-w-0 flex-col gap-1.5 rounded-xl border border-line bg-surface p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="flex min-w-0 items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-ink-faint">
          {canal && <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${canal}`} />}
          <span className="min-w-0">{rotulo}</span>
        </p>
        {Icone && (
          <span
            aria-hidden="true"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-raised text-ink-faint"
          >
            <Icone className="h-4 w-4" strokeWidth={1.75} />
          </span>
        )}
      </div>
      <p className={`text-2xl font-semibold leading-tight tracking-tight tabular-nums ${tinta}`}>{valor}</p>
      {contexto && <p className="text-xs leading-snug text-ink-muted tabular-nums">{contexto}</p>}
      {nota && <p className="mt-auto pt-1 text-xs leading-snug text-ink-faint">{nota}</p>}
    </div>
  );
}
