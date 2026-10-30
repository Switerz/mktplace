"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Info } from "lucide-react";

/**
 * VISUAL-PALETA-1 — a ressalva sai do corpo do card e vira um "ⓘ".
 *
 * A Torre do Atacado mostra o numero e quase nada em volta; a explicacao
 * (metodologia, cobertura, "nao e' comprador unico") continua existindo, mas
 * so' para quem procura. Nunca apaga a ressalva — so' tira do caminho do olho.
 *
 * Dois modos:
 *  - padrao: o "ⓘ" e' FOCAVEL (Tab ou toque no celular) e mostra o balao no
 *    hover e no foco; o leitor de tela le' o texto pelo `aria-label`.
 *  - `emControle`: dentro de um botao (card de KPI), onde elemento focavel
 *    aninhado e' invalido. Fica so' o `title` decorativo, fora do nome
 *    acessivel do botao; a ressalva completa esta' no detalhe que o botao abre.
 *
 * ABAS-OBJETIVAS — o balao e' `fixed`, posicionado pelo retangulo do "ⓘ".
 * Com `absolute` ele era cortado pelo `overflow-hidden` do `Panel` e das
 * tabelas com rolagem, e passava da borda da tela em 390px. Abre abaixo; sem
 * espaco, abre acima. Fecha ao rolar, porque a posicao ficaria velha.
 *
 * T-0028 — o balao vai para o `document.body` (portal). `fixed` so' e' relativo
 * a' tela se nenhum ancestral tiver `transform`: o `.card-hover:hover` sobe o
 * card com `translateY` e a entrada `.tc-entrada` deixa `transform` preenchido
 * em cada bloco. Dentro deles o balao abria centenas de px longe do "ⓘ", fora
 * da tela, e criava rolagem horizontal em 390px (medido).
 *
 * T-0033 — Tab ate' um "ⓘ" fora da tela: o `onFocus` abre e o navegador rola
 * ate' o "ⓘ" logo depois. O evento dessa rolagem chega no quadro seguinte e
 * fechava o balao na hora (22 de 22 em producao). Ver `rolagemAoAbrir`.
 */
const LARGURA = 256;
const MARGEM = 8;

/**
 * Decide o que a rolagem faz com o balao recem-aberto.
 *
 * Aberto pelo FOCO, a rolagem dos dois proximos quadros e' a do proprio foco
 * (o evento `scroll` sai antes dos `requestAnimationFrame` do quadro): ela
 * REPOSICIONA em vez de fechar, e ao fim do segundo quadro a posicao e'
 * recalculada, porque a primeira foi medida antes da rolagem. Depois disso,
 * ou se abriu pelo mouse, rolar fecha (a posicao ficaria velha).
 *
 * Janela por quadros e nao por relogio: nao depende da velocidade da maquina
 * e nao segura um balao velho por 100 ms. Escolhido em vez de "com foco no ⓘ
 * reposiciona sempre", que deixaria o balao aberto na rolagem do usuario
 * depois de um toque (o toque tambem foca o ⓘ).
 */
export function rolagemAoAbrir(
  porFoco: boolean,
  posicionar: () => void,
  fechar: () => void,
  quadro: (cb: () => void) => number = requestAnimationFrame,
  cancelar: (id: number) => void = cancelAnimationFrame,
): { aoRolar: () => void; parar: () => void } {
  if (!porFoco) return { aoRolar: fechar, parar: () => {} };
  let assentando = true;
  let id = quadro(() => {
    id = quadro(() => {
      assentando = false;
      posicionar();
    });
  });
  return {
    aoRolar: () => (assentando ? posicionar() : fechar()),
    parar: () => cancelar(id),
  };
}

export default function InfoDica({
  texto,
  emControle = false,
  className = "",
}: {
  texto: string;
  emControle?: boolean;
  className?: string;
}) {
  const gatilho = useRef<HTMLSpanElement>(null);
  const balao = useRef<HTMLSpanElement>(null);
  const [aberto, setAberto] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number; acima: boolean } | null>(null);
  const porFoco = useRef(false);

  useEffect(() => {
    if (!aberto || !gatilho.current) return;
    const posicionar = () => {
      if (!gatilho.current) return;
      const r = gatilho.current.getBoundingClientRect();
      const largura = Math.min(LARGURA, window.innerWidth - 2 * MARGEM);
      const left = Math.min(
        Math.max(MARGEM, r.left + r.width / 2 - largura / 2),
        window.innerWidth - largura - MARGEM,
      );
      const altura = balao.current?.offsetHeight ?? 0;
      const acima = r.bottom + 8 + altura > window.innerHeight - MARGEM && r.top - 8 - altura > MARGEM;
      setPos({ left, top: acima ? r.top - 8 - altura : r.bottom + 8, acima });
    };
    posicionar();

    const fechar = () => {
      setAberto(false);
      setPos(null);
    };
    const rolagem = rolagemAoAbrir(porFoco.current, posicionar, fechar);
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") fechar();
    };
    window.addEventListener("scroll", rolagem.aoRolar, true);
    window.addEventListener("resize", fechar);
    document.addEventListener("keydown", tecla);
    return () => {
      rolagem.parar();
      window.removeEventListener("scroll", rolagem.aoRolar, true);
      window.removeEventListener("resize", fechar);
      document.removeEventListener("keydown", tecla);
    };
  }, [aberto]);

  if (emControle) {
    return (
      <span aria-hidden="true" title={texto} className={`inline-flex shrink-0 cursor-help text-ink-faint ${className}`}>
        <Info className="h-3.5 w-3.5" strokeWidth={2} />
      </span>
    );
  }

  const abrir = (e: { type: string }) => {
    porFoco.current = e.type === "focus";
    setAberto(true);
  };
  const fechar = () => {
    setAberto(false);
    setPos(null);
  };

  return (
    <span
      ref={gatilho}
      tabIndex={0}
      role="img"
      aria-label={texto}
      onMouseEnter={abrir}
      onMouseLeave={fechar}
      onFocus={abrir}
      onBlur={fechar}
      className={`relative inline-flex shrink-0 cursor-help rounded-full text-ink-faint outline-none transition-colors hover:text-ink-muted focus-visible:ring-2 focus-visible:ring-accent ${className}`}
    >
      <Info aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={2} />
      {aberto &&
        createPortal(
          <span
            ref={balao}
            aria-hidden="true"
            style={{
              left: pos?.left ?? 0,
              top: pos?.top ?? 0,
              width: `min(${LARGURA}px, calc(100vw - ${2 * MARGEM}px))`,
              visibility: pos ? "visible" : "hidden",
            }}
            className={`pointer-events-none fixed z-50 rounded-lg border border-line bg-surface px-3 py-2 text-left text-xs font-normal normal-case leading-snug tracking-normal text-ink-muted shadow-[0_8px_24px_-6px_rgb(var(--tc-ink)/0.18)] motion-safe:animate-[dica-entra_160ms_cubic-bezier(0.2,0.8,0.2,1)] ${
              pos?.acima ? "origin-bottom" : "origin-top"
            }`}
          >
            {texto}
          </span>,
          document.body,
        )}
    </span>
  );
}
