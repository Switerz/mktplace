"use client";

/**
 * Mais vendidos por marca (Gerencial).
 *
 * Referencia: "Mix Marketplace" da Natalia. O ranking e' por ANUNCIO VENDIDO
 * em cada canal — kit e' kit, sem de-para — e respeita canal, marca, periodo e
 * comparacao da barra de filtros. Cada linha abre o detalhe: indicadores do
 * periodo, venda por dia/semana/mes e o que so' o canal sabe (TikTok: video,
 * live e card; ML: ads e ROAS).
 */
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { ChevronDown, Trophy } from "lucide-react";

import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import TableScrollHint from "@/components/TableScrollHint";
import InfoDica from "@/components/ui/InfoDica";
import Segmented from "@/components/ui/Segmented";
import { CHANNEL_DOT } from "@/lib/gerencial/channel-colors";
import { CHANNEL_LABEL } from "@/lib/gerencial/kpi-band";
import { MARCAS, hojeBrt } from "@/lib/metas";
import { fmtBrlCurto, fmtNumCurto, fmtPctBr, fmtVezes } from "@/lib/formatters";
import {
  agrupar, buscarDetalhe, buscarTopProdutos, comparacaoValida, rotuloPeriodo, variacao,
  type DetalheProduto, type Grao, type MarcaTop, type ProdutoTop, type TopProdutosResponse,
} from "@/lib/gerencial/top-produtos";

const Grafico = dynamic(() => import("./TopProdutoGrafico"), {
  ssr: false,
  loading: () => <div className="h-full w-full animate-pulse rounded-lg bg-raised motion-reduce:animate-none" />,
});

const VISIVEIS = 5;
const rotuloMarca = (id: string) => MARCAS.find((m) => m.id === id)?.label ?? id.toUpperCase();
const TH = "py-2 px-2 text-xs font-semibold uppercase tracking-wide text-ink-faint";

interface Props {
  channels: string[];
  brands: string[];
  dateFrom: string;
  dateTo: string;
  compare: boolean;
}

export default function TopProdutosMarca({ channels, brands, dateFrom, dateTo, compare }: Props) {
  const [dados, setDados] = useState<TopProdutosResponse | null>(null);
  const [erro, setErro] = useState(false);
  const [tentativa, setTentativa] = useState(0);
  const [abertas, setAbertas] = useState<Record<string, boolean>>({});
  const [marcaSel, setMarcaSel] = useState<string | null>(null);
  const [aberto, setAberto] = useState<{ marca: string; produto: ProdutoTop } | null>(null);
  const chave = [channels.join(","), brands.join(","), dateFrom, dateTo, compare].join("|");

  useEffect(() => {
    const ac = new AbortController();
    const q = new URLSearchParams({ channels: channels.join(","), date_from: dateFrom, date_to: dateTo, compare: String(compare) });
    if (brands.length) q.set("brands", brands.join(","));
    setErro(false);
    setDados(null);
    setAberto(null);
    buscarTopProdutos(q, ac.signal).then(setDados).catch((e) => {
      if (!(e instanceof DOMException && e.name === "AbortError")) setErro(true);
    });
    return () => ac.abort();
    // `chave` resume os filtros; `tentativa` refaz a busca.
  }, [chave, tentativa]);

  const comparando = dados ? comparacaoValida(dados, hojeBrt()) : false;
  const soHojeSemComparar = Boolean(dados?.comparacao_inicio) && !comparando;
  const vsRotulo = dados?.comparacao_inicio && dados.comparacao_fim
    ? `vs ${rotuloPeriodo(dados.comparacao_inicio, dados.comparacao_fim)}`
    : "vs anterior";
  // Aba: a escolhida, se ainda existe no recorte; senao a marca que mais vendeu.
  const marca = dados?.marcas.find((m) => m.marca === marcaSel) ?? dados?.marcas[0] ?? null;

  return (
    <section aria-labelledby="top-produtos-titulo" className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Trophy aria-hidden="true" className="h-4 w-4 text-ink-faint" strokeWidth={1.75} />
          <h2 id="top-produtos-titulo" className="text-sm font-semibold text-ink-muted">
            Mais vendidos por marca
          </h2>
          <InfoDica texto="Ranking por receita do anúncio como foi vendido em cada canal: kit conta como kit, sem de-para. TikTok e Mercado Livre somam o mesmo GMV da Torre; Shopee usa o preço do item sem cancelados e brindes. Clique num produto para ver o detalhe." />
        </div>
        <span className="text-xs text-ink-faint">Clique num produto para ver o detalhe</span>
      </div>

      {erro ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-raised p-3">
          <p className="text-sm text-ink-muted">Mais vendidos indisponível nesta carga.</p>
          <button
            type="button"
            onClick={() => setTentativa((t) => t + 1)}
            className="min-h-11 rounded-lg border border-line-strong px-3 text-xs font-semibold text-ink-muted hover:bg-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            Tentar novamente
          </button>
        </div>
      ) : !dados ? (
        <div className="flex flex-col gap-2" role="status" aria-busy="true">
          <span className="sr-only">Carregando mais vendidos…</span>
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="h-10 animate-pulse rounded bg-accent-soft motion-reduce:animate-none" />
          ))}
        </div>
      ) : !marca ? (
        <p className="text-sm text-ink-faint">Sem venda de produto no período e filtros selecionados.</p>
      ) : (
        <>
        <div className="flex flex-col gap-2">
          <div className="-mx-1 overflow-x-auto px-1 pb-1">
            <Segmented
              rotulo="Marca"
              opcoes={dados.marcas.map((m) => ({ valor: m.marca, texto: `${rotuloMarca(m.marca)} · ${fmtBrlCurto(m.receita_marca)}` }))}
              valor={marca.marca}
              aoEscolher={setMarcaSel}
            />
          </div>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-faint">
            <span className="tabular-nums">{fmtBrlCurto(marca.receita_marca)} no período</span>
            {comparando && (
              <>
                <Delta valor={variacao(marca.receita_marca, marca.receita_marca_ant)} />
                <span>{vsRotulo}</span>
              </>
            )}
            {soHojeSemComparar && <span>· dia em andamento, sem comparação com ontem</span>}
          </p>
        </div>
        <TableScrollHint>
          <table className="w-full min-w-[760px] table-fixed border-collapse">
            <caption className="sr-only">
              Produtos que mais venderam por marca no período, com receita, peças, preço médio e participação na receita da marca.
            </caption>
            <thead>
              <tr className="border-b border-line">
                <th scope="col" className={`${TH} w-9 text-left`}>#</th>
                <th scope="col" className={`${TH} text-left`}>Produto</th>
                <th scope="col" className={`${TH} w-28 text-right`}>Receita</th>
                {comparando && <th scope="col" className={`${TH} w-32 text-right`}>{vsRotulo}</th>}
                <th scope="col" className={`${TH} w-20 text-right`}>Peças</th>
                <th scope="col" className={`${TH} w-28 text-right`}>Preço médio</th>
                <th scope="col" className={`${TH} w-36 text-right`}>% da marca</th>
              </tr>
            </thead>
            <BlocoMarca
              key={marca.marca}
              m={marca}
              comparando={comparando}
              aberta={Boolean(abertas[marca.marca])}
              alternar={() => setAbertas((a) => ({ ...a, [marca.marca]: !a[marca.marca] }))}
              abrir={(produto) => setAberto({ marca: marca.marca, produto })}
            />
          </table>
        </TableScrollHint>
        </>
      )}

      <KpiDrilldownDialog
        open={aberto != null}
        onClose={() => setAberto(null)}
        title={aberto ? aberto.produto.produto : ""}
        largura="larga"
        focusResetKey={aberto ? `${aberto.produto.canal}:${aberto.produto.sku}` : undefined}
      >
        {aberto && dados && (
          <DetalheTop marca={aberto.marca} p={aberto.produto} inicio={dados.inicio} fim={dados.fim} comparando={comparando} />
        )}
      </KpiDrilldownDialog>
    </section>
  );
}

function Delta({ valor }: { valor: number | null }) {
  if (valor == null) return <span className="text-xs text-ink-faint">novo</span>;
  const sobe = valor >= 0;
  return (
    <span className={`inline-flex rounded-full px-1.5 py-0.5 text-xs font-semibold tabular-nums ${sobe ? "bg-ok-soft text-ok-ink" : "bg-crit-soft text-crit-ink"}`}>
      {fmtPctBr(valor)}
    </span>
  );
}

function CanalSelo({ canal }: { canal: ProdutoTop["canal"] }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-muted">
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${CHANNEL_DOT[canal]}`} />
      {CHANNEL_LABEL[canal]}
    </span>
  );
}

function KitSelo() {
  return <span className="rounded-md bg-accent-soft px-1.5 py-0.5 text-xs font-semibold text-accent-ink">Kit</span>;
}

function BlocoMarca({
  m, comparando, aberta, alternar, abrir,
}: { m: MarcaTop; comparando: boolean; aberta: boolean; alternar: () => void; abrir: (p: ProdutoTop) => void }) {
  const visiveis = aberta ? m.produtos : m.produtos.slice(0, VISIVEIS);
  const colunas = comparando ? 7 : 6;
  return (
    <tbody>
      {visiveis.map((p) => (
        <tr
          key={`${p.canal}:${p.sku}`}
          onClick={() => abrir(p)}
          className="group cursor-pointer transition-colors hover:bg-accent-soft/50"
        >
          <td className="px-2 py-2 align-top text-xs font-semibold tabular-nums text-ink-faint">{p.posicao}</td>
          <td className="px-2 py-2">
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); abrir(p); }}
              aria-haspopup="dialog"
              className="line-clamp-2 w-full rounded text-left text-sm font-medium leading-snug text-ink group-hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              title={p.produto}
            >
              {p.produto}
            </button>
            <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <CanalSelo canal={p.canal} />
              {p.kit && <KitSelo />}
              <span className="truncate font-mono text-xs text-ink-faint">{p.sku}</span>
            </span>
          </td>
          <td className="tabular px-2 py-2 text-right align-top text-sm font-semibold text-ink">{fmtBrlCurto(p.receita)}</td>
          {comparando && (
            <td className="px-2 py-2 text-right align-top"><Delta valor={variacao(p.receita, p.receita_ant)} /></td>
          )}
          <td className="tabular px-2 py-2 text-right align-top text-sm text-ink-muted">{fmtNumCurto(p.pecas)}</td>
          <td className="tabular px-2 py-2 text-right align-top text-sm text-ink-muted">
            {p.preco_medio != null ? fmtBrlCurto(p.preco_medio) : "—"}
          </td>
          <td className="px-2 py-2 align-top">
            {p.share_marca != null && (
              <span className="flex items-center justify-end gap-2">
                <span className="h-1.5 w-20 rounded-full bg-raised" aria-hidden="true">
                  <span className="block h-1.5 rounded-full bg-accent" style={{ width: `${Math.min(100, p.share_marca * 100)}%` }} />
                </span>
                <span className="tabular w-10 text-right text-sm text-ink-muted">{Math.round(p.share_marca * 100)}%</span>
              </span>
            )}
          </td>
        </tr>
      ))}
      {m.produtos.length > VISIVEIS && (
        <tr>
          <td colSpan={colunas} className="px-2 pb-2">
            <button
              type="button"
              onClick={alternar}
              aria-expanded={aberta}
              className="inline-flex min-h-11 items-center gap-1 rounded-lg px-2 text-xs font-semibold text-accent hover:bg-accent-soft/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {aberta ? "Mostrar só o top 5" : `Ver top ${m.produtos.length} de ${rotuloMarca(m.marca)}`}
              <ChevronDown aria-hidden="true" className={`h-4 w-4 transition-transform ${aberta ? "rotate-180" : ""}`} />
            </button>
          </td>
        </tr>
      )}
    </tbody>
  );
}

const GRAOS = [
  { valor: "dia", texto: "Dia" },
  { valor: "semana", texto: "Semana" },
  { valor: "mes", texto: "Mês" },
] as const;
const METRICAS = [
  { valor: "receita", texto: "Receita" },
  { valor: "pecas", texto: "Peças" },
] as const;
const TITULO_GRAO: Record<Grao, string> = { dia: "últimos 30 dias", semana: "últimas 12 semanas", mes: "últimos 12 meses" };

function Indicador({ rotulo, valor, delta }: { rotulo: string; valor: string; delta?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-line p-3">
      <span className="text-xs font-semibold uppercase tracking-wide text-ink-faint">{rotulo}</span>
      <span className="text-xl font-semibold tabular-nums text-ink">{valor}</span>
      {delta && <span className="self-start">{delta}</span>}
    </div>
  );
}

function DetalheTop({
  marca, p, inicio, fim, comparando,
}: { marca: string; p: ProdutoTop; inicio: string; fim: string; comparando: boolean }) {
  const [det, setDet] = useState<DetalheProduto | null>(null);
  const [erro, setErro] = useState(false);
  const [grao, setGrao] = useState<Grao>("dia");
  const [metrica, setMetrica] = useState<"receita" | "pecas">("receita");

  useEffect(() => {
    const ac = new AbortController();
    setDet(null);
    setErro(false);
    buscarDetalhe({ canal: p.canal, marca, sku: p.sku, dateFrom: inicio, dateTo: fim }, ac.signal).then(setDet).catch((e) => {
      if (!(e instanceof DOMException && e.name === "AbortError")) setErro(true);
    });
    return () => ac.abort();
  }, [p.canal, p.sku, marca, inicio, fim]);

  const barras = det ? agrupar(det.serie, fim, grao) : [];
  const fmtExtra = (v: number, f: "brl" | "num" | "vezes") => (f === "brl" ? fmtBrlCurto(v) : f === "vezes" ? fmtVezes(v) : fmtNumCurto(v));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-sm font-semibold text-ink">{rotuloMarca(marca)}</span>
        <CanalSelo canal={p.canal} />
        {p.kit && <KitSelo />}
        <span className="font-mono text-xs text-ink-faint">{p.sku}</span>
        <span className="text-xs text-ink-faint">#{p.posicao} da marca</span>
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Indicador rotulo="Receita" valor={fmtBrlCurto(p.receita)} delta={comparando ? <Delta valor={variacao(p.receita, p.receita_ant)} /> : undefined} />
        <Indicador rotulo="Peças" valor={fmtNumCurto(p.pecas)} delta={comparando ? <Delta valor={variacao(p.pecas, p.pecas_ant)} /> : undefined} />
        <Indicador rotulo="Preço médio" valor={p.preco_medio != null ? fmtBrlCurto(p.preco_medio) : "—"} />
        <Indicador rotulo="% da marca" valor={p.share_marca != null ? `${Math.round(p.share_marca * 100)}%` : "—"} />
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-ink">
            Venda {metrica === "receita" ? "em R$" : "em peças"} · {TITULO_GRAO[grao]}
          </h3>
          <div className="flex flex-wrap gap-2">
            <Segmented rotulo="Métrica" opcoes={METRICAS} valor={metrica} aoEscolher={setMetrica} />
            <Segmented rotulo="Agrupamento" opcoes={GRAOS} valor={grao} aoEscolher={setGrao} />
          </div>
        </div>
        <div className="h-56">
          {erro ? (
            <p className="text-sm text-ink-muted">Histórico do produto indisponível.</p>
          ) : !det ? (
            <div className="h-full w-full animate-pulse rounded-lg bg-raised motion-reduce:animate-none" />
          ) : (
            <Grafico barras={barras} metrica={metrica} canal={p.canal} />
          )}
        </div>
      </div>

      {det && (det.extras.length > 0 || det.aviso_extras) && (
        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold text-ink">
            {p.canal === "tiktok" ? "De onde veio o GMV no período" : "Ads no período"}
          </h3>
          {det.aviso_extras && (
            <p className="rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn-ink">{det.aviso_extras}</p>
          )}
          <dl className="grid grid-cols-2 gap-2 md:grid-cols-3">
            {det.extras.map((e) => (
              <div key={e.rotulo} className="flex flex-col gap-0.5 rounded-lg bg-raised px-3 py-2">
                <dt className="text-xs text-ink-faint">{e.rotulo}</dt>
                <dd className="text-sm font-semibold tabular-nums text-ink">
                  {fmtExtra(e.valor, e.formato)}
                  {e.formato === "brl" && p.canal === "tiktok" && p.receita > 0 && (
                    <span className="ml-1.5 text-xs font-medium text-ink-faint">{Math.round((e.valor / p.receita) * 100)}%</span>
                  )}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}
