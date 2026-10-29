"use client";

/**
 * T-0010 — o ESQUELETO comum da Expedicao, igual nos tres canais.
 *
 * Ordem fixa (o painel do TikTok e' a referencia visual):
 *
 *   1. cabecalho, frescor e filtros       (`cabecalho`)
 *   2. avisos da janela e qualidade do dado (`avisos`, `qualidade`)
 *   3. estado da carga                     (`estado`: carregando/erro/vazio/indisponivel)
 *   4. corpo, por profundidade:
 *        visao do gestor -> `CorpoGestor`: risco atual -> prazo e taxas ->
 *        jornada -> evolucao -> concentracao -> convite ao detalhe;
 *        detalhe operacional -> o que o canal tem de detalhe.
 *
 * O esqueleto NAO conhece canal nem metrica: cada painel entrega as secoes
 * que a SUA fonte sustenta. Secao sem fonte no canal vira
 * `SecaoIndisponivel` — um bloco curto com o `?` explicando —, nunca um
 * buraco nem um numero inventado.
 */
import type { ReactNode } from "react";

import Note from "@/components/ui/Note";
import StateBlock, { RetryButton } from "@/components/ui/StateBlock";
import type { ChaveMetrica } from "@/lib/expedicao-definicoes";

import AjudaMetrica from "./AjudaMetrica";
import { SecaoGerencial } from "./SecoesExpedicao";

/**
 * Tabelas densas da Expedicao — UMA definicao (antes duplicada na fila e no
 * TikTok, com espacamentos diferentes). Referencia: o painel do TikTok.
 */
export const CLASSE_TABELA =
  "w-full text-[13px] [&_td]:py-1.5 [&_td]:px-3 [&_th]:py-1.5 [&_th]:px-3";
export const CLASSE_CABECALHO =
  "sticky top-0 z-10 bg-raised text-left text-xs uppercase tracking-wide text-ink-faint";

/**
 * Classe da RAIZ de cada painel. A raiz continua sendo um `div` do proprio
 * painel (com `aria-busy`), e nao deste componente: o `main` e' do
 * AppShell e o teste de landmarks ancora na raiz do componente do canal.
 */
export const CLASSE_RAIZ_EXPEDICAO = "flex w-full flex-col gap-3 p-4";

/**
 * QA T-0010 (M4): os MESMOS textos de estado nos tres canais.
 */
export const TEXTO_ESTADO = {
  carregando: "Carregando os dados do canal…",
  erro: "Não foi possível carregar os dados deste canal agora.",
  vazio: "Nenhum pedido aguardando expedição nesta publicação.",
} as const;

export default function LayoutExpedicao({
  cabecalho,
  avisos,
  qualidade,
  estado,
  children,
}: {
  cabecalho: ReactNode;
  avisos?: ReactNode;
  qualidade?: ReactNode;
  estado?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <>
      {cabecalho}
      {avisos}
      {qualidade}
      {estado}
      {children}
    </>
  );
}

/**
 * A visao do gestor, na MESMA ordem nos tres canais. A ordem e' do
 * esqueleto, nao de quem chama: trocar as props de lugar nao muda a tela.
 */
export function CorpoGestor({
  risco,
  prazo,
  jornada,
  evolucao,
  concentracao,
  convite,
}: {
  risco: ReactNode;
  prazo: ReactNode;
  jornada?: ReactNode;
  evolucao: ReactNode;
  /** UI-EXPEDICAO-1: opcional — a fila junta concentracao e evolucao numa tabela. */
  concentracao?: ReactNode;
  convite?: ReactNode;
}) {
  return (
    <>
      {risco}
      {prazo}
      {jornada}
      {evolucao}
      {concentracao}
      {convite}
    </>
  );
}

/** Secao que a fonte do canal nao sustenta: dito em uma linha, com o `?`. */
export function SecaoIndisponivel({
  titulo,
  motivo,
  ajuda,
}: {
  titulo: string;
  motivo: string;
  ajuda?: ChaveMetrica;
}) {
  return (
    <SecaoGerencial titulo={titulo}>
      <p
        role="status"
        className="flex flex-wrap items-center gap-x-1 rounded-xl border border-dashed border-line-strong bg-surface px-4 py-3 text-sm text-ink-muted"
      >
        <span>Indisponível neste canal: {motivo}</span>
        {ajuda ? <AjudaMetrica chave={ajuda} /> : null}
      </p>
    </SecaoGerencial>
  );
}

export type TipoEstadoSecao = "carregando" | "erro" | "vazio" | "indisponivel" | "parcial" | "desatualizado";

/**
 * Estado de UMA secao, igual em todas as secoes dos tres canais.
 * Carregando, erro (com nova tentativa), vazio e indisponivel usam o
 * `StateBlock` compacto; parcial e desatualizado sao ressalvas sobre um
 * numero que continua na tela, entao viram `Note`.
 */
export function EstadoSecao({
  tipo,
  titulo,
  descricao,
  aoTentarDeNovo,
}: {
  tipo: TipoEstadoSecao;
  titulo: string;
  descricao?: ReactNode;
  aoTentarDeNovo?: () => void;
}) {
  if (tipo === "parcial" || tipo === "desatualizado") {
    return (
      <Note tom="atencao" papel="status">
        <span className="font-semibold">{tipo === "parcial" ? "Parcial" : "Desatualizada"}:</span> {titulo}
        {descricao ? <> {descricao}</> : null}
      </Note>
    );
  }
  return (
    <StateBlock
      tipo={tipo}
      titulo={titulo}
      descricao={descricao}
      compacto
      acao={aoTentarDeNovo ? <RetryButton onClick={aoTentarDeNovo} /> : undefined}
    />
  );
}
