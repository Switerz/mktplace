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
 *   4. risco atual (UI-EXPEDICAO-1): UM numero em destaque (vencidos; sem
 *      prazo publicado, acima de 48 h), com a barra de prazo e o "onde agir";
 *      os demais ao lado. Sem valor medido, a faixa de cartoes e a lista;
 *   5. prazo e jornada;
 *   6. "Backlog por marca": UMA tabela com a fila de agora de cada conta e a
 *      tendencia do periodo (o periodo fica no topo e comanda a pagina);
 *   7. concentracao por conta, com a fonte — so' no detalhe;
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
 * NUMERO PRIMEIRO. Ressalva que muda a leitura (desatualizada, inclui FBS,
 * limiar interno, recorte) vira selo ou "ⓘ" ao lado do titulo; metodologia
 * fica no catalogo (`expedicao-definicoes.ts`), fora da tela.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import BarraDeQualidade from "@/components/expedicao/BarraDeQualidade";
import EvolucaoPorMarca from "@/components/expedicao/EvolucaoPorMarca";
import type { LinhaDaFila } from "@/components/expedicao/EvolucaoPorMarca";
import LdrShopeePanel, { useLdrShopee } from "@/components/expedicao/LdrShopeePanel";
import {
  AVISO_FILA_COM_FBS,
  divisaoDaFila,
  textoDaReconciliacao,
  type DivisaoDaFila,
} from "@/lib/expedicao-shopee-ldr";
import CabecalhoExpedicao, { SeletorMarca } from "@/components/expedicao/CabecalhoExpedicao";
import RiscoEmDestaque from "@/components/expedicao/RiscoEmDestaque";
import TravadosDialog from "@/components/expedicao/TravadosDialog";
import { percentualDaFila, type AlvoTravados, type TipoLista } from "@/lib/expedicao-travados";
import {
  AtencaoAgora,
  ConviteAoDetalhe,
  FaixaKpis,
  SecaoGerencial,
  SeloLeitura,
} from "@/components/expedicao/SecoesExpedicao";
import InfoDica from "@/components/ui/InfoDica";
import { Ajuda, KpiComAjuda } from "@/components/expedicao/KpiComAjuda";
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
  rotuloDoProximoVencimento,
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
  ondeAgirNaFila,
  montarBarraDeQualidade,
  montarFrescor,
  montarKpisPrincipais,
  montarKpisSecundarios,
  montarMapaDeRisco,
  montarRelogios,
  montarSeries,
  montarTimeline,
  mostrarColunaReferencia,
  numeroDoPedido,
  paginaAtual,
  rotuloDeadline,
  rotuloDoNumero,
  rotuloIdade,
  type Canal,
  type FaixaPrazo,
  type Filtros,
  type Kpi,
  type LinhaRisco,
  type Ordenacao,
  type RespostaExpedicao,
  type RespostaTendencia,
  type Situacao,
  type Totais,
} from "@/lib/expedicao-contract";
import JanelaPersonalizada from "@/components/expedicao/JanelaPersonalizada";
import JornadaPedidos, { useJornada } from "@/components/expedicao/JornadaPedidos";
import LayoutExpedicao, {
  CLASSE_CABECALHO,
  CLASSE_RAIZ_EXPEDICAO,
  CLASSE_TABELA,
  CorpoGestor,
  EstadoSecao,
  TEXTO_ESTADO,
} from "@/components/expedicao/LayoutExpedicao";
import StateBlock, { RetryButton } from "@/components/ui/StateBlock";
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
import { formatarInteiro } from "@/lib/expedicao-formato";
import { buscarExpedicao } from "@/lib/expedicao-demo";
import { AJUDA_CANAL_SEM_PRAZO, chaveDoKpiDaFila, type ChaveMetrica } from "@/lib/expedicao-definicoes";

/** Janela da tendencia da fila, em DIAS. Horas so' na query. */
const REGRA = REGRA_JANELA.fila;

// EXPEDICAO-UX-2: a evolucao virou small multiples em SVG proprio
// (`EvolucaoPorMarca`). O recharts saiu do bloco — e, com ele, o import
// dinamico que existia so' para tira-lo do bundle inicial.

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

// EXPEDICAO-SHOPEE-LDR-1: a LDR operacional da Shopee nasce DESLIGADA. Com a
// flag off, a secao "Prazo" fica exatamente como antes — inclusive o aviso de
// que a LDR ainda nao e' calculada. Literal inline para o Next embutir.
const LDR_SHOPEE_LIGADA = process.env.NEXT_PUBLIC_EXPEDICAO_SHOPEE_LDR_ENABLED === "true";

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
  due_next_day: "Próximo dia de prazo",
};

/**
 * EXPEDICAO-VENCE-AMANHA — a lista que cada cartao do "Risco atual" abre e a
 * medida da fila PUBLICADA de onde ela vem (o numero do popup, ate' a lista
 * chegar).
 */
const LISTA_DO_KPI: Partial<Record<string, TipoLista>> = {
  overdue: "vencidos",
  due24h: "vence_24h",
  due_next_day: "vence_proximo",
  over48h: "acima_48h",
  stalled: "travados",
};
const MEDIDA_DA_LISTA: Record<TipoLista, keyof Totais> = {
  travados: "stalled_count",
  vencidos: "overdue_count",
  vence_24h: "due_within_24h_count",
  vence_proximo: "due_next_day_count",
  acima_48h: "over_48h_count",
};

/** O "ⓘ" de cada faixa da barra de prazo (so' a Shopee tem a barra). */
const AJUDA_DA_FAIXA: Record<FaixaPrazo["chave"], ChaveMetrica> = {
  overdue: "shopee.overdue",
  due24h: "shopee.due24h",
  on_time: "shopee.no_prazo",
  sem_prazo: "shopee.sem_prazo",
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

// T-0010: CLASSE_TABELA e CLASSE_CABECALHO vem do esqueleto comum
// (`LayoutExpedicao`), o mesmo do TikTok.

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

// T-0010: o numero da fila usa o formato unico da Expedicao.
const numero = formatarInteiro;

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

function CelulaKpi({
  kpi,
  destaque,
  canal,
  numeroGrande = false,
}: {
  kpi: Kpi;
  destaque: boolean;
  canal: Canal;
  /** Numero na escala grande sem a nota visivel (cartoes ao lado do destaque). */
  numeroGrande?: boolean;
}) {
  const texto = kpi.valorTexto ?? (kpi.valor === null ? "—" : numero(kpi.valor));
  return (
    <KpiComAjuda
      ajuda={chaveDoKpiDaFila(canal, kpi.chave)}
      rotulo={kpi.rotulo}
      valor={texto}
      // So' o numero na tela; a nota vira tooltip e a definicao, o "ⓘ".
      contexto={kpi.nota}
      tom={tomDoKpi(kpi)}
      destaque={destaque || numeroGrande}
    />
  );
}

/**
 * Pedidos FBS — responsabilidade logistica da SHOPEE, nunca risco do vendedor.
 * Fica a' parte do "Risco atual", em faixa secundaria e sem tom de alerta: o
 * vendedor nao age sobre eles. A conferencia mostra as parcelas e o total da
 * MESMA leitura; o total nao vira cartao.
 */
function BlocoFbs({ divisao }: { divisao: DivisaoDaFila }) {
  const f = divisao.fbs;
  return (
    <section aria-label="Pedidos FBS" className="mt-3" data-testid="exp-fila-fbs">
      <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-faint">
        Pedidos FBS — despacho da Shopee
      </h4>
      <FaixaKpis rotulo="Pedidos FBS" secundaria>
        <KpiComAjuda ajuda="shopee.fbs" rotulo="Em aberto (FBS)" valor={numero(f.backlog_count)} />
        <KpiComAjuda ajuda="shopee.fbs" rotulo="Vencidos (FBS)" valor={numero(f.overdue_count)} />
        <KpiComAjuda ajuda="shopee.fbs" rotulo="Acima de 48 h (FBS)" valor={numero(f.over_48h_count)} />
        <KpiComAjuda ajuda="shopee.fbs" rotulo="Travados (FBS)" valor={numero(f.stalled_count)} />
      </FaixaKpis>
      <p className="mt-2 text-xs text-ink-muted" data-testid="exp-fila-conferencia">
        {textoDaReconciliacao(divisao)}
      </p>
    </section>
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
        <div className="tc-barra flex h-full" style={{ width: `${largura}%` }}>
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
            className="tc-barra h-full rounded bg-accent/70"
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
  // EXPEDICAO-TRAVADOS-1: quais pedidos estao travados (popup).
  const [alvoTravados, setAlvoTravados] = useState<AlvoTravados | null>(null);

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
    buscarExpedicao(`${API}/api/v1/expedicao?${construirQuery(filtros)}`, {
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
  // QA T-0010: erro da tendencia com "Tentar novamente".
  const [tentativaTendencia, setTentativaTendencia] = useState(0);
  const chaveTendVigente = useRef(chaveTendencia);
  chaveTendVigente.current = chaveTendencia;

  useEffect(() => {
    if (!urlPronta) return;
    const minhaChave = chaveTendencia;
    let vivo = true;
    setCarregandoTendencia(true);
    setErroTendencia(false);
    void tentativaTendencia; // nova tentativa refaz a busca com a mesma chave
    buscarExpedicao(`${API}/api/v1/expedicao/trend?${chaveTendencia}`, {
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
  }, [chaveTendencia, urlPronta, tentativaTendencia]);

  // EXPEDICAO-SHOPEE-LDR-1: a rota da LDR tambem traz a fila aberta separada
  // em vendedor x FBS. UMA leitura, repartida entre o "Risco atual" e o painel.
  // T-0010: a jornada (fila aberta por etapa + coorte), recortada pela marca
  // no SERVIDOR. Nada aqui soma a fila paginada.
  // UI-EXPEDICAO-1: o periodo do topo comanda a pagina — a coorte da jornada
  // e a LDR leem a MESMA janela da tendencia. A fila aberta e' "agora".
  // T-0015: a fila aberta do ML tambem tem etapa (substatus do envio), entao
  // a jornada e' buscada nos dois canais; o `days` so' vai onde ha coorte.
  const jornada = useJornada(API, filtros.channel, marcaAtiva, { dias: janela });

  const ldr = useLdrShopee(API, marcaAtiva, LDR_SHOPEE_LIGADA && filtros.channel === "shopee", janela);

  const temFiltro =
    filtros.brands.length > 0 || filtros.accounts.length > 0 || filtros.situacao.length > 0;
  const estado = estadoDaTela({ carregando, erroHttp, resposta: dados, temFiltro });
  const relogios = dados ? montarRelogios(dados) : null;
  // Recorte por marca, por SELECAO das linhas que a API ja' publica.
  const recorte = recortarPorMarca(dados, marcaAtiva);
  // EXPEDICAO-VENCE-AMANHA: o rotulo do proximo dia de prazo vem da fotografia.
  const fotografiaDoPrazo = dados?.snapshot ?? null;
  const principais = montarKpisPrincipais(recorte.totais, recorte.frescor, filtros.channel, fotografiaDoPrazo);
  const secundarios = montarKpisSecundarios(recorte.totais, recorte.frescor, filtros.channel, fotografiaDoPrazo);
  // FBS fora do risco do vendedor. Com a divisao publicada, o "Risco atual" da
  // Shopee e' a fila ACIONAVEL do vendedor; FBS aparece a' parte. Sem ela, a
  // tela segue com a fila publicada E diz que ela inclui FBS (nunca zera nada).
  const divisao = filtros.channel === "shopee" ? divisaoDaFila(ldr.resposta) : null;
  const totaisRisco = divisao ? divisao.vendedor : recorte.totais;
  const contasRisco = divisao ? divisao.contasVendedor : recorte.contas;
  const risco = montarRiscoAtual(totaisRisco, recorte.frescor, filtros.channel, fotografiaDoPrazo).map((k) =>
    divisao && k.chave === "backlog"
      ? { ...k, rotulo: "Fila acionável do vendedor" }
      : // A divisao nao separa o proximo dia de prazo: vale a fila publicada,
        // e o cartao diz que ela inclui FBS (nunca zera nada).
        divisao && k.chave === "due_next_day"
        ? { ...k, valor: recorte.totais?.due_next_day_count ?? null, nota: "Fila publicada, com pedidos FBS." }
        : k,
  );
  const frescor = montarFrescor(recorte.frescor);
  const avisos = avisosDeCobertura(dados?.coverage ?? null);
  // A qualidade do dado e' do CANAL: a barra nao some quando se filtra marca.
  const qualidade = montarBarraDeQualidade(relogios, avisos, montarFrescor(dados?.freshness ?? []));
  const mapa = montarMapaDeRisco(recorte.contas, filtros.channel);
  const marcasPresentes = [...new Set((dados?.accounts ?? []).map((a) => a.brand))];
  const faixasPrazo = faixasDePrazo(totaisRisco);
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

  // QA T-0010 (M4): fila zerada nao esconde a pagina — como o TikTok com
  // base 0, o corpo aparece (zeros medidos, jornada, evolucao) sob o aviso.
  const mostraCorpo =
    estado === "ok" || estado === "fila_vazia_por_filtro" || estado === "fotografia_vazia";

  // Nova tentativa: um objeto de filtros novo, com a MESMA chave, refaz a
  // busca (o efeito depende de `filtros`) sem mudar a requisicao.
  /** O "ⓘ" de uma medida da fila, no canal atual (cabecalhos de tabela). */
  const ajudaDaFila = (kpi: string) => {
    const chave = chaveDoKpiDaFila(filtros.channel, kpi);
    return chave ? <Ajuda chave={chave} /> : null;
  };

  const tentarDeNovo = useCallback(() => setFiltros((f) => ({ ...f })), []);

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
  const atencao = montarAtencaoDaFila(totaisRisco, contasRisco, filtros.channel);
  // "Agora" e' o instante da fotografia. Com ela velha, a secao diz isso em
  // vez de deixar o gestor agir sobre o passado sem saber. Com a divisao, a
  // fotografia do risco e' a da divisao (outra leitura, outro instante).
  const quando = divisao
    ? `Fila do vendedor segundo a leitura de ${dataCurta(divisao.efetivoEm)}${
        divisao.velha ? " — desatualizada: confirme antes de agir" : ""
      }. FBS fica fora: quem despacha é a Shopee.`
    : relogios?.efetivoEm
      ? `Segundo a fotografia de ${dataCurta(relogios.efetivoEm)}${
          relogios.snapshotVelho ? " — desatualizada: confirme antes de agir" : ""
        }.`
      : undefined;
  const fotografiaVelha = divisao ? divisao.velha : Boolean(relogios?.snapshotVelho);

  // UI-EXPEDICAO-1 — UM numero manda no "Risco atual": vencidos (prazo do
  // marketplace); no canal sem prazo publicado, acima de 48 h. Sem valor
  // medido (marca de varias contas sem divisao), volta a faixa de cartoes.
  const chaveDestaque = temPrazo ? "overdue" : "over48h";
  const kpiDestaque = risco.find((k) => k.chave === chaveDestaque);
  const destaque =
    kpiDestaque && kpiDestaque.valor !== null && kpiDestaque.valorTexto === undefined
      ? kpiDestaque
      : null;
  const secundariosDoRisco = risco
    .filter((k) => k.chave !== chaveDestaque && k.chave !== "backlog" && k.chave !== "marcas_alerta")
    // Rotulo curto no cartao: "limiar interno da Torre" fica no "ⓘ".
    .map((k) => (k.chave === "over48h" ? { ...k, rotulo: "Acima de 48 h" } : k));
  // "Onde agir": so' a marca que concentra — os numeros ja' estao no destaque.
  const ondeAgir = ondeAgirNaFila(totaisRisco, contasRisco, filtros.channel);

  /**
   * Barra de situacao de prazo. Dentro do destaque, a legenda nao repete os
   * numeros que os cartoes ja' mostram (vencidos, 24 h): so' os que so' ela tem.
   */
  const temBarraDePrazo = Boolean(faixasPrazo && totaisRisco && totaisRisco.backlog_count > 0);
  const barraDePrazo = (dentroDoDestaque: boolean) =>
    temBarraDePrazo && faixasPrazo && totaisRisco ? (
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
              style={{ width: `${(f.valor / (totaisRisco?.backlog_count || 1)) * 100}%` }}
            />
          ))}
        </div>
        <ul
          className={
            dentroDoDestaque
              ? "mt-1.5 flex flex-wrap gap-x-3 gap-y-1"
              : "mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4"
          }
        >
          {faixasPrazo.map((f) => (
            <li key={f.chave} className="flex items-center gap-1.5">
              <span aria-hidden="true" className={`inline-block h-2.5 w-2.5 shrink-0 rounded-sm ${COR_SEGMENTO[f.chave]}`} />
              <span className="text-xs text-ink-muted">{f.rotulo}</span>
              {/* EXPEDICAO-VENCE-AMANHA: o que cada faixa trata, no hover. */}
              <Ajuda chave={AJUDA_DA_FAIXA[f.chave]} />
              {dentroDoDestaque && (f.chave === "overdue" || f.chave === "due24h") ? null : (
                <span
                  className={`text-sm font-semibold tabular-nums text-ink ${dentroDoDestaque ? "" : "ml-auto"}`}
                >
                  {numero(f.valor)}
                </span>
              )}
            </li>
          ))}
        </ul>
      </>
    ) : null;

  // 11. Tabela unica de marcas: a fotografia de cada conta + a tendencia.
  const linhasDaFila: LinhaDaFila[] = recorte.contas.map((a) => {
    const irmas = recorte.contas.filter((x) => x.brand === a.brand);
    const linha = mapa.linhas.find((l) => l.conta === a.shop_account);
    return {
      conta: a.shop_account,
      titulo: brandLabel(a.brand),
      apelido: irmas.length > 1 ? `conta ${irmas.indexOf(a) + 1} de ${irmas.length}` : null,
      backlog: a.backlog_count,
      vencidos: temPrazo ? a.overdue_count : null,
      pctVencidos: temPrazo ? percentualDaFila(a.overdue_count, a.backlog_count) : null,
      aoVerVencidos:
        temPrazo && a.overdue_count > 0
          ? () =>
              setAlvoTravados({
                titulo: `Vencidos · ${brandLabel(a.brand)}`,
                marca: a.brand,
                conta: a.shop_account,
                travados: a.overdue_count,
                backlog: a.backlog_count,
                lista: "vencidos",
              })
          : undefined,
      // EXPEDICAO-TABELA-AMANHA: os que vencem no proximo dia de prazo, por conta.
      venceProximo: temPrazo ? (a.due_next_day_count ?? null) : null,
      pctVenceProximo:
        temPrazo && a.due_next_day_count != null ? percentualDaFila(a.due_next_day_count, a.backlog_count) : null,
      aoVerVenceProximo:
        temPrazo && (a.due_next_day_count ?? 0) > 0
          ? () =>
              setAlvoTravados({
                titulo: `${rotuloDoProximoVencimento(fotografiaDoPrazo?.next_due_date, fotografiaDoPrazo?.effective_at)} · ${brandLabel(a.brand)}`,
                marca: a.brand,
                conta: a.shop_account,
                travados: a.due_next_day_count ?? 0,
                backlog: a.backlog_count,
                lista: "vence_proximo",
              })
          : undefined,
      acima48h: a.over_48h_count,
      travados: a.stalled_count,
      pctTravados: percentualDaFila(a.stalled_count, a.backlog_count),
      aoVerTravados:
        a.stalled_count > 0
          ? () =>
              setAlvoTravados({
                titulo: `Travados · ${brandLabel(a.brand)}`,
                marca: a.brand,
                conta: a.shop_account,
                travados: a.stalled_count,
                backlog: a.backlog_count,
              })
          : undefined,
      // Prazo: a barra e' a fila DA MARCA em 100% (quanto dela esta' vencido,
      // vence em 24 h ou no prazo); o tamanho da fila ja' esta' na coluna.
      barra: linha ? (
        <BarraDeRisco
          linha={linha}
          maximo={mapa.empilhavel ? linha.backlog : mapa.maximo}
          empilhavel={mapa.empilhavel}
        />
      ) : null,
    };
  });

  /**
   * "ver quais" de um cartao do "Risco atual": abre a lista dos pedidos dele
   * (EXPEDICAO-TRAVADOS-1 para travados; EXPEDICAO-VENCE-AMANHA para os demais).
   * A lista vem da fila PUBLICADA, entao o numero inicial tambem.
   */
  const botaoVerQuais = (k: Kpi, tipo: TipoLista) =>
    (k.valor ?? 0) > 0 ? (
      <button
        type="button"
        onClick={() =>
          setAlvoTravados({
            titulo: marcaAtiva ? `${k.rotulo} · ${brandLabel(marcaAtiva)}` : k.rotulo,
            marca: marcaAtiva,
            conta: null,
            travados: recorte.totais?.[MEDIDA_DA_LISTA[tipo]] ?? k.valor ?? 0,
            backlog: recorte.totais?.backlog_count ?? totaisRisco?.backlog_count ?? 0,
            incluiFbs: divisao !== null,
            lista: tipo,
          })
        }
        className="-my-3 inline-flex min-h-[44px] items-center font-semibold text-ink underline underline-offset-4 hover:text-accent"
      >
        ver quais
      </button>
    ) : null;

  /** Cartao ao lado do destaque: o numero, o % da fila e "ver quais". */
  const cartaoComLista = (k: Kpi, tipo: TipoLista) => {
    // O % usa a fila do proprio numero: o proximo dia de prazo vem da fila
    // publicada mesmo com a divisao vendedor x FBS.
    const base = divisao && tipo === "vence_proximo" ? recorte.totais?.backlog_count : totaisRisco?.backlog_count;
    const pct = percentualDaFila(k.valor ?? 0, base ?? 0);
    return (
      <KpiComAjuda
        key={k.chave}
        ajuda={chaveDoKpiDaFila(filtros.channel, k.chave)}
        rotulo={k.rotulo}
        valor={numero(k.valor ?? 0)}
        contexto={k.nota}
        tom={tomDoKpi(k)}
        destaque
        comparacao={
          <>
            {pct ? `${pct} da fila` : null}
            {pct && (k.valor ?? 0) > 0 ? " · " : null}
            {botaoVerQuais(k, tipo)}
          </>
        }
      />
    );
  };

  /** Legenda da barra por conta — a mesma na tabela unica e na do detalhe. */
  const legendaDaBarra = (
    <ul className="flex flex-wrap gap-x-4 gap-y-1">
      {mapa.empilhavel ? (
        (["overdue", "due24h", "on_time", "sem_prazo"] as const).map((chave) => {
          const rotulo = mapa.linhas[0]?.segmentos.find((s) => s.chave === chave)
            ?.rotulo;
          return rotulo ? (
            <ItemLegenda key={chave} cor={COR_SEGMENTO[chave]}>
              {rotulo}
              <Ajuda chave={AJUDA_DA_FAIXA[chave]} />
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
  );

  // O periodo vem da RESPOSTA (`window_hours`, primeira e ultima hora com
  // ponto), nao do seletor: e' o que de fato foi consultado. Some enquanto
  // carrega para nao rotular a tela nova com a janela antiga.
  const periodoConsultado =
    tendencia && estadoTend !== "carregando" && estadoTend !== "erro"
      ? `Período consultado: ${descreverJanelaEmHoras(tendencia.window_hours) ?? "não informado"}${
          tendencia.from_hour && tendencia.to_hour
            ? ` · pontos de ${formatarHoraBrt(tendencia.from_hour)} a ${formatarHoraBrt(tendencia.to_hour)} (Brasília)`
            : " · nenhum ponto no período"
        }`
      : null;

  // 12. PERIODO NO TOPO: comanda tendencia, jornada (coorte) e LDR. So' na
  // visao do gestor — o detalhe operacional e' a fila de agora.
  const controlePeriodo = (
    <div className="flex flex-wrap items-center gap-2" data-testid="exp-periodo">
      <span aria-hidden="true" className="text-xs text-ink-faint">
        Período
      </span>
      <Segmented
        rotulo="Período"
        opcoes={opcoesDoSeletor(REGRA)}
        valor={valorDoSeletor(janela, REGRA, personalizado)}
        aoEscolher={escolherJanela}
      />
      {periodoConsultado ? <InfoDica texto={periodoConsultado} /> : null}
      {valorDoSeletor(janela, REGRA, personalizado) === OPCAO_PERSONALIZADA && (
        <JanelaPersonalizada max={REGRA.max} diasAplicados={janela} aoAplicar={aplicarJanela} />
      )}
      {janelaDescartada && (
        <Note tom="atencao" papel="status">
          Janela do link inválida; usando a padrão.
        </Note>
      )}
    </div>
  );

  const faixaPrincipal = (
    <FaixaKpis rotulo="Indicadores principais">
      {principais.map((k) => (
        <CelulaKpi key={k.chave} kpi={k} canal={filtros.channel} destaque />
      ))}
    </FaixaKpis>
  );

  // EXPEDICAO-UX-3 — "Risco atual": os indicadores que o CANAL sustenta.
  // A divisao vem recortada pela marca no SERVIDOR (rota da LDR), entao o aviso
  // de "a tela nao soma contas" so' vale sem ela.
  const avisoRecorte = recorte.variasContas && !divisao ? (
    <Note tom="neutro" papel="status">
      {brandLabel(recorte.marca ?? "")}: {recorte.contas.length} contas, sem total — veja “Backlog por marca”.
    </Note>
  ) : null;

  const secaoRisco = (
    <SecaoGerencial titulo="Risco atual"
      // UI-EXPEDICAO-1: de quando e' a fila vai para o "ⓘ". Pedido do usuario
      // (30/10/2026): o selo "agora" saiu — o cabecalho ja' mostra a hora da
      // fotografia. Fica so' o aviso "desatualizada", que nunca se esconde.
      dica={quando}
      selo={
        <>
          {fotografiaVelha ? <SeloLeitura tom="atencao">desatualizada</SeloLeitura> : null}
          {filtros.channel === "shopee" && !divisao && recorte.totais ? (
            <span data-testid="exp-aviso-fbs" className="inline-flex items-center gap-1">
              <SeloLeitura tom="atencao">inclui FBS</SeloLeitura>
              <InfoDica texto={AVISO_FILA_COM_FBS} />
            </span>
          ) : null}
        </>
      }
    >
      {avisoRecorte}
      {destaque ? (
        <RiscoEmDestaque
          principal={destaque}
          backlog={risco.find((k) => k.chave === "backlog")}
          marcasAlerta={risco.find((k) => k.chave === "marcas_alerta")}
          ajuda={ajudaDaFila(chaveDestaque)}
          acao={LISTA_DO_KPI[destaque.chave] ? botaoVerQuais(destaque, LISTA_DO_KPI[destaque.chave]!) : null}
          secundarios={secundariosDoRisco.map((k) => {
            const lista = LISTA_DO_KPI[k.chave];
            return lista && k.valor !== null ? (
              cartaoComLista(k, lista)
            ) : (
              <CelulaKpi key={k.chave} kpi={k} canal={filtros.channel} destaque={false} numeroGrande />
            );
          })}
          // Sem prazo publicado (Mercado Livre), a barra seria 100% "sem prazo".
          prazo={temPrazo ? barraDePrazo(true) : undefined}
          ondeAgir={ondeAgir}
        />
      ) : (
        <FaixaKpis rotulo="Risco atual" colunas={risco.length === 6 ? 6 : 4}>
          {risco.map((k) => (
            <CelulaKpi key={k.chave} kpi={k} canal={filtros.channel} destaque />
          ))}
        </FaixaKpis>
      )}
      {divisao ? <BlocoFbs divisao={divisao} /> : null}
      {/* Com o destaque, a lista repetiria os numeros dele: vira o "onde agir". */}
      {destaque ? null : (
        <AtencaoAgora
          itens={atencao}
          semItens={recorte.variasContas ? "Leitura por conta: veja “Backlog por marca”." : undefined}
        />
      )}
    </SecaoGerencial>
  );

  // "Prazo": a Shopee publica `ship_by_date`; o Mercado Livre, nao.
  // EXPEDICAO-TRAVADOS-1: sem LDR e com a barra ja' no destaque, a secao
  // "Prazo" so' teria um paragrafo dizendo o que falta — sai da tela.
  const secaoPrazo = temPrazo && !LDR_SHOPEE_LIGADA && destaque && temBarraDePrazo ? null : temPrazo ? (
    // Sem LDR, o que falta medir vira o "ⓘ" do titulo.
    <SecaoGerencial titulo="Prazo" dica={LDR_SHOPEE_LIGADA ? undefined : SEM_LDR_SHOPEE}>
      {/* UI-EXPEDICAO-1: com o destaque, a barra de prazo ja' esta' no topo. */}
      {destaque && temBarraDePrazo ? null : (
        <Panel
          titulo={divisao ? "Fila do vendedor por situação de prazo" : "Pedidos em fila por situação de prazo"}
          acao={<Ajuda chave="shopee.prazo_situacao" />}
        >
          {barraDePrazo(false) ?? (
            <p className="text-sm text-ink-muted">
              {recorte.variasContas
                ? "Situação de prazo por conta em “Backlog por marca”."
                : "Sem pedidos em fila nesta fotografia."}
            </p>
          )}
        </Panel>
      )}
      {LDR_SHOPEE_LIGADA ? <LdrShopeePanel ldr={ldr} marca={marcaAtiva} /> : null}
    </SecaoGerencial>
  ) : (
    <SecaoGerencial titulo="Prazo" dica={SEM_PRAZO_CANAL_ML}>
      <Panel titulo="Prazo de despacho" acao={<Ajuda chave={AJUDA_CANAL_SEM_PRAZO.prazo} />}>
        {envelhecimento ? (
          <dl className="mt-3 grid gap-2 sm:grid-cols-2">
            {/* Com o destaque, "acima de 48 h" ja' e' o numero do topo. */}
            {destaque ? null : (
            <div className="rounded-lg bg-raised px-3 py-2">
              <dt className="text-xs text-ink-muted">
                Acima de 48 h{" "}
                <Ajuda chave={`${filtros.channel}.over48h`} />
              </dt>
              <dd className="text-sm text-ink">
                <span className="text-xl font-semibold tabular-nums">{numero(envelhecimento.acima48h)}</span>{" "}
                de {numero(envelhecimento.backlog)} pedidos em fila
              </dd>
            </div>
            )}
            <div className="rounded-lg bg-raised px-3 py-2">
              <dt className="text-xs text-ink-muted">
                Pedido mais antigo em fila <Ajuda chave={AJUDA_CANAL_SEM_PRAZO.maisAntigo} />
              </dt>
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
        // O recorte muda a leitura: nao e' o backlog inteiro.
        acao={<InfoDica texto={`Os ${TIMELINE_MAX_LINHAS} mais antigos da página atual, não o backlog inteiro.`} />}
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
                    className={`tc-barra block h-full rounded ${
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
      titulo="Backlog por marca"
      acao={
        <div className="flex flex-wrap items-center gap-1.5">
          {/* Fila publicada por conta: nao separa FBS, ao contrario do "Risco atual". */}
          {filtros.channel === "shopee" ? <SeloLeitura tom="atencao">inclui FBS</SeloLeitura> : null}
          <InfoDica texto={mapa.nota} />
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
      rodape={legendaDaBarra}
    >
      {estadoTend === "carregando" && (
        <p role="status" className="mb-2 text-xs text-ink-muted">
          Carregando tendência…
        </p>
      )}
      {/* Erro e vazio sao UMA linha, nao um retangulo de grafico vazio: sem
          serie, o espaco nao carrega informacao nenhuma. O caso tipico e' a
          fotografia velha caindo fora da janela — por isso a linha diz de
          quando e' a ultima fotografia. */}
      {estadoTend === "erro" && (
        <EstadoSecao
          tipo="erro"
          titulo="Tendência indisponível agora."
          aoTentarDeNovo={() => setTentativaTendencia((n) => n + 1)}
        />
      )}
      {estadoTend === "vazia" && (
        <p role="status" className="py-2 text-sm text-ink-muted">
          Sem pontos na janela escolhida.
          {relogios?.efetivoEm ? ` Última fotografia: ${dataCurta(relogios.efetivoEm)}.` : ""}
        </p>
      )}
      {estadoTend === "parcial" && (
        <Note tom="atencao" papel="status" className="mb-2">
          Série parcial: faltam contas em algumas horas.
        </Note>
      )}
      {/* A fotografia manda nas linhas: a tabela existe mesmo sem tendencia. */}
      <EvolucaoPorMarca
        resposta={temSerieNaTendencia ? tendencia : null}
        fila={linhasDaFila}
        rotuloBarra={temPrazo ? "Prazo" : "Distribuição"}
        rotuloProximo={rotuloDoProximoVencimento(fotografiaDoPrazo?.next_due_date, fotografiaDoPrazo?.effective_at, "Vencem")}
        ajudas={{
          vencidos: ajudaDaFila("overdue"),
          venceProximo: ajudaDaFila("due_next_day"),
          acima48h: ajudaDaFila("over48h"),
          travados: ajudaDaFila("stalled"),
        }}
      />
      {temSerieNaTendencia && (
        <>
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
      // A tabela e' a fila PUBLICADA, que nao separa FBS: o selo avisa, para
      // que ninguem compare com o "Risco atual" do vendedor sem saber.
      acao={
        <span className="flex items-center gap-1.5">
          {filtros.channel === "shopee" ? <SeloLeitura tom="atencao">inclui FBS</SeloLeitura> : null}
          <InfoDica texto={mapa.nota} />
        </span>
      }
      rodape={legendaDaBarra}
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
              {temPrazo && (
                <th scope="col" className="text-right">
                  Vencidos {ajudaDaFila("overdue")}
                </th>
              )}
              <th scope="col" className="text-right">
                Acima de 48h {ajudaDaFila("over48h")}
              </th>
              <th scope="col" className="text-right">
                Travados {ajudaDaFila("stalled")}
              </th>
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
      acao={<InfoDica texto={temPrazo ? EXPLICACAO_LIMIAR_48H : EXPLICACAO_LIMIAR_48H_SEM_PRAZO} />}
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
                  {mostrarRef && <th scope="col" className="pl-4">{rotuloDoNumero(filtros.channel)}</th>}
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
                    key={`${l.shop_account}-${numeroDoPedido(l) ?? i}`}
                    className="border-t border-line hover:bg-raised"
                  >
                    {mostrarRef && (
                      <td className="pl-4 font-mono text-xs text-ink-faint">
                        {numeroDoPedido(l) ?? "—"}
                      </td>
                    )}
                    <th
                      scope="row"
                      className={`text-left font-medium text-ink ${mostrarRef ? "" : "pl-4"}`}
                    >
                      {l.shop_account}
                    </th>
                    <td className="text-ink-muted">{brandLabel(l.brand)}</td>
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
      acao={<InfoDica texto="Pedido antigo não indica fonte desatualizada." />}
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
                  {brandLabel(f.brand)}
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
    // `div`, nao `main`: o landmark `main` e' do AppShell (um por pagina).
    <div className={CLASSE_RAIZ_EXPEDICAO} aria-busy={carregando}>
    {/* `key` por alvo: abrir outra marca comeca do zero, sem a lista anterior. */}
    <TravadosDialog
      key={
        alvoTravados
          ? `${alvoTravados.lista ?? "travados"}-${alvoTravados.marca ?? "todas"}-${alvoTravados.conta ?? "todas"}`
          : "fechado"
      }
      api={API}
      canal={filtros.channel}
      alvo={alvoTravados}
      aoFechar={() => setAlvoTravados(null)}
    />
    <LayoutExpedicao
      // 1-2. CABECALHO, FOTOGRAFIA, FRESCOR E FILTROS (compartilhado). O canal
      // fica NO TITULO, nao so' no seletor: numeros de uma loja sob o titulo de
      // outra foi o defeito que o EXP-3C2-R/V corrigiu.
      cabecalho={
        <CabecalhoExpedicao
          canal={ROTULO_CANAL[filtros.channel]}
          seletorCanal={seletorCanal}
          fotografia={fotografia}
          visao={visao}
          aoTrocarVisao={aoTrocarVisao}
          definicao={DEFINICAO_FILA[filtros.channel]}
          controles={visao === "gestor" ? controlePeriodo : undefined}
          filtroMarca={
            aoTrocarMarca ? (
              <SeletorMarca marca={marcaAtiva} marcasPresentes={marcasPresentes} aoTrocar={aoTrocarMarca} />
            ) : undefined
          }
        />
      }
      // 3. QUALIDADE DO DADO, CONSOLIDADA (compartilhada)
      qualidade={<BarraDeQualidade barra={qualidade} />}
      // Os estados sao os MESMOS componentes do TikTok (T-0010).
      estado={
        <>
          {estado === "carregando" && <StateBlock tipo="carregando" titulo={TEXTO_ESTADO.carregando} />}
          {estado === "canal_desligado" && (
            <StateBlock tipo="indisponivel" titulo="Este canal ainda não está disponível na API." />
          )}
          {estado === "indisponivel_backend" && (
            <StateBlock tipo="indisponivel" titulo="A API de Expedição está desligada. Nada a exibir." />
          )}
          {estado === "sem_snapshot" && (
            <StateBlock tipo="indisponivel" titulo="Nenhuma fotografia publicada ainda." />
          )}
          {estado === "batch_inconsistente" && (
            <StateBlock tipo="erro" titulo="A fotografia está inconsistente e não pode ser exibida." />
          )}
          {estado === "erro_validacao" && (
            <StateBlock tipo="erro" titulo="Filtro inválido. Ajuste a seleção e tente novamente." />
          )}
          {estado === "erro" && (
            <StateBlock
              tipo="erro"
              titulo={TEXTO_ESTADO.erro}
              acao={<RetryButton onClick={tentarDeNovo} />}
            />
          )}
          {estado === "fotografia_vazia" && (
            <StateBlock tipo="vazio" titulo={TEXTO_ESTADO.vazio} compacto />
          )}
        </>
      }
    >
      {/* Marca escolhida sem conta neste canal: ausencia, nao backlog zero. */}
      {mostraCorpo && recorte.semConta ? (
        <p role="status" className="rounded-xl border border-dashed border-line-strong bg-surface p-4 text-sm text-ink-muted">
          {brandLabel(recorte.marca ?? "")}{" "}
          {recorte.foraDaCobertura
            ? "não é coberta pela fonte deste canal — não é backlog zero."
            : "não tem conta nesta fotografia do canal."}
        </p>
      ) : null}
      {mostraCorpo && !recorte.semConta &&
        (visao === "gestor" ? (
          // RISCO ATUAL -> PRAZO -> JORNADA -> EVOLUCAO -> CONCENTRACAO: a
          // ordem e' do esqueleto, a mesma dos tres canais.
          <CorpoGestor
            risco={secaoRisco}
            prazo={secaoPrazo}
            // Compacta: a fila aberta so' com etapa real (sem repetir o
            // "Risco atual") e a coorte do periodo quando existe.
            jornada={<JornadaPedidos carga={jornada.carga} aoTentarDeNovo={jornada.tentarDeNovo} marca={marcaAtiva} compacta />}
            evolucao={painelEvolucao}
            convite={
              aoTrocarVisao && (
                <ConviteAoDetalhe
                  conteudo="Lista de pedidos, mais críticos e fonte por conta."
                  aoAbrir={() => aoTrocarVisao("detalhe")}
                />
              )
            }
          />
        ) : (
          <>
            {faixaPrincipal}
            <FaixaKpis rotulo="Indicadores secundários" secundaria>
              {secundarios.map((k) => (
                <CelulaKpi key={k.chave} kpi={k} canal={filtros.channel} destaque={false} />
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
    </LayoutExpedicao>
    </div>
  );
}
