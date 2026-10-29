"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";

/**
 * ANIMACOES-1 — o numero "assenta": ao montar conta de zero ate o valor, e ao
 * trocar o filtro conta do valor anterior ao novo.
 *
 * Recebe o texto JA FORMATADO ("R$ 1.284.530,00", "14.392", "R$ 1,28 mi",
 * "6,4") e anima so' o primeiro numero, preservando prefixo, sufixo, casas
 * decimais e separador de milhar do pt-BR. Se o prefixo ou o sufixo mudar (de
 * "mil" para "mi", por exemplo), troca direto: interpolar entre escalas
 * diferentes mostraria valores falsos no meio do caminho.
 *
 * Leitor de tela le' so' o valor final (`sr-only`); a contagem e' decorativa.
 * Com `prefers-reduced-motion`, mostra o valor final sem contar.
 */
const NUMERO = /\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?/;

type Partes = { antes: string; valor: number; casas: number; milhar: boolean; depois: string };

function partir(texto: string): Partes | null {
  const m = NUMERO.exec(texto);
  if (!m) return null;
  const bruto = m[0];
  const [inteiro, frac = ""] = bruto.split(",");
  return {
    antes: texto.slice(0, m.index),
    valor: Number(`${inteiro.replace(/\./g, "")}.${frac || "0"}`),
    casas: frac.length,
    milhar: inteiro.includes(".") || Number(inteiro.replace(/\./g, "")) >= 1000,
    depois: texto.slice(m.index + bruto.length),
  };
}

function montar(p: Partes, v: number): string {
  const n = v.toLocaleString("pt-BR", {
    minimumFractionDigits: p.casas,
    maximumFractionDigits: p.casas,
    useGrouping: p.milhar,
  });
  return `${p.antes}${n}${p.depois}`;
}

const suave = (t: number) => 1 - Math.pow(1 - t, 3);

// Layout effect no cliente: o primeiro quadro da contagem pinta ANTES da tela,
// sem piscar o valor final por um quadro. No servidor, efeito comum.
const useEfeitoDeQuadro = typeof window === "undefined" ? useEffect : useLayoutEffect;

export default function NumeroAnimado({ texto, className }: { texto: string; className?: string }) {
  const [exibido, setExibido] = useState(texto);
  const anterior = useRef<Partes | null>(null);

  useEfeitoDeQuadro(() => {
    const alvo = partir(texto);
    const de = anterior.current;
    anterior.current = alvo;
    const reduzir =
      typeof window === "undefined" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const mesmaEscala = !de || (alvo && de.antes === alvo.antes && de.depois === alvo.depois);
    if (!alvo || reduzir || !mesmaEscala || (de && de.valor === alvo.valor)) {
      setExibido(texto);
      return;
    }
    const inicio = de ? de.valor : 0;
    const duracao = de ? 450 : 650;
    setExibido(montar(alvo, inicio));
    const t0 = performance.now();
    let quadro = 0;
    const passo = (agora: number) => {
      const t = Math.min(1, (agora - t0) / duracao);
      setExibido(t < 1 ? montar(alvo, inicio + (alvo.valor - inicio) * suave(t)) : texto);
      if (t < 1) quadro = requestAnimationFrame(passo);
    };
    quadro = requestAnimationFrame(passo);
    return () => cancelAnimationFrame(quadro);
  }, [texto]);

  return (
    <span className={className}>
      <span aria-hidden="true">{exibido}</span>
      <span className="sr-only">{texto}</span>
    </span>
  );
}
