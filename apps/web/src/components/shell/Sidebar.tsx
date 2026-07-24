"use client";

import { Suspense } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { buildPreservedQuery, hrefForPage } from "@/lib/filters/nav-links";
import NavList from "./NavList";

const SIDEBAR_FALLBACK = (
  <aside
    aria-label="Navegação principal"
    className="hidden md:block md:w-60 md:shrink-0 md:border-r md:border-violet-100 md:bg-white"
  />
);

/**
 * Sidebar clara/lavanda persistente no desktop (Gate U1). Nao e colapsavel
 * neste gate — apenas visivel a partir do breakpoint md, substituindo a
 * antiga barra horizontal do AppNav.
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
    <aside className="hidden md:flex md:w-60 md:shrink-0 md:flex-col md:border-r md:border-violet-100 md:bg-white md:sticky md:top-0 md:h-screen md:overflow-y-auto">
      <div className="px-4 py-5">
        <NavList pathname={pathname} hrefFor={hrefFor} />
      </div>
    </aside>
  );
}
