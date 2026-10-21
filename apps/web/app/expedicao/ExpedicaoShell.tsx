"use client";

/**
 * Gate EXP-TK-OPS-1 — casca que escolhe entre a fila (Shopee, Mercado Livre) e
 * a serie diaria do TikTok Shop.
 *
 * POR QUE UMA CASCA E NAO UM TERCEIRO CANAL DENTRO DO `ExpedicaoClient`
 * ---------------------------------------------------------------------
 * `Canal` (em `expedicao-contract.ts`) e' o tipo da FILA, e varios contratos
 * sao `Record<Canal, ...>`: `ROTULO_CANAL`, `KPIS_PRINCIPAIS`, `canalTemPrazo`,
 * `montarMapaDeRisco`, `montarTimeline`. Acrescentar `"tiktokshop"` aquele tipo
 * obrigaria a inventar KPI de fila, mapa de risco e timeline para um canal que
 * nao publica fila nenhuma — dados que nao existem, so' para satisfazer o
 * compilador. Os corpos continuam separados: `ExpedicaoClient` (fila) e
 * `TikTokDispatchPanel` (coorte por vencimento).
 *
 * EXPEDICAO-UX-UNIFICADA-1 — UM SELETOR, UMA PROFUNDIDADE
 * -------------------------------------------------------
 * Antes eram dois niveis de navegacao: "Fila (Shopee / ML) | TikTok" aqui e
 * "Shopee | Mercado Livre" dentro da fila. Pareciam produtos diferentes. Agora
 * a casca e' dona de um seletor so' — Shopee | Mercado Livre | TikTok Shop — e
 * da profundidade (visao do gestor / detalhe operacional), que vale igual
 * para os tres.
 *
 * Os dois estados vivem na URL (`?channel=`, `?visao=`), com os mesmos valores
 * de antes para o canal: link antigo abre onde abria. A janela (`?dias=`)
 * continua sendo de cada painel; a casca so' a descarta quando a troca
 * atravessa fila <-> TikTok, porque os limites sao diferentes.
 *
 * Shopee <-> Mercado Livre NAO remonta a fila: o mesmo `ExpedicaoClient`
 * recebe o canal novo e aplica `aplicarCanal`, que decide campo a campo o que
 * atravessa a troca — exatamente como quando o seletor morava dentro dele.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import ExpedicaoClient from "./ExpedicaoClient";
import TikTokDispatchPanel from "@/components/expedicao/TikTokDispatchPanel";
import Segmented from "@/components/ui/Segmented";
import {
  ROTULO_CANAL_EXPEDICAO,
  canaisHabilitados,
  ehCanalDaFila,
  lerCanalExpedicao,
  urlDoCanal,
  type CanalExpedicao,
} from "@/lib/expedicao-canal";
import { VISAO_PADRAO, lerVisao, urlComVisao, type Visao } from "@/lib/expedicao-ux";

/**
 * Flags de canal, escritas INLINE: o Next troca `process.env.NEXT_PUBLIC_*`
 * por literal em build, a comparacao dobra para `false` e o minificador
 * elimina a opcao (e o painel do TikTok) do bundle. Atras de uma chamada de
 * funcao o compilador nao dobraria. `expedicaoMlHabilitado` continua sendo a
 * mesma regra, e ha teste amarrando as duas.
 */
const ML_LIGADO = process.env.NEXT_PUBLIC_EXPEDICAO_ML_ENABLED === "true";
const TIKTOK_LIGADO = process.env.NEXT_PUBLIC_EXPEDICAO_TIKTOK_ENABLED === "true";

const FLAGS = { ml: ML_LIGADO, tiktok: TIKTOK_LIGADO };

/** Identidade do canal: ponto com a cor do canal, nunca cor de estado. */
const PONTO_DO_CANAL: Record<CanalExpedicao, string> = {
  shopee: "bg-canal-shopee",
  mercadolivre: "bg-canal-ml",
  tiktokshop: "bg-canal-tiktok",
};

export default function ExpedicaoShell() {
  /**
   * Comeca no padrao e corrige no efeito: ler `window` durante o render faria
   * o HTML do servidor divergir do primeiro render do cliente.
   */
  const [canal, setCanal] = useState<CanalExpedicao>("shopee");
  const [visao, setVisao] = useState<Visao>(VISAO_PADRAO);
  const [pronta, setPronta] = useState(false);

  const lerDaUrl = useCallback(() => {
    setCanal(lerCanalExpedicao(window.location.search, FLAGS));
    setVisao(lerVisao(window.location.search));
  }, []);

  useEffect(() => {
    lerDaUrl();
    setPronta(true);
  }, [lerDaUrl]);

  // Voltar/avancar do navegador troca a URL sem remontar.
  useEffect(() => {
    const aoVoltar = () => lerDaUrl();
    window.addEventListener("popstate", aoVoltar);
    return () => window.removeEventListener("popstate", aoVoltar);
  }, [lerDaUrl]);

  /**
   * Foco do teclado na troca de canal. Shopee <-> Mercado Livre nao remonta
   * nada, entao o foco fica onde estava. Fila <-> TikTok troca o CORPO
   * inteiro, e o seletor renasce dentro do outro painel: sem isto, quem anda
   * com as setas perdia o foco para o `body` a cada travessia (medido no QA
   * deste gate). Se o foco estava no seletor, ele volta para a opcao marcada.
   */
  const seletorRef = useRef<HTMLSpanElement | null>(null);
  const devolverFoco = useRef(false);

  const trocarCanal = useCallback(
    (novo: CanalExpedicao) => {
      if (novo === canal) return;
      devolverFoco.current = Boolean(seletorRef.current?.contains(document.activeElement));
      window.history.replaceState(null, "", urlDoCanal(window.location.href, canal, novo));
      setCanal(novo);
    },
    [canal],
  );

  useEffect(() => {
    if (!devolverFoco.current) return;
    devolverFoco.current = false;
    seletorRef.current
      ?.querySelector<HTMLButtonElement>('[role="radio"][aria-checked="true"]')
      ?.focus();
  }, [canal]);

  const trocarVisao = useCallback((nova: Visao) => {
    setVisao(nova);
    window.history.replaceState(null, "", urlComVisao(window.location.href, nova));
  }, []);

  const opcoes = canaisHabilitados(FLAGS).map((c) => ({
    valor: c,
    texto: ROTULO_CANAL_EXPEDICAO[c],
    icone: <span aria-hidden="true" className={`h-2 w-2 rounded-full ${PONTO_DO_CANAL[c]}`} />,
  }));

  // Com um canal so' (as duas flags desligadas) nao ha o que escolher: o
  // titulo ja' diz o canal, e um seletor de uma opcao seria cromo vazio.
  const seletorCanal =
    opcoes.length > 1 ? (
      // `contents`: o invólucro so' carrega a ref, sem caixa propria no layout.
      <span ref={seletorRef} className="contents">
        <Segmented
          rotulo="Canal da Expedição"
          opcoes={opcoes}
          valor={canal}
          aoEscolher={trocarCanal}
        />
      </span>
    ) : null;

  // Ate' a URL ser lida, NENHUM painel monta. Antes a fila montava "por
  // enquanto" — e, com a janela na URL, ela lia `?dias=45` de um link do
  // TikTok pela regra DELA (maximo 14), julgava invalido e apagava o
  // parametro antes de o painel do TikTok nascer: o link de 45 dias abria em
  // 7 (medido no QA do EXPEDICAO-UX-JANELA-CUSTOMIZAVEL-1).
  if (!pronta) {
    return (
      <p role="status" className="py-12 text-center text-sm text-ink-muted">
        Carregando a Expedição…
      </p>
    );
  }
  return ehCanalDaFila(canal) ? (
    <ExpedicaoClient
      canal={canal}
      seletorCanal={seletorCanal}
      visao={visao}
      aoTrocarVisao={trocarVisao}
    />
  ) : (
    <TikTokDispatchPanel seletorCanal={seletorCanal} visao={visao} aoTrocarVisao={trocarVisao} />
  );
}
