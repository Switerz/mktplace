import { Columns3 } from "lucide-react";

import Panel from "@/components/ui/Panel";
import { ClassificacaoChip } from "@/components/dre/DreChips";
import {
  ROTULO_CANAL,
  custosAusentes,
  fmtPctDre,
  fmtReais,
  shareDaReceita,
  type CelulaDRE,
} from "@/lib/dre";

const PONTO: Record<string, string> = {
  tiktok: "bg-canal-tiktok",
  ml: "bg-canal-ml",
  shopee: "bg-canal-shopee",
};

/**
 * DRE-UX-1 — canais lado a lado.
 *
 * Cada canal mostra a PROPRIA classificacao: a Shopee nao calculavel nao
 * apaga a margem do ML e do TikTok, que aparecem com numero. Colunas dentro de
 * um unico painel (sem cartao dentro de cartao); no mobile empilham.
 */
export default function DreCanais({ canais, total }: { canais: CelulaDRE[]; total: CelulaDRE }) {
  return (
    <Panel
      titulo="Comparação por canal"
      descricao="Cada canal com a própria classificação. Participação sobre a receita da seleção."
      icone={Columns3}
      semPadding
    >
      <div
        className={`grid grid-cols-1 divide-y divide-line ${
          canais.length >= 3 ? "md:grid-cols-3" : canais.length === 2 ? "md:grid-cols-2" : ""
        } md:divide-x md:divide-y-0`}
      >
        {canais.map((c) => {
          const canal = c.canal ?? "";
          const m = c.margem_contribuicao_conhecida;
          const naoCalc = c.classificacao === "NAO_CALCULAVEL" || m.valor === null;
          const share = fmtPctDre(shareDaReceita(c, total));
          const faltam = custosAusentes(c);
          const receita = c.componentes.receita.valor;
          return (
            <section key={canal} aria-label={c.canal_label ?? ROTULO_CANAL[canal] ?? canal} className="flex min-w-0 flex-col gap-3 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h4 className="flex items-center gap-2 text-sm font-semibold text-ink">
                  <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${PONTO[canal] ?? "bg-ink-faint"}`} />
                  {c.canal_label ?? ROTULO_CANAL[canal] ?? canal}
                </h4>
                <ClassificacaoChip
                  classificacao={c.classificacao}
                  completude={c.completude.texto}
                  motivo={c.motivos_classificacao[0]}
                />
              </div>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-2.5 text-data">
                <div>
                  <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Receita</dt>
                  <dd className="mt-0.5 font-semibold tabular-nums text-ink">
                    {receita !== null ? fmtReais(receita) : "Sem valor"}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Participação</dt>
                  <dd className="mt-0.5 font-semibold tabular-nums text-ink">{share ?? "Sem receita total"}</dd>
                </div>
                <div>
                  <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Margem conhecida</dt>
                  <dd className={`mt-0.5 font-semibold tabular-nums ${naoCalc ? "text-ink-faint" : "text-ink"}`}>
                    {naoCalc || m.valor === null ? "Não calculável" : fmtReais(m.valor)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Margem %</dt>
                  <dd className={`mt-0.5 font-semibold tabular-nums ${naoCalc ? "text-ink-faint" : "text-ink"}`}>
                    {naoCalc ? "Não calculável" : fmtPctDre(m.pct_receita) ?? "Não calculável"}
                  </dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Completude</dt>
                  <dd className="mt-0.5 text-ink">
                    {c.completude.texto}
                    {faltam.length > 0 && <span className="text-ink-muted"> · faltam {faltam.join(", ")}</span>}
                  </dd>
                </div>
              </dl>
              {naoCalc && c.motivos_classificacao.length > 0 && (
                <p className="text-xs leading-snug text-ink-muted">
                  <span className="font-semibold text-ink">Por que não: </span>
                  {c.motivos_classificacao[0]}
                  {c.motivos_classificacao.length > 1 && ` (e mais ${c.motivos_classificacao.length - 1})`}
                </p>
              )}
            </section>
          );
        })}
      </div>
    </Panel>
  );
}
