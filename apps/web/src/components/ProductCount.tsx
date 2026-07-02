import { fmtNumber } from "@/lib/formatters";

interface Props {
  total: number | null;
  label?: string;
}

/** Contador padrao "N produtos" usado nas 3 abas de Produtos. */
export default function ProductCount({ total, label = "produtos" }: Props) {
  if (total == null) return null;
  return (
    <span className="text-xs text-slate-500 tabular-nums ml-auto">
      {fmtNumber(total)} {label}
    </span>
  );
}
