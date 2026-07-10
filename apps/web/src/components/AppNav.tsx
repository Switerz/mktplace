"use client";

import { Suspense } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { FILTER_AWARE_PAGES, isFilterAwarePath, appendQuery } from "@/lib/filters/nav-links";

const FILTER_QUERY_KEYS = ["channels", "brands", "date_from", "date_to", "compare"];

interface NavPage {
  href: string;
  label: string;
  badge?: string;
  disabled?: boolean;
}

interface NavSection {
  label: string;
  pages: NavPage[];
}

const SECTIONS: NavSection[] = [
  {
    label: "Cockpits",
    pages: [
      { href: "/", label: "Gerencial" },
      { href: "/canais", label: "Canais" },
      { href: "/produtos", label: "Produtos" },
      { href: "/qualidade", label: "Qualidade" },
      { href: "/financeiro", label: "Financeiro" },
      { href: "/regioes", label: "Regiões" },
      { href: "/tempo-real", label: "Tempo Real" },
    ],
  },
  {
    label: "Pedidos",
    pages: [
      { href: "/pedidos", label: "Geral" },
      { href: "/pedidos/tiktok", label: "TikTok Shop", badge: "Em breve", disabled: true },
      { href: "/pedidos/ml", label: "Mercado Livre", badge: "Em breve", disabled: true },
    ],
  },
  {
    label: "Inteligência",
    pages: [
      { href: "/inteligencia", label: "Ações ML + TikTok" },
    ],
  },
  {
    label: "Operações",
    pages: [
      { href: "/operacoes", label: "Criadores + Alertas" },
    ],
  },
];

const NAV_FALLBACK = <nav className="bg-white border-b border-violet-100 h-[46px]" aria-label="Navegação principal" />;

export default function AppNav() {
  return (
    <Suspense fallback={NAV_FALLBACK}>
      <AppNavInner />
    </Suspense>
  );
}

function AppNavInner() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const preservedQuery = isFilterAwarePath(pathname)
    ? (() => {
        const qs = new URLSearchParams();
        for (const key of FILTER_QUERY_KEYS) {
          const v = searchParams.get(key);
          if (v) qs.set(key, v);
        }
        return qs.toString();
      })()
    : "";

  function hrefFor(pageHref: string): string {
    if (!FILTER_AWARE_PAGES.has(pageHref)) return pageHref;
    return appendQuery(pageHref, preservedQuery);
  }

  return (
    <nav className="bg-white border-b border-violet-100" aria-label="Navegação principal">
      <div className="max-w-7xl mx-auto px-6 overflow-x-auto">
        <div className="flex items-stretch gap-0 min-w-max">
          {SECTIONS.map((section, si) => (
            <div key={section.label} className="flex items-stretch">
              {si > 0 && (
                <div className="flex items-center px-1">
                  <div className="w-px h-5 bg-violet-100" />
                </div>
              )}
              <div className="flex items-stretch gap-0">
                <span className="flex items-center text-[10px] font-semibold text-slate-600 uppercase tracking-wide px-3 select-none">
                  {section.label}
                </span>
                {section.pages.map((page) => {
                  const isActive =
                    page.href === "/"
                      ? pathname === "/" || pathname.startsWith("/brand")
                      : pathname.startsWith(page.href);
                  if (page.disabled) {
                    return (
                      <span
                        key={page.href}
                        className="flex items-center gap-1.5 px-3 py-3 text-sm text-slate-300 cursor-default select-none"
                        title={page.badge}
                      >
                        {page.label}
                        {page.badge && (
                          <span className="text-[9px] font-semibold text-slate-300 border border-slate-200 rounded px-1 leading-4 uppercase tracking-wide">
                            {page.badge}
                          </span>
                        )}
                      </span>
                    );
                  }
                  return (
                    <Link
                      key={page.href}
                      href={hrefFor(page.href)}
                      className={`flex items-center px-3 py-3 text-sm font-medium border-b-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-inset ${
                        isActive
                          ? "border-violet-600 text-violet-700"
                          : "border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-200"
                      }`}
                    >
                      {page.label}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </nav>
  );
}
