"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * Navegacao entre as duas visoes do Monitoramento de Precos.
 *
 * Sao LINKS (`aria-current="page"`), nao um radiogroup: trocar de visao troca
 * de rota. Mesma pele do `ui/Segmented` e o mesmo alvo de 44px.
 */
export const VISOES = [
  { href: "/monitoramento-preco", texto: "Lojas próprias" },
  { href: "/monitoramento-preco/revendedores", texto: "B2B / Revendedores" },
] as const;

export default function VisaoMonitoramentoNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Visões do monitoramento de preços">
      <ul className="inline-flex flex-wrap gap-1 rounded-lg border border-line bg-raised p-1">
        {VISOES.map((v) => {
          const ativa = pathname === v.href;
          return (
            <li key={v.href}>
              <Link
                href={v.href}
                aria-current={ativa ? "page" : undefined}
                className={`inline-flex min-h-11 items-center rounded-md px-3 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                  ativa
                    ? "bg-surface font-semibold text-ink shadow-sm ring-1 ring-line-strong"
                    : "text-ink-muted hover:bg-surface/70 hover:text-ink"
                }`}
              >
                {v.texto}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
