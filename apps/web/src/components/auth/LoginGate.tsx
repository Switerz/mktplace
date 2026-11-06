"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { EVENTO_SESSAO_EXPIRADA } from "@/lib/torre-fetch";
import { MENSAGEM_SESSAO_EXPIRADA, mensagemDeErroLogin } from "@/lib/login-mensagens";
import MarcaTorre from "@/components/shell/MarcaTorre";

/**
 * AUTH-GOOGLE-6 — porta do LoginGate do GoHunter. Enquanto nao ha sessao, mostra
 * "Entrar com Google" no lugar do app. Isto e so experiencia: a protecao real
 * esta no middleware e na API. O client id vem do 401 de /api/auth/me, nao de
 * variavel NEXT_PUBLIC.
 */
export interface SessaoUsuario {
  authEnabled: boolean;
  email: string;
  name?: string;
  picture?: string;
}

const SessaoContext = createContext<SessaoUsuario | null>(null);

/** Sessao atual, ou null fora do gate / antes de autenticar. */
export function useSessao(): SessaoUsuario | null {
  return useContext(SessaoContext);
}

interface GoogleCredentialResponse {
  credential?: string;
}
interface GoogleAccountsId {
  initialize(opts: { client_id: string; callback: (resp: GoogleCredentialResponse) => void }): void;
  renderButton(parent: HTMLElement, opts: Record<string, unknown>): void;
  disableAutoSelect?: () => void;
}
declare global {
  interface Window {
    google?: { accounts?: { id?: GoogleAccountsId } };
  }
}

type Estado = "checking" | "authed" | "anon";

export default function LoginGate({ children }: { children: React.ReactNode }) {
  const [estado, setEstado] = useState<Estado>("checking");
  const [sessao, setSessao] = useState<SessaoUsuario | null>(null);
  const [clientId, setClientId] = useState<string | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const botaoRef = useRef<HTMLDivElement>(null);

  // 1) Ha sessao? 200 -> entra; 401 -> login; rede falhou -> login com aviso.
  useEffect(() => {
    let cancelado = false;
    (async () => {
      try {
        const r = await fetch("/api/auth/me", { credentials: "same-origin" });
        if (cancelado) return;
        if (r.ok) {
          setSessao((await r.json()) as SessaoUsuario);
          setEstado("authed");
          return;
        }
        const corpo = await r.json().catch(() => ({}));
        if (cancelado) return;
        setClientId(typeof corpo?.clientId === "string" ? corpo.clientId : null);
        setEstado("anon");
      } catch {
        if (cancelado) return;
        setErro(mensagemDeErroLogin(0, null));
        setEstado("anon");
      }
    })();
    return () => {
      cancelado = true;
    };
  }, []);

  // 2) A API avisou que a sessao acabou.
  useEffect(() => {
    const aoExpirar = () => {
      setSessao(null);
      setErro(MENSAGEM_SESSAO_EXPIRADA);
      setEstado("anon");
    };
    window.addEventListener(EVENTO_SESSAO_EXPIRADA, aoExpirar);
    return () => window.removeEventListener(EVENTO_SESSAO_EXPIRADA, aoExpirar);
  }, []);

  // Sessao expirada depois de autenticar: o client id ainda nao foi lido.
  useEffect(() => {
    if (estado !== "anon" || clientId) return;
    let cancelado = false;
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then((r) => r.json().catch(() => ({})))
      .then((c) => {
        if (!cancelado && typeof c?.clientId === "string") setClientId(c.clientId);
      })
      .catch(() => {});
    return () => {
      cancelado = true;
    };
  }, [estado, clientId]);

  // 3) Anonimo com client id: carrega o GIS uma vez e desenha o botao.
  useEffect(() => {
    if (estado !== "anon" || !clientId) return;

    const aoReceberCredencial = async (resp: GoogleCredentialResponse) => {
      if (!resp.credential) return;
      setEnviando(true);
      setErro(null);
      try {
        const r = await fetch("/api/auth/google", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ credential: resp.credential }),
        });
        if (r.ok) {
          window.location.reload();
          return;
        }
        const corpo = await r.json().catch(() => null);
        setErro(mensagemDeErroLogin(r.status, corpo));
      } catch {
        setErro(mensagemDeErroLogin(0, null));
      } finally {
        setEnviando(false);
      }
    };

    const iniciar = () => {
      const id = window.google?.accounts?.id;
      if (!id || !botaoRef.current) return;
      id.initialize({ client_id: clientId, callback: aoReceberCredencial });
      id.renderButton(botaoRef.current, {
        theme: "outline",
        size: "large",
        text: "signin_with",
        shape: "pill",
        locale: "pt-BR",
      });
    };

    const existente = document.getElementById("gis-script");
    if (existente) {
      iniciar();
      return;
    }
    const script = document.createElement("script");
    script.id = "gis-script";
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.onload = iniciar;
    document.head.appendChild(script);
  }, [estado, clientId]);

  if (estado === "checking") {
    return (
      <div className="tc-fundo flex min-h-screen items-center justify-center bg-canvas">
        <div role="status" aria-live="polite" className="flex items-center gap-3 text-sm text-ink-muted">
          <span
            aria-hidden="true"
            className="h-5 w-5 animate-spin rounded-full border-2 border-line-strong border-t-accent motion-reduce:animate-none"
          />
          Verificando acesso…
        </div>
      </div>
    );
  }

  if (estado === "authed") {
    return <SessaoContext.Provider value={sessao}>{children}</SessaoContext.Provider>;
  }

  return (
    <div className="tc-fundo flex min-h-screen items-center justify-center bg-canvas px-4">
      <section
        aria-labelledby="login-titulo"
        className="flex w-full max-w-sm flex-col items-center gap-5 rounded-xl border border-line bg-surface px-8 py-10 text-center"
      >
        <MarcaTorre tamanho={44} className="h-11 w-11 rounded-md" />
        <div className="space-y-1">
          <h1 id="login-titulo" className="text-lg font-semibold text-ink">
            Torre de Controle · Gobeaute
          </h1>
          <p className="text-sm text-ink-muted">Acesso restrito a contas GoGroup.</p>
        </div>
        {clientId ? (
          <div
            ref={botaoRef}
            aria-busy={enviando}
            className={`flex min-h-11 items-center justify-center ${enviando ? "opacity-50" : ""}`}
          />
        ) : (
          <p className="text-xs text-ink-muted">Login Google indisponível no momento.</p>
        )}
        <p
          role="alert"
          className={erro ? "rounded-md bg-crit-soft px-3 py-2 text-xs text-crit-ink" : "sr-only"}
        >
          {erro}
        </p>
      </section>
    </div>
  );
}
