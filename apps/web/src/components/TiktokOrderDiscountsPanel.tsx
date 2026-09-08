"use client";

import { useState } from "react";
import type { TikTokOrderDiscountsBlock } from "@/lib/api-client";
import {
  buildCancelledDrilldown,
  buildDiscountTable,
  CANCELLED_COPY,
  CANCELLED_SECTION_TITLE,
  COVERAGE_LIMIT_COPY,
  coverageNote,
  deriveDiscountBlockView,
  DISCOUNT_COMPONENT_LABELS,
  DISCOUNT_COMPONENT_ORDER,
  DISCOUNTS_BLOCK_TITLE,
  RETROACTIVE_COPY,
  SELLER_COPY,
  SUBSIDY_COPY,
  type DiscountBlockPhase,
} from "@/lib/canais-tiktok-order-discounts";
import KpiDrilldownDialog from "./KpiDrilldownDialog";

const TONE_CLASS: Record<string, string> = {
  value: "text-slate-900",
  muted: "text-slate-400",
  warning: "text-amber-700",
};

interface Props {
  /**
   * `null` significa bloco AUSENTE — ou a pagina ainda nao tem dado fresco, ou
   * a API nao expoe o bloco. Distinto de um bloco presente em qualquer estado,
   * inclusive `error`, que a fonte de fato reportou.
   */
  block: TikTokOrderDiscountsBlock | null;
  phase: DiscountBlockPhase;
}

function Moldura({ children }: { children: React.ReactNode }) {
  return (
    <section className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5">
      <h2 className="text-sm font-semibold text-slate-700 mb-2">
        {DISCOUNTS_BLOCK_TITLE}
      </h2>
      {children}
    </section>
  );
}

/**
 * Bloco "Descontos e subsidios do pedido — TikTok Shop" da aba Canais (§28).
 *
 * Exibe os DOIS componentes separadamente e nunca somados, com o sinal da
 * fonte, porque tem financiadores diferentes. Nao existe total, margem, lucro,
 * receita liquida, caixa nem retorno. Cancelados aparecem so' no drill-down,
 * em secao visualmente separada.
 */
export default function TiktokOrderDiscountsPanel({ block, phase }: Props) {
  const [aberto, setAberto] = useState(false);

  // `loading` e `neutral` sao transitorios: skeleton e' promessa de que o dado
  // vem. `unavailable` e' TERMINAL — a requisicao acabou em erro —, entao a
  // promessa nao pode ser feita: mensagem estatica, sem pulso e sem numero.
  if (phase === "loading" || phase === "neutral") {
    return (
      <section
        className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5"
        aria-busy="true"
      >
        {/* O titulo fica FORA da animacao: uma regiao `aria-busy` sem nome
            acessivel anuncia "carregando" sem dizer o que carrega. */}
        <h2 className="text-sm font-semibold text-slate-700 mb-4">
          {DISCOUNTS_BLOCK_TITLE}
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
          descontos de pedido também não estão disponíveis.
        </p>
      </Moldura>
    );
  }

  if (!block) {
    // Fresco, mas sem bloco: a API nao expoe `tiktok_order_discounts`.
    // Mensagem estatica, nunca R$ 0,00 — que seria lido como "sem desconto".
    return (
      <Moldura>
        <p className="text-sm text-slate-400">
          Descontos de pedido indisponíveis para o período e filtros
          selecionados.
        </p>
      </Moldura>
    );
  }

  const view = deriveDiscountBlockView(block);
  const linhas = buildDiscountTable(block.rows);
  const cancelados = buildCancelledDrilldown(block.rows);
  const notaCobertura = coverageNote(block);

  return (
    <section className="bg-white rounded-2xl shadow-sm border border-violet-100 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <h2 className="text-sm font-semibold text-slate-700">
          {DISCOUNTS_BLOCK_TITLE}
        </h2>
        {view.hasRows && (
          <button
            type="button"
            onClick={() => setAberto(true)}
            className="text-xs font-medium text-violet-700 hover:text-violet-900 underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded min-h-[44px] min-w-[44px] px-2"
          >
            Ver detalhe e cancelados
          </button>
        )}
      </div>

      {view.windowLabel && (
        <p className="text-xs text-slate-500 mb-1">
          Período: {view.windowLabel}
          {view.dayCountLabel ? ` · ${view.dayCountLabel}` : ""}
        </p>
      )}
      {/* Frescor PROPRIO do bloco, nunca o `refreshedAt` geral da pagina. */}
      {view.freshnessLabel && (
        <p className="text-xs text-slate-500 mb-2">{view.freshnessLabel}</p>
      )}

      {view.warnings.length > 0 && (
        <ul className="mb-3 flex flex-col gap-1">
          {view.warnings.map((aviso) => (
            <li key={aviso} className="text-xs text-amber-700">
              {aviso}
            </li>
          ))}
        </ul>
      )}
      {notaCobertura && !view.warnings.length && (
        <p className="text-xs text-amber-700 mb-3">{notaCobertura}</p>
      )}
      {/* O limite acompanha a cobertura COMPLETA tambem: `complete` significa
          grade observada completa, e sem esta linha o leitor concluiria
          "ingestao comprovada". */}
      {view.hasRows && !view.coverageIsIncomplete && (
        <p className="text-xs text-slate-500 mb-3">{COVERAGE_LIMIT_COPY}</p>
      )}

      {view.emptyMessage ? (
        <p className={`text-sm ${TONE_CLASS[view.emptyTone]}`}>
          {view.emptyMessage}
        </p>
      ) : (
        /* `overflow-x-auto`: a tabela rola DENTRO do seu container e a pagina
            nunca ganha barra horizontal, inclusive em 390px. */
        <div className="overflow-x-auto -mx-5 px-5">
          <table className="w-full text-sm border-collapse">
            <caption className="sr-only">
              Descontos e subsídios do pedido por marca. O desconto financiado
              pela marca e o subsídio financiado pelo TikTok são exibidos
              separadamente e não somados.
            </caption>
            <thead>
              <tr className="text-left text-xs text-slate-500 border-b border-violet-100">
                <th scope="col" className="py-2 pr-4 font-medium">Marca</th>
                <th scope="col" className="py-2 pr-4 font-medium text-right whitespace-nowrap">
                  Pedidos comerciais
                </th>
                {DISCOUNT_COMPONENT_ORDER.map((chave) => (
                  <th
                    key={chave}
                    scope="col"
                    className="py-2 pr-4 font-medium text-right whitespace-nowrap"
                  >
                    {DISCOUNT_COMPONENT_LABELS[chave]}
                  </th>
                ))}
                <th scope="col" className="py-2 pr-4 font-medium text-right whitespace-nowrap">
                  % da marca
                </th>
                <th scope="col" className="py-2 pr-4 font-medium text-right whitespace-nowrap">
                  % do TikTok
                </th>
              </tr>
            </thead>
            <tbody>
              {linhas.map((linha) => (
                <tr
                  key={linha.brandKey}
                  className="border-b border-violet-50 last:border-0"
                >
                  <th scope="row" className="py-2 pr-4 font-medium text-slate-800 text-left">
                    {linha.brand}
                  </th>
                  <td className="py-2 pr-4 text-right tabular-nums text-slate-600 whitespace-nowrap">
                    {linha.commercialOrders}
                  </td>
                  {linha.cells.map((c) => (
                    <td
                      key={c.key}
                      className={`py-2 pr-4 text-right tabular-nums whitespace-nowrap ${TONE_CLASS[c.cell.tone]}`}
                    >
                      {c.cell.text}
                    </td>
                  ))}
                  <td className={`py-2 pr-4 text-right tabular-nums whitespace-nowrap ${TONE_CLASS[linha.sellerRate.tone]}`}>
                    {linha.sellerRate.text}
                  </td>
                  <td className={`py-2 pr-4 text-right tabular-nums whitespace-nowrap ${TONE_CLASS[linha.platformRate.tone]}`}>
                    {linha.platformRate.text}
                  </td>
                </tr>
              ))}
            </tbody>
            {/* Nenhum `<tfoot>` de total: os dois componentes tem
                financiadores diferentes e nao se somam. */}
          </table>
        </div>
      )}

      <div className="mt-4 pt-3 border-t border-violet-50 flex flex-col gap-1 text-xs text-slate-500">
        <p>{SELLER_COPY}</p>
        <p>{SUBSIDY_COPY}</p>
        <p>{RETROACTIVE_COPY}</p>
        <p>{CANCELLED_COPY}</p>
      </div>

      {/* Reusa o shell generico de drilldown — nenhum modal proprio. As linhas
          vem do bloco JA carregado: zero fetch ao abrir, entao o dialogo nao
          pode divergir da tabela. */}
      <KpiDrilldownDialog
        open={aberto}
        onClose={() => setAberto(false)}
        title={DISCOUNTS_BLOCK_TITLE}
      >
        {view.freshnessLabel && (
          <p className="text-xs text-slate-500">{view.freshnessLabel}</p>
        )}
        {linhas.map((linha) => (
          <div
            key={linha.brandKey}
            className="border-b border-violet-50 pb-3 last:border-0"
          >
            <p className="text-sm font-semibold text-slate-800">
              {linha.brand} · {linha.commercialOrders} pedidos comerciais
            </p>
            <dl className="mt-1 flex flex-col gap-0.5">
              {linha.cells.map((c) => (
                <div key={c.key} className="flex justify-between gap-4 text-sm">
                  <dt className="text-slate-600">{c.label}</dt>
                  <dd className={`tabular-nums ${TONE_CLASS[c.cell.tone]}`}>
                    {c.cell.text}
                  </dd>
                </div>
              ))}
              <div className="flex justify-between gap-4 text-sm">
                <dt className="text-slate-600">% financiado pela marca</dt>
                <dd className={`tabular-nums ${TONE_CLASS[linha.sellerRate.tone]}`}>
                  {linha.sellerRate.text}
                </dd>
              </div>
              <div className="flex justify-between gap-4 text-sm">
                <dt className="text-slate-600">% financiado pelo TikTok</dt>
                <dd className={`tabular-nums ${TONE_CLASS[linha.platformRate.tone]}`}>
                  {linha.platformRate.text}
                </dd>
              </div>
            </dl>
          </div>
        ))}

        {/* Secao VISUALMENTE separada. Cancelados nao integram as vendas
            comerciais, e exibi-los na mesma lista convidaria a soma-los. */}
        <section className="mt-2 pt-3 border-t-2 border-amber-200">
          <h3 className="text-sm font-semibold text-amber-800">
            {CANCELLED_SECTION_TITLE}
          </h3>
          <p className="text-xs text-slate-500 mt-0.5 mb-2">{CANCELLED_COPY}</p>
          {cancelados.map((linha) => (
            <div key={linha.brandKey} className="mb-2 last:mb-0">
              <p className="text-sm font-medium text-slate-700">
                {linha.brand} · {linha.cancelledOrders} pedidos cancelados
              </p>
              <dl className="flex flex-col gap-0.5">
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
        </section>

        <p className="text-xs text-slate-500">{block.limitation_note}</p>
      </KpiDrilldownDialog>
    </section>
  );
}
