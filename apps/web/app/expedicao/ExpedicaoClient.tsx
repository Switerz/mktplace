"use client";

/**
 * Gate EXP-2B — tela operacional da Expedicao.
 * Redesenhada no EXP-UX-1 e retematizada no UX-TORRE-1.
 *
 * Consome SOMENTE `/api/v1/expedicao` e `/api/v1/expedicao/trend`. Nao calcula
 * estado operacional: todas as classificacoes ja vem do pipeline, e refaze-las
 * aqui faria dois consumidores verem coisas diferentes da mesma fotografia.
 *
 * A logica pura vive em `@/lib/expedicao-contract`; este arquivo so' pinta.
 *
 * HIERARQUIA (EXPEDICAO-UX-UNIFICADA-1) — a MESMA dos tres canais:
 *   1. cabecalho do canal (compartilhado: `CabecalhoExpedicao`);
 *   2. fotografia, frescor e modo de publicacao, em selos no cabecalho;
 *   3. UMA barra de qualidade do dado, recolhivel (`BarraDeQualidade`);
 *   4. o que exige atencao agora — contagens da API em frase;
 *   5. KPIs principais;
 *   6. evolucao do backlog por conta;
 *   7. concentracao por conta, numa UNICA tabela com a barra embutida;
 *   8. detalhe operacional: KPIs secundarios, mais criticos, fila e fonte.
 *
 * Duas profundidades, escolhidas na casca (`?visao=`): a visao do gestor
 * (padrao) mostra 4-7; o detalhe operacional mostra os KPIs e o item 8, com
 * as contas. Nada saiu da tela: so' mudou de profundidade.
 *
 * O canal agora chega da casca, que e' dona do seletor unico
 * (Shopee | Mercado Livre | TikTok Shop). Este componente continua sendo o
 * corpo da FILA e nao conhece o TikTok.
 *
 * TEXTO LONGO FOI MOVIDO, NAO APAGADO. Metodologia, limiar interno e ressalva
 * de recorte vivem em "Como ler" — mesma palavra, sob demanda. Nenhuma
 * ressalva de qualidade foi removida nem teve o sentido alterado.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import BarraDeQualidade from "@/components/expedicao/BarraDeQualidade";
import EvolucaoPorMarca from "@/components/expedicao/EvolucaoPorMarca";
import CabecalhoExpedicao, { SeletorMarca } from "@/components/expedicao/CabecalhoExpedicao";
import {
  AtencaoAgora,
  ConviteAoDetalhe,
  FaixaKpis,
  SecaoGerencial,
} from "@/components/expedicao/SecoesExpedicao";
import Disclosure from "@/components/ui/Disclosure";
import KpiTile from "@/components/ui/KpiTile";
import Note from "@/components/ui/Note";
import Panel from "@/components/ui/Panel";
import Segmented from "@/components/ui/Segmented";
import type { Tom } from "@/components/ui/tone";
import { comMarcaNaQuery, type Visao } from "@/lib/expedicao-ux";
import { brandLabel } from "@/lib/filters/brands";

import {
  DEFINICAO_FILA,
  EXPLICACAO_FONTE,
  EXPLICACAO_LIMIAR_48H,
  EXPLICACAO_LIMIAR_48H_SEM_PRAZO,
  FILTROS_PADRAO,
  SEM_LDR_SHOPEE,
  SEM_PRAZO_CANAL_ML,
  envelhecimentoDaFila,
  faixasDePrazo,
  montarRiscoAtual,
  recortarPorMarca,
  rotuloFonte,
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
  fotografiaDaFila,
  montarAtencaoDaFila,
  montarBarraDeQualidade,
  montarFrescor,
  montarKpisPrincipais,
  montarKpisSecundarios,
  montarMapaDeRisco,
  montarRelogios,
  montarSeries,
  montarTimeline,
  mostrarColunaReferencia,
  paginaAtual,
  rotuloDeadline,
  rotuloIdade,
  type Canal,
  type Filtros,
  type Kpi,
  type LinhaRisco,
  type Ordenacao,
  type RespostaExpedicao,
  type RespostaTendencia,
  type Situacao,
} from "@/lib/expedicao-contract";
import JanelaPersonalizada from "@/components/expedicao/JanelaPersonalizada";
import {
  OPCAO_PERSONALIZADA,
  REGRA_JANELA,
  descreverJanelaEmHoras,
  lerDiasDaUrl,
  opcoesDoSeletor,
  queryDaTendenciaEmDias,
  urlComDias,
  valorDoSeletor,
} from "@/lib/expedicao-janela";
import { formatarHoraBrt, montarEvolucaoPorMarca } from "@/lib/expedicao-evolucao";

/** Janela da tendencia da fila, em DIAS. Horas so' na query. */
const REGRA = REGRA_JANELA.fila;

// EXPEDICAO-UX-2: a evolucao virou small multiples em SVG proprio
// (`EvolucaoPorMarca`). O recharts saiu do bloco — e, com ele, o import
// dinamico que existia so' para tira-lo do bundle inicial.

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

// A flag do Mercado Livre (`NEXT_PUBLIC_EXPEDICAO_ML_ENABLED`) mora na casca
// desde o EXPEDICAO-UX-UNIFICADA-1: e' ela que decide quais canais o seletor
// oferece e sanitiza `?channel=`. Este componente so' recebe um canal servivel.

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

/**
 * Cor por SITUACAO, nunca por conta: verde/ambar/vermelho sao estado.
 *
 * Tokens semanticos, nao literais: o mesmo PAPEL muda de valor entre claro e
 * escuro, e nenhuma tela escreve o hex.
 */
const COR_SEGMENTO: Record<string, string> = {
  overdue: "bg-crit",
  due24h: "bg-warn",
  on_time: "bg-ok",
  sem_prazo: "bg-line-strong",
};

const COR_BADGE: Record<string, string> = {
  overdue: "bg-crit-soft text-crit-ink ring-crit/30",
  due_within_24h: "bg-warn-soft text-warn-ink ring-warn/30",
  on_time: "bg-ok-soft text-ok-ink ring-ok/30",
  unavailable: "bg-raised text-ink-muted ring-line-strong",
};

/** Classe comum das tabelas densas da tela. */
const CLASSE_TABELA =
  "w-full text-[13px] [&_td]:py-1.5 [&_td]:pr-4 [&_th]:py-1.5 [&_th]:pr-4";
const CLASSE_CABECALHO =
  "sticky top-0 z-10 bg-surface text-left text-xs uppercase tracking-wide text-ink-faint";

// O `GrupoRadio` que morava aqui saiu no EXPEDICAO-UX-UNIFICADA-1. Ele so'
// existia porque os testes liam este arquivo procurando os atributos ARIA; com
// o seletor de canal na casca, a tela passaria a ter DOIS componentes de radio
// lado a lado. Todo seletor da Expedicao agora e' o `Segmented` — o mesmo
// teclado, o mesmo alvo de 44px — e os testes seguem a garantia ate' ele.

function dataCurta(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

// O selo de modo de publicacao ("atualizada a mao") saiu no
// EXPEDICAO-UX-UNIFICADA-1: a API devolve `load_mode` CONSTANTE, e desde
// 21/10/2026 a fotografia e' publicada pela DAG. Ver `Relogios`.

function numero(n: number | null | undefined): string {
  return typeof n === "number" ? n.toLocaleString("pt-BR") : "—";
}

/**
 * Severidade do KPI.
 *
 * `ausente` NAO e' uma nota ruim: e' a marca de que a medida nao existe na
 * fonte. O numero sai apagado, com moldura tracejada, em vez de verde — zero
 * seria uma medicao, e ausencia nao e'.
 */
function tomDoKpi(kpi: Kpi): Tom {
  if (kpi.valorTexto !== undefined) return "ausente";
  return kpi.alerta ? "atencao" : "neutro";
}

function CelulaKpi({ kpi, destaque }: { kpi: Kpi; destaque: boolean }) {
  const texto = kpi.valorTexto ?? (kpi.valor === null ? "—" : numero(kpi.valor));
  return (
    <KpiTile
      rotulo={kpi.rotulo}
      valor={texto}
      // No cartao grande a nota fica visivel; no compacto vira tooltip, para
      // a faixa secundaria nao reintroduzir cinco paragrafos na tela.
      comparacao={destaque ? (kpi.notaCurta ?? kpi.nota) : undefined}
      contexto={kpi.nota}
      tom={tomDoKpi(kpi)}
      destaque={destaque}
    />
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
    <div className="relative h-4 w-full min-w-[120px] overflow-hidden rounded bg-raised">
      {empilhavel ? (
        <div className="flex h-full" style={{ width: `${largura}%` }}>
          {linha.segmentos.map((s) => (
            <div
              key={s.chave}
              className={COR_SEGMENTO[s.chave] ?? "bg-line-strong"}
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
            className="h-full rounded bg-accent/70"
            style={{ width: `${largura}%` }}
            title={`Backlog: ${numero(linha.backlog)}`}
          />
          {linha.sobreposicoes.map((s, i) =>
            s.valor > 0 && linha.backlog > 0 ? (
              <div
                key={s.chave}
                className={`absolute top-0 h-full border-r-2 ${
                  i === 0 ? "border-warn" : "border-crit"
                }`}
                style={{ width: `${(s.valor / linha.backlog) * largura}%` }}
                title={`${s.rotulo}: ${numero(s.valor)} (sobrepõe o backlog)`}
              />
            ) : null,
          )}
        </>
      )}
    </div>
  );
}

function ItemLegenda({ cor, children }: { cor: string; children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-1.5">
      <span aria-hidden="true" className={`inline-block h-2.5 w-2.5 rounded-sm ${cor}`} />
      {children}
    </li>
  );
}

export default function ExpedicaoClient({
  canal,
  seletorCanal,
  visao,
  aoTrocarVisao,
  marca,
  aoTrocarMarca,
}: {
  /** Canal da fila ja' sanitizado pela casca (flag do ML aplicada la'). */
  canal: Canal;
  seletorCanal?: ReactNode;
  visao: Visao;
  aoTrocarVisao?: (v: Visao) => void;
  /** EXPEDICAO-UX-3: marca escolhida na casca (`?marca=`), ou nenhuma. */
  marca?: string | null;
  aoTrocarMarca?: (m: string | null) => void;
}) {
  const marcaAtiva = marca ?? null;
  // A casca so' monta este componente depois de ler a URL, entao o canal da
  // primeira requisicao ja' e' o do link: nenhum quadro de uma loja sob o
  // titulo de outra, nenhuma requisicao de Shopee jogada fora.
  const [filtros, setFiltros] = useState<Filtros>(() => aplicarCanal(FILTROS_PADRAO, canal));
  const [dados, setDados] = useState<RespostaExpedicao | null>(null);
  const [tendencia, setTendencia] = useState<RespostaTendencia | null>(null);
  const [janela, setJanela] = useState(REGRA.padrao);
  // "Personalizado" escolhido no seletor; a janela so' muda no "Aplicar".
  const [personalizado, setPersonalizado] = useState(false);
  // `?dias=` invalido no link: a tela usa o padrao E avisa.
  const [janelaDescartada, setJanelaDescartada] = useState(false);
  const [carregando, setCarregando] = useState(true);
  const [carregandoTendencia, setCarregandoTendencia] = useState(true);
  const [erroHttp, setErroHttp] = useState<number | null>(null);
  const [erroTendencia, setErroTendencia] = useState(false);
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
   * `urlPronta` segura as duas buscas ate' a janela (`?dias=`) ser lida: sem
   * esperar, um link de 10 dias pediria primeiro a janela padrao. O canal ja'
   * chega resolvido pela casca (EXPEDICAO-UX-UNIFICADA-1).
   */
  const [urlPronta, setUrlPronta] = useState(false);

  /**
   * Janela da URL. Valor invalido nao vira requisicao: cai no padrao, avisa, e
   * a URL e' reescrita para nao seguir adiante num link compartilhado.
   */
  const lerJanelaDaUrl = useCallback(() => {
    const { dias, descartado } = lerDiasDaUrl(window.location.search, REGRA);
    setJanela(dias);
    setPersonalizado(false);
    setJanelaDescartada(descartado);
    if (descartado) {
      window.history.replaceState(null, "", urlComDias(window.location.href, dias, REGRA));
    }
  }, []);

  useEffect(() => {
    lerJanelaDaUrl();
    setUrlPronta(true);
  }, [lerJanelaDaUrl]);

  /**
   * Troca Shopee <-> Mercado Livre vinda da casca. O componente NAO remonta:
   * `aplicarCanal` decide campo a campo o que atravessa (marca sim, conta nao,
   * situacao de prazo so' onde ha prazo) e volta a pagina para o inicio.
   */
  useEffect(() => {
    setFiltros((f) => (f.channel === canal ? f : aplicarCanal(f, canal)));
  }, [canal]);

  // Marca -> `brands` da requisicao da fila. A API recorta a LISTA de
  // pedidos; os indicadores da marca saem de `recortarPorMarca`.
  useEffect(() => {
    const alvo = marcaAtiva ? [marcaAtiva] : [];
    setFiltros((f) => (f.brands.join(",") === alvo.join(",") ? f : aplicarFiltro(f, { brands: alvo })));
  }, [marcaAtiva]);

  // Voltar/avancar do navegador pode trocar a janela sem remontar o
  // componente. O canal da URL e' relido pela casca, que repassa a prop.
  useEffect(() => {
    const aoVoltar = () => {
      lerJanelaDaUrl();
    };
    window.addEventListener("popstate", aoVoltar);
    return () => window.removeEventListener("popstate", aoVoltar);
  }, [lerJanelaDaUrl]);

  /** Aplica uma janela JA' validada: estado e URL juntos, como o canal. */
  const aplicarJanela = useCallback((dias: number) => {
    setJanela(dias);
    setJanelaDescartada(false);
    window.history.replaceState(null, "", urlComDias(window.location.href, dias, REGRA));
  }, []);

  /** Atalho aplica na hora; "Personalizado" so' abre o campo. */
  const escolherJanela = useCallback(
    (valor: string) => {
      if (valor === OPCAO_PERSONALIZADA) {
        setPersonalizado(true);
        return;
      }
      setPersonalizado(false);
      aplicarJanela(Number(valor));
    },
    [aplicarJanela],
  );

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
    () => comMarcaNaQuery(queryDaTendenciaEmDias(janela, filtros.channel), marcaAtiva),
    [janela, filtros.channel, marcaAtiva],
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
  // Recorte por marca, por SELECAO das linhas que a API ja' publica.
  const recorte = recortarPorMarca(dados, marcaAtiva);
  const principais = montarKpisPrincipais(recorte.totais, recorte.frescor, filtros.channel);
  const secundarios = montarKpisSecundarios(recorte.totais, recorte.frescor, filtros.channel);
  const risco = montarRiscoAtual(recorte.totais, recorte.frescor, filtros.channel);
  const frescor = montarFrescor(recorte.frescor);
  const avisos = avisosDeCobertura(dados?.coverage ?? null);
  // A qualidade do dado e' do CANAL: a barra nao some quando se filtra marca.
  const qualidade = montarBarraDeQualidade(relogios, avisos, montarFrescor(dados?.freshness ?? []));
  const mapa = montarMapaDeRisco(recorte.contas, filtros.channel);
  const marcasPresentes = [...new Set((dados?.accounts ?? []).map((a) => a.brand))];
  const faixasPrazo = faixasDePrazo(recorte.totais);
  const envelhecimento = envelhecimentoDaFila(recorte.totais, recorte.frescor);
  const series = montarSeries(tendencia?.points ?? []);
  // Marca de cada conta, lida do proprio ponto da serie (`brand`).
  const evolucao = montarEvolucaoPorMarca(tendencia?.points ?? []);
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

  // Selos do MESMO canal que o titulo: na troca Shopee <-> Mercado Livre,
  // enquanto a resposta nova nao chega, eles nao descrevem a fotografia antiga.
  const fotografia =
    dados && dados.channel === filtros.channel ? fotografiaDaFila(relogios) : null;
  const atencao = montarAtencaoDaFila(recorte.totais, recorte.contas, filtros.channel);
  // "Agora" e' o instante da fotografia. Com ela velha, a secao diz isso em
  // vez de deixar o gestor agir sobre o passado sem saber.
  const quando = relogios?.efetivoEm
    ? `Segundo a fotografia de ${dataCurta(relogios.efetivoEm)}${
        relogios.snapshotVelho ? " — desatualizada: confirme antes de agir" : ""
      }.`
    : undefined;

  const faixaPrincipal = (
    <FaixaKpis rotulo="Indicadores principais">
      {principais.map((k) => (
        <CelulaKpi key={k.chave} kpi={k} destaque />
      ))}
    </FaixaKpis>
  );

  // EXPEDICAO-UX-3 — "Risco atual": os indicadores que o CANAL sustenta.
  const avisoRecorte = recorte.variasContas ? (
    <Note tom="neutro" papel="status">
      {brandLabel(recorte.marca ?? "")} tem {recorte.contas.length} contas nesta fotografia. A API
      ainda não recorta o total por marca e a tela não soma contas: os números de cada conta
      estão em “Concentração por marca”.
    </Note>
  ) : null;

  const secaoRisco = (
    <SecaoGerencial titulo="Risco atual" descricao={quando}>
      {avisoRecorte}
      <FaixaKpis rotulo="Risco atual" colunas={risco.length === 6 ? 6 : 4}>
        {risco.map((k) => (
          <CelulaKpi key={k.chave} kpi={k} destaque />
        ))}
      </FaixaKpis>
      <AtencaoAgora
        itens={atencao}
        semItens={
          recorte.variasContas
            ? "Com mais de uma conta na marca, a leitura de atenção fica por conta: veja “Concentração por marca”."
            : undefined
        }
      />
    </SecaoGerencial>
  );

  // "Prazo": a Shopee publica `ship_by_date`; o Mercado Livre, nao.
  const secaoPrazo = temPrazo ? (
    <SecaoGerencial
      titulo="Prazo"
      descricao="Situação de cada pedido em fila frente ao prazo de envio publicado pela Shopee (ship_by_date)."
    >
      <Panel titulo="Pedidos em fila por situação de prazo">
        {faixasPrazo && recorte.totais && recorte.totais.backlog_count > 0 ? (
          <>
            <div
              className="flex h-3 w-full overflow-hidden rounded bg-raised"
              role="img"
              aria-label={faixasPrazo.map((f) => `${f.rotulo}: ${numero(f.valor)}`).join(", ")}
            >
              {faixasPrazo.map((f) => (
                <div
                  key={f.chave}
                  className={COR_SEGMENTO[f.chave]}
                  style={{ width: `${(f.valor / (recorte.totais?.backlog_count || 1)) * 100}%` }}
                />
              ))}
            </div>
            <ul className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {faixasPrazo.map((f) => (
                <li key={f.chave} className="flex items-baseline gap-2">
                  <span aria-hidden="true" className={`inline-block h-2.5 w-2.5 shrink-0 rounded-sm ${COR_SEGMENTO[f.chave]}`} />
                  <span className="text-xs text-ink-muted">{f.rotulo}</span>
                  <span className="ml-auto text-sm font-semibold tabular-nums text-ink">{numero(f.valor)}</span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-ink-muted">
            {recorte.variasContas
              ? "Situação de prazo por conta em “Concentração por marca”."
              : "Sem pedidos em fila nesta fotografia."}
          </p>
        )}
        <Note tom="neutro" papel="note" className="mt-3">
          {SEM_LDR_SHOPEE}
        </Note>
      </Panel>
    </SecaoGerencial>
  ) : (
    <SecaoGerencial titulo="Prazo" descricao="O canal não publica prazo de envio.">
      <Panel titulo="Prazo indisponível pelo canal">
        <p className="text-sm text-ink">{SEM_PRAZO_CANAL_ML}</p>
        {envelhecimento ? (
          <dl className="mt-3 grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg bg-raised px-3 py-2">
              <dt className="text-xs text-ink-muted">Acima de 48 h (limiar interno da Torre)</dt>
              <dd className="text-sm text-ink">
                <span className="text-xl font-semibold tabular-nums">{numero(envelhecimento.acima48h)}</span>{" "}
                de {numero(envelhecimento.backlog)} pedidos em fila
              </dd>
            </div>
            <div className="rounded-lg bg-raised px-3 py-2">
              <dt className="text-xs text-ink-muted">Pedido mais antigo em fila</dt>
              <dd className="text-sm text-ink">
                {envelhecimento.maisAntigo ? (
                  <>
                    <span className="text-xl font-semibold tabular-nums">
                      {formatarHoras(envelhecimento.maisAntigo.horas)}
                    </span>{" "}
                    · {brandLabel(envelhecimento.maisAntigo.marca)}
                  </>
                ) : (
                  "não informado pela fonte"
                )}
              </dd>
            </div>
          </dl>
        ) : null}
      </Panel>
    </SecaoGerencial>
  );

  const painelMaisCriticos =
    timeline.length > 0 ? (
      <Panel
        titulo={`Mais críticos (${timeline.length})`}
        acao={
          <Disclosure rotulo="Como ler">
            {temPrazo
              ? "Tempo em aberto até agora. A marca vermelha indica prazo contratual vencido."
              : "Tempo em aberto até agora. O Mercado Livre não publica prazo de despacho, " +
                "então não há fim contratual a desenhar."}{" "}
            Recorte dos {TIMELINE_MAX_LINHAS} mais antigos da página atual — não é o
            backlog inteiro.
          </Disclosure>
        }
      >
        <ul className="flex flex-col gap-1">
          {timeline.map((b) => {
            const maxH = timeline[0]?.horasAbertas || 1;
            return (
              <li key={b.chave} className="flex items-center gap-2">
                <span
                  className="w-20 shrink-0 truncate text-xs text-ink-muted"
                  title={b.conta}
                >
                  {b.conta}
                </span>
                <span className="h-3.5 flex-1 overflow-hidden rounded bg-raised">
                  <span
                    className={`block h-full rounded ${
                      b.vencido
                        ? "bg-crit"
                        : b.acimaDoLimiar
                          ? "bg-warn"
                          : "bg-accent/60"
                    }`}
                    style={{ width: `${(b.horasAbertas / maxH) * 100}%` }}
                  />
                </span>
                <span className="w-14 shrink-0 text-right text-xs tabular-nums text-ink">
                  {formatarHoras(b.horasAbertas)}
                </span>
              </li>
            );
          })}
        </ul>
      </Panel>
    ) : null;

  const painelEvolucao = (
    <Panel
      titulo="Evolução do backlog por marca"
      acao={
        <div className="flex flex-wrap items-center gap-1.5">
          <Segmented
            rotulo="Janela da tendência"
            opcoes={opcoesDoSeletor(REGRA)}
            valor={valorDoSeletor(janela, REGRA, personalizado)}
            aoEscolher={escolherJanela}
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
              className="min-h-[44px] rounded-md px-2 text-xs font-medium text-ink-muted underline underline-offset-4 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              {dadosDaTendencia ? "Ocultar dados" : "Ver dados"}
            </button>
          )}
        </div>
      }
    >
      {valorDoSeletor(janela, REGRA, personalizado) === OPCAO_PERSONALIZADA && (
        <div className="mb-3">
          <JanelaPersonalizada
            max={REGRA.max}
            diasAplicados={janela}
            aoAplicar={aplicarJanela}
          />
        </div>
      )}
      {janelaDescartada && (
        <Note tom="atencao" papel="status" className="mb-2">
          A janela do link era inválida e foi ignorada. Mostrando a janela
          padrão.
        </Note>
      )}
      {/* O periodo vem da RESPOSTA (`window_hours`, primeira e ultima
          hora com ponto), nao do seletor: e' o que de fato foi
          consultado. Some enquanto carrega para nao rotular o grafico
          novo com a janela antiga. */}
      {tendencia && estadoTend !== "carregando" && estadoTend !== "erro" && (
        <p className="mb-2 text-xs text-ink-muted" data-testid="exp-periodo-consultado">
          Período consultado:{" "}
          <span className="font-medium text-ink">
            {descreverJanelaEmHoras(tendencia.window_hours) ?? "não informado"}
          </span>
          {tendencia.from_hour && tendencia.to_hour
            ? ` · pontos de ${formatarHoraBrt(tendencia.from_hour)} a ${formatarHoraBrt(tendencia.to_hour)} (Brasília)`
            : " · nenhum ponto no período"}
        </p>
      )}
      {estadoTend === "carregando" && (
        <p role="status" className="py-12 text-center text-sm text-ink-muted">
          Carregando tendência…
        </p>
      )}
      {/* Erro e vazio sao UMA linha, nao um retangulo de grafico vazio: sem
          serie, o espaco nao carrega informacao nenhuma. O caso tipico e' a
          fotografia velha caindo fora da janela — por isso a linha diz de
          quando e' a ultima fotografia. */}
      {estadoTend === "erro" && (
        <p role="alert" className="py-2 text-sm text-crit-ink">
          Tendência indisponível agora.
        </p>
      )}
      {estadoTend === "vazia" && (
        <p role="status" className="py-2 text-sm text-ink-muted">
          Sem pontos na janela escolhida.
          {relogios?.efetivoEm ? ` Última fotografia: ${dataCurta(relogios.efetivoEm)}.` : ""}
        </p>
      )}
      {estadoTend === "parcial" && (
        <Note tom="atencao" papel="status" className="mb-2">
          Série parcial: alguma hora tem menos contas que o esperado. A linha se
          interrompe porque falta dado, não porque o backlog caiu.
        </Note>
      )}
      {temSerieNaTendencia && (
        <>
          {tendencia ? <EvolucaoPorMarca resposta={tendencia} /> : null}
          {dadosDaTendencia && (
            <div id="exp-tendencia-dados" className="mt-3 max-h-[320px] overflow-auto">
              <table className={CLASSE_TABELA}>
                <caption className="sr-only">
                  Backlog por marca, conta e hora — contas nunca são somadas
                </caption>
                <thead className={CLASSE_CABECALHO}>
                  <tr>
                    <th scope="col">Marca</th>
                    <th scope="col">Conta</th>
                    <th scope="col">Hora (Brasília)</th>
                    <th scope="col" className="text-right">Backlog</th>
                    <th scope="col" className="text-right">Acima de 48h</th>
                    <th scope="col" className="text-right">Travados</th>
                  </tr>
                </thead>
                <tbody>
                  {evolucao.flatMap((s) =>
                    s.pontos.map((p) => (
                      <tr
                        key={`${s.conta}-${p.hora}`}
                        className="border-t border-line"
                      >
                        <th scope="row" className="text-left font-normal text-ink">
                          {s.titulo}
                          {s.apelido ? <span className="ml-1 text-xs text-ink-faint">({s.apelido})</span> : null}
                        </th>
                        <td className="text-ink-faint">{s.conta}</td>
                        <td className="text-ink-muted">{formatarHoraBrt(p.hora)}</td>
                        <td className="text-right tabular-nums text-ink">
                          {numero(p.backlog)}
                        </td>
                        <td className="text-right tabular-nums text-ink">
                          {numero(p.over48h)}
                        </td>
                        <td className="text-right tabular-nums text-ink">
                          {numero(p.stalled)}
                        </td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Panel>
  );

  // 7. CONCENTRACAO POR CONTA — nas duas profundidades: para o gestor e'
  // "onde concentra"; para o operador, as contas do detalhe.
  const painelContas = (classe: string) => (
    <Panel
      className={classe}
      titulo="Concentração por marca"
      semPadding
      acao={<Disclosure rotulo="Como ler">{mapa.nota}</Disclosure>}
      rodape={
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {mapa.empilhavel ? (
            ["overdue", "due24h", "on_time", "sem_prazo"].map((chave) => {
              const rotulo = mapa.linhas[0]?.segmentos.find((s) => s.chave === chave)
                ?.rotulo;
              return rotulo ? (
                <ItemLegenda key={chave} cor={COR_SEGMENTO[chave]}>
                  {rotulo}
                </ItemLegenda>
              ) : null;
            })
          ) : (
            <>
              <ItemLegenda cor="bg-accent/70">Backlog</ItemLegenda>
              <ItemLegenda cor="bg-warn">Acima de 48h (sobrepõe)</ItemLegenda>
              <ItemLegenda cor="bg-crit">Travados (sobrepõe)</ItemLegenda>
            </>
          )}
        </ul>
      }
    >
      {/* UMA tabela, com a barra DENTRO dela. Antes eram duas leituras do
          MESMO dado empilhadas: uma lista de barras e, logo abaixo, a
          tabela com os mesmos numeros. A barra continua sendo desenhada
          pelo que o contrato autoriza (`mapa.empilhavel`), nunca por um
          `if` de canal espalhado aqui. */}
      <div className="overflow-x-auto px-4 py-2">
        <table className={CLASSE_TABELA}>
          <caption className="sr-only">
            Backlog por marca e conta, com as medidas do canal
          </caption>
          <thead className={CLASSE_CABECALHO}>
            <tr>
              <th scope="col">Marca</th>
              <th scope="col" className="w-[34%]">Distribuição</th>
              <th scope="col" className="text-right">Backlog</th>
              {temPrazo && <th scope="col" className="text-right">Vencidos</th>}
              <th scope="col" className="text-right">Acima de 48h</th>
              <th scope="col" className="text-right">Travados</th>
              <th scope="col" title={EXPLICACAO_FONTE}>Fonte</th>
            </tr>
          </thead>
          <tbody>
            {recorte.contas.map((a) => {
              const linha = mapa.linhas.find((l) => l.conta === a.shop_account);
              return (
                <tr key={a.shop_account} className="border-t border-line">
                  {/* Marca e' a identidade; a conta so' aparece, discreta,
                      quando difere do nome da marca (o seller_id do ML). */}
                  <th scope="row" className="text-left font-semibold text-ink">
                    {brandLabel(a.brand)}
                    {a.shop_account !== a.brand ? (
                      <span className="block text-xs font-normal text-ink-faint">
                        conta {a.shop_account}
                      </span>
                    ) : null}
                  </th>
                  <td>
                    {linha && (
                      <BarraDeRisco
                        linha={linha}
                        maximo={mapa.maximo}
                        empilhavel={mapa.empilhavel}
                      />
                    )}
                  </td>
                  <td className="text-right font-semibold tabular-nums text-ink">
                    {numero(a.backlog_count)}
                  </td>
                  {temPrazo && (
                    <td className="text-right tabular-nums text-ink">
                      {numero(a.overdue_count)}
                    </td>
                  )}
                  <td className="text-right tabular-nums text-ink">
                    {numero(a.over_48h_count)}
                  </td>
                  <td className="text-right tabular-nums text-ink">
                    {numero(a.stalled_count)}
                  </td>
                  <td className="text-ink-faint" title={EXPLICACAO_FONTE}>
                    {rotuloFonte(a.source_advanced)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );

  // 8. FILA OPERACIONAL — o instrumento de auditoria, so' no detalhe.
  const painelFila = (
    <Panel
      titulo="Fila operacional"
      semPadding
      acao={
        <Disclosure rotulo="Como ler">
          {temPrazo ? EXPLICACAO_LIMIAR_48H : EXPLICACAO_LIMIAR_48H_SEM_PRAZO}
        </Disclosure>
      }
    >
      <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-4 py-2">
        {SITUACOES.map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={filtros.situacao.includes(s)}
            onClick={() => alternarSituacao(s)}
            className={`min-h-[44px] min-w-[44px] rounded-md border px-2.5 text-xs font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
              filtros.situacao.includes(s)
                ? "border-accent bg-accent text-accent-on"
                : "border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink"
            }`}
          >
            {ROTULO_SITUACAO[s]}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-2 text-xs text-ink-muted">
          <span>Ordenar</span>
          <select
            value={filtros.orderBy}
            onChange={(e) =>
              setFiltros((f) => aplicarFiltro(f, { orderBy: e.target.value as Ordenacao }))
            }
            className="min-h-[44px] rounded-md border border-line bg-surface px-2 text-xs text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            {ORDENACOES.map((o) => (
              <option key={o} value={o}>{ROTULO_ORDEM[o]}</option>
            ))}
          </select>
        </label>
      </div>

      {estado === "fila_vazia_por_filtro" ? (
        <p role="status" className="py-8 text-center text-sm text-ink-muted">
          Nenhum pedido com os filtros atuais. O backlog total continua{" "}
          {numero(dados?.totals?.backlog_count ?? 0)}.
        </p>
      ) : (
        <>
          <div className="max-h-[560px] overflow-auto">
            <table className={CLASSE_TABELA}>
              <caption className="sr-only">Pedidos aguardando expedição</caption>
              <thead className={CLASSE_CABECALHO}>
                <tr>
                  {mostrarRef && <th scope="col" className="pl-4">Referência</th>}
                  <th scope="col" className={mostrarRef ? undefined : "pl-4"}>
                    Conta
                  </th>
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
                    className="border-t border-line hover:bg-raised"
                  >
                    {mostrarRef && (
                      <td className="pl-4 font-mono text-xs text-ink-faint">
                        {l.order_ref ?? "—"}
                      </td>
                    )}
                    <th
                      scope="row"
                      className={`text-left font-medium text-ink ${mostrarRef ? "" : "pl-4"}`}
                    >
                      {l.shop_account}
                    </th>
                    <td className="text-ink-muted">{l.brand}</td>
                    {temPrazo && (
                      <td className="text-ink-muted">{dataCurta(l.dispatch_deadline)}</td>
                    )}
                    <td>
                      <span
                        className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
                          COR_BADGE[l.deadline_status] ?? COR_BADGE.unavailable
                        }`}
                      >
                        {rotuloDeadline(l.deadline_status)}
                      </span>
                    </td>
                    <td className="text-right tabular-nums text-ink">
                      {formatarHoras(l.hours_open)}
                    </td>
                    <td className="text-ink-muted">
                      {rotuloIdade(l.operational_age_status)}
                    </td>
                    <td className="text-ink-faint">{l.carrier ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-2 text-xs text-ink-muted">
            <button
              type="button"
              disabled={filtros.offset === 0}
              onClick={() =>
                setFiltros((f) =>
                  aplicarFiltro(f, { offset: Math.max(0, f.offset - f.limit) }),
                )
              }
              className="min-h-[44px] min-w-[44px] rounded-md border border-line px-3 hover:text-ink disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
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
              className="min-h-[44px] min-w-[44px] rounded-md border border-line px-3 hover:text-ink disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Próxima
            </button>
          </div>
        </>
      )}
    </Panel>
  );

  // Frescor por marca fica no fim do detalhe: e' diagnostico, nao operacao.
  const painelFonte = (
    <Panel
      titulo="Fonte por marca"
      semPadding
      acao={
        <Disclosure rotulo="Como ler">
          Idade da fonte é quando o Data Mart foi lido. Idade do pedido mais antigo é
          contexto: um backlog legítimo sempre tem pedido velho, e isso não significa
          fonte desatualizada.
        </Disclosure>
      }
    >
      <div className="overflow-x-auto px-4 py-2">
        <table className={CLASSE_TABELA}>
          <caption className="sr-only">Frescor da fonte por marca</caption>
          <thead className={CLASSE_CABECALHO}>
            <tr>
              <th scope="col">Marca</th>
              <th scope="col">Estado da fonte</th>
              <th scope="col">Idade da fonte</th>
              <th scope="col">Pedido mais antigo</th>
              <th scope="col">Última leitura</th>
            </tr>
          </thead>
          <tbody>
            {frescor.map((f) => (
              <tr key={f.brand} className="border-t border-line">
                <th scope="row" className="text-left font-medium text-ink">
                  {f.brand}
                </th>
                <td className={f.alerta ? "text-warn-ink" : "text-ink-muted"}>
                  {f.rotulo}
                  {f.derivado && (
                    <span className="ml-1 text-xs text-ink-faint">(derivado do lote)</span>
                  )}
                </td>
                <td className="tabular-nums text-ink">{formatarHoras(f.sourceAgeHours)}</td>
                <td className="tabular-nums text-ink-faint">
                  {formatarHoras(f.oldestRowAgeHours)}
                </td>
                <td className="text-ink-faint">{dataCurta(f.watermark)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );

  // `div`, nao `main`: o landmark `main` e do AppShell (um por pagina). Dois
  // `main` quebram a navegacao por landmarks no leitor de tela — mesmo
  // motivo do `h2` no cabecalho compartilhado.
  return (
    <div
      className="mx-auto flex max-w-[1400px] flex-col gap-3 p-3 sm:p-4"
      aria-busy={carregando}
    >
      {/* 1-2. CABECALHO, FOTOGRAFIA E FRESCOR (compartilhado) ------------ */}
      {/* O canal fica NO TITULO, nao so' no seletor: numeros de uma loja sob
          o titulo de outra foi exatamente o defeito que o EXP-3C2-R/V
          corrigiu, e o titulo e' o ultimo lugar que o operador confere. */}
      <CabecalhoExpedicao
        canal={ROTULO_CANAL[filtros.channel]}
        seletorCanal={seletorCanal}
        fotografia={fotografia}
        visao={visao}
        aoTrocarVisao={aoTrocarVisao}
        definicao={DEFINICAO_FILA[filtros.channel]}
        filtroMarca={
          aoTrocarMarca ? (
            <SeletorMarca marca={marcaAtiva} marcasPresentes={marcasPresentes} aoTrocar={aoTrocarMarca} />
          ) : undefined
        }
      />

      {/* 3. QUALIDADE DO DADO, CONSOLIDADA (compartilhada) --------------- */}
      <BarraDeQualidade barra={qualidade} />

      {estado === "carregando" && (
        <p role="status" className="text-sm text-ink-muted">
          Carregando fotografia…
        </p>
      )}
      {estado === "canal_desligado" && (
        <p
          role="status"
          className="rounded-xl border border-line bg-surface px-4 py-6 text-sm text-ink-muted"
        >
          Este canal ainda não está disponível na API. A fotografia existe, mas a
          exposição depende de uma ativação coordenada.
        </p>
      )}
      {estado === "indisponivel_backend" && (
        <p role="alert" className="rounded-xl border border-line bg-surface p-4 text-sm text-ink">
          A API de Expedição está desligada. Nada a exibir.
        </p>
      )}
      {estado === "sem_snapshot" && (
        <p role="alert" className="rounded-xl border border-line bg-surface p-4 text-sm text-ink">
          Nenhuma fotografia publicada ainda.
        </p>
      )}
      {estado === "batch_inconsistente" && (
        <p
          role="alert"
          className="rounded-xl border border-crit/40 bg-crit-soft p-4 text-sm text-crit-ink"
        >
          A fotografia está inconsistente e não pode ser exibida. Nenhum número
          parcial é mostrado de propósito.
        </p>
      )}
      {estado === "erro_validacao" && (
        <p
          role="alert"
          className="rounded-xl border border-crit/40 bg-crit-soft p-4 text-sm text-crit-ink"
        >
          Filtro inválido. Ajuste a seleção e tente novamente.
        </p>
      )}
      {estado === "erro" && (
        <p
          role="alert"
          className="rounded-xl border border-crit/40 bg-crit-soft p-4 text-sm text-crit-ink"
        >
          Não foi possível carregar a Expedição agora.
        </p>
      )}
      {estado === "fotografia_vazia" && (
        <p role="status" className="rounded-xl border border-ok/35 bg-ok-soft p-4 text-sm text-ok-ink">
          Backlog zerado nesta fotografia. Nenhum pedido aguardando expedição.
        </p>
      )}

      {/* Marca escolhida sem conta neste canal: ausencia, nao backlog zero. */}
      {(estado === "ok" || estado === "fila_vazia_por_filtro") && recorte.semConta ? (
        <p role="status" className="rounded-xl border border-dashed border-line-strong bg-surface p-4 text-sm text-ink-muted">
          {brandLabel(recorte.marca ?? "")}{" "}
          {recorte.foraDaCobertura
            ? "não é coberta pela fonte deste canal — ausência de cobertura, não backlog zero."
            : "não tem conta nesta fotografia do canal."}
        </p>
      ) : null}
      {(estado === "ok" || estado === "fila_vazia_por_filtro") && !recorte.semConta &&
        (visao === "gestor" ? (
          <>
            {/* RISCO ATUAL -> PRAZO -> EVOLUCAO -> CONCENTRACAO, a mesma
                ordem dos tres canais (EXPEDICAO-UX-3). */}
            {secaoRisco}
            {secaoPrazo}
            {painelEvolucao}
            {painelContas("")}
            {aoTrocarVisao && (
              <ConviteAoDetalhe
                conteudo="Fila de pedidos com filtros, os mais críticos, os indicadores secundários e a fonte por marca ficam no detalhe operacional."
                aoAbrir={() => aoTrocarVisao("detalhe")}
              />
            )}
          </>
        ) : (
          <>
            {faixaPrincipal}
            <FaixaKpis rotulo="Indicadores secundários" secundaria>
              {secundarios.map((k) => (
                <CelulaKpi key={k.chave} kpi={k} destaque={false} />
              ))}
            </FaixaKpis>
            <div className="grid gap-3 lg:grid-cols-3">
              {painelMaisCriticos}
              {painelContas(timeline.length > 0 ? "lg:col-span-2" : "lg:col-span-3")}
            </div>
            {painelFila}
            {painelFonte}
          </>
        ))}
    </div>
  );
}
