"use client";

/**
 * Gate EXPEDICAO-UX-JANELA-CUSTOMIZAVEL-1 — campo "Últimos N dias".
 *
 * So' APLICA no envio (botao ou Enter), nunca a cada tecla: digitar "14"
 * passaria por "1" e dispararia uma requisicao de 1 dia no meio do caminho.
 *
 * Entrada invalida nao chega ao pai. Ela vira mensagem ligada ao campo
 * (`aria-invalid` + `aria-describedby`), e a janela em vigor continua a mesma —
 * o que esta' na tela continua sendo o que o rotulo de periodo diz.
 *
 * `type="text"` com `inputMode="numeric"`, e nao `type="number"`: o campo
 * numerico do navegador devolve "" para "1,5" e para "abc", e a mensagem nao
 * conseguiria dizer o que corrigir.
 */
import { useEffect, useId, useState, type FormEvent } from "react";

import { validarDias } from "@/lib/expedicao-janela";

export default function JanelaPersonalizada({
  max,
  diasAplicados,
  aoAplicar,
}: {
  max: number;
  diasAplicados: number;
  aoAplicar: (dias: number) => void;
}) {
  const id = useId();
  const idCampo = `${id}-dias`;
  const idDica = `${id}-dica`;
  const idErro = `${id}-erro`;
  const [texto, setTexto] = useState(String(diasAplicados));
  const [erro, setErro] = useState<string | null>(null);

  // Voltar/avancar do navegador troca a janela por fora: o campo acompanha.
  useEffect(() => {
    setTexto(String(diasAplicados));
    setErro(null);
  }, [diasAplicados]);

  const enviar = (e: FormEvent) => {
    e.preventDefault();
    const r = validarDias(texto, max);
    if (!r.ok) {
      setErro(r.erro);
      return;
    }
    setErro(null);
    setTexto(String(r.dias));
    if (r.dias !== diasAplicados) aoAplicar(r.dias);
  };

  return (
    <form
      onSubmit={enviar}
      noValidate
      aria-label="Janela personalizada"
      className="flex flex-col gap-1"
    >
      <div className="flex flex-wrap items-center gap-2 text-sm text-ink">
        <label htmlFor={idCampo} className="text-ink-muted">
          Últimos<span className="sr-only"> quantos dias</span>
        </label>
        <input
          id={idCampo}
          type="text"
          inputMode="numeric"
          autoComplete="off"
          value={texto}
          onChange={(e) => {
            setTexto(e.target.value);
            if (erro) setErro(null);
          }}
          aria-invalid={erro ? true : undefined}
          aria-describedby={erro ? `${idDica} ${idErro}` : idDica}
          className={`min-h-11 w-16 rounded-md border bg-surface px-2 text-right tabular-nums text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
            erro ? "border-crit" : "border-line-strong"
          }`}
        />
        <span aria-hidden="true" className="text-ink-muted">
          dias
        </span>
        <button
          type="submit"
          className="min-h-11 rounded-md border border-line-strong bg-surface px-3 text-sm font-semibold text-ink hover:bg-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Aplicar
        </button>
      </div>
      <p id={idDica} className="text-xs text-ink-faint">
        Número inteiro de 1 a {max} dias.
      </p>
      {erro && (
        <p id={idErro} role="alert" className="text-xs font-medium text-crit-ink">
          {erro}
        </p>
      )}
    </form>
  );
}
