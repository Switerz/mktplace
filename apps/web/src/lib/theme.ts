/**
 * UX-TORRE-1 — contrato do tema da Torre de Controle.
 *
 * Logica PURA, sem React e sem DOM, pelo mesmo motivo do contrato da
 * Expedicao: a decisao "qual tema vale agora" precisa ser testavel sem
 * harness, e o componente so' aplica o que estas funcoes devolvem.
 *
 * TRES ESTADOS, NAO DOIS
 * ----------------------
 *   "light" / "dark"  escolha MANUAL — sobrepoe o sistema operacional.
 *   "system"          segue `prefers-color-scheme`, inclusive quando ele muda
 *                     com a tela aberta.
 *
 * Isso e' o motivo de `darkMode: "class"` no Tailwind: com `darkMode: "media"`
 * nao existiria escolha manual, apenas o que o SO mandasse.
 */

export const THEME_STORAGE_KEY = "torre-tema";

export const TEMAS = ["light", "dark", "system"] as const;
export type PreferenciaTema = (typeof TEMAS)[number];

/** O tema efetivamente pintado. "system" nunca chega ao DOM. */
export type TemaResolvido = "light" | "dark";

/** Primeira visita segue o sistema — nunca impoe claro nem escuro. */
export const TEMA_PADRAO: PreferenciaTema = "system";

export const ROTULO_TEMA: Record<PreferenciaTema, string> = {
  light: "Claro",
  dark: "Escuro",
  system: "Sistema",
};

export const DESCRICAO_TEMA: Record<PreferenciaTema, string> = {
  light: "Tema claro, independente do sistema operacional",
  dark: "Tema escuro, independente do sistema operacional",
  system: "Acompanha a preferência do sistema operacional",
};

export const CONSULTA_ESCURO = "(prefers-color-scheme: dark)";

/** Classe que devolve uma rota inteira ao tema claro (ver globals.css). */
export const CLASSE_TEMA_CLARO_FIXO = "tc-tema-claro";

/**
 * FAIL-SAFE, nao fail-closed: valor estranho no `localStorage` (outra aba,
 * extensao, chave reaproveitada) cai no padrao em vez de quebrar a tela.
 */
export function sanitizarTema(valor: string | null | undefined): PreferenciaTema {
  return (TEMAS as readonly string[]).includes(valor as string)
    ? (valor as PreferenciaTema)
    : TEMA_PADRAO;
}

/** Preferencia + sistema -> tema pintado. Unica fonte da verdade. */
export function resolverTema(
  preferencia: PreferenciaTema,
  sistemaPrefereEscuro: boolean,
): TemaResolvido {
  if (preferencia === "system") return sistemaPrefereEscuro ? "dark" : "light";
  return preferencia;
}

/**
 * Rotas ja migradas para os tokens semanticos.
 *
 * Enquanto a migracao nao termina, uma rota FORA desta lista renderiza no tema
 * claro mesmo com o escuro ligado — ela foi desenhada em literais claros, e
 * deixa-la sob o fundo escuro produziria exatamente o defeito que este gate
 * existe para eliminar: um pedaco claro perdido dentro do escuro.
 *
 * Cada PR da sequencia (`/`, `/canais`, `/financeiro`, depois o resto) remove
 * a propria rota desta barreira ao migrar.
 */
export const ROTAS_TEMATIZADAS = ["/expedicao"] as const;

export function rotaTematizada(pathname: string | null | undefined): boolean {
  if (!pathname) return false;
  return ROTAS_TEMATIZADAS.some(
    (rota) => pathname === rota || pathname.startsWith(`${rota}/`),
  );
}

/**
 * Script sincrono injetado no `<head>`, ANTES da primeira pintura.
 *
 * Sem ele a pagina pinta o tema claro do HTML do servidor e so' entao o React
 * hidrata e troca para o escuro — o flash branco que todo dark mode mal feito
 * tem. O servidor nao pode adivinhar: `localStorage` e `prefers-color-scheme`
 * so' existem no navegador.
 *
 * O `try/catch` e' obrigatorio: `localStorage` LEVANTA (nao devolve null) em
 * janela anonima com cookies bloqueados, e uma excecao aqui deixaria a pagina
 * inteira sem tema.
 *
 * `data-tema` guarda a PREFERENCIA (inclusive "system"); a classe `dark`
 * guarda o RESULTADO. Sao coisas diferentes e o seletor precisa das duas.
 */
export function scriptDeTema(): string {
  return `(function(){try{var k=${JSON.stringify(THEME_STORAGE_KEY)};var p=window.localStorage.getItem(k);if(p!=="light"&&p!=="dark"&&p!=="system"){p=${JSON.stringify(
    TEMA_PADRAO,
  )};}var d=p==="dark"||(p==="system"&&window.matchMedia(${JSON.stringify(
    CONSULTA_ESCURO,
  )}).matches);var e=document.documentElement;e.classList.toggle("dark",d);e.style.colorScheme=d?"dark":"light";e.setAttribute("data-tema",p);}catch(_){}})();`;
}

/** Aplica no DOM o que `resolverTema` decidiu. Idempotente. */
export function aplicarTemaNoDocumento(
  raiz: {
    classList: { toggle: (c: string, on: boolean) => void };
    style: { colorScheme: string };
    setAttribute: (nome: string, valor: string) => void;
  },
  preferencia: PreferenciaTema,
  sistemaPrefereEscuro: boolean,
): TemaResolvido {
  const resolvido = resolverTema(preferencia, sistemaPrefereEscuro);
  raiz.classList.toggle("dark", resolvido === "dark");
  raiz.style.colorScheme = resolvido;
  raiz.setAttribute("data-tema", preferencia);
  return resolvido;
}
