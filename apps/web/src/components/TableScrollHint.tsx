"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { computeScrollEdges, type ScrollEdges } from "@/lib/scroll-hint";

interface Props {
  children: ReactNode;
  /** Classes extras no próprio elemento com overflow-x-auto (ex.: "max-h-80 overflow-y-auto"). */
  className?: string;
}

const NONE: ScrollEdges = { canScrollLeft: false, canScrollRight: false, isScrollable: false };

/**
 * Substitui um `<div className="overflow-x-auto">` simples quando a tabela
 * pode ser mais larga que a tela. Mostra uma sombra sutil na borda que tem
 * conteúdo escondido (some sozinha quando não há overflow, nunca polui
 * desktop) e, só em telas pequenas, um texto discreto "arraste para ver
 * mais" enquanto ainda houver conteúdo à direita.
 */
export default function TableScrollHint({ children, className = "" }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState<ScrollEdges>(NONE);

  const update = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const next = computeScrollEdges({
      scrollLeft: el.scrollLeft, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth,
    });
    // So atualiza o state se algum valor realmente mudou — computeScrollEdges
    // sempre retorna um objeto novo, e o effect abaixo roda apos TODO render;
    // sem essa checagem de igualdade, setEdges dispararia um re-render a
    // cada chamada (novo objeto por referencia), que dispara o effect de
    // novo, num loop infinito ("Maximum update depth exceeded").
    setEdges((prev) => (
      prev.canScrollLeft === next.canScrollLeft
        && prev.canScrollRight === next.canScrollRight
        && prev.isScrollable === next.isScrollable
    ) ? prev : next);
  }, []);

  // Listeners de scroll/resize do proprio elemento — montado uma vez.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, [update]);

  // Reavalia apos qualquer render (ex.: skeleton -> dados reais muda a
  // largura da tabela sem disparar o ResizeObserver, que so olha o proprio
  // container, nao o conteudo interno).
  useEffect(() => {
    update();
  });

  return (
    <div className="relative min-w-0">
      <div ref={ref} className={`overflow-x-auto ${className}`}>
        {children}
      </div>
      {edges.canScrollLeft && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 left-0 w-6 bg-gradient-to-r from-white to-transparent"
        />
      )}
      {edges.canScrollRight && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 right-0 w-6 bg-gradient-to-l from-white to-transparent"
        />
      )}
      {edges.canScrollRight && (
        <p aria-hidden="true" className="sm:hidden text-center text-[11px] text-slate-400 pt-1">
          ← arraste para ver mais →
        </p>
      )}
    </div>
  );
}
