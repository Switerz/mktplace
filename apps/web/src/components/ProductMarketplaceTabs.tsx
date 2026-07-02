interface Tab<T extends string> {
  value: T;
  label: string;
}

interface Props<T extends string> {
  tabs: Tab<T>[];
  active: T;
  onChange: (value: T) => void;
}

/** Navegacao por marketplace compartilhada entre as abas de Produtos. */
export default function ProductMarketplaceTabs<T extends string>({ tabs, active, onChange }: Props<T>) {
  return (
    <div role="tablist" aria-label="Marketplace" className="flex items-center gap-1 bg-white border border-violet-100 rounded-xl p-1 w-fit shadow-sm">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          aria-selected={active === t.value}
          onClick={() => onChange(t.value)}
          className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
            active === t.value
              ? "bg-violet-600 text-white shadow-sm"
              : "text-slate-500 hover:text-slate-700 hover:bg-slate-50"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
