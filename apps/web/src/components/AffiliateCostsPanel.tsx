"use client";

import { useState } from "react";
import type { AffiliateCostsBlock } from "@/lib/api-client";
import {
  AFFILIATE_BLOCK_TITLE,
  AFFILIATE_COMPONENT_LABELS,
  AFFILIATE_COMPONENT_ORDER,
  brandLabel,
  buildAffiliateDrilldown,
  deriveAffiliateBlockView,
  describeChannelStatus,
  formatRefMonth,
  formatSignedBrl,
  rowCoverageNote,
  type AffiliateBlockPhase,
} from "@/lib/canais-affiliate-costs";
import KpiDrilldownDialog from "./KpiDrilldownDialog";

const TONE_CLASS: Record<string, string> = {
  value: "text-slate-900",
  muted: "text-slate-400",
  warning: "text-amber-700",
};

interface Props {
  /**
   * `null` significa: bloco AUSENTE. Ou a pagina ainda nao tem dado fresco, ou
   * a API nao expoe o bloco. Distinto de um bloco presente em qualquer estado
   * — inclusive de `error`, que a fonte de fato reportou.
   */
  block: AffiliateCostsBlock | null;
  /** Fase do ciclo de requisicao. Ver `resolveBlockPhase`. */
  phase: AffiliateBlockPhase;
}

function Moldura({ children }: { children: React.ReactNode }) {
  return (
    <section className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5">
      <h2 className="text-sm font-semibold text-slate-700 mb-2">
        {AFFILIATE_BLOCK_TITLE}
      </h2>
      {children}
    </section>
  );
}

/**
 * Bloco "Impacto de afiliados no resultado" da aba Canais (contrato §23).
 *
 * Exibe os tres lancamentos SEPARADAMENTE e nunca somados, com o sinal da
 * fonte. Nao existe total, razao, ROI, ROAS nem receita atribuida: a decisao
 * de qual subconjunto constitui "custo de afiliado" esta aberta, e a ausencia
 * de sobreposicao entre os componentes nao esta provada.
 */
export default function AffiliateCostsPanel({ block, phase }: Props) {
  const [aberto, setAberto] = useState(false);

  // `loading` e `neutral` sao transitorios: skeleton e' promessa de que o dado
  // vem. `unavailable` e' TERMINAL — a requisicao acabou em erro —, entao a
  // promessa nao pode ser feita: mensagem estatica, sem pulso e sem numero.
  if (phase === "loading" || phase === "neutral") {
    // O titulo fica FORA da animacao e sempre presente: uma regiao
    // `aria-busy` sem nome acessivel anuncia "carregando" sem dizer o que
    // carrega. As demais secoes de /canais ja mantem o heading no skeleton —
    // esconde-lo aqui tambem quebraria a convencao da pagina.
    return (
      <section
        className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5"
        aria-busy="true"
      >
        <h2 className="text-sm font-semibold text-slate-700 mb-4">
          {AFFILIATE_BLOCK_TITLE}
        </h2>
        <div className="animate-pulse">
          <div className="h-8 w-full bg-violet-50 rounded mb-2" />
          <div className="h-8 w-full bg-violet-50 rounded" />
        </div>
      </section>
    );
  }

  if (phase === "unavailable") {
    return (
      <Moldura>
        <p className="text-sm text-amber-700">
          Não foi possível carregar os dados de canais nesta consulta, então os
          lançamentos de afiliado também não estão disponíveis.
        </p>
      </Moldura>
    );
  }

  if (!block) {
    // Fresco, mas sem bloco: a API não expõe `affiliate_costs`. Mensagem
    // estática, nunca R$ 0,00 — que seria lido como "não houve custo".
    return (
      <Moldura>
        <p className="text-sm text-slate-400">
          Dados de afiliado indisponíveis para o período e filtros selecionados.
        </p>
      </Moldura>
    );
  }

  const view = deriveAffiliateBlockView(block);
  const linhas = buildAffiliateDrilldown(block.rows);

  return (
    <section className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <h2 className="text-sm font-semibold text-slate-700">
          {AFFILIATE_BLOCK_TITLE}
        </h2>
        {view.hasRows && (
          <button
            type="button"
            onClick={() => setAberto(true)}
            className="text-xs font-medium text-violet-700 hover:text-violet-900 underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded min-h-[44px] min-w-[44px] px-2"
          >
            Ver detalhe por marca
          </button>
        )}
      </div>

      {view.monthsLabel && (
        <p className="text-xs text-slate-500 mb-1">
          Competência: {view.monthsLabel}
        </p>
      )}
      {/* Frescor PROPRIO do bloco, nunca o `refreshedAt` geral da pagina — e
          so' quando uma fotografia foi de fato consultada. */}
      {view.freshnessLabel && (
        <p className="text-xs text-slate-500 mb-3">{view.freshnessLabel}</p>
      )}

      {view.coverageWarning && (
        <p className="text-xs text-amber-700 mb-3">{view.coverageWarning}</p>
      )}

      {view.emptyMessage ? (
        <p className={`text-sm ${TONE_CLASS[view.emptyTone]}`}>
          {view.emptyMessage}
        </p>
      ) : (
        <div className="overflow-x-auto -mx-5 px-5">
          <table className="w-full text-sm border-collapse">
            <caption className="sr-only">
              Lançamentos de afiliado por marca e competência, exibidos
              separadamente e não somados.
            </caption>
            <thead>
              <tr className="text-left text-xs text-slate-500 border-b border-violet-100">
                <th scope="col" className="py-2 pr-4 font-medium">Marca</th>
                <th scope="col" className="py-2 pr-4 font-medium">Competência</th>
                {AFFILIATE_COMPONENT_ORDER.map((chave) => (
                  <th
                    key={chave}
                    scope="col"
                    className="py-2 pr-4 font-medium text-right whitespace-nowrap"
                  >
                    {AFFILIATE_COMPONENT_LABELS[chave]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((linha) => {
                const nota = rowCoverageNote(linha);
                return (
                  <tr
                    key={`${linha.ref_month}:${linha.brand}`}
                    className="border-b border-violet-50 last:border-0"
                  >
                    <th scope="row" className="py-2 pr-4 font-medium text-slate-800 text-left">
                      {brandLabel(linha.brand)}
                      {nota && (
                        <span className="block text-xs font-normal text-amber-700">
                          {nota}
                        </span>
                      )}
                    </th>
                    <td className="py-2 pr-4 text-slate-600 whitespace-nowrap">
                      {formatRefMonth(linha.ref_month)}
                    </td>
                    {AFFILIATE_COMPONENT_ORDER.map((chave) => {
                      const celula = formatSignedBrl(linha[chave]);
                      return (
                        <td
                          key={chave}
                          className={`py-2 pr-4 text-right tabular-nums whitespace-nowrap ${TONE_CLASS[celula.tone]}`}
                        >
                          {celula.text}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
            {/* Nenhum `<tfoot>` de total: os tres componentes nao se somam. */}
          </table>
        </div>
      )}

      {/* Status por canal, AUTORITATIVO. ML e Shopee sao "Dados
          indisponíveis" — nunca "Não aplicável": afiliado existe nesses
          canais, o que falta e' fonte confirmada. */}
      {block.channels.length > 0 && (
        <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-xs">
          {block.channels.map((canal) => {
            const d = describeChannelStatus(canal);
            return (
              <li key={canal.channel} className={TONE_CLASS[d.tone]}>
                <span className="font-medium">{d.label}:</span> {d.text}
              </li>
            );
          })}
        </ul>
      )}

      <div className="mt-4 pt-3 border-t border-violet-50 flex flex-col gap-1 text-xs text-slate-500">
        <p>{block.limitation_note}</p>
        <p>{block.return_note}</p>
        <p>{block.source_note}</p>
      </div>

      {/* Reusa o shell generico de drilldown. As linhas vem do bloco JA
          carregado — zero fetch adicional ao abrir, entao o dialogo nao pode
          divergir da tabela. */}
      <KpiDrilldownDialog
        open={aberto}
        onClose={() => setAberto(false)}
        title={AFFILIATE_BLOCK_TITLE}
      >
        {view.freshnessLabel && (
          <p className="text-xs text-slate-500">{view.freshnessLabel}</p>
        )}
        {linhas.map((linha) => (
          <div
            key={`${linha.refMonth}:${linha.brand}`}
            className="border-b border-violet-50 pb-3 last:border-0"
          >
            <p className="text-sm font-semibold text-slate-800">
              {linha.brand} · {linha.refMonth}
            </p>
            {linha.coverageNote && (
              <p className="text-xs text-amber-700">{linha.coverageNote}</p>
            )}
            <dl className="mt-1 flex flex-col gap-0.5">
              {linha.components.map((c) => (
                <div key={c.label} className="flex justify-between gap-4 text-sm">
                  <dt className="text-slate-600">{c.label}</dt>
                  <dd className={`tabular-nums ${TONE_CLASS[c.cell.tone]}`}>
                    {c.cell.text}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
        <p className="text-xs text-slate-500">{block.limitation_note}</p>
        <p className="text-xs text-slate-500">{block.return_note}</p>
      </KpiDrilldownDialog>
    </section>
  );
}
