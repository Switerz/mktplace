import Link from "next/link";
import { NAV_SECTIONS, isNavItemActive } from "./nav-config";

interface NavListProps {
  pathname: string;
  hrefFor: (pageHref: string) => string;
}

/**
 * Renderizacao dos grupos/links de navegacao — compartilhada pela Sidebar
 * (desktop) e pelo MobileDrawer, para nao duplicar a marcacao de item ativo
 * e de itens desabilitados em dois lugares.
 */
export default function NavList({ pathname, hrefFor }: NavListProps) {
  return (
    <nav aria-label="Navegação principal" className="flex flex-col gap-5">
      {NAV_SECTIONS.map((section) => (
        <div key={section.label} className="flex flex-col gap-1">
          <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide px-2">
            {section.label}
          </span>
          {section.pages.map((page) => {
            if (page.disabled) {
              return (
                <span
                  key={page.href}
                  className="flex items-center gap-1.5 px-2 py-2 rounded-lg text-sm text-slate-300 cursor-default select-none"
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
            const active = isNavItemActive(page.href, pathname);
            return (
              <Link
                key={page.href}
                href={hrefFor(page.href)}
                aria-current={active ? "page" : undefined}
                className={`px-2 py-2 rounded-lg text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
                  active
                    ? "bg-violet-50 text-violet-700 font-semibold"
                    : "text-slate-600 hover:bg-violet-50/60 hover:text-violet-700"
                }`}
              >
                {page.label}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
