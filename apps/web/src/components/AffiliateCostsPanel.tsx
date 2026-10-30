"use client";

import { useState, type ReactNode } from "react";
import type { AffiliateCostRow, AffiliateCostsBlock } from "@/lib/api-client";
import {
  AFFILIATE_BLOCK_TITLE,
  AFFILIATE_COMPONENT_LABELS,
  AFFILIATE_COMPONENT_ORDER,
  brandLabel,
  deriveAffiliateBlockView,
  describeChannelStatus,
  formatRefMonth,
  formatSignedBrl,
  rowCoverageNote,
  type AffiliateBlockPhase,
  type AffiliateComponentKey,
} from "@/lib/canais-affiliate-costs";
import { CHANNEL_DOT } from "@/lib/gerencial/channel-colors";
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

/** O canal do bloco usa `mercadolivre`; a cor de identidade, `ml`. */
const PONTO_DO_CANAL: Record<string, string> = {
  tiktok: CHANNEL_DOT.tiktok,
  mercadolivre: CHANNEL_DOT.ml,
  shopee: CHANNEL_DOT.shopee,
};

/**
 * Tamanho do lancamento, so' para a largura da barra e a ordem. O VALOR
 * exibido continua com o sinal da fonte.
 */
const tamanho = (v: number | null) => (v == null ? -1 : v < 0 ? 0 - v : v);

/**
 * POPUPS-1 — detalhe do bloco de afiliados.
 *
 * Uma secao por lancamento (os tres NUNCA somados, nem entre si nem num
 * total), com as marcas do maior ao menor e a barra relativa a maior —
 * responde "onde esta' a comissao". Competencia e "nao somar" em selos; as
 * notas da fonte no ⓘ; cobertura incompleta em linha curta, porque muda a
 * leitura. Sai a lista marca a marca que so' repetia a tabela.
 */
function DetalheAfiliados({
  block,
  monthsLabel,
  coverageWarning,
  children,
}: {
  block: AffiliateCostsBlock;
  monthsLabel: string;
  coverageWarning: string | null;
  /** Selos extras (frescor), montados por quem tem a `view`. */
  children?: ReactNode;
}) {
  const variosMeses = block.months_included.length > 1;
  const rotulo = (r: AffiliateCostRow) =>
    variosMeses ? `${brandLabel(r.brand)} · ${formatRefMonth(r.ref_month)}` : brandLabel(r.brand);
  // Cobertura por competencia, uma linha por mes (a contagem e' do mes).
  const porMes = new Map<string, string>();
  for (const r of block.rows) {
    const nota = rowCoverageNote(r);
    if (nota) porMes.set(r.ref_month, `${formatRefMonth(r.ref_month)}: ${nota}`);
  }

  const secao = (chave: AffiliateComponentKey) => {
    const linhas = [...block.rows].sort((a, b) => tamanho(b[chave]) - tamanho(a[chave]));
    const maior = Math.max(0, ...linhas.map((r) => tamanho(r[chave])));
    return (
      <DrilldownSecao key={chave} titulo={AFFILIATE_COMPONENT_LABELS[chave]}>
        <ul className="flex flex-col">
          {linhas.map((r) => {
            const celula = formatSignedBrl(r[chave]);
            return (
              <EvidenceRow
                key={`${r.ref_month}:${r.brand}`}
                label={rotulo(r)}
                value={celula.text}
                tone={celula.tone}
                pct={r[chave] != null && maior > 0 ? (tamanho(r[chave]) / maior) * 100 : null}
              />
            );
          })}
        </ul>
      </DrilldownSecao>
    );
  };

  return (
    <div className="flex flex-col gap-4 text-sm">
      <div className="flex flex-col gap-2">
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-ink-muted">
          {monthsLabel && <span className={SELO}>Competência {monthsLabel}</span>}
          <span className="rounded-full bg-warn-soft px-2 py-0.5 font-medium text-warn-ink">Não somar</span>
          {children}
          <InfoDica texto={[block.limitation_note, block.return_note, block.source_note].filter(Boolean).join(" ")} />
        </p>
        <DataQualityNote note={coverageWarning} />
        {[...porMes.entries()].map(([mes, nota]) => (
          <DataQualityNote key={mes} note={nota} />
        ))}
      </div>

      {AFFILIATE_COMPONENT_ORDER.map(secao)}

      {/* Status por canal, AUTORITATIVO: ML e Shopee sao "Dados
          indisponíveis", nunca "Não aplicável". */}
      {block.channels.length > 0 && (
        <DrilldownSecao titulo="Por canal">
          <ul className="flex flex-col">
            {block.channels.map((canal) => {
              const d = describeChannelStatus(canal);
              return (
                <EvidenceRow
                  key={canal.channel}
                  label={d.label}
                  ponto={PONTO_DO_CANAL[canal.channel]}
                  value={d.text}
                  tone={d.tone}
                />
              );
            })}
          </ul>
        </DrilldownSecao>
      )}
    </div>
  );
}

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
    <section className="bg-surface rounded-xl border border-line p-5">
      <h2 className="text-sm font-semibold text-ink-muted mb-2">
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
        className="bg-surface rounded-xl border border-line p-5"
        aria-busy="true"
      >
        <h2 className="text-sm font-semibold text-ink-muted mb-4">
          {AFFILIATE_BLOCK_TITLE}
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
    // Fresco, mas sem bloco: a API não expõe `affiliate_costs`. Mensagem
    // estática, nunca R$ 0,00 — que seria lido como "não houve custo".
    return (
      <Moldura>
        <p className="text-sm text-ink-faint">Sem dados de afiliado no recorte.</p>
      </Moldura>
    );
  }

  const view = deriveAffiliateBlockView(block);

  return (
    <section className="bg-surface rounded-xl border border-line p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        {/* As notas da fonte (nao somados, retorno indisponivel, competencia)
            mudam a leitura dos valores: ficam no ⓘ ao lado do titulo. */}
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-ink-muted">
          {AFFILIATE_BLOCK_TITLE}
          <InfoDica texto={[block.limitation_note, block.return_note, block.source_note].filter(Boolean).join(" ")} />
        </h2>
        {view.hasRows && (
          <button
            type="button"
            onClick={() => setAberto(true)}
            className="text-xs font-medium text-accent hover:text-accent-ink underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded min-h-[44px] min-w-[44px] px-2"
          >
            Ver detalhe por marca
          </button>
        )}
      </div>

      {view.monthsLabel && (
        <p className="text-xs text-ink-faint mb-1">
          Competência: {view.monthsLabel}
        </p>
      )}
      {/* Frescor PROPRIO do bloco, nunca o `refreshedAt` geral da pagina — e
          so' quando uma fotografia foi de fato consultada. */}
      {view.freshnessLabel && (
        <p className="text-xs text-ink-faint mb-3">{view.freshnessLabel}</p>
      )}

      {view.coverageWarning && (
        <p className="text-xs text-warn-ink mb-3">{view.coverageWarning}</p>
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
              <tr className="text-left text-xs text-ink-faint border-b border-line">
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
                    className="border-b border-line last:border-0"
                  >
                    <th scope="row" className="py-2 pr-4 font-medium text-ink text-left">
                      {brandLabel(linha.brand)}
                      {nota && (
                        <span className="block text-xs font-normal text-warn-ink">
                          {nota}
                        </span>
                      )}
                    </th>
                    <td className="py-2 pr-4 text-ink-muted whitespace-nowrap">
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

      {/* Reusa o shell generico de drilldown. As linhas vem do bloco JA
          carregado — zero fetch adicional ao abrir, entao o dialogo nao pode
          divergir da tabela. */}
      <KpiDrilldownDialog
        open={aberto}
        onClose={() => setAberto(false)}
        title={AFFILIATE_BLOCK_TITLE}
      >
        <DetalheAfiliados block={block} monthsLabel={view.monthsLabel} coverageWarning={view.coverageWarning}>
          {/* Frescor PROPRIO do bloco: selo curto, a frase inteira no ⓘ. */}
          {view.freshnessLabel && (
            <span className={`${SELO} inline-flex items-center gap-1`}>
              Carga manual
              <InfoDica texto={view.freshnessLabel} />
            </span>
          )}
        </DetalheAfiliados>
      </KpiDrilldownDialog>
    </section>
  );
}
