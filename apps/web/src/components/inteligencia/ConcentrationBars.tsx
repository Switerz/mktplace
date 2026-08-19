"use client";

import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { brandLabel } from "@/lib/inteligencia/brands";
import { decBr } from "@/lib/inteligencia/format";
import {
  BUCKET_LABEL, BUCKET_LETTER, type BrandConcentration, type BucketShare, type ParetoBucket,
} from "@/lib/inteligencia/pareto";

const BUCKET_FILL: Record<ParetoBucket, string> = {
  A_top50: "bg-violet-600",
  B_next30: "bg-violet-400",
  C_next15: "bg-violet-200",
  D_tail: "bg-slate-300",
};

interface Props {
  concentration: BrandConcentration[];
  onOpenBucket: (brand: string, share: BucketShare, totalGmv: number) => void;
  disabled?: boolean;
}

/**
 * Bloco 4 — concentração Pareto (Gate V3-1A).
 *
 * Itera as marcas presentes no PAYLOAD, o que corrige o defeito B4 (a página
 * anterior tinha três marcas hardcoded e Rituária ficava invisível).
 *
 * Cada segmento é um `<button>` real com nome acessível que identifica marca e
 * bucket — a barra não é um `<div>` decorativo com `title`.
 */
export default function ConcentrationBars({ concentration, onOpenBucket, disabled }: Props) {
  if (concentration.length === 0) {
    return (
      <p className="px-6 py-8 text-center text-sm text-slate-500">
        Sem dados de concentração para o escopo selecionado.
      </p>
    );
  }
  return (
    <div className="px-4 sm:px-6 py-5 flex flex-col gap-6">
      {concentration.map((c) => (
        <div key={c.brand}>
          <div className="flex items-baseline justify-between gap-3 flex-wrap mb-2">
            <h3 className="text-sm font-semibold text-slate-800">{brandLabel(c.brand)}</h3>
            <p className="text-xs text-slate-500 tabular-nums">
              {fmtNumber(c.totalProducts)} produtos · {fmtBrl(c.totalGmv)} de GMV
            </p>
          </div>

          {/* Barra: cada segmento é acionável e proporcional ao GMV do bucket */}
          <div className="flex min-h-11 rounded-lg overflow-hidden w-full bg-slate-50 border border-slate-100">
            {c.buckets.map((b) => {
              const width = b.sharePct == null ? 100 / c.buckets.length : b.sharePct;
              return (
                <button
                  key={b.bucket}
                  type="button"
                  disabled={disabled}
                  onClick={() => onOpenBucket(c.brand, b, c.totalGmv)}
                  style={{ width: `${width}%` }}
                  aria-label={`Detalhe do bucket ${BUCKET_LABEL[b.bucket]} de ${brandLabel(c.brand)}: ${fmtNumber(b.n_products)} produtos`}
                  className={`${BUCKET_FILL[b.bucket]} self-stretch min-h-11 min-w-11 flex items-center justify-center text-xs font-bold transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white disabled:pointer-events-none ${
                    b.bucket === "C_next15" || b.bucket === "D_tail" ? "text-slate-700" : "text-white"
                  }`}
                >
                  {BUCKET_LETTER[b.bucket]}
                </button>
              );
            })}
          </div>

          {/* Legenda textual — a cor nunca carrega a informação sozinha */}
          <ul className="flex gap-x-5 gap-y-1 mt-2 flex-wrap list-none p-0 m-0">
            {c.buckets.map((b) => (
              <li key={b.bucket} className="flex items-center gap-1.5 text-xs text-slate-500">
                <span className={`w-2.5 h-2.5 rounded-sm inline-block ${BUCKET_FILL[b.bucket]}`} aria-hidden="true" />
                <span className="font-semibold text-slate-700">{BUCKET_LETTER[b.bucket]}</span>
                <span className="tabular-nums">
                  {b.sharePct == null ? "sem dado" : `${decBr(b.sharePct, 0)}% do GMV`} ·{" "}
                  {fmtNumber(b.n_products)}p
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
