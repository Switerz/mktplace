"use client";

import { useEffect, useState } from "react";
import { CalendarDays, Check } from "lucide-react";
import Popover from "@/components/ui/Popover";
import { DATE_PRESET_OPTIONS, detectPreset, presetRange, toISODate } from "@/lib/filters/presets";
import { validateDateRange } from "@/lib/filters/url-state";
import type { DatePreset } from "@/lib/filters/types";

interface Props {
  dateFrom: string;
  dateTo: string;
  compare: boolean;
  onChange: (v: { dateFrom: string; dateTo: string }) => void;
  onCompareChange: (v: boolean) => void;
  /** Esconde o toggle de comparação em telas onde a fonte não calcula deltas
   * por período (ver docs/filtros_globais_contrato.md). */
  hideCompare?: boolean;
  /**
   * UX-TORRE-B2B-1 — `menu` move os presets para dentro de um gatilho que
   * mostra o preset ativo. Os BOTOES sao os mesmos (mesmo `aria-pressed`,
   * mesmo alvo de 44px); datas personalizadas e comparacao continuam na
   * barra, porque sao interacoes continuas e nao uma escolha unica.
   */
  variante?: "faixa" | "menu";
}

export default function DateRangeFilter({
  dateFrom, dateTo, compare, onChange, onCompareChange, hideCompare, variante = "faixa",
}: Props) {
  const menu = variante === "menu";
  const activePreset = detectPreset(dateFrom, dateTo);
  const [showCustom, setShowCustom] = useState(activePreset === "personalizado");
  const [error, setError] = useState<string | null>(null);
  const todayIso = toISODate(new Date());

  // Sincroniza showCustom quando dateFrom/dateTo mudam por causa externa a
  // este componente (navegação back/forward, troca de marca/tela que
  // preserva a querystring, link direto) — sem isto, voltar para uma URL
  // com intervalo personalizado continuava mostrando os botões de preset
  // sem revelar os campos de data.
  useEffect(() => {
    setShowCustom(detectPreset(dateFrom, dateTo) === "personalizado");
    setError(null);
  }, [dateFrom, dateTo]);

  function selectPreset(preset: DatePreset) {
    if (preset === "personalizado") {
      setShowCustom(true);
      return;
    }
    setShowCustom(false);
    setError(null);
    onChange(presetRange(preset));
  }

  function applyCustomChange(next: { dateFrom: string; dateTo: string }) {
    const validation = validateDateRange(next.dateFrom, next.dateTo);
    if (!validation.valid) {
      setError(validation.error ?? "Intervalo inválido.");
      return;
    }
    setError(null);
    onChange(next);
  }

  const presetAtivo = showCustom
    ? "Personalizado"
    : DATE_PRESET_OPTIONS.find((o) => o.value === activePreset)?.label ?? "Personalizado";

  // `fechar` so' existe na variante menu: escolher um preset encerra o painel.
  // "Personalizado" tambem fecha — os campos de data aparecem na propria barra.
  const presets = (fechar?: () => void) => (
        <div
          role="group"
          aria-label="Presets de período"
          className={
            menu
              ? "flex flex-col gap-0.5"
              : "flex gap-0.5 bg-raised border border-line rounded-lg p-0.5 overflow-x-auto min-w-0 max-w-full"
          }
        >
          {DATE_PRESET_OPTIONS.map((opt) => {
            const active = showCustom ? opt.value === "personalizado" : activePreset === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                aria-pressed={active}
                onClick={() => {
                  selectPreset(opt.value);
                  fechar?.();
                }}
                // 44x44px no runtime. `shrink-0` impede que o flex comprima o
                // botao abaixo do alvo: a faixa transborda e o `overflow-x-auto`
                // do proprio grupo rola, que e' o comportamento ja declarado ali.
                className={`inline-flex shrink-0 items-center ${menu ? "w-full justify-between" : "justify-center"} min-h-11 min-w-11 px-3 rounded-md text-sm whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${
                  active
                    ? "bg-surface font-semibold text-ink shadow-sm ring-1 ring-inset ring-line-strong"
                    : "text-ink-muted hover:bg-surface/70 hover:text-ink"
                }`}
              >
                {opt.label}
                {menu && active && <Check aria-hidden="true" className="h-4 w-4 text-accent" />}
              </button>
            );
          })}
        </div>
  );

  return (
    <div className="flex flex-col gap-2 min-w-0 max-w-full">
      <div className="flex items-center gap-2 min-w-0 flex-wrap">
        {menu ? (
          <Popover
            rotulo="Período"
            valor={presetAtivo}
            icone={<CalendarDays aria-hidden="true" className="h-4 w-4 text-ink-faint" />}
            largura="w-52"
          >
            {(fechar) => presets(fechar)}
          </Popover>
        ) : (
          <>
            <span className="text-xs text-ink-faint font-medium shrink-0">Período</span>
            {presets()}
          </>
        )}
        {/*
          O alvo aqui e' o LABEL, nao a caixinha: clicar em qualquer ponto do
          rotulo alterna o controle, entao `min-h-11` no label da uma area
          interativa real de 44px de altura. Aumentar o `<input>` a 44px
          desenharia uma caixa de selecao gigante sem ganho de alcance — e o
          que nao se faz e' o inverso, crescer so' o visual e deixar a area
          menor. `px-2` garante largura util junto ao texto.
        */}
        {!hideCompare && (
          <label className="inline-flex items-center gap-2 min-h-11 px-2 text-xs text-ink-muted font-medium ml-1 select-none cursor-pointer">
            <input
              type="checkbox"
              checked={compare}
              onChange={(e) => onCompareChange(e.target.checked)}
              className="w-4 h-4 shrink-0 rounded accent-[rgb(var(--tc-accent))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            />
            Comparar com período anterior
          </label>
        )}
      </div>
      {showCustom && (
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap min-w-0">
            {/* Nos campos de data o alvo e' o proprio `<input>`, que abre o
                seletor nativo: `min-h-11` vai nele, nao no label. Limites
                `min`/`max` e a validacao de intervalo seguem intocados. */}
            <label className="flex items-center gap-1.5 text-xs text-ink-faint min-w-0">
              De
              <input
                type="date"
                value={dateFrom}
                max={dateTo < todayIso ? dateTo : todayIso}
                onChange={(e) => e.target.value && applyCustomChange({ dateFrom: e.target.value, dateTo })}
                className="min-h-11 border border-line-strong bg-surface rounded-lg px-2 text-sm text-ink [color-scheme:inherit] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent min-w-0 max-w-full"
              />
            </label>
            <label className="flex items-center gap-1.5 text-xs text-ink-faint min-w-0">
              Até
              <input
                type="date"
                value={dateTo}
                min={dateFrom}
                max={todayIso}
                onChange={(e) => e.target.value && applyCustomChange({ dateFrom, dateTo: e.target.value })}
                className="min-h-11 border border-line-strong bg-surface rounded-lg px-2 text-sm text-ink [color-scheme:inherit] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent min-w-0 max-w-full"
              />
            </label>
          </div>
          {error && (
            <p role="alert" className="text-xs text-crit-ink font-medium">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
