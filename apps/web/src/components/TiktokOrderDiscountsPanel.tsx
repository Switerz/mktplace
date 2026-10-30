"use client";

import { useState } from "react";
import type { TikTokOrderDiscountRow, TikTokOrderDiscountsBlock } from "@/lib/api-client";
import { brandLabel } from "@/lib/canais-affiliate-costs";
import { CHANNEL_DOT } from "@/lib/gerencial/channel-colors";
import {
  buildDiscountTable,
  CANCELLED_COPY,
  CANCELLED_SECTION_TITLE,
  COVERAGE_LIMIT_COPY,
  coverageNote,
  deriveDiscountBlockView,
  DISCOUNT_COMPONENT_LABELS,
  DISCOUNT_COMPONENT_ORDER,
  DISCOUNTS_BLOCK_TITLE,
  formatCount,
  formatSignedBrl,
  formatSignedPct,
  RETROACTIVE_COPY,
  SELLER_COPY,
  SUBSIDY_COPY,
  type DiscountBlockPhase,
} from "@/lib/canais-tiktok-order-discounts";
import KpiDrilldownDialog from "./KpiDrilldownDialog";
import DrilldownSecao from "./drilldown/DrilldownSecao";
import EvidenceRow from "./drilldown/EvidenceRow";
import DataQualityNote from "./drilldown/DataQualityNote";
import InfoDica from "./ui/InfoDica";

const TONE_CLASS: Record<string, string> = {
  value: "text-ink",
  muted: "text-ink-faint",
  warning: "text-warn-ink",
};

/** POPUPS-1 — selo de contexto do detalhe (mesmo desenho do DrilldownContextLine). */
const SELO = "rounded-full border border-line bg-raised px-2 py-0.5 tabular-nums";

/**
 * Tamanho, so' para a largura da barra e a ordem. O VALOR exibido continua
 * com o sinal da fonte: negativo = a marca financiou.
 */
const tamanho = (v: number | null) => (v == null ? -1 : v < 0 ? 0 - v : v);

/** Largura da barra (0-100) relativa a maior linha. Nao e' taxa exibida. */
const barra = (v: number | null, maior: number) =>
  v == null || maior <= 0 ? null : (100 * tamanho(v)) / maior;

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
    <section className="bg-surface rounded-xl border border-line p-5">
      <h2 className="text-sm font-semibold text-ink-muted mb-2">
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
        className="bg-surface rounded-xl border border-line p-5"
        aria-busy="true"
      >
        {/* O titulo fica FORA da animacao: uma regiao `aria-busy` sem nome
            acessivel anuncia "carregando" sem dizer o que carrega. */}
        <h2 className="text-sm font-semibold text-ink-muted mb-4">
          {DISCOUNTS_BLOCK_TITLE}
        </h2>
        <div className="animate-pulse">
          <div className="h-8 w-full bg-accent-soft rounded mb-2" />
          <div className="h-8 w-full bg-accent-soft rounded" />
        </div>
      </section>
    );
  }

  if (phase === "unavailable") {
    return (
      <Moldura>
        <p className="text-sm text-warn-ink">Indisponível: falha ao carregar canais.</p>
      </Moldura>
    );
  }

  if (!block) {
    // Fresco, mas sem bloco: a API nao expoe `tiktok_order_discounts`.
    // Mensagem estatica, nunca R$ 0,00 — que seria lido como "sem desconto".
    return (
      <Moldura>
        <p className="text-sm text-ink-faint">Sem dados de desconto no recorte.</p>
      </Moldura>
    );
  }

  const view = deriveDiscountBlockView(block);
  const linhas = buildDiscountTable(block.rows);
  const notaCobertura = coverageNote(block);

  // POPUPS-1 — ordens do detalhe (maior para menor), so' com as linhas do
  // bloco. Desconto e subsidio sao ordenados pela TAXA do backend, que ja'
  // normaliza pelo valor cheio; nenhuma taxa e' recalculada aqui.
  const ordenar = (f: (r: TikTokOrderDiscountRow) => number | null) =>
    [...block.rows].sort((a, b) => tamanho(f(b)) - tamanho(f(a)));
  const maiorDe = (f: (r: TikTokOrderDiscountRow) => number | null) =>
    Math.max(0, ...block.rows.map((r) => tamanho(f(r))));
  const porGmv = ordenar((r) => r.official_gmv);
  const maiorGmv = maiorDe((r) => r.official_gmv);
  const porDesconto = ordenar((r) => r.seller_discount_rate);
  const maiorDesconto = maiorDe((r) => r.seller_discount_rate);
  const porSubsidio = ordenar((r) => r.platform_subsidy_rate);
  const maiorSubsidio = maiorDe((r) => r.platform_subsidy_rate);
  const porCancelados = ordenar((r) => r.cancelled_orders);

  return (
    <section className="bg-surface rounded-xl border border-line p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        {/* Quem financia cada componente e o "nao somar" mudam a leitura: ficam
            no ⓘ; as frases completas estao no detalhe. */}
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-ink-muted">
          {DISCOUNTS_BLOCK_TITLE}
          <InfoDica texto={`${SELLER_COPY} ${SUBSIDY_COPY} ${RETROACTIVE_COPY}`} />
        </h2>
        {view.hasRows && (
          <button
            type="button"
            onClick={() => setAberto(true)}
            className="text-xs font-medium text-accent hover:text-accent-ink underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded min-h-[44px] min-w-[44px] px-2"
          >
            Ver detalhe e cancelados
          </button>
        )}
      </div>

      {view.windowLabel && (
        <p className="text-xs text-ink-faint mb-1">
          Período: {view.windowLabel}
          {view.dayCountLabel ? ` · ${view.dayCountLabel}` : ""}
        </p>
      )}
      {/* Frescor PROPRIO do bloco, nunca o `refreshedAt` geral da pagina. */}
      {view.freshnessLabel && (
        <p className="text-xs text-ink-faint mb-3">{view.freshnessLabel}</p>
      )}

      {view.warnings.length > 0 && (
        <ul className="mb-3 flex flex-col gap-1">
          {view.warnings.map((aviso) => (
            <li key={aviso} className="text-xs text-warn-ink">
              {aviso}
            </li>
          ))}
        </ul>
      )}
      {notaCobertura && !view.warnings.length && (
        <p className="text-xs text-warn-ink mb-3">{notaCobertura}</p>
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
              <tr className="text-left text-xs text-ink-faint border-b border-line">
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
                  className="border-b border-line last:border-0"
                >
                  <th scope="row" className="py-2 pr-4 font-medium text-ink text-left">
                    {linha.brand}
                  </th>
                  <td className="py-2 pr-4 text-right tabular-nums text-ink-muted whitespace-nowrap">
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

      {/* Reusa o shell generico de drilldown — nenhum modal proprio. As linhas
          vem do bloco JA carregado: zero fetch ao abrir, entao o dialogo nao
          pode divergir da tabela. */}
      <KpiDrilldownDialog
        open={aberto}
        onClose={() => setAberto(false)}
        title={DISCOUNTS_BLOCK_TITLE}
      >
        <div className="flex flex-col gap-4 text-sm">
          {/* POPUPS-1 — contexto em selos; quem financia, revisao retroativa
              e o limite da cobertura no ⓘ; o "nao somar" fica a' vista. */}
          <div className="flex flex-col gap-2">
            <p className="flex flex-wrap items-center gap-1.5 text-xs text-ink-muted">
              {view.windowLabel && <span className={SELO}>{view.windowLabel}</span>}
              {view.dayCountLabel && <span className={SELO}>{view.dayCountLabel}</span>}
              <span className="rounded-full bg-warn-soft px-2 py-0.5 font-medium text-warn-ink">Não somar</span>
              {view.freshnessLabel && (
                <span
                  className={`${SELO} inline-flex items-center gap-1 ${
                    block.freshness_status === "stale_load" ? "text-warn-ink" : ""
                  }`}
                >
                  {block.freshness_status === "stale_load" ? "Carga antiga" : "Carga recente"}
                  <InfoDica texto={view.freshnessLabel} />
                </span>
              )}
              <InfoDica
                texto={`${SELLER_COPY} ${SUBSIDY_COPY} ${RETROACTIVE_COPY}${
                  view.coverageIsIncomplete ? "" : ` ${COVERAGE_LIMIT_COPY}`
                } ${block.limitation_note}`}
              />
            </p>
            {view.warnings.map((aviso) => (
              <DataQualityNote key={aviso} note={aviso} />
            ))}
            {!view.warnings.length && <DataQualityNote note={notaCobertura} />}
          </div>

          {/* Escala de cada marca: GMV oficial e pedidos comerciais, com o
              valor cheio (base das duas taxas) na sub-linha. */}
          <DrilldownSecao titulo="Vendas comerciais por marca">
            <ul className="flex flex-col">
              {porGmv.map((r) => (
                <EvidenceRow
                  key={r.brand}
                  label={brandLabel(r.brand)}
                  value={`${formatSignedBrl(r.official_gmv).text} · ${formatCount(r.commercial_orders)} pedidos`}
                  tone={r.official_gmv == null ? "muted" : "value"}
                  pct={barra(r.official_gmv, maiorGmv)}
                  reference={
                    r.full_product_value != null
                      ? `Valor cheio ${formatSignedBrl(r.full_product_value).text}`
                      : null
                  }
                />
              ))}
            </ul>
          </DrilldownSecao>

          {/* Os dois componentes em secoes PROPRIAS: financiadores diferentes.
              Cada linha traz o valor e a taxa do backend, com o sinal. */}
          <DrilldownSecao titulo={DISCOUNT_COMPONENT_LABELS.seller_discount_signed} acao={<span>% do valor cheio</span>}>
            <ul className="flex flex-col">
              {porDesconto.map((r) => (
                <EvidenceRow
                  key={r.brand}
                  label={brandLabel(r.brand)}
                  value={`${formatSignedBrl(r.seller_discount_signed).text} · ${formatSignedPct(r.seller_discount_rate).text}`}
                  tone={r.seller_discount_signed == null ? "muted" : "value"}
                  pct={barra(r.seller_discount_rate, maiorDesconto)}
                />
              ))}
            </ul>
          </DrilldownSecao>

          <DrilldownSecao titulo={DISCOUNT_COMPONENT_LABELS.platform_subsidy_amount} acao={<span>% do valor cheio</span>}>
            <ul className="flex flex-col">
              {porSubsidio.map((r) => (
                <EvidenceRow
                  key={r.brand}
                  label={brandLabel(r.brand)}
                  value={`${formatSignedBrl(r.platform_subsidy_amount).text} · ${formatSignedPct(r.platform_subsidy_rate).text}`}
                  tone={r.platform_subsidy_amount == null ? "muted" : "value"}
                  pct={barra(r.platform_subsidy_rate, maiorSubsidio)}
                  barra={CHANNEL_DOT.tiktok}
                />
              ))}
            </ul>
          </DrilldownSecao>

          {/* Secao VISUALMENTE separada. Cancelados nao integram as vendas
              comerciais, e exibi-los na mesma lista convidaria a soma-los. */}
          <section className="flex flex-col gap-1.5 border-t-2 border-warn/35 pt-3">
            <div className="flex items-center justify-between gap-2">
              <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-warn-ink">
                {CANCELLED_SECTION_TITLE}
                <InfoDica texto={CANCELLED_COPY} />
              </h3>
            </div>
            <ul className="flex flex-col">
              {porCancelados.map((r) => (
                <EvidenceRow
                  key={r.brand}
                  label={brandLabel(r.brand)}
                  value={`${formatCount(r.cancelled_orders)} pedidos`}
                  reference={`Desconto da marca ${formatSignedBrl(r.cancelled_seller_discount_signed).text} · Subsídio do TikTok ${formatSignedBrl(r.cancelled_platform_subsidy_amount).text}`}
                />
              ))}
            </ul>
          </section>
        </div>
      </KpiDrilldownDialog>
    </section>
  );
}
