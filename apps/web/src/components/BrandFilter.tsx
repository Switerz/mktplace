"use client";

import { Check, Tag } from "lucide-react";
import Popover from "@/components/ui/Popover";
import { ALL_BRAND_OPTIONS, brandLabel } from "@/lib/filters/brands";
import { toggleMultiSelect } from "@/lib/filters/multi-select";

interface Props {
  /** Marcas selecionadas (brand_key). Vazio = todas. */
  value: string[];
  onChange: (v: string[]) => void;
  /**
   * UX-TORRE-B2B-1 — `menu` recolhe as seis opcoes atras de um gatilho que
   * mostra a selecao atual. Usado na barra das telas que ja' tem filtro de
   * canal e periodo na mesma linha (Gerencial, Canais, Financeiro). As outras
   * rotas seguem com a faixa segmentada (`faixa`, o padrao).
   *
   * Os BOTOES sao os mesmos nas duas variantes — mesmo `aria-pressed`, mesmo
   * alvo de 44px, mesma regra "vazio = todas". Muda so' onde eles moram.
   */
  variante?: "faixa" | "menu";
}

/**
 * UX-TORRE-2: mesma aparencia e mesmo alvo do `MarketplaceFilter`. O botao
 * media ~36px (`py-2` + `line-height`) — agora o piso de 44px e' explicito.
 * "Todas" continua sendo a selecao vazia, nunca "nenhuma marca".
 */
const BASE_BTN =
  "inline-flex shrink-0 items-center min-h-11 min-w-11 px-3 rounded-md text-sm whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent";
const ACTIVE_BTN = "bg-surface font-semibold text-ink shadow-sm ring-1 ring-inset ring-line-strong";
const INACTIVE_BTN = "text-ink-muted hover:bg-surface/70 hover:text-ink";

/** Valor curto para o gatilho do menu: o escopo legivel sem abrir nada. */
function resumo(value: string[]): string {
  if (value.length === 0 || value.length === ALL_BRAND_OPTIONS.length) return "Todas";
  if (value.length === 1) return brandLabel(value[0]);
  return `${value.length} marcas`;
}

export default function BrandFilter({ value, onChange, variante = "faixa" }: Props) {
  const allActive = value.length === 0;
  const menu = variante === "menu";
  // Na lista do menu o rotulo encosta a esquerda e o check marca a selecao;
  // na faixa, centralizado como os demais segmentos.
  const alinhamento = menu ? "w-full justify-between" : "justify-center";

  const botoes = (
    <>
      <button
        type="button"
        aria-pressed={allActive}
        onClick={() => onChange([])}
        className={`${BASE_BTN} ${alinhamento} ${allActive ? ACTIVE_BTN : INACTIVE_BTN}`}
      >
        Todas
        {menu && allActive && <Check aria-hidden="true" className="h-4 w-4 text-accent" />}
      </button>
      {ALL_BRAND_OPTIONS.map((opt) => {
        const active = value.includes(opt.value);
        return (
          <button
            key={opt.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(toggleMultiSelect(value, opt.value))}
            className={`${BASE_BTN} ${alinhamento} ${active ? ACTIVE_BTN : INACTIVE_BTN}`}
          >
            {opt.label}
            {menu && active && <Check aria-hidden="true" className="h-4 w-4 text-accent" />}
          </button>
        );
      })}
    </>
  );

  if (menu) {
    return (
      <Popover
        rotulo="Marca"
        valor={resumo(value)}
        ativo={!allActive}
        icone={<Tag aria-hidden="true" className="h-4 w-4 text-ink-faint" />}
        largura="w-56"
      >
        {() => (
          <div role="group" aria-label="Filtro de marcas" className="flex flex-col gap-0.5">
            {botoes}
          </div>
        )}
      </Popover>
    );
  }

  return (
    <div
      role="group"
      aria-label="Filtro de marcas"
      className="flex gap-0.5 bg-raised border border-line rounded-lg p-0.5 w-fit overflow-x-auto min-w-0 max-w-full"
    >
      {botoes}
    </div>
  );
}
