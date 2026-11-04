"use client";

import { LogOut } from "lucide-react";
import { useSessao } from "./LoginGate";

/**
 * AUTH-GOOGLE-6 — conta e "Sair" no shell. So aparece com login ligado.
 * `compacto`: trilho recolhido da sidebar e topbar (so icone, e-mail no rotulo).
 */
export function iniciaisDe(email: string): string {
  const base = email.split("@")[0] ?? "";
  const partes = base.split(/[._-]+/).filter(Boolean);
  const letras = partes.length > 1 ? partes[0][0] + partes[1][0] : base.slice(0, 2);
  return (letras || "?").toUpperCase();
}

async function sair() {
  try {
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  } catch {
    // o reload abaixo mostra o estado real
  }
  window.google?.accounts?.id?.disableAutoSelect?.();
  window.location.reload();
}

export default function ContaUsuario({ compacto = false }: { compacto?: boolean }) {
  const sessao = useSessao();
  if (!sessao?.authEnabled) return null;

  const botao =
    "flex min-h-11 items-center justify-center gap-2 rounded-md text-xs font-medium text-shell-muted transition-colors hover:bg-shell-raised hover:text-shell-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-shell-accent";

  if (compacto) {
    return (
      <button
        type="button"
        onClick={sair}
        aria-label={`Sair (${sessao.email})`}
        title={`Sair (${sessao.email})`}
        className={`${botao} mx-auto min-w-11`}
      >
        <LogOut aria-hidden="true" className="h-4 w-4" strokeWidth={1.75} />
      </button>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span
        aria-hidden="true"
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-shell-raised text-[11px] font-semibold text-shell-ink"
      >
        {iniciaisDe(sessao.email)}
      </span>
      <span className="min-w-0 flex-1 truncate text-xs text-shell-muted" title={sessao.email}>
        {sessao.email}
      </span>
      <button type="button" onClick={sair} className={`${botao} shrink-0 px-3`}>
        <LogOut aria-hidden="true" className="h-4 w-4" strokeWidth={1.75} />
        Sair
      </button>
    </div>
  );
}
