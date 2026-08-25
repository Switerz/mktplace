"use client";

import { useSortableTable } from "@/lib/use-sortable-table";
import SortableHeader from "@/components/SortableHeader";
import TableScrollHint from "@/components/TableScrollHint";
import PeriodSelector from "@/components/PeriodSelector";
import ChannelMixChart from "@/components/ChannelMixChart";
import DataQualityNote from "@/components/drilldown/DataQualityNote";
import { SkeletonKpiCard } from "@/components/Skeleton";
import { fmtBrl, fmtNumber } from "@/lib/formatters";
import { decBr, pctBr } from "@/lib/inteligencia/format";
import type { BrandDetail, BrandDetailChannelRow, BrandDetailProduto } from "@/lib/api-client";
import type { MonthOption } from "@/lib/produtos-tab-transition";
import { fmtCompetencia, type MonthlyViewState } from "@/lib/brand/ref-month";
import { SECTION_MENSAL_PRODUTOS } from "@/lib/brand-arrival-context";

interface Props {
  state: MonthlyViewState;
  /** Só chega não-nulo quando a resposta da competência ATUAL está fresca. */
  detail: BrandDetail | null;
  refMonth: string | null;
  months: MonthOption[];
  onSelectMonth: (month: string) => void;
  onRetry: () => void;
  onOpenChannel: (row: BrandDetailChannelRow) => void;
  onOpenProduct: (produto: BrandDetailProduto) => void;
  /** Nota neutra quando a competência não intersecta o intervalo global. */
  regimeNote: string | null;
  /** `false` quando a marca não tem competência alguma no contrato — o vazio
   * "sem histórico" tem copy própria, e não é o mesmo que "mês sem dado". */
  hasHistory: boolean;
  /** `ref_month` ecoado pela resposta, para nomear a competência quando a URL
   * não pediu nenhuma e a lista veio vazia (`refMonth` fica `null` ali). */
  servedMonth: string | null;
  disabled?: boolean;
}

/**
 * Contêiner do REGIME MENSAL da Marca 360 (Gate V3-2, §8.2 do desenho).
 *
 * O regime temporal é propriedade **do contêiner**, não de cada card: tudo
 * aqui dentro é governado pela competência do `PeriodSelector`, e nada aqui
 * dentro é governado pelo intervalo global. Era exatamente essa fronteira que
 * faltava (defeito M1) — a página podia mostrar mix de canal de julho ao lado
 * de análise mensal de agosto sem nenhuma marcação.
 *
 * Duas decisões de leitura que valem registro:
 *
 * - **cada bloco repete a etiqueta de competência**, em vez de uma faixa
 *   sticky. O wireframe previa sticky no mobile; a etiqueta por bloco cumpre o
 *   mesmo objetivo (nunca perder a referência temporal ao rolar) e é
 *   estritamente mais informativa, porque sobrevive a captura de tela e a
 *   leitor de tela;
 * - **o cabeçalho não exibe timestamp.** `/brand-detail` não traz
 *   `refreshed_at` no contrato; inventar "atualizado agora" seria fabricar
 *   frescor. O que o cabeçalho declara é a fonte e o ESTADO da leitura.
 */
export default function TikTokMonthlyPanel({
  state, detail, refMonth, months, onSelectMonth, onRetry,
  onOpenChannel, onOpenProduct, regimeNote, hasHistory, servedMonth, disabled,
}: Props) {
  // Quando a URL não pediu competência e a lista veio vazia, `refMonth` é null:
  // aí o nome vem do `ref_month` que a resposta ecoou, nunca de um travessão.
  const competencia = fmtCompetencia(refMonth ?? servedMonth);
  const funnelRows = detail?.channel_funnel ?? [];
  const funnelSort = useSortableTable(
    funnelRows,
    (row: BrandDetailChannelRow, column: string) => {
      switch (column) {
        case "channel": return row.label;
        case "impressions": return row.impressions;
        case "ctr_pct": return row.ctr_pct;
        case "page_views": return row.page_views;
        case "cvr_pct": return row.cvr_pct;
        case "gmv": return row.gmv;
        default: return null;
      }
    },
    {
      channel: "text" as const, impressions: "numeric" as const, ctr_pct: "numeric" as const,
      page_views: "numeric" as const, cvr_pct: "numeric" as const, gmv: "numeric" as const,
    },
  );

  return (
    <section
      aria-label={`TikTok Shop, análise mensal da competência ${competencia}`}
      aria-busy={state === "loading"}
      className="rounded-2xl border-2 border-violet-200 bg-violet-50/30 overflow-hidden"
    >
      {/* ── faixa de título: o regime é do contêiner ────────────────────── */}
      <div className="bg-violet-100/70 border-b border-violet-200 px-4 sm:px-6 py-3 flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-bold text-violet-800">TikTok Shop · análise mensal</h2>
          <p className="text-xs text-violet-700">
            Competência <strong>{competencia}</strong> · fonte: contrato mensal do TikTok Shop ·{" "}
            <EstadoLeitura state={state} competencia={competencia} />
          </p>
        </div>
        {months.length > 0 && refMonth != null && (
          <PeriodSelector value={refMonth} onChange={onSelectMonth} months={months} />
        )}
      </div>

      <div className="px-4 sm:px-6 py-5 flex flex-col gap-5">
        {/* Relação entre os dois regimes: não é erro, é escopo — tom neutro. */}
        {regimeNote && (
          <p className="text-xs text-slate-600 bg-white border border-violet-100 rounded-xl px-3 py-2">
            {regimeNote}
          </p>
        )}

        {state === "loading" && (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3" aria-hidden="true">
            <SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard />
            <SkeletonKpiCard /><SkeletonKpiCard /><SkeletonKpiCard />
          </div>
        )}

        {state === "error" && (
          <div className="bg-white border border-rose-200 rounded-xl px-4 py-5 flex flex-col items-start gap-2">
            <p className="text-sm font-semibold text-rose-900">
              A leitura mensal falhou. Nenhum dado de {competencia} está sendo exibido.
            </p>
            <p className="text-xs text-slate-600">
              Nada aqui foi preenchido com resposta anterior nem com número gerado — a falha aparece
              como falha. Não sabemos ainda se a marca tem dado nesta competência.
            </p>
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex items-center justify-center min-h-11 min-w-11 px-4 rounded-xl border border-violet-200 bg-white text-sm font-semibold text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
            >
              Tentar novamente
            </button>
          </div>
        )}

        {/* Vazio SEM HISTÓRICO: a leitura teve sucesso e a marca não tem nenhuma
            competência com dado. Copy própria, porque oferecer "escolha outro
            mês" quando não existe mês algum seria conselho impossível. */}
        {state === "empty" && !hasHistory && (
          <div className="bg-white border border-violet-100 rounded-xl px-4 py-5">
            <p className="text-sm font-semibold text-slate-800">
              Esta marca não tem histórico no TikTok Shop.
            </p>
            <p className="text-xs text-slate-600 mt-1">
              A leitura de {competencia} foi bem-sucedida e o contrato não devolveu{" "}
              <strong>nenhuma competência com dado</strong> para esta marca. Não há seletor porque
              não há mês para escolher, e nenhum indicador foi exibido: zero agregado de um
              histórico inexistente não é venda zero.
            </p>
          </div>
        )}

        {/* Vazio de MÊS: a marca tem histórico, mas não neste mês. */}
        {state === "empty" && hasHistory && (
          <div className="bg-white border border-violet-100 rounded-xl px-4 py-5">
            <p className="text-sm font-semibold text-slate-800">
              Sem dado de TikTok Shop para {competencia}.
            </p>
            <p className="text-xs text-slate-600 mt-1">
              A competência pedida foi <strong>preservada</strong> — nenhum outro mês foi selecionado
              no seu lugar em silêncio.{" "}
              {months.length > 0 &&
                `Competências com dado para esta marca: ${months.map((m) => m.label).join(" · ")}.`}
            </p>
          </div>
        )}

        {state === "ready" && detail && (
          <>
            {/* ── KPIs mensais ───────────────────────────────────────────── */}
            <BlocoMensal titulo="Indicadores da competência" competencia={competencia}>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                <Stat label="GMV" value={fmtBrl(detail.gmv)} />
                <Stat label="Pedidos" value={fmtNumber(detail.orders)} />
                <Stat
                  label="Ticket médio"
                  value={detail.orders > 0 ? fmtBrl(detail.gmv / detail.orders) : "N/D"}
                  sub={detail.orders > 0 ? undefined : "sem pedidos na competência"}
                />
                <Stat label="Clientes" value={fmtNumber(detail.customers)} sub="dias com visitantes" />
                <Stat label="Conversão" value={pct(detail.cvr_pct)} sub="visitantes com dados" />
                <Stat label="COS" value={pct(detail.cos_pct)} sub="custo sobre GMV" />
              </div>
            </BlocoMensal>

            {/* ── mix de SUPERFÍCIE do TikTok: vídeo × live × product card ─
                Grandeza diferente do mix por marketplace do bloco global, com
                outra fonte (a série mensal deste contrato) e outro período. */}
            <BlocoMensal titulo="Mix de superfície · GMV diário" competencia={competencia}>
              <div className="bg-white border border-violet-100 rounded-xl px-4 pt-4 pb-2">
                <div className="flex flex-wrap gap-4 text-xs text-slate-600 mb-1">
                  <span>Vídeo <strong className="text-violet-700">{pct(detail.pct_video)}</strong></span>
                  <span>Live <strong className="text-cyan-700">{pct(detail.pct_live)}</strong></span>
                  <span>Product card <strong className="text-amber-700">{pct(detail.pct_card)}</strong></span>
                </div>
                {detail.daily.length > 0 ? (
                  <ChannelMixChart data={detail.daily} />
                ) : (
                  <p className="text-sm text-slate-500 py-8 text-center">
                    Sem série diária nesta competência.
                  </p>
                )}
              </div>
            </BlocoMensal>

            {/* ── funil por canal + drill-down por linha ─────────────────── */}
            <BlocoMensal titulo="Funil por superfície" competencia={competencia}>
              {funnelRows.length === 0 ? (
                <p className="text-sm text-slate-500 py-4">
                  Sem funil por superfície nesta competência. O bloco não é preenchido com zero.
                </p>
              ) : (
                <div className="bg-white border border-violet-100 rounded-xl overflow-hidden">
                  <TableScrollHint>
                    <table className="w-full text-sm" aria-label={`Funil de conversão por superfície em ${competencia}`}>
                      <caption className="sr-only">
                        Impressões, CTR, visitas à página do produto, CVR e GMV por superfície do TikTok Shop na competência {competencia}
                      </caption>
                      <thead>
                        <tr className="bg-slate-50 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                          <SortableHeader label="Superfície" column="channel" sort={funnelSort.sort} onSort={funnelSort.toggleSort} align="left" />
                          <SortableHeader label="Impressões" column="impressions" sort={funnelSort.sort} onSort={funnelSort.toggleSort} />
                          <SortableHeader label="CTR%" column="ctr_pct" sort={funnelSort.sort} onSort={funnelSort.toggleSort} />
                          <SortableHeader label="Pág. produto" column="page_views" sort={funnelSort.sort} onSort={funnelSort.toggleSort} />
                          <SortableHeader label="CVR%" column="cvr_pct" sort={funnelSort.sort} onSort={funnelSort.toggleSort} />
                          <SortableHeader label="GMV" column="gmv" sort={funnelSort.sort} onSort={funnelSort.toggleSort} />
                          <th scope="col" className="px-4 py-3 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider">Detalhe</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {funnelSort.sortedRows.map((ch) => (
                          <tr key={ch.channel}>
                            <td className="px-4 py-3 font-semibold text-slate-700">
                              <span className={`inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full font-semibold ${superficieTom(ch.channel)}`}>
                                {ch.label}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-right tabular-nums text-slate-600">{fmtNumber(ch.impressions)}</td>
                            <td className="px-4 py-3 text-right tabular-nums text-xs font-semibold text-slate-700">{pct(ch.ctr_pct, 2)}</td>
                            <td className="px-4 py-3 text-right tabular-nums text-slate-600">{fmtNumber(ch.page_views)}</td>
                            <td className="px-4 py-3 text-right tabular-nums text-xs font-semibold text-slate-700">{pct(ch.cvr_pct, 2)}</td>
                            <td className="px-4 py-3 text-right tabular-nums font-semibold text-slate-800">{fmtBrl(ch.gmv)}</td>
                            <td className="px-4 py-3 text-right">
                              <button
                                type="button"
                                onClick={() => onOpenChannel(ch)}
                                disabled={disabled}
                                aria-label={`Detalhe do funil da superfície ${ch.label} em ${competencia}`}
                                className="inline-flex items-center justify-center min-h-11 min-w-11 px-3 text-xs font-semibold text-violet-700 border border-violet-200 rounded-lg hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 disabled:opacity-50"
                              >
                                Detalhe
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableScrollHint>
                  <p className="px-4 py-2.5 border-t border-slate-100 text-xs text-slate-500">
                    CTR = impressões que geraram visita à página do produto · CVR = visitas que
                    converteram em pedido. As duas taxas vêm prontas do contrato.
                  </p>
                </div>
              )}
            </BlocoMensal>

            {/* ── conteúdo e creators ────────────────────────────────────── */}
            <BlocoMensal titulo="Conteúdo e creators" competencia={competencia}>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className="bg-white border border-violet-100 rounded-xl px-4 py-4">
                  <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Ecossistema</h4>
                  <div className="grid grid-cols-2 gap-3">
                    <Stat label="Vídeos ativos" value={fmtNumber(detail.active_videos)} />
                    <Stat label="Vídeos novos" value={fmtNumber(detail.new_videos_posted)} />
                    <Stat label="Creators de vídeo" value={fmtNumber(detail.active_video_creators)} />
                    <Stat label="Visualizações" value={fmtNumber(detail.total_views)} />
                    <Stat label="Lives" value={fmtNumber(detail.total_lives)} />
                    <Stat label="Creators de live" value={fmtNumber(detail.live_creators)} />
                  </div>
                  <p className="text-xs text-slate-500 mt-3">
                    Vídeos ativos e visualizações vêm da tabela <strong>de marca</strong>, não da soma
                    dos produtos: os dois números não conferem entre si e não devem ser comparados.
                  </p>
                </div>

                <div className="bg-white border border-violet-100 rounded-xl px-4 py-4">
                  <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Atratividade e frescor</h4>
                  <div className="grid grid-cols-2 gap-3">
                    <Stat label="GMV / mil views" value={detail.gpm == null ? "Sem dado" : `R$ ${decBr(detail.gpm, 2)}`} />
                    <Stat label="GMV por vídeo" value={detail.gmv_per_video == null ? "Sem dado" : fmtBrl(detail.gmv_per_video)} />
                    <Stat label="GMV por creator" value={detail.gmv_per_creator == null ? "Sem dado" : fmtBrl(detail.gmv_per_creator)} />
                    <Stat label="GMV por live" value={detail.gmv_per_live == null ? "Sem dado" : fmtBrl(detail.gmv_per_live)} />
                    <Stat label="Vídeos / creator" value={detail.videos_per_creator == null ? "Sem dado" : decBr(detail.videos_per_creator, 1)} />
                    <Stat label="Receita de vídeos novos" value={pct(detail.pct_gmv_fresh)} />
                  </div>
                  <div className="grid grid-cols-2 gap-3 mt-3">
                    <Stat label="GMV fresh" value={fmtBrl(detail.gmv_fresh)} sub={`${fmtNumber(detail.fresh_videos)} vídeos`} />
                    <Stat label="GMV evergreen" value={fmtBrl(detail.gmv_evergreen)} sub={`${fmtNumber(detail.evergreen_videos)} vídeos`} />
                  </div>
                </div>
              </div>

              {detail.top_creators.length > 0 && (
                <div className="bg-white border border-violet-100 rounded-xl overflow-hidden mt-4">
                  <h4 className="px-4 py-3 border-b border-violet-100 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                    Top 5 creators por GMV · {competencia}
                  </h4>
                  <TableScrollHint>
                    <table className="w-full" aria-label={`Top 5 creators por GMV em ${competencia}`}>
                      <thead>
                        <tr className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                          <th scope="col" className="text-left px-4 py-3">Creator</th>
                          <th scope="col" className="text-right px-4 py-3">GMV</th>
                          <th scope="col" className="text-right px-4 py-3">Vídeos</th>
                          <th scope="col" className="text-right px-4 py-3">Lives</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {detail.top_creators.map((c, i) => (
                          <tr key={c.creator}>
                            <td className="px-4 py-3 text-sm text-slate-700 font-medium">
                              <span className="text-slate-400 tabular-nums mr-2">{i + 1}.</span>
                              {c.creator}
                            </td>
                            <td className="text-right px-4 py-3 text-sm font-bold text-slate-900 tabular-nums">{fmtBrl(c.gmv)}</td>
                            <td className="text-right px-4 py-3 text-sm text-slate-600 tabular-nums">{fmtNumber(c.videos)}</td>
                            <td className="text-right px-4 py-3 text-sm text-slate-600 tabular-nums">{fmtNumber(c.lives)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableScrollHint>
                  <p className="px-4 py-2.5 border-t border-slate-100 text-xs text-slate-500">
                    Cinco de ao menos cinco — a lista vem capada no contrato. Sem detalhe por creator:
                    nome de creator é dado sensível e não viaja em URL.
                  </p>
                </div>
              )}
            </BlocoMensal>

            {/* ── produtos + drill-down por linha ───────────────────────── */}
            <BlocoMensal
              titulo="Produtos do TikTok Shop"
              competencia={competencia}
              id={SECTION_MENSAL_PRODUTOS}
            >
              {detail.top_produtos.length === 0 ? (
                <p className="text-sm text-slate-500 py-4">
                  Sem produtos com dado nesta competência. O bloco não é preenchido com zero.
                </p>
              ) : (
                <div className="bg-white border border-violet-100 rounded-xl overflow-hidden">
                  <TableScrollHint>
                    <table className="w-full" aria-label={`Top 5 produtos por GMV em ${competencia}`}>
                      <thead>
                        <tr className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                          <th scope="col" className="text-left px-4 py-3">Produto</th>
                          <th scope="col" className="text-right px-4 py-3">GMV</th>
                          <th scope="col" className="text-right px-4 py-3">Pedidos</th>
                          <th scope="col" className="text-right px-4 py-3">Vídeos</th>
                          <th scope="col" className="text-right px-4 py-3">GMV/1k views</th>
                          <th scope="col" className="text-right px-4 py-3">Detalhe</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {detail.top_produtos.map((p, i) => (
                          <tr key={p.product_id}>
                            {/* `min-w`: no mobile as outras cinco colunas reivindicavam a largura
                                natural e sobravam 114px para o nome, que quebrava em ate 13
                                linhas. A tabela ja rola na horizontal (TableScrollHint), entao
                                dar largura minima ao nome apenas desloca a rolagem — nada e
                                truncado e a pagina continua sem overflow. */}
                            <td className="px-4 py-3 text-sm text-slate-700 min-w-[180px] max-w-[260px]">
                              <span className="text-slate-400 tabular-nums mr-2">{i + 1}.</span>
                              <span className="font-medium">{p.product_name}</span>
                            </td>
                            <td className="text-right px-4 py-3 text-sm font-bold text-slate-900 tabular-nums">{fmtBrl(p.gmv)}</td>
                            <td className="text-right px-4 py-3 text-sm text-slate-600 tabular-nums">{fmtNumber(p.orders)}</td>
                            <td className="text-right px-4 py-3 text-sm text-slate-600 tabular-nums">{fmtNumber(p.videos)}</td>
                            <td className="text-right px-4 py-3 text-sm text-slate-600 tabular-nums">
                              {p.gpm == null ? <span className="text-slate-400" title="sem dado">Sem dado</span> : `R$ ${decBr(p.gpm, 2)}`}
                            </td>
                            <td className="text-right px-4 py-3">
                              <button
                                type="button"
                                onClick={() => onOpenProduct(p)}
                                disabled={disabled}
                                aria-label={`Detalhe do produto ${p.product_name} em ${competencia}`}
                                className="inline-flex items-center justify-center min-h-11 min-w-11 px-3 text-xs font-semibold text-violet-700 border border-violet-200 rounded-lg hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 disabled:opacity-50"
                              >
                                Detalhe
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableScrollHint>
                </div>
              )}
            </BlocoMensal>

            {/* ── limitações do dado, compactas e no lugar certo ────────── */}
            <div className="flex flex-col gap-2">
              <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Limitações do dado nesta competência
              </h3>
              <DataQualityNote
                note={
                  "Audiência não é coberta: as sete colunas de demografia (gênero e faixa etária de " +
                  "viewers e followers) são 100% nulas na fonte, então o bloco de Demographics foi " +
                  "removido em vez de exibir sete traços. Ads, ROAS e margem também não existem aqui — " +
                  "o contrato mensal do TikTok não traz investimento de mídia nem custo do produto. " +
                  "E este contrato não carrega timestamp de sincronização: o cabeçalho declara a fonte " +
                  "e o estado da leitura, nunca um 'atualizado em' que não foi entregue."
                }
              />
            </div>
          </>
        )}
      </div>
    </section>
  );
}

/** Bloco interno: título + etiqueta de COMPETÊNCIA sempre visível. */
function BlocoMensal({ titulo, competencia, id, children }: {
  titulo: string;
  competencia: string;
  id?: string;
  children: React.ReactNode;
}) {
  return (
    <div id={id} className={id ? "scroll-mt-28" : undefined}>
      <div className="flex items-baseline justify-between gap-3 flex-wrap mb-2">
        <h3 className="text-sm font-semibold text-slate-700">{titulo}</h3>
        <span className="text-xs font-semibold text-violet-700 bg-violet-100 rounded-full px-2.5 py-0.5">
          competência {competencia}
        </span>
      </div>
      {children}
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-slate-50 rounded-xl px-3 py-2.5">
      <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-0.5">{label}</p>
      <p className="text-base font-bold text-slate-800 tabular-nums leading-tight">{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
    </div>
  );
}

/** Estado da leitura mensal por extenso — nunca um timestamp inventado. */
function EstadoLeitura({ state, competencia }: { state: MonthlyViewState; competencia: string }) {
  const texto: Record<MonthlyViewState, string> = {
    loading: "lendo a competência…",
    error: "leitura falhou",
    empty: `sem dado em ${competencia}`,
    ready: "leitura ao vivo",
  };
  return <span className="font-semibold">{texto[state]}</span>;
}

/**
 * `null` é ausência de cobertura; zero é medida. Nunca se troca um pelo outro.
 *
 * E o separador decimal é **vírgula**: `toFixed` é insensível a locale e
 * renderizava `0.00%` numa interface inteiramente pt-BR — o mesmo defeito que o
 * QA visual do V3-1A já havia fechado na Inteligência, e que o QA do V3-3 pegou
 * de volta aqui. A quantidade de casas não mudou.
 */
function pct(v: number | null | undefined, digits = 1): string {
  return v == null ? "Sem dado" : pctBr(v, digits);
}

function superficieTom(channel: string): string {
  if (channel === "VIDEO") return "bg-violet-100 text-violet-700";
  if (channel === "LIVE") return "bg-cyan-100 text-cyan-700";
  return "bg-amber-100 text-amber-700";
}
