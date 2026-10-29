"use client";

/**
 * METAS-1 — grade marca x canal da aba "Metas".
 *
 * Cada celula e' um campo de texto em reais. Abaixo dele, em cinza, o GMV
 * realizado daquela marca naquele canal no mes, so' como referencia para quem
 * define a meta. A grade nao guarda estado: rascunho, salvo e realizado chegam
 * prontos do `MetasClient`.
 */
import {
  CANAIS,
  MARCAS,
  chave,
  fmtCompacto,
  lerValor,
  textoDoCampo,
  totais,
  type Analise,
  type Chave,
  type Rascunho,
  type Valores,
} from "@/lib/metas";

const PONTO_DO_CANAL: Record<string, string> = {
  tiktok: "bg-canal-tiktok",
  ml: "bg-canal-ml",
  shopee: "bg-canal-shopee",
};

interface Props {
  rascunho: Rascunho;
  salvo: Valores;
  analise: Analise;
  /** `null` = realizado indisponivel (a grade segue editavel); `"futuro"` = mes nao comecou. */
  realizado: Valores | null | "futuro";
  desabilitado: boolean;
  onChange: (k: Chave, texto: string) => void;
}

export default function MetasGrade({ rascunho, salvo, analise, realizado, desabilitado, onChange }: Props) {
  const t = totais(analise.efetivos);

  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-surface">
      <table className="w-full min-w-[760px] border-collapse text-sm">
        <caption className="sr-only">Metas de GMV por marca e canal</caption>
        <thead>
          <tr className="border-b border-line text-xs font-semibold uppercase tracking-wide text-ink-faint">
            <th scope="col" className="px-4 py-3 text-left">Marca</th>
            {CANAIS.map((c) => (
              <th key={c.id} scope="col" className="px-3 py-3 text-right">
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden="true" className={`h-2 w-2 rounded-full ${PONTO_DO_CANAL[c.id]}`} />
                  {c.label}
                </span>
              </th>
            ))}
            <th scope="col" className="px-4 py-3 text-right">Total da marca</th>
          </tr>
        </thead>
        <tbody>
          {MARCAS.map((m) => (
            <tr key={m.id} className="border-b border-line align-top last:border-b-0">
              <th scope="row" className="px-4 pt-5 text-left text-sm font-semibold text-ink">{m.label}</th>
              {CANAIS.map((c) => {
                const k = chave(m.id, c.id);
                return (
                  <td key={c.id} className="px-3 py-3">
                    <Celula
                      id={`meta-${m.id}-${c.id}`}
                      rotulo={`Meta de ${m.label} em ${c.label}`}
                      texto={rascunho[k] ?? ""}
                      erro={analise.invalidas[k]}
                      alterada={!analise.invalidas[k] && (analise.efetivos[k] ?? null) !== (salvo[k] ?? null)}
                      realizado={
                        realizado === "futuro" ? "futuro" : realizado ? (realizado[k] ?? null) : undefined
                      }
                      desabilitado={desabilitado}
                      onChange={(texto) => onChange(k, texto)}
                    />
                  </td>
                );
              })}
              <td className="px-4 pt-5 text-right font-semibold tabular-nums text-ink">
                {fmtCompacto(t.porMarca[m.id])}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t border-line-strong bg-raised">
            <th scope="row" className="px-4 py-3 text-left text-sm font-semibold text-ink">Total do canal</th>
            {CANAIS.map((c) => (
              <td key={c.id} className="px-3 py-3 text-right font-semibold tabular-nums text-ink">
                {fmtCompacto(t.porCanal[c.id])}
              </td>
            ))}
            <td className="px-4 py-3 text-right tabular-nums">
              <span className="block text-xs font-semibold uppercase tracking-wide text-ink-faint">Meta global</span>
              <span className="text-lg font-bold text-accent">{fmtCompacto(t.global)}</span>
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function Celula({
  id,
  rotulo,
  texto,
  erro,
  alterada,
  realizado,
  desabilitado,
  onChange,
}: {
  id: string;
  rotulo: string;
  texto: string;
  erro?: string;
  alterada: boolean;
  /** `undefined` = realizado indisponivel; `null` = sem venda registrada; `"futuro"` = mes nao comecou. */
  realizado: number | null | undefined | "futuro";
  desabilitado: boolean;
  onChange: (texto: string) => void;
}) {
  const borda = erro
    ? "border-crit ring-1 ring-crit"
    : alterada
      ? "border-accent ring-1 ring-accent"
      : "border-line-strong";
  const idAjuda = `${id}-ajuda`;

  return (
    <div className="ml-auto flex max-w-[200px] flex-col items-end gap-1">
      <label htmlFor={id} className="sr-only">{rotulo}</label>
      <div
        className={`flex min-h-11 w-full items-center rounded-lg border bg-surface px-2.5 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent ${borda}`}
      >
        <span aria-hidden="true" className="text-xs text-ink-faint">R$</span>
        <input
          id={id}
          type="text"
          inputMode="decimal"
          autoComplete="off"
          placeholder="sem meta"
          value={texto}
          disabled={desabilitado}
          aria-invalid={erro ? true : undefined}
          aria-describedby={idAjuda}
          onChange={(e) => onChange(e.target.value)}
          onBlur={() => {
            // Reescreve no formato brasileiro quando o valor e' valido:
            // "1500000" vira "1.500.000" ao sair do campo.
            const leitura = lerValor(texto);
            if (leitura.ok) {
              const formatado = textoDoCampo(leitura.valor);
              if (formatado !== texto) onChange(formatado);
            }
          }}
          className="min-w-0 flex-1 bg-transparent py-2 pl-1.5 text-right text-sm font-medium tabular-nums text-ink placeholder:text-ink-faint placeholder:font-normal focus:outline-none disabled:opacity-60"
        />
      </div>
      <span id={idAjuda} className={`text-xs tabular-nums ${erro ? "font-semibold text-crit-ink" : "text-ink-faint"}`}>
        {erro
          ? erro
          : realizado === "futuro"
            ? "mês ainda não começou"
            : realizado === undefined
              ? "realizado indisponível"
              : `realizado ${fmtCompacto(realizado ?? 0)}`}
      </span>
    </div>
  );
}
