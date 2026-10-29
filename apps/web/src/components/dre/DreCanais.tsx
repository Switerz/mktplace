import { Columns3 } from "lucide-react";

import Panel from "@/components/ui/Panel";
import InfoDica from "@/components/ui/InfoDica";
import { ClassificacaoChip } from "@/components/dre/DreChips";
import Motivos from "@/components/dre/Motivos";
import {
  ROTULO_CANAL,
  completudeExibida,
  custosAusentes,
  fmtReais,
  rotuloAusentes,
  semMargem,
  textoMargem,
  textoMargemPct,
  textoShare,
  type CelulaDRE,
} from "@/lib/dre";

const PONTO: Record<string, string> = {
  tiktok: "bg-canal-tiktok",
  ml: "bg-canal-ml",
  shopee: "bg-canal-shopee",
};

/**
 * DRE-UX-1/2 — canais lado a lado.
 *
 * Cada canal mostra a PROPRIA classificacao: a Shopee nao calculavel nao
 * apaga a margem do ML e do TikTok, que aparecem com numero. Colunas dentro de
 * um unico painel (sem cartao dentro de cartao); no mobile empilham.
 *
 * Margem, % e share saem SO' de `textoMargem`/`textoMargemPct`/`textoShare`:
 * margem nula vira "Não calculável", nunca "R$ 0".
 */
export default function DreCanais({ canais, total }: { canais: CelulaDRE[]; total: CelulaDRE }) {
  return (
    <Panel
      titulo={
        <>
          Comparação por canal
          <InfoDica texto="Participação sobre a receita da seleção." />
        </>
      }
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
          const sem = semMargem(c);
          const faltam = custosAusentes(c);
          const receita = c.componentes.receita.valor;
          const completude = completudeExibida(c);
          const nome = c.canal_label ?? ROTULO_CANAL[canal] ?? canal;
          return (
            <section key={canal} aria-label={nome} className="flex min-w-0 flex-col gap-3 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h4 className="flex items-center gap-2 text-sm font-semibold text-ink">
                  <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${PONTO[canal] ?? "bg-ink-faint"}`} />
                  {nome}
                </h4>
                <ClassificacaoChip classificacao={c.classificacao} completude={completude} />
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
                  <dd className="mt-0.5 font-semibold tabular-nums text-ink">{textoShare(c, total)}</dd>
                </div>
                <div>
                  <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Margem conhecida</dt>
                  <dd className={`mt-0.5 font-semibold tabular-nums ${sem ? "text-ink-faint" : "text-ink"}`}>
                    {textoMargem(c)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Margem %</dt>
                  <dd className={`mt-0.5 font-semibold tabular-nums ${sem ? "text-ink-faint" : "text-ink"}`}>
                    {textoMargemPct(c)}
                  </dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Completude</dt>
                  <dd className="mt-0.5 text-ink">
                    {completude ?? "Não calculável — veja os motivos"}
                    {faltam.length > 0 && (
                      <span className="block text-xs text-ink-muted">
                        {rotuloAusentes(c)}: {faltam.join(", ")}
                      </span>
                    )}
                  </dd>
                </div>
              </dl>
              {sem && c.motivos_classificacao.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-ink">Por que não é calculável</p>
                  <Motivos motivos={c.motivos_classificacao} visiveis={1} />
                </div>
              )}
            </section>
          );
        })}
      </div>
    </Panel>
  );
}
