"use client";

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

/**
 * UX-TORRE-1 — controle segmentado acessivel (radiogroup + roving tabindex).
 *
 * `role="radiogroup"` cria uma expectativa CONCRETA no leitor de tela: o grupo
 * inteiro e' UM ponto de tabulacao e as SETAS andam entre as opcoes. Um grupo
 * que anuncia o papel sem cumprir o teclado e' pior do que um que nao anuncia
 * nada.
 *
 * Alvo de 44px explicito (`min-h-11`): `py-*` mais `line-height` chegaria la'
 * por acidente aritmetico, e qualquer troca de fonte derrubaria o alvo sem
 * sinal nenhum.
 *
 * NOTA — o `GrupoRadio` interno do `ExpedicaoClient` continua existindo por
 * imposicao dos testes de contrato daquela tela (que leem o proprio arquivo
 * procurando os atributos ARIA). Os dois implementam exatamente o mesmo
 * comportamento; este e' o canonico para as telas novas.
 */

export interface OpcaoSegmentada<T extends string> {
  valor: T;
  texto: string;
  /** Rotulo completo para leitor de tela quando `texto` for so' um icone. */
  descricao?: string;
  icone?: ReactNode;
}

interface Props<T extends string> {
  rotulo: string;
  /** Id de um elemento visivel que rotula o grupo. Exclui `rotulo` do a11y. */
  rotuladoPor?: string;
  opcoes: readonly OpcaoSegmentada<T>[];
  valor: T;
  aoEscolher: (valor: T) => void;
  /** `compacto` esconde o texto no mobile e mantem so' o icone. */
  compacto?: boolean;
  /** So' o icone em qualquer largura; o texto continua no nome acessivel. */
  soIcone?: boolean;
  /**
   * UX-TORRE-B2B-1 — `shell` pinta sobre a moldura navy (rodape da sidebar).
   * Mesmo comportamento, mesmo alvo de 44px; muda so' a superficie.
   */
  tom?: "padrao" | "shell";
  className?: string;
}

/**
 * ANIMACOES-1 — a superficie do item ativo e' UMA pilula (`polegar`) que
 * desliza ate a opcao escolhida, em vez de trocar seco. Ate' a primeira
 * medicao (SSR, hidratacao) o proprio botao ativo pinta a superficie, para
 * nao haver um quadro sem destaque.
 */
const PELE = {
  padrao: {
    trilho: "border-line bg-raised",
    superficie: "bg-surface shadow-sm ring-1 ring-line-strong",
    ativo: "font-semibold text-ink",
    inativo: "text-ink-muted hover:bg-surface/70 hover:text-ink",
  },
  shell: {
    trilho: "border-shell-line bg-shell-raised/60",
    superficie: "bg-shell-active ring-1 ring-shell-line",
    ativo: "font-semibold text-shell-ink",
    inativo: "text-shell-faint hover:bg-shell-raised hover:text-shell-ink",
  },
} as const;

export default function Segmented<T extends string>({
  rotulo,
  rotuladoPor,
  opcoes,
  valor,
  aoEscolher,
  compacto = false,
  soIcone = false,
  tom = "padrao",
  className = "",
}: Props<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const trilho = useRef<HTMLDivElement>(null);
  const [polegar, setPolegar] = useState<{ x: number; w: number } | null>(null);
  const pele = PELE[tom];
  const indiceAtivo = opcoes.findIndex((o) => o.valor === valor);

  useLayoutEffect(() => {
    const medir = () => {
      const alvo = refs.current[indiceAtivo];
      setPolegar(alvo ? { x: alvo.offsetLeft, w: alvo.offsetWidth } : null);
    };
    medir();
    if (typeof ResizeObserver === "undefined" || !trilho.current) return;
    const ro = new ResizeObserver(medir);
    ro.observe(trilho.current);
    return () => ro.disconnect();
  }, [indiceAtivo, opcoes.length]);

  const andar = (de: number, passo: number) => {
    const destino = (de + passo + opcoes.length) % opcoes.length;
    aoEscolher(opcoes[destino].valor);
    refs.current[destino]?.focus();
  };

  return (
    <div
      role="radiogroup"
      aria-label={rotuladoPor ? undefined : rotulo}
      aria-labelledby={rotuladoPor}
      ref={trilho}
      className={`relative inline-flex items-center gap-0.5 rounded-lg border p-0.5 ${pele.trilho} ${className}`}
    >
      {polegar && (
        <span
          aria-hidden="true"
          className={`pointer-events-none absolute bottom-0.5 left-0 top-0.5 rounded-md ${pele.superficie} [transition:transform_200ms_var(--mov-ease),width_200ms_var(--mov-ease)]`}
          style={{ width: polegar.w, transform: `translateX(${polegar.x}px)` }}
        />
      )}
      {opcoes.map((o, i) => {
        const ativo = o.valor === valor;
        return (
          <button
            key={o.valor}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={ativo}
            aria-label={o.descricao}
            title={o.descricao}
            tabIndex={ativo ? 0 : -1}
            onClick={() => aoEscolher(o.valor)}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight" || e.key === "ArrowDown") {
                e.preventDefault();
                andar(i, 1);
              } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
                e.preventDefault();
                andar(i, -1);
              } else if (e.key === "Home") {
                e.preventDefault();
                andar(-1, 1);
              } else if (e.key === "End") {
                e.preventDefault();
                andar(0, -1);
              }
            }}
            className={`relative inline-flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-md px-3 text-sm transition-colors ${
              ativo ? `${pele.ativo} ${polegar ? "" : pele.superficie}` : pele.inativo
            }`}
          >
            {o.icone}
            <span className={soIcone ? "sr-only" : compacto ? "hidden sm:inline" : undefined}>{o.texto}</span>
          </button>
        );
      })}
    </div>
  );
}
