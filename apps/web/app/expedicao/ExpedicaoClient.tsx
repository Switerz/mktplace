"use client";

/**
 * Gate EXP-2B — tela operacional da Expedicao. Redesenhada no EXP-UX-1.
 *
 * Consome SOMENTE `/api/v1/expedicao` e `/api/v1/expedicao/trend`. Nao calcula
 * estado operacional: todas as classificacoes ja vem do pipeline, e refaze-las
 * aqui faria dois consumidores verem coisas diferentes da mesma fotografia.
 *
 * A logica pura vive em `@/lib/expedicao-contract`; este arquivo so' pinta.
 *
 * HIERARQUIA (EXP-UX-1): resumo primeiro, profundidade sob demanda.
 *   1. cabecalho compacto com canal, instante e frescor;
 *   2. UMA barra de qualidade do dado, expansivel — nunca uma pilha de banners;
 *   3. quatro KPIs que mudam de significado por canal;
 *   4. faixa secundaria com o resto das medidas;
 *   5. mapa de risco por conta;
 *   6. evolucao do backlog em grafico, com tabela acessivel sob demanda;
 *   7. timeline dos mais criticos da pagina;
 *   8. fila operacional completa, que segue sendo o instrumento de auditoria.
 */
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  CANAIS,
  EXPLICACAO_LIMIAR_48H,
  FILTROS_PADRAO,
  JANELAS_TENDENCIA,
  JANELA_INICIAL_HORAS,
  ORDENACOES,
  ROTULO_CANAL,
  SITUACOES,
  TIMELINE_MAX_LINHAS,
  aplicarCanal,
  aplicarFiltro,
  avisosDeCobertura,
  canalTemPrazo,
  chaveDaRequisicao,
  construirQuery,
  estadoDaTela,
  estadoDaTendencia,
  formatarHoras,
  montarBarraDeQualidade,
  montarFrescor,
  montarKpisPrincipais,
  montarKpisSecundarios,
  montarMapaDeRisco,
  montarPontosDoGrafico,
  montarRelogios,
  montarSeries,
  montarTimeline,
  mostrarColunaReferencia,
  paginaAtual,
  queryDaTendencia,
  rotuloDeadline,
  rotuloIdade,
  sanitizarCanal,
  type Canal,
  type Filtros,
  type Kpi,
  type LinhaRisco,
  type Ordenacao,
  type RespostaExpedicao,
  type RespostaTendencia,
  type Situacao,
} from "@/lib/expedicao-contract";

// Recharts fica fora do bundle inicial: a tela abre com KPIs e fila mesmo que
// o grafico ainda esteja chegando.
const TendenciaChart = dynamic(() => import("@/components/expedicao/TendenciaChart"), {
  ssr: false,
  loading: () => (
    <p role="status" className="py-12 text-center text-sm text-slate-500">
      Carregando gráfico…
    </p>
  ),
});

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

/** Cor por SITUACAO, nunca por conta: verde/ambar/vermelho sao estado. */
const COR_SEGMENTO: Record<string, string> = {
  overdue: "bg-red-500",
  due24h: "bg-amber-400",
  on_time: "bg-emerald-500",
  sem_prazo: "bg-slate-300",
};

const COR_BADGE: Record<string, string> = {
  overdue: "bg-red-50 text-red-800 ring-red-200",
  due_within_24h: "bg-amber-50 text-amber-900 ring-amber-200",
  on_time: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  unavailable: "bg-slate-100 text-slate-600 ring-slate-200",
};

function Cartao({
  titulo,
  acao,
  children,
}: {
  titulo: string;
  acao?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-800">{titulo}</h3>
        {acao}
      </div>
      {children}
    </section>
  );
}

/**
 * Grupo de radio com o teclado que o papel ARIA promete.
 *
 * `role="radiogroup"` cria uma expectativa concreta em leitor de tela: o grupo
 * e' UM ponto de tabulacao e as SETAS andam entre as opcoes. Sem isso, cada
 * opcao vira um tab stop e as setas nao fazem nada — o papel anuncia um
 * comportamento que a tela nao tem, o que e' pior que nao anunciar.
 *
 * `tabIndex` rovente: so' a opcao marcada entra na ordem de tabulacao.
 */
function GrupoRadio<T extends string>({
  rotulo,
  opcoes,
  valor,
  aoEscolher,
  className,
  classeOpcao,
}: {
  rotulo: string;
  opcoes: readonly { valor: T; texto: string }[];
  valor: T;
  aoEscolher: (v: T) => void;
  className: string;
  classeOpcao: (ativo: boolean) => string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const andar = (de: number, passo: number) => {
    const destino = (de + passo + opcoes.length) % opcoes.length;
    aoEscolher(opcoes[destino].valor);
    refs.current[destino]?.focus();
  };

  return (
    <div role="radiogroup" aria-label={rotulo} className={className}>
      {opcoes.map((o, i) => {
        const ativo = o.valor === valor;
        return (
          <button
            key={o.valor}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={ativo}
            tabIndex={ativo ? 0 : -1}
            onClick={() => aoEscolher(o.valor)}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight" || e.key === "ArrowDown") {
                e.preventDefault();
                andar(i, 1);
              } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
                e.preventDefault();
                andar(i, -1);
              } else if (e.key === "Home") {
                e.preventDefault();
                andar(-1, 1);
              } else if (e.key === "End") {
                e.preventDefault();
                andar(0, -1);
              }
            }}
            className={`min-h-[44px] min-w-[44px] rounded-md text-sm transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 ${classeOpcao(
              ativo,
            )}`}
          >
            {o.texto}
          </button>
        );
      })}
    </div>
  );
}

function dataCurta(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

/** "manual_snapshot" nao diz nada a quem opera. Isto diz. */
function modoHumano(modo: string): string {
  return modo === "manual_snapshot"
    ? "atualizada à mão, sem agendamento"
    : modo.replace(/_/g, " ");
}

function numero(n: number | null | undefined): string {
  return typeof n === "number" ? n.toLocaleString("pt-BR") : "—";
}

function CartaoKpi({ kpi, destaque }: { kpi: Kpi; destaque: boolean }) {
  const texto = kpi.valorTexto ?? (kpi.valor === null ? "—" : numero(kpi.valor));
  const ausente = kpi.valorTexto !== undefined;
  return (
    <div
      className={`rounded-xl border p-4 ${
        kpi.alerta
          ? "border-amber-300 bg-amber-50"
          : "border-slate-200 bg-white shadow-sm"
      }`}
    >
      <p className="text-xs font-medium text-slate-600">{kpi.rotulo}</p>
      <p
        className={`mt-1 font-semibold tabular-nums ${
          destaque ? "text-3xl" : "text-xl"
        } ${ausente ? "text-slate-400" : "text-slate-900"}`}
      >
        {texto}
      </p>
      {kpi.nota && (
        <p className="mt-1.5 text-[12px] leading-snug text-slate-500">{kpi.nota}</p>
      )}
    </div>
  );
}

/** Barra empilhada (Shopee) ou barra total com marcadores (ML). */
function BarraDeRisco({
  linha,
  maximo,
  empilhavel,
}: {
  linha: LinhaRisco;
  maximo: number;
  empilhavel: boolean;
}) {
  const largura = maximo > 0 ? (linha.backlog / maximo) * 100 : 0;
  return (
    <div className="flex items-center gap-3 py-1.5">
      <div className="w-32 shrink-0 truncate text-sm text-slate-700" title={linha.conta}>
        {linha.conta}
      </div>
      <div className="relative h-6 flex-1 overflow-hidden rounded bg-slate-100">
        {empilhavel ? (
          <div className="flex h-full" style={{ width: `${largura}%` }}>
            {linha.segmentos.map((s) => (
              <div
                key={s.chave}
                className={COR_SEGMENTO[s.chave] ?? "bg-slate-300"}
                style={{
                  width: linha.backlog > 0 ? `${(s.valor / linha.backlog) * 100}%` : "0%",
                }}
                title={`${s.rotulo}: ${numero(s.valor)}`}
              />
            ))}
          </div>
        ) : (
          <>
            {/* Barra do BACKLOG. As sobreposicoes viram marcadores por cima —
                empilhá-las desenharia um total que nao existe. */}
            <div
              className="h-full rounded bg-brand-600/70"
              style={{ width: `${largura}%` }}
              title={`Backlog: ${numero(linha.backlog)}`}
            />
            {linha.sobreposicoes.map((s, i) =>
              s.valor > 0 && linha.backlog > 0 ? (
                <div
                  key={s.chave}
                  className={`absolute top-0 h-full border-r-2 ${
                    i === 0 ? "border-amber-500" : "border-red-500"
                  }`}
                  style={{ width: `${(s.valor / linha.backlog) * largura}%` }}
                  title={`${s.rotulo}: ${numero(s.valor)} (sobrepõe o backlog)`}
                />
              ) : null,
            )}
          </>
        )}
      </div>
      <div className="w-16 shrink-0 text-right text-sm font-medium tabular-nums text-slate-800">
        {numero(linha.backlog)}
      </div>
    </div>
  );
}

export default function ExpedicaoClient() {
  const [filtros, setFiltros] = useState<Filtros>(FILTROS_PADRAO);
  const [dados, setDados] = useState<RespostaExpedicao | null>(null);
  const [tendencia, setTendencia] = useState<RespostaTendencia | null>(null);
  const [janela, setJanela] = useState(JANELA_INICIAL_HORAS);
  const [carregando, setCarregando] = useState(true);
  const [carregandoTendencia, setCarregandoTendencia] = useState(true);
  const [erroHttp, setErroHttp] = useState<number | null>(null);
  const [erroTendencia, setErroTendencia] = useState(false);
  const [qualidadeAberta, setQualidadeAberta] = useState(false);
  const [dadosDaTendencia, setDadosDaTendencia] = useState(false);

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
        if (!vivo || chaveVigente.current !== minhaChave) return;
        if (!r.ok) {
          setErroHttp(r.status);
          setDados(null);
          setCarregando(false);
          return;
        }
        const json = (await r.json()) as RespostaExpedicao;
        if (!vivo || chaveVigente.current !== minhaChave) return;
        setDados(json);
        setCarregando(false);
      })
      .catch(() => {
        if (!vivo || chaveVigente.current !== minhaChave) return;
        setErroHttp(0);
        setDados(null);
        setCarregando(false);
      });
    return () => {
      vivo = false;
    };
  }, [chave, filtros, urlPronta]);

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
  const principais = montarKpisPrincipais(
    dados?.totals ?? null,
    dados?.freshness ?? [],
    filtros.channel,
  );
  const secundarios = montarKpisSecundarios(
    dados?.totals ?? null,
    dados?.freshness ?? [],
    filtros.channel,
  );
  const frescor = montarFrescor(dados?.freshness ?? []);
  const avisos = avisosDeCobertura(dados?.coverage ?? null);
  const qualidade = montarBarraDeQualidade(relogios, avisos, frescor);
  const mapa = montarMapaDeRisco(dados?.accounts ?? [], filtros.channel);
  const series = montarSeries(tendencia?.points ?? []);
  const pontosGrafico = montarPontosDoGrafico(series);
  const contasSerie = series.map((s) => s.shopAccount);
  const estadoTend = estadoDaTendencia(
    carregandoTendencia,
    erroTendencia,
    series,
    dados?.coverage?.expected_accounts.length ?? 0,
  );
  // UMA condicao para o grafico, a tabela e o botao "Ver dados": se o bloco
  // nao e' renderizado, o controle que o revela nao pode existir.
  const temSerieNaTendencia = estadoTend === "ok" || estadoTend === "parcial";
  const timeline = montarTimeline(dados?.queue ?? [], filtros.channel);
  const mostrarRef = mostrarColunaReferencia(dados?.queue ?? []);
  const pag = paginaAtual(dados?.pagination ?? null);
  const temPrazo = canalTemPrazo(filtros.channel);

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
      {/* 1. CABECALHO COMPACTO ------------------------------------------- */}
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          {/* h2, nao h1: o `h1` da pagina e do shell (Topbar). Dois h1 quebram
              a arvore de cabecalhos para leitor de tela. */}
          {/* O canal fica NO TITULO, nao so' no seletor: numeros de uma loja
              sob o titulo de outra foi exatamente o defeito que o EXP-3C2-R/V
              corrigiu, e o titulo e' o ultimo lugar que o operador confere. */}
          <h2 className="text-xl font-semibold text-slate-900">
            Expedição — {ROTULO_CANAL[filtros.channel]}
          </h2>
          {ML_LIGADO && (
            <GrupoRadio
              rotulo="Canal da fotografia"
              opcoes={CANAIS.map((c) => ({ valor: c, texto: ROTULO_CANAL[c] }))}
              valor={filtros.channel}
              aoEscolher={trocarCanal}
              className="flex gap-1 rounded-lg bg-white p-1 ring-1 ring-slate-200"
              classeOpcao={(ativo) =>
                `px-4 ${
                  ativo
                    ? "bg-brand-600 font-semibold text-white"
                    : "text-slate-700 hover:bg-brand-50"
                }`
              }
            />
          )}
        </div>
        <p className="text-sm text-slate-600">
          Fotografia de{" "}
          <span className="font-medium text-slate-800">
            {dataCurta(relogios?.efetivoEm ?? null)}
          </span>
          {relogios?.snapshotAgeHours !== null && relogios?.snapshotAgeHours !== undefined && (
            <>
              {" · "}
              <span
                className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                  relogios.snapshotVelho
                    ? "bg-amber-100 text-amber-900"
                    : "bg-emerald-100 text-emerald-800"
                }`}
              >
                há {formatarHoras(relogios.snapshotAgeHours)}
              </span>
            </>
          )}
          {" · "}
          {modoHumano(relogios?.modoDeCarga ?? "manual_snapshot")}
        </p>
      </header>

      {/* 2. QUALIDADE DO DADO, CONSOLIDADA ------------------------------- */}
      {qualidade.principal && (
        <section
          aria-label="Qualidade do dado"
          className={`rounded-xl border ${
            qualidade.severidade === "alerta"
              ? "border-red-200 bg-red-50"
              : qualidade.severidade === "atencao"
                ? "border-amber-200 bg-amber-50"
                : "border-slate-200 bg-white"
          }`}
        >
          <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
            <p role="status" className="text-sm text-slate-800">
              {qualidade.principal.texto}
            </p>
            {qualidade.detalhes.length > 1 && (
              <button
                type="button"
                aria-expanded={qualidadeAberta}
                aria-controls="exp-qualidade-detalhes"
                onClick={() => setQualidadeAberta((v) => !v)}
                className="min-h-[44px] rounded-md px-3 text-sm font-medium text-slate-700 underline decoration-slate-400 underline-offset-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
              >
                {qualidadeAberta
                  ? "Ocultar detalhes"
                  : `Ver ${qualidade.detalhes.length} limitações`}
              </button>
            )}
          </div>
          {qualidadeAberta && (
            <ul
              id="exp-qualidade-detalhes"
              className="border-t border-black/5 px-4 py-2 text-sm text-slate-700"
            >
              {qualidade.detalhes.map((d) => (
                <li key={d.chave} className="flex gap-2 py-1">
                  <span aria-hidden="true" className="text-slate-400">
                    •
                  </span>
                  <span>{d.texto}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {estado === "carregando" && (
        <p role="status" className="text-sm text-slate-600">
          Carregando fotografia…
        </p>
      )}
      {estado === "canal_desligado" && (
        <p role="status" className="rounded-xl border border-slate-200 bg-white px-4 py-6 text-sm text-slate-700">
          Este canal ainda não está disponível na API. A fotografia existe, mas a
          exposição depende de uma ativação coordenada.
        </p>
      )}
      {estado === "indisponivel_backend" && (
        <p role="alert" className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
          A API de Expedição está desligada. Nada a exibir.
        </p>
      )}
      {estado === "sem_snapshot" && (
        <p role="alert" className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
          Nenhuma fotografia publicada ainda.
        </p>
      )}
      {estado === "batch_inconsistente" && (
        <p role="alert" className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm">
          A fotografia está inconsistente e não pode ser exibida. Nenhum número
          parcial é mostrado de propósito.
        </p>
      )}
      {estado === "erro_validacao" && (
        <p role="alert" className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm">
          Filtro inválido. Ajuste a seleção e tente novamente.
        </p>
      )}
      {estado === "erro" && (
        <p role="alert" className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm">
          Não foi possível carregar a Expedição agora.
        </p>
      )}
      {estado === "fotografia_vazia" && (
        <p role="status" className="rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-sm">
          Backlog zerado nesta fotografia. Nenhum pedido aguardando expedição.
        </p>
      )}

      {(estado === "ok" || estado === "fila_vazia_por_filtro") && (
        <>
          {/* 3. QUATRO KPIs PRINCIPAIS ---------------------------------- */}
          <section aria-label="Indicadores principais" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {principais.map((k) => (
              <CartaoKpi key={k.chave} kpi={k} destaque />
            ))}
          </section>

          {/* 4. FAIXA SECUNDARIA --------------------------------------- */}
          <section
            aria-label="Indicadores secundários"
            className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5"
          >
            {secundarios.map((k) => (
              <CartaoKpi key={k.chave} kpi={k} destaque={false} />
            ))}
          </section>

          {/* 5. MAPA DE RISCO POR CONTA -------------------------------- */}
          <Cartao titulo="Risco por conta">
            <p className="mb-3 text-[12px] leading-snug text-slate-500">{mapa.nota}</p>
            {mapa.empilhavel && (
              <ul className="mb-3 flex flex-wrap gap-3 text-[12px] text-slate-600">
                {["overdue", "due24h", "on_time", "sem_prazo"].map((chave) => {
                  const rotulo = mapa.linhas[0]?.segmentos.find((s) => s.chave === chave)?.rotulo;
                  return rotulo ? (
                    <li key={chave} className="flex items-center gap-1.5">
                      <span
                        aria-hidden="true"
                        className={`inline-block h-2.5 w-2.5 rounded-sm ${COR_SEGMENTO[chave]}`}
                      />
                      {rotulo}
                    </li>
                  ) : null;
                })}
              </ul>
            )}
            {!mapa.empilhavel && (
              <ul className="mb-3 flex flex-wrap gap-3 text-[12px] text-slate-600">
                <li className="flex items-center gap-1.5">
                  <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-sm bg-brand-600/70" />
                  Backlog
                </li>
                <li className="flex items-center gap-1.5">
                  <span aria-hidden="true" className="inline-block h-3 w-0.5 bg-amber-500" />
                  Acima de 48h (sobrepõe)
                </li>
                <li className="flex items-center gap-1.5">
                  <span aria-hidden="true" className="inline-block h-3 w-0.5 bg-red-500" />
                  Travados (sobrepõe)
                </li>
              </ul>
            )}
            {mapa.linhas.map((l) => (
              <BarraDeRisco
                key={l.conta}
                linha={l}
                maximo={mapa.maximo}
                empilhavel={mapa.empilhavel}
              />
            ))}
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-sm [&_td]:py-2 [&_td]:pr-4 [&_th]:pr-4">
                <caption className="sr-only">
                  Backlog por conta, com as medidas do canal
                </caption>
                <thead>
                  <tr className="text-left text-xs text-slate-500">
                    <th scope="col" className="py-2">Conta</th>
                    <th scope="col">Marca</th>
                    <th scope="col" className="text-right">Backlog</th>
                    {temPrazo && <th scope="col" className="text-right">Vencidos</th>}
                    <th scope="col" className="text-right">Acima de 48h</th>
                    <th scope="col" className="text-right">Travados</th>
                    <th scope="col">Fonte avançou</th>
                  </tr>
                </thead>
                <tbody>
                  {(dados?.accounts ?? []).map((a) => (
                    <tr key={a.shop_account} className="border-t border-slate-100">
                      <th scope="row" className="py-2 text-left font-normal">{a.shop_account}</th>
                      <td>{a.brand}</td>
                      <td className="text-right tabular-nums">{numero(a.backlog_count)}</td>
                      {temPrazo && (
                        <td className="text-right tabular-nums">{numero(a.overdue_count)}</td>
                      )}
                      <td className="text-right tabular-nums">{numero(a.over_48h_count)}</td>
                      <td className="text-right tabular-nums">{numero(a.stalled_count)}</td>
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

          {/* 6. EVOLUCAO DO BACKLOG ------------------------------------ */}
          <Cartao
            titulo="Evolução do backlog por conta"
            acao={
              <div className="flex items-center gap-2">
                <GrupoRadio
                  rotulo="Janela da tendência"
                  opcoes={JANELAS_TENDENCIA.map((j) => ({
                    valor: String(j.horas),
                    texto: j.rotulo,
                  }))}
                  valor={String(janela)}
                  aoEscolher={(v) => setJanela(Number(v))}
                  className="flex gap-1 rounded-lg bg-slate-100 p-1"
                  classeOpcao={(ativo) =>
                    `px-3 ${
                      ativo
                        ? "bg-white font-semibold text-slate-900 shadow-sm"
                        : "text-slate-600"
                    }`
                  }
                />
                {/* "Ver dados" so' existe quando HA dados a revelar.
                    Com a tendencia vazia — o caso da Shopee quando a
                    fotografia cai fora da janela — o botao aparecia, alternava
                    `aria-expanded` e apontava `aria-controls` para um alvo que
                    nunca era renderizado: um controle que nao faz nada e uma
                    referencia ARIA quebrada. */}
                {temSerieNaTendencia && (
                  <button
                    type="button"
                    aria-expanded={dadosDaTendencia}
                    aria-controls="exp-tendencia-dados"
                    onClick={() => setDadosDaTendencia((v) => !v)}
                    className="min-h-[44px] rounded-md px-3 text-sm text-slate-700 underline decoration-slate-400 underline-offset-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600"
                  >
                    {dadosDaTendencia ? "Ocultar dados" : "Ver dados"}
                  </button>
                )}
              </div>
            }
          >
            {estadoTend === "carregando" && (
              <p role="status" className="py-12 text-center text-sm text-slate-500">
                Carregando tendência…
              </p>
            )}
            {estadoTend === "erro" && (
              <p role="alert" className="py-12 text-center text-sm text-red-700">
                Tendência indisponível agora.
              </p>
            )}
            {estadoTend === "vazia" && (
              <p role="status" className="py-12 text-center text-sm text-slate-600">
                Sem pontos na janela escolhida.
              </p>
            )}
            {estadoTend === "parcial" && (
              <p
                role="status"
                className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
              >
                Série parcial: alguma hora tem menos contas que o esperado. A linha se
                interrompe porque falta dado, não porque o backlog caiu.
              </p>
            )}
            {temSerieNaTendencia && (
              <>
                <TendenciaChart pontos={pontosGrafico} contas={contasSerie} />
                {dadosDaTendencia && (
                  <div id="exp-tendencia-dados" className="mt-3 max-h-[320px] overflow-auto">
                    <table className="w-full text-sm [&_td]:py-2 [&_td]:pr-4 [&_th]:pr-4">
                      <caption className="sr-only">
                        Backlog por conta e hora — contas nunca são somadas
                      </caption>
                      <thead className="sticky top-0 bg-white">
                        <tr className="text-left text-xs text-slate-500">
                          <th scope="col" className="py-2">Conta</th>
                          <th scope="col">Hora</th>
                          <th scope="col" className="text-right">Backlog</th>
                          <th scope="col" className="text-right">Acima de 48h</th>
                          <th scope="col" className="text-right">Travados</th>
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
                              <td className="text-right tabular-nums">{numero(p.backlog)}</td>
                              <td className="text-right tabular-nums">{numero(p.over48h)}</td>
                              <td className="text-right tabular-nums">{numero(p.stalled)}</td>
                            </tr>
                          )),
                        )}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}
          </Cartao>

          {/* 7. TIMELINE DOS MAIS CRITICOS ----------------------------- */}
          {timeline.length > 0 && (
            <Cartao titulo={`Mais críticos desta página (${timeline.length})`}>
              <p className="mb-3 text-[12px] leading-snug text-slate-500">
                {temPrazo
                  ? "Tempo em aberto até agora. A marca vermelha indica prazo contratual vencido."
                  : "Tempo em aberto até agora. O Mercado Livre não publica prazo de despacho, " +
                    "então não há fim contratual a desenhar."}{" "}
                Recorte dos {TIMELINE_MAX_LINHAS} mais antigos da página atual — não é o
                backlog inteiro.
              </p>
              <ul className="flex flex-col gap-1.5">
                {timeline.map((b) => {
                  const maxH = timeline[0]?.horasAbertas || 1;
                  return (
                    <li key={b.chave} className="flex items-center gap-3">
                      <span className="w-28 shrink-0 truncate text-xs text-slate-600" title={b.conta}>
                        {b.conta}
                      </span>
                      <span className="h-4 flex-1 overflow-hidden rounded bg-slate-100">
                        <span
                          className={`block h-full rounded ${
                            b.vencido
                              ? "bg-red-500"
                              : b.acimaDoLimiar
                                ? "bg-amber-400"
                                : "bg-brand-600/60"
                          }`}
                          style={{ width: `${(b.horasAbertas / maxH) * 100}%` }}
                        />
                      </span>
                      <span className="w-16 shrink-0 text-right text-xs tabular-nums text-slate-700">
                        {formatarHoras(b.horasAbertas)}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </Cartao>
          )}

          {/* 8. FILA OPERACIONAL --------------------------------------- */}
          <Cartao titulo="Fila operacional">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              {SITUACOES.map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={filtros.situacao.includes(s)}
                  onClick={() => alternarSituacao(s)}
                  className={`min-h-[44px] min-w-[44px] rounded-md border px-3 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 ${
                    filtros.situacao.includes(s)
                      ? "border-brand-600 bg-brand-600 text-white"
                      : "border-slate-300 bg-white text-slate-700 hover:bg-brand-50"
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
                  className="min-h-[44px] rounded-md border border-slate-300 px-2 text-sm"
                >
                  {ORDENACOES.map((o) => (
                    <option key={o} value={o}>{ROTULO_ORDEM[o]}</option>
                  ))}
                </select>
              </label>
            </div>

            <p className="mb-2 text-[12px] text-slate-500">{EXPLICACAO_LIMIAR_48H}</p>

            {estado === "fila_vazia_por_filtro" ? (
              <p role="status" className="py-8 text-center text-sm text-slate-600">
                Nenhum pedido com os filtros atuais. O backlog total continua{" "}
                {numero(dados?.totals?.backlog_count ?? 0)}.
              </p>
            ) : (
              <>
                <div className="max-h-[520px] overflow-auto">
                  <table className="w-full text-sm [&_td]:py-2 [&_td]:pr-4 [&_th]:pr-4">
                    <caption className="sr-only">Pedidos aguardando expedição</caption>
                    <thead className="sticky top-0 bg-white">
                      <tr className="text-left text-xs text-slate-500">
                        {mostrarRef && <th scope="col" className="py-2">Referência</th>}
                        <th scope="col" className="py-2">Conta</th>
                        <th scope="col">Marca</th>
                        {temPrazo && <th scope="col">Prazo</th>}
                        <th scope="col">Situação</th>
                        <th scope="col" className="text-right">Aberto há</th>
                        <th scope="col">Idade operacional</th>
                        <th scope="col">Transportadora</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(dados?.queue ?? []).map((l, i) => (
                        <tr
                          key={`${l.shop_account}-${l.order_ref ?? i}`}
                          className="border-t border-slate-100 hover:bg-brand-50/40"
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
                          {temPrazo && <td>{dataCurta(l.dispatch_deadline)}</td>}
                          <td>
                            <span
                              className={`inline-block rounded px-1.5 py-0.5 text-[12px] font-medium ring-1 ring-inset ${
                                COR_BADGE[l.deadline_status] ?? COR_BADGE.unavailable
                              }`}
                            >
                              {rotuloDeadline(l.deadline_status)}
                            </span>
                          </td>
                          <td className="text-right tabular-nums">
                            {formatarHoras(l.hours_open)}
                          </td>
                          <td className="text-slate-600">{rotuloIdade(l.operational_age_status)}</td>
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
                    className="min-h-[44px] min-w-[44px] rounded-md border border-slate-300 px-3 disabled:opacity-40"
                  >
                    Anterior
                  </button>
                  <span className="tabular-nums">
                    Página {pag.pagina} de {pag.total} ·{" "}
                    {numero(dados?.pagination?.total ?? 0)} pedidos
                  </span>
                  <button
                    type="button"
                    disabled={!dados?.pagination?.has_more}
                    onClick={() =>
                      setFiltros((f) => aplicarFiltro(f, { offset: f.offset + f.limit }))
                    }
                    className="min-h-[44px] min-w-[44px] rounded-md border border-slate-300 px-3 disabled:opacity-40"
                  >
                    Próxima
                  </button>
                </div>
              </>
            )}
          </Cartao>

          {/* Frescor por marca fica no fim: e' diagnostico, nao operacao. */}
          <Cartao titulo="Fonte por marca">
            <p className="mb-2 text-[12px] text-slate-500">
              Idade da fonte é quando o Data Mart foi lido. Idade do pedido mais antigo é
              contexto: um backlog legítimo sempre tem pedido velho, e isso não significa
              fonte desatualizada.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm [&_td]:py-2 [&_td]:pr-4 [&_th]:pr-4">
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
        </>
      )}
    </div>
  );
}
