"use client";

import { useRef, type ReactNode } from "react";

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
  className?: string;
}

export default function Segmented<T extends string>({
  rotulo,
  rotuladoPor,
  opcoes,
  valor,
  aoEscolher,
  compacto = false,
  className = "",
}: Props<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

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
      className={`inline-flex items-center gap-0.5 rounded-lg border border-line bg-raised p-0.5 ${className}`}
    >
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
            className={`inline-flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-md px-3 text-sm transition-colors ${
              ativo
                ? "bg-surface font-semibold text-ink shadow-sm ring-1 ring-line-strong"
                : "text-ink-muted hover:bg-surface/70 hover:text-ink"
            }`}
          >
            {o.icone}
            <span className={compacto ? "hidden sm:inline" : undefined}>{o.texto}</span>
          </button>
        );
      })}
    </div>
  );
}
