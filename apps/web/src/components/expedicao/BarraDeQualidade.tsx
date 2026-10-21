"use client";

/**
 * Gate EXPEDICAO-UX-UNIFICADA-1 — UMA barra de qualidade do dado, para os
 * tres canais.
 *
 * Nasceu dentro do `ExpedicaoClient` (UX-TORRE-1) e saiu de la' porque o
 * TikTok empilhava tres banners no mesmo lugar. O que entra na barra continua
 * sendo decidido pelo contrato de cada canal (`montarBarraDeQualidade`,
 * `montarBarraDeQualidadeTikTok`); aqui so' se pinta: o item mais grave
 * fechado, todos os outros sob demanda. Nenhuma ressalva e' descartada.
 */
import { useId, useState } from "react";

import Note from "@/components/ui/Note";
import type { Tom } from "@/components/ui/tone";
import type { BarraQualidade, SeveridadeQualidade } from "@/lib/expedicao-ux";

const TOM: Record<SeveridadeQualidade, Tom> = {
  alerta: "critico",
  atencao: "atencao",
  info: "neutro",
};

const ROTULO: Record<SeveridadeQualidade, string> = {
  alerta: "Alerta",
  atencao: "Atenção",
  info: "Informação",
};

export default function BarraDeQualidade({ barra }: { barra: BarraQualidade }) {
  const [aberta, setAberta] = useState(false);
  const id = useId();
  if (!barra.principal) return null;
  const mais = barra.detalhes.length > 1;
  return (
    <section aria-label="Qualidade do dado">
      <Note
        tom={TOM[barra.principal.severidade]}
        papel="status"
        acao={
          mais ? (
            <button
              type="button"
              aria-expanded={aberta}
              aria-controls={id}
              onClick={() => setAberta((v) => !v)}
              className="min-h-11 shrink-0 rounded-md px-2 text-xs font-semibold underline underline-offset-4"
            >
              {aberta ? "Ocultar detalhes" : `Ver ${barra.detalhes.length} limitações`}
            </button>
          ) : undefined
        }
      >
        {barra.principal.texto}
      </Note>
      {mais && aberta ? (
        <ul
          id={id}
          className="mt-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink-muted"
        >
          {barra.detalhes.map((d) => (
            <li key={d.chave} className="flex gap-2 py-1">
              <span className="w-20 shrink-0 font-semibold text-ink">{ROTULO[d.severidade]}</span>
              <span>{d.texto}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
