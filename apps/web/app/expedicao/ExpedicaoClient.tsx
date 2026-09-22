"use client";

/**
 * Gate EXP-2B — tela operacional da Expedicao Shopee.
 *
 * Consome SOMENTE `/api/v1/expedicao` e `/api/v1/expedicao/trend`. Nao calcula
 * estado operacional: todas as classificacoes ja vem do pipeline, e refaze-las
 * aqui faria dois consumidores verem coisas diferentes da mesma fotografia.
 *
 * A logica pura vive em `@/lib/expedicao-contract`; este arquivo so' pinta.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  AVISO_SEM_AUTOMACAO,
  CANAIS,
  EXPLICACAO_LIMIAR_48H,
  FILTROS_PADRAO,
  JANELA_PADRAO_HORAS,
  ORDENACOES,
  ROTULO_CANAL,
  SITUACOES,
  aplicarCanal,
  aplicarFiltro,
  avisosDeCobertura,
  chaveDaRequisicao,
  construirQuery,
  estadoDaTela,
  estadoDaTendencia,
  formatarHoras,
  montarFrescor,
  montarKpis,
  montarRelogios,
  montarSeries,
  mostrarColunaReferencia,
  paginaAtual,
  queryDaTendencia,
  sanitizarCanal,
  sanitizarJanela,
  type Canal,
  type Filtros,
  type Ordenacao,
  type RespostaExpedicao,
  type RespostaTendencia,
  type Situacao,
} from "@/lib/expedicao-contract";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

/**
 * Flag do Mercado Livre.
 *
 * A comparacao esta ESCRITA AQUI, e nao atras de `expedicaoMlHabilitado(...)`,
 * por um motivo medido: o Next substitui `process.env.NEXT_PUBLIC_*` por um
 * literal em tempo de build, entao `"" === "true"` dobra para `false` e o
 * minificador ELIMINA o seletor do bundle. Com a chamada de funcao o
 * compilador nao consegue dobrar, e a marcacao viajava para o navegador mesmo
 * desligada — inerte, mas enviada.
 *
 * A regra continua sendo uma so': `expedicaoMlHabilitado` e' identica a esta
 * comparacao, e ha teste amarrando as duas.
 */
const ML_LIGADO = process.env.NEXT_PUBLIC_EXPEDICAO_ML_ENABLED === "true";

const ROTULO_SITUACAO: Record<Situacao, string> = {
  overdue: "Vencidos",
  due_within_24h: "Vence em 24h",
  on_time: "No prazo",
  deadline_unavailable: "Sem prazo",
  over_48h: "Acima de 48h",
  stalled: "Travados",
  slow: "Lentos",
  zombie: "Zumbis",
};

const ROTULO_ORDEM: Record<Ordenacao, string> = {
  criticidade: "Criticidade",
  deadline: "Prazo",
  oldest: "Mais antigos",
};

function Cartao({ titulo, children }: { titulo: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="mb-3 text-sm font-semibold text-slate-700">{titulo}</h3>
      {children}
    </section>
  );
}

function dataCurta(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

export default function ExpedicaoClient() {
  const [filtros, setFiltros] = useState<Filtros>(FILTROS_PADRAO);
  const [dados, setDados] = useState<RespostaExpedicao | null>(null);
  const [tendencia, setTendencia] = useState<RespostaTendencia | null>(null);
  const [janela, setJanela] = useState(JANELA_PADRAO_HORAS);
  const [carregando, setCarregando] = useState(true);
  const [carregandoTendencia, setCarregandoTendencia] = useState(true);
  const [erroHttp, setErroHttp] = useState<number | null>(null);
  const [erroTendencia, setErroTendencia] = useState(false);

  // Chave da requisicao resolvida. Resposta que chega com chave diferente da
  // atual e' DESCARTADA: sem isso, trocar filtro rapidamente deixaria a
  // resposta antiga (mais lenta) sobrescrever a nova.
  const chave = useMemo(() => chaveDaRequisicao(filtros), [filtros]);
  const chaveVigente = useRef(chave);
  chaveVigente.current = chave;

  /**
   * A URL so' pode ser lida DEPOIS da montagem — ler `window.location` durante
   * o render faria o HTML do servidor divergir do cliente.
   *
   * Isso cria um problema proprio: sem esperar por essa leitura, abrir
   * `?channel=mercadolivre` disparava PRIMEIRO uma requisicao de Shopee e
   * pintava um quadro escrito "Expedição — Shopee" antes de virar ML. Uma
   * requisicao desperdicada e, pior, um instante em que o operador le' numeros
   * de uma loja sob o titulo de outra.
   *
   * `urlPronta` segura as duas buscas ate' a URL ser resolvida. Com a flag
   * desligada ela ja' nasce `true`: nao ha URL a consultar, e o comportamento
   * fica identico ao de antes deste gate.
   */
  const [urlPronta, setUrlPronta] = useState(!ML_LIGADO);

  const lerCanalDaUrl = useCallback(() => {
    const daUrl = new URLSearchParams(window.location.search).get("channel");
    return sanitizarCanal(daUrl, ML_LIGADO);
  }, []);

  useEffect(() => {
    if (!ML_LIGADO) return;
    const canal = lerCanalDaUrl();
    setFiltros((f) => (f.channel === canal ? f : aplicarCanal(f, canal)));
    setUrlPronta(true);
  }, [lerCanalDaUrl]);

  // Voltar/avancar do navegador pode trocar a URL sem remontar o componente.
  // Sem isto, o endereco diria um canal e a tela mostraria outro.
  useEffect(() => {
    if (!ML_LIGADO) return;
    const aoVoltar = () => {
      const canal = lerCanalDaUrl();
      setFiltros((f) => (f.channel === canal ? f : aplicarCanal(f, canal)));
    };
    window.addEventListener("popstate", aoVoltar);
    return () => window.removeEventListener("popstate", aoVoltar);
  }, [lerCanalDaUrl]);

  /** Troca de canal: estado, URL e pagina, sempre juntos. */
  const trocarCanal = useCallback((canal: Canal) => {
    setFiltros((f) => (f.channel === canal ? f : aplicarCanal(f, canal)));
    const url = new URL(window.location.href);
    if (canal === FILTROS_PADRAO.channel) url.searchParams.delete("channel");
    else url.searchParams.set("channel", canal);
    window.history.replaceState(null, "", url.toString());
  }, []);

  useEffect(() => {
    if (!urlPronta) return;
    const minhaChave = chave;
    let vivo = true;
    setCarregando(true);
    setErroHttp(null);
    fetch(`${API}/api/v1/expedicao?${construirQuery(filtros)}`, {
      headers: { Accept: "application/json" },
    })
      .then(async (r) => {
        if (!r.ok) throw Object.assign(new Error("http"), { status: r.status });
        return (await r.json()) as RespostaExpedicao;
      })
      .then((json) => {
        if (!vivo || chaveVigente.current !== minhaChave) return;
        setDados(json);
        setCarregando(false);
      })
      .catch((e: { status?: number }) => {
        if (!vivo || chaveVigente.current !== minhaChave) return;
        setErroHttp(typeof e?.status === "number" ? e.status : 0);
        setCarregando(false);
      });
    return () => {
      vivo = false;
    };
  }, [chave, filtros, urlPronta]);

  // A tendencia carrega a MESMA chave de canal da fila, e a resposta que chega
  // com chave antiga e' descartada: sem isso, trocar de canal podia deixar o
  // grafico de um marketplace embaixo da fila do outro.
  const chaveTendencia = useMemo(
    () => queryDaTendencia(janela, filtros.channel),
    [janela, filtros.channel],
  );
  const chaveTendVigente = useRef(chaveTendencia);
  chaveTendVigente.current = chaveTendencia;

  useEffect(() => {
    if (!urlPronta) return;
    const minhaChave = chaveTendencia;
    let vivo = true;
    setCarregandoTendencia(true);
    setErroTendencia(false);
    fetch(`${API}/api/v1/expedicao/trend?${chaveTendencia}`, {
      headers: { Accept: "application/json" },
    })
      .then(async (r) => {
        if (!r.ok) throw new Error("http");
        return (await r.json()) as RespostaTendencia;
      })
      .then((json) => {
        if (!vivo || chaveTendVigente.current !== minhaChave) return;
        setTendencia(json);
        setCarregandoTendencia(false);
      })
      .catch(() => {
        if (!vivo || chaveTendVigente.current !== minhaChave) return;
        setErroTendencia(true);
        setCarregandoTendencia(false);
      });
    return () => {
      vivo = false;
    };
  }, [chaveTendencia, urlPronta]);

  const temFiltro =
    filtros.brands.length > 0 || filtros.accounts.length > 0 || filtros.situacao.length > 0;
  const estado = estadoDaTela({ carregando, erroHttp, resposta: dados, temFiltro });
  const relogios = dados ? montarRelogios(dados) : null;
  const kpis = montarKpis(dados?.totals ?? null, dados?.freshness ?? [], filtros.channel);
  const frescor = montarFrescor(dados?.freshness ?? []);
  const avisos = avisosDeCobertura(dados?.coverage ?? null);
  const series = montarSeries(tendencia?.points ?? []);
  const estadoTend = estadoDaTendencia(
    carregandoTendencia,
    erroTendencia,
    series,
    dados?.coverage?.expected_accounts.length ?? 0,
  );
  const mostrarRef = mostrarColunaReferencia(dados?.queue ?? []);
  const pag = paginaAtual(dados?.pagination ?? null);

  const alternarSituacao = useCallback((s: Situacao) => {
    setFiltros((f) =>
      aplicarFiltro(f, {
        situacao: f.situacao.includes(s)
          ? f.situacao.filter((x) => x !== s)
          : [...f.situacao, s],
      }),
    );
  }, []);

  // `div`, nao `main`: o landmark `main` e do AppShell (um por pagina). Dois
  // `main` quebram a navegacao por landmarks no leitor de tela — mesmo
  // motivo do `h2` no cabecalho abaixo.
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4 p-4" aria-busy={carregando}>
      <header className="flex flex-col gap-2">
        {/* h2, nao h1: o `h1` da pagina e do shell (Topbar). Dois h1 quebram
            a arvore de cabecalhos para leitor de tela. */}
        <h2 className="text-xl font-semibold text-slate-900">
          Expedição — {ROTULO_CANAL[filtros.channel]}
        </h2>
        {ML_LIGADO && (
          <div
            role="radiogroup"
            aria-label="Canal da fotografia"
            className="flex flex-wrap gap-2"
          >
            {CANAIS.map((c) => {
              const ativo = filtros.channel === c;
              return (
                <button
                  key={c}
                  type="button"
                  role="radio"
                  aria-checked={ativo}
                  onClick={() => trocarCanal(c)}
                  className={`min-h-[44px] min-w-[44px] rounded border px-4 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 ${
                    ativo
                      ? "border-sky-600 bg-sky-50 font-semibold text-sky-900"
                      : "border-slate-300 bg-white text-slate-700"
                  }`}
                >
                  {ROTULO_CANAL[c]}
                </button>
              );
            })}
          </div>
        )}
        <p className="text-sm text-slate-600">
          Fotografia de {dataCurta(relogios?.efetivoEm ?? null)} ·{" "}
          {relogios?.snapshotAgeHours !== null && relogios?.snapshotAgeHours !== undefined
            ? `publicada há ${formatarHoras(relogios.snapshotAgeHours)}`
            : "idade da fotografia indisponível"}{" "}
          · carga <code className="text-xs">{relogios?.modoDeCarga ?? "manual_snapshot"}</code>
        </p>
        {relogios?.semAutomacao !== false && (
          <p
            role="status"
            className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
          >
            {AVISO_SEM_AUTOMACAO}
          </p>
        )}
        {relogios?.snapshotVelho && (
          <p
            role="status"
            className="rounded border border-orange-300 bg-orange-50 px-3 py-2 text-sm text-orange-900"
          >
            Fotografia antiga: publicada há {formatarHoras(relogios.snapshotAgeHours)}. Os
            números descrevem aquele instante, não agora.
          </p>
        )}
        {avisos.map((a) => (
          <p
            key={a.texto}
            role="status"
            className="rounded border border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-700"
          >
            {a.texto}
          </p>
        ))}
      </header>

      {estado === "carregando" && (
        <p role="status" className="text-sm text-slate-600">
          Carregando fotografia…
        </p>
      )}
      {estado === "canal_desligado" && (
        <p role="status" className="rounded border border-slate-300 bg-slate-50 px-3 py-6 text-sm text-slate-700">
          Este canal ainda não está disponível na API. A fotografia existe, mas a
          exposição depende de uma ativação coordenada.
        </p>
      )}

      {estado === "indisponivel_backend" && (
        <p role="alert" className="rounded border border-slate-300 bg-slate-50 p-4 text-sm">
          A API de Expedição está desligada. Nada a exibir.
        </p>
      )}
      {estado === "sem_snapshot" && (
        <p role="alert" className="rounded border border-slate-300 bg-slate-50 p-4 text-sm">
          Nenhuma fotografia publicada ainda.
        </p>
      )}
      {estado === "batch_inconsistente" && (
        <p role="alert" className="rounded border border-red-300 bg-red-50 p-4 text-sm">
          A fotografia está inconsistente e não pode ser exibida. Nenhum número
          parcial é mostrado de propósito.
        </p>
      )}
      {estado === "erro_validacao" && (
        <p role="alert" className="rounded border border-red-300 bg-red-50 p-4 text-sm">
          Filtro inválido. Ajuste a seleção e tente novamente.
        </p>
      )}
      {estado === "erro" && (
        <p role="alert" className="rounded border border-red-300 bg-red-50 p-4 text-sm">
          Não foi possível carregar a Expedição agora.
        </p>
      )}
      {estado === "fotografia_vazia" && (
        <p role="status" className="rounded border border-emerald-300 bg-emerald-50 p-4 text-sm">
          Backlog zerado nesta fotografia. Nenhum pedido aguardando expedição.
        </p>
      )}

      {(estado === "ok" || estado === "fila_vazia_por_filtro") && (
        <>
          <section aria-label="Indicadores" className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {kpis.map((k) => (
              <div
                key={k.chave}
                className={`rounded-lg border p-3 ${
                  k.alerta ? "border-amber-400 bg-amber-50" : "border-slate-200 bg-white"
                }`}
              >
                <p className="text-xs text-slate-600">{k.rotulo}</p>
                <p className="text-2xl font-semibold text-slate-900">
                  {k.valorTexto ?? (k.valor === null ? "—" : k.valor.toLocaleString("pt-BR"))}
                </p>
                {k.nota && <p className="mt-1 text-[12px] leading-snug text-slate-500">{k.nota}</p>}
              </div>
            ))}
          </section>

          <Cartao titulo="Fonte e cobertura por marca">
            <p className="mb-2 text-[12px] text-slate-500">
              Idade da fonte é quando o Data Mart foi lido. Idade do pedido mais antigo é
              contexto: um backlog legítimo sempre tem pedido velho, e isso não significa
              fonte desatualizada.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Frescor da fonte por marca</caption>
                <thead>
                  <tr className="text-left text-xs text-slate-500">
                    <th scope="col" className="py-2">Marca</th>
                    <th scope="col">Estado da fonte</th>
                    <th scope="col">Idade da fonte</th>
                    <th scope="col">Pedido mais antigo</th>
                    <th scope="col">Última leitura</th>
                  </tr>
                </thead>
                <tbody>
                  {frescor.map((f) => (
                    <tr key={f.brand} className="border-t border-slate-100">
                      <th scope="row" className="py-2 text-left font-normal">{f.brand}</th>
                      <td className={f.alerta ? "text-amber-800" : "text-slate-700"}>
                        {f.rotulo}
                        {f.derivado && (
                          <span className="ml-1 text-[12px] text-slate-500">(derivado do lote)</span>
                        )}
                      </td>
                      <td>{formatarHoras(f.sourceAgeHours)}</td>
                      <td className="text-slate-500">{formatarHoras(f.oldestRowAgeHours)}</td>
                      <td className="text-slate-500">{dataCurta(f.watermark)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Cartao>

          <Cartao titulo="Backlog por conta">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Backlog por conta</caption>
                <thead>
                  <tr className="text-left text-xs text-slate-500">
                    <th scope="col" className="py-2">Conta</th>
                    <th scope="col">Marca</th>
                    <th scope="col">Backlog</th>
                    <th scope="col">Vencidos</th>
                    <th scope="col">Acima de 48h</th>
                    <th scope="col">Travados</th>
                    <th scope="col">Fonte avançou</th>
                  </tr>
                </thead>
                <tbody>
                  {(dados?.accounts ?? []).map((a) => (
                    <tr key={a.shop_account} className="border-t border-slate-100">
                      <th scope="row" className="py-2 text-left font-normal">{a.shop_account}</th>
                      <td>{a.brand}</td>
                      <td>{a.backlog_count.toLocaleString("pt-BR")}</td>
                      <td>{a.overdue_count.toLocaleString("pt-BR")}</td>
                      <td>{a.over_48h_count.toLocaleString("pt-BR")}</td>
                      <td>{a.stalled_count.toLocaleString("pt-BR")}</td>
                      <td className="text-slate-500">
                        {a.source_advanced ? "sim" : "não"}
                        <span className="ml-1 text-[12px]">(metadado)</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Cartao>

          <Cartao titulo="Evolução horária por conta">
            <label className="mb-2 flex items-center gap-2 text-sm">
              <span>Janela (horas)</span>
              <input
                type="number"
                min={1}
                max={336}
                value={janela}
                onChange={(e) => setJanela(sanitizarJanela(Number(e.target.value)))}
                className="min-h-[44px] w-24 rounded border border-slate-300 px-2 text-sm"
              />
            </label>
            {estadoTend === "carregando" && (
              <p role="status" className="text-sm text-slate-600">Carregando tendência…</p>
            )}
            {estadoTend === "erro" && (
              <p role="alert" className="text-sm text-red-700">Tendência indisponível agora.</p>
            )}
            {estadoTend === "vazia" && (
              <p role="status" className="text-sm text-slate-600">
                Sem pontos na janela escolhida.
              </p>
            )}
            {estadoTend === "parcial" && (
              <p role="status" className="mb-2 text-sm text-amber-800">
                Série parcial: alguma hora tem menos contas que o esperado. A queda é
                ausência de dado, não redução de backlog.
              </p>
            )}
            {(estadoTend === "ok" || estadoTend === "parcial") && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <caption className="sr-only">
                    Backlog por conta e hora — contas nunca são somadas
                  </caption>
                  <thead>
                    <tr className="text-left text-xs text-slate-500">
                      <th scope="col" className="py-2">Conta</th>
                      <th scope="col">Hora</th>
                      <th scope="col">Backlog</th>
                      <th scope="col">Acima de 48h</th>
                      <th scope="col">Travados</th>
                    </tr>
                  </thead>
                  <tbody>
                    {series.flatMap((s) =>
                      s.pontos.map((p) => (
                        <tr key={`${s.shopAccount}-${p.hora}`} className="border-t border-slate-100">
                          <th scope="row" className="py-2 text-left font-normal">
                            {s.shopAccount}
                          </th>
                          <td>{dataCurta(p.hora)}</td>
                          <td>{p.backlog.toLocaleString("pt-BR")}</td>
                          <td>{p.over48h.toLocaleString("pt-BR")}</td>
                          <td>{p.stalled.toLocaleString("pt-BR")}</td>
                        </tr>
                      )),
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </Cartao>

          <Cartao titulo="Fila operacional">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              {SITUACOES.map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={filtros.situacao.includes(s)}
                  onClick={() => alternarSituacao(s)}
                  className={`min-h-[44px] min-w-[44px] rounded border px-3 text-sm ${
                    filtros.situacao.includes(s)
                      ? "border-slate-800 bg-slate-800 text-white"
                      : "border-slate-300 bg-white text-slate-700"
                  }`}
                >
                  {ROTULO_SITUACAO[s]}
                </button>
              ))}
              <label className="flex items-center gap-2 text-sm">
                <span>Ordenar</span>
                <select
                  value={filtros.orderBy}
                  onChange={(e) =>
                    setFiltros((f) => aplicarFiltro(f, { orderBy: e.target.value as Ordenacao }))
                  }
                  className="min-h-[44px] rounded border border-slate-300 px-2 text-sm"
                >
                  {ORDENACOES.map((o) => (
                    <option key={o} value={o}>{ROTULO_ORDEM[o]}</option>
                  ))}
                </select>
              </label>
            </div>

            <p className="mb-2 text-[12px] text-slate-500">{EXPLICACAO_LIMIAR_48H}</p>

            {estado === "fila_vazia_por_filtro" ? (
              <p role="status" className="text-sm text-slate-600">
                Nenhum pedido com os filtros atuais. O backlog total continua{" "}
                {(dados?.totals?.backlog_count ?? 0).toLocaleString("pt-BR")}.
              </p>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <caption className="sr-only">Pedidos aguardando expedição</caption>
                    <thead>
                      <tr className="text-left text-xs text-slate-500">
                        {mostrarRef && <th scope="col" className="py-2">Referência</th>}
                        <th scope="col" className="py-2">Conta</th>
                        <th scope="col">Marca</th>
                        <th scope="col">Prazo</th>
                        <th scope="col">Situação</th>
                        <th scope="col">Aberto há</th>
                        <th scope="col">Idade operacional</th>
                        <th scope="col">Transportadora</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(dados?.queue ?? []).map((l, i) => (
                        <tr
                          key={`${l.shop_account}-${l.order_ref ?? i}`}
                          className="border-t border-slate-100"
                        >
                          {mostrarRef && (
                            <td className="py-2 font-mono text-[12px] text-slate-500">
                              {l.order_ref ?? "—"}
                            </td>
                          )}
                          <th scope="row" className="py-2 text-left font-normal">
                            {l.shop_account}
                          </th>
                          <td>{l.brand}</td>
                          <td>{dataCurta(l.dispatch_deadline)}</td>
                          <td>{l.deadline_status}</td>
                          <td>{formatarHoras(l.hours_open)}</td>
                          <td>{l.operational_age_status}</td>
                          <td className="text-slate-500">{l.carrier ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mt-3 flex items-center gap-3 text-sm">
                  <button
                    type="button"
                    disabled={filtros.offset === 0}
                    onClick={() =>
                      setFiltros((f) =>
                        aplicarFiltro(f, { offset: Math.max(0, f.offset - f.limit) }),
                      )
                    }
                    className="min-h-[44px] min-w-[44px] rounded border border-slate-300 px-3 disabled:opacity-40"
                  >
                    Anterior
                  </button>
                  <span>
                    Página {pag.pagina} de {pag.total} ·{" "}
                    {(dados?.pagination?.total ?? 0).toLocaleString("pt-BR")} pedidos
                  </span>
                  <button
                    type="button"
                    disabled={!dados?.pagination?.has_more}
                    onClick={() =>
                      setFiltros((f) => aplicarFiltro(f, { offset: f.offset + f.limit }))
                    }
                    className="min-h-[44px] min-w-[44px] rounded border border-slate-300 px-3 disabled:opacity-40"
                  >
                    Próxima
                  </button>
                </div>
              </>
            )}
          </Cartao>
        </>
      )}
    </div>
  );
}
