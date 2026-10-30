"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";

/**
 * ANIMACOES-1 — barra fina no topo enquanto a proxima rota carrega.
 *
 * O App Router nao avisa quando uma navegacao COMECA; o clique avisa. Um clique
 * num link interno para OUTRA rota inicia a barra (ela anda ate 70% e espera),
 * e a troca do `pathname` a completa e some. Trocar filtro muda so' a query,
 * entao nao acende nada. Uma trava de 8s apaga a barra se a navegacao morrer.
 */
type Fase = "parada" | "carregando" | "fim";

export default function BarraDeNavegacao() {
  const pathname = usePathname();
  const [fase, setFase] = useState<Fase>("parada");
  const anterior = useRef(pathname);

  useEffect(() => {
    const aoClicar = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const link = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!link || link.target === "_blank" || link.hasAttribute("download")) return;
      const destino = new URL(link.href, window.location.href);
      if (destino.origin !== window.location.origin || destino.pathname === window.location.pathname) return;
      setFase("carregando");
    };
    document.addEventListener("click", aoClicar, true);
    return () => document.removeEventListener("click", aoClicar, true);
  }, []);

  useEffect(() => {
    if (pathname === anterior.current) return;
    anterior.current = pathname;
    setFase("fim");
    const t = setTimeout(() => setFase("parada"), 500);
    return () => clearTimeout(t);
  }, [pathname]);

  useEffect(() => {
    if (fase !== "carregando") return;
    const t = setTimeout(() => setFase("parada"), 8000);
    return () => clearTimeout(t);
  }, [fase]);

  const escala = fase === "carregando" ? 0.7 : fase === "fim" ? 1 : 0;
  return (
    <div
      aria-hidden="true"
      className="tc-nav-progresso pointer-events-none fixed inset-x-0 top-0 z-[60] h-0.5 bg-accent"
      style={{
        transform: `scaleX(${escala})`,
        opacity: fase === "carregando" ? 1 : 0,
        transitionDuration: fase === "parada" ? "0ms" : undefined,
      }}
    />
  );
}
