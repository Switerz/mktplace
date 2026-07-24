"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";
import MobileDrawer from "./MobileDrawer";

/**
 * Shell compartilhado por todas as rotas (Gate U1): sidebar desktop + topbar
 * + drawer mobile + area de conteudo, montados uma unica vez no layout raiz
 * para que nao sejam remontados a cada navegacao entre paginas.
 *
 * `id="app-shell-root"` (Gate U2) e o alvo de `inert` do KpiDrilldownDialog
 * — o dialogo e portalizado para `document.body` (fora desta arvore) e
 * torna todo o shell inerte enquanto aberto, o que bloqueia inclusive o
 * hamburger do drawer mobile (nunca deve ser possivel abrir o drawer atras
 * do dialogo). O drawer, por sua vez, ja torna Topbar+main inertes
 * enquanto aberto — como esses cards ficam dentro de `main`, o drawer aberto
 * ja impede abrir o dialogo por tras dele, sem necessidade de coordenacao
 * adicional entre os dois.
 */
export default function AppShell({ children }: { children: React.ReactNode }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const pathname = usePathname();

  // Fecha o drawer automaticamente ao navegar para outra rota.
  useEffect(() => {
    setDrawerOpen(false);
  }, [pathname]);

  // Bloqueia o scroll de fundo enquanto o drawer estiver aberto.
  useEffect(() => {
    if (!drawerOpen) return;
    const original = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = original;
    };
  }, [drawerOpen]);

  return (
    <div id="app-shell-root" className="min-h-screen bg-[#f8f7ff] md:flex">
      <Sidebar />
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Topbar + conteudo ficam inertes enquanto o drawer mobile esta
            aberto (Finding 2, correcao U1) — nem foco nem leitores de tela
            alcancam o fundo; o ciclo de Tab fica preso ao proprio drawer. */}
        <div inert={drawerOpen || undefined} className="flex-1 min-w-0 flex flex-col">
          <Topbar
            open={drawerOpen}
            onToggleMenu={() => setDrawerOpen((open) => !open)}
            menuButtonRef={menuButtonRef}
          />
          <main className="flex-1 min-w-0">{children}</main>
        </div>
        <MobileDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} openerRef={menuButtonRef} />
      </div>
    </div>
  );
}
