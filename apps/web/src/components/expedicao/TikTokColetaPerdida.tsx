"use client";

/**
 * EXPEDICAO-TIKTOK-COLETA-PERDIDA — "quais pedidos venceram no dia X sem coleta?"
 *
 * Pedido da operacao em 05/11/2026: a transportadora nao veio em 04/11 e o
 * TikTok vai punir. Para contestar, escolhe-se o DIA DO PRAZO DE COLETA e a
 * lista traz os pedidos com etiqueta pronta ate' o fim daquele dia e sem
 * coleta ate' o fim dele — inclusive os coletados depois, que ja' sairam da
 * fila de abertos. Paginada (200 por pagina) e com CSV de TODAS as paginas.
 */
import { ChevronLeft, ChevronRight, Download } from "lucide-react";
import { useEffect, useState } from "react";

import KpiDrilldownDialog from "@/components/KpiDrilldownDialog";
import DrilldownSecao from "@/components/drilldown/DrilldownSecao";
import EvidenceRow from "@/components/drilldown/EvidenceRow";
import { buscarExpedicao } from "@/lib/expedicao-demo";
import { formatarInteiro } from "@/lib/expedicao-formato";
import {
  buscarTodasAsPaginas,
  csvDaColetaPerdida,
  diaCurto,
  faixaDaPagina,
  ROTULO_STATUS_TIKTOK,
  instanteCurto,
  queryColetaPerdida,
  rotuloStatusTikTok,
  type EscopoColeta,
  type PedidoColetaPerdida,
  type RespostaColetaPerdida,
} from "@/lib/expedicao-tiktok-abertos";
import { brandLabel } from "@/lib/filters/brands";

export function baixarCsv(conteudo: string, nome: string): void {
  let url: string | null = null;
  try {
    url = URL.createObjectURL(new Blob([conteudo], { type: "text/csv;charset=utf-8;" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = nome;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  } finally {
    if (url) URL.revokeObjectURL(url);
  }
}

/**
 * "Exportar CSV" de TODAS as paginas: busca uma por uma (cada uma cabe na
 * reserva pelo Metabase), mostra o progresso e so' baixa com a lista inteira.
 */
export function BotaoCsvCompleto({
  total,
  baixar,
  testId,
}: {
  total: number;
  baixar: (aoAvancar: (pagina: number) => void) => Promise<void>;
  testId?: string;
}) {
  const [estado, setEstado] = useState<{ tipo: "parado" } | { tipo: "baixando"; pagina: number } | { tipo: "erro" }>({
    tipo: "parado",
  });
  const paginas = Math.max(1, Math.ceil(total / 200));
  const exportar = async () => {
    setEstado({ tipo: "baixando", pagina: 1 });
    try {
      await baixar((pagina) => setEstado({ tipo: "baixando", pagina }));
      setEstado({ tipo: "parado" });
    } catch {
      setEstado({ tipo: "erro" });
    }
  };
  return (
    <button
      type="button"
      data-testid={testId}
      disabled={estado.tipo === "baixando"}
      title={`Baixa todos os ${formatarInteiro(total)} pedidos da lista (todas as páginas) em CSV`}
      onClick={() => void exportar()}
      className="-my-3 ml-2 inline-flex min-h-[44px] items-center gap-1.5 rounded-lg px-2 text-xs font-semibold text-accent-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-wait disabled:opacity-70"
    >
      <Download aria-hidden="true" className="h-3.5 w-3.5" />
      <span aria-live="polite">
        {estado.tipo === "baixando"
          ? `Exportando… ${Math.min(estado.pagina, paginas)}/${paginas}`
          : estado.tipo === "erro"
            ? "Falhou — tentar de novo"
            : `Exportar CSV (${formatarInteiro(total)})`}
      </span>
    </button>
  );
}

async function buscarPagina(
  api: string,
  vencimento: string,
  marca: string | null,
  pagina: number,
  status: string | null = null,
  escopo: EscopoColeta = "etiqueta_pronta",
  statusDia: string | null = null,
) {
  const r = await buscarExpedicao(`${api}${queryColetaPerdida(vencimento, marca, pagina, status, escopo, statusDia)}`, {
    cache: "no-store",
  });
  if (!r.ok) throw new Error(String(r.status));
  const dados = (await r.json()) as RespostaColetaPerdida;
  if (dados.availability !== "available") throw new Error("indisponivel");
  return dados;
}

type Carga = { tipo: "carregando" } | { tipo: "erro" } | { tipo: "ok"; dados: RespostaColetaPerdida };

export function PopupColetaPerdida({
  api,
  marca,
  vencimento,
  aberto,
  aoFechar,
  escopo = "etiqueta_pronta",
}: {
  api: string;
  marca: string | null;
  vencimento: string;
  aberto: boolean;
  aoFechar: () => void;
  escopo?: EscopoColeta;
}) {
  const [pagina, setPagina] = useState(1);
  const [carga, setCarga] = useState<Carga>({ tipo: "carregando" });
  const [tentativa, setTentativa] = useState(0);
  // Filtros do popup. A marca comeca na da tela; o CSV baixa SO' o filtrado.
  const [marcaSel, setMarcaSel] = useState<string | null>(marca);
  const [statusSel, setStatusSel] = useState<string | null>(null);
  // Status NO DIA do prazo (reconstruido): como o pedido estava naquele dia.
  const [statusDiaSel, setStatusDiaSel] = useState<string | null>(null);
  // Opcoes dos filtros: ficam da ultima resposta enquanto a proxima carrega.
  const [opcoes, setOpcoes] = useState<{
    marcas: string[];
    status: Record<string, number>;
    statusDia: Record<string, number>;
  }>({ marcas: [], status: {}, statusDia: {} });

  useEffect(() => setMarcaSel(marca), [marca]);
  // Outro dia: os status daquele dia nao valem para o novo.
  useEffect(() => setStatusDiaSel(null), [vencimento]);
  useEffect(() => setPagina(1), [vencimento, marcaSel, statusSel, statusDiaSel]);
  useEffect(() => {
    if (!aberto) return;
    let vivo = true;
    setCarga({ tipo: "carregando" });
    buscarPagina(api, vencimento, marcaSel, pagina, statusSel, escopo, statusDiaSel)
      .then((dados) => {
        if (!vivo) return;
        setCarga({ tipo: "ok", dados });
        setOpcoes({
          marcas: dados.all_brands ?? [],
          status: dados.status_counts ?? {},
          statusDia: dados.status_on_day_counts ?? {},
        });
      })
      .catch(() => vivo && setCarga({ tipo: "erro" }));
    return () => {
      vivo = false;
    };
  }, [api, vencimento, marcaSel, statusSel, statusDiaSel, pagina, aberto, tentativa, escopo]);

  const dados = carga.tipo === "ok" ? carga.dados : null;
  const pedidos: PedidoColetaPerdida[] = dados?.orders ?? [];
  const total = dados?.list_total ?? 0;
  const variasMarcas = !marcaSel && (dados?.brands.length ?? 0) > 1;
  const totalStatus = Object.values(opcoes.status).reduce((a, b) => a + b, 0);
  const totalStatusDia = Object.values(opcoes.statusDia).reduce((a, b) => a + b, 0);
  const filtrado = marcaSel !== marca || Boolean(statusSel) || Boolean(statusDiaSel);
  const dia = diaCurto(vencimento);
  const filtros = (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2" data-testid="exp-tiktok-coleta-perdida-filtros">
      <label className="flex items-center gap-2 text-xs text-ink-muted">
        Marca
        <select
          value={marcaSel ?? ""}
          onChange={(e) => setMarcaSel(e.target.value || null)}
          className="min-h-9 rounded-md border border-line bg-raised px-2 text-sm text-ink"
        >
          <option value="">Todas</option>
          {opcoes.marcas.map((m) => (
            <option key={m} value={m}>
              {brandLabel(m)}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2 text-xs text-ink-muted">
        Status em {dia}
        <select
          value={statusDiaSel ?? ""}
          onChange={(e) => setStatusDiaSel(e.target.value || null)}
          className="min-h-9 rounded-md border border-line bg-raised px-2 text-sm text-ink"
        >
          <option value="">Todos ({formatarInteiro(totalStatusDia)})</option>
          {Object.keys(ROTULO_STATUS_TIKTOK)
            .filter((st) => (opcoes.statusDia[st] ?? 0) > 0 || st === statusDiaSel)
            .map((st) => (
              <option key={st} value={st}>
                {rotuloStatusTikTok(st)} ({formatarInteiro(opcoes.statusDia[st] ?? 0)})
              </option>
            ))}
        </select>
      </label>
      <label className="flex items-center gap-2 text-xs text-ink-muted">
        Status hoje
        <select
          value={statusSel ?? ""}
          onChange={(e) => setStatusSel(e.target.value || null)}
          className="min-h-9 rounded-md border border-line bg-raised px-2 text-sm text-ink"
        >
          <option value="">Todos ({formatarInteiro(totalStatus)})</option>
          {Object.keys(ROTULO_STATUS_TIKTOK)
            .filter((st) => (opcoes.status[st] ?? 0) > 0 || st === statusSel)
            .map((st) => (
              <option key={st} value={st}>
                {rotuloStatusTikTok(st)} ({formatarInteiro(opcoes.status[st] ?? 0)})
              </option>
            ))}
        </select>
      </label>
      {filtrado ? (
        <button
          type="button"
          onClick={() => {
            setMarcaSel(marca);
            setStatusSel(null);
            setStatusDiaSel(null);
          }}
          className="min-h-9 text-xs font-semibold text-accent-ink underline-offset-4 hover:underline"
        >
          Limpar filtros
        </button>
      ) : null}
    </div>
  );
  const titulo =
    escopo === "todos"
      ? `Vencidos sem coleta — prazo de coleta ${diaCurto(vencimento)} (TikTok)`
      : `Não coletados no prazo — coleta vencida ${diaCurto(vencimento)} (TikTok)`;

  return (
    <KpiDrilldownDialog open={aberto} onClose={aoFechar} title={titulo} largura="larga" focusResetKey={vencimento}>
      <div className="flex flex-col gap-4 text-sm" data-testid="exp-tiktok-coleta-perdida-popup">
        {opcoes.marcas.length || dados ? filtros : null}
        {carga.tipo === "carregando" && !dados ? (
          <p role="status" className="text-ink-muted">Carregando pedidos…</p>
        ) : carga.tipo === "erro" ? (
          <p role="status" className="text-crit-ink">
            Não foi possível carregar os pedidos.{" "}
            <button type="button" onClick={() => setTentativa((n) => n + 1)} className="font-semibold underline underline-offset-4">
              Tentar de novo
            </button>
          </p>
        ) : dados && total === 0 ? (
          <p role="status" className="text-ink-muted">
            {filtrado
              ? "Nenhum pedido com esses filtros."
              : `Nenhum pedido ficou sem coleta no prazo de ${diaCurto(vencimento)}.`}
          </p>
        ) : dados ? (
          <>
            <div className="flex flex-wrap items-end gap-x-3 gap-y-1.5">
              <span className="text-[32px] font-bold leading-none tracking-tight tabular-nums text-crit-ink">
                {formatarInteiro(total)}
              </span>
              <span className="pb-0.5 text-xs text-ink-faint">
                {formatarInteiro(total - dados.still_waiting)} coletados depois · {formatarInteiro(dados.still_waiting)} ainda
                sem coleta · {marcaSel ? brandLabel(marcaSel) : "todas as marcas"}
                {statusDiaSel ? ` · em ${dia}: ${rotuloStatusTikTok(statusDiaSel)}` : ""}
                {statusSel ? ` · hoje: ${rotuloStatusTikTok(statusSel)}` : ""}
              </span>
            </div>
            {variasMarcas ? (
              <DrilldownSecao titulo="Por marca">
                <ul className="flex flex-col">
                  {dados.brands.map((b) => (
                    <EvidenceRow
                      key={b.brand}
                      label={brandLabel(b.brand)}
                      pct={(b.missed / total) * 100}
                      barra="bg-crit"
                      value={`${formatarInteiro(b.missed)} · ${formatarInteiro(b.still_waiting)} ainda sem coleta`}
                    />
                  ))}
                </ul>
              </DrilldownSecao>
            ) : null}
            <DrilldownSecao
              titulo="Pedidos"
              acao={
                <>
                  <span className="tabular-nums">{faixaDaPagina(pagina, dados.page_size, pedidos.length, total)}</span>
                  <BotaoCsvCompleto
                    total={total}
                    testId="exp-tiktok-coleta-perdida-csv"
                    baixar={async (aoAvancar) => {
                      const todos = await buscarTodasAsPaginas(
                        (p) => buscarPagina(api, vencimento, marcaSel, p, statusSel, escopo, statusDiaSel),
                        aoAvancar,
                      );
                      baixarCsv(
                        csvDaColetaPerdida(todos, vencimento),
                        `tiktok-${escopo === "todos" ? "vencidos-sem-coleta" : "nao-coletados-prazo"}-${vencimento}${marcaSel ? `-${marcaSel}` : ""}${statusDiaSel ? `-no-dia-${statusDiaSel.toLowerCase()}` : ""}${statusSel ? `-hoje-${statusSel.toLowerCase()}` : ""}.csv`,
                      );
                    }}
                  />
                </>
              }
            >
              <div className="max-h-[50vh] overflow-auto rounded-lg border border-line" aria-busy={carga.tipo === "carregando"}>
                <table className="w-full text-sm [&_td]:px-3 [&_td]:py-1.5 [&_th]:whitespace-nowrap [&_th]:px-3 [&_th]:py-1.5">
                  <caption className="sr-only">{titulo}</caption>
                  <thead className="sticky top-0 bg-raised text-left text-xs uppercase tracking-wide text-ink-faint">
                    <tr>
                      <th scope="col">Pedido</th>
                      {variasMarcas ? <th scope="col">Marca</th> : null}
                      <th scope="col">Em {dia}</th>
                      <th scope="col">Hoje</th>
                      <th scope="col">Pago em</th>
                      <th scope="col">Etiqueta pronta</th>
                      <th scope="col">Coleta</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pedidos.map((p) => (
                      <tr key={p.order_id} className="border-t border-line">
                        <td className="whitespace-nowrap font-mono text-xs text-ink">{p.order_id}</td>
                        {variasMarcas ? <td className="whitespace-nowrap text-ink">{brandLabel(p.brand)}</td> : null}
                        <td className="whitespace-nowrap font-medium text-ink">{rotuloStatusTikTok(p.status_on_day)}</td>
                        <td className="whitespace-nowrap text-ink-muted">{rotuloStatusTikTok(p.status)}</td>
                        <td className="whitespace-nowrap tabular-nums text-ink-muted">{instanteCurto(p.paid_at)}</td>
                        <td className="whitespace-nowrap tabular-nums text-ink-muted">{instanteCurto(p.ready_at)}</td>
                        <td className={`whitespace-nowrap tabular-nums ${p.collected_at ? "text-warn-ink" : "font-semibold text-crit-ink"}`}>
                          {p.collected_at ? `coletado ${instanteCurto(p.collected_at)}` : "ainda sem coleta"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {dados.has_more || pagina > 1 ? (
                <nav aria-label="Páginas da lista" className="mt-2 flex items-center justify-between gap-3">
                  <button
                    type="button"
                    onClick={() => setPagina((n) => Math.max(1, n - 1))}
                    disabled={pagina <= 1 || carga.tipo === "carregando"}
                    className="inline-flex min-h-11 items-center gap-1 rounded-md border border-line bg-surface px-3 text-sm font-semibold text-ink hover:border-line-strong disabled:cursor-default disabled:opacity-40"
                  >
                    <ChevronLeft aria-hidden="true" className="h-4 w-4" />
                    Anterior
                  </button>
                  <span className="text-xs tabular-nums text-ink-muted" aria-live="polite">
                    Página {formatarInteiro(pagina)} de {formatarInteiro(Math.max(pagina, Math.ceil(total / dados.page_size)))}
                  </span>
                  <button
                    type="button"
                    onClick={() => setPagina((n) => n + 1)}
                    disabled={!dados.has_more || carga.tipo === "carregando"}
                    className="inline-flex min-h-11 items-center gap-1 rounded-md border border-line bg-surface px-3 text-sm font-semibold text-ink hover:border-line-strong disabled:cursor-default disabled:opacity-40"
                  >
                    Próxima
                    <ChevronRight aria-hidden="true" className="h-4 w-4" />
                  </button>
                </nav>
              ) : null}
            </DrilldownSecao>
          </>
        ) : null}
        <p className="text-xs text-ink-faint">
          Pedidos com prazo de coleta em {diaCurto(vencimento)} (pagos de {diaCurto(dados?.paid_from)} a{" "}
          {diaCurto(dados?.paid_to)}),{" "}
          {escopo === "todos"
            ? "sem coleta (IN_TRANSIT) até o fim desse dia, com ou sem etiqueta pronta — a mesma conta da LDR."
            : "etiqueta pronta até o fim desse dia e sem coleta (IN_TRANSIT) até o fim dele."}{" "}
          &quot;Em {diaCurto(vencimento)}&quot; é como o pedido estava no fim desse dia: aguardando coleta se a etiqueta
          já estava pronta, senão aguardando despacho (ou retido pelo TikTok).
          Prazo reconstruído pela política do TikTok (coleta até 2 dias úteis após o pagamento).
        </p>
      </div>
    </KpiDrilldownDialog>
  );
}

/** Faixa com o seletor do dia de prazo e o atalho para a lista. */
export default function TikTokColetaPerdida({ api, marca }: { api: string; marca: string | null }) {
  const [vencimento, setVencimento] = useState<string | null>(null);
  const [carga, setCarga] = useState<Carga>({ tipo: "carregando" });
  const [aberto, setAberto] = useState(false);

  useEffect(() => {
    let vivo = true;
    setCarga({ tipo: "carregando" });
    buscarExpedicao(`${api}${queryColetaPerdida(vencimento, marca)}`, { cache: "no-store" })
      .then(async (r) => {
        if (!r.ok) throw new Error(String(r.status));
        const dados = (await r.json()) as RespostaColetaPerdida;
        if (!vivo) return;
        if (dados.availability !== "available") throw new Error("indisponivel");
        setCarga({ tipo: "ok", dados });
        if (!vencimento) setVencimento(dados.deadline);
      })
      .catch(() => vivo && setCarga({ tipo: "erro" }));
    return () => {
      vivo = false;
    };
  }, [api, marca, vencimento]);

  const dados = carga.tipo === "ok" ? carga.dados : null;
  const dias = dados?.deadline_days ?? [];
  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-line bg-surface px-4 py-3"
      data-testid="exp-tiktok-coleta-perdida"
    >
      <p className="text-sm font-semibold text-ink">Não coletados no prazo</p>
      <label className="flex items-center gap-2 text-xs text-ink-muted">
        Prazo de coleta
        <select
          value={vencimento ?? ""}
          onChange={(e) => setVencimento(e.target.value)}
          disabled={!dias.length}
          className="min-h-9 rounded-md border border-line bg-raised px-2 text-sm text-ink"
        >
          {dias.map((d) => (
            <option key={d} value={d}>
              {diaCurto(d)}
            </option>
          ))}
        </select>
      </label>
      <span className="min-w-0 flex-1 text-sm text-ink-muted" aria-live="polite">
        {carga.tipo === "carregando"
          ? "Contando…"
          : carga.tipo === "erro"
            ? "Não foi possível contar agora."
            : dados
              ? dados.list_total > 0
                ? `${formatarInteiro(dados.list_total)} pedidos com etiqueta pronta e sem coleta até o fim do dia · ${formatarInteiro(dados.still_waiting)} ainda sem coleta`
                : "Nenhum pedido ficou sem coleta nesse prazo."
              : null}
      </span>
      <button
        type="button"
        onClick={() => setAberto(true)}
        disabled={!dados || dados.list_total === 0}
        className="inline-flex min-h-11 items-center rounded-md border border-line bg-raised px-3 text-sm font-semibold text-ink hover:border-line-strong disabled:cursor-default disabled:opacity-40"
      >
        Ver pedidos
      </button>
      {vencimento ? (
        <PopupColetaPerdida api={api} marca={marca} vencimento={vencimento} aberto={aberto} aoFechar={() => setAberto(false)} />
      ) : null}
    </div>
  );
}
