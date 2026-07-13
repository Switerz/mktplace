export interface ScrollMetrics {
  scrollLeft: number;
  scrollWidth: number;
  clientWidth: number;
}

export interface ScrollEdges {
  /** Há conteúdo escondido à esquerda (já rolou para a direita). */
  canScrollLeft: boolean;
  /** Há conteúdo escondido à direita (ainda dá para rolar mais). */
  canScrollRight: boolean;
  /** scrollWidth > clientWidth — existe overflow horizontal de fato. */
  isScrollable: boolean;
}

/**
 * Pura, sem DOM — calcula os estados de borda a partir das métricas de
 * scroll de um elemento. `threshold` absorve arredondamento de subpixel
 * (zoom do navegador, escala de tela) para não deixar a sombra "piscando"
 * a 1px do fim do scroll.
 */
export function computeScrollEdges(metrics: ScrollMetrics, threshold = 4): ScrollEdges {
  const { scrollLeft, scrollWidth, clientWidth } = metrics;
  const isScrollable = scrollWidth > clientWidth + threshold;
  if (!isScrollable) {
    return { canScrollLeft: false, canScrollRight: false, isScrollable: false };
  }
  const maxScrollLeft = scrollWidth - clientWidth;
  return {
    canScrollLeft: scrollLeft > threshold,
    canScrollRight: scrollLeft < maxScrollLeft - threshold,
    isScrollable: true,
  };
}
