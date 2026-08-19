"use client";

import { fmtBrl } from "@/lib/formatters";
import type { Priority } from "@/lib/inteligencia/priorities";

const ACCENT: Record<Priority["kind"], { bar: string; chip: string }> = {
  parar: { bar: "bg-rose-500", chip: "bg-rose-50 text-rose-700 border-rose-200" },
  escalar: { bar: "bg-emerald-500", chip: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  testar: { bar: "bg-amber-500", chip: "bg-amber-50 text-amber-800 border-amber-200" },
};

/** Palavra que acompanha a cor — cor nunca é a única explicação. */
const TONE_WORD: Record<Priority["kind"], string> = {
  parar: "Sai agora",
  escalar: "Retorno alto",
  testar: "Sem mídia",
};

interface Props {
  priorities: Priority[];
  onOpen: (p: Priority) => void;
  disabled?: boolean;
}

/**
 * Bloco 2 — prioridades da fotografia ML (Gate V3-1A).
 *
 * Duas regras que o componente materializa e não permite burlar:
 * - a contagem exibida é de REGISTROS RECEBIDOS, com a limitação sempre
 *   visível no próprio cartão (nunca "N produtos no total");
 * - cartão sem registro não chega aqui — `buildPriorities` já o omite, então
 *   não existe "prioridade zero".
 *
 * O cartão inteiro é um `<button>` real, com nome acessível próprio e alvo
 * confortável — não é um `<div>` com `onClick`.
 */
export default function PriorityCards({ priorities, onOpen, disabled }: Props) {
  if (priorities.length === 0) return null;
  return (
    <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 list-none p-0 m-0">
      {priorities.map((p) => {
        const accent = ACCENT[p.kind];
        return (
          <li key={p.kind}>
            <button
              type="button"
              onClick={() => onOpen(p)}
              disabled={disabled}
              aria-label={`Abrir detalhe da prioridade ${p.title}: ${p.received} registros exibidos`}
              className="group w-full min-h-11 text-left bg-white border border-violet-100 rounded-2xl shadow-sm p-4 flex flex-col gap-2 transition-colors hover:border-violet-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 disabled:opacity-50 disabled:pointer-events-none"
            >
              <span className={`h-1 w-10 rounded-full ${accent.bar}`} aria-hidden="true" />
              <span className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-semibold text-slate-800">{p.title}</span>
                <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${accent.chip}`}>
                  {TONE_WORD[p.kind]}
                </span>
              </span>
              <span className="text-2xl font-bold text-slate-900 tabular-nums leading-none">
                {fmtBrl(p.amount)}
              </span>
              <span className="text-xs text-slate-500">
                {p.amountLabel} · {p.received} registro{p.received === 1 ? "" : "s"} exibido
                {p.received === 1 ? "" : "s"}
              </span>
              <span className="text-xs text-slate-600">{p.reason}</span>
              <span className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1">
                {p.limitation}
              </span>
              <span className="text-xs font-semibold text-violet-700 group-hover:underline mt-auto">
                Ver evidências →
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
