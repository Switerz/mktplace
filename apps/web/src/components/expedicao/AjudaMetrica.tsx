"use client";

/**
 * T-0010 — o `?` das metricas da Expedicao.
 *
 * Um `<button>` real, com alvo de 44px, que abre um painel curto com os seis
 * campos fixos do catalogo (`expedicao-definicoes.ts`): definicao, formula,
 * fonte, janela, cobertura e limitacoes — precedidos do rotulo de origem
 * ("Oficial do marketplace" / "Cálculo da Torre" / indisponivel) e seguidos
 * da referencia externa, quando houver.
 *
 * Comportamento:
 *   - abre por clique/toque e por teclado (Enter/Espaco sao o clique nativo);
 *   - abre por hover do mouse depois de um atraso curto, e fecha quando o
 *     ponteiro sai — a menos que tenha sido aberto por clique;
 *   - fecha com Esc (devolvendo o foco ao botao), com clique fora e no botao
 *     "Fechar"; se o foco estava dentro do painel, ele volta ao botao.
 *
 * SO' `<span>`: o `?` mora dentro de `<p>` (a linha de comparacao do KpiTile),
 * e `<div>` dentro de `<p>` quebra a hidratacao. O painel usa posicao FIXA,
 * calculada na abertura: assim ele escapa do `overflow-hidden` dos paineis e
 * nunca sai da tela em 390 px.
 */
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as EventoPonteiro,
} from "react";

import { CAMPOS_DEFINICAO, ROTULO_ORIGEM, definicaoDe, type ChaveMetrica } from "@/lib/expedicao-definicoes";

const ATRASO_HOVER_MS = 250;
const ATRASO_SAIDA_MS = 150;
const MARGEM = 8;
const LARGURA_MAX = 320;
const SELETOR_FOCAVEL = "a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex='-1'])";

type Origem = "clique" | "hover";

export default function AjudaMetrica({
  chave,
  className = "",
  abertoInicial = false,
}: {
  chave: ChaveMetrica;
  className?: string;
  /** Nasce aberto (renderizacao estatica em teste; a tela nao usa). */
  abertoInicial?: boolean;
}) {
  const def = definicaoDe(chave);
  const id = useId();
  const [aberto, setAberto] = useState<Origem | null>(abertoInicial ? "clique" : null);
  const [pos, setPos] = useState<{ top: number; left: number; largura: number } | null>(null);
  const botao = useRef<HTMLButtonElement>(null);
  const painel = useRef<HTMLSpanElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const limparTimer = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };

  const fechar = useCallback((devolverFoco: boolean) => {
    limparTimer();
    setAberto(null);
    setPos(null);
    if (devolverFoco) botao.current?.focus();
  }, []);

  // Posicao fixa, presa a' viewport: abaixo do botao, ou acima se nao couber.
  const posicionar = useCallback(() => {
    const b = botao.current;
    if (!b) return;
    const r = b.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const largura = Math.min(LARGURA_MAX, vw - 2 * MARGEM);
    const left = Math.min(Math.max(MARGEM, r.left + r.width / 2 - largura / 2), vw - MARGEM - largura);
    const altura = painel.current?.offsetHeight ?? 0;
    const abaixo = r.bottom + 4;
    const top = abaixo + altura > vh - MARGEM && r.top - 4 - altura >= MARGEM ? r.top - 4 - altura : abaixo;
    setPos({ top, left, largura });
  }, []);

  useLayoutEffect(() => {
    if (!aberto) return;
    posicionar();
  }, [aberto, posicionar]);

  useEffect(() => {
    if (!aberto) return;
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") fechar(true);
    };
    const fora = (e: PointerEvent) => {
      const alvo = e.target as Node;
      if (botao.current?.contains(alvo) || painel.current?.contains(alvo)) return;
      // Clique num elemento focavel fora do painel: o foco e' DELE, nao do
      // `?`. Clique em area nao focavel: o `mousedown` padrao (que vem DEPOIS
      // deste `pointerdown`) joga o foco no <body>; no proximo quadro o foco
      // volta ao botao, so' se tiver mesmo caido no <body> (QA T-0010, F1).
      const focavel = alvo instanceof Element && Boolean(alvo.closest(SELETOR_FOCAVEL));
      fechar(false);
      if (!focavel) {
        requestAnimationFrame(() => {
          if (!document.activeElement || document.activeElement === document.body) botao.current?.focus();
        });
      }
    };
    document.addEventListener("keydown", tecla);
    document.addEventListener("pointerdown", fora);
    window.addEventListener("resize", posicionar);
    window.addEventListener("scroll", posicionar, true);
    return () => {
      document.removeEventListener("keydown", tecla);
      document.removeEventListener("pointerdown", fora);
      window.removeEventListener("resize", posicionar);
      window.removeEventListener("scroll", posicionar, true);
    };
  }, [aberto, fechar, posicionar]);

  useEffect(() => limparTimer, []);

  const entrar = (e: EventoPonteiro) => {
    if (e.pointerType !== "mouse") return;
    limparTimer();
    if (aberto) return;
    timer.current = setTimeout(() => setAberto("hover"), ATRASO_HOVER_MS);
  };
  const sair = (e: EventoPonteiro) => {
    if (e.pointerType !== "mouse") return;
    limparTimer();
    if (aberto === "hover") timer.current = setTimeout(() => setAberto(null), ATRASO_SAIDA_MS);
  };

  return (
    <span className={`relative inline-flex align-middle ${className}`} onPointerEnter={entrar} onPointerLeave={sair}>
      <button
        ref={botao}
        type="button"
        aria-label={`O que é ${def.nome}`}
        aria-expanded={Boolean(aberto)}
        // Fechado, o painel nao existe no DOM: sem alvo, sem aria-controls.
        aria-controls={aberto ? id : undefined}
        onClick={() => {
          limparTimer();
          // Clique num `?` aberto por hover FIXA o painel; num aberto por
          // clique, fecha.
          setAberto((a) => (a === "clique" ? null : "clique"));
        }}
        className="-my-3 inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-ink-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-6px] focus-visible:outline-accent"
      >
        <span
          aria-hidden="true"
          className="inline-flex h-[18px] w-[18px] items-center justify-center rounded-full border border-line-strong text-xs font-semibold leading-none"
        >
          ?
        </span>
      </button>
      {/* QA T-0010 (B1): o painel FECHADO nao e' renderizado. Com `hidden` +
          `block`, o `.block` do Tailwind vencia o `[hidden]` do preflight e os
          paineis ficavam abertos sobre a tela (e o "Fechar" entrava no Tab). */}
      {aberto ? (
      <span
        ref={painel}
        id={id}
        role="dialog"
        aria-label={def.nome}
        // Ate' a posicao ser medida, invisivel (sem piscar no canto).
        style={pos ? { top: pos.top, left: pos.left, width: pos.largura } : { visibility: "hidden" }}
        className="fixed z-50 block rounded-xl border border-line bg-surface p-3 text-left text-xs font-normal normal-case leading-snug tracking-normal text-ink-muted shadow-[0_8px_24px_-6px_rgb(var(--tc-ink)/0.18)]"
      >
        <span className="mb-1.5 flex items-start justify-between gap-2">
          <span className="block text-sm font-semibold text-ink">{def.nome}</span>
          <button
            type="button"
            onClick={() => fechar(true)}
            className="-my-2 -mr-2 inline-flex min-h-11 min-w-11 items-center justify-center rounded-md text-xs font-medium text-ink-muted underline underline-offset-4 hover:text-ink"
          >
            Fechar
          </button>
        </span>
        {/* Revisao T-0010: de onde vem o numero, ANTES da definicao. */}
        <span className="block">
          <span
            data-origem={def.origem}
            className="inline-block rounded bg-raised px-1.5 py-0.5 font-semibold text-ink ring-1 ring-inset ring-line"
          >
            {ROTULO_ORIGEM[def.origem]}
          </span>
        </span>
        {CAMPOS_DEFINICAO.map(([campo, rotulo]) => (
          <span key={campo} className="mt-1 block">
            <span className="font-semibold text-ink">{rotulo}: </span>
            {def[campo]}
          </span>
        ))}
        {def.referencia ? (
          <span className="mt-1 block">
            <span className="font-semibold text-ink">Referência: </span>
            {def.referencia}
          </span>
        ) : null}
      </span>
      ) : null}
    </span>
  );
}
