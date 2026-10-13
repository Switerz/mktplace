"use client";

import { Monitor, Moon, Sun } from "lucide-react";
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
 *
 * UX-TORRE-B2B-1 — no desktop o seletor mora no rodape da sidebar navy
 * (`variante="shell"`). No trilho recolhido, tres alvos de 44px empilhados
 * ocupariam 132px de uma coluna de 64px; ali vira UM botao que avanca para a
 * proxima opcao (`ciclico`), anunciando a atual e a seguinte.
 */
const ICONE: Record<PreferenciaTema, React.ReactNode> = {
  light: <Sun aria-hidden="true" className="h-4 w-4" strokeWidth={1.75} />,
  dark: <Moon aria-hidden="true" className="h-4 w-4" strokeWidth={1.75} />,
  system: <Monitor aria-hidden="true" className="h-4 w-4" strokeWidth={1.75} />,
};

export default function ThemeToggle({
  variante = "padrao",
  ciclico = false,
}: {
  variante?: "padrao" | "shell";
  ciclico?: boolean;
}) {
  const { preferencia, definir } = useTema();

  if (ciclico) {
    const proxima = TEMAS[(TEMAS.indexOf(preferencia) + 1) % TEMAS.length];
    const rotulo = `Tema: ${ROTULO_TEMA[preferencia]}. Mudar para ${ROTULO_TEMA[proxima]}`;
    return (
      <button
        type="button"
        onClick={() => definir(proxima)}
        aria-label={rotulo}
        title={rotulo}
        className="mx-auto flex min-h-11 min-w-11 items-center justify-center rounded-md text-shell-muted transition-colors hover:bg-shell-raised hover:text-shell-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-shell-accent"
      >
        {ICONE[preferencia]}
      </button>
    );
  }

  return (
    <Segmented
      rotulo="Tema da interface"
      valor={preferencia}
      aoEscolher={definir}
      compacto={variante === "padrao"}
      soIcone={variante === "shell"}
      tom={variante}
      className={variante === "shell" ? "flex w-full justify-between" : ""}
      opcoes={TEMAS.map((t) => ({
        valor: t,
        texto: ROTULO_TEMA[t],
        descricao: DESCRICAO_TEMA[t],
        icone: ICONE[t],
      }))}
    />
  );
}
