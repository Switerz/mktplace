"use client";

import { Suspense, useEffect, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import ThemeToggle from "@/components/theme/ThemeToggle";
import { buildPreservedQuery, hrefForPage } from "@/lib/filters/nav-links";
import NavList from "./NavList";

/**
 * UX-TORRE-B2B-1 — modo da sidebar.
 *
 *   "auto"      CSS decide: trilho de icones abaixo de 1280px, aberta acima.
 *               E' o estado do primeiro paint (servidor e hidratacao), entao
 *               nao ha salto de layout para quem nunca mexeu no botao.
 *   "aberta" /
 *   "recolhida" escolha explicita do usuario.
 *
 * So' a escolha feita em tela LARGA (>= 1280px) vira preferencia salva. Em
 * notebook pequeno ou tablet a barra recolhe sozinha a cada carga — abrir ali
 * e' um ato momentaneo, e salvar faria o trilho sumir de novo na tela grande.
 */
type ModoSidebar = "auto" | "aberta" | "recolhida";

export const SIDEBAR_STORAGE_KEY = "torre-sidebar";
const LARGURA_TELA_LARGA = 1280;

const telaLarga = () => typeof window !== "undefined" && window.innerWidth >= LARGURA_TELA_LARGA;

const SIDEBAR_FALLBACK = (
  <aside
    aria-label="Navegação principal"
    className="hidden md:block md:w-16 md:shrink-0 md:bg-shell xl:w-60"
  />
);

/**
 * Sidebar persistente no desktop (Gate U1), agora a moldura navy do shell
 * (UX-TORRE-B2B-1, que substitui a regra antiga de "sidebar sempre clara" por
 * decisao do Mario em 13/10/2026).
 *
 * Navy com fundo violeta, nao carvao: o anti-referencia do Metabase era a
 * barra cinza generica com azul eletrico, e e' isso que continua proibido.
 */
export default function Sidebar() {
  return (
    <Suspense fallback={SIDEBAR_FALLBACK}>
      <SidebarInner />
    </Suspense>
  );
}

function SidebarInner() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const preservedQuery = buildPreservedQuery(pathname, (key) => searchParams.get(key));
  const hrefFor = (pageHref: string) => hrefForPage(pageHref, preservedQuery);

  const [modo, setModo] = useState<ModoSidebar>("auto");
  // A largura so' anima DEPOIS de aplicar a preferencia salva: sem isto, quem
  // salvou "recolhida" via a barra encolher sozinha a cada carga.
  const [animar, setAnimar] = useState(false);

  // Preferencia salva so' vale em tela larga. `localStorage` LEVANTA em janela
  // anonima com cookies bloqueados: falha cai no modo automatico.
  useEffect(() => {
    if (!telaLarga()) return;
    try {
      const salvo = window.localStorage.getItem(SIDEBAR_STORAGE_KEY);
      if (salvo === "aberta" || salvo === "recolhida") setModo(salvo);
    } catch {
      /* sem armazenamento: segue automatico */
    }
  }, []);

  useEffect(() => {
    // Dois quadros: o primeiro pinta a largura salva, o segundo liga a animacao.
    let id2 = 0;
    const id1 = window.requestAnimationFrame(() => {
      id2 = window.requestAnimationFrame(() => setAnimar(true));
    });
    return () => {
      window.cancelAnimationFrame(id1);
      window.cancelAnimationFrame(id2);
    };
  }, []);

  // Fora da tela larga, trocar de rota devolve ao automatico (recolhida).
  useEffect(() => {
    if (!telaLarga()) setModo("auto");
  }, [pathname]);

  const recolhidaAgora = () => {
    if (modo !== "auto") return modo === "recolhida";
    return !telaLarga();
  };

  function alternar() {
    const proximo: ModoSidebar = recolhidaAgora() ? "aberta" : "recolhida";
    setModo(proximo);
    if (telaLarga()) {
      try {
        window.localStorage.setItem(SIDEBAR_STORAGE_KEY, proximo);
      } catch {
        /* preferencia nao persiste; o estado da sessao continua valendo */
      }
    }
  }

  // Classes por modo. Em "auto" quem decide e' o breakpoint `xl` (1280px).
  const largura =
    modo === "aberta" ? "md:w-60" : modo === "recolhida" ? "md:w-16" : "md:w-16 xl:w-60";
  const soAberta =
    modo === "aberta" ? "block" : modo === "recolhida" ? "hidden" : "hidden xl:block";
  const soRecolhida =
    modo === "aberta" ? "hidden" : modo === "recolhida" ? "block" : "block xl:hidden";

  return (
    // Duas camadas de proposito: a EXTERNA estica com o flex do shell e pinta
    // a faixa ate o rodape; a INTERNA gruda no topo e rola sozinha.
    <aside
      className={`hidden md:block md:shrink-0 md:border-r md:border-shell-line md:bg-shell ${animar ? "transition-[width] duration-200" : ""} ${largura}`}
    >
      <div className="sticky top-0 flex h-screen flex-col">
        {/* Identidade + botao de recolher */}
        <div className="flex items-center gap-2.5 border-b border-shell-line px-3 py-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent">
            <span className="text-xs font-bold tracking-tight text-accent-on">TC</span>
          </div>
          <div className={`min-w-0 flex-1 ${soAberta}`}>
            <p className="truncate text-sm font-semibold leading-tight text-shell-ink">Torre de Controle</p>
            <p className="truncate text-xs leading-tight text-shell-faint">Marketplaces · Gobeaute</p>
          </div>
          <button
            type="button"
            onClick={alternar}
            aria-label="Recolher navegação"
            aria-expanded={true}
            title="Recolher navegação"
            className={`h-8 w-8 shrink-0 items-center justify-center rounded-md text-shell-faint transition-colors hover:bg-shell-raised hover:text-shell-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-shell-accent ${soAberta === "block" ? "flex" : soAberta === "hidden" ? "hidden" : "hidden xl:flex"}`}
          >
            <PanelLeftClose aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>

        {/* Expandir: so' no trilho, logo abaixo da marca */}
        <div className={`border-b border-shell-line py-2 ${soRecolhida}`}>
          <button
            type="button"
            onClick={alternar}
            aria-label="Expandir navegação"
            aria-expanded={false}
            title="Expandir navegação"
            className="mx-auto flex h-8 w-8 items-center justify-center rounded-md text-shell-faint transition-colors hover:bg-shell-raised hover:text-shell-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-shell-accent"
          >
            <PanelLeftOpen aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
          {/* Dois NavList e nao um com prop dinamica: em "auto" o breakpoint
              escolhe qual aparece sem esperar a hidratacao. O escondido sai da
              arvore de acessibilidade por `display: none`. */}
          <div className={soAberta}>
            <NavList pathname={pathname} hrefFor={hrefFor} />
          </div>
          <div className={soRecolhida}>
            <NavList pathname={pathname} hrefFor={hrefFor} recolhido />
          </div>
        </div>

        {/* Rodape: o tema mora aqui no desktop (a topbar so' existe no mobile) */}
        <div className="border-t border-shell-line px-2 py-2.5">
          <div className={soAberta}>
            <ThemeToggle variante="shell" />
          </div>
          <div className={soRecolhida}>
            <ThemeToggle variante="shell" ciclico />
          </div>
        </div>
      </div>
    </aside>
  );
}
