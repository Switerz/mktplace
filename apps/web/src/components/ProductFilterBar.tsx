import type { ReactNode } from "react";

interface Props {
  children: ReactNode;
}

const SELECT_CLASS =
  "text-sm border border-slate-200 rounded-lg px-3 py-1.5 text-slate-600 bg-white focus:outline-none focus:ring-2 focus:ring-violet-500 focus:border-transparent";

/**
 * Container padrao da barra de filtros das 3 abas de Produtos. Nao conhece
 * quais filtros existem em cada canal — cada aba decide o que renderizar
 * dentro (marca, status, velocidade, sinal, periodo/escopo, contador).
 */
export default function ProductFilterBar({ children }: Props) {
  return (
    <div className="px-6 py-4 border-b border-violet-50 flex items-center gap-3 flex-wrap">
      {children}
    </div>
  );
}

export function ProductSelect(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={SELECT_CLASS} />;
}
