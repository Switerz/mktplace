"use client";

import { Suspense } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { buildPreservedQuery, hrefForPage } from "@/lib/filters/nav-links";
import NavList from "./NavList";

const SIDEBAR_FALLBACK = (
  <aside
    aria-label="Navegação principal"
    className="hidden md:block md:w-56 md:shrink-0 md:border-r md:border-line md:bg-surface"
  />
);

/**
 * Sidebar persistente no desktop (Gate U1), agora tematizada (UX-TORRE-1).
 *
 * Continua sendo uma superficie de SUPERFICIE, nao um painel carvao: a Torre
 * rejeita o sidebar escuro do BI generico tambem no tema escuro, onde ela usa
 * o violeta profundo do sistema.
 *
 * Largura 56 (224px) em vez de 60 (240px): 16px que voltam para a area de
 * dados em toda rota.
 */
export default function Sidebar() {
  return (
    <Suspense fallback={SIDEBAR_FALLBACK}>
      <SidebarInner />
    </Suspense>
  );
}

function SidebarInner() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const preservedQuery = buildPreservedQuery(pathname, (key) => searchParams.get(key));
  const hrefFor = (pageHref: string) => hrefForPage(pageHref, preservedQuery);

  return (
    // Duas camadas de proposito: a EXTERNA estica com o flex do shell e pinta
    // a faixa ate o rodape (sem ela, a superficie da navegacao terminava na
    // altura da janela e deixava uma emenda visivel no meio da pagina); a
    // INTERNA e a que gruda no topo e rola sozinha.
    <aside className="hidden md:block md:w-56 md:shrink-0 md:border-r md:border-line md:bg-surface">
      <div className="sticky top-0 max-h-screen overflow-y-auto px-3 py-4">
        <NavList pathname={pathname} hrefFor={hrefFor} />
      </div>
    </aside>
  );
}
