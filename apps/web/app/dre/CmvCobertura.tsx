"use client";

import Chip from "@/components/ui/Chip";
import InfoDica from "@/components/ui/InfoDica";
import { FORMULA_CMV_COBERTO, resumoCoberturaCmv, type CelulaDRE } from "@/lib/dre";

/**
 * T-0055 C3 — cobertura do CMV do recorte e CMV % sobre a receita COM custo.
 *
 * Fica ao lado da DRE vertical, que continua mostrando o "% rec." do CMV
 * sobre a receita da DRE (GMV). Sao denominadores diferentes; a dica diz qual
 * e' qual. CMV `parcial` (cobertura abaixo do minimo) aparece nomeado: o custo
 * da receita sem custo nao e' zero e nao foi extrapolado.
 *
 * API anterior ao campo ou CMV sem valor: nao renderiza nada.
 */
export default function CmvCobertura({ celula, rotulo }: { celula: CelulaDRE; rotulo: string }) {
  const r = resumoCoberturaCmv(celula);
  if (!r) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5" aria-label={`Cobertura do CMV — ${rotulo}`}>
      <Chip tom={r.parcial ? "atencao" : "neutro"} ponto={r.parcial} title={FORMULA_CMV_COBERTO}>
        {r.texto}
      </Chip>
      {r.parcial && (
        <span className="text-xs text-ink-muted">
          CMV parcial: cobertura abaixo do mínimo, fora da margem; custo ausente não é zero.
        </span>
      )}
      <InfoDica texto={FORMULA_CMV_COBERTO} />
    </div>
  );
}
