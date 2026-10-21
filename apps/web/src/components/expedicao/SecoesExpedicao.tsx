"use client";

/**
 * Gate EXPEDICAO-UX-UNIFICADA-1 — as secoes que os tres canais compartilham.
 *
 * So' a ESTRUTURA e' comum: titulo, densidade, ordem e o vocabulario de
 * severidade. O conteudo de cada uma vem do contrato do canal — os itens de
 * atencao da fila sao contagens da API; os do TikTok sao os alertas que a API
 * ja' escreveu. Nenhuma destas pecas conhece canal.
 */
import type { ReactNode } from "react";

import Panel from "@/components/ui/Panel";
import type { ItemAtencao, TomAtencao } from "@/lib/expedicao-ux";

import { ID_FAIXA_DE_CONTROLES } from "./CabecalhoExpedicao";

const PONTO: Record<TomAtencao, string> = {
  critico: "bg-crit",
  atencao: "bg-warn",
  neutro: "bg-ink-faint",
  bom: "bg-ok",
};

const TINTA: Record<TomAtencao, string> = {
  critico: "text-crit-ink",
  atencao: "text-warn-ink",
  neutro: "text-ink-muted",
  bom: "text-ok-ink",
};

/** Escrito, e nao so' pintado: o ponto colorido nunca e' o unico sinal. */
const ROTULO: Record<TomAtencao, string> = {
  critico: "Crítico",
  atencao: "Atenção",
  neutro: "Nota",
  bom: "Info",
};

/**
 * "O que exige atenção agora" — a narrativa do gestor.
 *
 * Linhas densas numa lista, e nao um banner por item: era a pilha de banners
 * que empurrava o primeiro numero do TikTok para baixo da dobra.
 */
export function AtencaoAgora({
  itens,
  descricao,
}: {
  itens: ItemAtencao[];
  /** Uma linha. Com a fonte velha, diz de QUANDO e' o "agora". */
  descricao?: ReactNode;
}) {
  return (
    <Panel titulo="O que exige atenção agora" descricao={descricao}>
      {itens.length ? (
        <ul className="flex flex-col divide-y divide-line">
          {itens.map((i) => (
            <li key={i.chave} className="flex items-start gap-3 py-2 first:pt-0 last:pb-0">
              <span
                className={`flex w-20 shrink-0 items-center gap-1.5 pt-0.5 text-xs font-semibold uppercase tracking-wide ${TINTA[i.tom]}`}
              >
                <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${PONTO[i.tom]}`} />
                {ROTULO[i.tom]}
              </span>
              <p className="min-w-0 text-sm leading-snug text-ink">{i.texto}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-ink-muted">Nenhum alerta publicado para este recorte.</p>
      )}
    </Panel>
  );
}

/** Faixa de KPIs: quatro em destaque, ou a faixa secundaria densa. */
export function FaixaKpis({
  rotulo,
  secundaria = false,
  children,
}: {
  rotulo: string;
  secundaria?: boolean;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={rotulo}
      className={
        secundaria
          ? "grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5"
          : "grid grid-cols-2 gap-2 lg:grid-cols-4"
      }
    >
      {children}
    </section>
  );
}

/**
 * Fim da visao do gestor: o que existe no detalhe e o caminho ate' ele.
 *
 * Ao abrir, o foco vai para o seletor de profundidade, que agora diz
 * "Detalhe operacional": quem usa teclado ou leitor de tela fica no controle
 * que acabou de mudar, e a pagina rola ate' o topo do conteudo novo.
 */
export function ConviteAoDetalhe({
  conteudo,
  aoAbrir,
}: {
  /** O que o detalhe tem NESTE canal. */
  conteudo: string;
  aoAbrir: () => void;
}) {
  const abrir = () => {
    aoAbrir();
    requestAnimationFrame(() => {
      const faixa = document.getElementById(ID_FAIXA_DE_CONTROLES);
      faixa?.scrollIntoView({ block: "start" });
      faixa
        ?.querySelector<HTMLButtonElement>('[role="radio"][aria-checked="true"]')
        ?.focus({ preventScroll: true });
    });
  };
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl border border-dashed border-line-strong px-4 py-3">
      <p className="min-w-0 text-sm text-ink-muted">{conteudo}</p>
      <button
        type="button"
        onClick={abrir}
        className="inline-flex min-h-11 items-center rounded-md border border-line bg-surface px-3 text-sm font-semibold text-ink hover:border-line-strong"
      >
        Abrir detalhe operacional
      </button>
    </div>
  );
}
