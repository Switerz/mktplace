"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  CONSULTA_ESCURO,
  TEMA_PADRAO,
  THEME_STORAGE_KEY,
  aplicarTemaNoDocumento,
  resolverTema,
  sanitizarTema,
  type PreferenciaTema,
  type TemaResolvido,
} from "@/lib/theme";

interface Contexto {
  preferencia: PreferenciaTema;
  resolvido: TemaResolvido;
  definir: (p: PreferenciaTema) => void;
  /** Falso ate' a preferencia real ser lida do navegador. */
  pronto: boolean;
}

const TemaContexto = createContext<Contexto | null>(null);

/**
 * UX-TORRE-1 — estado do tema.
 *
 * O provedor NAO pinta o tema na primeira carga: quem faz isso e' o script
 * sincrono do `<head>` (`scriptDeTema`), antes da primeira pintura. Aqui o
 * estado apenas ACOMPANHA o que ja' esta no DOM, para que o seletor mostre a
 * opcao certa e para que os graficos saibam qual paleta usar.
 *
 * Comecar com `TEMA_PADRAO` e corrigir no efeito e' deliberado: ler
 * `localStorage` durante o render faria o HTML do servidor divergir do
 * primeiro render do cliente — o erro de hidratacao classico.
 */
export default function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [preferencia, setPreferencia] = useState<PreferenciaTema>(TEMA_PADRAO);
  const [resolvido, setResolvido] = useState<TemaResolvido>("light");
  const [pronto, setPronto] = useState(false);

  // 1. Le a preferencia gravada e sincroniza com o que o script ja' aplicou.
  useEffect(() => {
    let guardada: string | null = null;
    // `localStorage` LEVANTA em janela anonima com armazenamento bloqueado.
    try {
      guardada = window.localStorage.getItem(THEME_STORAGE_KEY);
    } catch {
      guardada = null;
    }
    const p = sanitizarTema(guardada);
    const escuro = window.matchMedia(CONSULTA_ESCURO).matches;
    setPreferencia(p);
    setResolvido(aplicarTemaNoDocumento(document.documentElement, p, escuro));
    setPronto(true);
  }, []);

  // 2. "Sistema" precisa continuar seguindo o sistema COM A TELA ABERTA —
  //    quem troca o tema do SO ao anoitecer espera a Torre acompanhar.
  useEffect(() => {
    const mq = window.matchMedia(CONSULTA_ESCURO);
    const aoMudar = (e: MediaQueryListEvent) => {
      if (preferencia !== "system") return;
      setResolvido(aplicarTemaNoDocumento(document.documentElement, "system", e.matches));
    };
    mq.addEventListener("change", aoMudar);
    return () => mq.removeEventListener("change", aoMudar);
  }, [preferencia]);

  const definir = useCallback((p: PreferenciaTema) => {
    setPreferencia(p);
    const escuro = window.matchMedia(CONSULTA_ESCURO).matches;
    setResolvido(aplicarTemaNoDocumento(document.documentElement, p, escuro));
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, p);
    } catch {
      // Sem persistencia a escolha vale so' nesta aba — melhor que quebrar.
    }
  }, []);

  const valor = useMemo(
    () => ({ preferencia, resolvido, definir, pronto }),
    [preferencia, resolvido, definir, pronto],
  );

  return <TemaContexto.Provider value={valor}>{children}</TemaContexto.Provider>;
}

/**
 * Fora do provedor devolve o tema claro em vez de levantar: um grafico
 * renderizado em teste ou em storybook nao deve derrubar a arvore.
 */
export function useTema(): Contexto {
  return (
    useContext(TemaContexto) ?? {
      preferencia: TEMA_PADRAO,
      resolvido: resolverTema(TEMA_PADRAO, false),
      definir: () => {},
      pronto: false,
    }
  );
}
