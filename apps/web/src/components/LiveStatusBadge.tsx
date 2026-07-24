interface LiveStatusBadgeProps {
  live: boolean;
  /** Texto exibido quando `live` e falso. Varia por pagina (ex: Pedidos usa
   * "Sem dados · API offline" em vez do texto padrao). */
  offlineLabel?: string;
}

/** Badge de dados ao vivo/demonstracao — antes duplicado dentro do <header>
 * de cada pagina, agora extraido porque o <header> generico foi absorvido
 * pelo shell (Gate U1). */
export default function LiveStatusBadge({ live, offlineLabel = "Demonstração · API offline" }: LiveStatusBadgeProps) {
  if (live) {
    return (
      <span className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-1.5 font-medium">
        Dados ao vivo · API conectada
      </span>
    );
  }
  return (
    <span className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5 font-medium">
      {offlineLabel}
    </span>
  );
}
