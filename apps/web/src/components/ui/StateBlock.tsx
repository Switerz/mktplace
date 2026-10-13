import type { ReactNode } from "react";
import { CircleAlert, CircleSlash, Inbox, RefreshCw } from "lucide-react";

/**
 * UX-TORRE-B2B-1 — os quatro estados que nao sao "dado presente".
 *
 *   vazio         a fonte respondeu e nao ha linha no recorte
 *   carregando    requisicao em voo (sem numero antigo na tela)
 *   erro          a fonte falhou nesta carga — com acao de nova tentativa
 *   indisponivel  o recorte nao existe nesta fonte (nao e' falha nem zero)
 *
 * Os quatro sao DIFERENTES e a tela nao pode confundi-los: "indisponivel"
 * pintado como "vazio" ensina o gestor a ler ausencia de medicao como zero.
 */
type Tipo = "vazio" | "carregando" | "erro" | "indisponivel";

const ICONE = { vazio: Inbox, carregando: RefreshCw, erro: CircleAlert, indisponivel: CircleSlash } as const;

const PELE: Record<Tipo, string> = {
  vazio: "border-line bg-surface text-ink-muted",
  carregando: "border-line bg-surface text-ink-muted",
  erro: "border-crit/35 bg-crit-soft text-crit-ink",
  indisponivel: "border-dashed border-line-strong bg-surface text-ink-muted",
};

export default function StateBlock({
  tipo,
  titulo,
  descricao,
  acao,
  compacto = false,
}: {
  tipo: Tipo;
  titulo: string;
  descricao?: ReactNode;
  acao?: ReactNode;
  /** Dentro de um painel (sem moldura propria). */
  compacto?: boolean;
}) {
  const Icone = ICONE[tipo];
  return (
    <div
      role={tipo === "erro" ? "alert" : "status"}
      aria-busy={tipo === "carregando" || undefined}
      className={`flex flex-col items-center gap-2 px-6 text-center ${
        compacto ? "py-8" : `rounded-xl border py-10 ${PELE[tipo]}`
      }`}
    >
      <Icone
        aria-hidden="true"
        className={`h-6 w-6 opacity-70 ${tipo === "carregando" ? "animate-spin motion-reduce:animate-none" : ""}`}
        strokeWidth={1.75}
      />
      <p className="text-sm font-semibold">{titulo}</p>
      {descricao && <p className="max-w-md text-xs leading-relaxed opacity-90">{descricao}</p>}
      {acao}
    </div>
  );
}

/** Botao padrao de nova tentativa, com alvo de 44px. */
export function RetryButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="mt-1 inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-current/30 px-3 text-xs font-semibold hover:bg-surface/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
      Tentar novamente
    </button>
  );
}
