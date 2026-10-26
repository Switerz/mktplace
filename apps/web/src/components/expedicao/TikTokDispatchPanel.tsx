"use client";

/**
 * Gate EXP-TK-OPS-1/2 — painel da LDR do TikTok Shop.
 * Retematizado no UX-TORRE-1.
 *
 * Componente PROPRIO, e nao um ramo dentro do `ExpedicaoClient`. Aquele serve
 * dois canais ja em producao e tem 43 KB; o TikTok tem outra forma de dado
 * (coorte por prazo, nao fila paginada), outros estados e outra pergunta. Um
 * `if (canal === "tiktokshop")` espalhado por ele poria Shopee e Mercado Livre
 * em risco a cada ajuste aqui.
 *
 * A tela responde, nesta ordem:
 *   1. estamos dentro da regua do TikTok?  -> cartao da LDR contra a meta de 4%
 *   2. quando o atraso comecou?            -> faixa do incidente + grafico
 *   3. quantos pedidos afetados?           -> cartoes de contagem
 *   4. quais lojas concentram?             -> tabela por marca
 *   5. quantos ainda da' para tratar?      -> cartao "ainda no prazo"
 *
 * As duas leituras aparecem SEPARADAS e NOMEADAS: a LDR (denominador = quem
 * vence no periodo) e o fluxo por data de pagamento (denominador = quem pagou
 * no dia). A segunda e' a visao que a gestao usa; usa-la como se fosse a LDR
 * inflaria a taxa toda vez que um feriado empurrasse tres dias de pagamento
 * para o mesmo vencimento.
 *
 * UX-TORRE-1 — O `dark:` ORFAO
 * ----------------------------
 * Este arquivo era o UNICO da Torre com classes `dark:*`, numa aplicacao sem
 * tema escuro global: quando o sistema operacional pedia escuro, so' este
 * painel ficava preto dentro de uma tela clara. Isso nunca foi significado de
 * negocio — era inconsistencia visual. Os pares `x dark:y` foram trocados pelo
 * token semantico correspondente, que ja' muda de valor com o tema. Nenhuma
 * severidade mudou de cor: `critico` continua sendo o papel critico.
 *
 * EXPEDICAO-UX-UNIFICADA-1 — A MESMA HIERARQUIA DA FILA
 * ----------------------------------------------------
 * Cabecalho, fotografia e barra de qualidade sao os componentes da fila
 * (`CabecalhoExpedicao`, `BarraDeQualidade`, `AtencaoAgora`). O CORPO continua
 * sendo deste painel: nao existe fila do TikTok e nenhuma foi inventada.
 *
 *   visao do gestor   atencao agora (alertas da API) -> LDR -> evolucao por
 *                     vencimento -> lojas
 *   detalhe           LDR -> coortes por vencimento -> lojas -> fluxo por
 *                     data de pagamento (visao auxiliar, NUNCA a LDR)
 *
 * O que mudou de lugar, e por que:
 *   - o aviso de prazo reconstruido e os alertas eram uma PILHA de banners
 *     antes do primeiro numero. O aviso entrou na barra de qualidade (e segue
 *     colado ao periodo, ao lado da LDR); os alertas viraram as linhas de
 *     "O que exige atenção agora", com o texto da API, sem reescrita;
 *   - sem nenhum vencimento fechado (o caso da serie vencida), o grafico vira
 *     uma linha: um retangulo de 224 px sem barra nao informa nada;
 *   - o fluxo por pagamento desceu para o detalhe. Lado a lado com a LDR na
 *     primeira dobra, os dois denominadores competiam pela mesma leitura.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import BarraDeQualidade from "@/components/expedicao/BarraDeQualidade";
import CabecalhoExpedicao, { SeletorMarca } from "@/components/expedicao/CabecalhoExpedicao";
import JanelaPersonalizada from "@/components/expedicao/JanelaPersonalizada";
import JornadaPedidos, { useJornada } from "@/components/expedicao/JornadaPedidos";
import LayoutExpedicao, {
  CLASSE_CABECALHO,
  CLASSE_RAIZ_EXPEDICAO,
  CLASSE_TABELA,
  CorpoGestor,
  TEXTO_ESTADO,
} from "@/components/expedicao/LayoutExpedicao";
import { Ajuda, KpiComAjuda, TaxaComBase } from "@/components/expedicao/KpiComAjuda";
import {
  AtencaoAgora,
  ConviteAoDetalhe,
  FaixaKpis,
  SecaoGerencial,
} from "@/components/expedicao/SecoesExpedicao";
import Chip from "@/components/ui/Chip";
import Disclosure from "@/components/ui/Disclosure";
import Note from "@/components/ui/Note";
import Panel from "@/components/ui/Panel";
import Segmented from "@/components/ui/Segmented";
import SortHeader from "@/components/ui/SortHeader";
import StateBlock, { RetryButton } from "@/components/ui/StateBlock";
import type { Visao } from "@/lib/expedicao-ux";
import { useSortableTable, type SortColumnType } from "@/lib/use-sortable-table";

import {
  DEFINICAO_TIKTOK,
  EVENTOS,
  EVENTO_PADRAO,
  EXPLICACAO_EVENTO,
  EXPLICACAO_FLUXO,
  EXPLICACAO_LDR,
  EXPLICACAO_META,
  META_TIKTOK_LDR,
  ROTULO_EVENTO,
  ROTULO_LIMIAR_INTERNO,
  ROTULO_META,
  ROTULO_SEVERIDADE,
  SELO_ACIMA_DA_META,
  SELO_DENTRO_DA_META,
  TITULO_FLUXO,
  TITULO_LDR,
  VALOR_SEM_TAXA,
  alturaDaBarra,
  alturaDaMeta,
  contarCriticos,
  contarForaDaMeta,
  descreverIncidente,
  explicarIndisponivelComMarca,
  formatarDiaCurto,
  formatarDiaLongo,
  formatarInteiro,
  formatarTaxa,
  fotografiaDoTikTok,
  marcasForaDaMeta,
  montarAtencaoDoTikTok,
  montarBarraDeQualidadeTikTok,
  queryDoTikTok,
  serieTemDiaMedido,
  severidadeDoDia,
  taxaMaxima,
  type Evento,
  type Severidade,
  type TikTokLdrDia,
  type TikTokMarca,
  type TikTokPayload,
} from "@/lib/expedicao-tiktok-contract";
import { buscarExpedicao } from "@/lib/expedicao-demo";
import {
  OPCAO_PERSONALIZADA,
  REGRA_JANELA,
  lerDiasDaUrl,
  opcoesDoSeletor,
  urlComDias,
  valorDoSeletor,
} from "@/lib/expedicao-janela";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

/** Janela do TikTok: dias, de 1 a 90, que e' a unidade da propria API. */
const REGRA = REGRA_JANELA.tiktok;

/**
 * A casca so' monta este painel DEPOIS de ler a URL no cliente, entao ler
 * `window` aqui nao diverge de HTML de servidor nenhum — e evita disparar a
 * janela padrao antes da janela do link. A guarda cobre um render fora do
 * navegador.
 */
function janelaInicial(): { dias: number; descartado: boolean } {
  if (typeof window === "undefined") return { dias: REGRA.padrao, descartado: false };
  return lerDiasDaUrl(window.location.search, REGRA);
}

/**
 * Severidade -> token. `parcial` continua HACHURADO de proposito: coorte
 * parcial nao pode parecer um resultado fechado, nem bom nem ruim.
 */
const COR_SEVERIDADE: Record<Severidade, string> = {
  ok: "bg-ok",
  fora_da_meta: "bg-warn",
  critico: "bg-crit",
  parcial:
    "bg-[repeating-linear-gradient(45deg,rgb(var(--tc-line-strong))_0px,rgb(var(--tc-line-strong))_4px,transparent_4px,transparent_8px)]",
};

const TEXTO_SEVERIDADE: Record<Severidade, string> = {
  ok: "text-ok-ink",
  fora_da_meta: "text-warn-ink",
  critico: "text-crit-ink",
  parcial: "text-ink-faint",
};

// T-0010: CLASSE_TABELA e CLASSE_CABECALHO vem do esqueleto comum.

const COLUNAS_MARCA: Record<string, SortColumnType> = {
  loja: "text",
  base: "numeric",
  late: "numeric",
  rate: "numeric",
};

function valorDaMarca(m: TikTokMarca, coluna: string): string | number | null {
  if (coluna === "loja") return m.shop_name ?? m.brand;
  if (coluna === "base") return m.base;
  if (coluna === "late") return m.late;
  return m.rate;
}

// ---------------------------------------------------------------------------
// Grafico
// ---------------------------------------------------------------------------
function Grafico({ dias }: { dias: TikTokLdrDia[] }) {
  const maximo = taxaMaxima(dias);
  const meta = alturaDaMeta(maximo);
  if (!dias.length) return null;
  return (
    <div>
      {/* `items-stretch` e nao `items-end`: com `items-end` cada coluna e'
          dimensionada pelo CONTEUDO, o `flex-1` de dentro nao recebe altura e
          a barra desaparece — o grafico renderiza numeros e datas sobre um
          retangulo vazio. Ja aconteceu neste componente. */}
      <div className="relative">
        {meta !== null ? (
          <div
            className="pointer-events-none absolute inset-x-0 z-10 border-t border-dashed border-warn"
            style={{ bottom: `calc(${meta}% + 1.25rem)` }}
            aria-hidden="true"
          >
            <span className="absolute -top-4 right-0 rounded bg-warn-soft px-1.5 py-0.5 text-xs font-medium text-warn-ink ring-1 ring-inset ring-warn/30">
              {ROTULO_META} {formatarTaxa(META_TIKTOK_LDR, 0)}
            </span>
          </div>
        ) : null}
        <div
          className="flex h-56 items-stretch gap-1 overflow-x-auto pb-1"
          role="img"
          aria-label={`Taxa por dia de vencimento. Máximo da série: ${formatarTaxa(
            maximo,
          )}. Referência do TikTok: ${formatarTaxa(META_TIKTOK_LDR, 0)} (medição interna).`}
        >
          {dias.map((d) => {
            const sev = severidadeDoDia(d);
            return (
              <div
                key={d.due_date}
                className="flex min-w-[38px] flex-1 flex-col items-center gap-1"
              >
                <span className={`text-xs tabular-nums ${TEXTO_SEVERIDADE[sev]}`}>
                  {d.rate === null ? VALOR_SEM_TAXA : `${Math.round(d.rate * 100)}`}
                </span>
                <div className="flex w-full flex-1 items-end">
                  <div
                    className={`w-full rounded-t ${COR_SEVERIDADE[sev]}`}
                    style={{ height: `${alturaDaBarra(d, maximo)}%` }}
                    title={`${formatarDiaLongo(d.due_date)} · ${formatarTaxa(d.rate)} · ${
                      ROTULO_SEVERIDADE[sev]
                    }`}
                  />
                </div>
                <span className="text-xs tabular-nums text-ink-faint">
                  {formatarDiaCurto(d.due_date)}
                </span>
              </div>
            );
          })}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
        {(["ok", "fora_da_meta", "critico", "parcial"] as Severidade[]).map((s) => (
          <span key={s} className="inline-flex items-center gap-1.5">
            <span className={`inline-block h-2.5 w-2.5 rounded-sm ${COR_SEVERIDADE[s]}`} />
            {ROTULO_SEVERIDADE[s]}
          </span>
        ))}
        <span className="text-ink-faint">Números em % dos pedidos que venciam no dia.</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Painel
// ---------------------------------------------------------------------------
export default function TikTokDispatchPanel({
  seletorCanal,
  visao = "gestor",
  aoTrocarVisao,
  marca = null,
  aoTrocarMarca,
}: {
  seletorCanal?: ReactNode;
  visao?: Visao;
  aoTrocarVisao?: (v: Visao) => void;
  /** EXPEDICAO-UX-3: marca da casca; vai para a API como `brands`. */
  marca?: string | null;
  aoTrocarMarca?: (m: string | null) => void;
}) {
  const [evento, setEvento] = useState<Evento>(EVENTO_PADRAO);
  const [inicial] = useState(janelaInicial);
  const [dias, setDias] = useState<number>(inicial.dias);
  const [personalizado, setPersonalizado] = useState(false);
  const [janelaDescartada, setJanelaDescartada] = useState(inicial.descartado);
  const [dados, setDados] = useState<TikTokPayload | null>(null);
  const [estado, setEstado] = useState<"carregando" | "ok" | "erro">("carregando");
  const [erro, setErro] = useState<string>("");

  const url = useMemo(() => `${BASE}${queryDoTikTok(evento, dias, marca)}`, [evento, dias, marca]);
  // Com o campo livre a janela troca mais rapido: a resposta de uma janela
  // antiga que chega por ultimo NAO pode sobrescrever a atual.
  const urlVigente = useRef(url);
  urlVigente.current = url;

  const carregar = useCallback(async () => {
    setEstado("carregando");
    try {
      const r = await buscarExpedicao(url, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const json = (await r.json()) as TikTokPayload;
      if (urlVigente.current !== url) return;
      setDados(json);
      setEstado("ok");
    } catch (e) {
      if (urlVigente.current !== url) return;
      setErro(e instanceof Error ? e.message : "falha de rede");
      setEstado("erro");
    }
  }, [url]);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  // `?dias=` invalido no link: usa o padrao e tira o lixo da URL.
  useEffect(() => {
    if (inicial.descartado) {
      window.history.replaceState(null, "", urlComDias(window.location.href, inicial.dias, REGRA));
    }
  }, [inicial]);

  // Voltar/avancar do navegador pode trocar a janela sem remontar o painel.
  useEffect(() => {
    const aoVoltar = () => {
      const lida = lerDiasDaUrl(window.location.search, REGRA);
      setDias(lida.dias);
      setPersonalizado(false);
      setJanelaDescartada(lida.descartado);
    };
    window.addEventListener("popstate", aoVoltar);
    return () => window.removeEventListener("popstate", aoVoltar);
  }, []);

  const aplicarJanela = useCallback((novo: number) => {
    setDias(novo);
    setJanelaDescartada(false);
    window.history.replaceState(null, "", urlComDias(window.location.href, novo, REGRA));
  }, []);

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
  const seletor = valorDoSeletor(dias, REGRA, personalizado);

  // T-0010: a jornada usa a mesma janela e o mesmo evento do painel.
  const jornada = useJornada(BASE, "tiktok", marca, { dias, evento });

  const ldr = dados?.ldr ?? null;
  const fluxo = dados?.payment_flow ?? [];
  const foraDaMeta = ldr ? contarForaDaMeta(ldr.daily) : 0;
  const criticos = ldr ? contarCriticos(ldr.daily) : 0;
  const incidente = descreverIncidente(ldr);
  const piores = marcasForaDaMeta(dados?.brands ?? []);
  const sla = dados?.sla_business_days?.[evento];
  const fotografia = fotografiaDoTikTok(dados);
  const barra = montarBarraDeQualidadeTikTok(dados);
  const atencao = montarAtencaoDoTikTok(dados);
  const temDiaMedido = ldr ? serieTemDiaMedido(ldr.daily) : false;
  const semVencimentoFechado = ldr ? ldr.mature_due_days === 0 : false;
  const publicadaEm = dados?.snapshot
    ? new Date(dados.snapshot.effective_at).toLocaleString("pt-BR", {
        dateStyle: "short",
        timeStyle: "short",
      })
    : null;
  // "Agora" e' o instante da serie. Com ela vencida, a secao diz isso.
  const quando = publicadaEm
    ? `Segundo a série publicada em ${publicadaEm}${
        fotografia?.desatualizada ? " — desatualizada: confirme antes de agir" : ""
      }.`
    : undefined;

  // Ordenacao client-side sobre `brands`, que vem INTEIRO no payload. Nao ha
  // paginacao aqui, entao ordenar nao esconde linha nenhuma nem recalcula
  // metrica: as taxas continuam sendo as que a API publicou.
  const { sort, toggleSort, sortedRows } = useSortableTable<TikTokMarca>(
    dados?.brands ?? [],
    valorDaMarca,
    COLUNAS_MARCA,
  );

  const controles = (
    <div className="flex flex-wrap items-center gap-2">
      <span id="tk-evento" className="text-xs text-ink-faint">
        Evento
      </span>
      <Segmented
        rotulo="Evento"
        rotuladoPor="tk-evento"
        opcoes={EVENTOS.map((e) => ({ valor: e, texto: ROTULO_EVENTO[e] }))}
        valor={evento}
        aoEscolher={setEvento}
      />
      <span id="tk-janela" className="text-xs text-ink-faint">
        Janela
      </span>
      <Segmented
        rotulo="Janela"
        rotuladoPor="tk-janela"
        opcoes={opcoesDoSeletor(REGRA)}
        valor={seletor}
        aoEscolher={escolherJanela}
      />
    </div>
  );

  // EXPEDICAO-UX-3 — "Risco atual": o que esta' pendente AGORA, com os
  // mesmos campos que a LDR ja' publicava (nenhum novo, nenhum recalculado).
  const secaoRisco = ldr ? (
    <SecaoGerencial titulo="Risco atual" descricao={quando}>
      <FaixaKpis rotulo="Risco atual">
        <KpiComAjuda
          ajuda="tiktok.pendente_vencido"
          rotulo="Venceram e não saíram"
          valor={formatarInteiro(ldr.pending_overdue)}
          destaque
          comparacao="Vencimentos da janela consultada, ainda sem o evento."
          tom={ldr.pending_overdue > 0 ? "critico" : "neutro"}
        />
        <KpiComAjuda
          ajuda="tiktok.vence_hoje_amanha"
          rotulo="Vencem hoje ou amanhã"
          valor={formatarInteiro(ldr.pending_at_risk)}
          destaque
          comparacao="Ainda dá para tratar dentro do prazo."
          tom={ldr.pending_at_risk > 0 ? "atencao" : "neutro"}
        />
        <KpiComAjuda
          ajuda="tiktok.pendente_no_prazo"
          rotulo="Pendentes no prazo"
          valor={formatarInteiro(ldr.pending_on_time)}
          destaque
          comparacao="Pedidos ainda sem o evento, dentro do prazo."
        />
        {/* Sem loja com base suficiente, a contagem nao existe: "—", nunca 0. */}
        <KpiComAjuda
          ajuda="tiktok.lojas_acima_meta"
          rotulo="Lojas acima da meta"
          valor={dados?.brands.length ? formatarInteiro(piores.length) : VALOR_SEM_TAXA}
          destaque
          comparacao={
            dados?.brands.length
              ? `De ${formatarInteiro(dados.brands.length)} com base suficiente · medição interna.`
              : "Nenhuma loja com base suficiente no período."
          }
          tom={!dados?.brands.length ? "ausente" : piores.length > 0 ? "critico" : "neutro"}
        />
      </FaixaKpis>
      <AtencaoAgora itens={atencao} />
    </SecaoGerencial>
  ) : null;

  // "Prazo": a LDR, que so' o TikTok sustenta. Coleta e despacho continuam
  // sendo eventos separados, escolhidos no cabecalho, com prazos diferentes.
  const secaoLdr = ldr ? (
    <SecaoGerencial
      titulo="Prazo"
      descricao={`${TITULO_LDR} · evento: ${ROTULO_EVENTO[evento]}${sla ? ` (${sla} dia(s) útil(eis))` : ""}.`}
      acao={
        // A ressalva fica COLADA na secao, nunca atras de um tooltip: o numero
        // sai desta tela para reuniao, e sem ela vira "o TikTok nos penalizou".
        <Disclosure rotulo="Como ler esta medição">
          <p>
            {EXPLICACAO_LDR}
            {sla ? ` Prazo do evento: ${sla} dia(s) útil(eis).` : ""}
          </p>
          <p className="mt-1.5">{EXPLICACAO_META}</p>
          <p className="mt-1.5">{EXPLICACAO_EVENTO[evento]}</p>
        </Disclosure>
      }
    >
      {/* Periodo da RESPOSTA (`ldr.from`, `ldr.to`, `ldr.days`), nao do
          seletor. O prazo reconstruido fica colado a ele: e' a ressalva que
          qualifica TODOS os numeros desta faixa. */}
      <p className="text-xs text-ink-muted" data-testid="tk-periodo-consultado">
        Período consultado:{" "}
        <span className="font-medium text-ink">
          vencimentos de {formatarDiaLongo(ldr.from)} a {formatarDiaLongo(ldr.to)}
        </span>{" "}
        ({ldr.days} {ldr.days === 1 ? "dia" : "dias"} + hoje)
        {dados?.deadline_is_reconstructed
          ? " · prazo reconstruído da política de dias úteis — indicador interno"
          : ""}
      </p>
      <FaixaKpis rotulo="Indicadores principais" colunas={3}>
        <KpiComAjuda
          ajuda="tiktok.ldr"
          rotulo="LDR da janela"
          valor={<TaxaComBase numerador={ldr.late} base={ldr.base} taxa={ldr.rate} />}
          destaque
          comparacao={
            ldr.rate === null
              ? "Sem vencimento maduro na janela."
              : // T-0010: a base ao lado da taxa ("62 de 1.820"), com a taxa
                // PUBLICADA pela API — a tela nao divide de novo.
                `${ROTULO_META}: ${formatarTaxa(
                  META_TIKTOK_LDR,
                  0,
                )} · ${ldr.above_target ? SELO_ACIMA_DA_META : SELO_DENTRO_DA_META}`
          }
          // Sem taxa nao ha veredito: verde aqui leria ausencia como acerto.
          tom={ldr.rate === null ? "ausente" : ldr.above_target ? "critico" : "bom"}
        />
        {/* `base` e `late` da LDR somam SO' vencimentos fechados. Sem nenhum
            fechado (o caso da serie vencida) eles valem zero por construcao,
            nao por medicao: a tela mostra ausencia, nao "0 venciam". */}
        <KpiComAjuda
          ajuda="tiktok.venciam"
          rotulo="Venciam no período"
          valor={semVencimentoFechado ? VALOR_SEM_TAXA : formatarInteiro(ldr.base)}
          destaque
          comparacao={`${ldr.mature_due_days} dia(s) fechado(s), ${ldr.partial_due_days} ainda no prazo.`}
          tom={semVencimentoFechado ? "ausente" : "neutro"}
        />
        <KpiComAjuda
          ajuda="tiktok.atrasaram"
          rotulo="Já atrasaram"
          valor={semVencimentoFechado ? VALOR_SEM_TAXA : formatarInteiro(ldr.late)}
          destaque
          comparacao={`${formatarInteiro(ldr.pending_overdue)} venceram e ainda não saíram.`}
          tom={semVencimentoFechado ? "ausente" : ldr.late > 0 ? "critico" : "bom"}
        />
      </FaixaKpis>
    </SecaoGerencial>
  ) : null;

  // 6. EVOLUCAO — quando comecou. Sem nenhum vencimento fechado o grafico
  // seria um retangulo sem barra: a tela diz por que nao ha o que desenhar.
  const painelEvolucao = ldr ? (
    <Panel
      titulo="Evolução por dia de vencimento"
      descricao={
        temDiaMedido
          ? foraDaMeta > 0
            ? `${foraDaMeta} dia(s) acima da meta, ${criticos} acima do ${ROTULO_LIMIAR_INTERNO.toLowerCase()}.${
                incidente ? ` ${incidente}` : ""
              }`
            : "Nenhum dia acima da meta na janela."
          : undefined
      }
    >
      {temDiaMedido ? (
        <Grafico dias={ldr.daily} />
      ) : (
        <p role="status" className="text-sm text-ink-muted">
          Nenhum vencimento da janela tem taxa fechada para desenhar.
          {fotografia?.desatualizada
            ? ` A série está desatualizada${publicadaEm ? ` (publicada em ${publicadaEm})` : ""}.`
            : ldr.partial_due_days > 0
              ? ` ${ldr.partial_due_days} dia(s) seguem no prazo (parcial).`
              : ""}
        </p>
      )}
    </Panel>
  ) : null;

  // 7. CONCENTRACAO — lojas, nas duas profundidades.
  const painelLojas = (classe: string) => (
    <Panel
      className={classe}
      semPadding
      titulo={
        <span className="flex items-center gap-2">
          Concentração por marca
          {piores.length ? (
            <Chip tom="critico" ponto>
              {piores.length} acima da meta
            </Chip>
          ) : null}
        </span>
      }
      rodape="Só lojas com base suficiente no período. Vencimentos ainda no prazo ficam de fora."
    >
      {/* Sem loja elegivel, um cabecalho de tabela sem linha nenhuma so'
          ocuparia espaco: a tela diz que nao ha loja a ranquear. */}
      {sortedRows.length === 0 ? (
        <p role="status" className="px-4 py-3 text-sm text-ink-muted">
          Nenhuma loja com base suficiente de vencimentos fechados no período.
        </p>
      ) : (
        <div className="max-h-96 overflow-auto">
          <table className={CLASSE_TABELA}>
            <caption className="sr-only">LDR por loja no período</caption>
            <thead className={CLASSE_CABECALHO}>
              <tr>
                <SortHeader
                  rotulo="Loja"
                  coluna="loja"
                  sort={sort}
                  aoOrdenar={toggleSort}
                  alinhamento="left"
                />
                <SortHeader rotulo="Venciam" coluna="base" sort={sort} aoOrdenar={toggleSort} />
                <SortHeader rotulo="Atrasados" coluna="late" sort={sort} aoOrdenar={toggleSort} />
                <SortHeader rotulo="LDR" coluna="rate" sort={sort} aoOrdenar={toggleSort} />
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((m) => (
                <tr key={m.brand} className="border-t border-line">
                  <th scope="row" className="text-left font-medium text-ink">
                    {m.shop_name ?? m.brand}
                  </th>
                  <td className="text-right tabular-nums text-ink">{formatarInteiro(m.base)}</td>
                  <td className="text-right tabular-nums text-ink">{formatarInteiro(m.late)}</td>
                  <td
                    className={`text-right font-semibold tabular-nums ${
                      m.above_target ? "text-crit-ink" : "text-ok-ink"
                    }`}
                  >
                    <TaxaComBase numerador={m.late} base={m.base} taxa={m.rate} linha />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );

  // 8. DETALHE — as coortes que compoem a LDR, linha a linha. Sao os mesmos
  // campos de `ldr.daily` que o grafico desenha, agora legiveis por leitor de
  // tela. Coorte parcial NUNCA mostra taxa fechada: ela ainda vai mudar.
  const painelCoortes = ldr ? (
    <Panel
      semPadding
      titulo="Coortes por dia de vencimento"
      descricao="A base da LDR: cada linha são os pedidos cujo envio vencia naquele dia."
    >
      {ldr.daily.length ? (
        <div className="max-h-96 overflow-auto">
          <table className={CLASSE_TABELA}>
            <caption className="sr-only">
              Coortes por dia de vencimento — o denominador da LDR
            </caption>
            <thead className={CLASSE_CABECALHO}>
              <tr>
                <th scope="col" className="font-semibold">Vencimento</th>
                <th scope="col" className="text-right font-semibold">Venciam</th>
                <th scope="col" className="text-right font-semibold">Atrasados</th>
                <th scope="col" className="text-right font-semibold">… ainda sem envio</th>
                <th scope="col" className="text-right font-semibold">No prazo s/ envio</th>
                <th scope="col" className="text-right font-semibold">
                  Taxa <Ajuda chave="tiktok.ldr" />
                </th>
                <th scope="col" className="font-semibold">Estado</th>
              </tr>
            </thead>
            <tbody>
              {ldr.daily.map((d) => {
                const sev = severidadeDoDia(d);
                return (
                  <tr key={d.due_date} className="border-t border-line">
                    <th scope="row" className="whitespace-nowrap text-left font-medium text-ink">
                      {formatarDiaLongo(d.due_date)}
                    </th>
                    <td className="text-right tabular-nums text-ink">{formatarInteiro(d.base)}</td>
                    <td className="text-right tabular-nums text-ink">{formatarInteiro(d.late)}</td>
                    <td className="text-right tabular-nums text-ink">
                      {formatarInteiro(d.pending_overdue)}
                    </td>
                    <td className="text-right tabular-nums text-ink">
                      {formatarInteiro(d.pending_on_time)}
                    </td>
                    <td
                      className={`text-right font-semibold tabular-nums ${TEXTO_SEVERIDADE[sev]}`}
                    >
                      {d.is_mature ? (
                        <TaxaComBase numerador={d.late} base={d.base} taxa={d.rate} linha />
                      ) : (
                        VALOR_SEM_TAXA
                      )}
                    </td>
                    <td className="whitespace-nowrap text-ink-muted">{ROTULO_SEVERIDADE[sev]}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p role="status" className="px-4 py-3 text-sm text-ink-muted">
          Nenhum pedido vence na janela consultada.
        </p>
      )}
    </Panel>
  ) : null;

  const painelFluxo = (classe: string) => (
    <Panel
      className={classe}
      semPadding
      titulo={
        <span className="flex flex-wrap items-center gap-2">
          {TITULO_FLUXO}
          {/* Nomeado como VISAO AUXILIAR na propria etiqueta: quem le
              de relance nao pode confundir este denominador com o da
              LDR. */}
          <Chip>visão auxiliar · não é a LDR</Chip>
        </span>
      }
      acao={<Disclosure rotulo="Como ler">{EXPLICACAO_FLUXO}</Disclosure>}
    >
      {/* QA T-0010 (M6): sem dia de pagamento na janela, estado de vazio. */}
      {fluxo.length === 0 ? (
        <p role="status" className="px-4 py-3 text-sm text-ink-muted">
          Nenhum dia de pagamento na janela consultada.
        </p>
      ) : (
      <div className="max-h-96 overflow-auto">
        <table className={CLASSE_TABELA}>
          <caption className="sr-only">
            Fluxo por data de pagamento — denominador diferente do da LDR
          </caption>
          <thead className={CLASSE_CABECALHO}>
            <tr>
              <th scope="col" className="font-semibold">Pago em</th>
              <th scope="col" className="text-right font-semibold">Pagos</th>
              <th scope="col" className="text-right font-semibold">No prazo</th>
              <th scope="col" className="text-right font-semibold">Atrasados</th>
              <th scope="col" className="text-right font-semibold">Vencidos s/ envio</th>
              <th scope="col" className="text-right font-semibold">No prazo s/ envio</th>
              <th scope="col" className="text-right font-semibold">
                  Taxa <Ajuda chave="tiktok.fluxo_pagamento" />
                </th>
            </tr>
          </thead>
          <tbody>
            {fluxo.map((f) => (
              <tr key={f.paid_date} className="border-t border-line">
                <th scope="row" className="whitespace-nowrap text-left font-medium text-ink">
                  {formatarDiaLongo(f.paid_date)}
                  {!f.is_mature ? (
                    <span className="ml-1.5 rounded bg-raised px-1.5 py-0.5 text-xs text-ink-faint ring-1 ring-inset ring-line">
                      parcial
                    </span>
                  ) : null}
                </th>
                <td className="text-right tabular-nums text-ink">
                  {formatarInteiro(f.paid_orders)}
                </td>
                <td className="text-right tabular-nums text-ink">
                  {formatarInteiro(f.shipped_on_time)}
                </td>
                <td className="text-right tabular-nums text-ink">
                  {formatarInteiro(f.shipped_late)}
                </td>
                <td className="text-right tabular-nums text-ink">
                  {formatarInteiro(f.pending_overdue)}
                </td>
                <td className="text-right tabular-nums text-ink">
                  {formatarInteiro(f.pending_on_time)}
                </td>
                <td
                  className={`text-right font-semibold tabular-nums ${
                    !f.is_mature
                      ? "text-ink-faint"
                      : (f.rate ?? 0) > META_TIKTOK_LDR
                        ? "text-crit-ink"
                        : "text-ok-ink"
                  }`}
                >
                  {/* Coorte parcial NUNCA mostra a taxa fechada: ela
                      ainda vai mudar. */}
                  {f.is_mature ? (
                    <TaxaComBase numerador={f.late} base={f.base} taxa={f.rate} linha />
                  ) : (
                    VALOR_SEM_TAXA
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}
    </Panel>
  );

  return (
    // `div`, nao `main`: o landmark `main` e' do AppShell (um por pagina).
    <div className={CLASSE_RAIZ_EXPEDICAO} aria-busy={estado === "carregando"}>
    <LayoutExpedicao
      // 1-2. CABECALHO, FOTOGRAFIA, FRESCOR E FILTROS (compartilhado)
      cabecalho={
        <CabecalhoExpedicao
          canal="TikTok Shop"
          seletorCanal={seletorCanal}
          fotografia={fotografia}
          visao={visao}
          aoTrocarVisao={aoTrocarVisao}
          controles={controles}
          definicao={DEFINICAO_TIKTOK}
          filtroMarca={
            aoTrocarMarca ? (
              <SeletorMarca
                marca={marca}
                marcasPresentes={(dados?.brands ?? []).map((b) => b.brand)}
                aoTrocar={aoTrocarMarca}
              />
            ) : undefined
          }
        />
      }
      avisos={
        <>
          {seletor === OPCAO_PERSONALIZADA ? (
            <JanelaPersonalizada max={REGRA.max} diasAplicados={dias} aoAplicar={aplicarJanela} />
          ) : null}
          {janelaDescartada ? (
            <Note tom="atencao" papel="status">
              A janela do link era inválida e foi ignorada. Mostrando a janela padrão.
            </Note>
          ) : null}
        </>
      }
      // 3. QUALIDADE DO DADO, CONSOLIDADA (compartilhada)
      qualidade={<BarraDeQualidade barra={barra} />}
      // Erro, ausencia e serie vencida sao estados DIFERENTES: erro e' a carga
      // que falhou agora (com nova tentativa); ausencia e' nao haver serie
      // nenhuma (nunca 0%); serie vencida e' dado que existe, velho, e aparece
      // no selo do cabecalho e na barra de qualidade.
      estado={
        <>
          {estado === "carregando" ? <StateBlock tipo="carregando" titulo={TEXTO_ESTADO.carregando} /> : null}
          {estado === "erro" ? (
            <StateBlock
              tipo="erro"
              titulo={TEXTO_ESTADO.erro}
              descricao={erro ? `Falha: ${erro}.` : undefined}
              acao={<RetryButton onClick={() => void carregar()} />}
            />
          ) : null}
          {estado === "ok" && dados?.availability === "unavailable" ? (
            <StateBlock
              tipo="indisponivel"
              titulo={explicarIndisponivelComMarca(dados.unavailable_reason, marca)}
              descricao="A tela não mostra 0% enquanto não houver série: zero aqui seria lido como “nenhum atraso”, que é diferente de “sem medição”."
            />
          ) : null}
        </>
      }
    >
      {estado === "ok" && dados?.availability === "available" && ldr ? (
        visao === "gestor" ? (
          // RISCO ATUAL -> PRAZO (LDR) -> JORNADA -> EVOLUCAO -> CONCENTRACAO:
          // a ordem e' do esqueleto, a mesma dos tres canais.
          <CorpoGestor
            risco={secaoRisco}
            prazo={secaoLdr}
            jornada={<JornadaPedidos carga={jornada.carga} aoTentarDeNovo={jornada.tentarDeNovo} marca={marca} />}
            evolucao={painelEvolucao}
            concentracao={painelLojas("")}
            convite={
              aoTrocarVisao ? (
                <ConviteAoDetalhe
                  conteudo="As coortes por dia de vencimento e o fluxo por data de pagamento (visão auxiliar, não é a LDR) ficam no detalhe operacional."
                  aoAbrir={() => aoTrocarVisao("detalhe")}
                />
              ) : null
            }
          />
        ) : (
          <>
            {secaoLdr}
            {/* DETALHE: coorte, loja e fluxo */}
            {painelCoortes}
            <div className="grid gap-3 lg:grid-cols-5">
              {painelLojas("lg:col-span-2")}
              {painelFluxo("lg:col-span-3")}
            </div>
          </>
        )
      ) : null}
    </LayoutExpedicao>
    </div>
  );
}
