import { CalendarDays, RefreshCw } from "lucide-react";
import { fmtRefreshedAt } from "@/lib/filters/format";

/**
 * UX-TORRE-B2B-1 — de onde e de quando e' o dado, num selo so'.
 *
 * Substitui o par "badge Dados ao vivo + linha Atualizado em ..." que cada
 * tela montava a mao em dois lugares do cabecalho. O selo continua dizendo as
 * MESMAS tres coisas — conexao, frescor, demonstracao — e nao afirma nada que
 * a pagina nao saiba:
 *
 *   live === null  a pagina ainda nao tem resposta compativel com o filtro
 *                  atual: so' "Atualizando…" (ou nada), nunca "ao vivo".
 *   live === true  ponto verde + "Ao vivo" + horario de `refreshed_at`.
 *   live === false demonstracao, em ambar — o dado de exemplo nao e' o real.
 *
 * O texto longo ("Dados ao vivo · API conectada") vai para o `title`: quem
 * passa o mouse le a frase inteira, quem bate o olho le o horario.
 */
export default function Freshness({
  live,
  refreshedAt,
  loading = false,
}: {
  live: boolean | null;
  refreshedAt: string | null;
  loading?: boolean;
}) {
  if (live == null) {
    if (!loading) return null;
    return (
      <span
        role="status"
        className="inline-flex min-h-8 items-center gap-1.5 whitespace-nowrap rounded-md bg-raised px-2.5 text-xs font-medium text-ink-muted ring-1 ring-inset ring-line"
      >
        <RefreshCw aria-hidden="true" className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
        Atualizando…
      </span>
    );
  }

  if (!live) {
    return (
      <span
        title="Modo demonstração: a API está offline e os números são de exemplo"
        className="inline-flex min-h-8 items-center gap-1.5 whitespace-nowrap rounded-md bg-warn-soft px-2.5 text-xs font-medium text-warn-ink ring-1 ring-inset ring-warn/30"
      >
        <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-warn" />
        Demonstração · API offline
      </span>
    );
  }

  const quando = fmtRefreshedAt(refreshedAt);
  return (
    <span
      title={`Dados ao vivo · API conectada${quando ? ` · atualizado em ${quando}` : ""}`}
      className="inline-flex min-h-8 items-center gap-1.5 whitespace-nowrap rounded-md bg-ok-soft px-2.5 text-xs font-medium text-ok-ink ring-1 ring-inset ring-ok/30"
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-ok" />
      Ao vivo
      {quando && (
        <>
          <span aria-hidden="true" className="text-ok-ink/60">·</span>
          <span className="tabular-nums">Atualizado em {quando}</span>
        </>
      )}
    </span>
  );
}

/** O escopo de tempo lido, em selo neutro ao lado do frescor. */
export function PeriodChip({ label }: { label: string }) {
  return (
    <span className="inline-flex min-h-8 items-center gap-1.5 whitespace-nowrap rounded-md bg-surface px-2.5 text-xs font-medium text-ink-muted ring-1 ring-inset ring-line">
      <CalendarDays aria-hidden="true" className="h-3.5 w-3.5 text-ink-faint" />
      <span className="sr-only">Período: </span>
      <span className="tabular-nums text-ink">{label}</span>
    </span>
  );
}
