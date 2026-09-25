"use client";

import Segmented from "@/components/ui/Segmented";
import { useTema } from "./ThemeProvider";
import { DESCRICAO_TEMA, ROTULO_TEMA, TEMAS, type PreferenciaTema } from "@/lib/theme";

/**
 * UX-TORRE-1 — seletor Claro / Escuro / Sistema.
 *
 * Tres opcoes, e nao um interruptor de duas: com um interruptor a primeira
 * visita ja' teria de escolher um lado, e quem troca o tema do sistema
 * operacional ao anoitecer perderia o acompanhamento automatico.
 *
 * No mobile so' o icone aparece; o rotulo continua no `aria-label` e no
 * `title`, entao leitor de tela e mouse leem a mesma coisa.
 */
const ICONE: Record<PreferenciaTema, React.ReactNode> = {
  light: <IconeSol />,
  dark: <IconeLua />,
  system: <IconeMonitor />,
};

export default function ThemeToggle() {
  const { preferencia, definir } = useTema();
  return (
    <Segmented
      rotulo="Tema da interface"
      valor={preferencia}
      aoEscolher={definir}
      compacto
      opcoes={TEMAS.map((t) => ({
        valor: t,
        texto: ROTULO_TEMA[t],
        descricao: DESCRICAO_TEMA[t],
        icone: ICONE[t],
      }))}
    />
  );
}

function IconeSol() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" className="h-4 w-4" aria-hidden="true">
      <circle cx="10" cy="10" r="3.5" />
      <path d="M10 2v1.5M10 16.5V18M2 10h1.5M16.5 10H18M4.4 4.4l1 1M14.6 14.6l1 1M15.6 4.4l-1 1M5.4 14.6l-1 1" strokeLinecap="round" />
    </svg>
  );
}

function IconeLua() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" className="h-4 w-4" aria-hidden="true">
      <path d="M16.5 11.8A6.8 6.8 0 0 1 8.2 3.5a6.8 6.8 0 1 0 8.3 8.3Z" strokeLinejoin="round" />
    </svg>
  );
}

function IconeMonitor() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" className="h-4 w-4" aria-hidden="true">
      <rect x="2.5" y="3.5" width="15" height="10" rx="1.5" />
      <path d="M7 16.5h6M10 13.5v3" strokeLinecap="round" />
    </svg>
  );
}
