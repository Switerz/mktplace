"use client";

import type { Marketplace } from "@/lib/mock-data";
import {
  DEFAULT_MARKETPLACE_SELECTION,
  isAllSelected,
  isMarketplaceSelected,
  toggleMarketplace,
  type MarketplaceSelection,
} from "@/lib/marketplace-filter";

const CHANNEL_OPTIONS: { value: Marketplace; label: string; ponto: string }[] = [
  { value: "tiktok", label: "TikTok Shop", ponto: "bg-canal-tiktok" },
  { value: "ml", label: "Mercado Livre", ponto: "bg-canal-ml" },
  { value: "shopee", label: "Shopee", ponto: "bg-canal-shopee" },
];

interface Props {
  value: MarketplaceSelection;
  onChange: (v: MarketplaceSelection) => void;
}

/**
 * `min-h-11 min-w-11` fecha o contrato de 44x44px do V3 — os quatro botoes
 * mediam 36px de altura, e este filtro aparece em sete rotas.
 *
 * `shrink-0` e' obrigatorio junto: sem ele o botao encolhe dentro do flex do
 * container e o `justify-center` passa a RECORTAR o rotulo em telas estreitas.
 * Com ele a faixa transborda e o `overflow-x-auto` que o container ja declarava
 * rola — o alvo nunca cai abaixo de 44px para caber.
 *
 * UX-TORRE-2: mesma aparencia do `Segmented` do sistema — trilho neutro e
 * opcao ativa em relevo. O violeta cheio em cada botao ativo (ate dez na mesma
 * faixa, somando marca e periodo) violava a disciplina do violeta e disputava
 * a atencao com os numeros. Tokens, nao literais: o filtro tambem aparece em
 * rotas ainda nao migradas, onde a barreira de tema devolve o valor claro.
 */
const BASE_BTN =
  "inline-flex shrink-0 items-center justify-center min-h-11 min-w-11 px-3 rounded-md text-sm whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent";
const ACTIVE_BTN = "bg-surface font-semibold text-ink shadow-sm ring-1 ring-inset ring-line-strong";
const INACTIVE_BTN = "text-ink-muted hover:bg-surface/70 hover:text-ink";

export default function MarketplaceFilter({ value, onChange }: Props) {
  const allActive = isAllSelected(value);

  return (
    <div
      role="group"
      aria-label="Filtro de marketplaces"
      className="flex gap-0.5 bg-raised border border-line rounded-lg p-0.5 w-fit overflow-x-auto min-w-0 max-w-full"
    >
      <button
        type="button"
        aria-pressed={allActive}
        onClick={() => onChange([...DEFAULT_MARKETPLACE_SELECTION])}
        className={`${BASE_BTN} ${allActive ? ACTIVE_BTN : INACTIVE_BTN}`}
      >
        Todos
      </button>
      {CHANNEL_OPTIONS.map((opt) => {
        const active = isMarketplaceSelected(value, opt.value);
        return (
          <button
            key={opt.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(toggleMarketplace(value, opt.value))}
            className={`${BASE_BTN} gap-1.5 ${active ? ACTIVE_BTN : INACTIVE_BTN}`}
          >
            {/* UX-TORRE-B2B-1: a cor de IDENTIDADE do canal, a mesma dos
                graficos e da matriz — o filtro ensina a legenda. */}
            <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${opt.ponto}`} />
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
