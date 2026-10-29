"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";

/**
 * UX-TORRE-B2B-1 — gatilho compacto + painel flutuante para filtros.
 *
 * E' um padrao de DIVULGACAO (`aria-expanded` + `aria-controls`), nao um
 * `role="menu"`: o conteudo continua sendo controles comuns (botoes com
 * `aria-pressed`), e um papel de menu prometeria ao leitor de tela uma
 * navegacao por setas que estes controles nao tem.
 *
 * Fecha por Escape (devolvendo o foco ao gatilho), por clique fora e quando o
 * foco sai do conjunto pelo Tab. O gatilho mostra o VALOR atual — quem le a
 * barra sabe o escopo sem abrir nada.
 *
 * Alvo de 44px explicito no gatilho, como em todo filtro da Torre.
 */
export default function Popover({
  rotulo,
  valor,
  ativo = false,
  icone,
  children,
  largura = "w-64",
  alinhar = "inicio",
}: {
  /** Nome do filtro ("Marca"). Vai visivel no gatilho e no nome acessivel. */
  rotulo: string;
  /** Valor atual, ja' formatado para leitura ("Todas", "Mês anterior"). */
  valor: ReactNode;
  /** Filtro restringindo o escopo (diferente do padrao): realce discreto. */
  ativo?: boolean;
  icone?: ReactNode;
  children: (fechar: () => void) => ReactNode;
  largura?: string;
  alinhar?: "inicio" | "fim";
}) {
  const [aberto, setAberto] = useState(false);
  const id = useId();
  const raiz = useRef<HTMLDivElement>(null);
  const gatilho = useRef<HTMLButtonElement>(null);

  // Fechar por ESCOLHA devolve o foco ao gatilho: o botao escolhido some com
  // o painel, e sem isto o foco de quem usa teclado cairia no <body>.
  const fechar = () => {
    setAberto(false);
    gatilho.current?.focus();
  };

  useEffect(() => {
    if (!aberto) return;
    const fora = (e: PointerEvent) => {
      if (raiz.current && !raiz.current.contains(e.target as Node)) setAberto(false);
    };
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setAberto(false);
        gatilho.current?.focus();
      }
    };
    document.addEventListener("pointerdown", fora);
    document.addEventListener("keydown", tecla);
    return () => {
      document.removeEventListener("pointerdown", fora);
      document.removeEventListener("keydown", tecla);
    };
  }, [aberto]);

  return (
    <div
      ref={raiz}
      className="relative shrink-0"
      onBlur={(e) => {
        if (aberto && raiz.current && !raiz.current.contains(e.relatedTarget as Node | null)) {
          setAberto(false);
        }
      }}
    >
      <button
        ref={gatilho}
        type="button"
        aria-expanded={aberto}
        aria-controls={id}
        onClick={() => setAberto((v) => !v)}
        className={`inline-flex min-h-11 items-center gap-2 rounded-lg border bg-surface px-3 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
          ativo || aberto ? "border-accent/50" : "border-line hover:border-line-strong"
        }`}
      >
        {icone}
        <span className="text-ink-muted">{rotulo}</span>
        <span className={`max-w-[11rem] truncate font-semibold ${ativo ? "text-accent-ink" : "text-ink"}`}>
          {valor}
        </span>
        <ChevronDown
          aria-hidden="true"
          className={`h-4 w-4 shrink-0 text-ink-faint transition-transform ${aberto ? "rotate-180" : ""}`}
        />
      </button>
      {/* `tabIndex={-1}`: clique no respiro do painel (ou, no Safari, num
          botao que nao recebe foco ao clicar) leva o foco para o PROPRIO
          painel. Sem isso o `onBlur` acima via `relatedTarget = null` e
          fechava o painel no meio do clique. */}
      {/* ANIMACOES-1 — fechado e' `invisible` (fora do foco e da arvore de
          acessibilidade, como o `hidden` de antes), mas continua no DOM para
          poder sair com transicao. A visibilidade so' troca no FIM da saida. */}
      <div
        id={id}
        tabIndex={-1}
        className={`absolute top-full z-40 mt-1.5 outline-none max-w-[calc(100vw-2rem)] rounded-xl border border-line bg-surface p-1.5 shadow-[0_8px_24px_-6px_rgb(var(--tc-ink)/0.18)] ${largura} ${
          alinhar === "fim" ? "right-0 origin-top-right" : "left-0 origin-top-left"
        } ${
          aberto
            ? "visible translate-y-0 scale-100 opacity-100 [transition:opacity_200ms_var(--mov-ease),transform_200ms_var(--mov-ease)]"
            : "pointer-events-none invisible -translate-y-1 scale-[0.97] opacity-0 [transition:opacity_150ms_var(--mov-ease),transform_150ms_var(--mov-ease),visibility_0s_linear_150ms]"
        }`}
      >
        {children(fechar)}
      </div>
    </div>
  );
}
