import { Filter } from "lucide-react";

import { ROTULO_CANAL } from "@/lib/dre";
import type { Marketplace } from "@/lib/mock-data";

/**
 * DRE-UX-2 — "Ver só <canais calculáveis>". O MESMO botão no resumo e no
 * ranking: só troca o filtro de canal; o agregado vem calculado pela API.
 */
export default function BotaoRecorteCalculavel({
  canais,
  aoAplicar,
}: {
  canais: Marketplace[];
  aoAplicar: (canais: Marketplace[]) => void;
}) {
  if (!canais.length) return null;
  return (
    <button
      type="button"
      onClick={() => aoAplicar(canais)}
      className="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-3 text-xs font-semibold text-accent hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <Filter aria-hidden="true" className="h-3.5 w-3.5" />
      Ver só {canais.map((c) => ROTULO_CANAL[c]).join(" + ")}
    </button>
  );
}
